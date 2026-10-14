import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Server, Settings2, Trash2 } from 'lucide-react';
import AccountBindForm from '@/components/market/AccountBindForm';
import AccountTable from '@/components/market/AccountTable';
import GatewayHealthCard from '@/components/market/GatewayHealthCard';
import SelfCheckCard from '@/components/settings/SelfCheckCard';
import { useAccounts } from '@/hooks/useAccounts';
import {
  useCreateGatewayNode,
  useDeleteGatewayNode,
  useGatewayNodes,
  useHealthLogs,
} from '@/hooks/useGateway';
import { apiError } from '@/lib/api';
import { NODE_STATUS_LABELS } from '@/lib/market';

const NODE_STATUS_STYLES: Record<string, string> = {
  online: 'bg-profit/15 text-profit border-profit/40',
  offline: 'bg-loss/15 text-loss border-loss/40',
  degraded: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  starting: 'bg-blue-500/15 text-blue-400 border-blue-500/40',
};

export default function GatewaySettingsPage() {
  const nodesQuery = useGatewayNodes();
  const nodes = nodesQuery.data?.items ?? [];
  const node = nodes.find((n) => n.is_primary) ?? nodes[0];
  const logsQuery = useHealthLogs(node?.id, 50);
  const accountsQuery = useAccounts();

  const createNode = useCreateGatewayNode();
  const deleteNode = useDeleteGatewayNode();

  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('本地网关');
  const [host, setHost] = useState('127.0.0.1');
  const [port, setPort] = useState('8090');

  const portNum = Number(port);
  const formValid = name.trim().length > 0 && host.trim().length > 0 &&
    Number.isInteger(portNum) && portNum >= 1 && portNum <= 65535;

  function submitNode() {
    if (!formValid) return;
    createNode.mutate(
      { name: name.trim(), host: host.trim(), port: portNum },
      { onSuccess: () => setFormOpen(false) },
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Server className="w-5 h-5 text-gray-400" /> 网关设置
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            MT5 网关节点注册与健康状态 · 账户绑定（网关须与 MT5 终端同机运行）
          </p>
        </div>
        <nav className="flex items-center gap-1 text-sm">
          <Link
            to="/settings/system"
            className="px-3 py-1.5 rounded-md text-gray-400 hover:bg-surface-800 hover:text-gray-200 transition-colors inline-flex items-center gap-1.5"
          >
            <Settings2 className="w-3.5 h-3.5" /> 系统
          </Link>
          <span className="px-3 py-1.5 rounded-md bg-surface-750 text-white">网关</span>
        </nav>
      </div>

      {/* 网关节点 */}
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium text-gray-300">网关节点</h2>
            {nodesQuery.data && (
              <span className="text-xs text-gray-600">共 {nodesQuery.data.total} 个</span>
            )}
          </div>
          <button type="button" className="btn-primary" onClick={() => setFormOpen((v) => !v)}>
            <Plus className="w-4 h-4" /> 注册节点
          </button>
        </div>

        {formOpen && (
          <div className="card">
            <div className="card-body grid grid-cols-1 sm:grid-cols-4 gap-3">
              <div>
                <p className="text-xs text-gray-500 mb-1">名称</p>
                <input className="input-dark" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} />
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">主机 / IP</p>
                <input className="input-dark font-mono" value={host} onChange={(e) => setHost(e.target.value)} placeholder="127.0.0.1" />
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">端口</p>
                <input className="input-dark font-mono" value={port} onChange={(e) => setPort(e.target.value)} placeholder="8090" />
              </div>
              <div className="flex items-end gap-2">
                <button type="button" className="btn-primary" disabled={!formValid || createNode.isPending} onClick={submitNode}>
                  注册并探活
                </button>
                <button type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>取消</button>
              </div>
              <p className="sm:col-span-4 text-xs text-gray-600">
                保存时后端立即探测 /health：不可达将登记为「离线」，心跳循环恢复连通后自动转在线。
              </p>
            </div>
          </div>
        )}
        {createNode.isError && (
          <div className="card border-loss/40 bg-loss/10">
            <div className="card-body text-sm text-loss">注册失败：{apiError(createNode.error)}</div>
          </div>
        )}

        <GatewayHealthCard
          node={node}
          logs={logsQuery.data?.items ?? []}
          isLoading={nodesQuery.isLoading}
          error={nodesQuery.error}
          errorMessage={nodesQuery.error ? apiError(nodesQuery.error) : undefined}
        />

        {nodes.length > 0 && (
          <div className="card overflow-hidden">
            <table className="table-dark">
              <thead>
                <tr>
                  <th>名称</th>
                  <th>地址</th>
                  <th className="w-20">状态</th>
                  <th className="w-16">主节点</th>
                  <th className="w-24 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((n) => (
                  <tr key={n.id}>
                    <td className="text-gray-200 text-sm">
                      {n.name}
                      {n.type === 'ea_bridge' && (
                        <span className="ml-2 px-1.5 py-0.5 rounded border text-xs bg-violet-500/10 text-violet-300 border-violet-500/40" title="EA 桥节点（MT5 终端内 EA 直连，无独立网关进程）">
                          EA 桥
                        </span>
                      )}
                    </td>
                    <td className="text-gray-400 font-mono text-xs">{n.host}:{n.port}</td>
                    <td>
                      <span className={`px-2 py-0.5 rounded border text-xs whitespace-nowrap ${NODE_STATUS_STYLES[n.status] ?? 'bg-surface-700 text-gray-300 border-surface-700'}`}>
                        {NODE_STATUS_LABELS[n.status] ?? n.status}
                      </span>
                    </td>
                    <td>
                      {n.is_primary ? (
                        <span className="text-xs text-blue-400">主</span>
                      ) : (
                        <span className="text-xs text-gray-700">—</span>
                      )}
                    </td>
                    <td className="text-right">
                      {!n.is_primary && (
                        <button
                          type="button"
                          className="text-loss hover:text-loss-light disabled:opacity-50 text-xs"
                          disabled={deleteNode.isPending}
                          title="删除节点（其健康日志一并清除）"
                          onClick={() => {
                            if (window.confirm(`删除网关节点「${n.name} · ${n.host}:${n.port}」？绑定该节点的账户将需重新绑定。`)) {
                              deleteNode.mutate(n.id);
                            }
                          }}
                        >
                          <Trash2 className="w-4 h-4 inline" /> 删除
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {deleteNode.isError && (
              <div className="px-4 py-2 text-sm text-loss bg-loss/10 border-t border-loss/30">
                删除失败：{apiError(deleteNode.error)}
              </div>
            )}
          </div>
        )}
      </section>

      {/* 一键自检 */}
      <SelfCheckCard />

      {/* MT5 账户 */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-gray-300">MT5 账户绑定</h2>
          {accountsQuery.data && accountsQuery.data.total > 0 && (
            <span className="text-xs text-gray-600">共 {accountsQuery.data.total} 个已绑定</span>
          )}
        </div>
        <AccountBindForm />
        <AccountTable
          accounts={accountsQuery.data?.items ?? []}
          isLoading={accountsQuery.isLoading}
          error={accountsQuery.error}
        />
        <p className="text-xs text-gray-600">
          密码全程 TLS 传输、Fernet 密文落库，任何接口不回传明文。绑定后可在「行情 · 数据管理」页同步余额。
        </p>
      </section>
    </div>
  );
}
