# 可视化策略构建器 P0 数据保护 实施计划

## Repository Research

### 当前状态（核心文件）

| 文件 | 现状 | 问题 |
|------|------|------|
| `frontend/src/pages/Strategies/BuilderPage.tsx` L270-296 | `doSaveEdit()` 先调 `saveSource.mutate({ source_code, base_updated_at: null })`，成功后再调 `update.mutate({ builder_config, direction, ... })` | **两次调用、非原子**；`base_updated_at: null` 刻意关闭乐观锁 |
| `frontend/src/pages/Strategies/BuilderPage.tsx` L298 | `showOverwriteWarning = strategy?.builder_config == null` | 只在 builder_config 为 null 时警告；**源码被手动修改过但旧 config 仍在时，静默覆盖无提示** |
| `frontend/src/hooks/useStrategies.ts` L94-114 | `useSaveSource()` 单独保存源码；`useUpdateStrategy()` 单独保存元数据 | 没有「原子保存 builder + source」的 hook |
| `backend/app/api/v1/endpoints/strategies.py` L218-240 | `PUT /{id}/source` 有 `base_updated_at` 乐观锁（但前端传 null）；`PATCH /{id}` 没有乐观锁 | 两个端点、无统一事务包装 |
| `backend/app/schemas/strategy.py` L60-82 | `StrategyUpdate` 没有 `base_updated_at`；`StrategySourceUpdate` 有但前端关闭 | 并发保护不完整 |
| `frontend/src/types/api.ts` L312-327 | `Strategy.updated_at: string` 已有 | 前端有能力传递乐观锁基值 |
| `frontend/src/lib/builder/types.ts` | `BuilderConfig` 有 version=1，支持扩展 | 可新增 `generated_hash` 字段来做脱离检测 |

### 关键代码位置

**非原子保存（BuilderPage L270-296）：**
```
saveSource.mutate({ base_updated_at: null })  ← 第一次调用，关闭乐观锁
  → onSuccess:
    update.mutate({ builder_config, direction, ... })  ← 第二次调用，可能前面成功这里失败
```

**覆盖警示条件过宽（BuilderPage L298）：**
```
showOverwriteWarning = mode === 'edit' && strategy?.builder_config == null
```

## Files and Modules

### 后端
| 文件 | 变更 |
|------|------|
| `backend/app/schemas/strategy.py` | 新增 `StrategyBuilderSave`（原子保存请求体：source_code + builder_config + direction + symbol_ids + timeframe + base_updated_at）；`StrategyUpdate` 也加 `base_updated_at` |
| `backend/app/api/v1/endpoints/strategies.py` | 新增 `PUT /strategies/{id}/builder-save` 端点（单次事务内同时更新 source_code + builder_config + 元数据 + 记录 generated_hash）；`PATCH /strategies/{id}` 加乐观锁校验；`GET /strategies/{id}` 响应加 `generated_hash` 字段 |
| `backend/app/models/strategy.py` | Strategy 模型加 `source_code_hash` 列（nullable，记录最后一次保存源码的 SHA-256） |
| `backend/migrations/` | 新增 alembic migration：加 `source_code_hash` 列 |

### 前端
| 文件 | 变更 |
|------|------|
| `frontend/src/hooks/useStrategies.ts` | 新增 `useSaveBuilder()` hook（调用 `PUT /{id}/builder-save`） |
| `frontend/src/types/api.ts` | `StrategyDetail` 加 `source_code_hash?: string`；`StrategyBuilderSave` 请求/响应类型 |
| `frontend/src/pages/Strategies/BuilderPage.tsx` | `doSaveEdit()` 改为单次 `saveBuilder.mutate()`；去掉 `base_updated_at: null`；加载时检测源码是否脱离（对比 backend `source_code_hash` 与本地 `generated.code` 的 hash）；脱离时弹窗让用户选"覆盖"/"返回编辑器"/"另存为副本" |

## Implementation Steps

### Step 1: 后端模型 + schema + 迁移
1. 在 Strategy 模型加 `source_code_hash: Mapped[str | None]`
2. 生成 alembic migration 并执行
3. `strategy.py` schema 新增 `StrategyBuilderSave`（含 base_updated_at）、`StrategyUpdate.base_updated_at`

### Step 2: 后端原子保存端点
1. `PUT /strategies/{id}/builder-save` — 单次事务内：
   - 乐观锁校验 base_updated_at（传 null 跳过，但文档注明不推荐）
   - 设置 source_code、builder_config、direction、symbol_ids、timeframe
   - 计算并存储 `sha256(source_code)` → `source_code_hash`
   - 同步跑 AST validate，error 级拒绝（与现有 save_source 一致）
2. `PATCH /strategies/{id}` 加 base_updated_at 乐观锁
3. `GET /strategies/{id}` 响应加 source_code_hash 字段

### Step 3: 前端 hook + 类型
1. `useStrategies.ts` 新增 `useSaveBuilder()` — 调 PUT /{id}/builder-save
2. `types/api.ts` StrategyDetail 加 source_code_hash

### Step 4: 前端 BuilderPage 改造
1. `doSaveEdit()` → 单个 `saveBuilder.mutate({ base_updated_at: strategy.updated_at, source_code: generated.code, builder_config: config, ... })`
2. 编辑模式加载后计算 `generatedHash = sha256(generated.code)`，对比 `strategy.source_code_hash`：
   - 一致 → 正常进入构建器
   - 不一致 → 脱离状态，弹窗：继续覆盖 / 返回代码编辑器 / 另存为新策略
3. `showOverwriteWarning` 改为也检测脱离状态

### Step 5: 回归测试
1. 后端：创建策略时 builder_config + source_code 同时写入；builder-save 单次事务成功；乐观锁冲突返回 409；source_code_hash 被正确更新
2. 前端：生成器快照测试 + 新增 builder-save hook 调用测试

## Dependencies and Considerations

- **alembic migration**：新增 `source_code_hash` 列需要 `alembic revision --autogenerate -m "add_source_code_hash"` 然后 `alembic upgrade head`
- **SHA-256 纯函数**：后端用 `hashlib.sha256(source_code.encode()).hexdigest()`；前端用 `crypto.subtle.digest('SHA-256', ...)` 或纯 JS 实现（保持与后端一致）
- **向后兼容**：`source_code_hash` 为 nullable，旧策略无此字段时前端判定为"未知，安全起见提示用户"
- **rollback/version restore**：回滚版本后 `source_code_hash` 也需要更新——在 rollback 端点追加 hash 更新
- **StrategyEditorPage（代码编辑器）**：它自己保存源码时也应该更新 source_code_hash——`save_source` 端点已存在，需要在那里也加 hash 计算

## Validation

- 后端 565 现有测试 + 新增原子保存/乐观锁/脱离检测测试全部通过
- 前端构建器 codegen 快照测试通过
- 手动测试：创建策略 → 进入构建器编辑 → 保存 → 再次进入构建器（应无脱离警告）→ 去代码编辑器手动改源码 → 保存 → 返回构建器（应显示脱离警告）
- 手动测试：两个窗口同时编辑同一策略 → 第二个窗口保存触发 409

## Risks

- **原子保存端点复用 create 的事务逻辑**：如果 StrategyCreate 有模板加载等特殊路径，builder-save 需要避免重复。handling：builder-save 只做已存在策略的更新路径，不复用模板逻辑。
- **前端 hash 计算性能**：长源码（200KB）SHA-256 浏览器端纯 JS 约 1ms，可忽略。
- **旧数据迁移后 source_code_hash 为 null**：前端需对 null 做安全降级（不强制提示，仅当用户显式修改时才触发脱离）。
- **base_updated_at 类型不匹配**：后端是 `datetime | None`，前端传 ISO 字符串。handling：已有 `StrategySourceUpdate` 用同样模式，能正常工作。
