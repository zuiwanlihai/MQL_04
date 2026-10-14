import { useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Search, Store } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useListings } from '@/hooks/useMarketplace';
import { apiError } from '@/lib/api';
import { DIRECTION_LABELS, MARKET_DISCLAIMER } from '@/lib/marketplace';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

export default function MarketPage() {
  const [page, setPage] = useState(1);
  const [symbol, setSymbol] = useState('');
  const [timeframe, setTimeframe] = useState('');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<'latest' | 'popular'>('latest');

  const list = useListings({ page, pageSize: 12, symbol, timeframe, q, sort });
  const data = list.data;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <Store className="w-5 h-5 text-blue-400" /> 策略市场
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          浏览社区共享的策略工具 · 导入副本到自己的策略库研究
        </p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            className="input-dark pl-9 w-56"
            placeholder="搜索策略名…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <input
          className="input-dark w-36 font-mono"
          placeholder="品种如 EURUSD"
          value={symbol}
          onChange={(e) => {
            setSymbol(e.target.value.toUpperCase());
            setPage(1);
          }}
        />
        <select
          className="input-dark w-28"
          value={timeframe}
          onChange={(e) => {
            setTimeframe(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部周期</option>
          {TIMEFRAMES.map((tf) => (
            <option key={tf} value={tf}>
              {tf}
            </option>
          ))}
        </select>
        <select
          className="input-dark w-32"
          value={sort}
          onChange={(e) => {
            setSort(e.target.value as 'latest' | 'popular');
            setPage(1);
          }}
        >
          <option value="latest">最新发布</option>
          <option value="popular">最多导入</option>
        </select>
        {data && (
          <span className="text-xs text-gray-500">
            共 {data.total} 个条目 · 第 {data.page}/{data.total_pages} 页
          </span>
        )}
      </div>

      {list.error && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(list.error)}</div>
        </div>
      )}

      {list.isLoading && (
        <div className="card">
          <div className="card-body text-center text-gray-500 py-10">正在加载市场…</div>
        </div>
      )}

      {!list.isLoading && data && data.items.length === 0 && (
        <div className="card">
          <div className="card-body text-center text-gray-500 py-10">
            市场还没有条目 — 到「策略」列表点「发布」分享你的第一个策略。
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {(data?.items ?? []).map((l) => (
          <Link
            key={l.id}
            to={`/marketplace/${l.id}`}
            className="card hover:border-blue-500/40 transition-colors"
          >
            <div className="card-body space-y-2">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-medium text-gray-100 leading-5">{l.strategy_name}</h3>
                <span className="text-xs text-gray-500 whitespace-nowrap flex items-center gap-1">
                  <Download className="w-3 h-3" /> {l.import_count}
                </span>
              </div>
              <p className="text-xs text-gray-500 line-clamp-2 min-h-8">
                {l.description || '（无描述）'}
              </p>
              <div className="flex gap-1 flex-wrap">
                {l.symbol_ids.slice(0, 4).map((s) => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-300 text-xs font-mono">
                    {s}
                  </span>
                ))}
                <span className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-400 text-xs font-mono">
                  {l.timeframe}
                </span>
                <span className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-400 text-xs">
                  {DIRECTION_LABELS[l.direction] ?? l.direction}
                </span>
              </div>
              <div className="text-xs text-gray-600">
                作者 {l.author_username || '未知'} · {new Date(l.created_at).toLocaleDateString('zh-CN')}
              </div>
            </div>
          </Link>
        ))}
      </div>

      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button
            type="button"
            className="btn-secondary px-3 py-1.5"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">
            {page} / {data.total_pages}
          </span>
          <button
            type="button"
            className="btn-secondary px-3 py-1.5"
            disabled={page >= data.total_pages}
            onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}

      <p className="text-2xs text-gray-600 text-center pt-2">{MARKET_DISCLAIMER}</p>
    </div>
  );
}
