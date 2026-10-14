"""Research notebook (M18) API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NotebookCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: str = Field(pattern="^(code|markdown)$")
    source: str = ""
    outputs: list = Field(default_factory=list)


class NotebookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class NotebookUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    cells: list[NotebookCell] | None = None


class NotebookCellRun(BaseModel):
    cell_id: str = Field(min_length=1, max_length=64)
    source: str = Field(max_length=200_000)


class NotebookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    cell_count: int
    created_at: datetime
    updated_at: datetime | None


class NotebookListResponse(BaseModel):
    items: list[NotebookOut]
    total: int


class NotebookDetailOut(NotebookOut):
    cells: list[NotebookCell]


class NotebookRunOut(BaseModel):
    ok: bool
    stdout: str = ""
    result_repr: str | None = None
    charts: list = Field(default_factory=list)
    etype: str | None = None
    message: str | None = None
    traceback: str | None = None
    terminated: str | None = None
    state_reset: bool = False


class QueueOverviewOut(BaseModel):
    running: list[str]
    pending_count: int
    oldest_pending_created_at: datetime | None = None
