"""candidate validation snapshots

Revision ID: 20260416_0007
Revises: 20260416_0006
Create Date: 2026-04-16 21:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0007"
down_revision: Union[str, None] = "20260416_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_validation_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("trade_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_pnl_pct", sa.Float(), nullable=True),
        sa.Column("avg_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("evaluation_status", sa.String(length=20), nullable=False, server_default="insufficient_data"),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_candidate_validation_snapshots_id", "candidate_validation_snapshots", ["id"])
    op.create_index(
        "ix_candidate_validation_snapshots_strategy_id",
        "candidate_validation_snapshots",
        ["strategy_id"],
    )
    op.create_index(
        "ix_candidate_validation_snapshots_strategy_version_id",
        "candidate_validation_snapshots",
        ["strategy_version_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_validation_snapshots_strategy_version_id", table_name="candidate_validation_snapshots")
    op.drop_index("ix_candidate_validation_snapshots_strategy_id", table_name="candidate_validation_snapshots")
    op.drop_index("ix_candidate_validation_snapshots_id", table_name="candidate_validation_snapshots")
    op.drop_table("candidate_validation_snapshots")
