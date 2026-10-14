import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { BookOpen, Bot } from 'lucide-react';
import { PAPER_STATUS_LABELS, useCreatePaperRun, usePaperRuns, usePausePaperRun, useResumePaperRun, useStopPaperRun } from '@/hooks/usePaper';
import { LIVE_STATUS_LABELS, useLiveRuns, usePauseLiveRun, useResumeLiveRun } from '@/hooks/useLive';
import { useStrategies } from '@/hooks/useStrategies';
import { useSymbols } from '@/hooks/useMarket';
import { useWatchlist } from '@/lib/watchlist';
import StrategyLogicDialog from '@/components/strategy/StrategyLogicDialog';
import { apiError } from '@/lib/api';
import type { PaperRun, PaperRunStatus } from '@/types/api';

/** 非终态视为「运行中」。 */
const ACTIVE: PaperRunStatus[] = ['pending', 'running', 'paused', 'stopping'];
const ACTIVE_LIVE = ['pending', 'running', 'paused', 'stopping'];

const DOT: Record<string, string> = {
  pending: 'bg-blue-400',
  running: 'bg-profit animate-pulse',
  paused: 'bg-yellow-400',
  stopping: 'bg-yellow-400',
};

const LABEL_COLOR: Record<string, string> = {
  pending: 'text-gray-300',
  running: 'text-blue-300',
  paused: 'text-yellow-400',
  stopping: 'text-yellow-400',
};

const money = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 由既有任务行构造重建参数(换品种 / 重新启动共用)。 */
function paperCreateBody(t: PaperRun, symbol = t.symbol) {
  return {
    strategy_id: t.strategy_id,
    account_id: t.account_id ?? '',
    version: t.strategy_version,
    symbol,
    timeframe: t.timeframe,
    initial_deposit: t.initial_deposit,
    leverage: t.leverage,
    execution_mode: t.execution_mode,
    spread_mode: t.spread_mode,
    spread_points: t.spread_points,
    commission_per_lot: t.commission_per_lot,
    slippage_points: t.slippage_points,
    warmup_bars: t.warmup_bars,
    parameters: t.parameters,
    assumption_ack: true,
  };
}

/** 顶栏常驻入口: 运行中的 AI 策略(虚拟下单 + 模拟盘交易)。按钮直接显示策略名, 点击展开速览; 无运行中策略时隐藏。 */
export default function TopbarRunningAI() {
  const [open, setOpen] = useState(false);
  const [logic, setLogic] = useState<{ id: string; name: string } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const list = usePaperRuns({ page: 1, pageSize: 50 });
  const liveList = useLiveRuns({ page: 1, pageSize: 50 });
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const symbolsQ = useSymbols('', '');
  const stop = useStopPaperRun();
  const create = useCreatePaperRun();
  const pause = usePausePaperRun();
  const resume = useResumePaperRun();
  const pauseLive = usePauseLiveRun();
  const resumeLive = useResumeLiveRun();

  const paperRuns = (list.data?.items ?? []).filter((t) => ACTIVE.includes(t.status));
  const liveRuns = (liveList.data?.items ?? []).filter(
    (t) => ACTIVE_LIVE.includes(t.status) && t.account_type === 'demo',
  );
  const runs = [
    ...paperRuns.map((t) => ({ kind: 'paper' as const, t })),
    ...liveRuns.map((t) => ({ kind: 'live' as const, t })),
  ];
  // 头部链接跟随内容: 含模拟盘任务 → 模拟盘列表, 否则 → 虚拟下单中心
  const hasLive = runs.some((r) => r.kind === 'live');
  const switching =
    stop.isPending || create.isPending || pause.isPending || resume.isPending ||
    pauseLive.isPending || resumeLive.isPending;

  // 品种下拉: 仅自选品种; 无自选时不可切换
  const { favSet } = useWatchlist();
  const allSymbols = symbolsQ.data?.items ?? [];
  const favSymbols = allSymbols.filter((s) => favSet.has(s.name));

  async function switchSymbol(t: PaperRun, name: string, symbol: string) {
    if (symbol === t.symbol) return;
    if (!window.confirm(
      `将策略「${name}」的交易品种从 ${t.symbol} 切换为 ${symbol}？\n\n` +
      `当前虚拟下单将停止（以最后收盘价平掉持仓），并以新品种重建，净值重置为初始资金。`,
    )) {
      return;
    }
    try {
      await stop.mutateAsync({ id: t.id, closePositions: true });
      await create.mutateAsync(paperCreateBody(t, symbol));
    } catch {
      // 错误信息在下方面板统一展示
    }
  }

  // 点击面板外部关闭
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  if (runs.length === 0) {
    return (
      <>
        {logic != null && (
          <StrategyLogicDialog
            open
            strategyId={logic.id}
            name={logic.name}
            onClose={() => setLogic(null)}
          />
        )}
      </>
    );
  }

  const first = runs[0];
  const firstName = strategies.data?.items.find((s) => s.id === first.t.strategy_id)?.name
    ?? first.t.strategy_id.slice(0, 8);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-sm text-gray-300 hover:bg-surface-800 hover:text-gray-100 transition-colors"
      >
        <Bot className="w-4 h-4 text-blue-400" />
        <span className="max-w-40 truncate">{firstName}</span>
        {runs.length > 1 && <span className="text-gray-500">+{runs.length - 1}</span>}
        <span className="px-1.5 py-0.5 rounded-full bg-blue-500/15 text-blue-300 border border-blue-500/40 text-2xs font-mono">
          {runs.length}
        </span>
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-2 w-96 rounded-lg border border-gray-700/50 bg-surface-800 shadow-lg z-50 overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-700/50 flex items-center justify-between">
            <span className="text-xs font-medium text-gray-300">
              运行中的 AI 策略（{runs.length}）
            </span>
            <Link
              to={hasLive ? '/trading/live-demo' : '/trading/paper'}
              onClick={() => setOpen(false)}
              className="text-xs text-blue-400 hover:text-blue-300"
            >
              {hasLive ? '模拟盘' : '虚拟下单中心'} →
            </Link>
          </div>
          <div className="max-h-80 overflow-y-auto">
            {runs.map(({ kind, t }) => {
              const name = strategies.data?.items.find((s) => s.id === t.strategy_id)?.name
                ?? t.strategy_id.slice(0, 8);
              const pnl = t.unrealized_pnl;
              const detailTo = kind === 'live' ? `/trading/live-demo/${t.id}` : `/trading/paper/${t.id}`;
              const statusLabels = kind === 'live' ? LIVE_STATUS_LABELS : PAPER_STATUS_LABELS;
              return (
                <div key={`${kind}-${t.id}`} className="px-3 py-2.5 border-b border-gray-700/30 last:border-b-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 min-w-0">
                      <Link
                        to={detailTo}
                        onClick={() => setOpen(false)}
                        className="text-sm text-gray-100 truncate hover:text-blue-300"
                        title={name}
                      >
                        {name}
                      </Link>
                      {kind === 'live' && (
                        <span className="text-2xs px-1 py-0.5 rounded bg-profit/15 text-profit shrink-0">模拟盘</span>
                      )}
                      <button
                        type="button"
                        onClick={() => setLogic({ id: t.strategy_id, name })}
                        className="text-gray-500 hover:text-blue-300 shrink-0"
                        title="查看 AI 核心逻辑"
                      >
                        <BookOpen className="w-3.5 h-3.5" />
                      </button>
                    </span>
                    <span className="flex items-center gap-2 whitespace-nowrap">
                      <span className={`text-xs flex items-center gap-1 ${LABEL_COLOR[t.status]}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${DOT[t.status] ?? 'bg-gray-500'}`} />
                        {statusLabels[t.status] ?? t.status}
                      </span>
                      {t.status === 'running' && (
                        <button
                          type="button"
                          disabled={switching}
                          className="text-xs text-yellow-400 hover:text-yellow-300 disabled:opacity-50"
                          onClick={() => (kind === 'live' ? pauseLive.mutate(t.id) : pause.mutate(t.id))}
                          title="暂停"
                        >
                          暂停
                        </button>
                      )}
                      {t.status === 'paused' && (
                        <button
                          type="button"
                          disabled={switching}
                          className="text-xs text-profit hover:text-profit-light disabled:opacity-50"
                          onClick={() => (kind === 'live' ? resumeLive.mutate(t.id) : resume.mutate(t.id))}
                          title="开始"
                        >
                          开始
                        </button>
                      )}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2 mt-1.5">
                    <span className="text-gray-500">
                      {kind === 'live' ? (
                        <>模拟盘 · <span className="font-mono text-gray-300">{t.symbol}</span></>
                      ) : (
                        <>净值 <span className="font-mono text-gray-100">{money(t.equity)}</span></>
                      )}
                    </span>
                    <span className={`font-mono text-xs ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {pnl >= 0 ? '+' : ''}{money(pnl)}
                    </span>
                    {kind === 'paper' && (
                      <label className="flex items-center gap-1.5 text-xs text-gray-500">
                        交易品种
                        <select
                          className="input-dark w-28 py-1 text-xs"
                          value={t.symbol}
                          disabled={switching || t.account_id == null || favSymbols.length === 0}
                          title={
                            t.account_id == null
                              ? '旧任务未绑定账户，无法切换'
                              : favSymbols.length === 0
                                ? '请先在行情页添加自选品种'
                                : '仅在自选品种间切换'
                          }
                          onChange={(e) => switchSymbol(t as PaperRun, name, e.target.value)}
                        >
                          {!favSymbols.some((s) => s.name === t.symbol) && (
                            <option value={t.symbol}>{t.symbol}</option>
                          )}
                          {favSymbols.map((s) => (
                            <option key={s.name} value={s.name}>{s.name}</option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {(stop.isError || create.isError || pauseLive.isError || resumeLive.isError) && (
            <div className="px-3 py-2 text-xs text-loss bg-loss/10 border-t border-loss/30">
              操作失败：{apiError(stop.error ?? create.error ?? pauseLive.error ?? resumeLive.error)}
            </div>
          )}
        </div>
      )}

      {logic != null && (
        <StrategyLogicDialog
          open
          strategyId={logic.id}
          name={logic.name}
          onClose={() => setLogic(null)}
        />
      )}
    </div>
  );
}
