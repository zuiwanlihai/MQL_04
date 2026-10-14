import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  GatewayNode,
  GatewayNodeListResponse,
  HealthLogListResponse,
} from '@/types/api';

/** 网关节点列表 — 5s 轮询（心跳写库节奏）。 */
export function useGatewayNodes() {
  return useQuery({
    queryKey: ['gateway', 'nodes'],
    queryFn: async () => {
      const { data } = await api.get<GatewayNodeListResponse>('/gateway/nodes');
      return data;
    },
    refetchInterval: 5_000,
  });
}

/** 手动注册网关节点（后端即时探活）。 */
export function useCreateGatewayNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string; host: string; port: number }): Promise<GatewayNode> => {
      const { data } = await api.post<GatewayNode>('/gateway/nodes', body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['gateway', 'nodes'] }),
  });
}

/** 删除网关节点（健康日志随外键级联清除）。 */
export function useDeleteGatewayNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`/gateway/nodes/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['gateway', 'nodes'] }),
  });
}

/** 网关健康日志 — 15s 轮询。 */
export function useHealthLogs(nodeId: string | undefined, limit = 50) {
  return useQuery({
    queryKey: ['gateway', 'health-logs', nodeId, limit],
    queryFn: async () => {
      const { data } = await api.get<HealthLogListResponse>(
        '/gateway/health-logs',
        { params: { node_id: nodeId, limit } },
      );
      return data;
    },
    enabled: Boolean(nodeId),
    refetchInterval: 15_000,
  });
}
