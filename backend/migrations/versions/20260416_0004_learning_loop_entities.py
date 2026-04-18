"""learning loop entities

Revision ID: 20260416_0004
Revises: 20260416_0003
Create Date: 2026-04-16 20:25:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0004"
down_revision: Union[str, None] = "20260416_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("watchlist_item_id", sa.Integer(), sa.ForeignKey("watchlist_items.id"), nullable=True),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column("signal_type", sa.String(length=30), nullable=False),
        sa.Column("signal_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=True),
        sa.Column("entry_zone", sa.JSON(), nullable=False),
        sa.Column("stop_zone", sa.JSON(), nullable=False),
        sa.Column("target_zone", sa.JSON(), nullable=False),
        sa.Column("signal_context", sa.JSON(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_signals_id", "signals", ["id"])
    op.create_index("ix_signals_strategy_id", "signals", ["strategy_id"])
    op.create_index("ix_signals_strategy_version_id", "signals", ["strategy_version_id"])
    op.create_index("ix_signals_watchlist_item_id", "signals", ["watchlist_item_id"])
    op.create_index("ix_signals_ticker", "signals", ["ticker"])

    op.create_table(
        "strategy_scorecards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("signals_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("executed_trades_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("closed_trades_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Float(), nullable=True),
        sa.Column("avg_return_pct", sa.Float(), nullable=True),
        sa.Column("expectancy", sa.Float(), nullable=True),
        sa.Column("profit_factor", sa.Float(), nullable=True),
        sa.Column("avg_holding_days", sa.Float(), nullable=True),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("activity_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fitness_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_scorecards_id", "strategy_scorecards", ["id"])
    op.create_index("ix_strategy_scorecards_strategy_id", "strategy_scorecards", ["strategy_id"])
    op.create_index("ix_strategy_scorecards_strategy_version_id", "strategy_scorecards", ["strategy_version_id"])

    op.create_table(
        "research_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_tasks_id", "research_tasks", ["id"])
    op.create_index("ix_research_tasks_strategy_id", "research_tasks", ["strategy_id"])

    op.add_column("positions", sa.Column("signal_id", sa.Integer(), nullable=True))
    op.add_column("positions", sa.Column("entry_context", sa.JSON(), nullable=True))
    op.add_column("positions", sa.Column("close_context", sa.JSON(), nullable=True))
    with op.batch_alter_table("positions") as batch_op:
        batch_op.create_foreign_key("fk_positions_signal_id", "signals", ["signal_id"], ["id"])

    op.add_column("trade_reviews", sa.Column("outcome", sa.String(length=20), nullable=True))
    op.add_column("trade_reviews", sa.Column("failure_mode", sa.String(length=50), nullable=True))
    op.add_column("trade_reviews", sa.Column("root_causes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("trade_reviews", sa.Column("recommended_changes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("trade_reviews", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("trade_reviews", sa.Column("review_priority", sa.String(length=20), nullable=False, server_default="normal"))
    op.add_column("trade_reviews", sa.Column("needs_strategy_update", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("trade_reviews", sa.Column("strategy_update_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trade_reviews", "strategy_update_reason")
    op.drop_column("trade_reviews", "needs_strategy_update")
    op.drop_column("trade_reviews", "review_priority")
    op.drop_column("trade_reviews", "confidence")
    op.drop_column("trade_reviews", "recommended_changes")
    op.drop_column("trade_reviews", "root_causes")
    op.drop_column("trade_reviews", "failure_mode")
    op.drop_column("trade_reviews", "outcome")

    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_constraint("fk_positions_signal_id", type_="foreignkey")
        batch_op.drop_column("close_context")
        batch_op.drop_column("entry_context")
        batch_op.drop_column("signal_id")

    op.drop_index("ix_research_tasks_strategy_id", table_name="research_tasks")
    op.drop_index("ix_research_tasks_id", table_name="research_tasks")
    op.drop_table("research_tasks")

    op.drop_index("ix_strategy_scorecards_strategy_version_id", table_name="strategy_scorecards")
    op.drop_index("ix_strategy_scorecards_strategy_id", table_name="strategy_scorecards")
    op.drop_index("ix_strategy_scorecards_id", table_name="strategy_scorecards")
    op.drop_table("strategy_scorecards")

    op.drop_index("ix_signals_ticker", table_name="signals")
    op.drop_index("ix_signals_watchlist_item_id", table_name="signals")
    op.drop_index("ix_signals_strategy_version_id", table_name="signals")
    op.drop_index("ix_signals_strategy_id", table_name="signals")
    op.drop_index("ix_signals_id", table_name="signals")
    op.drop_table("signals")
