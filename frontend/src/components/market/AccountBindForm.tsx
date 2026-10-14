import { useState, type FormEvent } from 'react';
import { Link2, Plug, Loader2, CheckCircle2 } from 'lucide-react';
import { useBindAccount, useTestAccount } from '@/hooks/useAccounts';
import { apiError } from '@/lib/api';
import { ACCOUNT_TYPE_LABELS, formatPrice } from '@/lib/market';
import type { AccountProbeResult, AccountTestRequest } from '@/types/api';

const EMPTY: AccountTestRequest = {
  gateway_url: 'http://localhost:8090',
  login: 0,
  password: '',
  server: '',
};

export default function AccountBindForm() {
  const [form, setForm] = useState<AccountTestRequest>(EMPTY);
  const [probe, setProbe] = useState<AccountProbeResult | null>(null);

  const test = useTestAccount();
  const bind = useBindAccount();

  const set = (key: keyof AccountTestRequest, value: string) =>
    setForm((prev) => ({
      ...prev,
      [key]: key === 'login' ? Number(value.replace(/\D/g, '')) || 0 : value,
    }));

  const valid = form.login > 0 && form.password.length > 0 && form.server.trim().length > 0;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setProbe(null);
    try {
      setProbe(await test.mutateAsync(form));
    } catch {
      // 错误通过 test.error 展示
    }
  }

  async function onBind() {
    if (!probe) return;
    try {
      await bind.mutateAsync(form);
      setProbe(null);
      setForm(EMPTY);
    } catch {
      // 错误通过 bind.error 展示
    }
  }

  const actionError = test.error ?? bind.error;

  return (
    <div className="card">
      <div className="card-header flex items-center gap-2">
        <Link2 className="w-4 h-4 text-blue-400" />
        <span className="text-sm font-medium text-gray-200">绑定 MT5 账户</span>
      </div>

      <form className="card-body space-y-4" onSubmit={onSubmit}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="block">
            <span className="block text-xs text-gray-500 mb-1">网关地址</span>
            <input
              type="text"
              className="input-dark font-mono"
              value={form.gateway_url ?? ''}
              onChange={(e) => set('gateway_url', e.target.value)}
              placeholder="http://localhost:8090"
            />
          </label>
          <label className="block">
            <span className="block text-xs text-gray-500 mb-1">MT5 账号</span>
            <input
              type="text"
              inputMode="numeric"
              className="input-dark font-mono"
              value={form.login || ''}
              onChange={(e) => set('login', e.target.value)}
              placeholder="例如 91350"
            />
          </label>
          <label className="block">
            <span className="block text-xs text-gray-500 mb-1">密码</span>
            <input
              type="password"
              className="input-dark"
              value={form.password}
              onChange={(e) => set('password', e.target.value)}
              placeholder="MT5 账户密码"
              autoComplete="new-password"
            />
          </label>
          <label className="block">
            <span className="block text-xs text-gray-500 mb-1">服务器</span>
            <input
              type="text"
              className="input-dark font-mono"
              value={form.server}
              onChange={(e) => set('server', e.target.value)}
              placeholder="例如 DBGMarkets-Demo"
            />
          </label>
        </div>

        {actionError && (
          <div className="text-sm text-loss bg-loss/10 border border-loss/40 rounded px-3 py-2">
            {apiError(actionError)}
          </div>
        )}

        {probe && (
          <div className="border border-profit/40 bg-profit/10 rounded px-3 py-3 space-y-2">
            <div className="flex items-center gap-2 text-sm text-profit">
              <CheckCircle2 className="w-4 h-4" />
              连接成功 — {probe.name}（{ACCOUNT_TYPE_LABELS[probe.account_type] ?? probe.account_type} ·{' '}
              {probe.server}）
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-xs text-gray-400">
              <span>
                余额 <span className="font-mono text-gray-200">{formatPrice(probe.balance, 2)}</span>{' '}
                {probe.currency}
              </span>
              <span>
                净值 <span className="font-mono text-gray-200">{formatPrice(probe.equity, 2)}</span>
              </span>
              <span>
                杠杆 <span className="font-mono text-gray-200">1:{probe.leverage}</span>
              </span>
              <span>
                可交易{' '}
                <span className={probe.is_trade_allowed ? 'text-profit' : 'text-loss'}>
                  {probe.is_trade_allowed ? '是' : '否'}
                </span>
              </span>
            </div>
          </div>
        )}

        <div className="flex items-center gap-3">
          <button type="submit" className="btn-secondary" disabled={!valid || test.isPending}>
            {test.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Plug className="w-4 h-4" />
            )}
            测试连接
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!probe || bind.isPending}
            onClick={onBind}
          >
            {bind.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            绑定账户
          </button>
          <span className="text-xs text-gray-600">先测试连接，成功后即可绑定。</span>
        </div>
      </form>
    </div>
  );
}
