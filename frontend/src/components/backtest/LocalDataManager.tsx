/**
 * 本地数据管理（回测中心第三个页签）：
 * 管理服务端共享K线缓存（全局一份，所有账户/浏览器共用；浏览器 localStorage
 * 仅作读加速）。数据供回测"本地缓存数据"来源使用；删除自选不影响这里的数据。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CloudDownload, Database, Download, Eye, HardDriveDownload, Loader2, RefreshCw, Trash2, X } from 'lucide-react';
import {
  clearKlineCache,
  deleteKlineCache,
  listKlineCache,
  loadKlines,
  mergeKlines,
  type KlineBar,
  type KlineCacheEntry,
} from '@/lib/klineCache';
import { api, apiError } from '@/lib/api';
import type { PriceBar } from '@/types/api';
import Mt5DownloadDialog from '@/components/backtest/Mt5DownloadDialog';

const fmtRange = (a: number, b: number) =>
  `${new Date(a).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })} ~ ${new Date(b).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}`;

const fmtBarTime = (t: number) =>
  new Date(t * 1000).toLocaleString('zh-CN', { year: '2-digit', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

const TF_SEC: Record<string, number> = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 };

/** 下载数据：该条缓存导出为 CSV（北京时间，UTF-8 BOM 便于 Excel 打开）。 */
async function downloadCsv(e: KlineCacheEntry) {
  const bars = await loadKlines(e.symbol, e.timeframe);
  if (bars.length === 0) return;
  const header = 'time,open,high,low,close,tick_volume,spread';
  const pad = (n: number) => String(n).padStart(2, '0');
  const rows = bars.map((b) => {
    const d = new Date(b.time * 1000);
    const t = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    return [t, b.open, b.high, b.low, b.close, b.tick_volume ?? 0, b.spread ?? 0].join(',');
  });
  const d1 = new Date(bars[0].time * 1000).toISOString().slice(0, 10);
  const d2 = new Date(bars[bars.length - 1].time * 1000).toISOString().slice(0, 10);
  const blob = new Blob([`\uFEFF${header}\n${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${e.symbol}_${e.timeframe}_${d1}_${d2}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** 查看数据：弹窗表格浏览该条缓存的全部K线（新→旧）。 */
function DataViewModal({ entry, onClose }: { entry: KlineCacheEntry; onClose: () => void }) {
  const [bars, setBars] = useState<KlineBar[]>([]);
  useEffect(() => {
    let alive = true;
    loadKlines(entry.symbol, entry.timeframe).then((b) => {
      if (alive) setBars([...b].reverse());
    });
    return () => {
      alive = false;
    };
  }, [entry.symbol, entry.timeframe]);
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="card w-full max-w-4xl max-h-[85vh] flex flex-col"
        onClick={(ev) => ev.stopPropagation()}
      >
        <div className="card-body flex-none pb-3 flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold text-white font-mono">
              {entry.symbol} / {entry.timeframe}
              <span className="ml-2 text-xs text-gray-500 font-sans">{entry.bars} 根 · 新→旧</span>
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">覆盖 {fmtRange(entry.firstTs, entry.lastTs)} · 北京时间</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn-ghost px-2 text-sm"
              onClick={() => downloadCsv(entry)}
              title="下载该条数据为 CSV"
            >
              <Download className="w-4 h-4 inline" /> 下载
            </button>
            <button type="button" className="btn-ghost px-2" onClick={onClose} title="关闭">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className="overflow-y-auto px-6 pb-6">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface-900">
              <tr className="text-left text-xs text-gray-500 border-b border-gray-700/60">
                <th className="py-2 pr-4 font-normal">时间</th>
                <th className="py-2 pr-4 font-normal text-right">开</th>
                <th className="py-2 pr-4 font-normal text-right">高</th>
                <th className="py-2 pr-4 font-normal text-right">低</th>
                <th className="py-2 pr-4 font-normal text-right">收</th>
                <th className="py-2 pr-4 font-normal text-right">Tick量</th>
                <th className="py-2 font-normal text-right">点差</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((b) => (
                <tr key={b.time} className="border-b border-gray-800/40 hover:bg-surface-800/60">
                  <td className="py-1.5 pr-4 text-gray-400 font-mono text-xs whitespace-nowrap">{fmtBarTime(b.time)}</td>
                  <td className="py-1.5 pr-4 text-gray-200 font-mono text-right">{b.open}</td>
                  <td className="py-1.5 pr-4 text-gray-200 font-mono text-right">{b.high}</td>
                  <td className="py-1.5 pr-4 text-gray-200 font-mono text-right">{b.low}</td>
                  <td className="py-1.5 pr-4 text-gray-200 font-mono text-right">{b.close}</td>
                  <td className="py-1.5 pr-4 text-gray-500 font-mono text-right">{b.tick_volume ?? 0}</td>
                  <td className="py-1.5 text-gray-500 font-mono text-right">{b.spread ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function LocalDataManager() {
  const [entries, setEntries] = useState<KlineCacheEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [viewing, setViewing] = useState<KlineCacheEntry | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [fetchErr, setFetchErr] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [dlOpen, setDlOpen] = useState(false);
  // 批量更新所有已下载数据
  const [syncing, setSyncing] = useState(false);
  const [syncProgress, setSyncProgress] = useState<{ done: number; total: number } | null>(null);
  // 货币标签筛选：null = 全部
  const [curFilter, setCurFilter] = useState<string | null>(null);

  // 从 MT5 拉取某品种/周期最近 count 根K线（>5000 自动按时间分段拼接，
  // 后端单次上限 5000），升序去重后返回。对话框、行内按钮、批量更新共用。
  const fetchRecentBars = async (
    symbol: string,
    timeframe: string,
    count: number,
    onProgress?: (seg: number, total: number) => void,
  ): Promise<PriceBar[]> => {
    const SEG = 5000;
    const segs = Math.ceil(count / SEG);
    const all: PriceBar[] = [];
    let endTs: number | undefined;
    for (let i = 0; i < segs && all.length < count; i++) {
      onProgress?.(i + 1, segs);
      const want = Math.min(SEG, count - all.length);
      const params: Record<string, string | number> = { timeframe, limit: want };
      if (endTs !== undefined) {
        params.end = endTs;
        params.start = endTs - (TF_SEC[timeframe] ?? 3600) * want - 7200;
      }
      const { data } = await api.get<PriceBar[]>(`/market/symbols/${symbol}/klines`, { params });
      if (!data || data.length === 0) break;
      all.unshift(...data);
      if (data.length < want) break; // 已到 MT5 可用历史尽头
      endTs = data[0].time - 1;
    }
    all.sort((a, b) => a.time - b.time);
    const dedup: PriceBar[] = [];
    for (const b of all) {
      if (dedup.length === 0 || dedup[dedup.length - 1].time !== b.time) dedup.push(b);
    }
    return dedup;
  };

  // 从 MT5 拉取K线并增量合并进本地缓存（对话框与行内按钮共用）。
  const downloadAndMerge = async (
    symbol: string,
    timeframe: string,
    count: number,
    onProgress?: (seg: number, total: number) => void,
  ): Promise<boolean> => {
    const key = `${symbol}:${timeframe}`;
    setDownloading(key);
    setFetchErr(null);
    setOkMsg(null);
    try {
      const dedup = await fetchRecentBars(symbol, timeframe, count, onProgress);
      if (dedup.length === 0) {
        setFetchErr(`${symbol} ${timeframe} 未取到K线（行情源可能未就绪），请稍后重试`);
        return false;
      }
      const r = await mergeKlines(symbol, timeframe, dedup);
      setOkMsg(`${symbol} ${timeframe}：已同步至最新，新增 ${r.added} 根，缓存共 ${r.total} 根`);
      refresh();
      return true;
    } catch (ex) {
      setFetchErr(`下载失败：${apiError(ex)}`);
      return false;
    } finally {
      setDownloading(null);
    }
  };

  // 批量更新：遍历所有已下载数据，按"距最新K线的时间缺口"估算需要补的根数，
  // 只拉取并合并新增部分（mergeKlines 按时间去重，多取的旧K线不会重复计入）。
  const syncAll = async () => {
    if (entries.length === 0 || syncing) return;
    setSyncing(true);
    setFetchErr(null);
    setOkMsg(null);
    let totalAdded = 0;
    let failed = 0;
    const failures: string[] = [];
    try {
      for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        setSyncProgress({ done: i + 1, total: entries.length });
        try {
          const tfSec = TF_SEC[e.timeframe] ?? 3600;
          const gap = Math.ceil((Date.now() / 1000 - e.lastTs / 1000) / tfSec) + 20;
          const want = Math.min(Math.max(gap, 50), 20000);
          const bars = await fetchRecentBars(e.symbol, e.timeframe, want);
          if (bars.length === 0) {
            failed += 1;
            failures.push(`${e.symbol} ${e.timeframe} 未取到K线`);
            continue;
          }
          const r = await mergeKlines(e.symbol, e.timeframe, bars);
          totalAdded += r.added;
        } catch (ex) {
          failed += 1;
          failures.push(`${e.symbol} ${e.timeframe}：${apiError(ex)}`);
          console.warn('[syncAll] entry failed:', e.symbol, e.timeframe, ex);
        }
      }
      if (totalAdded > 0 || failed === 0) {
        const head = `更新完成：${entries.length - failed}/${entries.length} 条已同步，共新增 ${totalAdded} 根K线`;
        if (failed === 0) {
          setOkMsg(totalAdded > 0 ? head : '更新完成：所有数据已是最新，无新增K线');
        } else {
          setOkMsg(`${head}；${failed} 条失败 —— ${failures.slice(0, 2).join('；')}`);
        }
      } else {
        setFetchErr(`更新失败：${failures.slice(0, 2).join('；')}${failures.length > 2 ? ` 等 ${failed} 条` : ''}`);
      }
      refresh();
    } finally {
      setSyncing(false);
      setSyncProgress(null);
    }
  };

  const refresh = useCallback(() => {
    listKlineCache()
      .then((list) => {
        setEntries(list);
        setLoaded(true);
      })
      .catch((ex) => {
        setFetchErr(`读取缓存失败：${apiError(ex)}`);
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const totalKb = entries.reduce((s, e) => s + e.kb, 0);

  // 货币/品种标签：按基础品种名（去掉 .E 之类经纪商后缀）聚合
  const baseSym = (symbol: string) => symbol.split('.')[0];
  // 周期排序权重（小周期在前）
  const TF_ORDER = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
  const tfRank = (tf: string) => {
    const i = TF_ORDER.indexOf(tf);
    return i < 0 ? 99 : i;
  };
  // 按品种 + 周期自动排序（M1 → M5 → M15 → M30 → H1 → H4 → D1）
  const sortedEntries = useMemo(
    () =>
      [...entries].sort(
        (a, b) =>
          baseSym(a.symbol).localeCompare(baseSym(b.symbol)) ||
          tfRank(a.timeframe) - tfRank(b.timeframe),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [entries],
  );
  const currencies = useMemo(() => {
    const seen: string[] = [];
    for (const e of sortedEntries) {
      const base = baseSym(e.symbol);
      if (!seen.includes(base)) seen.push(base);
    }
    return seen;
     
  }, [sortedEntries]);
  const activeFilter = curFilter !== null && currencies.includes(curFilter) ? curFilter : null;
  const filtered = activeFilter
    ? sortedEntries.filter((e) => baseSym(e.symbol) === activeFilter)
    : sortedEntries;
  const countFor = (c: string) => sortedEntries.filter((e) => baseSym(e.symbol) === c).length;
  // 当前缓存盘别（列表按活动 MT5 会话隔离，取首条即可判定）
  const isRealScope = entries[0]?.account_type === 'real';

  const handleDelete = (e: KlineCacheEntry) => {
    if (window.confirm(`删除缓存 ${e.symbol} ${e.timeframe}（${e.bars} 根）？删除后回测"本地数据"将不可用该品种，直到重新下载。`)) {
      deleteKlineCache(e.symbol, e.timeframe)
        .then(refresh)
        .catch((ex) => setFetchErr(`删除失败：${apiError(ex)}`));
    }
  };

  return (
    <div className="card">
      <div className="card-body">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-base font-semibold text-white flex items-center gap-2">
              <Database className="w-4 h-4 text-blue-300" /> 本地K线数据
              {entries.length > 0 && (
                <span
                  className={`text-2xs px-2 py-0.5 rounded-full border font-normal ${
                    isRealScope
                      ? 'text-amber-300 border-amber-400/40 bg-amber-400/10'
                      : 'text-blue-300 border-blue-400/40 bg-blue-400/10'
                  }`}
                >
                  {isRealScope ? '实盘数据' : '虚拟下单数据'}
                </span>
              )}
            </h2>
            <p className="text-xs text-gray-500 mt-1">
              "MT5下载数据"可选品种/周期/数量拉取K线写入<b>服务器共享缓存</b>（全局一份，所有账户/浏览器通用），
              本地已有数据时只增量更新最近的K线。浏览自选品种K线时也会自动缓存。
              数据用于回测"本地缓存数据"来源；删除自选品种不会清理这里的数据。
              <b className="text-gray-400">虚拟下单与实盘数据相互隔离</b>，此处仅显示当前登录盘的缓存。
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              className="btn-primary"
              disabled={downloading !== null || syncing}
              onClick={() => setDlOpen(true)}
              title="选择品种 / 周期 / 数量，从 MT5 下载K线到本地缓存"
            >
              <CloudDownload className="w-4 h-4" /> MT5下载数据
            </button>
            <span className="text-xs text-gray-500">
              {activeFilter ? `${filtered.length} / ${entries.length}` : entries.length} 个条目 · 共 {totalKb.toFixed(1)} KB
            </span>
            <button
              type="button"
              className="btn-ghost px-2"
              disabled={entries.length === 0 || syncing || downloading !== null}
              onClick={syncAll}
              title="同步所有已下载数据：只补最近新增的K线"
            >
              {syncing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {syncing && syncProgress ? ` 更新中 ${syncProgress.done}/${syncProgress.total}` : ' 更新'}
            </button>
            {entries.length > 0 && (
              <button
                type="button"
                className="text-sm text-loss hover:text-loss-light px-2 py-1"
                onClick={() => {
                  if (window.confirm(`清空${isRealScope ? '实盘' : '虚拟下单'}的全部K线缓存？回测"本地数据"来源将不可用这些品种，直到重新下载。（另一盘的数据不受影响）`)) {
                    clearKlineCache()
                      .then(refresh)
                      .catch((ex) => setFetchErr(`清空失败：${apiError(ex)}`));
                  }
                }}
              >
                <Trash2 className="w-3.5 h-3.5 inline" /> 清空本盘
              </button>
            )}
            {currencies.length > 1 && (
              <div className="flex items-center gap-1.5 flex-wrap w-full sm:w-auto" title="按货币/品种筛选">
                <button
                  type="button"
                  onClick={() => setCurFilter(null)}
                  className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
                    activeFilter === null
                      ? 'bg-blue-500/20 border-blue-400/60 text-blue-200'
                      : 'bg-surface-800/60 border-gray-700/60 text-gray-400 hover:text-gray-200 hover:border-gray-500/60'
                  }`}
                >
                  全部
                </button>
                {currencies.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setCurFilter(activeFilter === c ? null : c)}
                    className={`px-2.5 py-1 rounded-full text-xs border font-mono transition-colors ${
                      activeFilter === c
                        ? 'bg-blue-500/20 border-blue-400/60 text-blue-200'
                        : 'bg-surface-800/60 border-gray-700/60 text-gray-400 hover:text-gray-200 hover:border-gray-500/60'
                    }`}
                  >
                    {c} <span className="text-gray-500">{countFor(c)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {okMsg && (
          <div className="mt-3 px-3 py-2 text-sm text-profit bg-profit/10 border border-profit/30 rounded">
            {okMsg}
          </div>
        )}
        {fetchErr && (
          <div className="mt-3 px-3 py-2 text-sm text-loss bg-loss/10 border border-loss/30 rounded">
            {fetchErr}
          </div>
        )}

        {entries.length === 0 ? (
          <div className="text-center text-gray-500 py-10 text-sm">
            <HardDriveDownload className="w-8 h-8 mx-auto mb-3 text-gray-600" />
            {loaded ? (
              <>
                暂无本地缓存数据。
                <br />
                打开任一自选品种的K线图（如行情页 → 品种详情），数据会自动缓存到这里。
              </>
            ) : (
              '读取中…'
            )}
          </div>
        ) : (
          <div className="overflow-x-auto mt-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 border-b border-gray-700/60">
                  <th className="py-2 pr-4 font-normal">品种 / 周期</th>
                  <th className="py-2 pr-4 font-normal">K线根数</th>
                  <th className="py-2 pr-4 font-normal">覆盖区间</th>
                  <th className="py-2 pr-4 font-normal">大小</th>
                  <th className="py-2 font-normal text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((e) => (
                  <tr key={`${e.symbol}:${e.timeframe}`} className="border-b border-gray-800/40">
                    <td className="py-2.5 pr-4 text-gray-200 font-mono">
                      {e.symbol} <span className="text-gray-600">/</span> {e.timeframe}
                    </td>
                    <td className="py-2.5 pr-4 text-gray-300 font-mono">{e.bars}</td>
                    <td className="py-2.5 pr-4 text-xs text-gray-400 font-mono whitespace-nowrap">
                      {fmtRange(e.firstTs, e.lastTs)}
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-gray-500 font-mono">{e.kb.toFixed(1)} KB</td>
                    <td className="py-2.5 text-right whitespace-nowrap">
                      <button
                        type="button"
                        className="text-blue-300 hover:text-blue-200 text-sm px-1 disabled:opacity-50"
                        title="从 MT5 增量更新该品种最近 5000 根（旧历史保留）"
                        disabled={downloading !== null || syncing}
                        onClick={() => downloadAndMerge(e.symbol, e.timeframe, 5000)}
                      >
                        {downloading === `${e.symbol}:${e.timeframe}` ? (
                          <Loader2 className="w-3.5 h-3.5 inline animate-spin" />
                        ) : (
                          <CloudDownload className="w-3.5 h-3.5 inline" />
                        )}{' '}
                        从MT5下载
                      </button>
                      <span className="text-gray-700 mx-1">|</span>
                      <button
                        type="button"
                        className="text-blue-300 hover:text-blue-200 text-sm px-1"
                        title="查看该条缓存的K线数据"
                        onClick={() => setViewing(e)}
                      >
                        <Eye className="w-3.5 h-3.5 inline" /> 查看
                      </button>
                      <span className="text-gray-700 mx-1">|</span>
                      <button
                        type="button"
                        className="text-gray-400 hover:text-gray-200 text-sm px-1"
                        title="导出该条数据为 CSV 文件"
                        onClick={() => downloadCsv(e)}
                      >
                        <Download className="w-3.5 h-3.5 inline" /> 导出
                      </button>
                      <span className="text-gray-700 mx-1">|</span>
                      <button
                        type="button"
                        className="text-loss hover:text-loss-light text-sm px-1"
                        title="删除该条缓存"
                        onClick={() => handleDelete(e)}
                      >
                        <Trash2 className="w-3.5 h-3.5 inline" /> 删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {viewing && <DataViewModal entry={viewing} onClose={() => setViewing(null)} />}

      <Mt5DownloadDialog
        open={dlOpen}
        onClose={() => setDlOpen(false)}
        onDownload={downloadAndMerge}
        busyKey={downloading}
        defaultSymbol={entries[0]?.symbol}
      />
    </div>
  );
}
