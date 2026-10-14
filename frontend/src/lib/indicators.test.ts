import { describe, expect, it } from 'vitest';
import { boll, ema, emaValues, kdj, macd, rsi, sma } from './indicators';
import type { PriceBar } from '@/types/api';

/** 构造规则化 K 线：close = base + i*step，high/low 围绕 close 摆动。 */
function makeBars(n: number, base = 100, step = 1): PriceBar[] {
  const bars: PriceBar[] = [];
  for (let i = 0; i < n; i++) {
    const close = base + i * step;
    bars.push({
      time: 1700000000 + i * 3600,
      open: close - step / 2,
      high: close + 0.5,
      low: close - 0.5,
      close,
      tick_volume: 100,
    });
  }
  return bars;
}

describe('sma', () => {
  it('等差数列的 SMA 等于窗口中点', () => {
    const bars = makeBars(10, 100, 2); // close = 100,102,...,118
    const out = sma(bars, 5);
    expect(out).toHaveLength(6);
    expect(out[0].value).toBeCloseTo(104); // (100+102+104+106+108)/5
    expect(out[0].time).toBe(bars[4].time);
    expect(out[5].value).toBeCloseTo(114);
  });

  it('数据不足返回空', () => {
    expect(sma(makeBars(3), 5)).toHaveLength(0);
    expect(sma([], 5)).toHaveLength(0);
  });
});

describe('ema', () => {
  it('种子为首 period 个值的 SMA，之后按 k 递推', () => {
    const bars = makeBars(8, 100, 1);
    const out = ema(bars, 4);
    expect(out).toHaveLength(5);
    expect(out[0].value).toBeCloseTo((100 + 101 + 102 + 103) / 4); // 101.5
    const k = 2 / 5;
    expect(out[1].value).toBeCloseTo(104 * k + 101.5 * (1 - k));
  });

  it('emaValues 不足 period 的位置为 null', () => {
    const out = emaValues([1, 2, 3, 4, 5], 3);
    expect(out).toEqual([null, null, 2, 3, 4]);
  });
});

describe('boll', () => {
  it('常数序列上下轨重合于中轨', () => {
    const bars = makeBars(25, 100, 0);
    const { mid, upper, lower } = boll(bars, 20, 2);
    expect(mid).toHaveLength(6);
    expect(mid[0].value).toBe(100);
    expect(upper[0].value).toBe(100);
    expect(lower[0].value).toBe(100);
  });

  it('波动序列上轨高于中轨高于下轨', () => {
    const bars = makeBars(30, 100, 1);
    const { mid, upper, lower } = boll(bars, 20, 2);
    expect(upper[0].value).toBeGreaterThan(mid[0].value);
    expect(mid[0].value).toBeGreaterThan(lower[0].value);
  });
});

describe('macd', () => {
  it('稳定上涨序列 dif/dea 为正且 hist = dif - dea', () => {
    const bars = makeBars(60, 100, 0.5);
    const { dif, dea, hist } = macd(bars);
    expect(dif.length).toBeGreaterThan(0);
    expect(dea.length).toBeLessThanOrEqual(dif.length); // dea 比 dif 晚 signal-1 根
    expect(dif[dif.length - 1].value).toBeGreaterThan(0);
    const last = hist[hist.length - 1];
    const difLast = dif.find((p) => p.time === last.time);
    const deaLast = dea.find((p) => p.time === last.time);
    expect(last.value).toBeCloseTo(difLast!.value - deaLast!.value, 10);
  });

  it('数据不足 slow 时不输出', () => {
    const { dif } = macd(makeBars(20), 12, 26, 9);
    expect(dif).toHaveLength(0);
  });
});

describe('rsi', () => {
  it('单调上涨序列 RSI=100，单调下跌序列 RSI=0', () => {
    expect(rsi(makeBars(30, 100, 1), 14).every((p) => p.value === 100)).toBe(true);
    expect(rsi(makeBars(30, 100, -1), 14).every((p) => p.value === 0)).toBe(true);
  });

  it('数据不足返回空', () => {
    expect(rsi(makeBars(14), 14)).toHaveLength(0);
  });
});

describe('kdj', () => {
  it('恒定 RSV 序列 K/D 收敛于该 RSV，J 以 3K-2D 公式计算', () => {
    const bars = makeBars(40, 100, 1);
    const { k, d, j } = kdj(bars);
    expect(k).toHaveLength(32); // 40 - 9 + 1
    // rsv 恒为 (close[i]-low[i-8])/(high[i]-low[i-8]) = 8.5/9 ≈ 94.44 → K/D 收敛于它
    const limit = (100 * 8.5) / 9;
    expect(k[k.length - 1].value).toBeCloseTo(limit, 2); // 有限轮平滑未完全收敛，容差 0.01
    expect(d[d.length - 1].value).toBeCloseTo(limit, 2);
    const last = k[k.length - 1].time;
    expect(j.find((p) => p.time === last)!.value).toBeCloseTo(3 * k[k.length - 1].value - 2 * d[d.length - 1].value, 10);
  });

  it('一字板（high==low）RSV 取 50', () => {
    const bars = makeBars(12, 100, 0); // open=high=low=close
    const { k } = kdj(bars);
    expect(k[0].value).toBeCloseTo(50);
  });
});
