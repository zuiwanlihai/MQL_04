/**
 * 可视化策略构建器代码生成器（纯函数，可单测）。
 *
 * BuilderConfig → Python 策略源码，形状对齐 shared/sdk/templates/double_ma.py：
 * 零 import（纯 Python 指标辅助函数）、on_init ctx.declare(params)、
 * on_bar get_bars → 指标计算 → 规则条件 → ctx.buy/close。
 * 生成代码必然通过 strategy_validator AST 白名单校验。
 *
 * 指标口径与前端 lib/indicators.ts 一致：EMA 用 SMA 种子、RSI 用 Wilder
 * 平滑、BOLL 总体方差、MACD hist = dif - dea。
 */

import type {
  BuilderConfig,
  ConditionOp,
  IndicatorBlock,
  IndicatorOutput,
  Operand,
  RiskBlock,
  Rule,
} from './types';
import { INDICATOR_META } from './types';

export interface BuilderError {
  /** 关联的 indicator.id / rule.id；undefined 表示全局错误 */
  targetId?: string;
  message: string;
}

export interface CodegenResult {
  code: string;
  errors: BuilderError[];
}

const INDENT1 = '    ';
const INDENT2 = INDENT1 + INDENT1;
const INDENT3 = INDENT2 + INDENT1;

interface MetricVarInfo {
  block: IndicatorBlock;
  /** 全局序号（1-based），同 kind 也递增：ma_1 / ma_2 / rsi_3 … */
  n: number;
}

/** 指标参数校验；返回结构化错误列表（含 targetId = indicator.id）。 */
function validateIndicators(indicators: IndicatorBlock[]): BuilderError[] {
  const errors: BuilderError[] = [];
  const ids = new Set<string>();
  indicators.forEach((ind, i) => {
    const label = `指标 #${i + 1}`;
    if (ids.has(ind.id)) errors.push({ targetId: ind.id, message: `${label}：内部 id 重复（${ind.id}）` });
    ids.add(ind.id);
    if (!INDICATOR_META[ind.kind]) errors.push({ targetId: ind.id, message: `${label}：未知指标类型 ${ind.kind}` });
    if (ind.kind === 'MACD') {
      const { fast = 12, slow = 26, signal = 9 } = ind;
      if (!Number.isFinite(fast) || fast < 2) errors.push({ targetId: ind.id, message: `${label}：MACD 快线周期需 ≥ 2` });
      if (!Number.isFinite(slow) || slow <= (fast ?? 0)) errors.push({ targetId: ind.id, message: `${label}：MACD 慢线需大于快线` });
      if (!Number.isFinite(signal) || signal < 2) errors.push({ targetId: ind.id, message: `${label}：MACD 信号周期需 ≥ 2` });
    } else if (!Number.isFinite(ind.period) || ind.period < 2) {
      errors.push({ targetId: ind.id, message: `${label}：${ind.kind} 周期需 ≥ 2` });
    }
    if (ind.kind === 'BOLL' && (!Number.isFinite(ind.std ?? 2) || (ind.std ?? 2) <= 0)) {
      errors.push({ targetId: ind.id, message: `${label}：BOLL 标准差倍数需 > 0` });
    }
  });
  return errors;
}

function outputAllowed(kind: keyof typeof INDICATOR_META, output: IndicatorOutput): boolean {
  return (INDICATOR_META[kind]?.outputs ?? []).includes(output);
}

function validateRules(config: BuilderConfig, varByIndicator: Map<string, MetricVarInfo>): BuilderError[] {
  const errors: BuilderError[] = [];
  config.rules.forEach((rule, i) => {
    const label = `规则 #${i + 1}`;
    if (rule.open === null && rule.close === null) {
      errors.push({ targetId: rule.id, message: `${label}：至少配置一个动作（开仓或平仓）` });
    }
    if (config.direction === 'long_only' && (rule.open === 'sell' || rule.close === 'sell')) {
      errors.push({ targetId: rule.id, message: `${label}：方向限制为「仅做多」，不能配置做空动作` });
    }
    if (config.direction === 'short_only' && (rule.open === 'buy' || rule.close === 'buy')) {
      errors.push({ targetId: rule.id, message: `${label}：方向限制为「仅做空」，不能配置做多动作` });
    }
    for (const side of ['left', 'right'] as const) {
      const operand = rule[side];
      if (operand.type === 'indicator') {
        const info = varByIndicator.get(operand.indicatorId);
        if (!info) {
          errors.push({ targetId: rule.id, message: `${label}：${side === 'left' ? '左' : '右'}操作数引用了已删除的指标` });
        } else if (!outputAllowed(info.block.kind, operand.output)) {
          errors.push({ targetId: rule.id, message: `${label}：${info.block.kind} 不支持输出通道 ${operand.output}` });
        }
      }
    }
  });
  return errors;
}

function warmupBars(block: IndicatorBlock): number {
  switch (block.kind) {
    case 'RSI':
      return block.period + 1;
    case 'ATR':
      return block.period + 1;
    case 'MACD':
      return (block.slow ?? 26) + (block.signal ?? 9);
    default:
      return block.period;
  }
}

interface VarNames {
  /** 主变量名：ma_1 / boll_1 / macd_1 … */
  base: string;
  /** 参数键：ma_1_period / boll_1_std … */
  paramKey: (suffix: string) => string;
}

function varNames(info: MetricVarInfo): VarNames {
  const prefix = info.block.kind.toLowerCase();
  return {
    base: `${prefix}_${info.n}`,
    paramKey: (suffix) => `${prefix}_${info.n}_${suffix}`,
  };
}

/** 输出通道的变量名：value → ma_1；upper → boll_1_upper；dif → macd_1_dif。 */
function outputVar(base: string, output: IndicatorOutput): string {
  return output === 'value' ? base : `${base}_${output}`;
}

interface ExprCtx {
  vars: Map<string, MetricVarInfo>;
  /** cur：当前 bar 值；prev：前一根 bar 值（cross 条件专用）。 */
  phase: 'cur' | 'prev';
}

function operandExpr(op: Operand, ctx: ExprCtx): string {
  if (op.type === 'number') return formatNumber(op.value);
  if (op.type === 'close') return ctx.phase === 'cur' ? 'closes[-1]' : 'closes[-2]';
  const info = ctx.vars.get(op.indicatorId);
  if (!info) return '0.0'; // validateRules 已报错，此处兜底
  const names = varNames(info);
  const v = outputVar(names.base, op.output);
  return ctx.phase === 'cur' ? v : `${v}_prev`;
}

function formatNumber(n: number): string {
  return Number.isInteger(n) ? `${n}.0` : String(n);
}

function conditionExpr(rule: Rule, ctx: ExprCtx): string {
  const left = operandExpr(rule.left, { ...ctx, phase: 'cur' });
  const right = operandExpr(rule.right, { ...ctx, phase: 'cur' });
  switch (rule.op) {
    case 'greater_than':
      return `${left} > ${right}`;
    case 'less_than':
      return `${left} < ${right}`;
    case 'cross_above': {
      const lp = operandExpr(rule.left, { ...ctx, phase: 'prev' });
      const rp = operandExpr(rule.right, { ...ctx, phase: 'prev' });
      return `${lp} <= ${rp} and ${left} > ${right}`;
    }
    case 'cross_below': {
      const lp = operandExpr(rule.left, { ...ctx, phase: 'prev' });
      const rp = operandExpr(rule.right, { ...ctx, phase: 'prev' });
      return `${lp} >= ${rp} and ${left} < ${right}`;
    }
  }
}

function opDescription(rule: Rule, ctx: { vars: Map<string, MetricVarInfo> }): string {
  const side = (op: Operand): string => {
    if (op.type === 'number') return String(op.value);
    if (op.type === 'close') return '收盘价';
    const info = ctx.vars.get(op.indicatorId);
    if (!info) return '?';
    const base = varNames(info).base;
    return op.output === 'value' ? base : `${base}.${op.output}`;
  };
  const opText: Record<ConditionOp, string> = {
    cross_above: '上穿',
    cross_below: '下穿',
    greater_than: '大于',
    less_than: '小于',
  };
  const acts: string[] = [];
  if (rule.open) acts.push(rule.open === 'buy' ? '开多' : '开空');
  if (rule.close) acts.push(rule.close === 'buy' ? '平多' : '平空');
  return `${side(rule.left)} ${opText[rule.op]} ${side(rule.right)} → ${acts.join(' + ') || '无动作'}`;
}

// ── 指标辅助函数（固定文本，按需拼装） ─────────────────────────────

const HELPER_SMA = `def _sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period`;

const HELPER_EMA = `def _ema(values, period):
    if len(values) < period:
        return None
    ema = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    for v in values[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema`;

const HELPER_RSI = `def _rsi(values, period):
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        g = ch if ch > 0 else 0.0
        l = -ch if ch < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)`;

const HELPER_BOLL = `def _boll(values, period, k):
    if len(values) < period:
        return None, None, None
    window = values[-period:]
    mid = sum(window) / period
    std = (sum((v - mid) ** 2 for v in window) / period) ** 0.5
    return mid + k * std, mid, mid - k * std`;

const HELPER_ATR = `def _atr(bars, period):
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period`;

const HELPER_MACD = `def _macd(values, fast, slow, signal):
    if len(values) < slow + signal:
        return None, None, None

    def _ema_series(vals, period):
        if len(vals) < period:
            return [None] * len(vals)
        ema = sum(vals[:period]) / period
        out = [None] * (period - 1) + [ema]
        k = 2.0 / (period + 1)
        for v in vals[period:]:
            ema = v * k + ema * (1.0 - k)
            out.append(ema)
        return out

    fast_s = _ema_series(values, fast)
    slow_s = _ema_series(values, slow)
    difs = [
        f - s if f is not None and s is not None else None
        for f, s in zip(fast_s, slow_s)
    ]
    valid = [d for d in difs if d is not None]
    sig = _ema_series(valid, signal)
    dif = difs[-1]
    dea = sig[-1]
    if dif is None or dea is None:
        return None, None, None
    return dif, dea, dif - dea`;

// ── 生成器主体 ─────────────────────────────────────────────────────

export function generateStrategyCode(
  config: BuilderConfig,
  name = '可视化构建策略',
): CodegenResult {
  const errors: BuilderError[] = [];
  if (!config.symbol.trim()) errors.push({ message: '品种不能为空' });
  if (!Number.isFinite(config.volume) || config.volume <= 0) errors.push({ message: '手数必须大于 0' });
  if (!config.timeframe.trim()) errors.push({ message: '周期不能为空' });

  errors.push(...validateIndicators(config.indicators));

  // 变量分配：按数组顺序全局编号
  const counters = new Map<string, number>();
  const varByIndicator = new Map<string, MetricVarInfo>();
  config.indicators.forEach((block) => {
    const n = (counters.get(block.kind) ?? 0) + 1;
    counters.set(block.kind, n);
    varByIndicator.set(block.id, { block, n });
  });

  errors.push(...validateRules(config, varByIndicator));

  const lines: string[] = [];
  const push = (...l: string[]) => lines.push(...l);

  // ── 模块 docstring
  push(`"""${name}（可视化策略构建器生成）。`);
  push('');
  push(`品种 ${config.symbol} · 周期 ${config.timeframe} · 手数 ${config.volume} · `);
  push(
    `方向 ${config.direction === 'both' ? '双向' : config.direction === 'long_only' ? '仅做多' : '仅做空'}`,
  );
  push(`指标 ${config.indicators.length} 个 · 规则 ${config.rules.length} 条。`);
  push('"""');

  // ── 按需拼装指标辅助函数
  const used = new Set(config.indicators.map((i) => i.kind));
  if (used.size > 0) {
    push('');
    const helpers: string[] = [];
    if (used.has('MA')) helpers.push(HELPER_SMA);
    if (used.has('EMA')) helpers.push(HELPER_EMA);
    if (used.has('RSI')) helpers.push(HELPER_RSI);
    if (used.has('BOLL')) helpers.push(HELPER_BOLL);
    if (used.has('ATR')) helpers.push(HELPER_ATR);
    if (used.has('MACD')) helpers.push(HELPER_MACD);
    push(helpers.join('\n\n\n'));
  }

  // ── on_init
  push('');
  push('');
  push('def on_init(ctx):');
  push(`${INDENT1}"""Initialize strategy parameters and symbol declarations."""`);
  push(`${INDENT1}ctx.declare(`);
  push(`${INDENT2}symbol="${config.symbol}",`);
  push(`${INDENT2}period="${config.timeframe}",`);
  push(`${INDENT2}params={`);
  push(`${INDENT3}"volume": ${formatNumber(config.volume)},`);
  for (const block of config.indicators) {
    const names = varNames({ block, n: varByIndicator.get(block.id)!.n });
    if (block.kind === 'MACD') {
      push(`${INDENT3}"${names.paramKey('fast')}": ${block.fast ?? 12},`);
      push(`${INDENT3}"${names.paramKey('slow')}": ${block.slow ?? 26},`);
      push(`${INDENT3}"${names.paramKey('signal')}": ${block.signal ?? 9},`);
    } else {
      push(`${INDENT3}"${names.paramKey('period')}": ${block.period},`);
      if (block.kind === 'BOLL') {
        push(`${INDENT3}"${names.paramKey('std')}": ${formatNumber(block.std ?? 2)},`);
      }
    }
  }
  const lookback =
    config.indicators.reduce((m, b) => Math.max(m, warmupBars(b)), 2) + 2;
  push(`${INDENT3}"lookback": ${lookback},`);
  push(`${INDENT2}},`);
  push(`${INDENT1})`);

  // ── on_bar
  push('');
  push('');
  push('def on_bar(ctx, bar):');
  push(`${INDENT1}"""Process each completed bar."""`);
  push(`${INDENT1}p = ctx.params`);
  push(`${INDENT1}lookback = int(p["lookback"])`);
  push(`${INDENT1}bars = ctx.get_bars(ctx.symbol, ctx.period, count=lookback)`);
  push(`${INDENT1}if len(bars) < lookback:`);
  push(`${INDENT2}return`);
  push('');
  push(`${INDENT1}closes = [b.close for b in bars]`);

  // 指标计算行（cur + cross 条件所需的 prev）
  const needPrev = new Set<string>(); // `${indicatorId}|${output}`
  for (const rule of config.rules) {
    if (rule.op !== 'cross_above' && rule.op !== 'cross_below') continue;
    for (const side of [rule.left, rule.right]) {
      if (side.type === 'indicator') needPrev.add(`${side.indicatorId}|${side.output}`);
    }
  }

  const exprCtx: ExprCtx = { vars: varByIndicator, phase: 'cur' };
  if (config.indicators.length > 0) {
    push('');
    push(`${INDENT1}# 指标计算`);
    for (const block of config.indicators) {
      const info = varByIndicator.get(block.id)!;
      emitIndicatorLines(push, block, info, needPrev, config);
    }
  }

  // 规则块
  if (config.rules.length > 0) {
    push('');
    push(`${INDENT1}positions = ctx.get_positions()`);
  }
  for (let i = 0; i < config.rules.length; i++) {
    const rule = config.rules[i];
    push('');
    push(`${INDENT1}# 规则 ${i + 1}: ${opDescription(rule, exprCtx)}`);
    push(`${INDENT1}positions = ctx.get_positions()`);
    push(`${INDENT1}if ${conditionExpr(rule, exprCtx)}:`);
    emitActions(push, rule, config.direction, config);
  }

  // ── P3 风控：SL / TP 检查（每根 K 线规则之后统一扫一遍所有持仓） ──
  if (config.risk_blocks && config.risk_blocks.length > 0) {
    emitRiskChecks(push, config.risk_blocks);
  }

  // ── on_order / on_stop（validator 必需回调）
  push('');
  push('');
  push('def on_order(ctx, order):');
  push(`${INDENT1}"""Handle order fill notifications."""`);
  push(`${INDENT1}if order.success:`);
  push(
    `${INDENT2}ctx.log.info(f"Order filled: {order.order_id} volume={order.filled_volume} price={order.filled_price}")`,
  );
  push('');
  push('');
  push('def on_stop(ctx, reason):');
  push(`${INDENT1}"""Cleanup when strategy is stopped."""`);
  push(`${INDENT1}ctx.log.info(f"Strategy stopped: {reason}")`);

  return { code: lines.join('\n') + '\n', errors };
}

function emitIndicatorLines(
  push: (...l: string[]) => void,
  block: IndicatorBlock,
  info: MetricVarInfo,
  needPrev: Set<string>,
  config: BuilderConfig,
): void {
  const names = varNames(info);
  const IND = INDENT1;
  switch (block.kind) {
    case 'MA': {
      const k = names.paramKey('period');
      push(`${IND}${names.base} = _sma(closes, int(p["${k}"]))`);
      if (needPrev.has(`${block.id}|value`)) {
        push(`${IND}${names.base}_prev = _sma(closes[:-1], int(p["${k}"]))`);
      }
      break;
    }
    case 'EMA': {
      const k = names.paramKey('period');
      push(`${IND}${names.base} = _ema(closes, int(p["${k}"]))`);
      if (needPrev.has(`${block.id}|value`)) {
        push(`${IND}${names.base}_prev = _ema(closes[:-1], int(p["${k}"]))`);
      }
      break;
    }
    case 'RSI': {
      const k = names.paramKey('period');
      push(`${IND}${names.base} = _rsi(closes, int(p["${k}"]))`);
      if (needPrev.has(`${block.id}|value`)) {
        push(`${IND}${names.base}_prev = _rsi(closes[:-1], int(p["${k}"]))`);
      }
      break;
    }
    case 'ATR': {
      const k = names.paramKey('period');
      push(`${IND}${names.base} = _atr(bars, int(p["${k}"]))`);
      if (needPrev.has(`${block.id}|value`)) {
        push(`${IND}${names.base}_prev = _atr(bars[:-1], int(p["${k}"]))`);
      }
      break;
    }
    case 'BOLL': {
      const kp = names.paramKey('period');
      const ks = names.paramKey('std');
      const call = `_boll(closes, int(p["${kp}"]), float(p["${ks}"]))`;
      const slots: IndicatorOutput[] = ['upper', 'mid', 'lower'];
      const targets = slots.map(
        (o) => (configUsedOutputs(config, block).has(o) || needPrev.has(`${block.id}|${o}`)
          ? outputVar(names.base, o)
          : '_'),
      );
      push(`${IND}${targets.join(', ')} = ${call}`);
      for (const o of slots) {
        if (needPrev.has(`${block.id}|${o}`)) {
          const prevTargets = slots.map((x) =>
            x === o ? `${outputVar(names.base, o)}_prev` : '_',
          );
          push(`${IND}${prevTargets.join(', ')} = ${call.replace('closes', 'closes[:-1]')}`);
        }
      }
      break;
    }
    case 'MACD': {
      const kf = names.paramKey('fast');
      const ks2 = names.paramKey('slow');
      const kg = names.paramKey('signal');
      const call = `_macd(closes, int(p["${kf}"]), int(p["${ks2}"]), int(p["${kg}"]))`;
      const slots: IndicatorOutput[] = ['dif', 'dea', 'hist'];
      const targets = slots.map((o) =>
        configUsedOutputs(config, block).has(o) || needPrev.has(`${block.id}|${o}`)
          ? outputVar(names.base, o)
          : '_',
      );
      push(`${IND}${targets.join(', ')} = ${call}`);
      for (const o of slots) {
        if (needPrev.has(`${block.id}|${o}`)) {
          const prevTargets = slots.map((x) =>
            x === o ? `${outputVar(names.base, o)}_prev` : '_',
          );
          push(`${IND}${prevTargets.join(', ')} = ${call.replace('closes', 'closes[:-1]')}`);
        }
      }
      break;
    }
  }
}

/** 该指标被规则（cur 引用）使用的输出通道集合。 */
function configUsedOutputs(config: BuilderConfig, block: IndicatorBlock): Set<IndicatorOutput> {
  const used = new Set<IndicatorOutput>();
  for (const rule of config.rules) {
    for (const side of [rule.left, rule.right]) {
      if (side.type === 'indicator' && side.indicatorId === block.id) {
        used.add(side.output);
      }
    }
  }
  return used;
}

function emitActions(
  push: (...l: string[]) => void,
  rule: Rule,
  _direction: BuilderConfig['direction'],
  config: BuilderConfig,
): void {
  const IND2 = INDENT2;
  const IND3 = INDENT3;
  const IND4 = INDENT3 + INDENT1;
  const IND5 = IND4 + INDENT1;

  // ── 先平后开：反手（平多 + 开空）语义正确；平仓后刷新持仓快照 ──
  if (rule.close) {
    push(`${IND2}for pos in positions:`);
    push(`${IND3}if pos.direction == "${rule.close}":`);
    push(`${IND4}ctx.log.info("BUILD: close ${rule.close === 'buy' ? 'long' : 'short'}")`);
    push(`${IND4}result = ctx.close(pos.id)`);
    push(`${IND4}if not result.success:`);
    push(`${IND5}ctx.log.warn(f"close failed: {result.error_code} - {result.error_message}")`);
    if (rule.open) push(`${IND2}positions = ctx.get_positions()`);
  }

  if (rule.open) {
    const verb = rule.open === 'buy' ? 'open long' : 'open short';
    const fn = rule.open === 'buy' ? 'buy' : 'sell';
    const volExpr = emitVolumeExpr(config);
    // P3: entry gate — max_position_count 从 risk_blocks 取，缺省 1（即一次一仓，等价旧 if not positions）
    const maxCount = config.risk_blocks?.find((rb) => rb.kind === 'max_position_count')?.count ?? 1;
    push(`${IND2}if len(positions) < ${maxCount}:`);
    push(`${IND3}ctx.log.info("BUILD: ${verb}")`);
    // risk_percent 模式下 volume 计算可能抛 ValueError，加 try 保护
    if (config.sizing?.kind === 'risk_percent') {
      push(`${IND3}try:`);
      push(`${IND4}_vol = ${volExpr}`);
      push(`${IND4}if _vol <= 0:`);
      push(`${IND5}ctx.log.warn("risk_percent 计算出 volume <= 0，跳过开仓")`);
      push(`${IND5}continue`);
      push(`${IND3}except (ValueError, ZeroDivisionError) as _e:`);
      push(`${IND4}ctx.log.warn(f"risk_percent volume 计算失败: {_e}")`);
      push(`${IND4}continue`);
      push(`${IND3}result = ctx.${fn}(ctx.symbol, _vol)`);
    } else {
      push(`${IND3}result = ctx.${fn}(ctx.symbol, float(${volExpr}))`);
    }
    push(`${IND3}if not result.success:`);
    push(`${IND4}ctx.log.warn(f"${fn} failed: {result.error_code} - {result.error_message}")`);
  }
}

/** 生成开仓手数的表达式字符串。 */
function emitVolumeExpr(config: BuilderConfig): string {
  const s = config.sizing;
  if (!s) return 'p["volume"]';
  if (s.kind === 'fixed') return JSON.stringify(s.volume);
  // risk_percent
  // volume = (equity * rp%%) / (sl_pips * point * contract_size)
  // Python 沙箱 API：ctx.get_account().equity / ctx.get_symbol_info(ctx.symbol).point / .contract_size
  const rp = s.risk_percent / 100;
  const sl = s.sl_pips;
  return `(ctx.get_account().equity * ${formatNumber(rp)}) / (${sl} * ctx.get_symbol_info(ctx.symbol).point * ctx.get_symbol_info(ctx.symbol).contract_size)`;
}

/**
 * 生成 SL/TP 风控代码：遍历 positions，检查每种风控条件，
 * 触发时 ctx.close(pos.id) 平仓。放在 on_bar 规则之后、return 之前。
 *
 * pips 转换为绝对价格差：long 的 SL = open - pips*pip_size；TP = open + pips*pip_size；short 反之。
 * bar.close 在沙箱里已由 positions 的 current_price 等价，直接用 pos.current_price 比较。
 */
function emitRiskChecks(push: (...l: string[]) => void, riskBlocks: RiskBlock[]): void {
  const IND1 = INDENT1;
  const IND2 = INDENT2;
  const IND3 = INDENT3;
  const IND4 = INDENT3 + INDENT1;

  push('');
  push(`${IND1}# ── P3 风控检查 ──`);
  push(`${IND1}positions = ctx.get_positions()`);
  push(`${IND1}for pos in positions:`);

  for (const block of riskBlocks) {
    if (block.kind === 'stop_loss') {
      const abs = `${block.pips} * ctx.get_symbol_info(ctx.symbol).point`;
      push(`${IND2}# SL ${block.pips} pips`);
      push(`${IND2}if pos.direction == "buy" and pos.current_price <= pos.open_price - ${abs}:`);
      push(`${IND3}ctx.log.info(f"SL hit long {pos.id}: open={pos.open_price} cur={pos.current_price}")`);
      push(`${IND3}ctx.close(pos.id)`);
      push(`${IND2}if pos.direction == "sell" and pos.current_price >= pos.open_price + ${abs}:`);
      push(`${IND3}ctx.log.info(f"SL hit short {pos.id}: open={pos.open_price} cur={pos.current_price}")`);
      push(`${IND3}ctx.close(pos.id)`);
    } else if (block.kind === 'take_profit') {
      const abs = `${block.pips} * ctx.get_symbol_info(ctx.symbol).point`;
      push(`${IND2}# TP ${block.pips} pips`);
      push(`${IND2}if pos.direction == "buy" and pos.current_price >= pos.open_price + ${abs}:`);
      push(`${IND3}ctx.log.info(f"TP hit long {pos.id}: open={pos.open_price} cur={pos.current_price}")`);
      push(`${IND3}ctx.close(pos.id)`);
      push(`${IND2}if pos.direction == "sell" and pos.current_price <= pos.open_price - ${abs}:`);
      push(`${IND3}ctx.log.info(f"TP hit short {pos.id}: open={pos.open_price} cur={pos.current_price}")`);
      push(`${IND3}ctx.close(pos.id)`);
    } else if (block.kind === 'trailing_stop') {
      const trailAbs = `${block.pips} * ctx.get_symbol_info(ctx.symbol).point`;
      const key = `trail_${block.id}`;
      push(`${IND2}# Trailing Stop ${block.pips} pips（track extremum）`);
      // 初始化：首次遇到该持仓，记录入场价为当前极值
      push(`${IND2}_tkey = f"${key}_{pos.id}"`);
      push(`${IND2}if _tkey not in ctx.storage:`);
      push(`${IND3}ctx.storage[_tkey] = pos.open_price`);
      push(`${IND2}_ext = ctx.storage[_tkey]`);
      // long：跟踪最高价；short：跟踪最低价
      push(`${IND2}if pos.direction == "buy":`);
      push(`${IND3}_ext = max(_ext, pos.current_price)`);
      push(`${IND3}ctx.storage[_tkey] = _ext`);
      push(`${IND3}if pos.current_price <= _ext - ${trailAbs}:`);
      push(`${IND4}ctx.log.info(f"TRAIL hit long {pos.id}: ext={_ext} cur={pos.current_price}")`);
      push(`${IND4}ctx.close(pos.id)`);
      push(`${IND4}del ctx.storage[_tkey]`);
      push(`${IND2}elif pos.direction == "sell":`);
      push(`${IND3}_ext = min(_ext, pos.current_price)`);
      push(`${IND3}ctx.storage[_tkey] = _ext`);
      push(`${IND3}if pos.current_price >= _ext + ${trailAbs}:`);
      push(`${IND4}ctx.log.info(f"TRAIL hit short {pos.id}: ext={_ext} cur={pos.current_price}")`);
      push(`${IND4}ctx.close(pos.id)`);
      push(`${IND4}del ctx.storage[_tkey]`);
    } else if (block.kind === 'max_holding_time') {
      push(`${IND2}# Max Holding ${block.minutes} min`);
      push(`${IND2}_opened = pos.opened_at`);
      push(`${IND2}_elapsed_min = (ctx.now - _opened).total_seconds() / 60.0`);
      push(`${IND2}if _elapsed_min >= ${block.minutes}:`);
      push(`${IND3}ctx.log.info(f"MAX_HOLD hit {pos.id}: elapsed={_elapsed_min:.1f}min >= ${block.minutes}")`);
      push(`${IND3}ctx.close(pos.id)`);
    } else if (block.kind === 'daily_drawdown_cut') {
      push(`${IND2}# daily_drawdown_cut (${block.percent}%) — 账户级风控，待接入`);
    } else if (block.kind === 'cooldown') {
      push(`${IND2}# cooldown (${block.minutes} min) — 账户级风控，待接入`);
    }
    // max_position_count 是 entry gate，在 emitActions 开仓分支处理，不在此生成持仓检查代码
  }

  // 清理已平仓但 storage 里残留的 trailing key
  push(`${IND1}_live_ids = {p.id for p in positions}`);
  push(`${IND1}for _k in list(ctx.storage.keys()):`);
  push(`${IND2}if _k.startswith("trail_") and not any(_k.endswith("_" + _id) for _id in _live_ids):`);
  push(`${IND3}del ctx.storage[_k]`);

  push(`${IND1}# 风控完成`);
}
