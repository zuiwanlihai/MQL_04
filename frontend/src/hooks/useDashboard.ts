import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { DashboardOverview } from '@/types/api';

/** 仪表盘聚合 — 有活动实盘时 5s 轮询，否则 30s。 */
export function useDashboardOverview() {
  return useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: async () => {
      const { data } = await api.get<DashboardOverview>('/dashboard/overview');
      return data;
    },
    refetchInterval: (query) => {
      const d = query.state.data;
      const busy =
        (d?.totals.running_live ?? 0) > 0 ||
        (d?.live_runs ?? []).some((r) => r.status === 'pending' || r.status === 'stopping');
      return busy ? 5_000 : 30_000;
    },
  });
}
