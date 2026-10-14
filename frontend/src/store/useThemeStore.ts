import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * 交易外观偏好（PRD §4.3：红涨绿跌可配置，国内用户默认红涨绿跌）。
 *
 * 仅控制「涨跌方向色」（行情涨跌幅 / K 线蜡烛），不触碰「盈亏语义色」
 * （账户浮动盈亏的赚绿亏红是语义，与涨跌惯例无关）。持久化到 localStorage。
 */

interface ThemeState {
  /** true = 红涨绿跌（国内惯例，默认）；false = 绿涨红跌（国际惯例）。 */
  redUpGreenDown: boolean;
  setRedUpGreenDown: (v: boolean) => void;
  toggle: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      redUpGreenDown: true,
      setRedUpGreenDown: (v) => set({ redUpGreenDown: v }),
      toggle: () => set({ redUpGreenDown: !get().redUpGreenDown }),
    }),
    { name: 'mt5quant.theme' },
  ),
);

/** 涨色 / 跌色的十六进制值（供 lightweight-charts 等需要裸色值的场景）。 */
export function upDownColors(redUpGreenDown: boolean): { up: string; down: string } {
  return redUpGreenDown
    ? { up: '#ef4444', down: '#22c55e' } // 红涨绿跌
    : { up: '#22c55e', down: '#ef4444' }; // 绿涨红跌
}
