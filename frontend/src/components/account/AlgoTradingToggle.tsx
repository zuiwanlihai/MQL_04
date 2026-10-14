import { useEffect, useRef, useState } from 'react';
import { Bot, Loader2 } from 'lucide-react';
import { useAccounts, useActiveAccount, useToggleAlgoTrading } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';

/**
 * 顶栏「算法交易」开关（账户级）：跟随当前登录的 MT5 账户，
 * 切换时同步拨动 MT5 终端本体的「算法交易」按钮（Ctrl+E 注入）。
 */
export default function AlgoTradingToggle() {
  const toggleAlgo = useToggleAlgoTrading();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const msgTimer = useRef<number | null>(null);

  // 当前激活账户（第一个在线节点）→ 账户列表匹配出 id 与开关位
  const activeQ = useActiveAccount();
  const accountsQ = useAccounts();
  const active = activeQ.data?.find((i) => i.account) ?? null;
  const acct = active
    ? accountsQ.data?.items.find(
        (a) => a.login === active.account!.login && a.server === active.account!.server,
      )
    : undefined;
  const accountId = acct?.id ?? null;
  const algoEnabled = acct?.algo_trading_enabled ?? null;
  const algoLabel = acct ? `${acct.login} @ ${acct.server}` : '';

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

  function toggleAlgoClick() {
    if (!accountId || algoEnabled === null || toggleAlgo.isPending) return;
    const toEnabled = !algoEnabled;
    const confirmMsg = toEnabled
      ? `确认允许算法交易？\n\n账户 ${algoLabel}\n\n该账户上运行的实盘策略将恢复自动下单。`
      : `确认禁止算法交易？\n\n账户 ${algoLabel}\n\n该账户上所有策略的自动下单将被拒绝（开仓/平仓均拦截），手动交易不受影响。`;
    if (!window.confirm(confirmMsg)) return;
    toggleAlgo.mutate(
      { id: accountId, enabled: toEnabled },
      {
        onSuccess: (res) => {
          const t = res.terminal_sync;
          const fallback = toEnabled
            ? '算法交易已允许 — 策略自动下单恢复'
            : '算法交易已禁止 — 策略自动下单被拦截';
          if (t.ok) showMsg(true, t.message ?? fallback);
          else
            showMsg(
              false,
              `平台开关已保存，但 MT5 终端按钮切换失败：${t.message ?? '未知原因'}`,
            );
        },
        onError: (err) => showMsg(false, apiError(err)),
      },
    );
  }

  return (
    <div className="flex items-center gap-2" data-testid="algo-trading-bar">
      <button
        type="button"
        data-testid="algo-trading-toggle"
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-sm font-medium transition-colors whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed ${
          algoEnabled === null
            ? 'border-gray-700 bg-surface-900 text-gray-500'
            : algoEnabled
              ? 'border-profit/50 bg-profit/15 text-profit hover:bg-profit/25'
              : 'border-loss/50 bg-loss/10 text-loss hover:bg-loss/20'
        }`}
        disabled={!accountId || algoEnabled === null || toggleAlgo.isPending}
        title={
          !accountId
            ? '登录 MT5 账户后可操作'
            : `算法交易：${algoEnabled ? '已允许' : '已禁止'}（账户 ${algoLabel}）— 点击切换`
        }
        onClick={toggleAlgoClick}
      >
        {toggleAlgo.isPending ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Bot className="w-4 h-4" />
        )}
        算法交易
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            algoEnabled ? 'bg-profit' : 'bg-loss'
          }`}
        />
      </button>
      {msg && (
        <span
          className={`text-xs font-mono max-w-80 truncate ${
            msg.ok ? 'text-profit' : 'text-loss'
          }`}
          title={msg.text}
        >
          {msg.ok ? '✓ ' : '✕ '}
          {msg.text}
        </span>
      )}
    </div>
  );
}
