import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  GripVertical,
  Info,
  Loader2,
  Plus,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import Editor from '@monaco-editor/react';
import '@/lib/monacoSetup';
import { useCreateStrategy, useSaveBuilder, useStrategy } from '@/hooks/useStrategies';
import { apiError } from '@/lib/api';
import { generateStrategyCode, type BuilderError } from '@/lib/builder/codegen';
import {
  emptyConfig,
  INDICATOR_META,
  newId,
  OP_LABELS,
  OUTPUT_LABELS,
  type BuilderConfig,
  type ConditionOp,
  type DirectionLimit,
  type IndicatorBlock,
  type IndicatorKind,
  type IndicatorOutput,
  type Operand,
  type PositionSizing,
  type Rule,
} from '@/lib/builder/types';

/** SHA-256 hex digest（与后端 _source_hash 实现一致）。 */
async function sha256(source: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

const DIRECTION_OPTIONS: { value: DirectionLimit; label: string }[] = [
  { value: 'both', label: '双向' },
  { value: 'long_only', label: '仅做多' },
  { value: 'short_only', label: '仅做空' },
];

const CONDITION_OPS: ConditionOp[] = ['cross_above', 'cross_below', 'greater_than', 'less_than'];

/** 操作数编辑器：收盘价 / 指标输出 / 常量。 */
function OperandEditor({
  value,
  onChange,
  config,
}: {
  value: Operand;
  onChange: (op: Operand) => void;
  config: BuilderConfig;
}) {
  const mode = value.type;
  return (
    <div className="flex items-center gap-1 min-w-0">
      <select
        className="input-dark w-20 text-xs px-1.5 py-1"
        value={mode}
        onChange={(e) => {
          const m = e.target.value;
          if (m === 'close') onChange({ type: 'close' });
          else if (m === 'number') onChange({ type: 'number', value: 70 });
          else {
            const first = config.indicators[0];
            onChange(
              first
                ? {
                    type: 'indicator',
                    indicatorId: first.id,
                    output: INDICATOR_META[first.kind].outputs[0],
                  }
                : { type: 'close' },
            );
          }
        }}
      >
        <option value="close">收盘价</option>
        <option value="indicator">指标</option>
        <option value="number">常量</option>
      </select>
      {value.type === 'indicator' && (
        <>
          <select
            className="input-dark w-28 text-xs px-1.5 py-1 min-w-0"
            value={value.indicatorId}
            onChange={(e) => {
              const ind = config.indicators.find((i) => i.id === e.target.value);
              if (!ind) return;
              onChange({ type: 'indicator', indicatorId: ind.id, output: INDICATOR_META[ind.kind].outputs[0] });
            }}
          >
            {config.indicators.map((ind, i) => (
              <option key={ind.id} value={ind.id}>
                {ind.kind.toLowerCase()}_{i + 1}
              </option>
            ))}
          </select>
          <select
            className="input-dark w-16 text-xs px-1.5 py-1"
            value={value.output}
            onChange={(e) =>
              onChange({ type: 'indicator', indicatorId: value.indicatorId, output: e.target.value as IndicatorOutput })
            }
          >
            {config.indicators
              .filter((i) => i.id === value.indicatorId)
              .flatMap((ind) =>
                INDICATOR_META[ind.kind].outputs.map((o) => (
                  <option key={o} value={o}>
                    {OUTPUT_LABELS[o]}
                  </option>
                )),
              )}
          </select>
        </>
      )}
      {value.type === 'number' && (
        <input
          type="number"
          step="any"
          className="input-dark w-20 text-xs px-1.5 py-1"
          value={value.value}
          onChange={(e) => onChange({ type: 'number', value: Number(e.target.value) })}
        />
      )}
    </div>
  );
}

interface Props {
  mode: 'create' | 'edit';
}

export default function BuilderPage({ mode }: Props) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const strategyQuery = useStrategy(mode === 'edit' ? id : undefined);
  const create = useCreateStrategy();
  const saveBuilder = useSaveBuilder();

  const [config, setConfig] = useState<BuilderConfig>(emptyConfig());
  const [configLoaded, setConfigLoaded] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [savedTip, setSavedTip] = useState(false);
  const [generatedHash, setGeneratedHash] = useState<string | null>(null);
  const [semanticsOpen, setSemanticsOpen] = useState(false);
  const [serverErrors, setServerErrors] = useState<BuilderError[]>([]);
  const [mobileTab, setMobileTab] = useState<'palette' | 'canvas' | 'code'>('canvas');

  // 编辑模式：加载既有 builder_config（无配置 → 空白 + 覆盖警示）
  const strategy = strategyQuery.data;
  useEffect(() => {
    if (mode === 'edit' && strategy && !configLoaded) {
      setConfigLoaded(true);
      setConfig(strategy.builder_config ?? emptyConfig());
    }
  }, [mode, strategy, configLoaded]);

  const dragRef = useRef<{ layer: 'indicators' | 'rules'; index: number } | null>(null);

  // 每次生成代码变化时算 SHA-256（用于脱离检测）
  const generated = useMemo(() => generateStrategyCode(config, name || '可视化构建策略'), [config, name]);
  useEffect(() => {
    sha256(generated.code).then(setGeneratedHash);
  }, [generated.code]);
  const hasErrors = generated.errors.length > 0;

  // P2：合并前端 codegen + 后端 422 返回的结构化错误，用于卡片级高亮
  const combinedErrors = useMemo(
    () => [...generated.errors, ...serverErrors],
    [generated.errors, serverErrors],
  );
  const errorTargetIds = useMemo(
    () => new Set(combinedErrors.filter((e) => e.targetId).map((e) => e.targetId)),
    [combinedErrors],
  );

  // 脱离检测：后端存的 hash 与当前 builder 生成的 hash 不一致 → 源码被手动改过
  // null 情况：旧策略迁移、或还没保存过（首次进入构建器）→ 不强制警告
  const divergence =
    mode === 'edit' && !!strategy?.source_code_hash && generatedHash !== null
      ? strategy.source_code_hash !== generatedHash
      : false;

  const archived = strategy?.status === 'archived';
  const busy = create.isPending || saveBuilder.isPending;

  // ── config 修改工具
  const patch = useCallback((fn: (c: BuilderConfig) => BuilderConfig) => {
    setConfig((c) => fn(structuredClone(c)));
    setSavedTip(false);
    setServerErrors([]);  // config 变化后清除后端错误
  }, []);

  const addIndicator = (kind: IndicatorKind) => {
    const block: IndicatorBlock =
      kind === 'MACD'
        ? { id: newId('ind'), kind, period: 0, fast: 12, slow: 26, signal: 9 }
        : kind === 'BOLL'
          ? { id: newId('ind'), kind, period: 20, std: 2 }
          : { id: newId('ind'), kind, period: kind === 'RSI' || kind === 'ATR' ? 14 : 20 };
    patch((c) => ({ ...c, indicators: [...c.indicators, block] }));
  };

  const updateIndicator = (indId: string, upd: Partial<IndicatorBlock>) => {
    patch((c) => ({
      ...c,
      indicators: c.indicators.map((i) => (i.id === indId ? { ...i, ...upd } : i)),
    }));
  };

  const removeIndicator = (indId: string) => {
    patch((c) => ({
      ...c,
      indicators: c.indicators.filter((i) => i.id !== indId),
      // 引用被删指标的规则操作数回退为收盘价
      rules: c.rules.map((r) => ({
        ...r,
        left: r.left.type === 'indicator' && r.left.indicatorId === indId ? { type: 'close' } : r.left,
        right: r.right.type === 'indicator' && r.right.indicatorId === indId ? { type: 'close' } : r.right,
      })),
    }));
  };

  const addRule = (op: ConditionOp) => {
    const first = config.indicators[0];
    const left: Operand = first
      ? { type: 'indicator', indicatorId: first.id, output: INDICATOR_META[first.kind].outputs[0] }
      : { type: 'close' };
    const rule: Rule = { id: newId('rule'), op, left, right: { type: 'close' }, open: 'buy', close: null };
    patch((c) => ({ ...c, rules: [...c.rules, rule] }));
  };

  const updateRule = (ruleId: string, upd: Partial<Rule>) => {
    patch((c) => ({
      ...c,
      rules: c.rules.map((r) => (r.id === ruleId ? { ...r, ...upd } : r)),
    }));
  };

  const removeRule = (ruleId: string) => {
    patch((c) => ({ ...c, rules: c.rules.filter((r) => r.id !== ruleId) }));
  };

  const onDrop = (layer: 'indicators' | 'rules', index: number) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d || d.layer !== layer || d.index === index) return;
    patch((c) => {
      const arr = [...c[layer]];
      const [moved] = arr.splice(d.index, 1);
      arr.splice(index, 0, moved);
      return { ...c, [layer]: arr };
    });
  };

  const dragProps = (layer: 'indicators' | 'rules', index: number) => ({
    draggable: true,
    onDragStart: () => {
      dragRef.current = { layer, index };
    },
    onDragOver: (e: React.DragEvent) => e.preventDefault(),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      onDrop(layer, index);
    },
  });

  // ── 保存流
  const doSaveCreate = () => {
    if (!name.trim() || hasErrors || busy) return;
    create.mutate(
      {
        name: name.trim(),
        description: description.trim(),
        direction: config.direction,
        symbol_ids: [config.symbol],
        timeframe: config.timeframe,
        source_code: generated.code,
        builder_config: config,
      },
      {
        onSuccess: (s) => {
          setSaveOpen(false);
          navigate(`/strategies/${s.id}/edit`);
        },
      },
    );
  };

  const doSaveEdit = () => {
    if (!id || hasErrors || busy || archived) return;
    saveBuilder.mutate(
      {
        id,
        payload: {
          source_code: generated.code,
          builder_config: config,
          direction: config.direction,
          symbol_ids: [config.symbol],
          timeframe: config.timeframe,
          base_updated_at: strategy?.updated_at ?? null,
        },
      },
      {
        onSuccess: () => {
          setSavedTip(true);
          void strategyQuery.refetch();
        },
        onError: (err) => {
          // 409 冲突：提示刷新
          // 422 builder_config / source_code：解析结构化错误用于卡片高亮
          const detail = apiError(err);
          if (detail.includes('409') || detail.includes('已在其他窗口')) {
            alert('策略已在其他窗口被修改，请刷新后重试。');
            return;
          }
          // 解析后端返回的结构化 builder errors
          const respData = (err as { response?: { data?: { detail?: { errors?: BuilderError[]; type?: string } } } })
            .response?.data?.detail;
          if (respData?.type === 'builder_config' && Array.isArray(respData.errors)) {
            setServerErrors(respData.errors);
          }
        },
      },
    );
  };

  // 覆盖警示：builder_config 缺失（手工策略）OR 源码已脱离（手动改过）
  const showOverwriteWarning =
    mode === 'edit' && (strategy?.builder_config == null || divergence);
  if (mode === 'edit' && strategyQuery.isLoading) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">正在加载策略…</div>
      </div>
    );
  }
  if (mode === 'edit' && (strategyQuery.isError || !strategy)) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body flex items-center justify-between">
          <span className="text-sm text-loss">加载失败：{apiError(strategyQuery.error)}</span>
          <Link to="/strategies" className="btn-secondary">返回策略列表</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3" style={{ height: 'calc(100vh - 104px)' }}>
      {/* 顶部工具条 */}
      <div className="flex items-center gap-3 flex-wrap shrink-0">
        <Link to="/strategies" className="btn-ghost w-9 h-9 p-0 flex items-center justify-center" title="返回策略列表">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-lg font-semibold text-white">可视化策略构建器</h1>
        {mode === 'edit' && strategy && (
          <span className="text-sm text-gray-500">
            编辑「{strategy.name}」
            {archived && <span className="text-loss ml-2">（已归档，只读）</span>}
          </span>
        )}
        <span
          className={`px-2 py-0.5 rounded border text-xs ${
            hasErrors
              ? 'bg-loss/15 text-loss border-loss/40'
              : 'bg-profit/15 text-profit border-profit/40'
          }`}
        >
          {hasErrors ? `${generated.errors.length} 项配置错误` : '配置校验通过'}
        </span>
        {savedTip && (
          <span className="text-xs text-profit flex items-center gap-1">
            <Check className="w-3 h-3" /> 已保存
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {mode === 'create' ? (
            <button
              type="button"
              className="btn-primary px-3 py-1.5"
              disabled={hasErrors || busy}
              onClick={() => setSaveOpen(true)}
            >
              <Save className="w-4 h-4" /> 保存为策略
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary px-3 py-1.5"
              disabled={hasErrors || busy || archived}
              onClick={doSaveEdit}
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              保存（覆盖源码）
            </button>
          )}
          {mode === 'edit' && strategy && (
            <Link to={`/strategies/${strategy.id}/edit`} className="btn-secondary px-3 py-1.5">
              打开编辑器
            </Link>
          )}
        </div>
      </div>

      {(showOverwriteWarning || divergence) && (
        <div className="px-3 py-2 rounded bg-yellow-500/10 border border-yellow-500/30 text-xs text-yellow-400 shrink-0 flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {divergence
            ? '当前源码与积木配置已脱离（源码可能被手动修改过），保存将用构建器生成代码覆盖现有源码。'
            : '该策略不含构建器配置（手工编写），保存将用生成代码覆盖现有源码。'}
        </div>
      )}

      {hasErrors && (
        <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-xs text-loss shrink-0 space-y-0.5">
          {generated.errors.map((e, i) => (
            <p key={i}>· {e.message}</p>
          ))}
        </div>
      )}

      {/* 三栏主体（md 起始终可见；移动端由 tab 切换） */}
      {/* 移动端 tab bar */}
      <div className="md:hidden flex gap-1 px-2 pb-1 border-b border-gray-700/50 shrink-0">
        {[
          { k: 'palette' as const, label: '🧩 积木' },
          { k: 'canvas' as const, label: '📐 画布' },
          { k: 'code' as const, label: '💻 代码' },
        ].map((t) => (
          <button
            key={t.k}
            type="button"
            className={`flex-1 py-1.5 text-xs rounded transition-colors ${
              mobileTab === t.k
                ? 'bg-gray-700 text-white font-medium'
                : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800'
            }`}
            onClick={() => setMobileTab(t.k)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[220px_minmax(0,1fr)_minmax(0,42%)] gap-3">
        {/* 左：积木面板 */}
        <div className={`card flex flex-col overflow-y-auto min-h-0 ${mobileTab === 'palette' ? 'flex' : 'hidden'} md:flex`}>
          <div className="card-body space-y-4 text-sm">
            <div>
              <p className="text-xs text-gray-400 font-medium mb-2">指标块（点击添加）</p>
              <div className="space-y-1.5">
                {(Object.keys(INDICATOR_META) as IndicatorKind[]).map((k) => (
                  <button
                    key={k}
                    type="button"
                    className="w-full text-left px-2.5 py-1.5 rounded border border-gray-700 bg-surface-900 hover:border-blue-500/60 hover:bg-blue-500/10 transition-colors"
                    onClick={() => addIndicator(k)}
                    title={INDICATOR_META[k].desc}
                  >
                    <span className="text-xs text-gray-200">{INDICATOR_META[k].label}</span>
                    <span className="text-2xs text-gray-500 ml-1">{INDICATOR_META[k].desc}</span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-400 font-medium mb-2">条件块（点击新增规则）</p>
              <div className="space-y-1.5">
                {CONDITION_OPS.map((op) => (
                  <button
                    key={op}
                    type="button"
                    className="w-full text-left px-2.5 py-1.5 rounded border border-gray-700 bg-surface-900 hover:border-blue-500/60 hover:bg-blue-500/10 transition-colors text-xs text-gray-200"
                    onClick={() => addRule(op)}
                  >
                    {OP_LABELS[op]}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-2xs text-gray-600 leading-relaxed">
              拖动积木卡片可排序。指标按添加顺序编号（ma_1、ma_2…），规则自上而下逐条评估。
            </p>
          </div>
        </div>

        {/* 中：画布 */}
        <div className={`min-h-0 overflow-y-auto space-y-3 pr-0.5 ${mobileTab === 'canvas' ? 'block' : 'hidden'} md:block`}>
          {/* 全局配置 */}
          <div className="card">
            <div className="card-body grid grid-cols-2 md:grid-cols-4 gap-3">
              <label className="block">
                <span className="text-xs text-gray-400">品种</span>
                <input
                  className="input-dark mt-1 font-mono"
                  value={config.symbol}
                  onChange={(e) => patch((c) => ({ ...c, symbol: e.target.value.toUpperCase() }))}
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-400">周期</span>
                <select
                  className="input-dark mt-1"
                  value={config.timeframe}
                  onChange={(e) => patch((c) => ({ ...c, timeframe: e.target.value }))}
                >
                  {TIMEFRAMES.map((tf) => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-gray-400">方向限制</span>
                <select
                  className="input-dark mt-1"
                  value={config.direction}
                  onChange={(e) => patch((c) => ({ ...c, direction: e.target.value as DirectionLimit }))}
                >
                  {DIRECTION_OPTIONS.map((d) => (
                    <option key={d.value} value={d.value}>{d.label}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-gray-400">仓位模式</span>
                <select
                  className="input-dark mt-1"
                  value={config.sizing?.kind ?? 'legacy'}
                  onChange={(e) => {
                    const v = e.target.value;
                    patch((c) => {
                      if (v === 'legacy') return { ...c, sizing: undefined };
                      if (v === 'fixed') return { ...c, sizing: { kind: 'fixed', volume: c.volume } };
                      return { ...c, sizing: { kind: 'risk_percent', risk_percent: 1, sl_pips: 50 } };
                    });
                  }}
                >
                  <option value="legacy">固定手数（{config.volume}）</option>
                  <option value="fixed">固定手数（可配置）</option>
                  <option value="risk_percent">风险比例</option>
                </select>
              </label>
              {config.sizing?.kind === 'fixed' && (
                <label className="block">
                  <span className="text-xs text-gray-400">固定手数</span>
                  <input
                    type="number" step="0.01" min="0.01"
                    className="input-dark mt-1 font-mono"
                    value={config.sizing.volume}
                    onChange={(e) => patch((c) => ({ ...c, sizing: { ...(c.sizing as PositionSizing), volume: Number(e.target.value) } }))}
                  />
                </label>
              )}
              {config.sizing?.kind === 'risk_percent' && (
                <>
                  <label className="block">
                    <span className="text-xs text-gray-400">风险比例 (%)</span>
                    <input
                      type="number" step="0.1" min="0.1" max="100"
                      className="input-dark mt-1 font-mono"
                      value={config.sizing.risk_percent}
                      onChange={(e) => patch((c) => ({ ...c, sizing: { ...(c.sizing as PositionSizing), risk_percent: Number(e.target.value) } }))}
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-gray-400">用于反推的 SL (pips)</span>
                    <input
                      type="number" step="1" min="1"
                      className="input-dark mt-1 font-mono"
                      value={config.sizing.sl_pips}
                      onChange={(e) => patch((c) => ({ ...c, sizing: { ...(c.sizing as PositionSizing), sl_pips: Number(e.target.value) } }))}
                    />
                  </label>
                </>
              )}
            </div>
          </div>

          {/* 指标层 */}
          <div className="card">
            <div className="card-header flex items-center justify-between">
              <span className="text-xs text-gray-300 font-medium">指标层（{config.indicators.length}）</span>
            </div>
            <div className="card-body space-y-2">
              {config.indicators.length === 0 && (
                <p className="text-xs text-gray-600 py-3 text-center">从左侧面板添加指标块</p>
              )}
              {config.indicators.map((ind, i) => {
                const label =
                  ind.kind === 'MACD'
                    ? `${ind.fast}/${ind.slow}/${ind.signal}`
                    : ind.kind === 'BOLL'
                      ? `${ind.period}×${ind.std}`
                      : String(ind.period);
                const hasErr = errorTargetIds.has(ind.id);
                return (
                  <div
                    key={ind.id}
                    {...dragProps('indicators', i)}
                    className={`flex items-center gap-2 flex-wrap px-2.5 py-2 rounded border ${
                      hasErr
                        ? 'border-loss bg-loss/10'
                        : 'border-gray-700 bg-surface-900'
                    } cursor-grab active:cursor-grabbing`}
                    title={hasErr ? combinedErrors.filter((e) => e.targetId === ind.id).map((e) => e.message).join('\n') : undefined}
                  >
                    <GripVertical className={`w-3.5 h-3.5 ${hasErr ? 'text-loss' : 'text-gray-600'} shrink-0`} />
                    {hasErr && <AlertTriangle className="w-3.5 h-3.5 text-loss shrink-0" />}
                    <span className="text-xs font-mono text-blue-300 shrink-0">
                      {ind.kind.toLowerCase()}_{i + 1}
                    </span>
                    <span className={`text-xs ${hasErr ? 'text-loss/80' : 'text-gray-400'} shrink-0`}>{INDICATOR_META[ind.kind].label}</span>
                    {ind.kind === 'MACD' ? (
                      <>
                        <NumField label="快" value={ind.fast ?? 12} min={2} onChange={(v) => updateIndicator(ind.id, { fast: v })} />
                        <NumField label="慢" value={ind.slow ?? 26} min={3} onChange={(v) => updateIndicator(ind.id, { slow: v })} />
                        <NumField label="信号" value={ind.signal ?? 9} min={2} onChange={(v) => updateIndicator(ind.id, { signal: v })} />
                      </>
                    ) : (
                      <NumField
                        label="周期"
                        value={ind.period}
                        min={2}
                        onChange={(v) => updateIndicator(ind.id, { period: v })}
                      />
                    )}
                    {ind.kind === 'BOLL' && (
                      <NumField
                        label="σ"
                        value={ind.std ?? 2}
                        min={0.1}
                        step={0.1}
                        onChange={(v) => updateIndicator(ind.id, { std: v })}
                      />
                    )}
                    <span className="text-2xs text-gray-600 ml-auto shrink-0">{label}</span>
                    <button
                      type="button"
                      className="btn-ghost p-1 text-loss hover:text-loss-light shrink-0"
                      title="删除指标"
                      onClick={() => removeIndicator(ind.id)}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          {/* 规则层 */}
          <div className="card">
            <div className="card-header flex items-center justify-between">
              <button
                type="button"
                className="flex items-center gap-1 text-xs text-gray-300 font-medium hover:text-gray-200"
                onClick={() => setSemanticsOpen((v) => !v)}
              >
                规则层（{config.rules.length}）
                <Info className="w-3.5 h-3.5 text-gray-500" />
                {semanticsOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>
              {config.rules.length === 0 && config.indicators.length > 0 && (
                <button type="button" className="btn-ghost text-xs" onClick={() => addRule('cross_above')}>
                  <Plus className="w-3 h-3" /> 新增规则
                </button>
              )}
            </div>
            {semanticsOpen && (
              <div className="px-3 py-2 text-2xs text-gray-400 leading-relaxed border-b border-gray-700/50 bg-gray-900/30">
                <p className="mb-1 font-medium text-gray-300">执行语义（线性规则列表）</p>
                <ul className="space-y-0.5 list-disc list-inside">
                  <li>规则按列表顺序 <span className="text-gray-300">自上而下</span> 逐条检查。</li>
                  <li>同一根 K 线内多条规则同时触发时，<span className="text-gray-300">先平后开</span>：先执行所有平仓动作，再执行所有开仓动作。</li>
                  <li>开仓仅在 <span className="text-gray-300">无对应方向持仓</span> 时执行；平仓仅在 <span className="text-gray-300">有对应方向持仓</span> 时执行。</li>
                  <li>若开启了方向限制（如仅做多），所有规则的开仓/平仓动作都会被校验是否匹配方向。</li>
                  <li>订单失败（余额不足、点差过大等）会跳过该订单，不会中断策略运行。</li>
                </ul>
              </div>
            )}
            <div className="card-body space-y-2">
              {config.rules.length === 0 && (
                <p className="text-xs text-gray-600 py-3 text-center">从左侧面板添加条件块（上穿 / 下穿 / 大于 / 小于）</p>
              )}
              {config.rules.map((rule, i) => {
                const hasErr = errorTargetIds.has(rule.id);
                return (
                  <div
                    key={rule.id}
                    {...dragProps('rules', i)}
                    className={`px-2.5 py-2 rounded border ${
                      hasErr
                        ? 'border-loss bg-loss/10'
                        : 'border-gray-700 bg-surface-900'
                    } cursor-grab active:cursor-grabbing space-y-2`}
                    title={hasErr ? combinedErrors.filter((e) => e.targetId === rule.id).map((e) => e.message).join('\n') : undefined}
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <GripVertical className={`w-3.5 h-3.5 ${hasErr ? 'text-loss' : 'text-gray-600'} shrink-0`} />
                      {hasErr && <AlertTriangle className="w-3.5 h-3.5 text-loss shrink-0" />}
                    <span className="text-xs text-gray-500 shrink-0">#{i + 1}</span>
                    <select
                      className="input-dark w-24 text-xs px-1.5 py-1"
                      value={rule.op}
                      onChange={(e) => updateRule(rule.id, { op: e.target.value as ConditionOp })}
                    >
                      {CONDITION_OPS.map((op) => (
                        <option key={op} value={op}>{OP_LABELS[op]}</option>
                      ))}
                    </select>
                    <OperandEditor value={rule.left} onChange={(left) => updateRule(rule.id, { left })} config={config} />
                    <span className="text-xs text-gray-500">与</span>
                    <OperandEditor value={rule.right} onChange={(right) => updateRule(rule.id, { right })} config={config} />
                    <button
                      type="button"
                      className="btn-ghost p-1 text-loss hover:text-loss-light ml-auto shrink-0"
                      title="删除规则"
                      onClick={() => removeRule(rule.id)}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap pl-6">
                    <span className="text-xs text-gray-500 shrink-0">满足时</span>
                    <select
                      className="input-dark w-28 text-xs px-1.5 py-1"
                      value={rule.open ?? ''}
                      onChange={(e) => updateRule(rule.id, { open: (e.target.value || null) as Rule['open'] })}
                    >
                      <option value="">不开仓</option>
                      <option value="buy">开多</option>
                      <option value="sell">开空</option>
                    </select>
                    <select
                      className="input-dark w-28 text-xs px-1.5 py-1"
                      value={rule.close ?? ''}
                      onChange={(e) => updateRule(rule.id, { close: (e.target.value || null) as Rule['close'] })}
                    >
                      <option value="">不平仓</option>
                      <option value="buy">平多</option>
                      <option value="sell">平空</option>
                    </select>
                    <span className="text-2xs text-gray-600">开仓仅在有持仓可平/无持仓时执行，先平后开</span>
                  </div>
                </div>
                );
              })}
            </div>
          </div>

          {/* 风控层（P3） */}
          <div className="card">
            <div className="card-header flex items-center justify-between">
              <span className="text-xs text-gray-300 font-medium">风控层（{config.risk_blocks?.length ?? 0}）</span>
              <div className="flex gap-1.5">
                <button
                  type="button" className="btn-ghost text-xs px-2 py-1 hover:text-loss"
                  onClick={() => patch((c) => ({ ...c, risk_blocks: [...(c.risk_blocks ?? []), { id: newId('risk'), kind: 'stop_loss', pips: 50 }] }))}
                >+ 止损</button>
                <button
                  type="button" className="btn-ghost text-xs px-2 py-1 hover:text-profit"
                  onClick={() => patch((c) => ({ ...c, risk_blocks: [...(c.risk_blocks ?? []), { id: newId('risk'), kind: 'take_profit', pips: 100 }] }))}
                >+ 止盈</button>
                <button
                  type="button" className="btn-ghost text-xs px-2 py-1"
                  onClick={() => patch((c) => ({ ...c, risk_blocks: [...(c.risk_blocks ?? []), { id: newId('risk'), kind: 'trailing_stop', pips: 30 }] }))}
                  title="跟随极值的移动止损"
                >+ 移动止损</button>
                <button
                  type="button" className="btn-ghost text-xs px-2 py-1"
                  onClick={() => patch((c) => ({ ...c, risk_blocks: [...(c.risk_blocks ?? []), { id: newId('risk'), kind: 'max_holding_time', minutes: 1440 }] }))}
                  title="超过指定时长强制平仓"
                >+ 最长持仓</button>
                <button
                  type="button" className="btn-ghost text-xs px-2 py-1"
                  onClick={() => patch((c) => ({ ...c, risk_blocks: [...(c.risk_blocks ?? []), { id: newId('risk'), kind: 'max_position_count', count: 1 }] }))}
                  title="最多同时持仓数（开仓前检查）"
                >+ 最大持仓</button>
              </div>
            </div>
            <div className="card-body space-y-2">
              {(config.risk_blocks?.length ?? 0) === 0 && (
                <p className="text-xs text-gray-600 py-2 text-center">暂未配置风控。上方按钮快速添加止损/止盈/移动止损/最长持仓。</p>
              )}
              {(config.risk_blocks ?? []).map((block) => {
                const hasErr = errorTargetIds.has(block.id);
                const isSL = block.kind === 'stop_loss';
                const isTP = block.kind === 'take_profit';
                const isTrail = block.kind === 'trailing_stop';
                const isMaxHold = block.kind === 'max_holding_time';
                const isMaxCount = block.kind === 'max_position_count';
                return (
                  <div
                    key={block.id}
                    className={`flex items-center gap-2 flex-wrap px-2.5 py-2 rounded border ${
                      hasErr ? 'border-loss bg-loss/10' : 'border-gray-700 bg-surface-900'
                    }`}
                    title={hasErr ? combinedErrors.filter((e) => e.targetId === block.id).map((e) => e.message).join('\n') : undefined}
                  >
                    <span className={`text-xs font-medium ${
                      isSL ? 'text-loss' : isTP ? 'text-profit' : isTrail ? 'text-yellow-400' : isMaxHold ? 'text-cyan-400' : isMaxCount ? 'text-purple-400' : 'text-gray-400'
                    }`}>
                      {isSL ? '🛑 止损' : isTP ? '🎯 止盈' : isTrail ? '〰️ 移动止损' : isMaxHold ? '⏱ 最长持仓' : isMaxCount ? '🔢 最大持仓' : '🔒 账户级（待接入）'}
                    </span>
                    {(isSL || isTP || isTrail) && (
                      <>
                        <span className="text-xs text-gray-500">pips</span>
                        <input
                          type="number" step="1" min="1"
                          className="input-dark w-20 text-xs px-1.5 py-1 font-mono"
                          value={block.pips}
                          onChange={(e) => patch((c) => ({
                            ...c,
                            risk_blocks: (c.risk_blocks ?? []).map((b) =>
                              b.id === block.id ? { ...b, pips: Number(e.target.value) } : b,
                            ),
                          }))}
                        />
                      </>
                    )}
                    {isMaxHold && (
                      <>
                        <span className="text-xs text-gray-500">分钟</span>
                        <input
                          type="number" step="10" min="1"
                          className="input-dark w-24 text-xs px-1.5 py-1 font-mono"
                          value={block.minutes}
                          onChange={(e) => patch((c) => ({
                            ...c,
                            risk_blocks: (c.risk_blocks ?? []).map((b) =>
                              b.id === block.id ? { ...b, minutes: Number(e.target.value) } : b,
                            ),
                          }))}
                        />
                      </>
                    )}
                    {isMaxCount && (
                      <>
                        <span className="text-xs text-gray-500">个</span>
                        <input
                          type="number" step="1" min="1"
                          className="input-dark w-20 text-xs px-1.5 py-1 font-mono"
                          value={block.count}
                          onChange={(e) => patch((c) => ({
                            ...c,
                            risk_blocks: (c.risk_blocks ?? []).map((b) =>
                              b.id === block.id ? { ...b, count: Math.max(1, Math.floor(Number(e.target.value))) } : b,
                            ),
                          }))}
                        />
                      </>
                    )}
                    <span className="text-2xs text-gray-500 ml-auto">
                      {isSL ? '触及止损价平仓' : isTP ? '触及止盈价平仓' : isTrail ? '跟随最高/最低价回撤平仓' : isMaxHold ? '持仓超过指定时长强制平仓' : isMaxCount ? '开仓前检查，超过则跳过新仓' : '账户级风控，待接入账户数据后生效'}
                    </span>
                    <button
                      type="button"
                      className="btn-ghost p-1 text-loss hover:text-loss-light"
                      title="删除风控"
                      onClick={() => patch((c) => ({
                        ...c,
                        risk_blocks: (c.risk_blocks ?? []).filter((b) => b.id !== block.id),
                      }))}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* 右：代码预览 */}
        <div className={`card flex flex-col overflow-hidden min-h-0 ${mobileTab === 'code' ? 'flex' : 'hidden'} md:flex`}>
          <div className="px-3 py-2 border-b border-gray-700/50 flex items-center justify-between shrink-0">
            <span className="text-xs text-gray-400">生成代码预览（只读）</span>
          </div>
          <div className="flex-1 min-h-0">
            <Editor
              path="builder-preview.py"
              language="python"
              theme="mt5-dark"
              value={generated.code}
              options={{
                readOnly: true,
                fontSize: 12,
                fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                automaticLayout: true,
                tabSize: 4,
                padding: { top: 8 },
              }}
            />
          </div>
        </div>
      </div>

      {/* 新建保存对话框 */}
      {saveOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="card w-full max-w-md">
            <div className="card-header">
              <h3 className="text-sm font-medium text-gray-200">保存为策略</h3>
              <button type="button" className="btn-ghost p-1" onClick={() => setSaveOpen(false)}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="card-body space-y-3">
              <label className="block">
                <span className="text-xs text-gray-400">名称 *</span>
                <input
                  className="input-dark mt-1"
                  value={name}
                  maxLength={120}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="如：双均线趋势"
                />
              </label>
              <label className="block">
                <span className="text-xs text-gray-400">描述</span>
                <textarea
                  className="input-dark mt-1"
                  rows={2}
                  maxLength={2000}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="策略思路简述"
                />
              </label>
              <p className="text-2xs text-gray-500">
                将创建策略「{config.symbol} · {config.timeframe}」并写入生成源码与积木配置，
                之后可在编辑器中继续手改扩展。
              </p>
              {create.isError && (
                <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
                  {apiError(create.error)}
                </div>
              )}
              <div className="flex justify-end gap-2">
                <button type="button" className="btn-ghost" onClick={() => setSaveOpen(false)}>取消</button>
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!name.trim() || create.isPending}
                  onClick={doSaveCreate}
                >
                  {create.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                  创建并打开编辑器
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function NumField({
  label,
  value,
  min,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  step?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-2xs text-gray-500 shrink-0">
      {label}
      <input
        type="number"
        min={min}
        step={step}
        className="input-dark w-16 text-xs px-1.5 py-0.5 font-mono"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
