"""external backtest skill candidate link

Revision ID: 20260422_0022
Revises: 20260422_0021
Create Date: 2026-04-22 21:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260422_0022"
down_revision: Union[str, None] = "20260422_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("external_backtest_runs")}
    if "skill_candidate_id" not in columns:
        with op.batch_alter_table("external_backtest_runs") as batch_op:
            batch_op.add_column(sa.Column("skill_candidate_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_external_backtest_runs_skill_candidate_id_memory_items",
                "memory_items",
                ["skill_candidate_id"],
                ["id"],
            )

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("external_backtest_runs")}
    if "ix_external_backtest_runs_skill_candidate_id" not in indexes:
        op.create_index(
            "ix_external_backtest_runs_skill_candidate_id",
            "external_backtest_runs",
            ["skill_candidate_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("external_backtest_runs")}
    if "ix_external_backtest_runs_skill_candidate_id" in indexes:
        op.drop_index("ix_external_backtest_runs_skill_candidate_id", table_name="external_backtest_runs")

    columns = {column["name"] for column in inspector.get_columns("external_backtest_runs")}
    if "skill_candidate_id" in columns:
        with op.batch_alter_table("external_backtest_runs") as batch_op:
            batch_op.drop_constraint(
                "fk_external_backtest_runs_skill_candidate_id_memory_items",
                type_="foreignkey",
            )
            batch_op.drop_column("skill_candidate_id")
