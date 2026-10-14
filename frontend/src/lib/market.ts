// 行情展示工具 — PRD 4.3：涨跌方向色可配置（默认红涨绿跌，国内惯例）

/**
 * 涨跌幅 class：正 → 涨色，负 → 跌色，零 → 灰。
 * `redUpGreenDown=true` 时涨=红(text-loss)/跌=绿(text-profit)；false 反之。
 * 默认 true 保持向后兼容。
 */
export function changeClass(value: number, redUpGreenDown = true): string {
  if (value > 0) return redUpGreenDown ? 'text-loss' : 'text-profit';
  if (value < 0) return redUpGreenDown ? 'text-profit' : 'text-loss';
  return 'text-gray-400';
}

/** 带符号的涨跌幅文本，如 +0.25% / -1.03% / 0.00%。 */
export function formatPercent(value: number, digits = 2): string {
  const safe = Number.isFinite(value) ? value : 0;
  return `${safe > 0 ? '+' : ''}${safe.toFixed(digits)}%`;
}

/** 价格格式化：按小数位数输出，等宽字体渲染。 */
export function formatPrice(value: number, digits = 5): string {
  if (!Number.isFinite(value) || value === 0) return '—';
  return value.toFixed(digits);
}

/** 秒级 epoch → 本地时间 HH:mm:ss。 */
export function formatQuoteTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

/** uptime 秒 → 中文可读（3天4小时 / 5小时12分 / 45秒）。 */
export function formatUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (days > 0) return `${days}天${hours}小时`;
  if (hours > 0) return `${hours}小时${minutes}分`;
  return `${minutes}分${seconds % 60}秒`;
}

/** 相对时间（中文）：刚刚 / 5秒前 / 3分钟前 / 2小时前。 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const diff = Math.max(0, Date.now() - t) / 1000;
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${Math.floor(diff)}秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
  return `${Math.floor(diff / 86400)}天前`;
}

/** 账户类型中文。 */
export const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  demo: '模拟',
  real: '实盘',
  contest: '竞赛',
};

/** 网关状态中文。 */
export const NODE_STATUS_LABELS: Record<string, string> = {
  online: '在线',
  offline: '离线',
  degraded: '降级',
  starting: '启动中',
};
