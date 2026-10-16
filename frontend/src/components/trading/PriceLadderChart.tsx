/**
 * PriceLadderChart — 价格网格梯（权益图表右半屏）。
 *
 * 纵轴为价格，横向画出：
 * - 现价线（实时 bid，蓝色）
 * - 下一步买入线（现价下方最近的未持仓买位，绿色加粗）
 * - 下一步卖出线（现价上方最近的未持仓卖位，红色加粗）
 * - 网格线（双向：锚定 − k·step 买位 / 锚定 + k·step 卖位；
 *   已持仓=绿色实线，未开仓=灰色虚线）
 *
 * 网格信息来自 detail.strategy_state.anchor + parameters.grid_step/max_levels；
 * 非网格策略自动降级为「现价 + 持仓入场价」。
 */

interface LadderLine {
  price: number;
  top: number; // percent
  kind:
    | 'current'
    | 'next-open'
    | 'next-open-sell'
    | 'entry'
    | 'grid-occupied'
    | 'grid-free';
  label: string;
  level?: number;
}

const KIND_STYLES: Record<LadderLine['kind'], { line: string; chip: string }> = {
  current: { line: 'border-t-2 border-blue-400', chip: 'bg-blue-500/20 text-blue-300 border-blue-400/50' },
  'next-open': { line: 'border-t-2 border-profit', chip: 'bg-profit/20 text-profit border-profit/60 font-medium' },
  'next-open-sell': { line: 'border-t-2 border-loss', chip: 'bg-loss/20 text-loss border-loss/60 font-medium' },
  entry: { line: 'border-t border-dashed text-profit/70 border-current', chip: 'bg-surface-700 text-gray-300 border-gray-600' },
  'grid-occupied': { line: 'border-t border-profit', chip: 'bg-profit text-white border-profit' },
  'grid-free': { line: 'border-t border-dashed border-gray-600', chip: 'bg-surface-800/80 text-gray-500 border-gray-700' },
};

const Z_BY_KIND: Record<LadderLine['kind'], number> = {
  'grid-free': 10,
  'grid-occupied': 11,
  entry: 13,
  'next-open': 20,
  'next-open-sell': 20,
  current: 30,
};

export function PriceLadderChart({
  height = 240,
  digits,
  currentPrice,
  anchor,
  stepPrice,
  maxLevels,
  openPrices,
  sellOpenPrices = [],
  history,
}: {
  height?: number;
  digits: number;
  currentPrice: number | null;
  anchor: number | null;
  stepPrice: number;
  maxLevels: number;
  openPrices: number[];
  /** 空头持仓入场价（双向网格：锚定线上方卖出开空） */
  sellOpenPrices?: number[];
  /** 价格走势（策略启动至今的收盘价序列，时间升序），叠加在网格线之下 */
  history?: { time: number; price: number }[];
}) {
  const hist = history ?? [];
  const hasGrid = anchor != null && stepPrice > 0 && maxLevels > 0;

  // 网格位（双向）：买位 = 锚定 − k·step（降序），卖位 = 锚定 + k·step（升序）
  const gridLevels: { level: number; price: number; side: 'buy' | 'sell' }[] = hasGrid
    ? [
        ...Array.from({ length: maxLevels }, (_, i) => ({
          level: i + 1,
          price: (anchor as number) - (i + 1) * stepPrice,
          side: 'buy' as const,
        })),
        ...Array.from({ length: maxLevels }, (_, i) => ({
          level: i + 1,
          price: (anchor as number) + (i + 1) * stepPrice,
          side: 'sell' as const,
        })),
      ]
    : [];
  // 占用判定容差：买单按 ask 成交会略高于触发线（一个点差量级），放宽到 step/5
  const tol = stepPrice / 5;
  const isOccupied = (g: { price: number; side: 'buy' | 'sell' }) =>
    (g.side === 'buy' ? openPrices : sellOpenPrices).some((p) => Math.abs(p - g.price) < tol);

  // 下一步买入 = 现价以下最高的未持仓买位（全在上方时取最接近锚定价者）
  let nextOpen: number | null = null;
  if (hasGrid && openPrices.length < maxLevels) {
    const freeBuy = gridLevels
      .filter((g) => g.side === 'buy' && !isOccupied(g))
      .map((g) => g.price); // 降序
    if (freeBuy.length) {
      const below = currentPrice != null ? freeBuy.filter((p) => p < currentPrice) : [];
      nextOpen = (below.length ? below : freeBuy)[0];
    }
  }
  // 下一步卖出 = 现价上方最低的未持仓卖位（全在下方时取最接近锚定价者）
  let nextOpenSell: number | null = null;
  if (hasGrid && sellOpenPrices.length < maxLevels) {
    const freeSell = gridLevels
      .filter((g) => g.side === 'sell' && !isOccupied(g))
      .map((g) => g.price); // 升序
    if (freeSell.length) {
      const above = currentPrice != null ? freeSell.filter((p) => p > currentPrice) : [];
      nextOpenSell = (above.length ? above : freeSell)[0];
    }
  }
  const all = [
    currentPrice,
    ...openPrices,
    ...sellOpenPrices,
    ...gridLevels.map((g) => g.price),
    ...hist.map((h) => h.price),
  ].filter((v): v is number => v != null && Number.isFinite(v));

  if (all.length === 0 && currentPrice == null) {
    return (
      <div
        className="w-full flex items-center justify-center rounded bg-surface-800/30 border border-gray-800/60"
        style={{ height }}
      >
        <p className="text-sm text-gray-500">暂无价格数据（等待行情推送）</p>
      </div>
    );
  }

  let lo = Math.min(...(all.length ? all : [currentPrice as number]));
  let hi = Math.max(...(all.length ? all : [currentPrice as number]));
  const span = hi - lo;
  const pad = span > 0 ? span * 0.12 : Math.abs(hi || 1) * 0.002 || 1;
  lo -= pad;
  hi += pad;
  const yOf = (p: number) => Math.min(99, Math.max(1, ((hi - p) / (hi - lo)) * 100));

  const lines: LadderLine[] = [];
  if (currentPrice != null) {
    lines.push({ price: currentPrice, top: yOf(currentPrice), kind: 'current', label: '现价' });
  }
  if (nextOpen != null) {
    lines.push({ price: nextOpen, top: yOf(nextOpen), kind: 'next-open', label: '下一步买入' });
  }
  if (nextOpenSell != null) {
    lines.push({
      price: nextOpenSell,
      top: yOf(nextOpenSell),
      kind: 'next-open-sell',
      label: '下一步卖出',
    });
  }
  for (const g of gridLevels) {
    const occ = isOccupied(g);
    lines.push({
      price: g.price,
      top: yOf(g.price),
      kind: occ ? 'grid-occupied' : 'grid-free',
      label: occ ? `已持仓 L${g.level}` : g.side === 'buy' ? `买 L${g.level}` : `卖 L${g.level}`,
      level: g.level,
    });
  }
  // 非网格策略：入场价参考线（网格模式下已由「已持仓 Lx」表达，不重复画）
  if (!hasGrid) {
    for (const p of openPrices) {
      lines.push({ price: p, top: yOf(p), kind: 'entry', label: '入场价' });
    }
  }
  lines.sort((a, b) => Z_BY_KIND[a.kind] - Z_BY_KIND[b.kind]);

  const fmt = (v: number) => v.toFixed(digits);

  return (
    <div className="w-full">
      {/* legend */}
      <div className="flex items-center gap-3 flex-wrap text-2xs text-gray-500 mb-1">
        {hist.length > 1 && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 border-t-2 border-sky-400" /> 价格走势
          </span>
        )}
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 border-t-2 border-blue-400" /> 现价
        </span>
        {nextOpen != null && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 border-t-2 border-profit" /> 下一步买入
          </span>
        )}
        {nextOpenSell != null && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 border-t-2 border-loss" /> 下一步卖出
          </span>
        )}
        {hasGrid && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 border-t border-dashed border-gray-500" /> 网格
          </span>
        )}
      </div>
      <div
        className="relative w-full rounded bg-surface-800/30 border border-gray-800/60 overflow-hidden"
        style={{ height }}
      >
        {/* 价格走势折线（启动至今，与网格共用同一价格轴） */}
        {hist.length > 1 && (
          <svg
            className="absolute inset-0 w-full h-full"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            style={{ zIndex: 5 }}
          >
            <polygon
              points={`0,100 ${hist
                .map((h, i) => `${(i / (hist.length - 1)) * 100},${yOf(h.price)}`)
                .join(' ')} 100,100`}
              fill="rgba(56,189,248,0.07)"
            />
            <polyline
              points={hist
                .map((h, i) => `${(i / (hist.length - 1)) * 100},${yOf(h.price)}`)
                .join(' ')}
              fill="none"
              stroke="#38bdf8"
              strokeWidth="1.5"
              strokeOpacity="0.85"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        )}
        {hist.length > 0 && (
          <div
            className="absolute right-1 w-1.5 h-1.5 rounded-full bg-sky-300 shadow-[0_0_6px_rgba(56,189,248,0.9)]"
            style={{ top: `${yOf(hist[hist.length - 1].price)}%`, transform: 'translateY(-50%)', zIndex: 6 }}
          />
        )}
        {lines.map((ln, i) => {
          const st = KIND_STYLES[ln.kind];
          return (
            <div
              key={`${ln.kind}-${i}`}
              className="absolute left-0 right-0"
              style={{ top: `${ln.top}%`, transform: 'translateY(-50%)', zIndex: Z_BY_KIND[ln.kind] }}
            >
              <div className={`${st.line} w-full`} />
              <div className="absolute left-2 -top-2.5">
                <span className={`px-1.5 py-0.5 rounded border text-2xs leading-none ${st.chip}`}>
                  {ln.label}
                </span>
              </div>
              <div className="absolute right-2 -top-2.5">
                <span
                  className={`px-1.5 py-0.5 rounded border text-2xs leading-none font-mono ${st.chip} ${
                    ln.kind === 'current' ? 'bg-blue-500/30' : 'bg-surface-900/80'
                  }`}
                >
                  {fmt(ln.price)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
