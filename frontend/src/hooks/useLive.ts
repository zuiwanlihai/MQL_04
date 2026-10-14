import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  AdmissionResult,
  AlertListResponse,
  DeviationReport,
  LiveCreateBody,
  LiveListResponse,
  LiveLogListResponse,
  LiveRun,
  LiveRunDetail,
  LiveTradeListResponse,
  RiskEventListResponse,
  RiskRule,
  RiskRuleApplyTo,
  RiskRuleSpec,
} from '@/types/api';

const API = '/live';

export const LIVE_STATUS_LABELS: Record<string, string> = {
  pending: '启动中',
  running: '运行中',
  paused: '已暂停',
  stopping: '停止中',
  stopped: '已停止',
  failed: '失败',
};

export const RISK_ACTION_LABELS: Record<string, string> = {
  reject: '拒绝下单',
  halt_open_today: '当日停止开仓',
  close_all_and_stop: '全平仓 + 停止策略',
};

export const RISK_TYPE_LABELS: Record<string, string> = {
  daily_max_loss: '单日最大亏损（金额）',
  daily_max_loss_pct: '单日最大亏损（百分比）',
  max_drawdown: '最大总回撤（百分比）',
  max_lot_per_order: '单笔最大手数',
  max_open_positions: '最大持仓数',
  max_exposure: '最大同向敞口',
  max_orders_per_minute: '每分钟下单频率',
  consecutive_loss_cooldown: '连续亏损暂停',
  order_rate_window: '时间窗口限频',
  manual_lot_cap: '平台上限（单笔手数）',
  reconcile_frozen: '对账不一致冻结',
};

/** 各规则类型的阈值单位（输入框后缀 / 表格展示） */
export const RISK_TYPE_UNITS: Record<string, string> = {
  daily_max_loss: 'USD',
  daily_max_loss_pct: '%',
  max_drawdown: '%',
  max_lot_per_order: '手',
  max_open_positions: '单',
  max_exposure: '手',
  max_orders_per_minute: '次',
  consecutive_loss_cooldown: '笔',
  order_rate_window: '次',
};

export const DEFAULT_DEPLOY_RULES: RiskRuleSpec[] = [
  { type: 'daily_max_loss_pct', threshold: 5, action: 'halt_open_today' },
  { type: 'max_drawdown', threshold: 20, action: 'close_all_and_stop' },
  { type: 'max_lot_per_order', threshold: 0.1, action: 'reject' },
];

export interface LiveListParams {
  page: number;
  pageSize: number;
  strategyId?: string;
  accountId?: string;
  accountType?: 'demo' | 'real' | 'contest';
  status?: string;
}

/** 实盘列表 — 存在活动任务时 3s 轮询。 */
export function useLiveRuns(params: LiveListParams) {
  return useQuery({
    queryKey: [
      'live',
      {
        page: params.page,
        pageSize: params.pageSize,
        strategyId: params.strategyId,
        accountId: params.accountId,
        status: params.status,
      },
    ],
    queryFn: async () => {
      const { data } = await api.get<LiveListResponse>(API, {
        params: {
          page: params.page,
          page_size: params.pageSize,
          strategy_id: params.strategyId || undefined,
          account_id: params.accountId || undefined,
          account_type: params.accountType || undefined,
          status: params.status || undefined,
        },
      });
      return data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((t) => t.status === 'pending' || t.status === 'running' || t.status === 'stopping')
        ? 3000
        : false;
    },
  });
}

/** 任务详情 — 活动状态 3s 轮询账户镜像。 */
export function useLiveRun(id: string | undefined) {
  return useQuery({
    queryKey: ['live', id],
    queryFn: async () => {
      const { data } = await api.get<LiveRunDetail>(`${API}/${id}`);
      return data;
    },
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === 'pending' || s === 'running' || s === 'stopping' ? 3000 : false;
    },
  });
}

export function useLiveTrades(id: string | undefined, page: number, enabled: boolean) {
  return useQuery({
    queryKey: ['live', id, 'trades', page],
    queryFn: async () => {
      const { data } = await api.get<LiveTradeListResponse>(`${API}/${id}/trades`, {
        params: { page, page_size: 50 },
      });
      return data;
    },
    enabled: Boolean(id) && enabled,
    refetchInterval: enabled ? 5000 : false,
  });
}

export function useLiveLogs(
  id: string | undefined,
  opts: { page: number; kind?: string; enabled: boolean; poll: boolean },
) {
  return useQuery({
    queryKey: ['live', id, 'logs', opts.page, opts.kind],
    queryFn: async () => {
      const { data } = await api.get<LiveLogListResponse>(`${API}/${id}/logs`, {
        params: { page: opts.page, page_size: 200, kind: opts.kind || undefined },
      });
      return data;
    },
    enabled: Boolean(id) && opts.enabled,
    refetchInterval: opts.poll ? 5000 : false,
  });
}

/** 准入四闸检查（向导第 3 步）。 */
export function useCheckAdmission() {
  return useMutation({
    mutationFn: async (body: {
      strategy_id: string;
      version: number;
      account_id: string;
    }): Promise<AdmissionResult> => {
      const { data } = await api.post<AdmissionResult>(`${API}/wizard/check-admission`, body);
      return data;
    },
  });
}

function invalidateRun(qc: ReturnType<typeof useQueryClient>, id: string) {
  qc.invalidateQueries({ queryKey: ['live'] });
  qc.invalidateQueries({ queryKey: ['live', id] });
}

export function useDeployLiveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: LiveCreateBody): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(API, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live'] }),
  });
}

export function usePauseLiveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(`${API}/${id}/pause`);
      return data;
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}

export function useResumeLiveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(`${API}/${id}/resume`);
      return data;
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}

export function useStopLiveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, closePositions }: { id: string; closePositions: boolean }): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(`${API}/${id}/stop`, { close_positions: closePositions });
      return data;
    },
    onSuccess: (_, { id }) => invalidateRun(qc, id),
  });
}

/** 网格策略专用：把锚定价重置为当前最新买价（运行时生效）。 */
export function useResetLiveAnchor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(`${API}/${id}/reset-anchor`);
      return data;
    },
    // 子进程异步落盘 storage.json → 立即刷一次，1.2s 后再刷一次兜底
    onSuccess: (_, id) => {
      invalidateRun(qc, id);
      setTimeout(() => invalidateRun(qc, id), 1200);
    },
  });
}

/** 删除已终态（已停止/失败）的任务记录；运行中的任务必须先停止。 */
export function useDeleteLiveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live'] }),
  });
}

export function useResetLiveRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(`${API}/${id}/reset`);
      return data;
    },
    onSuccess: (_, id) => invalidateRun(qc, id),
  });
}

export function useKillSwitch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<{ status: string; runs: number }> => {
      const { data } = await api.post<{ status: string; runs: number }>(`${API}/kill-switch`, {
        confirm: 'KILL',
      });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live'] });
      qc.invalidateQueries({ queryKey: ['live-alerts'] });
    },
  });
}

export function useReAdmit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, note }: { id: string; note: string }): Promise<LiveRun> => {
      const { data } = await api.post<LiveRun>(`${API}/${id}/re-admit`, {
        review_note: note,
        confirm: 'READMIT',
      });
      return data;
    },
    onSuccess: (_, { id }) => invalidateRun(qc, id),
  });
}

export function useAlerts(enabled: boolean) {
  return useQuery({
    queryKey: ['live-alerts', 'feed'],
    queryFn: async () => {
      const { data } = await api.get<AlertListResponse>(`${API}/alerts`, {
        params: { page: 1, page_size: 20 },
      });
      return data;
    },
    enabled,
    refetchInterval: 10000,
  });
}

export function useAlertsPaged(opts: { page: number; unreadOnly: boolean }) {
  return useQuery({
    queryKey: ['live-alerts', 'paged', opts],
    queryFn: async () => {
      const { data } = await api.get<AlertListResponse>(`${API}/alerts`, {
        params: { page: opts.page, page_size: 50, unread_only: opts.unreadOnly },
      });
      return data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: 10000,
  });
}

export function useMarkAlertRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data } = await api.post(`${API}/alerts/${id}/read`);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live-alerts'] }),
  });
}

export function useMarkAllAlertsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<{ status: string; marked: number }> => {
      const { data } = await api.post<{ status: string; marked: number }>(
        `${API}/alerts/read-all`,
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live-alerts'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

export function useRiskEvents(opts: { accountId?: string; runId?: string; page: number }) {
  return useQuery({
    queryKey: ['live-risk-events', opts],
    queryFn: async () => {
      const { data } = await api.get<RiskEventListResponse>(`${API}/risk-events`, {
        params: {
          account_id: opts.accountId || undefined,
          run_id: opts.runId || undefined,
          page: opts.page,
          page_size: 50,
        },
      });
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

export interface RiskRuleCreateBody {
  scope: 'global' | 'account' | 'run';
  account_id?: string;
  run_id?: string;
  type: RiskRuleSpec['type'];
  threshold: number;
  cooldown_minutes?: number;
  window_minutes?: number;
  apply_to?: RiskRuleApplyTo;
  action: RiskRuleSpec['action'];
  note?: string;
}

/** 规则适用范围（订单来源）标签 */
export const APPLY_TO_LABELS: Record<string, string> = {
  ai: 'AI',
  manual: '手动',
  both: 'AI+手动',
};

export function useCreateRiskRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: RiskRuleCreateBody): Promise<RiskRule> => {
      const { data } = await api.post<RiskRule>(`${API}/rules`, body);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live-rules'] });
      qc.invalidateQueries({ queryKey: ['live-risk-events'] });
    },
  });
}

export function useUpdateRiskRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      ...patch
    }: {
      id: string;
      threshold?: number;
      action?: RiskRuleSpec['action'];
      enabled?: boolean;
      note?: string;
    }): Promise<RiskRule> => {
      const { data } = await api.patch<RiskRule>(`${API}/rules/${id}`, patch);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live-rules'] }),
  });
}

export function useDeleteRiskRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/rules/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live-rules'] }),
  });
}

export function useUnfreezeAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, note }: { id: string; note: string }): Promise<{ status: string }> => {
      const { data } = await api.post<{ status: string }>(`${API}/accounts/${id}/unfreeze`, {
        review_note: note,
        confirm: 'UNFREEZE',
      });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

export function useRiskRules(accountId?: string) {
  return useQuery({
    queryKey: ['live-rules', accountId],
    queryFn: async () => {
      const { data } = await api.get<RiskRule[]>(`${API}/rules`, {
        params: { account_id: accountId || undefined },
      });
      return data;
    },
  });
}

/** 实盘功能开关探针 — 控制侧边栏入口显隐（后端接口同样拒绝）。 */
export function useLiveMeta() {
  return useQuery({
    queryKey: ['live-meta'],
    queryFn: async () => {
      const { data } = await api.get<{
        enabled: boolean;
        manual_trade_enabled: boolean;
        max_concurrent: number;
        min_paper_days: number;
        observation_days: number;
      }>(`${API}/meta/status`);
      return data;
    },
    staleTime: 60_000,
    retry: 2,
    refetchInterval: 60_000,
  });
}

/**
 * 执行偏差报告（M4 P1）— 该 run 的实际成交 vs 理论成交逐笔对比。
 * compute-on-read：成交落库即数据就绪，无需手动刷新。
 */
export function useRunDeviation(runId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['live', runId, 'deviation'],
    queryFn: async () => {
      const { data } = await api.get<DeviationReport>(
        `${API}/${runId}/deviation`,
      );
      return data;
    },
    enabled: enabled && !!runId,
    retry: false,
  });
}
