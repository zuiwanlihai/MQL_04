import { useState } from 'react';
import { ListChecks, Plus, Trash2 } from 'lucide-react';
import {
  useAlertRules,
  useCreateAlertRule,
  useDeleteAlertRule,
  useUpdateAlertRule,
} from '@/hooks/useAlertRules';
import { useAccounts } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';
import type {
  AlertEvent,
  AlertOperator,
  AlertRuleCreateBody,
  AlertRuleType,
  CustomMetric,
} from '@/types/api';

const TYPE_LABELS: Record<AlertRuleType, string> = {
  drawdown_pct: '回撤比例（实盘/虚拟下单权益自峰值）',
  daily_loss_pct: '当日亏损比例（实盘）',
  gateway_offline: '网关断线持续时长',
  trade_filled: '新成交提醒（实盘/虚拟下单）',
  custom_metric: '自定义指标比较（实盘镜像字段）',
};

const THRESHOLD_HINT: Record<AlertRuleType, { label: string; unit: string; def: number }> = {
  drawdown_pct: { label: '回撤阈值', unit: '%', def: 10 },
  daily_loss_pct: { label: '日亏阈值', unit: '%', def: 5 },
  gateway_offline: { label: '断线时长', unit: '秒', def: 60 },
  trade_filled: { label: '（无需阈值）', unit: '', def: 0 },
  custom_metric: { label: '比较阈值', unit: '', def: 0 },
};

const OPERATOR_LABELS: Record<AlertOperator, string> = {
  lt: '< 小于',
  lte: '≤ 小于等于',
  gt: '> 大于',
  gte: '≥ 大于等于',
};

const METRIC_LABELS: Record<CustomMetric, string> = {
  equity: '权益 equity',
  balance: '余额 balance',
  unrealized_pnl: '浮动盈亏 unrealized_pnl',
  margin_free: '可用保证金 margin_free',
};

const LEVEL_LABELS: Record<AlertEvent['level'], string> = {
  info: '提示',
  warn: '警告',
  error: '错误',
  critical: '严重',
};

const LEVELS: AlertEvent['level'][] = ['info', 'warn', 'error', 'critical'];

export default function AlertRulesCard() {
  const rules = useAlertRules();
  const accounts = useAccounts();
  const create = useCreateAlertRule();
  const update = useUpdateAlertRule();
  const remove = useDeleteAlertRule();

  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('');
  const [type, setType] = useState<AlertRuleType>('drawdown_pct');
  const [scope, setScope] = useState<'global' | 'account'>('global');
  const [accountId, setAccountId] = useState('');
  const [threshold, setThreshold] = useState('10');
  const [operator, setOperator] = useState<AlertOperator>('gte');
  const [metric, setMetric] = useState<CustomMetric>('equity');
  const [level, setLevel] = useState<AlertEvent['level']>('warn');
  const [cooldown, setCooldown] = useState('300');

  const items = rules.data ?? [];
  const accountItems = accounts.data?.items ?? [];
  const mutationError = create.error ?? update.error ?? remove.error;
  const th = THRESHOLD_HINT[type];

  function submit() {
    const body: AlertRuleCreateBody = {
      name: name.trim(),
      type,
      scope,
      account_id: scope === 'account' ? accountId || null : null,
      threshold: Number(threshold) || 0,
      operator,
      metric: type === 'custom_metric' ? metric : null,
      level,
      cooldown_seconds: Math.max(0, Number(cooldown) || 0),
    };
    create.mutate(body, {
      onSuccess: () => {
        setFormOpen(false);
        setName('');
      },
    });
  }

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <ListChecks className="w-4 h-4 text-yellow-400" /> 告警规则
        </h2>
        <button type="button" className="btn-secondary" onClick={() => setFormOpen((v) => !v)}>
          <Plus className="w-4 h-4" /> 新建规则
        </button>
      </div>
      <div className="card-body space-y-4">
        <p className="text-xs text-gray-600">
          用户自定义的告警条件（回撤 / 当日亏损 / 网关断线 / 成交提醒 / 自定义指标）。触发后写入站内告警并按通知渠道级别推送；同一对象在冷却期内只提醒一次。
        </p>

        {formOpen && (
          <div className="rounded-md border border-gray-700/50 bg-surface-900 p-4 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div>
                <p className="text-xs text-gray-500 mb-1">规则名称</p>
                <input
                  className="input-dark"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="如：回撤超10%告警"
                  maxLength={100}
                />
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">规则类型</p>
                <select
                  className="input-dark"
                  value={type}
                  onChange={(e) => {
                    const t = e.target.value as AlertRuleType;
                    setType(t);
                    setThreshold(String(THRESHOLD_HINT[t].def));
                  }}
                >
                  {(Object.keys(TYPE_LABELS) as AlertRuleType[]).map((t) => (
                    <option key={t} value={t}>{TYPE_LABELS[t]}</option>
                  ))}
                </select>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">作用范围</p>
                <select
                  className="input-dark"
                  value={scope}
                  onChange={(e) => setScope(e.target.value as 'global' | 'account')}
                >
                  <option value="global">全部任务</option>
                  <option value="account">指定 MT5 账户</option>
                </select>
              </div>
              {scope === 'account' && (
                <div>
                  <p className="text-xs text-gray-500 mb-1">绑定账户</p>
                  <select
                    className="input-dark"
                    value={accountId}
                    onChange={(e) => setAccountId(e.target.value)}
                  >
                    <option value="">请选择账户…</option>
                    {accountItems.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.login}@{a.server}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {type === 'custom_metric' && (
                <>
                  <div>
                    <p className="text-xs text-gray-500 mb-1">指标字段</p>
                    <select
                      className="input-dark"
                      value={metric}
                      onChange={(e) => setMetric(e.target.value as CustomMetric)}
                    >
                      {(Object.keys(METRIC_LABELS) as CustomMetric[]).map((m) => (
                        <option key={m} value={m}>{METRIC_LABELS[m]}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <p className="text-xs text-gray-500 mb-1">比较方式</p>
                    <select
                      className="input-dark"
                      value={operator}
                      onChange={(e) => setOperator(e.target.value as AlertOperator)}
                    >
                      {(Object.keys(OPERATOR_LABELS) as AlertOperator[]).map((o) => (
                        <option key={o} value={o}>{OPERATOR_LABELS[o]}</option>
                      ))}
                    </select>
                  </div>
                </>
              )}
              {type !== 'trade_filled' && (
                <div>
                  <p className="text-xs text-gray-500 mb-1">
                    {th.label}
                    {th.unit ? `（${th.unit}）` : ''}
                  </p>
                  <input
                    className="input-dark"
                    type="number"
                    min={0}
                    step="any"
                    value={threshold}
                    onChange={(e) => setThreshold(e.target.value)}
                  />
                </div>
              )}
              <div>
                <p className="text-xs text-gray-500 mb-1">告警级别</p>
                <select
                  className="input-dark"
                  value={level}
                  onChange={(e) => setLevel(e.target.value as AlertEvent['level'])}
                >
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>{LEVEL_LABELS[l]}</option>
                  ))}
                </select>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">冷却时间（秒）</p>
                <input
                  className="input-dark"
                  type="number"
                  min={0}
                  step={30}
                  value={cooldown}
                  onChange={(e) => setCooldown(e.target.value)}
                />
              </div>
            </div>
            {create.isError && <p className="text-xs text-loss">{apiError(create.error)}</p>}
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-primary"
                disabled={
                  create.isPending ||
                  name.trim().length === 0 ||
                  (scope === 'account' && !accountId) ||
                  ((type === 'drawdown_pct' || type === 'daily_loss_pct') && !(Number(threshold) > 0)) ||
                  (type === 'gateway_offline' && Number(threshold) < 5)
                }
                onClick={submit}
              >
                保存
              </button>
              <button type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>取消</button>
            </div>
          </div>
        )}

        {mutationError && (
          <div className="text-sm text-loss bg-loss/10 border border-loss/30 rounded-md px-3 py-2">
            {apiError(mutationError)}
          </div>
        )}

        {rules.isError && <div className="text-sm text-loss">{apiError(rules.error)}</div>}

        {items.length === 0 && !rules.isLoading ? (
          <p className="text-sm text-gray-600 py-4 text-center">尚未创建任何告警规则。</p>
        ) : (
          <ul className="divide-y divide-gray-700/50">
            {items.map((r) => (
              <li key={r.id} className="py-3 flex items-center gap-3 flex-wrap">
                <span className="text-sm text-gray-200 flex-1 min-w-40 truncate" title={r.name}>
                  {r.name}
                </span>
                <span className="px-2 py-0.5 rounded border text-xs bg-surface-700 text-gray-300 border-gray-600 shrink-0">
                  {TYPE_LABELS[r.type]?.split('（')[0] ?? r.type}
                </span>
                <span className="text-xs text-gray-500 shrink-0">
                  {r.type === 'trade_filled'
                    ? '每笔新成交'
                    : r.type === 'custom_metric'
                      ? `${r.metric} ${OPERATOR_LABELS[r.operator].split(' ')[0]} ${r.threshold}`
                      : `${r.threshold}${r.type === 'gateway_offline' ? 's' : '%'}`}
                  {r.scope === 'account' && ' · 指定账户'}
                </span>
                <span className="text-xs text-gray-500 shrink-0">冷却 {r.cooldown_seconds}s</span>
                <select
                  className="input-dark w-24 text-xs py-1"
                  value={r.level}
                  onChange={(e) =>
                    update.mutate({ id: r.id, level: e.target.value as AlertEvent['level'] })
                  }
                >
                  {LEVELS.map((l) => (
                    <option key={l} value={l}>{LEVEL_LABELS[l]}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className={`text-xs px-2 py-1 rounded border transition-colors ${
                    r.enabled
                      ? 'text-profit border-profit/40 hover:bg-profit/10'
                      : 'text-gray-500 border-gray-600 hover:bg-surface-700'
                  }`}
                  disabled={update.isPending}
                  title={r.enabled ? '点击停用' : '点击启用'}
                  onClick={() => update.mutate({ id: r.id, enabled: !r.enabled })}
                >
                  {r.enabled ? '启用中' : '已停用'}
                </button>
                <button
                  type="button"
                  className="text-loss hover:text-loss-light text-xs disabled:opacity-50"
                  disabled={remove.isPending}
                  title="删除该告警规则"
                  aria-label={`删除告警规则 ${r.name}`}
                  onClick={() => {
                    if (window.confirm(`删除告警规则「${r.name}」？`)) remove.mutate(r.id);
                  }}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
