# SDK v1 API 规范（v1.1.0）

> 文档状态：随 M3「编辑器+管理」切片产出 ｜ 日期：2026-10-10
> 上游依据：`docs/PRD-MT5量化交易平台.md` §M3 ①~⑦（能力边界与契约原则的唯一权威）
> 实现锚点：`shared/sdk/context.py`（IDE 类型面）、`shared/sdk/indicators.py`、`shared/sdk/errors.py`、
> `backend/app/services/strategy_validator.py`（静态校验）、`backend/app/api/v1/endpoints/strategies.py`（管理 API）
>
> **本文只做展开，不放宽 PRD 边界。** 标注「随回测切片（M4）实现」的接口在本阶段仅有类型 stub，
> 沙箱运行时注入尚未落地；编辑器与静态校验已按本规范执行。

---

## 1. 策略骨架与回调契约

```python
def on_init(ctx):          # 必须。初始化：ctx.declare() 声明品种/周期/参数
def on_bar(ctx, bar):      # 必须。每根已收盘 K 线回调（预热期同样调用，预热期下单被拒）
def on_order(ctx, order):  # 必须。成交回报（回测中在 ctx.buy() 调用栈内重入触发）
def on_stop(ctx, reason):  # 可选。平台停止策略时的收尾
def on_tick(ctx, tick):    # 可选。真实 tick 回调（SDK v1.1.0）
```

- `on_tick`（SDK v1.1.0）：**仅在模拟盘/实盘由真实 tick 触发，回测不调用**（编辑器以 W001 info 提示）。
  - 形参 `tick: Tick`（`symbol/time/bid/ask`，forex `last/volume` 恒 0；`time` 为 UTC）。
  - 模拟盘按当前 tick bid/ask 撮合：买入=ask+滑点、卖出=bid−滑点；实盘走真实 broker 成交。
  - `on_tick` 内下单与 `on_bar` 同一套 gate（预热期拒绝、单回调 20 次限频）；`ctx.get_quote`
    返回 tick 真实点差。
  - **无预热期 on_tick 语义**：启动历史回放期间不发送 tick，回放完成后（运行日志出现 started）才触发。
  - 轮询采样间隔约 1 秒（`TICK_POLL_SECONDS`），非券商原生逐笔推送。
- 回调抛出异常 → **终止策略**并保留完整堆栈（M4 沙箱实现；本阶段校验器保证签名 arity：E005）。
- 时间统一 **UTC**；`bar.time` 为**开盘时间**，该 bar 仅在收盘后对 `on_bar` 可见。

## 2. ctx API 面（封闭集合，PRD ①）

### 2.1 声明与元数据

| 成员 | 签名 | 说明 | 状态 |
|---|---|---|---|
| `ctx.declare` | `declare(symbol: str, period: str = "H1", params: dict \| None = None)` | 仅 `on_init` 内调用；重复声明报错；未声明品种的行情/交易一律拒绝 | stub ✅（M4 强制执行） |
| `ctx.symbol` / `ctx.period` / `ctx.params` | 属性 | 声明结果只读视图 | stub ✅ |
| `ctx.mode` | `"backtest" \| "paper" \| "live"` | 仅供日志/防御判断；不鼓励按模式分叉 | stub（M4） |
| `ctx.sdk_version` | `str` | 如 `"1.1.0"` | stub（M4） |
| `ctx.strategy_id` | `str` | 实例唯一标识 | stub（M4） |

### 2.2 行情数据（只读、有界）

| 成员 | 签名 | 硬边界 | 状态 |
|---|---|---|---|
| `ctx.get_bars` | `get_bars(symbol, period, count=100, *, end_time=None) -> list[Bar]` | `count ≤ 5000`；仅已声明品种；**自动截断至当前 bar 收盘**（未来数据契约）；显式 `end_time` 编辑器警告 W002 | stub（M4） |
| `ctx.get_quote` | `get_quote(symbol) -> Quote \| None` | 仅已声明品种 | stub（M4） |
| `ctx.get_symbol_info` | `get_symbol_info(symbol) -> SymbolInfo \| None` | point/volume_step/min/max/contract_size/margin 等 | stub（M4） |

### 2.3 账户与持仓（只读，仅限绑定账户）

`ctx.get_account() -> AccountInfo`、`ctx.get_positions() -> list[Position]`、
`ctx.get_position(id) -> Position | None` —— 跨账户访问 = 严重越权（PRD ③，M4 拦截）。

### 2.4 交易指令（MVP 仅市价）

| 成员 | 签名 | 说明 |
|---|---|---|
| `ctx.buy` | `buy(symbol, volume, *, comment="") -> OrderResult` | 市价开多；手数按 volume_step 向下取整，实际手数经 `on_order` 回传 |
| `ctx.sell` | `sell(symbol, volume, *, comment="") -> OrderResult` | 市价开空 |
| `ctx.close` | `close(position_id, *, volume=None, comment="") -> OrderResult` | 平仓/减仓（按手数，非比例） |

**MVP 不下发券商侧止损/止盈**——退出逻辑全部由策略在 `on_bar` 实现；模板与文档必须明示
「策略崩溃/网关断线期间持仓无任何保护」（见 `atr_stop_management.py` 模板注释）。
单根 K 线下单调数上限 20（超限 `RATE_LIMITED` 拒单，策略可继续）。

### 2.5 状态持久化与日志

- `ctx.storage`：dict 风格键值（`get/[]/del/keys/clear/size_bytes`），配额 1MB；
  **重启恢复契约**：仅 storage 恢复，内存变量由策略在 `on_init` 自行重建。
- `ctx.log.info/warn/error` 与 `print` 同源同契约（≤100 条/秒，超限丢弃并注明）。

### 2.6 时间

`ctx.now`（当前策略时钟，UTC）、`ctx.current_bar`。
**不授予真实时钟**：`datetime.now()/utcnow()/today()` 被校验器拦截（F002/I004）。

## 3. 内置指标库 v1（封闭清单）

`SMA / EMA / WMA / MACD / RSI / KDJ / BOLL / ATR / CCI / ADX / OBV`
（实现：`shared/sdk/indicators.py`；纯函数无状态；清单外指标用 numpy 自行实现。）

## 4. 错误码（三态同一套，PRD ④）

枚举见 `shared/sdk/errors.py`。处置唯一分类：
- **安全性超限**（超时/内存/沙箱越界/非法 import）→ 终止策略 + 告警（实盘），终止前触发 `on_stop`。
- **业务性拒绝**（保证金不足/风控拦截/市场关闭/频率超限/storage 满）→ 结构化错误码返回，策略继续。

| 码 | 触发 |
|---|---|
| `MARGIN_NOT_ENOUGH` | 保证金不足（回测同样校验） |
| `MARKET_CLOSED` | 非交易时段发单 |
| `RATE_LIMITED` | 单 bar 下单调数 > 20 |
| `RESOURCE_TIMEOUT` / `RESOURCE_EXCEEDED` | 回调超时 / 内存超限 |
| `STORAGE_QUOTA_EXCEEDED` | storage 写入超 1MB |
| `SYMBOL_NOT_DECLARED` | 访问未声明品种 |

## 5. import 白名单（PRD ③，封闭集合）

- **标准库**：`math statistics datetime decimal collections itertools functools dataclasses enum typing json re string copy random`
- **第三方**：`numpy pandas scipy`（版本随 SDK 钉死）
- **禁止**：网络/文件/子进程/并发/反射/真实时钟 全部模块（校验器 I001 清单）
- **逃逸入口**（校验器 F003/I004）：`numpy.load/save/loadtxt/memmap/fromfile`、
  `pandas.read_* / to_* / HDFStore / ExcelFile`、`datetime.now/utcnow/today/fromtimestamp/strptime`
- 动态构造调用（如 `getattr(pd, "read_csv")`）**静态校验无法识别**——由 M4 沙箱 import 钩子兜底。

## 6. 静态校验规则表（编辑器 lint + 冻结前置检查）

| 码 | 级别 | 规则 |
|---|---|---|
| E001 | error | 语法错误 |
| E002/E003/E004 | error | 缺少 on_init / on_bar / on_order |
| E005 | error | 回调形参数量不符（on_init=1, on_bar=2, on_order=2, on_stop=2, on_tick=2） |
| I001 | error | 禁用模块 import |
| I002 | error | 非白名单模块 |
| I003 | error | `import *` |
| I004 | error | 白名单模块的逃逸成员 |
| F001 | error | eval/exec/compile/\_\_import\_\_/open/breakpoint |
| F002 | error | 真实时钟调用 |
| F003 | error | numpy/pandas 文件/URL 入口 |
| W001 | info | on_tick 仅在模拟盘/实盘触发，回测不调用 |
| W002 | warning | get_bars 显式 end_time |
| W003 | warning | 模块顶层可执行语句 |
| W004 | info | 缺少可选 on_stop |

`POST /api/v1/strategies/validate` 永远 200，返回 `{ok, issues[]}`；
`POST /strategies/{id}/versions` 冻结前校验，error 级返回 422 + 同一结构体。

## 7. 管理 API（本切片已实现）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/strategies` | 创建（201；同名 409；mql5 422；可携 `template_id` 初始化源码） |
| GET | `/strategies` | 分页列表（page/page_size/status/q；**不含 source_code**） |
| GET | `/strategies/{id}` | 详情（含工作副本 source_code） |
| PATCH | `/strategies/{id}` | 元数据/状态更新 |
| PUT | `/strategies/{id}/source` | 保存工作副本（乐观锁 `base_updated_at` → 409；archived → 409；返回 validation） |
| DELETE | `/strategies/{id}` | 删除（204，版本级联） |
| GET/POST | `/strategies/{id}/versions` | 版本列表 / 冻结新版本（校验 422、无变化 409） |
| POST | `/strategies/{id}/versions/{v}/rollback` | 回滚 = 产生新版本号（单调递增；目标=当前 → 409） |
| POST | `/strategies/validate` | 独立 AST 校验（永远 200） |
| GET | `/strategies/templates[/{id}]` | 模板库（列表不含源码） |

## 8. 编辑器补全词表

唯一维护点：`frontend/src/lib/sdkCompletion.ts`（词表与本规范 §2/§5/§6 同步，
新增/废弃接口必须先改本规范再改词表）。

## 9. 随回测切片（M4）实现清单

- 子进程沙箱 + import 钩子（白名单判定以「策略顶层可 import 的模块」为单位；
  第三方库内部依赖不受限，但其对外逃逸入口按 §5 逐一处置）
- 资源限额执行（回调时长/内存/下单调数/storage 配额/日志限速）
- 撮合三档、未来数据截断的运行时执行、`on_order` 重入、随机种子固定、BLAS 线程=1
- `compiled_at / compile_errors` 字段填充（表结构已预留）

## 10. 演进承诺（PRD ⑥）

语义化版本；minor 只增不改；破坏性变更需 major + 6 周弃用公告期；
弃用接口在编辑器删除线提示并给出替代写法。
