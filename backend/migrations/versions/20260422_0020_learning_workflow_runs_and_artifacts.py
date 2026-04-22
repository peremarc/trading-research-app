"""learning workflow runs and artifacts

Revision ID: 20260422_0020
Revises: 20260420_0019
Create Date: 2026-04-22 10:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260422_0020"
down_revision: Union[str, None] = "20260420_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "learning_workflow_runs" not in existing_tables:
        op.create_table(
            "learning_workflow_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("run_kind", sa.String(length=24), nullable=False),
            sa.Column("trigger_source", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="completed"),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("input_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("context_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("output_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("artifact_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["workflow_id"], ["learning_workflows.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "learning_workflow_artifacts" not in existing_tables:
        op.create_table(
            "learning_workflow_artifacts",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("workflow_run_id", sa.Integer(), nullable=False),
            sa.Column("artifact_type", sa.String(length=40), nullable=False),
            sa.Column("entity_type", sa.String(length=40), nullable=True),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("ticker", sa.String(length=16), nullable=True),
            sa.Column("strategy_version_id", sa.Integer(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["workflow_id"], ["learning_workflows.id"]),
            sa.ForeignKeyConstraint(["workflow_run_id"], ["learning_workflow_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    run_indexes = {item["name"] for item in inspector.get_indexes("learning_workflow_runs")}
    for name, cols in [
        ("ix_learning_workflow_runs_id", ["id"]),
        ("ix_learning_workflow_runs_workflow_id", ["workflow_id"]),
        ("ix_learning_workflow_runs_run_kind", ["run_kind"]),
        ("ix_learning_workflow_runs_trigger_source", ["trigger_source"]),
        ("ix_learning_workflow_runs_status", ["status"]),
    ]:
        if name not in run_indexes:
            op.create_index(name, "learning_workflow_runs", cols)

    artifact_indexes = {item["name"] for item in inspector.get_indexes("learning_workflow_artifacts")}
    for name, cols in [
        ("ix_learning_workflow_artifacts_id", ["id"]),
        ("ix_learning_workflow_artifacts_workflow_id", ["workflow_id"]),
        ("ix_learning_workflow_artifacts_workflow_run_id", ["workflow_run_id"]),
        ("ix_learning_workflow_artifacts_artifact_type", ["artifact_type"]),
        ("ix_learning_workflow_artifacts_entity_type", ["entity_type"]),
        ("ix_learning_workflow_artifacts_entity_id", ["entity_id"]),
        ("ix_learning_workflow_artifacts_ticker", ["ticker"]),
        ("ix_learning_workflow_artifacts_strategy_version_id", ["strategy_version_id"]),
    ]:
        if name not in artifact_indexes:
            op.create_index(name, "learning_workflow_artifacts", cols)


def downgrade() -> None:
    for name in [
        "ix_learning_workflow_artifacts_strategy_version_id",
        "ix_learning_workflow_artifacts_ticker",
        "ix_learning_workflow_artifacts_entity_id",
        "ix_learning_workflow_artifacts_entity_type",
        "ix_learning_workflow_artifacts_artifact_type",
        "ix_learning_workflow_artifacts_workflow_run_id",
        "ix_learning_workflow_artifacts_workflow_id",
        "ix_learning_workflow_artifacts_id",
    ]:
        op.drop_index(name, table_name="learning_workflow_artifacts")
    for name in [
        "ix_learning_workflow_runs_status",
        "ix_learning_workflow_runs_trigger_source",
        "ix_learning_workflow_runs_run_kind",
        "ix_learning_workflow_runs_workflow_id",
        "ix_learning_workflow_runs_id",
    ]:
        op.drop_index(name, table_name="learning_workflow_runs")
    op.drop_table("learning_workflow_artifacts")
    op.drop_table("learning_workflow_runs")
