import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { FileText, Plus, Trash2 } from 'lucide-react';
import { apiError } from '@/lib/api';
import {
  useCreateNotebook,
  useDeleteNotebook,
  useNotebooks,
} from '@/hooks/useNotebooks';

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(iso));
}

export default function NotebookListPage() {
  const navigate = useNavigate();
  const list = useNotebooks();
  const create = useCreateNotebook();
  const del = useDeleteNotebook();
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');

  async function onCreate() {
    const t = title.trim() || '未命名笔记本';
    try {
      const nb = await create.mutateAsync(t);
      setCreating(false);
      setTitle('');
      navigate(`/notebooks/${nb.id}`);
    } catch (e) {
      setError(apiError(e));
    }
  }

  async function onDelete(id: string, name: string) {
    if (!window.confirm(`删除笔记本「${name}」？该操作不可恢复。`)) return;
    await del.mutateAsync(id);
  }

  const items = list.data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">研究笔记</h1>
          <p className="text-sm text-gray-500 mt-1">
            沙箱子进程逐格执行 · get_bars 拉取行情 · plot 内置图表 · 变量状态跨格保持
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setCreating(true)}>
          <Plus className="w-4 h-4" /> 新建笔记本
        </button>
      </div>

      {creating && (
        <div className="card p-4 space-y-3">
          <input
            className="input-dark w-96 max-w-full"
            placeholder="笔记本标题…"
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onCreate();
              if (e.key === 'Escape') setCreating(false);
            }}
          />
          {error && <p className="text-sm text-red-400">{error}</p>}
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={onCreate}>
              创建
            </button>
            <button type="button" className="btn-secondary" onClick={() => setCreating(false)}>
              取消
            </button>
          </div>
        </div>
      )}

      {list.isLoading ? (
        <div className="card p-8 text-center text-sm text-gray-500">加载中…</div>
      ) : items.length === 0 ? (
        <div className="card p-10 text-center">
          <FileText className="w-10 h-10 mx-auto text-gray-600 mb-3" />
          <p className="text-sm text-gray-400">还没有笔记本，点击右上角新建一个开始研究。</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {items.map((nb) => (
            <div key={nb.id} className="card p-4 group">
              <div className="flex items-start justify-between gap-2">
                <Link
                  to={`/notebooks/${nb.id}`}
                  className="text-sm font-medium text-white hover:text-blue-400 flex items-center gap-2 min-w-0"
                >
                  <FileText className="w-4 h-4 flex-shrink-0 text-gray-500" />
                  <span className="truncate">{nb.title}</span>
                </Link>
                <button
                  type="button"
                  className="text-gray-600 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity"
                  onClick={() => onDelete(nb.id, nb.title)}
                  title="删除"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
                <span>{nb.cell_count} 个 cell</span>
                <span>更新于 {fmtDate(nb.updated_at)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
