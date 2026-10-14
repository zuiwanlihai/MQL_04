"""Audit log query / chain-verify / export endpoints (M7, PRD M7 ③④).

Admin-gated read-only surface over the write-once hash chain.  There is
deliberately **no** update/delete path — tamper evidence is provided by
``POST /audit/verify`` which re-walks the whole chain and raises a
「审计链校验失败」critical alert on the first broken link.  The export
endpoint records its own ``audit_export`` row (导出行为本身也记入审计).
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, DBSession
from app.models import AuditLog, User
from app.schemas.audit import AuditListResponse, AuditVerifyOut
from app.services.alert import emit as alert_emit
from app.services.audit import AuditActor, verify_chain
from app.services.audit import record as audit_record

router = APIRouter()

_PAGE_SIZE_MAX = 200
_EXPORT_ROWS_MAX = 10000
_RESULTS = ("success", "failure", "blocked")


def _user_id(current: dict) -> uuid.UUID:
    return uuid.UUID(current["sub"])


def _actor(current: dict, request: Request | None = None) -> AuditActor:
    ip = request.client.host if request is not None and request.client else ""
    return AuditActor(actor_type="user", actor_id=_user_id(current),
                      actor_label=str(current.get("email") or current["sub"])[:120],
                      source_ip=ip or "")


async def _require_admin(db: AsyncSession, current: dict) -> None:
    user = await db.get(User, _user_id(current))
    if user is None or (user.role != "admin" and not user.is_superuser):
        raise HTTPException(status_code=403, detail="权限不足：审计功能仅管理员可用")


def _aware(dt: datetime | None) -> datetime | None:
    """Treat naive ISO inputs as UTC (time contract M3 ⑤ is UTC everywhere)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _filters(
    start: datetime | None,
    end: datetime | None,
    actor_id: uuid.UUID | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    result: str | None,
) -> list:
    conds = []
    if start is not None:
        conds.append(AuditLog.created_at >= _aware(start))
    if end is not None:
        conds.append(AuditLog.created_at <= _aware(end))
    if actor_id is not None:
        conds.append(AuditLog.actor_id == actor_id)
    if action:
        conds.append(AuditLog.action == action)
    if target_type:
        conds.append(AuditLog.target_type == target_type)
    if target_id:
        conds.append(AuditLog.target_id == target_id)
    if result:
        if result not in _RESULTS:
            raise HTTPException(status_code=422, detail="无效的结果筛选值")
        conds.append(AuditLog.result == result)
    return conds


@router.get("", response_model=AuditListResponse)
async def list_audit_logs(
    current: CurrentUser, db: DBSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_PAGE_SIZE_MAX),
    start: datetime | None = Query(default=None, description="起始时间（UTC ISO）"),
    end: datetime | None = Query(default=None, description="截止时间（UTC ISO）"),
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None, max_length=48),
    target_type: str | None = Query(default=None, max_length=24),
    target_id: str | None = Query(default=None, max_length=64),
    result: str | None = Query(default=None),
) -> AuditListResponse:
    await _require_admin(db, current)
    conds = _filters(start, end, actor_id, action, target_type, target_id, result)
    total = (await db.execute(
        select(func.count()).select_from(AuditLog).where(*conds)
    )).scalar_one()
    rows = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return AuditListResponse(
        items=list(rows), total=total, page=page, page_size=page_size,
        total_pages=max((total + page_size - 1) // page_size, 1),
    )


@router.post("/verify", response_model=AuditVerifyOut)
async def verify_audit_chain(
    current: CurrentUser, db: DBSession, request: Request
) -> AuditVerifyOut:
    """Re-walk the whole hash chain; broken link → critical alert + audit row."""
    await _require_admin(db, current)
    total, broken = await verify_chain()
    ok = broken == -1
    checked_at = datetime.now(UTC)
    await audit_record(
        "audit_verify", actor=_actor(current, request),
        target_type="system", target_id="audit_chain",
        params={"total": total, "ok": ok, "broken_index": broken},
        result="success" if ok else "failure",
    )
    if not ok:
        await alert_emit(
            source="audit_chain", level="critical",
            title=f"审计链校验失败（第 {broken + 1} 条断链）",
            message=(f"共 {total} 条审计记录，校验发现链在索引 {broken}（0 基）处断裂，"
                     "可能存在篡改或删除，请立即排查。"),
        )
    return AuditVerifyOut(total=total, ok=ok, broken_index=broken,
                          checked_at=checked_at)


_CSV_COLUMNS = (
    "created_at", "ts_ms", "actor_type", "actor_label", "action",
    "target_type", "target_id", "result", "error_code", "risk_decision",
    "source_ip", "correlation_id", "params", "hash",
)


@router.get("/export")
async def export_audit_logs(
    current: CurrentUser, db: DBSession, request: Request,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None, max_length=48),
    target_type: str | None = Query(default=None, max_length=24),
    target_id: str | None = Query(default=None, max_length=64),
    result: str | None = Query(default=None),
) -> Response:
    """CSV/JSON export of the filtered chain slice; the export itself is audited."""
    await _require_admin(db, current)
    conds = _filters(start, end, actor_id, action, target_type, target_id, result)
    rows = (await db.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .limit(_EXPORT_ROWS_MAX)
    )).scalars().all()

    filters = {"start": str(start) if start else None,
               "end": str(end) if end else None,
               "actor_id": str(actor_id) if actor_id else None,
               "action": action, "target_type": target_type,
               "target_id": target_id, "result": result}
    await audit_record("audit_export", actor=_actor(current, request),
                       target_type="system", target_id="audit_chain",
                       params={"format": format, "rows": len(rows),
                               "filters": filters})

    if format == "json":
        payload = [
            {c: (str(r.created_at) if c == "created_at"
                 else json.dumps(getattr(r, c), ensure_ascii=False, default=str)
                 if c == "params"
                 else getattr(r, c))
             for c in _CSV_COLUMNS}
            for r in rows
        ]
        body = json.dumps(payload, ensure_ascii=False, default=str)
        media = "application/json"
        filename = "audit_logs.json"
    else:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_COLUMNS)
        for r in rows:
            writer.writerow([
                r.created_at.isoformat() if r.created_at else "",
                r.ts_ms, r.actor_type, r.actor_label, r.action,
                r.target_type, r.target_id, r.result, r.error_code or "",
                r.risk_decision or "", r.source_ip, r.correlation_id,
                json.dumps(r.params, ensure_ascii=False, default=str),
                r.hash,
            ])
        body = "\ufeff" + buf.getvalue()  # BOM: Excel 中文不乱码
        media = "text/csv; charset=utf-8"
        filename = "audit_logs.csv"

    return Response(
        content=body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
