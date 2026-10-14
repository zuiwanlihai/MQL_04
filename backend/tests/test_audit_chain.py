"""Audit hash-chain integrity (M6 Phase D/E)."""

from __future__ import annotations


def test_chain_intact_after_multiple_records(client):
    async def _run():
        from app.services.audit import AuditActor, record, verify_chain

        for i in range(3):
            await record("live_deploy",
                         actor=AuditActor(actor_type="user", actor_label=f"u{i}"),
                         target_type="live_run", target_id=f"audit-chain-{i}",
                         params={"i": i})
        return await verify_chain()

    total, broken = client.portal.call(_run)
    assert broken == -1
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
        total, broken = await verify_chain()
        assert broken >= 0  # the tampered row breaks the chain
        # restore so later tests (e.g. the patrol suite) see an intact chain
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(AuditLog).where(AuditLog.id == last.id)
                .values(params=original)
            )
            await db.commit()
        total, broken = await verify_chain()
        return total, broken

    total, broken = client.portal.call(_run)
    assert broken == -1  # restored → chain whole again
