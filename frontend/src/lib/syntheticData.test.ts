import { describe, it, expect } from 'vitest';
import { generateM1Candles, aggregateCandles, generateCandles, MAX_M1_BARS, type TrendShape } from './syntheticData';

const SHAPES: TrendShape[] = ['random', 'uptrend', 'downtrend', 'vShape', 'invVShape', 'wShape', 'mShape'];

describe('syntheticData', () => {
  describe('generateM1Candles 基础', () => {
    it('返回正确数量的 1 分钟 K 线', () => {
      const start = new Date('2026-01-01T00:00:00');
      const end = new Date('2026-01-01T01:00:00');
      const data = generateM1Candles(start, end);
      expect(data).toHaveLength(60);
      expect(data[0].time).toBe(Math.floor(start.getTime() / 1000));
      expect(data[1].time - data[0].time).toBe(60);
    });

    it('OHLC 约束正确', () => {
      const data = generateM1Candles('2026-01-01T00:00:00', '2026-01-01T00:10:00');
      for (const c of data) {
        expect(c.high).toBeGreaterThanOrEqual(Math.max(c.open, c.close));
        expect(c.low).toBeLessThanOrEqual(Math.min(c.open, c.close));
        expect(c.volume).toBeGreaterThan(0);
      }
    });

    it('首根 open 等于 basePrice', () => {
      const data = generateM1Candles('2026-01-01T00:00:00', '2026-01-01T00:05:00', 2500);
      expect(data[0].open).toBeCloseTo(2500);
    });

    it('时间范围无效返回空数组', () => {
      expect(generateM1Candles('2026-01-02T00:00:00', '2026-01-01T00:00:00')).toEqual([]);
    });

    it('超限抛错', () => {
      const d = new Date();
      const start = new Date(d.getTime() - (MAX_M1_BARS + 60) * 60_000).toISOString();
      expect(() => generateM1Candles(start, d.toISOString())).toThrow(/时间范围过大/);
    });
  });

  describe('形状趋势（600 根 M1，低噪声 + 强趋势）', () => {
    const START = '2026-01-01T00:00:00';
    const END = '2026-01-01T10:00:00';
    const BASE = 1900;
    const VOL = 0.0002;
    const STRENGTH = 1.2;
    const closes = (data: { close: number }[]) => data.map((d) => d.close);
    const argmin = (arr: number[]) => arr.indexOf(Math.min(...arr));
    const argmax = (arr: number[]) => arr.indexOf(Math.max(...arr));

    it.each(SHAPES)('%s 返回正确数量', (shape) => {
      expect(generateM1Candles(START, END, BASE, VOL, shape, STRENGTH).length).toBe(600);
    });

    it('uptrend: 末价 > 首价 30%+', () => {
      const d = generateM1Candles(START, END, BASE, VOL, 'uptrend', STRENGTH);
      expect(d[d.length - 1].close).toBeGreaterThan(d[0].open * 1.3);
    });

    it('downtrend: 末价 < 首价 30%+', () => {
      const d = generateM1Candles(START, END, BASE, VOL, 'downtrend', STRENGTH);
      expect(d[d.length - 1].close).toBeLessThan(d[0].open * 0.7);
    });

    it('vShape: 最低点在中间 15%~85% 区间', () => {
      const d = generateM1Candles(START, END, BASE, VOL, 'vShape', STRENGTH);
      const ratio = argmin(closes(d)) / (d.length - 1);
      expect(ratio).toBeGreaterThan(0.15); expect(ratio).toBeLessThan(0.85);
      expect(Math.min(...closes(d))).toBeLessThan(d[0].open);
    });

    it('invVShape: 最高点在中间 15%~85% 区间', () => {
      const d = generateM1Candles(START, END, BASE, VOL, 'invVShape', STRENGTH);
      const ratio = argmax(closes(d)) / (d.length - 1);
      expect(ratio).toBeGreaterThan(0.15); expect(ratio).toBeLessThan(0.85);
      expect(Math.max(...closes(d))).toBeGreaterThan(d[0].open);
    });

    it('wShape: 两个低点分别在前半段和后半段', () => {
      const d = generateM1Candles(START, END, BASE, VOL, 'wShape', STRENGTH);
      const arr = closes(d), h = d.length / 2;
      expect(argmin(arr.slice(0, h))).toBeGreaterThan(20);
      expect(argmin(arr.slice(0, h))).toBeLessThan(h);
    });

    it('mShape: 两个高点分别在前半段和后半段', () => {
      const d = generateM1Candles(START, END, BASE, VOL, 'mShape', STRENGTH);
      const arr = closes(d), h = d.length / 2;
      expect(argmax(arr.slice(0, h))).toBeGreaterThan(20);
      expect(argmax(arr.slice(0, h))).toBeLessThan(h);
    });
  });

  describe('aggregateCandles', () => {
    const m1 = generateM1Candles('2026-01-01T00:00:00', '2026-01-01T01:00:00');

    it('M15/M30/H1 数量正确', () => {
      expect(aggregateCandles(m1, 'M15')).toHaveLength(4);
      expect(aggregateCandles(m1, 'M30')).toHaveLength(2);
      expect(aggregateCandles(m1, 'H1')).toHaveLength(1);
      expect(aggregateCandles(m1, 'M1')).toHaveLength(60);
    });

    it('H1 聚合 OHLCV 正确', () => {
      const h1 = aggregateCandles(m1, 'H1');
      expect(h1[0].open).toBe(m1[0].open);
      expect(h1[0].close).toBe(m1[59].close);
      expect(h1[0].high).toBe(Math.max(...m1.map((c) => c.high)));
      expect(h1[0].low).toBe(Math.min(...m1.map((c) => c.low)));
      expect(h1[0].volume).toBe(m1.reduce((s, c) => s + c.volume, 0));
    });

    it('D1 跨午夜分成 2 根', () => {
      const m1x = generateM1Candles('2026-01-01T23:55:00', '2026-01-02T00:05:00');
      expect(aggregateCandles(m1x, 'D1').length).toBeGreaterThanOrEqual(2);
    });
  });

  describe('generateCandles', () => {
    it('一步到位生成 H1', () => {
      const data = generateCandles('2026-01-01T00:00:00', '2026-01-02T00:00:00', 'H1');
      expect(data).toHaveLength(24);
    });

    it('uptrend D1 聚合首尾正确', () => {
      const data = generateCandles('2026-01-01T00:00:00', '2026-01-03T00:00:00', 'D1', 1900, 0.001, 'uptrend', 0.3);
      expect(data.length).toBeGreaterThanOrEqual(2);
      expect(data[data.length - 1].close).toBeGreaterThan(data[0].open);
    });
  });
});
