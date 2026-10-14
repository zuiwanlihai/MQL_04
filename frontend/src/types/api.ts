import type { BuilderConfig } from '@/lib/builder/types';

// ── Authentication ──

export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string;
  role: 'admin' | 'trader' | 'analyst' | 'viewer';
  is_active: boolean;
  totp_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface Session {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface LoginRequest {
  email: string;
  password: string;
}

/** Returned by /auth/login when the account has 2FA enabled. */
export interface TwoFAChallenge {
  requires_2fa: true;
  pre_auth_token: string;
  ttl_seconds: number;
}

export type LoginResult = Session | TwoFAChallenge;

export interface TwoFASetupOut {
  secret: string;
  otpauth_uri: string;
  qr_svg_base64: string;
}

export interface TwoFAEnableOut {
  recovery_codes: string[];
}

export interface TwoFAStatusOut {
  enabled: boolean;
  recovery_codes_remaining: number;
}

export interface RegisterRequest {
  email: string;
  username: string;
  password: string;
  full_name: string;
}

// ── MT5 Account ──

export interface MT5Account {
  id: string;
  login: number;
  server: string;
  name: string;
  account_type: 'demo' | 'real' | 'contest';
  leverage: number;
  currency: string;
  balance: number;
  credit: number;
  equity: number;
  margin: number;
  margin_free: number;
  margin_level: number;
  is_connected: boolean;
  is_trade_allowed: boolean;
  trade_frozen: boolean;
  live_allowed: boolean;
  /** 允许算法交易：false 时该账户的策略自动下单被后端前置闸拒绝 */
  algo_trading_enabled: boolean;
  gateway_node_id: string;
  /** 绑定时选择了「记住密码」→ 存储加密凭据，可一键切换登录 */
  has_credentials: boolean;
  last_sync_at: string;
  created_at: string;
  updated_at: string;
}


// ── Gateway ──

export type GatewayNodeStatus = 'online' | 'offline' | 'degraded' | 'starting';
export type GatewayNodeType = 'python' | 'ea_bridge';

export interface GatewayNode {
  id: string;
  name: string;
  type: GatewayNodeType;
  host: string;
  port: number;
  status: GatewayNodeStatus;
  version: string;
  uptime_seconds: number;
  active_sessions: number;
  cpu_percent: number;
  memory_percent: number;
  last_heartbeat: string;
  is_primary: boolean;
  metadata: Record<string, string>;
  created_at: string;
}

export interface GatewayHealthLog {
  id: string;
  node_id: string;
  status: GatewayNodeStatus;
  latency_ms: number;
  error_rate: number;
  message: string | null;
  recorded_at: string;
}

export interface GatewayNodeListResponse {
  items: GatewayNode[];
  total: number;
}

export interface HealthLogListResponse {
  items: GatewayHealthLog[];
  total: number;
}

// ── Account binding (M1) ──

export interface AccountTestRequest {
  gateway_url?: string;
  gateway_node_id?: string;
  login: number;
  password: string;
  server: string;
}

/** 绑定：remember_password=false 时仅本次登录用密码，不落库（前端切换需重输） */
export interface AccountBindRequest extends AccountTestRequest {
  remember_password?: boolean;
}

/** 激活（切换登录）：未记住密码的账户必须携带本次密码（不落库） */
export interface ActivateAccountRequest {
  password?: string;
}

export interface AccountProbeResult {
  login: number;
  server: string;
  name: string;
  account_type: 'demo' | 'real' | 'contest';
  leverage: number;
  currency: string;
  balance: number;
  credit: number;
  equity: number;
  margin: number;
  margin_free: number;
  margin_level: number;
  is_trade_allowed: boolean;
}

export interface AccountListResponse {
  items: MT5Account[];
  total: number;
}

/** MT5 终端本体「算法交易」按钮（AutoTrading）同步结果。 */
export interface TerminalSyncInfo {
  /** 终端本体「算法交易」按钮是否已同步到目标状态 */
  ok: boolean;
  trade_allowed: boolean | null;
  message: string | null;
}

export interface AlgoTradingToggleResult {
  account: MT5Account;
  terminal_sync: TerminalSyncInfo;
}

export interface ActiveAccountInfo {
  node_id: string;
  node_name: string;
  account: AccountProbeResult | null;
}

export interface ActiveAccountListResponse {
  items: ActiveAccountInfo[];
}

/** 网关交易白名单状态（灰度放行的 MT5 登录号列表）。 */
export interface TradeWhitelistInfo {
  trade_enabled: boolean;
  logins: number[];
}

// ── Symbols ──

export type MarketTimeframe = 'M1' | 'M5' | 'M15' | 'M30' | 'H1' | 'H4' | 'D1';

export type SymbolGroup = 'forex' | 'indices' | 'commodities' | 'crypto' | 'stocks';

export interface Symbol {
  id: string;
  name: string;
  description: string;
  group: SymbolGroup;
  base_currency: string;
  quote_currency: string;
  digits: number;
  point: number;
  spread: number;
  tick_size: number;
  tick_value: number;
  contract_size: number;
  min_volume: number;
  max_volume: number;
  volume_step: number;
  swap_long: number;
  swap_short: number;
  margin_percent: number;
  is_trade_allowed: boolean;
  is_active: boolean;
  session_open: string;
  session_close: string;
}

export interface PriceBar {
  time: number; // unix timestamp in seconds
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume?: number;
  real_volume?: number;
  spread?: number;
}

export interface Quote {
  symbol_id: string;
  symbol_name: string;
  bid: number;
  ask: number;
  last: number;
  change: number;
  change_percent: number;
  spread: number;
  time: string;
  /** 与 K 线同轴（UTC 伪 epoch）的 tick 时间；缺失/为 null 时回退 time */
  bar_time?: string | null;
  high_day: number;
  low_day: number;
  volume: number;
}

export interface SymbolListResponse {
  items: Symbol[];
  total: number;
}

export interface QuoteListResponse {
  items: Quote[];
  total: number;
}

// ── Data quality check (数据中心 P1) ──

export type QualitySeverity = 'info' | 'warning' | 'error';

export interface QualityIssue {
  severity: QualitySeverity;
  code: string;
  message: string;
  start_time: number | null;
  end_time: number | null;
}

export interface DataQualityReport {
  symbol: string;
  timeframe: MarketTimeframe;
  start: number;
  end: number;
  bars_analyzed: number;
  quality_score: number;
  verdict: 'ok' | 'warn' | 'poor';
  gap_count: number;
  weekend_gap_count: number;
  invalid_count: number;
  spike_count: number;
  duplicate_count: number;
  zero_volume_count: number;
  issues: QualityIssue[];
  truncated: boolean;
}

// ── Strategy ──

export type StrategyStatus = 'draft' | 'active' | 'paused' | 'archived';
export type StrategyLanguage = 'mql5' | 'python';
export type StrategyDirection = 'long_only' | 'short_only' | 'both';

export interface Strategy {
  id: string;
  name: string;
  description: string;
  language: StrategyLanguage;
  direction: StrategyDirection;
  status: StrategyStatus;
  current_version: number | null;
  symbol_ids: string[];
  timeframe: string;
  tags: string[];
  parameters: Record<string, unknown> | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface StrategyVersion {
  id: string;
  strategy_id: string;
  version: number;
  source_code: string;
  compiled_at: string | null;
  is_compiled: boolean;
  compile_errors: string[] | null;
  changelog: string;
  created_by: string | null;
  created_at: string;
}

export interface StrategyTemplate {
  id: string;
  name: string;
  description: string;
  language: StrategyLanguage;
  category: string;
  source_code: string;
  parameters: Record<string, string>;
}

export interface StrategyTemplateSummary {
  id: string;
  name: string;
  description: string;
  language: StrategyLanguage;
  category: string;
  parameters: Record<string, string>;
}

export interface StrategyDetail extends Strategy {
  source_code: string;
  builder_config: BuilderConfig | null;
}

export interface StrategyCreate {
  name: string;
  description?: string;
  language?: StrategyLanguage;
  direction?: StrategyDirection;
  symbol_ids?: string[];
  timeframe?: string;
  tags?: string[];
  template_id?: string | null;
  source_code?: string | null;
  builder_config?: BuilderConfig | null;
  parameters?: Record<string, unknown> | null;
}

export interface StrategyUpdate {
  name?: string;
  description?: string;
  direction?: StrategyDirection;
  status?: StrategyStatus;
  symbol_ids?: string[];
  timeframe?: string;
  tags?: string[];
  builder_config?: BuilderConfig | null;
  parameters?: Record<string, unknown> | null;
}

export interface VersionCreate {
  changelog?: string;
}

export interface StrategyListResponse {
  items: Strategy[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface TemplateListResponse {
  items: StrategyTemplateSummary[];
  total: number;
}

export interface ValidationIssue {
  code: string;
  severity: 'error' | 'warning' | 'info';
  line: number;
  column: number;
  message: string;
}

export interface ValidationResult {
  ok: boolean;
  issues: ValidationIssue[];
}

export interface StrategySourceOut {
  source_code: string;
  updated_at: string;
  validation: ValidationResult;
}

export interface VersionListResponse {
  items: StrategyVersion[];
  total: number;
}

// ── Strategy marketplace (PRD Phase 3) ──

export interface MarketListing {
  id: string;
  strategy_id: string | null;
  author_id: string;
  author_username: string;
  strategy_name: string;
  description: string;
  language: StrategyLanguage;
  direction: StrategyDirection;
  symbol_ids: string[];
  timeframe: string;
  tags: string[];
  import_count: number;
  created_at: string;
}

export interface MarketListingDetail extends MarketListing {
  source_code: string;
}

export interface MarketListingListResponse {
  items: MarketListing[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PublishListingPayload {
  strategy_id: string;
  disclaimer_ack: boolean;
}

export interface ListingQuery {
  page: number;
  pageSize: number;
  symbol?: string;
  timeframe?: string;
  q?: string;
  sort?: 'latest' | 'popular';
}

// ── Backtest (M4) ──

export type BacktestStatus =
  | 'pending'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled';
export type ExecutionMode = 'conservative' | 'standard' | 'optimistic';
export type SpreadMode = 'current' | 'fixed';

export interface BacktestTask {
  id: string;
  strategy_id: string;
  strategy_version: number | null;
  status: BacktestStatus;
  execution_mode: ExecutionMode;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  initial_deposit: number;
  leverage: number;
  spread_mode: SpreadMode;
  spread_points: number | null;
  commission_per_lot: number;
  slippage_points: number | null;
  warmup_bars: number;
  parameters: Record<string, unknown>;
  group_id: string | null;
  progress_percent: number;
  error_code: string | null;
  error_message: string | null;
  timed_out: boolean;
  rating: number | null;
  data_source: string;
  started_at: string | null;
  completed_at: string | null;
  created_by: string | null;
  created_at: string;
}

export interface BacktestTaskDetail extends BacktestTask {
  config_snapshot: Record<string, unknown>;
}

export interface LocalBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume?: number;
  spread?: number;
}

export interface BacktestCreate {
  strategy_id: string;
  version?: number | null;
  symbol: string;
  timeframe: string;
  start_date: string;
  end_date: string;
  data_source?: 'server' | 'local';
  local_bars?: LocalBar[] | null;
  initial_deposit: number;
  leverage: number;
  execution_mode: ExecutionMode;
  spread_mode: SpreadMode;
  spread_points?: number | null;
  commission_per_lot: number;
  slippage_points?: number | null;
  warmup_bars: number;
  parameters: Record<string, unknown>;
  assumption_ack: boolean;
  sensitivity: boolean;
}

export interface BacktestListResponse {
  items: BacktestTask[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface EquityPoint {
  time: string;
  equity: number;
  balance: number;
  drawdown: number;
  baseline: number;
}

export interface BacktestReport {
  id: string;
  task_id: string;
  total_return: number;
  total_return_percent: number;
  annual_return_percent: number;
  annual_volatility_percent: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  recovery_factor: number;
  max_drawdown_percent: number;
  max_drawdown_amount: number;
  win_rate: number;
  profit_factor: number;
  expectancy: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  avg_win: number;
  avg_loss: number;
  avg_hold_hours: number;
  consecutive_wins: number;
  consecutive_losses: number;
  total_swap: number;
  equity_curve: EquityPoint[];
  monthly_returns: Record<string, number>;
  generated_at: string;
}

export interface BacktestReportResponse {
  task: BacktestTaskDetail;
  report: BacktestReport;
}

export interface BacktestTrade {
  id: string;
  task_id: string;
  ticket: number;
  direction: 'buy' | 'sell';
  volume: number;
  open_time: string;
  open_price: number;
  base_price: number;
  close_time: string | null;
  close_price: number | null;
  profit: number;
  profit_percent: number;
  swap: number;
  commission: number;
  spread_cost: number;
  slippage_cost: number;
  comment: string;
  is_forced: boolean;
}

export interface TradeListResponse {
  items: BacktestTrade[];
  total: number;
}

export interface BacktestLog {
  id: string;
  task_id: string;
  seq: number;
  bar_time: string | null;
  kind: 'log' | 'signal' | 'system';
  level: string;
  message: string;
  payload: Record<string, unknown> | null;
}

export interface LogListResponse {
  items: BacktestLog[];
  total: number;
  truncated: boolean;
}

export interface SensitivitySide {
  task_id: string;
  execution_mode: ExecutionMode;
  status: BacktestStatus;
  annual_return_percent: number | null;
  max_drawdown_percent: number | null;
  sharpe_ratio: number | null;
  total_return_percent: number | null;
}

export interface SensitivityCompare {
  group_id: string;
  conservative: SensitivitySide;
  standard: SensitivitySide;
  annual_diff_percent: number | null;
  sensitive: boolean;
}

// ── Portfolio backtest (multi-symbol, PRD Phase 3 P2) ──

export type PortfolioStatus = 'running' | 'completed' | 'failed';

export interface PortfolioCreate {
  strategy_id: string;
  symbols: string[];
  timeframe: string;
  start_date: string;
  end_date: string;
  total_deposit: number;
  leverage: number;
  execution_mode: ExecutionMode;
  spread_mode: SpreadMode;
  spread_points?: number | null;
  commission_per_lot: number;
  slippage_points?: number | null;
  warmup_bars: number;
  assumption_ack: boolean;
}

export interface PortfolioSymbolRow {
  symbol: string;
  task_id: string;
  deposit: number | null;
  total_return: number;
  total_return_percent: number;
  max_drawdown_percent: number;
  sharpe_ratio: number;
  total_trades: number;
  win_rate: number;
  profit_factor: number;
  total_swap: number;
}

export interface PortfolioMetrics {
  total_return: number;
  total_return_percent: number;
  annual_return_percent: number;
  max_drawdown_percent: number;
  max_drawdown_amount: number;
  sharpe_ratio: number;
}

export interface PortfolioResult {
  metrics: PortfolioMetrics;
  equity_curve: EquityPoint[];
  symbols: PortfolioSymbolRow[];
}

export interface PortfolioBacktest {
  id: string;
  strategy_id: string;
  strategy_name: string;
  symbols: string[];
  timeframe: string;
  total_deposit: number;
  status: PortfolioStatus;
  params: Record<string, unknown>;
  result: PortfolioResult | null;
  error: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface PortfolioListResponse {
  items: PortfolioBacktest[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// ── Overfit-guard analyses (M-WF/MC, PRD Phase 2) ──

export type AnalysisKind = 'walk_forward' | 'monte_carlo' | 'optimization';
export type AnalysisStatus = 'running' | 'completed' | 'failed';
export type MCMethod = 'bootstrap' | 'shuffle';

export interface WalkForwardCreate {
  folds: number;
  mode: 'rolling' | 'anchored';
}

export interface MonteCarloCreate {
  simulations: number;
  method: MCMethod;
  ruin_dd_percent: number;
  seed?: number | null;
}

export interface MCPercentiles {
  p5: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
  mean: number;
}

export interface MCResult {
  method: MCMethod;
  simulations: number;
  ruin_dd_percent: number;
  seed: number;
  original: { final_return_percent: number; max_dd_percent: number; trades: number };
  final_return_percent: MCPercentiles;
  max_dd_percent: MCPercentiles;
  prob_loss: number;
  prob_ruin: number;
}

export interface WFChildWindow {
  role: 'is' | 'oos';
  fold: number;
  task_id: string;
  start: string;
  end: string;
}

export interface WFResultRow {
  fold: number;
  is_task_id: string;
  oos_task_id: string;
  is_return_percent: number;
  oos_return_percent: number;
  is_max_dd_percent: number;
  oos_max_dd_percent: number;
  is_sharpe: number;
  oos_sharpe: number;
  oos_trades: number;
  efficiency: number | null;
}

export interface WFResult {
  mode: 'rolling' | 'anchored';
  folds: number;
  rows: WFResultRow[];
  summary: {
    oos_profitable: number;
    oos_median_return_percent: number | null;
    oos_mean_return_percent: number | null;
    wf_efficiency_mean: number | null;
    robust: boolean;
  };
}

export interface BacktestAnalysis {
  id: string;
  task_id: string;
  kind: AnalysisKind;
  status: AnalysisStatus;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  updated_at: string | null;
}

// ── Parameter optimization (grid / random, PRD Phase 2 P1) ──

export interface OptimizationParamSpace {
  name: string;
  values: (number | string | boolean)[];
}

export type OptimizationObjective =
  | 'sharpe_ratio'
  | 'annual_return_percent'
  | 'total_return_percent'
  | 'calmar_ratio'
  | 'profit_factor';

export interface OptimizationCreate {
  space: OptimizationParamSpace[];
  method: 'grid' | 'random';
  /** 随机采样次数；method=grid 时可省略（后端默认值不参与）。 */
  samples?: number;
  objective: OptimizationObjective;
  seed?: number | null;
}

export interface OptimizationResultRow {
  index: number;
  task_id: string;
  params: Record<string, number | string | boolean>;
  objective: number | null;
  total_return_percent: number | null;
  max_drawdown_percent: number | null;
  sharpe_ratio: number | null;
  total_trades: number | null;
}

export interface OptimizationResult {
  method: 'grid' | 'random';
  objective: OptimizationObjective;
  rows: OptimizationResultRow[];
  best: OptimizationResultRow | null;
}

export interface AnalysisListResponse {
  items: BacktestAnalysis[];
  total: number;
}

// ── Trading ──

export type InstanceStatus = 'running' | 'stopped' | 'error' | 'starting';

// ── Paper trading (M5) ──

export type PaperRunStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'stopping'
  | 'stopped'
  | 'failed';
export type PaperExecutionMode = 'standard' | 'optimistic';

export interface PaperPosition {
  id: string;
  symbol: string;
  direction: 'buy' | 'sell';
  volume: number;
  open_price: number;
  current_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  swap: number;
  commission: number;
  opened_at: string | null;
}

export interface PaperRun {
  id: string;
  strategy_id: string;
  account_id: string | null;
  strategy_version: number | null;
  status: PaperRunStatus;
  execution_mode: PaperExecutionMode;
  symbol: string;
  timeframe: string;
  initial_deposit: number;
  leverage: number;
  spread_mode: SpreadMode;
  spread_points: number | null;
  commission_per_lot: number;
  slippage_points: number | null;
  warmup_bars: number;
  parameters: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  balance: number;
  equity: number;
  margin_used: number;
  margin_free: number;
  unrealized_pnl: number;
  last_bar_time: string | null;
  started_at: string | null;
  stopped_at: string | null;
  created_at: string;
}

export interface PaperEquityPoint {
  time: string | null;
  equity: number;
  balance: number;
}

export interface PaperRunDetail extends PaperRun {
  config_snapshot: Record<string, unknown>;
  positions: PaperPosition[];
  equity_history: PaperEquityPoint[];
  /** 子进程 ctx.storage 快照（网格锚定价等策略状态；best-effort，可为 null） */
  strategy_state: Record<string, unknown> | null;
}

export interface PaperCreate {
  strategy_id: string;
  account_id: string;
  version?: number | null;
  symbol: string;
  timeframe: string;
  initial_deposit: number;
  leverage: number;
  execution_mode: PaperExecutionMode;
  spread_mode: SpreadMode;
  spread_points?: number | null;
  commission_per_lot: number;
  slippage_points?: number | null;
  warmup_bars: number;
  parameters: Record<string, unknown>;
  assumption_ack: boolean;
}

export interface PaperListResponse {
  items: PaperRun[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PaperTrade {
  id: string;
  run_id: string;
  ticket: number;
  direction: 'buy' | 'sell';
  volume: number;
  open_time: string;
  open_price: number;
  base_price: number;
  close_time: string;
  close_price: number;
  profit: number;
  profit_percent: number;
  swap: number;
  commission: number;
  spread_cost: number;
  slippage_cost: number;
  comment: string;
  is_forced: boolean;
}

export interface PaperTradeListResponse {
  items: PaperTrade[];
  total: number;
}

export interface PaperLog {
  id: string;
  run_id: string;
  seq: number;
  bar_time: string | null;
  kind: 'log' | 'signal' | 'system';
  level: string;
  message: string;
  payload: Record<string, unknown> | null;
}

export interface PaperLogListResponse {
  items: PaperLog[];
  total: number;
}

// ── Live trading (M6) ──

export type LiveRunStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'stopping'
  | 'stopped'
  | 'failed';

export interface LivePosition {
  ticket: number;
  symbol: string;
  type: 'buy' | 'sell';
  volume: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  profit: number;
  swap: number;
  commission: number;
  time: number;
  magic: number;
  identifier: number;
  comment: string;
}

export interface LiveRun {
  id: string;
  strategy_id: string;
  strategy_version: number | null;
  account_id: string;
  account_type: 'demo' | 'real' | 'contest' | null;
  status: LiveRunStatus;
  symbol: string;
  timeframe: string;
  execution_mode: string;
  allocated_capital: number;
  leverage: number;
  magic: number;
  warmup_bars: number;
  parameters: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  observation_ends_at: string | null;
  capital_cap_pct: number;
  lot_cap: number | null;
  no_new_orders: boolean;
  reconcile_pending: boolean;
  demoted: boolean;
  balance: number;
  equity: number;
  margin_used: number;
  margin_free: number;
  unrealized_pnl: number;
  last_bar_time: string | null;
  started_at: string | null;
  stopped_at: string | null;
  created_at: string;
}

export interface LiveRunDetail extends LiveRun {
  config_snapshot: Record<string, unknown>;
  admission_snapshot: Record<string, unknown>;
  positions: LivePosition[];
  equity_history: { time: string | null; equity: number; balance: number }[];
  /** 子进程 ctx.storage 快照（网格锚定价等策略状态；best-effort，可为 null） */
  strategy_state: Record<string, unknown> | null;
}

export interface GateResult {
  key: string;
  label: string;
  passed: boolean;
  reason: string;
  metrics: Record<string, unknown>;
}

export interface AdmissionResult {
  strategy_id: string;
  strategy_version: number;
  account_id: string;
  allowed: boolean;
  gates: GateResult[];
  decision_metrics: Record<string, unknown>;
}

export type RiskRuleType =
  | 'daily_max_loss'
  | 'daily_max_loss_pct'
  | 'max_drawdown'
  | 'max_lot_per_order'
  | 'max_open_positions'
  | 'max_exposure'
  | 'max_orders_per_minute'
  | 'consecutive_loss_cooldown'
  | 'order_rate_window';

export type RiskRuleAction = 'reject' | 'halt_open_today' | 'close_all_and_stop';

export type RiskRuleApplyTo = 'ai' | 'manual' | 'both';

export interface RiskRuleSpec {
  type: RiskRuleType;
  threshold: number;
  action: RiskRuleAction;
}

export interface RiskRule {
  id: string;
  scope: 'global' | 'account' | 'run';
  account_id: string | null;
  run_id: string | null;
  type: RiskRuleType;
  threshold: number;
  cooldown_minutes?: number | null;
  window_minutes?: number | null;
  apply_to: RiskRuleApplyTo;
  action: RiskRuleAction;
  enabled: boolean;
  note: string;
  created_at: string;
}

export interface RiskEvent {
  id: string;
  run_id: string | null;
  account_id: string | null;
  rule_type: string;
  source: string;
  threshold: number;
  observed: number;
  action_taken: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface RiskEventListResponse {
  items: RiskEvent[];
  total: number;
}

export interface LiveCreateBody {
  strategy_id: string;
  version: number;
  account_id: string;
  scope: 'demo' | 'real';
  symbol: string;
  timeframe: string;
  allocated_capital: number;
  leverage: number;
  warmup_bars: number;
  parameters: Record<string, unknown>;
  rules: RiskRuleSpec[];
  observation_days?: number | null;
  disclaimer_ack: boolean;
  account_tail: string;
}

export interface LiveTrade {
  id: string;
  run_id: string;
  deal_ticket: number;
  order_ticket: number;
  position_id: string;
  direction: 'buy' | 'sell';
  entry: string;
  volume: number;
  open_time: string;
  price: number;
  close_time: string | null;
  profit: number;
  swap: number;
  commission: number;
  retcode: number;
  comment: string;
  is_forced: boolean;
}

export interface LiveTradeListResponse {
  items: LiveTrade[];
  total: number;
}

// ── M4 slippage calibration / execution deviation ─────────────

export interface CalibrationSource {
  account_id: string;
  account_label: string;
  run_count: number;
  sample_count: number;
}

export interface SlippageCalibration {
  symbol: string;
  sample_count: number;
  skipped_count: number;
  mean_points: number | null;
  median_points: number | null;
  p75_points: number | null;
  p95_points: number | null;
  max_points: number | null;
  recommended_points: number | null;
  slippage_cost: number;
  first_deal_time: string | null;
  last_deal_time: string | null;
  insufficient: boolean;
  sources: CalibrationSource[];
}

export interface DeviationTrade {
  deal_ticket: number;
  direction: 'buy' | 'sell';
  entry: string;
  volume: number;
  open_time: string;
  signal_bar_time: string | null;
  signal_price: number | null;
  signal_spread_points: number | null;
  theoretical_price: number | null;
  actual_price: number;
  total_deviation_points: number | null;
  slippage_points: number | null;
  actual_spread_points: number | null;
  drift_points: number | null;
  latency_seconds: number | null;
  cost: number;
}

export interface DeviationReport {
  run_id: string;
  symbol: string;
  point: number;
  sample_count: number;
  skipped_count: number;
  avg_total_deviation_points: number | null;
  median_total_deviation_points: number | null;
  p95_total_deviation_points: number | null;
  avg_slippage_points: number | null;
  avg_signal_spread_points: number | null;
  avg_actual_spread_points: number | null;
  avg_latency_seconds: number | null;
  total_deviation_cost: number;
  verdict: 'aligned' | 'moderate' | 'divergent' | 'insufficient';
  trades: DeviationTrade[];
}

export interface LiveLog {
  id: string;
  run_id: string;
  seq: number;
  bar_time: string | null;
  kind: 'log' | 'signal' | 'system' | 'risk';
  level: string;
  message: string;
  payload: Record<string, unknown> | null;
}

export interface LiveLogListResponse {
  items: LiveLog[];
  total: number;
}

export interface LiveListResponse {
  items: LiveRun[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AlertEvent {
  id: string;
  level: 'info' | 'warn' | 'error' | 'critical';
  source: string;
  title: string;
  message: string;
  account_id: string | null;
  run_id: string | null;
  is_read: boolean;
  created_at: string;
}

export interface AlertListResponse {
  items: AlertEvent[];
  total: number;
  unread: number;
}

// ── Dashboard aggregation (M7) ──

export interface DashboardAccount {
  id: string;
  login: number;
  name: string;
  account_type: 'demo' | 'real' | 'contest';
  currency: string;
  balance: number;
  equity: number;
  margin_level: number;
  is_connected: boolean;
  trade_frozen: boolean;
  live_allowed: boolean;
}

export interface DashboardTotals {
  equity: number;
  today_pnl: number;
  unrealized_pnl: number;
  running_live: number;
  running_paper: number;
  demoted_live: number;
}

export interface DashboardEquityPoint {
  time: string;
  equity: number;
  balance: number;
}

export interface DashboardPosition {
  run_id: string;
  run_symbol: string;
  ticket: number;
  symbol: string;
  type: string;
  volume: number;
  price_open: number;
  price_current: number;
  profit: number;
  swap: number;
  time_msc: number;
}

export interface DashboardLiveRun {
  id: string;
  strategy_id: string;
  strategy_name: string;
  status: LiveRunStatus;
  symbol: string;
  timeframe: string;
  equity: number;
  unrealized_pnl: number;
  today_pnl: number;
  demoted: boolean;
  no_new_orders: boolean;
  reconcile_pending: boolean;
  observation_ends_at: string | null;
  capital_cap_pct: number;
  last_bar_time: string | null;
  started_at: string | null;
  account_type: 'demo' | 'real' | 'contest' | null;
}

export interface DashboardGateway {
  id: string | null;
  name: string;
  status: GatewayNodeStatus;
  version: string;
  uptime_seconds: number;
  active_sessions: number;
  cpu_percent: number;
  memory_percent: number;
  last_heartbeat: string | null;
}

export interface DashboardAlert {
  id: string;
  level: AlertEvent['level'];
  source: string;
  title: string;
  message: string;
  run_id: string | null;
  is_read: boolean;
  created_at: string;
}

export interface DashboardOverview {
  accounts: DashboardAccount[];
  totals: DashboardTotals;
  equity_curve: DashboardEquityPoint[];
  positions: DashboardPosition[];
  live_runs: DashboardLiveRun[];
  gateway: DashboardGateway;
  alerts: DashboardAlert[];
  alerts_unread: number;
}

// ── Audit (M7) ──

export interface AuditLogEntry {
  id: string;
  ts_ms: number;
  actor_type: string;
  actor_id: string | null;
  actor_label: string;
  action: string;
  target_type: string;
  target_id: string;
  params: Record<string, unknown>;
  result: string;
  error_code: string | null;
  risk_decision: string | null;
  rule_id: string | null;
  source_ip: string;
  correlation_id: string;
  prev_hash: string;
  hash: string;
  created_at: string;
}

export interface AuditListResponse {
  items: AuditLogEntry[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AuditVerifyResult {
  total: number;
  ok: boolean;
  broken_index: number;
  /** ok | fork | critical —— 分叉（并发写入的旧病灶，内容完好）与篡改分开报 */
  verdict: 'ok' | 'fork' | 'critical';
  findings_total: number;
  reason: string | null;
  checked_at: string;
}

// ── Notification channels (M10) ──

export type ChannelKind = 'email' | 'telegram' | 'dingtalk' | 'wecom';

export interface NotificationChannel {
  id: string;
  kind: ChannelKind;
  target_masked: string;
  min_level: AlertEvent['level'];
  enabled: boolean;
  verified_at: string | null;
  created_at: string;
}

export interface ChannelCreateBody {
  kind: ChannelKind;
  target: string;
  min_level: AlertEvent['level'];
}

export interface ChannelTestResult {
  ok: boolean;
  detail: string;
}

// ── Alert rules (M15) ──

export type AlertRuleType =
  | 'drawdown_pct'
  | 'daily_loss_pct'
  | 'gateway_offline'
  | 'trade_filled'
  | 'custom_metric';

export type AlertRuleScope = 'global' | 'account';

export type AlertOperator = 'lt' | 'lte' | 'gt' | 'gte';

export type CustomMetric = 'equity' | 'balance' | 'unrealized_pnl' | 'margin_free';

export interface AlertRule {
  id: string;
  name: string;
  type: AlertRuleType;
  scope: AlertRuleScope;
  account_id: string | null;
  threshold: number;
  operator: AlertOperator;
  metric: string;
  level: AlertEvent['level'];
  cooldown_seconds: number;
  enabled: boolean;
  note: string;
  created_at: string;
}

export interface AlertRuleCreateBody {
  name: string;
  type: AlertRuleType;
  scope: AlertRuleScope;
  account_id?: string | null;
  threshold: number;
  operator?: AlertOperator;
  metric?: CustomMetric | null;
  level: AlertEvent['level'];
  cooldown_seconds: number;
  note?: string;
}

export interface AlertRuleUpdateBody {
  name?: string;
  threshold?: number;
  operator?: AlertOperator;
  metric?: CustomMetric | null;
  level?: AlertEvent['level'];
  cooldown_seconds?: number;
  enabled?: boolean;
  note?: string;
}

// ── Gateway ops (M11) ──

export type SelfCheckStatus = 'pass' | 'warn' | 'fail';

export interface SelfCheckItem {
  key: string;
  label: string;
  status: SelfCheckStatus;
  detail: string;
  fix_hint: string;
  fix_commands?: string[];
}

export interface SelfCheckResult {
  all_pass: boolean;
  checked_at: string;
  items: SelfCheckItem[];
}

export interface PowerFixResult {
  executed: boolean;
  commands: string[];
  detail: string;
  results?: { cmd: string; rc: number; out: string }[];
}

// ── Manual trading (M14) ──

export interface ManualOrderBody {
  account_id: string;
  symbol: string;
  side: 'buy' | 'sell';
  volume: number;
  deviation?: number;
  comment?: string;
}

export type PendingOrderType = 'buy_limit' | 'sell_limit' | 'buy_stop' | 'sell_stop';

export interface ManualPendingBody {
  account_id: string;
  symbol: string;
  order_type: PendingOrderType;
  volume: number;
  price: number;
  sl?: number | null;
  tp?: number | null;
  comment?: string;
}

export interface ManualCloseBody {
  account_id: string;
  ticket: number;
  volume?: number | null;
}

export interface ManualCancelBody {
  account_id: string;
  ticket: number;
}

export interface TradeActionResult {
  success: boolean;
  action: 'order' | 'pending' | 'close' | 'cancel';
  retcode: number | null;
  retcode_text: string | null;
  order: number | null;
  deal: number | null;
  price: number | null;
  volume: number | null;
  error_code: string | null;
  error_message: string | null;
  account: Record<string, unknown> | null;
  positions: BrokerPosition[] | null;
}

export interface BrokerPosition {
  ticket: number;
  symbol: string;
  type: 'buy' | 'sell';
  volume: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  profit: number;
  swap: number;
  commission: number;
  time: number;
  magic: number;
  identifier: number;
  comment: string;
}

export interface BrokerOrder {
  ticket: number;
  symbol: string;
  type: number;
  state: number;
  volume: number;
  volume_initial: number;
  price_open: number;
  price_current: number;
  sl: number;
  tp: number;
  time_setup: number;
  magic: number;
  comment: string;
}

export interface BrokerDeal {
  ticket: number;
  order: number;
  time: number;
  symbol: string;
  type: number;
  entry: number;
  volume: number;
  price: number;
  commission: number;
  swap: number;
  profit: number;
  balance: number;
  magic: number;
  comment: string;
}

export interface TradeSnapshot {
  account: Record<string, unknown> | null;
  positions: BrokerPosition[];
  orders: BrokerOrder[];
}

// ── Tick data management (M-Tick, 数据中心 P1) ──

export type TickSyncStatus = 'pending' | 'running' | 'done' | 'failed';

export interface TickSyncJob {
  id: string;
  symbol: string;
  start_time: string;
  end_time: string;
  status: TickSyncStatus;
  tick_count: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TickSyncJobListResponse {
  items: TickSyncJob[];
  total: number;
}

export interface TickSyncRequest {
  symbol: string;
  /** epoch 秒（含） */
  start: number;
  /** epoch 秒（不含） */
  end: number;
}

export interface TickCoverage {
  symbol: string;
  tick_count: number;
  min_time_msc: number;
  max_time_msc: number;
}

export interface TickCoverageResponse {
  items: TickCoverage[];
}

export interface TickRow {
  time_msc: number;
  bid: number;
  ask: number;
  last: number;
  volume: number;
  flags: number;
}

export interface TickListResponse {
  items: TickRow[];
  total: number;
}

export interface TickPurgeResponse {
  symbol: string;
  deleted: number;
}

// ── Market history management (M2, 数据中心) ──

export interface HistoryProbeRequest {
  symbol: string;
  timeframe: MarketTimeframe;
}

export interface HistoryBackfillRequest {
  symbol: string;
  timeframe: MarketTimeframe;
  /** epoch 秒（含） */
  start: number;
  /** epoch 秒（不含） */
  end: number;
}

/** 一条已追踪的终端本地 K 线范围元数据。 */
export interface MarketHistoryRange {
  symbol: string;
  timeframe: string;
  first_bar_time: string | null;
  last_bar_time: string | null;
  bars_count: number;
  probe_truncated: boolean;
  last_synced_at: string | null;
  last_error: string | null;
}

export interface HistoryCoverageResponse {
  items: MarketHistoryRange[];
  total: number;
}

export interface HistoryBackfillResponse {
  symbol: string;
  timeframe: string;
  first_bar_time: string | null;
  last_bar_time: string | null;
  bars_count: number;
  windows: number;
  attempts: number;
  complete: boolean;
  requested_start: number;
  requested_end: number;
}

export interface HistorySyncResponse {
  updated: number;
  failed: number;
}

export interface HistoryRemoveResponse {
  symbol: string;
  timeframe: string;
  removed: boolean;
}

// ── Pagination ──

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}

// ── Research notebook (M18 研究笔记本) ──

export interface NotebookCell {
  id: string;
  type: 'code' | 'markdown';
  source: string;
  outputs: unknown[];
}

export interface NotebookSummary {
  id: string;
  title: string;
  cell_count: number;
  created_at: string;
  updated_at: string | null;
}

export interface NotebookDetail extends NotebookSummary {
  cells: NotebookCell[];
}

export interface NotebookListResponse {
  items: NotebookSummary[];
  total: number;
}

export interface NotebookRunResult {
  ok: boolean;
  stdout: string;
  result_repr: string | null;
  charts: NotebookChart[];
  etype: string | null;
  message: string | null;
  traceback: string | null;
  terminated: string | null;
  state_reset: boolean;
}

export interface NotebookChart {
  title: string;
  series: { name: string; points: { time: string; value: number | null }[] }[];
}

/** 回测队列总览（任务队列监控）。 */
export interface QueueOverview {
  running: string[];
  pending_count: number;
  oldest_pending_created_at: string | null;
}
