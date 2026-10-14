# MT5 Quant Platform — Backend

平台后端服务：FastAPI（默认 `:8000`），提供 REST + WebSocket API、策略沙箱与回测 / 模拟 / 实盘 runner、风控准入、对账与审计。

平台总览与三服务（Backend / Gateway / Frontend）联调见仓库根 [README.md](../README.md)；本文只写后端专属内容：环境变量、数据库迁移、测试库要求。

> 本文件同时是 Python 包 `mt5-quant-platform` 的长描述——`pyproject.toml` 的 `readme = "README.md"` 指向它。

## 快速开始

前置：Python 3.12+；PostgreSQL 15+ 运行在 `localhost:5432`。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip.exe install -e ".[dev]"   # dev extras：pytest / pytest-asyncio / httpx / ruff / mypy 等

# 创建 backend/.env（最少两项，见下节「配置」）：
# DATABASE_URL=postgresql+asyncpg://mt5user:mt5password@localhost:5432/mt5quant
# JWT_SECRET_KEY=<随机 32+ 字符>

.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

策略 SDK **不需要单独安装**：仓库里的 `shared/` 由应用通过 `sys.path` 直接引用（见 [app/sandbox/__init__.py](app/sandbox/__init__.py) 与 [app/seed.py](app/seed.py)）。因此 `backend/` 必须与 `shared/` 保持在同一仓库内的同级位置，不能单独拷贝 `backend/` 目录部署。

受支持的部署形态是原生 Windows 本地运行（与 Gateway / Frontend 同机）。根目录 Makefile / docker-compose 已标 **⚠️ LEGACY**（P2-1）：compose 引用的 Dockerfile 并不存在，不要当作可用的部署路径。

## 配置

全部设置由 `pydantic-settings` 从环境变量或 `backend/.env` 读取；**唯一权威清单在 [app/config.py](app/config.py)**（约 100 项）。常用项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://mt5user:mt5pass@localhost:5432/mt5quant` | 开发占位默认，部署必须覆盖 |
| `DATABASE_SYNC_URL` | 由 `DATABASE_URL` 推导 | 同步连接串（Alembic 用） |
| `JWT_SECRET_KEY` | 占位串 | ≥32 字符；生产必须覆盖 |
| `CREDENTIALS_KEY` | 空 | 账户密码的 Fernet 密钥；为空时从 `JWT_SECRET_KEY` 派生 |
| `GATEWAY_URL` | `http://127.0.0.1:8090` | 本地网关地址 |
| `GATEWAY_API_KEY` | `dev-gateway-key` | 须与 `gateway/.env` 一致 |
| `LIVE_ENABLED` | `false` | 实盘执行灰度开关 |
| `MANUAL_TRADE_ENABLED` | `false` | 手动交易面板灰度开关 |
| `CORS_ORIGINS` | `localhost:3000` / `:5173` | 允许的前端来源 |
| `LOG_LEVEL` / `DEBUG` | `INFO` / `false` | |

## 数据库与迁移

- 迁移目录 `alembic/`，配置 `alembic.ini`。URL **不**写在 ini 里：`alembic/env.py` 从应用 settings 读取（即 `.env` 的 `DATABASE_URL` / `DATABASE_SYNC_URL`）。
- 应用迁移：`python -m alembic upgrade head`；新建迁移：`python -m alembic revision --autogenerate -m "..."`。
- 对目标环境执行迁移前先备份，并按项目惯例由环境负责人执行。

## 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m ruff check app tests ..\shared\tests
.\.venv\Scripts\python.exe -m mypy app --show-error-codes
.\.venv\Scripts\python.exe -m pytest tests -q            # 需要本地 PostgreSQL
.\.venv\Scripts\python.exe -m pytest ..\shared\tests -q  # SDK 指标库测试（不需要 DB）
```

测试数据库与 `.env` 无关，由 [tests/conftest.py](tests/conftest.py) 在任何 `app.*` 导入前固定覆写：

- 固定连接 `mt5user:mt5password@localhost:5432`，库名 `mt5_quant_test`——与应用的 `mt5quant` 是**两个库**。这三个值是 conftest 里的硬编码：本地角色密码若不是 `mt5password`，后端测试会直接连不上。
- 库不存在时自动 `CREATE DATABASE`，随后 drop/create 全部表并写入内置策略模板；角色需要 **CREATEDB** 权限（CI 的 postgres service 和根 README 的 docker 一键起法里 `mt5user` 都是超级用户，天然满足）。
- 测试会清空 `mt5_quant_test` 的全部表——永远不要把应用数据指到这个库名上。

CI（`.github/workflows/ci.yml`）：backend job 跑在 `ubuntu-latest`，Python 3.12 + postgres:16 service，依次执行 Ruff（含 `../shared/tests`）、Mypy、`pytest tests` 与 `pytest ../shared/tests`。

## 目录结构

```
app/
  api/          v1 REST 端点 + WebSocket（行情推送 market_ws）
  engine/       SQLAlchemy async engine / session 生命周期
  models/       ORM 模型
  schemas/      Pydantic 请求 / 响应模型
  sandbox/      策略沙箱子进程（import hook、子进程 runner、EA 桥）
  services/     backtest / paper / live runner（公共基类 run_base.py）、
                风控准入、对账、通知渠道、告警规则、审计巡查、行情同步等
alembic/        迁移脚本（versions/ 按日期编号）
tests/          pytest 测试套件（测试库由 conftest 自动创建，见上）
```
