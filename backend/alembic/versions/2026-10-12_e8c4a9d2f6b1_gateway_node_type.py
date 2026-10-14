"""gateway_nodes: type + ea_key_hash (P4 EA 桥, EB-1)

Revision ID: e8c4a9d2f6b1
Revises: b3d8f1a5c7e2
Create Date: 2026-10-12 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8c4a9d2f6b1'
down_revision: Union[str, Sequence[str], None] = 'b3d8f1a5c7e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gateway_nodes',
        sa.Column('type', sa.String(length=16), nullable=False,
                  server_default='python',
                  comment='python | ea_bridge (P4 EA 桥节点).'),
    )
    op.create_check_constraint(
        'ck_gateway_nodes_type', 'gateway_nodes',
        "type IN ('python', 'ea_bridge')",
    )
    op.add_column(
        'gateway_nodes',
        sa.Column('ea_key_hash', sa.String(length=64), nullable=True,
                  comment='sha256(node_key) — X-EA-Key credential for /ea/* endpoints.'),
    )
    op.create_unique_constraint(
        'uq_gateway_nodes_ea_key_hash', 'gateway_nodes', ['ea_key_hash']
    )


def downgrade() -> None:
    op.drop_constraint('uq_gateway_nodes_ea_key_hash', 'gateway_nodes',
                       type_='unique')
    op.drop_column('gateway_nodes', 'ea_key_hash')
    op.drop_constraint('ck_gateway_nodes_type', 'gateway_nodes',
                       type_='check')
    op.drop_column('gateway_nodes', 'type')
