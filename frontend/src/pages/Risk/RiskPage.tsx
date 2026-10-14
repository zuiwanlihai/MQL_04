import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  Lock,
  Plus,
  ShieldAlert,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import {
  APPLY_TO_LABELS,
  RISK_ACTION_LABELS,
  RISK_TYPE_LABELS,
  RISK_TYPE_UNITS,
  useCreateRiskRule,
  useDeleteRiskRule,
  useLiveRuns,
  useReAdmit,
  useRiskEvents,
  useRiskRules,
  useUnfreezeAccount,
  useUpdateRiskRule,
} from '@/hooks/useLive';
import { useAccounts } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';
import { relativeTime } from '@/lib/market';
import { useAuthStore } from '@/store/useAuthStore';
import type { RiskRuleAction, RiskRuleApplyTo, RiskRuleType } from '@/types/api';

const RULE_TYPES = Object.keys(RISK_TYPE_LABELS) as RiskRuleType[];
const RULE_ACTIONS = Object.keys(RISK_ACTION_LABELS) as RiskRuleAction[];

const SCOPE_LABELS: Record<string, string> = {
  global: '全局',
  account: '账户',
  run: '任务',
};

const ACTION_STYLES: Record<RiskRuleAction, string> = {
  reject: 'bg-surface-700 text-gray-300 border-gray-600',
  halt_open_today: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  close_all_and_stop: 'bg-loss/15 text-loss border-loss/40',
};

const APPLY_TO_STYLES: Record<string, string> = {
  ai: 'border-blue-500/40 bg-blue-500/10 text-blue-300',
  manual: 'border-purple-500/40 bg-purple-500/10 text-purple-300',
  both: 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300',
};

export default function RiskPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const canEdit = isAdmin || user?.role === 'trader';

  const accounts = useAccounts();
  const [accountId, setAccountId] = useState('');
  const [page, setPage] = useState(1);

  const rules = useRiskRules(accountId || undefined);
  const events = useRiskEvents({ accountId: accountId || undefined, page });
  const createRule = useCreateRiskRule();
  const updateRule = useUpdateRiskRule();
  const deleteRule = useDeleteRiskRule();
  const unfreeze = useUnfreezeAccount();
  const readmit = useReAdmit();
  const runs = useLiveRuns({ page: 1, pageSize: 100 });

  const [formOpen, setFormOpen] = useState(false);
  const [fType, setFType] = useState<RiskRuleType>('daily_max_loss_pct');
  const [fThreshold, setFThreshold] = useState('5');
  const [fCooldownHours, setFCooldownHours] = useState('4');
  const [fWindowMinutes, setFWindowMinutes] = useState('5');
  const [fApplyTo, setFApplyTo] = useState<RiskRuleApplyTo>('both');
  const [fAction, setFAction] = useState<RiskRuleAction>('halt_open_today');
  const [fNote, setFNote] = useState('');

  const [unfreezeTarget, setUnfreezeTarget] = useState<string | null>(null);
  const [unfreezeNote, setUnfreezeNote] = useState('');
  const [readmitTarget, setReadmitTarget] = useState<string | null>(null);
  const [readmitNote, setReadmitNote] = useState('');

  const frozenAccounts = (accounts.data?.items ?? []).filter((a) => a.trade_frozen);
  const demotedRuns = (runs.data?.items ?? []).filter((r) => r.demoted);
  const mutationError =
    createRule.error ?? updateRule.error ?? deleteRule.error ?? unfreeze.error ?? readmit.error;

  function submitRule() {
    const threshold = Number(fThreshold);
    if (!Number.isFinite(threshold) || threshold <= 0) return;
    const isCooldown = fType === 'consecutive_loss_cooldown';
    const cooldownMinutes = isCooldown ? Math.round(Number(fCooldownHours) * 60) : undefined;
    if (isCooldown && (!cooldownMinutes || cooldownMinutes <= 0)) return;
    const isRate = fType === 'order_rate_window';
    const windowMinutes = isRate ? Math.round(Number(fWindowMinutes)) : undefined;
    if (isRate && (!windowMinutes || windowMinutes <= 0)) return;
    const scope = accountId ? 'account' : 'global';
    createRule.mutate(
      {
        scope,
        account_id: accountId || undefined,
        type: fType,
        threshold,
        cooldown_minutes: cooldownMinutes,
        window_minutes: windowMinutes,
        apply_to: fApplyTo,
        action: fAction,
        note: fNote,
      },
      { onSuccess: () => setFormOpen(false) },
    );
  }

  // 阈值列展示：连续亏损冷却「3 笔 · 休 4 小时」；时间窗口限频「1 次 · 每 5 分钟」
  const thresholdCell = (r: { type: string; threshold: number; cooldown_minutes?: number | null; window_minutes?: number | null }) => (
    <>
      {r.threshold}
      {RISK_TYPE_UNITS[r.type] && <span className="text-gray-500 ml-1 text-xs">{RISK_TYPE_UNITS[r.type]}</span>}
      {r.type === 'consecutive_loss_cooldown' && r.cooldown_minutes != null && (
        <span className="text-gray-500 ml-1 text-xs">· 休 {r.cooldown_minutes % 60 === 0 ? `${r.cooldown_minutes / 60} 小时` : `${r.cooldown_minutes} 分钟`}</span>
      )}
      {r.type === 'order_rate_window' && r.window_minutes != null && (
        <span className="text-gray-500 ml-1 text-xs">· 每 {r.window_minutes} 分钟</span>
      )}
    </>
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <ShieldAlert className="w-5 h-5 text-loss" /> 风控中心
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          账户级规则 · 事前风控触发记录 · 对账冻结解冻 · 自动降级人工复核（全部操作留审计）
        </p>
      </div>

      {mutationError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(mutationError)}</div>
        </div>
      )}

      {/* 账户选择 */}
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-sm text-gray-400">账户</label>
        <select
          className="input-dark w-72"
          value={accountId}
          onChange={(e) => {
            setAccountId(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部 / 全局规则</option>
          {(accounts.data?.items ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.login} · {a.name || a.server} {a.trade_frozen ? '（已冻结）' : ''}
            </option>
          ))}
        </select>
        {canEdit && (
          <button type="button" className="btn-primary" onClick={() => setFormOpen((v) => !v)}>
            <Plus className="w-4 h-4" /> 新建规则
          </button>
        )}
      </div>

      {/* 规则表单 */}
      {formOpen && canEdit && (
        <div className="card">
          <div className="card-body grid grid-cols-1 sm:grid-cols-4 gap-3">
            <div>
              <p className="text-xs text-gray-500 mb-1">规则类型</p>
              <select className="input-dark" value={fType} onChange={(e) => setFType(e.target.value as RiskRuleType)}>
                {(RULE_TYPES as string[]).filter((t) => t !== 'manual_lot_cap' && t !== 'reconcile_frozen').map((t) => (
                  <option key={t} value={t}>{RISK_TYPE_LABELS[t]}</option>
                ))}
              </select>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">
                {fType === 'consecutive_loss_cooldown'
                  ? '连续亏损笔数（>0）'
                  : fType === 'order_rate_window'
                    ? '窗口内最多开仓次数（>0）'
                    : `阈值（>0${RISK_TYPE_UNITS[fType] ? `，单位：${RISK_TYPE_UNITS[fType]}` : ''}）`}
              </p>
              <div className="relative">
                <input
                  className="input-dark w-full pr-12"
                  value={fThreshold}
                  onChange={(e) => setFThreshold(e.target.value)}
                />
                {RISK_TYPE_UNITS[fType] && (
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-500 pointer-events-none">
                    {RISK_TYPE_UNITS[fType]}
                  </span>
                )}
              </div>
            </div>
            {fType === 'consecutive_loss_cooldown' && (
              <div>
                <p className="text-xs text-gray-500 mb-1">休息时长（&gt;0）</p>
                <div className="relative">
                  <input
                    className="input-dark w-full pr-12"
                    value={fCooldownHours}
                    onChange={(e) => setFCooldownHours(e.target.value)}
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-500 pointer-events-none">
                    小时
                  </span>
                </div>
              </div>
            )}
            {fType === 'order_rate_window' && (
              <div>
                <p className="text-xs text-gray-500 mb-1">时间窗口（&gt;0，平仓不计入）</p>
                <div className="relative">
                  <input
                    className="input-dark w-full pr-14"
                    value={fWindowMinutes}
                    onChange={(e) => setFWindowMinutes(e.target.value)}
                  />
                  <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-500 pointer-events-none">
                    分钟
                  </span>
                </div>
              </div>
            )}
            <div>
              <p className="text-xs text-gray-500 mb-1">触发动作</p>
              <select className="input-dark" value={fAction} onChange={(e) => setFAction(e.target.value as RiskRuleAction)}>
                {RULE_ACTIONS.map((a) => (
                  <option key={a} value={a}>{RISK_ACTION_LABELS[a]}</option>
                ))}
              </select>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">适用范围</p>
              <select className="input-dark" value={fApplyTo} onChange={(e) => setFApplyTo(e.target.value as RiskRuleApplyTo)}>
                <option value="both">AI+手动</option>
                <option value="ai">AI（策略自动下单）</option>
                <option value="manual">手动（手动下单）</option>
              </select>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">备注</p>
              <input
                className="input-dark"
                placeholder="留空自动生成"
                value={fNote}
                onChange={(e) => setFNote(e.target.value)}
                maxLength={200}
              />
            </div>
            <div className="sm:col-span-4 flex items-center gap-2">
              <button type="button" className="btn-primary" disabled={createRule.isPending} onClick={submitRule}>
                保存（{accountId ? '账户级' : '全局'}）
              </button>
              <button type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>取消</button>
            </div>
          </div>
        </div>
      )}

      {/* 规则表 */}
      <div className="card overflow-hidden">
        <div className="card-header">
          <h2 className="text-sm font-medium text-gray-300">风控规则</h2>
        </div>
        <table className="table-dark">
          <thead>
            <tr>
              <th>类型</th>
              <th className="w-20">作用域</th>
              <th className="text-right">阈值</th>
              <th className="w-24">适用</th>
              <th>动作</th>
              <th className="w-20">状态</th>
              <th>备注</th>
              {canEdit && <th className="w-28 text-right">操作</th>}
            </tr>
          </thead>
          <tbody>
            {(rules.data ?? []).map((r) => (
              <tr key={r.id}>
                <td className="text-gray-200 text-sm">{RISK_TYPE_LABELS[r.type] ?? r.type}</td>
                <td className="text-xs text-gray-400">{SCOPE_LABELS[r.scope] ?? r.scope}</td>
                <td className="text-right font-mono text-gray-300">{thresholdCell(r)}</td>
                <td>
                  <span className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${APPLY_TO_STYLES[r.apply_to ?? 'both']}`}>
                    {APPLY_TO_LABELS[r.apply_to ?? 'both']}
                  </span>
                </td>
                <td>
                  <span className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${ACTION_STYLES[r.action]}`}>
                    {RISK_ACTION_LABELS[r.action]}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    disabled={!canEdit || updateRule.isPending}
                    className={`text-xs ${r.enabled ? 'text-profit' : 'text-gray-500'} disabled:cursor-default`}
                    title={canEdit ? '点击切换启用状态' : ''}
                    onClick={() => canEdit && updateRule.mutate({ id: r.id, enabled: !r.enabled })}
                  >
                    {r.enabled ? '启用' : '停用'}
                  </button>
                </td>
                <td className="text-xs text-gray-500 max-w-52 truncate" title={r.note}>{r.note || '—'}</td>
                {canEdit && (
                  <td className="text-right">
                    <button
                      type="button"
                      className="text-loss hover:text-loss-light disabled:opacity-50"
                      disabled={deleteRule.isPending}
                      title="删除规则"
                      onClick={() => {
                        if (window.confirm('删除该风控规则？操作将记入审计。')) deleteRule.mutate(r.id);
                      }}
                    >
                      <Trash2 className="w-4 h-4 inline" />
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {(rules.data ?? []).length === 0 && (
          <div className="card-body text-center text-gray-500 py-8">该范围暂无风控规则。</div>
        )}
      </div>

      {/* 冻结账户 + 降级复核 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="card-header">
            <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <Lock className="w-4 h-4 text-yellow-400" /> 对账冻结账户
            </h2>
          </div>
          <div className="card-body space-y-3">
            {frozenAccounts.length === 0 ? (
              <p className="text-sm text-gray-600">没有处于冻结状态的账户。</p>
            ) : (
              frozenAccounts.map((a) => (
                <div key={a.id} className="border border-yellow-500/30 bg-yellow-500/5 rounded-md p-3">
                  <p className="text-sm text-gray-200">
                    {a.login} · {a.name || a.server}
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    对账不一致已冻结自动交易，需管理员复核后解冻（UNFREEZE 二次确认 + 审计）。
                  </p>
                  {isAdmin ? (
                    unfreezeTarget === a.id ? (
                      <div className="mt-2 space-y-2">
                        <input
                          className="input-dark"
                          placeholder="复核意见（≥8 字，审计留痕）"
                          value={unfreezeNote}
                          onChange={(e) => setUnfreezeNote(e.target.value)}
                        />
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={unfreeze.isPending || unfreezeNote.trim().length < 8}
                            onClick={() =>
                              unfreeze.mutate(
                                { id: a.id, note: unfreezeNote.trim() },
                                { onSuccess: () => { setUnfreezeTarget(null); setUnfreezeNote(''); } },
                              )
                            }
                          >
                            确认解冻（UNFREEZE）
                          </button>
                          <button type="button" className="btn-ghost" onClick={() => setUnfreezeTarget(null)}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <button type="button" className="btn-secondary mt-2" onClick={() => setUnfreezeTarget(a.id)}>
                        复核并解冻
                      </button>
                    )
                  ) : (
                    <p className="text-xs text-gray-600 mt-2">仅管理员可执行解冻。</p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-header">
            <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-loss" /> 自动降级复核
            </h2>
          </div>
          <div className="card-body space-y-3">
            {demotedRuns.length === 0 ? (
              <p className="text-sm text-gray-600">没有处于降级状态的实盘任务。</p>
            ) : (
              demotedRuns.map((r) => (
                <div key={r.id} className="border border-loss/30 bg-loss/5 rounded-md p-3">
                  <p className="text-sm text-gray-200">
                    <Link to={`/trading/live/${r.id}`} className="text-blue-400 hover:text-blue-300">
                      {r.symbol} / {r.timeframe}
                    </Link>
                    <span className="text-xs text-gray-500 ml-2">状态：{r.status}</span>
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    触发全平仓+停止级风控已自动降级，恢复实盘资格需管理员复核（READMIT 二次确认 + 审计）。
                  </p>
                  {isAdmin ? (
                    readmitTarget === r.id ? (
                      <div className="mt-2 space-y-2">
                        <input
                          className="input-dark"
                          placeholder="复核意见（≥8 字，审计留痕）"
                          value={readmitNote}
                          onChange={(e) => setReadmitNote(e.target.value)}
                        />
                        <div className="flex gap-2">
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={readmit.isPending || readmitNote.trim().length < 8}
                            onClick={() =>
                              readmit.mutate(
                                { id: r.id, note: readmitNote.trim() },
                                { onSuccess: () => { setReadmitTarget(null); setReadmitNote(''); } },
                              )
                            }
                          >
                            确认恢复资格（READMIT）
                          </button>
                          <button type="button" className="btn-ghost" onClick={() => setReadmitTarget(null)}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <button type="button" className="btn-secondary mt-2" onClick={() => setReadmitTarget(r.id)}>
                        复核并恢复
                      </button>
                    )
                  ) : (
                    <p className="text-xs text-gray-600 mt-2">仅管理员可执行降级复核。</p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* 风控事件 */}
      <div className="card overflow-hidden">
        <div className="card-header flex items-center justify-between">
          <h2 className="text-sm font-medium text-gray-300">风控触发事件</h2>
          {events.data && (
            <span className="text-xs text-gray-500">共 {events.data.total} 条</span>
          )}
        </div>
        <table className="table-dark">
          <thead>
            <tr>
              <th>时间</th>
              <th>规则</th>
              <th>来源</th>
              <th className="text-right">阈值 / 实测</th>
              <th>执行动作</th>
              <th className="w-24">关联</th>
            </tr>
          </thead>
          <tbody>
            {(events.data?.items ?? []).map((e) => (
              <tr key={e.id}>
                <td className="text-xs text-gray-500 whitespace-nowrap" title={e.created_at}>
                  {relativeTime(e.created_at)}
                </td>
                <td className="text-sm text-gray-200">{RISK_TYPE_LABELS[e.rule_type] ?? e.rule_type}</td>
                <td className="text-xs text-gray-400">
                  {e.source === 'live' ? '实盘' : e.source === 'paper' ? '模拟' : e.source === 'manual' ? '手动' : e.source === 'reconcile' ? '对账' : e.source}
                </td>
                <td className="text-right font-mono text-xs whitespace-nowrap">
                  <span className="text-gray-400">
                    {e.threshold}
                    {RISK_TYPE_UNITS[e.rule_type] && <span className="text-gray-600 ml-0.5">{RISK_TYPE_UNITS[e.rule_type]}</span>}
                  </span>
                  <span className="text-gray-600 mx-1">/</span>
                  <span className={e.action_taken === 'reject' ? 'text-loss' : 'text-yellow-400'}>
                    {e.observed}
                    {RISK_TYPE_UNITS[e.rule_type] && <span className="text-gray-600 ml-0.5">{RISK_TYPE_UNITS[e.rule_type]}</span>}
                  </span>
                </td>
                <td>
                  <span className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${ACTION_STYLES[e.action_taken as RiskRuleAction] ?? 'bg-surface-700 text-gray-300 border-gray-600'}`}>
                    {RISK_ACTION_LABELS[e.action_taken] ?? e.action_taken}
                  </span>
                </td>
                <td>
                  {e.run_id ? (
                    <Link to={`/trading/live/${e.run_id}`} className="text-xs text-blue-400 hover:text-blue-300">
                      任务
                    </Link>
                  ) : (
                    <span className="text-xs text-gray-700">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {(events.data?.items ?? []).length === 0 && (
          <div className="card-body text-center text-gray-500 py-8">暂无风控触发记录。</div>
        )}
        {events.data && events.data.total > 50 && (
          <div className="flex items-center justify-center gap-3 py-3 border-t border-gray-700/50">
            <button type="button" className="btn-secondary px-3 py-1.5" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-sm text-gray-400">{page} / {Math.ceil(events.data.total / 50)}</span>
            <button type="button" className="btn-secondary px-3 py-1.5" disabled={page >= Math.ceil(events.data.total / 50)} onClick={() => setPage((p) => p + 1)}>
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
