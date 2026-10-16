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
    expect(errors[0].message).toContain('仅做多');
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
    const msgs = errors.map((e) => e.message);
    // 两条规则 × 左右操作数 = 4 条悬空引用
    expect(errors.length).toBe(4);
    expect(msgs[0]).toContain('已删除的指标');
    // P2：每条错误都带 targetId 指向规则
    expect(errors[0].targetId).toBeTruthy();
  });

  it('非法参数报错（period<2 / MACD slow<=fast）', () => {
    const bad = emptyConfig();
    bad.indicators = [ma('a', 1)];
    expect(generateStrategyCode(bad).errors.map((e) => e.message).join()).toContain('≥ 2');

    const badMacd = emptyConfig();
    badMacd.indicators = [{ id: 'm', kind: 'MACD', period: 0, fast: 26, slow: 12, signal: 9 }];
    expect(generateStrategyCode(badMacd).errors.map((e) => e.message).join()).toContain('慢线需大于快线');
  });

  it('手数非法与空品种报错', () => {
    const cfg = dmaConfig();
    cfg.volume = 0;
    expect(generateStrategyCode(cfg).errors.map((e) => e.message).join()).toContain('手数');
    const cfg2 = dmaConfig();
    cfg2.symbol = ' ';
    expect(generateStrategyCode(cfg2).errors.map((e) => e.message).join()).toContain('品种');
  });

  it('规则无动作报错', () => {
    const cfg = dmaConfig();
    cfg.rules = [crossRule('r1', 'ind_a', 'ind_b', null, null)];
    expect(generateStrategyCode(cfg).errors.map((e) => e.message).join()).toContain('至少配置一个动作');
  });
});

// ─────────────────────────────────────────────────────────────────────
// P3：风控积木 + 仓位模式
// ─────────────────────────────────────────────────────────────────────

describe('P3 风控积木代码生成', () => {
  it('无 risk_blocks → 不生成风控检查代码', () => {
    const { code } = generateStrategyCode(dmaConfig());
    expect(code).not.toContain('P3 风控检查');
  });

  it('SL + TP 各生成一段检查', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [
      { id: 'sl1', kind: 'stop_loss', pips: 50 },
      { id: 'tp1', kind: 'take_profit', pips: 100 },
    ];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('P3 风控检查');
    expect(code).toContain('SL hit long');
    expect(code).toContain('SL hit short');
    expect(code).toContain('TP hit long');
    expect(code).toContain('TP hit short');
    // pips 数值出现在代码中
    expect(code).toContain('50 * ctx.get_symbol_info(ctx.symbol).point');
    expect(code).toContain('100 * ctx.get_symbol_info(ctx.symbol).point');
    // 生成代码通过 AST 校验
    expect(generateStrategyCode(cfg).errors.filter((e) => !e.targetId).length).toBe(0);
  });
});

describe('P3 仓位模式代码生成', () => {
  it('无 sizing → 用 p["volume"]', () => {
    const { code } = generateStrategyCode(dmaConfig());
    expect(code).toContain('float(p["volume"])');
  });

  it('fixed sizing → 硬编码体积', () => {
    const cfg = dmaConfig();
    cfg.sizing = { kind: 'fixed', volume: 0.25 };
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('float(0.25)');
    expect(code).not.toContain('p["volume"]');
  });

  it('risk_percent sizing → 动态体积计算', () => {
    const cfg = dmaConfig();
    cfg.sizing = { kind: 'risk_percent', risk_percent: 1.5, sl_pips: 50 };
    cfg.risk_blocks = [{ id: 'sl1', kind: 'stop_loss', pips: 50 }];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('ctx.get_account().equity');
    expect(code).toContain('ctx.get_symbol_info(ctx.symbol).point');
    expect(code).toContain('ctx.get_symbol_info(ctx.symbol).contract_size');
    expect(code).toContain('try:');
    expect(code).toContain('_vol');
    // 不包含固定 p["volume"]
    expect(code).not.toContain('p["volume"]');
  });
});

// ─────────────────────────────────────────────────────────────────────
// P3 第二批：移动止损 + 最长持仓 + 账户级标记
// ─────────────────────────────────────────────────────────────────────

describe('P3第二批 更多风控代码生成', () => {
  it('trailing_stop 用 ctx.storage 跟踪极值', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [{ id: 't1', kind: 'trailing_stop', pips: 30 }];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('Trailing Stop');
    expect(code).toContain('ctx.storage');
    expect(code).toContain('max(_ext, pos.current_price)');
    expect(code).toContain('min(_ext, pos.current_price)');
    expect(code).toContain('del ctx.storage[_tkey]');
    // storage key 前缀和持仓 id 拼接
    expect(code).toContain('f"trail_t1_{pos.id}"');
  });

  it('max_holding_time 对比 pos.opened_at 与 ctx.now', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [{ id: 'm1', kind: 'max_holding_time', minutes: 1440 }];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('Max Holding');
    expect(code).toContain('pos.opened_at');
    expect(code).toContain('ctx.now');
    expect(code).toContain('_elapsed_min');
    expect(code).toContain('1440');
  });

  it('账户级风控单独配置时生成注释说明待接入', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [
      { id: 'd1', kind: 'daily_drawdown_cut', percent: 10 },
    ];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('daily_drawdown_cut');
    expect(code).toContain('账户级风控，待接入');
  });

  it('多风控积木叠加：SL + TP + Trailing + MaxHold 全生成', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [
      { id: 's1', kind: 'stop_loss', pips: 50 },
      { id: 't1', kind: 'take_profit', pips: 100 },
      { id: 'tr1', kind: 'trailing_stop', pips: 30 },
      { id: 'mh1', kind: 'max_holding_time', minutes: 60 },
    ];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('SL hit');
    expect(code).toContain('TP hit');
    expect(code).toContain('TRAIL hit');
    expect(code).toContain('MAX_HOLD hit');
    // trailing storage 清理逻辑
    expect(code).toContain('_live_ids = {p.id for p in positions}');
    expect(code).toContain('del ctx.storage[_k]');
  });
});

// ─────────────────────────────────────────────────────────────────────
// P3-MPC: 最大持仓数（entry gate，不是 exit gate）
// ─────────────────────────────────────────────────────────────────────

describe('P3 最大持仓数（entry gate）', () => {
  it('缺省 = 一次一仓（if len(positions) < 1）', () => {
    const { code } = generateStrategyCode(dmaConfig());
    expect(code).toContain('len(positions) < 1');
  });

  it('max_position_count=3 → 允许同时开 3 个', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [{ id: 'mp1', kind: 'max_position_count', count: 3 }];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('len(positions) < 3');
    // entry gate，emitRiskChecks 里不应生成持仓检查代码
    expect(code).not.toContain('MAX_POS');
  });

  it('max_position_count=1 显式 → 等价缺省', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [{ id: 'mp1', kind: 'max_position_count', count: 1 }];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('len(positions) < 1');
  });

  it('与其他风控叠加 → entry gate + exit gate 同时存在', () => {
    const cfg = dmaConfig();
    cfg.risk_blocks = [
      { id: 'mp1', kind: 'max_position_count', count: 2 },
      { id: 'sl1', kind: 'stop_loss', pips: 50 },
    ];
    const { code } = generateStrategyCode(cfg);
    expect(code).toContain('len(positions) < 2');
    expect(code).toContain('SL hit');
  });
});
