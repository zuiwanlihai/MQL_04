"""MT5 bridge — serialises every MetaTrader5 call onto one worker thread.

The ``MetaTrader5`` package is not thread-safe and holds a single terminal
connection, so all calls go through a dedicated single-thread executor and
async routes await them via ``run_in_executor`` (never blocking the loop).

``ensure_initialized`` runs before each request: a failed ``initialize``
retries once (self-healing after terminal restarts / AV interference) and
raises a Chinese error message carrying ``mt5.last_error()``.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from app.config import settings

logger = logging.getLogger("gateway.mt5")

T = TypeVar("T")

# Trade/Kill commands jump ahead of market polling in the single-worker queue
# (lower number = higher priority).  Guarantees the Kill Switch stays ≤3s even
# while a large rates download is in flight.
PRIORITY_TRADE = -10
PRIORITY_NORMAL = 0

# Upper bound on the terminal-release hop during shutdown.  ``mt5.shutdown()``
# is normally instant, but a wedged IPC call must not keep the gateway process
# alive forever — see ``MT5Client.shutdown``.
SHUTDOWN_TIMEOUT_SECONDS = 10.0

# Pending-order type name → MetaTrader5 ORDER_TYPE_* constant (getattr'd lazily
# so the module can be imported without the MT5 package present, e.g. tests).
PENDING_TYPES = {
    "buy_limit": "ORDER_TYPE_BUY_LIMIT",
    "sell_limit": "ORDER_TYPE_SELL_LIMIT",
    "buy_stop": "ORDER_TYPE_BUY_STOP",
    "sell_stop": "ORDER_TYPE_SELL_STOP",
}

# -- MT5 fixed enum values (stable across MetaTrader5 versions) ---------------
# TRADE_RETCODE → Chinese text (only the codes brokers realistically return).
RETCODE_TEXT: dict[int, str] = {
    10004: "重新报价（价格已变动）",
    10006: "请求被拒绝",
    10007: "请求已被取消",
    10008: "挂单已放置",
    10009: "交易请求已完成",
    10010: "交易请求部分成交",
    10011: "交易请求执行错误",
    10012: "交易请求超时",
    10013: "请求无效",
    10014: "手数无效",
    10015: "价格无效",
    10016: "止损/止盈无效",
    10017: "交易被禁用",
    10018: "资金不足",
    10019: "价格已变动",
    10020: "无当前价格（市场关闭）",
    10021: "挂单有效期无效",
    10022: "订单状态已改变",
    10023: "请求过于频繁",
    10024: "无变化",
    10025: "服务器禁止自动交易",
    10026: "客户端禁止自动交易",
    10027: "订单被锁定",
    10028: "冻结状态",
    10029: "成交模式无效",
    10030: "与交易服务器无连接",
    10031: "仅允许真实账户",
    10032: "达到挂单数量上限",
    10033: "达到持仓数量上限",
    10034: "市场已关闭",
}

# -- timeframe map (hardcoded, per plan §1) -----------------------------------
TF_MAP: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D1": 16408,
    "W1": 32769,
    "MN1": 49153,
}

# TF → seconds (used for the rates look-back window)
TF_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}

# -- errors -------------------------------------------------------------------
class MT5Error(Exception):
    """Gateway-side failure with a user-facing Chinese ``detail``."""

    def __init__(self, detail: str, status_code: int = 503) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class MT5TerminalUnavailable(MT5Error):
    """Terminal not reachable / not initialised."""


class MT5LoginError(MT5Error):
    """mt5.login rejected the credentials (→ HTTP 400)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=400)


class MT5SymbolNotFound(MT5Error):
    """Requested symbol unknown to the terminal (→ HTTP 404)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=404)


class MT5TradeNotAllowed(MT5Error):
    """Terminal/account forbids algo trading (→ HTTP 409)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=409)


class MT5Client:
    """Async facade over the MetaTrader5 module.

    All calls run on ONE dedicated worker thread (mt5 is not thread-safe and
    holds a single terminal connection).  The work queue is *priority* based:
    trade / kill commands (``PRIORITY_TRADE``) jump ahead of queued market
    polls, so a Kill Switch is never stuck behind a large rates download.
    """

    def __init__(self) -> None:
        # queue items are (priority, seq, job) tuples; None is the shutdown
        # sentinel.  ``job`` = (fn, args, kwargs, future).
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()  # FIFO tie-breaker within a priority
        self._worker = threading.Thread(
            target=self._work_loop, name="mt5-worker", daemon=True
        )
        self._worker.start()
        self._shutdown = False
        self._initialized = False
        self._import_error: str | None = None
        # tick 时间轴相对 bar(UTC) 时间轴的整分钟偏移（见 _refresh_tz_skew）
        self._tz_skew = 0
        self._tz_skew_at = 0.0
        # 持仓/挂单时间轴相对 bar(UTC) 轴的偏移（见 _trade_axis_skew）
        self._trade_skew = 0
        self._trade_skew_at = 0.0
        # rates 尾部自愈冷却：(symbol, timeframe) → 冷却截止时间。
        # 休市/周末时自愈永不收敛，冷却期内跳过重试防打爆。
        self._tail_heal_cooldown: dict[tuple[str, str], float] = {}
        # K 线轴「当前时刻」提示：symbol → (轴上 now, 采集时刻)。
        # bars 轴是 broker 服务器时区伪 epoch，与 time.time() 有固定偏移
        # （demo 实测 -28800s），尾部自愈的新鲜度判定必须同轴。
        self._axis_hint: dict[str, tuple[int, float]] = {}
        # /account/login 切换登录后的凭据记忆（仅内存，重启回退 .env 默认）。
        # _ensure_initialized_sync 重连时必须复用，否则会静默切回默认账户。
        self._login_override: tuple[int, str, str] | None = None
        # 登录卡死冷却截止（time.monotonic 基准）：mt5.login 遇终端 OTP
        # 对话框/服务器不可达会无限阻塞，超时放弃后短窗内拒绝再次登录，
        # 避免堆积卡死的 IPC 线程。
        self._login_stuck_until = 0.0
        try:
            import MetaTrader5 as mt5  # noqa: PLC0415 — imported lazily, may be missing

            self._mt5 = mt5
        except Exception as exc:  # noqa: BLE001 — surface any import failure
            self._mt5 = None
            self._import_error = str(exc)

    # -- plumbing -------------------------------------------------------------
    def _work_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # shutdown sentinel
                break
            _, _, (fn, args, kwargs, fut) = item
            try:
                result = fn(*args, **kwargs)
                if not fut.set_running_or_notify_cancel():
                    continue
                fut.set_result(result)
            except BaseException as exc:  # noqa: BLE001 — propagate to awaiter
                if fut.set_running_or_notify_cancel():
                    fut.set_exception(exc)

    async def run(
        self,
        fn: Callable[..., T],
        *args: Any,
        priority: int = PRIORITY_NORMAL,
        **kwargs: Any,
    ) -> T:
        """Queue a blocking function on the serialised MT5 worker thread.

        ``priority`` lower = earlier (trade commands use ``PRIORITY_TRADE``).
        """
        if self._shutdown:
            raise MT5TerminalUnavailable("网关正在关闭")
        loop = asyncio.get_running_loop()
        fut: Future = Future()
        self._queue.put((priority, next(self._seq), (fn, args, kwargs, fut)))
        return await asyncio.wrap_future(fut, loop=loop)

    def _ensure_initialized_sync(self) -> None:
        """Lazy init with one self-healing retry (blocking, worker thread).

        When ``MT5_LOGIN/PASSWORD/SERVER`` are configured they are passed to
        ``initialize`` directly — this is the reliable way to recover a
        terminal whose saved session was invalidated (a bare ``initialize``
        on an unauthorised terminal returns ``-6 Authorization failed``).
        """
        if self._mt5 is None:
            raise MT5TerminalUnavailable(
                f"MetaTrader5 Python 包不可用：{self._import_error}"
            )
        if self._initialized and self._mt5.terminal_info() is not None:
            # terminal_info 在终端被杀后可能仍返回陈旧 IPC 缓存（实测
            # symbol_select 报 (-1, Terminal: Call failed)）——用 symbols_total
            # 做真实探针，None 则强制重连，重新附着 TerminalGuard 拉起的终端。
            if self._mt5.symbols_total() is not None:
                return
            self._mt5.shutdown()
            self._initialized = False
        self._initialized = False
        kwargs: dict[str, Any] = {"path": settings.MT5_PATH}
        if self._login_override is not None:
            ov_login, ov_password, ov_server = self._login_override
            kwargs.update(login=ov_login, password=ov_password, server=ov_server)
        elif settings.MT5_LOGIN and settings.MT5_PASSWORD and settings.MT5_SERVER:
            kwargs.update(
                login=settings.MT5_LOGIN,
                password=settings.MT5_PASSWORD,
                server=settings.MT5_SERVER,
            )
        for attempt in (1, 2):
            if self._mt5.initialize(**kwargs):
                self._initialized = True
                return
            code, msg = self._mt5.last_error()
            if attempt == 2:
                raise MT5TerminalUnavailable(
                    f"MT5 终端连接失败（第{attempt}次）：({code},{msg})。"
                    f"请确认终端路径 {settings.MT5_PATH} 正确、账户凭据有效"
                )
            self._mt5.shutdown()

    async def ensure_initialized(self) -> None:
        await self.run(self._ensure_initialized_sync)

    def shutdown_sync(self) -> None:
        if self._mt5 is not None and self._initialized:
            self._mt5.shutdown()
            self._initialized = False

    async def shutdown(self) -> None:
        """Release the terminal connection, then stop the worker thread.

        This MUST NOT raise.  The caller (``lifespan``) clears the run-state
        file immediately after, and a state file left behind makes the *next*
        boot report ``abnormal_restart`` → a spurious「请先核对持仓」critical
        alert on every ordinary restart, which trains the user to ignore the
        one alert that matters.

        ``_shutdown`` is latched *before* the queue hop so no new work is
        accepted, and the release job is pushed onto the queue directly rather
        than via ``run()`` — which would reject it, since it is latched.  It
        goes in at trade priority so it jumps any market polls still queued
        behind a large rates download.  Idempotent.
        """
        if self._shutdown:
            return
        self._shutdown = True
        try:
            loop = asyncio.get_running_loop()
            fut: Future = Future()
            self._queue.put(
                (PRIORITY_TRADE, next(self._seq), (self.shutdown_sync, (), {}, fut))
            )
            # Bounded wait: if the worker is wedged inside a stuck MT5 IPC
            # call, stop anyway and let the process exit.  A leaked terminal
            # handle is far less harmful than a gateway that never terminates.
            await asyncio.wait_for(
                asyncio.wrap_future(fut, loop=loop),
                timeout=SHUTDOWN_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — shutdown must never fail the lifespan
            logger.warning("MT5 release did not complete cleanly", exc_info=True)
        finally:
            self._queue.put(None)  # stop the worker thread

    # -- account ---------------------------------------------------------------
    @staticmethod
    def _account_summary(account: Any) -> dict[str, Any]:
        mode = getattr(account, "trade_mode", None)
        # ACCOUNT_TRADE_MODE: 0=demo, 1=contest, 2=real
        account_type = {0: "demo", 1: "contest", 2: "real"}.get(mode, "demo")
        return {
            "login": int(getattr(account, "login", 0) or 0),
            "server": str(getattr(account, "server", "") or ""),
            "name": str(getattr(account, "name", "") or ""),
            "type": account_type,
            "leverage": int(getattr(account, "leverage", 0) or 0),
            "currency": str(getattr(account, "currency", "") or ""),
            "balance": float(getattr(account, "balance", 0.0) or 0.0),
            "credit": float(getattr(account, "credit", 0.0) or 0.0),
            "equity": float(getattr(account, "equity", 0.0) or 0.0),
            "margin": float(getattr(account, "margin", 0.0) or 0.0),
            "margin_free": float(getattr(account, "margin_free", 0.0) or 0.0),
            "margin_level": float(getattr(account, "margin_level", 0.0) or 0.0),
            "trade_allowed": bool(getattr(account, "trade_allowed", False)),
        }

    def _current_login_sync(self) -> int | None:
        if not self._initialized:
            return None
        acct = self._mt5.account_info()
        return int(acct.login) if acct is not None else None

    def _remember_login_sync(self, login: int, password: str, server: str) -> None:
        # 记忆凭据供 IPC 重连自愈复用（同 _login_only_sync 的行为）
        self._login_override = (int(login), password, server)

    def _login_only_sync(self, login: int, password: str, server: str) -> dict[str, Any]:
        """mt5.login 本体（独立线程执行——见 login 的超时设计）。"""
        ok = self._mt5.login(login=login, password=password, server=server)
        if not ok:
            code, msg = self._mt5.last_error()
            raise MT5LoginError(self._friendly_login_error(code, msg))
        account = self._mt5.account_info()
        if account is None:
            code, msg = self._mt5.last_error()
            raise MT5LoginError(self._friendly_login_error(code, msg))
        # 记忆切换凭据：后续 IPC 重连（终端重启/断线自愈）复用同账户，
        # 防止 _ensure_initialized_sync 按 .env 默认静默切回。
        self._login_override = (int(login), password, server)
        return self._account_summary(account)

    @staticmethod
    def _friendly_login_error(code: int, msg: str) -> str:
        """把 MT5 原始错误码翻译成用户能行动的提示。"""
        if code == -6:
            return (
                "MT5 拒绝登录（-6 Authorization failed）：账号、密码或服务器名不匹配"
                "（密码区分大小写）。若该账户已绑定，直接在账户列表点击登录即可，"
                "无需重新绑定。"
            )
        if code == -5:
            return "MT5 登录失败：账号不存在或密码错误（-5 Invalid account）"
        return f"MT5 登录失败：({code},{msg})"

    def _recover_after_failed_login_sync(self) -> None:
        """失败登录后的主动恢复：分离 IPC 再按 override/env 重新登录。

        实测：对坏服务器/坏凭据的 login 失败（-6）后，终端会陷入该服务
        器的重连循环，MT5 IPC 全局被堵（health/quotes 全部饿死）。立即
        shutdown 分离 + 重新 initialize 到原账户可快速恢复服务。
        """
        try:
            self._mt5.shutdown()
        except Exception:  # noqa: BLE001 — detach is best-effort
            pass
        self._initialized = False
        self._ensure_initialized_sync()

    async def login(self, login: int, password: str, server: str) -> dict[str, Any]:
        """切换终端登录账户（有界超时 + 失败自恢复，绝不拖垮网关）。

        mt5.login 对「服务器不可达 / 终端弹出 OTP 验证对话框」可能无限
        阻塞——2026-10-12 实盘切换验证实测：卡在队列工作线程后 health/
        quotes 全部饿死。因此 initialize（终端附着）仍走 worker 串行，
        login 本体放独立线程 + wait_for 超时；超时给调用方明确错误，
        卡住的线程交由 OS 回收，并进入 120s 冷却拒绝再次登录。
        登录「失败返回」（-6 等）同样会让终端卡进坏服务器重连循环堵死
        IPC——失败后立即主动恢复到原账户再抛错。
        """
        if self._shutdown:
            raise MT5TerminalUnavailable("网关正在关闭")
        if self._login_stuck_until and time.monotonic() < self._login_stuck_until:
            raise MT5LoginError(
                "上一次登录仍卡在 MT5 IPC：请先在 MT5 终端处理验证/OTP 对话框后重试"
            )
        await self.run(self._ensure_initialized_sync)
        # 同账户短路：终端已登录同一账户时绝不 re-login——反复 mt5.login
        # 会重置终端的交易历史同步（history_deals_get 按日期区间查询依赖
        # 该同步），导致风控 deals 统计拿不到刚成交的单。
        current = await self.run(self._current_login_sync)
        if current == int(login):
            await self.run(self._remember_login_sync, int(login), password, server)
            summary = await self.account()
            if summary is not None:
                return summary
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._login_only_sync, login, password, server),
                timeout=45.0,
            )
        except TimeoutError:
            self._login_stuck_until = time.monotonic() + 120.0
            raise MT5LoginError(
                "MT5 登录超时（45s）：终端可能弹出验证/OTP 对话框，或服务器不可达。"
                "请在 MT5 终端完成或取消验证后重试"
            ) from None
        except MT5LoginError:
            # 失败登录会让终端卡进坏服务器的重连循环（IPC 全局阻塞）——
            # 立即按原账户重新附着恢复服务，再把原始错误抛给调用方。
            try:
                await asyncio.wait_for(
                    self.run(self._recover_after_failed_login_sync), timeout=60.0
                )
            except Exception:  # noqa: BLE001 — 恢复尽力而为，原始错误优先
                pass
            raise

    def _logout_sync(self) -> None:
        if self._initialized and self._mt5.terminal_info() is not None:
            self._mt5.logout()  # idempotent — succeeds even when logged out
        # 回到 .env 默认账户行为（下次重连按默认凭据自动登录）
        self._login_override = None

    async def logout(self) -> None:
        await self.run(self._logout_sync)

    def _account_sync(self) -> dict[str, Any] | None:
        self._ensure_initialized_sync()
        account = self._mt5.account_info()
        return self._account_summary(account) if account is not None else None

    async def account(self) -> dict[str, Any] | None:
        return await self.run(self._account_sync)

    # -- health ----------------------------------------------------------------
    def _health_sync(self) -> dict[str, Any]:
        import psutil  # noqa: PLC0415 — optional weight only needed here

        from app import ops  # local import — ops reads client, avoid cycle

        proc = psutil.Process()
        payload: dict[str, Any] = {
            "version": "1.0.0",
            "mt5_version": "",
            "uptime_seconds": 0.0,
            "cpu_percent": round(proc.cpu_percent(interval=0.0), 1),
            "memory_percent": round(psutil.virtual_memory().percent, 1),
            "active_sessions": 0,
            "terminal": {"connected": False, "trade_allowed": False},
            "account": None,
            "error": None,
            # M11: stale state file at boot → previous run did not exit cleanly.
            # The backend heartbeat turns the first sighting into a critical
            #「请先核对持仓」alert (PRD: 异常重启不得静默恢复).
            "abnormal_restart": ops.ABNORMAL_RESTART is not None,
            "abnormal_restart_info": ops.ABNORMAL_RESTART,
            "guard_gave_up": ops.GIVEUP_FLAG[0],
        }

        if self._mt5 is None:
            payload["status"] = "no_terminal"
            payload["error"] = f"MetaTrader5 Python 包不可用：{self._import_error}"
            return payload

        try:
            payload["mt5_version"] = str(getattr(self._mt5, "__version__", "") or "")
            payload["uptime_seconds"] = time.time() - START_TIME

            if not self._ensure_initialized_available():
                payload["status"] = "no_terminal"
                code, msg = self._mt5.last_error()
                payload["error"] = f"MT5 终端不可达：({code},{msg})"
                return payload

            terminal = self._mt5.terminal_info()
            if terminal is None:
                payload["status"] = "no_terminal"
                code, msg = self._mt5.last_error()
                payload["error"] = f"MT5 终端不可达：({code},{msg})"
                return payload

            payload["terminal"] = {
                "connected": bool(getattr(terminal, "connected", False)),
                "trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            }
            payload["active_sessions"] = 1 if getattr(terminal, "connected", False) else 0

            account = self._mt5.account_info()
            if account is None:
                payload["status"] = "no_login"
                return payload

            payload["account"] = self._account_summary(account)
            if payload["terminal"]["connected"]:
                payload["status"] = "ok"
            else:
                payload["status"] = "degraded"
                payload["error"] = "终端未登录或通信状态异常"
            return payload
        except MT5Error as exc:
            payload["status"] = "no_terminal"
            payload["error"] = exc.detail
            return payload

    def _ensure_initialized_available(self) -> bool:
        """Non-raising init probe used by /health (must always answer)."""
        try:
            self._ensure_initialized_sync()
            return True
        except MT5Error:
            return False

    async def health(self) -> dict[str, Any]:
        return await self.run(self._health_sync)

    # -- symbols ----------------------------------------------------------------
    def _symbols_sync(self) -> list[dict[str, Any]]:
        self._ensure_initialized_sync()
        rows = self._mt5.symbols_get()
        if rows is None:
            return []
        out: list[dict[str, Any]] = []
        for value in rows:
            info = self._mt5.symbol_info(value.name)
            if info is None:
                continue
            out.append(self._symbol_dict(info))
        return out

    @staticmethod
    def _symbol_dict(info: Any) -> dict[str, Any]:
        def g(name: str, default: Any = None) -> Any:
            return getattr(info, name, default)

        def g2(*names: str, default: Any = None) -> Any:
            """First present attribute wins.

            MetaTrader5 5.0.6231 renamed SymbolInfo fields
            (contract_size→trade_contract_size, tick_size→trade_tick_size,
            tick_value→trade_tick_value, session_begin→session_open,
            session_end→session_close).  Support both spellings so a package
            upgrade/downgrade never silently zeroes economics again.
            """
            for n in names:
                v = getattr(info, n, None)
                if v is not None:
                    return v
            return default

        return {
            "name": g("name", ""),
            "path": g("path", ""),
            "description": g("description", ""),
            "digits": g("digits", 0),
            "point": g("point", 0.0),
            "spread": g("spread", 0),
            "trade_contract_size": g2("trade_contract_size", "contract_size", default=0.0),
            "volume_min": g("volume_min", 0.0),
            "volume_max": g("volume_max", 0.0),
            "volume_step": g("volume_step", 0.0),
            "swap_long": g("swap_long", 0),
            "swap_short": g("swap_short", 0),
            "margin_initial": g("margin_initial", 0.0),
            "trade_mode": g("trade_mode", -1),
            "session_open": g2("session_open", "session_begin", default=None),
            "session_close": g2("session_close", "session_end", default=None),
            "currency_base": g("currency_base", ""),
            "currency_profit": g("currency_profit", ""),
            "trade_tick_size": g2("trade_tick_size", "tick_size", default=0.0),
            "trade_tick_value": g2("trade_tick_value", "tick_value", default=0.0),
            "visible": g("visible", True),
        }

    async def symbols(self) -> list[dict[str, Any]]:
        return await self.run(self._symbols_sync)

    # -- rates -------------------------------------------------------------------
    def _rates_sync(self, name: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        self._ensure_initialized_sync()
        upper = name.upper()
        if not self._mt5.symbol_select(upper, True):
            code, msg = self._mt5.last_error()
            raise MT5SymbolNotFound(f"品种不存在：{name}（({code},{msg})）")
        if self._mt5.symbol_info(upper) is None:
            raise MT5SymbolNotFound(f"品种不存在：{name}")

        tf = TF_MAP[timeframe]
        seconds = TF_SECONDS[timeframe]

        # 主路径：copy_rates_from_pos —— 实测（2026-10-13 probe_axis.py）它的
        # 尾根内容**新鲜**（活 bar close == 当前 tick 价），但时间是「服务器
        # 时区伪 epoch」（+skew，demo 实测 10800s）。−skew 后即真实 UTC 轴。
        # （原主路径 copy_rates_range 时间轴虽是 UTC，但终端对 range 窗口的
        # 尾根缓存长期滞后 ~1h —— 旧价画在当前桶 = 假蜡烛，且尾部自愈冷却
        # 后放弃。故反转：from_pos 为主，range 降级兜底。）
        fresh = self._mt5.copy_rates_from_pos(upper, tf, 0, limit)
        if fresh is not None and len(fresh) > 0:
            skew = self._trade_axis_skew([upper])
            return [
                {
                    "time": int(r["time"]) - skew,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "tick_volume": int(r["tick_volume"]),
                    "spread": int(r["spread"]),
                    "real_volume": int(r["real_volume"]),
                }
                for r in fresh
            ]

        # 兜底：from_pos 空（休市且终端无本地历史）→ range 拉 UTC 窗口。
        # 窗口按 limit×1.6 估根，周末/节假日会少拉（历史缺失但不报错）。
        date_from = datetime.now(UTC) - timedelta(
            seconds=int(seconds * limit * 1.6) + seconds
        )
        rates = self._mt5.copy_rates_range(upper, tf, date_from, datetime.now(UTC))
        if rates is None:
            return []
        # numpy structured rows support []-indexing but not .get()
        names = set(getattr(rates.dtype, "names", ()) or ())
        rows = [
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r["tick_volume"]) if "tick_volume" in names else 0,
                "spread": int(r["spread"]) if "spread" in names else 0,
                "real_volume": int(r["real_volume"]) if "real_volume" in names else 0,
            }
            for r in rates[-limit:]
        ]
        return rows

    async def rates(self, name: str, timeframe: str, limit: int) -> list[dict[str, Any]]:
        """K 线 + 尾部滞后自愈。

        终端只为「开了图表」的品种维护 bar 历史，未开图表品种 copy_rates
        会返回陈旧缓存；检测到尾根落后 ≥2 根（缺当前活 bar + 上一根完成
        bar）时轻量重试，触发终端后台补数。重试必须在 async 层循环（不霸
        占单 worker 队列）；永不收敛（休市）时写入冷却，冷却期内跳过重试。
        """
        rows = await self.run(self._rates_sync, name, timeframe, limit)
        key = (name.upper(), timeframe)
        if time.time() >= self._tail_heal_cooldown.get(key, 0.0):
            tf_sec = TF_SECONDS[timeframe]
            for _ in range(max(1, settings.RATES_TAIL_HEAL_MAX_ATTEMPTS)):
                if not rows:
                    break
                axis_now = self._axis_now_hint(name)
                expected = axis_now - axis_now % tf_sec  # 当前桶起点（同轴）
                if rows[-1]["time"] >= expected - tf_sec:
                    break  # 有当前活 bar 或上一根刚收完的 bar → 新鲜
                await asyncio.sleep(settings.RATES_TAIL_HEAL_INTERVAL)
                rows = await self.run(self._rates_sync, name, timeframe, limit)
            else:
                self._tail_heal_cooldown[key] = (
                    time.time() + settings.RATES_TAIL_HEAL_COOLDOWN
                )
        return rows

    def _axis_now_hint(self, name: str) -> int:
        """K 线轴上的「当前时刻」估计，供尾部自愈同轴判新鲜。

        bars 轴是 broker 服务器时区伪 epoch（demo 实测比 time.time() 慢
        28800s），用真实 now 判定会把新鲜尾根误判为滞后、休市期空转自愈。
        优先取报价批次缓存的 bar_time（tick−skew 与 bars 同轴），并补偿
        提示采集后流逝的时间；15s 内无报价提示则退回真实 now（旧行为）。
        """
        hint = self._axis_hint.get(name.upper())
        if hint is not None:
            axis_t, captured = hint
            elapsed = time.time() - captured
            if 0.0 <= elapsed <= 15.0:
                return axis_t + int(elapsed)
        return int(time.time())

    # -- rates by date range (backtest slice M4) -------------------------------
    def _rates_range_sync(
        self,
        name: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Candles inside [date_from, date_to) — ascending, capped at ``limit``."""
        self._ensure_initialized_sync()
        upper = name.upper()
        if not self._mt5.symbol_select(upper, True):
            code, msg = self._mt5.last_error()
            raise MT5SymbolNotFound(f"品种不存在：{name}（({code},{msg})）")
        if self._mt5.symbol_info(upper) is None:
            raise MT5SymbolNotFound(f"品种不存在：{name}")

        rates = self._mt5.copy_rates_range(upper, TF_MAP[timeframe], date_from, date_to)
        if rates is None:
            return []
        names = set(getattr(rates.dtype, "names", ()) or ())
        rows = [
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r["tick_volume"]) if "tick_volume" in names else 0,
                "spread": int(r["spread"]) if "spread" in names else 0,
                "real_volume": int(r["real_volume"]) if "real_volume" in names else 0,
            }
            for r in rates[:limit]
        ]
        return rows

    async def rates_range(
        self,
        name: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.run(
            self._rates_range_sync, name, timeframe, date_from, date_to, limit
        )

    # -- history probe / backfill (M2 数据中心) ---------------------------------
    def _history_probe_sync(self, name: str, timeframe: str) -> dict[str, Any]:
        """探测终端本地已缓存 K 线范围（纯本地，不做下载重试）。

        MT5 行为：copy_rates_from_pos 超出本地序列起点返回 None/空，且单次
        调用只返回当前已就绪的数据、不会等待下载 —— 因此分页向历史回溯遇到
        短读即为「本地最早」。terminal_info().maxbars 是终端「图表最大 K 线数」
        玻璃天花板，一并返回供前端提示。
        注意：count 必须严格小于 maxbars —— 请求 count == maxbars 时终端直接
        返回 (-2, 'Invalid params') 空结果（5.0.6231 实测）。
        """
        self._ensure_initialized_sync()
        upper = name.upper()
        if not self._mt5.symbol_select(upper, True):
            code, msg = self._mt5.last_error()
            raise MT5SymbolNotFound(f"品种不存在：{name}（({code},{msg})）")
        if self._mt5.symbol_info(upper) is None:
            raise MT5SymbolNotFound(f"品种不存在：{name}")

        tf = TF_MAP[timeframe]
        page = settings.HISTORY_PROBE_PAGE_SIZE
        max_bars: int | None = None
        tinfo = self._mt5.terminal_info()
        if tinfo is not None:
            max_bars = int(getattr(tinfo, "maxbars", 0) or 0) or None
        if max_bars:
            # count 严格小于 maxbars（== maxbars 会被终端拒绝返回空）
            page = max(1, min(page, max_bars - 1))

        latest = self._mt5.copy_rates_from_pos(upper, tf, 0, 1)
        if latest is None or len(latest) == 0:
            # 本地无任何该周期数据（休市且从未拉取过）
            return {
                "symbol": upper,
                "timeframe": timeframe,
                "first_bar_time": None,
                "last_bar_time": None,
                "bars_count": 0,
                "truncated": False,
                "max_bars": max_bars,
            }
        last_bar_time = int(latest[0]["time"])

        total = 0
        first_bar_time: int | None = None
        truncated = False
        offset = 0
        while True:
            # ascending（旧→新）；offset 从当前向历史方向推进
            chunk = self._mt5.copy_rates_from_pos(upper, tf, offset, page)
            n = 0 if chunk is None else len(chunk)
            if n == 0:
                if offset == 0 and page > 1:
                    # 防御：个别终端对超大 count 静默返回空（maxbars 边界），
                    # 折半重试直到 page==1 仍空才判定为本地起点
                    page = max(1, page // 2)
                    continue
                break  # 空读 → 已到本地序列起点
            first_bar_time = int(chunk[0]["time"])
            total += n
            if n < page:
                break  # 短读 → 本地最早
            offset += n
            if total >= settings.HISTORY_PROBE_MAX_BARS:
                truncated = True
                break
        return {
            "symbol": upper,
            "timeframe": timeframe,
            "first_bar_time": first_bar_time,
            "last_bar_time": last_bar_time,
            "bars_count": total,
            "truncated": truncated,
            "max_bars": max_bars,
        }

    async def history_probe(self, name: str, timeframe: str) -> dict[str, Any]:
        return await self.run(self._history_probe_sync, name, timeframe)

    async def history_backfill(
        self,
        name: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        """循环请求 [start_ts, end_ts) 触发终端向券商补下载历史 K 线。

        必须是 async 方法内 for 循环 —— 绝不能把重试循环写进同步函数霸占
        单 worker 队列（交易命令会被整段堵死）。每轮之间 sleep 释放 worker
        保 Kill Switch ≤3s。收敛判定：
        - 某轮结果 first <= start_ts → complete=True（已覆盖请求起点）
        - 连续两轮签名 (count, first, last) 完全一致 → 终端已尽力，提前结束
        """
        if max_attempts is None:
            max_attempts = settings.HISTORY_BACKFILL_MAX_ATTEMPTS
        interval = settings.HISTORY_BACKFILL_RETRY_INTERVAL
        date_from = datetime.fromtimestamp(start_ts, UTC)
        date_to = datetime.fromtimestamp(end_ts, UTC)

        attempts = 0
        complete = False
        prev_sig: tuple[int, int | None, int | None] | None = None
        first_bar: int | None = None
        last_bar: int | None = None
        bars = 0
        for _ in range(max(1, max_attempts)):
            attempts += 1
            rows = await self.run(
                self._rates_range_sync, name, timeframe, date_from, date_to, 200_000
            )
            bars = len(rows)
            first_bar = rows[0]["time"] if rows else None
            last_bar = rows[-1]["time"] if rows else None
            if first_bar is not None and first_bar <= start_ts:
                complete = True
                break
            sig = (bars, first_bar, last_bar)
            if sig == prev_sig:
                break  # 两轮签名一致 → 终端已尽力
            prev_sig = sig
            if attempts < max_attempts:
                await asyncio.sleep(interval)  # 释放 worker，给终端下载时间
        return {
            "first_bar_time": first_bar,
            "last_bar_time": last_bar,
            "bars_count": bars,
            "attempts": attempts,
            "complete": complete,
            "requested_start": start_ts,
            "requested_end": end_ts,
        }

    # -- ticks by date range (M-Tick 数据管理) ----------------------------------
    def _ticks_range_sync(
        self,
        name: str,
        date_from: datetime,
        date_to: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Ticks inside [date_from, date_to) — ascending by time_msc, capped.

        Rows: time_msc (ms epoch), bid, ask, last, volume, flags.  ``last``/volume
        are 0 for forex quotes without last-trade ticks.

        时区语义：copy_ticks_range 的起止参数与返回 tick 的 time/time_msc 都按
        MT5 服务器时区轴解释（实测服务器 UTC+3 时 tick.time 比 UTC now 快 3h）。
        本端点对调用方承诺 UTC 语义：检索窗口先加 skew 落到服务器轴，返回行
        再减 skew 还原为 UTC 时间戳。
        """
        self._ensure_initialized_sync()
        upper = name.upper()
        if not self._mt5.symbol_select(upper, True):
            code, msg = self._mt5.last_error()
            raise MT5SymbolNotFound(f"品种不存在：{name}（({code},{msg})）")
        if self._mt5.symbol_info(upper) is None:
            raise MT5SymbolNotFound(f"品种不存在：{name}")

        # 必须显式传 COPY_TICKS_ALL：省略 flags 时终端返回 None（-2 Invalid
        # arguments），表现为「永远查不到任何 tick」的静默失败（实测踩坑）。
        skew = 0
        info_tick = self._mt5.symbol_info_tick(upper)
        if info_tick is not None and info_tick.time:
            skew = int(info_tick.time) - int(time.time())
        d_from = date_from + timedelta(seconds=skew)
        d_to = date_to + timedelta(seconds=skew)
        ticks = self._mt5.copy_ticks_range(
            upper, d_from, d_to, self._mt5.COPY_TICKS_ALL
        )
        if ticks is None:
            return []
        names = set(getattr(ticks.dtype, "names", ()) or ())
        has_msc = "time_msc" in names
        rows = [
            {
                "time_msc": (
                    (int(r["time_msc"]) if has_msc else int(r["time"]) * 1000)
                    - skew * 1000
                ),
                "bid": float(r["bid"]),
                "ask": float(r["ask"]),
                "last": float(r["last"]),
                "volume": int(r["volume"]),
                "flags": int(r["flags"]) if "flags" in names else 0,
            }
            for r in ticks[:limit]
        ]
        return rows

    async def ticks_range(
        self,
        name: str,
        date_from: datetime,
        date_to: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await self.run(self._ticks_range_sync, name, date_from, date_to, limit)

    # -- quotes / clock axes ------------------------------------------------------
    # MetaTrader5 python 的 tick.time 与 bar time 不同轴（见 _refresh_tz_skew）。
    _TZ_SKEW_TTL = 600.0  # 10 分钟刷新，跟随服务器 DST 切换

    def _refresh_tz_skew(self, tick_times: list[int]) -> None:
        """估算 tick 轴相对 bar(UTC) 轴的偏移（整分钟）。

        MetaTrader5 python 的 ``symbol_info_tick().time`` 是「服务器时区伪
        epoch」，而 ``copy_rates()`` 的 bar time 是真实 UTC 伪 epoch，二者相
        差整时区偏移（如 GMT+3 → 10800s）。用真实墙钟估算并取整分钟。

        抗污染：个别品种喂价时间戳可能异常（实测 XAUUSD 超前 ~+18min），
        单样本会污染全局 skew → 改采批次内最多 3 个品种的 (tick−now) 排序
        取中位；偶数个样本取较小中位 —— 低估方向配合 bar_time 的
        min(·, now) 钳制是安全的（略超前会被钳回当前桶，高估则会把 bar
        推进过去的桶）。休市 tick 停摆会让估算偏大，但彼时无新 bar 形成，
        对桶无实际影响。
        """
        now = time.time()
        if self._tz_skew_at and now - self._tz_skew_at < self._TZ_SKEW_TTL:
            return
        deltas = sorted(t - now for t in tick_times if t > 0)
        if not deltas:
            return
        median = deltas[(len(deltas) - 1) // 2]
        self._tz_skew = round(median / 60) * 60
        self._tz_skew_at = now

    def _trade_axis_skew(self, symbols: list[str]) -> int:
        """持仓/挂单时间轴 → K 线(真实 UTC)轴的偏移（对齐用法：raw − skew）。

        实测（2026-10-12 probe_axis.py）：``position.time`` /
        ``order.time_setup`` / ``tick.time`` / ``copy_rates_from_pos`` 全部
        是「服务器时区伪 epoch」（demo 实测比真实 UTC 快 +10800s），而
        ``copy_rates_range``（K 线 API 主路径）返回真实 UTC —— 透传会导致
        前端把开仓/挂单画错 K 线（差 N 根）。

        估算法：最新 tick.time − copy_rates_range(UTC 窗口) 最后一根 M1
        bar.time。休市/周末时两者同时停摆在周五收盘，差值仍精确等于时区
        偏移（不像 tick−now 会被停摆时长污染）。批次最多 3 个品种取较小
        中位（抗单品种喂价时间戳漂移，如 XAUUSD 超前 ~+18min），钳制
        ±15h、取整分钟。10 分钟缓存跟随 DST 切换。
        """
        now = time.time()
        if self._trade_skew_at and now - self._trade_skew_at < self._TZ_SKEW_TTL:
            return self._trade_skew
        samples: list[int] = []
        for sym in symbols[:3]:
            sym = (sym or "").upper()
            if not sym:
                continue
            try:
                # symbol_info_tick 只对 Market Watch 里的品种有值——新进程
                # 可能尚未选中该品种，必须先 symbol_select
                if not self._mt5.symbol_select(sym, True):
                    continue
                tick = self._mt5.symbol_info_tick(sym)
                if tick is None or int(getattr(tick, "time", 0) or 0) <= 0:
                    continue
                utc_now = datetime.now(UTC)
                bars = self._mt5.copy_rates_range(
                    sym, TF_MAP["M1"], utc_now - timedelta(days=4), utc_now
                )
                if bars is None or len(bars) == 0:
                    continue
                samples.append(int(tick.time) - int(bars[-1]["time"]))
            except Exception:
                continue
        if not samples:
            # 无样本（品种异常/全部失败）→ 沿用报价路径的估算；两者皆无
            # 说明什么都没学到，不缓存（_trade_skew_at=0），下次调用重试
            self._trade_skew = self._tz_skew
            self._trade_skew_at = now if self._tz_skew else 0.0
            return self._trade_skew
        samples.sort()
        # floor 取整：diff = tz + tick 秒位噪声（0..59s），floor 恰好剥掉噪声
        # （round 会把 ≥30s 的噪声进位成整分钟误差，M1 图上差一根）
        self._trade_skew = max(
            -54000, min(54000, (samples[(len(samples) - 1) // 2] // 60) * 60)
        )
        self._trade_skew_at = now
        return self._trade_skew

    def _quote_sync(self, name: str) -> dict[str, Any] | None:
        upper = name.upper()
        if not self._mt5.symbol_select(upper, True):
            return None
        info = self._mt5.symbol_info(upper)
        if info is None:
            return None
        tick = self._mt5.symbol_info_tick(upper)
        if tick is None:
            return None

        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        tick_time = int(getattr(tick, "time", 0) or 0)
        quote: dict[str, Any] = {
            "symbol": upper,
            "description": getattr(info, "description", ""),
            "bid": bid,
            "ask": ask,
            "last": float(getattr(tick, "last", 0.0) or 0.0) or bid,
            "time": tick_time,
            "volume": int(getattr(tick, "volume", 0) or 0),
            "point": float(getattr(info, "point", 0.0) or 0.0),
            "prev_close": 0.0,
            "high_day": 0.0,
            "low_day": 0.0,
        }

        # prev_close = 昨日 D1 close；high/low_day = 今日 D1（60s 模块缓存）
        now = time.time()
        cached = _daily_cache.get(upper)
        if cached is None or now - cached[0] > 60:
            today_start = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            bars = self._mt5.copy_rates_range(
                upper, TF_MAP["D1"], today_start - timedelta(days=2),
                datetime.now(UTC),
            )
            prev_close, high_day, low_day = 0.0, 0.0, 0.0
            if bars is not None and len(bars):
                today_ts = int(today_start.timestamp())
                todays_high = [
                    float(b["high"]) for b in bars if int(b["time"]) >= today_ts
                ]
                todays_low = [
                    float(b["low"]) for b in bars if int(b["time"]) >= today_ts
                ]
                closes_before = [
                    float(b["close"]) for b in bars if int(b["time"]) < today_ts
                ]
                if closes_before:
                    prev_close = closes_before[-1]
                if todays_high and todays_low:
                    high_day = max(todays_high)
                    low_day = min(todays_low)
                else:
                    all_bars = list(bars)
                    high_day = max(float(b["high"]) for b in all_bars)
                    low_day = min(float(b["low"]) for b in all_bars)
            _daily_cache[upper] = (now, prev_close, high_day, low_day)
        _, prev_close, high_day, low_day = _daily_cache[upper]

        quote["prev_close"] = prev_close
        quote["high_day"] = high_day
        quote["low_day"] = low_day
        return quote

    def _quotes_sync(self, names: list[str]) -> list[dict[str, Any]]:
        self._ensure_initialized_sync()
        out: list[dict[str, Any]] = []
        samples: list[int] = []
        for name in names:
            quote = self._quote_sync(name.strip())
            if quote is None:
                continue
            if quote["time"] > 0 and len(samples) < 3:
                samples.append(int(quote["time"]))
            out.append(quote)
        # 批次级估 skew（多品种中位数抗单品种喂价时间戳污染），再统一补
        # bar_time：与 K 线（copy_rates → 真实 UTC 伪 epoch）同轴供前端对
        # 桶；min(·, now) 钳制未来时间戳，污染 tick 也落在当前桶。
        self._refresh_tz_skew(samples)
        now = int(time.time())
        for quote in out:
            bt = min(int(quote["time"]) - self._tz_skew, now)
            quote["bar_time"] = bt
            if bt > 0:
                # 供 rates() 尾部自愈同轴判新鲜（_axis_now_hint）
                self._axis_hint[quote["symbol"].upper()] = (bt, now)
        return out

    async def quotes(self, names: list[str]) -> list[dict[str, Any]]:
        return await self.run(self._quotes_sync, names)

    # -- trading (M6 live deployment) -------------------------------------------
    def _check_trade_allowed_sync(self) -> None:
        """Server-side gate: feature flag, login whitelist, algo-trade switches."""
        if not settings.TRADE_ENABLED:
            raise MT5TradeNotAllowed("网关交易功能未启用（TRADE_ENABLED=false）")
        self._ensure_initialized_sync()
        terminal = self._mt5.terminal_info()
        account = self._mt5.account_info()
        if terminal is None or account is None:
            code, msg = self._mt5.last_error()
            raise MT5TerminalUnavailable(f"MT5 终端/账户不可用：({code},{msg})")
        allowed = settings.trade_allowed_logins
        login = int(getattr(account, "login", 0) or 0)
        if allowed and login not in allowed:
            raise MT5TradeNotAllowed(f"账户 {login} 不在交易白名单内（灰度限制）")
        if not getattr(terminal, "trade_allowed", False):
            raise MT5TradeNotAllowed("终端禁止自动交易（请开启「允许算法交易」）")
        if not getattr(account, "trade_allowed", False):
            raise MT5TradeNotAllowed("账户禁止交易（只读模式或已被禁用）")

    def _norm_volume_sync(self, upper: str, volume: float) -> tuple[float, Any]:
        """Floor to the symbol volume step; reject out-of-range. Returns (vol, info)."""
        info = self._mt5.symbol_info(upper)
        if info is None:
            raise MT5SymbolNotFound(f"品种不存在：{upper}")
        step = float(getattr(info, "volume_step", 0.0) or 0.0)
        vmin = float(getattr(info, "volume_min", 0.0) or 0.0)
        vmax = float(getattr(info, "volume_max", 0.0) or 0.0)
        digits = int(getattr(info, "volume_digits", 2) or 2)
        if step > 0:
            vol = round(math.floor(volume / step + 1e-9) * step, digits)
        else:
            vol = float(volume)
        if vol <= 0 or vol < vmin or vol > vmax:
            raise MT5Error(
                f"手数 {volume} 归一化后 {vol} 越界 [{vmin}, {vmax}]（步长 {step}）",
                status_code=422,
            )
        return vol, info

    def _filling(self, info: Any) -> int:
        """Pick a supported filling mode from the symbol bitmask."""
        fm = int(getattr(info, "filling_mode", 0) or 0)
        if fm & 1:
            return self._mt5.ORDER_FILLING_FOK
        if fm & 2:
            return self._mt5.ORDER_FILLING_IOC
        return self._mt5.ORDER_FILLING_RETURN

    @staticmethod
    def _trade_result(res: Any) -> dict[str, Any]:
        retcode = int(getattr(res, "retcode", -1) or -1)
        return {
            "retcode": retcode,
            "success": retcode in (10009, 10010),  # done | partfilled
            "retcode_text": RETCODE_TEXT.get(retcode, f"未知返回码 {retcode}"),
            "deal": int(getattr(res, "deal", 0) or 0),
            "order": int(getattr(res, "order", 0) or 0),
            "price": float(getattr(res, "price", 0.0) or 0.0),
            "volume": float(getattr(res, "volume", 0.0) or 0.0),
            "comment": str(getattr(res, "comment", "") or ""),
        }

    def _deviation(self, deviation: int) -> int:
        """Clamp slippage to the gateway cap (never accept an unbounded value)."""
        return max(0, min(int(deviation), settings.TRADE_MAX_DEVIATION))

    def _place_market_sync(
        self, symbol: str, volume: float, side: str,
        magic: int, comment: str, deviation: int,
    ) -> dict[str, Any]:
        self._check_trade_allowed_sync()
        upper = symbol.upper()
        vol, info = self._norm_volume_sync(upper, volume)
        tick = self._mt5.symbol_info_tick(upper)
        if tick is None:
            code, msg = self._mt5.last_error()
            raise MT5Error(f"无当前价格：{upper}（({code},{msg})）", status_code=409)
        is_buy = side == "buy"
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": upper,
            "volume": vol,
            "type": self._mt5.ORDER_TYPE_BUY if is_buy else self._mt5.ORDER_TYPE_SELL,
            "price": float(tick.ask if is_buy else tick.bid),
            "deviation": self._deviation(deviation),
            "magic": int(magic),
            "comment": str(comment)[:31],
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(info),
        }
        res = self._mt5.order_send(request)
        if res is None:
            code, msg = self._mt5.last_error()
            raise MT5Error(f"order_send 调用失败：({code},{msg})")
        out = self._trade_result(res)
        # Slippage-calibration snapshot: the quote the request was priced at.
        out["bid"] = float(tick.bid)
        out["ask"] = float(tick.ask)
        return out

    def _place_pending_sync(
        self, symbol: str, volume: float, order_type: str, price: float,
        sl: float | None, tp: float | None, magic: int, comment: str,
    ) -> dict[str, Any]:
        """Place a limit/stop pending order (TRADE_ACTION_PENDING).

        Price-side sanity (limit must be on the executable side of the current
        quote, stop beyond it) is left to the broker server — its retcode
        (10015/10016/10020...) is surfaced verbatim by ``_trade_result``.
        """
        self._check_trade_allowed_sync()
        upper = symbol.upper()
        vol, info = self._norm_volume_sync(upper, volume)
        type_name = PENDING_TYPES[order_type]
        request: dict[str, Any] = {
            "action": self._mt5.TRADE_ACTION_PENDING,
            "symbol": upper,
            "volume": vol,
            "type": getattr(self._mt5, type_name),
            "price": float(price),
            "magic": int(magic),
            "comment": str(comment)[:31],
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(info),
        }
        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        res = self._mt5.order_send(request)
        if res is None:
            code, msg = self._mt5.last_error()
            raise MT5Error(f"order_send 调用失败：({code},{msg})")
        return self._trade_result(res)

    def _close_position_sync(
        self, ticket: int, volume: float | None, magic: int, deviation: int
    ) -> dict[str, Any]:
        self._check_trade_allowed_sync()
        rows = self._mt5.positions_get(ticket=int(ticket))
        if not rows:
            raise MT5Error(f"持仓不存在：{ticket}", status_code=404)
        pos = rows[0]
        upper = str(pos.symbol)
        pos_vol = float(pos.volume)
        vol, info = self._norm_volume_sync(upper, pos_vol if volume is None else volume)
        vol = min(vol, pos_vol)
        tick = self._mt5.symbol_info_tick(upper)
        if tick is None:
            code, msg = self._mt5.last_error()
            raise MT5Error(f"无当前价格：{upper}（({code},{msg})）", status_code=409)
        closing_buy = int(pos.type) == self._mt5.ORDER_TYPE_BUY
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": upper,
            "volume": vol,
            "type": self._mt5.ORDER_TYPE_SELL if closing_buy else self._mt5.ORDER_TYPE_BUY,
            "position": int(pos.ticket),
            "price": float(tick.bid if closing_buy else tick.ask),
            "deviation": self._deviation(deviation),
            "magic": int(magic),
            "comment": "close",
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(info),
        }
        res = self._mt5.order_send(request)
        if res is None:
            code, msg = self._mt5.last_error()
            raise MT5Error(f"order_send 调用失败：({code},{msg})")
        out = self._trade_result(res)
        # Slippage-calibration snapshot for closes as well.
        out["bid"] = float(tick.bid)
        out["ask"] = float(tick.ask)
        return out

    def _cancel_order_sync(self, ticket: int) -> dict[str, Any]:
        self._check_trade_allowed_sync()
        # MetaTrader5 包没有 TRADE_ACTION_CANCEL — 撤单用 TRADE_ACTION_REMOVE。
        request = {
            "action": self._mt5.TRADE_ACTION_REMOVE,
            "order": int(ticket),
        }
        res = self._mt5.order_send(request)
        if res is None:
            code, msg = self._mt5.last_error()
            raise MT5Error(f"order_send 调用失败：({code},{msg})")
        out = self._trade_result(res)
        return out

    @staticmethod
    def _position_dict(p: Any) -> dict[str, Any]:
        return {
            "ticket": int(p.ticket),
            "symbol": str(p.symbol),
            "type": "buy" if int(p.type) == 0 else "sell",
            "volume": float(p.volume),
            "price_open": float(p.price_open),
            "price_current": float(p.price_current),
            "sl": float(p.sl),
            "tp": float(p.tp),
            "profit": float(p.profit),
            "swap": float(p.swap),
            # 新版 MT5 build 移除了持仓级 commission 字段——缺失时回退 0
            "commission": float(getattr(p, "commission", 0.0) or 0.0),
            "time": int(p.time),
            "magic": int(p.magic),
            "identifier": int(getattr(p, "identifier", p.ticket)),
            "comment": str(getattr(p, "comment", "") or ""),
        }

    def _positions_sync(self, symbol: str | None) -> list[dict[str, Any]]:
        self._ensure_initialized_sync()
        kwargs: dict[str, Any] = {"symbol": symbol.upper()} if symbol else {}
        rows = self._mt5.positions_get(**kwargs) or []
        out = [self._position_dict(p) for p in rows]
        # position.time 是服务器时区伪 epoch → 对齐到 K 线(UTC)轴
        skew = self._trade_axis_skew([str(d["symbol"]) for d in out])
        if skew:
            for d in out:
                if d["time"] > 0:
                    d["time"] = max(0, d["time"] - skew)
        return out

    @staticmethod
    def _order_dict(o: Any) -> dict[str, Any]:
        return {
            "ticket": int(o.ticket),
            "symbol": str(o.symbol),
            "type": int(o.type),
            "state": int(o.state),
            # 新版 MT5 build 的 TradeOrder 无 volume 字段——回退 volume_initial/volume_filled
            "volume": float(
                getattr(o, "volume", None)
                or getattr(o, "volume_filled", None)
                or getattr(o, "volume_initial", 0.0)
                or 0.0
            ),
            "volume_initial": float(getattr(o, "volume_initial", 0.0) or 0.0),
            "price_open": float(getattr(o, "price_open", 0.0) or 0.0),
            "price_current": float(getattr(o, "price_current", 0.0) or 0.0),
            "sl": float(getattr(o, "sl", 0.0) or 0.0),
            "tp": float(getattr(o, "tp", 0.0) or 0.0),
            "time_setup": int(o.time_setup),
            "magic": int(o.magic),
            "comment": str(getattr(o, "comment", "") or ""),
        }

    def _orders_sync(self, symbol: str | None) -> list[dict[str, Any]]:
        self._ensure_initialized_sync()
        kwargs: dict[str, Any] = {"symbol": symbol.upper()} if symbol else {}
        rows = self._mt5.orders_get(**kwargs) or []
        out = [self._order_dict(o) for o in rows]
        # order.time_setup 与 position.time 同轴 → 同样对齐到 K 线(UTC)轴
        skew = self._trade_axis_skew([str(d["symbol"]) for d in out])
        if skew:
            for d in out:
                if d["time_setup"] > 0:
                    d["time_setup"] = max(0, d["time_setup"] - skew)
        return out

    @staticmethod
    def _deal_dict(d: Any) -> dict[str, Any]:
        return {
            "ticket": int(d.ticket),
            "order": int(d.order),
            "time": int(d.time),
            "symbol": str(d.symbol),
            "type": int(d.type),          # 0 buy | 1 sell
            "entry": int(d.entry),        # 0 in | 1 out | 2 inout
            "volume": float(d.volume),
            "price": float(d.price),
            "commission": float(getattr(d, "commission", 0.0) or 0.0),
            "swap": float(d.swap),
            "profit": float(d.profit),
            "balance": float(getattr(d, "balance", 0.0) or 0.0),
            "magic": int(d.magic),
            "comment": str(getattr(d, "comment", "") or ""),
        }

    def _deals_sync(
        self, start: int, end: int, symbol: str | None, position: int | None = None
    ) -> list[dict[str, Any]]:
        self._ensure_initialized_sync()
        if position is not None:
            # 按持仓票号查询：终端历史缓存未按日期同步时仍可靠（服务端直查）
            rows = self._mt5.history_deals_get(position=int(position)) or []
        else:
            date_from = datetime.fromtimestamp(start, UTC)
            date_to = datetime.fromtimestamp(end, UTC)
            rows = self._mt5.history_deals_get(date_from, date_to) or []
        out = [self._deal_dict(d) for d in rows]
        if symbol:
            upper = symbol.upper()
            out = [d for d in out if d["symbol"] == upper]
        return out

    # async wrappers — trade commands jump the queue (Kill Switch ≤3s)
    async def place_market(
        self, symbol: str, volume: float, side: str,
        magic: int = 0, comment: str = "", deviation: int = 20,
    ) -> dict[str, Any]:
        return await self.run(
            self._place_market_sync, symbol, volume, side, magic, comment, deviation,
            priority=PRIORITY_TRADE,
        )

    async def place_pending(
        self, symbol: str, volume: float, order_type: str, price: float,
        sl: float | None = None, tp: float | None = None,
        magic: int = 0, comment: str = "",
    ) -> dict[str, Any]:
        return await self.run(
            self._place_pending_sync, symbol, volume, order_type, price,
            sl, tp, magic, comment,
            priority=PRIORITY_TRADE,
        )

    async def close_position(
        self, ticket: int, volume: float | None = None,
        magic: int = 0, deviation: int = 20,
    ) -> dict[str, Any]:
        return await self.run(
            self._close_position_sync, ticket, volume, magic, deviation,
            priority=PRIORITY_TRADE,
        )

    async def cancel_order(self, ticket: int) -> dict[str, Any]:
        return await self.run(
            self._cancel_order_sync, ticket, priority=PRIORITY_TRADE
        )

    async def positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return await self.run(
            self._positions_sync, symbol, priority=PRIORITY_TRADE
        )

    async def orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        return await self.run(
            self._orders_sync, symbol, priority=PRIORITY_TRADE
        )

    async def deals(
        self, start: int, end: int, symbol: str | None = None,
        position: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self.run(
            self._deals_sync, start, end, symbol, position, priority=PRIORITY_TRADE
        )


# -- module state ----------------------------------------------------------------
START_TIME = time.time()
_daily_cache: dict[str, tuple[float, float, float, float]] = {}

client = MT5Client()
