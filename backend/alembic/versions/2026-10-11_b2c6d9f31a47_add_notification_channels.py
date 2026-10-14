"""add notification_channels table (M10 告警通知渠道)

Revision ID: b2c6d9f31a47
Revises: a1f5c8e20934
Create Date: 2026-10-11 11:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c6d9f31a47'
down_revision: Union[str, Sequence[str], None] = 'a1f5c8e20934'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_channels',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='Owning platform user.'),
        sa.Column('kind', sa.String(length=16), nullable=False,
                  comment='email | telegram | dingtalk | wecom.'),
        sa.Column('target', sa.String(length=500), nullable=False,
                  comment="Email address / 'bot_token:chat_id' / webhook URL. Secrets — mask in responses."),
        sa.Column('min_level', sa.String(length=8), nullable=False,
                  comment='Only alerts at or above this level are dispatched.'),
        sa.Column('enabled', sa.Boolean(), nullable=False,
                  comment='Disabled channels are skipped by the dispatcher.'),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True,
                  comment='Last successful test-send (email channels flip this).'),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'kind', 'target', name='uq_notify_user_kind_target'),
    )
    op.create_index(op.f('ix_notification_channels_user_id'), 'notification_channels', ['user_id'], unique=False)
    op.create_index('ix_notify_channels_user_enabled', 'notification_channels', ['user_id', 'enabled'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_notify_channels_user_enabled', table_name='notification_channels')
    op.drop_index(op.f('ix_notification_channels_user_id'), table_name='notification_channels')
    op.drop_table('notification_channels')
