import { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useCreateStrategy, useTemplates } from '@/hooks/useStrategies';
import { useSymbols } from '@/hooks/useMarket';
import { apiError } from '@/lib/api';
import { useWatchlist } from '@/lib/watchlist';
import { resolveSchema, seedValues } from '@/lib/strategyParams';
import StrategyParamsPanel from '@/components/strategies/StrategyParamsPanel';
import type { StrategyDirection, StrategyTemplateSummary } from '@/types/api';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

const CATEGORY_LABELS: Record<string, string> = {
  trend: '趋势',
  grid: '网格',
  mean_reversion: '均值回归',
  risk_management: '风控',
};

const DIRECTION_OPTIONS: { value: StrategyDirection; label: string }[] = [
  { value: 'both', label: '双向' },
  { value: 'long_only', label: '仅做多' },
  { value: 'short_only', label: '仅做空' },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function StrategyCreateDialog({ open, onClose }: Props) {
  const navigate = useNavigate();
  const templates = useTemplates();
  const create = useCreateStrategy();

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [direction, setDirection] = useState<StrategyDirection>('both');
  const [timeframe, setTimeframe] = useState('H1');
  const [symbols, setSymbols] = useState('EURUSD');
  const [tags, setTags] = useState('');
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, unknown>>({});

  // 自选列表下拉：点选自选品种追加到「品种」输入框
  const { favSet } = useWatchlist();
  const allSymbols = useSymbols('', '').data?.items ?? [];
  const favSymbols = allSymbols.filter((s) => favSet.has(s.name));
  const [favPick, setFavPick] = useState('');
  const addFavSymbol = (sym: string) => {
    setFavPick('');
    if (!sym) return;
    const current = symbols
      .split(/[,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!current.includes(sym)) setSymbols([...current, sym].join(','));
  };

  const selectedTpl: StrategyTemplateSummary | undefined = templates.data?.items.find(
    (t) => t.id === templateId,
  );
  const schema = selectedTpl ? resolveSchema(Object.keys(selectedTpl.parameters ?? {})) : null;

  useEffect(() => {
    setParams(schema ? seedValues(schema) : {});
     
  }, [templateId, schema]);

  if (!open) return null;

  const submit = () => {
    if (!name.trim()) return;
    create.mutate(
      {
        name: name.trim(),
        description: description.trim(),
        direction,
        timeframe,
        symbol_ids: symbols
          .split(/[,，\s]+/)
          .map((s) => s.trim())
          .filter(Boolean),
        tags: tags
          .split(/[,，\s]+/)
          .map((t) => t.trim())
          .filter(Boolean),
        template_id: templateId,
        parameters: schema ? params : undefined,
      },
      {
        onSuccess: (s) => {
          onClose();
          navigate(`/strategies/${s.id}/edit`);
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="card-header sticky top-0 bg-surface-800 z-10">
          <h3 className="text-sm font-medium text-gray-200">新建策略</h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="card-body space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">名称 *</span>
              <input
                className="input-dark mt-1"
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：EURUSD 双均线"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">方向</span>
              <select
                className="input-dark mt-1"
                value={direction}
                onChange={(e) => setDirection(e.target.value as StrategyDirection)}
              >
                {DIRECTION_OPTIONS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block">
            <span className="text-xs text-gray-400">描述</span>
            <textarea
              className="input-dark mt-1"
              rows={2}
              value={description}
              maxLength={2000}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="策略思路简述"
            />
          </label>

          <div className="grid grid-cols-3 gap-3">
            <label className="block">
              <span className="text-xs text-gray-400">品种（逗号分隔）</span>
              <input
                className="input-dark mt-1 font-mono"
                value={symbols}
                onChange={(e) => setSymbols(e.target.value.toUpperCase())}
                placeholder="EURUSD, XAUUSD"
              />
              <select
                className="input-dark mt-1 font-mono"
                value={favPick}
                title="自选列表"
                onChange={(e) => addFavSymbol(e.target.value)}
              >
                <option value="">
                  {favSymbols.length > 0
                    ? '＋ 从自选列表添加品种…'
                    : '自选列表为空 — 去行情页点 ☆ 添加'}
                </option>
                {favSymbols.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">周期</span>
              <select
                className="input-dark mt-1"
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>
                    {tf}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">标签（逗号分隔）</span>
              <input
                className="input-dark mt-1"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="趋势, 测试"
              />
            </label>
          </div>

          <div>
            <span className="text-xs text-gray-400">从模板创建（可选）</span>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setTemplateId(null)}
                className={`text-left px-3 py-2 rounded border text-sm transition-colors ${
                  templateId === null
                    ? 'border-blue-500/60 bg-blue-500/10 text-blue-300'
                    : 'border-gray-700 bg-surface-900 text-gray-400 hover:border-gray-600'
                }`}
              >
                空白策略
                <p className="text-xs text-gray-500 mt-0.5">仅骨架回调</p>
              </button>
              {(templates.data?.items ?? []).map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTemplateId(t.id)}
                  className={`text-left px-3 py-2 rounded border text-sm transition-colors ${
                    templateId === t.id
                      ? 'border-blue-500/60 bg-blue-500/10 text-blue-300'
                      : 'border-gray-700 bg-surface-900 text-gray-400 hover:border-gray-600'
                  }`}
                >
                  {t.name}
                  <span className="ml-1 text-xs text-gray-600">
                    [{CATEGORY_LABELS[t.category] ?? t.category}]
                  </span>
                  <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{t.description}</p>
                </button>
              ))}
            </div>
            {templates.isLoading && (
              <p className="text-xs text-gray-500 mt-2">
                <Loader2 className="w-3 h-3 inline animate-spin" /> 加载模板…
              </p>
            )}
            {selectedTpl &&
              (schema ? (
                <div className="mt-3 rounded border border-blue-500/30 bg-surface-900 p-3">
                  <p className="text-xs text-gray-400 mb-2">
                    将预填「{selectedTpl.name}」源码，可在下方调整策略参数（保存到策略级参数）：
                  </p>
                  <StrategyParamsPanel groups={schema} values={params} onChange={setParams} />
                </div>
              ) : (
                <p className="text-xs text-gray-500 mt-2">
                  将预填「{selectedTpl.name}」源码，参数默认值：
                  {Object.entries(selectedTpl.parameters)
                    .map(([k, v]) => `${k}=${v}`)
                    .join('，')}
                </p>
              ))}
          </div>

          {create.isError && (
            <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
              {apiError(create.error)}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-ghost" onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!name.trim() || create.isPending}
              onClick={submit}
            >
              {create.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              创建并编辑
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
