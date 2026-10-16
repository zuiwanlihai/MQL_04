import { useState } from 'react';
import { BarChart2, ChevronLeft, ChevronRight, Download, GitCompare, Layers, Loader2, Plus, RotateCcw, Save, Star, Trash2, X, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import BacktestCreateDialog from '@/components/backtest/BacktestCreateDialog';
import CompareDialog from '@/components/backtest/CompareDialog';
import PortfolioCreateDialog from '@/components/backtest/PortfolioCreateDialog';
import CandlestickChart from '@/components/charts/CandlestickChart';
import { MODE_LABELS, useBacktests, useBulkDeleteBacktests, useCancelBacktest, useDeleteBacktest, usePortfolios, useRateBacktest, useRerunBacktest } from '@/hooks/useBacktests';
import LocalDataManager from '@/components/backtest/LocalDataManager';
import { useQueueOverview } from '@/hooks/useNotebooks';
import { useStrategies } from '@/hooks/useStrategies';
import { api, apiError } from '@/lib/api';
import { generateCandles, SHAPE_LABELS, type Candle, type TrendShape } from '@/lib/syntheticData';
import type { BacktestStatus, PortfolioStatus } from '@/types/api';

const STATUS_OPTIONS: { value: '' | BacktestStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '排队中' },
  { value: 'running', label: '运行中' },
  { value: 'cancelling', label: '取消中' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
];

const STATUS_STYLES: Record<BacktestStatus, string> = {
  pending: 'bg-surface-700 text-gray-300 border-gray-600',
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  cancelling: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  completed: 'bg-profit/15 text-profit border-profit/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
  cancelled: 'bg-gray-500/15 text-gray-400 border-gray-500/40',
};

const STATUS_LABELS: Record<BacktestStatus, string> = {
  pending: '排队中',
  running: '运行中',
  cancelling: '取消中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

const ACTIVE: BacktestStatus[] = ['pending', 'running', 'cancelling'];

const PF_STATUS_STYLES: Record<PortfolioStatus, string> = {
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  completed: 'bg-profit/15 text-profit border-profit/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const PF_STATUS_LABELS: Record<PortfolioStatus, string> = {
  running: '聚合中',
  completed: '已完成',
  failed: '失败',
};

export default function BacktestListPage() {
  const [tab, setTab] = useState<'tasks' | 'portfolios' | 'viz' | 'localdata'>('tasks');
  const [page, setPage] = useState(1);
  const [pfPage, setPfPage] = useState(1);
  const [status, setStatus] = useState<'' | BacktestStatus>('');
  const [strategyId, setStrategyId] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [pfCreateOpen, setPfCreateOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);

  // ─── viz state（虚拟数据生成面板） ───
  const [vizStart, setVizStart] = useState(() => {
    const d = new Date();
    d.setMonth(d.getMonth() - 3);  // 默认 3 个月前
    return d.toISOString().slice(0, 16);
  });
  const [vizEnd, setVizEnd] = useState(() => new Date().toISOString().slice(0, 16));
  const [vizBasePrice, setVizBasePrice] = useState(1900);
  const [vizVol, setVizVol] = useState(0.0005);
  const [vizShape, setVizShape] = useState<TrendShape>('random');
  const [vizTrendStrength, setVizTrendStrength] = useState(0.15);
  const [vizData, setVizData] = useState<Candle[]>([]);
  const [vizError, setVizError] = useState<string | null>(null);
  const [vizLoading, setVizLoading] = useState(false);

  const runGenerate = () => {
    setVizError(null);
    setVizLoading(true);
    try {
      const data = generateCandles(vizStart, vizEnd, 'M1', vizBasePrice, vizVol, vizShape, vizTrendStrength);
      setVizData(data);
    } catch (e) {
      setVizError((e as Error).message);
      setVizData([]);
    } finally {
      setVizLoading(false);
    }
  };

  // ─── 保存合成数据集（前端直接发 bars 数组，后端存 Parquet/JSONB） ───
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveDesc, setSaveDesc] = useState('');
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveResult, setSaveResult] = useState<string | null>(null);

  const doSave = async () => {
    if (!saveName.trim()) { setSaveResult('请输入名称'); return; }
    if (vizData.length === 0) { setSaveResult('还没有数据可以保存'); return; }
    setSaveLoading(true);
    setSaveResult(null);
    try {
      const firstTime = vizData[0].time;
      const lastTime = vizData[vizData.length - 1].time + 60;
      const totalBars = vizData.length;

      // ── 形状分两路，统一走 Parquet 存储 ──
      //   简单形状（random / uptrend / downtrend）  → 只传参数，后端 numpy GBM 生成
      //   复杂形状（V / 倒V / W / M）               → 传前端已生成的 bars，后端直接写 Parquet
      const SIMPLE_SHAPES: TrendShape[] = ['random', 'uptrend', 'downtrend'];
      const isSimple = SIMPLE_SHAPES.includes(vizShape);

      let body: any;
      if (isSimple) {
        // 每根 bar 的 log drift（uptrend 正、downtrend 负、random 零）
        const drift =
          vizShape === 'uptrend'   ?  vizTrendStrength / totalBars
          : vizShape === 'downtrend' ? -vizTrendStrength / totalBars
          :                             0;
        body = {
          name: saveName.trim(),
          description: saveDesc.trim() || null,
          symbol: 'XAUUSD',
          base_price: vizBasePrice,
          volatility: vizVol,
          drift,
          first_time: firstTime,
          last_time: lastTime,
          seed: null,
          // bars 字段不传 → 后端走 GBM 路径
        };
      } else {
        // 复杂形状：前端本地已生成好 bars，传给后端直接写 Parquet
        body = {
          name: saveName.trim(),
          description: saveDesc.trim() || null,
          symbol: 'XAUUSD',
          base_price: vizBasePrice,
          volatility: vizVol,
          bars: vizData.map((c) => ({
            time: c.time, open: c.open, high: c.high,
            low: c.low, close: c.close, volume: c.volume,
          })),
        };
      }

      await api.post('/synthetic-datasets/generate', body);
      setSaveResult('保存成功 ✅');
      setTimeout(() => { setSaveOpen(false); setSaveResult(null); }, 1500);
    } catch (e) {
      setSaveResult(apiError(e));
    } finally {
      setSaveLoading(false);
    }
  };

  const toggleSelect = (id: string) =>
    setSelected((prev) =>
      prev.includes(id)
        ? prev.filter((x) => x !== id)
        : prev.length >= 4
          ? prev
          : [...prev, id],
    );

  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const list = useBacktests({ page, pageSize: 20, strategyId, status });
  const portfolios = usePortfolios(pfPage);
  const cancel = useCancelBacktest();
  const rerun = useRerunBacktest();
  const rate = useRateBacktest();
  const del = useDeleteBacktest();
  const bulkDel = useBulkDeleteBacktests();
  const queue = useQueueOverview();
  const data = list.data;
  const pfData = portfolios.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          <div>
            <h1 className="text-xl font-semibold text-white">回测中心</h1>
            <p className="text-sm text-gray-500 mt-1">
              子进程沙箱回放 · 三档撮合假设 · 敏感性对比（SDK v1 / Python）
            </p>
          </div>
          {queue.data && (
            <div className="flex items-center gap-3 text-xs text-gray-400 border-l border-gray-700/60 pl-3 ml-1">
              <span className="text-gray-500 font-medium">队列</span>
              <span>
                运行中{' '}
                <span className="text-blue-300 font-semibold">{queue.data.running.length}</span>
              </span>
              <span>
                排队{' '}
                <span className="text-yellow-300 font-semibold">{queue.data.pending_count}</span>
              </span>
            </div>
          )}
        </div>
        {tab !== 'localdata' && (tab === 'tasks' ? (
          <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>
            <Plus className="w-4 h-4" /> 新建回测
          </button>
        ) : (
          <button type="button" className="btn-primary" onClick={() => setPfCreateOpen(true)}>
            <Layers className="w-4 h-4" /> 新建组合回测
          </button>
        ))}
      </div>

      <div className="flex items-center gap-1 border-b border-gray-700/60">
        <button
          type="button"
          onClick={() => setTab('tasks')}
          className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
            tab === 'tasks'
              ? 'border-blue-400 text-blue-300 font-medium'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          回测任务
        </button>
        <button
          type="button"
          onClick={() => setTab('portfolios')}
          className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
            tab === 'portfolios'
              ? 'border-blue-400 text-blue-300 font-medium'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          组合回测
        </button>
        <button
          type="button"
          onClick={() => setTab('localdata')}
          className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
            tab === 'localdata'
              ? 'border-blue-400 text-blue-300 font-medium'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          本地数据管理
        </button>
        <button
          type="button"
          onClick={() => setTab('viz')}
          className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
            tab === 'viz'
              ? 'border-blue-400 text-blue-300 font-medium'
              : 'border-transparent text-gray-500 hover:text-gray-300'
          }`}
        >
          虚拟数据生成
        </button>
        {tab === 'portfolios' && pfData && (
          <span className="ml-auto text-xs text-gray-500">共 {pfData.total} 个组合</span>
        )}
      </div>

      {tab === 'tasks' && (
      <>
      <div className="flex items-center gap-3 flex-wrap">
        <select
          className="input-dark w-48"
          value={strategyId}
          onChange={(e) => {
            setStrategyId(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部策略</option>
          {(strategies.data?.items ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <select
          className="input-dark w-32"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as '' | BacktestStatus);
            setPage(1);
          }}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {data && (
          <span className="text-xs text-gray-500">
            共 {data.total} 个任务 · 第 {data.page}/{data.total_pages} 页
          </span>
        )}
        {selected.length > 0 && (
          <div className="flex items-center gap-2 ml-auto">
            <button
              type="button"
              className="px-3 py-1.5 flex items-center gap-1.5 disabled:opacity-50 rounded border border-loss/40 bg-loss/10 text-loss hover:bg-loss/20"
              disabled={bulkDel.isPending}
              onClick={() => {
                if (window.confirm(`确认删除选中的 ${selected.length} 条回测记录？运行中的任务会自动跳过，报告、成交与日志将一并删除，不可恢复。`)) {
                  bulkDel.mutate(selected, { onSuccess: () => setSelected([]) });
                }
              }}
              title="批量删除选中记录（运行中任务将跳过，不可恢复）"
            >
              {bulkDel.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              删除（{selected.length}）
            </button>
            <button
              type="button"
              className="btn-primary px-3 py-1.5 flex items-center gap-1.5 disabled:opacity-50"
              disabled={selected.length < 2}
              onClick={() => setCompareOpen(true)}
              title={selected.length < 2 ? '至少选择 2 个已完成任务' : '对比选中任务'}
            >
              <GitCompare className="w-4 h-4" /> 对比（{selected.length}）
            </button>
            <button
              type="button"
              className="btn-ghost px-2 py-1.5 flex items-center gap-1 text-gray-400"
              onClick={() => setSelected([])}
            >
              <X className="w-3.5 h-3.5" /> 清除
            </button>
          </div>
        )}
      </div>

      {list.isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(list.error)}</div>
        </div>
      )}

      {list.isLoading ? (
        <div className="card">
          <div className="card-body text-center text-gray-500 py-10">正在加载回测任务…</div>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="table-dark">
            <thead>
              <tr>
                <th className="w-8" />
                <th>策略 / 版本</th>
                <th>品种 / 周期</th>
                <th>区间</th>
                <th>档位</th>
                <th>状态 / 进度</th>
                <th>创建时间</th>
                <th>评分</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((t) => {
                const sname = strategies.data?.items.find((s) => s.id === t.strategy_id)?.name;
                const active = ACTIVE.includes(t.status);
                return (
                  <tr key={t.id}>
                    <td>
                      {t.status === 'completed' && (
                        <input
                          type="checkbox"
                          className="accent-blue-400 cursor-pointer"
                          checked={selected.includes(t.id)}
                          onChange={() => toggleSelect(t.id)}
                          title="加入对比"
                        />
                      )}
                    </td>
                    <td>
                      <span className="text-gray-100 text-sm">{sname ?? t.strategy_id.slice(0, 8)}</span>
                      <p className="text-xs text-gray-500 font-mono">
                        {t.strategy_version == null ? '工作副本' : `v${t.strategy_version}`}
                        {t.group_id && <span className="ml-2 text-blue-300">配对组</span>}
                      </p>
                    </td>
                    <td className="text-gray-400">
                      <span className="font-mono">{t.symbol}</span>
                      <span className="text-gray-600 mx-1">/</span>
                      <span className="font-mono">{t.timeframe}</span>
                      {t.data_source === 'local' && (
                        <span
                          className="ml-1.5 px-1.5 py-0.5 text-2xs rounded bg-blue-600/20 text-blue-300 align-middle"
                          title="K线数据来源：浏览器本地缓存"
                        >
                          本地
                        </span>
                      )}
                    </td>
                    <td className="text-xs text-gray-500 font-mono whitespace-nowrap">
                      {new Date(t.start_date).toLocaleDateString('zh-CN')} ~{' '}
                      {new Date(t.end_date).toLocaleDateString('zh-CN')}
                    </td>
                    <td>
                      <span
                        className={`px-2 py-0.5 rounded border text-xs ${
                          t.execution_mode === 'optimistic'
                            ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40'
                            : 'bg-surface-700 text-gray-300 border-gray-600'
                        }`}
                      >
                        {MODE_LABELS[t.execution_mode]}档
                      </span>
                    </td>
                    <td>
                      <div className="flex items-center gap-2">
                        <span
                          className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${STATUS_STYLES[t.status]}`}
                        >
                          {STATUS_LABELS[t.status]}
                        </span>
                        {active && (
                          <div className="w-20 h-1.5 rounded bg-surface-700 overflow-hidden">
                            <div
                              className="h-full bg-blue-400 transition-all"
                              style={{ width: `${t.progress_percent}%` }}
                            />
                          </div>
                        )}
                        {active && <span className="text-xs text-gray-500">{t.progress_percent}%</span>}
                        {t.timed_out && <span className="text-xs text-loss">超时</span>}
                      </div>
                      {t.status === 'failed' && t.error_message && (
                        <p className="text-xs text-loss mt-1 max-w-64 truncate" title={t.error_message}>
                          {t.error_code ? `[${t.error_code}] ` : ''}
                          {t.error_message}
                        </p>
                      )}
                    </td>
                    <td className="text-xs text-gray-500 whitespace-nowrap">
                      {new Date(t.created_at).toLocaleString('zh-CN')}
                    </td>
                    <td className="whitespace-nowrap">
                      <span className="inline-flex items-center gap-0.5">
                        {[1, 2, 3, 4, 5].map((n) => (
                          <button
                            key={n}
                            type="button"
                            disabled={rate.isPending}
                            className={`disabled:opacity-50 ${
                              n <= (t.rating ?? 0)
                                ? 'text-yellow-400 hover:text-yellow-300'
                                : 'text-gray-600 hover:text-yellow-400'
                            }`}
                            title={t.rating === n ? '点击清除评分' : `评 ${n} 星`}
                            onClick={() => rate.mutate({ id: t.id, rating: t.rating === n ? null : n })}
                          >
                            <Star className={`w-3.5 h-3.5 ${n <= (t.rating ?? 0) ? 'fill-current' : ''}`} />
                          </button>
                        ))}
                      </span>
                    </td>
                    <td className="text-right whitespace-nowrap">
                      {t.status === 'completed' ? (
                        <Link
                          to={`/backtest/${t.id}/report`}
                          className="text-blue-400 hover:text-blue-300 text-sm"
                        >
                          查看报告
                        </Link>
                      ) : (
                        <button
                          type="button"
                          disabled
                          className="text-gray-600 text-sm cursor-not-allowed"
                          title={active ? '任务进行中，完成后可查看报告' : '任务未产生报告'}
                        >
                          暂无报告
                        </button>
                      )}
                      {active && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                            disabled={cancel.isPending}
                            onClick={() => {
                              if (window.confirm('确认取消该回测任务？')) cancel.mutate(t.id);
                            }}
                            title="取消任务"
                          >
                            {cancel.isPending && cancel.variables === t.id ? (
                              <Loader2 className="w-3.5 h-3.5 inline animate-spin" />
                            ) : (
                              <XCircle className="w-3.5 h-3.5 inline" />
                            )}{' '}
                            取消
                          </button>
                        </>
                      )}
                      {(t.status === 'failed' || t.status === 'cancelled') && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            className="text-gray-400 hover:text-gray-200 text-sm disabled:opacity-50"
                            disabled={rerun.isPending}
                            onClick={() => rerun.mutate(t.id)}
                            title="按原配置重新提交（新随机种子）"
                          >
                            {rerun.isPending && rerun.variables === t.id ? (
                              <Loader2 className="w-3.5 h-3.5 inline animate-spin" />
                            ) : (
                              <RotateCcw className="w-3.5 h-3.5 inline" />
                            )}{' '}
                            重跑
                          </button>
                        </>
                      )}
                      {!active && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={del.isPending}
                            className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                            onClick={() => {
                              if (window.confirm('删除该回测记录？报告、成交与日志将一并删除，不可恢复。')) {
                                del.mutate(t.id);
                              }
                            }}
                            title="删除记录（不可恢复）"
                          >
                            {del.isPending && del.variables === t.id ? (
                              <Loader2 className="w-3.5 h-3.5 inline animate-spin" />
                            ) : (
                              <Trash2 className="w-3.5 h-3.5 inline" />
                            )}{' '}
                            删除
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {(data?.items ?? []).length === 0 && (
            <div className="card-body text-center text-gray-500 py-10">
              还没有回测任务，点击右上角「新建回测」开始。
            </div>
          )}
          {(cancel.isError || rerun.isError || rate.isError || del.isError || bulkDel.isError) && (
            <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
              操作失败：{apiError(cancel.error ?? rerun.error ?? rate.error ?? del.error ?? bulkDel.error)}
            </div>
          )}
        </div>
      )}

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
      </>
      )}

      {tab === 'viz' && (
        <div className="space-y-4">
          <div className="card">
            <div className="card-body space-y-4">
              <p className="text-xs text-gray-500">
                合成 M1 粒度随机漫步 OHLCV，支持日线级别宏观形状叠加，M1 最大 130 万根。
              </p>
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <label className="text-xs text-gray-500 block mb-1">起始时间</label>
                  <input
                    type="datetime-local"
                    className="input-dark text-xs px-2 py-1.5 w-48"
                    value={vizStart}
                    onChange={(e) => setVizStart(e.target.value)}
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">结束时间</label>
                  <input
                    type="datetime-local"
                    className="input-dark text-xs px-2 py-1.5 w-48"
                    value={vizEnd}
                    onChange={(e) => setVizEnd(e.target.value)}
                  />
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-xs text-gray-500">粒度固定：</span>
                  <span className="text-xs font-medium text-blue-300">1 分钟</span>
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">起始价</label>
                  <input
                    type="number" step="1"
                    className="input-dark text-xs px-2 py-1.5 w-28"
                    value={vizBasePrice}
                    onChange={(e) => setVizBasePrice(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">波动率</label>
                  <input
                    type="number" step="0.0001"
                    className="input-dark text-xs px-2 py-1.5 w-28"
                    value={vizVol}
                    onChange={(e) => setVizVol(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">行情形状</label>
                  <select
                    className="input-dark text-xs px-2 py-1.5 w-32"
                    value={vizShape}
                    onChange={(e) => setVizShape(e.target.value as TrendShape)}
                  >
                    {Object.entries(SHAPE_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-gray-500 block mb-1">
                    趋势强度 <span className="text-gray-600">({(vizTrendStrength * 100).toFixed(0)}%)</span>
                  </label>
                  <input
                    type="range" min="0.01" max="0.50" step="0.01"
                    className="w-28 accent-blue-500"
                    value={vizTrendStrength}
                    disabled={vizShape === 'random'}
                    onChange={(e) => setVizTrendStrength(Number(e.target.value))}
                  />
                </div>
                <button
                  type="button"
                  className="btn-primary text-sm px-4 py-1.5 disabled:opacity-50"
                  onClick={runGenerate}
                  disabled={vizLoading}
                >
                  {vizLoading ? (
                    <><Loader2 className="w-4 h-4 animate-spin" /> 生成中…</>
                  ) : (
                    <><Download className="w-4 h-4" /> 生成并画图</>
                  )}
                </button>
                {vizData.length > 0 && (
                  <>
                    <button
                      type="button"
                      className="btn-ghost text-sm px-4 py-1.5"
                      onClick={() => { setSaveOpen(true); setSaveName(''); setSaveDesc(''); setSaveResult(null); }}
                    >
                      <Save className="w-4 h-4" /> 保存为数据集
                    </button>
                    <span className="text-xs text-gray-500">
                      {vizData.length} 根 · 首 {vizData[0].open.toFixed(2)} → 尾 {vizData[vizData.length - 1].close.toFixed(2)}
                    </span>
                  </>
                )}
              </div>
              {vizError && (
                <div className="text-xs text-loss bg-loss/10 border border-loss/30 rounded px-3 py-2">
                  {vizError}
                </div>
              )}
            </div>
          </div>

          {/* 图表区 */}
          {vizData.length > 0 ? (
            <CandlestickChart data={vizData} height={480} />
          ) : (
            <div className="card flex flex-col items-center justify-center py-24 text-center">
              <BarChart2 className="w-12 h-12 text-gray-600 mb-4" />
              <h3 className="text-base font-medium text-gray-300">还没有数据</h3>
              <p className="text-sm text-gray-500 mt-1">设置时间范围和周期 → 点击「生成并画图」</p>
            </div>
          )}
        </div>
      )}

      {tab === 'localdata' && <LocalDataManager />}

      {tab === 'portfolios' && (
        <>
          {portfolios.isError && (
            <div className="card border-loss/40 bg-loss/10">
              <div className="card-body text-sm text-loss">{apiError(portfolios.error)}</div>
            </div>
          )}
          {portfolios.isLoading ? (
            <div className="card">
              <div className="card-body text-center text-gray-500 py-10">正在加载组合回测…</div>
            </div>
          ) : (
            <div className="card overflow-hidden">
              <table className="table-dark">
                <thead>
                  <tr>
                    <th>策略</th>
                    <th>品种</th>
                    <th>周期</th>
                    <th>总资金</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {(pfData?.items ?? []).map((p) => (
                    <tr key={p.id}>
                      <td className="text-gray-100 text-sm">
                        {p.strategy_name || p.strategy_id.slice(0, 8)}
                      </td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {p.symbols.map((s) => (
                            <span
                              key={s}
                              className="px-1.5 py-0.5 rounded bg-surface-700 border border-gray-600 text-xs font-mono text-gray-300"
                            >
                              {s}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="font-mono text-gray-400">{p.timeframe}</td>
                      <td className="font-mono text-gray-200">{p.total_deposit.toLocaleString()}</td>
                      <td>
                        <span
                          className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${PF_STATUS_STYLES[p.status]}`}
                        >
                          {PF_STATUS_LABELS[p.status]}
                        </span>
                        {p.status === 'failed' && p.error && (
                          <p className="text-xs text-loss mt-1 max-w-64 truncate" title={p.error}>
                            {p.error}
                          </p>
                        )}
                      </td>
                      <td className="text-xs text-gray-500 whitespace-nowrap">
                        {new Date(p.created_at).toLocaleString('zh-CN')}
                      </td>
                      <td className="text-right whitespace-nowrap">
                        <Link
                          to={`/backtest/portfolio/${p.id}`}
                          className="text-blue-400 hover:text-blue-300 text-sm"
                        >
                          查看报告
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {(pfData?.items ?? []).length === 0 && (
                <div className="card-body text-center text-gray-500 py-10">
                  还没有组合回测，点击右上角「新建组合回测」开始。
                </div>
              )}
            </div>
          )}
          {pfData && pfData.total_pages > 1 && (
            <div className="flex items-center justify-center gap-3">
              <button
                type="button"
                className="btn-secondary px-3 py-1.5"
                disabled={pfPage <= 1}
                onClick={() => setPfPage((pg) => Math.max(1, pg - 1))}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-sm text-gray-400">
                {pfPage} / {pfData.total_pages}
              </span>
              <button
                type="button"
                className="btn-secondary px-3 py-1.5"
                disabled={pfPage >= pfData.total_pages}
                onClick={() => setPfPage((pg) => Math.min(pfData.total_pages, pg + 1))}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </>
      )}

      <BacktestCreateDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <PortfolioCreateDialog open={pfCreateOpen} onClose={() => setPfCreateOpen(false)} />

      {compareOpen && selected.length >= 2 && (
        <CompareDialog taskIds={selected} onClose={() => setCompareOpen(false)} />
      )}

      {/* 保存为合成数据集 dialog */}
      {saveOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => !saveLoading && setSaveOpen(false)}>
          <div className="card w-[420px]" onClick={(e) => e.stopPropagation()}>
            <div className="card-body space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-medium text-white">保存为合成数据集</h3>
                <button type="button" className="text-gray-500 hover:text-gray-300" disabled={saveLoading} onClick={() => setSaveOpen(false)}>
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">名称 *</label>
                <input
                  type="text" maxLength={128}
                  className="input-dark w-full"
                  placeholder="如：XAUUSD-2026H1-随机漫步"
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">描述（可选）</label>
                <textarea
                  rows={2} maxLength={512}
                  className="input-dark w-full resize-none"
                  placeholder="随机漫步 OHLCV，M1 粒度"
                  value={saveDesc}
                  onChange={(e) => setSaveDesc(e.target.value)}
                />
              </div>
              <p className="text-xs text-gray-500">
                将保存 <span className="text-gray-300">{vizData.length}</span> 根 M1 K 线（{vizShape} · 起始价 {vizBasePrice} · 波动率 {vizVol}）
              </p>
              {saveResult && (
                <div className={`text-xs rounded px-3 py-2 ${saveResult.includes('成功') ? 'text-profit bg-profit/10 border border-profit/30' : 'text-loss bg-loss/10 border border-loss/30'}`}>
                  {saveResult}
                </div>
              )}
              <div className="flex justify-end gap-2 pt-1">
                <button type="button" className="btn-ghost" disabled={saveLoading} onClick={() => setSaveOpen(false)}>取消</button>
                <button type="button" className="btn-primary" disabled={saveLoading} onClick={doSave}>
                  {saveLoading ? <><Loader2 className="w-4 h-4 animate-spin" /> 保存中…</> : '保存'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
