import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Loader2, Pause, Play, RotateCcw, ShieldCheck, Square } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { EquityChart } from '@/components/backtest/Charts';
import {
  LIVE_STATUS_LABELS,
  useLiveLogs,
  useLiveRun,
  useLiveTrades,
  usePauseLiveRun,
  useReAdmit,
  useResetLiveAnchor,
  useResetLiveRun,
  useResumeLiveRun,
  useRunDeviation,
  useStopLiveRun,
} from '@/hooks/useLive';
import { useStrategies } from '@/hooks/useStrategies';
import { useKlines, useQuotesStream } from '@/hooks/useMarket';
import { PriceLadderChart } from '@/components/trading/PriceLadderChart';
import { resolvePlaybook } from '@/lib/strategyPlaybook';
import { apiError } from '@/lib/api';
import type { LiveRunStatus, MarketTimeframe } from '@/types/api';

const STATUS_STYLES: Record<LiveRunStatus, string> = {
  pending: 'bg-surface-700 text-gray-300 border-gray-600',
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  paused: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopping: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopped: 'bg-gray-500/15 text-gray-400 border-gray-500/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const money = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const dt = (s: string | number | null | undefined) =>
  s == null
    ? '—'
    : new Date(typeof s === 'number' ? s * 1000 : s).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'profit' | 'loss' }) {
  return (
    <div className="card">
      <div className="card-body py-3">
        <p className="text-xs text-gray-500">{label}</p>
        <p
          className={`text-lg font-mono mt-0.5 ${
            tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : 'text-gray-100'
          }`}
        >
          {value}
        </p>
      </div>
    </div>
  );
}

const LOG_KINDS = ['', 'log', 'signal', 'system', 'risk'] as const;
const LOG_KIND_LABELS: Record<string, string> = {
  '': '全部类型',
  log: '日志',
  signal: '信号',
  system: '系统',
  risk: '风控',
};

const DEVIATION_VERDICT: Record<string, { label: string; cls: string }> = {
  aligned: { label: '口径一致', cls: 'bg-profit/15 text-profit border-profit/40' },
  moderate: { label: '轻微偏差', cls: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40' },
  divergent: { label: '显著偏差', cls: 'bg-loss/15 text-loss border-loss/40' },
  insufficient: { label: '样本不足', cls: 'bg-surface-700 text-gray-400 border-gray-600' },
};

const pt1 = (v: number | null | undefined, digits = 1) => (v == null ? '—' : v.toFixed(digits));

function DevStat({ label, value, tone }: { label: string; value: string; tone?: 'profit' | 'loss' }) {
  return (
    <div className="bg-surface-700/40 rounded-lg px-3 py-2 border border-gray-800">
      <p className="text-xs text-gray-500">{label}</p>
      <p
        className={`text-base font-mono mt-0.5 ${
          tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : 'text-gray-100'
        }`}
      >
        {value}
      </p>
    </div>
  );
}

export default function LiveDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const run = useLiveRun(runId);
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const pause = usePauseLiveRun();
  const resume = useResumeLiveRun();
  const stop = useStopLiveRun();
  const reset = useResetLiveRun();
  const resetAnchor = useResetLiveAnchor();
  const readmit = useReAdmit();

  const [tab, setTab] = useState<
    'chart' | 'positions' | 'trades' | 'deviation' | 'logs' | 'config' | 'playbook'
  >('chart');
  const [vh, setVh] = useState(() => window.innerHeight);
  useEffect(() => {
    const onResize = () => setVh(window.innerHeight);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const chartH = Math.max(360, vh - 330);
  const [tradePage, setTradePage] = useState(1);
  const [logPage, setLogPage] = useState(1);
  const [logKind, setLogKind] = useState('');
  const [reviewNote, setReviewNote] = useState('');

  const detail = run.data;
  const status = detail?.status;
  const live = status === 'running' || status === 'pending' || status === 'stopping';
  const anyPending = pause.isPending || resume.isPending || stop.isPending || reset.isPending || readmit.isPending;

  const trades = useLiveTrades(runId, tradePage, tab === 'trades');
  const deviation = useRunDeviation(runId, tab === 'deviation');
  const logs = useLiveLogs(runId, {
    page: logPage,
    kind: logKind || undefined,
    enabled: tab === 'logs',
    poll: live,
  });
  // 右半屏价格网格的实时现价（WS 推送，断线自动回退 5s 轮询）
  const { quotes } = useQuotesStream(detail ? [detail.symbol] : []);
  // 价格走势数据：启动至今的 M1 收盘价（30s 轻量刷新）
  const klines = useKlines(
    detail?.symbol,
    (detail?.timeframe ?? 'M1') as MarketTimeframe,
    1500,
    30_000,
  );
  const priceHistory = useMemo(() => {
    const startMs = detail ? Date.parse(detail.started_at ?? detail.created_at) : NaN;
    if (!Number.isFinite(startMs)) return [];
    const bars = klines.data ?? [];
    if (!bars.length) return [];
    // K 线时间走券商轴（伪 epoch，与真实 UTC 有固定偏移，历史库重建期间还会额外滞后），
    // 不能直接与真实 UTC 的启动时刻比较；用「尾根 − 运行时长」把启动线换算到同一根轴上
    const runSecs = Math.max(0, (Date.now() - startMs) / 1000);
    const axisCutoff = bars[bars.length - 1].time - runSecs;
    return bars.filter((b) => b.time >= axisCutoff).map((b) => ({ time: b.time, price: b.close }));
  }, [klines.data, detail]);

  if (run.isError) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body text-sm text-loss">{apiError(run.error)}</div>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">加载实盘任务…</div>
      </div>
    );
  }

  const runStrategy = strategies.data?.items.find((s) => s.id === detail.strategy_id);
  const strategyName = runStrategy?.name;
  const playbook = resolvePlaybook(
    runStrategy?.parameters ? Object.keys(runStrategy.parameters) : null,
  );
  const isDemoRun = detail.account_type === 'demo';
  const listPath = isDemoRun ? '/trading/live-demo' : '/trading/live';
  const equityPoints = (detail.equity_history ?? []).map((p) => ({
    time: p.time ?? new Date().toISOString(),
    equity: p.equity,
    balance: p.balance,
    drawdown: 0,
    baseline: detail.allocated_capital,
  }));
  const inObservation =
    detail.observation_ends_at != null && new Date(detail.observation_ends_at) > new Date();
  const dev = deviation.data;
  const devVerdict = dev ? (DEVIATION_VERDICT[dev.verdict] ?? DEVIATION_VERDICT.insufficient) : null;

  // ── 右半屏价格网格（网格策略）：锚定价来自 ctx.storage 快照 ──
  const liveQuote = quotes.get(detail.symbol);
  const currentPrice =
    liveQuote?.bid ??
    (detail.positions ?? []).filter((p) => p.type === 'buy')[0]?.price_current ??
    null;
  const symbolInfo = ((detail.config_snapshot ?? {}).symbol_info ?? {}) as {
    point?: number;
    digits?: number;
  };
  const runParams = detail.parameters ?? {};
  const gridStep = Number(runParams.grid_step ?? 0);
  const point = Number(symbolInfo.point ?? 0);
  const stepPrice = gridStep > 0 && point > 0 ? gridStep * point : 0;
  const maxLevels = Number(runParams.max_levels ?? 0);
  const anchorRaw = (detail.strategy_state as Record<string, unknown> | null)?.anchor;
  const anchor = anchorRaw != null && Number.isFinite(Number(anchorRaw)) ? Number(anchorRaw) : null;
  const priceDigits =
    Number(symbolInfo.digits ?? 0) ||
    (currentPrice != null ? (currentPrice >= 100 ? 2 : currentPrice >= 10 ? 3 : 5) : 5);
  const openPrices = (detail.positions ?? [])
    .filter((p) => p.type === 'buy')
    .map((p) => p.price_open);
  const sellOpenPrices = (detail.positions ?? [])
    .filter((p) => p.type === 'sell')
    .map((p) => p.price_open);
  // 最近一笔成交（开仓方向 + 实际成交价；平仓成交不体现在 positions 里）
  const lastFill = (() => {
    const positions = detail.positions ?? [];
    if (!positions.length) return null;
    const last = positions.reduce((a, b) => ((b.time ?? 0) >= (a.time ?? 0) ? b : a));
    return { price: last.price_open, side: last.type };
  })();

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <Link to={listPath} className="text-gray-500 hover:text-gray-300 text-sm">
              {isDemoRun ? '模拟盘交易' : '实盘交易'}
            </Link>
            <span className="text-gray-700">/</span>
            <h1 className="text-xl font-semibold text-white">{strategyName ?? detail.strategy_id.slice(0, 8)}</h1>
            <span
              className={`px-2 py-0.5 rounded border text-xs ${
                isDemoRun
                  ? 'bg-profit/15 text-profit border-profit/40'
                  : 'bg-loss/15 text-loss border-loss/40'
              }`}
            >
              {isDemoRun ? '模拟账户 · 零资金风险' : '真实资金'}
            </span>
            <span className={`px-2 py-0.5 rounded border text-xs ${STATUS_STYLES[detail.status]}`}>
              {LIVE_STATUS_LABELS[detail.status]}
            </span>
            {detail.demoted && (
              <span className="px-2 py-0.5 rounded border text-xs bg-loss/15 text-loss border-loss/40">已自动降级</span>
            )}
            {live && <Loader2 className="w-4 h-4 animate-spin text-blue-400" />}
          </div>
          <p className="text-xs text-gray-500 mt-1 font-mono">
            {detail.symbol} / {detail.timeframe} · v{detail.strategy_version ?? '—'} · 杠杆 1:{detail.leverage} ·{' '}
            分配资金 {money(detail.allocated_capital)} · magic {detail.magic}
          </p>
          {inObservation && (
            <p className="text-xs text-yellow-400 mt-1">
              观察期中（至 {dt(detail.observation_ends_at)}）：单笔上限 {money(detail.lot_cap)} 手 · 资金上限{' '}
              {detail.capital_cap_pct}%
            </p>
          )}
          {detail.no_new_orders && <p className="text-xs text-yellow-400 mt-1">风控触发「当日停止开仓」，仅允许平仓。</p>}
          {detail.reconcile_pending && (
            <p className="text-xs text-yellow-400 mt-1">存在结果未知的订单，等待对账收敛。</p>
          )}
          {detail.status === 'failed' && detail.error_message && (
            <p className="text-sm text-loss mt-1 whitespace-pre-line">
              {detail.error_code ? `[${detail.error_code}] ` : ''}
              {detail.error_message}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {status === 'running' && (
            <button type="button" className="btn-secondary" disabled={anyPending} onClick={() => pause.mutate(detail.id)}>
              <Pause className="w-4 h-4" /> 暂停
            </button>
          )}
          {status === 'paused' && (
            <button
              type="button"
              className="btn-primary"
              disabled={anyPending || detail.demoted}
              title={detail.demoted ? '已降级，需风控复核后恢复' : '恢复喂入 K 线'}
              onClick={() => resume.mutate(detail.id)}
            >
              <Play className="w-4 h-4" /> 恢复
            </button>
          )}
          {(status === 'running' || status === 'paused') && (
            <button
              type="button"
              className="btn-secondary border-loss/40 text-loss hover:bg-loss/10"
              disabled={anyPending}
              onClick={() => {
                if (window.confirm('停止该实盘？将通过网关平掉该策略（magic 匹配）的全部持仓。')) {
                  stop.mutate({ id: detail.id, closePositions: true });
                }
              }}
            >
              <Square className="w-4 h-4" /> 停止（平仓）
            </button>
          )}
          {(status === 'stopped' || status === 'failed') && !detail.demoted && (
            <button
              type="button"
              className="btn-secondary"
              disabled={anyPending}
              onClick={() => {
                if (window.confirm('重置将清空成交与日志并重新拉取历史启动。确认？')) {
                  reset.mutate(detail.id);
                }
              }}
            >
              <RotateCcw className="w-4 h-4" /> 重置并重新启动
            </button>
          )}
        </div>
      </div>

      {/* demotion re-admit panel */}
      {detail.demoted && (
        <div className="card border-loss/40">
          <div className="card-body space-y-2">
            <p className="text-sm text-loss font-medium flex items-center gap-1">
              <ShieldCheck className="w-4 h-4" /> 自动降级：实盘资格已撤销，需管理员人工复核
            </p>
            <textarea
              className="input-dark text-xs font-mono"
              rows={2}
              placeholder="复核意见（≥8 字，写入审计）"
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
            />
            <button
              type="button"
              className="btn-primary"
              disabled={reviewNote.trim().length < 8 || readmit.isPending}
              onClick={() => {
                if (window.confirm('确认复核通过并恢复该任务的实盘资格？')) {
                  readmit.mutate({ id: detail.id, note: reviewNote }, { onSuccess: () => setReviewNote('') });
                }
              }}
            >
              {readmit.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              复核并恢复资格
            </button>
            {readmit.isError && <p className="text-xs text-loss">{apiError(readmit.error)}</p>}
          </div>
        </div>
      )}

      {(pause.isError || resume.isError || stop.isError || reset.isError) && (
        <div className="px-4 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
          操作失败：{apiError(pause.error ?? resume.error ?? stop.error ?? reset.error)}
        </div>
      )}

      {/* account mirror (real broker) */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat label="净值（真实）" value={money(detail.equity)} />
        <Stat label="余额" value={money(detail.balance)} />
        <Stat
          label="浮动盈亏"
          value={money(detail.unrealized_pnl)}
          tone={detail.unrealized_pnl >= 0 ? 'profit' : 'loss'}
        />
        <Stat label="占用保证金" value={money(detail.margin_used)} />
        <Stat label="可用保证金" value={money(detail.margin_free)} />
        <Stat label="最后 K 线" value={dt(detail.last_bar_time)} />
      </div>

      {/* tabs */}
      <div className="flex items-center gap-1 border-b border-gray-800">
        {(
          [
            ['chart', '图表'],
            ['positions', `当前持仓（${(detail.positions ?? []).length}）`],
            ['trades', '成交明细'],
            ['deviation', '执行偏差'],
            ['logs', '运行日志'],
            ['config', '准入与配置'],
            ['playbook', strategyName ?? 'AI 说明'],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === k ? 'border-blue-500 text-blue-300' : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
        {live && tab === 'logs' && <span className="text-xs text-gray-600 ml-2">（5s 自动刷新）</span>}
      </div>

      {tab === 'chart' && (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">
            权益曲线（{equityPoints.length} 点 · 净值/分配资金{' '}
            <span className={(detail.equity ?? 0) >= detail.allocated_capital ? 'text-profit' : 'text-loss'}>
              {money(detail.allocated_capital > 0 ? ((detail.equity ?? 0) / detail.allocated_capital - 1) * 100 : 0)}%
            </span>
            ）
          </h3>
        </div>
        <div className="card-body grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div>
            {equityPoints.length > 1 ? (
              <EquityChart points={equityPoints} height={chartH} />
            ) : (
              <p className="text-sm text-gray-500 text-center py-8">暂无权益数据</p>
            )}
          </div>
          <div className="lg:border-l lg:border-gray-800 lg:pl-5">
            <div className="flex items-center justify-between mb-2">
              <h4 className="text-xs font-medium text-gray-300">
                价格网格 · {detail.symbol}
                {anchor != null && stepPrice > 0 && (
                  <span className="text-gray-500 font-normal ml-2">
                    锚定 {anchor.toFixed(priceDigits)} · 间距 {money(stepPrice, priceDigits)} ×{' '}
                    {maxLevels} 格
                  </span>
                )}
              </h4>
              {lastFill && (
                <span
                  className={`text-xs font-mono ${
                    lastFill.side === 'buy' ? 'text-profit' : 'text-loss'
                  }`}
                >
                  最近成交 {lastFill.price.toFixed(priceDigits)}{' '}
                  {lastFill.side === 'buy' ? '买开' : '卖开'}
                </span>
              )}
              {currentPrice != null && (
                <span className="text-xs font-mono text-blue-300">
                  现价 {currentPrice.toFixed(priceDigits)}
                </span>
              )}
              {anchor != null && (status === 'running' || status === 'paused') && (
                <button
                  type="button"
                  className="text-xs px-2.5 py-1 rounded border border-gray-600 text-gray-300 hover:border-blue-500 hover:text-blue-300 disabled:opacity-50 transition-colors"
                  disabled={resetAnchor.isPending}
                  onClick={() => {
                    if (runId) resetAnchor.mutate(runId);
                  }}
                  title="把网格锚定价重置为当前最新买价，网格线随之平移"
                >
                  {resetAnchor.isPending ? (
                    <Loader2 className="w-3 h-3 animate-spin inline mr-1" />
                  ) : (
                    <RotateCcw className="w-3 h-3 inline mr-1" />
                  )}
                  重置锚点
                </button>
              )}
            </div>
            <PriceLadderChart
              height={chartH - 28}
              digits={priceDigits}
              currentPrice={currentPrice}
              anchor={anchor}
              stepPrice={stepPrice}
              maxLevels={maxLevels}
              openPrices={openPrices}
              sellOpenPrices={sellOpenPrices}
              history={priceHistory}
            />
          </div>
        </div>
      </div>
      )}

      {tab === 'positions' && (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">当前持仓（{(detail.positions ?? []).length}）</h3>
          <span className="text-xs text-gray-500">来源：MT5 网关实时镜像</span>
        </div>
        <div className="card-body p-0 overflow-x-auto">
          {(detail.positions ?? []).length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-6">当前无持仓</p>
          ) : (
            <table className="table-dark">
              <thead>
                <tr>
                  <th>票号</th>
                  <th>品种</th>
                  <th>方向</th>
                  <th>手数</th>
                  <th>开仓价</th>
                  <th>现价</th>
                  <th className="text-right">浮动盈亏</th>
                  <th>开仓时间</th>
                </tr>
              </thead>
              <tbody>
                {detail.positions.map((p) => (
                  <tr key={p.ticket} className="text-xs">
                    <td className="font-mono text-gray-500">{p.ticket}</td>
                    <td className="font-mono">{p.symbol}</td>
                    <td className={p.type === 'buy' ? 'text-profit' : 'text-loss'}>{p.type === 'buy' ? '买' : '卖'}</td>
                    <td className="font-mono">{money(p.volume)}</td>
                    <td className="font-mono">{money(p.price_open, 5)}</td>
                    <td className="font-mono">{money(p.price_current, 5)}</td>
                    <td className={`text-right font-mono ${p.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {money(p.profit)}
                    </td>
                    <td className="text-gray-400 whitespace-nowrap">{dt(p.time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      )}

      {tab === 'trades' && (
        <div className="card overflow-hidden">
          <table className="table-dark">
            <thead>
              <tr>
                <th>成交票号</th>
                <th>方向</th>
                <th>开/平</th>
                <th>手数</th>
                <th>价格</th>
                <th>时间</th>
                <th className="text-right">盈亏</th>
                <th className="text-right">手续费</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {(trades.data?.items ?? []).map((t) => (
                <tr key={t.id} className="text-xs">
                  <td className="font-mono text-gray-500">{t.deal_ticket}</td>
                  <td className={t.direction === 'buy' ? 'text-profit' : 'text-loss'}>
                    {t.direction === 'buy' ? '买' : '卖'}
                  </td>
                  <td className="text-gray-400">
                    {t.entry === 'in' ? '开仓' : t.entry === 'out' ? '平仓' : '部分平'}
                  </td>
                  <td className="font-mono">{money(t.volume)}</td>
                  <td className="font-mono">{money(t.price, 5)}</td>
                  <td className="text-gray-400 whitespace-nowrap">{dt(t.open_time)}</td>
                  <td className={`text-right font-mono ${t.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {money(t.profit)}
                    {t.is_forced && <span className="text-loss ml-1">⚠</span>}
                  </td>
                  <td className="text-right font-mono text-gray-400">{money(t.commission)}</td>
                  <td className="text-gray-500 max-w-40 truncate" title={t.comment}>{t.comment || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {trades.isLoading && <p className="py-6 text-center text-sm text-gray-500">加载成交…</p>}
          {!trades.isLoading && (trades.data?.items ?? []).length === 0 && (
            <p className="py-6 text-center text-sm text-gray-500">暂无成交记录</p>
          )}
          {(trades.data?.total ?? 0) > 50 && (
            <div className="flex items-center justify-center gap-3 py-3 border-t border-gray-700/50">
              <button
                type="button"
                className="btn-secondary px-3 py-1"
                disabled={tradePage <= 1}
                onClick={() => setTradePage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-sm text-gray-400">
                {tradePage} / {Math.ceil((trades.data?.total ?? 0) / 50)}
              </span>
              <button
                type="button"
                className="btn-secondary px-3 py-1"
                disabled={tradePage >= Math.ceil((trades.data?.total ?? 0) / 50)}
                onClick={() => setTradePage((p) => p + 1)}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {tab === 'deviation' && (
        <div className="space-y-4">
          {deviation.isLoading && (
            <div className="card">
              <div className="card-body text-center text-sm text-gray-500 py-10">加载执行偏差报告…</div>
            </div>
          )}
          {deviation.isError && (
            <div className="card border-loss/40 bg-loss/10">
              <div className="card-body text-sm text-loss">{apiError(deviation.error)}</div>
            </div>
          )}
          {dev && dev.sample_count === 0 && (
            <div className="card">
              <div className="card-body text-center text-sm text-gray-500 py-10">
                暂无含信号基准的成交
                <span className="block text-xs text-gray-600 mt-1">
                  偏差分析依赖携带信号价快照的成交记录（新版本引擎起产生）
                </span>
              </div>
            </div>
          )}
          {dev && dev.sample_count > 0 && devVerdict && (
            <>
              <div className="card">
                <div className="card-header flex items-center gap-2 flex-wrap">
                  <h3 className="text-sm font-medium text-gray-200">回测-实盘偏差报告</h3>
                  <span className={`px-2 py-0.5 rounded border text-xs ${devVerdict.cls}`}>
                    {devVerdict.label}
                  </span>
                  <span className="text-xs text-gray-500 ml-auto">
                    {dev.symbol} · 样本 {dev.sample_count} 笔
                    {dev.skipped_count > 0 && ` · 跳过 ${dev.skipped_count} 笔（无信号基准）`}
                  </span>
                </div>
                <div className="card-body grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
                  <DevStat label="平均总偏差" value={`${pt1(dev.avg_total_deviation_points)} 点`} />
                  <DevStat label="中位总偏差" value={`${pt1(dev.median_total_deviation_points)} 点`} />
                  <DevStat label="P95 总偏差" value={`${pt1(dev.p95_total_deviation_points)} 点`} />
                  <DevStat label="平均滑点" value={`${pt1(dev.avg_slippage_points)} 点`} />
                  <DevStat
                    label="点差（信号/实际）"
                    value={`${pt1(dev.avg_signal_spread_points)} / ${pt1(dev.avg_actual_spread_points)}`}
                  />
                  <DevStat label="平均延迟" value={`${pt1(dev.avg_latency_seconds, 2)} 秒`} />
                  <DevStat
                    label="总偏差成本"
                    value={money(dev.total_deviation_cost)}
                    tone={dev.total_deviation_cost > 0 ? 'loss' : undefined}
                  />
                </div>
              </div>
              <div className="card overflow-hidden">
                <div className="card-body p-0 overflow-x-auto">
                  <table className="table-dark">
                    <thead>
                      <tr>
                        <th>成交票号</th>
                        <th>方向</th>
                        <th>开/平</th>
                        <th>手数</th>
                        <th>信号价</th>
                        <th>理论价</th>
                        <th>实际价</th>
                        <th className="text-right">总偏差(点)</th>
                        <th className="text-right">滑点(点)</th>
                        <th className="text-right">点差(点)</th>
                        <th className="text-right">漂移(点)</th>
                        <th className="text-right">延迟(秒)</th>
                        <th className="text-right">成本</th>
                        <th>成交时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dev.trades.map((t) => {
                        const total = t.total_deviation_points;
                        return (
                          <tr key={t.deal_ticket} className="text-xs">
                            <td className="font-mono text-gray-500">{t.deal_ticket}</td>
                            <td className={t.direction === 'buy' ? 'text-profit' : 'text-loss'}>
                              {t.direction === 'buy' ? '买' : '卖'}
                            </td>
                            <td className="text-gray-400">
                              {t.entry === 'in' ? '开仓' : t.entry === 'out' ? '平仓' : '部分平'}
                            </td>
                            <td className="font-mono">{money(t.volume)}</td>
                            <td className="font-mono text-gray-400">
                              {t.signal_price == null ? '—' : money(t.signal_price, 5)}
                            </td>
                            <td className="font-mono text-gray-400">
                              {t.theoretical_price == null ? '—' : money(t.theoretical_price, 5)}
                            </td>
                            <td className="font-mono">{money(t.actual_price, 5)}</td>
                            <td
                              className={`text-right font-mono ${
                                total == null ? 'text-gray-300' : total > 2 ? 'text-loss' : total < -2 ? 'text-profit' : 'text-gray-300'
                              }`}
                            >
                              {pt1(total)}
                            </td>
                            <td className="text-right font-mono text-gray-400">{pt1(t.slippage_points)}</td>
                            <td className="text-right font-mono text-gray-400">{pt1(t.actual_spread_points)}</td>
                            <td className="text-right font-mono text-gray-400">{pt1(t.drift_points)}</td>
                            <td className="text-right font-mono text-gray-400">{pt1(t.latency_seconds, 2)}</td>
                            <td className={`text-right font-mono ${t.cost > 0 ? 'text-loss' : 'text-gray-400'}`}>
                              {money(t.cost)}
                            </td>
                            <td className="text-gray-400 whitespace-nowrap">{dt(t.open_time)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'logs' && (
        <div className="card overflow-hidden">
          <div className="card-header flex items-center gap-2">
            <select
              className="input-dark w-28 text-xs"
              value={logKind}
              onChange={(e) => {
                setLogKind(e.target.value);
                setLogPage(1);
              }}
            >
              {LOG_KINDS.map((k) => (
                <option key={k} value={k}>
                  {LOG_KIND_LABELS[k]}
                </option>
              ))}
            </select>
            <span className="text-xs text-gray-500 ml-auto">共 {logs.data?.total ?? 0} 条</span>
          </div>
          <div className="card-body p-0 max-h-[480px] overflow-y-auto">
            <table className="table-dark">
              <tbody>
                {(logs.data?.items ?? []).map((l) => (
                  <tr key={l.id} className="text-xs align-top">
                    <td className="font-mono text-gray-600 whitespace-nowrap pl-4">{l.seq}</td>
                    <td className="text-gray-500 whitespace-nowrap">{dt(l.bar_time)}</td>
                    <td>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] border ${
                          l.kind === 'risk'
                            ? 'bg-loss/15 text-loss border-loss/40'
                            : l.level === 'error'
                              ? 'bg-loss/15 text-loss border-loss/40'
                              : l.level === 'warn'
                                ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40'
                                : 'bg-surface-700 text-gray-400 border-gray-700'
                        }`}
                      >
                        {LOG_KIND_LABELS[l.kind] ?? l.kind}·{l.level}
                      </span>
                    </td>
                    <td className="text-gray-300 break-all pr-4 py-1.5">{l.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {logs.isLoading && <p className="py-6 text-center text-sm text-gray-500">加载日志…</p>}
            {!logs.isLoading && (logs.data?.items ?? []).length === 0 && (
              <p className="py-6 text-center text-sm text-gray-500">暂无日志</p>
            )}
          </div>
          {(logs.data?.total ?? 0) > 200 && (
            <div className="flex items-center justify-center gap-3 py-3 border-t border-gray-700/50">
              <button
                type="button"
                className="btn-secondary px-3 py-1"
                disabled={logPage <= 1}
                onClick={() => setLogPage((p) => Math.max(1, p - 1))}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-sm text-gray-400">
                {logPage} / {Math.ceil((logs.data?.total ?? 0) / 200)}
              </span>
              <button
                type="button"
                className="btn-secondary px-3 py-1"
                disabled={logPage >= Math.ceil((logs.data?.total ?? 0) / 200)}
                onClick={() => setLogPage((p) => p + 1)}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {tab === 'config' && (
        <div className="space-y-4">
          <div className="card">
            <div className="card-header">
              <h3 className="text-sm font-medium text-gray-200">准入闸门快照（部署时冻结）</h3>
            </div>
            <div className="card-body">
              <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
                {JSON.stringify(detail.admission_snapshot ?? {}, null, 2)}
              </pre>
            </div>
          </div>
          <div className="card">
            <div className="card-header">
              <h3 className="text-sm font-medium text-gray-200">冻结配置快照</h3>
            </div>
            <div className="card-body">
              <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap break-all max-h-[480px] overflow-y-auto">
                {JSON.stringify(detail.config_snapshot ?? {}, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      )}

      {tab === 'playbook' && (
        <div className="card">
          <div className="card-header flex items-center justify-between">
            <h3 className="text-sm font-medium text-gray-200">
              AI 策略说明 · {playbook?.label ?? strategyName ?? '自定义策略'}
            </h3>
            {playbook && (
              <span className="text-2xs text-gray-600">模板家族：{playbook.family}</span>
            )}
          </div>
          <div className="card-body space-y-4">
            {playbook ? (
              playbook.sections.map((sec, i) => (
                <section key={sec.title}>
                  <h4 className="text-sm font-medium text-gray-200">
                    {i + 1}. {sec.title}
                  </h4>
                  <ul className="mt-1 space-y-1">
                    {sec.body.map((line) => (
                      <li key={line} className="text-xs text-gray-400 leading-relaxed flex gap-1.5">
                        <span className="text-gray-600 shrink-0">•</span>
                        <span>{line}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              ))
            ) : (
              <p className="text-xs text-gray-500">
                该策略为自定义版本，平台无法自动识别模板家族 —— 交易规则以策略源码为准（可在策略页「编辑源码」查看）。
              </p>
            )}
            <p className="text-2xs text-gray-600 border-t border-gray-800/60 pt-2 leading-relaxed">
              以上为模板默认规则；实际运行以冻结版本源码 + 部署参数 + 风控规则为准。SDK v1 无券商侧保护单：止损/止盈均由策略逻辑在平台内市价触发，网关断线期间持仓无平台保护。
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
