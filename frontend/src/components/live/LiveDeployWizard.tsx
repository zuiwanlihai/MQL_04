import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Check, Loader2, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAccounts, useActiveAccount } from '@/hooks/useAccounts';
import {
  DEFAULT_DEPLOY_RULES,
  RISK_ACTION_LABELS,
  RISK_TYPE_LABELS,
  useCheckAdmission,
  useDeployLiveRun,
} from '@/hooks/useLive';
import { useStrategies, useVersions } from '@/hooks/useStrategies';
import StrategyParamsPanel from '@/components/strategies/StrategyParamsPanel';
import { apiError } from '@/lib/api';
import { resolveSchema, seedValues } from '@/lib/strategyParams';
import type { AdmissionResult, RiskRuleSpec, RiskRuleType } from '@/types/api';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
const STEPS = ['策略与版本', 'MT5 账户', '准入检查', '资金与风控', '确认部署'];

interface Props {
  open: boolean;
  onClose: () => void;
  presetStrategyId?: string;
  /** demo=模拟盘交易（免准入闸，仅模拟账户）；real=实盘（全闸） */
  scope?: 'demo' | 'real';
}

export default function LiveDeployWizard({ open, onClose, presetStrategyId, scope = 'real' }: Props) {
  const navigate = useNavigate();
  const isDemo = scope === 'demo';
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const accounts = useAccounts();
  const activeQ = useActiveAccount();
  const admission = useCheckAdmission();
  const deploy = useDeployLiveRun();

  const [step, setStep] = useState(0);
  const [strategyId, setStrategyId] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [symbol, setSymbol] = useState('EURUSD');
  const [timeframe, setTimeframe] = useState('H1');
  const [capital, setCapital] = useState('1000');
  const [leverage, setLeverage] = useState('100');
  const [warmup, setWarmup] = useState('100');
  const [paramsText, setParamsText] = useState('{}');
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [rules, setRules] = useState<RiskRuleSpec[]>(DEFAULT_DEPLOY_RULES);
  const [disclaimer, setDisclaimer] = useState(false);
  const [tail, setTail] = useState('');
  const [admissionResult, setAdmissionResult] = useState<AdmissionResult | null>(null);

  const versions = useVersions(strategyId || undefined);
  const selectedStrategy = useMemo(
    () => strategies.data?.items.find((s) => s.id === strategyId),
    [strategies.data, strategyId],
  );

  const schema = selectedStrategy?.parameters
    ? resolveSchema(Object.keys(selectedStrategy.parameters))
    : null;

  useEffect(() => {
    setParams(schema ? seedValues(schema, selectedStrategy?.parameters) : {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema, strategyId]);

  // 当前登录的 MT5 账户（互斥语义：登录即路由）— 自动作为实盘的绑定账户
  const activeAccount = useMemo(() => {
    const online = (activeQ.data ?? []).find((i) => i.account !== null);
    if (!online?.account) return null;
    return (
      accounts.data?.items.find(
        (a) => a.login === online.account!.login && a.server === online.account!.server,
      ) ?? null
    );
  }, [activeQ.data, accounts.data]);

  useEffect(() => {
    if (activeAccount?.leverage) setLeverage(String(activeAccount.leverage));
  }, [activeAccount?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setDisclaimer(false);
    setTail('');
    setAdmissionResult(null);
    if (presetStrategyId) setStrategyId(presetStrategyId);
     
  }, [open, presetStrategyId]);

  useEffect(() => {
    if (selectedStrategy?.symbol_ids[0]) setSymbol(selectedStrategy.symbol_ids[0]);
    if (selectedStrategy?.timeframe) setTimeframe(selectedStrategy.timeframe);
  }, [selectedStrategy]);

  if (!open) return null;

  const runAdmission = () => {
    if (!strategyId || version == null || !activeAccount) return;
    admission.mutate(
      { strategy_id: strategyId, version, account_id: activeAccount.id },
      { onSuccess: (res) => setAdmissionResult(res) },
    );
  };

  const updateRule = (i: number, patch: Partial<RiskRuleSpec>) => {
    setRules((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };
  const addRule = () =>
    setRules((rs) => [...rs, { type: 'max_open_positions', threshold: 5, action: 'reject' }]);
  const removeRule = (i: number) => setRules((rs) => rs.filter((_, idx) => idx !== i));

  const canNext = (): boolean => {
    if (step === 0) return Boolean(strategyId && version != null);
    if (step === 1) {
      return Boolean(activeAccount) && activeAccount?.account_type === scope;
    }
    if (step === 2) return isDemo || Boolean(admissionResult?.allowed);
    if (step === 3) return rules.length > 0 && Number(capital) > 0;
    if (step === 4) return disclaimer && tail.length === 4;
    return false;
  };

  const submit = () => {
    if (!strategyId || version == null || !activeAccount) return;
    let parameters: Record<string, unknown>;
    if (schema) {
      parameters = params;
    } else {
      try {
        const parsed = JSON.parse(paramsText || '{}');
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('必须是 JSON 对象');
        }
        parameters = parsed as Record<string, unknown>;
      } catch {
        return;
      }
    }
    deploy.mutate(
      {
        strategy_id: strategyId,
        version,
        account_id: activeAccount.id,
        scope,
        symbol: symbol.trim().toUpperCase(),
        timeframe,
        allocated_capital: Number(capital),
        leverage: Number(leverage),
        warmup_bars: Number(warmup) || 0,
        parameters,
        rules,
        disclaimer_ack: true,
        account_tail: tail,
      },
      { onSuccess: (run) => { onClose(); navigate(isDemo ? `/trading/live-demo/${run.id}` : `/trading/live/${run.id}`); } },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="card w-full max-w-3xl max-h-[92vh] overflow-y-auto">
        <div className="card-header sticky top-0 bg-surface-800 z-10">
          <h3 className="text-sm font-medium text-gray-200">
            {isDemo ? '模拟盘交易部署向导' : '实盘部署向导'}
          </h3>
          <button type="button" className="btn-ghost p-1" onClick={onClose}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* stepper */}
        <div className="px-6 pt-4 flex items-center gap-1">
          {STEPS.map((label, i) => (
            <div key={label} className="flex items-center gap-1 flex-1">
              <div
                className={`flex items-center gap-1.5 text-xs whitespace-nowrap ${
                  i < step ? 'text-profit' : i === step ? 'text-blue-300' : 'text-gray-600'
                }`}
              >
                <span
                  className={`w-5 h-5 rounded-full flex items-center justify-center border text-[10px] ${
                    i < step
                      ? 'bg-profit/20 border-profit/40'
                      : i === step
                        ? 'bg-blue-500/20 border-blue-500/50'
                        : 'border-gray-700'
                  }`}
                >
                  {i < step ? <Check className="w-3 h-3" /> : i + 1}
                </span>
                {label}
              </div>
              {i < STEPS.length - 1 && <div className="flex-1 h-px bg-gray-800" />}
            </div>
          ))}
        </div>

        <div className="card-body space-y-4 pt-2">
          {step === 0 && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs text-gray-400">策略 *</span>
                  <select
                    className="input-dark mt-1"
                    value={strategyId}
                    onChange={(e) => {
                      setStrategyId(e.target.value);
                      setVersion(null);
                      setAdmissionResult(null);
                    }}
                  >
                    <option value="">请选择策略…</option>
                    {(strategies.data?.items ?? [])
                      .filter((s) => s.status !== 'archived')
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                          {s.current_version == null ? '（未冻结）' : `（当前 v${s.current_version}）`}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">冻结版本 *</span>
                  <select
                    className="input-dark mt-1"
                    value={version ?? ''}
                    onChange={(e) => {
                      setVersion(e.target.value === '' ? null : Number(e.target.value));
                      setAdmissionResult(null);
                    }}
                    disabled={!strategyId}
                  >
                    <option value="">请选择已冻结版本…</option>
                    {(versions.data?.items ?? []).map((v) => (
                      <option key={v.id} value={v.version}>
                        v{v.version} — {v.changelog || '无说明'}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="text-xs text-gray-500">
                实盘只能部署已冻结版本（准入闸门以版本为维度）。若列表为空请先在策略编辑器冻结版本。
              </p>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-xs text-gray-400">品种</span>
                  <input
                    className="input-dark mt-1 font-mono"
                    value={symbol}
                    maxLength={32}
                    onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">周期</span>
                  <select className="input-dark mt-1" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {TIMEFRAMES.map((tf) => (
                      <option key={tf} value={tf}>{tf}</option>
                    ))}
                  </select>
                </label>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <div className="space-y-1">
                <span className="text-xs text-gray-400">当前登录 MT5 账户</span>
                {activeQ.isLoading || accounts.isLoading ? (
                  <p className="text-sm text-gray-500 flex items-center gap-2">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> 正在获取当前登录账户…
                  </p>
                ) : activeAccount ? (
                  <p className="text-sm text-gray-200 font-mono">
                    {activeAccount.login}@{activeAccount.server}
                    <span
                      className={`ml-2 text-xs px-1.5 py-0.5 rounded font-sans ${
                        activeAccount.account_type === 'real'
                          ? 'bg-loss/15 text-loss'
                          : 'bg-profit/15 text-profit'
                      }`}
                    >
                      {activeAccount.account_type === 'real' ? '实盘' : '模拟'}
                    </span>
                    <span className="text-gray-500 ml-2 font-sans">
                      1:{activeAccount.leverage} · {activeAccount.currency}
                    </span>
                  </p>
                ) : (
                  <p className="text-sm text-yellow-400">
                    未检测到已登录的 MT5 账户 — 请退出登录，在登录页使用
                    {isDemo ? '模拟' : '实盘'} MT5 账号登录后再部署。
                  </p>
                )}
                {activeAccount && activeAccount.account_type !== scope && (
                  <p className="text-sm text-loss">
                    {isDemo
                      ? '模拟盘交易仅支持模拟账户 — 当前登录的是实盘账户，请先在总览页切换登录。'
                      : '实盘仅支持实盘账户 — 当前登录的是模拟账户，请先在总览页切换登录。'}
                  </p>
                )}
                <p className="text-xs text-gray-500">
                  {isDemo
                    ? '模拟盘交易自动使用当前登录的模拟账户：订单真实发往 MT5 模拟服务器（零资金风险），行情与下单路由到该账户所在的网关节点。'
                    : '实盘自动使用当前登录账户：行情与下单将路由到该账户所在的网关节点（服务器）。'}
                </p>
              </div>
              <div>
                <span className="text-xs text-gray-400">
                  策略参数{schema ? '（结构化）' : '（JSON 对象）'}
                </span>
                {schema ? (
                  <div className="mt-2 rounded border border-blue-500/30 bg-surface-900 p-3 max-h-72 overflow-y-auto">
                    <StrategyParamsPanel groups={schema} values={params} onChange={setParams} />
                  </div>
                ) : (
                  <textarea
                    className="input-dark mt-1 font-mono text-xs"
                    rows={3}
                    value={paramsText}
                    onChange={(e) => setParamsText(e.target.value)}
                    placeholder='{"fast": 10, "slow": 30}'
                  />
                )}
              </div>
            </>
          )}

          {step === 2 && isDemo && (
            <div className="px-3 py-3 rounded border border-profit/40 bg-profit/5 text-sm flex items-start gap-2">
              <span className="text-profit"><Check className="w-4 h-4" /></span>
              <div>
                <p className="text-gray-200">模拟盘交易 — 免准入检查</p>
                <p className="text-xs text-gray-500">
                  权限闸 / 回测闸 / 模拟时长闸 / 记录闸均已取消，也不设观察期限额；订单将真实发往 MT5 模拟服务器（零资金风险）。
                </p>
              </div>
            </div>
          )}

          {step === 2 && !isDemo && (
            <>
              {!admissionResult && (
                <button
                  type="button"
                  className="btn-primary"
                  disabled={admission.isPending}
                  onClick={runAdmission}
                >
                  {admission.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                  运行准入四闸检查
                </button>
              )}
              {admission.isError && (
                <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
                  {apiError(admission.error)}
                </div>
              )}
              {admissionResult && (
                <div className="space-y-2">
                  {admissionResult.gates.map((g) => (
                    <div
                      key={g.key}
                      className={`px-3 py-2 rounded border text-sm flex items-start gap-2 ${
                        g.passed
                          ? 'border-profit/40 bg-profit/5'
                          : 'border-loss/40 bg-loss/5'
                      }`}
                    >
                      <span className={g.passed ? 'text-profit' : 'text-loss'}>
                        {g.passed ? <Check className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                      </span>
                      <div>
                        <p className="text-gray-200">{g.label}</p>
                        <p className="text-xs text-gray-500">{g.reason}</p>
                      </div>
                    </div>
                  ))}
                  {!admissionResult.allowed && (
                    <p className="text-xs text-loss">存在未通过的闸门，无法进入下一步。请补齐对应前置（回测 / 继续模拟）。</p>
                  )}
                  <button type="button" className="btn-ghost text-xs" onClick={runAdmission}>
                    重新检查
                  </button>
                </div>
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div className="grid grid-cols-3 gap-3">
                <label className="block">
                  <span className="text-xs text-gray-400">分配资金上限</span>
                  <input className="input-dark mt-1" value={capital} onChange={(e) => setCapital(e.target.value)} />
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">杠杆</span>
                  <input className="input-dark mt-1" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
                </label>
                <label className="block">
                  <span className="text-xs text-gray-400">预热 K 线数</span>
                  <input className="input-dark mt-1" value={warmup} onChange={(e) => setWarmup(e.target.value)} />
                </label>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-gray-400">风控规则（强制，至少一条亏损/回撤类）</span>
                  <button type="button" className="btn-ghost text-xs" onClick={addRule}>+ 添加规则</button>
                </div>
                <div className="space-y-2">
                  {rules.map((r, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <select
                        className="input-dark flex-1 text-xs"
                        value={r.type}
                        onChange={(e) => updateRule(i, { type: e.target.value as RiskRuleType })}
                      >
                        {Object.entries(RISK_TYPE_LABELS)
                          .filter(([k]) => k !== 'manual_lot_cap' && k !== 'reconcile_frozen')
                          .map(([k, v]) => (
                            <option key={k} value={k}>{v}</option>
                          ))}
                      </select>
                      <input
                        className="input-dark w-24 text-xs"
                        value={r.threshold}
                        onChange={(e) => updateRule(i, { threshold: Number(e.target.value) })}
                      />
                      <select
                        className="input-dark w-40 text-xs"
                        value={r.action}
                        onChange={(e) => updateRule(i, { action: e.target.value as RiskRuleSpec['action'] })}
                      >
                        {Object.entries(RISK_ACTION_LABELS).map(([k, v]) => (
                          <option key={k} value={k}>{v}</option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="btn-ghost text-loss text-xs"
                        onClick={() => removeRule(i)}
                        disabled={rules.length <= 1}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded border border-yellow-500/40 bg-yellow-500/5 p-3">
                <p className="text-xs font-medium text-yellow-300 flex items-center gap-1">
                  <AlertTriangle className="w-3.5 h-3.5" /> 风险真空披露
                </p>
                <p className="text-xs text-gray-300 mt-1 leading-relaxed">
                  平台风控依赖网关在线：网关掉线期间持仓无平台保护，且 MVP 无券商侧保护单。请确保运行环境（Windows 常开、MT5 终端登录）稳定。
                </p>
              </div>
            </>
          )}

          {step === 4 && (
            <>
              <div className="rounded border border-gray-700 bg-surface-900 p-3 text-xs text-gray-400 space-y-1">
                <p>策略：{selectedStrategy?.name} v{version}</p>
                <p>账户：{activeAccount?.login} @ {activeAccount?.server}</p>
                <p>品种/周期：{symbol} / {timeframe} · 分配资金 {capital} · 杠杆 1:{leverage}</p>
                <p>风控规则：{rules.length} 条{isDemo ? ' · 免观察期（模拟盘交易）' : ' · 观察期默认 7 天'}</p>
              </div>

              <div className="rounded border border-loss/40 bg-loss/5 p-3 space-y-2">
                <p className="text-xs font-medium text-loss flex items-center gap-1">
                  <AlertTriangle className="w-3.5 h-3.5" /> {isDemo ? '交易披露（必须勾选）' : '免责条款（必须勾选）'}
                </p>
                <p className="text-xs text-gray-300 leading-relaxed">
                  {isDemo
                    ? '我理解模拟盘交易通过 MT5 模拟账户真实下单（无本金损失风险），撮合与风控结果可能与回测/虚拟下单存在差异，网关掉线期间模拟持仓无保护。'
                    : '我理解实盘交易存在本金损失风险，撮合与风控结果可能与回测/模拟存在差异，网关掉线期间持仓无保护。我已确认风控规则并自行承担交易后果。'}
                </p>
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="checkbox" checked={disclaimer} onChange={(e) => setDisclaimer(e.target.checked)} />
                  <span className="text-gray-200">我已阅读并同意上述免责条款</span>
                </label>
              </div>

              <label className="block">
                <span className="text-xs text-gray-400">二次确认：输入 MT5 账号尾号 4 位 *</span>
                <input
                  className="input-dark mt-1 font-mono w-40"
                  value={tail}
                  maxLength={4}
                  placeholder={activeAccount ? String(activeAccount.login).slice(-4) : ''}
                  onChange={(e) => setTail(e.target.value.replace(/\D/g, ''))}
                />
              </label>

              {deploy.isError && (
                <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
                  {apiError(deploy.error)}
                </div>
              )}
            </>
          )}

          <div className="flex justify-between gap-2 pt-2">
            <button
              type="button"
              className="btn-ghost"
              disabled={step === 0 || deploy.isPending}
              onClick={() => setStep((s) => Math.max(0, s - 1))}
            >
              上一步
            </button>
            {step < STEPS.length - 1 ? (
              <button
                type="button"
                className="btn-primary"
                disabled={!canNext()}
                onClick={() => {
                  if (step === 1 && !admissionResult) {
                    // entering admission step; user clicks check there
                  }
                  setStep((s) => s + 1);
                }}
              >
                下一步
              </button>
            ) : (
              <button
                type="button"
                className="btn-danger"
                disabled={!canNext() || deploy.isPending}
                onClick={submit}
              >
                {deploy.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                {isDemo ? '确认部署模拟盘交易' : '确认部署实盘'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
