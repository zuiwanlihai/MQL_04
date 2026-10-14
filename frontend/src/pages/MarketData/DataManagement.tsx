import { Link } from 'react-router-dom';
import { ArrowLeft, Server } from 'lucide-react';
import AccountBindForm from '@/components/market/AccountBindForm';
import AccountTable from '@/components/market/AccountTable';
import GatewayHealthCard from '@/components/market/GatewayHealthCard';
import HistoryDataPanel from '@/components/market/HistoryDataPanel';
import QualityCheckPanel from '@/components/market/QualityCheckPanel';
import TickDataPanel from '@/components/market/TickDataPanel';
import { useAccounts } from '@/hooks/useAccounts';
import { useGatewayNodes, useHealthLogs } from '@/hooks/useGateway';
import { apiError } from '@/lib/api';

export default function DataManagement() {
  const nodesQuery = useGatewayNodes();
  const nodes = nodesQuery.data?.items ?? [];
  const node = nodes.find((n) => n.is_primary) ?? nodes[0];
  const logsQuery = useHealthLogs(node?.id, 50);

  const accountsQuery = useAccounts();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <Link
            to="/market"
            className="btn-ghost w-9 h-9 p-0 flex items-center justify-center"
            title="返回行情总览"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <h1 className="text-xl font-semibold text-white">行情数据管理</h1>
            <p className="text-sm text-gray-500 mt-1">
              网关健康状态、账户绑定与数据源维护
            </p>
          </div>
        </div>
      </div>

      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <Server className="w-4 h-4 text-gray-500" />
          <h2 className="text-sm font-medium text-gray-300">网关节点</h2>
          {nodesQuery.data && (
            <span className="text-xs text-gray-600">共 {nodesQuery.data.total} 个节点</span>
          )}
        </div>

        <GatewayHealthCard
          node={node}
          logs={logsQuery.data?.items ?? []}
          isLoading={nodesQuery.isLoading}
          error={nodesQuery.error}
          errorMessage={nodesQuery.error ? apiError(nodesQuery.error) : undefined}
        />

        {nodes.length > 1 && (
          <div className="flex items-center gap-2 flex-wrap">
            {nodes.map((n) => (
              <span
                key={n.id}
                className={`px-2 py-1 rounded border text-xs font-mono ${
                  n.id === node?.id
                    ? 'border-blue-500/60 text-blue-300 bg-blue-500/10'
                    : 'border-surface-700 text-gray-500'
                }`}
              >
                {n.name} · {n.host}:{n.port}
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-gray-300">账户绑定</h2>
        <AccountBindForm />
        <AccountTable
          accounts={accountsQuery.data?.items ?? []}
          isLoading={accountsQuery.isLoading}
          error={accountsQuery.error}
        />
        {accountsQuery.data && accountsQuery.data.total > 0 && (
          <p className="text-xs text-gray-600">共 {accountsQuery.data.total} 个已绑定账户</p>
        )}
      </section>

      <QualityCheckPanel />

      <HistoryDataPanel />

      <TickDataPanel />
    </div>
  );
}
