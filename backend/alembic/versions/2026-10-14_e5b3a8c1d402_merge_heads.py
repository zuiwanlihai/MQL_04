"""合并两个 head：risk_position_log 与 audit_logs.seq

Revision ID: e5b3a8c1d402
Revises: a7e3c9d2b502, c9d2e5a7f104
Create Date: 2026-10-14 21:45:00.000000

2026-10-13 起仓里就有两条 head：``a7e3c9d2b502``（持仓登记表，10-13 20:05）
和它之后新加的 ``b4f1a8c2d703``/``c9d2e5a7f104``（都从 ``a7c9e1f3b205`` 分叉）。
两个 head 并存时 ``alembic upgrade head`` 直接报错退出——也就是说线上升级
自 10-13 起就是断的，而开发库正好停在 a7e3c9d2b502 那一支上：paper_trades
的 suspect 列一直没迁移过去。

这里只做合并，不带任何 DDL：两条支线互不相干，先后顺序无所谓。
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401  (合并版本不含 DDL，保留导入以免 alembic 模板漂移)

# revision identifiers, used by Alembic.
revision: str = 'e5b3a8c1d402'
down_revision: Union[str, Sequence[str], None] = ('a7e3c9d2b502', 'c9d2e5a7f104')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
