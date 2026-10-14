import { useState } from 'react';
import { AlertTriangle, ChevronLeft, Download, Loader2 } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import Editor from '@monaco-editor/react';
import ImportListingDialog from '@/components/marketplace/ImportListingDialog';
import { useListing } from '@/hooks/useMarketplace';
import { apiError } from '@/lib/api';
import { DIRECTION_LABELS, MARKET_DISCLAIMER } from '@/lib/marketplace';

export default function MarketListingDetailPage() {
  const { listingId } = useParams();
  const listing = useListing(listingId);
  const [importOpen, setImportOpen] = useState(false);
  const d = listing.data;

  return (
    <div className="space-y-4 max-w-5xl">
      <Link to="/marketplace" className="text-sm text-gray-400 hover:text-gray-200 inline-flex items-center gap-1">
        <ChevronLeft className="w-4 h-4" /> 返回市场
      </Link>

      {listing.isLoading && (
        <div className="card">
          <div className="card-body text-center text-gray-500 py-10">正在加载…</div>
        </div>
      )}
      {listing.error && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(listing.error)}</div>
        </div>
      )}

      {d && (
        <>
          <div className="card">
            <div className="card-body space-y-3">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <h1 className="text-lg font-semibold text-white">{d.strategy_name}</h1>
                  <p className="text-sm text-gray-500 mt-1">
                    作者 {d.author_username || '未知'} · 发布于{' '}
                    {new Date(d.created_at).toLocaleString('zh-CN')} ·{' '}
                    <span className="inline-flex items-center gap-1">
                      <Download className="w-3 h-3" /> 被导入 {d.import_count} 次
                    </span>
                  </p>
                </div>
                <button type="button" className="btn-primary" onClick={() => setImportOpen(true)}>
                  <Download className="w-4 h-4" /> 导入到我的策略
                </button>
              </div>

              {d.description && <p className="text-sm text-gray-300 whitespace-pre-wrap">{d.description}</p>}

              <div className="flex gap-1.5 flex-wrap">
                {d.symbol_ids.map((s) => (
                  <span key={s} className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-300 text-xs font-mono">
                    {s}
                  </span>
                ))}
                <span className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-400 text-xs font-mono">
                  {d.timeframe}
                </span>
                <span className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-400 text-xs">
                  {DIRECTION_LABELS[d.direction] ?? d.direction}
                </span>
                {d.tags.map((t) => (
                  <span key={t} className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-500 text-xs">
                    #{t}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="px-4 py-2.5 rounded bg-yellow-500/10 border border-yellow-500/40 text-xs text-yellow-300 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{MARKET_DISCLAIMER}</span>
          </div>

          <div className="card">
            <div className="card-header">
              <h3 className="text-sm font-medium text-gray-200">冻结源码（发布时刻快照，只读）</h3>
            </div>
            <div className="h-[480px]">
              <Editor
                path="market-listing.py"
                language="python"
                theme="mt5-dark"
                value={d.source_code}
                options={{
                  readOnly: true,
                  fontSize: 12,
                  fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                  minimap: { enabled: false },
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 4,
                  padding: { top: 8 },
                }}
              />
            </div>
          </div>

          <ImportListingDialog listing={importOpen ? d : null} onClose={() => setImportOpen(false)} />
        </>
      )}

      {listing.isFetching && !listing.data && (
        <div className="text-center text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin inline" />
        </div>
      )}
    </div>
  );
}
