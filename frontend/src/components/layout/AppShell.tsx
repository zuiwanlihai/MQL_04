import { useEffect } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import {
  BarChart3,
  Bell,
  Code2,
  FlaskConical,
  LayoutDashboard,
  LineChart,
  LogOut,
  NotebookPen,
  Repeat,
  Settings,
  ShieldAlert,
  Store,
  Wand2,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import { useLiveMeta } from '@/hooks/useLive';
import { useActiveAccount } from '@/hooks/useAccounts';
import { currentActiveType, navDisableReason } from '@/lib/mutex';
import { setKlineAccountType } from '@/lib/klineCache';
import { useAuthStore } from '@/store/useAuthStore';
import MT5AccountSwitcher from '@/components/account/MT5AccountSwitcher';
import AlgoTradingToggle from '@/components/account/AlgoTradingToggle';
import TopbarRunningAI from '@/components/layout/TopbarRunningAI';
import WhitelistButton from '@/components/layout/WhitelistButton';

interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  disabledReason?: string;
}

const BASE_NAV: NavItem[] = [
  { label: '总览', to: '/dashboard', icon: LayoutDashboard },
  { label: '行情', to: '/market', icon: LineChart },
  { label: '策略', to: '/strategies', icon: Code2 },
  { label: '策略构建', to: '/strategies/builder', icon: Wand2 },
  { label: '策略市场', to: '/marketplace', icon: Store },
  { label: '回测', to: '/backtest', icon: BarChart3 },
  { label: '研究笔记', to: '/notebooks', icon: NotebookPen },
  { label: '模拟盘交易', to: '/trading/live-demo', icon: FlaskConical },
];
// 虚拟下单已合并到「回测中心」tab（BacktestListPage 的 paper tab），侧栏独立入口移除。
// 路由 /trading/paper 保留（兼容直接访问），路由内部自动重定向到 /backtest?tab=paper。
const LIVE_NAV: NavItem = { label: '实盘', to: '/trading/live', icon: Zap };
// 手动交易独立入口已取消 — 面板分别嵌入虚拟下单、模拟盘交易、实盘、行情页面（ManualTradePanel）。

const TAIL_NAV: NavItem[] = [
  { label: '风控', to: '/risk', icon: ShieldAlert },
  { label: '警告', to: '/alerts', icon: Bell },
  { label: '设置', to: '/settings/system', icon: Settings },
];

export default function AppShell() {
  const navigate = useNavigate();
  const { user, logout } = useAuthStore();
  const liveMeta = useLiveMeta();
  const liveEnabled = liveMeta.data?.enabled ?? false;

  // 实盘/模拟互斥：按网关当前登录账户类型置灰侧栏入口
  const activeQ = useActiveAccount();
  const activeType = currentActiveType(activeQ.data);

  // K线缓存按虚拟下单/实盘隔离：跟随活动 MT5 会话切换缓存盘别
  useEffect(() => {
    setKlineAccountType(activeType);
  }, [activeType]);

  const NAV_ITEMS: NavItem[] = (liveEnabled
    ? [...BASE_NAV, LIVE_NAV, ...TAIL_NAV]
    : [...BASE_NAV, ...TAIL_NAV]
  ).map((item) => ({ ...item, disabledReason: navDisableReason(item.to, activeType) }));

  async function onLogout() {
    await logout();
    navigate('/login', { replace: true });
  }

  return (
    <div className="flex h-screen bg-surface-950 text-gray-200">
      {/* Sidebar */}
      <aside className="w-60 flex-shrink-0 bg-surface-900 border-r border-gray-700/50 flex flex-col">
        <div className="h-14 flex items-center px-4 border-b border-gray-700/50">
          <span className="text-sm font-semibold text-white tracking-wide">
            MT5 量化交易
          </span>
        </div>

        <nav className="flex-1 overflow-y-auto py-3 px-2 space-y-0.5">
          {NAV_ITEMS.map((item) =>
            item.disabledReason ? (
              <span
                key={item.to}
                title={item.disabledReason}
                className="flex items-center gap-3 rounded-md px-3 py-2 text-sm cursor-not-allowed opacity-40 select-none text-gray-400"
              >
                <item.icon className="w-4 h-4 flex-shrink-0" />
                {item.label}
              </span>
            ) : (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                    isActive
                      ? 'bg-surface-750 text-white'
                      : 'text-gray-400 hover:bg-surface-800 hover:text-gray-200'
                  }`
                }
              >
                <item.icon className="w-4 h-4 flex-shrink-0" />
                {item.label}
              </NavLink>
            ),
          )}
        </nav>

        <div className="p-3 border-t border-gray-700/50 space-y-1">
          <button
            onClick={() => navigate('/mt5-login')}
            className="w-full flex items-center gap-3 rounded-md px-3 py-2 text-sm text-gray-400 hover:bg-surface-800 hover:text-gray-200 transition-colors"
            title="切换登录 MT5 交易账户（实盘/模拟）"
          >
            <Repeat className="w-4 h-4 flex-shrink-0" />
            切换账户
          </button>
          <p className="text-2xs uppercase tracking-wider text-gray-600 px-3">
            认证切片 v0.1
          </p>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-14 flex items-center justify-between px-6 border-b border-gray-700/50 bg-surface-900/60">
          <div className="flex items-center gap-3">
            <p className="text-sm text-gray-500">总览</p>
            <TopbarRunningAI />
            <WhitelistButton />
          </div>
          <div className="flex-1 flex justify-center px-4">
            <AlgoTradingToggle />
          </div>
          <div className="flex items-center gap-4">
            <MT5AccountSwitcher />
            <span className="text-sm text-gray-400">
              {user?.email ?? '—'}
            </span>
            <button
              onClick={onLogout}
              className="btn-ghost"
              title="退出登录"
            >
              <LogOut className="w-4 h-4" />
              退出登录
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
