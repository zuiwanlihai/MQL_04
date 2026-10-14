import { AlertTriangle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { MODE_LABELS, useSensitivityCompare } from '@/hooks/useBacktests';
import type { ExecutionMode } from '@/types/api';

interface Props {
  groupId: string;
  selfTaskId: string;
}

const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}%`);

export default function SensitivityPanel({ groupId, selfTaskId }: Props) {
  const cmp = useSensitivityCompare(groupId);
  const d = cmp.data;
  if (!d) {
    return (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">敏感性对比（保守 vs 标准）</h3>
        </div>
        <div className="card-body text-sm text-gray-500 py-6 text-center">加载对比组数据…</div>
      </div>
    );
  }
  const sides: { mode: ExecutionMode; side: typeof d.conservative }[] = [
    { mode: 'conservative', side: d.conservative },
    { mode: 'standard', side: d.standard },
  ];
  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">敏感性对比（保守 vs 标准）</h3>
        {d.sensitive ? (
          <span className="flex items-center gap-1 text-xs text-loss bg-loss/10 border border-loss/40 rounded px-2 py-0.5">
            <AlertTriangle className="w-3.5 h-3.5" /> 执行价格敏感（年化差 {fmtPct(d.annual_diff_percent)} 个百分点）
          </span>
        ) : (
          <span className="text-xs text-profit">年化差 {fmtPct(d.annual_diff_percent)} 个百分点，稳健</span>
        )}
      </div>
      <div className="card-body overflow-x-auto p-0">
        <table className="table-dark text-sm">
          <thead>
            <tr>
              <th>档位</th>
              <th>状态</th>
              <th className="text-right">总收益率</th>
              <th className="text-right">年化收益</th>
              <th className="text-right">最大回撤</th>
              <th className="text-right">夏普</th>
              <th className="text-right">报告</th>
            </tr>
          </thead>
          <tbody>
            {sides.map(({ mode, side }) => (
              <tr key={mode}>
                <td className="text-gray-200">{MODE_LABELS[mode]}档</td>
                <td className="text-xs text-gray-400">{side.status}</td>
                <td className="text-right font-mono">{fmtPct(side.total_return_percent)}</td>
                <td className="text-right font-mono">{fmtPct(side.annual_return_percent)}</td>
                <td className="text-right font-mono">{fmtPct(side.max_drawdown_percent)}</td>
                <td className="text-right font-mono">
                  {side.sharpe_ratio == null ? '—' : side.sharpe_ratio.toFixed(2)}
                </td>
                <td className="text-right">
                  {side.status === 'completed' ? (
                    <Link
                      to={`/backtest/${side.task_id}/report`}
                      className={side.task_id === selfTaskId ? 'text-gray-600' : 'text-blue-400 hover:text-blue-300'}
                    >
                      {side.task_id === selfTaskId ? '当前' : '查看'}
                    </Link>
                  ) : (
                    <span className="text-xs text-gray-600">未完成</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
