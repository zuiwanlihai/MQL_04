import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  PaperCreate,
  PaperExecutionMode,
  PaperListResponse,
  PaperLogListResponse,
  PaperRun,
  PaperRunDetail,
  PaperTradeListResponse,
} from '@/types/api';

const API = '/paper';

/** 虚拟下单仅开放标准/乐观两档（保守档依赖次根开盘价，增量模式无法预知）。 */
export const PAPER_MODE_ASSUMPTION_TEXT: Record<PaperExecutionMode, string> = {
  standard:
    '标准档：以当根收盘价成交。买入=收盘价+点差+滑点；卖出=收盘价-滑点。默认滑点 1 点。',
  optimistic: '乐观档：以当根收盘价成交，不计点差与滑点。结果偏乐观，不具备实盘参考意义。',
};

export const PAPER_MODE_LABELS: Record<PaperExecutionMode, string> = {
  standard: '标准',
  optimistic: '乐观',
};

export const PAPER_STATUS_LABELS: Record<string, string> = {
  pending: '启动中',
  running: '运行中',
  paused: '已暂停',
  stopping: '停止中',
  stopped: '已停止',
  failed: '失败',
};

export interface PaperListParams {
  page: number;
  pageSize: number;
  strategyId?: string;
  status?: string;
}

/** 虚拟下单列表 — 存在活动任务时 3s 轮询。 */
export function usePaperRuns(params: PaperListParams) {
  return useQuery({
    queryKey: [
      'paper',
      { page: params.page, pageSize: params.pageSize, strategyId: params.strategyId, status: params.status },
    ],
    queryFn: async () => {
      const { data } = await api.get<PaperListResponse>(API, {
        params: {
          page: params.page,
          page_size: params.pageSize,
          strategy_id: params.strategyId || undefined,
          status: params.status || undefined,
        },
      });
      return data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((t) => t.status === 'pending' || t.status === 'running' || t.status === 'stopping')
        ? 3000
        : false;
    },
  });
}

/** 任务详情 — 活动状态 3s 轮询账户镜像。 */
export function usePaperRun(id: string | undefined) {
  return useQuery({
    queryKey: ['paper', id],
    queryFn: async () => {
      const { data } = await api.get<PaperRunDetail>(`${API}/${id}`);
      return data;
    },
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === 'pending' || s === 'running' || s === 'stopping' ? 3000 : false;
    },
  });
}

export function usePaperTrades(id: string | undefined, page: number, enabled: boolean) {
  return useQuery({
    queryKey: ['paper', id, 'trades', page],
    queryFn: async () => {
      const { data } = await api.get<PaperTradeListResponse>(`${API}/${id}/trades`, {
        params: { page, page_size: 50 },
      });
      return data;
    },
    enabled: Boolean(id) && enabled,
    refetchInterval: enabled ? 5000 : false,
  });
}

export function usePaperLogs(
  id: string | undefined,
  opts: { page: number; kind?: string; level?: string; enabled: boolean; poll: boolean },
) {
  return useQuery({
    queryKey: ['paper', id, 'logs', opts.page, opts.kind, opts.level],
    queryFn: async () => {
      const { data } = await api.get<PaperLogListResponse>(`${API}/${id}/logs`, {
        params: { page: opts.page, page_size: 200, kind: opts.kind || undefined, level: opts.level || undefined },
      });
      return data;
    },
    enabled: Boolean(id) && opts.enabled,
    refetchInterval: opts.poll ? 5000 : false,
  });
}

function invalidateRun(qc: ReturnType<typeof useQueryClient>, id: string) {
  qc.invalidateQueries({ queryKey: ['paper'] });
  qc.invalidateQueries({ queryKey: ['paper', id] });
}

export function useCreatePaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: PaperCreate): Promise<PaperRun> => {
      const { data } = await api.post<PaperRun>(API, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['paper'] }),
  });
}

export function usePausePaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<PaperRun> => {
      const { data } = await api.post<PaperRun>(`${API}/${id}/pause`);
      return data;
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}

export function useResumePaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<PaperRun> => {
      const { data } = await api.post<PaperRun>(`${API}/${id}/resume`);
      return data;
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}

export function useStopPaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, closePositions }: { id: string; closePositions: boolean }): Promise<PaperRun> => {
      const { data } = await api.post<PaperRun>(`${API}/${id}/stop`, { close_positions: closePositions });
      return data;
    },
    onSuccess: (_, { id }) => invalidateRun(qc, id),
  });
}

/** 网格策略专用：把锚定价重置为当前最新买价（运行时生效）。 */
export function useResetPaperAnchor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<PaperRun> => {
      const { data } = await api.post<PaperRun>(`${API}/${id}/reset-anchor`);
      return data;
    },
    // 子进程异步落盘 storage.json → 立即刷一次，1.2s 后再刷一次兜底
    onSuccess: (_, id) => {
      invalidateRun(qc, id);
      setTimeout(() => invalidateRun(qc, id), 1200);
    },
  });
}

export function useResetPaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<PaperRun> => {
      const { data } = await api.post<PaperRun>(`${API}/${id}/reset`);
      return data;
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}

/** 删除虚拟下单记录 — 仅终态（已停止/失败），成交与日志一并清理。 */
export function useDeletePaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/${id}`);
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}
