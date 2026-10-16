/**
 * shared grid-level calculation for PriceLadderChart + GridLevelTable.
 *
 * 两处组件（网格梯图 / 格位表）必须基于同一份网格位计算，
 * 避免各自算一遍出现层级错位 / 占用判定不一致。
 */

export interface GridLevel {
  level: number;
  price: number;
  /** 'buy' = anchor − k·step (下方买位)；'sell' = anchor + k·step (上方卖位) */
  side: 'buy' | 'sell';
  occupied: boolean;
}

export function buildGridLevels(params: {
  anchor: number | null;
  stepPrice: number;
  maxLevels: number;
  openPrices: number[];
  sellOpenPrices?: number[];
}): GridLevel[] {
  const { anchor, stepPrice, maxLevels, openPrices, sellOpenPrices = [] } = params;
  if (anchor == null || stepPrice <= 0 || maxLevels <= 0) return [];

  const tol = stepPrice / 5; // 成交价略偏离触发线的容差（ask 买入会高于触发线）

  const all: GridLevel[] = [];
  for (let i = 0; i < maxLevels; i++) {
    const level = i + 1;
    all.push({
      level,
      price: anchor - level * stepPrice,
      side: 'buy',
      occupied: false,
    });
    all.push({
      level,
      price: anchor + level * stepPrice,
      side: 'sell',
      occupied: false,
    });
  }
  // 占用判定：直接算价格差（和 PriceLadderChart.isOccupied 逻辑完全一致）
  for (const g of all) {
    const prices = g.side === 'buy' ? openPrices : sellOpenPrices;
    g.occupied = prices.some((p) => Math.abs(p - g.price) < tol);
  }
  return all;
}
