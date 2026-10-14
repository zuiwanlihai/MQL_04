import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, CheckCircle2, Loader2, LogOut, Plus } from 'lucide-react';
import AccountInlineBind from '@/components/account/AccountInlineBind';
import { useAccounts, useActiveAccount, useActivateAccount } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';
import { useAuthStore } from '@/store/useAuthStore';

/**
 * 第二步：登录 MT5 交易账户（邮箱登录平台后进入）。
 * 选择已绑定账户 → 网关 mt5.login 切登录（期间行情中断 5-10s）→ 进入总览。
 * 登录的账户类型驱动全局互斥：模拟 → 实盘禁用；实盘 → 虚拟下单禁用。
 */
export default function MT5LoginPage() {
  const navigate = useNavigate();
  const { logout } = useAuthStore();
  const accounts = useAccounts();
  const activeQ = useActiveAccount();
  const activate = useActivateAccount();

  const [selected, setSelected] = useState('');
  const [inlineBind, setInlineBind] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState('');

  const items = accounts.data?.items ?? [];
  const selectedItem = items.find((a) => a.id === selected) ?? null;
  const needsPassword = Boolean(selectedItem && !selectedItem.has_credentials);
  const activeAccount = useMemo(() => {
    const online = (activeQ.data ?? []).find((i) => i.account !== null);
    return online?.account ?? null;
  }, [activeQ.data]);

  async function onActivate() {
    if (!selected || activate.isPending) return;
    if (needsPassword && !password) return;
    setError(null);
    activate.mutate(
      { id: selected, password: needsPassword ? password : undefined },
      {
        onSuccess: () => navigate('/dashboard', { replace: true }),
        onError: (err) => setError(apiError(err)),
      },
    );
  }

  async function onLogout() {
    await logout();
    navigate('/login', { replace: true });
  }

  return (
    <div className="max-w-md mx-auto">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">登录 MT5 交易账户</h1>
        <p className="text-gray-400 text-sm mt-1">
          选择要登录的 MT5 账户（切换期间行情中断约 5-10 秒）；也可以随时从左侧菜单前往其他页面。
        </p>
      </div>

      <div className="card p-6">
          {error && (
            <div className="text-sm text-loss bg-loss/10 border border-loss/30 rounded-md px-3 py-2 mb-4">
              {error}
            </div>
          )}

          {activeAccount && (
            <div className="mb-4 rounded border border-profit/40 bg-profit/10 p-4">
              <p className="text-sm text-profit flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4" />
                网关当前已登录
              </p>
              <p className="font-mono text-lg text-white mt-1">
                {activeAccount.login}
                <span className="text-sm text-gray-400"> @ {activeAccount.server}</span>
                <span
                  className={`ml-2 text-xs px-1.5 py-0.5 rounded align-middle ${
                    activeAccount.account_type === 'real'
                      ? 'bg-loss/15 text-loss'
                      : 'bg-profit/15 text-profit'
                  }`}
                >
                  {activeAccount.account_type === 'real' ? '实盘' : '模拟'}
                </span>
              </p>
              <button
                type="button"
                className="btn-primary w-full mt-3"
                onClick={() => navigate('/dashboard', { replace: true })}
              >
                直接进入平台
              </button>
              <p className="text-2xs text-gray-500 mt-2">不需要重新绑定 — 上面的账户已经可以用了。</p>
            </div>
          )}

          {accounts.isLoading ? (
            <p className="text-sm text-gray-500 text-center py-6 flex items-center justify-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" /> 加载已绑定账户…
            </p>
          ) : items.length === 0 && !inlineBind ? (
            <p className="text-sm text-gray-500 text-center py-6">
              尚未绑定 MT5 账户 — 在下方输入账户密码登录并绑定。
            </p>
          ) : (
            <div className="space-y-2">
              {items.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  onClick={() => {
                    setSelected(a.id);
                    setPassword('');
                    setError(null);
                  }}
                  className={`w-full text-left px-3 py-2 rounded border text-sm flex items-center justify-between ${
                    selected === a.id
                      ? 'border-blue-500/60 bg-blue-500/10'
                      : 'border-gray-700 bg-surface-900 hover:border-gray-600'
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <span className="font-mono text-gray-100">{a.login}</span>
                    <span className="text-gray-500">@ {a.server}</span>
                    {!a.has_credentials && (
                      <span
                        className="text-[10px] px-1 py-0.5 rounded bg-yellow-500/15 text-yellow-400"
                        title="绑定时未记住密码：切换登录时需输入一次密码"
                      >
                        未记住密码
                      </span>
                    )}
                  </span>
                  <span className="flex items-center gap-2">
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded ${
                        a.account_type === 'real'
                          ? 'bg-loss/15 text-loss'
                          : 'bg-profit/15 text-profit'
                      }`}
                    >
                      {a.account_type === 'real' ? '实盘' : a.account_type === 'contest' ? '竞赛' : '模拟'}
                    </span>
                    {selected === a.id && <Check className="w-4 h-4 text-blue-400" />}
                  </span>
                </button>
              ))}
            </div>
          )}

          {inlineBind ? (
            <div className="mt-3">
              <AccountInlineBind
                confirmLabel="绑定并选中"
                boundPairs={items.map((a) => ({ login: a.login, server: a.server }))}
                onBound={(a) => {
                  setSelected(a.id);
                  setInlineBind(false);
                }}
                onClose={() => setInlineBind(false)}
              />
            </div>
          ) : (
            <button
              type="button"
              className="mt-3 text-xs text-blue-300 hover:text-blue-200 underline underline-offset-2 flex items-center gap-1"
              onClick={() => setInlineBind(true)}
            >
              <Plus className="w-3.5 h-3.5" /> 绑定新账户（输入账户密码登录绑定）
            </button>
          )}

          {needsPassword && (
            <div className="mt-3">
              <label htmlFor="act-password" className="block text-xs text-gray-400 mb-1">
                该账户未记住密码 — 请输入 MT5 账户密码（仅本次登录使用，不保存）
              </label>
              <input
                id="act-password"
                type="password"
                className="input-dark w-full"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="MT5 账户密码"
              />
            </div>
          )}

          <button
            type="button"
            className="btn-primary w-full mt-5"
            disabled={!selected || activate.isPending || (needsPassword && !password)}
            onClick={onActivate}
          >
            {activate.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> 正在切换网关登录…
              </>
            ) : (
              '登录该 MT5 账户'
            )}
          </button>

          <p className="text-2xs text-gray-600 mt-3 leading-relaxed">
            模拟账户：可用「虚拟下单」（虚拟撮合）与「模拟盘交易」（订单真实发往 MT5 模拟服务器，零资金风险）；实盘账户：可用「实盘」真实下单。
          </p>
      </div>

      <div className="text-center mt-4">
        <button
          type="button"
          className="btn-ghost text-xs"
          onClick={onLogout}
          disabled={activate.isPending}
        >
          <LogOut className="w-3.5 h-3.5" /> 退出平台登录
        </button>
      </div>
    </div>
  );
}
