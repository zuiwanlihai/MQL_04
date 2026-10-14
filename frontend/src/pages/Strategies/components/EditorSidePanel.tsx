import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import BacktestCreateDialog from '@/components/backtest/BacktestCreateDialog';
import StrategyParamsPanel from '@/components/strategies/StrategyParamsPanel';
import { MODE_LABELS, useBacktests } from '@/hooks/useBacktests';
import { useFreezeVersion, useUpdateStrategy } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import { resolveSchema, seedValues, V4_PARAM_KEYS } from '@/lib/strategyParams';
import type { StrategyDetail, ValidationResult } from '@/types/api';

interface Props {
  strategy: StrategyDetail;
  dirty: boolean;
  saving: boolean;
  validation: ValidationResult | null;
  onJumpToIssue: (line: number) => void;
  onSaved: (updatedAt: string) => void;
}

const SEVERITY_STYLES = {
  error: 'text-loss',
  warning: 'text-yellow-400',
  info: 'text-gray-400',
} as const;

export default function EditorSidePanel({
  strategy,
  dirty,
  saving,
  validation,
  onJumpToIssue,
  onSaved,
}: Props) {
  const [tab, setTab] = useState<'meta' | 'params' | 'lint' | 'backtest'>('meta');
  const [changelog, setChangelog] = useState('');
  const freeze = useFreezeVersion();

  const issues = validation?.issues ?? [];
  const errorCount = issues.filter((i) => i.severity === 'error').length;
  const warnCount = issues.filter((i) => i.severity === 'warning').length;

  return (
    <aside className="card flex flex-col overflow-hidden h-full">
      <div className="card-header">
        <div className="flex gap-1">
          {(['meta', 'params', 'lint', 'backtest'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              className={`px-2.5 py-1 rounded text-xs transition-colors ${
                tab === t
                  ? 'bg-blue-500/20 text-blue-300'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {t === 'meta'
                ? '元信息'
                : t === 'params'
                  ? '参数'
                  : t === 'lint'
                    ? `校验${errorCount ? ` (${errorCount})` : ''}`
                    : '回测'}
            </button>
          ))}
        </div>
      </div>

      <div className="card-body flex-1 overflow-y-auto space-y-4">
        {tab === 'meta' && (
          <>
            <div className="space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-500">名称</span>
                <span className="text-gray-200">{strategy.name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">状态</span>
                <span className="text-gray-200">{strategy.status}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">当前版本</span>
                <span className="font-mono text-gray-200">
                  {strategy.current_version == null ? '未冻结' : `v${strategy.current_version}`}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">品种</span>
                <span className="font-mono text-gray-200">{strategy.symbol_ids.join(', ') || '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">周期</span>
                <span className="font-mono text-gray-200">{strategy.timeframe}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-500">语言</span>
                <span className="text-gray-200">{strategy.language}</span>
              </div>
            </div>

            <div className="border-t border-gray-700/50 pt-3 space-y-2">
              <label className="block text-xs text-gray-400">
                版本说明（冻结时写入 changelog）
                <textarea
                  className="input-dark mt-1"
                  rows={2}
                  maxLength={200}
                  value={changelog}
                  onChange={(e) => setChangelog(e.target.value)}
                  placeholder="本次冻结的改动说明"
                />
              </label>
              <button
                type="button"
                className="btn-success w-full"
                disabled={freeze.isPending || saving || dirty}
                title={dirty ? '请先保存代码再冻结版本' : '校验通过后冻结为不可变版本'}
                onClick={() =>
                  freeze.mutate(
                    { id: strategy.id, body: { changelog } },
                    {
                      onSuccess: (v) => {
                        setChangelog('');
                        onSaved(new Date().toISOString());
                        window.alert(`已冻结为 v${v.version}`);
                      },
                    },
                  )
                }
              >
                {freeze.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                冻结版本
              </button>
              {dirty && (
                <p className="text-xs text-yellow-400">有未保存修改，请先保存（Ctrl+S）再冻结。</p>
              )}
              {freeze.isError && (
                <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-xs text-loss">
                  冻结失败：{apiError(freeze.error)}
                  {(freeze.error as unknown as { response?: { data?: { detail?: ValidationResult } } })
                ?.response?.data?.detail?.issues && (
                    <ul className="mt-1 list-disc list-inside">
                      {(
                        freeze.error as unknown as {
                          response: { data: { detail: ValidationResult } };
                        }
                      ).response.data.detail.issues
                        .filter((i) => i.severity === 'error')
                        .slice(0, 5)
                        .map((i) => (
                          <li key={`${i.code}-${i.line}`}>
                            {i.code} L{i.line}: {i.message}
                          </li>
                        ))}
                    </ul>
                  )}
                </div>
              )}
            </div>

            <div className="border-t border-gray-700/50 pt-3">
              <p className="text-xs text-gray-600 leading-relaxed">
                提示：MVP 阶段沙箱执行与回测随回测切片（M4）上线。当前编辑器提供 SDK v1 静态校验、
                版本管理与模板库；「部署/回测」按钮将在 M4 接入。
              </p>
            </div>
          </>
        )}

        {tab === 'params' && <ParamsTab strategy={strategy} />}

        {tab === 'lint' && (
          <div className="space-y-1">
            {validation == null && (
              <p className="text-sm text-gray-500">输入代码后自动校验…</p>
            )}
            {validation && issues.length === 0 && (
              <p className="text-sm text-profit">✓ 通过 SDK v1 静态校验</p>
            )}
            {validation && issues.length > 0 && (
              <p className="text-xs text-gray-500 mb-2">
                {errorCount} 错误 · {warnCount} 警告（错误代码无法冻结版本）
              </p>
            )}
            {issues.map((i, idx) => (
              <button
                key={`${i.code}-${i.line}-${idx}`}
                type="button"
                className="w-full text-left px-2 py-1.5 rounded hover:bg-surface-700/60 transition-colors"
                onClick={() => onJumpToIssue(i.line)}
              >
                <div className="flex items-center gap-2 text-xs">
                  <span className={`font-mono ${SEVERITY_STYLES[i.severity]}`}>{i.code}</span>
                  <span className="text-gray-500">L{i.line}</span>
                </div>
                <p className="text-xs text-gray-300 mt-0.5">{i.message}</p>
              </button>
            ))}
          </div>
        )}

        {tab === 'backtest' && <BacktestTab strategy={strategy} />}
      </div>
    </aside>
  );
}

function BacktestTab({ strategy }: { strategy: StrategyDetail }) {
  const [open, setOpen] = useState(false);
  const bt = useBacktests({ page: 1, pageSize: 10, strategyId: strategy.id });
  const items = bt.data?.items ?? [];
  return (
    <div className="space-y-3">
      <button type="button" className="btn-primary w-full" onClick={() => setOpen(true)}>
        新建回测
      </button>
      {bt.isLoading ? (
        <p className="text-xs text-gray-500 text-center py-4">加载回测任务…</p>
      ) : items.length === 0 ? (
        <p className="text-xs text-gray-600 text-center py-4">该策略还没有回测任务。</p>
      ) : (
        <ul className="space-y-2">
          {items.map((t) => (
            <li key={t.id} className="rounded border border-gray-700/60 bg-surface-900 px-2.5 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-gray-300">
                  {MODE_LABELS[t.execution_mode]}档 · {t.symbol}/{t.timeframe}
                </span>
                <span className="text-xs text-gray-500">{t.status}</span>
              </div>
              <div className="flex items-center justify-between mt-1">
                <span className="text-xs text-gray-600 font-mono">
                  {new Date(t.created_at).toLocaleString('zh-CN')}
                </span>
                {t.status === 'completed' ? (
                  <Link to={`/backtest/${t.id}/report`} className="text-xs text-blue-400 hover:text-blue-300">
                    报告 →
                  </Link>
                ) : (
                  <span className="text-xs text-gray-600">{t.progress_percent}%</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      <BacktestCreateDialog open={open} onClose={() => setOpen(false)} presetStrategyId={strategy.id} />
    </div>
  );
}

function ParamsTab({ strategy }: { strategy: StrategyDetail }) {
  const update = useUpdateStrategy();
  const [values, setValues] = useState<Record<string, unknown>>({});

  const schema = useMemo(() => {
    const keys = strategy.parameters ? Object.keys(strategy.parameters) : undefined;
    if (keys && keys.length) return resolveSchema(keys);
    // 回退：源码含 v4.0 特征键 → 用完整 schema
    if (/min_score/.test(strategy.source_code) && /boll_dev/.test(strategy.source_code)) {
      return resolveSchema(V4_PARAM_KEYS);
    }
    return null;
  }, [strategy.parameters, strategy.source_code]);

  useEffect(() => {
    if (schema) setValues(seedValues(schema, strategy.parameters));
  }, [schema, strategy.parameters]);

  if (!schema) {
    return (
      <p className="text-xs text-gray-600 leading-relaxed">
        当前策略无结构化参数 schema（仅 v4.0 家族支持面板配置）。其余策略仍可在回测/模拟/实盘弹窗以 JSON 形式配置参数。
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <StrategyParamsPanel groups={schema} values={values} onChange={setValues} />
      {update.isError && (
        <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-xs text-loss">
          保存失败：{apiError(update.error)}
        </div>
      )}
      {update.isSuccess && <p className="text-xs text-profit">参数已保存</p>}
      <button
        type="button"
        className="btn-primary w-full"
        disabled={update.isPending}
        onClick={() => update.mutate({ id: strategy.id, body: { parameters: values } })}
      >
        {update.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
        保存参数
      </button>
    </div>
  );
}
