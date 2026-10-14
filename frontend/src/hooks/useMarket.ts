import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, refreshAccessToken } from '@/lib/api';
import {
  connectQuoteStream,
  type QuoteStreamStatus,
} from '@/lib/quoteStream';
import type {
  DataQualityReport,
  MarketTimeframe,
  PriceBar,
  Quote,
  QuoteListResponse,
  SymbolGroup,
  SymbolListResponse,
} from '@/types/api';

/** 品种列表（分组/搜索在后端内存过滤）— 30s 刷新。 */
export function useSymbols(group: SymbolGroup | '', q: string) {
  return useQuery({
    queryKey: ['market', 'symbols', { group, q }],
    queryFn: async () => {
      const { data } = await api.get<SymbolListResponse>('/market/symbols', {
        params: { group: group || undefined, q: q || undefined },
      });
      return data;
    },
    staleTime: 30_000,
  });
}

/** K 线数据质量检查（数据中心 P1：缺口/异常值，即时分析不落库）。 */
export function useDataQuality() {
  return useMutation({
    mutationFn: async (params: {
      symbol: string;
      timeframe: MarketTimeframe;
      start: number;
      end: number;
    }): Promise<DataQualityReport> => {
      const { data } = await api.get<DataQualityReport>('/market/quality', {
        params,
      });
      return data;
    },
  });
}

/** 单品种详情。 */
export function useSymbol(name: string | undefined) {
  return useQuery({
    queryKey: ['market', 'symbol', name],
    queryFn: async () => {
      const { data } = await api.get(`/market/symbols/${name}`);
      return data;
    },
    enabled: Boolean(name),
  });
}

/** 批量报价 — 5s 轮询，keepPreviousData 防闪烁。 */
export function useQuotes(names: string[]) {
  const key = [...names].sort().join(',');
  return useQuery({
    queryKey: ['market', 'quotes', key],
    queryFn: async () => {
      const { data } = await api.get<QuoteListResponse>('/market/quotes', {
        params: { symbols: key },
      });
      return data;
    },
    enabled: key.length > 0,
    refetchInterval: 5_000,
    placeholderData: (prev) => prev,
  });
}

/** WS 实时报价流 + 5s 轮询降级（行情页实时化）。
 *
 * - `live`：WS 推送（tick 级，变化即达）
 * - `polling`：WS 断开重连期间，回退 REST 轮询
 * - `error`：连 REST 轮询也失败
 * - `degraded`：网关侧故障帧（连接保留，自动恢复）
 */
export function useQuotesStream(names: string[]): {
  quotes: Map<string, Quote>;
  status: QuoteStreamStatus;
  degraded: boolean;
} {
  const key = useMemo(() => [...names].sort().join(','), [names]);
  const [quotes, setQuotes] = useState<Map<string, Quote>>(new Map());
  const [wsStatus, setWsStatus] = useState<QuoteStreamStatus>('connecting');
  const [degraded, setDegraded] = useState(false);

  // 降级链：WS 不可用期间保持 REST 轮询（initial 'connecting' 也先拉一次）。
  const pollingQuery = useQuery({
    queryKey: ['market', 'quotes', key],
    queryFn: async () => {
      const { data } = await api.get<QuoteListResponse>('/market/quotes', {
        params: { symbols: key },
      });
      return data;
    },
    enabled: key.length > 0 && wsStatus !== 'live',
    refetchInterval: 5_000,
    placeholderData: (prev) => prev,
  });

  useEffect(() => {
    if (!key) {
      setQuotes(new Map());
      return;
    }
    const handle = connectQuoteStream({
      symbols: key.split(','),
      fetchTicket: async () => {
        const { data } = await api.post<{ ticket: string }>('/market/ws-ticket');
        return data.ticket;
      },
      getSocketUrl: (ticket) =>
        `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/quotes?ticket=${encodeURIComponent(ticket)}`,
      WebSocketImpl: window.WebSocket,
      onRefreshAuth: () => refreshAccessToken(),
      callbacks: {
        onStatus: (s) => {
          setWsStatus(s);
          if (s === 'live') setDegraded(false);
        },
        // 收到行情帧即视为恢复（gateway_error 后连接未断，无状态切换）。
        onQuotes: (items) => {
          setDegraded(false);
          setQuotes((prev) => {
            const next = new Map(prev);
            for (const q of items) next.set(q.symbol_id, q);
            return next;
          });
        },
        // 订阅生效：丢弃不在订阅集里的旧品种（分组/搜索切换残留）。
        onSubscribed: (symbols) =>
          setQuotes((prev) => {
            const next = new Map<string, Quote>();
            for (const s of symbols) {
              const q = prev.get(s);
              if (q) next.set(s, q);
            }
            return next;
          }),
        onDegraded: () => setDegraded(true),
      },
    });
    return () => handle.close();
  }, [key]);

  // 非实时期间由 REST 轮询接管整张报价表。
  useEffect(() => {
    if (wsStatus !== 'live' && pollingQuery.data) {
      setQuotes(
        new Map(pollingQuery.data.items.map((q) => [q.symbol_id, q])),
      );
    }
  }, [pollingQuery.data, wsStatus]);

  const status: QuoteStreamStatus =
    wsStatus === 'live' ? 'live' : pollingQuery.isError ? 'error' : 'polling';
  return { quotes, status, degraded };
}

/** K 线 — 切换周期时重新请求；refetchMs 用于低频校准（实时尾根由 WS 聚合）。 */
export function useKlines(
  name: string | undefined,
  timeframe: MarketTimeframe,
  limit = 500,
  refetchMs = 0,
) {
  return useQuery({
    queryKey: ['market', 'klines', name, timeframe, limit],
    queryFn: async () => {
      const { data } = await api.get<PriceBar[]>(
        `/market/symbols/${name}/klines`,
        { params: { timeframe, limit } },
      );
      return data;
    },
    enabled: Boolean(name),
    refetchInterval: refetchMs > 0 ? refetchMs : false,
    staleTime: 0,
  });
}
