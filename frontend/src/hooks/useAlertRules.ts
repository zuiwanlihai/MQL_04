import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  AlertRule,
  AlertRuleCreateBody,
  AlertRuleUpdateBody,
} from '@/types/api';

const API = '/alert-rules/rules';

/** 本人自定义告警规则列表。 */
export function useAlertRules() {
  return useQuery({
    queryKey: ['alert-rules'],
    queryFn: async () => {
      const { data } = await api.get<AlertRule[]>(API);
      return data;
    },
  });
}

function invalidate(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['alert-rules'] });
}

export function useCreateAlertRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: AlertRuleCreateBody): Promise<AlertRule> => {
      const { data } = await api.post<AlertRule>(API, body);
      return data;
    },
    onSuccess: () => invalidate(qc),
  });
}

export function useUpdateAlertRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      ...patch
    }: { id: string } & AlertRuleUpdateBody): Promise<AlertRule> => {
      const { data } = await api.patch<AlertRule>(`${API}/${id}`, patch);
      return data;
    },
    onSuccess: () => invalidate(qc),
  });
}

export function useDeleteAlertRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/${id}`);
    },
    onSuccess: () => invalidate(qc),
  });
}
