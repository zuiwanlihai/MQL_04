import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useStrategies, useVersions } from '@/hooks/useStrategies';
import {
  MODE_ASSUMPTION_TEXT,
  MODE_LABELS,
  useCreateBacktest,
  useSlippageCalibration,
} from '@/hooks/useBacktests';
import StrategyParamsPanel from '@/components/strategies/StrategyParamsPanel';
import { loadKlines, type KlineBar } from '@/lib/klineCache';
import { useSymbols } from '@/hooks/useMarket';
import { useWatchlist } from '@/lib/watchlist';
import { api, apiError } from '@/lib/api';
import { resolveSchema, seedValues } from '@/lib/strategyParams';
import type { ExecutionMode, SpreadMode } from '@/types/api';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

const MODE_OPTIONS: ExecutionMode[] = ['conservative', 'standard', 'optimistic'];

function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** 从编辑器 Tab 打开时预选策略 */
  presetStrategyId?: string;
}

export default function BacktestCreateDialog({ open, onClose, presetStrategyId }: Props) {
  const navigate = useNavigate();
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const create = useCreateBacktest();

  const [strategyId, setStrategyId] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [symbol, setSymbol] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('H1');
  const [dataSource, setDataSource] = useState<'server' | 'local' | 'synthetic'>('server');
  const [syntheticDatasetId, setSyntheticDatasetId] = useState<string>('');
  // 合成数据集列表
  const [syntheticDatasets, setSyntheticDatasets] = useState<
    { id: number; name: string; symbol: string; bar_count: number; first_time: number; last_time: number }[]
  >([]);
  const [syntheticDsLoading, setSyntheticDsLoading] = useState(false);
  useEffect(() => {
    if (!open || dataSource !== 'synthetic') return;
    let alive = true;
    setSyntheticDsLoading(true);
    api
      .get<typeof syntheticDatasets>('/synthetic-datasets?limit=100')
      .then((r) => { if (alive) setSyntheticDatasets(r.data); })
      .catch(() => { if (alive) setSyntheticDatasets([]); })
      .finally(() => { if (alive) setSyntheticDsLoading(false); });
    return () => { alive = false; };
  }, [open, dataSource]);
  const selectedSynthDs = useMemo(
    () => syntheticDatasets.find((d) => String(d.id) === syntheticDatasetId) ?? null,
    [syntheticDatasets, syntheticDatasetId],
  );
  // 选合成数据集时，自动把 start/end 对齐到数据集的时间边界
  useEffect(() => {
    if (!selectedSynthDs) return;
    setStart(toLocalInput(new Date(selectedSynthDs.first_time * 1000)));
    setEnd(toLocalInput(new Date(selectedSynthDs.last_time * 1000)));
  }, [selectedSynthDs?.id]);
  // 本地缓存K线（下载或浏览自选时落盘到服务器缓存）——异步加载，
  // 供"本地数据"选项展示根数/覆盖区间与提交
  const [cachedBars, setCachedBars] = useState<KlineBar[]>([]);
  useEffect(() => {
    let alive = true;
    setCachedBars([]);
    loadKlines(symbol, timeframe).then((bars) => {
      if (alive) setCachedBars(bars);
    });
    return () => {
      alive = false;
    };
  }, [symbol, timeframe]);
  const localCoverage = useMemo(() => {
    if (cachedBars.length === 0) return null;
    const fmt = (t: number) => new Date(t * 1000).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' });
    return {
      firstTs: cachedBars[0].time * 1000,
      lastTs: cachedBars[cachedBars.length - 1].time * 1000,
      first: fmt(cachedBars[0].time),
      last: fmt(cachedBars[cachedBars.length - 1].time),
      enough: cachedBars.length >= 50,
    };
  }, [cachedBars]);
  const [start, setStart] = useState(() => toLocalInput(new Date(Date.now() - 90 * 86400_000)));
  const [end, setEnd] = useState(() => toLocalInput(new Date()));
  const [deposit, setDeposit] = useState('10000');
  const [leverage, setLeverage] = useState('100');
  const [mode, setMode] = useState<ExecutionMode>('conservative');
  const [spreadMode, setSpreadMode] = useState<SpreadMode>('current');
  const [spreadPoints, setSpreadPoints] = useState('20');
  const [commission, setCommission] = useState('0');
  const [slippage, setSlippage] = useState('');
  const [warmup, setWarmup] = useState('100');
  const [paramsText, setParamsText] = useState('{}');
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [ack, setAck] = useState(false);
  const [sensitivity, setSensitivity] = useState(false);
  const [paramsError, setParamsError] = useState<string | null>(null);

  const versions = useVersions(strategyId || undefined);
  // 品种下拉: 自选品种优先, 无自选时回退全量
  const { favSet } = useWatchlist();
  const allSymbols = useSymbols('', '').data?.items ?? [];
  const favSymbols = allSymbols.filter((s) => favSet.has(s.name));
  const symbolOptions = favSymbols.length > 0 ? favSymbols : allSymbols;
  // M4 真实滑点校准：品种确定后拉取实盘成交滑点统计，预填滑点默认值
  const calibration = useSlippageCalibration(
    symbol.trim().toUpperCase() || undefined,
    open,
  );
  const slippageTouched = useRef(false);

  useEffect(() => {
    const rec = calibration.data?.recommended_points;
    if (rec === null || rec === undefined) return;
    if (slippageTouched.current) return;
    setSlippage(String(rec));
     
  }, [calibration.data]);

  useEffect(() => {
    if (!open) return;
    setAck(false);
    slippageTouched.current = false;
    setDataSource('server');
    setSyntheticDatasetId('');
    if (presetStrategyId) setStrategyId(presetStrategyId);
     
  }, [open, presetStrategyId]);

  const selectedStrategy = useMemo(
    () => strategies.data?.items.find((s) => s.id === strategyId),
    [strategies.data, strategyId],
  );

  const schema = selectedStrategy?.parameters
    ? resolveSchema(Object.keys(selectedStrategy.parameters))
    : null;

  useEffect(() => {
    setParams(schema ? seedValues(schema, selectedStrategy?.parameters) : {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema, strategyId]);

  useEffect(() => {
    if (selectedStrategy?.symbol_ids[0]) setSymbol(selectedStrategy.symbol_ids[0]);
    if (selectedStrategy?.timeframe) setTimeframe(selectedStrategy.timeframe);
  }, [selectedStrategy]);

  if (!open) return null;

  const effectiveMode: ExecutionMode = sensitivity ? 'conservative' : mode;
  const assumptionText = MODE_ASSUMPTION_TEXT[effectiveMode];

  const submit = () => {
    if (!strategyId || !ack) return;
    let parameters: Record<string, unknown>;
    if (schema) {
      parameters = params;
    } else {
      try {
        const parsed = JSON.parse(paramsText || '{}');
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('必须是 JSON 对象');
        }
        parameters = parsed as Record<string, unknown>;
        setParamsError(null);
      } catch (e) {
        setParamsError(`策略参数 JSON 解析失败：${(e as Error).message}`);
        return;
      }
    }
    if (effectiveMode === 'optimistic') {
      if (!window.confirm('乐观档不计点差与滑点，结果偏乐观、不具备实盘参考意义。确认仍要提交？')) {
        return;
      }
    }
    const startDate = new Date(start);
    const endDate = new Date(end);
    if (dataSource === 'local' && localCoverage) {
      const sOk = startDate.getTime() >= localCoverage.firstTs;
      const eOk = endDate.getTime() <= localCoverage.lastTs;
      if (!sOk || !eOk) {
        if (!window.confirm(`本地缓存仅覆盖 ${localCoverage.first} ~ ${localCoverage.last}，所选区间部分无数据，回测将按交集执行。确认继续？`)) {
          return;
        }
      }
    }
    if (dataSource === 'synthetic' && !syntheticDatasetId) return;
    create.mutate(
      {
        strategy_id: strategyId,
        version,
        symbol: (dataSource === 'synthetic' && selectedSynthDs?.symbol)
          ? selectedSynthDs.symbol
          : symbol.trim().toUpperCase(),
        timeframe,
        data_source: dataSource,
        local_bars: dataSource === 'local' ? cachedBars : null,
        synthetic_dataset_id: dataSource === 'synthetic' ? Number(syntheticDatasetId) : null,
        start_date: startDate.toISOString(),
        end_date: endDate.toISOString(),
        initial_deposit: Number(deposit),
        leverage: Number(leverage),
        execution_mode: effectiveMode,
        spread_mode: spreadMode,
        spread_points: spreadMode === 'fixed' ? Number(spreadPoints) : null,
        commission_per_lot: Number(commission) || 0,
        slippage_points: slippage.trim() === '' ? null : Number(slippage),
        warmup_bars: Number(warmup) || 0,
        parameters,
        assumption_ack: true,
        sensitivity,
      },
      {
        onSuccess: (tasks) => {
          onClose();
          if (tasks.length === 1) {
            navigate(`/backtest/${tasks[0].id}/report`);
          } else {
            navigate('/backtest');
          }
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[92vh] overflow-y-auto">
        <div className="card-header sticky top-0 bg-surface-800 z-10">
          <h3 className="text-sm font-medium text-gray-200">新建回测</h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body space-y-4">
          {/* ── Step 1: 数据来源（必须最先选，后续字段联动） ─────────────── */}
          <div className="grid grid-cols-4 gap-3">
            <label className="block col-span-4">
              <span className="text-xs text-gray-400">
                数据来源 <span className="text-red-400">*</span>
                {dataSource === 'synthetic' && syntheticDatasets.length === 0 && (
                  <button
                    type="button"
                    className="text-blue-400 hover:text-blue-300 text-xs ml-2 underline"
                    onClick={() => {
                      // 跳到合成数据集页面（策略回测 → 可视化虚拟数据）
                      // 这里不硬编码路由，用户手动切 Tab 即可；弹窗关闭再打开会刷新列表
                    }}
                  >
                    暂无数据集？去生成 →
                  </button>
                )}
              </span>
              <select
                className="input-dark mt-1"
                value={dataSource}
                onChange={(e) => {
                  setDataSource(e.target.value as 'server' | 'local' | 'synthetic');
                  if (e.target.value !== 'synthetic') setSyntheticDatasetId('');
                }}
              >
                <option value="server">服务器数据（网关 MT5 历史，推荐）</option>
                <option
                  value="local"
                  disabled={!localCoverage?.enough}
                  title={!localCoverage?.enough ? '本地无足够缓存：请先在行情页浏览该品种积累K线缓存' : ''}
                >
                  本地缓存数据{localCoverage ? `（${symbol} ${timeframe} · ${cachedBars.length} 根）` : '（无缓存）'}
                </option>
                <option value="synthetic">合成数据集（已保存的虚拟数据）</option>
              </select>

              {/* ── synthetic: 选数据集，品种/时间区间自动对齐 ── */}
              {dataSource === 'synthetic' && (
                <div className="mt-2 grid grid-cols-4 gap-2">
                  <select
                    className="input-dark col-span-3"
                    value={syntheticDatasetId}
                    disabled={syntheticDsLoading}
                    onChange={(e) => setSyntheticDatasetId(e.target.value)}
                  >
                    <option value="">
                      {syntheticDsLoading
                        ? '加载中…'
                        : syntheticDatasets.length === 0
                          ? '（暂无合成数据集，请先到可视化虚拟数据页生成并保存）'
                          : '请选择数据集…'}
                    </option>
                    {syntheticDatasets.map((d) => (
                      <option key={d.id} value={String(d.id)}>
                        #{d.id} {d.name}（{d.symbol} · {d.bar_count.toLocaleString()} 根 M1）
                      </option>
                    ))}
                  </select>
                  <div className="col-span-1 text-xs text-gray-500 flex items-center">
                    {selectedSynthDs ? (
                      <>
                        锁定品种：
                        <span className="font-mono text-gray-300 ml-1">{selectedSynthDs.symbol}</span>
                      </>
                    ) : '选完自动对齐'}
                  </div>
                </div>
              )}

              {/* ── local: 缓存覆盖提示 ── */}
              {dataSource === 'local' && localCoverage && (
                <span className="block mt-1 text-2xs text-blue-300">
                  缓存覆盖：{localCoverage.first} ~ {localCoverage.last}
                  {localCoverage.enough
                    ? '。建议将回测区间设在覆盖范围内，区间外部分按交集执行。'
                    : '。缓存不足 50 根，无法提交。'}
                </span>
              )}
            </label>
          </div>

          {/* ── Step 2: 策略 + 版本 ────────────────────────────────────────── */}
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">策略 *</span>
              <select
                className="input-dark mt-1"
                value={strategyId}
                onChange={(e) => {
                  setStrategyId(e.target.value);
                  setVersion(null);
                }}
              >
                <option value="">请选择策略…</option>
                {(strategies.data?.items ?? [])
                  .filter((s) => s.status !== 'archived')
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.current_version == null ? '（未冻结）' : `（当前 v${s.current_version}）`}
                    </option>
                  ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">版本</span>
              <select
                className="input-dark mt-1"
                value={version ?? ''}
                onChange={(e) => setVersion(e.target.value === '' ? null : Number(e.target.value))}
                disabled={!strategyId}
              >
                <option value="">当前工作副本（提交时冻结）</option>
                {(versions.data?.items ?? []).map((v) => (
                  <option key={v.id} value={v.version}>
                    v{v.version}
                    {v.compile_errors ? '（校验失败）' : ''} — {v.changelog || '无说明'}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* ── Step 3: 品种 / 周期 / 时间区间（synthetic: 品种+时间锁死） ── */}
          <div className="grid grid-cols-4 gap-3">
            {dataSource === 'synthetic' ? (
              <>
                <label className="block">
                  <span className="text-xs text-gray-400">品种（数据集自带，锁定）</span>
                  <input
                    className="input-dark mt-1 font-mono"
                    value={selectedSynthDs?.symbol ?? '—'}
                    disabled
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">回测周期</span>
                  <select
                    className="input-dark mt-1"
                    value={timeframe}
                    onChange={(e) => setTimeframe(e.target.value)}
                    title="合成数据集原生是 M1，回测时会自动聚合到所选周期"
                  >
                    {TIMEFRAMES.map((tf) => (
                      <option key={tf} value={tf}>
                        {tf}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block col-span-2">
                  <span className="text-xs text-gray-400">虚拟数据时间（不可修改）</span>
                  <div className="mt-1 input-dark py-2 text-xs text-gray-300 font-mono truncate">
                    {selectedSynthDs
                      ? `${new Date(selectedSynthDs.first_time * 1000).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}  ~  ${new Date(selectedSynthDs.last_time * 1000).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })}`
                      : '— 请先选择数据集 —'}
                  </div>
                </label>
              </>
            ) : (
              <>
                <label className="block">
                  <span className="text-xs text-gray-400">品种 *</span>
                  <select
                    className="input-dark mt-1 font-mono"
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                  >
                    {!symbolOptions.some((s) => s.name === symbol) && (
                      <option value={symbol}>{symbol}</option>
                    )}
                    {symbolOptions.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">周期</span>
                  <select className="input-dark mt-1" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {TIMEFRAMES.map((tf) => (
                      <option key={tf} value={tf}>
                        {tf}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">起始时间 *</span>
                  <input
                    type="datetime-local"
                    className="input-dark mt-1"
                    value={start}
                    onChange={(e) => setStart(e.target.value)}
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">结束时间 *</span>
                  <input
                    type="datetime-local"
                    className="input-dark mt-1"
                    value={end}
                    onChange={(e) => setEnd(e.target.value)}
                  />
                </label>
              </>
            )}
          </div>

          {/* ── Step 4: 资金 / 杠杆 / 点差 / 滑点 ────────────────────────── */}
          <div className="grid grid-cols-4 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">初始资金</span>
              <input className="input-dark mt-1" value={deposit} onChange={(e) => setDeposit(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">杠杆（1-1000）</span>
              <input className="input-dark mt-1" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">预热 K 线数（0-1000）</span>
              <input className="input-dark mt-1" value={warmup} onChange={(e) => setWarmup(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">佣金 / 手</span>
              <input className="input-dark mt-1" value={commission} onChange={(e) => setCommission(e.target.value)} />
            </label>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">点差模式</span>
              <select
                className="input-dark mt-1"
                value={spreadMode}
                onChange={(e) => setSpreadMode(e.target.value as SpreadMode)}
              >
                <option value="current">跟随行情点差</option>
                <option value="fixed">固定点差</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">固定点差（点）</span>
              <input
                className="input-dark mt-1"
                value={spreadPoints}
                disabled={spreadMode !== 'fixed'}
                onChange={(e) => setSpreadPoints(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">滑点（点，留空=档位默认）</span>
              <input
                className="input-dark mt-1"
                value={slippage}
                placeholder="保守2 / 标准1 / 乐观0"
                onChange={(e) => {
                  slippageTouched.current = true;
                  setSlippage(e.target.value);
                }}
              />
              {calibration.data && calibration.data.sample_count > 0 && (
                calibration.data.insufficient ? (
                  <p className="text-xs text-gray-500 mt-1">
                    实盘校准样本不足（{calibration.data.sample_count} 笔），暂不自动预填
                  </p>
                ) : (
                  <p className="text-xs text-profit mt-1">
                    已按实盘校准预填 · 样本 {calibration.data.sample_count} 笔 ·
                    p95 {calibration.data.p95_points} 点
                  </p>
                )
              )}
            </label>
          </div>

          <div>
            <span className="text-xs text-gray-400">
              策略参数{schema ? '（结构化）' : '（JSON 对象）'}
            </span>
            {schema ? (
              <div className="mt-2 rounded border border-blue-500/30 bg-surface-900 p-3 max-h-72 overflow-y-auto">
                <StrategyParamsPanel groups={schema} values={params} onChange={setParams} />
              </div>
            ) : (
              <textarea
                className="input-dark mt-1 font-mono text-xs"
                rows={3}
                value={paramsText}
                onChange={(e) => setParamsText(e.target.value)}
                placeholder='{"fast": 10, "slow": 30}'
              />
            )}
            {paramsError && <p className="text-xs text-loss mt-1">{paramsError}</p>}
          </div>

          <div>
            <span className="text-xs text-gray-400">撮合档位 *</span>
            <div className="mt-2 grid grid-cols-3 gap-2">
              {MODE_OPTIONS.map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={sensitivity && m === 'optimistic'}
                  onClick={() => {
                    setMode(m);
                    setAck(false);
                  }}
                  className={`text-left px-3 py-2 rounded border text-sm transition-colors disabled:opacity-40 ${
                    effectiveMode === m
                      ? 'border-blue-500/60 bg-blue-500/10 text-blue-300'
                      : 'border-gray-700 bg-surface-900 text-gray-400 hover:border-gray-600'
                  }`}
                >
                  {MODE_LABELS[m]}档
                  <p className="text-xs text-gray-500 mt-0.5">
                    {m === 'conservative'
                      ? '次根开盘成交 · 含成本'
                      : m === 'standard'
                        ? '当根收盘成交 · 含成本'
                        : '当根收盘成交 · 零成本'}
                  </p>
                </button>
              ))}
            </div>
          </div>

          <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              className="mt-1"
              checked={sensitivity}
              onChange={(e) => {
                setSensitivity(e.target.checked);
                setAck(false);
                if (e.target.checked && mode === 'optimistic') setMode('conservative');
              }}
            />
            <span>
              敏感性对比：同配置成对创建「保守 + 标准」两个任务
              <span className="text-xs text-gray-500 block mt-0.5">
                用于检验策略对执行价格假设的稳健性；年化收益差 &gt; 5 个百分点将告警。
              </span>
            </span>
          </label>

          <div className="rounded border border-yellow-500/40 bg-yellow-500/5 p-3 space-y-2">
            <p className="text-xs font-medium text-yellow-300 flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" /> 成交价构成假设（提交前必须确认）
            </p>
            <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-line">{assumptionText}</p>
            {sensitivity && (
              <p className="text-xs text-gray-400">
                敏感性模式将同时确认保守档与标准档假设：
                <br />
                {MODE_ASSUMPTION_TEXT.standard}
              </p>
            )}
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              <span className="text-gray-200">我已阅读并确认上述成交价构成假设</span>
            </label>
          </div>

          {create.isError && (
            <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
              {apiError(create.error)}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!strategyId || !ack || create.isPending}
              onClick={submit}
            >
              {create.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              提交回测{sensitivity ? '（成对 2 个任务）' : ''}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
