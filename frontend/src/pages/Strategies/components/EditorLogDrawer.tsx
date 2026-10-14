import { ChevronDown, ChevronUp } from 'lucide-react';
import type { ValidationResult } from '@/types/api';

interface Props {
  validation: ValidationResult | null;
  open: boolean;
  onToggle: () => void;
  onJumpToIssue: (line: number) => void;
}

const SEVERITY_STYLES = {
  error: 'text-loss',
  warning: 'text-yellow-400',
  info: 'text-gray-400',
} as const;

/** 编辑器底部校验日志抽屉：展示最近一次 AST 校验结果，点击跳行。 */
export default function EditorLogDrawer({ validation, open, onToggle, onJumpToIssue }: Props) {
  const issues = validation?.issues ?? [];
  const errorCount = issues.filter((i) => i.severity === 'error').length;
  const warnCount = issues.filter((i) => i.severity === 'warning').length;

  return (
    <div className="border-t border-gray-700/50 bg-surface-900 shrink-0">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-2 text-xs hover:bg-surface-800 transition-colors"
      >
        <span className="text-gray-400 font-medium">SDK 校验</span>
        {validation == null ? (
          <span className="text-gray-600">等待校验…</span>
        ) : issues.length === 0 ? (
          <span className="text-profit">✓ 无问题</span>
        ) : (
          <>
            <span className="text-loss">{errorCount} 错误</span>
            <span className="text-yellow-400">{warnCount} 警告</span>
            <span className="text-gray-600">共 {issues.length} 条</span>
          </>
        )}
        <span className="ml-auto text-gray-500">
          {open ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
        </span>
      </button>

      {open && (
        <div className="max-h-40 overflow-y-auto border-t border-gray-700/50">
          {issues.length === 0 ? (
            <p className="px-4 py-3 text-xs text-gray-500">
              {validation ? '当前代码通过 SDK v1 静态校验。' : '输入代码后将自动进行 AST 校验。'}
            </p>
          ) : (
            <ul>
              {issues.map((i, idx) => (
                <li key={`${i.code}-${i.line}-${idx}`}>
                  <button
                    type="button"
                    onClick={() => onJumpToIssue(i.line)}
                    className="w-full text-left px-4 py-1.5 flex items-baseline gap-3 hover:bg-surface-800 transition-colors"
                  >
                    <span className={`font-mono text-xs shrink-0 ${SEVERITY_STYLES[i.severity]}`}>
                      {i.code}
                    </span>
                    <span className="font-mono text-xs text-gray-500 shrink-0">
                      L{i.line}:{i.column}
                    </span>
                    <span className="text-xs text-gray-300">{i.message}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
