/**
 * 策略市场查询纯函数：将页面筛选状态映射为后端 /market/listings 的 querystring。
 * 空值一律剔除，避免后端把空字符串当有效筛选（如 symbol="" 触发 JSONB contains）。
 */
import type { ListingQuery } from '@/types/api';

export function buildListingQuery(params: ListingQuery): Record<string, string | number | undefined> {
  return {
    page: params.page,
    page_size: params.pageSize,
    symbol: params.symbol?.trim().toUpperCase() || undefined,
    timeframe: params.timeframe || undefined,
    q: params.q?.trim() || undefined,
    sort: params.sort && params.sort !== 'latest' ? params.sort : undefined,
  };
}

export const DIRECTION_LABELS: Record<string, string> = {
  long_only: '仅做多',
  short_only: '仅做空',
  both: '双向',
};

/** 法务边界文案（发布确认 / 列表页脚 / 详情横幅共用口径）。 */
export const MARKET_DISCLAIMER =
  '策略内容仅供学习与技术交流，不构成投资建议；平台不提供信号售卖/代客理财功能；使用者需自行验证策略风险。';
