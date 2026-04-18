"""market state snapshots

Revision ID: 20260418_0015
Revises: 20260418_0014
Create Date: 2026-04-18 17:40:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260418_0015"
down_revision: Union[str, None] = "20260418_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_state_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("pdca_phase", sa.String(length=20), nullable=True),
        sa.Column("execution_mode", sa.String(length=30), nullable=False, server_default="global"),
        sa.Column("benchmark_ticker", sa.String(length=12), nullable=False, server_default="SPY"),
        sa.Column("regime_label", sa.String(length=40), nullable=False),
        sa.Column("regime_confidence", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("source_context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_state_snapshots_id", "market_state_snapshots", ["id"])
    op.create_index("ix_market_state_snapshots_trigger", "market_state_snapshots", ["trigger"])
    op.create_index("ix_market_state_snapshots_pdca_phase", "market_state_snapshots", ["pdca_phase"])
    op.create_index("ix_market_state_snapshots_regime_label", "market_state_snapshots", ["regime_label"])


def downgrade() -> None:
    op.drop_index("ix_market_state_snapshots_regime_label", table_name="market_state_snapshots")
    op.drop_index("ix_market_state_snapshots_pdca_phase", table_name="market_state_snapshots")
    op.drop_index("ix_market_state_snapshots_trigger", table_name="market_state_snapshots")
    op.drop_index("ix_market_state_snapshots_id", table_name="market_state_snapshots")
    op.drop_table("market_state_snapshots")
