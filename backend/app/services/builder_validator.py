"""
可视化策略构建器 DSL 校验器（P1）。

纯函数模块：builder_config dict → list[dict]（空 = 合法，非空 = 错误列表）。
每个错误 dict 含：
  targetId: str | None  —— 关联的 indicator.id / rule.id；None 表示全局错误
  message: str          —— 错误文本

与前端 codegen.ts 的 validateIndicators + validateRules 等价覆盖 14 条规则。
不生成 Python 代码，只做防御性校验——前后端都有校验才能堵"非法配置静默入库"的坑。
"""

from __future__ import annotations

from typing import TypedDict


class BuilderError(TypedDict, total=False):
    targetId: str | None
    message: str


# ── 枚举常量（与前端 types.ts 严格对齐） ──────────────────────────

INDICATOR_KINDS = frozenset({"MA", "EMA", "RSI", "BOLL", "ATR", "MACD"})

# 每种指标合法的 IndicatorOutput 通道（与前端 INDICATOR_META.outputs 一致）
INDICATOR_OUTPUTS: dict[str, frozenset[str]] = {
    "MA":   frozenset({"value"}),
    "EMA":  frozenset({"value"}),
    "RSI":  frozenset({"value"}),
    "BOLL": frozenset({"upper", "mid", "lower"}),
    "ATR":  frozenset({"value"}),
    "MACD": frozenset({"dif", "dea", "hist"}),
}

CONDITION_OPS = frozenset({"cross_above", "cross_below", "greater_than", "less_than"})
DIRECTIONS    = frozenset({"long_only", "short_only", "both"})
RISK_KINDS    = frozenset({"stop_loss", "take_profit", "trailing_stop", "max_holding_time", "daily_drawdown_cut", "cooldown", "max_position_count"})
SIZING_KINDS  = frozenset({"fixed", "risk_percent"})


# ── 入口 ──────────────────────────────────────────────────────────


def validate_builder_config(config: dict) -> list[BuilderError]:
    """返回错误列表；空列表 = 合法。不抛异常。"""
    errors: list[BuilderError] = []
    if not isinstance(config, dict):
        errors.append({"targetId": None, "message": "builder_config 必须是对象"})
        return errors

    # ── version ──
    version = config.get("version")
    if version != 1:
        errors.append({"targetId": None, "message": f"未知 DSL 版本 {version!r}，仅支持 1"})
        # 版本不对就不往下走了，后续规则可能全部基于 v1 数据结构
        return errors

    # ── 顶层字段（targetId=None = 全局） ──
    symbol = config.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        errors.append({"targetId": None, "message": "品种（symbol）不能为空"})

    timeframe = config.get("timeframe")
    if not isinstance(timeframe, str) or not timeframe.strip():
        errors.append({"targetId": None, "message": "周期（timeframe）不能为空"})

    volume = config.get("volume")
    if not isinstance(volume, (int, float)) or isinstance(volume, bool) or volume <= 0:
        errors.append({"targetId": None, "message": "手数（volume）必须大于 0"})

    direction = config.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        errors.append({"targetId": None, "message": f"方向限制 {direction!r} 不合法，允许 {DIRECTIONS}"})

    # ── 指标层 ──
    indicators = config.get("indicators") or []
    if not isinstance(indicators, list):
        errors.append({"targetId": None, "message": "indicators 必须是数组"})
    else:
        errors.extend(_validate_indicators(indicators))

    # 收集指标 id → kind 映射（规则校验要用来查悬空引用 + 输出通道）
    ind_kind_by_id: dict[str, str] = {}
    if isinstance(indicators, list):
        for i, ind in enumerate(indicators):
            if isinstance(ind, dict) and ind.get("id") and ind.get("kind") in INDICATOR_KINDS:
                ind_kind_by_id[ind["id"]] = ind["kind"]

    # ── 规则层 ──
    rules = config.get("rules") or []
    if not isinstance(rules, list):
        errors.append({"targetId": None, "message": "rules 必须是数组"})
    else:
        errors.extend(_validate_rules(rules, direction, ind_kind_by_id))

    # ── 仓位模式（P3，可选） ──
    sizing = config.get("sizing")
    if sizing is not None:
        errors.extend(_validate_sizing(sizing))

    # ── 风控积木（P3，可选） ──
    risk_blocks = config.get("risk_blocks")
    if risk_blocks is not None:
        if not isinstance(risk_blocks, list):
            errors.append({"targetId": None, "message": "risk_blocks 必须是数组"})
        else:
            errors.extend(_validate_risk_blocks(risk_blocks))

    # risk_percent 模式需要 SL 风控——交叉校验
    if (
        isinstance(sizing, dict)
        and sizing.get("kind") == "risk_percent"
        and isinstance(risk_blocks, list)
        and not any(b.get("kind") == "stop_loss" for b in risk_blocks if isinstance(b, dict))
    ):
        errors.append({
            "targetId": None,
            "message": "risk_percent 仓位模式需要至少一个止损（stop_loss）风控积木"
        })

    return errors


# ── 指标校验 ───────────────────────────────────────────────────────


def _validate_indicators(indicators: list[dict]) -> list[BuilderError]:
    errors: list[BuilderError] = []
    seen_ids: set[str] = set()

    for i, ind in enumerate(indicators):
        ind_id = ind.get("id") if isinstance(ind, dict) else None
        label = f"指标 #{i + 1}"
        target = ind_id if isinstance(ind_id, str) else None

        if not isinstance(ind, dict):
            errors.append({"targetId": target, "message": f"{label}：必须是对象"})
            continue

        # id
        if not isinstance(ind_id, str) or not ind_id:
            errors.append({"targetId": target, "message": f"{label}：id 缺失或非字符串"})
            seen_ids.add("<missing>")  # 占位，避免后续重复
        elif ind_id in seen_ids:
            errors.append({"targetId": target, "message": f"{label}：内部 id 重复（{ind_id}）"})
            seen_ids.add(ind_id)
        else:
            seen_ids.add(ind_id)

        # kind
        kind = ind.get("kind")
        if kind not in INDICATOR_KINDS:
            errors.append({"targetId": target, "message": f"{label}：未知指标类型 {kind!r}"})
            continue  # kind 不对就跳过后续参数校验

        # 参数（对齐前端 codegen validateIndicators）
        if kind == "MACD":
            fast = ind.get("fast", 12)
            slow = ind.get("slow", 26)
            signal = ind.get("signal", 9)
            if not _is_number(fast) or fast < 2:
                errors.append({"targetId": target, "message": f"{label}：MACD 快线周期需 ≥ 2"})
            if not _is_number(slow) or slow <= (fast if _is_number(fast) else 0):
                errors.append({"targetId": target, "message": f"{label}：MACD 慢线需大于快线"})
            if not _is_number(signal) or signal < 2:
                errors.append({"targetId": target, "message": f"{label}：MACD 信号周期需 ≥ 2"})
        else:
            period = ind.get("period")
            if not _is_number(period) or period < 2:
                errors.append({"targetId": target, "message": f"{label}：{kind} 周期需 ≥ 2"})

        if kind == "BOLL":
            std = ind.get("std", 2)
            if not _is_number(std) or std <= 0:
                errors.append({"targetId": target, "message": f"{label}：BOLL 标准差倍数需 > 0"})

    return errors


# ── 规则校验 ───────────────────────────────────────────────────────


def _validate_rules(
    rules: list[dict],
    direction: str | None,
    ind_kind_by_id: dict[str, str],
) -> list[BuilderError]:
    errors: list[BuilderError] = []

    for i, rule in enumerate(rules):
        rule_id = rule.get("id") if isinstance(rule, dict) else None
        label = f"规则 #{i + 1}"
        target = rule_id if isinstance(rule_id, str) else None

        if not isinstance(rule, dict):
            errors.append({"targetId": target, "message": f"{label}：必须是对象"})
            continue

        # op
        op = rule.get("op")
        if op not in CONDITION_OPS:
            errors.append({"targetId": target, "message": f"{label}：未知操作符 {op!r}"})

        # open / close（至少一个）
        open_action = rule.get("open")
        close_action = rule.get("close")
        if open_action is None and close_action is None:
            errors.append({"targetId": target, "message": f"{label}：至少配置一个动作（开仓或平仓）"})

        # 方向冲突（long_only 不能 sell；short_only 不能 buy）
        if direction == "long_only":
            if open_action == "sell" or close_action == "sell":
                errors.append({"targetId": target, "message": f"{label}：方向限制为「仅做多」，不能配置做空动作"})
        elif direction == "short_only":
            if open_action == "buy" or close_action == "buy":
                errors.append({"targetId": target, "message": f"{label}：方向限制为「仅做空」，不能配置做多动作"})

        # open / close 值合法性
        for act_name, act_val in (("open", open_action), ("close", close_action)):
            if act_val is not None and act_val not in ("buy", "sell"):
                errors.append({"targetId": target, "message": f"{label}：{act_name} 动作值 {act_val!r} 不合法"})

        # left / right operand 校验
        for side_name in ("left", "right"):
            operand = rule.get(side_name)
            errors.extend(
                _validate_operand(target, f"{label}：{side_name}操作数", operand, ind_kind_by_id)
            )

    return errors


def _validate_operand(
    target_id: str | None,
    label: str,
    operand: object,
    ind_kind_by_id: dict[str, str],
) -> list[BuilderError]:
    errors: list[BuilderError] = []
    if not isinstance(operand, dict):
        errors.append({"targetId": target_id, "message": f"{label}：必须是对象"})
        return errors

    op_type = operand.get("type")
    if op_type == "close":
        return errors  # 合法

    if op_type == "number":
        if not _is_number(operand.get("value")):
            errors.append({"targetId": target_id, "message": f"{label}：常量操作数需含数值 value"})
        return errors

    if op_type == "indicator":
        ind_id = operand.get("indicatorId")
        output = operand.get("output")
        kind = ind_kind_by_id.get(ind_id)
        if kind is None:
            errors.append({"targetId": target_id, "message": f"{label}：引用了已删除的指标（id={ind_id!r}）"})
        elif output not in INDICATOR_OUTPUTS.get(kind, frozenset()):
            errors.append({"targetId": target_id, "message": f"{label}：{kind} 不支持输出通道 {output!r}"})
        return errors

    errors.append({"targetId": target_id, "message": f"{label}：未知操作数类型 {op_type!r}"})
    return errors


# ── P3 仓位模式校验 ───────────────────────────────────────────────


def _validate_sizing(sizing: object) -> list[BuilderError]:
    errors: list[BuilderError] = []
    if not isinstance(sizing, dict):
        return [{"targetId": None, "message": "sizing 必须是对象"}]

    kind = sizing.get("kind")
    if kind not in SIZING_KINDS:
        errors.append({"targetId": None, "message": f"sizing.kind {kind!r} 不合法，允许 {SIZING_KINDS}"})
        return errors

    if kind == "fixed":
        volume = sizing.get("volume")
        if not _is_number(volume) or volume <= 0:
            errors.append({"targetId": None, "message": "sizing.fixed.volume 必须 > 0"})
    elif kind == "risk_percent":
        rp = sizing.get("risk_percent")
        sl = sizing.get("sl_pips")
        if not _is_number(rp) or not (0 < rp <= 100):
            errors.append({"targetId": None, "message": "sizing.risk_percent 必须在 (0, 100] 范围内"})
        if not _is_number(sl) or sl <= 0:
            errors.append({"targetId": None, "message": "sizing.risk_percent.sl_pips 必须 > 0"})

    return errors


# ── P3 风控积木校验 ───────────────────────────────────────────────


def _validate_risk_blocks(risk_blocks: list[dict]) -> list[BuilderError]:
    errors: list[BuilderError] = []
    seen_ids: set[str] = set()

    for i, block in enumerate(risk_blocks):
        target = block.get("id") if isinstance(block, dict) else None
        label = f"风控 #{i + 1}"
        has_target = isinstance(target, str) and target
        if has_target:
            if target in seen_ids:
                errors.append({"targetId": target, "message": f"{label}：id 重复（{target}）"})
            seen_ids.add(target)
        elif isinstance(block, dict):
            errors.append({"targetId": None, "message": f"{label}：id 缺失"})

        if not isinstance(block, dict):
            errors.append({"targetId": target if has_target else None, "message": f"{label}：必须是对象"})
            continue

        kind = block.get("kind")
        if kind not in RISK_KINDS:
            errors.append({"targetId": target, "message": f"{label}：未知风控类型 {kind!r}"})
            continue

        if kind in ("stop_loss", "take_profit", "trailing_stop"):
            pips = block.get("pips")
            if not _is_number(pips) or pips <= 0:
                errors.append({"targetId": target, "message": f"{label}：pips 必须 > 0"})
        elif kind == "max_holding_time" or kind == "cooldown":
            minutes = block.get("minutes")
            if not _is_number(minutes) or minutes <= 0:
                errors.append({"targetId": target, "message": f"{label}：minutes 必须 > 0"})
        elif kind == "max_position_count":
            count = block.get("count")
            if not _is_number(count) or not isinstance(count, int) or count <= 0:
                errors.append({"targetId": target, "message": f"{label}：count 必须是正整数"})
        elif kind == "daily_drawdown_cut":
            percent = block.get("percent")
            if not _is_number(percent) or not (0 < percent <= 100):
                errors.append({"targetId": target, "message": f"{label}：percent 必须在 (0, 100] 范围内"})

    return errors


# ── 工具 ───────────────────────────────────────────────────────────


def _is_number(v: object) -> bool:
    """int / float 且非 bool。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)
