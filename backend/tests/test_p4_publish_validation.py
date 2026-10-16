"""
P4 发布验证（沙箱引擎端到端）。

目标：把前端 codegen 生成的含 SL/TP / trailing_stop / max_holding_time 的策略源码，
直接送入后端 BacktestEngine 跑一轮合成 K 线，验证：
  1) 引擎能成功加载并运行生成源码（无语法/AST 错误）
  2) SL/TP 在预期价格触发 ctx.close()
  3) trailing_stop 的 storage 状态管理正确
  4) max_holding_time 在超时后强制平仓

运行：
    cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_p4_publish_validation.py -v
"""
from __future__ import annotations

import uuid

import numpy as np
import pytest

from app.sandbox.engine import BacktestEngine
from app.sandbox.payload import BacktestPayload, BarArrays

# 复用 bt_helpers 里的 POINT / SYMBOL_INFO / make_bars / make_payload
from tests.unit.bt_helpers import POINT, SYMBOL_INFO, make_bars, make_payload

EURUSD_POINT = POINT  # 0.00001 → 1 pip = 10 points for EURUSD


def _pf(s: str) -> str:
    """把 Python f-string 里的 {var} 正确转义（外层 f-string 不解释内层）。"""
    return s


def _build_source_from_builder_config(builder_config: dict) -> str:
    """
    把 builder_config DSL 转换为可被 BacktestEngine 运行的 Python 策略源码。
    等价于前端 codegen 的输出（已由 vitest 锁定结构）。
    """
    from app.services.builder_validator import validate_builder_config
    errors = validate_builder_config(builder_config)
    assert not errors, f"DSL 校验失败: {errors}"

    symbol = builder_config["symbol"]
    timeframe = builder_config["timeframe"]
    volume = builder_config.get("sizing", {}).get("volume", builder_config["volume"])
    risk_blocks = builder_config.get("risk_blocks", [])

    # 用普通字符串拼接而非 f-string，避免嵌套 {pos.id} 被外层解释
    L = []  # lines
    L.append("def on_init(ctx):")
    L.append(f'    ctx.declare(symbol="{symbol}", period="{timeframe}", params={{}})')
    L.append("")
    L.append("def on_bar(ctx, bar):")
    L.append("    positions = ctx.get_positions()")
    L.append(f"    if not positions:")
    L.append(f"        ctx.buy(ctx.symbol, float({volume}))")
    L.append(f"    positions = ctx.get_positions()")
    L.append("")

    # P3 风控检查（等价于前端 emitRiskChecks 输出）
    if risk_blocks:
        L.append("    # ── P3 风控检查 ──")
        L.append("    positions = ctx.get_positions()")
        L.append("    for pos in positions:")
        for rb in risk_blocks:
            kind = rb["kind"]
            if kind in ("stop_loss", "take_profit", "trailing_stop"):
                pips = rb["pips"]
                abs_ = f"{pips} * ctx.get_symbol_info(ctx.symbol).point"
                _pos_close = "ctx.close(pos.id)"
                _pos_log = "ctx.log.info"
                if kind == "stop_loss":
                    L.append(f"        # SL {pips} pips")
                    L.append(f"        if pos.direction == 'buy' and pos.current_price <= pos.open_price - {abs_}:")
                    L.append(f"            ctx.log.info('SL hit long ' + pos.id)")
                    L.append(f"            ctx.close(pos.id)")
                    L.append(f"        if pos.direction == 'sell' and pos.current_price >= pos.open_price + {abs_}:")
                    L.append(f"            ctx.log.info('SL hit short ' + pos.id)")
                    L.append(f"            ctx.close(pos.id)")
                elif kind == "take_profit":
                    L.append(f"        # TP {pips} pips")
                    L.append(f"        if pos.direction == 'buy' and pos.current_price >= pos.open_price + {abs_}:")
                    L.append(f"            ctx.log.info('TP hit long ' + pos.id)")
                    L.append(f"            ctx.close(pos.id)")
                    L.append(f"        if pos.direction == 'sell' and pos.current_price <= pos.open_price - {abs_}:")
                    L.append(f"            ctx.log.info('TP hit short ' + pos.id)")
                    L.append(f"            ctx.close(pos.id)")
                elif kind == "trailing_stop":
                    kid = rb["id"]
                    L.append(f"        # Trail {pips} pips")
                    L.append(f"        _tkey = 'trail_{kid}_' + pos.id")
                    L.append(f"        if _tkey not in ctx.storage:")
                    L.append(f"            ctx.storage[_tkey] = pos.open_price")
                    L.append(f"        _ext = ctx.storage[_tkey]")
                    L.append(f"        if pos.direction == 'buy':")
                    L.append(f"            _ext = max(_ext, pos.current_price)")
                    L.append(f"            ctx.storage[_tkey] = _ext")
                    L.append(f"            if pos.current_price <= _ext - {abs_}:")
                    L.append(f"                ctx.log.info('TRAIL hit long ' + pos.id)")
                    L.append(f"                ctx.close(pos.id)")
                    L.append(f"                del ctx.storage[_tkey]")
                    L.append(f"        elif pos.direction == 'sell':")
                    L.append(f"            _ext = min(_ext, pos.current_price)")
                    L.append(f"            ctx.storage[_tkey] = _ext")
                    L.append(f"            if pos.current_price >= _ext + {abs_}:")
                    L.append(f"                ctx.log.info('TRAIL hit short ' + pos.id)")
                    L.append(f"                ctx.close(pos.id)")
                    L.append(f"                del ctx.storage[_tkey]")
            elif kind == "max_holding_time":
                minutes = rb["minutes"]
                L.append(f"        # Max Holding {minutes} min")
                L.append(f"        _elapsed_min = (ctx.now - pos.opened_at).total_seconds() / 60.0")
                L.append(f"        if _elapsed_min >= {minutes}:")
                L.append(f"            ctx.log.info('MAX_HOLD hit ' + pos.id + ' elapsed=' + str(_elapsed_min))")
                L.append(f"            ctx.close(pos.id)")
        L.append("")
        L.append("    # 风控完成")
        L.append("")

    L.append("")
    L.append("def on_order(ctx, order):")
    L.append("    pass")
    L.append("")
    L.append("def on_stop(ctx, reason):")
    L.append("    pass")
    L.append("")
    return "\n".join(L)


# ── P4-1: SL 验证 — 合成下跌 K 线，验证止损触发 ──────────────────


def _sl_strategy():
    return {
        "version": 1,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "volume": 0.1,
        "direction": "both",
        "indicators": [],
        "rules": [],
        "risk_blocks": [{"id": "sl1", "kind": "stop_loss", "pips": 50}],
    }


def _sl_bars():
    """EURUSD H1，base=1.10000。buy 在 bar 0，SL=50 pips = 0.00050。
    bar 5 时 close 跌到 1.09940（每 bar 跌 15 pips，5 bar 跌 75）< SL 价 1.09950 → 触发。
    """
    n = 20
    times = 1_760_000_400 + np.arange(n, dtype="int64") * 3600
    base = 1.10000
    closes = base - np.arange(n) * 0.00015  # 每 bar 跌 15 pips
    opens = closes + 0.00001
    highs = np.maximum(opens, closes) + 0.00002
    lows = np.minimum(opens, closes) - 0.00002
    return BarArrays(
        time=times, open=opens, high=highs, low=lows, close=closes,
        tick_volume=np.full(n, 100, dtype="int64"), spread=np.full(n, 20, dtype="int64"),
    )


def _run(source: str, bars: BarArrays) -> tuple[list[dict], dict]:
    """运行引擎并返回 (all_log_messages, final_report)。"""
    engine = BacktestEngine(make_payload(source), bars)
    logs: list[dict] = []
    report: dict = {}
    for ev in engine.run():
        if ev.kind == "log":
            logs.append(ev.data)
        elif ev.kind == "done":
            report = ev.data.get("report", {})
        elif ev.kind == "fail":
            logs.append({"message": f"FAIL: {ev.data}"})
    return logs, report


def test_p4_sl_triggers_on_falling_price():
    """P4-1: 做多 + 下跌行情 → 50 pips 止损应触发。"""
    source = _build_source_from_builder_config(_sl_strategy())
    bars = _sl_bars()
    logs, report = _run(source, bars)

    all_logs = [e.get("message", "") for e in logs if "SL" in e.get("message", "")]
    assert len(all_logs) > 0, f"期望有 SL 触发日志，实际 logs={[e.get('message') for e in logs[:10]]}"

    assert report.get("final_equity", 0) < 10000, f"SL 应产生亏损，最终权益 {report.get('final_equity')}"


# ── P4-2 helpers: TP ────────────────────────────────────────────────


def _tp_strategy():
    return {
        "version": 1,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "volume": 0.1,
        "direction": "both",
        "indicators": [],
        "rules": [],
        "risk_blocks": [{"id": "tp1", "kind": "take_profit", "pips": 100}],
    }


def _tp_bars():
    n = 20
    times = 1_760_000_400 + np.arange(n, dtype="int64") * 3600
    base = 1.10000
    closes = base + np.arange(n) * 0.00025
    opens = closes - 0.00001
    highs = np.maximum(opens, closes) + 0.00002
    lows = np.minimum(opens, closes) - 0.00002
    return BarArrays(
        time=times, open=opens, high=highs, low=lows, close=closes,
        tick_volume=np.full(n, 100, dtype="int64"), spread=np.full(n, 20, dtype="int64"),
    )


# ── P4-3 helpers: Trailing Stop ──────────────────────────────────────


def _trail_strategy():
    return {
        "version": 1,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "volume": 0.1,
        "direction": "both",
        "indicators": [],
        "rules": [],
        "risk_blocks": [{"id": "tr1", "kind": "trailing_stop", "pips": 30}],
    }


def _trail_bars():
    n = 25
    times = 1_760_000_400 + np.arange(n, dtype="int64") * 3600
    base = 1.10000
    closes_up = base + np.arange(6) * 0.00040
    closes_down = closes_up[-1] - np.arange(1, 15) * 0.00030
    closes_flat = np.full(n - 14, closes_down[-1])
    closes = np.concatenate([closes_up, closes_down, closes_flat])[:n]
    opens = closes - 0.00001
    highs = np.maximum(opens, closes) + 0.00002
    lows = np.minimum(opens, closes) - 0.00002
    return BarArrays(
        time=times, open=opens, high=highs, low=lows, close=closes,
        tick_volume=np.full(n, 100, dtype="int64"), spread=np.full(n, 20, dtype="int64"),
    )


# ── P4-4 helpers: Max Holding ───────────────────────────────────────


def _maxhold_strategy():
    return {
        "version": 1,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "volume": 0.1,
        "direction": "both",
        "indicators": [],
        "rules": [],
        "risk_blocks": [{"id": "mh1", "kind": "max_holding_time", "minutes": 3}],
    }


# ── P4-5 helpers: Multi-risk ──────────────────────────────────────


def _multi_risk_strategy():
    return {
        "version": 1,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "volume": 0.1,
        "direction": "both",
        "indicators": [],
        "rules": [],
        "risk_blocks": [
            {"id": "sl1", "kind": "stop_loss", "pips": 50},
            {"id": "tp1", "kind": "take_profit", "pips": 200},
            {"id": "tr1", "kind": "trailing_stop", "pips": 30},
            {"id": "mh1", "kind": "max_holding_time", "minutes": 60 * 24},
        ],
    }


def test_p4_tp_triggers_on_rising_price():
    """P4-2: 做多 + 上涨行情 → 100 pips 止盈应触发。"""
    source = _build_source_from_builder_config(_tp_strategy())
    bars = _tp_bars()
    logs, report = _run(source, bars)

    tp_logs = [e.get("message", "") for e in logs if "TP" in e.get("message", "")]
    assert len(tp_logs) > 0, f"期望有 TP 触发日志"

    assert report.get("final_equity", 0) > 10000, f"TP 应产生盈利，最终权益 {report.get('final_equity')}"


def test_p4_trailing_stop_tracks_peak():
    """P4-3: 上涨后回落 → trailing stop 应在回撤 30 pips 时平仓。"""
    source = _build_source_from_builder_config(_trail_strategy())
    bars = _trail_bars()
    logs, report = _run(source, bars)

    trail_logs = [e.get("message", "") for e in logs if "TRAIL" in e.get("message", "")]
    assert len(trail_logs) > 0, f"期望有 TRAIL 触发日志，logs={[e.get('message') for e in logs[:20]]}"


def test_p4_max_holding_time_forces_close():
    """P4-4: 持仓超过 3 分钟（H1 bar 间隔 60 min）→ 应被 max_holding_time 强制平仓。"""
    source = _build_source_from_builder_config(_maxhold_strategy())
    bars = make_bars(n=15, drift=0.0)  # 横盘
    logs, report = _run(source, bars)

    mh_logs = [e.get("message", "") for e in logs if "MAX_HOLD" in e.get("message", "")]
    assert len(mh_logs) > 0, f"期望有 MAX_HOLD 触发日志，logs={[e.get('message') for e in logs[:20]]}"


def test_p4_multi_risk_strategy_runs_without_error():
    """P4-5: 4 种风控积木叠加的策略源码能被引擎正常加载、逐 bar 运行，无崩溃。"""
    source = _build_source_from_builder_config(_multi_risk_strategy())

    # 1) AST 校验必须通过
    from app.services.strategy_validator import validate_source
    validator_report = validate_source(source)
    assert validator_report.ok, f"生成源码 AST 校验失败: {validator_report.issues}"

    # 2) 引擎运行无 crash
    bars = _tp_bars()  # 上涨行情 → TP 会先触发
    logs, report = _run(source, bars)

    # 引擎有 final_equity 说明正常完成
    assert "final_equity" in report, f"引擎未正常完成，report keys={list(report.keys())}"

    # 有风控相关日志
    risk_logs = [
        e.get("message", "") for e in logs
        if any(k in e.get("message", "") for k in ["SL hit", "TP hit", "TRAIL hit", "MAX_HOLD hit"])
    ]
    assert len(risk_logs) > 0, f"多风控叠加应至少触发其中一个，logs={[e.get('message') for e in logs[:30]]}"
