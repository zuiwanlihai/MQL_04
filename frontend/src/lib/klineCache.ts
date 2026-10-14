/**
 * K 线缓存（服务器真源 + localStorage 读加速；按虚拟下单/实盘隔离）。
 *
 * - 数据持久化在后端 kline_cache 表，全局共享、不跟账户/浏览器走；
 *   但按 **账户类型（demo/real）隔离** —— 虚拟下单与实盘行情品种不同
 *   （如实盘经纪商品种带 .E 后缀），各自独立一份，互不混用；
 * - 当前盘别由 AppShell 根据活动 MT5 会话调用 setKlineAccountType 同步；
 * - 浏览器 localStorage 只作读加速（按盘别分键）：命中直接用，未命中回源；
 * - 写入同时更新服务器与 localStorage（服务器失败时抛错，由调用方提示）；
 * - 缓存键独立于自选关系：把品种从自选删除后，已缓存数据依然保留。
 */

import { api } from '@/lib/api';

interface PriceBarLike {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume?: number;
  spread?: number;
}

/** 每个缓存条目最多保留多少根（与服务器端上限一致） */
const MAX_BARS = 20000;
/** localStorage 读加速条目上限（配额保护；服务器端才是全量） */
const LS_MAX_ENTRIES = 4;

export type KlineBar = PriceBarLike;
export type KlineAccountType = 'demo' | 'real';

/** 当前盘别（虚拟下单/实盘）——由 AppShell 按活动 MT5 会话同步；未知时默认虚拟下单 */
let klineAccountType: KlineAccountType = 'demo';

export function setKlineAccountType(t: string | null | undefined) {
  klineAccountType = t === 'real' ? 'real' : 'demo';
}

export function getKlineAccountType(): KlineAccountType {
  return klineAccountType;
}

const lsKey = () => `mt5quant.klines.v1.${klineAccountType}`;
const typeQ = () => `account_type=${klineAccountType}`;

type KlineStore = Record<string, PriceBarLike[]>;

function loadStore(): KlineStore {
  try {
    const raw = localStorage.getItem(lsKey());
    const obj = raw ? (JSON.parse(raw) as unknown) : null;
    if (!obj || typeof obj !== 'object') return {};
    const out: KlineStore = {};
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      if (Array.isArray(v)) out[k] = v as PriceBarLike[];
    }
    return out;
  } catch {
    return {};
  }
}

function persistStore(store: KlineStore) {
  // 逐次淘汰最旧条目直到写入成功（localStorage 配额保护）
  for (;;) {
    try {
      localStorage.setItem(lsKey(), JSON.stringify(store));
      return;
    } catch {
      const keys = Object.keys(store);
      if (keys.length === 0) return; // 只剩一条仍写不进（配额极小），放弃
      const oldest = keys.reduce((a, b) =>
        (store[a][store[a].length - 1]?.time ?? 0) <= (store[b][store[b].length - 1]?.time ?? 0)
          ? a
          : b,
      );
      delete store[oldest];
    }
  }
}

function lsSave(symbol: string, timeframe: string, bars: PriceBarLike[]) {
  const store = loadStore();
  const key = `${symbol}:${timeframe}`;
  const trimmed = [...bars].sort((a, b) => a.time - b.time).slice(-MAX_BARS);
  delete store[key];
  const entries = Object.entries(store);
  if (entries.length >= LS_MAX_ENTRIES) entries.splice(0, entries.length - LS_MAX_ENTRIES + 1);
  persistStore(Object.fromEntries([...entries, [key, trimmed] as const]));
}

export interface KlineCacheEntry {
  symbol: string;
  timeframe: string;
  account_type: string;
  bars: number;
  firstTs: number;
  lastTs: number;
  kb: number;
}

interface ServerEntry {
  symbol: string;
  timeframe: string;
  account_type: string;
  bars: number;
  firstTs: number;
  lastTs: number;
  kb: number;
}

/** 列出当前盘的全部缓存条目（服务器真源）。 */
export async function listKlineCache(): Promise<KlineCacheEntry[]> {
  const { data } = await api.get<ServerEntry[]>(`/kline-cache?${typeQ()}`);
  return (data ?? []).sort((a, b) => b.lastTs - a.lastTs);
}

/** 读取该条缓存的全部K线（localStorage 命中直接返回，否则回源服务器并回填）。 */
export async function loadKlines(symbol: string, timeframe: string): Promise<PriceBarLike[]> {
  const key = `${symbol}:${timeframe}`;
  const cached = loadStore()[key];
  if (cached && cached.length > 0) return cached;
  try {
    const { data } = await api.get<{ bars: PriceBarLike[] }>(
      `/kline-cache/${symbol}/${timeframe}?${typeQ()}`,
    );
    const bars = data?.bars ?? [];
    if (bars.length > 0) lsSave(symbol, timeframe, bars);
    return bars;
  } catch {
    return [];
  }
}

/** 保存（覆盖式，服务器 + localStorage）；bars 为空不写，避免把好缓存冲掉。 */
export async function saveKlines(symbol: string, timeframe: string, bars: PriceBarLike[]) {
  if (!Array.isArray(bars) || bars.length === 0) return;
  await api.put(`/kline-cache/${symbol}/${timeframe}?${typeQ()}`, { bars });
  lsSave(symbol, timeframe, bars);
}

/**
 * 增量合并：把从 MT5 新拉的一批K线并入缓存（当前盘）。
 * 保留旧批次中早于新批次起点的历史、以及晚于新批次终点（形成中）的K线，
 * 新批次覆盖重叠区间，尾部截断到 MAX_BARS。
 */
export async function mergeKlines(
  symbol: string,
  timeframe: string,
  incoming: PriceBarLike[],
): Promise<{ total: number; added: number }> {
  if (incoming.length === 0) {
    const cur = await loadKlines(symbol, timeframe);
    return { total: cur.length, added: 0 };
  }
  const existing = await loadKlines(symbol, timeframe);
  let merged: PriceBarLike[];
  if (existing.length === 0) {
    merged = [...incoming];
  } else {
    const firstNew = incoming[0].time;
    const lastNew = incoming[incoming.length - 1].time;
    const keptOld = existing.filter((b) => b.time < firstNew);
    const tailExtra = existing.filter((b) => b.time > lastNew);
    merged = [...keptOld, ...incoming, ...tailExtra];
  }
  merged.sort((a, b) => a.time - b.time);
  const dedup: PriceBarLike[] = [];
  for (const b of merged) {
    if (dedup.length === 0 || dedup[dedup.length - 1].time !== b.time) dedup.push(b);
  }
  const trimmed = dedup.slice(-MAX_BARS);
  const added = Math.max(0, trimmed.length - existing.length);
  await saveKlines(symbol, timeframe, trimmed);
  return { total: trimmed.length, added };
}

/** 删除单个条目（品种+周期，当前盘）。 */
export async function deleteKlineCache(symbol: string, timeframe: string) {
  await api.delete(`/kline-cache/${symbol}/${timeframe}?${typeQ()}`);
  const store = loadStore();
  delete store[`${symbol}:${timeframe}`];
  persistStore(store);
}

/** 清空当前盘的全部缓存（服务器 + 浏览器读加速）。 */
export async function clearKlineCache() {
  await api.delete(`/kline-cache?${typeQ()}`);
  try {
    localStorage.removeItem(lsKey());
  } catch {
    /* ignore */
  }
}
