import { useState } from 'react';
import { BookOpen, ChevronLeft, ChevronRight, Pause, Play, Plus, Square, Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import PaperCreateDialog from '@/components/paper/PaperCreateDialog';
import StrategyLogicDialog from '@/components/strategy/StrategyLogicDialog';
import {
  PAPER_MODE_LABELS,
  PAPER_STATUS_LABELS,
  usePaperRuns,
  usePausePaperRun,
  useResumePaperRun,
  useStopPaperRun,
  useDeletePaperRun,
  useCreatePaperRun,
} from '@/hooks/usePaper';
import { useStrategies } from '@/hooks/useStrategies';
import { useAccounts } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';
import type { PaperRun, PaperRunStatus } from '@/types/api';

const STATUS_OPTIONS: { value: '' | PaperRunStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '启动中' },
  { value: 'running', label: '运行中' },
  { value: 'paused', label: '已暂停' },
  { value: 'stopping', label: '停止中' },
  { value: 'stopped', label: '已停止' },
  { value: 'failed', label: '失败' },
];

const STATUS_STYLES: Record<PaperRunStatus, string> = {
  pending: 'bg-surface-700 text-gray-300 border-gray-600',
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  paused: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopping: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopped: 'bg-gray-500/15 text-gray-400 border-gray-500/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const ACTIVE: PaperRunStatus[] = ['pending', 'running', 'stopping'];

const money = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function PaperListPage({ compact = false }: { compact?: boolean }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<'' | PaperRunStatus>('');
  const [strategyId, setStrategyId] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [logic, setLogic] = useState<{ id: string; name: string } | null>(null);

  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const accounts = useAccounts();
  const list = usePaperRuns({ page, pageSize: 20, status, strategyId });
  const pause = usePausePaperRun();
  const resume = useResumePaperRun();
  const stop = useStopPaperRun();
  const del = useDeletePaperRun();
  const create = useCreatePaperRun();
  const data = list.data;

  const anyPending = pause.isPending || resume.isPending || stop.isPending || del.isPending || create.isPending;

  // 已停止/失败的虚拟下单「开始」= 以相同配置重建并启动（原记录保留）
  const restartRun = async (t: PaperRun) => {
    try {
      await create.mutateAsync({
        strategy_id: t.strategy_id,
        // gateway 模式绑定原账户；synthetic 模式复用原数据集
        ...(t.data_source === 'synthetic'
          ? { data_source: 'synthetic' as const, synthetic_dataset_id: t.synthetic_dataset_id }
          : { account_id: t.account_id }),
        version: t.strategy_version,
        symbol: t.symbol,
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
      });
    } catch {
      // 失败原因在列表底部统一展示
    }
  };

  // 重启可行性：gateway 任务需已绑定账户；synthetic 任务需数据集引用仍在（删除后置 NULL）
  const canRestart = (t: PaperRun) =>
    t.data_source === 'synthetic' ? t.synthetic_dataset_id != null : t.account_id != null;
  const restartBlockReason = (t: PaperRun) =>
    t.data_source === 'synthetic'
      ? '原合成数据集已被删除，无法重启'
      : '旧任务未绑定账户，无法重启';

  return (
    <div className="space-y-4">
      {!compact && (
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-semibold text-white">虚拟下单中心</h1>
            <p className="text-sm text-gray-500 mt-1">
              长驻沙箱进程 · 按已收盘 K 线增量撮合 · 暂停 / 开始 / 停止 / 删除
            </p>
          </div>
          <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>
            新建虚拟下单
          </button>
        </div>
      )}
      {compact && (
        <div className="flex items-center justify-end gap-4">
          <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>
            <Plus className="w-4 h-4" /> 新建虚拟下单
          </button>
        </div>
      )}

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
            setStatus(e.target.value as '' | PaperRunStatus);
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
            共 {data.total} 个虚拟下单 · 第 {data.page}/{data.total_pages} 页
          </span>
        )}
      </div>

      {list.isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(list.error)}</div>
        </div>
      )}

      {list.isLoading ? (
        <div className="card">
          <div className="card-body text-center text-gray-500 py-10">正在加载虚拟下单任务…</div>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <table className="table-dark">
            <thead>
              <tr>
                <th>策略 / 版本</th>
                <th>绑定账户</th>
                <th>品种 / 周期</th>
                <th>档位</th>
                <th>状态</th>
                <th className="text-right">净值 / 余额</th>
                <th className="text-right">浮动盈亏</th>
                <th>最后 K 线</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((t) => {
                const sname = strategies.data?.items.find((s) => s.id === t.strategy_id)?.name;
                const acct = accounts.data?.items.find((a) => a.id === t.account_id);
                const active = ACTIVE.includes(t.status);
                return (
                  <tr key={t.id}>
                    <td>
                      <Link to={`/trading/paper/${t.id}`} className="text-gray-100 text-sm hover:text-blue-300">
                        {sname ?? t.strategy_id.slice(0, 8)}
                      </Link>
                      <button
                        type="button"
                        onClick={() => setLogic({ id: t.strategy_id, name: sname ?? t.strategy_id.slice(0, 8) })}
                        className="ml-1.5 text-gray-500 hover:text-blue-300 align-middle"
                        title="查看 AI 核心逻辑"
                      >
                        <BookOpen className="w-3.5 h-3.5 inline" />
                      </button>
                      <p className="text-xs text-gray-500 font-mono">
                        {t.strategy_version == null ? '工作副本' : `v${t.strategy_version}`}
                      </p>
                    </td>
                    <td className="text-gray-400 text-xs font-mono whitespace-nowrap">
                      {t.data_source === 'synthetic' ? (
                        <span title="合成数据集回放，不占用 MT5 账户">
                          合成数据集{t.synthetic_dataset_id != null ? ` #${t.synthetic_dataset_id}` : ''}
                        </span>
                      ) : acct ? (
                        `${acct.login}@${acct.server}`
                      ) : t.account_id ? (
                        '已解绑账户'
                      ) : (
                        '默认网关（旧任务）'
                      )}
                    </td>
                    <td className="text-gray-400">
                      <span className="font-mono">{t.symbol}</span>
                      <span className="text-gray-600 mx-1">/</span>
                      <span className="font-mono">{t.timeframe}</span>
                    </td>
                    <td>
                      <span
                        className={`px-2 py-0.5 rounded border text-xs ${
                          t.execution_mode === 'optimistic'
                            ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40'
                            : 'bg-surface-700 text-gray-300 border-gray-600'
                        }`}
                      >
                        {PAPER_MODE_LABELS[t.execution_mode]}档
                      </span>
                    </td>
                    <td>
                      <span
                        className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${STATUS_STYLES[t.status]}`}
                      >
                        {PAPER_STATUS_LABELS[t.status]}
                      </span>
                      {t.status === 'failed' && t.error_message && (
                        <p className="text-xs text-loss mt-1 max-w-64 truncate" title={t.error_message}>
                          {t.error_code ? `[${t.error_code}] ` : ''}
                          {t.error_message}
                        </p>
                      )}
                    </td>
                    <td className="text-right font-mono text-sm whitespace-nowrap">
                      <span className="text-gray-100">{money(t.equity)}</span>
                      <span className="text-gray-600 mx-1">/</span>
                      <span className="text-gray-400">{money(t.balance)}</span>
                    </td>
                    <td
                      className={`text-right font-mono text-sm ${
                        t.unrealized_pnl >= 0 ? 'text-profit' : 'text-loss'
                      }`}
                    >
                      {money(t.unrealized_pnl)}
                    </td>
                    <td className="text-xs text-gray-500 whitespace-nowrap">
                      {t.last_bar_time ? new Date(t.last_bar_time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—'}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      <Link
                        to={`/trading/paper/${t.id}`}
                        className="text-blue-400 hover:text-blue-300 text-sm"
                      >
                        详情
                      </Link>
                      {t.status === 'running' && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={anyPending}
                            className="text-yellow-400 hover:text-yellow-300 text-sm disabled:opacity-50"
                            onClick={() => pause.mutate(t.id)}
                            title="暂停（停止喂入新 K 线，进程保留）"
                          >
                            <Pause className="w-3.5 h-3.5 inline" /> 暂停
                          </button>
                        </>
                      )}
                      {t.status === 'paused' && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={anyPending}
                            className="text-profit hover:text-profit-light text-sm disabled:opacity-50"
                            onClick={() => resume.mutate(t.id)}
                            title="开始（恢复喂入 K 线）"
                          >
                            <Play className="w-3.5 h-3.5 inline" /> 开始
                          </button>
                        </>
                      )}
                      {(t.status === 'running' || t.status === 'paused') && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={anyPending}
                            className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                            onClick={() => {
                              if (window.confirm('停止该虚拟下单？停止时将以最后收盘价平掉全部持仓。')) {
                                stop.mutate({ id: t.id, closePositions: true });
                              }
                            }}
                            title="停止并平仓"
                          >
                            <Square className="w-3.5 h-3.5 inline" /> 停止
                          </button>
                        </>
                      )}
                      {(t.status === 'stopped' || t.status === 'failed') && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={anyPending || !canRestart(t)}
                            className="text-profit hover:text-profit-light text-sm disabled:opacity-50"
                            onClick={() => {
                              if (window.confirm(
                                `以相同配置重新启动该虚拟下单（${t.symbol} ${t.timeframe}）？净值将重置为初始资金。`,
                              )) {
                                restartRun(t);
                              }
                            }}
                            title={canRestart(t) ? '以相同配置重建并启动' : restartBlockReason(t)}
                          >
                            <Play className="w-3.5 h-3.5 inline" /> 开始
                          </button>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={anyPending}
                            className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                            onClick={() => {
                              if (window.confirm('删除该虚拟下单记录？成交与日志将一并删除，不可恢复。')) {
                                del.mutate(t.id);
                              }
                            }}
                            title="删除记录（不可恢复）"
                          >
                            <Trash2 className="w-3.5 h-3.5 inline" /> 删除
                          </button>
                        </>
                      )}
                      {active && <span className="text-xs text-gray-500 ml-2">处理中…</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {(data?.items ?? []).length === 0 && (
            <div className="card-body text-center text-gray-500 py-10">
              还没有虚拟下单任务，点击右上角「新建虚拟下单」开始。
            </div>
          )}
          {(pause.isError || resume.isError || stop.isError || del.isError || create.isError) && (
            <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
              操作失败：{apiError(pause.error ?? resume.error ?? stop.error ?? del.error ?? create.error)}
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

      <PaperCreateDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <StrategyLogicDialog
        open={logic != null}
        strategyId={logic?.id}
        name={logic?.name ?? ''}
        onClose={() => setLogic(null)}
      />
    </div>
  );
}
