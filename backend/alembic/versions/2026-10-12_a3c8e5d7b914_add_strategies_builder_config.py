"""strategies.builder_config column (P2 visual strategy builder)

Revision ID: a3c8e5d7b914
Revises: f2b9d4e6a820
Create Date: 2026-10-12 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3c8e5d7b914'
down_revision: Union[str, Sequence[str], None] = 'f2b9d4e6a820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'strategies',
        sa.Column(
            'builder_config',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='可视化构建器积木配置（P2）；NULL=非构建器创建或已脱离',
        ),
    )


def downgrade() -> None:
    op.drop_column('strategies', 'builder_config')
