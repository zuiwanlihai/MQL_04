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

/**
 * 风控积木（P3）。
 *
 * stop_loss / take_profit — 价格阈值，每根 K 线检查，策略级。
 * trailing_stop          — 跟随极值的移动止损，用 ctx.storage 跟踪入场后最高/最低价，策略级。
 * max_holding_time        — 最长持仓时长（分钟），pos.opened_at 对比 ctx.now，策略级。
 * max_position_count     — 最多同时持仓数（开仓前检查，entry gate），策略级。
 * daily_drawdown_cut / cooldown — 账户级熔断，依赖账户数据，暂标记"待接入"。
 */
export type RiskBlock =
  | {
      id: string;
      kind: 'stop_loss';
      /** 点数（pips），正数。例如 SL=50 表示 50 pips 止损。 */
      pips: number;
    }
  | {
      id: string;
      kind: 'take_profit';
      /** 点数（pips），正数。例如 TP=100 表示 100 pips 止盈。 */
      pips: number;
    }
  | {
      id: string;
      kind: 'trailing_stop';
      /** 移动止损距离（pips），正数。例如 trail=30 表示跟踪最高/最低价 30 pips 回撤触发。 */
      pips: number;
    }
  | {
      id: string;
      kind: 'max_holding_time';
      /** 最长持仓（分钟），正数。超过后强制平仓。 */
      minutes: number;
    }
  | {
      id: string;
      kind: 'daily_drawdown_cut';
      /** 日回撤熔断（%），正数。账户级风控，待接入。 */
      percent: number;
    }
  | {
      id: string;
      kind: 'cooldown';
      /** 冷却期（分钟）。强制平仓后多少分钟不开新仓。账户级风控，待接入。 */
      minutes: number;
    }
  | {
      id: string;
      kind: 'max_position_count';
      /** 最多同时持仓数，正整数。例如 1 = 一次一仓（默认行为）；3 = 最多 3 个持仓。 */
      count: number;
    };

/** 仓位模式：固定手数 / 账户权益比例。 */
export type PositionSizing =
  | { kind: 'fixed'; volume: number }
  | {
      kind: 'risk_percent';
      /** 每次开仓风险占账户权益比例，范围 (0, 100]，例如 1 表示 1%。 */
      risk_percent: number;
      /** 止损点数（pips），用于反推手数：volume = (equity * risk%%) / (sl_pips * pip_size)。 */
      sl_pips: number;
    };

export interface BuilderConfig {
  version: 1;
  symbol: string;
  timeframe: string;
  /** 旧字段：固定手数。当 sizing 存在时被忽略（向后兼容）。 */
  volume: number;
  direction: DirectionLimit;
  indicators: IndicatorBlock[];
  rules: Rule[];
  /** P3 新增：仓位模式。缺省 = 用 volume 固定手数。 */
  sizing?: PositionSizing;
  /** P3 新增：风控积木列表。缺省 = 无风控。 */
  risk_blocks?: RiskBlock[];
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
