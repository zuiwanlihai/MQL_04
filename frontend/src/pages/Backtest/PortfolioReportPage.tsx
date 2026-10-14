import { ArrowLeft, Loader2 } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { DrawdownChart, EquityChart } from '@/components/backtest/Charts';
import { usePortfolio } from '@/hooks/useBacktests';
import { apiError } from '@/lib/api';
import type { PortfolioSymbolRow } from '@/types/api';

const STATUS_STYLES: Record<string, string> = {
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  completed: 'bg-profit/15 text-profit border-profit/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const STATUS_LABELS: Record<string, string> = {
  running: '聚合中',
  completed: '已完成',
  failed: '失败',
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

function SymbolTable({ rows }: { rows: PortfolioSymbolRow[] }) {
  return (
    <div className="card overflow-hidden">
      <div className="card-header">
        <h3 className="text-sm font-medium text-gray-200">分品种明细</h3>
        <span className="text-xs text-gray-500">点击品种查看单品种完整报告</span>
      </div>
      <table className="table-dark">
        <thead>
          <tr>
            <th>品种</th>
            <th>分配资金</th>
            <th>总收益</th>
            <th>收益率</th>
            <th>最大回撤</th>
            <th>夏普</th>
            <th>交易笔数</th>
            <th>胜率</th>
            <th>盈亏比</th>
            <th>隔夜费</th>
            <th className="text-right">报告</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td className="font-mono text-gray-100">{r.symbol}</td>
              <td className="font-mono text-gray-400">{num(r.deposit ?? undefined)}</td>
              <td className={`font-mono ${tone(r.total_return) === 'up' ? 'text-profit' : 'text-loss'}`}>
                {num(r.total_return)}
              </td>
              <td className={`font-mono ${tone(r.total_return_percent) === 'up' ? 'text-profit' : 'text-loss'}`}>
                {pct(r.total_return_percent)}
              </td>
              <td className="font-mono text-loss">{pct(r.max_drawdown_percent)}</td>
              <td className="font-mono text-gray-300">{num(r.sharpe_ratio)}</td>
              <td className="font-mono text-gray-400">{r.total_trades}</td>
              <td className="font-mono text-gray-400">{pct(r.win_rate * 100)}</td>
              <td className="font-mono text-gray-400">{num(r.profit_factor)}</td>
              <td className={`font-mono ${tone(r.total_swap) === 'up' ? 'text-profit' : 'text-loss'}`}>
                {num(r.total_swap)}
              </td>
              <td className="text-right">
                <Link
                  to={`/backtest/${r.task_id}/report`}
                  className="text-blue-400 hover:text-blue-300 text-sm"
                >
                  查看
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PortfolioReportPage() {
  const { portfolioId } = useParams<{ portfolioId: string }>();
  const q = usePortfolio(portfolioId);
  const pf = q.data;

  if (q.isError) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body text-sm text-loss">{apiError(q.error)}</div>
      </div>
    );
  }
  if (!pf) {
    return (
      <div className="text-sm text-gray-500 py-10 text-center flex items-center justify-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> 加载组合回测…
      </div>
    );
  }

  const params = (pf.params ?? {}) as Record<string, unknown>;
  const children = (params.children as { symbol: string; task_id: string }[] | undefined) ?? [];
  const res = pf.result;
  const metrics = res?.metrics;
  const startIso = typeof params.start === 'string' ? params.start : pf.created_at;
  const endIso = typeof params.end === 'string' ? params.end : pf.created_at;
  const mode = typeof params.execution_mode === 'string' ? params.execution_mode : 'conservative';
  const modeLabels: Record<string, string> = { conservative: '保守', standard: '标准', optimistic: '乐观' };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <Link to="/backtest" className="btn-ghost p-1.5" title="返回列表">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-lg font-semibold text-white">
          组合回测报告
          <span className="ml-2 text-xs font-mono text-gray-500">{pf.id.slice(0, 8)}</span>
        </h1>
        <span className="text-sm text-gray-400">{pf.strategy_name || pf.strategy_id.slice(0, 8)}</span>
        <div className="flex gap-1">
          {pf.symbols.map((s) => (
            <span
              key={s}
              className="px-2 py-0.5 rounded bg-surface-700 border border-gray-600 text-xs font-mono text-gray-200"
            >
              {s}
            </span>
          ))}
        </div>
        <span className={`px-2 py-0.5 rounded border text-xs ${STATUS_STYLES[pf.status]}`}>
          {STATUS_LABELS[pf.status]}
        </span>
        <span className="px-2 py-0.5 rounded border text-xs bg-surface-700 text-gray-300 border-gray-600">
          {modeLabels[mode] ?? mode}档
        </span>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="card">
          <div className="card-body text-sm space-y-1.5">
            <div className="flex justify-between">
              <span className="text-gray-500">品种数 / 周期</span>
              <span className="font-mono text-gray-200">
                {pf.symbols.length} / {pf.timeframe}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">回放区间</span>
              <span className="font-mono text-gray-200 text-xs">
                {new Date(startIso).toLocaleString('zh-CN')} ~ {new Date(endIso).toLocaleString('zh-CN')}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">总资金（均分）</span>
              <span className="font-mono text-gray-200">{num(pf.total_deposit)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">子任务进度</span>
              <span className="font-mono text-gray-200">
                {pf.status === 'running' ? '子回测执行中…' : `${children.length} 个子任务`}
              </span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-body text-sm space-y-1.5">
            <p className="text-xs text-gray-500 font-medium">组合口径说明</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              每个品种独立子回测（资金均分、独立账户模拟），组合权益 = 各品种权益按时间戳并集前值填充求和；
              组合指标由降采样合并曲线近似计算，与单品种报告口径存在采样差异。
            </p>
          </div>
        </div>
      </div>

      {pf.status === 'running' && (
        <div className="card">
          <div className="card-body space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-gray-300 flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin text-blue-300" />
                {children.length} 个子回测排队 / 运行中，全部完成后自动聚合…
              </span>
            </div>
          </div>
        </div>
      )}

      {pf.status === 'failed' && (
        <div className="card border-loss/40">
          <div className="card-body text-sm space-y-2">
            <p className="text-loss font-medium">组合回测失败</p>
            <p className="text-gray-300 whitespace-pre-wrap break-all">{pf.error ?? '未知错误'}</p>
            {children.length > 0 && (
              <div className="flex flex-wrap gap-3 pt-1">
                {children.map((c) => (
                  <Link
                    key={c.task_id}
                    to={`/backtest/${c.task_id}/report`}
                    className="text-blue-400 hover:text-blue-300 text-sm font-mono"
                  >
                    {c.symbol}
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {pf.status === 'completed' && metrics && res && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
            <Metric
              label="组合总收益"
              value={num(metrics.total_return)}
              tone={tone(metrics.total_return)}
            />
            <Metric
              label="组合收益率"
              value={pct(metrics.total_return_percent)}
              tone={tone(metrics.total_return_percent)}
            />
            <Metric
              label="年化收益"
              value={pct(metrics.annual_return_percent)}
              tone={tone(metrics.annual_return_percent)}
            />
            <Metric label="组合夏普" value={num(metrics.sharpe_ratio)} />
            <Metric label="最大回撤" value={pct(metrics.max_drawdown_percent)} tone="down" />
            <Metric label="回撤金额" value={num(metrics.max_drawdown_amount)} tone="down" />
          </div>

          <div className="card">
            <div className="card-header">
              <h3 className="text-sm font-medium text-gray-200">组合权益曲线</h3>
              <span className="text-xs text-gray-500">蓝=组合净值 · 灰虚线=无操作基准</span>
            </div>
            <div className="card-body">
              <EquityChart points={res.equity_curve} />
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h3 className="text-sm font-medium text-gray-200">组合回撤曲线</h3>
            </div>
            <div className="card-body">
              <DrawdownChart points={res.equity_curve} />
            </div>
          </div>

          <SymbolTable rows={res.symbols} />
        </>
      )}
    </div>
  );
}
