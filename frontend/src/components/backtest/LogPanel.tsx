import { useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useBacktestLogs } from '@/hooks/useBacktests';
import type { BacktestLog } from '@/types/api';

const KIND_LABELS: Record<BacktestLog['kind'], string> = {
  log: '日志',
  signal: '信号',
  system: '系统',
};

const LEVEL_STYLES: Record<string, string> = {
  info: 'text-gray-300',
  warn: 'text-yellow-400',
  error: 'text-loss',
};

interface Props {
  taskId: string;
  poll: boolean;
}

export default function LogPanel({ taskId, poll }: Props) {
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState('');
  const [level, setLevel] = useState('');
  const logs = useBacktestLogs(taskId, { page, kind, level, enabled: true, poll });
  const items = logs.data?.items ?? [];
  const total = logs.data?.total ?? 0;
  const totalPages = Math.max(Math.ceil(total / 200), 1);

  return (
    <div className="card">
      <div className="card-header flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-medium text-gray-200">运行日志（{total} 行）</h3>
        <select
          className="input-dark w-28 text-xs py-1"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部类型</option>
          <option value="log">日志</option>
          <option value="signal">信号</option>
          <option value="system">系统</option>
        </select>
        <select
          className="input-dark w-24 text-xs py-1"
          value={level}
          onChange={(e) => {
            setLevel(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部级别</option>
          <option value="info">info</option>
          <option value="warn">warn</option>
          <option value="error">error</option>
        </select>
        {poll && <span className="text-xs text-gray-500 animate-pulse">实时刷新中…</span>}
      </div>
      <div className="card-body p-0">
        {logs.data?.truncated && (
          <p className="px-4 py-2 text-xs text-yellow-400 bg-yellow-500/10 border-b border-yellow-500/30">
            日志超过 5000 行上限，超出部分已被丢弃。
          </p>
        )}
        <div className="max-h-96 overflow-y-auto font-mono text-xs">
          {items.map((l) => (
            <div key={l.id} className="px-4 py-1 border-b border-gray-800/60 flex gap-3">
              <span className="text-gray-600 shrink-0">#{l.seq}</span>
              <span className="text-gray-500 shrink-0">
                {l.bar_time ? new Date(l.bar_time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '—'}
              </span>
              <span className="text-gray-500 shrink-0 w-10">{KIND_LABELS[l.kind]}</span>
              <span className={`shrink-0 w-12 ${LEVEL_STYLES[l.level] ?? 'text-gray-300'}`}>{l.level}</span>
              <span className="text-gray-300 break-all whitespace-pre-wrap">{l.message}</span>
            </div>
          ))}
          {items.length === 0 && !logs.isLoading && (
            <p className="py-6 text-center text-gray-500">暂无匹配日志</p>
          )}
        </div>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 py-2 border-t border-gray-700/50">
          <button
            type="button"
            className="btn-secondary px-2 py-1"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <span className="text-xs text-gray-400">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            className="btn-secondary px-2 py-1"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
