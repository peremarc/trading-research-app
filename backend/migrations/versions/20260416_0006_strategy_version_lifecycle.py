"""strategy version lifecycle

Revision ID: 20260416_0006
Revises: 20260416_0005
Create Date: 2026-04-16 22:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260416_0006"
down_revision: Union[str, None] = "20260416_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "strategy_versions",
        sa.Column("lifecycle_stage", sa.String(length=20), nullable=False, server_default="candidate"),
    )


def downgrade() -> None:
    op.drop_column("strategy_versions", "lifecycle_stage")
