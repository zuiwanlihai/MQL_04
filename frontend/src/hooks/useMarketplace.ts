import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { buildListingQuery } from '@/lib/marketplace';
import type {
  MarketListingDetail,
  MarketListingListResponse,
  PublishListingPayload,
  ListingQuery,
  Strategy,
} from '@/types/api';

const API = '/market/listings';

/** 市场浏览 — 全登录用户可见，keepPreviousData 防翻页闪烁。 */
export function useListings(params: ListingQuery) {
  return useQuery({
    queryKey: ['marketplace', params],
    queryFn: async () => {
      const { data } = await api.get<MarketListingListResponse>(API, {
        params: buildListingQuery(params),
      });
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

/** 市场条目详情（含冻结源码）。 */
export function useListing(id: string | undefined) {
  return useQuery({
    queryKey: ['marketplace', id],
    queryFn: async () => {
      const { data } = await api.get<MarketListingDetail>(`${API}/${id}`);
      return data;
    },
    enabled: Boolean(id),
  });
}

/** 发布策略到市场（须 disclaimer_ack=true）。 */
export function usePublishListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: PublishListingPayload): Promise<MarketListingDetail> => {
      const { data } = await api.post<MarketListingDetail>(API, body);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['marketplace'] }),
  });
}

/** 作者下架（软删，幂等）。 */
export function useRemoveListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      await api.delete(`${API}/${id}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['marketplace'] }),
  });
}

/** 导入副本为本人新草稿策略。 */
export function useImportListing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }): Promise<Strategy> => {
      const { data } = await api.post<Strategy>(`${API}/${id}/import`, { name });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['marketplace'] });
      qc.invalidateQueries({ queryKey: ['strategies'] });
    },
  });
}
