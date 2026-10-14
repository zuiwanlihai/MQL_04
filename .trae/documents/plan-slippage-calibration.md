# M4 真实滑点校准 + 回测-实盘偏差报告 — 实现计划

## Context

PRD M4 预期落差管理两项 P1 遗留：

- **真实滑点校准**：模拟/实盘运行期统计该账户实际成交滑点，自动回写为该账户回测的默认滑点参数，使回测口径随使用逐步收敛于用户真实环境
- **回测-实盘偏差报告**：策略转实盘后，持续对比同期信号的理论成交与实际成交，量化偏差来源（滑点/点差/延迟）

### 现状调研结论

- **滑点定义（MT5 deviation 口径）**：下单请求时报价 vs 实际成交价之差。buy: `fill − ask@request`；sell: `bid@request − fill`。网关 `_place_market_sync`/`_close_position_sync` 已在请求前取 `symbol_info_tick`（[mt5_client.py:861](../../gateway/app/mt5_client.py)），但 `_trade_result` 只回 `price/deal/order/volume`，**请求时 bid/ask 没有回传**——需扩展
- **硬约束**（project_memory）：`_trade_result` 返回 dict 不得附加 TradeResult schema 之外的键（`extra="forbid"`，成功 2xx 会过响应校验）——**schema 与 dict 必须同步扩展**，新增可选字段（默认 None），既有 `TradeResult(**out)` 测试断言不受影响
- **理论成交价来源**：子进程 `LiveEngine.request_order` 已随 order_request 传 `bar_time`（信号 bar 收盘），但**信号 bar 收盘价与点差未传**——child 侧 bars.close/spread[index] 现成，补两个字段即可
- **落库点**：parent `_record_deal` 写 LiveTrade（price=真实成交价），无请求快照/信号价列——需加列
- **LiveTrade 无 symbol 列**：校准按品种聚合走 join live_runs.symbol；point 值从 `run.config_snapshot.symbol_info.point` 取
- **模拟盘不参与校准**：paper 成交是 FillModel 模拟产物（滑点=配置值本身），纳入统计是循环论证——**校准仅统计实盘成交**（PRD「模拟/实盘」中的模拟盘部分在纸面上由三态一致架构保证，此处取真实信号源）
- **校准结果不建快照表**：compute-on-read（market_quality 先例）——成交落库即数据就绪，查询即最新，避免快照过期与 watcher 状态同步

### 设计决策

1. **偏差分解完全自洽**（buy 方向）：`actual − theoretical = (price−ask) + ask−bid + (bid − signal_price − signal_spread×point)` = **滑点 + 实际点差 + 请求时漂移（延迟）**；sell 方向 bid 口径无点差成本，`actual − theoretical = (bid−price) + (signal_price − bid)` = 滑点 + 漂移
2. **推荐滑点 = ceil(p95)**（保守取向，宁高勿低）；样本 <5 笔返回 `insufficient`（前端显示「样本不足」不强填）
3. **校准聚合维度 = 用户 + 品种**（该用户全部 MT5 账户的实盘成交合并，sources 列出账户明细）——回测不绑定账户，「该账户回测」落地为回测对话框打开时按品种自动预填推荐值（用户可见可改，即「默认滑点参数」）
4. **偏差报告 per live run**：GET /live/{run_id}/deviation，逐笔 + 汇总，compute-on-read
5. 路由顺序硬约束：`GET /backtests/slippage-calibration` 必须注册在 `GET /backtests/{task_id}` 之前（uuid 捕获教训）；`/{run_id}/deviation` 注册在 `GET /{run_id}`（697 行）之前

## 一、网关（gateway/）

### gateway/app/schemas.py — TradeResult 扩展 3 个可选字段
```
requested_price: float | None = None   # 请求价（buy=ask / sell=bid）
bid: float | None = None               # 请求时 bid
ask: float | None = None               # 请求时 ask
```

### gateway/app/mt5_client.py — _trade_result 扩展
- 签名改为 `_trade_result(res, tick=None)`：tick 非 None 时填 `requested_price = ask(buy)/bid(sell)`…… 職责划分调整：`_trade_result` 保持静态纯函数，改为在 `_place_market_sync`/`_close_position_sync` 调用处 `out.update(...)` 附加三字段（tick 变量已在作用域内，零额外 MT5 调用）
- `_close_position_sync` 同样捕获（closing side：平多=sell 平空=buy）

### gateway/tests — test_trade 扩展
假 MT5 模块的 tick 对象补 bid/ask 字段；断言 TradeResult(**out) 构造通过 + 三字段值正确；不传 tick 的路径（如有）字段为 None。

## 二、后端（backend/）

### app/models/live.py — LiveTrade 加 5 列 + alembic 迁移
```
requested_price     NUMERIC(18,8) NULL  # 请求价
bid_at_request      NUMERIC(18,8) NULL  # 请求时 bid
ask_at_request      NUMERIC(18,8) NULL  # 请求时 ask
signal_price        NUMERIC(18,8) NULL  # 信号 bar 收盘价（bid）
signal_spread_points NUMERIC(12,2) NULL # 信号 bar 点差（points）
signal_bar_time     DateTime(tz) NULL   # 信号 bar 收盘时间（延迟度量）
```
全部 nullable（reconcile 补录/历史行无这些数据）。迁移 down_revision=当前 head。

### app/sandbox/live_engine.py — order_request 补 2 字段
`request_order` 的 payload 增加 `signal_price = float(bars.close[index])`、`signal_spread = int(bars.spread[index])`（index<0 时 None）。

### app/services/live/manager.py — 链路透传
- `_handle_order_request` 取 `req["signal_price"]/["signal_spread"]/["bar_time"]` 传给 `_execute_at_gateway` → `_record_deal`
- `_record_deal` 落库新列；`requested_price/bid/ask` 从 gw 响应取（网关新字段，向后兼容 .get 容错旧网关）

### app/services/slippage.py — 新建（核心计算，纯函数+DB 聚合）
- `deal_slippage_points(deal, point)`：buy: `(price−ask)/point`；sell: `(bid−price)/point`；缺请求快照 → None（不计入）
- `calibration_for_symbol(db, user_id, symbol)`：join live_runs（user+symbol）取 LiveTrade（in+out 都算执行样本），分组输出 `{sample_count, mean/median/p75/p95/max(points), recommended_points, slippage_cost, window_start/end, insufficient, sources:[{account_id,account_label,run_count,sample_count}]}`；account label 脱敏用 mt5_accounts.login 尾号（沿用现有展示惯例）
- `deviation_report(db, run_id)`：逐笔 `{deal_ticket,direction,entry,volume,signal_bar_time,signal_price,signal_spread_points,theoretical_price,actual_price,total_deviation_points,slippage_points,actual_spread_points,spread_delta_points,drift_points,latency_seconds,cost}` + 汇总 `{sample_count,avg/median/p95 total_deviation, avg slippage, avg spread (signal vs actual), avg latency, total_slippage_cost, verdict}`；verdict 阈值：avg_slippage ≤2 aligned / ≤10 moderate / >10 divergent（points）
- 防御：point 缺失（config_snapshot 无 symbol_info）的行跳过并计数

### app/schemas/slippage.py — 新建
SlippageCalibrationOut / DeviationTradeOut / DeviationReportOut，extra="forbid"。

### app/api/v1/endpoints/backtests.py — 1 端点（注意路由顺序！）
`GET /backtests/slippage-calibration?symbol=X`（登录用户，symbol 必填）→ SlippageCalibrationOut。**注册在 GET /{task_id} 之前**。

### app/api/v1/endpoints/live.py — 1 端点
`GET /live/{run_id}/deviation`（owner 校验同 /{run_id}）→ DeviationReportOut。注册在 GET /{run_id} 之前。

### 后端测试
- `tests/test_slippage_calibration.py`：构造 live_run + LiveTrade 行矩阵（buy/sell × 有/无请求快照 × 多点差），断言滑点公式、p95 推荐值、样本不足 insufficient、跨用户隔离、out 单计入；`/backtests/slippage-calibration` 端点 200/401/422
- `tests/test_live_deviation.py`：分解自洽性断言（滑点+点差+漂移=总偏差，buy）；verdict 三档；signal_price 为空行跳过；run 归属 404

## 三、前端（frontend/）

### types/api.ts — 类型
SlippageCalibration / DeviationReport / DeviationTrade。

### api 层 + hooks
`fetchSlippageCalibration(symbol)` / `fetchRunDeviation(runId)`。

### BacktestCreateDialog — 校准预填
- 打开时若 symbol 已选 → 拉校准；`recommended_points` 非 null 且用户未手动改过滑点框 → 预填 + 徽标「已按实盘校准 · 样本 N · p95 X 点」；insufficient → 显示「样本不足（N 笔）」灰字；请求失败静默（不阻塞表单）
- PaperCreateDialog 同样预填（同 symbol）

### LiveDetailPage — 「执行偏差」卡片
- 汇总区：样本数、平均/中位/p95 总偏差、平均滑点、点差对比（信号 bar vs 实际）、平均延迟、滑点总成本、verdict 徽标（绿 aligned / 黄 moderate / 红 divergent）
- 逐笔表：时间/方向/手数/信号价/理论价/实际价/滑点(points)/点差(points)/延迟(s)/偏差成本——沿用现有 trades 表格风格
- run 无可比样本（signal_price 空）→ 显示「暂无含信号基准的成交」空态

## 四、门禁与验证

1. `ruff check .`（backend）
2. 网关 pytest（gateway/ 目录，backend\.venv）
3. 后端全量 pytest
4. `npm run build`（tsc 零错误）
5. 浏览器 E2E（vite 3001，localStorage 登录绕过）：
   - dev 库构造种子 LiveTrade 行（带 signal/requested 快照）→ LiveDetailPage 偏差面板渲染数值正确 → 清理种子
   - 回测对话框打开 → 校准徽标与预填值出现 → 手动改值不被覆盖 → symbol 无样本时灰字提示
6. 重启 backend + gateway（改了双方代码）

## 五、明确不做（scope 边界）

- 不做快照校准表（compute-on-read 即可，避免过期数据）
- 不做模拟盘成交校准（模拟滑点是配置值本身，循环论证）
- 不做「真实点差」第三档 spread_mode（剩余清单另一独立项，不混入本切片）
- 不后端强制覆盖用户滑点配置（只做默认值预填，显性可见）
