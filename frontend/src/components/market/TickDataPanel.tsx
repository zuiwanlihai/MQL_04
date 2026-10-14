import { useState } from 'react';
import { Database, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { usePurgeTicks, useRequestTickSync, useTickCoverage, useTickJobs } from '@/hooks/useTicks';
import { apiError } from '@/lib/api';
import type { TickSyncStatus } from '@/types/api';

const MAX_SPAN_DAYS = 7;

const STATUS_STYLES: Record<TickSyncStatus, { label: string; cls: string }> = {
  pending: { label: '排队中', cls: 'bg-gray-500/15 text-gray-400 border-gray-500/40' },
  running: { label: '同步中', cls: 'bg-blue-500/15 text-blue-300 border-blue-500/40' },
  done: { label: '完成', cls: 'bg-profit/15 text-profit border-profit/40' },
  failed: { label: '失败', cls: 'bg-loss/15 text-loss border-loss/40' },
};

function fmtMsc(msc: number): string {
  return new Date(msc).toLocaleString('zh-CN', { hour12: false });
}

function fmtBytes(n: number): string {
  return n.toLocaleString('zh-CN');
}

/** datetime-local 值 → epoch 秒（本地时区）。 */
function toEpoch(value: string): number {
  return Math.floor(new Date(value).getTime() / 1000);
}

/** 默认窗口：从 7 天前到现在。 */
function defaultWindow(): { start: string; end: string } {
  const now = new Date();
  const weekAgo = new Date(now.getTime() - MAX_SPAN_DAYS * 86_400_000);
  const fmt = (d: Date) => {
    const pad = (x: number) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  return { start: fmt(weekAgo), end: fmt(now) };
}

export default function TickDataPanel() {
  const coverageQuery = useTickCoverage();
  const jobsQuery = useTickJobs(20);
  const sync = useRequestTickSync();
  const purge = usePurgeTicks();

  const [symbol, setSymbol] = useState('EURUSD');
  const [window_, setWindow_] = useState(defaultWindow);

  const coverage = coverageQuery.data?.items ?? [];
  const jobs = jobsQuery.data?.items ?? [];

  const submitSync = () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    sync.mutate({ symbol: sym, start: toEpoch(window_.start), end: toEpoch(window_.end) });
  };

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Database className="w-4 h-4 text-gray-500" />
        <h2 className="text-sm font-medium text-gray-300">Tick 数据管理</h2>
        {coverageQuery.data && (
          <span className="text-xs text-gray-600">
            已缓存 {coverage.length} 个品种
          </span>
        )}
      </div>

      {/* 发起同步 */}
      <div className="card">
        <div className="card-body space-y-3">
          <p className="text-xs text-gray-500">
            从网关拉取指定时间窗的 tick 数据入库（单次最多 {MAX_SPAN_DAYS} 天，按 6 小时分片幂等写入，重复同步自动去重）。
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">品种</span>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="EURUSD"
                className="input w-32 font-mono"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">开始时间</span>
              <input
                type="datetime-local"
                value={window_.start}
                onChange={(e) => setWindow_((w) => ({ ...w, start: e.target.value }))}
                className="input w-56"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">结束时间</span>
              <input
                type="datetime-local"
                value={window_.end}
                onChange={(e) => setWindow_((w) => ({ ...w, end: e.target.value }))}
                className="input w-56"
              />
            </label>
            <button
              type="button"
              className="btn-primary h-9 px-4 disabled:opacity-50"
              disabled={sync.isPending || !symbol.trim()}
              onClick={submitSync}
            >
              {sync.isPending ? (
                <Loader2 className="w-4 h-4 inline animate-spin mr-1" />
              ) : (
                <RefreshCw className="w-4 h-4 inline mr-1" />
              )}
              发起同步
            </button>
          </div>
          {sync.isError && (
            <p className="text-sm text-loss">同步请求失败：{apiError(sync.error)}</p>
          )}
          {purge.isError && (
            <p className="text-sm text-loss">清除失败：{apiError(purge.error)}</p>
          )}
        </div>
      </div>

      {/* 覆盖范围 */}
      <div className="card overflow-hidden">
        {coverageQuery.isLoading ? (
          <div className="card-body text-center text-gray-500 py-8">正在加载覆盖范围…</div>
        ) : coverage.length === 0 ? (
          <div className="card-body text-center text-gray-500 py-8">
            尚未缓存任何 tick 数据，请使用上方表单发起同步。
          </div>
        ) : (
          <table className="table-dark">
            <thead>
              <tr>
                <th>品种</th>
                <th className="text-right">Tick 数</th>
                <th>最早数据</th>
                <th>最新数据</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((c) => (
                <tr key={c.symbol}>
                  <td className="font-mono text-gray-200">{c.symbol}</td>
                  <td className="text-right font-mono text-gray-200">{fmtBytes(c.tick_count)}</td>
                  <td className="text-xs text-gray-500">{fmtMsc(c.min_time_msc)}</td>
                  <td className="text-xs text-gray-500">{fmtMsc(c.max_time_msc)}</td>
                  <td className="text-right">
                    <button
                      type="button"
                      className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                      disabled={purge.isPending}
                      onClick={() => {
                        if (window.confirm(`确认清除 ${c.symbol} 的全部 ${c.tick_count} 条 tick 数据？`)) {
                          purge.mutate(c.symbol);
                        }
                      }}
                    >
                      <Trash2 className="w-3.5 h-3.5 inline" /> 清除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 任务列表 */}
      {jobs.length > 0 && (
        <div className="card overflow-hidden">
          <table className="table-dark">
            <thead>
              <tr>
                <th>同步任务</th>
                <th>时间窗</th>
                <th>状态</th>
                <th className="text-right">入库条数</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const st = STATUS_STYLES[j.status];
                return (
                  <tr key={j.id}>
                    <td className="font-mono text-gray-200">{j.symbol}</td>
                    <td className="text-xs text-gray-500">
                      {new Date(j.start_time).toLocaleString('zh-CN', { hour12: false })}
                      {' → '}
                      {new Date(j.end_time).toLocaleString('zh-CN', { hour12: false })}
                    </td>
                    <td>
                      <span className={`px-2 py-0.5 rounded border text-xs ${st.cls}`}>
                        {st.label}
                      </span>
                    </td>
                    <td className="text-right font-mono text-gray-300">{j.tick_count}</td>
                    <td className="text-xs text-loss max-w-80 truncate" title={j.error ?? ''}>
                      {j.error ?? ''}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
