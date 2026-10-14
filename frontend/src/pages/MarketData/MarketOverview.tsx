import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Database, Search } from 'lucide-react';
import GroupTabs from '@/components/market/GroupTabs';
import QuoteTable from '@/components/market/QuoteTable';
import { useQuotesStream, useSymbols } from '@/hooks/useMarket';
import { apiError } from '@/lib/api';
import { useWatchlist } from '@/lib/watchlist';
import type { QuoteStreamStatus } from '@/lib/quoteStream';
import type { SymbolGroup } from '@/types/api';

const STATUS_META: Record<QuoteStreamStatus, { dot: string; text: string }> = {
  live: { dot: 'text-profit', text: '实时推送' },
  polling: { dot: 'text-chart-yellow', text: '轮询降级' },
  connecting: { dot: 'text-chart-yellow', text: '连接中' },
  error: { dot: 'text-loss', text: '报价不可用' },
};

export default function MarketOverview() {
  const [group, setGroup] = useState<SymbolGroup | '' | 'fav'>('');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const { favorites, favSet, toggle: toggleFavorite } = useWatchlist();

  // 搜索防抖 300ms
  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  // 「自选」无服务端分组：拉全量后按收藏过滤
  const isFavTab = group === 'fav';
  const symbolsQuery = useSymbols(isFavTab ? '' : group, q);
  const symbols = useMemo(() => {
    const items = symbolsQuery.data?.items ?? [];
    return isFavTab ? items.filter((s) => favSet.has(s.name)) : items;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsQuery.data, isFavTab, favorites]);
  const quoteNames = symbols.slice(0, 50).map((s) => s.name);
  const { quotes: quoteMap, status, degraded } = useQuotesStream(quoteNames);
  const statusMeta = STATUS_META[status];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">行情总览</h1>
          <p className="text-sm text-gray-500 mt-1 flex items-center gap-1.5">
            数据源：MT5 网关 · tick 级实时推送
            <span className={`inline-flex items-center gap-1 ${statusMeta.dot}`}>
              ● {statusMeta.text}
            </span>
            {degraded && (
              <span className="text-chart-yellow">（行情源波动，自动恢复中）</span>
            )}
          </p>
        </div>
        <Link to="/market/data" className="btn-secondary">
          <Database className="w-4 h-4" />
          数据管理
        </Link>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <GroupTabs value={group} onChange={setGroup} />
        <div className="relative flex-1 min-w-56 max-w-80">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索品种名称或描述…"
            className="input-dark pl-9"
          />
        </div>
      </div>

      {symbolsQuery.isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">
            {apiError(symbolsQuery.error)}
          </div>
        </div>
      )}

      {symbolsQuery.isLoading ? (
        <div className="card">
          <div className="card-body text-center text-gray-500 py-12">
            正在加载品种…
          </div>
        </div>
      ) : (
        <>
          <QuoteTable
            symbols={symbols}
            quotes={quoteMap}
            favorites={favSet}
            onToggleFavorite={toggleFavorite}
            emptyHint={isFavTab ? '暂无自选品种 — 点击品种行左侧的 ☆ 星标添加。' : undefined}
          />
          <p className="text-xs text-gray-600">
            {isFavTab
              ? `自选 ${symbols.length} 个品种`
              : `共 ${symbolsQuery.data?.total ?? 0} 个品种${symbols.length > 50 ? '（报价列仅显示前 50 个）' : ''}`}
          </p>
        </>
      )}
    </div>
  );
}
