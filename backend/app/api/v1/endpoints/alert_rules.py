"""Alert-rule CRUD endpoints (M15, PRD M7 告警规则 P0).

Ownership model matches notify channels: rules are per-user, cross-user
access → 404.  Every mutation is audited (PRD M7 ①「配置变更」).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.dependencies import CurrentUser, DBSession
from app.models import AlertRule, MT5Account
from app.schemas.alert_rule import AlertRuleCreate, AlertRuleOut, AlertRuleUpdate
from app.services.alert_rules import clear_rule_state
from app.services.audit import AuditActor
from app.services.audit import record as audit_record

router = APIRouter()


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _actor(current: dict, request: Request | None = None) -> AuditActor:
    ip = request.client.host if request is not None and request.client else ""
    return AuditActor(actor_type="user", actor_id=_user_id(current),
                      actor_label=str(current.get("email") or current["sub"])[:120],
                      source_ip=ip or "")


async def _get_owned_rule(db, rule_id: uuid.UUID, current: dict) -> AlertRule:
    rule = await db.get(AlertRule, rule_id)
    if rule is None or str(rule.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    return rule


async def _validate_account(db, account_id: uuid.UUID | None, current: dict):
    if account_id is None:
        return None
    acct = await db.get(MT5Account, account_id)
    if acct is None or (acct.user_id is not None
                        and str(acct.user_id) != current["sub"]):
        raise HTTPException(status_code=404, detail="MT5 账户不存在")
    return acct


@router.get("/rules", response_model=list[AlertRuleOut])
async def list_rules(current: CurrentUser, db: DBSession) -> list[AlertRule]:
    rows = (await db.execute(
        select(AlertRule).where(AlertRule.user_id == _user_id(current))
        .order_by(AlertRule.created_at.desc())
    )).scalars().all()
    return list(rows)


@router.post("/rules", response_model=AlertRuleOut, status_code=201)
async def create_rule(
    payload: AlertRuleCreate, current: CurrentUser, db: DBSession, request: Request
) -> AlertRule:
    await _validate_account(db, payload.account_id, current)
    rule = AlertRule(
        user_id=_user_id(current), name=payload.name, type=payload.type,
        scope=payload.scope, account_id=payload.account_id,
        threshold=Decimal(str(round(payload.threshold, 6))),
        operator=payload.operator,
        metric=payload.metric or "", level=payload.level,
        cooldown_seconds=payload.cooldown_seconds, enabled=True,
        note=payload.note,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await audit_record("alert_rule_change", actor=_actor(current, request),
                       target_type="alert_rule", target_id=str(rule.id),
                       params={"op": "create", "new": {
                           "name": rule.name, "type": rule.type,
                           "threshold": payload.threshold, "level": rule.level,
                           "scope": rule.scope}})
    return rule


@router.patch("/rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(
    rule_id: uuid.UUID, payload: AlertRuleUpdate,
    current: CurrentUser, db: DBSession, request: Request
) -> AlertRule:
    rule = await _get_owned_rule(db, rule_id, current)
    old = {"threshold": float(rule.threshold), "level": rule.level,
           "enabled": rule.enabled, "cooldown_seconds": rule.cooldown_seconds}
    data = payload.model_dump(exclude_unset=True)
    if "threshold" in data and data["threshold"] is not None:
        data["threshold"] = Decimal(str(round(data["threshold"], 6)))
    for field, value in data.items():
        setattr(rule, field, value)
    if rule.type in ("drawdown_pct", "daily_loss_pct") and rule.threshold <= 0:
        raise HTTPException(status_code=422, detail="百分比类规则的阈值必须大于 0")
    if rule.type == "gateway_offline" and rule.threshold < 5:
        raise HTTPException(status_code=422, detail="断线阈值不得小于 5 秒")
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    # A re-tuned rule must be able to fire again immediately.
    clear_rule_state(str(rule.id))
    await audit_record("alert_rule_change", actor=_actor(current, request),
                       target_type="alert_rule", target_id=str(rule.id),
                       params={"op": "update", "old": old,
                               "new": {"threshold": float(rule.threshold),
                                       "level": rule.level,
                                       "enabled": rule.enabled,
                                       "cooldown_seconds": rule.cooldown_seconds}})
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: uuid.UUID, current: CurrentUser, db: DBSession, request: Request
) -> None:
    rule = await _get_owned_rule(db, rule_id, current)
    name, rtype = rule.name, rule.type
    await db.delete(rule)
    await db.commit()
    clear_rule_state(str(rule_id))
    await audit_record("alert_rule_change", actor=_actor(current, request),
                       target_type="alert_rule", target_id=str(rule_id),
                       params={"op": "delete", "old": {"name": name, "type": rtype}})
