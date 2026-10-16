"""
Strategy research (M3) request/response schemas.

Mirrors ``frontend/src/types/api.ts``.  The list response never carries
``source_code`` (working copy) — only ``GET /strategies/{id}`` returns it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StrategyStatus = Literal["draft", "active", "paused", "archived"]
StrategyLanguage = Literal["mql5", "python"]
StrategyDirection = Literal["long_only", "short_only", "both"]
Timeframe = Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class ValidationIssue(BaseModel):
    """One AST-validation finding (also reused as 422 detail on freeze)."""

    code: str = Field(description="规则码，如 E001 / I001 / W003")
    severity: Literal["error", "warning", "info"]
    line: int = Field(ge=1)
    column: int = Field(ge=0)
    message: str


class ValidationResult(BaseModel):
    ok: bool = Field(description="无 error 级问题即为 True")
    issues: list[ValidationIssue]


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    language: StrategyLanguage = "python"
    direction: StrategyDirection = "both"
    symbol_ids: list[str] = Field(default_factory=list, max_length=50)
    timeframe: Timeframe = "H1"
    tags: list[str] = Field(default_factory=list, max_length=20)
    template_id: uuid.UUID | None = Field(
        default=None, description="从模板创建时携带，服务端用模板源码初始化工作副本"
    )
    source_code: str | None = Field(
        default=None, max_length=200_000,
        description="初始工作副本源码（构建器生成）；template_id 优先",
    )
    builder_config: dict | None = Field(
        default=None, description="可视化构建器积木配置（P2）；与 source_code 成对提交",
    )
    parameters: dict | None = Field(
        default=None, description="策略级参数覆盖（参数面板提交，合并到模板默认值之上）",
    )


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    direction: StrategyDirection | None = None
    status: StrategyStatus | None = None
    symbol_ids: list[str] | None = Field(default=None, max_length=50)
    timeframe: Timeframe | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    builder_config: dict | None = Field(
        default=None,
        description="可视化构建器积木配置（P2）；显式传 null 清除绑定",
    )
    parameters: dict | None = Field(
        default=None, description="策略级参数覆盖；显式传 null 清除",
    )
    base_updated_at: datetime | None = Field(
        default=None,
        description="乐观锁：客户端持有的 updated_at；不匹配返回 409；null 跳过检查（不推荐）",
    )


class StrategyBuilderSave(BaseModel):
    """原子保存：源码 + builder_config + 元数据在同一事务内一起写入。

    替换前端 BuilderPage 原来两次独立调用（save_source + update）的非原子保存流。
    后端会计算 source_code 的 SHA-256 并存储到 source_code_hash，供构建器下次
    进入时判断源码是否被手动修改过。
    """

    source_code: str = Field(max_length=200_000)
    builder_config: dict = Field(description="可视化构建器积木配置")
    direction: StrategyDirection | None = None
    symbol_ids: list[str] | None = Field(default=None, max_length=50)
    timeframe: Timeframe | None = None
    base_updated_at: datetime | None = Field(
        default=None,
        description="乐观锁：客户端持有的 updated_at；不匹配返回 409",
    )


class StrategySourceUpdate(BaseModel):
    source_code: str = Field(max_length=200_000)
    base_updated_at: datetime | None = Field(
        default=None,
        description="乐观锁：客户端持有的 updated_at；不匹配返回 409",
    )


class StrategyOut(BaseModel):
    """List/detail row WITHOUT source_code (matches frontend ``Strategy``)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    language: StrategyLanguage
    direction: StrategyDirection
    status: StrategyStatus
    current_version: int | None
    symbol_ids: list[str]
    timeframe: str
    tags: list[str]
    parameters: dict | None = None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime | None = None


class StrategyDetailOut(StrategyOut):
    """Detail response adds the working-copy source."""

    source_code: str
    builder_config: dict | None = None
    source_code_hash: str | None = Field(
        default=None,
        description="SHA-256 hex of current source_code; null = migrated before column existed.",
    )


class StrategyListResponse(BaseModel):
    items: list[StrategyOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class StrategyVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    version: int
    source_code: str
    compiled_at: datetime | None
    is_compiled: bool = False
    compile_errors: list[str] | None
    changelog: str
    created_by: uuid.UUID | None
    created_at: datetime


class VersionCreate(BaseModel):
    changelog: str = Field(default="", max_length=200)


class VersionListResponse(BaseModel):
    items: list[StrategyVersionOut]
    total: int


class StrategySourceOut(BaseModel):
    """Response of PUT /strategies/{id}/source."""

    source_code: str
    updated_at: datetime
    validation: ValidationResult


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    language: StrategyLanguage
    category: str
    source_code: str
    parameters: dict[str, str]


class TemplateSummaryOut(BaseModel):
    """Template list item — no source code."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    language: StrategyLanguage
    category: str
    parameters: dict[str, str]


class TemplateListResponse(BaseModel):
    items: list[TemplateSummaryOut]
    total: int
