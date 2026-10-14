"""add alert_rules table (M15 自定义告警规则引擎, PRD M7 P0)

Revision ID: e4f8a2c95d31
Revises: d3e7a1b84c25
Create Date: 2026-10-11 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e4f8a2c95d31'
down_revision: Union[str, Sequence[str], None] = 'd3e7a1b84c25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alert_rules',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='Owning platform user (rules are per-user, like channels).'),
        sa.Column('name', sa.String(length=100), nullable=False,
                  comment='Display name shown in the alert title.'),
        sa.Column('type', sa.String(length=24), nullable=False,
                  comment='drawdown_pct | daily_loss_pct | gateway_offline | trade_filled | custom_metric.'),
        sa.Column('scope', sa.String(length=16), nullable=False,
                  comment='global | account.'),
        sa.Column('account_id', postgresql.UUID(as_uuid=True), nullable=True,
                  comment='Bound account when scope=account.'),
        sa.Column('threshold', sa.Numeric(precision=20, scale=6), nullable=False,
                  comment='Percent / seconds / absolute value depending on type.'),
        sa.Column('operator', sa.String(length=4), nullable=False,
                  comment='Comparison for custom_metric: lt | lte | gt | gte.'),
        sa.Column('metric', sa.String(length=32), nullable=False,
                  comment='Run mirror field for custom_metric.'),
        sa.Column('level', sa.String(length=8), nullable=False,
                  comment='AlertEvent level when the rule fires.'),
        sa.Column('cooldown_seconds', sa.Integer(), nullable=False,
                  comment='Per-subject re-alert suppression window.'),
        sa.Column('enabled', sa.Boolean(), nullable=False,
                  comment='Disabled rules are ignored by the evaluator.'),
        sa.Column('note', sa.String(length=200), nullable=False,
                  comment='Operator note.'),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['mt5_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_alert_rules_user_id'), 'alert_rules', ['user_id'], unique=False)
    op.create_index(op.f('ix_alert_rules_account_id'), 'alert_rules', ['account_id'], unique=False)
    op.create_index('ix_alert_rules_user_enabled', 'alert_rules', ['user_id', 'enabled'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_alert_rules_user_enabled', table_name='alert_rules')
    op.drop_index(op.f('ix_alert_rules_account_id'), table_name='alert_rules')
    op.drop_index(op.f('ix_alert_rules_user_id'), table_name='alert_rules')
    op.drop_table('alert_rules')
