"""strategy context rules

Revision ID: 20260417_0009
Revises: 20260417_0008
Create Date: 2026-04-17 17:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260417_0009"
down_revision: Union[str, None] = "20260417_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strategy_context_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("feature_scope", sa.String(length=30), nullable=False),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("feature_value", sa.String(length=120), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False, server_default="downgrade_to_watch"),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="feature_outcome_stat"),
        sa.Column("evidence_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_context_rules_id", "strategy_context_rules", ["id"])
    op.create_index("ix_strategy_context_rules_strategy_id", "strategy_context_rules", ["strategy_id"])
    op.create_index(
        "ix_strategy_context_rules_strategy_version_id",
        "strategy_context_rules",
        ["strategy_version_id"],
    )
    op.create_index("ix_strategy_context_rules_feature_scope", "strategy_context_rules", ["feature_scope"])
    op.create_index("ix_strategy_context_rules_feature_key", "strategy_context_rules", ["feature_key"])
    op.create_index("ix_strategy_context_rules_feature_value", "strategy_context_rules", ["feature_value"])


def downgrade() -> None:
    op.drop_index("ix_strategy_context_rules_feature_value", table_name="strategy_context_rules")
    op.drop_index("ix_strategy_context_rules_feature_key", table_name="strategy_context_rules")
    op.drop_index("ix_strategy_context_rules_feature_scope", table_name="strategy_context_rules")
    op.drop_index("ix_strategy_context_rules_strategy_version_id", table_name="strategy_context_rules")
    op.drop_index("ix_strategy_context_rules_strategy_id", table_name="strategy_context_rules")
    op.drop_index("ix_strategy_context_rules_id", table_name="strategy_context_rules")
    op.drop_table("strategy_context_rules")
