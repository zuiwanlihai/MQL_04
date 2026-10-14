import { BookOpen, Loader2, X } from 'lucide-react';
import { useStrategy } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';

interface Props {
  strategyId: string | undefined;
  name: string;
  open: boolean;
  onClose: () => void;
}

interface DocBlock {
  header: string | null;
  lines: string[];
}

/** 把源码头部文档串切成「小节标题 + 正文」块。 */
function parseDoc(doc: string): DocBlock[] {
  const blocks: DocBlock[] = [];
  let cur: DocBlock | null = null;
  for (const raw of doc.split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      if (cur) {
        blocks.push(cur);
        cur = null;
      }
      continue;
    }
    if (/[:：]\s*$/.test(line) && line.length < 60) {
      if (cur) blocks.push(cur);
      cur = { header: line.trim(), lines: [] };
    } else {
      if (!cur) cur = { header: null, lines: [] };
      cur.lines.push(line.trim());
    }
  }
  if (cur) blocks.push(cur);
  return blocks;
}

/** AI 策略核心逻辑 — 展示策略源码头部的说明文档(权威版本)。 */
export default function StrategyLogicDialog({ strategyId, name, open, onClose }: Props) {
  const detail = useStrategy(open ? strategyId : undefined);

  if (!open) return null;

  const source = detail.data?.source_code ?? '';
  const m = source.match(/^\s*"""([\s\S]*?)"""/);
  const doc = m ? m[1].trim() : '';
  const blocks = doc ? parseDoc(doc) : [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[88vh] flex flex-col">
        <div className="card-header flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-blue-400 shrink-0" />
          <h3 className="text-sm font-medium text-gray-200 truncate">
            AI 核心逻辑 · {name}
          </h3>
          <button type="button" className="btn-ghost p-1 ml-auto shrink-0" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body overflow-y-auto">
          {detail.isLoading && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-6 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" /> 正在加载策略文档…
            </div>
          )}
          {detail.isError && (
            <div className="text-sm text-loss py-6">{apiError(detail.error)}</div>
          )}
          {blocks.length > 0 ? (
            <div className="space-y-3">
              {blocks.map((b, i) => (
                <div key={i}>
                  {b.header && (
                    <h4 className="text-sm font-semibold text-blue-300 mb-1">{b.header}</h4>
                  )}
                  {b.lines.length > 0 && (
                    <p className="text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">
                      {b.lines.join('\n')}
                    </p>
                  )}
                </div>
              ))}
              <p className="text-xs text-gray-600 pt-2 border-t border-gray-700/50">
                以上内容摘自策略源码的说明文档，随策略版本一起保存；参数与阈值可在策略详情中调整。
              </p>
            </div>
          ) : (
            !detail.isLoading &&
            !detail.isError && (
              <div className="text-sm text-gray-500 py-6">
                {detail.data?.description || '该策略未提供说明文档。'}
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
