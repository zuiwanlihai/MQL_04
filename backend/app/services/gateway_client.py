"""
Async HTTP client for the local MT5 gateway service (:8090).

All outbound calls carry the shared ``X-Gateway-Key`` header and use a
tight timeout (10s total / 3s connect) so a hung gateway cannot stall the
API.  Transport-level failures are normalised into
:class:`GatewayUnavailable` carrying an actionable Chinese message.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx

from app.config import settings

_UNAVAILABLE_HINT = (
    "行情网关不可达：{reason}。请在「行情 · 数据管理」页确认网关服务已启动"
)


class GatewayError(Exception):
    """Base gateway error; ``detail`` is a user-facing Chinese message."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class GatewayUnavailable(GatewayError):  # noqa: N818 — name fixed by plan spec
    """The gateway process could not be reached (connect/timeout errors)."""


class GatewayTimeout(GatewayError):  # noqa: N818 — mirrors sibling names
    """A request was sent but the response timed out — outcome UNKNOWN.

    For trade calls this means the broker may have executed the order; the
    caller must reconcile instead of blindly retrying.
    """


class GatewayLoginError(GatewayError):
    """MT5 login was rejected by the gateway (HTTP 400)."""


class GatewaySymbolNotFound(GatewayError):  # noqa: N818 — name fixed by plan spec
    """The requested symbol does not exist at the gateway (HTTP 404)."""


class GatewayTradeNotAllowed(GatewayError):  # noqa: N818 — mirrors sibling names
    """Gateway refuses the trade (feature flag off / whitelist / read-only)."""


class GatewayClient:
    """Thin typed wrapper over the gateway's REST routes."""

    def __init__(self, base_url: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=(base_url or settings.GATEWAY_URL).rstrip("/"),
            headers={"X-Gateway-Key": settings.GATEWAY_API_KEY},
            timeout=httpx.Timeout(settings.GATEWAY_TIMEOUT_SECONDS, connect=3.0),
        )

    # -- lifecycle -----------------------------------------------------------
    async def aclose(self) -> None:
        await self._client.aclose()

    # -- internals -----------------------------------------------------------
    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        # httpx treats timeout=None as "no timeout" — drop it to keep the
        # client default (a hung gateway must never stall the caller).
        if kwargs.get("timeout") is None:
            kwargs.pop("timeout", None)
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            # request (possibly) reached the gateway but no response — for
            # trade calls the outcome is UNKNOWN; reconcile, never retry.
            raise GatewayTimeout(
                f"网关响应超时：{exc.__class__.__name__}，请求结果未知"
            ) from exc
        except httpx.HTTPError as exc:
            reason = str(exc) or exc.__class__.__name__
            raise GatewayUnavailable(_UNAVAILABLE_HINT.format(reason=reason)) from exc

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            detail = body.get("detail")
            if isinstance(detail, str) and detail:
                return detail
        except ValueError:
            pass
        return f"网关返回 HTTP {resp.status_code}"

    # -- routes --------------------------------------------------------------
    async def health(self) -> dict:
        resp = await self._request("GET", "/health")
        if resp.status_code != 200:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def selfcheck(self) -> dict:
        """One-click diagnostics proxied from the gateway (M11).

        The checklist runs several OS probes (powercfg ×2, w32tm) each capped
        at 8s on the gateway, so the default 10s client timeout is too tight —
        allow 30s end-to-end.
        """
        resp = await self._request("GET", "/selfcheck", timeout=30.0)
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def power_fix(self, execute: bool = False) -> dict:
        """Power-plan fix — guidance-only unless ``execute`` (M11)."""
        resp = await self._request("POST", "/power/fix", json={"execute": execute})
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def trade_whitelist(self) -> dict:
        """Current trade whitelist (feature flag + allowed logins)."""
        resp = await self._request("GET", "/trade/whitelist")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def trade_whitelist_add(self, login: int) -> dict:
        """Add an MT5 login to the gateway trade whitelist (persisted)."""
        resp = await self._request("POST", "/trade/whitelist", json={"login": login})
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def trade_whitelist_remove(self, login: int) -> dict:
        """Remove an MT5 login from the gateway trade whitelist (persisted)."""
        resp = await self._request("DELETE", f"/trade/whitelist/{login}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def login(self, login: int, password: str, server: str) -> dict:
        """Authenticate against MT5; returns the account summary dict."""
        resp = await self._request(
            "POST",
            "/account/login",
            json={"login": login, "password": password, "server": server},
        )
        if resp.status_code == 400:
            raise GatewayLoginError(self._error_detail(resp))
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def logout(self) -> None:
        resp = await self._request("POST", "/account/logout")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))

    async def account(self, timeout: float | None = None) -> dict | None:
        """Real-time account snapshot; None when MT5 is not logged in (409)."""
        resp = await self._request("GET", "/account", timeout=timeout)
        if resp.status_code == 409:
            return None
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def symbols(self) -> list[dict]:
        resp = await self._request("GET", "/symbols")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def rates(self, name: str, timeframe: str, limit: int) -> list[dict]:
        resp = await self._request(
            "GET",
            f"/symbols/{name}/rates",
            params={"timeframe": timeframe, "limit": limit},
        )
        if resp.status_code == 404:
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def rates_range(
        self,
        name: str,
        timeframe: str,
        start: int,
        end: int,
        limit: int = 5000,
    ) -> list[dict]:
        """Candles in [start, end) epoch seconds, ascending (backtest slice)."""
        resp = await self._request(
            "GET",
            f"/symbols/{name}/rates/range",
            params={"timeframe": timeframe, "start": start, "end": end, "limit": limit},
        )
        if resp.status_code == 404:
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def ticks_range(
        self,
        name: str,
        start: int,
        end: int,
        limit: int = 100_000,
    ) -> list[dict]:
        """Ticks in [start, end) epoch seconds — time_msc ms ascending rows."""
        resp = await self._request(
            "GET",
            f"/symbols/{name}/ticks/range",
            params={"start": start, "end": end, "limit": limit},
        )
        if resp.status_code == 404:
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def probe_history(
        self, name: str, timeframe: str, timeout: float | None = None
    ) -> dict:
        """Terminal-local cached bar range for a symbol/timeframe (M2)."""
        resp = await self._request(
            "POST",
            f"/symbols/{name}/history/probe",
            json={"timeframe": timeframe},
            timeout=timeout,
        )
        if resp.status_code == 404:
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def history_backfill(
        self,
        name: str,
        timeframe: str,
        start: int,
        end: int,
        max_attempts: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Retry a [start, end) window until the terminal finishes downloading."""
        payload: dict = {"timeframe": timeframe, "start": start, "end": end}
        if max_attempts is not None:
            payload["max_attempts"] = max_attempts
        resp = await self._request(
            "POST",
            f"/symbols/{name}/history/backfill",
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 404:
            raise GatewaySymbolNotFound(f"品种不存在：{name}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def quotes(self, names: list[str]) -> list[dict]:
        resp = await self._request(
            "GET", "/quotes", params={"symbols": ",".join(names)}
        )
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    # -- trading (M6 live execution) ---------------------------------------------
    async def place_market(
        self,
        symbol: str,
        volume: float,
        side: str,
        magic: int = 0,
        comment: str = "",
        deviation: int = 20,
        timeout: float | None = None,
    ) -> dict:
        """Market order via the gateway. 409 → GatewayTradeNotAllowed."""
        resp = await self._request(
            "POST",
            "/trade/order",
            json={
                "symbol": symbol, "volume": volume, "side": side,
                "magic": magic, "comment": comment, "deviation": deviation,
            },
            timeout=timeout,
        )
        if resp.status_code == 409:
            raise GatewayTradeNotAllowed(self._error_detail(resp))
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def place_pending(
        self,
        symbol: str,
        volume: float,
        order_type: str,
        price: float,
        sl: float | None = None,
        tp: float | None = None,
        magic: int = 0,
        comment: str = "",
        timeout: float | None = None,
    ) -> dict:
        """Limit/stop pending order via the gateway. 409 → GatewayTradeNotAllowed."""
        resp = await self._request(
            "POST",
            "/trade/pending",
            json={
                "symbol": symbol, "volume": volume, "order_type": order_type,
                "price": price, "sl": sl, "tp": tp,
                "magic": magic, "comment": comment,
            },
            timeout=timeout,
        )
        if resp.status_code == 409:
            raise GatewayTradeNotAllowed(self._error_detail(resp))
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def close_position(
        self,
        ticket: int,
        volume: float | None = None,
        magic: int = 0,
        deviation: int = 20,
        timeout: float | None = None,
    ) -> dict:
        resp = await self._request(
            "POST",
            "/trade/close",
            json={
                "ticket": ticket, "volume": volume,
                "magic": magic, "deviation": deviation,
            },
            timeout=timeout,
        )
        if resp.status_code == 409:
            raise GatewayTradeNotAllowed(self._error_detail(resp))
        if resp.status_code == 404:
            raise GatewayError(f"持仓不存在：{ticket}")
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def cancel_order(self, ticket: int, timeout: float | None = None) -> dict:
        resp = await self._request(
            "POST", "/trade/cancel", json={"ticket": ticket}, timeout=timeout
        )
        if resp.status_code == 409:
            raise GatewayTradeNotAllowed(self._error_detail(resp))
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def set_terminal_algo(self, enabled: bool, timeout: float | None = None) -> dict:
        """拨动 MT5 终端本体「算法交易」按钮（Ctrl+E 注入，网关侧复读验证）。

        失败（找不到窗口/注入被拦/状态未翻转）→ GatewayError，文案可直接展示。
        """
        resp = await self._request(
            "POST", "/terminal/algo-trading", json={"enabled": enabled}, timeout=timeout,
        )
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def positions(
        self, symbol: str | None = None, timeout: float | None = None
    ) -> list[dict]:
        params = {"symbol": symbol} if symbol else None
        resp = await self._request("GET", "/trade/positions", params=params, timeout=timeout)
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def orders(
        self, symbol: str | None = None, timeout: float | None = None
    ) -> list[dict]:
        params = {"symbol": symbol} if symbol else None
        resp = await self._request("GET", "/trade/orders", params=params, timeout=timeout)
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()

    async def deals(
        self, start: int, end: int, symbol: str | None = None,
        timeout: float | None = None, position: int | None = None,
    ) -> list[dict]:
        params: dict = {"start": start, "end": end}
        if symbol:
            params["symbol"] = symbol
        if position is not None:
            # 按持仓票号直查（终端历史缓存未同步时仍可靠）
            params["position"] = position
        resp = await self._request("GET", "/trade/deals", params=params, timeout=timeout)
        if resp.status_code >= 400:
            raise GatewayError(self._error_detail(resp))
        return resp.json()


async def get_gateway_client() -> AsyncGenerator[GatewayClient, None]:
    """FastAPI dependency — one client per request, always closed."""
    client = GatewayClient()
    try:
        yield client
    finally:
        await client.aclose()
