"""notebooks table (M18 research notebook)

Revision ID: b8f3e2a6c914
Revises: e5a1c7d9f204
Create Date: 2026-10-12 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8f3e2a6c914'
down_revision: Union[str, Sequence[str], None] = 'e5a1c7d9f204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notebooks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('cells', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_notebooks_user_id', 'notebooks', ['user_id'])
    op.create_index('ix_notebooks_user_updated', 'notebooks', ['user_id', 'updated_at'])


def downgrade() -> None:
    op.drop_index('ix_notebooks_user_updated', table_name='notebooks')
    op.drop_index('ix_notebooks_user_id', table_name='notebooks')
    op.drop_table('notebooks')
