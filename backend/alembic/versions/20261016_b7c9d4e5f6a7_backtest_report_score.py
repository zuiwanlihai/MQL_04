"""backtest_reports.score — 策略综合评分 JSON（评分系统接线）

Revision ID: b7c9d4e5f6a7
Revises: d1a2b3c4e5f6
Create Date: 2026-10-16 11:40:00.000000

评分算法 (app.sandbox.metrics.strategy_score) 与前端面板早已上线，
但报告落库时缺少承接列，分数在 queue 写库环节被丢弃。本迁移补上
可空 JSONB 列：历史报告保持 NULL（前端显示"评分数据缺失，需重跑"），
新完成的回测写入完整评分结构。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7c9d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'd1a2b3c4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'backtest_reports',
        sa.Column(
            'score',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='策略综合评分：{score,grade(S-F),credibility,dimensions,'
                    'highlights,shortcomings}；NULL = 旧报告需重跑回测。',
        ),
    )


def downgrade() -> None:
    op.drop_column('backtest_reports', 'score')
