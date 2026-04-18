"""system event log

Revision ID: 20260418_0013
Revises: 20260418_0012
Create Date: 2026-04-18 08:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260418_0013"
down_revision: Union[str, None] = "20260418_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="system"),
        sa.Column("pdca_phase_hint", sa.String(length=20), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_system_events_id", "system_events", ["id"])
    op.create_index("ix_system_events_event_type", "system_events", ["event_type"])
    op.create_index("ix_system_events_entity_type", "system_events", ["entity_type"])
    op.create_index("ix_system_events_entity_id", "system_events", ["entity_id"])
    op.create_index("ix_system_events_pdca_phase_hint", "system_events", ["pdca_phase_hint"])


def downgrade() -> None:
    op.drop_index("ix_system_events_pdca_phase_hint", table_name="system_events")
    op.drop_index("ix_system_events_entity_id", table_name="system_events")
    op.drop_index("ix_system_events_entity_type", table_name="system_events")
    op.drop_index("ix_system_events_event_type", table_name="system_events")
    op.drop_index("ix_system_events_id", table_name="system_events")
    op.drop_table("system_events")
