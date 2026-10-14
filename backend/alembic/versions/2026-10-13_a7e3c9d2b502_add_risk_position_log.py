"""add risk_position_log

Revision ID: a7e3c9d2b502
Revises: a7c9e1f3b205
Create Date: 2026-10-13

持仓登记表：登记所有"见过的持仓"，平仓后按票号补查成交盈亏，
作为终端按日期历史同步失效时的风控数据源（连亏冷却/窗口限频）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a7e3c9d2b502"
down_revision = "a7c9e1f3b205"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_position_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ticket", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("open_time", sa.BigInteger(), nullable=False),
        sa.Column("closed_at", sa.BigInteger(), nullable=True),
        sa.Column("closed_profit", sa.Float(), nullable=True),
        sa.UniqueConstraint("account_id", "ticket", name="uq_risk_poslog_acct_ticket"),
    )
    op.create_index("ix_risk_position_log_account_id", "risk_position_log", ["account_id"])
    op.create_index("ix_risk_poslog_acct_open", "risk_position_log", ["account_id", "open_time"])
    op.create_index("ix_risk_poslog_acct_closed", "risk_position_log", ["account_id", "closed_at"])


def downgrade() -> None:
    op.drop_index("ix_risk_poslog_acct_closed", table_name="risk_position_log")
    op.drop_index("ix_risk_poslog_acct_open", table_name="risk_position_log")
    op.drop_index("ix_risk_position_log_account_id", table_name="risk_position_log")
    op.drop_table("risk_position_log")
