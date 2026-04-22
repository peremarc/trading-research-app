"""external backtest run links

Revision ID: 20260422_0021
Revises: 20260422_0020
Create Date: 2026-04-22 21:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260422_0021"
down_revision: Union[str, None] = "20260422_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "external_backtest_runs" not in existing_tables:
        op.create_table(
            "external_backtest_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("remote_run_id", sa.String(length=80), nullable=False),
            sa.Column("provider", sa.String(length=40), nullable=False, server_default="backtesting"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("engine", sa.String(length=80), nullable=True),
            sa.Column("spec_version", sa.String(length=40), nullable=True),
            sa.Column("dataset_version", sa.String(length=160), nullable=True),
            sa.Column("strategy_id", sa.Integer(), nullable=True),
            sa.Column("strategy_version_id", sa.Integer(), nullable=True),
            sa.Column("research_task_id", sa.Integer(), nullable=True),
            sa.Column("linked_entity_type", sa.String(length=40), nullable=True),
            sa.Column("linked_entity_id", sa.String(length=80), nullable=True),
            sa.Column("target_type", sa.String(length=40), nullable=True),
            sa.Column("target_code", sa.String(length=120), nullable=True),
            sa.Column("target_version", sa.String(length=40), nullable=True),
            sa.Column("requested_by", sa.String(length=80), nullable=True),
            sa.Column("source_app", sa.String(length=80), nullable=True),
            sa.Column("latest_run_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("summary_metrics", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("artifact_refs", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("backtest_spec", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["research_task_id"], ["research_tasks.id"]),
            sa.ForeignKeyConstraint(["strategy_id"], ["strategies.id"]),
            sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("remote_run_id"),
        )

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("external_backtest_runs")}
    for name, cols in [
        ("ix_external_backtest_runs_id", ["id"]),
        ("ix_external_backtest_runs_remote_run_id", ["remote_run_id"]),
        ("ix_external_backtest_runs_provider", ["provider"]),
        ("ix_external_backtest_runs_status", ["status"]),
        ("ix_external_backtest_runs_strategy_id", ["strategy_id"]),
        ("ix_external_backtest_runs_strategy_version_id", ["strategy_version_id"]),
        ("ix_external_backtest_runs_research_task_id", ["research_task_id"]),
        ("ix_external_backtest_runs_linked_entity_type", ["linked_entity_type"]),
        ("ix_external_backtest_runs_linked_entity_id", ["linked_entity_id"]),
        ("ix_external_backtest_runs_target_type", ["target_type"]),
        ("ix_external_backtest_runs_target_code", ["target_code"]),
    ]:
        if name not in indexes:
            op.create_index(name, "external_backtest_runs", cols)


def downgrade() -> None:
    for name in [
        "ix_external_backtest_runs_target_code",
        "ix_external_backtest_runs_target_type",
        "ix_external_backtest_runs_linked_entity_id",
        "ix_external_backtest_runs_linked_entity_type",
        "ix_external_backtest_runs_research_task_id",
        "ix_external_backtest_runs_strategy_version_id",
        "ix_external_backtest_runs_strategy_id",
        "ix_external_backtest_runs_status",
        "ix_external_backtest_runs_provider",
        "ix_external_backtest_runs_remote_run_id",
        "ix_external_backtest_runs_id",
    ]:
        op.drop_index(name, table_name="external_backtest_runs")
    op.drop_table("external_backtest_runs")
