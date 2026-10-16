import { useMemo, useState } from 'react';
import { ArrowLeft, Info } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { DrawdownChart, EquityChart } from '@/components/backtest/Charts';
import LogPanel from '@/components/backtest/LogPanel';
import MonthlyHeatmap from '@/components/backtest/MonthlyHeatmap';
import OverfitPanel from '@/components/backtest/OverfitPanel';
import OptimizationPanel from '@/components/backtest/OptimizationPanel';
import SensitivityPanel from '@/components/backtest/SensitivityPanel';
import { StrategyScorePanel } from '@/components/backtest/StrategyScore';
import TradeTable from '@/components/backtest/TradeTable';
import { MODE_LABELS, useBacktestReport, useBacktestTask } from '@/hooks/useBacktests';
import { apiError } from '@/lib/api';
import type { BacktestTaskDetail } from '@/types/api';

const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  running: '运行中',
  cancelling: '取消中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
};

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return (
    <div className="card">
      <div className="card-body py-3">
        <p className="text-xs text-gray-500">{label}</p>
        <p
          className={`text-lg font-semibold font-mono mt-0.5 ${
            tone === 'up' ? 'text-profit' : tone === 'down' ? 'text-loss' : 'text-gray-100'
          }`}
        >
          {value}
        </p>
      </div>
    </div>
  );
}

const pct = (v: number | undefined) => (v == null ? '—' : `${v.toFixed(2)}%`);
const num = (v: number | undefined, d = 2) => (v == null ? '—' : v.toFixed(d));
const tone = (v: number | undefined): 'up' | 'down' | undefined =>
  v == null ? undefined : v >= 0 ? 'up' : 'down';

function AssumptionBox({ task }: { task: BacktestTaskDetail }) {
  const snap = task.config_snapshot ?? {};
  const [open, setOpen] = useState(false);
  const text = (snap.assumption_text as string) ?? '';
  return (
    <div className="card">
      <button
        type="button"
        className="card-body w-full flex items-center gap-2 text-left py-3"
        onClick={() => setOpen((o) => !o)}
      >
        <Info className="w-4 h-4 text-blue-300 shrink-0" />
        <span className="text-sm text-gray-300">
          撮合假设：{MODE_LABELS[task.execution_mode]}档
          <span className="text-gray-500 ml-2">{open ? '收起' : '展开详情'}</span>
        </span>
      </button>
      {open && (
        <div className="px-4 pb-3 space-y-1 text-xs text-gray-400 border-t border-gray-700/50 pt-2">
          <p className="text-gray-300 leading-relaxed whitespace-pre-line">{text}</p>
          {'swap_long' in snap && (
            <p>
              隔夜利息：按品种 swap_long/swap_short 点数每晚计提
              （周三×3 覆盖周末，周六/周日不计；本次任务 swap_long={String(snap.swap_long)}、
              swap_short={String(snap.swap_short)} 点/手/夜）。
            </p>
          )}
          <p>
            点差模式：{task.spread_mode === 'fixed' ? `固定 ${num(task.spread_points ?? undefined, 1)} 点` : '跟随行情'}
            {' · '}滑点：{task.slippage_points == null ? '档位默认' : `${num(task.slippage_points, 1)} 点`}
            {' · '}佣金/手：{num(task.commission_per_lot)}
          </p>
          <p className="font-mono">
            seed={String(snap.seed ?? '—')} · source_hash={String(snap.source_hash ?? '—')} · warmup=
            {task.warmup_bars}
            {snap.warmup_actual != null ? `（实际 ${String(snap.warmup_actual)}）` : ''}
            {snap.n_bars != null ? ` · bars=${String(snap.n_bars)}` : ''}
          </p>
          {snap.data_fingerprint != null && (
            <p className="font-mono text-gray-600">数据指纹：{JSON.stringify(snap.data_fingerprint)}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function BacktestReportPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const taskQ = useBacktestTask(taskId);
  const task = taskQ.data;
  const reportQ = useBacktestReport(taskId, task?.status);
  const report = reportQ.data?.report;

  // ── Tabs ──
  type TabKey = 'overview' | 'score' | 'trades' | 'overfit' | 'optimization' | 'sensitivity' | 'logs';
  const allTabs: { key: TabKey; label: string; show: boolean }[] = [
    { key: 'overview',      label: '📊 概览',       show: task?.status === 'completed' && !!report },
    { key: 'score',         label: '🎯 评分',       show: task?.status === 'completed' && !!report },
    { key: 'trades',        label: '📋 成交明细',   show: task?.status === 'completed' && !!report },
    { key: 'overfit',       label: '🧪 过拟合检测', show: task?.status === 'completed' },
    { key: 'optimization',  label: '⚙️ 参数优化',   show: task?.status === 'completed' },
    { key: 'sensitivity',   label: '🔍 灵敏度分析', show: !!task?.group_id },
    { key: 'logs',          label: '📝 运行日志',   show: true },
  ];
  const visibleTabs = allTabs.filter((t) => t.show);
  const [activeTab, setActiveTab] = useState<TabKey>('overview');
  const activeSafe = useMemo(() => {
    if (visibleTabs.some((t) => t.key === activeTab)) return activeTab;
    return visibleTabs[0]?.key ?? 'logs';
  }, [activeTab, visibleTabs]);

  if (taskQ.isError) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body text-sm text-loss">{apiError(taskQ.error)}</div>
      </div>
    );
  }
  if (!task) {
    return <div className="text-sm text-gray-500 py-10 text-center">加载任务…</div>;
  }

  const active = task.status === 'pending' || task.status === 'running' || task.status === 'cancelling';

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Link to="/backtest" className="btn-ghost p-1.5" title="返回列表">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-lg font-semibold text-white">
          回测报告
          <span className="ml-2 text-xs font-mono text-gray-500">{task.id.slice(0, 8)}</span>
        </h1>
        <span className="px-2 py-0.5 rounded border text-xs bg-surface-700 text-gray-300 border-gray-600">
          {MODE_LABELS[task.execution_mode]}档
        </span>
        <span className="px-2 py-0.5 rounded border text-xs bg-blue-500/15 text-blue-300 border-blue-500/40">
          {STATUS_LABELS[task.status]}
        </span>
        {task.timed_out && (
          <span className="px-2 py-0.5 rounded border text-xs bg-loss/15 text-loss border-loss/40">超时中断</span>
        )}
        {task.execution_mode === 'optimistic' && (
          <span className="px-2 py-0.5 rounded border text-xs bg-yellow-500/15 text-yellow-400 border-yellow-500/40">
            乐观档 · 结果仅供参考
          </span>
        )}
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="card">
          <div className="card-body text-sm space-y-1.5">
            <div className="flex justify-between">
              <span className="text-gray-500">品种 / 周期</span>
              <span className="font-mono text-gray-200">
                {task.symbol} / {task.timeframe}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">回放区间</span>
              <span className="font-mono text-gray-200 text-xs">
                {new Date(task.start_date).toLocaleString('zh-CN')} ~{' '}
                {new Date(task.end_date).toLocaleString('zh-CN')}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">初始资金 / 杠杆</span>
              <span className="font-mono text-gray-200">
                {num(task.initial_deposit)} / 1:{task.leverage}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">版本</span>
              <span className="font-mono text-gray-200">
                {task.strategy_version == null ? '工作副本' : `v${task.strategy_version}`}
              </span>
            </div>
          </div>
        </div>
        <AssumptionBox task={task} />
      </div>

      {active && (
        <div className="card">
          <div className="card-body space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-gray-300">
                {task.status === 'pending'
                  ? '任务排队中…'
                  : task.status === 'cancelling'
                    ? '正在取消…'
                    : '回测运行中…'}
              </span>
              <span className="font-mono text-gray-400">{task.progress_percent}%</span>
            </div>
            <div className="h-2 rounded bg-surface-700 overflow-hidden">
              <div className="h-full bg-blue-400 transition-all" style={{ width: `${task.progress_percent}%` }} />
            </div>
          </div>
        </div>
      )}

      {task.status === 'failed' && (
        <div className="card border-loss/40">
          <div className="card-body text-sm space-y-1">
            <p className="text-loss font-medium">任务失败{task.error_code ? `（${task.error_code}）` : ''}</p>
            <p className="text-gray-300 whitespace-pre-wrap break-all">{task.error_message ?? '未知错误'}</p>
          </div>
        </div>
      )}

      {task.status === 'completed' && report && (
        <>
          {report.total_trades === 0 && (
            <div className="card border-amber-400/40 bg-amber-400/10">
              <div className="card-body text-sm space-y-1">
                <p className="font-medium text-amber-300">本任务 0 笔成交 —— 策略在回测区间内未触发任何开仓信号</p>
                <p className="text-gray-300 leading-relaxed">
                  因此资金曲线为直线、月度收益与交易统计为 0、无成交明细，这是正常结果而非功能缺失。
                  常见原因：多因子得分未达开仓阈值、过滤条件未满足、或区间内实际可用数据太少。
                  可在页面底部"运行日志"查看逐根 K 线的信号得分记录（如 [SHADOW] score 未达阈值）。
                </p>
                {(() => {
                  const ec = report.equity_curve ?? [];
                  if (ec.length === 0) return null;
                  const fmt = (s: string) => new Date(s).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' });
                  return (
                    <p className="text-gray-400">
                      实际数据覆盖：<span className="font-mono">{fmt(ec[0].time)}</span> ~{' '}
                      <span className="font-mono">{fmt(ec[ec.length - 1].time)}</span>
                      （请求区间为{' '}
                      <span className="font-mono">
                        {new Date(task.start_date).toLocaleDateString('zh-CN')} ~{' '}
                        {new Date(task.end_date).toLocaleDateString('zh-CN')}
                      </span>
                      ，若两者差异大，说明数据源在该区间内只有部分K线）
                    </p>
                  );
                })()}
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
            <Metric label="总收益率" value={pct(report.total_return_percent)} tone={tone(report.total_return_percent)} />
            <Metric label="年化收益" value={pct(report.annual_return_percent)} tone={tone(report.annual_return_percent)} />
            <Metric label="夏普比率" value={num(report.sharpe_ratio)} />
            <Metric label="最大回撤" value={pct(report.max_drawdown_percent)} tone="down" />
            <Metric label="胜率" value={pct(report.win_rate)} />
            <Metric label="盈亏比" value={num(report.profit_factor)} />
            <Metric label="索提诺比率" value={num(report.sortino_ratio)} />
            <Metric label="卡玛比率" value={num(report.calmar_ratio)} />
            <Metric label="年化波动" value={pct(report.annual_volatility_percent)} />
            <Metric label="交易笔数" value={String(report.total_trades)} />
            <Metric label="期望收益/笔" value={num(report.expectancy)} tone={tone(report.expectancy)} />
            <Metric label="恢复因子" value={num(report.recovery_factor)} />
            <Metric label="隔夜费" value={num(report.total_swap)} tone={tone(report.total_swap)} />
          </div>
        </>
      )}

      {task.status === 'completed' && !report && (
        <p className="text-sm text-gray-500">报告数据加载中…</p>
      )}

      {/* ── 可切换标签：概览 / 成交明细 / 过拟合 / 参数优化 / 灵敏度 / 运行日志 ── */}
      <div className="card">
        <div className="card-body p-2">
          <div className="flex items-center gap-1 bg-surface-900 rounded-lg p-1 border border-gray-700/50 overflow-x-auto">
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={`px-3 py-1.5 text-sm rounded-md transition-colors whitespace-nowrap ${
                  activeSafe === tab.key
                    ? 'bg-surface-700 text-white'
                    : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="mt-4">
            {activeSafe === 'overview' && task.status === 'completed' && report && (
              <div className="space-y-4">
                <div className="card">
                  <div className="card-header">
                    <h3 className="text-sm font-medium text-gray-200">资金曲线</h3>
                    <span className="text-xs text-gray-500">蓝=净值 · 紫=余额 · 灰虚线=无操作基准</span>
                  </div>
                  <div className="card-body">
                    <EquityChart points={report.equity_curve} />
                  </div>
                </div>
                <div className="card">
                  <div className="card-header">
                    <h3 className="text-sm font-medium text-gray-200">回撤曲线</h3>
                  </div>
                  <div className="card-body">
                    <DrawdownChart points={report.equity_curve} />
                  </div>
                </div>
                <div className="grid md:grid-cols-2 gap-4">
                  <div className="card">
                    <div className="card-header">
                      <h3 className="text-sm font-medium text-gray-200">月度收益（%）</h3>
                    </div>
                    <div className="card-body">
                      <MonthlyHeatmap monthly={report.monthly_returns} />
                    </div>
                  </div>
                  <div className="card">
                    <div className="card-header">
                      <h3 className="text-sm font-medium text-gray-200">交易统计</h3>
                    </div>
                    <div className="card-body text-sm space-y-1.5">
                      <div className="flex justify-between">
                        <span className="text-gray-500">盈利 / 亏损笔数</span>
                        <span className="font-mono text-gray-200">
                          {report.winning_trades} / {report.losing_trades}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">平均盈利 / 亏损</span>
                        <span className="font-mono text-profit">{num(report.avg_win)}</span>
                        <span className="text-gray-600">/</span>
                        <span className="font-mono text-loss">{num(report.avg_loss)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">平均持仓时长</span>
                        <span className="font-mono text-gray-200">{num(report.avg_hold_hours, 1)} 小时</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">最长连胜 / 连亏</span>
                        <span className="font-mono text-gray-200">
                          {report.consecutive_wins} / {report.consecutive_losses}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">最大回撤金额</span>
                        <span className="font-mono text-loss">{num(report.max_drawdown_amount)}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeSafe === 'score' && task.status === 'completed' && report && (
              report.score ? (
                <StrategyScorePanel score={report.score} report={report} />
              ) : (
                <div className="card border-amber-400/40 bg-amber-400/10">
                  <div className="card-body text-sm">
                    <p className="font-medium text-amber-300">评分数据缺失</p>
                    <p className="text-gray-300 mt-1">这份回测报告生成时评分逻辑尚未上线，需要重新运行一次回测才能看到评分。</p>
                  </div>
                </div>
              )
            )}

            {activeSafe === 'trades' && task.status === 'completed' && report && (
              <TradeTable taskId={task.id} enabled />
            )}

            {activeSafe === 'overfit' && task.status === 'completed' && (
              <OverfitPanel taskId={task.id} />
            )}

            {activeSafe === 'optimization' && task.status === 'completed' && (
              <OptimizationPanel taskId={task.id} />
            )}

            {activeSafe === 'sensitivity' && task.group_id && (
              <SensitivityPanel groupId={task.group_id} selfTaskId={task.id} />
            )}

            {activeSafe === 'logs' && (
              <LogPanel taskId={task.id} poll={active} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
