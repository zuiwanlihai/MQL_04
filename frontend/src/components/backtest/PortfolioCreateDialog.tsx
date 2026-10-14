import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Loader2, Plus, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useStrategies } from '@/hooks/useStrategies';
import { MODE_ASSUMPTION_TEXT, MODE_LABELS, useCreatePortfolio } from '@/hooks/useBacktests';
import { apiError } from '@/lib/api';
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
}

/** 新建多品种组合回测：一个策略 × 2~8 个品种，资金均分（等权）。 */
export default function PortfolioCreateDialog({ open, onClose }: Props) {
  const navigate = useNavigate();
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const create = useCreatePortfolio();

  const [strategyId, setStrategyId] = useState('');
  const [symbolInput, setSymbolInput] = useState('');
  const [symbols, setSymbols] = useState<string[]>([]);
  const [timeframe, setTimeframe] = useState('H1');
  const [start, setStart] = useState(() => toLocalInput(new Date(Date.now() - 90 * 86400_000)));
  const [end, setEnd] = useState(() => toLocalInput(new Date()));
  const [deposit, setDeposit] = useState('20000');
  const [leverage, setLeverage] = useState('100');
  const [mode, setMode] = useState<ExecutionMode>('conservative');
  const [spreadMode, setSpreadMode] = useState<SpreadMode>('current');
  const [spreadPoints, setSpreadPoints] = useState('20');
  const [commission, setCommission] = useState('0');
  const [slippage, setSlippage] = useState('');
  const [warmup, setWarmup] = useState('100');
  const [ack, setAck] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setAck(false);
    setLocalError(null);
    setSymbols([]);
    setSymbolInput('');
  }, [open]);

  const selectedStrategy = useMemo(
    () => strategies.data?.items.find((s) => s.id === strategyId),
    [strategies.data, strategyId],
  );

  useEffect(() => {
    if (selectedStrategy?.timeframe) setTimeframe(selectedStrategy.timeframe);
  }, [selectedStrategy]);

  if (!open) return null;

  const addSymbol = () => {
    const s = symbolInput.trim().toUpperCase();
    setLocalError(null);
    if (!s) return;
    if (symbols.includes(s)) {
      setLocalError(`品种 ${s} 已添加`);
      return;
    }
    if (symbols.length >= 8) {
      setLocalError('最多 8 个品种');
      return;
    }
    setSymbols([...symbols, s]);
    setSymbolInput('');
  };

  const submit = () => {
    setLocalError(null);
    if (!strategyId) {
      setLocalError('请选择策略');
      return;
    }
    if (symbols.length < 2) {
      setLocalError('组合回测至少需要 2 个品种');
      return;
    }
    if (!ack) return;
    if (mode === 'optimistic') {
      if (!window.confirm('乐观档不计点差与滑点，结果偏乐观、不具备实盘参考意义。确认仍要提交？')) {
        return;
      }
    }
    const startDate = new Date(start);
    const endDate = new Date(end);
    create.mutate(
      {
        strategy_id: strategyId,
        symbols,
        timeframe,
        start_date: startDate.toISOString(),
        end_date: endDate.toISOString(),
        total_deposit: Number(deposit),
        leverage: Number(leverage),
        execution_mode: mode,
        spread_mode: spreadMode,
        spread_points: spreadMode === 'fixed' ? Number(spreadPoints) : null,
        commission_per_lot: Number(commission) || 0,
        slippage_points: slippage.trim() === '' ? null : Number(slippage),
        warmup_bars: Number(warmup) || 0,
        assumption_ack: true,
      },
      {
        onSuccess: (pf) => {
          onClose();
          navigate(`/backtest/portfolio/${pf.id}`);
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[92vh] overflow-y-auto">
        <div className="card-header sticky top-0 bg-surface-800 z-10">
          <h3 className="text-sm font-medium text-gray-200">新建组合回测（多品种）</h3>
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
                onChange={(e) => setStrategyId(e.target.value)}
              >
                <option value="">请选择策略…</option>
                {(strategies.data?.items ?? [])
                  .filter((s) => s.status !== 'archived')
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
              </select>
              <p className="text-xs text-gray-500 mt-1">
                同一策略在每个品种上各跑一个独立子回测，资金均分（等权）。
                策略内 ctx.declare 的品种由各子任务自动注入。
              </p>
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
          </div>

          <div>
            <span className="text-xs text-gray-400">品种列表 *（2~8 个，回车添加）</span>
            <div className="mt-1 flex items-center gap-2">
              <input
                className="input-dark flex-1 font-mono"
                value={symbolInput}
                placeholder="如 EURUSD"
                maxLength={32}
                onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addSymbol();
                  }
                }}
              />
              <button type="button" className="btn-secondary px-3 py-1.5 flex items-center gap-1" onClick={addSymbol}>
                <Plus className="w-4 h-4" /> 添加
              </button>
            </div>
            {symbols.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {symbols.map((s) => (
                  <span
                    key={s}
                    className="px-2 py-1 rounded bg-surface-700 border border-gray-600 text-xs font-mono text-gray-200 flex items-center gap-1"
                  >
                    {s}
                    <button
                      type="button"
                      className="text-gray-500 hover:text-loss"
                      onClick={() => setSymbols(symbols.filter((x) => x !== s))}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            {symbols.length >= 2 && Number(deposit) > 0 && (
              <p className="text-xs text-gray-500 mt-1">
                每品种分配资金：{(Number(deposit) / symbols.length).toFixed(2)}
              </p>
            )}
          </div>

          <div className="grid grid-cols-4 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">总资金 *</span>
              <input className="input-dark mt-1" value={deposit} onChange={(e) => setDeposit(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">杠杆（1-1000）</span>
              <input className="input-dark mt-1" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">起始时间 *</span>
              <input type="datetime-local" className="input-dark mt-1" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">结束时间 *</span>
              <input type="datetime-local" className="input-dark mt-1" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </div>

          <div className="grid grid-cols-4 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">预热 K 线数（0-1000）</span>
              <input className="input-dark mt-1" value={warmup} onChange={(e) => setWarmup(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">佣金 / 手</span>
              <input className="input-dark mt-1" value={commission} onChange={(e) => setCommission(e.target.value)} />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">点差模式</span>
              <select className="input-dark mt-1" value={spreadMode} onChange={(e) => setSpreadMode(e.target.value as SpreadMode)}>
                <option value="current">跟随行情点差</option>
                <option value="fixed">固定点差</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">滑点（点，留空=档位默认）</span>
              <input
                className="input-dark mt-1"
                value={slippage}
                placeholder="保守2 / 标准1 / 乐观0"
                onChange={(e) => setSlippage(e.target.value)}
              />
            </label>
          </div>
          {spreadMode === 'fixed' && (
            <label className="block max-w-64">
              <span className="text-xs text-gray-400">固定点差（点）</span>
              <input className="input-dark mt-1" value={spreadPoints} onChange={(e) => setSpreadPoints(e.target.value)} />
            </label>
          )}

          <div>
            <span className="text-xs text-gray-400">撮合档位 *</span>
            <div className="mt-2 grid grid-cols-3 gap-2">
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

          <div className="rounded border border-yellow-500/40 bg-yellow-500/5 p-3 space-y-2">
            <p className="text-xs font-medium text-yellow-300 flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" /> 成交价构成假设（提交前必须确认）
            </p>
            <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-line">
              {MODE_ASSUMPTION_TEXT[mode]}
            </p>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              <span className="text-gray-200">我已阅读并确认上述成交价构成假设</span>
            </label>
          </div>

          {(localError || create.isError) && (
            <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
              {localError ?? apiError(create.error)}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!strategyId || symbols.length < 2 || !ack || create.isPending}
              onClick={submit}
            >
              {create.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              提交组合回测（{Math.max(symbols.length, 0)} 个子任务）
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
