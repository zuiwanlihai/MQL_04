import { Link } from 'react-router-dom';
import { Bot, ChevronRight } from 'lucide-react';
import { PAPER_STATUS_LABELS, usePaperRuns } from '@/hooks/usePaper';
import { useStrategies } from '@/hooks/useStrategies';
import type { PaperRunStatus } from '@/types/api';

/** 非终态视为「运行中」：启动中 / 运行中 / 已暂停 / 停止中。 */
const ACTIVE: PaperRunStatus[] = ['pending', 'running', 'paused', 'stopping'];

const STATUS_STYLES: Record<PaperRunStatus, string> = {
  pending: 'bg-surface-700 text-gray-300 border-gray-600',
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  paused: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopping: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopped: 'bg-gray-500/15 text-gray-400 border-gray-500/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const money = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 行情页顶部：已运行的 AI 策略（虚拟下单）速览卡片，空则整体隐藏。 */
export default function RunningStrategies() {
  const list = usePaperRuns({ page: 1, pageSize: 50 });
  const strategies = useStrategies({ page: 1, pageSize: 100 });

  const runs = (list.data?.items ?? []).filter((t) => ACTIVE.includes(t.status));
  if (list.isLoading || runs.length === 0) return null;

  return (
    <div className="card">
      <div className="card-body py-3 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium text-gray-200">
            <Bot className="w-4 h-4 text-blue-400" />
            运行中的 AI 策略
            <span className="text-xs text-gray-500">（{runs.length}）</span>
          </div>
          <Link to="/trading/paper" className="text-xs text-blue-400 hover:text-blue-300 inline-flex items-center gap-0.5">
            虚拟下单中心 <ChevronRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {runs.map((t) => {
            const name = strategies.data?.items.find((s) => s.id === t.strategy_id)?.name
              ?? t.strategy_id.slice(0, 8);
            const pnl = t.unrealized_pnl;
            return (
              <Link
                key={t.id}
                to={`/trading/paper/${t.id}`}
                className="block rounded-lg border border-gray-700/50 bg-surface-800/60 hover:border-blue-500/40 hover:bg-surface-800 transition-colors p-3 space-y-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm text-gray-100 truncate" title={name}>{name}</span>
                  <span className={`px-1.5 py-0.5 rounded border text-xs whitespace-nowrap ${STATUS_STYLES[t.status]}`}>
                    {PAPER_STATUS_LABELS[t.status]}
                  </span>
                </div>
                <div className="text-xs text-gray-500 font-mono">
                  {t.symbol} · {t.timeframe}
                  {t.strategy_version != null ? ` · v${t.strategy_version}` : ''}
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-400">净值 <span className="font-mono text-gray-100">{money(t.equity)}</span></span>
                  <span className={`font-mono ${pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {pnl >= 0 ? '+' : ''}{money(pnl)}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
