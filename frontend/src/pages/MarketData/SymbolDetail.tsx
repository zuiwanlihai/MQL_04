import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, HardDriveDownload } from 'lucide-react';
import KlineChart from '@/components/market/KlineChart';
import FloatingToolbar from '@/components/market/FloatingToolbar';
import SymbolDropdown from '@/components/market/SymbolDropdown';
import OneClickTrade from '@/components/market/OneClickTrade';
import ManualTradePanel from '@/components/trading/ManualTradePanel';
import { useAccounts, useActiveAccount } from '@/hooks/useAccounts';
import { useTradeSnapshot } from '@/hooks/useManualTrade';
import { useKlines, useQuotesStream, useSymbol } from '@/hooks/useMarket';
import { apiError } from '@/lib/api';
import { loadKlines, saveKlines } from '@/lib/klineCache';
import { useWatchlist } from '@/lib/watchlist';
import {
  changeClass,
  formatPercent,
  formatPrice,
} from '@/lib/market';
import { useThemeStore } from '@/store/useThemeStore';
import type { BrokerOrder, BrokerPosition, MarketTimeframe, PriceBar } from '@/types/api';

const TIMEFRAMES: MarketTimeframe[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

// 周期 → 秒（与后端 MarketTimeframe 对齐；D1 按 MT5 服务器日界取整）
const TF_SECONDS: Record<MarketTimeframe, number> = {
  M1: 60,
  M5: 300,
  M15: 900,
  M30: 1800,
  H1: 3600,
  H4: 14400,
  D1: 86400,
};

export default function SymbolDetail() {
  const { symbolId } = useParams<{ symbolId: string }>();
  const [timeframe, setTimeframe] = useState<MarketTimeframe>('H1');
  const redUpGreenDown = useThemeStore((s) => s.redUpGreenDown);

  const symbolQuery = useSymbol(symbolId);
  const { quotes: quoteMap, degraded } = useQuotesStream(symbolId ? [symbolId] : []);
  const quote = symbolId ? quoteMap.get(symbolId) : undefined;

  const symbol = symbolQuery.data;
  const digits = symbol?.digits ?? 5;

  // 当前登录的 MT5 账户 — 手动交易面板目标（登录即路由）
  const accounts = useAccounts();
  const activeQ = useActiveAccount();
  const activeAccountId = useMemo(() => {
    const online = (activeQ.data ?? []).find((i) => i.account !== null);
    if (!online?.account) return null;
    return (
      accounts.data?.items.find(
        (a) => a.login === online.account!.login && a.server === online.account!.server,
      )?.id ?? null
    );
  }, [activeQ.data, accounts.data]);
  // 账户类型（real → 下单需二次确认）
  const activeAccountType = useMemo(() => {
    const online = (activeQ.data ?? []).find((i) => i.account !== null);
    return online?.account?.account_type ?? null;
  }, [activeQ.data]);

  // 账户镜像快照（10s 轮询）→ 当前品种的持仓/挂单，画到 K 线图上
  const snapQ = useTradeSnapshot(activeAccountId ?? undefined);
  // 账户货币（盈亏计价，如 USD）— 快照 account.currency
  const accountCurrency = useMemo(() => {
    const acct = snapQ.data?.account;
    return acct && typeof acct.currency === 'string' ? acct.currency : null;
  }, [snapQ.data]);
  const chartPositions = useMemo<BrokerPosition[]>(
    () => (snapQ.data?.positions ?? []).filter((p) => p.symbol === symbolId),
    [snapQ.data, symbolId],
  );
  const chartOrders = useMemo<BrokerOrder[]>(
    () => (snapQ.data?.orders ?? []).filter((o) => o.symbol === symbolId),
    [snapQ.data, symbolId],
  );

  // K 线根数：覆盖当前品种最早持仓的开仓时间，「开单线」才能从左画到开单那根 K 线
  // （跨度按周期秒数折算为根数上界，周末/休市只多不少；上限与后端一致 5000）
  const klineLimit = useMemo(() => {
    const earliest = chartPositions.reduce((min, p) => Math.min(min, p.time), Infinity);
    if (!Number.isFinite(earliest)) return 500;
    const need = Math.ceil((Date.now() / 1000 - earliest) / TF_SECONDS[timeframe]) + 50;
    return Math.min(5000, Math.max(500, need));
  }, [chartPositions, timeframe]);
  const klinesQuery = useKlines(symbolId, timeframe, klineLimit, 30_000);

  // ── 自选品种 K 线本地缓存（服务器真源 + 浏览器读加速）──────────
  // 拉取成功即落盘（仅自选品种）；缓存键独立于自选关系——
  // 把品种从自选删除后，已缓存数据不会被清理，仍可离线查看。
  const { favorites } = useWatchlist();
  const isFav = favorites.includes(symbolId ?? '');
  useEffect(() => {
    if (!symbolId || !isFav) return;
    if (klinesQuery.data && klinesQuery.data.length > 0) {
      saveKlines(symbolId, timeframe, klinesQuery.data).catch(() => {
        /* 落盘失败不影响看图 */
      });
    }
  }, [symbolId, timeframe, klinesQuery.data, isFav]);

  // 本地缓存兜底：API 未返回（首屏加载中 / 断网失败）时用缓存绘制
  const [cachedBars, setCachedBars] = useState<PriceBar[]>([]);
  useEffect(() => {
    let alive = true;
    setCachedBars([]);
    if (!symbolId) return;
    loadKlines(symbolId, timeframe).then((bars) => {
      if (alive) setCachedBars(bars as PriceBar[]);
    });
    return () => {
      alive = false;
    };
  }, [symbolId, timeframe]);
  const bars: PriceBar[] = klinesQuery.data ?? (cachedBars as PriceBar[]);
  const usingCache = !klinesQuery.data && bars.length > 0;

  // 实时尾根蜡烛：用 WS 报价的 bid 聚合（MT5 K 线默认 bid 价）。
  // quote.bar_time 与 bar.time 同一 UTC 伪 epoch 时间轴，直接对桶取整；
  // bar_time 缺失时回退 quote.time（服务器时区轴，会偏移）。
  const liveBar = useMemo<PriceBar | undefined>(() => {
    const bars = klinesQuery.data;
    if (!quote || !bars || bars.length === 0) return undefined;
    const t = Date.parse(quote.bar_time ?? quote.time) / 1000;
    if (!Number.isFinite(t)) return undefined;
    const bucket = Math.floor(t / TF_SECONDS[timeframe]) * TF_SECONDS[timeframe];
    const last = bars[bars.length - 1];
    if (bucket === last.time) {
      return {
        ...last,
        close: quote.bid,
        high: Math.max(last.high, quote.bid),
        low: Math.min(last.low, quote.bid),
      };
    }
    if (bucket > last.time) {
      return { time: bucket, open: quote.bid, high: quote.bid, low: quote.bid, close: quote.bid };
    }
    return undefined;
  }, [quote, klinesQuery.data, timeframe]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            to="/market"
            className="btn-ghost px-2"
            title="返回行情总览"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <SymbolDropdown current={symbolId ?? ''} />
            <p className="text-sm text-gray-500 mt-0.5">
              {symbol?.description ?? (symbolQuery.isError ? '品种加载失败' : '加载中…')}
            </p>
          </div>
        </div>

        {/* 一键交易条 — 买入 / 卖出 / 全部平仓（当前品种） */}
        <div className="flex-1 flex justify-center px-4">
          <OneClickTrade
            accountId={activeAccountId}
            accountType={activeAccountType}
            symbol={symbolId ?? ''}
            positions={chartPositions}
            digits={digits}
            bid={quote?.bid ?? null}
            ask={quote?.ask ?? null}
            tickValue={symbol?.tick_value ?? null}
            tickSize={symbol?.tick_size ?? null}
            currency={accountCurrency}
          />
        </div>

        {/* 报价头 — WS 实时推送 */}
        {quote && symbol && (
          <div className="flex items-center gap-6 font-mono">
            <div className="text-right">
              <p className="text-xs text-gray-500">买价</p>
              <p className="text-lg text-gray-100">{formatPrice(quote.bid, digits)}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-gray-500">卖价</p>
              <p className="text-lg text-gray-100">{formatPrice(quote.ask, digits)}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-gray-500">涨跌幅</p>
              <p className={`text-lg ${changeClass(quote.change_percent, redUpGreenDown)}`}>
                {formatPercent(quote.change_percent)}
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs text-gray-500">点差</p>
              <p className="text-lg text-gray-300">{quote.spread}</p>
            </div>
          </div>
        )}
      </div>

      {degraded && (
        <div className="card border-chart-yellow/40 bg-chart-yellow/10">
          <div className="card-body text-sm text-chart-yellow">
            行情源波动，正在自动恢复…
          </div>
        </div>
      )}

      {/* 周期工具条悬浮在 K 线图上，可拖动 */}
      <div className="relative">
        <FloatingToolbar storageKey={`tf:${symbolId ?? 'default'}`} defaultX={8} defaultY={8}>
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-3 py-1.5 text-sm rounded-md font-mono transition-colors ${
                timeframe === tf
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-surface-800'
              }`}
            >
              {tf}
            </button>
          ))}
          <span className="px-2 text-2xs text-gray-600 whitespace-nowrap">
            共 {bars.length} 根 · 北京时间
          </span>
          {usingCache && (
            <span
              className="px-2 py-0.5 text-2xs rounded bg-blue-600/20 text-blue-300 whitespace-nowrap inline-flex items-center gap-1"
              title="当前显示本地缓存的K线（自选品种已落盘），网络恢复后自动更新"
            >
              <HardDriveDownload className="w-3 h-3" /> 本地缓存
            </span>
          )}
        </FloatingToolbar>

        <KlineChart
          bars={bars}
          liveBar={liveBar}
          loading={klinesQuery.isLoading && bars.length === 0}
          error={klinesQuery.isError && bars.length === 0 ? apiError(klinesQuery.error) : null}
          storageKey={symbolId && timeframe ? `${symbolId}:${timeframe}` : undefined}
          digits={digits}
          positions={chartPositions}
          orders={chartOrders}
        />
      </div>

      {/* 手动交易（嵌入行情页，目标=当前登录账户，品种预填当前品种） */}
      {activeAccountId ? (
        <ManualTradePanel
          accountId={activeAccountId}
          defaultSymbol={symbolId ?? 'EURUSD'}
          title={`手动交易 · ${symbolId ?? ''}`}
        />
      ) : (
        <div className="card">
          <div className="card-body text-sm text-gray-500">
            手动交易需登录 MT5 账户 — 当前未检测到已登录账户（离线/未登录）。
          </div>
        </div>
      )}
    </div>
  );
}
