import type { ParamField, ParamGroup } from '@/lib/strategyParams';

interface Props {
  groups: ParamGroup[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
}

/** 可复用分组参数表单：按 schema 渲染输入并产出 typed values。 */
export default function StrategyParamsPanel({ groups, values, onChange }: Props) {
  const setField = (key: string, value: unknown) => {
    onChange({ ...values, [key]: value });
  };

  return (
    <div className="space-y-4">
      {groups.map((group) => (
        <section key={group.id} className="space-y-2">
          <h4 className="text-xs font-medium text-gray-300 border-b border-gray-700/60 pb-1">
            {group.label}
          </h4>
          <div className="grid grid-cols-2 gap-2">
            {group.fields.map((field) => (
              <Field
                key={field.key}
                field={field}
                value={values[field.key]}
                onChange={(v) => setField(field.key, v)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function Field({
  field,
  value,
  onChange,
}: {
  field: ParamField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (field.type === 'bool') {
    return (
      <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer py-1">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>{field.label}</span>
      </label>
    );
  }

  if (field.type === 'enum') {
    return (
      <label className="block">
        <span className="text-xs text-gray-400">{field.label}</span>
        <select
          className="input-dark mt-1"
          value={String(value ?? field.default ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    );
  }

  const numeric = field.type === 'int' || field.type === 'float';
  const strValue = value === undefined || value === null ? '' : String(value);

  // −/+ 步进：以当前值（空则取默认）± step，夹在 min/max 内
  const stepValue = (dir: 1 | -1) => {
    const parsed = typeof value === 'number' ? value
      : typeof value === 'string' && value !== '' ? Number(value) : NaN;
    const base = Number.isFinite(parsed)
      ? parsed
      : typeof field.default === 'number' ? field.default : 0;
    const st = field.step ?? (field.type === 'int' ? 1 : 0.1);
    let n = base + dir * st;
    if (field.type === 'int') n = Math.round(n);
    if (field.min != null) n = Math.max(field.min, n);
    if (field.max != null) n = Math.min(field.max, n);
    const decimals = (String(st).split('.')[1] ?? '').length;
    onChange(Number(n.toFixed(Math.min(decimals, 6))));
  };

  if (numeric) {
    return (
      <label className="block">
        <span className="text-xs text-gray-400">
          {field.label}
          {field.unit ? <span className="text-gray-600">（{field.unit}）</span> : null}
        </span>
        <div className="mt-1 flex">
          <button
            type="button"
            aria-label={`减小 ${field.label}`}
            className="px-2.5 shrink-0 rounded-l border border-gray-700 bg-surface-800 text-gray-300 hover:bg-surface-700 hover:text-white active:bg-surface-600"
            onClick={() => stepValue(-1)}
          >
            −
          </button>
          <input
            type="number"
            className="input-dark rounded-none border-l-0 border-r-0 text-center [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            value={strValue}
            min={field.min}
            max={field.max}
            step={field.step ?? (field.type === 'int' ? 1 : 'any')}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === '') {
                onChange('');
                return;
              }
              const n = Number(raw);
              if (Number.isNaN(n)) return;
              onChange(field.type === 'int' ? Math.round(n) : n);
            }}
          />
          <button
            type="button"
            aria-label={`增大 ${field.label}`}
            className="px-2.5 shrink-0 rounded-r border border-gray-700 bg-surface-800 text-gray-300 hover:bg-surface-700 hover:text-white active:bg-surface-600"
            onClick={() => stepValue(1)}
          >
            +
          </button>
        </div>
        {field.hint && <p className="text-xs text-gray-600 mt-0.5">{field.hint}</p>}
      </label>
    );
  }

  return (
    <label className="block">
      <span className="text-xs text-gray-400">
        {field.label}
        {field.unit ? <span className="text-gray-600">（{field.unit}）</span> : null}
      </span>
      <input
        type="text"
        className="input-dark mt-1"
        value={strValue}
        onChange={(e) => onChange(e.target.value)}
      />
      {field.hint && <p className="text-xs text-gray-600 mt-0.5">{field.hint}</p>}
    </label>
  );
}