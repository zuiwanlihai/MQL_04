import { useState } from 'react';
import { Link, ChevronLeft, ChevronRight, Plus, Search, Wand2 } from 'lucide-react';
import PublishListingDialog from '@/components/marketplace/PublishListingDialog';
import StrategyCreateDialog from '@/components/strategies/StrategyCreateDialog';
import StrategyTable from '@/components/strategies/StrategyTable';
import StrategyVersionsDialog from '@/components/strategies/StrategyVersionsDialog';
import { useStrategies } from '@/hooks/useStrategies';
import type { Strategy, StrategyStatus } from '@/types/api';

const STATUS_OPTIONS: { value: '' | StrategyStatus; label: string }[] = [
  { value: '', label: '全部状态' },
  { value: 'draft', label: '草稿' },
  { value: 'active', label: '启用' },
  { value: 'paused', label: '暂停' },
  { value: 'archived', label: '归档' },
];

export default function StrategyListPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<'' | StrategyStatus>('');
  const [q, setQ] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [versionsFor, setVersionsFor] = useState<Strategy | null>(null);
  const [publishFor, setPublishFor] = useState<Strategy | null>(null);

  const list = useStrategies({ page, pageSize: 20, status, q });
  const data = list.data;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">策略研发</h1>
          <p className="text-sm text-gray-500 mt-1">
            Python 策略管理 · 版本冻结 · 模板库 · Web 编辑器（SDK v1）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/strategies/builder" className="btn-secondary">
            <Wand2 className="w-4 h-4" /> 可视化构建
          </Link>
          <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>
            <Plus className="w-4 h-4" /> 新建策略
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative">
          <Search className="w-4 h-4 text-gray-500 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            className="input-dark pl-9 w-64"
            placeholder="搜索名称 / 描述…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <select
          className="input-dark w-32"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as '' | StrategyStatus);
            setPage(1);
          }}
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {data && (
          <span className="text-xs text-gray-500">
            共 {data.total} 个策略 · 第 {data.page}/{data.total_pages} 页
          </span>
        )}
      </div>

      <StrategyTable
        strategies={data?.items ?? []}
        isLoading={list.isLoading}
        error={list.error}
        onShowVersions={setVersionsFor}
        onPublish={setPublishFor}
      />

      {data && data.total_pages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <button
            type="button"
            className="btn-secondary px-3 py-1.5"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm text-gray-400">
            {page} / {data.total_pages}
          </span>
          <button
            type="button"
            className="btn-secondary px-3 py-1.5"
            disabled={page >= data.total_pages}
            onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}

      <StrategyCreateDialog open={createOpen} onClose={() => setCreateOpen(false)} />

      <PublishListingDialog strategy={publishFor} onClose={() => setPublishFor(null)} />

      {versionsFor && (
        <StrategyVersionsDialog strategy={versionsFor} onClose={() => setVersionsFor(null)} />
      )}
    </div>
  );
}
