"""decision context snapshots and feature outcome stats

Revision ID: 20260417_0008
Revises: 20260416_0007
Create Date: 2026-04-17 16:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260417_0008"
down_revision: Union[str, None] = "20260416_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decision_context_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signal_id", sa.Integer(), sa.ForeignKey("signals.id"), nullable=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id"), nullable=True),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id"), nullable=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("decision_phase", sa.String(length=20), nullable=False, server_default="do"),
        sa.Column("planner_action", sa.String(length=30), nullable=False, server_default="watch"),
        sa.Column("executed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("execution_outcome", sa.String(length=30), nullable=True),
        sa.Column("quant_features", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("visual_features", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("calendar_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("news_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("web_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("macro_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("ai_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("position_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_decision_context_snapshots_id", "decision_context_snapshots", ["id"])
    op.create_index("ix_decision_context_snapshots_signal_id", "decision_context_snapshots", ["signal_id"])
    op.create_index("ix_decision_context_snapshots_analysis_run_id", "decision_context_snapshots", ["analysis_run_id"])
    op.create_index("ix_decision_context_snapshots_position_id", "decision_context_snapshots", ["position_id"])
    op.create_index("ix_decision_context_snapshots_strategy_id", "decision_context_snapshots", ["strategy_id"])
    op.create_index(
        "ix_decision_context_snapshots_strategy_version_id",
        "decision_context_snapshots",
        ["strategy_version_id"],
    )
    op.create_index("ix_decision_context_snapshots_ticker", "decision_context_snapshots", ["ticker"])

    op.create_table(
        "feature_outcome_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("feature_scope", sa.String(length=30), nullable=False),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("feature_value", sa.String(length=120), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("executed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("avg_pnl_pct", sa.Float(), nullable=True),
        sa.Column("avg_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("avg_runup_pct", sa.Float(), nullable=True),
        sa.Column("expectancy", sa.Float(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("evidence_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("last_recomputed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_feature_outcome_stats_id", "feature_outcome_stats", ["id"])
    op.create_index("ix_feature_outcome_stats_strategy_id", "feature_outcome_stats", ["strategy_id"])
    op.create_index(
        "ix_feature_outcome_stats_strategy_version_id",
        "feature_outcome_stats",
        ["strategy_version_id"],
    )
    op.create_index("ix_feature_outcome_stats_feature_scope", "feature_outcome_stats", ["feature_scope"])
    op.create_index("ix_feature_outcome_stats_feature_key", "feature_outcome_stats", ["feature_key"])
    op.create_index("ix_feature_outcome_stats_feature_value", "feature_outcome_stats", ["feature_value"])


def downgrade() -> None:
    op.drop_index("ix_feature_outcome_stats_feature_value", table_name="feature_outcome_stats")
    op.drop_index("ix_feature_outcome_stats_feature_key", table_name="feature_outcome_stats")
    op.drop_index("ix_feature_outcome_stats_feature_scope", table_name="feature_outcome_stats")
    op.drop_index("ix_feature_outcome_stats_strategy_version_id", table_name="feature_outcome_stats")
    op.drop_index("ix_feature_outcome_stats_strategy_id", table_name="feature_outcome_stats")
    op.drop_index("ix_feature_outcome_stats_id", table_name="feature_outcome_stats")
    op.drop_table("feature_outcome_stats")

    op.drop_index("ix_decision_context_snapshots_ticker", table_name="decision_context_snapshots")
    op.drop_index("ix_decision_context_snapshots_strategy_version_id", table_name="decision_context_snapshots")
    op.drop_index("ix_decision_context_snapshots_strategy_id", table_name="decision_context_snapshots")
    op.drop_index("ix_decision_context_snapshots_position_id", table_name="decision_context_snapshots")
    op.drop_index("ix_decision_context_snapshots_analysis_run_id", table_name="decision_context_snapshots")
    op.drop_index("ix_decision_context_snapshots_signal_id", table_name="decision_context_snapshots")
    op.drop_index("ix_decision_context_snapshots_id", table_name="decision_context_snapshots")
    op.drop_table("decision_context_snapshots")
