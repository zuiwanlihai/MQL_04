import { Fragment, useState } from 'react';
import { ChevronLeft, ChevronRight, Download, Loader2 } from 'lucide-react';
import { downloadTradesCsv, useBacktestTrades } from '@/hooks/useBacktests';
import { apiError } from '@/lib/api';
import type { BacktestTrade } from '@/types/api';

const fmt = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const dt = (s: string | null) => (s ? new Date(s).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—');

function Breakdown({ t }: { t: BacktestTrade }) {
  return (
    <div className="text-xs text-gray-400 space-y-0.5">
      <p>
        参考价 {fmt(t.base_price, 5)} → 成交价 {fmt(t.open_price, 5)}
        {t.close_price != null && <>（平仓 {fmt(t.close_price, 5)}）</>}
      </p>
      <p>
        点差成本 {fmt(t.spread_cost)} · 滑点成本 {fmt(t.slippage_cost)} · 佣金 {fmt(t.commission)} · 库存费{' '}
        {fmt(t.swap)}
      </p>
      {t.is_forced && <p className="text-loss">⚠ 保证金不足被强制平仓</p>}
      {t.comment && <p className="text-gray-500">备注：{t.comment}</p>}
    </div>
  );
}

interface Props {
  taskId: string;
  enabled: boolean;
}

export default function TradeTable({ taskId, enabled }: Props) {
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const trades = useBacktestTrades(taskId, page, enabled);
  const items = trades.data?.items ?? [];
  const total = trades.data?.total ?? 0;
  const totalPages = Math.max(Math.ceil(total / 50), 1);

  const csv = async () => {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadTradesCsv(taskId);
    } catch (e) {
      setDownloadError(apiError(e));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">成交明细（{total} 笔）</h3>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={csv} disabled={downloading}>
          {downloading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
          导出 CSV
        </button>
      </div>
      {downloadError && <p className="px-4 pt-2 text-xs text-loss">{downloadError}</p>}
      <div className="card-body overflow-x-auto p-0">
        <table className="table-dark">
          <thead>
            <tr>
              <th>#</th>
              <th>方向</th>
              <th>手数</th>
              <th>开仓时间</th>
              <th>开仓价</th>
              <th>平仓时间</th>
              <th>平仓价</th>
              <th className="text-right">盈亏</th>
              <th className="text-right">收益率</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <Fragment key={t.id}>
                <tr className="text-xs">
                  <td className="font-mono text-gray-500">{t.ticket}</td>
                  <td>
                    <span className={t.direction === 'buy' ? 'text-profit' : 'text-loss'}>
                      {t.direction === 'buy' ? '买' : '卖'}
                    </span>
                  </td>
                  <td className="font-mono">{fmt(t.volume, 2)}</td>
                  <td className="text-gray-400 whitespace-nowrap">{dt(t.open_time)}</td>
                  <td className="font-mono">{fmt(t.open_price, 5)}</td>
                  <td className="text-gray-400 whitespace-nowrap">{dt(t.close_time)}</td>
                  <td className="font-mono">{fmt(t.close_price, 5)}</td>
                  <td className={`text-right font-mono ${t.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {fmt(t.profit)}
                    {t.is_forced && <span className="text-loss ml-1">⚠</span>}
                  </td>
                  <td className={`text-right font-mono ${t.profit_percent >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {fmt(t.profit_percent)}%
                  </td>
                  <td className="text-right">
                    <button
                      type="button"
                      className="text-gray-500 hover:text-gray-300"
                      onClick={() => setExpanded(expanded === t.ticket ? null : t.ticket)}
                    >
                      {expanded === t.ticket ? '收起' : '构成'}
                    </button>
                  </td>
                </tr>
                {expanded === t.ticket && (
                  <tr>
                    <td colSpan={10} className="bg-surface-900/60 px-4 py-2">
                      <Breakdown t={t} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {trades.isLoading && enabled && (
          <p className="py-6 text-center text-sm text-gray-500">加载成交…</p>
        )}
        {!trades.isLoading && items.length === 0 && (
          <p className="py-6 text-center text-sm text-gray-500">该任务没有成交记录</p>
        )}
      </div>
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 py-3 border-t border-gray-700/50">
          <button
            type="button"
            className="btn-secondary px-3 py-1"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            className="btn-secondary px-3 py-1"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}
