"""add paper_runs.account_id (M12 模拟盘绑定 MT5 账户)

Revision ID: d3e7a1b84c25
Revises: b2c6d9f31a47
Create Date: 2026-10-11 20:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd3e7a1b84c25'
down_revision: Union[str, Sequence[str], None] = 'b2c6d9f31a47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'paper_runs',
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True,
                  comment='Bound MT5 account (M12); NULL = legacy default node.'),
    )
    op.create_foreign_key(
        'fk_paper_runs_account_id', 'paper_runs', 'mt5_accounts',
        ['account_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_paper_runs_account_id'), 'paper_runs', ['account_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_paper_runs_account_id'), table_name='paper_runs')
    op.drop_constraint('fk_paper_runs_account_id', 'paper_runs', type_='foreignkey')
    op.drop_column('paper_runs', 'account_id')
