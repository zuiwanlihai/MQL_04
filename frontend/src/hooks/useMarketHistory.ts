import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  HistoryBackfillRequest,
  HistoryBackfillResponse,
  HistoryCoverageResponse,
  HistoryProbeRequest,
  HistoryRemoveResponse,
  HistorySyncResponse,
  MarketHistoryRange,
} from '@/types/api';

/** 已追踪的终端本地 K 线范围 — 15s 轮询。 */
export function useHistoryCoverage() {
  return useQuery({
    queryKey: ['market-history', 'coverage'],
    queryFn: async () => {
      const { data } = await api.get<HistoryCoverageResponse>('/market/history/coverage');
      return data;
    },
    refetchInterval: 15_000,
  });
}

/** 探测终端本地范围并追踪（品种+周期）。 */
export function useProbeHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: HistoryProbeRequest): Promise<MarketHistoryRange> => {
      const { data } = await api.post<MarketHistoryRange>('/market/history/probe', body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['market-history'] }),
  });
}

/** 补数：触发终端向券商下载 [start, end)（可能需要约 1 分钟，覆盖全局 30s 超时）。 */
export function useHistoryBackfill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: HistoryBackfillRequest): Promise<HistoryBackfillResponse> => {
      const { data } = await api.post<HistoryBackfillResponse>(
        '/market/history/backfill',
        body,
        { timeout: 120_000 },
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['market-history'] }),
  });
}

/** 立即执行一轮增量同步。 */
export function useSyncHistoryRound() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<HistorySyncResponse> => {
      const { data } = await api.post<HistorySyncResponse>('/market/history/sync');
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['market-history'] }),
  });
}

/** 移除追踪（不删除任何数据，仅停止跟踪）。 */
export function useRemoveHistoryCoverage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (p: { symbol: string; timeframe: string }): Promise<HistoryRemoveResponse> => {
      const { data } = await api.delete<HistoryRemoveResponse>(
        `/market/history/coverage/${encodeURIComponent(p.symbol)}/${encodeURIComponent(p.timeframe)}`,
      );
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['market-history'] }),
  });
}
