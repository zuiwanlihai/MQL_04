import { describe, expect, it } from 'vitest';
import {
  currentActiveType,
  navDisableReason,
  LIVE_DEMO_MUTEX_REASON,
  LIVE_MUTEX_REASON,
  PAPER_MUTEX_REASON,
} from './mutex';
import type { ActiveAccountInfo } from '@/types/api';

function node(
  account_type: 'demo' | 'real' | 'contest' | null,
): ActiveAccountInfo {
  return {
    node_id: 'n1',
    node_name: '本地网关',
    terminal_trade_allowed: true,
    account:
      account_type === null
        ? null
        : {
            login: 91350,
            server: 'DBGMarkets-Demo',
            name: 'X',
            account_type,
            leverage: 100,
            currency: 'USD',
            balance: 0,
            credit: 0,
            equity: 0,
            margin: 0,
            margin_free: 0,
            margin_level: 0,
            is_trade_allowed: true,
          },
  };
}

describe('currentActiveType', () => {
  it('undefined / 空列表 / 全离线 → null', () => {
    expect(currentActiveType(undefined)).toBeNull();
    expect(currentActiveType([])).toBeNull();
    expect(currentActiveType([node(null)])).toBeNull();
  });

  it('取第一个在线节点的账户类型', () => {
    expect(currentActiveType([node('demo')])).toBe('demo');
    expect(currentActiveType([node('real')])).toBe('real');
    expect(currentActiveType([node('contest')])).toBe('contest');
  });

  it('离线节点在前时跳过取后面的在线节点', () => {
    expect(currentActiveType([node(null), node('demo')])).toBe('demo');
  });
});

describe('navDisableReason', () => {
  it('登录实盘 → 虚拟下单/模拟盘交易禁用，实盘不禁用', () => {
    expect(navDisableReason('/trading/paper', 'real')).toBe(PAPER_MUTEX_REASON);
    expect(navDisableReason('/trading/live-demo', 'real')).toBe(LIVE_DEMO_MUTEX_REASON);
    expect(navDisableReason('/trading/live', 'real')).toBeUndefined();
    expect(navDisableReason('/trading/manual', 'real')).toBeUndefined();
  });

  it('登录模拟 → 实盘禁用；虚拟下单/模拟盘交易开放（订单发往 MT5 模拟服务器）', () => {
    expect(navDisableReason('/trading/live', 'demo')).toBe(LIVE_MUTEX_REASON);
    expect(navDisableReason('/trading/live-demo', 'demo')).toBeUndefined();
    expect(navDisableReason('/trading/paper', 'demo')).toBeUndefined();
  });

  it('竞赛账户：实盘/模拟盘交易均禁用', () => {
    expect(navDisableReason('/trading/live', 'contest')).toBe(LIVE_MUTEX_REASON);
    expect(navDisableReason('/trading/live-demo', 'contest')).toBe(LIVE_DEMO_MUTEX_REASON);
  });

  it('离线（null）一律不禁用', () => {
    expect(navDisableReason('/trading/paper', null)).toBeUndefined();
    expect(navDisableReason('/trading/live-demo', null)).toBeUndefined();
    expect(navDisableReason('/trading/live', null)).toBeUndefined();
    expect(navDisableReason('/trading/manual', null)).toBeUndefined();
  });

  it('其他入口不受互斥影响', () => {
    expect(navDisableReason('/dashboard', 'real')).toBeUndefined();
    expect(navDisableReason('/market', 'demo')).toBeUndefined();
  });
});
