import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { PowerFixResult, SelfCheckResult } from '@/types/api';

/** 手动触发一次网关自检（不自动轮询——探针较重，按需点击）。 */
export function useRunSelfCheck() {
  return useMutation({
    mutationFn: async (): Promise<SelfCheckResult> => {
      const { data } = await api.get<SelfCheckResult>('/gateway/selfcheck', {
        timeout: 40_000, // 多个 OS 探针串行，最坏 ~25s
      });
      return data;
    },
  });
}

/** 电源方案修复：默认仅返回命令指引；execute=true 真实修改（管理员权限）。 */
export function usePowerFix() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (execute: boolean): Promise<PowerFixResult> => {
      const { data } = await api.post<PowerFixResult>('/gateway/power/fix', { execute });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['gateway'] }),
  });
}
