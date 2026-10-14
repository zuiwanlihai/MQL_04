/**
 * 可视化策略构建器积木 DSL（P2）。
 *
 * config 整体以 JSONB 存 strategies.builder_config，形状必须稳定：
 * 前端 codegen 依据它生成 Python 策略源码；再次进入构建器按它恢复积木。
 * 所有字段变更需考虑旧 config 的向后兼容（只增不改语义）。
 */

export type IndicatorKind = 'MA' | 'EMA' | 'RSI' | 'BOLL' | 'ATR' | 'MACD';

/** 指标积木：MA/EMA/RSI/BOLL/ATR 用 period；MACD 用 fast/slow/signal；BOLL 额外 std。 */
export interface IndicatorBlock {
  id: string;
  kind: IndicatorKind;
  period: number;
  fast?: number;
  slow?: number;
  signal?: number;
  std?: number;
}

/** 指标输出通道：MA/EMA/RSI/ATR 仅 value；BOLL 三轨；MACD 三线。 */
export type IndicatorOutput = 'value' | 'upper' | 'mid' | 'lower' | 'dif' | 'dea' | 'hist';

/** 条件左右操作数：指标输出 | 收盘价 | 常量。 */
export type Operand =
  | { type: 'indicator'; indicatorId: string; output: IndicatorOutput }
  | { type: 'close' }
  | { type: 'number'; value: number };

export type ConditionOp = 'cross_above' | 'cross_below' | 'greater_than' | 'less_than';

/** 规则 = 条件 + 满足时动作（开仓方向 / 平仓方向，均可空）。 */
export interface Rule {
  id: string;
  op: ConditionOp;
  left: Operand;
  right: Operand;
  open: 'buy' | 'sell' | null;
  close: 'buy' | 'sell' | null;
}

export type DirectionLimit = 'long_only' | 'short_only' | 'both';

export interface BuilderConfig {
  version: 1;
  symbol: string;
  timeframe: string;
  volume: number;
  direction: DirectionLimit;
  indicators: IndicatorBlock[];
  rules: Rule[];
}

export const INDICATOR_META: Record<
  IndicatorKind,
  { label: string; desc: string; outputs: IndicatorOutput[] }
> = {
  MA: { label: 'MA 均线', desc: '简单移动平均', outputs: ['value'] },
  EMA: { label: 'EMA 均线', desc: '指数移动平均', outputs: ['value'] },
  RSI: { label: 'RSI', desc: '相对强弱指标', outputs: ['value'] },
  BOLL: { label: 'BOLL', desc: '布林带（上/中/下轨）', outputs: ['upper', 'mid', 'lower'] },
  ATR: { label: 'ATR', desc: '真实波幅均值', outputs: ['value'] },
  MACD: { label: 'MACD', desc: '异同均线（DIF/DEA/HIST）', outputs: ['dif', 'dea', 'hist'] },
};

export const OUTPUT_LABELS: Record<IndicatorOutput, string> = {
  value: '值',
  upper: '上轨',
  mid: '中轨',
  lower: '下轨',
  dif: 'DIF',
  dea: 'DEA',
  hist: 'HIST',
};

export const OP_LABELS: Record<ConditionOp, string> = {
  cross_above: '上穿',
  cross_below: '下穿',
  greater_than: '大于',
  less_than: '小于',
};

export function emptyConfig(): BuilderConfig {
  return {
    version: 1,
    symbol: 'EURUSD',
    timeframe: 'H1',
    volume: 0.1,
    direction: 'both',
    indicators: [],
    rules: [],
  };
}

let seq = 0;
export function newId(prefix: string): string {
  seq = (seq + 1) % 100000;
  return `${prefix}_${Date.now().toString(36)}${seq.toString(36)}`;
}
