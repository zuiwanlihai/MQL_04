import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  TickCoverageResponse,
  TickListResponse,
  TickPurgeResponse,
  TickSyncJobListResponse,
  TickSyncRequest,
} from '@/types/api';

/** 各品种已缓存 tick 覆盖范围 — 15s 轮询（同步任务写库节奏）。 */
export function useTickCoverage() {
  return useQuery({
    queryKey: ['ticks', 'coverage'],
    queryFn: async () => {
      const { data } = await api.get<TickCoverageResponse>('/ticks/coverage');
      return data;
    },
    refetchInterval: 15_000,
  });
}

/** 当前用户的 tick 同步任务列表 — 3s 轮询。 */
export function useTickJobs(limit = 50) {
  return useQuery({
    queryKey: ['ticks', 'jobs', limit],
    queryFn: async () => {
      const { data } = await api.get<TickSyncJobListResponse>('/ticks/jobs', {
        params: { limit },
      });
      return data;
    },
    refetchInterval: 3_000,
  });
}

/** 发起 tick 同步（后端串行执行器排队处理）。 */
export function useRequestTickSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: TickSyncRequest) => {
      const { data } = await api.post('/ticks/sync', body);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ticks', 'jobs'] });
      qc.invalidateQueries({ queryKey: ['ticks', 'coverage'] });
    },
  });
}

/** 清除某品种全部已缓存 tick。 */
export function usePurgeTicks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (symbol: string): Promise<TickPurgeResponse> => {
      const { data } = await api.delete<TickPurgeResponse>(
        `/ticks/${encodeURIComponent(symbol)}`,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ticks'] }),
  });
}

/** 查询某品种已存储的 tick 明细（用于预览）。 */
export function useTicks(symbol: string | undefined, start?: number, end?: number) {
  return useQuery({
    queryKey: ['ticks', 'rows', symbol, start ?? 0, end ?? 0],
    queryFn: async () => {
      const { data } = await api.get<TickListResponse>(
        `/ticks/${encodeURIComponent(symbol!)}`,
        { params: { start, end, limit: 50 } },
      );
      return data;
    },
    enabled: Boolean(symbol),
  });
}
