import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  Loader2,
  Play,
  Plus,
  RotateCcw,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import { apiError } from '@/lib/api';
import {
  useNotebook,
  useRestartKernel,
  useRunCell,
  useUpdateNotebook,
} from '@/hooks/useNotebooks';
import type { NotebookCell } from '@/types/api';
import NotebookLineChart from '@/components/notebook/NotebookLineChart';

let _cellSeq = 0;
function newCellId(): string {
  _cellSeq += 1;
  return `cell-${Date.now().toString(36)}-${_cellSeq}`;
}

export default function NotebookDetailPage() {
  const { id } = useParams<{ id: string }>();
  const query = useNotebook(id);
  const update = useUpdateNotebook(id ?? '');
  const run = useRunCell(id ?? '');
  const restart = useRestartKernel(id ?? '');

  const [cells, setCells] = useState<NotebookCell[]>([]);
  const [title, setTitle] = useState('');
  const [dirty, setDirty] = useState(false);
  const [runningCell, setRunningCell] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadedRef = useRef(false);

  // Load once from the server; afterwards local edits win.
  useEffect(() => {
    if (query.data && !loadedRef.current) {
      loadedRef.current = true;
      setCells(query.data.cells);
      setTitle(query.data.title);
    }
  }, [query.data]);

  const persist = useCallback(
    (next: NotebookCell[], nextTitle: string) => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(async () => {
        try {
          await update.mutateAsync({ title: nextTitle, cells: next });
          setDirty(false);
        } catch (e) {
          setNotice(`自动保存失败：${apiError(e)}`);
        }
      }, 800);
    },
    [update],
  );

  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
  }, []);

  const editCell = (cellId: string, source: string) => {
    setCells((prev) => {
      const next = prev.map((c) => (c.id === cellId ? { ...c, source } : c));
      setDirty(true);
      persist(next, title);
      return next;
    });
  };

  const addCell = (type: 'code' | 'markdown') => {
    setCells((prev) => {
      const next = [...prev, { id: newCellId(), type, source: '', outputs: [] }];
      setDirty(true);
      persist(next, title);
      return next;
    });
  };

  const removeCell = (cellId: string) => {
    setCells((prev) => {
      const next = prev.filter((c) => c.id !== cellId);
      setDirty(true);
      persist(next, title);
      return next;
    });
  };

  const moveCell = (cellId: string, dir: -1 | 1) => {
    setCells((prev) => {
      const i = prev.findIndex((c) => c.id === cellId);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      setDirty(true);
      persist(next, title);
      return next;
    });
  };

  async function onRun(cell: NotebookCell) {
    setNotice('');
    setRunningCell(cell.id);
    try {
      const result = await run.mutateAsync({ cellId: cell.id, source: cell.source });
      const outputs: unknown[] = [];
      if (result.stdout) outputs.push({ kind: 'stdout', text: result.stdout });
      if (result.result_repr) outputs.push({ kind: 'result', text: result.result_repr });
      for (const chart of result.charts) outputs.push({ kind: 'chart', chart });
      if (result.message) outputs.push({ kind: 'note', text: result.message });
      if (result.etype) {
        outputs.push({
          kind: 'error',
          etype: result.etype,
          text: result.traceback || result.etype,
          state_reset: result.state_reset,
        });
      }
      setCells((prev) => {
        const next = prev.map((c) => (c.id === cell.id ? { ...c, outputs } : c));
        setDirty(true);
        persist(next, title);
        return next;
      });
    } catch (e) {
      setNotice(apiError(e));
    } finally {
      setRunningCell(null);
    }
  }

  async function onRestart() {
    try {
      const r = await restart.mutateAsync();
      setNotice(r.message ?? '内核已重启');
    } catch (e) {
      setNotice(apiError(e));
    }
  }

  async function onSaveNow() {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    try {
      await update.mutateAsync({ title, cells });
      setDirty(false);
      setNotice('已保存');
    } catch (e) {
      setNotice(`保存失败：${apiError(e)}`);
    }
  }

  if (query.isLoading) {
    return <div className="card p-8 text-center text-sm text-gray-500">加载中…</div>;
  }
  if (query.error || !query.data) {
    return (
      <div className="card p-8 text-center space-y-3">
        <p className="text-sm text-red-400">
          {query.error ? apiError(query.error) : '笔记本不存在'}
        </p>
        <Link to="/notebooks" className="btn-secondary inline-flex items-center gap-1">
          <ChevronLeft className="w-4 h-4" /> 返回列表
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <Link to="/notebooks" className="text-gray-500 hover:text-gray-300">
            <ChevronLeft className="w-5 h-5" />
          </Link>
          <input
            className="bg-transparent text-xl font-semibold text-white outline-none focus:bg-surface-800 rounded px-2 py-1 min-w-0"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setDirty(true);
              persist(cells, e.target.value);
            }}
          />
          <span className="text-xs text-gray-600 flex-shrink-0">
            {dirty ? '未保存' : '已保存'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="btn-secondary" onClick={onSaveNow}>
            <Save className="w-4 h-4" /> 保存
          </button>
          <button type="button" className="btn-secondary" onClick={onRestart}>
            <RotateCcw className="w-4 h-4" /> 重启内核
          </button>
        </div>
      </div>

      {notice && (
        <div className="card px-3 py-2 text-sm text-gray-300 flex items-center justify-between">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice('')} className="text-gray-500 hover:text-gray-300">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {cells.map((cell, idx) => (
        <div key={cell.id} className="card overflow-hidden">
          <div className="flex items-center justify-between px-3 py-1.5 bg-surface-800/60 border-b border-gray-700/40">
            <span className="text-xs text-gray-500">
              [{idx + 1}] {cell.type === 'code' ? '代码' : 'Markdown'}
            </span>
            <div className="flex items-center gap-1">
              {cell.type === 'code' && (
                <button
                  type="button"
                  className="btn-secondary px-2 py-1 text-xs"
                  disabled={runningCell === cell.id}
                  onClick={() => onRun(cell)}
                  title="运行 (Ctrl+Enter)"
                >
                  {runningCell === cell.id ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Play className="w-3.5 h-3.5" />
                  )}
                  运行
                </button>
              )}
              <button type="button" className="text-gray-600 hover:text-gray-300 p-1" onClick={() => moveCell(cell.id, -1)} title="上移">
                <ArrowUp className="w-3.5 h-3.5" />
              </button>
              <button type="button" className="text-gray-600 hover:text-gray-300 p-1" onClick={() => moveCell(cell.id, 1)} title="下移">
                <ArrowDown className="w-3.5 h-3.5" />
              </button>
              <button type="button" className="text-gray-600 hover:text-red-400 p-1" onClick={() => removeCell(cell.id)} title="删除">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
          <textarea
            className="w-full bg-surface-900 text-gray-200 text-sm font-mono px-4 py-3 outline-none resize-y min-h-[72px]"
            rows={Math.max(3, cell.source.split('\n').length)}
            spellCheck={false}
            value={cell.source}
            onChange={(e) => editCell(cell.id, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && cell.type === 'code') {
                e.preventDefault();
                onRun(cell);
              }
            }}
          />
          {cell.outputs.length > 0 && (
            <div className="px-4 py-3 border-t border-gray-700/40 space-y-2">
              {cell.outputs.map((out, i) => {
                const o = out as {
                  kind: string;
                  text?: string;
                  etype?: string;
                  chart?: Parameters<typeof NotebookLineChart>[0]['chart'];
                };
                if (o.kind === 'chart' && o.chart) {
                  return (
                    <div key={i} className="rounded border border-gray-700/50 p-2">
                      <NotebookLineChart chart={o.chart} />
                    </div>
                  );
                }
                if (o.kind === 'error') {
                  return (
                    <pre
                      key={i}
                      className="text-xs font-mono text-red-400 whitespace-pre-wrap max-h-48 overflow-auto"
                    >
                      {o.etype}: {o.text}
                    </pre>
                  );
                }
                if (o.kind === 'result') {
                  return (
                    <pre key={i} className="text-xs font-mono text-emerald-400 whitespace-pre-wrap">
                      {o.text}
                    </pre>
                  );
                }
                if (o.kind === 'note') {
                  return (
                    <p key={i} className="text-xs text-amber-400">
                      {o.text}
                    </p>
                  );
                }
                return (
                  <pre key={i} className="text-xs font-mono text-gray-300 whitespace-pre-wrap max-h-64 overflow-auto">
                    {o.text}
                  </pre>
                );
              })}
            </div>
          )}
        </div>
      ))}

      <div className="flex items-center gap-2">
        <button type="button" className="btn-secondary" onClick={() => addCell('code')}>
          <Plus className="w-4 h-4" /> 代码格
        </button>
        <button type="button" className="btn-secondary" onClick={() => addCell('markdown')}>
          <Plus className="w-4 h-4" /> Markdown 格
        </button>
        <span className="text-xs text-gray-600 ml-2">Ctrl+Enter 运行当前格 · 800ms 防抖自动保存</span>
      </div>
    </div>
  );
}
