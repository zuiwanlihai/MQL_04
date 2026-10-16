"""paper_runs: add data_source + synthetic_dataset_id (M5-合成数据集回放)

Revision ID: a1b2c3d4e5f6
Revises: e4f8a2c1b3d5
Create Date: 2026-10-15 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e4f8a2c1b3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'paper_runs',
        sa.Column('data_source', sa.String(length=16), nullable=False,
                  server_default='gateway',
                  comment='行情来源：gateway=网关实时 / synthetic=合成数据集回放。'),
    )
    op.add_column(
        'paper_runs',
        sa.Column('synthetic_dataset_id', sa.Integer(), nullable=True,
                  comment='data_source=synthetic 时：SyntheticDataset 主键（Integer）。'),
    )
    op.create_foreign_key(
        'fk_paper_runs_synthetic_dataset', 'paper_runs', 'synthetic_datasets',
        ['synthetic_dataset_id'], ['id'], ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_paper_runs_synthetic_dataset_id'), 'paper_runs',
        ['synthetic_dataset_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_paper_runs_synthetic_dataset_id'), table_name='paper_runs')
    op.drop_constraint('fk_paper_runs_synthetic_dataset', 'paper_runs', type_='foreignkey')
    op.drop_column('paper_runs', 'synthetic_dataset_id')
    op.drop_column('paper_runs', 'data_source')
