/**
 * 顶栏「交易白名单」按钮：把当前登录的 MT5 账户加入网关下单白名单。
 * 已加白 → 绿色徽标；未加白 → 点击确认后加入（网关运行时生效并持久化）。
 */
import { useEffect, useRef, useState } from 'react';
import { Loader2, ShieldCheck, ShieldPlus } from 'lucide-react';
import { useActiveAccount, useAddTradeWhitelist, useTradeWhitelist } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';

export default function WhitelistButton() {
  const activeQ = useActiveAccount();
  const whitelistQ = useTradeWhitelist();
  const addWhitelist = useAddTradeWhitelist();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const msgTimer = useRef<number | null>(null);

  const active = activeQ.data?.find((i) => i.account) ?? null;
  const login = active?.account?.login ?? null;
  const label = login ? `${login} @ ${active!.account!.server}` : '';
  const whitelisted = login !== null && (whitelistQ.data?.logins ?? []).includes(login);

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

  function add() {
    if (login === null || addWhitelist.isPending) return;
    if (!window.confirm(`将账户 ${label} 加入交易白名单？\n\n加入后该账户即可通过平台下单（模拟/实盘均受灰度开关约束）。`)) return;
    addWhitelist.mutate(login, {
      onSuccess: (res) =>
        showMsg(true, `账户 ${login} 已加入交易白名单（当前放行 ${res.logins.length} 个账户）`),
      onError: (err) => showMsg(false, apiError(err)),
    });
  }

  if (login === null) return null; // 未登录 MT5 账户时不显示

  return (
    <div className="flex items-center gap-2">
      {whitelisted ? (
        <span
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-profit/40 bg-profit/10 text-profit text-sm font-medium whitespace-nowrap cursor-default"
          title={`账户 ${label} 已在交易白名单内，可直接下单`}
          data-testid="whitelist-badge"
        >
          <ShieldCheck className="w-4 h-4" />
          已加白
        </span>
      ) : (
        <button
          type="button"
          data-testid="whitelist-add"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-gray-600 bg-surface-900 text-gray-300 hover:bg-surface-800 hover:text-white text-sm font-medium transition-colors whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed"
          disabled={addWhitelist.isPending || !whitelistQ.data?.trade_enabled}
          title={
            !whitelistQ.data?.trade_enabled
              ? '网关交易开关未开启，无法加白'
              : `账户 ${label} 尚未加入交易白名单 — 点击加入`
          }
          onClick={add}
        >
          {addWhitelist.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <ShieldPlus className="w-4 h-4" />
          )}
          加入白名单
        </button>
      )}
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
