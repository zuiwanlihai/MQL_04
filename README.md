# MT5 量化交易平台

MetaTrader 5 Web 量化交易平台 — 后端 FastAPI + Gateway + React 前端。

## 架构

```
┌─────────────┐    HTTP     ┌──────────────┐   IPC    ┌────────────┐
│  React Web  │ ──────────► │   Backend    │ ────────► │ MT5 Gateway│
│  (Frontend) │  :3000      │   :8000      │  :8090    │            │
└─────────────┘             └──────┬───────┘           └─────┬──────┘
                                    │                         │
                           ┌────────▼──────┐          ┌───────▼──────┐
                           │  PostgreSQL   │          │ MT5 Terminal │
                           │  :5432        │          │  terminal64  │
                           └───────────────┘          └──────────────┘
```

三个独立服务。部署主路径为 Windows 本地（Gateway 依赖 MT5 terminal，必须 Windows）；CI 中 backend / frontend job 跑在 ubuntu-latest，gateway job 跑在 windows-latest。Docker 路径已标记 LEGACY（见 Makefile 头部注释）。

## 前置条件

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | 3.12+ | Gateway/Backend 共用 venv（可分开） |
| Node.js | 20+ | Frontend Vite |
| PostgreSQL | 15+ | Backend 元数据存储 |
| MetaTrader 5 | build 6241+ | 需 terminal64.exe 已安装并登录 |

## 启动路径（Windows Native — 主路径）

### 1. PostgreSQL

```powershell
# Docker 快速起（密码与 CI / 后端测试一致）
docker run -d --name mt5_pg -e POSTGRES_USER=mt5user -e POSTGRES_PASSWORD=mt5password -e POSTGRES_DB=mt5quant -p 5432:5432 postgres:15
```

`mt5user` 在容器内是超级用户；后端测试固定连 `mt5user/mt5password`（测试库 `mt5_quant_test` 由 conftest 自动创建），见 [backend/README.md](backend/README.md)。

### 2. Backend（FastAPI, port 8000）

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip.exe install -e ".[dev]"   # 本地开发装 dev extras（pytest / ruff / mypy）
# shared/sdk 无需安装：SDK 由应用通过 sys.path 直接引用仓库内 shared/（保持 backend/ 与 shared/ 同级）

# 创建 .env（最少）:
# DATABASE_URL=postgresql+asyncpg://mt5user:mt5password@localhost:5432/mt5quant
# JWT_SECRET_KEY=<随机 32+ 字符>

.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

后端专属说明（环境变量、数据库迁移、测试库要求）见 [backend/README.md](backend/README.md)。

### 3. Gateway（port 8090 — 绑定 MT5 terminal）

MT5 terminal64.exe **必须先启动并登录** Broker 账户，Gateway 通过 SDK IPC 与之通信。

```powershell
cd gateway
python -m venv .venv
.\.venv\Scripts\pip.exe install -e .

# 创建 gateway\.env（最少）:
# GATEWAY_API_KEY=<后端绑定流程写入的随机值>
# MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
# MT5_LOGIN=<可选: 自动登录号>
# MT5_PASSWORD=<可选>
# MT5_SERVER=<可选>

.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8090
```

Gateway 启动时会做以下自检（失败即拒绝启动）：
- GATEWAY_API_KEY 非空（不能留默认值对外暴露）
- MT5_PATH 存在
- MT5 IPC 能连通（terminal_info() 返回非 None）

### 4. Frontend (Vite, port 3000)

```powershell
cd frontend
npm install
npm run dev       # dev server :3000（/api、/ws 代理到 :8000，见 vite.config.ts）
# 或
npm run build     # 生产构建到 dist/
```

## 配置文件位置

| 服务 | 路径 | 格式 |
|---|---|---|
| Backend | `backend/.env` | pydantic-settings 读取 |
| Gateway | `gateway/.env` | pydantic-settings 读取 |
| Frontend | `frontend/.env`（可选） | Vite 读取 |

Gateway 的 `.env` 是后端 **调用 `/account/bind`** 后自动写入的——密钥、登录号、服务器都来自绑定流程，不建议手工创建。

## 测试

```powershell
# Backend（需要本地 PostgreSQL；测试库由 conftest 自动创建，见 backend/README.md）
cd backend
.\.venv\Scripts\python.exe -m ruff check app tests ..\shared\tests
.\.venv\Scripts\python.exe -m mypy app --show-error-codes
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m pytest ..\shared\tests -q    # SDK 指标库测试（不需要 DB）

# Gateway（conftest 只注入测试密钥，不需要真实 MT5）
cd gateway
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m pytest tests -q

# Frontend
cd frontend
npm run lint        # ESLint
npm test -- --run   # Vitest（含 SDK 指标镜像对照）
npm run build       # tsc -b + vite build
```

CI 工作流（`.github/workflows/ci.yml`）：backend / frontend job 跑在 `ubuntu-latest`（backend 带 postgres:16 service），gateway job 跑在 `windows-latest`（MetaTrader5 wheel 仅 Windows）。

## 关键设计约束

| 约束 | 位置 | 说明 |
|---|---|---|
| MT5 terminal 是 source of truth | Gateway mt5_client.py | Gateway 进程被杀/重启后，MT5 持仓通过 positions_get() 立即可见——reconcile 兜底 |
| Gateway 重启幂等 | Gateway trade.py | 相同 comment（run_id:req_id）的重复下单请求在 TTL=300s 内返回缓存结果，不重复发单 |
| 时间轴统一 UTC | Gateway mt5_client.py | copy_rates_from_pos / symbol_info_tick 都减去 broker TZ skew，全链路对齐到 UTC epoch |
| 正常运行零 churn | Gateway ops.py / mt5_client.py | _ensure_initialized_sync 三重短路，login() 同账户短路，guard_loop 只 probe 不 login |
| 沙箱安全边界 | Backend sandbox/import_hook.py | eval/exec/open/import 被 hook 拦截；用户策略在 sandbox 子进程内运行（EA 运行于 MT5 终端，经 EA 桥通信） |

## 三阶段优化

| 阶段 | 进度 | 详情 |
|---|---|---|
| P0 可靠性与实盘安全 | 4/4 ✅ | 网关退出 + CI 门禁 + 沙箱边界 + FaultState |
| P1 回测正确性 + 实盘闭环 | 12/12 ✅ | 撮合语义 + 时间轴 + 幂等键 + churn 排查 + 模拟账户验证 |
| P2 交付卫生 | 4/7 | 部署路径 · 运行说明（README） · 基础设施清理 · 指标库测试；余 P2-4 部分完成、P2-6 起步（BaseRunManager 基类）、P2-5 暂缓 |

详细见 `优化方案.md`。
