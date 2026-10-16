# 可视化策略构建器 P1 服务端可信校验 实施计划

## Repository Research

### 当前 gaps（需要 P1 填补）

| 层 | 位置 | 现状 | P1 要做的 |
|----|------|------|-----------|
| **后端 schema** | `schemas/strategy.py` StrategyBuilderSave | `builder_config: dict` — 任何 dict 都收 | → Pydantic v1 DSL model 覆盖 |
| **后端端点** | `strategies.py` builder-save | 直接 `strategy.builder_config = payload.builder_config` | → 先验 DSL，error 级 AST 拒绝 |
| **后端端点** | `strategies.py` builder-save | 没有调 `validate_source()` | → 跑 AST 校验，error 级拒绝 |
| **后端端点** | `strategies.py` create | source_code + builder_config 直接写 | → 同样跑 DSL + AST 校验 |
| **前端校验** | `codegen.ts` validateIndicators/validateRules | 有 15+ 条具体校验规则（MACD fast/slow/signal 范围、BOLL std>0、指标 id 重复、悬空引用、输出通道不兼容、方向冲突） | → 后端重写等价逻辑（可以简化，不需要生成代码能力，只要校验 DSL） |
| **AST validator** | `services/strategy_validator.py` validate_source() | rollback_version 调用了（有错误时 422）；save_source 调用了（返回 validation 报告但不拒绝）；**builder-save 没调** | → builder-save 也要调，error 级拒绝 |

### 前端校验规则清单（后端必须等价覆盖）

| # | 规则 | 前端 codegen 位置 |
|---|------|-------------------|
| 1 | version 必须是 1 | types.ts 定义 |
| 2 | symbol 非空 | codegen L283 |
| 3 | timeframe 非空 | codegen L285 |
| 4 | volume > 0 | codegen L284 |
| 5 | 指标 id 不重复 | codegen L44 |
| 6 | 指标 kind 是 MA/EMA/RSI/BOLL/ATR/MACD | codegen L46 |
| 7 | MACD fast ≥ 2、slow > fast、signal ≥ 2 | codegen L48-51 |
| 8 | 非 MACD 指标 period ≥ 2 | codegen L52-53 |
| 9 | BOLL std > 0 | codegen L55-56 |
| 10 | 规则至少一个动作（open 或 close） | codegen L70-71 |
| 11 | direction=long_only 时不能有 sell 动作 | codegen L73-74 |
| 12 | direction=short_only 时不能有 buy 动作 | codegen L76-77 |
| 13 | 指标操作数不能引用不存在的指标 | codegen L83 |
| 14 | 指标输出通道必须在该指标的合法 outputs 里（MA→value only, BOLL→upper/mid/lower, MACD→dif/dea/hist 等） | codegen L85 |

## Files and Modules

### 新增
| 文件 | 作用 |
|------|------|
| `backend/app/services/builder_validator.py` | 版本化 DSL 校验器（BuilderConfig → 错误列表）；纯函数，等价于前端 codegen.ts 的 validateIndicators + validateRules |

### 修改
| 文件 | 改动 |
|------|------|
| `backend/app/schemas/strategy.py` | StrategyBuilderSave / StrategyCreate 的 `builder_config: dict` → 加 `Annotated[dict, ...]` 或保留 dict 但在端点里手动调 builder_validator（避免 Pydantic model 和前端 TS 类型漂移） |
| `backend/app/api/v1/endpoints/strategies.py` | create / builder-save / update 都调 `validate_builder_dsl()` + `validate_source()`；错误级 DSL 拒绝 422，error 级 AST 也拒绝 422 |
| `backend/tests/test_p0_builder_save.py` → 改名 `test_strategy_builder.py` | 扩为 P0+P1 验证；新增非法 DSL 被 422 拒绝、未知 version 被拒绝、AST error 被拒绝 |

### 决定：DSL schema 用 dict + 手动校验 vs Pydantic model

**选 dict + 手动校验**，原因：
- 前端 TS types 和后端 Python Pydantic model 容易漂移（字段名/枚举值/可选 vs 必填）
- 前端已经有完整 codegen 校验链，后端做**防御性校验**而不是**类型镜像**
- 只需要 `builder_validator.validate(dict) -> list[str]`，与前端 validateIndicators/validateRules 等价
- 如果以后要升级到 v2，validator 里加 `if version == 2: validate_v2()` 即可

## Implementation Steps

### Step 1: 创建 builder_validator.py
纯函数模块，暴露：
```python
def validate_builder_config(config: dict) -> list[str]:
    """返回错误列表；空列表 = 合法。不抛异常。"""
```
实现前端 codegen.ts 全部 14 条校验规则。测试直接 import 调。

### Step 2: builder-save + create 端点加校验
```
validate_builder_config(payload.builder_config) → 非空 → HTTPException 422
validate_source(payload.source_code) → report.ok == False → HTTPException 422
```
注意：save_source 当前**只返回 validation 不拒绝**（返回 200 + validation 字段）。P1 让 builder-save 和 create **更严格**——error 级 AST 直接拒绝保存。

### Step 3: create 端点也加 DSL 校验
当前 create 直接把 builder_config 写 DB，如果前端传了非法 dict 也会被接受。加 validate_builder_config。

### Step 4: update 端点（PATCH）加 DSL 校验
StrategyUpdate.builder_config 是 Optional，显式传 dict 时也要校验。

### Step 5: 测试覆盖
扩 test_p0_builder_save.py 为 test_strategy_builder.py，新增：
- 非法 DSL（MACD fast=1、period=0、悬空 indicatorId、重复 id、direction 冲突）→ 422
- 未知 version=99 → 422
- builder-save + 非法源码（AST error）→ 422
- 合法 DSL + 合法源码 → 200（回归 P0 测试）

### Step 6: 后端全量测试
`pytest tests/ -q` 确保没破坏现有 570 个测试。

## Dependencies and Considerations

- **不生成代码**：后端 builder_validator 只校验 DSL，不尝试从 DSL 生成源码。源码一致性通过 SHA-256 + AST 白名单来兜底——如果源码里有 DSL 生成不出来的内容（AST error），AST 检查会拒绝。
- **AST 白名单**：validate_source 已有白名单机制（禁止 import os/eval 等），P1 利用现有能力而不是重写。
- **rollback_version 也要加 DSL 校验**：rollback 会把旧版本 source_code 恢复回去，但旧版本的 builder_config 可能非法（如果以前就没校验）。P1 对 rollback 做 AST 校验继续保留，DSL 校验可选（旧 config 可能没有 builder_config）。
- **前端 builder_config = null 的情况**：旧策略和代码编辑器创建的策略没有 builder_config → 后端 builder_validator 跳过（只校验非 null 的情况）。

## Validation

- 后端 builder_validator 单测：覆盖 14 条规则 + 正常配置全部通过
- 后端 API 集成测试：P0 4 条回归 + P1 至少 6 条新增（非法 DSL 4 条 + 未知 version 1 条 + AST error 1 条）
- 全量 pytest：570 + 10 ≥ 580 passed
- 手动测试：前端构建器保存（本来就合法）→ 应该 200；如果前端传了被篡改的 config（用 DevTools）→ 后端 422

## Risks

- **旧 builder_config 没有 version 字段**：Pydantic strict mode 会拒绝。handling：validate_builder_config 开头 `config.get("version")`，缺或未知 → 返回 "未知 DSL 版本" 错误（422）。但 create 端点可能需要给旧策略迁移——在 migration 里或 validator 开头做 `config.setdefault("version", 1)`？不对，我们是校验，不是修补。handling：create/update 时如果 builder_config 有缺失 version，**显式补齐**为 1，让 validator 通过；但非法 version（v=99）仍然拒绝。
- **前端和后端校验不完全一致**：可能前端允许某配置但后端拒绝。handling：P1 实现后跑一遍前端构建器的手动测试，确保全部正常保存。
- **update 端点的 builder_config 可能是 null**：StrategyUpdate.builder_config 是 `dict | None`，显式 null 表示"清除绑定"。handling：validate_builder_config 只在 payload.builder_config is not None 时调。
