"""strategy evolution events

Revision ID: 20260416_0003
Revises: 20260416_0002
Create Date: 2026-04-16 19:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0003"
down_revision: Union[str, None] = "20260416_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "strategy_change_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("source_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("new_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("trade_review_id", sa.Integer(), sa.ForeignKey("trade_reviews.id"), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("proposed_change", sa.Text(), nullable=True),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        sa.Column("applied_automatically", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_change_events_id", "strategy_change_events", ["id"])
    op.create_index("ix_strategy_change_events_strategy_id", "strategy_change_events", ["strategy_id"])

    op.create_table(
        "strategy_activation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("activated_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=False),
        sa.Column("previous_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("activation_reason", sa.Text(), nullable=False),
        sa.Column("activated_automatically", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_activation_events_id", "strategy_activation_events", ["id"])
    op.create_index("ix_strategy_activation_events_strategy_id", "strategy_activation_events", ["strategy_id"])
    op.create_index("ix_strategy_activation_events_activated_version_id", "strategy_activation_events", ["activated_version_id"])


def downgrade() -> None:
    op.drop_index("ix_strategy_activation_events_activated_version_id", table_name="strategy_activation_events")
    op.drop_index("ix_strategy_activation_events_strategy_id", table_name="strategy_activation_events")
    op.drop_index("ix_strategy_activation_events_id", table_name="strategy_activation_events")
    op.drop_table("strategy_activation_events")

    op.drop_index("ix_strategy_change_events_strategy_id", table_name="strategy_change_events")
    op.drop_index("ix_strategy_change_events_id", table_name="strategy_change_events")
    op.drop_table("strategy_change_events")
