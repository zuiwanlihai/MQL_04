# MT5 网关 + 账户绑定（M1）+ 行情数据 垂直切片实施计划

## Context

认证闭环切片已完成（后端 5 端点 + 前端登录注册 + 中文化 + pytest/ruff 全绿）。用户选择下一个切片为 **MT5 网关 + 账户绑定 + 行情数据**，数据源为**本机真实 MT5 终端**（已确认：终端安装于 `C:\Program Files\MetaTrader 5\terminal64.exe`，**已登录账户**；MetaTrader5 PyPI 5.0.6231 有 cp314 win_amd64 wheel，可在现有 Python 3.14 上运行）。

对应 PRD：M1 网关绑定与心跳、M2 数据中心（品种树 / 实时报价表 / K线图 / 历史数据范围）。PRD 规则：**MT5 网关是唯一权威数据源，平台不落库行情**（缓存加速留待回测切片）。

范围（用户已确认）：含账户绑定 M1；行情读取（品种/报价/K线）；**不含**下单交易。

## 架构总览

```
MT5终端(已登录) ←MetaTrader5包← gateway/ 独立服务(127.0.0.1:8090, X-Gateway-Key)
                                   ↑ httpx (heartbeat 5s 出站轮询 + 行情/账户代理)
backend FastAPI(:8000)  —— gateway_nodes / gateway_health_logs / mt5_accounts 落库
                                   ↓ Bearer + React Query
frontend(:3000)  /market 行情总览 | /market/:symbolId K线详情 | /market/data 健康+绑定管理
```

## 1. 网关服务 `gateway/`（新，独立 venv）

目录：

```
gateway/
  pyproject.toml      # fastapi, uvicorn[standard], pydantic-settings, psutil, MetaTrader5==5.0.6231
  .env                # GATEWAY_API_KEY / MT5_PATH / GATEWAY_HOST / GATEWAY_PORT（gitignore）
  app/
    config.py         # GatewaySettings：HOST=127.0.0.1, PORT=8090, API_KEY=dev-gateway-key,
                      # MT5_PATH 默认 C:\Program Files\MetaTrader 5\terminal64.exe,
                      # 可选 MT5_LOGIN/PASSWORD/SERVER 启动自动登录
    security.py       # require_gateway_key：secrets.compare_digest；/health 免密钥，其余 401「网关密钥无效」
    mt5_client.py     # MT5Client：全部 mt5 调用经单线程 ThreadPoolExecutor 串行化（mt5 非线程安全），
                      # async 路由 run_in_executor，绝不阻塞事件循环
    schemas.py
    main.py           # lifespan：记录 uptime、可选自动登录、shutdown 调 mt5.shutdown()
    routes/{health,account,market}.py
```

路由：

| 方法/路径 | 要点 |
|---|---|
| GET `/health`（免密钥） | `{status: ok\|degraded\|no_login\|no_terminal, version, mt5_version, uptime_seconds, cpu_percent, memory_percent, active_sessions, terminal:{connected,trade_allowed}, account:{...}\|null, error}`；**永远可答**（心跳依赖） |
| POST `/account/login` | `{login,password,server}` → mt5.login → account 摘要；失败 400「MT5 登录失败：(code,msg)」 |
| POST `/account/logout` | 幂等 |
| GET `/symbols` | symbols_get 全量 + 属性，网关内 60s 缓存 |
| GET `/symbols/{name}/rates?timeframe=&limit=` | 前置 `symbol_select`；`copy_rates_range`（回溯 now - 周期秒×limit×1.6）；time=epoch 秒；品种不存在 404 |
| GET `/quotes?symbols=A,B` | symbol_info_tick；prev_close/今日 D1 走 60s 模块缓存 |

关键注意点：
- `ensure_initialized()`：每次请求前懒检测，`initialize(path=...)` 为 False → 取 `mt5.last_error()` 抛中文错误并自愈重试；终端已在运行会附着不重复拉起；路径含空格直接字符串传参（不走 shell）
- 周期映射硬编码 `TF_MAP`：M1/M5/M15/M30=1/5/15/30，H1/H4=16385/16388，D1/W1/MN1=16408/32769/49153；非法值 422
- 启动：`.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8090`，**单 worker、不加 --reload**（多进程/reload 会破坏与终端的单连接）
- 状态矩阵：终端不可达 → 业务接口 503 中文 detail，/health 仍 200 `no_terminal`；未登录 → /health `no_login`，行情接口用终端缓存仍可答

## 2. 后端 `backend/`

### 新文件

```
app/models/gateway_node.py、gateway_health_log.py、mt5_account.py   # 注册进 app/models/__init__.py
app/schemas/gateway.py、accounts.py、market.py
app/services/gateway_client.py       # GatewayClient(httpx.AsyncClient 单例, Timeout(10, connect=3),
                                     # X-Gateway-Key 头) + GatewayUnavailable/GatewayLoginError(中文 detail)
                                     # + get_gateway_client 可被 dependency_overrides 替换成 stub
app/services/gateway_heartbeat.py    # heartbeat_loop/heartbeat_once/classify/ensure_default_node
app/services/credentials.py          # Fernet 加密
app/services/market_mapper.py        # MT5 dict → 前端 Symbol/Quote/PriceBar
app/api/v1/endpoints/gateway.py、accounts.py、market.py   # router.py 挂 3 个子路由
tests/test_credentials.py、test_market_api.py、test_accounts_api.py、test_gateway_api.py
```

改文件：`app/config.py`、`app/main.py`（lifespan 心跳任务）、`app/models/__init__.py`、`app/api/v1/router.py`、`pyproject.toml`（运行期显式加 `httpx>=0.27`、`cryptography>=42`）、`backend/.env`、`tests/conftest.py`。

### 模型（严格对齐 `frontend/src/types/api.ts` 既有类型；风格照 `models/user.py`：`BaseModel, kw_only=True`、`default_factory`）

- **GatewayNode** `gateway_nodes`：name(默认"本地网关")、host+port(UniqueConstraint)、status(online/offline/degraded/starting)、version、uptime_seconds、active_sessions、cpu_percent、memory_percent、last_heartbeat、is_primary(默认 True)、**metadata JSONB**
  - ⚠️ SQLAlchemy 声明类属性名 `metadata` 与 `Base.metadata` 冲突 → ORM 属性命名 `metadata_json = mapped_column("metadata", JSONB, ...)`，端点手动映射为响应字段 `metadata`（不依赖 from_attributes）；JSONB 需 `from sqlalchemy.dialects.postgresql import JSONB`
- **GatewayHealthLog** `gateway_health_logs`：node_id(FK CASCADE, index)、status、latency_ms、error_rate(默认0)、message(Text, null)、recorded_at(index)
- **MT5Account** `mt5_accounts`：login(BigInteger, index)+server(UniqueConstraint)、name、account_type(demo/real/contest)、leverage、currency、balance/credit/equity/margin/margin_free/margin_level Numeric(20,6)、is_connected、is_trade_allowed、gateway_node_id(FK 非空)、last_sync_at、**password_encrypted(Text) 永不进响应 schema**、user_id(FK users, index)

### 配置追加（config.py）

```
GATEWAY_URL=http://127.0.0.1:8090   GATEWAY_API_KEY=dev-gateway-key   GATEWAY_TIMEOUT_SECONDS=10
GATEWAY_HEARTBEAT_ENABLED=true      # conftest 置 false（必须在 import app 前）
HEALTH_LOG_SAMPLE_INTERVAL=30       HEALTH_LOG_RETENTION_HOURS=24      CREDENTIALS_KEY=""
（GATEWAY_HEARTBEAT_INTERVAL=5 / GATEWAY_OFFLINE_THRESHOLD=30 已存在，复用）
```

### 心跳（lifespan 挂载）

- 每 5s GET /health（计时 latency_ms）→ upsert gateway_nodes（按 host+port）+ 状态判定：成功 `ok`→online、`no_login/degraded`→degraded、请求失败第1次 degraded、连续 6 次(30s)→offline（防抖）
- **健康日志采样防膨胀**：状态变化必写 + 否则 ≥30s 才写；error_rate 用 5 分钟滑窗；每 10 分钟 DELETE 早于 24h 的记录；recorded_at 索引
- shutdown：cancel 任务须在 `close_db()` 之前
- `tests/conftest.py`：`os.environ["GATEWAY_HEARTBEAT_ENABLED"]="false"`（与 DATABASE_URL 同处，import app 前）

### 端点（全部 `CurrentUser + DBSession`，风格照 endpoints/auth.py；503/400/409 detail 全中文）

**/gateway**：GET `/nodes`（列表）、POST `/nodes`（手动补充，创建时探活，失败 status=offline）、DELETE `/nodes/{id}`（204，级联删日志）、GET `/health-logs?node_id=&limit=500`（recorded_at DESC）

**/accounts**（PRD：密码全程不回传；归属 user_id 校验）：
- POST `/accounts/test`：`{gateway_url?, login, password, server}` → 调网关 login+摘要，**不落库**；503 网关不可达 / 400 登录失败
- POST `/accounts`：`ensure_default_node` → test 流程 → `credentials.encrypt_secret(password)` 落库 → 201；重复 409「该账户已绑定」
- GET `/accounts`：`{items, total}`，`MT5AccountOut` 显式字段（结构性排除 password_encrypted）
- DELETE `/accounts/{id}`：204；POST `/accounts/{id}/sync`：解密密码、必要时重新 login、刷新余额/净值/杠杆/is_connected + last_sync_at

**/market**（本期不落库行情，网关透传）：
- GET `/symbols?group=&q=`：`{items, total}`（分组/搜索在后端内存过滤，网关一次全量）
- GET `/symbols/{name}` → 404「品种不存在：{name}」
- GET `/symbols/{name}/klines?timeframe=M1..D1&limit=1..5000(默认500)` → list[PriceBar] 升序
- GET `/quotes?symbols=A,B`(1..50) → list[Quote]（派生 change/change_percent/spread/high_day/low_day 在 mapper 算）
- 网关不可达统一 `GatewayUnavailable` → 503 中文 detail「行情网关不可达：…请在「行情 · 数据管理」页确认网关服务已启动」

### 字段映射（market_mapper.py，全部 `getattr(..., default)` 防御取值；首跑 curl /symbols 后据实冻结）

- Symbol：id=name；group 由 `si.path` 关键词映射（Forex→forex、Metal/Commodit→commodities、Index→indices、Crypto→crypto、Stock/ETF→stocks，兜底：3+3字母对→forex 否则 stocks）；digits/point/spread/contract_size/volume_*/swap_* 直取；margin_percent←margin_initial；is_trade_allowed←trade_mode==0；session_open/close 缺失→""
- Quote：last=last or bid；spread=round((ask-bid)/point)；change/change_percent 用网关 prev_close；high_day/low_day 用今日 D1；time → ISO 字符串
- PriceBar：time=epoch 秒（直接喂 lightweight-charts）、OHLC float、tick_volume/spread/real_volume 可选

### 凭据加密（credentials.py）

`Fernet(urlsafe_b64encode(sha256(CREDENTIALS_KEY or JWT_SECRET_KEY)))`，token 加 `v1:` 版本前缀；`decrypt` 失败抛「凭据解密失败，请重新绑定」；明文密码不写日志、Pydantic repr 排除。

### alembic

```powershell
cd backend; .venv\Scripts\python -m alembic revision --autogenerate -m "add gateway tables"
.venv\Scripts\python -m alembic upgrade head    # 修订文件落 backend/alembic/versions/（唯一正确目录）
```
验证 `\dt` 见 3 新表；测试库由 conftest create_all 自动覆盖，不受影响。

## 3. 前端 `frontend/`

新文件：
```
src/pages/MarketData/MarketOverview.tsx、SymbolDetail.tsx、DataManagement.tsx
src/components/market/{GroupTabs,QuoteTable,KlineChart,GatewayHealthCard,AccountBindForm,AccountTable}.tsx
src/hooks/{useMarket,useGateway,useAccounts}.ts
```
改：`routes.tsx`（3 条占位换 lazy 真实页面，`/market/data` 静态段排在 `/market/:symbolId` 前）、`types/api.ts`（增补 `AccountProbeResult`/`AccountBindRequest`/`AccountTestRequest`/`MarketTimeframe`；既有 6 个行情类型不动）。

React Query（Provider 已挂 staleTime 30s/retry 1，全站首个使用方）：

| Hook | key | refetchInterval |
|---|---|---|
| useSymbols(group,q) | ['market','symbols',{group,q}] | 30s |
| useQuotes(names) | ['market','quotes',names] | **5s** + `keepPreviousData` 防闪烁 |
| useKlines(sym,tf,limit) | ['market','klines',...] | false（切周期重取） |
| useGatewayNodes | ['gateway','nodes'] | **5s** |
| useHealthLogs | ['gateway','health-logs',id] | 15s |
| useAccounts | ['accounts'] | 30s；mutation 后 invalidate |

页面要点：
- **MarketOverview**：分组页签（全部/外汇/商品/指数/加密货币/股票）+ 搜索（防抖300ms）+ 「数据管理」链接；QuoteTable 列：品种/bid/ask/点差/涨跌幅/详情；涨跌**红涨绿跌**（text-loss 红 / text-profit 绿，常量 `UP_IS_LOSS` 放 utils，与 PRD 4.3 一致）；价格 `font-mono`
- **SymbolDetail**：报价头（5s）+ KlineChart + 周期按钮 M1/M5/M15/M30/H1/H4/D1 + 返回链接
- **KlineChart**（lightweight-charts 4.2.1）：`createChart` + `addCandlestickSeries({upColor:'#ef4444',downColor:'#22c55e',...})`；`setData` 用 `PriceBar.time`（epoch 秒 = UTCTimestamp）；v4 无 timezone → `localization.timeFormatter`+`tickMarkFormatter` 用 `Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai'})`；`ResizeObserver` 自适应；**StrictMode 双跑 → cleanup 必须 `chart.remove()`+`observer.disconnect()`**；503 时渲染中文错误态而非空图
- **DataManagement**：①网关健康卡片（状态徽章/版本/uptime 中文格式/CPU/MEM 进度条/最后心跳相对时间/host:port/metadata.mt5_version）+ 健康日志列表；②账户绑定：表单（网关地址默认 localhost:8090/账号/密码/服务器）→ [测试连接] 展示摘要 → [绑定]；账户表格（类型中文：模拟/实盘/竞赛、余额/净值/杠杆/连接状态/最后同步/[同步][解绑确认]）

## 4. 执行顺序（每步验证）

1. 后端依赖+配置+env → `pip show httpx cryptography`、settings 打印
2. 3 模型 + `__init__` 注册 → `ruff check .`；pytest conftest create_all 不报错
3. alembic autogenerate + upgrade → `alembic current`、`\dt` 见 3 表
4. credentials.py + 单测（往返、v1: 前缀）→ pytest
5. gateway_client + market_mapper + /market 端点 + 路由 → 未起网关 curl 得 503 中文；`pytest tests/test_market_api.py`（stub）
6. /accounts 端点 → `pytest tests/test_accounts_api.py`（绑定后响应无密码、重复 409、sync 刷新）
7. /gateway 端点 → `pytest tests/test_gateway_api.py`
8. 心跳 + main.py lifespan + conftest 禁用 → 全量 `pytest -q`、`ruff check .`
9. 网关骨架+venv+`/health` → `curl localhost:8090/health`（终端已登录应 `ok`+account 非空）；错 key 401
10. 网关其余 5 路由 → curl 流 1–7（§6A）
11. 联调：uvicorn 心跳开 → psql 见节点 online + 日志 30s 采样；停网关 30s → offline
12. 前端 types+hooks+MarketOverview+路由 → `npx tsc -b`；浏览器 /market 5s 红绿刷新
13. SymbolDetail+KlineChart → 切周期出图、无 console 错误
14. DataManagement → 浏览器全流程
15. 门禁：`pytest -q && ruff check .`（backend）、`npx tsc -b && npm run build`（frontend）

## 5. 主要风险与对策

- **initialize 失败**（路径错/提权不一致/杀软）→ 每请求自愈重试；/health 永远可答带 error 原文；前端卡片给排障文案（PRD M1 要求附带排障指引）
- **品种不在 Market Watch** → rates/quotes 前置 `symbol_select(name, True)`；仍无 → 404 中文提示
- **Symbol 字段名与设想不符** → mapper 全 `getattr` 默认值；步骤 10 先落真实字段清单再冻结映射；前端缺失渲染 `—`
- **心跳表膨胀** → 采样（状态变化必写+≥30s）+ 24h 滚动 DELETE + 索引 + 端点 limit≤500
- **密码泄漏** → 响应 schema 显式字段、不写日志、Fernet 加密落库、`.env` gitignore
- **单终端单账户**：login 会顶掉终端手动登录 → 文案明示共用会话
- **测试跨事件循环 asyncpg** → 断言一律经 TestClient；心跳 conftest 禁用
- **uvicorn 多 worker/--reload 破坏 mt5 单连接** → 网关固定单 worker 无 reload
- **`/market/data` 被 `/market/:symbolId` 吞** → 静态路由排列在前 + 浏览器直开冒烟

## 6. 验证方案

**A. 网关 curl**（key=`dev-gateway-key`）：/health（200 ok+account）→ 错 key /symbols 401 → /account/login（真实账号）→ /symbols → /rates?timeframe=M1&limit=100 → /quotes?symbols=EURUSD,XAUUSD → 负例（404 品种 / 422 非法周期）

**B. 后端 curl**（登录拿 token）：/gateway/nodes（online、5s 内心跳）→ /gateway/health-logs（30s 后 1–2 条）→ /market/symbols?group=forex&q=EUR → /klines?timeframe=H1&limit=200 → /quotes → accounts test→bind→list（无 password）→sync→unbind → 降级（停网关 503 中文 + 30s 后 offline）→ 无 token 401

**C. SQL 抽查**：gateway_nodes 状态/心跳、health_logs 延迟、mt5_accounts.password_encrypted 为 `v1:` 密文

**D. 浏览器 E2E**：/market 分组+搜索+5s 红绿刷新 → 详情 K线切周期+缩放自适应+console 无错 → /market/data 健康卡片走动+日志增量 → 绑定全流程（测试→绑定→同步→解绑）→ 异常（停网关红色离线+中文错误态、错密码中文提示、重启恢复）→ 全程中文无残留

**E. 门禁**：backend `pytest -q && ruff check .`；frontend `npx tsc -b && npm run build`
