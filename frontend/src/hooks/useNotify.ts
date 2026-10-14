import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  ChannelCreateBody,
  ChannelTestResult,
  NotificationChannel,
} from '@/types/api';

const API = '/notify';

/** 本人通知渠道列表。 */
export function useNotifyChannels() {
  return useQuery({
    queryKey: ['notify-channels'],
    queryFn: async () => {
      const { data } = await api.get<NotificationChannel[]>(`${API}/channels`);
      return data;
    },
  });
}

function invalidate(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['notify-channels'] });
}

export function useCreateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ChannelCreateBody): Promise<NotificationChannel> => {
      const { data } = await api.post<NotificationChannel>(`${API}/channels`, body);
      return data;
    },
    onSuccess: () => invalidate(qc),
  });
}

export function useUpdateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      ...patch
    }: {
      id: string;
      min_level?: ChannelCreateBody['min_level'];
      enabled?: boolean;
    }): Promise<NotificationChannel> => {
      const { data } = await api.patch<NotificationChannel>(`${API}/channels/${id}`, patch);
      return data;
    },
    onSuccess: () => invalidate(qc),
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/channels/${id}`);
    },
    onSuccess: () => invalidate(qc),
  });
}

/** 发送一条测试消息到指定渠道（不产生站内告警）。 */
export function useTestChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<ChannelTestResult> => {
      const { data } = await api.post<ChannelTestResult>(`${API}/channels/${id}/test`);
      return data;
    },
    onSuccess: () => invalidate(qc),
  });
}
