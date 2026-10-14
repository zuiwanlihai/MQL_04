"""
Map raw gateway/MT5 dicts onto the frontend Symbol/Quote/PriceBar shapes.

Every field uses ``getattr(..., default)``-style defensive reads so a
schema mismatch with the gateway degrades to defaults instead of a 500.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.schemas.market import PriceBarOut, QuoteOut, SymbolOut


def _f(value: object, default: float = 0.0) -> float:
    """Best-effort float conversion (gateway may emit None/str)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)


def _i(value: object, default: int = 0) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return int(default)


def _map_group(path: str, name: str) -> str:
    p = (path or "").lower()
    if "forex" in p or " fx" in p or p == "fx":
        return "forex"
    if "metal" in p or "commodit" in p:
        return "commodities"
    if "index" in p or "indice" in p:
        return "indices"
    if "crypto" in p or "digital" in p:
        return "crypto"
    if "stock" in p or "etf" in p or "share" in p:
        return "stocks"
    # Fallback: classic 6-letter pair (EURUSD) reads as forex.
    return "forex" if re.fullmatch(r"[A-Za-z]{6}", name or "") else "stocks"


def _session(value: object) -> str:
    """MT5 session times arrive as HHMM ints — render 'HH:MM'; '' if absent."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.isdigit() and len(text) <= 4:
        text = text.zfill(4)  # 100 → 0100 (01:00)
        return f"{int(text[:2]):02d}:{int(text[2:]):02d}"
    if re.fullmatch(r"\d{2}:\d{2}", text):
        return text
    return ""


def _split_currencies(name: str, raw: dict) -> tuple[str, str]:
    base = str(raw.get("currency_base") or "")
    quote_ccy = str(raw.get("currency_profit") or "")
    if not base and re.fullmatch(r"[A-Za-z]{6}", name):
        base, quote_ccy = name[:3].upper(), name[3:].upper()
    return base, quote_ccy


def _iso(epoch: object) -> str:
    """Epoch seconds (or ISO passthrough) → ISO string; empty when missing."""
    if isinstance(epoch, str) and epoch:
        return epoch
    ts = _f(epoch, 0.0)
    if ts <= 0:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def map_symbol(raw: dict) -> SymbolOut:
    name = str(raw.get("name") or raw.get("symbol") or "")
    path = str(raw.get("path") or "")
    base, quote = _split_currencies(name, raw)
    point = _f(raw.get("point"), 0.0)
    trade_mode = _i(raw.get("trade_mode"), -1)

    return SymbolOut(
        id=name,
        name=name,
        description=str(raw.get("description") or name),
        group=_map_group(path, name),  # type: ignore[arg-type]
        base_currency=base,
        quote_currency=quote,
        digits=_i(raw.get("digits")),
        point=point,
        spread=_i(raw.get("spread")),
        tick_size=_f(raw.get("trade_tick_size"), point),
        tick_value=_f(raw.get("trade_tick_value")),
        contract_size=_f(raw.get("trade_contract_size")),
        min_volume=_f(raw.get("volume_min")),
        max_volume=_f(raw.get("volume_max")),
        volume_step=_f(raw.get("volume_step")),
        swap_long=_f(raw.get("swap_long")),
        swap_short=_f(raw.get("swap_short")),
        margin_percent=_f(raw.get("margin_initial")),
        is_trade_allowed=trade_mode == 0,
        is_active=bool(raw.get("visible", True)),
        session_open=_session(raw.get("session_open")),
        session_close=_session(raw.get("session_close")),
    )


def map_quote(raw: dict) -> QuoteOut:
    name = str(raw.get("symbol") or raw.get("name") or "")
    bid = _f(raw.get("bid"))
    ask = _f(raw.get("ask"))
    last = _f(raw.get("last")) or bid
    point = _f(raw.get("point"), 0.0) or 1e-5
    prev_close = _f(raw.get("prev_close"))

    change = (last - prev_close) if prev_close else 0.0
    change_pct = (change / prev_close * 100.0) if prev_close else 0.0
    spread_points = round((ask - bid) / point) if point else 0

    return QuoteOut(
        symbol_id=name,
        symbol_name=str(raw.get("description") or name),
        bid=bid,
        ask=ask,
        last=last,
        change=change,
        change_percent=change_pct,
        spread=spread_points,
        time=_iso(raw.get("time")),
        bar_time=_iso(raw["bar_time"]) if raw.get("bar_time") else None,
        high_day=_f(raw.get("high_day"), _f(raw.get("high"))),
        low_day=_f(raw.get("low_day"), _f(raw.get("low"))),
        volume=_f(raw.get("volume")),
    )


def map_bar(raw: dict) -> PriceBarOut:
    return PriceBarOut(
        time=_i(raw.get("time")),
        open=_f(raw.get("open")),
        high=_f(raw.get("high")),
        low=_f(raw.get("low")),
        close=_f(raw.get("close")),
        tick_volume=_i(raw.get("tick_volume")),
        spread=_i(raw.get("spread")),
        real_volume=_i(raw.get("real_volume")),
    )
