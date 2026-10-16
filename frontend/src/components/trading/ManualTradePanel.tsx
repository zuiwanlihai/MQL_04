import { useState } from 'react';
import { AlertTriangle, Ban, Loader2, XCircle } from 'lucide-react';
import { useLiveMeta } from '@/hooks/useLive';
import {
  useManualCancel,
  useManualClose,
  useManualOrder,
  useTradeDeals,
  useTradeSnapshot,
} from '@/hooks/useManualTrade';
import { apiError } from '@/lib/api';
import { formatPrice } from '@/lib/market';
import type { TradeActionResult } from '@/types/api';

const ORDER_TYPE_LABELS: Record<number, string> = {
  0: '买入', 1: '卖出', 2: 'BuyLimit 挂单', 3: 'SellLimit 挂单',
  4: 'BuyStop 挂单', 5: 'SellStop 挂单',
};

const DEAL_ENTRY_LABELS: Record<number, string> = { 0: '开仓', 1: '平仓', 2: '反手' };

const money = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });

const dt = (epoch: number | null | undefined) =>
  epoch ? new Date(epoch * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—';

function ResultBanner({ res, onClose }: { res: TradeActionResult; onClose: () => void }) {
  const ok = res.success;
  return (
    <div
      className={`px-3 py-2 rounded border text-sm flex items-start justify-between gap-2 ${
        ok ? 'bg-profit/10 border-profit/40 text-profit' : 'bg-loss/10 border-loss/40 text-loss'
      }`}
    >
      <div className="space-y-0.5">
        <p className="font-medium">
          {ok
            ? `${res.action === 'order' ? '下单成交' : res.action === 'close' ? '平仓成交' : '撤单成功'}`
            : `${res.action === 'order' ? '下单被拒' : res.action === 'close' ? '平仓被拒' : '撤单被拒'}`}
          {res.retcode_text ? `（retcode ${res.retcode} · ${res.retcode_text}）` : ''}
        </p>
        {ok && (
          <p className="text-xs">
            deal={res.deal ?? '—'} · order={res.order ?? '—'} · 成交价{' '}
            {res.price != null ? formatPrice(res.price, 5) : '—'} · 手数 {res.volume ?? '—'}
          </p>
        )}
        {!ok && <p className="text-xs">{res.error_message ?? res.error_code ?? '未知错误'}</p>}
      </div>
      <button type="button" className="btn-ghost p-1" onClick={onClose} aria-label="关闭">
        <XCircle className="w-4 h-4" />
      </button>
    </div>
  );
}

interface Props {
  /** 目标 MT5 账户 id（由父级决定：行情=当前登录账户；虚拟下单/实盘=运行所属账户） */
  accountId: string;
  /** 预填品种（行情页传当前选中品种） */
  defaultSymbol?: string;
  /** 标题（默认「手动交易」） */
  title?: string;
}

/**
 * 手动交易面板（嵌入虚拟下单 / 实盘 / 行情页面共用）。
 * 每笔操作先过事前风控闸（单笔手数/持仓数/敞口/保证金/冻结）并写入审计链。
 */
export default function ManualTradePanel({ accountId, defaultSymbol }: Props) {
  const liveMeta = useLiveMeta();
  const enabled = liveMeta.data?.manual_trade_enabled ?? false;

  const [tab, setTab] = useState<'order' | 'positions' | 'orders' | 'deals'>('order');
  const [symbol, setSymbol] = useState(defaultSymbol ?? 'EURUSD');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [volume, setVolume] = useState('0.01');
  const [deviation, setDeviation] = useState('20');
  const [result, setResult] = useState<TradeActionResult | null>(null);
  const [dealsHours, setDealsHours] = useState(24);

  const snap = useTradeSnapshot(accountId, enabled);
  const deals = useTradeDeals(accountId, dealsHours, enabled);
  const order = useManualOrder();
  const close = useManualClose();
  const cancel = useManualCancel();

  const busy = order.isPending || close.isPending || cancel.isPending;
  const writeError = order.error ?? close.error ?? cancel.error;

  function submitOrder() {
    if (!enabled || busy) return;
    const vol = Number(volume);
    if (!(vol > 0)) return;
    order.mutate(
      {
        account_id: accountId,
        symbol: symbol.trim().toUpperCase(),
        side,
        volume: vol,
        deviation: Number(deviation) || 0,
      },
      { onSuccess: (res) => setResult(res) },
    );
  }

  function submitClose(ticket: number, posSymbol: string, posVolume: number) {
    if (!enabled || busy) return;
    const all = window.confirm(
      `确认市价平仓？\n\n持仓 #${ticket} · ${posSymbol} · ${posVolume} 手\n\n` +
        `注意：手动平仓会影响该账户上的所有持仓（包括实盘策略持仓）。`,
    );
    if (!all) return;
    close.mutate({ account_id: accountId, ticket }, { onSuccess: (res) => setResult(res) });
  }

  function submitCancel(ticket: number) {
    if (!enabled || busy) return;
    if (!window.confirm(`确认撤销挂单 #${ticket}？`)) return;
    cancel.mutate({ account_id: accountId, ticket }, { onSuccess: (res) => setResult(res) });
  }

  return (
    <div className="space-y-4">
      {!enabled && (
        <div className="px-4 py-3 rounded border border-yellow-500/40 bg-yellow-500/10 text-sm text-yellow-300 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          手动交易功能未启用（灰度开关 MANUAL_TRADE_ENABLED=false）。启用后方可下单/平仓/撤单，查看不受限。
        </div>
      )}

      {result && <ResultBanner res={result} onClose={() => setResult(null)} />}
      {writeError && (
        <div className="px-3 py-2 rounded bg-loss/10 border border-loss/30 text-sm text-loss">
          {apiError(writeError)}
        </div>
      )}

      {/* tab bar */}
      <div className="flex items-center gap-1 border-b border-gray-800">
        {[
          { k: 'order', label: '手动下单' },
          { k: 'positions', label: '持仓记录', count: (snap.data?.positions ?? []).length },
          { k: 'orders', label: '挂单记录', count: (snap.data?.orders ?? []).length },
          { k: 'deals', label: '成交历史', count: (deals.data ?? []).length },
        ].map((t) => (
          <button
            key={t.k}
            type="button"
            onClick={() => setTab(t.k as typeof tab)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === t.k ? 'border-blue-500 text-blue-300' : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
            {typeof t.count === 'number' && t.count > 0 && (
              <span className="ml-1.5 px-1.5 py-0.5 rounded bg-surface-700 text-gray-300 text-[10px] font-mono">
                {t.count}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* order ticket */}
      {tab === 'order' && (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">下单票（市价单）</h3>
        </div>
        <div className="card-body">
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3 items-end">
            <div className="col-span-2 grid grid-cols-2 gap-2">
              {(['buy', 'sell'] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setSide(s)}
                  className={`px-3 py-2 rounded border text-sm transition-colors ${
                    side === s
                      ? s === 'buy'
                        ? 'border-profit/60 bg-profit/10 text-profit'
                        : 'border-loss/60 bg-loss/10 text-loss'
                      : 'border-gray-700 bg-surface-900 text-gray-400 hover:border-gray-600'
                  }`}
                >
                  {s === 'buy' ? '买入 Buy' : '卖出 Sell'}
                </button>
              ))}
            </div>
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
              <span className="text-xs text-gray-400">手数</span>
              <input
                className="input-dark mt-1 font-mono"
                value={volume}
                onChange={(e) => setVolume(e.target.value)}
                placeholder="0.01"
              />
            </label>
            <label className="block">
              <span className="text-xs text-gray-400">滑点上限（点）</span>
              <input
                className="input-dark mt-1"
                value={deviation}
                onChange={(e) => setDeviation(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="btn-primary col-span-2 md:col-span-1"
              disabled={!accountId || !enabled || !Number(volume) || busy}
              onClick={submitOrder}
            >
              {order.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
              {side === 'buy' ? '买入' : '卖出'}
            </button>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            下单前经平台单笔手数兜底 + 账户风控规则（单笔上限/持仓数/同向敞口/可用保证金）；被拦截的单不会到达券商。
          </p>
        </div>
      </div>
      )}

      {/* positions */}
      {tab === 'positions' && (
      <div className="card">
        <div className="card-header flex items-center justify-between">
          <h3 className="text-sm font-medium text-gray-200">
            券商持仓（{(snap.data?.positions ?? []).length}）
          </h3>
          {snap.isError && <span className="text-xs text-loss">{apiError(snap.error)}</span>}
        </div>
        <div className="card-body p-0 overflow-x-auto">
          {(snap.data?.positions ?? []).length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-6">
              {accountId ? '该账户当前无持仓' : '未指定账户'}
            </p>
          ) : (
            <table className="table-dark">
              <thead className="sticky top-0 z-20 bg-surface-800">
                <tr>
                  <th>Ticket</th>
                  <th>品种</th>
                  <th>方向</th>
                  <th>手数</th>
                  <th>开仓价</th>
                  <th>现价</th>
                  <th className="text-right">浮动盈亏</th>
                  <th>来源</th>
                  <th className="sticky right-0 bg-surface-800 text-right z-30">操作</th>
                </tr>
              </thead>
              <tbody>
                {(snap.data?.positions ?? []).map((p) => (
                  <tr key={p.ticket} className="text-xs">
                    <td className="font-mono text-gray-500">{p.ticket}</td>
                    <td className="font-mono">{p.symbol}</td>
                    <td className={p.type === 'buy' ? 'text-profit' : 'text-loss'}>
                      {p.type === 'buy' ? '买' : '卖'}
                    </td>
                    <td className="font-mono">{money(p.volume)}</td>
                    <td className="font-mono">{formatPrice(p.price_open, 5)}</td>
                    <td className="font-mono">{formatPrice(p.price_current, 5)}</td>
                    <td className={`text-right font-mono ${p.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {money(p.profit)}
                    </td>
                    <td>
                      {p.magic === 0 ? (
                        <span className="text-gray-400">手动</span>
                      ) : (
                        <span
                          className="px-1.5 py-0.5 rounded border text-[10px] bg-yellow-500/15 text-yellow-400 border-yellow-500/40"
                          title={`策略 magic=${p.magic}：手动平仓会影响实盘策略持仓`}
                        >
                          策略
                        </span>
                      )}
                    </td>
                    <td className="sticky right-0 bg-surface-800 text-right z-10 whitespace-nowrap">
                      <button
                        type="button"
                        className="btn-ghost text-xs text-loss hover:bg-loss/10 px-2 py-1"
                        disabled={!enabled || busy}
                        onClick={() => submitClose(p.ticket, p.symbol, p.volume)}
                      >
                        <Ban className="w-3.5 h-3.5 mr-1 inline" />
                        平仓
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      )}

      {/* pending orders */}
      {tab === 'orders' && (
      <div className="card">
        <div className="card-header">
          <h3 className="text-sm font-medium text-gray-200">
            挂单（{(snap.data?.orders ?? []).length}）
          </h3>
        </div>
        <div className="card-body p-0 overflow-x-auto">
          {(snap.data?.orders ?? []).length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-6">当前无挂单</p>
          ) : (
            <table className="table-dark">
              <thead className="sticky top-0 z-20 bg-surface-800">
                <tr>
                  <th>Ticket</th>
                  <th>品种</th>
                  <th>类型</th>
                  <th>手数</th>
                  <th>委托价</th>
                  <th>设置时间</th>
                  <th className="sticky right-0 bg-surface-800 text-right z-30">操作</th>
                </tr>
              </thead>
              <tbody>
                {(snap.data?.orders ?? []).map((o) => (
                  <tr key={o.ticket} className="text-xs">
                    <td className="font-mono text-gray-500">{o.ticket}</td>
                    <td className="font-mono">{o.symbol}</td>
                    <td>{ORDER_TYPE_LABELS[o.type] ?? `type ${o.type}`}</td>
                    <td className="font-mono">{money(o.volume)}</td>
                    <td className="font-mono">{formatPrice(o.price_open, 5)}</td>
                    <td className="text-gray-400 whitespace-nowrap">{dt(o.time_setup)}</td>
                    <td className="sticky right-0 bg-surface-800 text-right z-10 whitespace-nowrap">
                      <button
                        type="button"
                        className="btn-ghost text-xs px-2 py-1"
                        disabled={!enabled || busy}
                        onClick={() => submitCancel(o.ticket)}
                      >
                        撤单
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      )}

      {/* deals */}
      {tab === 'deals' && (
      <div className="card">
        <div className="card-header flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-gray-200">成交回报</h3>
          <select
            className="input-dark text-xs py-1"
            value={dealsHours}
            onChange={(e) => setDealsHours(Number(e.target.value))}
          >
            <option value={1}>近 1 小时</option>
            <option value={24}>近 24 小时</option>
            <option value={168}>近 7 天</option>
          </select>
        </div>
        <div className="card-body p-0 overflow-x-auto">
          {(deals.data ?? []).length === 0 ? (
            <p className="text-sm text-gray-500 text-center py-6">
              {accountId ? '所选时段无成交' : '未指定账户'}
            </p>
          ) : (
            <table className="table-dark">
              <thead>
                <tr>
                  <th>Deal</th>
                  <th>时间</th>
                  <th>品种</th>
                  <th>方向</th>
                  <th>开/平</th>
                  <th>手数</th>
                  <th>价格</th>
                  <th className="text-right">盈亏</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {(deals.data ?? []).map((d) => (
                  <tr key={d.ticket} className="text-xs">
                    <td className="font-mono text-gray-500">{d.ticket}</td>
                    <td className="text-gray-400 whitespace-nowrap">{dt(d.time)}</td>
                    <td className="font-mono">{d.symbol}</td>
                    <td className={d.type === 0 ? 'text-profit' : 'text-loss'}>
                      {d.type === 0 ? '买' : '卖'}
                    </td>
                    <td>{DEAL_ENTRY_LABELS[d.entry] ?? d.entry}</td>
                    <td className="font-mono">{money(d.volume)}</td>
                    <td className="font-mono">{formatPrice(d.price, 5)}</td>
                    <td className={`text-right font-mono ${d.profit >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {money(d.profit)}
                    </td>
                    <td className="text-gray-500">{d.magic === 0 ? '手动' : `策略 ${d.magic}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      )}
    </div>
  );
}
