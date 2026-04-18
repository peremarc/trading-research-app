"""event dispatch status

Revision ID: 20260418_0014
Revises: 20260418_0013
Create Date: 2026-04-18 08:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260418_0014"
down_revision: Union[str, None] = "20260418_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_events",
        sa.Column("dispatch_status", sa.String(length=20), nullable=False, server_default="pending"),
    )
    op.add_column("system_events", sa.Column("dispatched_phase", sa.String(length=20), nullable=True))
    op.add_column("system_events", sa.Column("dispatch_note", sa.Text(), nullable=True))
    op.add_column("system_events", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("system_events", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_system_events_dispatch_status", "system_events", ["dispatch_status"])
    op.create_index("ix_system_events_dispatched_phase", "system_events", ["dispatched_phase"])


def downgrade() -> None:
    op.drop_index("ix_system_events_dispatched_phase", table_name="system_events")
    op.drop_index("ix_system_events_dispatch_status", table_name="system_events")
    op.drop_column("system_events", "processed_at")
    op.drop_column("system_events", "dispatched_at")
    op.drop_column("system_events", "dispatch_note")
    op.drop_column("system_events", "dispatched_phase")
    op.drop_column("system_events", "dispatch_status")
