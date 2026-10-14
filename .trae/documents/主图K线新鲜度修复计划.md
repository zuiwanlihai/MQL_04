# 主图 K 线新鲜度修复计划（网关尾部自愈 + skew 抗污染）

## Context

用户截图反馈：XAUUSD 详情页头部报价实时跳动，但 M1 主图 K 线冻结在 09:30（北京时间）。上一轮诊断已确认两个根因：

1. **终端 bar 缓存陈旧**：MT5 终端只为「开了图表」的品种维护 bar 历史；未开图表品种 `copy_rates` 返回陈旧缓存。首次 `copy_rates_range` 请求会触发后台补数、后续请求才新鲜（实测：诊断请求后 XAUUSD M1 lag 从小时级降到 2s）。后端 `/market/symbols/{name}/klines` 是纯代理，无任何自愈。
2. **skew 污染**：XAUUSD 喂价 tick 时间戳在服务器轴上超前 ~+18min；全局 `_tz_skew` 由批次**首个**品种的 tick 估算（`_refresh_tz_skew`），被污染后**所有**品种 `bar_time` 偏移 → 前端 liveBar 对桶错位、出现悬空蜡烛。

修复全部收敛在网关 `gateway/app/mt5_client.py`；后端纯代理透传、前端 30s refetch（SymbolDetail.tsx L36）+ liveBar 按 `quote.bar_time` 对桶（L41-47）均已就位，**不动**。

## 改动 1：rates 尾部滞后自愈 — `gateway/app/mt5_client.py`

**只在 async 层 `rates()`（L476）加重试循环**（铁律：重试绝不能写进同步函数霸占单 worker 队列，见 history_backfill L615 注释）：

```python
async def rates(self, name, timeframe, limit):
    rows = await self.run(self._rates_sync, name, timeframe, limit)
    tf_sec = TF_SECONDS[timeframe]
    key = (name.upper(), timeframe)
    now = time.time()
    if now < self._tail_heal_cooldown.get(key, 0.0):
        return rows                      # 冷却期内直接返回
    for _ in range(settings.RATES_TAIL_HEAL_MAX_ATTEMPTS):
        if not rows:
            break
        t = int(time.time())
        expected = t - t % tf_sec        # 当前桶起点（bars 为真实 UTC 轴）
        if rows[-1]["time"] >= expected - tf_sec:
            break                        # 有当前活 bar 或上一根刚收完的 bar → 新鲜
        await asyncio.sleep(settings.RATES_TAIL_HEAL_INTERVAL)  # 释放 worker 让终端补数
        rows = await self.run(self._rates_sync, name, timeframe, limit)
    else:
        if rows:
            self._tail_heal_cooldown[key] = time.time() + settings.RATES_TAIL_HEAL_COOLDOWN
    return rows
```

- 滞后判定阈值 `last < expected - tf`：缺「当前活 bar + 上一根完成 bar」才算陈旧；稀疏 tick 品种刚跨桶、活 bar 未生成时不触发（前端 liveBar 会用实时报价补当前桶）。
- **冷却防打爆**：休市/周末永不收敛，重试耗尽后写 5 分钟冷却（实例级 `self._tail_heal_cooldown: dict[tuple[str, str], float]`，`__init__` 初始化）。
- 只改 `rates()`（后端 /klines 数据源）；`rates_range` 供回测批量历史，尾部新鲜度无感知，不动。

**`gateway/app/config.py` 新增**（与 HISTORY_BACKFILL_* L45-46 并列）：

```python
RATES_TAIL_HEAL_MAX_ATTEMPTS: int = 2
RATES_TAIL_HEAL_INTERVAL: float = 0.8
RATES_TAIL_HEAL_COOLDOWN: float = 300.0
```

## 改动 2：skew 抗污染 + bar_time 钳制 — `gateway/app/mt5_client.py`

- **`_refresh_tz_skew(samples: list[int])`（L732）改为批量样本**：接收本批次最多 3 个品种的 tick.time，对 `(tick_time - now)` 取中位数再取整分钟。样本 3 个 → median；2 个 → 取**较小** delta（低估 + 钳制是安全方向：bar_time 略超前会被钳回当前桶，高估则会把 bar 推进过去的桶）；1 个 → 该样本。TTL（10 分钟）逻辑不变。
- **`_quote_sync`（L746）**：删掉内联 `self._refresh_tz_skew(tick_time)`（L760）和 `"bar_time"` 字段（L769）。
- **`_quotes_sync`（L817）** 重排为三步：① 循环构建 quote，收集最多 3 个有效 `tick_time` 样本；② 统一 `_refresh_tz_skew(samples)`；③ 逐条补 `bar_time = min(tick_time - self._tz_skew, int(time.time()))` —— `min()` 钳制未来时间戳，污染 tick 也会落在当前桶。
- 输出 shape 不变（`time`/`bar_time` 字段保留），后端 mapper / QuoteHub 泵 / 前端零改动。

## 改动 3：测试

**`gateway/tests/test_quotes.py`（新建，复用 test_ticks.py 的 fake 模式）** — fake 模块需补 `symbol_info_tick`（按 FAKE 编程式返回 tick.time；`_quote_sync` 里 D1 日内高低走 `copy_rates_range`，fake 返回 None 即可，注意清 `_daily_cache`）：

1. 3 样本中位数：EURUSD/GBPUSD 正常 `now+10800`、XAUUSD 污染 `now+11880` → 三个品种 bar_time 都 ≈ now（±2s），不被 18min 污染。
2. 钳制：单品种污染 tick → bar_time ≤ now。
3. TTL 内不重估：预设 skew 后立即二次调用 → skew 不被覆盖。

**`gateway/tests/test_history.py`（追加，复用 FAKE.range_provider）** — bars 构造到当前桶附近：

4. 首轮陈旧（尾根落后 ≥2 根）、次轮新鲜 → `range_calls ≥ 2` 且返回新鲜尾根。
5. 数据新鲜 → `range_calls == 1`，无重试。
6. 永不收敛（一直陈旧）→ 重试上限 + 冷却生效（紧接第二次 `rates()` 只增 1 次 range 调用）。

## 门禁（按惯例切片过门禁）

1. gateway：cwd=`gateway`，`backend\.venv\Scripts\python.exe -m ruff check app tests` + `backend\.venv\Scripts\python.exe -m pytest tests`
2. backend：ruff + pytest 全量（确认零改动不受影响）
3. 前端零改动 → 跳过 tsc/vitest
4. 重启网关（8090）与后端（8000）后台服务
5. 浏览器 E2E（复用 resume=4b3d29e6-ab67-48f3-8d3a-13800368ba12）：
   - XAUUSD M1 详情页首次加载 K 线尾根即新鲜（lag ≤ 1-2 根），头部报价跳动
   - liveBar 随 tick 更新、无悬空蜡烛
   - 抽查 EURUSD 不受 XAUUSD tick 污染影响（bar_time ≈ 北京时间当前分钟）

完成后汇报等确认，测试数据留 dev 库。
