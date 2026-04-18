"""candidate validation replay fields

Revision ID: 20260417_0010
Revises: 20260417_0009
Create Date: 2026-04-17 19:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260417_0010"
down_revision: Union[str, None] = "20260417_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("candidate_validation_snapshots", sa.Column("profit_factor", sa.Float(), nullable=True))
    op.add_column(
        "candidate_validation_snapshots",
        sa.Column("distinct_tickers", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "candidate_validation_snapshots",
        sa.Column("window_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("candidate_validation_snapshots", sa.Column("rolling_pass_rate", sa.Float(), nullable=True))
    op.add_column("candidate_validation_snapshots", sa.Column("replay_score", sa.Float(), nullable=True))
    op.add_column(
        "candidate_validation_snapshots",
        sa.Column("validation_mode", sa.String(length=40), nullable=False, server_default="candidate_validation"),
    )
    op.add_column("candidate_validation_snapshots", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column(
        "candidate_validation_snapshots",
        sa.Column("validation_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("candidate_validation_snapshots", "validation_payload")
    op.drop_column("candidate_validation_snapshots", "decision_reason")
    op.drop_column("candidate_validation_snapshots", "validation_mode")
    op.drop_column("candidate_validation_snapshots", "replay_score")
    op.drop_column("candidate_validation_snapshots", "rolling_pass_rate")
    op.drop_column("candidate_validation_snapshots", "window_count")
    op.drop_column("candidate_validation_snapshots", "distinct_tickers")
    op.drop_column("candidate_validation_snapshots", "profit_factor")
