"""skill validation records

Revision ID: 20260420_0019
Revises: 20260420_0018
Create Date: 2026-04-20 17:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260420_0019"
down_revision: Union[str, None] = "20260420_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "skill_validation_records" not in existing_tables:
        op.create_table(
            "skill_validation_records",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=False),
            sa.Column("revision_id", sa.Integer(), nullable=True),
            sa.Column("validation_mode", sa.String(length=40), nullable=False),
            sa.Column("validation_outcome", sa.String(length=24), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("run_id", sa.String(length=120), nullable=True),
            sa.Column("artifact_url", sa.Text(), nullable=True),
            sa.Column("evidence_note", sa.Text(), nullable=True),
            sa.Column("sample_size", sa.Integer(), nullable=True),
            sa.Column("win_rate", sa.Float(), nullable=True),
            sa.Column("avg_pnl_pct", sa.Float(), nullable=True),
            sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
            sa.Column("evidence_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["candidate_id"], ["memory_items.id"]),
            sa.ForeignKeyConstraint(["revision_id"], ["memory_items.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("skill_validation_records")}
    for name, cols in [
        ("ix_skill_validation_records_id", ["id"]),
        ("ix_skill_validation_records_candidate_id", ["candidate_id"]),
        ("ix_skill_validation_records_revision_id", ["revision_id"]),
        ("ix_skill_validation_records_validation_mode", ["validation_mode"]),
        ("ix_skill_validation_records_validation_outcome", ["validation_outcome"]),
        ("ix_skill_validation_records_run_id", ["run_id"]),
    ]:
        if name not in indexes:
            op.create_index(name, "skill_validation_records", cols)


def downgrade() -> None:
    op.drop_index("ix_skill_validation_records_run_id", table_name="skill_validation_records")
    op.drop_index("ix_skill_validation_records_validation_outcome", table_name="skill_validation_records")
    op.drop_index("ix_skill_validation_records_validation_mode", table_name="skill_validation_records")
    op.drop_index("ix_skill_validation_records_revision_id", table_name="skill_validation_records")
    op.drop_index("ix_skill_validation_records_candidate_id", table_name="skill_validation_records")
    op.drop_index("ix_skill_validation_records_id", table_name="skill_validation_records")
    op.drop_table("skill_validation_records")

