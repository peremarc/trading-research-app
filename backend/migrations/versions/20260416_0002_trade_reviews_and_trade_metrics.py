"""trade reviews and trade metrics

Revision ID: 20260416_0002
Revises: 20260416_0001
Create Date: 2026-04-16 19:25:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0002"
down_revision: Union[str, None] = "20260416_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("positions", sa.Column("pnl_pct", sa.Float(), nullable=True))
    op.add_column("positions", sa.Column("max_drawdown_pct", sa.Float(), nullable=True))
    op.add_column("positions", sa.Column("max_runup_pct", sa.Float(), nullable=True))
    op.add_column("positions", sa.Column("review_status", sa.String(length=20), nullable=False, server_default="pending"))

    op.create_table(
        "trade_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("outcome_label", sa.String(length=20), nullable=False),
        sa.Column("cause_category", sa.String(length=40), nullable=False),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column("lesson_learned", sa.Text(), nullable=False),
        sa.Column("proposed_strategy_change", sa.Text(), nullable=True),
        sa.Column("should_modify_strategy", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_trade_reviews_id", "trade_reviews", ["id"])
    op.create_index("ix_trade_reviews_position_id", "trade_reviews", ["position_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_reviews_position_id", table_name="trade_reviews")
    op.drop_index("ix_trade_reviews_id", table_name="trade_reviews")
    op.drop_table("trade_reviews")

    op.drop_column("positions", "review_status")
    op.drop_column("positions", "max_runup_pct")
    op.drop_column("positions", "max_drawdown_pct")
    op.drop_column("positions", "pnl_pct")
