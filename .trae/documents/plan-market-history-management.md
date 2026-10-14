# M2 历史数据管理 — 实现计划

## Context

PRD M2 数据中心遗留项：「显示每个品种已缓存的数据范围，手动触发补数；后台定时增量同步」。平台 K 线不落库（market.py「本期不落库」、质量检查 compute-on-read 先例），「已缓存数据范围」指 **MT5 终端本地**可用 K 线范围——MT5 的 copy_rates 请求超出终端本地历史时会异步向券商下载，因此「补数」= 循环重试 copy_rates_range 直到范围收敛。补数后的真实价值：回测/模拟盘启动取数（走网关 rates/range）能拿到更早历史。

设计决策：
- **只落范围元数据** `market_data_ranges`（全局共享表，不分用户），K 线本身不落库——符合「网关为唯一权威来源」原则
- **补数同步阻塞**（单请求 ≤200k 根、最坏约 60s，前端 timeout 覆盖 120s），不建任务表——K 线补数天然幂等崩溃安全（下载的数据留在终端本地），tick 的任务表模式是为分钟级大窗口设计的，此处过度设计
- **周期集合 M1~D1** 与现有 MarketTimeframe 一致（W1/MN 留作后续独立小切片）
- 多节点不涉及：history 端点与 klines/quality 一致走全局默认网关

## 一、网关（gateway/）

### gateway/app/config.py — 新增 4 项
```
HISTORY_PROBE_PAGE_SIZE=100_000、HISTORY_PROBE_MAX_BARS=500_000
HISTORY_BACKFILL_MAX_ATTEMPTS=6、HISTORY_BACKFILL_RETRY_INTERVAL=2.0
```

### gateway/app/mt5_client.py — 新增 probe/backfill（rates 区块后、ticks 前）
- `_history_probe_sync(name, timeframe)`：纯本地探测不重试。`copy_rates_from_pos(0,1)` 拿最新一根 → 从 offset=0 起按 PAGE_SIZE 分页向历史回溯（短读=本地最早→终止），total 达 PROBE_MAX_BARS 置 truncated=True；读取 `terminal_info().maxbars`（探测 page 取 min(page, maxbars)）一并返回。MT5 行为注释：copy_rates_from_pos 超出序列起点返回 None/空；单次调用只返回当前就绪数据不等待。
- `history_backfill(name, timeframe, start_ts, end_ts, max_attempts)`：**async 方法内 for 循环**（绝不能把重试循环写进同步函数霸占单 worker 队列），每次 attempt 调 `await self.run(self._rates_range_sync, ...)`（复用 476-522 行），attempt 间 `await asyncio.sleep(interval)` 释放 worker 保交易插队。收敛判定：结果 first<=start_ts → complete=True；两轮签名（count,first,last）完全一致 → break（终端已尽力）。

### gateway/app/routes/market.py — 新增 2 个 POST 路由
- `POST /symbols/{name}/history/probe` body `{timeframe}` → `{symbol,timeframe,first_bar_time,last_bar_time,bars_count,truncated,max_bars}`
- `POST /symbols/{name}/history/backfill` body `{timeframe,start,end,max_attempts?}` → `{first_bar_time,last_bar_time,bars_count,attempts,complete,requested_start,requested_end}`
- 请求模型内联（跟随网关现状）

### gateway/tests/test_history.py — 新增
照抄 test_ticks.py 假 MT5 模块模式（sys.modules 替换 + numpy dtype 构造）。用例：probe 全序列/空序列/分页回溯/未知品种 404；backfill 编程式收敛（None→部分→全量）断言 attempts/complete；两轮签名一致提前 break；first<=start → complete=True。

## 二、后端（backend/）

### app/config.py — 新增段
```
MARKET_HISTORY_SYNC_ENABLED=True / MARKET_HISTORY_SYNC_INTERVAL_SECONDS=300(ge=30)
MARKET_HISTORY_SYNC_BATCH=20 / MARKET_HISTORY_SYNC_PAIR_DELAY=0.05
MARKET_HISTORY_PROBE_TIMEOUT=30.0 / MARKET_HISTORY_BACKFILL_TIMEOUT=60.0
MARKET_HISTORY_BACKFILL_WINDOW_BARS=50_000 / MARKET_HISTORY_BACKFILL_MAX_BARS=200_000
MARKET_HISTORY_BACKFILL_MAX_ATTEMPTS=6
```

### app/models/market_history.py — 新建
`MarketDataRange(BaseModel, kw_only=True)`，`__tablename__="market_data_ranges"`，UniqueConstraint(symbol,timeframe)；字段 symbol(32)/timeframe(8)/first_bar_time/last_bar_time( DateTime(timezone=True) 可空)/bars_count/probe_truncated/last_synced_at/last_error(Text)。`app/models/__init__.py` 注册（alembic 依赖）。

### alembic 迁移 — 新建
down_revision=`d9b7e4c2a516`（当前 head）；create_table 照抄 tick 表迁移的 BaseModel 三列手写方式 + unique constraint；每列 comment。

### app/schemas/market_history.py — 新建
HistoryProbeRequest/HistoryBackfillRequest/MarketDataRangeOut/HistoryCoverageResponse/HistoryBackfillResponse/HistorySyncResponse，全部 extra="forbid"。

### app/services/market_history.py — 新建（核心）
- `_locks: dict[(symbol,tf), asyncio.Lock]` + `Semaphore(1)` backfill 串行化
- `probe_and_upsert(db, symbol, tf)`：调 GatewayClient.probe_history → upsert
- `backfill(db, symbol, tf, start, end)`：跨度校验（≤MAX_BARS 折算根数）→ 按 WINDOW_BARS 切时间窗串行调网关 → 收尾 probe_and_upsert 刷新 → 返回汇总
- `sync_round(db, max_pairs)`：`last_synced_at.asc().nulls_first()` 轮转取 BATCH 对，每对 `client.rates(sym, tf, 2)` 只更新 last_bar_time（first/bars_count 是 probe 快照不动）；GatewayUnavailable → 置 last_error 并立即 break 本轮；单对 GatewayError 记 last_error 继续；对间 sleep(PAIR_DELAY)；break 前已请求对也推进 last_synced_at
- `run_sync_loop()`：照抄 heartbeat_loop 骨架（try/except 不死 + sleep 读 settings）
- `remove_tracking(db, symbol, tf)`

### app/services/gateway_client.py — 新增 2 方法
`probe_history(name, tf, timeout=None)` / `history_backfill(name, tf, start, end, max_attempts=None, timeout=None)`，POST 到网关对应端点；404→GatewaySymbolNotFound，>=400→GatewayError（照 rates_range 错误翻译风格）。

### app/api/v1/endpoints/market_history.py — 新建（前缀 /market/history）
注册顺序：`GET /coverage` → `POST /probe` → `POST /backfill` → `POST /sync` → `DELETE /coverage/{symbol}/{timeframe}`（字面量在路径参数前，项目踩坑约定）。全部 CurrentUser 必须登录；symbol `.strip().upper()`；GatewaySymbolNotFound→404 其余 GatewayError→503（market.py `_fail` 模式）。backfill：未追踪 404「请先探测/添加追踪」、end<=start 或跨度超限 422、audit.record("market_history.backfill")。DELETE：audit.record("market_history.untrack")。

### app/api/v1/router.py — include_router(prefix="/market/history", tags=["market"])
### app/main.py — lifespan 启动/取消 run_sync_loop（MARKET_HISTORY_SYNC_ENABLED 门控）

### backend/tests/test_market_history_api.py — 新建
类级 monkeypatch GatewayClient（test_ticks_api.py:41-58 同款，service 自建 client 不走 DI）。覆盖：5 端点 401；probe 首建/幂等更新/网关 404 不留行；coverage 列表；backfill 未追踪 404/422 校验/窗口切分调用次数+probe 补调/网关失败 503；sync 更新 last_bar_time 不动 first/ GatewayUnavailable failed≥1；DELETE+audit/未追踪 404。

## 三、前端（frontend/）

### src/types/api.ts — 新增类型
MarketHistoryRangeOut/HistoryCoverageResponse/HistoryBackfillResponse/HistorySyncResponse + 请求类型（timeframe 复用 MarketTimeframe）

### src/hooks/useMarketHistory.ts — 新建
useHistoryCoverage（15s 轮询）/useProbeHistory/useHistoryBackfill（**timeout:120_000 覆盖 axios 全局 30s**，lib/api.ts:37）/useSyncHistoryRound/useRemoveHistoryCoverage；成功后 invalidate coverage。

### src/components/market/HistoryDataPanel.tsx — 新建（镜像 TickDataPanel）
说明文案（追踪终端本地 K 线范围；补数触发终端向券商下载供回测/模拟盘取数）→ 追踪表单（品种 input + 周期 select + 「探测并追踪」）→ coverage 表格（品种/周期/最早/最新/条数/最后同步/错误 truncate+title）→ 行操作「补数」（内联展开起止 datetime-local，默认起=first_bar_time 止=now，提交提示「可能需要约 1 分钟」）/「重新探测」/「移除」→ 面板级「立即同步」→ truncated 时提示「已达探测上限（终端 max bars=N），可调大终端『图表最大 K 线数』」→ complete=False 文案用「已达可用历史边界」不写「失败」。

### src/pages/MarketData/DataManagement.tsx — `<HistoryDataPanel />` 插在 QualityCheckPanel 与 TickDataPanel 之间

## 验证

1. `backend\.venv\Scripts\python.exe -m ruff check app` + alembic upgrade head（dev 库）
2. 网关测试：cd gateway && `backend\.venv\Scripts\python.exe -m pytest tests`（新增 test_history.py 全过 + 存量不破）
3. 后端测试：`backend\.venv\Scripts\python.exe -m pytest tests/test_market_history_api.py` → 全量 pytest
4. 前端 `npm run build`（tsc+vite）
5. 浏览器 E2E：数据管理页新面板渲染 → 追踪 EURUSD/H1 → 立即同步 last_bar_time 刷新 → 补数（较早 start）后 first_bar_time 前移 → 移除追踪 → 网关停止时行内报错
6. 重启 backend（uvicorn 8000）与无需重启网关（网关新增路由需重启 gateway 8090）

## 风险说明
- TERMINAL_MAXBARS 玻璃天花板：终端「图表最大 K 线数」默认约 100k，超出截断——前端已带提示文案，属终端配置不可平台修改
- 券商历史深度有限：complete=False 表示「已达可用历史边界」，非错误
