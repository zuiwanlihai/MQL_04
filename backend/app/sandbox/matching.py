"""Three-tier fill model + simulated account (M4, PRD §撮合假设).

MT5 bars are **bid-denominated**: ``open/high/low/close`` are bid prices and
``spread`` is in points.  A buy crosses the spread (pays ask = bid +
spread·point); a sell receives the bid.  Slippage is always adverse.

Fill model per tier (``side`` = order direction):

    conservative  base = NEXT bar's open (signal fires at current close).
                  buy  = base + spread + slip;  sell = base − slip.
                  An order on the final bar is rejected MARKET_CLOSED.
    standard      base = current close, same side convention.
    optimistic    price = current close, zero spread/slippage.

Every fill records its breakdown (base/spread/slippage) so the trade table
can show the four-component execution price (成交构成).

``AccountSim`` is a hedging account: buy and sell positions coexist, margin
is ``contract_size × volume × price / leverage``, free-margin shortfalls
return INSUFFICIENT_MARGIN (business error, strategy continues), and a
margin level below ``stop_out_level`` force-closes the biggest losers first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from sdk.errors import ErrorCode

# Bar period → seconds (metrics need bars-per-year; mirrors Timeframe enum).
TF_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
    "MN": 2592000,
}

OPTIMISTIC = "optimistic"
STANDARD = "standard"
CONSERVATIVE = "conservative"


@dataclass
class Fill:
    """One execution price with its cost breakdown (absolute price units)."""

    price: float
    base_price: float
    spread_cost: float
    slippage_cost: float


@dataclass
class MatchResult:
    """Outcome of an order attempt — mirrors OrderResult fields."""

    success: bool
    error_code: ErrorCode | None = None
    error_message: str | None = None
    fill: Fill | None = None
    position_id: str | None = None
    volume: float = 0.0
    profit: float = 0.0
    trade: dict | None = None  # completed round trip (close only)


class FillModel:
    """Computes execution prices for the configured tier."""

    def __init__(
        self,
        mode: str,
        point: float,
        spread_mode: str,
        fixed_spread_points: float,
        spread_snapshot: float,
        slippage_points: float,
    ) -> None:
        self.mode = mode
        self.point = point
        self.spread_mode = spread_mode
        self.fixed_spread_points = fixed_spread_points
        self.spread_snapshot = spread_snapshot
        self.slippage_points = slippage_points

    def spread_points_at(self, bar_spread: int) -> float:
        """Effective spread (points) for a bar; 0 in optimistic tier."""
        if self.mode == OPTIMISTIC:
            return 0.0
        if self.spread_mode == "fixed":
            return self.fixed_spread_points
        pts = bar_spread if bar_spread > 0 else self.spread_snapshot
        return float(pts)

    def fill_for(
        self,
        side: str,
        cur_close: float,
        next_open: float | None,
        next_spread_pts: int,
        cur_spread_pts: int,
    ) -> MatchResult:
        """Execution price for a buy/sell order.

        ``next_open``/``next_spread_pts`` are only used by the conservative
        tier; pass None when the current bar is the last one.
        """
        if self.mode == OPTIMISTIC:
            return MatchResult(
                success=True,
                fill=Fill(price=cur_close, base_price=cur_close,
                          spread_cost=0.0, slippage_cost=0.0),
            )

        if self.mode == CONSERVATIVE:
            if next_open is None:
                return MatchResult(
                    success=False,
                    error_code=ErrorCode.MARKET_CLOSED,
                    error_message="最后一根 K 线无法在次根开盘成交，挂单被拒绝",
                )
            base = next_open
            spread_p = self.spread_points_at(next_spread_pts) * self.point
        else:  # STANDARD
            base = cur_close
            spread_p = self.spread_points_at(cur_spread_pts) * self.point

        slip_p = self.slippage_points * self.point
        if side == "buy":
            price = base + spread_p + slip_p
            return MatchResult(
                success=True,
                fill=Fill(price=price, base_price=base,
                          spread_cost=spread_p, slippage_cost=slip_p),
            )
        price = base - slip_p
        return MatchResult(
            success=True,
            fill=Fill(price=price, base_price=base,
                      spread_cost=0.0, slippage_cost=slip_p),
        )


@dataclass
class SimPosition:
    """An open hedging position inside the account simulator."""

    id: str
    ticket: int
    direction: str  # "buy" | "sell"
    volume: float
    open_price: float
    base_price: float
    spread_cost: float
    slippage_cost: float
    open_time: int  # epoch seconds (signal bar close)
    comment: str = ""
    # Overnight swap accrued so far, attributed to the *remaining* volume
    # (pro-rata portions are moved out on partial closes).
    swap_accrued: float = 0.0

    def unrealized(self, bid_close: float, contract_size: float) -> float:
        if self.direction == "buy":
            return (bid_close - self.open_price) * contract_size * self.volume
        return (self.open_price - bid_close) * contract_size * self.volume


@dataclass
class AccountSim:
    """Margin/hedging account driven by bar closes (bid prices)."""

    initial_deposit: float
    leverage: int
    contract_size: float
    point: float
    volume_step: float
    volume_min: float
    volume_max: float
    commission_per_lot: float
    stop_out_level: float
    # Overnight swap in points per lot per night (MT5 convention, from the
    # gateway symbol info).  0 disables accrual (legacy payloads).
    swap_long: float = 0.0
    swap_short: float = 0.0

    balance: float = field(init=False)
    positions: dict[str, SimPosition] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    total_swap: float = field(default=0.0)  # cumulative swap COST (positive)
    _next_pos: int = field(default=1, init=False)
    _next_ticket: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        self.balance = self.initial_deposit

    # ── margin helpers ─────────────────────────────────────────

    def margin_for(self, volume: float, price: float) -> float:
        return self.contract_size * volume * price / self.leverage

    def margin_used(self, bid_close: float) -> float:
        return sum(self.margin_for(p.volume, bid_close) for p in self.positions.values())

    def unrealized_pnl(self, bid_close: float) -> float:
        return sum(p.unrealized(bid_close, self.contract_size) for p in self.positions.values())

    def equity(self, bid_close: float) -> float:
        return self.balance + self.unrealized_pnl(bid_close)

    def margin_level(self, bid_close: float) -> float | None:
        used = self.margin_used(bid_close)
        if used <= 0:
            return None
        return self.equity(bid_close) / used * 100.0

    def free_margin(self, bid_close: float) -> float:
        return self.equity(bid_close) - self.margin_used(bid_close)

    # ── overnight swap (P1 隔夜利息) ─────────────────────────────

    def accrue_swap(self, bar_time: int, nights: int) -> float:
        """Charge ``nights`` nights of swap on every open position.

        Called by the engine at the first bar of each new UTC day, *before*
        the strategy callback — a position opened during that bar is not
        charged for its opening night (matches broker rollover behaviour).

        Charge per night = swap_points × point × contract_size × volume
        (account currency, signed: positive rate credits, negative debits).
        The signed charge hits the balance immediately (MT5 daily deduction)
        and accumulates on the position; on close it is attributed to the
        trade's ``swap`` column for display — the trade ``profit`` itself
        stays the price P&L net of commission (MT5-native semantics).
        Returns the net charged amount (negative = cost).
        """
        if nights <= 0 or (self.swap_long == 0.0 and self.swap_short == 0.0):
            return 0.0
        net = 0.0
        for pos in self.positions.values():
            rate = self.swap_long if pos.direction == "buy" else self.swap_short
            if rate == 0.0:
                continue
            charge = rate * nights * self.point * self.contract_size * pos.volume
            pos.swap_accrued += charge  # signed accumulation (negative = cost)
            self.balance += charge
            net += charge
        self.total_swap += -net  # cumulative cost, positive number
        return net

    # ── volume normalisation ───────────────────────────────────

    def normalize_volume(self, volume: float) -> tuple[float, MatchResult | None]:
        """Floor to volume_step; reject out-of-range. Returns (vol, error)."""
        if self.volume_step > 0:
            vol = round(math.floor(volume / self.volume_step + 1e-9) * self.volume_step, 8)
        else:
            vol = volume
        if vol < self.volume_min or vol > self.volume_max or vol <= 0:
            return vol, MatchResult(
                success=False,
                error_code=ErrorCode.INVALID_VOLUME,
                error_message=(
                    f"手数 {volume} 归一化后 {vol} 越界 "
                    f"[{self.volume_min}, {self.volume_max}]，步长 {self.volume_step}"
                ),
            )
        return vol, None

    # ── order application ──────────────────────────────────────

    def open_position(
        self, side: str, fill: Fill, volume: float, bar_time: int, comment: str
    ) -> MatchResult:
        pos_id = str(self._next_pos)
        self._next_pos += 1
        ticket = self._next_ticket
        self._next_ticket += 1
        self.positions[pos_id] = SimPosition(
            id=pos_id,
            ticket=ticket,
            direction=side,
            volume=volume,
            open_price=fill.price,
            base_price=fill.base_price,
            spread_cost=fill.spread_cost,
            slippage_cost=fill.slippage_cost,
            open_time=bar_time,
            comment=comment,
        )
        return MatchResult(
            success=True, fill=fill, position_id=pos_id, volume=volume
        )

    def close_position(
        self,
        pos_id: str,
        fill: Fill,
        bar_time: int,
        volume: float | None = None,
        comment: str = "",
        is_forced: bool = False,
    ) -> MatchResult:
        pos = self.positions.get(pos_id)
        if pos is None:
            return MatchResult(
                success=False,
                error_code=ErrorCode.POSITION_NOT_FOUND,
                error_message=f"持仓 {pos_id} 不存在",
            )
        close_vol = pos.volume if volume is None else min(volume, pos.volume)
        if close_vol <= 0:
            return MatchResult(
                success=False,
                error_code=ErrorCode.INVALID_VOLUME,
                error_message="平仓手数必须为正",
            )
        # P1-5: 非 volume_step 倍数的平仓手数必须明确拒绝，不得静默截断。
        # 开仓时 normalize_volume 会 floor 到 step（MT5 截断语义），但平仓
        # 时策略传入非 step 倍数意味着逻辑错误，应该暴露。
        if volume is not None and self.volume_step > 0:
            ratio = close_vol / self.volume_step
            if abs(round(ratio) - ratio) > 1e-6:
                return MatchResult(
                    success=False,
                    error_code=ErrorCode.INVALID_VOLUME,
                    error_message=(
                        f"平仓手数 {close_vol} 不是 volume_step={self.volume_step} 的整数倍"
                    ),
                )
        if pos.direction == "buy":
            pnl = (fill.price - pos.open_price) * self.contract_size * close_vol
        else:
            pnl = (pos.open_price - fill.price) * self.contract_size * close_vol
        commission = self.commission_per_lot * close_vol
        # Pro-rata share of the position's accrued swap (signed, ≤0 = cost)
        # is attributed to this trade for display; the balance was already
        # adjusted at each daily accrual, so profit excludes swap here.
        swap_portion = pos.swap_accrued * close_vol / pos.volume
        pos.swap_accrued -= swap_portion
        profit = pnl - commission
        self.balance += profit
        margin_at_open = self.margin_for(close_vol, pos.open_price)
        profit_percent = profit / margin_at_open * 100.0 if margin_at_open > 0 else 0.0

        trade = {
            "ticket": pos.ticket,
            "direction": pos.direction,
            "volume": close_vol,
            "open_time": pos.open_time,
            "open_price": pos.open_price,
            "base_price": pos.base_price,
            "close_time": bar_time,
            "close_price": fill.price,
            "profit": profit,
            "profit_percent": profit_percent,
            "swap": swap_portion,
            "commission": commission,
            "spread_cost": pos.spread_cost + fill.spread_cost,
            "slippage_cost": pos.slippage_cost + fill.slippage_cost,
            "comment": comment or pos.comment,
            "is_forced": is_forced,
        }
        self.trades.append(trade)

        pos.volume = round(pos.volume - close_vol, 8)
        if pos.volume <= 0:
            del self.positions[pos_id]
        return MatchResult(
            success=True, fill=fill, position_id=pos_id,
            volume=close_vol, profit=profit, trade=trade,
        )

    # ── stop-out ───────────────────────────────────────────────

    def check_stop_out(
        self, bid_close: float, bar_time: int, close_fill_for: "Callable[..., Any]"
    ) -> list[MatchResult]:
        """Force-close the biggest losers until margin level recovers.

        ``close_fill_for(direction)`` returns a MatchResult from the FillModel
        for closing that side at the current bar.
        """
        forced: list[MatchResult] = []
        level = self.margin_level(bid_close)
        if level is None or level >= self.stop_out_level:
            return forced
        while self.positions:
            losers = sorted(
                self.positions.values(),
                key=lambda p: p.unrealized(bid_close, self.contract_size),
            )
            pos = losers[0]
            result = close_fill_for(pos.direction)
            if not result.success or result.fill is None:
                break
            res = self.close_position(
                pos.id, result.fill, bar_time,
                comment="stop-out", is_forced=True,
            )
            if res.success:
                forced.append(res)
            level = self.margin_level(bid_close)
            if level is None or level >= self.stop_out_level:
                break
        return forced

    def force_close_all(
        self, bar_time: int, close_fill_for: "Callable[..., Any]"
    ) -> list[MatchResult]:
        """End-of-run forced close at the final bar's close price."""
        out: list[MatchResult] = []
        for pos_id in sorted(self.positions, key=lambda k: int(k)):
            pos = self.positions[pos_id]
            result = close_fill_for(pos.direction)
            if not result.success or result.fill is None:
                continue
            res = self.close_position(
                pos_id, result.fill, bar_time,
                comment="end-of-run", is_forced=True,
            )
            if res.success:
                out.append(res)
        return out
