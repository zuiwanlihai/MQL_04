# 认证闭环（Auth Vertical Slice）实施计划

## Context

项目处于骨架状态：后端只有 `/ping` 占位路由，`User` 模型已写但未注册（且其 relationship 引用了 5 个不存在的模型，直接导入会炸 mapper），alembic 迁移目录缺失，前端 `App.tsx`/`routes.tsx` 引用的 `pages/Login|Register|Dashboard`、`components/layout/AppShell`、`store/useAuthStore` 全部不存在（应用当前无法编译运行）。

本轮目标：打通**认证闭环**——后端注册/登录/JWT/刷新/登出 + alembic 初始迁移，前端 api 客户端 + auth store + 三个页面 + AppShell，使应用可本地登录运行。

**已确认的环境决策**：winget 安装本机 PostgreSQL 17；用 Python 3.14 在 `backend/.venv` 建虚拟环境（若个别依赖无 3.14 wheel 则记录报告）。本机无 Docker，`docker/Dockerfile.backend|frontend` 也不存在，一切验证走本地进程。

## 关键设计决策

| 决策 | 结论 |
|---|---|
| 字段对齐 | `User` 删 `display_name`，增 `username`(unique)、`full_name`、`role`(String(16), 默认 `'trader'`)——以前端 `types/api.ts` 的 `User` 为契约。**首个注册用户自动 `role='admin'` + `is_superuser=True`** |
| 悬空关系 | `user.py` 中 `mt5_accounts/strategies/alert_rules/risk_rules` 四个 relationship 删除并留 TODO 注释（对应模型不存在，导入即崩）；`sessions` 保留 |
| Refresh | DB-backed `UserSession` 行 + 不透明随机 refresh token（`secrets.token_urlsafe`）+ SHA-256 哈希存储 + 每次 refresh 轮转 + logout 置 `revoked_at`。access token 为无状态 JWT（claims: `sub`, `sid`, `type='access'`） |
| 密码哈希 | `argon2-cffi`（与 user.py 注释 "Argon2id" 一致；passlib 在 Py3.14 有兼容风险），pyproject 加依赖 |
| 前端 token | localStorage（`mt5quant.*`），`checkSession` 用 refresh 换新；axios 拦截器**单飞 refresh**（并发 401 共用一个刷新 Promise） |
| 错误形状 | 后端 FastAPI `HTTPException detail`；前端 `apiError()` 单点归一化（含 422 detail 数组） |
| TOTP | `UserLogin` 接受可选 `totp_code` 但服务层忽略（留 TODO），本轮不实现 2FA |

## 后端改动

### 新建文件
- `backend/app/models/user_session.py` — `user_sessions` 表：`user_id`(FK CASCADE, index)、`refresh_token_hash`(unique, index)、`user_agent`、`ip_address`(String(45))、`expires_at`、`revoked_at`、`last_used_at`、`relationship("User", back_populates="sessions")`
- `backend/app/schemas/auth.py` — `UserRegister` / `UserLogin`(含可选 totp_code) / `RefreshRequest` / `LogoutRequest` / `UserResponse`(`from_attributes=True`，字段=前端 User 8 字段) / `SessionResponse`(access_token, refresh_token, token_type='bearer', expires_in, user——严格对齐前端 `Session`)
- `backend/app/services/security.py` — `hash_password` / `verify_password`(argon2, 含 needs_rehash) / `create_access_token(user_id, sid)` / `sha256_hex`
- `backend/app/services/auth.py` — `register_user`(email/username 冲突→409；首个用户 admin；注册即登录) / `authenticate_user`(失败统一 401 防枚举；更新 last_login_at) / `refresh_session`(按 hash 查有效行→轮转+顺延 expires_at) / `logout_session`(置 revoked_at，找不到也返回成功) / `get_user_for_me`
- `backend/app/api/v1/endpoints/auth.py` — 五端点：`POST /register`(201)、`POST /login`、`POST /refresh`(body 带 refresh_token)、`POST /logout`、`GET /me`(CurrentUser)
- `backend/app/{schemas,services,api/v1/endpoints}/__init__.py`

### 修改文件
- `backend/app/models/user.py` — 删 `display_name`；增 `username/full_name/role`；删 4 个悬空 relationship（留 TODO）；清理孤儿 import
- `backend/app/models/__init__.py` — 注册 `User`、`UserSession`
- `backend/app/api/v1/router.py` — 启用 `auth_router`（`prefix="/auth"`），其余保持注释
- `backend/pyproject.toml` — dependencies 加 `argon2-cffi>=2.3.0`
- `backend/app/config.py` — 可选：给 `ACCESS_TOKEN_EXPIRE_MINUTES`/`REFRESH_TOKEN_EXPIRE_DAYS` 加 `AliasChoices` 兼容 compose 的 `JWT_*` 环境变量名
- `backend/.env` — 新建：DATABASE_URL 对齐 compose 凭据（`mt5user:mt5password@localhost:5432/mt5_quant`）、≥32 位随机 JWT_SECRET_KEY、CORS 含 `http://localhost:3000`
- `backend/app/dependencies.py` — **不改**

### Alembic
1. winget 装 PostgreSQL 17（交互式设密码）→ 建 `mt5user/mt5password` 角色 + `mt5_quant` 库（**不跑 init.sql**，含 timescaledb 扩展，本地用不到）
2. `alembic init -t async alembic`（`alembic.ini` 已有 `script_location = alembic`，就地初始化）
3. 改写 `env.py`：URL 取 `settings.DATABASE_URL`（`%` 转义 `%%`），`target_metadata = Base.metadata`（`from app.models import Base`），保留 async 模板与 `compare_type=True`
4. `alembic revision --autogenerate -m "initial users and user_sessions"` → `alembic upgrade head`

## 前端改动

### 新建文件
- `frontend/src/lib/api.ts` — axios 实例 `baseURL:'/api/v1'`；token 读写 localStorage（不 import store 防循环依赖）；请求拦截器附 Bearer；响应拦截器 401→单飞 refresh→重放，refresh 失败清 token 跳 `/login`；导出 `apiError(e)`
- `frontend/src/store/useAuthStore.ts` — zustand persist **只持久化 tokens**；state `{user, accessToken, refreshToken, isLoading(初始 true), isAuthenticated}`；actions: `setSession/login/register/checkSession/logout`（checkSession 用 refresh 换新，失败清态置 isLoading=false）
- `frontend/src/pages/Login.tsx` — `.card` + `.input-dark` + `.btn-primary`，成功跳 `location.state?.from ?? /dashboard`，底部链到 `/register`（**default export**）
- `frontend/src/pages/Register.tsx` — username/full_name/email/password/确认密码；成功即 setSession（注册即登录）→ `/dashboard`；409 显示已存在（**default export**）
- `frontend/src/pages/Dashboard.tsx` — 欢迎区 + 占位统计卡，复用 `.card/.card-header/.card-body`（**default export**）
- `frontend/src/components/layout/AppShell.tsx` — **必须含 `<Outlet/>`**（App.tsx layout route 依赖）；左侧边栏 8 项 NavLink 严格对齐 routes.tsx（Dashboard/行情/策略/回测/交易/风控/告警/设置，lucide-react 图标）+ 顶栏当前用户 + 登出按钮

### 不改
`App.tsx`、`routes.tsx`、`types/api.ts`、`main.tsx`。样式只用 `index.css` 既有类与 `tailwind.config.ts` 色板，不引入新设计系统。

## 执行清单

1. venv：`py -3.14 -m venv .venv` → `pip install -e ".[dev]"`（pyproject 同步加 argon2-cffi；若 Py3.14 wheel 缺失则记录报告）
2. winget 装 PG 17 → 建角色/库 → 写 `backend/.env`
3. 改 `models/user.py`（字段 + 删悬空关系）
4. 新建 `user_session.py` + 改 `models/__init__.py` → 验证 `python -c "from app.models import Base"` 无 mapper 错误
5. 新建 schemas / services（security + auth）
6. 新建 endpoints/auth.py + 挂载 router（+ 可选 config AliasChoices）
7. alembic init(async) → 改 env.py → autogenerate → upgrade head
8. 起 uvicorn 跑 curl 全流程（见下）
9. 前端 `npm install` → api.ts → useAuthStore → Login/Register/Dashboard → AppShell
10. `npm run build`（tsc -b）+ 浏览器手测
11. （可选）pytest 认证流测试 + `ruff check app/`

## 验证

**后端 curl（PowerShell）**：
```
GET  /health, /api/v1/ping                          → 200
POST /api/v1/auth/register {email,username,password,full_name}
      → 201，含双 token，expires_in=1800，首个用户 role=admin
GET  /api/v1/auth/me (Bearer)                       → User 8 字段
同 email 再注册 → 409；错密码登录 → 401
POST /auth/refresh → 新 Session；旧 refresh 再用 → 401（轮转）
POST /auth/logout  → 200；该 refresh 再 refresh → 401（撤销）
GET  /docs 目检
```

**前端**：`npm run build` 零类型错误；`npm run dev` → 未登录访问 /dashboard 跳 /login → 注册进 dashboard → 刷新页面仍登录（checkSession 走 refresh）→ 侧边栏导航正常 → 登出回 /login → 停后端刷新应落 /login 而非白屏。
