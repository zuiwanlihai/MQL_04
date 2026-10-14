"""Audit hash-chain integrity (M6 Phase D/E)."""

from __future__ import annotations

import uuid


def test_chain_intact_after_multiple_records(client):
    async def _run():
        from app.services.audit import AuditActor, record, verify_chain

        for i in range(3):
            await record("live_deploy",
                         actor=AuditActor(actor_type="user", actor_label=f"u{i}"),
                         target_type="live_run", target_id=f"audit-chain-{i}",
                         params={"i": i})
        return await verify_chain()

    total, findings = client.portal.call(_run)
    assert findings == []
    assert total >= 3


def test_chain_detects_tampering(client):
    async def _run():
        from sqlalchemy import select, update

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import record, verify_chain

        await record("live_stop", target_type="live_run",
                     target_id="tamper-me", params={"x": 1})
        async with AsyncSessionLocal() as db:
            last = (await db.execute(
                select(AuditLog).where(AuditLog.target_id == "tamper-me")
                .order_by(AuditLog.created_at.desc()).limit(1)
            )).scalars().first()
            assert last is not None
            original = last.params
            await db.execute(
                update(AuditLog).where(AuditLog.id == last.id)
                .values(params={"x": 999})
            )
            await db.commit()
        total, findings = await verify_chain()
        assert findings, "改过的行没有被检出"
        assert findings[0].kind == "tampered", findings[0].detail
        # restore so later tests (e.g. the patrol suite) see an intact chain
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(AuditLog).where(AuditLog.id == last.id)
                .values(params=original)
            )
            await db.commit()
        total, findings = await verify_chain()
        return total, findings

    total, findings = client.portal.call(_run)
    assert findings == []  # restored → chain whole again


# ── 2.8 竞争与顺序：两类假警报 ─────────────────────────────────


def test_concurrent_writes_do_not_fork_the_chain(client):
    """并发写不能分叉。

    「读链尾 → 插入」本来是分离的两步：两个请求同时读到同一行，就写出两条
    prev_hash 相同的行。分叉当时无声无息，直到巡检走到第二条才报「审计链校验
    失败」——真被篡改时反而没人信这个警报了。
    """

    async def _run():
        import asyncio

        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import AuditActor, record, verify_chain

        tag = f"race-{uuid.uuid4().hex[:8]}"
        await asyncio.gather(*[
            record("live_deploy",
                   actor=AuditActor(actor_type="user", actor_label=f"u{i}"),
                   target_type="live_run", target_id=f"{tag}-{i}", params={"i": i})
            for i in range(8)
        ])
        async with AsyncSessionLocal() as db:
            prevs = (await db.execute(
                select(AuditLog.prev_hash).where(AuditLog.target_id.like(f"{tag}-%"))
            )).scalars().all()
            assert len(prevs) == 8, "并发写丢了行"
            assert len(set(prevs)) == 8, (
                f"链分叉了：{len(prevs) - len(set(prevs))} 条记录共享同一个前驱"
            )
        return await verify_chain()

    _total, findings = client.portal.call(_run)
    assert findings == [], f"并发写让巡检报出假篡改：{findings}"


def test_chain_order_does_not_rest_on_created_at(client):
    """链的先后由写入顺序（seq）决定，不由 created_at / uuid 决定。

    created_at 只有微秒精度，同一微秒里的两行靠 uuid4 主键分先后——一个随机
    数。这里把时间戳排成链的**倒序**（同一微秒在真实负载里更容易出现，但结果
    一样）：若巡检仍按 created_at 走，第二行起就会报断链，而数据其实完好。
    """

    async def _run():
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select, update

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import record, verify_chain

        tag = f"order-{uuid.uuid4().hex[:8]}"
        for i in range(5):
            await record("live_deploy", target_type="live_run",
                         target_id=f"{tag}-{i}", params={"i": i})
        base = datetime(2026, 1, 1, tzinfo=UTC)
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.target_id.like(f"{tag}-%"))
                .order_by(AuditLog.seq.asc())
            )).scalars().all()
            assert [r.target_id for r in rows] == [f"{tag}-{i}" for i in range(5)], \
                "seq 没有反映写入顺序"
            for i, r in enumerate(rows):
                # 越晚写的行，created_at 越早 —— 按时间序遍历只能是倒着的
                await db.execute(
                    update(AuditLog).where(AuditLog.id == r.id)
                    .values(created_at=base - timedelta(seconds=i))
                )
            await db.commit()
        return await verify_chain()

    _total, findings = client.portal.call(_run)
    assert findings == [], f"巡检被 created_at 带偏，报了假断链：{findings}"


def test_verify_chain_pages_instead_of_loading_everything(client):
    """分批扫描照样走得完整张表（巡检不该把整表读进内存）。"""

    async def _run():
        from app.services.audit import record, verify_chain

        tag = f"page-{uuid.uuid4().hex[:8]}"
        for i in range(7):
            await record("live_deploy", target_type="live_run",
                         target_id=f"{tag}-{i}", params={"i": i})
        # batch_size=2：强制分页走多轮，且最后一轮不足一批
        return await verify_chain(batch_size=2), await verify_chain(batch_size=3)

    (total2, f2), (total3, f3) = client.portal.call(_run)
    assert f2 == [] and f3 == []
    assert total2 == total3, "总行数不该随批大小变化"
    assert total2 >= 7


# ── 异常的定性，以及「老断链不能再遮挡新篡改」 ────────────────


def _fork_row(last, *, tag: str):
    """照着链尾记录造一行「分叉行」：prev_hash 照抄链尾的前驱。

    这正是 2.8 之前 record() 在并发下会写出的形状——「读链尾」与「插入」不
    原子，两行共享同一个前驱，各自的内容却都是完好的。
    """
    from app.models import AuditLog
    from app.services.audit import _compute_hash, _payload_of

    row = AuditLog(action="live_deploy", actor_label="fork-fixture",
                   target_type="live_run", target_id=f"{tag}-fork",
                   params={}, prev_hash=last.prev_hash)
    row.hash = _compute_hash(row.prev_hash, _payload_of(row))
    return row


def test_fork_is_classified_as_fork_not_tampering(client):
    """分叉要报成「分叉」：内容完好，只是和前一条共享前驱。"""

    async def _run():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import AuditActor, record, verify_chain

        tag = f"fork-{uuid.uuid4().hex[:8]}"
        for i in range(2):
            await record("live_deploy",
                         actor=AuditActor(actor_type="user", actor_label="fork"),
                         target_type="live_run", target_id=f"{tag}-{i}",
                         params={"i": i})
        async with AsyncSessionLocal() as db:
            last = (await db.execute(
                select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)
            )).scalars().one()
            fork = _fork_row(last, tag=tag)
            db.add(fork)
            await db.commit()
            fork_seq = fork.seq

        total, findings = await verify_chain()

        # 清理：从链尾起删掉本次写的行（没有别的行挂在它们后面）
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.target_id.like(f"{tag}%"))
                .order_by(AuditLog.seq.desc())
            )).scalars().all()
            for r in rows:
                await db.delete(r)
            await db.commit()
        _, after = await verify_chain()
        return findings, fork_seq, after

    findings, fork_seq, after = client.portal.call(_run)
    assert [f.kind for f in findings] == ["fork"], [f.kind for f in findings]
    assert findings[0].seq == fork_seq, "异常要定位到出问题的那条记录"
    assert "前驱" in findings[0].detail
    assert after == [], "清理后链应回到完好"


def test_a_fork_does_not_hide_a_later_tamper(client):
    """先有（良性的）分叉，后面的篡改也必须照样被报出来。

    旧巡检发现第一处异常就返回：断点之后的半张表再没人看过。只要链上存在
    一处断链——哪怕是历史遗留的分叉——再往链尾追加篡改行就能永远躺在盲区。
    """

    async def _run():
        from sqlalchemy import select, update

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import AuditActor, record, verify_chain

        tag = f"mask-{uuid.uuid4().hex[:8]}"
        actor = AuditActor(actor_type="user", actor_label="mask-test")
        # 两行垫场：分叉行的前驱不能是链首（空前驱是另一种异常）
        for i in (0, 1):
            await record("live_deploy", actor=actor, target_type="live_run",
                         target_id=f"{tag}-{i}", params={"i": i})
        async with AsyncSessionLocal() as db:
            last = (await db.execute(
                select(AuditLog).order_by(AuditLog.seq.desc()).limit(1)
            )).scalars().one()
            db.add(_fork_row(last, tag=tag))
            await db.commit()
        # 分叉之后照常写入，然后改掉最后一行
        for i in (2, 3):
            await record("live_deploy", actor=actor, target_type="live_run",
                         target_id=f"{tag}-{i}", params={"i": i})
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(AuditLog).where(AuditLog.target_id == f"{tag}-3")
                .values(params={"i": 999})
            )
            await db.commit()

        try:
            total, findings = await verify_chain()
        finally:
            # 修复被改的行，再从链尾起删掉本次写的行
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(AuditLog).where(AuditLog.target_id == f"{tag}-3")
                    .values(params={"i": 3})
                )
                await db.commit()
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(AuditLog).where(AuditLog.target_id.like(f"{tag}%"))
                    .order_by(AuditLog.seq.desc())
                )).scalars().all()
                for r in rows:
                    await db.delete(r)
                await db.commit()
            _, after = await verify_chain()
        return findings, after

    findings, after = client.portal.call(_run)
    kinds = [f.kind for f in findings]
    assert "fork" in kinds, f"分叉没被报出来：{kinds}"
    assert "tampered" in kinds, f"分叉之后的篡改被漏掉了：{kinds}"
    assert kinds.index("tampered") > kinds.index("fork"), "篡改在分叉之后"
    assert after == [], "清理后链应回到完好"


def test_restarted_chain_is_classified_as_restart(client):
    """链中途冒出一个空前驱：有人把历史截断后重新起链，这要报成 restart。"""

    async def _run():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import AuditActor, _compute_hash, _payload_of, record, verify_chain

        tag = f"restart-{uuid.uuid4().hex[:8]}"
        actor = AuditActor(actor_type="user", actor_label="restart-test")
        for i in (0, 1):
            await record("live_deploy", actor=actor, target_type="live_run",
                         target_id=f"{tag}-{i}", params={"i": i})
        async with AsyncSessionLocal() as db:
            # 新行自称链首（prev_hash 空），内容自洽——像一次「重新起链」
            row = AuditLog(action="live_deploy", actor_label="restart-fixture",
                           target_type="live_run", target_id=f"{tag}-restart",
                           params={}, prev_hash="")
            row.hash = _compute_hash("", _payload_of(row))
            db.add(row)
            await db.commit()
            restart_seq = row.seq

        try:
            total, findings = await verify_chain()
        finally:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(AuditLog).where(AuditLog.target_id.like(f"{tag}%"))
                    .order_by(AuditLog.seq.desc())
                )).scalars().all()
                for r in rows:
                    await db.delete(r)
                await db.commit()
            _, after = await verify_chain()
        return findings, restart_seq, after

    findings, restart_seq, after = client.portal.call(_run)
    assert [f.kind for f in findings] == ["restart"], [f.kind for f in findings]
    assert findings[0].seq == restart_seq
    assert after == [], "清理后链应回到完好"


def test_deleted_row_is_classified_as_missing(client):
    """中间少了一行：报文要说「有记录被删除」，不是笼统一句「断链」。"""

    async def _run():
        from sqlalchemy import select

        from app.engine.session import AsyncSessionLocal
        from app.models import AuditLog
        from app.services.audit import AuditActor, record, verify_chain

        tag = f"del-{uuid.uuid4().hex[:8]}"
        actor = AuditActor(actor_type="user", actor_label="del-test")
        for i in range(3):
            await record("live_deploy", actor=actor, target_type="live_run",
                         target_id=f"{tag}-{i}", params={"i": i})
        async with AsyncSessionLocal() as db:
            # 删中间那一行；它的后继会看见一个「不存在的前驱」
            middle = (await db.execute(
                select(AuditLog).where(AuditLog.target_id == f"{tag}-1")
            )).scalars().one()
            middle_seq = middle.seq
            await db.delete(middle)
            await db.commit()

        try:
            total, findings = await verify_chain()
        finally:
            # 从链尾起删掉本次写的行（-2 先走，否则它会指向被删的 -1）
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(AuditLog).where(AuditLog.target_id.like(f"{tag}%"))
                    .order_by(AuditLog.seq.desc())
                )).scalars().all()
                for r in rows:
                    await db.delete(r)
                await db.commit()
            _, after = await verify_chain()
        return findings, middle_seq, after

    findings, middle_seq, after = client.portal.call(_run)
    assert [f.kind for f in findings] == ["missing"], [f.kind for f in findings]
    # 只报一处：删一行 = 一处异常，后续行重新锚定后不再跟着报
    assert findings[0].seq > middle_seq, "报的应该是被删行的后继"
    assert after == [], "清理后链应回到完好"
