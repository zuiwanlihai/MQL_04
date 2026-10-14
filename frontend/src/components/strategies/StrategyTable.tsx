import { Loader2, Pencil, Trash2, GitBranch, Upload, Wand2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useDeleteStrategy } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import type { Strategy, StrategyStatus } from '@/types/api';

const STATUS_STYLES: Record<StrategyStatus, string> = {
  draft: 'bg-surface-700 text-gray-300 border-gray-600',
  active: 'bg-profit/15 text-profit border-profit/40',
  paused: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  archived: 'bg-loss/15 text-loss border-loss/40',
};

const STATUS_LABELS: Record<StrategyStatus, string> = {
  draft: '草稿',
  active: '启用',
  paused: '暂停',
  archived: '归档',
};

const DIRECTION_LABELS: Record<string, string> = {
  long_only: '仅做多',
  short_only: '仅做空',
  both: '双向',
};

interface Props {
  strategies: Strategy[];
  isLoading: boolean;
  error: unknown;
  onShowVersions: (s: Strategy) => void;
  onPublish: (s: Strategy) => void;
}

export default function StrategyTable({ strategies, isLoading, error, onShowVersions, onPublish }: Props) {
  const del = useDeleteStrategy();

  if (error) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body text-sm text-loss">{apiError(error)}</div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">正在加载策略列表…</div>
      </div>
    );
  }

  if (strategies.length === 0) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">
          还没有策略，点击右上角「新建策略」从模板开始。
        </div>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <table className="table-dark">
        <thead>
          <tr>
            <th>名称</th>
            <th>状态</th>
            <th>版本</th>
            <th>品种 / 周期</th>
            <th>方向</th>
            <th>标签</th>
            <th>更新时间</th>
            <th className="text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((s) => (
            <tr key={s.id}>
              <td>
                <Link to={`/strategies/${s.id}/edit`} className="text-gray-100 hover:text-blue-300">
                  {s.name}
                </Link>
                {s.description && (
                  <p className="text-xs text-gray-500 max-w-56 truncate">{s.description}</p>
                )}
              </td>
              <td>
                <span className={`px-2 py-0.5 rounded border text-xs ${STATUS_STYLES[s.status]}`}>
                  {STATUS_LABELS[s.status]}
                </span>
              </td>
              <td className="font-mono text-gray-300">
                {s.current_version == null ? <span className="text-gray-600">未冻结</span> : `v${s.current_version}`}
              </td>
              <td className="text-gray-400">
                <span className="font-mono">{s.symbol_ids.join(', ') || '—'}</span>
                <span className="text-gray-600 mx-1">/</span>
                <span className="font-mono">{s.timeframe}</span>
              </td>
              <td className="text-gray-400 text-xs">{DIRECTION_LABELS[s.direction] ?? s.direction}</td>
              <td>
                <div className="flex gap-1 flex-wrap">
                  {s.tags.slice(0, 3).map((t) => (
                    <span key={t} className="px-1.5 py-0.5 rounded bg-surface-700 text-gray-400 text-xs">
                      {t}
                    </span>
                  ))}
                  {s.tags.length > 3 && <span className="text-xs text-gray-600">+{s.tags.length - 3}</span>}
                </div>
              </td>
              <td className="text-xs text-gray-500">{new Date(s.updated_at).toLocaleString('zh-CN')}</td>
              <td className="text-right whitespace-nowrap">
                <Link
                  to={`/strategies/${s.id}/edit`}
                  className="text-blue-400 hover:text-blue-300 text-sm"
                  title="打开编辑器"
                >
                  <Pencil className="w-3.5 h-3.5 inline" /> 编辑
                </Link>
                <span className="text-gray-700 mx-2">|</span>
                <Link
                  to={`/strategies/${s.id}/builder`}
                  className="text-violet-300 hover:text-violet-200 text-sm"
                  title="用可视化构建器编辑（保存将覆盖源码）"
                >
                  <Wand2 className="w-3.5 h-3.5 inline" /> 构建
                </Link>
                <span className="text-gray-700 mx-2">|</span>
                <button
                  type="button"
                  className="text-gray-400 hover:text-blue-300 text-sm"
                  onClick={() => onPublish(s)}
                  title="发布到策略市场"
                >
                  <Upload className="w-3.5 h-3.5 inline" /> 发布
                </button>
                <span className="text-gray-700 mx-2">|</span>
                <button
                  type="button"
                  className="text-gray-400 hover:text-gray-200 text-sm"
                  onClick={() => onShowVersions(s)}
                  title="版本历史"
                >
                  <GitBranch className="w-3.5 h-3.5 inline" /> 版本
                </button>
                <span className="text-gray-700 mx-2">|</span>
                <button
                  type="button"
                  className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                  disabled={del.isPending}
                  onClick={() => {
                    if (window.confirm(`确认删除策略「${s.name}」？其所有冻结版本将一并删除。`)) {
                      del.mutate(s.id);
                    }
                  }}
                  title="删除"
                >
                  {del.isPending && del.variables === s.id ? (
                    <Loader2 className="w-3.5 h-3.5 inline animate-spin" />
                  ) : (
                    <Trash2 className="w-3.5 h-3.5 inline" />
                  )}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {del.isError && (
        <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
          删除失败：{apiError(del.error)}
        </div>
      )}
    </div>
  );
}
