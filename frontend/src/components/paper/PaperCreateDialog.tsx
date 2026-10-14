import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useStrategies, useVersions } from '@/hooks/useStrategies';
import { useAccounts, useActiveAccount } from '@/hooks/useAccounts';
import { useSymbols } from '@/hooks/useMarket';
import { useWatchlist } from '@/lib/watchlist';
import { PAPER_MODE_ASSUMPTION_TEXT, PAPER_MODE_LABELS, useCreatePaperRun } from '@/hooks/usePaper';
import { useSlippageCalibration } from '@/hooks/useBacktests';
import StrategyParamsPanel from '@/components/strategies/StrategyParamsPanel';
import { apiError } from '@/lib/api';
import { resolveSchema, seedValues } from '@/lib/strategyParams';
import type { PaperExecutionMode, SpreadMode } from '@/types/api';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

const MODE_OPTIONS: PaperExecutionMode[] = ['standard', 'optimistic'];

interface Props {
  open: boolean;
  onClose: () => void;
  presetStrategyId?: string;
}

export default function PaperCreateDialog({ open, onClose, presetStrategyId }: Props) {
  const navigate = useNavigate();
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const accounts = useAccounts();
  const activeQ = useActiveAccount();
  const create = useCreatePaperRun();

  const [strategyId, setStrategyId] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [symbol, setSymbol] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('H1');
  const [deposit, setDeposit] = useState('10000');
  const [leverage, setLeverage] = useState('100');
  const [mode, setMode] = useState<PaperExecutionMode>('standard');
  const [spreadMode, setSpreadMode] = useState<SpreadMode>('current');
  const [spreadPoints, setSpreadPoints] = useState('20');
  const [commission, setCommission] = useState('0');
  const [slippage, setSlippage] = useState('');
  const [warmup, setWarmup] = useState('100');
  const [paramsText, setParamsText] = useState('{}');
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [ack, setAck] = useState(false);
  const [paramsError, setParamsError] = useState<string | null>(null);

  const versions = useVersions(strategyId || undefined);
  // 品种下拉: 自选品种优先, 无自选时回退全量
  const { favSet } = useWatchlist();
  const allSymbols = useSymbols('', '').data?.items ?? [];
  const favSymbols = allSymbols.filter((s) => favSet.has(s.name));
  const symbolOptions = favSymbols.length > 0 ? favSymbols : allSymbols;
  // M4 真实滑点校准：按品种预填滑点默认值（与回测表单同一数据源）
  const calibration = useSlippageCalibration(
    symbol.trim().toUpperCase() || undefined,
    open,
  );
  const slippageTouched = useRef(false);
  const depositTouched = useRef(false);

  // 滑点校准结果到达后预填（用户手改过则不覆盖）
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
    depositTouched.current = false;
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

  // 当前登录的 MT5 账户（互斥语义：登录即路由）— 自动作为虚拟下单的绑定账户
  const activeAccount = useMemo(() => {
    const online = (activeQ.data ?? []).find((i) => i.account !== null);
    if (!online?.account) return null;
    return (
      accounts.data?.items.find(
        (a) => a.login === online.account!.login && a.server === online.account!.server,
      ) ?? null
    );
  }, [activeQ.data, accounts.data]);

  // 初始资金默认取当前登录账户余额（最近同步值），可手动修改
  useEffect(() => {
    if (!open || depositTouched.current) return;
    const bal = activeAccount?.balance;
    if (typeof bal === 'number' && bal > 0) setDeposit(String(bal));
  }, [open, activeAccount]);

  useEffect(() => {
    if (selectedStrategy?.symbol_ids[0]) setSymbol(selectedStrategy.symbol_ids[0]);
    if (selectedStrategy?.timeframe) setTimeframe(selectedStrategy.timeframe);
  }, [selectedStrategy]);

  if (!open) return null;

  const assumptionText = PAPER_MODE_ASSUMPTION_TEXT[mode];

  const submit = () => {
    if (!strategyId || !activeAccount || !ack) return;
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
    if (mode === 'optimistic') {
      if (!window.confirm('乐观档不计点差与滑点，结果偏乐观、不具备实盘参考意义。确认仍要启动？')) {
        return;
      }
    }
    create.mutate(
      {
        strategy_id: strategyId,
        account_id: activeAccount.id,
        version,
        symbol: symbol.trim().toUpperCase(),
        timeframe,
        initial_deposit: Number(deposit),
        leverage: Number(leverage),
        execution_mode: mode,
        spread_mode: spreadMode,
        spread_points: spreadMode === 'fixed' ? Number(spreadPoints) : null,
        commission_per_lot: Number(commission) || 0,
        slippage_points: slippage.trim() === '' ? null : Number(slippage),
        warmup_bars: Number(warmup) || 0,
        parameters,
        assumption_ack: true,
      },
      {
        onSuccess: (run) => {
          onClose();
          navigate(`/trading/paper/${run.id}`);
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[92vh] overflow-y-auto">
        <div className="card-header sticky top-0 bg-surface-800 z-10">
          <h3 className="text-sm font-medium text-gray-200">新建虚拟下单</h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body space-y-4">
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
                <option value="">当前工作副本（启动时冻结）</option>
                {(versions.data?.items ?? []).map((v) => (
                  <option key={v.id} value={v.version}>
                    v{v.version}
                    {v.compile_errors ? '（校验失败）' : ''} — {v.changelog || '无说明'}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="space-y-1">
            <span className="text-xs text-gray-400">当前登录 MT5 账户</span>
            {activeQ.isLoading || accounts.isLoading ? (
              <p className="text-sm text-gray-500 flex items-center gap-2">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> 正在获取当前登录账户…
              </p>
            ) : activeAccount ? (
              <p className="text-sm text-gray-200 font-mono">
                {activeAccount.login}@{activeAccount.server}
                <span
                  className={`ml-2 text-xs px-1.5 py-0.5 rounded font-sans ${
                    activeAccount.account_type === 'real'
                      ? 'bg-loss/15 text-loss'
                      : 'bg-profit/15 text-profit'
                  }`}
                >
                  {activeAccount.account_type === 'real' ? '实盘' : '模拟'}
                </span>
              </p>
            ) : (
              <p className="text-sm text-yellow-400">
                未检测到已登录的 MT5 账户 — 请退出登录，在登录页使用 MT5 账号登录后再创建。
              </p>
            )}
            <p className="text-xs text-gray-500">
              虚拟下单自动使用当前登录账户所在的网关节点（服务器）取行情并登录。
            </p>
          </div>

          <div className="grid grid-cols-4 gap-3">
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
              <span className="text-xs text-gray-400">
                初始资金{activeAccount ? '（默认=账户余额）' : ''}
              </span>
              <input
                className="input-dark mt-1"
                value={deposit}
                onChange={(e) => {
                  depositTouched.current = true;
                  setDeposit(e.target.value);
                }}
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">杠杆（1-1000）</span>
              <input className="input-dark mt-1" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
            </label>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">预热 K 线数（0-1000）</span>
              <input className="input-dark mt-1" value={warmup} onChange={(e) => setWarmup(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">佣金 / 手</span>
              <input className="input-dark mt-1" value={commission} onChange={(e) => setCommission(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">滑点（点，留空=档位默认）</span>
              <input
                className="input-dark mt-1"
                value={slippage}
                placeholder="标准1 / 乐观0"
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
            <div className="mt-2 grid grid-cols-2 gap-2">
              {MODE_OPTIONS.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    setMode(m);
                    setAck(false);
                  }}
                  className={`text-left px-3 py-2 rounded border text-sm transition-colors ${
                    mode === m
                      ? 'border-blue-500/60 bg-blue-500/10 text-blue-300'
                      : 'border-gray-700 bg-surface-900 text-gray-400 hover:border-gray-600'
                  }`}
                >
                  {PAPER_MODE_LABELS[m]}档
                  <p className="text-xs text-gray-500 mt-0.5">
                    {m === 'standard' ? '当根收盘成交 · 含成本' : '当根收盘成交 · 零成本'}
                  </p>
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-1.5">
              虚拟下单按已收盘 K 线增量撮合，保守档（次根开盘成交）不适用于实时模式，故仅开放标准 / 乐观两档。
            </p>
          </div>

          <div className="rounded border border-yellow-500/40 bg-yellow-500/5 p-3 space-y-2">
            <p className="text-xs font-medium text-yellow-300 flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" /> 成交价构成假设（启动前必须确认）
            </p>
            <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-line">{assumptionText}</p>
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
              disabled={!strategyId || !activeAccount || !ack || create.isPending}
              onClick={submit}
            >
              {create.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              启动虚拟下单
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
