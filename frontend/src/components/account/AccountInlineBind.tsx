import { useState, type FormEvent } from 'react';
import { CheckCircle2, Link2, Loader2, Plug, X } from 'lucide-react';
import { useBindAccount, useTestAccount } from '@/hooks/useAccounts';
import { useGatewayNodes } from '@/hooks/useGateway';
import { apiError } from '@/lib/api';
import { ACCOUNT_TYPE_LABELS, formatPrice } from '@/lib/market';
import type { AccountProbeResult, MT5Account } from '@/types/api';

/** 上次绑定成功的账户+服务器 — 下次打开自动预填（密码永远不本地保存）。 */
const LAST_BIND_KEY = 'mt5-bind-last';

function loadLastBind(): { login: string; server: string } {
  try {
    const raw = localStorage.getItem(LAST_BIND_KEY);
    if (!raw) return { login: '', server: '' };
    const p = JSON.parse(raw) as { login?: unknown; server?: unknown };
    return {
      login: typeof p.login === 'string' ? p.login : '',
      server: typeof p.server === 'string' ? p.server : '',
    };
  } catch {
    return { login: '', server: '' };
  }
}

interface Props {
  /** 预选网关节点（通常 = 当前已选账户所在节点）。 */
  defaultNodeId?: string;
  /** 绑定成功回调：父组件据此自动选中新账户。 */
  onBound: (account: MT5Account) => void;
  onClose: () => void;
  /** 绑定后自动选中的动作文案，如「启动虚拟下单」/「部署实盘」。 */
  confirmLabel?: string;
  /** 已绑定账户（login+server）— 命中时阻止重复绑定并引导走列表登录。 */
  boundPairs?: { login: number; server: string }[];
}

/**
 * M13：虚拟下单/实盘流程内直接「账户密码登录 + 绑定」。
 * 复用 /accounts/test 与 /accounts 接口；选择网关节点后，登录探测与落库
 * 都指向该节点（服务器 = 节点 host:port），绑定成功即自动选中。
 */
export default function AccountInlineBind({
  defaultNodeId,
  onBound,
  onClose,
  confirmLabel = '绑定并选中',
  boundPairs = [],
}: Props) {
  const nodes = useGatewayNodes();
  const test = useTestAccount();
  const bind = useBindAccount();

  const last = loadLastBind();
  const [nodeId, setNodeId] = useState(defaultNodeId ?? '');
  const [login, setLogin] = useState(last.login);
  const [password, setPassword] = useState('');
  const [server, setServer] = useState(last.server);
  const [rememberPassword, setRememberPassword] = useState(true);
  const [probe, setProbe] = useState<AccountProbeResult | null>(null);

  const nodeItems = nodes.data?.items ?? [];
  const valid = Number(login) > 0 && password.length > 0 && server.trim().length > 0;
  const duplicate = boundPairs.some(
    (p) => String(p.login) === login.trim() && p.server.toLowerCase() === server.trim().toLowerCase(),
  );

  const body = () => ({
    gateway_node_id: nodeId || undefined,
    login: Number(login),
    password,
    server: server.trim(),
    remember_password: rememberPassword,
  });

  async function onTest(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setProbe(null);
    try {
      setProbe(await test.mutateAsync(body()));
    } catch {
      // 错误通过 test.error 展示
    }
  }

  async function onBind() {
    if (!probe || bind.isPending) return;
    try {
      const acct = await bind.mutateAsync(body());
      try {
        localStorage.setItem(
          LAST_BIND_KEY,
          JSON.stringify({ login: String(acct.login), server: acct.server }),
        );
      } catch {
        // localStorage 不可用时仅影响下次预填
      }
      onBound(acct);
    } catch {
      // 错误通过 bind.error 展示
    }
  }

  const actionError = test.error ?? bind.error;

  return (
    <div className="rounded border border-blue-500/40 bg-blue-500/5 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-blue-300 flex items-center gap-1.5">
          <Link2 className="w-3.5 h-3.5" /> 新账户登录绑定
        </p>
        <button type="button" className="btn-ghost p-1" onClick={onClose} aria-label="关闭">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="col-span-2 block">
          <span className="text-xs text-gray-400">网关节点（服务器） *</span>
          <select
            className="input-dark mt-1"
            value={nodeId}
            onChange={(e) => {
              setNodeId(e.target.value);
              setProbe(null);
            }}
          >
            <option value="">默认节点（本地网关）</option>
            {nodeItems.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name} · {n.host}:{n.port}（{n.status}）
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-gray-400">MT5 账号 *</span>
          <input
            type="text"
            inputMode="numeric"
            className="input-dark mt-1 font-mono"
            value={login}
            onChange={(e) => {
              setLogin(e.target.value.replace(/\D/g, ''));
              setProbe(null);
            }}
            placeholder="例如 91350"
          />
        </label>
        <label className="block">
          <span className="text-xs text-gray-400">服务器名 *</span>
          <input
            type="text"
            className="input-dark mt-1 font-mono"
            value={server}
            onChange={(e) => {
              setServer(e.target.value);
              setProbe(null);
            }}
            placeholder="例如 DBGMarkets-Demo"
          />
        </label>
        <label className="col-span-2 block">
          <span className="text-xs text-gray-400">密码 *</span>
          <input
            type="password"
            className="input-dark mt-1"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setProbe(null);
            }}
            placeholder="MT5 账户密码（记住时 Fernet 加密存储，绝不回显）"
            autoComplete="new-password"
          />
        </label>
        <label className="col-span-2 flex items-center gap-2 text-xs text-gray-400 cursor-pointer select-none">
          <input
            type="checkbox"
            className="accent-blue-500"
            checked={rememberPassword}
            onChange={(e) => setRememberPassword(e.target.checked)}
          />
          记住密码（加密存储，之后切换登录免输入）
        </label>
      </div>

      {duplicate && (
        <div className="text-xs text-yellow-400 bg-yellow-500/10 border border-yellow-500/40 rounded px-2 py-1.5">
          该账户已绑定，无需重新绑定 — 关闭此表单，直接在账户列表中点击它即可登录
          （已记住密码的账户不需要输密码）。
        </div>
      )}

      {actionError && (
        <div className="text-xs text-loss bg-loss/10 border border-loss/40 rounded px-2 py-1.5">
          {apiError(actionError)}
        </div>
      )}

      {probe && (
        <div className="border border-profit/40 bg-profit/10 rounded px-2.5 py-2 space-y-1">
          <div className="flex items-center gap-1.5 text-xs text-profit">
            <CheckCircle2 className="w-3.5 h-3.5" />
            登录成功 — {probe.name}（{ACCOUNT_TYPE_LABELS[probe.account_type] ?? probe.account_type} ·{' '}
            {probe.server}）
          </div>
          <div className="grid grid-cols-2 gap-x-3 text-[11px] text-gray-400">
            <span>
              余额 <span className="font-mono text-gray-200">{formatPrice(probe.balance, 2)}</span> {probe.currency}
            </span>
            <span>
              杠杆 <span className="font-mono text-gray-200">1:{probe.leverage}</span>
            </span>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-secondary text-xs"
          disabled={!valid || duplicate || test.isPending}
          onClick={onTest}
        >
          {test.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
          测试连接
        </button>
        <button
          type="button"
          className="btn-primary text-xs"
          disabled={!probe || duplicate || bind.isPending}
          onClick={onBind}
        >
          {bind.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {confirmLabel}
        </button>
        <span className="text-[11px] text-gray-500">
          {duplicate ? '已绑定的账户请在列表中直接登录。' : '先测试登录，成功后即可绑定并自动选中。'}
        </span>
      </div>
    </div>
  );
}
