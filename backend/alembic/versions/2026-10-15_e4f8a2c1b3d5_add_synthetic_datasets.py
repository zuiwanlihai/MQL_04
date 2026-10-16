"""add synthetic_datasets table

Revision ID: e4f8a2c1b3d5
Revises: 9c50c3f084a0
Create Date: 2026-10-15 12:10:00.000000

一张表存前端可视化生成的合成 M1 K 线数据集（元数据 + JSONB bars 数组）。
回测 data_source="synthetic" 和虚拟下单 replay 模式都从这里读。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = 'e4f8a2c1b3d5'
down_revision = '9c50c3f084a0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'synthetic_datasets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('name', sa.String(128), nullable=False, index=True),
        sa.Column('description', sa.String(512), nullable=True),
        sa.Column('symbol', sa.String(32), nullable=False, server_default='XAUUSD'),
        sa.Column('base_price', sa.Float(), nullable=False, server_default=sa.text('1900.0')),
        sa.Column('volatility', sa.Float(), nullable=False, server_default=sa.text('0.0005')),
        sa.Column('first_time', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('last_time', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('bars', JSONB(), nullable=False),
        sa.Column('bar_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
    )


def downgrade() -> None:
    op.drop_table('synthetic_datasets')
