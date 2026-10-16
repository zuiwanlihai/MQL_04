import { Loader2, Play, Plus, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAnalyses, useCreateOptimization } from '@/hooks/useBacktests';
import { useStrategy } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import { resolveSchema, type ParamField } from '@/lib/strategyParams';
import type {
  OptimizationObjective,
  OptimizationResult,
  OptimizationResultRow,
} from '@/types/api';

const OBJ_LABELS: Record<OptimizationObjective, string> = {
  sharpe_ratio: '夏普比率',
  annual_return_percent: '年化收益',
  total_return_percent: '总收益率',
  calmar_ratio: '卡玛比率',
  profit_factor: '盈亏比',
};

const pctTone = (v: number | null | undefined) =>
  v == null ? 'text-gray-100' : v >= 0 ? 'text-profit' : 'text-loss';
const fmtPct = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}%`);
const fmtNum = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d));

interface SpaceRow {
  name: string;
  values: string; // comma separated, parsed on submit
}

const EMPTY_PARAMS: Record<string, unknown> = {};

const IDENT_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Parse "1, 2.5, fast" → typed candidate values (numeric when possible). */
function parseValues(raw: string): (number | string)[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map((s) => {
      const n = Number(s);
      return Number.isNaN(n) ? s : n;
    });
}

/** Suggestion chip: one declared strategy parameter with ready-made candidates. */
interface ParamSuggestion {
  key: string;
  label: string;
  currentText: string;
  candidates: string; // comma list, '' = not grid-searchable
}

function formatCandidate(n: number, type: ParamField['type'], step: number): string {
  if (type === 'int') return String(Math.round(n));
  const decimals = Math.max(1, (String(step).split('.')[1] ?? '').length);
  const snapped = Math.round(n / step) * step;
  return snapped.toFixed(decimals).replace(/0+$/, '').replace(/\.$/, '');
}

/**
 * Build a default candidate list around the frozen value: [v-step, v, v+step],
 * widened to ±2 steps when min/max clipping leaves fewer than 2 distinct values.
 * bool/enum return all options; free text is not grid-searchable.
 */
function buildCandidates(field: ParamField, current: unknown): string {
  if (field.type === 'bool') return 'false, true';
  if (field.type === 'enum') return (field.options ?? []).map((o) => o.value).join(', ');
  const v = Number(current);
  if (!Number.isFinite(v)) return '';
  const step = field.step && field.step > 0 ? field.step : v !== 0 ? Math.abs(v) * 0.1 : 1;
  const lo = field.min ?? -Infinity;
  const hi = field.max ?? Infinity;
  const clampFmt = (n: number) =>
    formatCandidate(Math.min(hi, Math.max(lo, n)), field.type, step);
  let values = [...new Set([v - step, v, v + step].map(clampFmt))];
  if (values.length < 3) {
    // Boundary value (e.g. v == min): widen to ±2 steps so both directions
    // are explored where the bounds allow.
    values = [...new Set([v - 2 * step, v - step, v, v + step, v + 2 * step].map(clampFmt))];
  }
  return values.join(', ');
}

function formatCurrent(v: unknown): string {
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (v == null) return '—';
  return String(v);
}

/**
 * Derive the clickable default-parameter list from the task's frozen parameters
 * (full set when created via the structured form), falling back to the strategy's
 * saved params. Known template families get labels/bounds/steps via resolveSchema;
 * unknown numeric/bool keys still produce usable suggestions.
 */
function buildSuggestions(defaults: Record<string, unknown>): ParamSuggestion[] {
  const keys = Object.keys(defaults);
  if (keys.length === 0) return [];
  const known = new Map<string, ParamField>();
  for (const group of resolveSchema(keys) ?? []) {
    for (const f of group.fields) known.set(f.key, f);
  }
  const out: ParamSuggestion[] = [];
  for (const key of keys) {
    let field = known.get(key);
    if (!field) {
      const v = defaults[key];
      if (typeof v === 'boolean') {
        field = { key, label: key, type: 'bool', default: v };
      } else if (typeof v === 'number' && Number.isFinite(v)) {
        field = {
          key,
          label: key,
          type: Number.isInteger(v) ? 'int' : 'float',
          default: v,
          step: v !== 0 ? Math.abs(v) * 0.1 : 1,
        };
      }
    }
    if (!field || field.type === 'text') continue;
    const current = defaults[key] ?? field.default;
    const candidates = buildCandidates(field, current);
    // Need ≥2 distinct candidates for a grid search (e.g. min==max edge case).
    if (candidates.split(', ').length < 2) continue;
    out.push({ key, label: field.label, currentText: formatCurrent(current), candidates });
  }
  return out;
}

function ParamSpaceEditor({
  rows,
  onChange,
  suggestions,
}: {
  rows: SpaceRow[];
  onChange: (rows: SpaceRow[]) => void;
  suggestions: ParamSuggestion[];
}) {
  const update = (i: number, patch: Partial<SpaceRow>) =>
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  // Click a default-param chip: fill the first blank row, otherwise append.
  const pick = (s: ParamSuggestion) => {
    if (rows.some((r) => r.name.trim() === s.key) || rows.length >= 4) return;
    const blankIdx = rows.findIndex((r) => !r.name.trim() && !r.values.trim());
    if (blankIdx >= 0) {
      onChange(rows.map((r, i) => (i === blankIdx ? { name: s.key, values: s.candidates } : r)));
    } else {
      onChange([...rows, { name: s.key, values: s.candidates }]);
    }
  };

  const addedKeys = new Set(rows.map((r) => r.name.trim()).filter(Boolean));
  const roomLeft = rows.length < 4;

  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            className="input w-40 font-mono"
            placeholder="参数名（如 fast_ma）"
            list="optim-param-keys"
            value={row.name}
            onChange={(e) => update(i, { name: e.target.value })}
          />
          <span className="text-gray-600">=</span>
          <input
            className="input flex-1 min-w-48 font-mono"
            placeholder="候选值，逗号分隔（如 5, 10, 20）"
            value={row.values}
            onChange={(e) => update(i, { values: e.target.value })}
          />
          <button
            type="button"
            className="btn-ghost p-1.5 text-loss"
            title="删除该参数"
            onClick={() => onChange(rows.filter((_, idx) => idx !== i))}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
      <datalist id="optim-param-keys">
        {suggestions.map((s) => (
          <option key={s.key} value={s.key}>
            {s.label}（当前 {s.currentText}）
          </option>
        ))}
      </datalist>
      {suggestions.length > 0 && (
        <div className="space-y-1 pt-0.5">
          <p className="text-[11px] text-gray-500">
            策略默认参数（点击添加，候选值已按当前值与步长自动生成；最多 4 个参数）
          </p>
          <div className="flex flex-wrap gap-1.5">
            {suggestions.map((s) => {
              const added = addedKeys.has(s.key);
              const disabled = added || !roomLeft;
              return (
                <button
                  key={s.key}
                  type="button"
                  disabled={disabled}
                  title={
                    added
                      ? `${s.key} 已添加`
                      : `${s.key}（${s.label}）当前 = ${s.currentText}；候选：${s.candidates}`
                  }
                  onClick={() => pick(s)}
                  className={`px-2 py-0.5 rounded border text-[11px] font-mono transition-colors ${
                    added
                      ? 'border-profit/40 bg-profit/10 text-profit cursor-default'
                      : 'border-gray-600 bg-surface-700 text-gray-300 hover:border-blue-400 hover:text-blue-300'
                  } disabled:opacity-70`}
                >
                  {s.key}
                  <span className={added ? 'text-profit/70 ml-1' : 'text-gray-500 ml-1'}>
                    {added ? '✓' : s.currentText}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      {rows.length < 4 && (
        <button
          type="button"
          className="btn-secondary px-2.5 py-1 text-xs flex items-center gap-1"
          onClick={() => onChange([...rows, { name: '', values: '' }])}
        >
          <Plus className="w-3.5 h-3.5" /> 添加参数
        </button>
      )}
    </div>
  );
}

function ResultTable({ result }: { result: OptimizationResult }) {
  return (
    <div className="overflow-x-auto">
      <table className="table-dark text-xs">
        <thead>
          <tr>
            <th>#</th>
            <th>参数组合</th>
            <th className="text-right">{OBJ_LABELS[result.objective]}</th>
            <th className="text-right">总收益</th>
            <th className="text-right">最大回撤</th>
            <th className="text-right">夏普</th>
            <th className="text-right">交易</th>
            <th className="text-right">报告</th>
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row: OptimizationResultRow, rank) => (
            <tr key={row.index}>
              <td className="text-gray-400">
                {rank + 1}
                {rank === 0 && result.best && (
                  <span className="ml-1.5 px-1.5 py-0.5 rounded bg-profit/15 text-profit border border-profit/40 text-[10px]">
                    最优
                  </span>
                )}
              </td>
              <td>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(row.params).map(([k, v]) => (
                    <span
                      key={k}
                      className="px-1.5 py-0.5 rounded bg-surface-700 border border-gray-600 font-mono text-gray-300"
                    >
                      {k}={String(v)}
                    </span>
                  ))}
                </div>
              </td>
              <td className={`text-right font-mono ${pctTone(row.objective)}`}>
                {fmtNum(row.objective, 4)}
              </td>
              <td className={`text-right font-mono ${pctTone(row.total_return_percent)}`}>
                {fmtPct(row.total_return_percent)}
              </td>
              <td className="text-right font-mono text-loss">{fmtPct(row.max_drawdown_percent)}</td>
              <td className="text-right font-mono text-gray-200">{fmtNum(row.sharpe_ratio)}</td>
              <td className="text-right font-mono text-gray-400">{row.total_trades ?? '—'}</td>
              <td className="text-right">
                <Link
                  to={`/backtest/${row.task_id}/report`}
                  className="text-blue-400 hover:text-blue-300"
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

export default function OptimizationPanel({
  taskId,
  paramSeed,
  strategyId,
}: {
  taskId: string;
  /** Frozen parameters of this task (full declared set when form-created). */
  paramSeed?: Record<string, unknown> | null;
  /** Used to fall back to the strategy's saved params when the task omits keys. */
  strategyId?: string;
}) {
  const q = useAnalyses(taskId, true);
  const analyses = q.data?.items ?? [];
  const create = useCreateOptimization(taskId);
  const seedEmpty = !paramSeed || Object.keys(paramSeed).length === 0;
  // Only fetch the strategy when the task carries no frozen parameter keys.
  const strategyQ = useStrategy(seedEmpty ? strategyId : undefined);
  const [rows, setRows] = useState<SpaceRow[]>([{ name: '', values: '' }]);
  const [method, setMethod] = useState<'grid' | 'random'>('grid');
  const [samples, setSamples] = useState(20);
  const [objective, setObjective] = useState<OptimizationObjective>('sharpe_ratio');

  // Prefer the task's frozen values; fall back to the strategy's saved defaults.
  const defaults = seedEmpty
    ? (strategyQ.data?.parameters ?? EMPTY_PARAMS)
    : (paramSeed as Record<string, unknown>);
  const suggestions = useMemo(() => buildSuggestions(defaults), [defaults]);

  const latest = analyses.find((a) => a.kind === 'optimization');
  const res = (latest?.result ?? null) as OptimizationResult | null;
  const children = (latest?.params?.children ?? []) as { index: number }[];

  // Validation + combo estimate, recomputed on render.
  const parsed = rows
    .filter((r) => r.name.trim() && r.values.trim())
    .map((r) => ({ name: r.name.trim(), values: parseValues(r.values) }));
  const invalidName = parsed.some((p) => !IDENT_RE.test(p.name));
  const shortValues = parsed.some((p) => p.values.length < 2);
  const totalCombos = parsed.reduce((acc, p) => acc * p.values.length, 1);
  const overLimit = totalCombos > 100;
  const estimated = method === 'grid' ? totalCombos : Math.min(samples, totalCombos);
  const canSubmit =
    parsed.length > 0 && !invalidName && !shortValues && !overLimit && !create.isPending &&
    (method === 'grid' || (samples >= 1 && samples <= totalCombos));

  const submit = () => {
    create.mutate({
      space: parsed,
      method,
      ...(method === 'random' ? { samples } : {}),
      objective,
    });
  };

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-200">参数优化</h3>
        <span className="text-xs text-gray-500">网格 / 随机搜索，按目标指标排名（每个组合一个子回测）</span>
      </div>
      <div className="card-body space-y-3">
        <ParamSpaceEditor rows={rows} onChange={setRows} suggestions={suggestions} />
        <div className="flex flex-wrap items-end gap-2 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">搜索方式</span>
            <select
              className="input w-32"
              value={method}
              onChange={(e) => setMethod(e.target.value as 'grid' | 'random')}
            >
              <option value="grid">网格（全组合）</option>
              <option value="random">随机采样</option>
            </select>
          </label>
          {method === 'random' && (
            <label className="flex flex-col gap-1">
              <span className="text-xs text-gray-500">采样次数</span>
              <input
                type="number"
                className="input w-24"
                min={1}
                max={100}
                value={samples}
                onChange={(e) => setSamples(Number(e.target.value) || 20)}
              />
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-xs text-gray-500">目标指标（降序）</span>
            <select
              className="input w-36"
              value={objective}
              onChange={(e) => setObjective(e.target.value as OptimizationObjective)}
            >
              {(Object.keys(OBJ_LABELS) as OptimizationObjective[]).map((k) => (
                <option key={k} value={k}>
                  {OBJ_LABELS[k]}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn-primary flex items-center gap-1.5 disabled:opacity-50"
            disabled={!canSubmit}
            onClick={submit}
          >
            {create.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            开始优化（{parsed.length > 0 && !overLimit ? estimated : 0} 个子回测）
          </button>
        </div>
        {invalidName && (
          <p className="text-xs text-loss">参数名必须是合法标识符（字母/下划线开头）</p>
        )}
        {shortValues && <p className="text-xs text-loss">每个参数至少 2 个候选值</p>}
        {overLimit && <p className="text-xs text-loss">组合总数 {totalCombos} 超过上限 100</p>}
        {create.isError && <p className="text-sm text-loss">{apiError(create.error)}</p>}
        {latest?.status === 'running' && (
          <p className="text-sm text-blue-300">
            子回测执行中（{children.length} 个组合，完成后按{OBJ_LABELS[objective]}自动排名聚合）…
          </p>
        )}
        {latest?.status === 'failed' && (
          <p className="text-sm text-loss whitespace-pre-wrap break-all">{latest.error ?? '优化失败'}</p>
        )}
        {res && latest?.status === 'completed' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="px-2 py-0.5 rounded border border-gray-600 bg-surface-700 text-gray-300">
                {res.method === 'grid' ? '网格全组合' : '随机采样'} · {res.rows.length} 组
              </span>
              {res.best && (
                <span className="px-2 py-0.5 rounded border border-profit/40 bg-profit/15 text-profit">
                  最优：{Object.entries(res.best.params)
                    .map(([k, v]) => `${k}=${String(v)}`)
                    .join(' · ')}
                </span>
              )}
            </div>
            <ResultTable result={res} />
            <p className="text-xs text-gray-500">
              提示：目标指标在单一历史窗口上排名最高 ≠ 样本外稳健，建议对最优参数再跑 Walk-Forward 验证。
            </p>
          </div>
        )}
        {!res && latest?.status !== 'running' && latest?.status !== 'failed' && (
          <p className="text-xs text-gray-500">
            基于本任务冻结的配置（品种/区间/成本/撮合档位），只替换参数重跑。参数名需与策略
            ctx.declare(params=…) 声明的键一致，未声明的参数将被忽略。
          </p>
        )}
      </div>
    </div>
  );
}
