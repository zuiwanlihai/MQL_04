import { Loader2, Play } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useAnalyses,
  useCreateMonteCarlo,
  useCreateWalkForward,
} from '@/hooks/useBacktests';
import { apiError } from '@/lib/api';
import type {
  BacktestAnalysis,
  MCMethod,
  MCResult,
  WFResult,
} from '@/types/api';

const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}%`);
const fmtNum = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d));
const pctTone = (v: number | null | undefined) =>
  v == null ? 'text-gray-100' : v >= 0 ? 'text-profit' : 'text-loss';

const STATUS_BADGES: Record<string, string> = {
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  completed: 'bg-profit/15 text-profit border-profit/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};
const STATUS_LABELS: Record<string, string> = {
  running: '进行中',
  completed: '已完成',
  failed: '失败',
};

function percentileTable(title: string, p: MCResult['final_return_percent']) {
  return (
    <table className="table-dark text-xs">
      <thead>
        <tr>
          <th>{title}</th>
          <th className="text-right">P5</th>
          <th className="text-right">P25</th>
          <th className="text-right">P50</th>
          <th className="text-right">P75</th>
          <th className="text-right">P95</th>
          <th className="text-right">均值</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td className="text-gray-300">总收益率</td>
          {[p.p5, p.p25, p.p50, p.p75, p.p95, p.mean].map((v, i) => (
            <td key={i} className={`text-right font-mono ${pctTone(v)}`}>
              {v.toFixed(2)}%
            </td>
          ))}
        </tr>
      </tbody>
    </table>
  );
}

function MonteCarloCard({ analyses, taskId }: { analyses: BacktestAnalysis[]; taskId: string }) {
  const create = useCreateMonteCarlo(taskId);
  const [simulations, setSimulations] = useState(1000);
  const [method, setMethod] = useState<MCMethod>('bootstrap');
  const [ruin, setRuin] = useState(30);

  const latest = analyses.find((a) => a.kind === 'monte_carlo');
  const res = (latest?.result ?? null) as MCResult | null;

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">蒙特卡洛模拟</h3>
        <span className="text-xs text-gray-500">重采样成交序列，量化路径风险</span>
      </div>
      <div className="card-body space-y-3">
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">模拟次数</span>
            <input
              type="number"
              className="input w-28"
              min={100}
              max={10000}
              step={100}
              value={simulations}
              onChange={(e) => setSimulations(Number(e.target.value) || 1000)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">方法</span>
            <select
              className="input w-36"
              value={method}
              onChange={(e) => setMethod(e.target.value as MCMethod)}
            >
              <option value="bootstrap">Bootstrap（有放回）</option>
              <option value="shuffle">Shuffle（打乱顺序）</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">破产回撤线 %</span>
            <input
              type="number"
              className="input w-24"
              min={1}
              max={100}
              value={ruin}
              onChange={(e) => setRuin(Number(e.target.value) || 30)}
            />
          </label>
          <button
            type="button"
            className="btn-primary flex items-center gap-1.5 disabled:opacity-50"
            disabled={create.isPending}
            onClick={() => create.mutate({ simulations, method, ruin_dd_percent: ruin })}
          >
            {create.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            运行模拟
          </button>
        </div>
        {create.isError && <p className="text-sm text-loss">{apiError(create.error)}</p>}
        {latest?.status === 'failed' && (
          <p className="text-sm text-loss">{latest.error ?? '分析失败'}</p>
        )}
        {res && latest?.status === 'completed' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="px-2 py-0.5 rounded border border-gray-600 bg-surface-700 text-gray-300">
                {res.method === 'bootstrap' ? 'Bootstrap' : 'Shuffle'} · {res.simulations} 次 · seed={res.seed}
              </span>
              <span
                className={`px-2 py-0.5 rounded border ${
                  res.prob_loss > 0.3
                    ? 'bg-loss/15 text-loss border-loss/40'
                    : 'bg-profit/15 text-profit border-profit/40'
                }`}
              >
                亏损概率 {(res.prob_loss * 100).toFixed(1)}%
              </span>
              <span
                className={`px-2 py-0.5 rounded border ${
                  res.prob_ruin > 0
                    ? 'bg-loss/15 text-loss border-loss/40'
                    : 'bg-profit/15 text-profit border-profit/40'
                }`}
              >
                破产概率（回撤&gt;{res.ruin_dd_percent}%）{(res.prob_ruin * 100).toFixed(1)}%
              </span>
              <span className="px-2 py-0.5 rounded border border-gray-600 bg-surface-700 text-gray-400">
                实际：收益 {fmtPct(res.original.final_return_percent)} · 回撤{' '}
                {fmtPct(res.original.max_dd_percent)}
              </span>
            </div>
            <div className="overflow-x-auto">
              {percentileTable('收益分布', res.final_return_percent)}
            </div>
            <div className="overflow-x-auto">
              {percentileTable('最大回撤分布', res.max_dd_percent)}
            </div>
          </div>
        )}
        {!res && latest?.status !== 'failed' && (
          <p className="text-xs text-gray-500">
            尚未运行。Bootstrap 会同时改变收益与回撤分布；Shuffle 只改变交易顺序（收益不变），用于检验回撤对顺序的敏感度。
          </p>
        )}
      </div>
    </div>
  );
}

function WalkForwardCard({ analyses, taskId }: { analyses: BacktestAnalysis[]; taskId: string }) {
  const create = useCreateWalkForward(taskId);
  const [folds, setFolds] = useState(4);
  const [mode, setMode] = useState<'rolling' | 'anchored'>('rolling');

  const latest = analyses.find((a) => a.kind === 'walk_forward');
  const res = (latest?.result ?? null) as WFResult | null;
  const children = (latest?.params?.children ?? []) as { role: string; fold: number; task_id: string }[];

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">Walk-Forward 滚动验证</h3>
        <span className="text-xs text-gray-500">样本内/样本外分段回测，检验稳健性</span>
      </div>
      <div className="card-body space-y-3">
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">折数（2-8）</span>
            <input
              type="number"
              className="input w-24"
              min={2}
              max={8}
              value={folds}
              onChange={(e) => setFolds(Number(e.target.value) || 4)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">窗口模式</span>
            <select
              className="input w-40"
              value={mode}
              onChange={(e) => setMode(e.target.value as 'rolling' | 'anchored')}
            >
              <option value="rolling">滚动窗口</option>
              <option value="anchored">扩张窗口</option>
            </select>
          </label>
          <button
            type="button"
            className="btn-primary flex items-center gap-1.5 disabled:opacity-50"
            disabled={create.isPending}
            onClick={() => create.mutate({ folds, mode })}
          >
            {create.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            启动验证（{folds * 2} 个子回测）
          </button>
        </div>
        {create.isError && <p className="text-sm text-loss">{apiError(create.error)}</p>}
        {latest?.status === 'running' && (
          <p className="text-sm text-blue-300">
            子回测执行中（{children.length} 个任务，完成后自动聚合）…
          </p>
        )}
        {latest?.status === 'failed' && (
          <p className="text-sm text-loss whitespace-pre-wrap break-all">{latest.error ?? '验证失败'}</p>
        )}
        {res && latest?.status === 'completed' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="px-2 py-0.5 rounded border border-gray-600 bg-surface-700 text-gray-300">
                {res.mode === 'rolling' ? '滚动' : '扩张'}窗口 · {res.folds} 折
              </span>
              <span
                className={`px-2 py-0.5 rounded border ${
                  res.summary.robust
                    ? 'bg-profit/15 text-profit border-profit/40'
                    : 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40'
                }`}
              >
                {res.summary.robust ? '样本外稳健' : '样本外一致性不足，警惕过拟合'}
              </span>
              <span className="px-2 py-0.5 rounded border border-gray-600 bg-surface-700 text-gray-400">
                样本外盈利折数 {res.summary.oos_profitable}/{res.folds}
              </span>
              {res.summary.wf_efficiency_mean != null && (
                <span className="px-2 py-0.5 rounded border border-gray-600 bg-surface-700 text-gray-400">
                  WF 效率均值 {fmtNum(res.summary.wf_efficiency_mean)}
                </span>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="table-dark text-xs">
                <thead>
                  <tr>
                    <th>折</th>
                    <th className="text-right">样本内收益</th>
                    <th className="text-right">样本外收益</th>
                    <th className="text-right">样本内回撤</th>
                    <th className="text-right">样本外回撤</th>
                    <th className="text-right">WF 效率</th>
                    <th className="text-right">样本外交易</th>
                    <th className="text-right">报告</th>
                  </tr>
                </thead>
                <tbody>
                  {res.rows.map((row) => (
                    <tr key={row.fold}>
                      <td className="text-gray-300">{row.fold}</td>
                      <td className={`text-right font-mono ${pctTone(row.is_return_percent)}`}>
                        {fmtPct(row.is_return_percent)}
                      </td>
                      <td className={`text-right font-mono ${pctTone(row.oos_return_percent)}`}>
                        {fmtPct(row.oos_return_percent)}
                      </td>
                      <td className="text-right font-mono text-loss">{fmtPct(row.is_max_dd_percent)}</td>
                      <td className="text-right font-mono text-loss">{fmtPct(row.oos_max_dd_percent)}</td>
                      <td className="text-right font-mono text-gray-200">{fmtNum(row.efficiency)}</td>
                      <td className="text-right font-mono text-gray-400">{row.oos_trades}</td>
                      <td className="text-right whitespace-nowrap">
                        <Link
                          to={`/backtest/${row.oos_task_id}/report`}
                          className="text-blue-400 hover:text-blue-300"
                        >
                          OOS
                        </Link>
                        <span className="text-gray-700 mx-1">|</span>
                        <Link
                          to={`/backtest/${row.is_task_id}/report`}
                          className="text-blue-400 hover:text-blue-300"
                        >
                          IS
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        {!res && latest?.status !== 'running' && latest?.status !== 'failed' && (
          <p className="text-xs text-gray-500">
            将区间切成 N+1 段：每折在样本内（IS）与紧随的样本外（OOS）窗口各跑一次回测。样本外表现持续显著弱于样本内 → 参数可能过拟合。
          </p>
        )}
      </div>
    </div>
  );
}

export default function OverfitPanel({ taskId }: { taskId: string }) {
  const q = useAnalyses(taskId, true);
  const analyses = q.data?.items ?? [];
  if (q.isLoading) {
    return (
      <div className="card">
        <div className="card-body text-sm text-gray-500 py-6 text-center">加载分析记录…</div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-gray-200">防过拟合分析</h2>
        {analyses.some((a) => a.status === 'running') && (
          <span className={`px-2 py-0.5 rounded border text-xs ${STATUS_BADGES.running}`}>
            {STATUS_LABELS.running}
          </span>
        )}
      </div>
      <div className="grid lg:grid-cols-2 gap-4">
        <MonteCarloCard analyses={analyses} taskId={taskId} />
        <WalkForwardCard analyses={analyses} taskId={taskId} />
      </div>
    </div>
  );
}
