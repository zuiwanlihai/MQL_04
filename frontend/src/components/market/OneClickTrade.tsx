import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2 } from 'lucide-react';
import { useLiveMeta } from '@/hooks/useLive';
import {
  useManualCancel,
  useManualClose,
  useManualOrder,
  useManualPending,
  useTradeSnapshot,
} from '@/hooks/useManualTrade';
import { apiError } from '@/lib/api';
import { formatPrice } from '@/lib/market';
import type {
  BrokerPosition,
  BrokerOrder,
  ManualPendingBody,
  PendingOrderType,
  TradeActionResult,
} from '@/types/api';

const VOL_KEY = 'oneclick:volume';
const DEVIATION = 20; // 与手动交易面板默认滑点一致

const PENDING_TYPE_META: Record<
  PendingOrderType,
  { label: string; ref: 'ask' | 'bid'; hint: string }
> = {
  buy_limit: { label: '买限价', ref: 'ask', hint: '低于现价买入' },
  sell_limit: { label: '卖限价', ref: 'bid', hint: '高于现价卖出' },
  buy_stop: { label: '买突破', ref: 'ask', hint: '高于现价追买' },
  sell_stop: { label: '卖突破', ref: 'bid', hint: '低于现价追卖' },
};

// BrokerOrder.type 数字 → 挂单类型（MT5 ORDER_TYPE 枚举）
const ORDER_TYPE_NUM: Record<number, string> = {
  2: 'buy_limit',
  3: 'sell_limit',
  4: 'buy_stop',
  5: 'sell_stop',
};

function loadVolume(): string {
  try {
    const v = localStorage.getItem(VOL_KEY);
    if (v && Number(v) > 0) return v;
  } catch {
    /* ignore */
  }
  return '0.01';
}

interface Props {
  /** 当前登录账户 id（未登录为 null → 按钮禁用） */
  accountId: string | null;
  /** 账户类型（real 时下单需确认） */
  accountType?: 'demo' | 'real' | 'contest' | null;
  /** 当前品种 */
  symbol: string;
  /** 当前品种持仓（全部平仓的范围） */
  positions: BrokerPosition[];
  /** 价格小数位（成交提示用） */
  digits?: number;
  /** 实时买价/卖价（挂单默认价参考 + 持仓盈亏实时插值） */
  bid?: number | null;
  ask?: number | null;
  /** 品种合约信息（盈亏实时插值：每跳价值 / 最小变动价位） */
  tickValue?: number | null;
  tickSize?: number | null;
  /** 账户货币（盈亏计价货币，如 USD） */
  currency?: string | null;
}

/**
 * K 线图页头部一键交易条：手数 + 买入 / 卖出 / 挂单（展开面板） / 全部平仓。
 * 复用手动交易的后端通道（风控闸 + 审计链），结果以浮动提示短暂展示。
 */
export default function OneClickTrade({
  accountId,
  accountType,
  symbol,
  positions,
  digits = 5,
  bid = null,
  ask = null,
  tickValue = null,
  tickSize = null,
  currency = null,
}: Props) {
  const liveMeta = useLiveMeta();
  const enabled = liveMeta.data?.manual_trade_enabled ?? false;

  const [volume, setVolume] = useState(loadVolume);
  const [closing, setClosing] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const msgTimer = useRef<number | null>(null);

  const order = useManualOrder();
  const close = useManualClose();
  const pending = useManualPending();
  const cancel = useManualCancel();
  const busy =
    order.isPending || close.isPending || closing || pending.isPending || cancel.isPending;

  // ── 挂单面板 ──
  const [showPending, setShowPending] = useState(false);
  const [pType, setPType] = useState<PendingOrderType>('buy_limit');
  const [pPrice, setPPrice] = useState('');
  const [pSl, setPSl] = useState('');
  const [pTp, setPTp] = useState('');
  // 该账户镜像（挂单列表 + 撤销）；面板打开时才启用轮询
  const snapshot = useTradeSnapshot(showPending && accountId ? accountId : undefined);
  const symOrders = (snapshot.data?.orders ?? []).filter(
    (o) => o.symbol === symbol.toUpperCase(),
  );

  function refPrice(t: PendingOrderType): string {
    const p = PENDING_TYPE_META[t].ref === 'ask' ? ask : bid;
    return p != null && p > 0 ? String(p) : '';
  }

  function changePType(t: PendingOrderType) {
    setPType(t);
    setPPrice(refPrice(t));
  }

  function togglePending() {
    if (!showPending) setPPrice(refPrice(pType));
    setShowPending((v) => !v);
  }

  function submitPending() {
    if (disabledReason || busy || !accountId) return;
    const vol = Number(volume);
    const price = Number(pPrice);
    if (!(vol > 0)) {
      showMsg(false, '手数无效 — 需大于 0');
      return;
    }
    if (!(price > 0)) {
      showMsg(false, '挂单价格无效 — 需大于 0');
      return;
    }
    const sl = pSl.trim() ? Number(pSl) : null;
    const tp = pTp.trim() ? Number(pTp) : null;
    if (sl != null && !(sl > 0)) {
      showMsg(false, '止损价无效');
      return;
    }
    if (tp != null && !(tp > 0)) {
      showMsg(false, '止盈价无效');
      return;
    }
    const meta = PENDING_TYPE_META[pType];
    if (
      accountType === 'real' &&
      !window.confirm(
        `实盘账户挂单确认\n\n${meta.label} ${vol} 手 ${symbol} @ ${price}\n\n真实资金操作，请确认。`,
      )
    ) {
      return;
    }
    const body: ManualPendingBody = {
      account_id: accountId,
      symbol,
      order_type: pType,
      volume: vol,
      price,
      sl,
      tp,
    };
    pending.mutate(body, {
      onSuccess: (res) => {
        if (res.success) {
          showMsg(true, `挂单已提交 — ${meta.label} ${res.volume ?? vol} 手 @${res.price != null ? formatPrice(res.price, digits) : formatPrice(price, digits)}`);
          setPSl('');
          setPTp('');
        } else {
          showMsg(
            false,
            `挂单被拒：${res.error_message ?? res.error_code ?? `retcode ${res.retcode ?? '—'}`}`,
          );
        }
      },
      onError: (err) => showMsg(false, apiError(err)),
    });
  }

  function cancelOrder(o: BrokerOrder) {
    if (disabledReason || !accountId || cancel.isPending) return;
    const label = orderLabel(o);
    if (!window.confirm(`撤销挂单？\n\n${label}`)) return;
    cancel.mutate(
      { account_id: accountId, ticket: o.ticket },
      {
        onSuccess: (res) => {
          if (res.success) showMsg(true, `挂单 ${o.ticket} 已撤销`);
          else
            showMsg(
              false,
              `撤单被拒：${res.error_message ?? res.error_code ?? `retcode ${res.retcode ?? '—'}`}`,
            );
        },
        onError: (err) => showMsg(false, apiError(err)),
      },
    );
  }

  function orderLabel(o: BrokerOrder): string {
    const t = ORDER_TYPE_NUM[o.type] as PendingOrderType | undefined;
    return `${t ? PENDING_TYPE_META[t].label : `type${o.type}`} ${o.volume} 手 ${o.symbol} @${formatPrice(o.price_open, digits)}`;
  }

  useEffect(
    () => () => {
      if (msgTimer.current) window.clearTimeout(msgTimer.current);
    },
    [],
  );

  function showMsg(ok: boolean, text: string) {
    setMsg({ ok, text });
    if (msgTimer.current) window.clearTimeout(msgTimer.current);
    msgTimer.current = window.setTimeout(() => setMsg(null), 6000);
  }

  function changeVolume(v: string) {
    setVolume(v);
    try {
      localStorage.setItem(VOL_KEY, v);
    } catch {
      /* ignore */
    }
  }

  // ── 持仓盈亏：快照 profit（10s 轮询）+ 实时报价插值 ──
  // 买：平仓价=bid；卖：平仓价=ask。Δ盈利 = (现价 - 快照价) / tickSize × tickValue × 手数
  function liveProfit(p: BrokerPosition): number {
    let adj = 0;
    const cur = p.type === 'buy' ? bid : ask;
    if (tickValue && tickSize && tickValue > 0 && tickSize > 0 && cur != null && p.price_current > 0) {
      const delta = p.type === 'buy' ? cur - p.price_current : p.price_current - cur;
      adj = (delta / tickSize) * tickValue * p.volume;
    }
    return p.profit + adj;
  }

  function netProfit(p: BrokerPosition): number {
    return p.profit + p.swap + p.commission;
  }

  const livePositions = positions.map((p) => ({ pos: p, pl: liveProfit(p) }));
  const totalPl = livePositions.reduce((s, x) => s + x.pl, 0);
  const totalNet = positions.reduce((s, p) => s + netProfit(p), 0);

  const disabledReason = !symbol
    ? '品种未加载'
    : !accountId
      ? '登录 MT5 账户后可交易'
      : !enabled
        ? '手动交易未启用（MANUAL_TRADE_ENABLED=false）'
        : null;

  function submitOrder(side: 'buy' | 'sell') {
    if (disabledReason || busy) return;
    const vol = Number(volume);
    if (!(vol > 0)) {
      showMsg(false, '手数无效 — 需大于 0');
      return;
    }
    const sideText = side === 'buy' ? '买入' : '卖出';
    if (
      accountType === 'real' &&
      !window.confirm(`实盘账户下单确认\n\n${sideText} ${vol} 手 ${symbol}\n\n真实资金操作，请确认。`)
    ) {
      return;
    }
    order.mutate(
      { account_id: accountId!, symbol, side, volume: vol, deviation: DEVIATION },
      {
        onSuccess: (res) => {
          if (res.success) {
            showMsg(
              true,
              `${sideText} ${res.volume ?? vol} 手成交 @${res.price != null ? formatPrice(res.price, digits) : '—'}`,
            );
          } else {
            showMsg(
              false,
              `下单被拒：${res.error_message ?? res.error_code ?? `retcode ${res.retcode ?? '—'}`}`,
            );
          }
        },
        onError: (err) => showMsg(false, apiError(err)),
      },
    );
  }

  async function closeAll() {
    if (disabledReason || busy || positions.length === 0) return;
    const total = Number(positions.reduce((s, p) => s + p.volume, 0).toFixed(2));
    if (
      !window.confirm(
        `确认全部平仓？\n\n${symbol} · ${positions.length} 笔持仓 · 合计 ${total} 手\n\n` +
          `注意：手动平仓会影响该账户上的持仓（包括实盘策略持仓）。`,
      )
    ) {
      return;
    }
    setClosing(true);
    try {
      const rs = await Promise.allSettled(
        positions.map((p) => close.mutateAsync({ account_id: accountId!, ticket: p.ticket })),
      );
      const done = rs.filter((r) => r.status === 'fulfilled' && r.value.success).length;
      const failed = rs.length - done;
      if (failed === 0) {
        showMsg(true, `全部平仓完成 — ${done} 笔`);
      } else {
        const rej = rs.find((r): r is PromiseRejectedResult => r.status === 'rejected');
        const rejected = rs.find(
          (r): r is PromiseFulfilledResult<TradeActionResult> =>
            r.status === 'fulfilled' && !r.value.success,
        );
        const why = rej
          ? apiError(rej.reason)
          : rejected
            ? (rejected.value.error_message ?? rejected.value.error_code ?? '被拒')
            : '未知原因';
        showMsg(false, `平仓完成 ${done} 笔 / 失败 ${failed} 笔：${why}`);
      }
    } finally {
      setClosing(false);
    }
  }

  const btnBase =
    'flex items-center gap-1.5 px-4 py-1.5 rounded-md text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed';

  return (
    <div className="relative flex flex-wrap items-center gap-x-2 gap-y-1.5" data-testid="one-click-trade">
      {/* 当前品种持仓盈亏 — 按货币汇总：同币种下只显示 货币 / 总手数 / 笔数 / 总盈亏 */}
      {livePositions.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="position-pnl">
          <span
            className="text-xs font-mono font-medium text-gray-300 pr-0.5"
            title={`当前品种 ${symbol.toUpperCase()} 的持仓`}
          >
            {symbol.toUpperCase()}
          </span>
          <span
            className={`flex items-center gap-1.5 px-2 py-0.5 rounded bg-surface-800/70 cursor-default ${
              totalPl >= 0 ? 'border border-profit/20' : 'border border-loss/20'
            }`}
            title={
              livePositions
                .map(({ pos, pl }) =>
                  `#${pos.ticket} ${pos.type === 'buy' ? '买' : '卖'} ${pos.volume} 手 @` +
                  `${formatPrice(pos.price_open, digits)} → ${pl >= 0 ? '+' : ''}${pl.toFixed(2)}${currency ? ` ${currency}` : ''}`
                )
                .join('\n') +
              `\n合计 ${totalPl >= 0 ? '+' : ''}${totalPl.toFixed(2)}${currency ? ` ${currency}` : ''}（净值 ${totalNet.toFixed(2)}${currency ? ` ${currency}` : ''}）`
            }
          >
            <span className="text-xs text-gray-500">{currency ?? '—'}</span>
            <span className="text-xs font-mono text-gray-300">
              {(positions.reduce((s, p) => s + p.volume, 0)).toFixed(2)}手
            </span>
            <span className="text-xs text-gray-500">×</span>
            <span className="text-xs font-mono text-gray-300">{livePositions.length}笔</span>
            <span
              className={`text-xs font-mono font-semibold ${totalPl >= 0 ? 'text-profit' : 'text-loss'}`}
            >
              {totalPl >= 0 ? '+' : ''}
              {totalPl.toFixed(2)}
            </span>
          </span>
        </div>
      )}
      {/* 操作按钮组：ml-auto 钉右侧，flex-shrink-0 永不被持仓标签挤出屏幕 */}
      <div className="ml-auto flex items-center gap-2 flex-shrink-0">
        <label className="flex items-center gap-1.5" title="下单手数（自动记忆）">
          <span className="text-xs text-gray-500 whitespace-nowrap">手数</span>
          <input
            className="input-dark w-16 px-2 py-1.5 text-center font-mono text-sm"
            value={volume}
            onChange={(e) => changeVolume(e.target.value)}
            placeholder="0.01"
            inputMode="decimal"
          />
        </label>
        <button
          type="button"
          className={`${btnBase} bg-blue-600 hover:bg-blue-500 text-white`}
          disabled={Boolean(disabledReason) || busy}
          title={disabledReason ?? '市价买入（一键下单）'}
          onClick={() => submitOrder('buy')}
        >
          {order.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          买入
        </button>
        <button
          type="button"
          className={`${btnBase} bg-rose-600 hover:bg-rose-500 text-white`}
          disabled={Boolean(disabledReason) || busy}
          title={disabledReason ?? '市价卖出（一键下单）'}
          onClick={() => submitOrder('sell')}
        >
          {order.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          卖出
        </button>
        <button
          type="button"
          className="px-3 py-1.5 rounded-md border border-gray-600 text-gray-300 hover:bg-surface-800 hover:text-white text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
          disabled={Boolean(disabledReason) || busy}
          title={disabledReason ?? '挂限价/突破单'}
          onClick={togglePending}
        >
          {showPending ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          挂单
        </button>
        <button
          type="button"
          className="px-3 py-1.5 rounded-md border border-gray-600 text-gray-300 hover:bg-surface-800 hover:text-loss text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
          disabled={Boolean(disabledReason) || busy || positions.length === 0}
          title={
            disabledReason ??
            (positions.length === 0
              ? '当前品种无持仓'
              : `平掉 ${symbol} 的 ${positions.length} 笔持仓`)
          }
          onClick={closeAll}
        >
          {closing && <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" />}
          全部平仓
        </button>
      </div>
      {msg && (
        <span
          className={`text-xs font-mono max-w-64 truncate ${
            msg.ok ? 'text-profit' : 'text-loss'
          }`}
          title={msg.text}
        >
          {msg.ok ? '✓ ' : '✕ '}
          {msg.text}
        </span>
      )}
      {showPending && (
        <div
          className="absolute top-14 left-1/2 -translate-x-1/2 z-40 w-[440px] max-w-[92vw] rounded-lg border border-gray-700 bg-surface-900 shadow-xl p-3 space-y-2"
          data-testid="pending-panel"
        >
          <div className="grid grid-cols-[56px_1fr] items-center gap-x-2 gap-y-2">
            <span className="text-xs text-gray-500 whitespace-nowrap">类型</span>
            <select
              className="input-dark w-full px-2 py-1.5 text-sm"
              value={pType}
              onChange={(e) => changePType(e.target.value as PendingOrderType)}
            >
              {(Object.keys(PENDING_TYPE_META) as PendingOrderType[]).map((t) => (
                <option key={t} value={t}>
                  {PENDING_TYPE_META[t].label}（{PENDING_TYPE_META[t].hint}）
                </option>
              ))}
            </select>
            <span className="text-xs text-gray-500 whitespace-nowrap">价格</span>
            <input
              className="input-dark w-full px-2 py-1.5 font-mono text-sm"
              value={pPrice}
              onChange={(e) => setPPrice(e.target.value)}
              placeholder={`触发价（${PENDING_TYPE_META[pType].ref === 'ask' ? '卖价' : '买价'} ${refPrice(pType) || '—'}）`}
              inputMode="decimal"
            />
            <span className="text-xs text-gray-500 whitespace-nowrap">止损</span>
            <input
              className="input-dark w-full px-2 py-1.5 font-mono text-sm"
              value={pSl}
              onChange={(e) => setPSl(e.target.value)}
              placeholder="可选"
              inputMode="decimal"
            />
            <span className="text-xs text-gray-500 whitespace-nowrap">止盈</span>
            <input
              className="input-dark w-full px-2 py-1.5 font-mono text-sm"
              value={pTp}
              onChange={(e) => setPTp(e.target.value)}
              placeholder="可选"
              inputMode="decimal"
            />
            <span />
            <div className="flex justify-end">
              <button
                type="button"
                className={`${btnBase} bg-amber-600 hover:bg-amber-500 text-white whitespace-nowrap`}
                disabled={Boolean(disabledReason) || busy}
                title={disabledReason ?? '提交挂单'}
                onClick={submitPending}
              >
                {pending.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                挂单
              </button>
            </div>
          </div>
          <div className="border-t border-gray-700/60 pt-2">
            <p className="text-xs text-gray-500 mb-1">
              {symbol.toUpperCase()} 当前挂单（{symOrders.length}）
            </p>
            {symOrders.length === 0 ? (
              <p className="text-xs text-gray-600">无挂单</p>
            ) : (
              <ul className="space-y-1 max-h-28 overflow-y-auto">
                {symOrders.map((o) => (
                  <li key={o.ticket} className="flex items-center justify-between gap-2">
                    <span className="text-xs font-mono text-gray-300 truncate">
                      {orderLabel(o)}
                      {(o.sl > 0 || o.tp > 0) && (
                        <span className="text-gray-500">
                          {' '}
                          sl:{o.sl > 0 ? formatPrice(o.sl, digits) : '—'} tp:
                          {o.tp > 0 ? formatPrice(o.tp, digits) : '—'}
                        </span>
                      )}
                    </span>
                    <button
                      type="button"
                      className="text-xs px-2 py-0.5 rounded border border-gray-600 text-gray-400 hover:text-loss hover:border-loss/50 transition-colors whitespace-nowrap"
                      disabled={cancel.isPending}
                      onClick={() => cancelOrder(o)}
                    >
                      撤销
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
