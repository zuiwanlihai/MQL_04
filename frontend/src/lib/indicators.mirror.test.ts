/**
 * P2-7 镜像对照（前端侧）：TS 指标实现必须逐点复现 golden fixture。
 *
 * fixture 由 shared/tests/gen_golden.py 从 shared/sdk/indicators.py 生成，
 * Python 侧在 shared/tests/test_golden_mirror.py 断言同一份文件——
 * 只改一侧口径、不改另一侧，两边都会红。
 */
import { describe, expect, it } from 'vitest';
import type { IndicatorPoint } from './indicators';
import { boll, ema, kdj, macd, rsi, sma } from './indicators';
import type { PriceBar } from '@/types/api';
import rawGolden from '../../../shared/tests/fixtures/indicators_golden.json';

interface GoldenPoint {
  time: number;
  value: number;
}

interface Golden {
  params: {
    sma_period: number;
    ema_period: number;
    boll_period: number;
    boll_mult: number;
    macd_fast: number;
    macd_slow: number;
    macd_signal: number;
    rsi_period: number;
    kdj_period: number;
    kdj_smooth_k: number;
    kdj_smooth_d: number;
  };
  bars: PriceBar[];
  expected: {
    sma: GoldenPoint[];
    ema: GoldenPoint[];
    boll: { mid: GoldenPoint[]; upper: GoldenPoint[]; lower: GoldenPoint[] };
    macd: { dif: GoldenPoint[]; dea: GoldenPoint[]; hist: GoldenPoint[] };
    rsi: GoldenPoint[];
    kdj: { k: GoldenPoint[]; d: GoldenPoint[]; j: GoldenPoint[] };
  };
}

const golden = rawGolden as unknown as Golden;

function expectPointsToMatch(actual: IndicatorPoint[], expected: GoldenPoint[]): void {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((p, i) => {
    expect(p.time).toBe(expected[i].time);
    expect(p.value).toBeCloseTo(expected[i].value, 9);
  });
}

describe('indicators golden mirror (P2-7)', () => {
  const { bars, params: p, expected } = golden;

  it('sma 与 SDK 一致', () => {
    expectPointsToMatch(sma(bars, p.sma_period), expected.sma);
  });

  it('ema 与 SDK 一致', () => {
    expectPointsToMatch(ema(bars, p.ema_period), expected.ema);
  });

  it('boll 与 SDK 一致（总体标准差）', () => {
    const { mid, upper, lower } = boll(bars, p.boll_period, p.boll_mult);
    expectPointsToMatch(mid, expected.boll.mid);
    expectPointsToMatch(upper, expected.boll.upper);
    expectPointsToMatch(lower, expected.boll.lower);
  });

  it('macd 与 SDK 一致（signal 种子为 dif 有效段前 signal 值 SMA）', () => {
    const { dif, dea, hist } = macd(bars, p.macd_fast, p.macd_slow, p.macd_signal);
    expectPointsToMatch(dif, expected.macd.dif);
    expectPointsToMatch(dea, expected.macd.dea);
    expectPointsToMatch(hist, expected.macd.hist);
  });

  it('rsi 与 SDK 一致（Wilder 平滑）', () => {
    expectPointsToMatch(rsi(bars, p.rsi_period), expected.rsi);
  });

  it('kdj 与 SDK 一致（首根即参与平滑）', () => {
    const { k, d, j } = kdj(bars, p.kdj_period, p.kdj_smooth_k, p.kdj_smooth_d);
    expectPointsToMatch(k, expected.kdj.k);
    expectPointsToMatch(d, expected.kdj.d);
    expectPointsToMatch(j, expected.kdj.j);
  });
});
