"""SyntheticDataset model — 前端可视化虚拟数据生成的合成 K 线存储。

一张表存元数据 + JSONB bars 数组。回测 data_source="synthetic" 时
worker 从这里读 bar 直接喂给 bars_from_local()；虚拟下单 paper run
可指定 synthetic_dataset_id 走 replay 模式。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class SyntheticDataset(BaseModel, kw_only=True):
    """一份命名的合成 K 线数据集（M1 基础粒度）。

    覆盖 ``BaseModel.id`` 为 Integer 自增主键，与迁移脚本 ``e4f8a2c1b3d5``
    保持一致。``paper_runs.synthetic_dataset_id`` 外键引用此列。

    注意：由于父类 BaseModel 继承了 MappedAsDataclass，每个 mapped_column
    都会变成 dataclass 字段——没有显式 ``default=`` 的列会变成 __init__ 必填参数。
    所有 nullable=True 的列必须加上 ``default=None``，否则 ORM 构造时会报缺参。
    """

    __tablename__ = "synthetic_datasets"

    # 覆盖 BaseModel 的 UUID 主键为 Integer 自增主键
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, sort_order=-100,
        default=None,
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True, default=None)

    # 生成参数（复用时用得上）
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="XAUUSD")
    base_price: Mapped[float] = mapped_column(nullable=False, default=1900.0)
    volatility: Mapped[float] = mapped_column(nullable=False, default=0.0005)

    # 时间范围（秒级 epoch）
    first_time: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_time: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # bar 数组（升序）：{time,open,high,low,close,volume}
    # nullable=True —— 新数据集走 Parquet（storage_path），bars 留空
    bars: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="Legacy JSONB bars. NULL when storage_path is set (Parquet).",
    )
    storage_path: Mapped[str | None] = mapped_column(
        String(512), nullable=True, default=None,
        comment="Relative path under synthetic runtime dir. NULL = legacy JSONB.",
    )
    bar_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<SyntheticDataset {self.name} n={self.bar_count}>"
