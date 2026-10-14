import { useState } from 'react';
import { Hand } from 'lucide-react';
import { useAccounts } from '@/hooks/useAccounts';
import ManualTradePanel from '@/components/trading/ManualTradePanel';

/**
 * 手动交易（路由保留用于深链，侧栏入口已取消 — 面板嵌入虚拟下单/实盘/行情页）。
 */
export default function ManualTradePage() {
  const accounts = useAccounts();
  const [accountId, setAccountId] = useState('');

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Hand className="w-5 h-5 text-blue-400" /> 手动交易
          </h1>
          <p className="text-xs text-gray-500 mt-1">
            选择账户后进行手动下单/平仓/撤单。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400">账户</label>
          <select
            className="input-dark"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
          >
            <option value="">请选择 MT5 账户…</option>
            {(accounts.data?.items ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.login}@{a.server}
                {a.trade_frozen ? '（已冻结）' : ''}
              </option>
            ))}
          </select>
        </div>
      </div>

      {accountId ? (
        <ManualTradePanel accountId={accountId} />
      ) : (
        <p className="text-sm text-gray-500 text-center py-10">请先选择账户</p>
      )}
    </div>
  );
}
