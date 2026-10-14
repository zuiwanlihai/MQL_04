/**
 * K 线技术指标计算（纯函数，PRD M2 P1：主图 MA/BOLL、副图 MACD/RSI/KDJ）。
 *
 * 口径对齐 MT5：EMA 用 SMA 种子；BOLL 标准差除以 n（总体标准差）；
 * RSI 用 Wilder 平滑；KDJ 的 RSV 分母为 0（一字板）时取 50。
 * 所有函数输入按时间升序的 K 线数组，输出仅包含「首个有效值之后」的点。
 */
import type { PriceBar } from '@/types/api';

export interface IndicatorPoint {
  time: number;
  value: number;
}

type SourceFn = (bar: PriceBar) => number;

export const CLOSE: SourceFn = (b) => b.close;

/** 简单移动平均（SMA）。 */
export function sma(bars: PriceBar[], period: number, source: SourceFn = CLOSE): IndicatorPoint[] {
  const out: IndicatorPoint[] = [];
  if (bars.length < period || period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += source(bars[i]);
    if (i >= period) sum -= source(bars[i - period]);
    if (i >= period - 1) out.push({ time: bars[i].time, value: sum / period });
  }
  return out;
}

/**
 * 指数移动平均（EMA）：种子为首 period 个值的 SMA，之后 k = 2/(period+1)。
 * 输入为已提取的源值数组（供 MACD 对 dif 序列二次平滑复用）。
 */
export function emaValues(values: number[], period: number): Array<number | null> {
  const out: Array<number | null> = new Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return out;
  const k = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i];
  let prev = seed / period;
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** 指数移动平均（EMA），对 K 线源值计算。 */
export function ema(bars: PriceBar[], period: number, source: SourceFn = CLOSE): IndicatorPoint[] {
  const values = bars.map(source);
  const smoothed = emaValues(values, period);
  const out: IndicatorPoint[] = [];
  for (let i = 0; i < bars.length; i++) {
    if (smoothed[i] !== null) out.push({ time: bars[i].time, value: smoothed[i] as number });
  }
  return out;
}

/** 布林带（BOLL，MT5 口径：总体标准差）。 */
export interface BollResult {
  mid: IndicatorPoint[];
  upper: IndicatorPoint[];
  lower: IndicatorPoint[];
}

export function boll(bars: PriceBar[], period = 20, mult = 2): BollResult {
  const mid = sma(bars, period);
  const upper: IndicatorPoint[] = [];
  const lower: IndicatorPoint[] = [];
  for (let i = period - 1; i < bars.length; i++) {
    const m = mid[i - period + 1].value;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = bars[j].close - m;
      sq += d * d;
    }
    const sd = Math.sqrt(sq / period);
    const time = bars[i].time;
    upper.push({ time, value: m + mult * sd });
    lower.push({ time, value: m - mult * sd });
  }
  return { mid, upper, lower };
}

/** MACD（MT5 口径：hist = dif - dea，不乘 2）。 */
export interface MacdResult {
  dif: IndicatorPoint[];
  dea: IndicatorPoint[];
  hist: IndicatorPoint[];
}

export function macd(bars: PriceBar[], fast = 12, slow = 26, signal = 9): MacdResult {
  const closes = bars.map((b) => b.close);
  const emaFast = emaValues(closes, fast);
  const emaSlow = emaValues(closes, slow);
  // dif 从两者都可用的位置开始
  const start = slow - 1;
  const difValues: number[] = [];
  for (let i = start; i < bars.length; i++) {
    difValues.push((emaFast[i] as number) - (emaSlow[i] as number));
  }
  const deaSmoothed = emaValues(difValues, signal);
  const dif: IndicatorPoint[] = [];
  const dea: IndicatorPoint[] = [];
  const hist: IndicatorPoint[] = [];
  for (let i = 0; i < difValues.length; i++) {
    const time = bars[start + i].time;
    dif.push({ time, value: difValues[i] });
    const d = deaSmoothed[i];
    if (d !== null) {
      dea.push({ time, value: d });
      hist.push({ time, value: difValues[i] - d });
    }
  }
  return { dif, dea, hist };
}

/** RSI（Wilder 平滑）。 */
export function rsi(bars: PriceBar[], period = 14): IndicatorPoint[] {
  const out: IndicatorPoint[] = [];
  if (bars.length <= period || period <= 0) return out;
  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const ch = bars[i].close - bars[i - 1].close;
    if (ch > 0) avgGain += ch;
    else avgLoss -= ch;
  }
  avgGain /= period;
  avgLoss /= period;
  const push = (time: number, g: number, l: number) => {
    const rs = l === 0 ? Infinity : g / l;
    out.push({ time, value: l === 0 ? 100 : 100 - 100 / (1 + rs) });
  };
  push(bars[period].time, avgGain, avgLoss);
  for (let i = period + 1; i < bars.length; i++) {
    const ch = bars[i].close - bars[i - 1].close;
    avgGain = (avgGain * (period - 1) + Math.max(ch, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-ch, 0)) / period;
    push(bars[i].time, avgGain, avgLoss);
  }
  return out;
}

/** KDJ（9,3,3 标准参数）。 */
export interface KdjResult {
  k: IndicatorPoint[];
  d: IndicatorPoint[];
  j: IndicatorPoint[];
}

export function kdj(bars: PriceBar[], period = 9, smoothK = 3, smoothD = 3): KdjResult {
  const k: IndicatorPoint[] = [];
  const d: IndicatorPoint[] = [];
  const j: IndicatorPoint[] = [];
  if (bars.length < period || period <= 0) return { k, d, j };
  let prevK = 50;
  let prevD = 50;
  for (let i = period - 1; i < bars.length; i++) {
    let hh = -Infinity;
    let ll = Infinity;
    for (let n = i - period + 1; n <= i; n++) {
      if (bars[n].high > hh) hh = bars[n].high;
      if (bars[n].low < ll) ll = bars[n].low;
    }
    const rsv = hh === ll ? 50 : ((bars[i].close - ll) / (hh - ll)) * 100;
    const kv = (rsv + (smoothK - 1) * prevK) / smoothK;
    const dv = (kv + (smoothD - 1) * prevD) / smoothD;
    prevK = kv;
    prevD = dv;
    const time = bars[i].time;
    k.push({ time, value: kv });
    d.push({ time, value: dv });
    j.push({ time, value: 3 * kv - 2 * dv });
  }
  return { k, d, j };
}
