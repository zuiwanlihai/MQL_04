"""
StrategyContext - the `ctx` object passed to strategy callback functions.

This is a STUB for IDE autocompletion and type checking.
The actual implementation is in backend/app/sandbox/context.py
and is injected into the sandbox process at runtime.

The Context is the ONLY way strategies interact with the outside world:
- Market data access (current/historical bars, quotes)
- Order placement (market buy/sell/close)
- Account info (balance, equity, margin, positions) - READ ONLY
- State persistence (ctx.storage key-value store)
- Logging (ctx.log.info/warn/error)

ALL times are UTC. ALL bar access is limited to current bar and earlier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .errors import ErrorCode


# ─── Data Structures ────────────────────────────────────────────

@dataclass
class Bar:
    """OHLCV bar data for a single period."""
    symbol: str
    period: str           # "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN"
    time: datetime        # Bar open time (UTC)
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: float = 0.0


@dataclass
class Quote:
    """Real-time bid/ask quote."""
    symbol: str
    bid: float
    ask: float
    spread: float
    time: datetime


@dataclass
class Tick:
    """Real-time tick delivered to ``on_tick`` (paper/live only).

    ``last``/``volume`` stay reserved for symbols with last-trade ticks
    (forex quotes carry 0).
    """
    symbol: str
    time: datetime        # Tick time (UTC, millisecond precision source)
    bid: float
    ask: float
    last: float = 0.0
    volume: float = 0.0


@dataclass
class SymbolInfo:
    """Static symbol properties."""
    name: str
    display_name: str
    category: str
    base_currency: str
    quote_currency: str
    digits: int
    point: float
    volume_step: float
    volume_min: float
    volume_max: float
    contract_size: float
    margin_initial: float
    trade_mode: str       # "full", "close_only", "disabled"


@dataclass
class AccountInfo:
    """Current account state (read-only)."""
    balance: float
    equity: float
    margin_free: float
    margin_used: float
    currency: str
    leverage: int


@dataclass
class Position:
    """Current open position."""
    id: str
    symbol: str
    direction: str        # "buy" or "sell"
    volume: float
    open_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float
    swap: float
    commission: float
    opened_at: datetime


@dataclass
class OrderResult:
    """Result of an order placement attempt.

    Attributes:
        success: True if the order was accepted and (for backtests) filled.
        order_id: Position ticket for opens; order/position id for closes.
        error_code: SDK ErrorCode if success is False.
        error_message: Human-readable reason when success is False.
        filled_volume: Actually filled volume (may differ from requested on
            partial fills; always equals requested for this sandbox).
        filled_price: Execution price after spread + slippage + model tier.
        commission: **Only non-zero on close orders.**  ``commission_per_lot``
            is charged once at close (round-trip) by ``AccountSim``; open order
            results always return 0.0 here.  Do not use open.commission to
            estimate total trade cost.
    """
    success: bool
    order_id: Optional[str] = None
    error_code: Optional[ErrorCode] = None
    error_message: Optional[str] = None
    filled_volume: Optional[float] = None
    filled_price: Optional[float] = None
    commission: float = 0.0


# ─── Context Interface ──────────────────────────────────────────

class StrategyContext:
    """
    The context object passed to strategy lifecycle callbacks.

    This is the complete API surface available to strategies.
    Any capability not listed here is NOT available.

    SDK v1.1.0 - Bar-driven core (``on_bar``); ``on_tick`` fires on real
    ticks in paper/live only — the backtest never calls it.
    """

    # ── Runtime metadata ──────────────────────────────────────

    @property
    def mode(self) -> str:
        """Runtime mode: "backtest", "paper", or "live"."""
        ...

    @property
    def sdk_version(self) -> str:
        """SDK version string (e.g., "1.1.0")."""
        ...

    @property
    def strategy_id(self) -> str:
        """Unique strategy instance identifier."""
        ...

    # ── Declaration (call inside on_init ONLY) ─────────────────

    def declare(
        self,
        symbol: str,
        period: str = "H1",
        params: dict[str, object] | None = None,
    ) -> None:
        """
        Declare the symbol, primary period and default parameters.

        Must be called from ``on_init``; re-declaring raises.
        Undeclared symbols are rejected by ``get_bars`` / orders.
        """
        ...

    @property
    def symbol(self) -> str:
        """Declared primary symbol name (empty before ``declare``)."""
        ...

    @property
    def period(self) -> str:
        """Declared primary bar period."""
        ...

    @property
    def params(self) -> dict[str, object]:
        """Declared parameters (mutable during ``on_init`` only)."""
        ...

    # ── Market Data (read-only, bounded) ───────────────────────

    def get_bars(
        self,
        symbol: str,
        period: str,
        count: int = 100,
        *,
        end_time: Optional[datetime] = None,
    ) -> list[Bar]:
        """
        Get historical bars for a symbol.

        Args:
            symbol: Symbol name (must be declared in on_init).
            period: Bar period (M1, M5, M15, M30, H1, H4, D1, W1, MN).
            count: Number of bars to return (max 5000).
            end_time: End time for the query. If None, uses current bar time.

        Returns:
            List of Bar objects sorted by time ascending.

        Raises:
            Returns empty list if symbol not declared or period invalid.
        """
        ...

    def get_quote(self, symbol: str) -> Optional[Quote]:
        """Get current bid/ask quote. Returns None if unavailable.

        During ``on_tick`` (paper/live) the quote reflects the live tick's
        real bid/ask spread; otherwise it is derived from the current bar.
        """
        ...

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        """Get static symbol properties."""
        ...

    # ── Account Info (read-only, strategy's bound account only) ─

    def get_account(self) -> AccountInfo:
        """Get current account state."""
        ...

    def get_positions(self) -> list[Position]:
        """Get all current open positions for the strategy's account."""
        ...

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get a specific position by ID."""
        ...

    # ── Order Placement ────────────────────────────────────────

    def buy(
        self,
        symbol: str,
        volume: float,
        *,
        comment: str = "",
    ) -> OrderResult:
        """
        Place a market buy order.

        Args:
            symbol: Trading symbol.
            volume: Desired volume (will be normalized to lot step).
            comment: Optional comment for the order.

        Returns:
            OrderResult with success status and fill details or error code.
        """
        ...

    def sell(
        self,
        symbol: str,
        volume: float,
        *,
        comment: str = "",
    ) -> OrderResult:
        """
        Place a market sell order (open short).

        Args:
            symbol: Trading symbol.
            volume: Desired volume (will be normalized to lot step).
            comment: Optional comment for the order.

        Returns:
            OrderResult with success status and fill details or error code.
        """
        ...

    def close(
        self,
        position_id: str,
        *,
        volume: Optional[float] = None,
        comment: str = "",
    ) -> OrderResult:
        """
        Close (or reduce) an open position.

        Args:
            position_id: ID of the position to close.
            volume: Volume to close. If None, closes the entire position.
            comment: Optional comment.

        Returns:
            OrderResult with success status and fill details or error code.
        """
        ...

    # ── State Persistence ──────────────────────────────────────

    @property
    def storage(self) -> "StrategyStorage":
        """
        Key-value store for strategy state persistence.

        Data persists across bar events, strategy restarts, and
        between backtest/paper/live modes. Quota: 1MB.

        Usage:
            ctx.storage["last_signal"] = "buy"
            value = ctx.storage.get("last_signal", "none")
            del ctx.storage["old_key"]
        """
        ...

    # ── Logging ────────────────────────────────────────────────

    @property
    def log(self) -> "StrategyLogger":
        """
        Structured logging interface.

        Usage:
            ctx.log.info(f"Signal: {signal_value}")
            ctx.log.warn("Position size approaching limit")
            ctx.log.error(f"Unexpected state: {state}")
        """
        ...

    # ── Time ───────────────────────────────────────────────────

    @property
    def now(self) -> datetime:
        """Current time in the strategy's clock (UTC)."""
        ...

    @property
    def current_bar(self) -> Optional[Bar]:
        """The bar currently being processed."""
        ...


# ─── Storage Interface ─────────────────────────────────────────

class StrategyStorage:
    """Key-value store with dict-like interface, 1MB quota."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key."""
        ...

    def __getitem__(self, key: str) -> Any:
        ...

    def __setitem__(self, key: str, value: Any) -> None:
        """Set a value. Raises if quota exceeded."""
        ...

    def __delitem__(self, key: str) -> None:
        ...

    def __contains__(self, key: str) -> bool:
        ...

    def keys(self):
        """Return all stored keys."""
        ...

    def clear(self) -> None:
        """Remove all stored data."""
        ...

    @property
    def size_bytes(self) -> int:
        """Current storage size in bytes."""
        ...


# ─── Logger Interface ──────────────────────────────────────────

class StrategyLogger:
    """Structured logger for strategy output."""

    def info(self, message: str) -> None:
        """Log informational message."""
        ...

    def warn(self, message: str) -> None:
        """Log warning message."""
        ...

    def error(self, message: str) -> None:
        """Log error message."""
        ...