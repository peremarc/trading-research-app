"""initial schema

Revision ID: 20260416_0001
Revises:
Create Date: 2026-04-16 18:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pdca_cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cycle_date", sa.Date(), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_pdca_cycles_id", "pdca_cycles", ["id"])
    op.create_index("ix_pdca_cycles_cycle_date", "pdca_cycles", ["cycle_date"])

    op.create_table(
        "strategies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("horizon", sa.String(length=50), nullable=False),
        sa.Column("bias", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategies_id", "strategies", ["id"])
    op.create_index("ix_strategies_code", "strategies", ["code"], unique=True)

    op.create_table(
        "strategy_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("general_rules", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("is_baseline", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_strategy_versions_id", "strategy_versions", ["id"])
    op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"])
    with op.batch_alter_table("strategies") as batch_op:
        batch_op.create_foreign_key(
            "fk_strategies_current_version_id",
            "strategy_versions",
            ["current_version_id"],
            ["id"],
        )

    op.create_table(
        "screeners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("current_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_screeners_id", "screeners", ["id"])
    op.create_index("ix_screeners_code", "screeners", ["code"], unique=True)

    op.create_table(
        "screener_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("screener_id", sa.Integer(), sa.ForeignKey("screeners.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("universe", sa.String(length=50), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column("sorting", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_screener_versions_id", "screener_versions", ["id"])
    op.create_index("ix_screener_versions_screener_id", "screener_versions", ["screener_id"])
    with op.batch_alter_table("screeners") as batch_op:
        batch_op.create_foreign_key(
            "fk_screeners_current_version_id",
            "screener_versions",
            ["current_version_id"],
            ["id"],
        )

    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_watchlists_id", "watchlists", ["id"])
    op.create_index("ix_watchlists_code", "watchlists", ["code"], unique=True)

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("watchlist_id", sa.Integer(), sa.ForeignKey("watchlists.id"), nullable=False),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("strategy_hypothesis", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("key_metrics", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
    )
    op.create_index("ix_watchlist_items_id", "watchlist_items", ["id"])
    op.create_index("ix_watchlist_items_watchlist_id", "watchlist_items", ["watchlist_id"])
    op.create_index("ix_watchlist_items_ticker", "watchlist_items", ["ticker"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("watchlist_item_id", sa.Integer(), sa.ForeignKey("watchlist_items.id"), nullable=True),
        sa.Column("quant_summary", sa.JSON(), nullable=False),
        sa.Column("visual_summary", sa.JSON(), nullable=False),
        sa.Column("combined_score", sa.Float(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("risk_reward", sa.Float(), nullable=True),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("decision_confidence", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_analysis_runs_id", "analysis_runs", ["id"])
    op.create_index("ix_analysis_runs_ticker", "analysis_runs", ["ticker"])

    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id"), nullable=True),
        sa.Column("account_mode", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("entry_date", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("target_price", sa.Float(), nullable=True),
        sa.Column("size", sa.Float(), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=True),
        sa.Column("exit_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.Column("pnl_realized", sa.Float(), nullable=True),
        sa.Column("pnl_unrealized", sa.Float(), nullable=True),
    )
    op.create_index("ix_positions_id", "positions", ["id"])
    op.create_index("ix_positions_ticker", "positions", ["ticker"])

    op.create_table(
        "position_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id"), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index("ix_position_events_id", "position_events", ["id"])
    op.create_index("ix_position_events_position_id", "position_events", ["position_id"])

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_type", sa.String(length=30), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ticker", sa.String(length=12), nullable=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("strategy_version_id", sa.Integer(), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id"), nullable=True),
        sa.Column("pdca_cycle_id", sa.Integer(), sa.ForeignKey("pdca_cycles.id"), nullable=True),
        sa.Column("market_context", sa.JSON(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(length=30), nullable=True),
        sa.Column("expectations", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("lessons", sa.Text(), nullable=True),
    )
    op.create_index("ix_journal_entries_id", "journal_entries", ["id"])
    op.create_index("ix_journal_entries_ticker", "journal_entries", ["ticker"])

    op.create_table(
        "memory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("memory_type", sa.String(length=30), nullable=False),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memory_items_id", "memory_items", ["id"])
    op.create_index("ix_memory_items_memory_type", "memory_items", ["memory_type"])
    op.create_index("ix_memory_items_scope", "memory_items", ["scope"])
    op.create_index("ix_memory_items_key", "memory_items", ["key"])


def downgrade() -> None:
    op.drop_index("ix_memory_items_key", table_name="memory_items")
    op.drop_index("ix_memory_items_scope", table_name="memory_items")
    op.drop_index("ix_memory_items_memory_type", table_name="memory_items")
    op.drop_index("ix_memory_items_id", table_name="memory_items")
    op.drop_table("memory_items")

    op.drop_index("ix_journal_entries_ticker", table_name="journal_entries")
    op.drop_index("ix_journal_entries_id", table_name="journal_entries")
    op.drop_table("journal_entries")

    op.drop_index("ix_position_events_position_id", table_name="position_events")
    op.drop_index("ix_position_events_id", table_name="position_events")
    op.drop_table("position_events")

    op.drop_index("ix_positions_ticker", table_name="positions")
    op.drop_index("ix_positions_id", table_name="positions")
    op.drop_table("positions")

    op.drop_index("ix_analysis_runs_ticker", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")

    op.drop_index("ix_watchlist_items_ticker", table_name="watchlist_items")
    op.drop_index("ix_watchlist_items_watchlist_id", table_name="watchlist_items")
    op.drop_index("ix_watchlist_items_id", table_name="watchlist_items")
    op.drop_table("watchlist_items")

    op.drop_index("ix_watchlists_code", table_name="watchlists")
    op.drop_index("ix_watchlists_id", table_name="watchlists")
    op.drop_table("watchlists")

    with op.batch_alter_table("screeners") as batch_op:
        batch_op.drop_constraint("fk_screeners_current_version_id", type_="foreignkey")
    op.drop_index("ix_screener_versions_screener_id", table_name="screener_versions")
    op.drop_index("ix_screener_versions_id", table_name="screener_versions")
    op.drop_table("screener_versions")

    op.drop_index("ix_screeners_code", table_name="screeners")
    op.drop_index("ix_screeners_id", table_name="screeners")
    op.drop_table("screeners")

    with op.batch_alter_table("strategies") as batch_op:
        batch_op.drop_constraint("fk_strategies_current_version_id", type_="foreignkey")
    op.drop_index("ix_strategy_versions_strategy_id", table_name="strategy_versions")
    op.drop_index("ix_strategy_versions_id", table_name="strategy_versions")
    op.drop_table("strategy_versions")

    op.drop_index("ix_strategies_code", table_name="strategies")
    op.drop_index("ix_strategies_id", table_name="strategies")
    op.drop_table("strategies")

    op.drop_index("ix_pdca_cycles_cycle_date", table_name="pdca_cycles")
    op.drop_index("ix_pdca_cycles_id", table_name="pdca_cycles")
    op.drop_table("pdca_cycles")
