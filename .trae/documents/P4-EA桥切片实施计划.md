# P4 · EA 桥（方案 B）切片实施计划

> 日期：2026-10-12 ｜ 依据：PRD §5.1 方案 B「MQL5 EA 桥接：可用轻量 EA 替代自建 Python 服务、降低同机耦合」+ §6 表格「备选/Phase 2 补充 tick 级推送」
> 用户已拍板：Phase 4 主线 = EA 桥（其余候选：多账户分发、SaaS 前置、运维可靠性包未选）

## 1. 概述与价值

MT5 终端内运行轻量 EA（MQL5），以 **WebRequest(HTTPS) 同步回环**与后端直连：

- **tick 上行推送**：EA OnTick/OnTimer 采集 → POST 给后端 → 喂 runner（on_tick 链路），消除 Python 网关 1s 轮询粒度
- **替代 Python 网关进程**：EA 节点机器无需安装 Python 服务/看门狗/服务注册，只需 MT5 终端 + EA（运维面大幅收窄）
- **命令下行**：后端把已过风控闸的指令搭车 sync 响应下发，EA 执行 MQL5 交易函数；结果下一轮回传

**定位（不变）**：Python 网关仍是默认（方案 A），EA 桥为**补充通道**，双通道并存、按节点选型。
**不做**：托管模式（凭据托管需安全评审+法务，另立专项）、EA 节点上的看门狗/自检/服务常驻（EA 无进程管理能力，UI 标注能力差异）、多账户分发。

## 2. 传输选型（已定）

| 候选 | 结论 |
|---|---|
| MQL5 原生 Socket*（TCP 裸帧） | ❌ 无 TLS，明文穿公网不可接受 |
| ZeroMQ / WebSocket | ❌ MQL5 无原生支持，需第三方 DLL，违背「轻量 EA」与打包分发 |
| **WebRequest (HTTPS)** | ✅ 原生、TLS、零依赖；同步阻塞 + 终端需白名单 URL（用户设置项，文档写明）；命令延迟上界 = EA 轮询周期（EventSetMillisecondTimer，默认 500ms，优于现轮询 1s） |

协议帧：JSON 请求/响应体（HTTPS POST），EA ↔ 后端双向对等——上行 tick/账户快照/命令结果，下行命令数组。

## 3. 现状核查（已确认）

- 后端 tick 喂 runner 协议：`{"cmd":"ticks","rows":[[time_msc,bid,ask],...]}`，`TICK_MAX_ROWS_PER_SEND=2000`；paper/live 两条 `_tick_poll` 各自实现（游标 `last_tick_msc`，`time_msc > cursor` 过滤）→ 抽公共 helper `services/tick_feed.py` 单点分支
- `GatewayNode` 无 type 列；`UniqueConstraint(host,port)` 对 EA 节点无意义（EA 无监听端口）→ type 列 + host 填终端标识、port=0
- 配对机制可复用：`POST /gateway/pair-codes`（admin，一次性 8 位码 10min TTL）+ `pairing.py` CLI 模式 → EA 侧改为「终端全局变量存 node_key」
- MT5 已知坑（MQL5 侧必须重做）：① `copy_ticks_range` 省略 flags 静默返回 None → EA 必须显式 `COPY_TICKS_ALL`；② tick 时间轴=服务器时区 → EA 本地 skew 自洽归一化（`SymbolInfoTick().time - TimeCurrent 差值`方案在 EA 内天然可用 `TimeTradeServer()`）
- 风控不动：事前风控/事前订单检查仍在后端，EA 只是执行器，收到即执行，无自主下单
- `gateway_route` 统一解析账户→节点；EA 节点账户同样走该入口（type 判断处扩展）
- 审计：`ea.node_pair` / 交易类沿用现有 action；tick 摄入**不逐条审计**（噪声）

## 4. 协议设计（三端点）

| 端点 | 语义 |
|---|---|
| `POST /api/v1/ea/pair` | `{pair_code, mt5_login, mt5_server, terminal_info}` → 一次性码校验 → upsert 节点（type=ea_bridge, host=`ea:{mt5_server}:{login}`, port=0）→ 返回 `node_key`（随机 32B hex，存节点 metadata 密文列？MVP 明文哈希存库、EA 端 GlobalVariable 持久化） |
| `POST /api/v1/ea/sync` | `X-EA-Key: node_key`；上行 `{cursor_last_msc, ticks:{sym:[[t,b,a]...]}, account?:{...}}` → 返回 `{commands:[{type,req_id,...}], next_wait_ms}`；tick 摄入注册表缓冲（按 node+symbol deque，容量上限丢弃最旧+计数告警） |
| `POST /api/v1/ea/result` | `X-EA-Key`；`{req_id, ok, payload?, error?}` → 匹配 pending future（超时 30s 自动 fail，复用 req_id RPC 模式） |

命令类型（分期落地）：`rates_req`（EB-2）、`account_req`（EB-2 可选）、`order_request`/`close_all`（EB-3，**已过后端风控闸**才入队）。

## 5. 切片划分（4 个，串行推进）

### EB-1 · 协议骨架 + EA 程序 + tick 上行（模拟盘 on_tick 先通）
- 后端：迁移加 `gateway_nodes.type`（CHECK `IN ('python','ea_bridge')`，**三处同步**）；`services/ea_bridge.py`（EARegistry：会话/缓冲/命令队列/futures）；三端点 + `EA_BRIDGE_ENABLED` 灰度开关；`tick_feed.py` 抽公共取数 helper（EA 节点 run 读缓冲，同游标语义）
- MQL5：`deploy/ea/EAQuantBridge.mq5`——输入框（后端 URL + 配对码）、pair→GlobalVariable 存 key、OnTimer(500ms) 同步回环、copy_ticks 游标采集 + skew 归一 + COPY_TICKS_ALL、断线指数退避（1s→30s 上限）恢复自动补追
- 守卫：EA 节点账户创建模拟盘/实盘暂 409「EB-2 开放」（tick 链路用假 EA 单测 + 真子进程测试覆盖，复用 test_paper_runner_started_gate_and_ticks 手法）
- 门禁：ruff + pytest（新增 ~12 用例：pair 流程/坏码 409/key 认证 401/游标去重/skew 归一/缓冲溢出丢弃/开关关闭 404）+ 浏览器冒烟（节点列表出现 ea_bridge 类型）

### EB-2 · rates RPC + 模拟盘完整数据面
- `rates_req` 命令：后端启动快照/补数请求入队 → EA `copy_rates` 分页应答（窗口切窗复用 BACKFILL 语义的简化版：EA 侧只按请求范围返回，切窗逻辑留在后端）
- paper manager 启动取数分支：EA 节点经 RPC 拉最近 N 根（**必须用「最新 N 根」语义**，禁固定日历窗——休市坑），warmup 回放照旧
- 解除 409 守卫；账户镜像：EA sync 上行 account 快照 → `_write_account` 同构（防漏赋值坑：对照 paper manager account 分支逐字段核对）
- 门禁：pytest（rates RPC 成功/超时/EA 掉线 fail-fast/启动快照语义）+ 真终端 demo 账户模拟盘全链路冒烟（市场开盘时）

### EB-3 · 交易指令下行
- 命令通道：`order_request`（req_id + 完整指令参数）入队条件=**后端风控闸已过**（evaluate 通过才入队）；EA 端 `CTrade` 执行 → result 回传 → manager `_record_deal` 路径不变
- Kill Switch / close_all：插入队首，**命令延迟上界=EA 轮询周期**（文档/UI 标注 vs Python 网关 ≤3s 的差异）；EA 侧执行前二次校验 magic/symbol 防串单
- 手动交易面板对 EA 节点账户的可用性评估（M14 链路同样命令化，若改动大则本切片 409 暂不支持并标注）
- 门禁：pytest（下单往返/close_all 优先级/EA 掉线时订单 fail-fast/风控未过不入队）+ demo 账户真实下单冒烟

### EB-4 · 前端 + 分发 + E2E 收尾
- 前端：节点管理页 type 徽标（python/ea_bridge）+ EA 接入向导抽屉（配对码生成 → 终端白名单 URL 设置图文 → EA 参数填写说明）；节点能力差异说明（无看门狗/自检项置灰）
- 分发：`deploy/ea/`（EAQuantBridge.mq5 + 安装指引 README）；installer 可选携带
- 浏览器 E2E：admin 生成配对码 → （真终端挂 EA）节点上线 type=ea_bridge → 绑账户 → 模拟盘跑 on_tick → 报告页正常 → 停止收尾
- 门禁：tsc build + vitest + E2E 五步全过 + 审计页有 ea.node_pair 记录

## 6. 验收门禁（每切片通用）

- `alembic current` 达当切片 head；模型 CHECK + 迁移 + 元数据测试三处同步
- backend `ruff check app tests` + pytest 全量绿；前端变更切片加 tsc build + vitest
- 真实 MT5 冒烟受休市阻塞时：以真子进程/假 EA 测试闭环 + 次日开盘补冒烟（沿用 on_tick 切片先例）
- 审计相对增量断言；audit_record 不传请求 db

## 7. 关键坑位备忘（本专项适用）

- `gateway_nodes.type` CHECK 枚举三处同步（模型/迁移/元数据测试）——backtest_analyses.kind 曾实测漂移
- EA WebRequest 白名单是**用户终端设置**（工具→选项→EA），指引必须写进向导，否则 EA 静默 4014 错
- WebRequest 同步阻塞单线程：EA OnTick 事件队列会积压——采集用 OnTimer 轮询 copy_ticks 游标而非 OnTick 计数，慢网下 tick 不丢（游标补追）
- `copy_ticks_range` 必须显式 flags=COPY_TICKS_ALL（缺省静默 None）；时间轴 skew 归一化（服务器时区坑复刻）
- order 指令 result 的 dict 绝不能附加调试键（TradeResult extra="forbid" 响应校验 500 坑）
- EA 节点 port=0：`uq_gateway_nodes_host_port` 不受影响；gateway_route/心跳/健康轮询对 ea_bridge 节点**跳过 HTTP 探活**（改为看 sync 最后活跃时间）——lifespan 心跳循环需按 type 分支，防 5s 一轮对 EA 节点 408 刷日志
- node_key 是节点凭据：不进日志、不进前端响应；pair 响应一次下发后仅存终端 GlobalVariable
- 单终端单账户约束对 EA 节点同样成立（host 编码 login 天然防重）
