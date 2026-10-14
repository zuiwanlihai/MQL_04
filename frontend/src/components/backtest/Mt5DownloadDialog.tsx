/**
 * MT5 下载数据弹窗：选择品种 / 周期 / 数量，从 MT5 拉取K线写入本地缓存。
 * 本地已有该条数据时增量合并（只补最近的新K线，旧历史保留）。
 */
import { useEffect, useState } from 'react';
import { CloudDownload, Loader2, X } from 'lucide-react';
import { useSymbols } from '@/hooks/useMarket';
import { listKlineCache, type KlineCacheEntry } from '@/lib/klineCache';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] as const;
const COUNTS = [500, 1000, 2000, 5000, 10000, 20000] as const;

const fmt = (t: number) =>
  new Date(t).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' });

interface Props {
  open: boolean;
  onClose: () => void;
  /** 执行下载并增量合并（>5000 根自动分段）；返回 true 表示成功 */
  onDownload: (
    symbol: string,
    timeframe: string,
    count: number,
    onProgress?: (seg: number, total: number) => void,
  ) => Promise<boolean>;
  /** 正在下载的 key（symbol:tf），用于禁用按钮 */
  busyKey: string | null;
  /** 默认选中的品种（取缓存第一条） */
  defaultSymbol?: string;
}

export default function Mt5DownloadDialog({ open, onClose, onDownload, busyKey, defaultSymbol }: Props) {
  const symbols = useSymbols('', '').data?.items ?? [];
  const [symbol, setSymbol] = useState(defaultSymbol ?? '');
  const [timeframe, setTimeframe] = useState<string>('H1');
  const [count, setCount] = useState<number>(5000);
  const [submitting, setSubmitting] = useState(false);
  const [progress, setProgress] = useState<{ seg: number; total: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const sym = symbol || (symbols[0]?.name ?? '');
  const [existing, setExisting] = useState<KlineCacheEntry | null>(null);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    listKlineCache()
      .then((list) => {
        if (alive) {
          setExisting(list.find((e) => e.symbol === sym && e.timeframe === timeframe) ?? null);
        }
      })
      .catch(() => {
        if (alive) setExisting(null);
      });
    return () => {
      alive = false;
    };
  }, [open, sym, timeframe]);

  if (!open) return null;

  const handleSubmit = async () => {
    if (!sym) return;
    setSubmitting(true);
    setErr(null);
    setProgress(null);
    try {
      const ok = await onDownload(sym, timeframe, count, (seg, total) => setProgress({ seg, total }));
      if (ok) onClose();
    } finally {
      setSubmitting(false);
      setProgress(null);
    }
  };

  const busy = submitting || busyKey !== null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={busy ? undefined : onClose}>
      <div className="card w-full max-w-md" onClick={(ev) => ev.stopPropagation()}>
        <div className="card-body">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-semibold text-white flex items-center gap-2">
              <CloudDownload className="w-4 h-4 text-blue-300" /> 从 MT5 下载数据
            </h3>
            <button type="button" className="btn-ghost px-2" onClick={onClose} disabled={busy}>
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="mt-4 space-y-3">
            <label className="block">
              <span className="text-xs text-gray-500">品种</span>
              <select
                className="input mt-1"
                value={sym}
                onChange={(e) => setSymbol(e.target.value)}
                disabled={busy}
              >
                {symbols.length === 0 && <option value="">加载中…</option>}
                {symbols.map((s) => (
                  <option key={s.name} value={s.name}>{s.name}</option>
                ))}
              </select>
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-gray-500">周期</span>
                <select
                  className="input mt-1"
                  value={timeframe}
                  onChange={(e) => setTimeframe(e.target.value)}
                  disabled={busy}
                >
                  {TIMEFRAMES.map((tf) => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-gray-500">数量（根）</span>
                <select
                  className="input mt-1"
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  disabled={busy}
                >
                  {COUNTS.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </label>
            </div>

            <p className="text-xs text-gray-500 bg-surface-800/60 rounded px-3 py-2">
              {existing ? (
                <>
                  本地已有 <span className="text-gray-300 font-mono">{existing.bars}</span> 根
                  （{fmt(existing.firstTs)} ~ {fmt(existing.lastTs)}），下载将<b className="text-gray-300">只增量更新最近的数据</b>，旧历史保留。
                </>
              ) : (
                <>本地无该条数据，将全新写入。下载会覆盖该品种+周期的旧数据（如有）。</>
              )}
            </p>

            {count > 5000 && (
              <p className="text-xs text-amber-300/90 bg-amber-400/10 border border-amber-400/20 rounded px-3 py-2">
                将自动分 {Math.ceil(count / 5000)} 段下载（每段 5000 根），耗时稍长；
                {count > 10000 && ' 大容量数据占用浏览器存储较多，建议只对重点品种使用。'}
              </p>
            )}

            {err && <p className="text-sm text-loss">{err}</p>}

            <button
              type="button"
              className="btn-primary w-full justify-center"
              disabled={busy || !sym}
              onClick={handleSubmit}
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CloudDownload className="w-4 h-4" />}
              {busy
                ? `下载中${progress && progress.total > 1 ? `（第 ${progress.seg}/${progress.total} 段）` : '…'}`
                : `下载 ${sym || ''} ${timeframe} · ${count} 根`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
