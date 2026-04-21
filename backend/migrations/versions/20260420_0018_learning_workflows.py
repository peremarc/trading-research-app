"""learning workflows

Revision ID: 20260420_0018
Revises: 20260420_0017
Create Date: 2026-04-20 16:55:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260420_0018"
down_revision: Union[str, None] = "20260420_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "learning_workflows" not in existing_tables:
        op.create_table(
            "learning_workflows",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("workflow_type", sa.String(length=40), nullable=False),
            sa.Column("scope", sa.String(length=80), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
            sa.Column("priority", sa.String(length=16), nullable=False, server_default="normal"),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("context", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("items", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("open_item_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("workflow_type", "scope", name="uq_learning_workflow_type_scope"),
        )

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("learning_workflows")}
    for name, cols in [
        ("ix_learning_workflows_id", ["id"]),
        ("ix_learning_workflows_workflow_type", ["workflow_type"]),
        ("ix_learning_workflows_scope", ["scope"]),
        ("ix_learning_workflows_status", ["status"]),
        ("ix_learning_workflows_priority", ["priority"]),
    ]:
        if name not in indexes:
            op.create_index(name, "learning_workflows", cols)


def downgrade() -> None:
    op.drop_index("ix_learning_workflows_priority", table_name="learning_workflows")
    op.drop_index("ix_learning_workflows_status", table_name="learning_workflows")
    op.drop_index("ix_learning_workflows_scope", table_name="learning_workflows")
    op.drop_index("ix_learning_workflows_workflow_type", table_name="learning_workflows")
    op.drop_index("ix_learning_workflows_id", table_name="learning_workflows")
    op.drop_table("learning_workflows")
