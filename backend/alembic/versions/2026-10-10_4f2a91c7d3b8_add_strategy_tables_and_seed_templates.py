"""add strategy tables and seed templates

Revision ID: 4f2a91c7d3b8
Revises: 828a159b3c71
Create Date: 2026-10-10 21:10:00.000000

"""
import json
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4f2a91c7d3b8'
down_revision: Union[str, Sequence[str], None] = '828a159b3c71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "shared" / "sdk" / "templates"


def _templates() -> list[dict]:
    import sys

    shared_dir = str(_TEMPLATE_DIR.parent.parent)  # <repo>/shared
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)
    from sdk.templates import seed_rows  # type: ignore

    rows = []
    for row in seed_rows():
        row = dict(row)
        row["parameters"] = json.dumps(row["parameters"])
        rows.append(row)
    return rows


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'strategies',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='Owning platform user.'),
        sa.Column('name', sa.String(length=120), nullable=False, comment='Strategy name, unique per user.'),
        sa.Column('description', sa.Text(), nullable=False, comment='Free-form description.'),
        sa.Column('language', sa.String(length=16), nullable=False, comment='python | mql5 (MVP only python is accepted by the API).'),
        sa.Column('direction', sa.String(length=16), nullable=False, comment='long_only | short_only | both'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='draft | active | paused | archived'),
        sa.Column('symbol_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Declared symbol names, e.g. [\'EURUSD\']. No FK — symbols come from the gateway at runtime.'),
        sa.Column('timeframe', sa.String(length=8), nullable=False, comment='Primary bar period (M1/M5/M15/M30/H1/H4/D1).'),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='User tags for filtering.'),
        sa.Column('current_version', sa.Integer(), nullable=True, comment='Latest frozen version number; None = never frozen.'),
        sa.Column('source_code', sa.Text(), nullable=False, comment='Working copy edited in the web editor. Excluded from list responses; only returned by the detail endpoint.'),
        sa.Column('created_by', sa.UUID(), nullable=True, comment='User who created the strategy.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_strategies_user_name'),
    )
    op.create_index('ix_strategies_user_status', 'strategies', ['user_id', 'status'], unique=False)
    op.create_index(op.f('ix_strategies_user_id'), 'strategies', ['user_id'], unique=False)

    op.create_table(
        'strategy_versions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('strategy_id', sa.UUID(), nullable=False, comment='Parent strategy.'),
        sa.Column('version', sa.Integer(), nullable=False, comment='Monotonically increasing version number (1-based).'),
        sa.Column('source_code', sa.Text(), nullable=False, comment='Frozen source code at this version.'),
        sa.Column('changelog', sa.String(length=200), nullable=False, comment='User-supplied note for this freeze.'),
        sa.Column('compiled_at', sa.DateTime(timezone=True), nullable=True, comment='Compile time — filled by the backtest slice (M4).'),
        sa.Column('compile_errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='Compile error list — filled by the backtest slice (M4).'),
        sa.Column('created_by', sa.UUID(), nullable=True, comment='User who froze this version.'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('strategy_id', 'version', name='uq_strategy_versions_strategy_version'),
    )
    op.create_index(op.f('ix_strategy_versions_strategy_id'), 'strategy_versions', ['strategy_id'], unique=False)

    op.create_table(
        'strategy_templates',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False, comment='Template name (unique).'),
        sa.Column('description', sa.Text(), nullable=False, comment='What the template demonstrates.'),
        sa.Column('language', sa.String(length=16), nullable=False, comment='python | mql5.'),
        sa.Column('category', sa.String(length=32), nullable=False, comment='trend | mean_reversion | grid | risk_management …'),
        sa.Column('sort_order', sa.Integer(), nullable=False, comment='Display order in the template gallery.'),
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='Default parameter map shown in the create dialog.'),
        sa.Column('source_code', sa.Text(), nullable=False, comment='Template source code (read-only).'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    bind = op.get_bind()
    for row in _templates():
        bind.execute(
            sa.text(
                "INSERT INTO strategy_templates "
                "(id, name, description, language, category, sort_order, parameters, source_code) "
                "VALUES (:id, :name, :description, :language, :category, :sort_order, "
                "CAST(:parameters AS jsonb), :source_code) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            row,
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('strategy_templates')
    op.drop_index(op.f('ix_strategy_versions_strategy_id'), table_name='strategy_versions')
    op.drop_table('strategy_versions')
    op.drop_index(op.f('ix_strategies_user_status'), table_name='strategies')
    op.drop_index(op.f('ix_strategies_user_id'), table_name='strategies')
    op.drop_table('strategies')
