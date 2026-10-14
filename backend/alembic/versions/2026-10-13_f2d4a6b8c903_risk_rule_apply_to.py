"""risk_rules apply_to (ai | manual | both) order-source scope

Revision ID: f2d4a6b8c903
Revises: e8b1c3d5a702
Create Date: 2026-10-13 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2d4a6b8c903'
down_revision: Union[str, Sequence[str], None] = 'e8b1c3d5a702'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'risk_rules',
        sa.Column('apply_to', sa.String(length=8), nullable=False, server_default='both',
                  comment='ai | manual | both — 订单来源适用范围。'),
    )


def downgrade() -> None:
    op.drop_column('risk_rules', 'apply_to')
