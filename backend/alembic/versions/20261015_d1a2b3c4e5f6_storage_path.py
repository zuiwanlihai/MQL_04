"""synthetic_datasets: storage_path + make bars nullable (Parquet-first storage)

Revision ID: d1a2b3c4e5f6
Revises: a1b2c3d4e5f6
Create Date: 2026-10-15 18:00:00.000000

合成数据集重构：
  - 新增 ``storage_path`` 列（varchar 512），后端生成时写 Parquet 文件。
  - ``bars`` JSONB 改为 nullable —— 老数据仍保留在 bars 里；新数据集
    写 Parquet，bars 列存 NULL，节省大量空间（13 个月 M1 从 34MB JSONB
    降到 18MB zstd Parquet）。
  - 所有读路径先看 storage_path，有则走 Parquet，没有则 fallback 到 bars。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1a2b3c4e5f6'
down_revision: Union[str, Sequence[str], None] = ('f3b8a92c1d4e', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'synthetic_datasets',
        sa.Column('storage_path', sa.String(512), nullable=True,
                  comment='Relative path under synthetic runtime dir. NULL = legacy JSONB bars.'),
    )
    # bars 保持原列名 + 类型，只放宽 NOT NULL → NULL
    op.alter_column('synthetic_datasets', 'bars', nullable=True)


def downgrade() -> None:
    op.alter_column('synthetic_datasets', 'bars', nullable=False)
    op.drop_column('synthetic_datasets', 'storage_path')
