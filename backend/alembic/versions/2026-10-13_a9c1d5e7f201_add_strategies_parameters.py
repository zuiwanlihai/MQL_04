"""strategies.parameters column (strategy params panel)

Revision ID: a9c1d5e7f201
Revises: e9d5b0c8a3f7
Create Date: 2026-10-13 07:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9c1d5e7f201'
down_revision: Union[str, Sequence[str], None] = 'e9d5b0c8a3f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'strategies',
        sa.Column(
            'parameters',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Strategy-level parameter overrides editable from the params panel.',
        ),
    )


def downgrade() -> None:
    op.drop_column('strategies', 'parameters')