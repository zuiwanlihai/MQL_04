"""Audit writer + chain-integrity checker (M6, PRD M7 审计 P0).

Single insert helper used by every audited endpoint/manager action.  Rows are
hash-chained (``hash = sha256(prev_hash + canonical_json)``); the nightly
``verify_chain`` walks the whole table and reports **every** anomaly, each
classified (分叉 / 缺前驱 / 内容被改 / 链被重置) — a chain that stops at the
first break leaves everything behind it permanently unverified.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.engine.session import AsyncSessionLocal
from app.models import AuditLog

# 写链的事务级顾问锁的键（任意常数，全平台用同一个即可）。见 `record` 里的说明。
_CHAIN_LOCK_KEY = 0x4D514C4155444954  # "MQLAUDIT" 的 ASCII


@dataclass
class AuditActor:
    """Who performed the action (from the request / system)."""

    actor_type: str = "user"      # user | system | strategy
    actor_id: uuid.UUID | None = None  # was `object` — mypy can't pass to AuditLog.actor_id: Mapped[UUID | None]
    actor_label: str = ""
    source_ip: str = ""


def _canonical(fields: dict) -> str:
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)


def _compute_hash(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(payload)).encode("utf-8")).hexdigest()


def _payload_of(row: AuditLog) -> dict:
    """链上参与哈希的字段（顺序无关，canonical json 会排序）。

    读、写两侧共用这一个函数。哈希链最怕「写的时候按这套字段算、验的时候按
    另一套读」——那种不一致会伪装成篡改，把人引向错误的结论。
    """
    return {
        "ts_ms": row.ts_ms,
        "actor_type": row.actor_type,
        "actor_id": str(row.actor_id) if row.actor_id else "",
        "actor_label": row.actor_label,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "params": row.params,
        "result": row.result,
        "error_code": row.error_code or "",
        "risk_decision": row.risk_decision or "",
        "rule_id": row.rule_id or "",
        "source_ip": row.source_ip,
        "correlation_id": row.correlation_id,
    }


# ── 链异常的分类 ──────────────────────────────────────────────
# 巡检报出来的每一处异常都要先定性：分叉（并发写的旧毛病，内容没动）和
# 篡改（内容或历史被动过）是两回事，报警口径和处置动作都不同。混成同一个
# 「断链」，代价是历史留下来的分叉会让巡检常驻告警状态，真实篡改再来时
# 已经没人当回事了。

_TAMPERED_HINT = "内容与自身哈希对不上：这行被改过，或整行被替换"
_RESTART_HINT = "链中途出现空前驱：历史在这里被截断，之后重新起链"
_MISSING_HINT = "前驱哈希在链上不存在：它前面有记录被删除"
_FORK_HINT = ("与前一条记录共享同一个前驱：并发写入分叉（分叉行自身内容完好）。"
              "写入侧已加锁修复——核对时间戳：旧记录是历史遗留，"
              "修复之后再现说明锁没生效，需立即排查")


@dataclass(frozen=True)
class ChainFinding:
    """巡检发现的一处链异常。"""

    index: int    # 链序里的位置（0 基）
    seq: int      # 写入序号：拿它去审计列表里定位那一行
    kind: str     # fork | missing | tampered | restart
    detail: str   # 人话说明，可直接进告警文案


#: 除了 fork，其余都是「有人动过数据」，告警口径必须比 fork 重。
_CRITICAL_KINDS = frozenset({"missing", "tampered", "restart"})


def verdict(findings: list[ChainFinding]) -> str:
    """把发现归纳成告警口径：``ok`` | ``fork`` | ``critical``。

    分叉必须单独一档。巡检按「状态变化」告警：只要历史分叉把状态钉在
    broken 上，此后真实篡改发生也不会再触发一次告警——旧分叉就这么变成了
    新篡改的掩护。分开之后，fork→critical 本身就是一次状态变化。
    """
    if not findings:
        return "ok"
    return "critical" if any(f.kind in _CRITICAL_KINDS for f in findings) else "fork"


async def _classify(db, row: AuditLog, index: int) -> ChainFinding:
    """给一行异常定性。

    关键一步是**按这行自己记录的前驱**重算它的 hash：算得出来说明这行内容
    没被动过（只是挂错了位置）；算不出来说明内容被改过。
    """
    if row.hash != _compute_hash(row.prev_hash, _payload_of(row)):
        return ChainFinding(index, row.seq, "tampered", _TAMPERED_HINT)
    if not row.prev_hash:
        return ChainFinding(index, row.seq, "restart", _RESTART_HINT)
    earlier = (await db.execute(
        select(AuditLog.seq)
        .where(AuditLog.hash == row.prev_hash, AuditLog.seq < row.seq)
        .limit(1)
    )).scalar_one_or_none()
    if earlier is not None:
        return ChainFinding(index, row.seq, "fork",
                            f"{_FORK_HINT}（共享的前驱是第 {earlier} 号记录）")
    return ChainFinding(index, row.seq, "missing", _MISSING_HINT)


async def record(
    action: str,
    *,
    actor: AuditActor | None = None,
    target_type: str = "",
    target_id: str = "",
    params: dict | None = None,
    result: str = "success",
    error_code: str | None = None,
    risk_decision: str | None = None,
    rule_id: str | None = None,
    correlation_id: str = "",
    db=None,
) -> AuditLog:
    """Append one audit row to the hash chain (same session as the caller if given).

    传 ``db`` 时**只有调用方 commit，这行才存在**：它属于调用方那个事务，
    而请求会话（``get_db``）在请求结束时只是 close——没提交就回滚，审计行
    无声无息地消失。所以只有在「审计必须与状态变更同生共死」时才传 ``db``
    （并在同一处 ``commit``）；事后补记的审计一律交给自己（不传）。
    """
    actor = actor or AuditActor(actor_type="system")
    params = params or {}
    now = datetime.now(UTC)
    own = db is None
    ctx = AsyncSessionLocal() if own else None
    db = db or await ctx.__aenter__()  # type: ignore[union-attr]
    try:
        # 「读链尾 → 插入」必须是原子的。两个请求同时读到同一行，就会写出两条
        # prev_hash 相同的行——链在这里分叉。分叉当时不报错，事后 verify_chain
        # 走到第二条才发现 prev_hash 对不上，于是报警「审计链校验失败」：真实的
        # 篡改反而会淹没在这种假警报里（喊狼来了的代价是没人再信）。
        # 事务级顾问锁到提交/回滚时自动释放，不占连接、也不会像行锁那样跟调用方
        # 事务里的其它锁排成环；锁的持有时间就是调用方事务剩下的部分。
        await db.execute(select(func.pg_advisory_xact_lock(_CHAIN_LOCK_KEY)))
        last = (await db.execute(
            select(AuditLog.hash).order_by(AuditLog.seq.desc()).limit(1)
        )).scalar_one_or_none()
        prev_hash = last or ""
        row = AuditLog(
            ts_ms=int(now.timestamp() * 1000),
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            actor_label=actor.actor_label,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            params=params,
            result=result,
            error_code=error_code,
            risk_decision=risk_decision,
            rule_id=rule_id,
            source_ip=actor.source_ip,
            correlation_id=correlation_id,
            prev_hash=prev_hash,
            hash="",
        )
        row.hash = _compute_hash(prev_hash, _payload_of(row))
        db.add(row)
        if own:
            await db.commit()
        return row
    finally:
        if own:
            # mypy 不追踪 `if own:` 与 `ctx is not None` 的等价关系，收窄
            assert ctx is not None
            await ctx.__aexit__(None, None, None)


async def verify_chain(batch_size: int = 500) -> tuple[int, list[ChainFinding]]:
    """Walk the whole audit chain; return ``(total, findings)``.

    ``findings`` 为空表示链完好；否则按链序给出每一处异常，用
    :func:`verdict` 归纳成告警口径。

    断一处**不停**巡检：走到异常就重新锚定到这一行的 hash 继续往下走。老写法
    一发现断链就返回，断点之后的半张表从此再没人校验——只要链上先存在一处
    （哪怕是良性的）断链，往链尾追加篡改行的人就可以永远躺在盲区里。

    顺序按 ``seq``（写入顺序）：``created_at`` 只有微秒精度、主键是 uuid4，
    都排不出稳定的先后，用它们遍历会把「同一微秒里的两行」读成断链。

    分批扫描：审计表只增不减，整表 load 进内存的话，迟早有一天夜间巡检会先
    把自己 OOM 掉——那时就再也没人发现真正的篡改了。keyset 分页按 seq 前进，
    内存占用与表大小无关（异常稀少，定性所需的额外查询只发生在异常行上）。
    """
    prev_hash = ""
    scanned = 0
    last_seq: int | None = None
    findings: list[ChainFinding] = []
    async with AsyncSessionLocal() as db:
        # 行数单独 count：「共 N 条」的 N 必须是表里的条数，不是巡检走到的
        # 条数——否则表尾有多少没查过就没人看得见了。
        total = int((await db.execute(
            select(func.count()).select_from(AuditLog)
        )).scalar_one())
        while scanned < total:
            stmt = select(AuditLog).order_by(AuditLog.seq.asc()).limit(batch_size)
            if last_seq is not None:
                stmt = stmt.where(AuditLog.seq > last_seq)
            rows = (await db.execute(stmt)).scalars().all()
            if not rows:  # 校验途中被删了行（本身就是篡改）
                findings.append(ChainFinding(
                    scanned, last_seq or 0, "missing",
                    "巡检途中链尾少了行：继续遍历时已经取不到记录"))
                return total, findings
            for i, r in enumerate(rows):
                expected = _compute_hash(prev_hash, _payload_of(r))
                if r.prev_hash == prev_hash and r.hash == expected:
                    prev_hash = r.hash
                    continue
                findings.append(await _classify(db, r, scanned + i))
                # 重新锚定。不这么做的话，断点之后每一行都要跟着报——一处断链
                # 会膨胀成几百条「异常」，真正的位置反而被淹掉。
                prev_hash = r.hash
            scanned += len(rows)
            last_seq = rows[-1].seq
    return total, findings
