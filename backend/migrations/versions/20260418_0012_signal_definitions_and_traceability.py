"""signal definitions and downstream traceability

Revision ID: 20260418_0012
Revises: 20260418_0011
Create Date: 2026-04-18 08:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260418_0012"
down_revision: Union[str, None] = "20260418_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signal_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hypothesis_id", sa.Integer(), sa.ForeignKey("hypotheses.id"), nullable=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("setup_id", sa.Integer(), sa.ForeignKey("setups.id"), nullable=True),
        sa.Column("signal_kind", sa.String(length=20), nullable=False, server_default="trigger"),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("activation_conditions", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("intended_usage", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_signal_definitions_id", "signal_definitions", ["id"])
    op.create_index("ix_signal_definitions_code", "signal_definitions", ["code"], unique=True)
    op.create_index("ix_signal_definitions_hypothesis_id", "signal_definitions", ["hypothesis_id"])
    op.create_index("ix_signal_definitions_strategy_id", "signal_definitions", ["strategy_id"])
    op.create_index("ix_signal_definitions_setup_id", "signal_definitions", ["setup_id"])

    op.add_column("signals", sa.Column("hypothesis_id", sa.Integer(), nullable=True))
    op.add_column("signals", sa.Column("setup_id", sa.Integer(), nullable=True))
    op.add_column("signals", sa.Column("signal_definition_id", sa.Integer(), nullable=True))
    op.create_index("ix_signals_hypothesis_id", "signals", ["hypothesis_id"])
    op.create_index("ix_signals_setup_id", "signals", ["setup_id"])
    op.create_index("ix_signals_signal_definition_id", "signals", ["signal_definition_id"])
    with op.batch_alter_table("signals") as batch_op:
        batch_op.create_foreign_key("fk_signals_hypothesis_id", "hypotheses", ["hypothesis_id"], ["id"])
        batch_op.create_foreign_key("fk_signals_setup_id", "setups", ["setup_id"], ["id"])
        batch_op.create_foreign_key(
            "fk_signals_signal_definition_id",
            "signal_definitions",
            ["signal_definition_id"],
            ["id"],
        )

    op.add_column("positions", sa.Column("hypothesis_id", sa.Integer(), nullable=True))
    op.add_column("positions", sa.Column("setup_id", sa.Integer(), nullable=True))
    op.add_column("positions", sa.Column("signal_definition_id", sa.Integer(), nullable=True))
    op.create_index("ix_positions_hypothesis_id", "positions", ["hypothesis_id"])
    op.create_index("ix_positions_setup_id", "positions", ["setup_id"])
    op.create_index("ix_positions_signal_definition_id", "positions", ["signal_definition_id"])
    with op.batch_alter_table("positions") as batch_op:
        batch_op.create_foreign_key("fk_positions_hypothesis_id", "hypotheses", ["hypothesis_id"], ["id"])
        batch_op.create_foreign_key("fk_positions_setup_id", "setups", ["setup_id"], ["id"])
        batch_op.create_foreign_key(
            "fk_positions_signal_definition_id",
            "signal_definitions",
            ["signal_definition_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_constraint("fk_positions_signal_definition_id", type_="foreignkey")
        batch_op.drop_constraint("fk_positions_setup_id", type_="foreignkey")
        batch_op.drop_constraint("fk_positions_hypothesis_id", type_="foreignkey")
    op.drop_index("ix_positions_signal_definition_id", table_name="positions")
    op.drop_index("ix_positions_setup_id", table_name="positions")
    op.drop_index("ix_positions_hypothesis_id", table_name="positions")
    op.drop_column("positions", "signal_definition_id")
    op.drop_column("positions", "setup_id")
    op.drop_column("positions", "hypothesis_id")

    with op.batch_alter_table("signals") as batch_op:
        batch_op.drop_constraint("fk_signals_signal_definition_id", type_="foreignkey")
        batch_op.drop_constraint("fk_signals_setup_id", type_="foreignkey")
        batch_op.drop_constraint("fk_signals_hypothesis_id", type_="foreignkey")
    op.drop_index("ix_signals_signal_definition_id", table_name="signals")
    op.drop_index("ix_signals_setup_id", table_name="signals")
    op.drop_index("ix_signals_hypothesis_id", table_name="signals")
    op.drop_column("signals", "signal_definition_id")
    op.drop_column("signals", "setup_id")
    op.drop_column("signals", "hypothesis_id")

    op.drop_index("ix_signal_definitions_setup_id", table_name="signal_definitions")
    op.drop_index("ix_signal_definitions_strategy_id", table_name="signal_definitions")
    op.drop_index("ix_signal_definitions_hypothesis_id", table_name="signal_definitions")
    op.drop_index("ix_signal_definitions_code", table_name="signal_definitions")
    op.drop_index("ix_signal_definitions_id", table_name="signal_definitions")
    op.drop_table("signal_definitions")
