"""
Built-in technical indicators for Strategy SDK v1.

All indicators are pure functions (stateless).
Input: list of float values or OHLC data
Output: list of float values (same length as input, NaN for insufficient data)

Closed list per PRD M3:
    SMA, EMA, WMA, MACD, RSI, KDJ, BOLL, ATR, CCI, ADX, OBV
"""

from __future__ import annotations

import math
from typing import Optional


def _nan_list(length: int) -> list[float]:
    return [float("nan")] * length


# ─── Moving Averages ───────────────────────────────────────────

def sma(data: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    result = _nan_list(len(data))
    if len(data) < period:
        return result
    window_sum = sum(data[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(data)):
        window_sum += data[i] - data[i - period]
        result[i] = window_sum / period
    return result


def ema(data: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    result = _nan_list(len(data))
    if len(data) < period:
        return result
    multiplier = 2.0 / (period + 1)
    # First EMA value is SMA
    result[period - 1] = sum(data[:period]) / period
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    return result


def wma(data: list[float], period: int) -> list[float]:
    """Weighted Moving Average."""
    result = _nan_list(len(data))
    if len(data) < period:
        return result
    weight_sum = period * (period + 1) / 2
    for i in range(period - 1, len(data)):
        weighted = sum(
            data[i - period + 1 + j] * (j + 1) for j in range(period)
        )
        result[i] = weighted / weight_sum
    return result


# ─── MACD ──────────────────────────────────────────────────────

def macd(
    data: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float], list[float]]:
    """
    MACD indicator.

    Returns:
        (macd_line, signal_line, histogram)
        histogram = macd_line - signal_line
    """
    ema_fast = ema(data, fast)
    ema_slow = ema(data, slow)

    macd_line = _nan_list(len(data))
    for i in range(len(data)):
        if not math.isnan(ema_fast[i]) and not math.isnan(ema_slow[i]):
            macd_line[i] = ema_fast[i] - ema_slow[i]

    signal_line = ema([0.0 if math.isnan(v) else v for v in macd_line], signal)
    # Fix: ema with NaN-aware start
    valid_macd = [0.0 if math.isnan(v) else v for v in macd_line]
    signal_line = ema(valid_macd, signal)
    # Restore NaN for pre-signal period
    first_valid = slow + signal - 2
    for i in range(first_valid):
        signal_line[i] = float("nan")

    histogram = _nan_list(len(data))
    for i in range(len(data)):
        if not math.isnan(macd_line[i]) and not math.isnan(signal_line[i]):
            histogram[i] = macd_line[i] - signal_line[i]

    return macd_line, signal_line, histogram


# ─── RSI ───────────────────────────────────────────────────────

def rsi(data: list[float], period: int = 14) -> list[float]:
    """Relative Strength Index (Wilder's smoothing)."""
    result = _nan_list(len(data))
    if len(data) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, len(data)):
        delta = data[i] - data[i - 1]
        gains.append(delta if delta > 0 else 0.0)
        losses.append(-delta if delta < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


# ─── KDJ ───────────────────────────────────────────────────────

def kdj(
    high: list[float],
    low: list[float],
    close: list[float],
    period: int = 9,
    k_period: int = 3,
    d_period: int = 3,
) -> tuple[list[float], list[float], list[float]]:
    """
    KDJ Stochastic indicator.

    Returns:
        (k_line, d_line, j_line)
    """
    n = len(close)
    k_line = _nan_list(n)
    d_line = _nan_list(n)
    j_line = _nan_list(n)

    if n < period:
        return k_line, d_line, j_line

    rsv = _nan_list(n)
    for i in range(period - 1, n):
        hh = max(high[i - period + 1 : i + 1])
        ll = min(low[i - period + 1 : i + 1])
        if hh == ll:
            rsv[i] = 50.0
        else:
            rsv[i] = (close[i] - ll) / (hh - ll) * 100.0

    # First K/D/J values use 50 as initial
    start = period - 1
    k_line[start] = 50.0
    d_line[start] = 50.0

    for i in range(start + 1, n):
        if not math.isnan(rsv[i]):
            k_line[i] = (k_period - 1) / k_period * k_line[i - 1] + rsv[i] / k_period
            d_line[i] = (d_period - 1) / d_period * d_line[i - 1] + k_line[i] / d_period
        else:
            k_line[i] = k_line[i - 1]
            d_line[i] = d_line[i - 1]
        j_line[i] = 3 * k_line[i] - 2 * d_line[i]

    return k_line, d_line, j_line


# ─── Bollinger Bands ───────────────────────────────────────────

def boll(
    data: list[float],
    period: int = 20,
    deviations: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    """
    Bollinger Bands.

    Returns:
        (middle_band, upper_band, lower_band)
        middle = SMA(period)
        upper = middle + deviations * std
        lower = middle - deviations * std
    """
    n = len(data)
    middle = sma(data, period)
    upper = _nan_list(n)
    lower = _nan_list(n)

    for i in range(period - 1, n):
        window = data[i - period + 1 : i + 1]
        mean = middle[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + deviations * std
        lower[i] = mean - deviations * std

    return middle, upper, lower


# ─── ATR ───────────────────────────────────────────────────────

def atr(
    high: list[float],
    low: list[float],
    close: list[float],
    period: int = 14,
) -> list[float]:
    """Average True Range (Wilder's smoothing)."""
    n = len(close)
    result = _nan_list(n)
    if n < period + 1:
        return result

    tr = _nan_list(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )

    # First ATR is simple average
    result[period] = sum(tr[1 : period + 1]) / period
    for i in range(period + 1, n):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period

    return result


# ─── CCI ───────────────────────────────────────────────────────

def cci(
    high: list[float],
    low: list[float],
    close: list[float],
    period: int = 20,
) -> list[float]:
    """Commodity Channel Index."""
    n = len(close)
    result = _nan_list(n)
    if n < period:
        return result

    tp = [(high[i] + low[i] + close[i]) / 3.0 for i in range(n)]
    tp_sma = sma(tp, period)

    for i in range(period - 1, n):
        mean = tp_sma[i]
        md = sum(abs(tp[j] - mean) for j in range(i - period + 1, i + 1)) / period
        if md == 0:
            result[i] = 0.0
        else:
            result[i] = (tp[i] - mean) / (0.015 * md)

    return result


# ─── ADX ───────────────────────────────────────────────────────

def adx(
    high: list[float],
    low: list[float],
    close: list[float],
    period: int = 14,
) -> tuple[list[float], list[float], list[float]]:
    """
    Average Directional Index.

    Returns:
        (adx_line, plus_di, minus_di)
    """
    n = len(close)
    adx_line = _nan_list(n)
    plus_di = _nan_list(n)
    minus_di = _nan_list(n)

    if n < period * 2:
        return adx_line, plus_di, minus_di

    tr = _nan_list(n)
    plus_dm = _nan_list(n)
    minus_dm = _nan_list(n)

    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    # Smooth with Wilder's method
    atr_smooth = sum(tr[1 : period + 1])
    plus_dm_smooth = sum(plus_dm[1 : period + 1])
    minus_dm_smooth = sum(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        atr_smooth = atr_smooth - atr_smooth / period + tr[i]
        plus_dm_smooth = plus_dm_smooth - plus_dm_smooth / period + plus_dm[i]
        minus_dm_smooth = minus_dm_smooth - minus_dm_smooth / period + minus_dm[i]

        if atr_smooth > 0:
            pdi = (plus_dm_smooth / atr_smooth) * 100.0
            mdi = (minus_dm_smooth / atr_smooth) * 100.0
            plus_di[i] = pdi
            minus_di[i] = mdi

            denom = pdi + mdi
            if denom > 0:
                dx = abs(pdi - mdi) / denom * 100.0
                if i == period + 1:
                    adx_line[i] = dx
                else:
                    adx_line[i] = (adx_line[i - 1] * (period - 1) + dx) / period

    return adx_line, plus_di, minus_di


# ─── OBV ───────────────────────────────────────────────────────

def obv(close: list[float], volume: list[float]) -> list[float]:
    """On-Balance Volume."""
    result = _nan_list(len(close))
    if not close:
        return result

    result[0] = 0.0
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            result[i] = result[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            result[i] = result[i - 1] - volume[i]
        else:
            result[i] = result[i - 1]

    return result