import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { AuditListResponse, AuditVerifyResult } from '@/types/api';

const API = '/audit';

export interface AuditListParams {
  page: number;
  pageSize: number;
  start?: string;
  end?: string;
  action?: string;
  actorId?: string;
  targetType?: string;
  targetId?: string;
  result?: string;
}

/** 审计日志列表（admin）— 过滤 + 分页。 */
export function useAuditList(params: AuditListParams) {
  return useQuery({
    queryKey: ['audit', params],
    queryFn: async () => {
      const { data } = await api.get<AuditListResponse>(API, {
        params: {
          page: params.page,
          page_size: params.pageSize,
          start: params.start || undefined,
          end: params.end || undefined,
          action: params.action || undefined,
          actor_id: params.actorId || undefined,
          target_type: params.targetType || undefined,
          target_id: params.targetId || undefined,
          result: params.result || undefined,
        },
      });
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

/** 一键全链完整性校验。 */
export function useVerifyAuditChain() {
  return useMutation({
    mutationFn: async (): Promise<AuditVerifyResult> => {
      const { data } = await api.post<AuditVerifyResult>(`${API}/verify`);
      return data;
    },
  });
}

/** 导出审计（CSV/JSON）— 浏览器端触发下载，导出行为由后端记审计。 */
export function useExportAudit() {
  return useMutation({
    mutationFn: async (filters: {
      format: 'csv' | 'json';
      start?: string;
      end?: string;
      action?: string;
      result?: string;
    }) => {
      const { format, ...rest } = filters;
      const { data } = await api.get(`${API}/export`, {
        params: {
          format,
          start: rest.start || undefined,
          end: rest.end || undefined,
          action: rest.action || undefined,
          result: rest.result || undefined,
        },
        responseType: 'blob',
      });
      return { blob: data as Blob, filename: `audit_logs.${format}` };
    },
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
  });
}
