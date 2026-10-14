import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type {
  BrokerDeal,
  ManualCancelBody,
  ManualCloseBody,
  ManualOrderBody,
  ManualPendingBody,
  TradeActionResult,
  TradeSnapshot,
} from '@/types/api';

const API = '/trade';

/** 账户镜像快照（持仓+挂单）；面板打开时轮询刷新。 */
export function useTradeSnapshot(accountId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['trade', 'snapshot', accountId],
    queryFn: async () => {
      const { data } = await api.get<TradeSnapshot>(
        `${API}/accounts/${accountId}/snapshot`,
      );
      return data;
    },
    enabled: Boolean(accountId) && enabled,
    refetchInterval: enabled ? 10_000 : false,
  });
}

/** 近 N 小时成交回报。 */
export function useTradeDeals(
  accountId: string | undefined,
  hours: number,
  enabled = true,
  refetchInterval: number | false = false,
) {
  return useQuery({
    queryKey: ['trade', 'deals', accountId, hours],
    queryFn: async () => {
      const { data } = await api.get<BrokerDeal[]>(
        `${API}/accounts/${accountId}/deals`,
        { params: { hours } },
      );
      return data;
    },
    enabled: Boolean(accountId) && enabled,
    refetchInterval,
  });
}

function useTradeAction<T>(path: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: T): Promise<TradeActionResult> => {
      const { data } = await api.post<TradeActionResult>(`${API}/${path}`, body);
      return data;
    },
    onSuccess: (res, body) => {
      // 写操作成功后立即刷新该账户的持仓/挂单镜像
      const accountId = (body as { account_id: string }).account_id;
      void res;
      qc.invalidateQueries({ queryKey: ['trade', 'snapshot', accountId] });
      qc.invalidateQueries({ queryKey: ['trade', 'deals', accountId] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
    },
  });
}

/** 市价单（真实资金！后端风控闸 + 审计）。 */
export const useManualOrder = () => useTradeAction<ManualOrderBody>('order');

/** 挂限价/突破单（后端风控闸 + 审计）。 */
export const useManualPending = () => useTradeAction<ManualPendingBody>('pending');

/** 平仓（全平/部分平）。 */
export const useManualClose = () => useTradeAction<ManualCloseBody>('close');

/** 撤销挂单。 */
export const useManualCancel = () => useTradeAction<ManualCancelBody>('cancel');
