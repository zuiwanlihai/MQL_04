"""backtest_tasks.synthetic_dataset_id (M19)

data_source=synthetic 时引用 synthetic_datasets 主键，与 paper_runs 同模式。

Revision ID: 2026-10-15_f3b8a92c1d4e
Revises: 2026-10-15_a1b2c3d4e5f6
Create Date: 2026-10-15 13:45:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "f3b8a92c1d4e"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backtest_tasks",
        sa.Column(
            "synthetic_dataset_id", sa.Integer(), nullable=True,
            comment="data_source=synthetic 时：SyntheticDataset 主键。",
        ),
    )
    op.create_index(
        "ix_backtest_tasks_synthetic_dataset_id",
        "backtest_tasks", ["synthetic_dataset_id"], unique=False,
    )
    op.create_foreign_key(
        "fk_backtest_tasks_synthetic_dataset_id",
        "backtest_tasks", "synthetic_datasets",
        ["synthetic_dataset_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_backtest_tasks_synthetic_dataset_id", "backtest_tasks", type_="foreignkey")
    op.drop_index("ix_backtest_tasks_synthetic_dataset_id", table_name="backtest_tasks")
    op.drop_column("backtest_tasks", "synthetic_dataset_id")
