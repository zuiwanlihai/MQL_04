/**
 * GridLevelTable — 网格格位表（横向两行）。
 *
 * 上半表（AI 名称 / 买位）：从右向左排 L1..Lmax（价格升序 ↓）
 * 下半表（AI 名称 / 卖位）：从左向右排 R1..Rmax（价格升序 ↑）
 *
 * 每格显示：层级 + 价格 + 占用状态 + AI/策略实例名称（可选）。
 * 与 PriceLadderChart 共用 lib/gridLevels.ts 计算，避免错位。
 */

import { buildGridLevels } from '../../lib/gridLevels';

interface Props {
  anchor: number | null;
  stepPrice: number;
  maxLevels: number;
  openPrices: number[];
  sellOpenPrices?: number[];
  digits: number;
  /** AI/策略实例名称映射：key = "buy:L3" / "sell:R5" */
  aiNames?: Partial<Record<string, string>>;
}

const BG_FREE = 'bg-surface-800 text-gray-500 border-gray-700';
const BG_OCC  = 'bg-profit text-white border-profit';

export function GridLevelTable({
  anchor, stepPrice, maxLevels, openPrices, sellOpenPrices = [],
  digits, aiNames = {},
}: Props) {
  const all = buildGridLevels({ anchor, stepPrice, maxLevels, openPrices, sellOpenPrices });
  if (all.length === 0) {
    return (
      <div className="rounded bg-surface-800/30 border border-gray-800/60 p-6 text-center text-gray-500 text-sm">
        暂无网格数据（非网格策略或参数未配置）
      </div>
    );
  }

  const buyLevels  = all.filter(g => g.side === 'buy').sort((a, b) => b.level - a.level); // Lmax → L1（价高 → 价低）
  const sellLevels = all.filter(g => g.side === 'sell').sort((a, b) => a.level - b.level); // R1 → Rmax（价低 → 价高）

  const fmt = (v: number) => v.toFixed(digits);

  const renderRow = (side: 'buy' | 'sell', levels: typeof all) => (
    <div className="flex items-center gap-1">
      <span className="w-10 shrink-0 text-2xs text-gray-500 text-right pr-1">
        {side === 'buy' ? '买' : '卖'}
      </span>
      <div className={`flex gap-1 flex-wrap ${side === 'buy' ? 'justify-start' : 'justify-start'}`}>
        {levels.map((g) => {
          const key = `${side}:${side === 'buy' ? 'L' : 'R'}${g.level}`;
          const aiName = aiNames[key] ?? '';
          const cls = g.occupied ? BG_OCC : BG_FREE;
          return (
            <div
              key={key}
              className={`min-w-[92px] rounded border px-2 py-1.5 text-center ${cls}`}
            >
              <div className="flex items-center justify-between text-2xs">
                <span className="font-medium">
                  {side === 'buy' ? 'L' : 'R'}{g.level}
                </span>
                {g.occupied && (
                  <span className="w-1.5 h-1.5 rounded-full bg-profit/80" title="已持仓" />
                )}
              </div>
              <div className="font-mono text-[11px] leading-tight mt-0.5">
                {fmt(g.price)}
              </div>
              {aiName && (
                <div className="truncate text-[10px] text-sky-400/80 mt-0.5 max-w-[84px]" title={aiName}>
                  {aiName}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );

  return (
    <div className="space-y-2 p-3 rounded bg-surface-800/30 border border-gray-800/60">
      {/* 卖位表 */}
      {renderRow('sell', sellLevels)}
      {/* 分隔：锚定参考线 */}
      {anchor != null && (
        <div className="flex items-center gap-2 text-2xs text-gray-500">
          <span className="w-10 shrink-0 text-right pr-1">锚定</span>
          <span className="font-mono text-gray-400">--- {fmt(anchor)} ---</span>
        </div>
      )}
      {/* 买位表 */}
      {renderRow('buy', buyLevels)}
    </div>
  );
}
