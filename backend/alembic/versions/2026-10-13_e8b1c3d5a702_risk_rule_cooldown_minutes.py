"""risk_rules cooldown_minutes for consecutive_loss_cooldown rule

Revision ID: e8b1c3d5a702
Revises: c5a9e4f7b801
Create Date: 2026-10-13 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8b1c3d5a702'
down_revision: Union[str, Sequence[str], None] = 'c5a9e4f7b801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'risk_rules',
        sa.Column('cooldown_minutes', sa.Integer(), nullable=True,
                  comment='consecutive_loss_cooldown only: pause minutes after N consecutive losses.'),
    )


def downgrade() -> None:
    op.drop_column('risk_rules', 'cooldown_minutes')
