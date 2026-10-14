import { useState } from 'react';
import { CalendarClock, History, Loader2, Radar, RefreshCw, Trash2 } from 'lucide-react';
import {
  useHistoryBackfill,
  useHistoryCoverage,
  useProbeHistory,
  useRemoveHistoryCoverage,
  useSyncHistoryRound,
} from '@/hooks/useMarketHistory';
import { apiError } from '@/lib/api';
import type { MarketHistoryRange, MarketTimeframe } from '@/types/api';

const TIMEFRAMES: MarketTimeframe[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

function fmtTs(v: string | null): string {
  return v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '—';
}

/** ISO / null → datetime-local 本地值（null 取当前时间）。 */
function toLocalInput(v: string | null): string {
  const d = v ? new Date(v) : new Date();
  const pad = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** datetime-local 值 → epoch 秒（本地时区）。 */
function toEpoch(value: string): number {
  return Math.floor(new Date(value).getTime() / 1000);
}

export default function HistoryDataPanel() {
  const coverageQuery = useHistoryCoverage();
  const probe = useProbeHistory();
  const backfill = useHistoryBackfill();
  const syncRound = useSyncHistoryRound();
  const remove = useRemoveHistoryCoverage();

  const [symbol, setSymbol] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState<MarketTimeframe>('H1');
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [win, setWin] = useState({ start: '', end: '' });
  const [notice, setNotice] = useState<string | null>(null);

  const coverage = coverageQuery.data?.items ?? [];

  const openBackfill = (row: MarketHistoryRange) => {
    const key = `${row.symbol}:${row.timeframe}`;
    setNotice(null);
    setExpandedKey(key);
    setWin({ start: toLocalInput(row.first_bar_time), end: toLocalInput(null) });
  };

  const submitBackfill = (row: MarketHistoryRange) => {
    const start = toEpoch(win.start);
    const end = toEpoch(win.end);
    if (!start || !end || end <= start) {
      setNotice('请先选择有效的起止时间');
      return;
    }
    backfill.mutate(
      {
        symbol: row.symbol,
        timeframe: row.timeframe as MarketTimeframe,
        start,
        end,
      },
      {
        onSuccess: (res) => {
          setNotice(
            res.complete
              ? `${row.symbol}/${row.timeframe}：${res.windows} 个窗口 × ${res.attempts} 轮补数完成`
              : `${row.symbol}/${row.timeframe}：补数已执行 ${res.windows} 个窗口，已到达终端可用历史边界（非错误）`,
          );
          setExpandedKey(null);
        },
        onError: () => setNotice(null),
      },
    );
  };

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <History className="w-4 h-4 text-gray-500" />
        <h2 className="text-sm font-medium text-gray-300">历史数据管理</h2>
        {coverageQuery.data && (
          <span className="text-xs text-gray-600">已追踪 {coverage.length} 个品种/周期</span>
        )}
        <button
          type="button"
          className="btn-ghost h-8 px-3 text-xs ml-auto disabled:opacity-50"
          disabled={syncRound.isPending}
          onClick={() =>
            syncRound.mutate(undefined, {
              onSuccess: (res) =>
                setNotice(`增量同步完成：更新 ${res.updated}，失败 ${res.failed}`),
            })
          }
        >
          {syncRound.isPending ? (
            <Loader2 className="w-3.5 h-3.5 inline animate-spin mr-1" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5 inline mr-1" />
          )}
          立即同步
        </button>
      </div>

      {/* 追踪表单 */}
      <div className="card">
        <div className="card-body space-y-3">
          <p className="text-xs text-gray-500">
            追踪 MT5 终端本地已缓存的 K 线范围（每 5 分钟后台自动增量同步最新 K 线）。
            补数会触发终端向券商下载更早的历史 K 线，供回测 / 虚拟下单 / 实盘启动取数使用；K 线本身不落平台库。
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
              <span className="text-xs text-gray-500">周期</span>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value as MarketTimeframe)}
                className="input w-24 font-mono"
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>
                    {tf}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="btn-primary h-9 px-4 disabled:opacity-50"
              disabled={probe.isPending || !symbol.trim()}
              onClick={() =>
                probe.mutate(
                  { symbol: symbol.trim().toUpperCase(), timeframe },
                  {
                    onSuccess: (res) => {
                      setNotice(
                        `${res.symbol}/${res.timeframe} 探测完成：本地 ${res.bars_count.toLocaleString('zh-CN')} 根 K 线`,
                      );
                      setSymbol('');
                    },
                  },
                )
              }
            >
              {probe.isPending ? (
                <Loader2 className="w-4 h-4 inline animate-spin mr-1" />
              ) : (
                <Radar className="w-4 h-4 inline mr-1" />
              )}
              探测并追踪
            </button>
          </div>
          {notice && <p className="text-xs text-gray-400">{notice}</p>}
          {probe.isError && (
            <p className="text-sm text-loss">探测失败：{apiError(probe.error)}</p>
          )}
          {remove.isError && (
            <p className="text-sm text-loss">移除失败：{apiError(remove.error)}</p>
          )}
        </div>
      </div>

      {/* 覆盖范围表 */}
      <div className="card overflow-hidden">
        {coverageQuery.isLoading ? (
          <div className="card-body text-center text-gray-500 py-8">正在加载覆盖范围…</div>
        ) : coverage.length === 0 ? (
          <div className="card-body text-center text-gray-500 py-8">
            尚未追踪任何品种范围，请使用上方表单「探测并追踪」。
          </div>
        ) : (
          <table className="table-dark">
            <thead>
              <tr>
                <th>品种</th>
                <th>周期</th>
                <th>最早 K 线</th>
                <th>最新 K 线</th>
                <th className="text-right">条数</th>
                <th>最后同步</th>
                <th>说明</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((row) => {
                const key = `${row.symbol}:${row.timeframe}`;
                return (
                  <BackfillRows
                    key={key}
                    row={row}
                    expanded={expandedKey === key}
                    win={win}
                    onWin={setWin}
                    onOpen={() => openBackfill(row)}
                    onSubmit={() => submitBackfill(row)}
                    submitting={backfill.isPending}
                    onClose={() => setExpandedKey(null)}
                    onReprobe={() =>
                      probe.mutate(
                        {
                          symbol: row.symbol,
                          timeframe: row.timeframe as MarketTimeframe,
                        },
                        {
                          onSuccess: (res) =>
                            setNotice(
                              `${res.symbol}/${res.timeframe} 重新探测完成：本地 ${res.bars_count.toLocaleString('zh-CN')} 根`,
                            ),
                        },
                      )
                    }
                    onRemove={() => {
                      if (window.confirm(`确认停止追踪 ${row.symbol}/${row.timeframe}？`)) {
                        remove.mutate({ symbol: row.symbol, timeframe: row.timeframe });
                      }
                    }}
                    removing={remove.isPending}
                  />
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

interface BackfillRowsProps {
  row: MarketHistoryRange;
  expanded: boolean;
  win: { start: string; end: string };
  onWin: (w: { start: string; end: string }) => void;
  onOpen: () => void;
  onSubmit: () => void;
  submitting: boolean;
  onClose: () => void;
  onReprobe: () => void;
  onRemove: () => void;
  removing: boolean;
}

/** 一行覆盖记录 + 可选的内联补数展开行。 */
function BackfillRows(p: BackfillRowsProps) {
  const { row } = p;
  const hint = row.last_error
    ? { text: row.last_error, cls: 'text-loss' }
    : row.probe_truncated
      ? {
          text: '已达探测上限（终端「图表最大 K 线数」限制），可在终端设置中调大',
          cls: 'text-amber-400',
        }
      : { text: '', cls: '' };
  return (
    <>
      <tr>
        <td className="font-mono text-gray-200">{row.symbol}</td>
        <td className="font-mono text-gray-400">{row.timeframe}</td>
        <td className="text-xs text-gray-500">{fmtTs(row.first_bar_time)}</td>
        <td className="text-xs text-gray-500">{fmtTs(row.last_bar_time)}</td>
        <td className="text-right font-mono text-gray-200">
          {row.bars_count.toLocaleString('zh-CN')}
        </td>
        <td className="text-xs text-gray-500">{fmtTs(row.last_synced_at)}</td>
        <td className={`text-xs max-w-56 truncate ${hint.cls}`} title={hint.text}>
          {hint.text}
        </td>
        <td className="text-right whitespace-nowrap">
          <button
            type="button"
            className="text-blue-300 hover:text-blue-200 text-sm mr-3"
            onClick={p.expanded ? p.onClose : p.onOpen}
          >
            <CalendarClock className="w-3.5 h-3.5 inline" /> 补数
          </button>
          <button
            type="button"
            className="text-gray-400 hover:text-gray-200 text-sm mr-3 disabled:opacity-50"
            disabled={p.removing}
            onClick={p.onReprobe}
          >
            重新探测
          </button>
          <button
            type="button"
            className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
            disabled={p.removing}
            onClick={p.onRemove}
          >
            <Trash2 className="w-3.5 h-3.5 inline" /> 移除
          </button>
        </td>
      </tr>
      {p.expanded && (
        <tr>
          <td colSpan={8} className="bg-surface-800/60">
            <div className="flex flex-wrap items-end gap-3 py-2 px-2">
              <label className="flex flex-col gap-1">
                <span className="text-xs text-gray-500">开始时间</span>
                <input
                  type="datetime-local"
                  value={p.win.start}
                  onChange={(e) => p.onWin({ ...p.win, start: e.target.value })}
                  className="input w-56"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs text-gray-500">结束时间</span>
                <input
                  type="datetime-local"
                  value={p.win.end}
                  onChange={(e) => p.onWin({ ...p.win, end: e.target.value })}
                  className="input w-56"
                />
              </label>
              <button
                type="button"
                className="btn-primary h-9 px-4 disabled:opacity-50"
                disabled={p.submitting}
                onClick={p.onSubmit}
              >
                {p.submitting ? (
                  <Loader2 className="w-4 h-4 inline animate-spin mr-1" />
                ) : (
                  <RefreshCw className="w-4 h-4 inline mr-1" />
                )}
                确认补数
              </button>
              <span className="text-xs text-gray-500 pb-2">
                可能需要约 1 分钟；已到达可用历史边界时按「非错误」处理。
              </span>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
