import { describe, expect, it } from 'vitest';
import { generateStrategyCode } from './codegen';
import { emptyConfig, type BuilderConfig, type IndicatorBlock, type Rule } from './types';

function ma(id: string, period: number): IndicatorBlock {
  return { id, kind: 'MA', period };
}

function crossRule(id: string, leftId: string, rightId: string, open: 'buy' | 'sell' | null, close: 'buy' | 'sell' | null): Rule {
  return {
    id,
    op: 'cross_above',
    left: { type: 'indicator', indicatorId: leftId, output: 'value' },
    right: { type: 'indicator', indicatorId: rightId, output: 'value' },
    open,
    close,
  };
}

/** 双均线趋势策略（对标 double_ma.py）。 */
function dmaConfig(): BuilderConfig {
  const cfg = emptyConfig();
  cfg.indicators = [ma('ind_a', 5), ma('ind_b', 20)];
  cfg.rules = [
    crossRule('r1', 'ind_a', 'ind_b', 'buy', null),
    { ...crossRule('r2', 'ind_a', 'ind_b', null, 'buy'), op: 'cross_below' },
  ];
  return cfg;
}

describe('codegen 结构', () => {
  it('双均线样例生成必含回调与交易结构', () => {
    const { code, errors } = generateStrategyCode(dmaConfig(), '双均线测试');
    expect(errors).toEqual([]);
    expect(code).toContain('def on_init(ctx):');
    expect(code).toContain('def on_bar(ctx, bar):');
    expect(code).toContain('def on_order(ctx, order):');
    expect(code).toContain('def on_stop(ctx, reason):');
    expect(code).toContain('ctx.declare(');
    expect(code).toContain('symbol="EURUSD"');
    expect(code).toContain('ctx.get_bars(ctx.symbol, ctx.period, count=lookback)');
    expect(code).toContain('_sma(closes');
    expect(code).toContain('ctx.buy(ctx.symbol, float(p["volume"]))');
    expect(code).toContain('ctx.close(pos.id)');
    // 交叉语义：prev <= prev and cur > cur
    expect(code).toContain('ma_1_prev <= ma_2_prev and ma_1 > ma_2');
    expect(code).toContain('ma_1_prev >= ma_2_prev and ma_1 < ma_2');
  });

  it('params 携带指标参数与 lookback', () => {
    const { code } = generateStrategyCode(dmaConfig());
    expect(code).toContain('"ma_1_period": 5,');
    expect(code).toContain('"ma_2_period": 20,');
    expect(code).toContain('"volume": 0.1,');
    expect(code).toContain('"lookback":');
    expect(code).toMatch(/"lookback": 2[12]/); // max warmup=20 → +2
  });

  it('空规则生成无交易的合法骨架', () => {
    const { code, errors } = generateStrategyCode(emptyConfig());
    expect(errors).toEqual([]);
    expect(code).toContain('def on_init(ctx):');
    expect(code).not.toContain('ctx.buy(');
    expect(code).not.toContain('_sma(');
  });
});

describe('codegen 指标', () => {
  it('RSI 超买平多（number 操作数）', () => {
    const cfg = emptyConfig();
    cfg.indicators = [{ id: 'r', kind: 'RSI', period: 14 }];
    cfg.rules = [
      {
        id: 'r1',
        op: 'greater_than',
        left: { type: 'indicator', indicatorId: 'r', output: 'value' },
        right: { type: 'number', value: 70 },
        open: null,
        close: 'buy',
      },
    ];
    const { code, errors } = generateStrategyCode(cfg);
    expect(errors).toEqual([]);
    expect(code).toContain('def _rsi(values, period):');
    expect(code).toContain('rsi_1 = _rsi(closes, int(p["rsi_1_period"]))');
    expect(code).toContain('rsi_1 > 70.0');
    // greater_than 不需要 prev
    expect(code).not.toContain('rsi_1_prev');
  });

  it('BOLL 上轨/下轨三输出与 prev 解包', () => {
    const cfg = emptyConfig();
    cfg.indicators = [{ id: 'b', kind: 'BOLL', period: 20, std: 2 }];
    cfg.rules = [
      {
        id: 'r1',
        op: 'cross_below',
        left: { type: 'indicator', indicatorId: 'b', output: 'lower' },
        right: { type: 'close' },
        open: 'buy',
        close: null,
      },
      {
        id: 'r2',
        op: 'cross_above',
        left: { type: 'indicator', indicatorId: 'b', output: 'upper' },
        right: { type: 'close' },
        open: null,
        close: 'buy',
      },
    ];
    const { code, errors } = generateStrategyCode(cfg);
    expect(errors).toEqual([]);
    expect(code).toContain('def _boll(values, period, k):');
    expect(code).toContain('boll_1_upper, _, boll_1_lower = _boll(closes,');
    expect(code).toContain('_, _, boll_1_lower_prev = _boll(closes[:-1],');
    expect(code).toContain('boll_1_upper_prev, _, _ = _boll(closes[:-1],');
    expect(code).toContain('boll_1_lower_prev >= closes[-2] and boll_1_lower < closes[-1]');
    expect(code).toContain('"boll_1_std": 2.0,');
  });

  it('MACD 三线输出', () => {
    const cfg = emptyConfig();
    cfg.indicators = [{ id: 'm', kind: 'MACD', period: 0, fast: 12, slow: 26, signal: 9 }];
    cfg.rules = [
      {
        id: 'r1',
        op: 'cross_above',
        left: { type: 'indicator', indicatorId: 'm', output: 'dif' },
        right: { type: 'indicator', indicatorId: 'm', output: 'dea' },
        open: 'buy',
        close: null,
      },
    ];
    const { code, errors } = generateStrategyCode(cfg);
    expect(errors).toEqual([]);
    expect(code).toContain('def _macd(values, fast, slow, signal):');
    expect(code).toContain('macd_1_dif, macd_1_dea, _ = _macd(closes, int(p["macd_1_fast"]), int(p["macd_1_slow"]), int(p["macd_1_signal"]))');
    expect(code).toContain('macd_1_dif_prev <= macd_1_dea_prev and macd_1_dif > macd_1_dea');
    expect(code).toContain('"lookback": 37,'); // 26+9=35 → +2
  });

  it('同类型指标自动编号 ma_1/ma_2/ma_3', () => {
    const cfg = emptyConfig();
    cfg.indicators = [ma('a', 5), ma('b', 10), ma('c', 20)];
    cfg.rules = [
      {
        id: 'r1',
        op: 'greater_than',
        left: { type: 'indicator', indicatorId: 'c', output: 'value' },
        right: { type: 'close' },
        open: 'buy',
        close: null,
      },
    ];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('"ma_1_period": 5,');
    expect(code).toContain('"ma_2_period": 10,');
    expect(code).toContain('"ma_3_period": 20,');
    expect(code).toContain('ma_3 = _sma(closes, int(p["ma_3_period"]))');
    // 未被引用的指标不生成 prev
    expect(code).not.toContain('ma_1_prev');
  });

  it('ATR 使用 bars 序列（含 high/low）', () => {
    const cfg = emptyConfig();
    cfg.indicators = [{ id: 'a', kind: 'ATR', period: 14 }];
    cfg.rules = [
      {
        id: 'r1',
        op: 'less_than',
        left: { type: 'indicator', indicatorId: 'a', output: 'value' },
        right: { type: 'close' },
        open: 'buy',
        close: null,
      },
    ];
    const { code, errors } = generateStrategyCode(cfg);
    expect(errors).toEqual([]);
    expect(code).toContain('def _atr(bars, period):');
    expect(code).toContain('atr_1 = _atr(bars, int(p["atr_1_period"]))');
  });
});

describe('codegen 动作与方向联动', () => {
  it('long_only 配置做空动作报错', () => {
    const cfg = dmaConfig();
    cfg.direction = 'long_only';
    cfg.rules = [crossRule('r1', 'ind_a', 'ind_b', 'sell', null)];
    const { errors } = generateStrategyCode(cfg);
    expect(errors.length).toBe(1);
    expect(errors[0]).toContain('仅做多');
  });

  it('规则动作先平后开（反手语义）', () => {
    const cfg = dmaConfig();
    cfg.rules = [
      {
        id: 'r1',
        op: 'greater_than',
        left: { type: 'close' },
        right: { type: 'number', value: 1 },
        open: 'sell',
        close: 'buy',
      },
    ];
    const { code, errors } = generateStrategyCode(cfg);
    expect(errors).toEqual([]);
    const closeIdx = code.indexOf('ctx.close(pos.id)');
    const refreshIdx = code.indexOf('positions = ctx.get_positions()', closeIdx);
    const buyIdx = code.indexOf('ctx.sell(ctx.symbol', closeIdx);
    expect(closeIdx).toBeGreaterThan(-1);
    expect(buyIdx).toBeGreaterThan(closeIdx);
    expect(refreshIdx).toBeGreaterThan(closeIdx);
    expect(refreshIdx).toBeLessThan(buyIdx);
  });

  it('close 后不再开仓时无需刷新持仓快照', () => {
    const cfg = dmaConfig();
    cfg.rules = [
      {
        id: 'r1',
        op: 'greater_than',
        left: { type: 'close' },
        right: { type: 'number', value: 1 },
        open: null,
        close: 'buy',
      },
    ];
    const code = generateStrategyCode(cfg).code;
    const closeIdx = code.indexOf('ctx.close(pos.id)');
    const rest = code.slice(closeIdx);
    expect(rest).not.toContain('positions = ctx.get_positions()');
  });
});

describe('codegen 校验', () => {
  it('悬空指标引用与非法输出通道报错', () => {
    const cfg = dmaConfig();
    cfg.indicators = [];
    const { errors } = generateStrategyCode(cfg);
    // 两条规则 × 左右操作数 = 4 条悬空引用
    expect(errors.length).toBe(4);
    expect(errors[0]).toContain('已删除的指标');
  });

  it('非法参数报错（period<2 / MACD slow<=fast）', () => {
    const bad = emptyConfig();
    bad.indicators = [ma('a', 1)];
    expect(generateStrategyCode(bad).errors.join()).toContain('≥ 2');

    const badMacd = emptyConfig();
    badMacd.indicators = [{ id: 'm', kind: 'MACD', period: 0, fast: 26, slow: 12, signal: 9 }];
    expect(generateStrategyCode(badMacd).errors.join()).toContain('慢线需大于快线');
  });

  it('手数非法与空品种报错', () => {
    const cfg = dmaConfig();
    cfg.volume = 0;
    expect(generateStrategyCode(cfg).errors.join()).toContain('手数');
    const cfg2 = dmaConfig();
    cfg2.symbol = ' ';
    expect(generateStrategyCode(cfg2).errors.join()).toContain('品种');
  });

  it('规则无动作报错', () => {
    const cfg = dmaConfig();
    cfg.rules = [crossRule('r1', 'ind_a', 'ind_b', null, null)];
    expect(generateStrategyCode(cfg).errors.join()).toContain('至少配置一个动作');
  });
});
