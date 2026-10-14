"""Research notebook (M18 研究笔记本) model.

轻量自研研究笔记本：cells 以 JSONB 整体存储（code/markdown 两类），
outputs 随保存持久化（所见即所得）。执行状态（kernel globals）不落库，
只存进程内会话 + 磁盘 pickle 快照，见 services/notebook_kernel.py。
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Notebook(BaseModel, kw_only=True):
    """One research notebook owned by a single user."""

    __tablename__ = "notebooks"
    __table_args__ = (Index("ix_notebooks_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    cells: Mapped[list] = mapped_column(
        JSONB, nullable=False, default_factory=list
    )

    # summary/notes 允许用户附加说明（v1 预留，不进 UI）
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    def __repr__(self) -> str:  # pragma: no cover — debug helper
        return f"<Notebook {self.id} {self.title!r} cells={len(self.cells)}>"
