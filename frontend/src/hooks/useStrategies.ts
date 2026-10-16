import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  Strategy,
  StrategyBuilderSave,
  StrategyCreate,
  StrategyDetail,
  StrategyListResponse,
  StrategySourceOut,
  StrategyUpdate,
  TemplateListResponse,
  StrategyTemplate,
  ValidationResult,
  VersionCreate,
  VersionListResponse,
  StrategyVersion,
} from '@/types/api';

const API = '/strategies';

export interface StrategyListParams {
  page: number;
  pageSize: number;
  status?: string;
  q?: string;
}

/** 策略列表 — 分页/筛选，keepPreviousData 防翻页闪烁。 */
export function useStrategies(params: StrategyListParams) {
  return useQuery({
    queryKey: ['strategies', { page: params.page, pageSize: params.pageSize, status: params.status, q: params.q }],
    queryFn: async () => {
      const { data } = await api.get<StrategyListResponse>(API, {
        params: {
          page: params.page,
          page_size: params.pageSize,
          status: params.status || undefined,
          q: params.q || undefined,
        },
      });
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

/** 策略详情（含工作副本源码）。 */
export function useStrategy(id: string | undefined) {
  return useQuery({
    queryKey: ['strategies', id],
    queryFn: async () => {
      const { data } = await api.get<StrategyDetail>(`${API}/${id}`);
      return data;
    },
    enabled: Boolean(id),
  });
}

export function useCreateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: StrategyCreate): Promise<Strategy> => {
      const { data } = await api.post<Strategy>(API, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
}

export function useUpdateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: StrategyUpdate }): Promise<Strategy> => {
      const { data } = await api.patch<Strategy>(`${API}/${id}`, body);
      return data;
    },
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['strategies'] });
      qc.invalidateQueries({ queryKey: ['strategies', id] });
    },
  });
}

export function useDeleteStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  });
}

/** 保存工作副本（乐观锁）。 */
export function useSaveSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      source_code,
      base_updated_at,
    }: {
      id: string;
      source_code: string;
      base_updated_at?: string | null;
    }): Promise<StrategySourceOut> => {
      const { data } = await api.put<StrategySourceOut>(`${API}/${id}/source`, {
        source_code,
        base_updated_at: base_updated_at ?? null,
      });
      return data;
    },
    onSuccess: (_, { id }) => qc.invalidateQueries({ queryKey: ['strategies', id] }),
  });
}

/**
 * 原子保存构建器：source_code + builder_config + 元数据单次事务内一起写入。
 * 替换原 BuilderPage 两次独立调用（saveSource 再 update）的非原子保存流。
 * 返回完整 StrategyDetail（含 source_code_hash）供前端更新缓存。
 */
export function useSaveBuilder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      payload,
    }: {
      id: string;
      payload: StrategyBuilderSave;
    }): Promise<StrategyDetail> => {
      const { data } = await api.put<StrategyDetail>(`${API}/${id}/builder-save`, payload);
      return data;
    },
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['strategies', id] });
      qc.invalidateQueries({ queryKey: ['strategies'] });
    },
  });
}

/** 独立 AST 校验（debounce 自动调用）。 */
export function useValidateSource() {
  return useMutation({
    mutationFn: async (source_code: string): Promise<ValidationResult> => {
      const { data } = await api.post<ValidationResult>(`${API}/validate`, { source_code });
      return data;
    },
  });
}

export function useVersions(id: string | undefined) {
  return useQuery({
    queryKey: ['strategies', id, 'versions'],
    queryFn: async () => {
      const { data } = await api.get<VersionListResponse>(`${API}/${id}/versions`);
      return data;
    },
    enabled: Boolean(id),
  });
}

export function useFreezeVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: VersionCreate }): Promise<StrategyVersion> => {
      const { data } = await api.post<StrategyVersion>(`${API}/${id}/versions`, body);
      return data;
    },
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['strategies', id] });
      qc.invalidateQueries({ queryKey: ['strategies', id, 'versions'] });
    },
  });
}

export function useRollbackVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, version }: { id: string; version: number }): Promise<StrategyVersion> => {
      const { data } = await api.post<StrategyVersion>(`${API}/${id}/versions/${version}/rollback`);
      return data;
    },
    onSuccess: (_, { id }) => {
      qc.invalidateQueries({ queryKey: ['strategies', id] });
      qc.invalidateQueries({ queryKey: ['strategies', id, 'versions'] });
    },
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: ['strategies', 'templates'],
    queryFn: async () => {
      const { data } = await api.get<TemplateListResponse>(`${API}/templates`);
      return data;
    },
    staleTime: 300_000,
  });
}

export function useTemplate(id: string | undefined) {
  return useQuery({
    queryKey: ['strategies', 'templates', id],
    queryFn: async () => {
      const { data } = await api.get<StrategyTemplate>(`${API}/templates/${id}`);
      return data;
    },
    enabled: Boolean(id),
  });
}
