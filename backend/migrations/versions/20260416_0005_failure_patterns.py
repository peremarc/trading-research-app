"""failure patterns

Revision ID: 20260416_0005
Revises: 20260416_0004
Create Date: 2026-04-16 20:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0005"
down_revision: Union[str, None] = "20260416_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "failure_patterns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("failure_mode", sa.String(length=50), nullable=False),
        sa.Column("pattern_signature", sa.String(length=160), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("avg_loss_pct", sa.Float(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_failure_patterns_id", "failure_patterns", ["id"])
    op.create_index("ix_failure_patterns_strategy_id", "failure_patterns", ["strategy_id"])
    op.create_index("ix_failure_patterns_strategy_version_id", "failure_patterns", ["strategy_version_id"])


def downgrade() -> None:
    op.drop_index("ix_failure_patterns_strategy_version_id", table_name="failure_patterns")
    op.drop_index("ix_failure_patterns_strategy_id", table_name="failure_patterns")
    op.drop_index("ix_failure_patterns_id", table_name="failure_patterns")
    op.drop_table("failure_patterns")
