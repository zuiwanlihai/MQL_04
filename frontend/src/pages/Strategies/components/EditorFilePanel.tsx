import { useState } from 'react';
import { FileCode2, Library, Loader2 } from 'lucide-react';
import { useTemplates } from '@/hooks/useStrategies';
import { api, apiError } from '@/lib/api';
import type { StrategyTemplate } from '@/types/api';

interface Props {
  strategyName: string;
  onLoadTemplate: (name: string, source: string) => void;
}

/** 编辑器左栏：当前策略文件 + SDK v1 模板库（点击载入源码）。 */
export default function EditorFilePanel({ strategyName, onLoadTemplate }: Props) {
  const templatesQuery = useTemplates();
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleLoad = async (id: string, name: string) => {
    setLoadingId(id);
    setError(null);
    try {
      const { data } = await api.get<StrategyTemplate>(`/strategies/templates/${id}`);
      onLoadTemplate(name, data.source_code);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <aside className="card flex flex-col overflow-hidden h-full">
      <div className="card-header">
        <h3 className="text-xs font-medium text-gray-400 uppercase tracking-wider">文件</h3>
      </div>
      <div className="card-body flex-1 overflow-y-auto space-y-4">
        <div>
          <p className="text-xs text-gray-500 mb-1.5">当前策略</p>
          <div className="flex items-center gap-2 px-2 py-1.5 rounded bg-blue-500/10 border border-blue-500/30 text-blue-300 text-sm">
            <FileCode2 className="w-4 h-4 shrink-0" />
            <span className="truncate font-mono">{strategyName}.py</span>
          </div>
        </div>

        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <Library className="w-3.5 h-3.5 text-gray-500" />
            <p className="text-xs text-gray-500">策略模板库</p>
          </div>
          {templatesQuery.isLoading && (
            <p className="text-xs text-gray-600 py-2">加载模板…</p>
          )}
          {templatesQuery.isError && (
            <p className="text-xs text-loss py-2">{apiError(templatesQuery.error)}</p>
          )}
          <ul className="space-y-1">
            {(templatesQuery.data?.items ?? []).map((tpl) => (
              <li key={tpl.id}>
                <button
                  type="button"
                  disabled={loadingId !== null}
                  onClick={() => void handleLoad(tpl.id, tpl.name)}
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-surface-700/60 transition-colors disabled:opacity-50"
                  title={tpl.description}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm text-gray-200 truncate">{tpl.name}</span>
                    {loadingId === tpl.id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400 shrink-0" />
                    ) : (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-700 text-gray-400 shrink-0">
                        {tpl.category}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 truncate-2 mt-0.5">{tpl.description}</p>
                </button>
              </li>
            ))}
          </ul>
          {error && <p className="text-xs text-loss mt-2">载入失败：{error}</p>}
        </div>
      </div>
    </aside>
  );
}
