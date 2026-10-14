"""One-click self-check (M11, PRD M1): terminal / broker / power / time / disk.

Runs a closed list of probes and returns a per-item report the Web UI renders
as a checklist.  MT5-backed probes go through the single-worker queue
(``client.run``) like every other terminal call; OS probes run on a worker
thread and degrade to ``warn`` (never crash) when the command is unavailable.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.power import fix_commands, probe_power_plan, smart_decode

GATEWAY_VERSION = "1.0.0"
_DISK_MIN_BYTES = 1 * 1024**3  # 1 GB
_W32TM_TIMEOUT = 8.0
_START = time.time()


def _item(key: str, label: str, status: str, detail: str = "",
          fix_hint: str = "", fix_commands_list: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "label": label, "status": status,
                           "detail": detail, "fix_hint": fix_hint}
    if fix_commands_list:
        out["fix_commands"] = fix_commands_list
    return out


# ── MT5 probes (single sync job on the worker thread) ───────────────────────

def _mt5_probe_sync(mt5: Any) -> dict[str, Any]:
    """One queue job collecting every terminal-facing observation."""
    res: dict[str, Any] = {"mt5_available": mt5 is not None}
    if mt5 is None:
        return res
    terminal = mt5.terminal_info()
    res["terminal_present"] = terminal is not None
    if terminal is not None:
        res["connected"] = bool(getattr(terminal, "connected", False))
        res["trade_allowed"] = bool(getattr(terminal, "trade_allowed", False))
        res["build"] = str(getattr(terminal, "build", "") or "")
    account = mt5.account_info()
    res["logged_in"] = account is not None and int(getattr(account, "login", 0) or 0) > 0
    if account is not None:
        res["login"] = int(getattr(account, "login", 0) or 0)
        res["server"] = str(getattr(account, "server", "") or "")
    return res


def _w32tm_sync() -> tuple[int, str]:
    try:
        proc = subprocess.run(["w32tm", "/query", "/status"], capture_output=True,
                              timeout=_W32TM_TIMEOUT)
        return proc.returncode, smart_decode((proc.stdout or b"") + (proc.stderr or b""))
    except FileNotFoundError:
        return -1, "w32tm 不存在"
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


async def run_selfcheck(client: Any) -> dict[str, Any]:
    """Full checklist.  ``client`` is the module-level MT5Client singleton."""
    items: list[dict[str, Any]] = []

    # -- MT5 terminal group (one queued job) --
    try:
        probe = await client.run(
            lambda: _mt5_probe_sync(client._mt5))  # noqa: SLF001 — same package
    except Exception as exc:  # queue dead / shutdown
        probe = {"mt5_available": False, "error": str(exc)[:120]}

    if not probe.get("mt5_available"):
        items.append(_item(
            "terminal_process", "MT5 终端进程", "fail",
            probe.get("error", "MetaTrader5 Python 包不可用"),
            "确认终端已安装且路径正确（gateway/.env 的 MT5_PATH）"))
        for key, label in (("terminal_login", "终端已登录券商"),
                           ("broker_connected", "券商服务器连接"),
                           ("algo_trade", "允许算法交易")):
            items.append(_item(key, label, "fail", "终端不可达，无法检查", ""))
    else:
        if probe.get("terminal_present"):
            items.append(_item("terminal_process", "MT5 终端进程", "pass",
                               f"build {probe.get('build') or '—'}"))
        else:
            items.append(_item("terminal_process", "MT5 终端进程", "fail",
                               "terminal_info() 不可达（终端未运行？守护循环将尝试拉起）",
                               "手动启动 MT5 终端，或等待自动拉起"))

        if probe.get("logged_in"):
            items.append(_item("terminal_login", "终端已登录券商", "pass",
                               f"{probe.get('login')} @ {probe.get('server')}"))
        else:
            items.append(_item("terminal_login", "终端已登录券商", "fail",
                               "终端未登录任何账户",
                               "检查 gateway/.env 的 MT5_LOGIN/PASSWORD/SERVER 后重启网关"))

        if probe.get("connected"):
            items.append(_item("broker_connected", "券商服务器连接", "pass", "已连接"))
        else:
            items.append(_item("broker_connected", "券商服务器连接", "fail",
                               "终端显示未连接券商服务器",
                               "检查网络与券商服务器状态（右下角连接图标）"))

        if probe.get("trade_allowed"):
            items.append(_item("algo_trade", "允许算法交易", "pass", "终端已开启自动交易"))
        else:
            items.append(_item("algo_trade", "允许算法交易", "fail",
                               "终端未勾选「允许算法交易」——实盘下单将被全部拒绝",
                               "MT5 终端：工具 → 选项 → 专家 → 勾选「允许算法交易」"))

    # -- power plan --
    power = await probe_power_plan()
    if power["ok"] is True:
        items.append(_item("power_plan", "电源方案（接通电源不休眠）", "pass", power["detail"]))
    elif power["ok"] is None:
        items.append(_item("power_plan", "电源方案（接通电源不休眠）", "warn",
                           power["detail"], "无法读取电源配置，请手动确认"))
    else:
        items.append(_item("power_plan", "电源方案（接通电源不休眠）", "fail",
                           power["detail"] + " —— 休眠会导致平台风控失联（风险真空）",
                           "使用「一键修复」生成并执行电源命令", fix_commands()))

    # -- time sync --
    rc, out = await asyncio.to_thread(_w32tm_sync)
    if rc != 0:
        items.append(_item("time_sync", "系统时间同步", "warn", f"w32tm 查询失败：{out[:100]}",
                           "非 Windows 环境或 w32tm 不可用，请手动确认时间准确"))
    elif "N/A" in out or "n/a" in out.lower():
        items.append(_item("time_sync", "系统时间同步", "warn", "尚未成功进行过时间同步",
                           "以管理员执行：w32tm /resync", ["w32tm /resync"]))
    else:
        import re

        m = re.search(r"(?:Last Successful Time Sync|上次成功同步时间)\s*[:：]\s*(.+)", out)
        items.append(_item("time_sync", "系统时间同步", "pass",
                           f"最近成功同步：{m.group(1).strip()}" if m else "已同步"))

    # -- gateway version (display only) --
    items.append(_item("gateway_version", "网关版本", "pass",
                       f"v{GATEWAY_VERSION} · 已运行 {int(time.time() - _START)}s"))

    # -- disk space --
    try:
        usage = shutil.disk_usage(settings.RUNTIME_DIR_ROOT)
        free_gb = usage.free / 1024**3
        if usage.free >= _DISK_MIN_BYTES:
            items.append(_item("disk_space", "磁盘剩余空间", "pass", f"{free_gb:.1f} GB"))
        else:
            items.append(_item("disk_space", "磁盘剩余空间", "warn", f"{free_gb:.1f} GB（<1GB）",
                               "清理磁盘，避免数据同步与日志写入失败"))
    except Exception as exc:  # noqa: BLE001
        items.append(_item("disk_space", "磁盘剩余空间", "warn", f"查询失败：{exc}", ""))

    all_pass = all(i["status"] == "pass" for i in items)
    return {"all_pass": all_pass, "checked_at": datetime.now(UTC).isoformat(),
            "items": items}
