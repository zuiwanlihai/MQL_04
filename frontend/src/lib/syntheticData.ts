/**
 * 合成 K 线数据生成器
 *
 * 策略：
 *   1. 先按 M1（1 分钟）粒度生成 OHLCV 基础数据（几何布朗运动近似 + 形状趋势漂移）
 *   2. 其他周期（M5/M15/M30/H1/H2/H4/D1/W1/MN1/Y1）都从 M1 聚合得出
 *      - 固定分钟数（M5/M15/M30/H1/H2/H4）：连续 N 根分组
 *      - 日历边界（D1/W1/MN1/Y1）：按自然日/周/月/年切分（处理跨午夜）
 *
 * 聚合规则：open = 分组第一根的 open, high = max, low = min, close = 最后一根的 close, vol = sum
 */

export interface Candle {
  /** unix 秒级时间戳 */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** 周期 → 分钟数（固定分钟类）或特殊标记（日历类） */
export type Timeframe =
  | 'M1' | 'M5' | 'M15' | 'M30'
  | 'H1' | 'H2' | 'H4'
  | 'D1' | 'W1' | 'MN1' | 'Y1';

const FIXED_MINUTES: Record<Exclude<Timeframe, 'D1' | 'W1' | 'MN1' | 'Y1'>, number> = {
  M1: 1,
  M5: 5,
  M15: 15,
  M30: 30,
  H1: 60,
  H2: 120,
  H4: 240,
};

/** 最大 M1 数据量护栏（浏览器端 JS 生成上限） */
export const MAX_M1_BARS = 1_300_000;

// ─────────────────────────────────────────────────────────────────────
// 形状趋势
// ─────────────────────────────────────────────────────────────────────

/** 日线级别宏观形状 */
export type TrendShape =
  | 'random'       // 随机震荡（纯随机漫步，无方向倾向）
  | 'uptrend'      // 单边上涨
  | 'downtrend'    // 单边下跌
  | 'vShape'       // V 形（先跌后涨）
  | 'invVShape'    // 倒 V（先涨后跌）
  | 'wShape'       // W 形双重底
  | 'mShape';      // M 形双顶

export const SHAPE_LABELS: Record<TrendShape, string> = {
  random: '随机震荡',
  uptrend: '单边上涨',
  downtrend: '单边下跌',
  vShape: 'V 形反转',
  invVShape: '倒 V 反转',
  wShape: 'W 形（双重底）',
  mShape: 'M 形（双顶）',
};

/**
 * 形状趋势系数：返回当前进度（0~1）处的趋势方向 [-1, +1]
 * 所有形状从 0 开始、到 0 结束（积分对称），中间按形状分段拐点。
 * - +1 表示最强向上漂移
 * - -1 表示最强向下漂移
 * - 0 表示无方向倾向
 *
 * 公式验证（每段起点终点线性插值）：
 *   vShape:     [0,0.5] 0→-1 (c=-2p),  [0.5,1] +1→0 (c=-2p+2)
 *   invVShape:  [0,0.5] 0→+1 (c=2p),   [0.5,1] -1→0 (c=2p-2)
 *   wShape:     [0,0.25] 0→-1 (c=-4p), [0.25,0.75] +1→-1 (c=-4p+2), [0.75,1] +1→0 (c=-4p+4)
 *   mShape:     wShape 取反: c = 4p, 4p-2, 4p-4
 */
function trendCoefficient(progress: number, shape: TrendShape): number {
  const p = progress;
  switch (shape) {
    case 'random':
      return 0;

    case 'uptrend':
      return 1;

    case 'downtrend':
      return -1;

    case 'vShape':
      return p < 0.5 ? -2 * p : -2 * p + 2;

    case 'invVShape':
      return p < 0.5 ? 2 * p : 2 * p - 2;

    case 'wShape':
      if (p < 0.25) return -4 * p;
      if (p < 0.75) return -4 * p + 2;
      return -4 * p + 4;

    case 'mShape':
      if (p < 0.25) return 4 * p;
      if (p < 0.75) return 4 * p - 2;
      return 4 * p - 4;
  }
}

// ─────────────────────────────────────────────────────────────────────
// 1. 生成 M1 基础数据
// ─────────────────────────────────────────────────────────────────────

/**
 * 按 1 分钟粒度生成合成 K 线（几何布朗运动近似 + 可选形状趋势）。
 *
 * @param start 起始时间（Date 或 ISO 字符串）
 * @param end   结束时间
 * @param basePrice 起始价格（默认 1900，适合 XAUUSD）
 * @param volatility 单根 M1 的波动率（默认 0.0005 = 0.05%）
 * @param shape 宏观形状（默认 'random' 纯随机漫步）
 * @param trendStrength 整个时间段最大趋势位移百分比（默认 0.15 = 15%），
 *                      仅在 shape !== 'random' 时生效
 */
export function generateM1Candles(
  start: Date | string,
  end: Date | string,
  basePrice = 1900,
  volatility = 0.0005,
  shape: TrendShape = 'random',
  trendStrength = 0.15,
): Candle[] {
  const t0 = new Date(start).getTime();
  const t1 = new Date(end).getTime();
  if (t1 <= t0) return [];

  const totalMinutes = Math.floor((t1 - t0) / 60_000);
  if (totalMinutes > MAX_M1_BARS) {
    throw new Error(
      `时间范围过大：${totalMinutes} 根 M1 超过上限 ${MAX_M1_BARS}。` +
      '请缩小范围或选择更高周期。',
    );
  }

  const candles: Candle[] = new Array(totalMinutes);
  let price = basePrice;

  const trendPerBar = (shape === 'random') ? 0 : trendStrength / totalMinutes;

  for (let i = 0; i < totalMinutes; i++) {
    const ts = Math.floor((t0 + i * 60_000) / 1000);
    const r1 = Math.random() || 1e-9;
    const r2 = Math.random();
    const z = Math.sqrt(-2 * Math.log(r1)) * Math.cos(2 * Math.PI * r2);
    const noise = volatility * z;

    const progress = totalMinutes === 1 ? 0 : i / (totalMinutes - 1);
    const trend = trendCoefficient(progress, shape) * trendPerBar;
    const drift = noise + trend;

    const open = price;
    const close = open * (1 + drift);
    const spread = Math.abs(drift) * 0.6 + volatility * 0.3;
    const high = Math.max(open, close) * (1 + spread);
    const low = Math.min(open, close) * (1 - spread);
    const volume = Math.round(50 + Math.random() * 200);

    candles[i] = { time: ts, open, high, low, close, volume };
    price = close;
  }

  return candles;
}

// ─────────────────────────────────────────────────────────────────────
// 2. 聚合
// ─────────────────────────────────────────────────────────────────────

function toLocalParts(ts: number) {
  const d = new Date(ts * 1000);
  return {
    y: d.getFullYear(),
    m: d.getMonth() + 1,
    d: d.getDate(),
    h: d.getHours(),
    w: d.getDay(),
  };
}

const PAD = (n: number) => String(n).padStart(2, '0');

function boundaryKey(ts: number, tf: Timeframe): string {
  const p = toLocalParts(ts);
  switch (tf) {
    case 'D1':  return `${p.y}-${PAD(p.m)}-${PAD(p.d)}`;
    case 'W1':  {
      const date = new Date(ts * 1000);
      const day = date.getDay() || 7;
      const monday = new Date(date);
      monday.setDate(date.getDate() - day + 1);
      return `${monday.getFullYear()}-${PAD(monday.getMonth() + 1)}-${PAD(monday.getDate())}`;
    }
    case 'MN1': return `${p.y}-${PAD(p.m)}`;
    case 'Y1':  return `${p.y}`;
    default:    return '';
  }
}

function aggregateGroup(bucket: Candle[]): Candle {
  let high = -Infinity;
  let low = Infinity;
  let vol = 0;
  for (const c of bucket) {
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
    vol += c.volume;
  }
  return {
    time: bucket[0].time,
    open: bucket[0].open,
    high,
    low,
    close: bucket[bucket.length - 1].close,
    volume: vol,
  };
}

export function aggregateCandles(m1: Candle[], timeframe: Timeframe): Candle[] {
  if (timeframe === 'M1') return m1.slice();

  if (timeframe in FIXED_MINUTES) {
    const n = FIXED_MINUTES[timeframe as keyof typeof FIXED_MINUTES];
    const out: Candle[] = [];
    for (let i = 0; i + n <= m1.length; i += n) {
      out.push(aggregateGroup(m1.slice(i, i + n)));
    }
    return out;
  }

  const buckets = new Map<string, Candle[]>();
  for (const c of m1) {
    const key = boundaryKey(c.time, timeframe);
    let arr = buckets.get(key);
    if (!arr) { arr = []; buckets.set(key, arr); }
    arr.push(c);
  }
  return Array.from(buckets.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([, group]) => aggregateGroup(group));
}

export function generateCandles(
  start: Date | string,
  end: Date | string,
  timeframe: Timeframe,
  basePrice = 1900,
  volatility = 0.0005,
  shape: TrendShape = 'random',
  trendStrength = 0.15,
): Candle[] {
  const m1 = generateM1Candles(start, end, basePrice, volatility, shape, trendStrength);
  return aggregateCandles(m1, timeframe);
}
