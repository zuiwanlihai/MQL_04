import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  AnalysisListResponse,
  BacktestAnalysis,
  BacktestCreate,
  BacktestListResponse,
  BacktestLog,
  BacktestReportResponse,
  BacktestTask,
  BacktestTaskDetail,
  ExecutionMode,
  LogListResponse,
  MonteCarloCreate,
  OptimizationCreate,
  PortfolioCreate,
  PortfolioBacktest,
  PortfolioListResponse,
  SensitivityCompare,
  SlippageCalibration,
  TradeListResponse,
  WalkForwardCreate,
} from '@/types/api';

const API = '/backtests';

/** 与后端 sandbox/payload.py describe_execution_mode 保持一致的档位假设文案。 */
export const MODE_ASSUMPTION_TEXT: Record<ExecutionMode, string> = {
  conservative:
    '保守档：信号在当根收盘产生，于次根 K 线开盘成交。开多=次根开盘价+点差+滑点；开空=次根开盘价-滑点；平多=次根开盘价-滑点；平空=次根开盘价+点差+滑点。默认滑点 2 点；最后一根 K 线的挂单被拒绝（MARKET_CLOSED）。',
  standard:
    '标准档：以当根收盘价成交。买入=收盘价+点差+滑点；卖出=收盘价-滑点。默认滑点 1 点。',
  optimistic: '乐观档：以当根收盘价成交，不计点差与滑点。结果偏乐观，不具备实盘参考意义。',
};

export const MODE_LABELS: Record<ExecutionMode, string> = {
  conservative: '保守',
  standard: '标准',
  optimistic: '乐观',
};

export interface BacktestListParams {
  page: number;
  pageSize: number;
  strategyId?: string;
  status?: string;
}

/** 回测任务列表 — 存在进行中任务时 2s 轮询刷新进度。 */
export function useBacktests(params: BacktestListParams) {
  return useQuery({
    queryKey: [
      'backtests',
      { page: params.page, pageSize: params.pageSize, strategyId: params.strategyId, status: params.status },
    ],
    queryFn: async () => {
      const { data } = await api.get<BacktestListResponse>(API, {
        params: {
          page: params.page,
          page_size: params.pageSize,
          strategy_id: params.strategyId || undefined,
          status: params.status || undefined,
        },
      });
      return data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((t) => t.status === 'pending' || t.status === 'running' || t.status === 'cancelling')
        ? 2000
        : false;
    },
  });
}

/** 任务详情 — running 时 2s 轮询。 */
export function useBacktestTask(id: string | undefined) {
  return useQuery({
    queryKey: ['backtests', id],
    queryFn: async () => {
      const { data } = await api.get<BacktestTaskDetail>(`${API}/${id}`);
      return data;
    },
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === 'pending' || s === 'running' || s === 'cancelling' ? 2000 : false;
    },
  });
}

/** 报告（409 = 尚未生成 → 静默不重试，由外层轮询任务状态后再次拉取）。 */
export function useBacktestReport(id: string | undefined, taskStatus: string | undefined) {
  return useQuery({
    queryKey: ['backtests', id, 'report'],
    queryFn: async () => {
      const { data } = await api.get<BacktestReportResponse>(`${API}/${id}/report`);
      return data;
    },
    enabled: Boolean(id) && taskStatus === 'completed',
    retry: false,
  });
}

export function useBacktestTrades(id: string | undefined, page: number, enabled: boolean) {
  return useQuery({
    queryKey: ['backtests', id, 'trades', page],
    queryFn: async () => {
      const { data } = await api.get<TradeListResponse>(`${API}/${id}/trades`, {
        params: { page, page_size: 50 },
      });
      return data;
    },
    enabled: Boolean(id) && enabled,
  });
}

export function useBacktestLogs(
  id: string | undefined,
  opts: { page: number; kind?: string; level?: string; enabled: boolean; poll: boolean },
) {
  return useQuery({
    queryKey: ['backtests', id, 'logs', opts.page, opts.kind, opts.level],
    queryFn: async () => {
      const { data } = await api.get<LogListResponse>(`${API}/${id}/logs`, {
        params: { page: opts.page, page_size: 200, kind: opts.kind || undefined, level: opts.level || undefined },
      });
      return data;
    },
    enabled: Boolean(id) && opts.enabled,
    refetchInterval: opts.poll ? 5000 : false,
  });
}

export function useSensitivityCompare(groupId: string | undefined) {
  return useQuery({
    queryKey: ['backtests', 'compare', groupId],
    queryFn: async () => {
      const { data } = await api.get<SensitivityCompare>(`${API}/compare/${groupId}`);
      return data;
    },
    enabled: Boolean(groupId),
    refetchInterval: (query) => {
      const d = query.state.data;
      if (!d) return false;
      return d.conservative.status !== 'completed' || d.standard.status !== 'completed' ? 2000 : false;
    },
  });
}

export function useCreateBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: BacktestCreate): Promise<BacktestTask[]> => {
      const { data } = await api.post<BacktestTask[]>(API, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }),
  });
}

export function useCancelBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<BacktestTask> => {
      const { data } = await api.post<BacktestTask>(`${API}/${id}/cancel`);
      return data;
    },
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['backtests'] });
      qc.invalidateQueries({ queryKey: ['backtests', id] });
    },
  });
}

export function useRerunBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<BacktestTask> => {
      const { data } = await api.post<BacktestTask>(`${API}/${id}/rerun`);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }),
  });
}

/** 打分 1-5 星; 传 null 清除评分。 */
export function useRateBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, rating }: { id: string; rating: number | null }): Promise<BacktestTask> => {
      const { data } = await api.post<BacktestTask>(`${API}/${id}/rating`, { rating });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests'] }),
  });
}

/** 删除回测任务记录 — 仅终态, 报告/成交/日志一并清理。 */
export function useDeleteBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/${id}`);
    },
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['backtests'] });
      qc.invalidateQueries({ queryKey: ['backtests', id] });
    },
  });
}

/** 批量删除回测任务 — 仅终态 (completed/failed/cancelled) 会被真正删除。 */
export function useBulkDeleteBacktests() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]): Promise<void> => {
      if (ids.length === 0) return;
      await api.post(`${API}/bulk-delete`, { ids });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backtests'] });
    },
  });
}

// ── Portfolio backtest (multi-symbol, PRD Phase 3 P2) ──

/** 组合回测列表 — 存在 running 时 3s 轮询（读取时聚合）。 */
export function usePortfolios(page: number) {
  return useQuery({
    queryKey: ['portfolio-backtests', page],
    queryFn: async () => {
      const { data } = await api.get<PortfolioListResponse>(`${API}/portfolios`, {
        params: { page, page_size: 20 },
      });
      return data;
    },
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((p) => p.status === 'running') ? 3000 : false;
    },
  });
}

/** 组合回测详情 — running 时 3s 轮询（读取时聚合）。 */
export function usePortfolio(id: string | undefined) {
  return useQuery({
    queryKey: ['portfolio-backtests', id],
    queryFn: async () => {
      const { data } = await api.get<PortfolioBacktest>(`${API}/portfolios/${id}`);
      return data;
    },
    enabled: Boolean(id),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 3000 : false),
  });
}

export function useCreatePortfolio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: PortfolioCreate): Promise<PortfolioBacktest> => {
      const { data } = await api.post<PortfolioBacktest>(`${API}/portfolios`, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['portfolio-backtests'] }),
  });
}

// ── Overfit-guard analyses (Walk-Forward / Monte Carlo) ──

/** 某回测任务的分析记录 — 存在 running 的 Walk-Forward 时 3s 轮询（读取时聚合）。 */
export function useAnalyses(taskId: string | undefined, taskCompleted: boolean) {
  return useQuery({
    queryKey: ['backtests', taskId, 'analyses'],
    queryFn: async () => {
      const { data } = await api.get<AnalysisListResponse>(`${API}/analyses`, {
        params: { task_id: taskId, page_size: 50 },
      });
      return data;
    },
    enabled: Boolean(taskId) && taskCompleted,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((a) => a.status === 'running') ? 3000 : false;
    },
  });
}

export function useCreateWalkForward(taskId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: WalkForwardCreate): Promise<BacktestAnalysis> => {
      const { data } = await api.post<BacktestAnalysis>(`${API}/${taskId}/walk-forward`, body);
      return data;
    },
    onSuccess: (_, __) => {
      qc.invalidateQueries({ queryKey: ['backtests', taskId, 'analyses'] });
      qc.invalidateQueries({ queryKey: ['backtests'] });
    },
  });
}

export function useCreateMonteCarlo(taskId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: MonteCarloCreate): Promise<BacktestAnalysis> => {
      const { data } = await api.post<BacktestAnalysis>(`${API}/${taskId}/monte-carlo`, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['backtests', taskId, 'analyses'] }),
  });
}

/** 参数优化（网格/随机）— 每个组合一个真实子回测，读取时聚合。 */
export function useCreateOptimization(taskId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: OptimizationCreate): Promise<BacktestAnalysis> => {
      const { data } = await api.post<BacktestAnalysis>(`${API}/${taskId}/optimize`, body);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backtests', taskId, 'analyses'] });
      qc.invalidateQueries({ queryKey: ['backtests'] });
    },
  });
}

/** 下载成交 CSV（带鉴权头的 blob 下载）。 */
export async function downloadTradesCsv(id: string): Promise<void> {
  const resp = await api.get(`${API}/${id}/trades.csv`, { responseType: 'blob' });
  const url = URL.createObjectURL(resp.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `backtest_${id}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * 真实滑点校准（M4 P1）— 该用户全部实盘账户在指定品种上的实际成交滑点统计。
 * 回测/虚拟下单表单据此自动预填滑点默认值；样本不足（<5）时 insufficient=true。
 */
export function useSlippageCalibration(symbol: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['slippage-calibration', symbol],
    queryFn: async () => {
      const { data } = await api.get<SlippageCalibration>(
        `${API}/slippage-calibration`,
        { params: { symbol } },
      );
      return data;
    },
    enabled: enabled && !!symbol,
    staleTime: 60_000,
    retry: false,
  });
}

export type { BacktestLog };
