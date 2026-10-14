import { Activity, Cpu, HardDrive, Radio } from 'lucide-react';
import type { GatewayHealthLog, GatewayNode } from '@/types/api';
import { formatUptime, NODE_STATUS_LABELS, relativeTime } from '@/lib/market';

const STATUS_STYLES: Record<string, string> = {
  online: 'bg-profit/15 text-profit border-profit/40',
  offline: 'bg-loss/15 text-loss border-loss/40',
  degraded: 'bg-yellow-500/15 text-yellow-400 border-yellow-500/40',
  starting: 'bg-blue-500/15 text-blue-400 border-blue-500/40',
};

function Meter({
  label,
  percent,
  icon,
}: {
  label: string;
  percent: number;
  icon: React.ReactNode;
}) {
  const value = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));
  const color = value >= 85 ? 'bg-loss' : value >= 60 ? 'bg-yellow-500' : 'bg-profit';
  return (
    <div className="flex items-center gap-2 min-w-36">
      <span className="text-gray-500">{icon}</span>
      <span className="text-xs text-gray-400 w-8 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-surface-700 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${value}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-400 w-12 text-right">{value.toFixed(0)}%</span>
    </div>
  );
}

interface Props {
  node?: GatewayNode;
  logs: GatewayHealthLog[];
  isLoading: boolean;
  error: unknown;
  errorMessage?: string;
}

export default function GatewayHealthCard({ node, logs, isLoading, error, errorMessage }: Props) {
  if (isLoading && !node) {
    return (
      <div className="card">
        <div className="card-body text-center text-gray-500 py-10">正在读取网关状态…</div>
      </div>
    );
  }

  if (error || !node) {
    return (
      <div className="card border-loss/40 bg-loss/10">
        <div className="card-body text-sm text-loss">
          {errorMessage || '暂时无法获取网关状态。'}
        </div>
      </div>
    );
  }

  const statusLabel = NODE_STATUS_LABELS[node.status] ?? node.status;
  const mt5Version = node.metadata?.mt5_version;
  const account = node.metadata?.account;

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Radio className="w-4 h-4 text-blue-400" />
          <span className="text-sm font-medium text-gray-200">{node.name}</span>
          <span
            className={`px-2 py-0.5 rounded border text-xs ${
              STATUS_STYLES[node.status] ?? 'bg-surface-700 text-gray-300 border-surface-700'
            }`}
          >
            {statusLabel}
          </span>
          {node.is_primary && (
            <span className="px-2 py-0.5 rounded border text-xs bg-blue-500/15 text-blue-400 border-blue-500/40">
              主节点
            </span>
          )}
        </div>
        <span className="text-xs font-mono text-gray-500">
          {node.host}:{node.port}
        </span>
      </div>

      <div className="card-body space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-xs text-gray-500">版本</p>
            <p className="text-sm font-mono text-gray-200 mt-1">{node.version || '—'}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">运行时长</p>
            <p className="text-sm text-gray-200 mt-1">{formatUptime(node.uptime_seconds)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">活动会话</p>
            <p className="text-sm font-mono text-gray-200 mt-1">{node.active_sessions}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500">最后心跳</p>
            <p className="text-sm text-gray-200 mt-1">{relativeTime(node.last_heartbeat)}</p>
          </div>
        </div>

        {(mt5Version || account) && (
          <div className="flex items-center gap-4 text-xs text-gray-500 flex-wrap">
            {mt5Version && (
              <span>
                MT5 终端：<span className="font-mono text-gray-400">{mt5Version}</span>
              </span>
            )}
            {account && (
              <span>
                登录账户：<span className="font-mono text-gray-400">{account}</span>
              </span>
            )}
          </div>
        )}

        <div className="flex items-center gap-6 flex-wrap">
          <Meter label="CPU" percent={node.cpu_percent} icon={<Cpu className="w-3.5 h-3.5" />} />
          <Meter
            label="内存"
            percent={node.memory_percent}
            icon={<HardDrive className="w-3.5 h-3.5" />}
          />
        </div>

        <div>
          <div className="flex items-center gap-2 mb-2">
            <Activity className="w-4 h-4 text-gray-500" />
            <span className="text-xs font-medium text-gray-400">健康日志（最近 {logs.length} 条）</span>
          </div>
          {logs.length === 0 ? (
            <p className="text-xs text-gray-600 py-2">暂无健康日志。</p>
          ) : (
            <ul className="max-h-56 overflow-y-auto space-y-1 pr-1">
              {logs.map((log) => (
                <li
                  key={log.id}
                  className="flex items-start gap-3 text-xs font-mono text-gray-500 border-b border-surface-700/50 pb-1"
                >
                  <span className="text-gray-600 shrink-0 w-20">
                    {new Date(log.recorded_at).toLocaleTimeString('zh-CN', { hour12: false })}
                  </span>
                  <span
                    className={
                      log.status === 'online'
                        ? 'text-profit shrink-0 w-12'
                        : log.status === 'offline'
                          ? 'text-loss shrink-0 w-12'
                          : 'text-yellow-400 shrink-0 w-12'
                    }
                  >
                    {NODE_STATUS_LABELS[log.status] ?? log.status}
                  </span>
                  <span className="text-right shrink-0 w-20">{log.latency_ms.toFixed(1)}ms</span>
                  <span className="flex-1 text-gray-500 break-all">
                    {log.message ?? '—'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
