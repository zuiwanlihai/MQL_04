import type { ActiveAccountInfo } from '@/types/api';

export type AccountType = 'demo' | 'real' | 'contest';

/**
 * 实盘/模拟互斥：从 /accounts/active 聚合结果中取第一个在线节点的
 * 当前登录账户类型；全部离线/未知 → null（不据此禁用任何入口）。
 */
export function currentActiveType(
  items: ActiveAccountInfo[] | undefined,
): AccountType | null {
  for (const n of items ?? []) {
    if (n.account) return n.account.account_type;
  }
  return null;
}

export const PAPER_MUTEX_REASON =
  '虚拟下单仅支持模拟账户：当前登录的是实盘账户，请先在总览页切换登录';

export const LIVE_DEMO_MUTEX_REASON =
  '模拟盘交易仅支持模拟账户：当前登录的是实盘账户，请先在总览页切换登录';

export const LIVE_MUTEX_REASON =
  '实盘仅支持实盘账户：当前登录的是模拟账户，请先在总览页切换登录';

/**
 * 侧栏导航互斥禁用：返回置灰原因文案；不需要禁用返回 undefined。
 * 登录实盘 → 虚拟下单/模拟盘交易禁用；
 * 登录模拟/竞赛 → 实盘禁用（模拟盘交易走 MT5 模拟服务器，零资金风险）。
 * 离线（null）不禁用——后端创建时仍有 409 双保险。
 */
export function navDisableReason(
  to: string,
  activeType: AccountType | null,
): string | undefined {
  if (to === '/trading/paper' && activeType === 'real') return PAPER_MUTEX_REASON;
  if (to === '/trading/live-demo') {
    if (activeType !== null && activeType !== 'demo') return LIVE_DEMO_MUTEX_REASON;
    return undefined;
  }
  if (to === '/trading/live') {
    if (activeType !== null && activeType !== 'real') return LIVE_MUTEX_REASON;
    return undefined;
  }
  return undefined;
}
