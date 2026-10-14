import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useAuthStore } from '@/store/useAuthStore';
import type { TwoFAEnableOut, TwoFASetupOut, TwoFAStatusOut } from '@/types/api';

const API = '/auth/2fa';

/** 2FA 状态（供设置页展示）。 */
export function use2FAStatus() {
  return useQuery({
    queryKey: ['2fa-status'],
    queryFn: async () => {
      const { data } = await api.get<TwoFAStatusOut>(`${API}/status`);
      return data;
    },
    staleTime: 30_000,
  });
}

function invalidateAuth() {
  // user.totp_enabled 存在 zustand 会话里 — 通过 refresh 重新拉取 /auth/me 数据
  void useAuthStore.getState().checkSession();
}

export function useSetup2FA() {
  return useMutation({
    mutationFn: async (): Promise<TwoFASetupOut> => {
      const { data } = await api.post<TwoFASetupOut>(`${API}/setup`);
      return data;
    },
  });
}

export function useEnable2FA() {
  return useMutation({
    mutationFn: async (code: string): Promise<TwoFAEnableOut> => {
      const { data } = await api.post<TwoFAEnableOut>(`${API}/enable`, { code });
      return data;
    },
    onSuccess: () => invalidateAuth(),
  });
}

export function useDisable2FA() {
  return useMutation({
    mutationFn: async (password: string): Promise<void> => {
      await api.post(`${API}/disable`, { password });
    },
    onSuccess: () => invalidateAuth(),
  });
}
