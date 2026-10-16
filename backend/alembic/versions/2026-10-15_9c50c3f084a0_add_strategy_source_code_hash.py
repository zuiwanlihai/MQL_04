"""add_strategy_source_code_hash

Revision ID: 9c50c3f084a0
Revises: e5b3a8c1d402
Create Date: 2026-10-15 09:53:19.120773

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9c50c3f084a0'
down_revision: Union[str, Sequence[str], None] = 'e5b3a8c1d402'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'strategies',
        sa.Column(
            'source_code_hash', sa.String(length=64), nullable=True,
            comment='SHA-256 hex of the current source_code; used by the visual '
                    'builder to detect if the user manually edited the code since '
                    'last builder-save.',
        ),
    )


def downgrade() -> None:
    op.drop_column('strategies', 'source_code_hash')
