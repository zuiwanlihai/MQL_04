"""Research notebook endpoints (M18 研究笔记本).

Route order matters: every literal sub-path is declared BEFORE
``/{notebook_id}`` so the uuid converter never swallows it.  Cells are
stored wholesale as JSONB; execution goes through
:mod:`app.services.notebook_kernel` (supervised subprocess per cell,
sandboxed imports, pre-fetched gateway data).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.config import settings
from app.dependencies import CurrentUser, DBSession
from app.models import Notebook
from app.schemas.notebook import (
    NotebookCell,
    NotebookCellRun,
    NotebookCreate,
    NotebookDetailOut,
    NotebookListResponse,
    NotebookOut,
    NotebookRunOut,
    NotebookUpdate,
)
from app.services import notebook_kernel

router = APIRouter()

MAX_CELLS = 100
MAX_CELL_SOURCE_CHARS = 200_000
MAX_TOTAL_SOURCE_CHARS = 1_000_000
MAX_OUTPUT_CHARS = 65_536

DEFAULT_CELLS: list[dict] = [
    {
        "id": "cell-1",
        "type": "code",
        "source": (
            "# 研究笔记本：get_bars 拉行情，plot 画图，Ctrl+Enter 运行\n"
            "# df = get_bars('EURUSD', 'H1', 500)\n"
            "# df['close'].pct_change().std()\n"
            "# plot(df['close'], title='EURUSD H1 close')\n"
        ),
        "outputs": [],
    },
]


async def _get_owned(db, notebook_id: uuid.UUID, current: dict) -> Notebook:
    row = await db.get(Notebook, notebook_id)
    if row is None or str(row.user_id) != current["sub"]:
        raise HTTPException(status_code=404, detail="笔记本不存在")
    return row


def _sanitize_cells(cells: list[NotebookCell]) -> list[NotebookCell]:
    """Bound cell count / source size / persisted output size."""
    if len(cells) > MAX_CELLS:
        raise HTTPException(status_code=422, detail=f"cell 数量超过上限（{MAX_CELLS}）")
    total = 0
    out: list[NotebookCell] = []
    for c in cells:
        if len(c.source) > MAX_CELL_SOURCE_CHARS:
            raise HTTPException(status_code=422, detail="单个 cell 源码超过 200K 字符")
        total += len(c.source)
        kept: list = []
        size = 0
        for item in c.outputs:
            piece = len(str(item))
            if size + piece > MAX_OUTPUT_CHARS:
                kept.append({"kind": "note", "text": "…[输出超限已截断]"})
                break
            size += piece
            kept.append(item)
        out.append(c.model_copy(update={"outputs": kept}) if kept != c.outputs else c)
    if total > MAX_TOTAL_SOURCE_CHARS:
        raise HTTPException(status_code=422, detail="notebook 总源码超过 1M 字符")
    return out


def _detail(row: Notebook) -> NotebookDetailOut:
    return NotebookDetailOut(
        id=row.id,
        title=row.title,
        cell_count=len(row.cells or []),
        created_at=row.created_at,
        updated_at=row.updated_at,
        cells=[NotebookCell.model_validate(c) for c in (row.cells or [])],
    )


# ---------------------------------------------------------------------------
# List / create (literal paths before /{notebook_id})
# ---------------------------------------------------------------------------


@router.get("", response_model=NotebookListResponse)
async def list_notebooks(
    current: CurrentUser,
    db: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> NotebookListResponse:
    uid = uuid.UUID(current["sub"])
    base = select(Notebook).where(Notebook.user_id == uid)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Notebook.updated_at.desc().nullslast())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).scalars()
    items = [
        NotebookOut(
            id=r.id,
            title=r.title,
            cell_count=len(r.cells or []),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    return NotebookListResponse(items=items, total=total)


@router.post("", response_model=NotebookDetailOut, status_code=status.HTTP_201_CREATED)
async def create_notebook(
    payload: NotebookCreate, current: CurrentUser, db: DBSession
) -> NotebookDetailOut:
    row = Notebook(
        user_id=uuid.UUID(current["sub"]),
        title=payload.title.strip() or "未命名笔记本",
        cells=[dict(c) for c in DEFAULT_CELLS],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _detail(row)


# ---------------------------------------------------------------------------
# Per-notebook (id paths last)
# ---------------------------------------------------------------------------


@router.get("/{notebook_id}", response_model=NotebookDetailOut)
async def get_notebook(
    notebook_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> NotebookDetailOut:
    return _detail(await _get_owned(db, notebook_id, current))


@router.patch("/{notebook_id}", response_model=NotebookDetailOut)
async def update_notebook(
    notebook_id: uuid.UUID,
    payload: NotebookUpdate,
    current: CurrentUser,
    db: DBSession,
) -> NotebookDetailOut:
    row = await _get_owned(db, notebook_id, current)
    if payload.title is not None:
        row.title = payload.title.strip() or row.title
    if payload.cells is not None:
        row.cells = [c.model_dump() for c in _sanitize_cells(payload.cells)]
    await db.commit()
    await db.refresh(row)
    return _detail(row)


@router.delete("/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook(
    notebook_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> None:
    row = await _get_owned(db, notebook_id, current)
    await db.delete(row)
    await db.commit()
    notebook_kernel.reset_session(row.id)


@router.post("/{notebook_id}/run", response_model=NotebookRunOut)
async def run_cell(
    notebook_id: uuid.UUID,
    payload: NotebookCellRun,
    current: CurrentUser,
    db: DBSession,
) -> NotebookRunOut:
    if not settings.NOTEBOOK_ENABLED:
        raise HTTPException(status_code=409, detail="研究笔记本功能未启用")
    await _get_owned(db, notebook_id, current)
    if len(payload.source) > MAX_CELL_SOURCE_CHARS:
        raise HTTPException(status_code=422, detail="cell 源码超过 200K 字符")
    outcome = await notebook_kernel.run_cell(notebook_id, payload.source)
    return NotebookRunOut(
        ok=outcome.ok,
        stdout=outcome.stdout,
        result_repr=outcome.result_repr,
        charts=outcome.charts,
        etype=outcome.etype,
        message=outcome.message,
        traceback=outcome.traceback,
        terminated=outcome.terminated,
        state_reset=outcome.state_reset,
    )


@router.post("/{notebook_id}/restart", response_model=NotebookRunOut)
async def restart_kernel(
    notebook_id: uuid.UUID, current: CurrentUser, db: DBSession
) -> NotebookRunOut:
    await _get_owned(db, notebook_id, current)
    notebook_kernel.reset_session(notebook_id)
    return NotebookRunOut(ok=True, message="内核已重启，变量状态已清空")
