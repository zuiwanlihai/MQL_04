import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Circle,
  Radio,
  ShieldAlert,
  Zap,
} from 'lucide-react';
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useDashboardOverview } from '@/hooks/useDashboard';
import { useAccounts, useActiveAccount } from '@/hooks/useAccounts';
import { useTradeDeals, useTradeSnapshot } from '@/hooks/useManualTrade';
import { LIVE_STATUS_LABELS } from '@/hooks/useLive';
import RunningStrategies from '@/components/market/RunningStrategies';
import { apiError } from '@/lib/api';
import { NODE_STATUS_LABELS, relativeTime } from '@/lib/market';
import { useAuthStore } from '@/store/useAuthStore';
import type { DashboardLiveRun } from '@/types/api';

const money = (v: number | null | undefined) =>
  v == null
    ? '—'
    : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const signed = (v: number) => `${v >= 0 ? '+' : ''}${money(v)}`;

const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  trader: '交易员',
  analyst: '分析师',
  viewer: '访客',
};

const ALERT_DOT: Record<string, string> = {
  info: 'bg-blue-400',
  warn: 'bg-yellow-400',
  error: 'bg-orange-500',
  critical: 'bg-loss',
};

const RUN_LIGHT: Record<string, string> = {
  running: 'bg-profit',
  pending: 'bg-blue-400',
  paused: 'bg-yellow-400',
  stopping: 'bg-yellow-400',
  stopped: 'bg-gray-500',
  failed: 'bg-loss',
};

const GATEWAY_STYLE: Record<string, string> = {
  online: 'bg-profit/15 text-profit border-profit/40',
  offline: 'bg-loss/15 text-loss border-loss/40',
  degraded: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  starting: 'bg-blue-500/15 text-blue-400 border-blue-500/40',
};

function BigStat({
  label,
  value,
  sub,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  sub?: React.ReactNode;
  tone?: 'neutral' | 'profit' | 'loss';
}) {
  const color = tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : 'text-white';
  return (
    <div className="card p-4">
      <p className="text-xs uppercase tracking-wider text-gray-500">{label}</p>
      <p className={`text-2xl font-semibold mt-2 font-mono ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-600 mt-2">{sub}</p>}
    </div>
  );
}

function RunCard({ run }: { run: DashboardLiveRun }) {
  const light = run.demoted ? 'bg-loss' : RUN_LIGHT[run.status] ?? 'bg-gray-500';
  return (
    <Link
      to={`/trading/live/${run.id}`}
      className="card p-4 hover:border-blue-500/40 transition-colors block"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Circle className={`w-3 h-3 fill-current ${light}`} />
          <span className="text-sm font-medium text-gray-100 truncate">{run.strategy_name}</span>
        </div>
        <span className="text-xs text-gray-500 whitespace-nowrap">{LIVE_STATUS_LABELS[run.status]}</span>
      </div>
      <p className="text-xs text-gray-500 mt-2 font-mono">
        {run.symbol} / {run.timeframe}
      </p>
      <div className="flex items-end justify-between mt-3">
        <div>
          <p className="text-xs text-gray-600">净值</p>
          <p className="text-sm font-mono text-gray-200">{money(run.equity)}</p>
        </div>
        <div className="text-right">
          <p className="text-xs text-gray-600">今日盈亏</p>
          <p className={`text-sm font-mono ${run.today_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
            {signed(run.today_pnl)}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1 mt-2">
        {run.account_type === 'demo' && (
          <span className="text-2xs px-1.5 py-0.5 rounded bg-profit/15 text-profit">模拟盘交易</span>
        )}
        {run.demoted && <span className="text-2xs px-1.5 py-0.5 rounded bg-loss/15 text-loss">已降级</span>}
        {run.no_new_orders && <span className="text-2xs px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-400">停止开仓</span>}
        {run.reconcile_pending && <span className="text-2xs px-1.5 py-0.5 rounded bg-yellow-500/15 text-yellow-400">待对账</span>}
        {run.observation_ends_at && !run.demoted && (
          <span className="text-2xs px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400">观察期 {run.capital_cap_pct}%</span>
        )}
      </div>
    </Link>
  );
}

export default function Dashboard() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError, error } = useDashboardOverview();

  // ── 登录中的 MT5 账户 → 账户级实时盈亏（含手动交易） ──
  const accountsQ = useAccounts();
  const activeQ = useActiveAccount();
  const activeAccountId = useMemo(() => {
    const online = (activeQ.data ?? []).find((i) => i.account !== null);
    if (!online?.account) return null;
    return (
      accountsQ.data?.items.find(
        (a) => a.login === online.account!.login && a.server === online.account!.server,
      )?.id ?? null
    );
  }, [activeQ.data, accountsQ.data]);

  // 快照（10s 轮询）：全账户持仓 → 浮动盈亏；账户货币
  const snapQ = useTradeSnapshot(activeAccountId ?? undefined);
  // 今日成交（60s 轮询）→ 今日已实现盈亏
  const dealsQ = useTradeDeals(activeAccountId ?? undefined, 48, true, 60_000);

  const acctCurrency = useMemo(() => {
    const acct = snapQ.data?.account;
    return acct && typeof acct.currency === 'string' ? acct.currency : null;
  }, [snapQ.data]);

  // 登录账户实时净值（快照 10s 轮询，含浮动）；无快照时回退 DB 镜像合计
  const acctEquity = useMemo(() => {
    const acct = snapQ.data?.account;
    return acct && typeof acct.equity === 'number' ? acct.equity : null;
  }, [snapQ.data]);

  const acctLabel = useMemo(() => {
    const acct = snapQ.data?.account;
    if (!acct) return null;
    const login = typeof acct.login === 'number' ? acct.login : '';
    const server = typeof acct.server === 'string' ? acct.server : '';
    return login ? `${login}${server ? ` @ ${server}` : ''}` : null;
  }, [snapQ.data]);

  const floatingPnl = useMemo(
    () => (snapQ.data?.positions ?? []).reduce((s, p) => s + p.profit + p.swap, 0),
    [snapQ.data],
  );

  const todayPnl = useMemo(() => {
    if (!activeAccountId) return null;
    const dayStart = Math.floor(new Date().setHours(0, 0, 0, 0) / 1000);
    const realized = (dealsQ.data ?? [])
      .filter((d) => d.type <= 1 && d.time >= dayStart) // 仅买卖成交，剔除出入金
      .reduce((s, d) => s + d.profit + d.swap + d.commission, 0);
    return realized + floatingPnl;
  }, [activeAccountId, dealsQ.data, floatingPnl]);

  const unrealizedPnl = activeAccountId ? floatingPnl : (data?.totals.unrealized_pnl ?? 0);
  const todayValue = activeAccountId ? todayPnl : (data?.totals.today_pnl ?? 0);
  const cur = acctCurrency ? ` ${acctCurrency}` : '';
  const todaySrc = activeAccountId ? '登录账户 · 已实现+浮动' : '运行中实盘合计';
  const unrealSrc = activeAccountId
    ? `${snapQ.data?.positions.length ?? 0} 笔持仓 · 登录账户`
    : `${data?.totals.running_live ?? 0} 个实盘运行中`;

  const chartData = useMemo(
    () =>
      (data?.equity_curve ?? []).map((p) => ({
        t: new Date(p.time).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' }),
        equity: p.equity,
      })),
    [data?.equity_curve],
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">
            欢迎回来，{user?.full_name || user?.username || '交易员'}
          </h1>
          <p className="text-sm text-gray-500 mt-1">MT5 量化交易平台 — 实时监控总览</p>
        </div>
        {user && (
          <span className="text-xs uppercase tracking-wider px-2.5 py-1 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/30">
            {ROLE_LABELS[user.role] ?? user.role}
          </span>
        )}
      </div>

      {isError && (
        <div className="card border-loss/40 bg-loss/10">
          <div className="card-body text-sm text-loss">{apiError(error)}</div>
        </div>
      )}

      {/* 大数字 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <BigStat
          label="账户净值"
          value={
            acctEquity != null
              ? money(acctEquity) + (acctCurrency ? ` ${acctCurrency}` : '')
              : money(data?.totals.equity)
          }
          sub={
            acctEquity != null
              ? `登录账户 ${acctLabel ?? ''} · 实时`
              : data
                ? `${data.accounts.length} 个账户`
                : '加载中…'
          }
        />
        <BigStat
          label="今日盈亏"
          value={data || activeAccountId ? signed(todayValue ?? 0) + cur : '—'}
          tone={(todayValue ?? 0) >= 0 ? 'profit' : 'loss'}
          sub={
            data && (todayValue ?? 0) !== 0 ? (
              <span className="inline-flex items-center gap-1">
                {(todayValue ?? 0) >= 0 ? (
                  <ArrowUpRight className="w-3 h-3 text-profit" />
                ) : (
                  <ArrowDownRight className="w-3 h-3 text-loss" />
                )}
                {todaySrc}
              </span>
            ) : (
              todaySrc
            )
          }
        />
        <BigStat
          label="浮动盈亏"
          value={data || activeAccountId ? signed(unrealizedPnl ?? 0) + cur : '—'}
          tone={(unrealizedPnl ?? 0) >= 0 ? 'profit' : 'loss'}
          sub={unrealSrc}
        />
        <BigStat
          label="运行任务"
          value={`${(data?.totals.running_live ?? 0) + (data?.totals.running_paper ?? 0)}`}
          sub={`实盘 ${data?.totals.running_live ?? 0} · 模拟 ${data?.totals.running_paper ?? 0}${
            data && data.totals.demoted_live > 0 ? ` · 降级 ${data.totals.demoted_live}` : ''
          }`}
        />
      </div>

      {/* 权益曲线 + 网关健康 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card lg:col-span-2">
          <div className="card-header">
            <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <Activity className="w-4 h-4 text-blue-400" /> 当日权益曲线
            </h2>
          </div>
          <div className="card-body">
            {chartData.length === 0 ? (
              <p className="text-sm text-gray-600 py-16 text-center">暂无当日权益数据（需运行中的实盘/虚拟下单）。</p>
            ) : (
              <div style={{ height: 260 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#60a5fa" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#60a5fa" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="t" tick={{ fill: '#6b7280', fontSize: 11 }} tickLine={false} axisLine={{ stroke: '#374151' }} minTickGap={30} />
                    <YAxis
                      domain={['dataMin', 'dataMax']}
                      tick={{ fill: '#6b7280', fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      width={64}
                      tickFormatter={(v: number) => v.toLocaleString('zh-CN')}
                    />
                    <Tooltip
                      contentStyle={{ background: '#1a1f2b', border: '1px solid #374151', borderRadius: 6, fontSize: 12 }}
                      labelStyle={{ color: '#9ca3af' }}
                      formatter={(v: number) => [`$${money(v)}`, '净值']}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#60a5fa" strokeWidth={2} fill="url(#eq)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>

        {/* 网关健康 */}
        <div className="card">
          <div className="card-header">
            <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <Radio className="w-4 h-4 text-blue-400" /> 网关健康
            </h2>
          </div>
          <div className="card-body space-y-3">
            {data?.gateway.id ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-200">{data.gateway.name}</span>
                  <span className={`px-2 py-0.5 rounded border text-xs ${GATEWAY_STYLE[data.gateway.status] ?? 'bg-surface-700 text-gray-300 border-surface-700'}`}>
                    {NODE_STATUS_LABELS[data.gateway.status] ?? data.gateway.status}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div>
                    <p className="text-gray-600">最后心跳</p>
                    <p className="text-gray-300 mt-0.5">{relativeTime(data.gateway.last_heartbeat)}</p>
                  </div>
                  <div>
                    <p className="text-gray-600">活动会话</p>
                    <p className="text-gray-300 mt-0.5 font-mono">{data.gateway.active_sessions}</p>
                  </div>
                  <div>
                    <p className="text-gray-600">CPU</p>
                    <p className="text-gray-300 mt-0.5 font-mono">{data.gateway.cpu_percent.toFixed(0)}%</p>
                  </div>
                  <div>
                    <p className="text-gray-600">内存</p>
                    <p className="text-gray-300 mt-0.5 font-mono">{data.gateway.memory_percent.toFixed(0)}%</p>
                  </div>
                </div>
              </>
            ) : (
              <p className="text-sm text-gray-600 py-6 text-center">未检测到网关节点。</p>
            )}
          </div>
        </div>
      </div>

      {/* 运行中策略卡片 */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
            <Zap className="w-4 h-4 text-loss" /> 运行中策略
          </h2>
          <Link to="/trading/live" className="text-xs text-blue-400 hover:text-blue-300">
            进入实盘中心 →
          </Link>
        </div>
        {data && data.live_runs.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {data.live_runs.map((r) => (
              <RunCard key={r.id} run={r} />
            ))}
          </div>
        ) : (
          <div className="card">
            <div className="card-body text-sm text-gray-600 py-8 text-center">
              当前没有运行中的实盘 / 模拟盘策略。
            </div>
          </div>
        )}
      </div>

      {/* 运行中的 AI 策略（虚拟下单） */}
      <RunningStrategies />

      {/* 持仓 + 告警 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card overflow-hidden">
          <div className="card-header">
            <h2 className="text-sm font-medium text-gray-300">当前持仓（{data?.positions.length ?? 0}）</h2>
          </div>
          {data && data.positions.length > 0 ? (
            <table className="table-dark">
              <thead>
                <tr>
                  <th>品种</th>
                  <th>方向</th>
                  <th className="text-right">手数</th>
                  <th className="text-right">现价</th>
                  <th className="text-right">浮盈</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={`${p.run_id}-${p.ticket}`}>
                    <td className="font-mono text-gray-200">{p.symbol}</td>
                    <td>
                      <span className={p.type === 'buy' ? 'text-profit' : 'text-loss'}>
                        {p.type === 'buy' ? '买入' : '卖出'}
                      </span>
                    </td>
                    <td className="text-right font-mono text-gray-300">{p.volume}</td>
                    <td className="text-right font-mono text-gray-300">{p.price_current}</td>
                    <td className={`text-right font-mono ${p.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {signed(p.profit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="card-body text-sm text-gray-600 py-8 text-center">暂无持仓。</div>
          )}
        </div>

        <div className="card">
          <div className="card-header flex items-center justify-between">
            <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-yellow-400" /> 最新告警
            </h2>
            {data && data.alerts_unread > 0 && (
              <span className="text-xs text-loss">{data.alerts_unread} 条未读</span>
            )}
            <Link to="/alerts" className="text-xs text-blue-400 hover:text-blue-300">全部 →</Link>
          </div>
          <div className="card-body">
            {data && data.alerts.length > 0 ? (
              <ul className="space-y-2 max-h-72 overflow-y-auto pr-1">
                {data.alerts.map((a) => (
                  <li key={a.id} className="flex items-start gap-2 text-sm">
                    <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${ALERT_DOT[a.level] ?? 'bg-gray-500'}`} />
                    <div className="min-w-0 flex-1">
                      <p className={`truncate ${a.is_read ? 'text-gray-500' : 'text-gray-200'}`}>{a.title}</p>
                      <p className="text-xs text-gray-600">{relativeTime(a.created_at)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-600 py-8 text-center">暂无告警。</p>
            )}
          </div>
        </div>
      </div>

      {isLoading && (
        <p className="text-center text-sm text-gray-600">正在加载仪表盘…</p>
      )}

      <div className="flex items-center gap-4 text-xs text-gray-600">
        <Link to="/risk" className="hover:text-gray-400 inline-flex items-center gap-1">
          <ShieldAlert className="w-3.5 h-3.5" /> 风控中心
        </Link>
        <Link to="/risk/audit" className="hover:text-gray-400">审计日志</Link>
        <Link to="/alerts" className="hover:text-gray-400">告警中心</Link>
      </div>
    </div>
  );
}
