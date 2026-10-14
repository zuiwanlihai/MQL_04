"""add backtest_analyses (Walk-Forward / Monte Carlo, PRD 回测中心 P1)

Revision ID: a7c3e1f92b48
Revises: f6a9b3d47e52
Create Date: 2026-10-12 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7c3e1f92b48'
down_revision: Union[str, Sequence[str], None] = 'f6a9b3d47e52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'backtest_analyses',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='Owning platform user.'),
        sa.Column('task_id', sa.UUID(), nullable=False, comment='Source backtest task (config template).'),
        sa.Column('kind', sa.String(length=16), nullable=False, comment='walk_forward | monte_carlo.'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='running | completed | failed.'),
        sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Run parameters + WF child task window mapping.'),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='Aggregated analysis output (cached on terminal).'),
        sa.Column('error', sa.Text(), nullable=True, comment='User-facing failure detail (WF child failure etc.).'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['backtest_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('walk_forward', 'monte_carlo')", name='ck_backtest_analyses_kind'),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name='ck_backtest_analyses_status'),
    )
    op.create_index('ix_backtest_analyses_task', 'backtest_analyses', ['task_id'], unique=False)
    op.create_index('ix_backtest_analyses_user_created', 'backtest_analyses', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_backtest_analyses_user_created', table_name='backtest_analyses')
    op.drop_index('ix_backtest_analyses_task', table_name='backtest_analyses')
    op.drop_table('backtest_analyses')
