import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  AccountBindRequest,
  AccountListResponse,
  AccountProbeResult,
  AccountTestRequest,
  ActiveAccountInfo,
  ActiveAccountListResponse,
  AlgoTradingToggleResult,
  MT5Account,
  TradeWhitelistInfo,
} from '@/types/api';

/** 已绑定账户列表 — 30s 刷新，mutation 后 invalidate。 */
export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: async () => {
      const { data } = await api.get<AccountListResponse>('/accounts');
      return data;
    },
    refetchInterval: 30_000,
  });
}

/**
 * 各网关节点当前登录的账户（实时探活）— 15s 轮询。
 * 网关不可达时该节点 account=null（后端保证 200），前端据此显示离线。
 */
export function useActiveAccount() {
  return useQuery({
    queryKey: ['active-account'],
    queryFn: async (): Promise<ActiveAccountInfo[]> => {
      const { data } = await api.get<ActiveAccountListResponse>('/accounts/active');
      return data.items;
    },
    refetchInterval: 15_000,
  });
}

/** 一键切换登录（网关 mt5.login；期间行情中断 5-10s）。
 * 未记住密码的账户（has_credentials=false）必须携带本次 password（不落库）。 */
export function useActivateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { id: string; password?: string }): Promise<MT5Account> => {
      const { id, password } = args;
      const { data } = await api.post<MT5Account>(
        `/accounts/${id}/activate`,
        password ? { password } : undefined,
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['active-account'] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
    },
  });
}

/** 网关交易白名单状态 — 60s 轮询。 */
export function useTradeWhitelist() {
  return useQuery({
    queryKey: ['trade-whitelist'],
    queryFn: async (): Promise<TradeWhitelistInfo> => {
      const { data } = await api.get<TradeWhitelistInfo>('/trade/whitelist');
      return data;
    },
    refetchInterval: 60_000,
  });
}

/** 把 MT5 登录号加入网关交易白名单（立即生效并持久化）。 */
export function useAddTradeWhitelist() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (login: number): Promise<TradeWhitelistInfo> => {
      const { data } = await api.post<TradeWhitelistInfo>('/trade/whitelist', { login });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['trade-whitelist'] });
    },
  });
}

/** 测试连接（不落库）。 */
export function useTestAccount() {
  return useMutation({
    mutationFn: async (body: AccountTestRequest): Promise<AccountProbeResult> => {
      const { data } = await api.post<AccountProbeResult>('/accounts/test', body);
      return data;
    },
  });
}

/** 绑定账户。 */
export function useBindAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: AccountBindRequest): Promise<MT5Account> => {
      const { data } = await api.post<MT5Account>('/accounts', body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

/** 同步账户余额/净值。 */
export function useSyncAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<MT5Account> => {
      const { data } = await api.post<MT5Account>(`/accounts/${id}/sync`);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

/** 允许/禁止算法交易（账户级开关 + 同步 MT5 终端本体按钮）。
 * 禁用时策略自动下单被后端前置闸拒绝；终端按钮经网关注入 Ctrl+E 切换。 */
export function useToggleAlgoTrading() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (args: { id: string; enabled: boolean }): Promise<AlgoTradingToggleResult> => {
      const { data } = await api.post<AlgoTradingToggleResult>(
        `/accounts/${args.id}/algo-trading`,
        { enabled: args.enabled },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] });
      // 终端按钮实况在 active-account 探活里 → 立即刷新徽标（不必等 15s 轮询）
      qc.invalidateQueries({ queryKey: ['active-account'] });
    },
  });
}

/** 解绑账户。 */
export function useUnbindAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`/accounts/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}
