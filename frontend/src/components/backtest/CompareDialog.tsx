import { useQueries } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api';
import type { BacktestReportResponse } from '@/types/api';

const pct = (v: number | undefined | null) => (v == null ? '—' : `${v.toFixed(2)}%`);
const num = (v: number | undefined | null, d = 2) => (v == null ? '—' : v.toFixed(d));

interface MetricDef {
  label: string;
  get: (r: BacktestReportResponse['report']) => number | null | undefined;
  better: 'high' | 'low'; // higher / lower wins
  fmt: (v: number | null | undefined) => string;
}

const METRICS: MetricDef[] = [
  { label: '总收益率', get: (r) => r.total_return_percent, better: 'high', fmt: pct },
  { label: '年化收益', get: (r) => r.annual_return_percent, better: 'high', fmt: pct },
  { label: '最大回撤', get: (r) => r.max_drawdown_percent, better: 'low', fmt: pct },
  { label: '夏普比率', get: (r) => r.sharpe_ratio, better: 'high', fmt: num },
  { label: '索提诺比率', get: (r) => r.sortino_ratio, better: 'high', fmt: num },
  { label: '卡玛比率', get: (r) => r.calmar_ratio, better: 'high', fmt: num },
  { label: '胜率', get: (r) => r.win_rate * 100, better: 'high', fmt: pct },
  { label: '盈亏比', get: (r) => r.profit_factor, better: 'high', fmt: num },
  { label: '期望收益/笔', get: (r) => r.expectancy, better: 'high', fmt: num },
  { label: '交易笔数', get: (r) => r.total_trades, better: 'high', fmt: (v) => String(v ?? '—') },
  { label: '隔夜费', get: (r) => r.total_swap, better: 'high', fmt: num },
];

export default function CompareDialog({
  taskIds,
  onClose,
}: {
  taskIds: string[];
  onClose: () => void;
}) {
  const queries = useQueries({
    queries: taskIds.map((id) => ({
      queryKey: ['backtests', id, 'report', 'compare'],
      queryFn: async (): Promise<BacktestReportResponse> => {
        const { data } = await api.get<BacktestReportResponse>(`/backtests/${id}/report`);
        return data;
      },
      enabled: true,
      staleTime: 30_000,
    })),
  });
  const loaded = queries.map((q) => q.data ?? null);
  const allLoaded = loaded.every(Boolean);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-5xl max-h-[90vh] overflow-y-auto">
        <div className="card-header flex items-center justify-between sticky top-0 bg-surface-800 z-10">
          <h3 className="text-sm font-medium text-gray-200">
            回测对比（A/B）
            <span className="ml-2 text-xs text-gray-500">{taskIds.length} 个已完成任务</span>
          </h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose} title="关闭">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body space-y-4">
          {!allLoaded && <p className="text-sm text-gray-500 py-4 text-center">加载报告…</p>}
          {allLoaded && (
            <>
              <div className="overflow-x-auto">
                <table className="table-dark text-xs">
                  <thead>
                    <tr>
                      <th className="w-32">指标</th>
                      {loaded.map((d, i) => {
                        const t = d!.task;
                        return (
                          <th key={i} className="text-left">
                            <Link
                              to={`/backtest/${t.id}/report`}
                              className="text-blue-400 hover:text-blue-300"
                              onClick={onClose}
                            >
                              {t.id.slice(0, 8)}
                            </Link>
                            <p className="text-gray-500 font-normal mt-0.5">
                              {t.symbol} / {t.timeframe} · {t.execution_mode === 'conservative' ? '保守' : t.execution_mode === 'standard' ? '标准' : '乐观'}档
                              <br />
                              {new Date(t.start_date).toLocaleDateString('zh-CN')} ~{' '}
                              {new Date(t.end_date).toLocaleDateString('zh-CN')}
                              {Object.keys(t.parameters ?? {}).length > 0 && (
                                <>
                                  <br />
                                  <span className="font-mono">
                                    {Object.entries(t.parameters)
                                      .map(([k, v]) => `${k}=${String(v)}`)
                                      .join(' · ')}
                                  </span>
                                </>
                              )}
                            </p>
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {METRICS.map((m) => {
                      const values = loaded.map((d) => m.get(d!.report));
                      const best = m.better === 'high'
                        ? Math.max(...values.filter((v): v is number => v != null))
                        : Math.min(...values.filter((v): v is number => v != null));
                      const singleBest =
                        values.filter((v) => v != null).length > 1 ? best : null;
                      return (
                        <tr key={m.label}>
                          <td className="text-gray-400">{m.label}</td>
                          {values.map((v, i) => (
                            <td
                              key={i}
                              className={`font-mono ${
                                v != null && singleBest != null && v === singleBest
                                  ? 'text-profit font-semibold'
                                  : 'text-gray-200'
                              }`}
                            >
                              {m.fmt(v)}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-gray-500">
                绿色 = 该指标在所选任务中最优（回撤取最小值）。不同品种/区间/档位的任务对比仅供参考。
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
