# on_tick 策略回调切片实施计划

> PRD M3 P1 能力（「该能力随 Tick 数据管理（P1）引入」——Tick 数据管理已交付，本切片兑现 on_tick）。
> 调研结论与方案决策基于对 SDK / 三态引擎 / 网关 / validator 的实读核实。

## 范围与 PRD 口径（docs/PRD-MT5量化交易平台.md L255-263）

- **回测不调用 on_tick**（保持 bar 驱动，零改动）；**模拟盘/实盘用真实 tick 触发**；策略感知、文档明示。
- 网关侧零改动：`GET /symbols/{name}/ticks/range`（routes/market.py L85，limit≤200000，行=[time_msc,bid,ask,last,volume,flags] 升序）与 `GET /quotes` 已存在；后端 `GatewayClient.ticks_range`（services/gateway_client.py L189）已封装。
- 明确不做：回测 tick 级回放撮合、tick 历史 get_ticks API、网关主动推送（轮询采样已满足）、前端 tick 延迟监控。

## 关键决策

1. **协议字段**：`{"cmd":"ticks","rows":[[time_msc,bid,ask],...]}`（forex last/volume 恒 0 不传，省 40% 带宽）；SDK `Tick` dataclass 保留 last/volume 可选字段备未来。
2. **tick 轮询**：独立 asyncio task（不并入 `_poll`——bar 轮询 10s 对 tick 太慢），`TICK_POLL_SECONDS=1`；单线程事件循环顺序 `h.sup.send` 天然保证与 bars 消息有序。
3. **游标**：`Handle.last_tick_msc` 仅内存、启动置 now_ms（「on_tick 从运行起点触发」，重启丢 <1s 尾巴可接受）；返回行数==limit 时以末行 msc 推进游标续追；单次发送分块 `TICK_MAX_ROWS_PER_SEND=2000` 防 stdin 超长行。
4. **has_on_tick**：validator 导出 `has_callback(source,"on_tick")`；paper/live 部署端点构建 payload 时计算一次写入 `BacktestPayload.has_on_tick` + Handle；父进程凭此决定是否起 tick task，子进程 `_load_strategy` 自查 module_dict 双保险。
5. **tick 撮合（模拟盘）**：按当前 tick bid/ask 成交（buy=ask+滑点、sell=bid−滑点，符合 PRD「实时行情撮合」）；实现最小化——engine 记 `_last_tick`，`_normal_fill` 开头按 `phase=="on_tick"` 分支拦截，**不改 FillModel 签名**。实盘本走真实 broker RPC 零改动。
6. **W001 → info**：文案改「on_tick 仅在模拟盘/实盘触发，回测不调用」（原「不会被调用」语义已不成立）；info 保留回测覆盖度提示。
7. **预热期**：模拟盘 warmup 历史 bar 回放完（`eng.index >= warmup_end`、`self.live=True`）后 tick task 才开始发送（父进程侧 gate），无预热期 on_tick 语义，文档明示；`_order_gate` warmup 检查照常兜底。
8. **on_order 重入**：`_call_on_order` 已保存/恢复 phase（engine.py L350），无需改动；仅需 `_order_gate`（context.py L242-244，现只放行 on_bar/on_order）加放行 `"on_tick"`，且每 tick 重置 `_orders_this_bar`（复用单回调限额）。
9. **未来数据契约**：`eng.bars` 本就只含已收盘 bar，on_tick 中 get_bars 天然只见已收盘数据；ctx.get_quote 在 on_tick 时按 `_last_tick` 构造真实 spread Quote。

## 分步实施

### Step 1 — SDK（shared/sdk/context.py、shared/sdk/__init__.py、backend/app/sandbox/context.py）
- 新增 `@dataclass Tick: symbol/time(UTC datetime)/bid/ask/last=0.0/volume=0.0`（置于 Quote 后）；`StrategyContext` docstring 更新「Bar 驱动 + paper/live on_tick，回测不调用」。
- `__version__ = "1.1.0"`，核实 sdk_version 属性引用点。
- 后端 context.py：`get_quote`（L206）加 `_last_tick` 分支（tick 时间新于当前 bar → 真实 spread Quote）；`_order_gate` 放行 `"on_tick"`。

### Step 2 — validator（backend/app/services/strategy_validator.py）
- L216-220 W001 改 info 级 + 新文案；CALLBACK_ARITY 加 `"on_tick": 2`（E005 形参校验自动生效）；新增模块级 `has_callback(source, name) -> bool`（复用 `_CallbackVisitor` 扫描逻辑）。

### Step 3 — 引擎（backend/app/sandbox/engine.py、paper_engine.py、live_engine.py）
- BacktestEngine：`_load_strategy` 检出 `self._has_on_tick`；新增 `_last_tick: tuple[int,float,float]|None`；新增 `feed_ticks(rows)` 生成器——逐 tick：置 `_last_tick` → `phase="on_tick"` → 重置订单计数 → `_call("on_tick", ctx, Tick, timeout=callback_timeout_seconds)`；异常分支与 `_process_bar` 一致（`_fail(..., call_stop=True)` → `_halted`）；不追加 bar、不做估值/stop-out/swap。
- `_normal_fill`（L203）加 tick 分支（决策 5）。
- PaperEngine/LiveEngine 各加 `feed_ticks` 包装：halted/空 bars 早退、批次末 `_persist_storage()` + `account_event()` + `_drain()`（不逐 tick persist）。

### Step 4 — runner + payload（paper_runner.py L101、live_runner.py L164、payload.py）
- 各加 `elif name == "ticks": _emit_all(..., engine.feed_ticks(rows))` 分支（fail → EXIT_SAFETY）。
- `BacktestPayload` 加 `has_on_tick: bool = False`（dataclass 默认值向后兼容旧 payload.json）。

### Step 5 — manager + 配置（services/paper/manager.py、services/live/manager.py、config.py）
- config 加 `TICK_POLL_SECONDS: int = Field(default=1, ge=1)`、`TICK_MAX_ROWS_PER_SEND: int = 2000`。
- Handle 加 `last_tick_msc: int`、`has_on_tick: bool`；`start()` 时若 payload.has_on_tick 创建 `_tick_poll(run_id)` task：sleep(TICK_POLL_SECONDS) → 检查 handle 替换/paused/stopping/live 阶段 gate → `client.ticks_range(symbol, last_tick_msc//1000, now, limit=200000)`（接口秒入参/毫秒返回，游标按 msc 过滤 `time_msc > last_tick_msc`）→ 裁剪 [msc,bid,ask] 分块 send → 推进游标；GatewayError 记 warning 降频日志 continue（休市/网关离线容错）；send 失败 return；`finally: await client.aclose()`；`stop()`/handle 清理处 cancel。

### Step 6 — 前端/文档
- frontend/src/lib/sdkCompletion.ts：回调补全加 on_tick；确认校验结果 info 级展示正常。
- docs/SDK-v1-API规范.md：W001 条目改口径、补 Tick/on_tick 签名、版本 1.1.0、明示「回测不调用、无预热期、tick 期间下单模拟盘按 tick 价/实盘真实成交」。

### Step 7 — 测试（backend/tests/）
- validator：on_tick 形参 E005、W001 info 文案、has_callback true/false。
- paper engine：feed_ticks 顺序触发、异常→fail+halted、on_tick 内 buy 按 ask 成交、get_quote 返回 tick 价、无 on_tick 空转。
- live engine：feed_ticks 触发 + 下单走 RPC。
- runner：stdin "ticks" 分发（mock engine）。
- manager：mock GatewayClient.ticks_range → 发出 ticks 消息、limit 截断游标推进、GatewayError 容错、无 has_on_tick 不起 task。

## 门禁与 E2E

- 后端 ruff + pytest 全量；前端 tsc build + vitest。
- 浏览器 E2E：创建含 on_tick 的策略 → 编辑器显示 info 提示（非 warning）→ 部署模拟盘 → 运行日志出现 tick 回调与按 ask 成交记录 → 停止；同策略跑回测 → 无 tick 相关行为、报告正常。

## 硬约束复检
- 实盘子进程单线程读 stdin：ticks 消息经 Transport._inbox 分流（live_runner L74-116），与 order_result 的 req_id 匹配互不干扰（顺序处理）。
- 消息行长度：分块 2000 行 < stdout 管道 1.1MB 上限既有教训口径。
- payload dataclass 新字段带默认值，旧 payload.json 兼容。
