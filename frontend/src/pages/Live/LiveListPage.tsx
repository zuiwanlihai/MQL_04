import { useState } from 'react';
import { ChevronLeft, ChevronRight, Pause, Play, ShieldX, Square, Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import LiveDeployWizard from '@/components/live/LiveDeployWizard';
import { LIVE_STATUS_LABELS, useDeleteLiveRun, useKillSwitch, useLiveRuns, usePauseLiveRun, useResumeLiveRun, useStopLiveRun } from '@/hooks/useLive';
import { useStrategies } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import type { LiveRunStatus } from '@/types/api';

const STATUS_OPTIONS: { value: '' | LiveRunStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '启动中' },
  { value: 'running', label: '运行中' },
  { value: 'paused', label: '已暂停' },
  { value: 'stopping', label: '停止中' },
  { value: 'stopped', label: '已停止' },
  { value: 'failed', label: '失败' },
];

const STATUS_STYLES: Record<LiveRunStatus, string> = {
  pending: 'bg-surface-700 text-gray-300 border-gray-600',
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  paused: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopping: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopped: 'bg-gray-500/15 text-gray-400 border-gray-500/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const money = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

interface Props {
  /** demo=模拟盘交易（免准入闸，订单发往 MT5 模拟服务器）；real=实盘 */
  scope?: 'demo' | 'real';
}

export default function LiveListPage({ scope = 'real' }: Props) {
  const isDemo = scope === 'demo';
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<'' | LiveRunStatus>('');
  const [deployOpen, setDeployOpen] = useState(false);
  const [killArmed, setKillArmed] = useState(false);

  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const list = useLiveRuns({ page, pageSize: 20, status, accountType: scope });
  const pause = usePauseLiveRun();
  const resume = useResumeLiveRun();
  const stop = useStopLiveRun();
  const kill = useKillSwitch();
  const del = useDeleteLiveRun();
  const data = list.data;
  const anyPending = pause.isPending || resume.isPending || stop.isPending || kill.isPending || del.isPending;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            {isDemo ? '模拟盘交易' : '实盘交易'}
            <span
              className={`px-2 py-0.5 rounded border text-xs ${
                isDemo
                  ? 'bg-profit/15 text-profit border-profit/40'
                  : 'bg-loss/15 text-loss border-loss/40'
              }`}
            >
              {isDemo ? '模拟账户 · 零资金风险' : '真实资金'}
            </span>
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {isDemo
              ? '冻结版本 · 订单真实发往 MT5 模拟服务器 · 事前风控 + 对账（免准入闸与观察期）'
              : '经准入四闸放行的冻结版本 · 网关真实下单 · 事前风控 + 对账 + 自动降级'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {killArmed ? (
            <>
              <button
                type="button"
                className="btn-danger animate-pulse"
                disabled={kill.isPending}
                onClick={() =>
                  kill.mutate(undefined, {
                    onSuccess: () => setKillArmed(false),
                  })
                }
              >
                <ShieldX className="w-4 h-4" /> 再次点击确认熔断
              </button>
              <button type="button" className="btn-ghost" onClick={() => setKillArmed(false)}>
                取消
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn-danger"
              title="Kill Switch：撤销全部挂单 → 平掉全部持仓 → 停止全部策略"
              onClick={() => setKillArmed(true)}
            >
              <ShieldX className="w-4 h-4" /> 一键熔断
            </button>
          )}
          <button type="button" className="btn-primary" onClick={() => setDeployOpen(true)}>
            {isDemo ? '部署模拟盘' : '部署实盘'}
          </button>
        </div>
      </div>

      {kill.isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">熔断执行失败：{apiError(kill.error)}</div>
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap">
        <select
          className="input-dark w-32"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as '' | LiveRunStatus);
            setPage(1);
          }}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        {data && (
          <span className="text-xs text-gray-500">
            共 {data.total} 个{isDemo ? '模拟盘交易' : '实盘'} · 第 {data.page}/{data.total_pages} 页
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
      <div className="card-body text-center text-gray-500 py-10">
        正在加载{isDemo ? '模拟盘交易' : '实盘'}任务…
      </div>
    </div>
  ) : (
        <div className="card overflow-hidden">
          <table className="table-dark">
            <thead>
              <tr>
                <th>策略 / 版本</th>
                <th>品种 / 周期</th>
                <th>状态</th>
                <th className="text-right">净值 / 余额</th>
                <th className="text-right">浮动盈亏</th>
                <th>风控标记</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((t) => {
                const sname = strategies.data?.items.find((s) => s.id === t.strategy_id)?.name;
                return (
                  <tr key={t.id}>
                    <td>
                      <Link to={`/trading/live/${t.id}`} className="text-gray-100 text-sm hover:text-blue-300">
                        {sname ?? t.strategy_id.slice(0, 8)}
                      </Link>
                      <p className="text-xs text-gray-500 font-mono">v{t.strategy_version ?? '—'}</p>
                    </td>
                    <td className="text-gray-400">
                      <span className="font-mono">{t.symbol}</span>
                      <span className="text-gray-600 mx-1">/</span>
                      <span className="font-mono">{t.timeframe}</span>
                    </td>
                    <td>
                      <span className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${STATUS_STYLES[t.status]}`}>
                        {LIVE_STATUS_LABELS[t.status]}
                      </span>
                      {t.status === 'failed' && t.error_message && (
                        <p className="text-xs text-loss mt-1 max-w-64 truncate" title={t.error_message}>
                          {t.error_code ? `[${t.error_code}] ` : ''}{t.error_message}
                        </p>
                      )}
                    </td>
                    <td className="text-right font-mono text-sm whitespace-nowrap">
                      <span className="text-gray-100">{money(t.equity)}</span>
                      <span className="text-gray-600 mx-1">/</span>
                      <span className="text-gray-400">{money(t.balance)}</span>
                    </td>
                    <td className={`text-right font-mono text-sm ${t.unrealized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {money(t.unrealized_pnl)}
                    </td>
                    <td className="text-xs space-y-0.5">
                      {t.demoted && <p className="text-loss">已降级（需复核）</p>}
                      {t.no_new_orders && <p className="text-yellow-400">当日停止开仓</p>}
                      {t.reconcile_pending && <p className="text-yellow-400">待对账</p>}
                      {t.observation_ends_at && !t.demoted && (
                        <p className="text-gray-500">观察期至 {new Date(t.observation_ends_at).toLocaleDateString('zh-CN')}</p>
                      )}
                      {!t.demoted && !t.no_new_orders && !t.reconcile_pending && !t.observation_ends_at && (
                        <span className="text-gray-700">—</span>
                      )}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      <Link to={`/trading/live/${t.id}`} className="text-blue-400 hover:text-blue-300 text-sm">
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
                            title="暂停（不再开新仓，进程保留）"
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
                            disabled={anyPending || t.demoted}
                            className="text-profit hover:text-profit-light text-sm disabled:opacity-50"
                            onClick={() => resume.mutate(t.id)}
                            title="开始"
                          >
                            <Play className="w-3.5 h-3.5 inline" /> 开始
                          </button>
                        </>
                      )}
                      {(t.status === 'stopped' || t.status === 'failed') && (
                        <>
                          <span className="text-gray-700 mx-2">|</span>
                          <button
                            type="button"
                            disabled={anyPending}
                            className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                            onClick={() => {
                              if (window.confirm(`删除该${isDemo ? '模拟盘交易' : '实盘'}任务记录？\n\n成交明细、运行日志等将一并删除，不可恢复。`)) {
                                del.mutate(t.id);
                              }
                            }}
                            title="删除记录"
                          >
                            <Trash2 className="w-3.5 h-3.5 inline" /> 删除
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
                              if (window.confirm(`停止该${isDemo ? '模拟盘交易' : '实盘'}？可选择是否通过网关平掉持仓。`)) {
                                stop.mutate({ id: t.id, closePositions: true });
                              }
                            }}
                            title="停止（经网关平仓）"
                          >
                            <Square className="w-3.5 h-3.5 inline" /> 停止
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
              还没有{isDemo ? '模拟盘交易' : '实盘'}任务，点击右上角「{isDemo ? '部署模拟盘' : '部署实盘'}」通过向导
              {isDemo ? '（免准入检查）' : '完成准入检查后'}启动。
            </div>
          )}
          {(pause.isError || resume.isError || stop.isError || del.isError) && (
            <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
              操作失败：{apiError(pause.error ?? resume.error ?? stop.error ?? del.error)}
            </div>
          )}
        </div>
      )}

      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button type="button" className="btn-secondary px-3 py-1.5" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">{page} / {data.total_pages}</span>
          <button type="button" className="btn-secondary px-3 py-1.5" disabled={page >= data.total_pages} onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}>
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}

      <LiveDeployWizard open={deployOpen} onClose={() => setDeployOpen(false)} scope={scope} />
    </div>
  );
}
