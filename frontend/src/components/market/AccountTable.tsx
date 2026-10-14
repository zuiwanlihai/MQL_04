import { Loader2, RefreshCw, Unlink } from 'lucide-react';
import { useSyncAccount, useUnbindAccount } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';
import { ACCOUNT_TYPE_LABELS, formatPrice, relativeTime } from '@/lib/market';
import type { MT5Account } from '@/types/api';

interface Props {
  accounts: MT5Account[];
  isLoading: boolean;
  error: unknown;
}

export default function AccountTable({ accounts, isLoading, error }: Props) {
  const sync = useSyncAccount();
  const unbind = useUnbindAccount();

  if (error) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body text-sm text-loss">{apiError(error)}</div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">正在加载已绑定账户…</div>
      </div>
    );
  }

  if (accounts.length === 0) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">
          尚未绑定任何 MT5 账户，请在上方填写凭据并测试连接。
        </div>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <table className="table-dark">
        <thead>
          <tr>
            <th>账号</th>
            <th>名称</th>
            <th>类型</th>
            <th>服务器</th>
            <th className="text-right">余额</th>
            <th className="text-right">净值</th>
            <th className="text-right">杠杆</th>
            <th>连接状态</th>
            <th>最后同步</th>
            <th className="text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((acc) => (
            <tr key={acc.id}>
              <td className="font-mono text-gray-200">{acc.login}</td>
              <td className="text-gray-400 max-w-40 truncate">{acc.name}</td>
              <td className="text-gray-400">
                {ACCOUNT_TYPE_LABELS[acc.account_type] ?? acc.account_type}
              </td>
              <td className="font-mono text-gray-400">{acc.server}</td>
              <td className="text-right font-mono text-gray-200">
                {formatPrice(acc.balance, 2)}{' '}
                <span className="text-gray-500 text-xs">{acc.currency}</span>
              </td>
              <td className="text-right font-mono text-gray-200">{formatPrice(acc.equity, 2)}</td>
              <td className="text-right font-mono text-gray-400">1:{acc.leverage}</td>
              <td>
                <span
                  className={`px-2 py-0.5 rounded border text-xs ${
                    acc.is_connected
                      ? 'bg-profit/15 text-profit border-profit/40'
                      : 'bg-loss/15 text-loss border-loss/40'
                  }`}
                >
                  {acc.is_connected ? '已连接' : '未连接'}
                </span>
              </td>
              <td className="text-xs text-gray-500">{relativeTime(acc.last_sync_at)}</td>
              <td className="text-right whitespace-nowrap">
                <button
                  type="button"
                  className="text-blue-400 hover:text-blue-300 text-sm disabled:opacity-50"
                  disabled={sync.isPending || unbind.isPending}
                  onClick={() => sync.mutate(acc.id)}
                  title="同步余额与净值"
                >
                  {sync.isPending && sync.variables === acc.id ? (
                    <Loader2 className="w-3.5 h-3.5 inline animate-spin" />
                  ) : (
                    <RefreshCw className="w-3.5 h-3.5 inline" />
                  )}{' '}
                  同步
                </button>
                <span className="text-gray-700 mx-2">|</span>
                <button
                  type="button"
                  className="text-loss hover:text-loss-light text-sm disabled:opacity-50"
                  disabled={sync.isPending || unbind.isPending}
                  onClick={() => {
                    if (window.confirm(`确认解绑账户 ${acc.login}（${acc.server}）？`)) {
                      unbind.mutate(acc.id);
                    }
                  }}
                >
                  <Unlink className="w-3.5 h-3.5 inline" /> 解绑
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {sync.isError && (
        <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
          同步失败：{apiError(sync.error)}
        </div>
      )}
      {unbind.isError && (
        <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
          解绑失败：{apiError(unbind.error)}
        </div>
      )}
    </div>
  );
}
