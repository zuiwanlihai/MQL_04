import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, Loader2, Plus, RefreshCw, UserCog } from 'lucide-react';
import { useAccounts, useActiveAccount, useActivateAccount } from '@/hooks/useAccounts';
import { useAuthStore } from '@/store/useAuthStore';

const TYPE_BADGE: Record<string, { label: string; cls: string }> = {
  real: { label: '实盘', cls: 'bg-loss/15 text-loss' },
  demo: { label: '模拟', cls: 'bg-profit/15 text-profit' },
  contest: { label: '竞赛', cls: 'bg-yellow-500/15 text-yellow-400' },
};

/**
 * 顶栏 MT5 账户切换器：
 * 显示网关当前登录的 账号@服务器；点开下拉可直接切换到已绑定的其他账户
 * （免输密码的账户一键切换，未记住密码的跳转登录页输一次）。
 */
export default function MT5AccountSwitcher() {
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const activeQ = useActiveAccount();
  const accounts = useAccounts();
  const activate = useActivateAccount();
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);

  const activeAccount = (activeQ.data ?? []).find((i) => i.account !== null)?.account ?? null;
  const items = accounts.data?.items ?? [];
  const viewerRole = user?.role ?? 'viewer';
  const canManage = viewerRole === 'admin' || viewerRole === 'trader';

  async function onPick(id: string, login: number | string, type: string, hasCreds: boolean) {
    if (switching) return;
    if (!hasCreds) {
      setOpen(false);
      navigate('/mt5-login');
      return;
    }
    if (!window.confirm(`切换登录到 ${login}（${TYPE_BADGE[type]?.label ?? type}）？\n行情将中断约 5-10 秒。`)) {
      return;
    }
    setSwitching(String(id));
    activate.mutate({ id }, {
      onSuccess: () => {
        setSwitching(null);
        setOpen(false);
      },
      onError: () => setSwitching(null),
    });
  }

  const badge = activeAccount ? TYPE_BADGE[activeAccount.account_type] : undefined;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => canManage && setOpen((v) => !v)}
        className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm border transition-colors ${
          activeAccount
            ? 'border-gray-700 bg-surface-800 hover:border-gray-600 text-gray-200'
            : 'border-blue-500/40 bg-blue-500/10 hover:bg-blue-500/20 text-blue-300'
        } ${canManage ? '' : 'cursor-default opacity-70'}`}
        title={canManage ? '切换 MT5 账户' : '当前角色不可管理 MT5 账户'}
      >
        <UserCog className="w-4 h-4 flex-shrink-0" />
        {activeAccount ? (
          <span className="flex items-center gap-1.5 min-w-0">
            <span className="font-mono text-xs text-white whitespace-nowrap">
              {activeAccount.login} <span className="text-gray-500">@ {activeAccount.server}</span>
            </span>
            {badge && (
              <span className={`text-2xs px-1.5 py-0.5 rounded flex-shrink-0 ${badge.cls}`}>
                {badge.label}
              </span>
            )}
          </span>
        ) : (
          <span className="text-xs">登录 MT5 账户</span>
        )}
        {canManage && <ChevronDown className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />}
      </button>

      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-2 z-50 w-72 card p-2 shadow-xl">
            <p className="px-2 py-1.5 text-2xs uppercase tracking-wider text-gray-500">
              网关当前登录
            </p>
            {activeAccount ? (
              <p className="px-2 pb-2 font-mono text-sm text-white">
                {activeAccount.login}
                <span className="text-xs text-gray-500"> @ {activeAccount.server}</span>
                {badge && (
                  <span className={`ml-2 text-2xs px-1.5 py-0.5 rounded align-middle ${badge.cls}`}>
                    {badge.label}
                  </span>
                )}
              </p>
            ) : (
              <p className="px-2 pb-2 text-xs text-gray-500">离线或未登录</p>
            )}

            <div className="border-t border-gray-700/50 pt-1.5 mt-1">
              <p className="px-2 py-1 text-2xs uppercase tracking-wider text-gray-500">
                切换到已绑定账户
              </p>
              {accounts.isLoading && (
                <p className="px-2 py-2 text-xs text-gray-500 flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> 加载中…
                </p>
              )}
              {!accounts.isLoading && items.length === 0 && (
                <p className="px-2 py-2 text-xs text-gray-500">暂无绑定账户</p>
              )}
              {items.map((a) => {
                const b = TYPE_BADGE[a.account_type];
                const isActive = activeAccount?.login === a.login && activeAccount?.server === a.server;
                return (
                  <button
                    key={a.id}
                    type="button"
                    disabled={isActive || switching !== null}
                    onClick={() => onPick(a.id, a.login, a.account_type, a.has_credentials)}
                    className={`w-full text-left px-2 py-1.5 rounded text-sm flex items-center justify-between gap-2 transition-colors ${
                      isActive
                        ? 'opacity-60 cursor-default'
                        : 'hover:bg-surface-700 text-gray-200'
                    }`}
                  >
                    <span className="flex items-center gap-1.5 min-w-0">
                      <span className="font-mono text-xs">{a.login}</span>
                      <span className="text-xs text-gray-500 whitespace-nowrap">@ {a.server}</span>
                      {!a.has_credentials && (
                        <span
                          className="text-2xs px-1 py-0.5 rounded bg-yellow-500/15 text-yellow-400 flex-shrink-0"
                          title="未记住密码：点击后到登录页输入一次"
                        >
                          需密码
                        </span>
                      )}
                    </span>
                    <span className="flex items-center gap-1.5 flex-shrink-0">
                      {b && (
                        <span className={`text-2xs px-1.5 py-0.5 rounded ${b.cls}`}>{b.label}</span>
                      )}
                      {switching === a.id ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400" />
                      ) : isActive ? (
                        <span className="text-2xs text-profit">当前</span>
                      ) : (
                        <RefreshCw className="w-3 h-3 text-gray-600" />
                      )}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="border-t border-gray-700/50 mt-1.5 pt-1.5">
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  navigate('/mt5-login');
                }}
                className="w-full text-left px-2 py-1.5 rounded text-xs text-blue-300 hover:bg-surface-700 flex items-center gap-1.5"
              >
                <Plus className="w-3.5 h-3.5" /> 登录其他账户 / 绑定新账户…
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
