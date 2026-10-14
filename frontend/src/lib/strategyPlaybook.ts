/**
 * AI 策略说明书（六段规则）：开仓/平仓/止损/止盈/移动止损/移动止盈。
 *
 * 按策略参数键探测模板家族（与 strategyParams.resolveSchema 同一探测规则），
 * 内容与 shared/sdk/templates 各模板源码的真实逻辑一一对应；策略未使用的
 * 保护机制如实写「无」，不虚构。
 */

export interface PlaybookSection {
  title: string;
  body: string[];
}

export interface Playbook {
  family: string;
  label: string;
  sections: PlaybookSection[];
}

const NONE = (what: string) => [`无 —— 本策略不使用${what}。`];

const GRID: Playbook = {
  family: 'grid',
  label: '网格交易',
  sections: [
    {
      title: '开仓条件',
      body: [
        '收盘价 ≤ 锚定价 − 第 N 档 × 网格间距（点换算为价格距离）时，市价买入该档。',
        '同一档位只持一单；总持仓层数不超过「最大网格层数」。',
        '锚定价 = 启动时的最新买价，重启后自动恢复（不重置）。',
      ],
    },
    {
      title: '平仓条件',
      body: ['任一持仓满足「收盘价 ≥ 入场价 + 一格间距」即市价平仓（赚一格就走）。'],
    },
    { title: '止损条件', body: NONE('止损') },
    { title: '止盈条件', body: ['固定目标止盈：每档 +1 格间距即为目标，由平仓条件触发。'] },
    { title: '移动止损条件', body: NONE('移动止损') },
    { title: '移动止盈条件', body: NONE('移动止盈') },
  ],
};

const DOUBLE_MA: Playbook = {
  family: 'double_ma',
  label: '双均线交叉',
  sections: [
    {
      title: '开仓条件',
      body: [
        '快线均线上穿慢线均线（金叉）且当前无任何持仓时，市价买入。',
        '快/慢线 = 最近 fast_period / slow_period 根收盘价的简单平均。',
      ],
    },
    { title: '平仓条件', body: ['快线均线下穿慢线均线（死叉）时，平掉全部多仓。'] },
    { title: '止损条件', body: NONE('止损') },
    { title: '止盈条件', body: NONE('固定止盈') },
    { title: '移动止损条件', body: NONE('移动止损') },
    { title: '移动止盈条件', body: NONE('移动止盈') },
  ],
};

const CHANNEL: Playbook = {
  family: 'channel_breakout',
  label: '唐奇安通道突破',
  sections: [
    {
      title: '开仓条件',
      body: [
        '收盘价突破前 entry_window 根 K 线的最高价（不含当根）且当前无多仓时，市价买入。',
      ],
    },
    { title: '平仓条件', body: ['收盘价跌破前 exit_window 根 K 线的最低价时，平掉全部多仓。'] },
    { title: '止损条件', body: NONE('止损') },
    { title: '止盈条件', body: NONE('固定止盈') },
    { title: '移动止损条件', body: NONE('移动止损') },
    { title: '移动止盈条件', body: NONE('移动止盈') },
  ],
};

const RSI: Playbook = {
  family: 'rsi_mean_reversion',
  label: 'RSI 均值回归',
  sections: [
    { title: '开仓条件', body: ['RSI < 超卖阈值（默认 30）且当前无多仓时，市价买入。'] },
    { title: '平仓条件', body: ['RSI > 超买阈值（默认 70）时，平掉全部多仓。'] },
    { title: '止损条件', body: NONE('止损') },
    { title: '止盈条件', body: NONE('固定止盈') },
    { title: '移动止损条件', body: NONE('移动止损') },
    { title: '移动止盈条件', body: NONE('移动止盈') },
  ],
};

const ATR_STOP: Playbook = {
  family: 'atr_stop_management',
  label: '双均线 + ATR 移动止损',
  sections: [
    {
      title: '开仓条件',
      body: [
        '快线均线上穿慢线均线（金叉）且当前无多仓时，市价买入。',
        '买入同时记录初始止损价 = 成交时收盘价 − ATR × 倍数。',
      ],
    },
    { title: '平仓条件', body: ['收盘价 ≤ 当前止损线时市价平仓，随后清空止损记录。'] },
    {
      title: '止损条件',
      body: [
        '初始止损 = 收盘价 − ATR × 倍数（策略侧市价触发，非券商保护单）。',
      ],
    },
    { title: '止盈条件', body: ['无固定止盈 —— 趋势跟踪策略，利润由移动止损逐步锁定。'] },
    {
      title: '移动止损条件',
      body: [
        '每根 K 线将止损线抬升为 max(历史止损线, 收盘价 − ATR × 倍数)，只升不降。',
      ],
    },
    { title: '移动止盈条件', body: NONE('独立的移动止盈') },
  ],
};

const V4: Playbook = {
  family: 'boll_macd_ma_v4',
  label: 'BOLL+MACD+MA 四维评分',
  sections: [
    {
      title: '开仓条件',
      body: [
        '四维评分制，加权总分 ≥ 「开仓最低总分」（默认 70）才允许开仓：',
        '① 趋势门槛：EMA200 斜率达阈值才放开对应方向；② 布林带宽收窄（挤压后突破）；',
        '③ MACD 金叉/死叉佐证（距零轴过远按 ATR 折算扣分）；④ 位置分为必要分（回踩深度）。',
        '同一时间只持一单；相关性品种同向持仓时拒开。',
      ],
    },
    {
      title: '平仓条件',
      body: [
        '止损线/止盈/时间止损/跟踪线任一触发即离场；另设策略级最大回撤熔断（触发全平）。',
      ],
    },
    {
      title: '止损条件',
      body: [
        '固定止损：多头 = 布林下轨 − sl_atr_long × ATR，空头 = 布林上轨 + sl_atr_short × ATR。',
      ],
    },
    {
      title: '止盈条件',
      body: [
        '浮盈比例达 first_tp%（多 50% / 空 60%）后分批止盈（TP1 部分平 + 其余全平）。',
        '时间止损：持仓超过 time_exit 根 K 线（多 8 / 空 6）强制离场。',
      ],
    },
    {
      title: '移动止损条件',
      body: [
        'TP1 之后启用跟踪线，只升不降：阶段 1 = 布林中轨（保本线）；',
        '阶段 2 = max(上轨 × 0.95, 上轨 − 0.5 × ATR)；收盘跌破跟踪线即市价离场。',
      ],
    },
    { title: '移动止盈条件', body: ['跟踪线在盈利阶段即充当移动止盈，无独立机制。'] },
  ],
};

const FAMILIES: { match: (s: Set<string>) => boolean; pb: Playbook }[] = [
  { match: (s) => s.has('min_score') && s.has('boll_dev'), pb: V4 },
  { match: (s) => s.has('grid_step'), pb: GRID },
  { match: (s) => s.has('entry_window') && s.has('exit_window'), pb: CHANNEL },
  { match: (s) => s.has('oversold') && s.has('overbought'), pb: RSI },
  { match: (s) => s.has('atr_mult') && s.has('atr_period'), pb: ATR_STOP },
  { match: (s) => s.has('fast_period') && s.has('slow_period'), pb: DOUBLE_MA },
];

/** 按策略参数键解析 AI 说明书；未知家族返回 null（回落通用文案）。 */
export function resolvePlaybook(keys?: string[] | null): Playbook | null {
  if (!keys || keys.length === 0) return null;
  const set = new Set(keys);
  for (const f of FAMILIES) if (f.match(set)) return f.pb;
  return null;
}
