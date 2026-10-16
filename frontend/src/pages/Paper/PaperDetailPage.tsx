import { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, Loader2, Pause, Play, RotateCcw, Square } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { EquityChart } from '@/components/backtest/Charts';
import ManualTradePanel from '@/components/trading/ManualTradePanel';
import {
  PAPER_MODE_LABELS,
  PAPER_STATUS_LABELS,
  usePaperLogs,
  usePaperRun,
  usePaperTrades,
  usePausePaperRun,
  useResetPaperAnchor,
  useResetPaperRun,
  useResumePaperRun,
  useStopPaperRun,
} from '@/hooks/usePaper';
import { useStrategies } from '@/hooks/useStrategies';
import { useKlines, useQuotesStream } from '@/hooks/useMarket';
import { PriceLadderChart } from '@/components/trading/PriceLadderChart';
import { GridLevelTable } from '@/components/trading/GridLevelTable';
import { apiError } from '@/lib/api';
import type { MarketTimeframe, PaperRunStatus } from '@/types/api';

const STATUS_STYLES: Record<PaperRunStatus, string> = {
  pending: 'bg-surface-700 text-gray-300 border-gray-600',
  running: 'bg-blue-500/15 text-blue-300 border-blue-500/40',
  paused: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopping: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  stopped: 'bg-gray-500/15 text-gray-400 border-gray-500/40',
  failed: 'bg-loss/15 text-loss border-loss/40',
};

const money = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const dt = (s: string | null) =>
  s ? new Date(s).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—';

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

const LOG_KINDS = ['', 'log', 'signal', 'system'] as const;
const LOG_KIND_LABELS: Record<string, string> = { '': '全部类型', log: '日志', signal: '信号', system: '系统' };
const LOG_LEVELS = ['', 'info', 'warn', 'error'] as const;
const LOG_LEVEL_LABELS: Record<string, string> = { '': '全部级别', info: 'info', warn: 'warn', error: 'error' };

export default function PaperDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const run = usePaperRun(runId);
  const strategies = useStrategies({ page: 1, pageSize: 100 });
  const pause = usePausePaperRun();
  const resume = useResumePaperRun();
  const stop = useStopPaperRun();
  const reset = useResetPaperRun();
  const resetAnchor = useResetPaperAnchor();

  const [tab, setTab] = useState<
    'equity' | 'aiNames' | 'positions' | 'trades' | 'logs' | 'config'
  >('equity');
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
  const [logLevel, setLogLevel] = useState('');

  const detail = run.data;
  const status = detail?.status;
  const live = status === 'running' || status === 'pending' || status === 'stopping';
  const anyPending = pause.isPending || resume.isPending || stop.isPending || reset.isPending;

  const trades = usePaperTrades(runId, tradePage, tab === 'trades');
  const logs = usePaperLogs(runId, {
    page: logPage,
    kind: logKind || undefined,
    level: logLevel || undefined,
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
    return <div className="card"><div className="card-body text-center text-gray-500 py-10">加载虚拟下单任务…</div></div>;
  }

  const runStrategy = strategies.data?.items.find((s) => s.id === detail.strategy_id);
  const strategyName = runStrategy?.name;
  const snapAccount = (detail.config_snapshot?.account ?? null) as
    | { id: string; login: number; server: string }
    | null;
  const equityPoints = (detail.equity_history ?? []).map((p) => ({
    time: p.time ?? new Date().toISOString(),
    equity: p.equity,
    balance: p.balance,
    drawdown: 0,
    baseline: detail.initial_deposit,
  }));
  const realizedPnl = (detail.equity ?? 0) - (detail.balance ?? 0) - (detail.unrealized_pnl ?? 0);

  // ── 右半屏价格网格（网格策略）：锚定价来自 ctx.storage 快照 ──
  const liveQuote = quotes.get(detail.symbol);
  const currentPrice =
    liveQuote?.bid ??
    (detail.positions ?? []).filter((p) => p.direction === 'buy')[0]?.current_price ??
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
    .filter((p) => p.direction === 'buy')
    .map((p) => p.open_price);
  const sellOpenPrices = (detail.positions ?? [])
    .filter((p) => p.direction === 'sell')
    .map((p) => p.open_price);
  // 最近一笔成交（开仓方向 + 实际成交价；平仓成交不体现在 positions 里）
  const lastFill = (() => {
    const positions = detail.positions ?? [];
    if (!positions.length) return null;
    const last = positions.reduce((a, b) =>
      (b.opened_at ?? '') >= (a.opened_at ?? '') ? b : a,
    );
    return { price: last.open_price, side: last.direction };
  })();

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <Link to="/trading/paper" className="text-gray-500 hover:text-gray-300 text-sm">
              虚拟下单中心
            </Link>
            <span className="text-gray-700">/</span>
            <h1 className="text-xl font-semibold text-white">{strategyName ?? detail.strategy_id.slice(0, 8)}</h1>
            <span className={`px-2 py-0.5 rounded border text-xs ${STATUS_STYLES[detail.status]}`}>
              {PAPER_STATUS_LABELS[detail.status]}
            </span>
            {live && <Loader2 className="w-4 h-4 animate-spin text-blue-400" />}
          </div>
          <p className="text-xs text-gray-500 mt-1 font-mono">
            {detail.symbol} / {detail.timeframe} · {PAPER_MODE_LABELS[detail.execution_mode]}档 ·{' '}
            {detail.strategy_version == null ? '工作副本' : `v${detail.strategy_version}`} · 杠杆 1:{detail.leverage} ·{' '}
            初始资金 {money(detail.initial_deposit)}
            {snapAccount && (
              <>
                {' · '}
                <span className="text-gray-400">
                  绑定账户 {snapAccount.login}@{snapAccount.server}
                </span>
              </>
            )}
          </p>
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
            <button type="button" className="btn-primary" disabled={anyPending} onClick={() => resume.mutate(detail.id)}>
              <Play className="w-4 h-4" /> 恢复
            </button>
          )}
          {(status === 'running' || status === 'paused') && (
            <button
              type="button"
              className="btn-secondary border-loss/40 text-loss hover:bg-loss/10"
              disabled={anyPending}
              onClick={() => {
                if (window.confirm('停止该虚拟下单？停止时将以最后收盘价平掉全部持仓。')) {
                  stop.mutate({ id: detail.id, closePositions: true });
                }
              }}
            >
              <Square className="w-4 h-4" /> 停止（平仓）
            </button>
          )}
          {(status === 'stopped' || status === 'failed') && (
            <button
              type="button"
              className="btn-secondary"
              disabled={anyPending}
              onClick={() => {
                if (window.confirm('重置将清空全部成交、日志与账户状态，并重新拉取历史启动。确认？')) {
                  reset.mutate(detail.id);
                }
              }}
            >
              <RotateCcw className="w-4 h-4" /> 重置并重新启动
            </button>
          )}
        </div>
      </div>

      {(pause.isError || resume.isError || stop.isError || reset.isError) && (
        <div className="px-4 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
          操作失败：{apiError(pause.error ?? resume.error ?? stop.error ?? reset.error)}
        </div>
      )}

      {/* account mirror */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat label="净值" value={money(detail.equity)} />
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
            ['equity', '权益曲线'],
            ['aiNames', strategyName ?? 'AI 名称'],
            ['positions', `当前持仓（${(detail.positions ?? []).length}）`],
            ['trades', '成交明细'],
            ['logs', '运行日志'],
            ['config', '配置快照'],
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

      {tab === 'equity' && (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">
            权益曲线（{equityPoints.length} 点 · 累计收益{' '}
            <span className={(detail.equity ?? 0) >= detail.initial_deposit ? 'text-profit' : 'text-loss'}>
              {money(((detail.equity ?? 0) / detail.initial_deposit - 1) * 100)}%
            </span>
            ，已实现盈亏 {money(realizedPnl)}）
          </h3>
        </div>
        <div className="card-body">
          {equityPoints.length > 1 ? (
            <EquityChart points={equityPoints} height={chartH} />
          ) : (
            <p className="text-sm text-gray-500 text-center py-8">暂无权益数据</p>
          )}
        </div>
      </div>
      )}

      {tab === 'aiNames' && (
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-medium text-gray-200">
              价格网格 · {detail.symbol}
              {anchor != null && stepPrice > 0 && (
                <span className="text-gray-500 font-normal ml-2">
                  锚定 {anchor.toFixed(priceDigits)} · 间距 {money(stepPrice, priceDigits)} ×{' '}
                  {maxLevels} 格
                </span>
              )}
            </h3>
            <span className="text-2xs text-gray-500">
              AI 策略：
              <span className="text-sky-300 font-medium">{strategyName ?? '未命名'}</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
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
        </div>
        <div className="card-body space-y-4">
          <PriceLadderChart
            height={Math.max(320, chartH - 180)}
            digits={priceDigits}
            currentPrice={currentPrice}
            anchor={anchor}
            stepPrice={stepPrice}
            maxLevels={maxLevels}
            openPrices={openPrices}
            sellOpenPrices={sellOpenPrices}
            history={priceHistory}
          />
          <GridLevelTable
            anchor={anchor}
            stepPrice={stepPrice}
            maxLevels={maxLevels}
            openPrices={openPrices}
            sellOpenPrices={sellOpenPrices}
            digits={priceDigits}
          />
        </div>
      </div>
      )}

      {tab === 'positions' && (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">
            当前持仓（{(detail.positions ?? []).length}）
          </h3>
        </div>
        <div className="card-body p-0 overflow-x-auto">
          {(detail.positions ?? []).length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-6">当前无持仓</p>
          ) : (
            <table className="table-dark">
              <thead>
                <tr>
                  <th>#</th>
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
                  <tr key={p.id} className="text-xs">
                    <td className="font-mono text-gray-500">{p.id}</td>
                    <td className={p.direction === 'buy' ? 'text-profit' : 'text-loss'}>
                      {p.direction === 'buy' ? '买' : '卖'}
                    </td>
                    <td className="font-mono">{money(p.volume)}</td>
                    <td className="font-mono">{money(p.open_price, 5)}</td>
                    <td className="font-mono">{money(p.current_price, 5)}</td>
                    <td className={`text-right font-mono ${p.unrealized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {money(p.unrealized_pnl)}
                    </td>
                    <td className="text-gray-400 whitespace-nowrap">{dt(p.opened_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      )}

      {/* manual trading on the bound broker account（手动交易嵌入虚拟下单页） */}
      {detail.account_id && (
        <ManualTradePanel accountId={detail.account_id} title="手动交易（券商账户）" />
      )}

      {tab === 'trades' && (
        <div className="card overflow-hidden">
          <table className="table-dark">
            <thead>
              <tr>
                <th>#</th>
                <th>方向</th>
                <th>手数</th>
                <th>开仓时间</th>
                <th>开仓价</th>
                <th>平仓时间</th>
                <th>平仓价</th>
                <th className="text-right">盈亏</th>
                <th className="text-right">收益率</th>
              </tr>
            </thead>
            <tbody>
              {(trades.data?.items ?? []).map((t) => (
                <tr key={t.id} className="text-xs">
                  <td className="font-mono text-gray-500">{t.ticket}</td>
                  <td className={t.direction === 'buy' ? 'text-profit' : 'text-loss'}>
                    {t.direction === 'buy' ? '买' : '卖'}
                  </td>
                  <td className="font-mono">{money(t.volume)}</td>
                  <td className="text-gray-400 whitespace-nowrap">{dt(t.open_time)}</td>
                  <td className="font-mono">{money(t.open_price, 5)}</td>
                  <td className="text-gray-400 whitespace-nowrap">{dt(t.close_time)}</td>
                  <td className="font-mono">{money(t.close_price, 5)}</td>
                  <td className={`text-right font-mono ${t.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {money(t.profit)}
                    {t.is_forced && <span className="text-loss ml-1">⚠</span>}
                  </td>
                  <td className={`text-right font-mono ${t.profit_percent >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {money(t.profit_percent)}%
                  </td>
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
            <select
              className="input-dark w-28 text-xs"
              value={logLevel}
              onChange={(e) => {
                setLogLevel(e.target.value);
                setLogPage(1);
              }}
            >
              {LOG_LEVELS.map((l) => (
                <option key={l} value={l}>
                  {LOG_LEVEL_LABELS[l]}
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
                          l.level === 'error'
                            ? 'bg-loss/15 text-loss border-loss/40'
                            : l.level === 'warn'
                              ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40'
                              : 'bg-surface-700 text-gray-400 border-gray-700'
                        }`}
                      >
                        {l.kind === 'signal' ? '信号' : l.kind === 'system' ? '系统' : '日志'}·{l.level}
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
      )}
    </div>
  );
}
