import { lazy, type ReactNode } from 'react';
import { Navigate } from 'react-router-dom';

// Lazy load all page components
const Login = lazy(() => import('@/pages/Login'));
const Register = lazy(() => import('@/pages/Register'));
const Dashboard = lazy(() => import('@/pages/Dashboard'));
const MT5LoginPage = lazy(() => import('@/pages/MT5LoginPage'));
const MarketOverview = lazy(() => import('@/pages/MarketData/MarketOverview'));
const SymbolDetail = lazy(() => import('@/pages/MarketData/SymbolDetail'));
const DataManagement = lazy(() => import('@/pages/MarketData/DataManagement'));
const StrategyListPage = lazy(() => import('@/pages/Strategies/StrategyListPage'));
const StrategyEditorPage = lazy(() => import('@/pages/Strategies/StrategyEditorPage'));
const BuilderPage = lazy(() => import('@/pages/Strategies/BuilderPage'));
const MarketPage = lazy(() => import('@/pages/Marketplace/MarketPage'));
const MarketListingDetailPage = lazy(() => import('@/pages/Marketplace/MarketListingDetailPage'));
const BacktestListPage = lazy(() => import('@/pages/Backtest/BacktestListPage'));
const BacktestReportPage = lazy(() => import('@/pages/Backtest/BacktestReportPage'));
const PortfolioReportPage = lazy(() => import('@/pages/Backtest/PortfolioReportPage'));
const PaperDetailPage = lazy(() => import('@/pages/Paper/PaperDetailPage'));
const LiveListPage = lazy(() => import('@/pages/Live/LiveListPage'));
const LiveDetailPage = lazy(() => import('@/pages/Live/LiveDetailPage'));
const ManualTradePage = lazy(() => import('@/pages/Trading/ManualTradePage'));
const NotebookListPage = lazy(() => import('@/pages/Notebook/NotebookListPage'));
const NotebookDetailPage = lazy(() => import('@/pages/Notebook/NotebookDetailPage'));
const RiskPage = lazy(() => import('@/pages/Risk/RiskPage'));
const AuditPage = lazy(() => import('@/pages/Risk/AuditPage'));
const AlertsPage = lazy(() => import('@/pages/Alerts/AlertsPage'));
const GatewaySettingsPage = lazy(() => import('@/pages/Settings/GatewaySettingsPage'));
const SystemSettingsPage = lazy(() => import('@/pages/Settings/SystemSettingsPage'));

export interface RouteConfig {
  path: string;
  element: ReactNode;
  public?: boolean;
}

export const routeConfigs: RouteConfig[] = [
  // Public routes
  { path: '/login', element: <Login />, public: true },
  { path: '/register', element: <Register />, public: true },

  // Authenticated routes
  { path: '/dashboard', element: <Dashboard /> },
  { path: '/mt5-login', element: <MT5LoginPage /> },
  { path: '/market', element: <MarketOverview /> },
  { path: '/market/data', element: <DataManagement /> },
  { path: '/market/:symbolId', element: <SymbolDetail /> },
  { path: '/strategies', element: <StrategyListPage /> },
  { path: '/strategies/builder', element: <BuilderPage mode="create" /> },
  { path: '/strategies/:id/builder', element: <BuilderPage mode="edit" /> },
  { path: '/strategies/:id/edit', element: <StrategyEditorPage /> },
  { path: '/marketplace', element: <MarketPage /> },
  { path: '/marketplace/:listingId', element: <MarketListingDetailPage /> },
  { path: '/backtest', element: <BacktestListPage /> },
  { path: '/backtest/portfolio/:portfolioId', element: <PortfolioReportPage /> },
  { path: '/backtest/:taskId/report', element: <BacktestReportPage /> },
  { path: '/notebooks', element: <NotebookListPage /> },
  { path: '/notebooks/:id', element: <NotebookDetailPage /> },
  // 虚拟下单已合并到「回测中心」tab，独立入口重定向过去（兼容旧链接）
  { path: '/trading/paper', element: <Navigate to="/backtest" replace /> },
  { path: '/trading/paper/:runId', element: <PaperDetailPage /> },
  { path: '/trading/live-demo', element: <LiveListPage scope="demo" /> },
  { path: '/trading/live-demo/:runId', element: <LiveDetailPage /> },
  { path: '/trading/live', element: <LiveListPage scope="real" /> },
  { path: '/trading/live/:runId', element: <LiveDetailPage /> },
  { path: '/trading/manual', element: <ManualTradePage /> },
  { path: '/risk', element: <RiskPage /> },
  { path: '/risk/audit', element: <AuditPage /> },
  { path: '/alerts', element: <AlertsPage /> },
  { path: '/settings/gateway', element: <GatewaySettingsPage /> },
  { path: '/settings/system', element: <SystemSettingsPage /> },
];