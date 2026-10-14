import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  NotebookCell,
  NotebookDetail,
  NotebookListResponse,
  NotebookRunResult,
  QueueOverview,
} from '@/types/api';

/** 研究笔记本列表。 */
export function useNotebooks() {
  return useQuery({
    queryKey: ['notebooks'],
    queryFn: async () => {
      const { data } = await api.get<NotebookListResponse>('/notebooks');
      return data;
    },
  });
}

/** 单个笔记本详情（含 cells）。 */
export function useNotebook(id: string | undefined) {
  return useQuery({
    queryKey: ['notebooks', id],
    queryFn: async () => {
      const { data } = await api.get<NotebookDetail>(`/notebooks/${id}`);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateNotebook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (title: string): Promise<NotebookDetail> => {
      const { data } = await api.post<NotebookDetail>('/notebooks', { title });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notebooks'] }),
  });
}

export function useUpdateNotebook(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      title?: string;
      cells?: NotebookCell[];
    }): Promise<NotebookDetail> => {
      const { data } = await api.patch<NotebookDetail>(`/notebooks/${id}`, body);
      return data;
    },
    onSuccess: (updated) => {
      qc.setQueryData(['notebooks', id], updated);
      qc.invalidateQueries({ queryKey: ['notebooks'] });
    },
  });
}

export function useDeleteNotebook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await api.delete(`/notebooks/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notebooks'] }),
  });
}

/** 运行一个 cell（子进程沙箱执行，覆盖全局 30s 超时）。 */
export function useRunCell(id: string) {
  return useMutation({
    mutationFn: async (p: { cellId: string; source: string }): Promise<NotebookRunResult> => {
      const { data } = await api.post<NotebookRunResult>(
        `/notebooks/${id}/run`,
        { cell_id: p.cellId, source: p.source },
        { timeout: 120_000 },
      );
      return data;
    },
  });
}

/** 重启内核（清空变量状态）。 */
export function useRestartKernel(id: string) {
  return useMutation({
    mutationFn: async (): Promise<NotebookRunResult> => {
      const { data } = await api.post<NotebookRunResult>(`/notebooks/${id}/restart`);
      return data;
    },
  });
}

/** 回测队列总览（任务队列监控，10s 轮询）。 */
export function useQueueOverview() {
  return useQuery({
    queryKey: ['backtests', 'queue-overview'],
    queryFn: async () => {
      const { data } = await api.get<QueueOverview>('/backtests/queue/overview');
      return data;
    },
    refetchInterval: 10_000,
  });
}
