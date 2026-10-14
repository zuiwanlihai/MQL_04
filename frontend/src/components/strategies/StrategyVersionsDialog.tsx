import { useState } from 'react';
import { Loader2, RotateCcw, X } from 'lucide-react';
import { useRollbackVersion, useVersions } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import type { Strategy } from '@/types/api';

interface Props {
  strategy: Strategy | null;
  onClose: () => void;
}

export default function StrategyVersionsDialog({ strategy, onClose }: Props) {
  const versions = useVersions(strategy?.id);
  const rollback = useRollbackVersion();
  const [expanded, setExpanded] = useState<number | null>(null);

  if (!strategy) return null;

  const items = versions.data?.items ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[85vh] flex flex-col">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">
            版本历史 · {strategy.name}
            <span className="ml-2 text-xs text-gray-500">
              当前生效：{strategy.current_version == null ? '未冻结' : `v${strategy.current_version}`}
            </span>
          </h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body overflow-y-auto flex-1 space-y-2">
          {versions.isLoading && (
            <p className="text-sm text-gray-500 text-center py-6">
              <Loader2 className="w-4 h-4 inline animate-spin" /> 加载版本…
            </p>
          )}
          {versions.isError && (
            <p className="text-sm text-loss">{apiError(versions.error)}</p>
          )}
          {!versions.isLoading && items.length === 0 && (
            <p className="text-sm text-gray-500 text-center py-6">
              尚未冻结任何版本。在编辑器中点击「冻结版本」可保存当前代码为不可变快照。
            </p>
          )}
          {items.map((v) => (
            <div key={v.id} className="rounded border border-gray-700/60 bg-surface-900">
              <div className="flex items-center justify-between px-3 py-2">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-blue-300">v{v.version}</span>
                  {strategy.current_version === v.version && (
                    <span className="px-1.5 py-0.5 rounded bg-profit/15 text-profit text-xs border border-profit/40">
                      当前
                    </span>
                  )}
                  <span className="text-xs text-gray-500">
                    {new Date(v.created_at).toLocaleString('zh-CN')}
                  </span>
                  {v.changelog && (
                    <span className="text-xs text-gray-400 truncate max-w-40">{v.changelog}</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="text-xs text-gray-400 hover:text-gray-200"
                    onClick={() => setExpanded(expanded === v.version ? null : v.version)}
                  >
                    {expanded === v.version ? '收起代码' : '查看代码'}
                  </button>
                  <button
                    type="button"
                    className="text-xs text-yellow-400 hover:text-yellow-300 disabled:opacity-50"
                    disabled={
                      rollback.isPending || strategy.current_version === v.version
                    }
                    onClick={() => {
                      if (
                        window.confirm(
                          `回滚到 v${v.version}？将以其代码生成新版本并设为当前生效版本。`,
                        )
                      ) {
                        rollback.mutate({ id: strategy.id, version: v.version });
                      }
                    }}
                    title="回滚（产生新版本号）"
                  >
                    {rollback.isPending &&
                    (rollback.variables as { version: number })?.version === v.version ? (
                      <Loader2 className="w-3 h-3 inline animate-spin" />
                    ) : (
                      <RotateCcw className="w-3 h-3 inline" />
                    )}{' '}
                    回滚
                  </button>
                </div>
              </div>
              {expanded === v.version && (
                <pre className="text-xs text-gray-300 bg-surface-950 border-t border-gray-700/60 p-3 max-h-72 overflow-auto whitespace-pre font-mono">
                  {v.source_code}
                </pre>
              )}
            </div>
          ))}
          {rollback.isError && (
            <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
              回滚失败：{apiError(rollback.error)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
