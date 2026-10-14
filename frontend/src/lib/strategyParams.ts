/**
 * v4.0 策略（BOLL+MACD+MA 完整版）参数面板 schema。
 *
 * 前端维护参数元数据（类型/分组/中文标签/默认值），参数面板据此渲染结构化表单，
 * 产出 `Record<string, unknown>` 提交到后端，经沙箱 `payload.parameters` 覆盖策略
 * `ctx.declare` 的默认值而端到端生效。
 */

export type ParamFieldType = 'int' | 'float' | 'bool' | 'enum' | 'text';

export interface ParamField {
  key: string;
  label: string;
  type: ParamFieldType;
  default: unknown;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
  hint?: string;
}

export interface ParamGroup {
  id: string;
  label: string;
  fields: ParamField[];
}

const LOT_MODE_OPTIONS = [
  { value: 'risk', label: '风险百分比（risk）' },
  { value: 'fixed', label: '固定手数（fixed）' },
];

export const V4_PARAM_GROUPS: ParamGroup[] = [
  {
    id: 'trend_gate',
    label: '趋势门槛',
    fields: [
      { key: 'ema_period', label: 'EMA 周期', type: 'int', default: 200, min: 20, max: 500, step: 1 },
      { key: 'long_slope_thr', label: '做多斜率阈值', type: 'float', default: 0.0001, step: 0.00001, hint: 'EMA200 斜率 ≥ 此值才允许做多' },
      { key: 'short_slope_thr', label: '做空斜率阈值', type: 'float', default: 0.00008, step: 0.00001, hint: 'EMA200 斜率低于此值才允许做空' },
    ],
  },
  {
    id: 'boll',
    label: 'BOLL · 带宽',
    fields: [
      { key: 'boll_period', label: 'BOLL 周期', type: 'int', default: 20, min: 5, max: 200, step: 1 },
      { key: 'boll_dev', label: '标准差倍数', type: 'float', default: 2.0, min: 1.0, max: 5.0, step: 0.1 },
      { key: 'bw_lookback_long', label: '带宽回顾（多）', type: 'int', default: 20, min: 5, max: 100, step: 1 },
      { key: 'bw_lookback_short', label: '带宽回顾（空）', type: 'int', default: 15, min: 5, max: 100, step: 1 },
    ],
  },
  {
    id: 'macd',
    label: 'MACD',
    fields: [
      { key: 'macd_fast', label: '快线 EMA', type: 'int', default: 12, min: 2, max: 100, step: 1 },
      { key: 'macd_slow', label: '慢线 EMA', type: 'int', default: 26, min: 5, max: 200, step: 1 },
      { key: 'macd_signal', label: '信号线', type: 'int', default: 9, min: 2, max: 100, step: 1 },
      { key: 'zero_atr_mult', label: '零轴 ATR 倍数', type: 'float', default: 0.1, min: 0, max: 5, step: 0.05, hint: '金叉距零轴 < ATR×此值才计分' },
    ],
  },
  {
    id: 'scoring',
    label: '评分阈值',
    fields: [
      { key: 'min_score', label: '开仓最低总分', type: 'int', default: 70, min: 0, max: 100, step: 1 },
      { key: 'shadow_min', label: '影子记录下限', type: 'int', default: 60, min: 0, max: 100, step: 1 },
      { key: 'min_position_score', label: '位置分必要分', type: 'int', default: 20, min: 0, max: 100, step: 1, hint: '位置分（0~30）必须 ≥ 此值' },
    ],
  },
  {
    id: 'risk_lot',
    label: '风险与手数',
    fields: [
      { key: 'risk_pct_long', label: '做多风险 %', type: 'float', default: 1.0, min: 0.1, max: 10, step: 0.1, unit: '%' },
      { key: 'risk_pct_short', label: '做空风险 %', type: 'float', default: 0.8, min: 0.1, max: 10, step: 0.1, unit: '%' },
      { key: 'sl_atr_long', label: '止损 ATR（多）', type: 'float', default: 1.0, min: 0.2, max: 5, step: 0.1 },
      { key: 'sl_atr_short', label: '止损 ATR（空）', type: 'float', default: 1.2, min: 0.2, max: 5, step: 0.1 },
      { key: 'lot_mode', label: '手数模式', type: 'enum', default: 'risk', options: LOT_MODE_OPTIONS },
      { key: 'fixed_volume', label: '固定手数', type: 'float', default: 0.1, min: 0.01, max: 10, step: 0.01, hint: '仅 lot_mode=fixed 时生效' },
      { key: 'value_per_unit', label: '每单位名义价值', type: 'float', default: 100000, min: 1, max: 1000000, step: 1000 },
      { key: 'min_lot', label: '最小手数', type: 'float', default: 0.01, min: 0.01, max: 1, step: 0.01 },
      { key: 'max_lot', label: '最大手数', type: 'float', default: 5.0, min: 0.1, max: 100, step: 0.1 },
    ],
  },
  {
    id: 'vol_p1',
    label: '波动率仓位（P1）',
    fields: [
      { key: 'atr_pct_window', label: 'ATR 百分位窗口', type: 'int', default: 100, min: 20, max: 500, step: 10 },
      { key: 'atr_pct_high', label: '高波动分位（×低系数）', type: 'float', default: 80.0, min: 50, max: 100, step: 1, unit: '%' },
      { key: 'atr_pct_low', label: '低波动分位（×高系数）', type: 'float', default: 30.0, min: 0, max: 50, step: 1, unit: '%' },
      { key: 'vol_factor_high', label: '高波动手数系数', type: 'float', default: 0.70, min: 0.1, max: 2, step: 0.05 },
      { key: 'vol_factor_low', label: '低波动手数系数', type: 'float', default: 1.10, min: 0.1, max: 2, step: 0.05 },
    ],
  },
  {
    id: 'corr_slope_p2',
    label: '相关性 · 斜率（P2）',
    fields: [
      { key: 'corr_symbols', label: '相关性品种（逗号分隔）', type: 'text', default: '', hint: '留空则关闭相关性门槛' },
      { key: 'slope_window', label: '斜率窗口', type: 'int', default: 30, min: 5, max: 200, step: 1 },
      { key: 'slope_persist_bars', label: '斜率持续 K 线', type: 'int', default: 120, min: 10, max: 500, step: 10 },
      { key: 'slope_review_bars', label: '斜率复核窗口', type: 'int', default: 240, min: 10, max: 1000, step: 10 },
    ],
  },
  {
    id: 'exit',
    label: '出场',
    fields: [
      { key: 'first_tp_long', label: '首止盈（多，点）', type: 'int', default: 50, min: 1, max: 1000, step: 1 },
      { key: 'first_tp_short', label: '首止盈（空，点）', type: 'int', default: 60, min: 1, max: 1000, step: 1 },
      { key: 'time_exit_long', label: '时间止损（多，根）', type: 'int', default: 8, min: 1, max: 100, step: 1 },
      { key: 'time_exit_short', label: '时间止损（空，根）', type: 'int', default: 6, min: 1, max: 100, step: 1 },
    ],
  },
  {
    id: 'circuit_breaker',
    label: '熔断',
    fields: [
      { key: 'max_consec', label: '连续亏损（冷却）', type: 'int', default: 3, min: 1, max: 20, step: 1 },
      { key: 'cooldown_hours', label: '冷却时长（小时）', type: 'int', default: 4, min: 0, max: 72, step: 1 },
      { key: 'max_consec_hard', label: '连续亏损（硬停）', type: 'int', default: 5, min: 1, max: 20, step: 1 },
      { key: 'cooldown_hard_hours', label: '硬停时长（小时）', type: 'int', default: 24, min: 0, max: 168, step: 1 },
      { key: 'daily_max_loss_pct', label: '日亏损熔断 %', type: 'float', default: 3.0, min: 0.1, max: 50, step: 0.1, unit: '%' },
      { key: 'weekly_max_loss_pct', label: '周亏损熔断 %', type: 'float', default: 6.0, min: 0.1, max: 50, step: 0.1, unit: '%' },
      { key: 'max_drawdown_pct', label: '最大回撤熔断 %', type: 'float', default: 15.0, min: 1, max: 80, step: 0.5, unit: '%' },
    ],
  },
  {
    id: 'data',
    label: '数据层',
    fields: [
      { key: 'record_cap', label: '特征/影子记录上限', type: 'int', default: 500, min: 50, max: 5000, step: 10 },
    ],
  },
  {
    id: 'warmup',
    label: '预热',
    fields: [
      { key: 'history', label: '预热 K 线数', type: 'int', default: 400, min: 50, max: 2000, step: 10 },
    ],
  },
];

/** v4.0 家族全部参数键（探测/回退用）。 */
export const V4_PARAM_KEYS: string[] = V4_PARAM_GROUPS.flatMap((g) =>
  g.fields.map((f) => f.key),
);

/* ── SDK v1 模板参数面板（与 shared/sdk/templates ctx.declare 对应） ── */

const VOLUME_FIELD: ParamField = {
  key: 'volume', label: '下单手数', type: 'float',
  default: 0.1, min: 0.01, max: 100, step: 0.01,
};

const GRID_PARAM_GROUPS: ParamGroup[] = [
  {
    id: 'grid', label: '网格',
    fields: [
      { key: 'grid_step', label: '网格间距（点）', type: 'int', default: 200, min: 1, max: 10000, step: 10, hint: '点=券商最小报价单位，跨品种通用：EURUSD 200点=0.0020，XAUUSD 200点=2.00' },
      { key: 'max_levels', label: '最大网格层数', type: 'int', default: 5, min: 1, max: 50, step: 1, hint: '同时持有的网格挂单上限' },
    ],
  },
  { id: 'lot', label: '手数', fields: [VOLUME_FIELD] },
];

const DOUBLE_MA_PARAM_GROUPS: ParamGroup[] = [
  {
    id: 'ma', label: '均线',
    fields: [
      { key: 'fast_period', label: '快线周期', type: 'int', default: 5, min: 1, max: 500, step: 1 },
      { key: 'slow_period', label: '慢线周期', type: 'int', default: 20, min: 2, max: 1000, step: 1, hint: '必须大于快线周期' },
    ],
  },
  { id: 'lot', label: '手数', fields: [VOLUME_FIELD] },
];

const CHANNEL_PARAM_GROUPS: ParamGroup[] = [
  {
    id: 'channel', label: '唐奇安通道',
    fields: [
      { key: 'entry_window', label: '入场回看（根）', type: 'int', default: 20, min: 2, max: 500, step: 1, hint: '突破 N 根最高价入场' },
      { key: 'exit_window', label: '出场回看（根）', type: 'int', default: 10, min: 1, max: 500, step: 1, hint: '应小于入场回看' },
    ],
  },
  { id: 'lot', label: '手数', fields: [VOLUME_FIELD] },
];

const RSI_PARAM_GROUPS: ParamGroup[] = [
  {
    id: 'rsi', label: 'RSI 均值回归',
    fields: [
      { key: 'rsi_period', label: 'RSI 周期', type: 'int', default: 14, min: 2, max: 100, step: 1 },
      { key: 'oversold', label: '超卖阈值', type: 'float', default: 30, min: 1, max: 90, step: 1, hint: 'RSI 低于此值做多' },
      { key: 'overbought', label: '超买阈值', type: 'float', default: 70, min: 10, max: 99, step: 1, hint: 'RSI 高于此值做空' },
    ],
  },
  { id: 'lot', label: '手数', fields: [VOLUME_FIELD] },
];

const ATR_STOP_PARAM_GROUPS: ParamGroup[] = [
  {
    id: 'ma', label: '均线',
    fields: [
      { key: 'fast_period', label: '快线周期', type: 'int', default: 10, min: 1, max: 500, step: 1 },
      { key: 'slow_period', label: '慢线周期', type: 'int', default: 30, min: 2, max: 1000, step: 1 },
    ],
  },
  {
    id: 'atr', label: 'ATR 移动止损',
    fields: [
      { key: 'atr_period', label: 'ATR 周期', type: 'int', default: 14, min: 2, max: 100, step: 1 },
      { key: 'atr_mult', label: 'ATR 倍数', type: 'float', default: 2.0, min: 0.5, max: 10, step: 0.1, hint: '止损距离 = ATR × 此值' },
    ],
  },
  { id: 'lot', label: '手数', fields: [VOLUME_FIELD] },
];

/** 按参数键探测模板家族并返回面板 schema；未知家族返回 null（回落 JSON 编辑）。 */
export function resolveSchema(keys?: string[] | null): ParamGroup[] | null {
  if (!keys || keys.length === 0) return null;
  const set = new Set(keys);
  if (set.has('min_score') && set.has('boll_dev')) return V4_PARAM_GROUPS;
  if (set.has('grid_step')) return GRID_PARAM_GROUPS;
  if (set.has('entry_window') && set.has('exit_window')) return CHANNEL_PARAM_GROUPS;
  if (set.has('oversold') && set.has('overbought')) return RSI_PARAM_GROUPS;
  if (set.has('atr_mult') && set.has('atr_period')) return ATR_STOP_PARAM_GROUPS;
  if (set.has('fast_period') && set.has('slow_period')) return DOUBLE_MA_PARAM_GROUPS;
  return null;
}

/** 按 schema 生成默认值 map。 */
export function defaultValues(groups: ParamGroup[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const g of groups) for (const f of g.fields) out[f.key] = f.default;
  return out;
}

/** 将外部值（模板字符串 / 已存 typed 值）按字段类型规整。 */
export function coerceParam(field: ParamField, raw: unknown): unknown {
  switch (field.type) {
    case 'int': {
      const n = Number(raw);
      return Number.isFinite(n) ? Math.round(n) : field.default;
    }
    case 'float': {
      const n = Number(raw);
      return Number.isFinite(n) ? n : field.default;
    }
    case 'bool':
      return raw === true || raw === 'true' || raw === '1' || raw === 1;
    case 'enum':
      return field.options?.some((o) => o.value === raw) ? String(raw) : String(field.default);
    case 'text':
    default:
      return String(raw ?? field.default ?? '');
  }
}

/** 以 schema 默认值为底，叠加既有 seed（已存在的键按类型规整覆盖）。 */
export function seedValues(
  groups: ParamGroup[],
  seed?: Record<string, unknown> | null,
): Record<string, unknown> {
  const values = defaultValues(groups);
  if (!seed) return values;
  for (const g of groups) {
    for (const f of g.fields) {
      const raw = seed[f.key];
      if (raw === undefined) continue;
      values[f.key] = coerceParam(f, raw);
    }
  }
  return values;
}