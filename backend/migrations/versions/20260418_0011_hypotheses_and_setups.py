"""hypotheses and setups catalog

Revision ID: 20260418_0011
Revises: 20260417_0010
Create Date: 2026-04-18 00:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260418_0011"
down_revision: Union[str, None] = "20260417_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("proposition", sa.Text(), nullable=False),
        sa.Column("market", sa.String(length=50), nullable=False),
        sa.Column("horizon", sa.String(length=50), nullable=False),
        sa.Column("bias", sa.String(length=20), nullable=False),
        sa.Column("success_criteria", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_hypotheses_id", "hypotheses", ["id"])
    op.create_index("ix_hypotheses_code", "hypotheses", ["code"], unique=True)

    op.create_table(
        "setups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hypothesis_id", sa.Integer(), sa.ForeignKey("hypotheses.id"), nullable=True),
        sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=True),
        sa.Column("timeframe", sa.String(length=20), nullable=False, server_default="1D"),
        sa.Column("ideal_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("conditions", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_setups_id", "setups", ["id"])
    op.create_index("ix_setups_code", "setups", ["code"], unique=True)
    op.create_index("ix_setups_hypothesis_id", "setups", ["hypothesis_id"])
    op.create_index("ix_setups_strategy_id", "setups", ["strategy_id"])

    op.add_column("strategies", sa.Column("hypothesis_id", sa.Integer(), nullable=True))
    op.create_index("ix_strategies_hypothesis_id", "strategies", ["hypothesis_id"])
    with op.batch_alter_table("strategies") as batch_op:
        batch_op.create_foreign_key("fk_strategies_hypothesis_id", "hypotheses", ["hypothesis_id"], ["id"])

    op.add_column("watchlists", sa.Column("hypothesis_id", sa.Integer(), nullable=True))
    op.add_column("watchlists", sa.Column("setup_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("watchlists") as batch_op:
        batch_op.create_foreign_key("fk_watchlists_hypothesis_id", "hypotheses", ["hypothesis_id"], ["id"])
        batch_op.create_foreign_key("fk_watchlists_setup_id", "setups", ["setup_id"], ["id"])

    op.add_column("watchlist_items", sa.Column("setup_id", sa.Integer(), nullable=True))
    op.create_index("ix_watchlist_items_setup_id", "watchlist_items", ["setup_id"])
    with op.batch_alter_table("watchlist_items") as batch_op:
        batch_op.create_foreign_key("fk_watchlist_items_setup_id", "setups", ["setup_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("watchlist_items") as batch_op:
        batch_op.drop_constraint("fk_watchlist_items_setup_id", type_="foreignkey")
    op.drop_index("ix_watchlist_items_setup_id", table_name="watchlist_items")
    op.drop_column("watchlist_items", "setup_id")

    with op.batch_alter_table("watchlists") as batch_op:
        batch_op.drop_constraint("fk_watchlists_setup_id", type_="foreignkey")
        batch_op.drop_constraint("fk_watchlists_hypothesis_id", type_="foreignkey")
    op.drop_column("watchlists", "setup_id")
    op.drop_column("watchlists", "hypothesis_id")

    with op.batch_alter_table("strategies") as batch_op:
        batch_op.drop_constraint("fk_strategies_hypothesis_id", type_="foreignkey")
    op.drop_index("ix_strategies_hypothesis_id", table_name="strategies")
    op.drop_column("strategies", "hypothesis_id")

    op.drop_index("ix_setups_strategy_id", table_name="setups")
    op.drop_index("ix_setups_hypothesis_id", table_name="setups")
    op.drop_index("ix_setups_code", table_name="setups")
    op.drop_index("ix_setups_id", table_name="setups")
    op.drop_table("setups")

    op.drop_index("ix_hypotheses_code", table_name="hypotheses")
    op.drop_index("ix_hypotheses_id", table_name="hypotheses")
    op.drop_table("hypotheses")
