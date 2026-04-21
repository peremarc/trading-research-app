"""knowledge claims

Revision ID: 20260420_0017
Revises: 20260419_0016
Create Date: 2026-04-20 16:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260420_0017"
down_revision: Union[str, None] = "20260419_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "knowledge_claims" not in existing_tables:
        op.create_table(
            "knowledge_claims",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("claim_type", sa.String(length=40), nullable=False),
            sa.Column("scope", sa.String(length=80), nullable=False),
            sa.Column("key", sa.String(length=160), nullable=False),
            sa.Column("claim_text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="provisional"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("freshness_state", sa.String(length=20), nullable=False, server_default="current"),
            sa.Column("linked_ticker", sa.String(length=12), nullable=True),
            sa.Column("strategy_version_id", sa.Integer(), nullable=True),
            sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("support_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("contradiction_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("meta", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["strategy_version_id"], ["strategy_versions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("scope", "key", name="uq_knowledge_claim_scope_key"),
        )

    if "knowledge_claim_evidence" not in existing_tables:
        op.create_table(
            "knowledge_claim_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("claim_id", sa.Integer(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("source_key", sa.String(length=160), nullable=False),
            sa.Column("stance", sa.String(length=20), nullable=False, server_default="support"),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("evidence_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("strength", sa.Float(), nullable=False, server_default="0.6"),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["claim_id"], ["knowledge_claims.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("claim_id", "source_key", name="uq_knowledge_claim_evidence_claim_source_key"),
        )

    inspector = sa.inspect(bind)
    claim_indexes = {item["name"] for item in inspector.get_indexes("knowledge_claims")}
    for name, cols in [
        ("ix_knowledge_claims_id", ["id"]),
        ("ix_knowledge_claims_claim_type", ["claim_type"]),
        ("ix_knowledge_claims_scope", ["scope"]),
        ("ix_knowledge_claims_key", ["key"]),
        ("ix_knowledge_claims_status", ["status"]),
        ("ix_knowledge_claims_freshness_state", ["freshness_state"]),
        ("ix_knowledge_claims_linked_ticker", ["linked_ticker"]),
        ("ix_knowledge_claims_strategy_version_id", ["strategy_version_id"]),
    ]:
        if name not in claim_indexes:
            op.create_index(name, "knowledge_claims", cols)

    evidence_indexes = {item["name"] for item in inspector.get_indexes("knowledge_claim_evidence")}
    for name, cols in [
        ("ix_knowledge_claim_evidence_id", ["id"]),
        ("ix_knowledge_claim_evidence_claim_id", ["claim_id"]),
        ("ix_knowledge_claim_evidence_source_type", ["source_type"]),
        ("ix_knowledge_claim_evidence_source_key", ["source_key"]),
        ("ix_knowledge_claim_evidence_stance", ["stance"]),
    ]:
        if name not in evidence_indexes:
            op.create_index(name, "knowledge_claim_evidence", cols)


def downgrade() -> None:
    op.drop_index("ix_knowledge_claim_evidence_stance", table_name="knowledge_claim_evidence")
    op.drop_index("ix_knowledge_claim_evidence_source_key", table_name="knowledge_claim_evidence")
    op.drop_index("ix_knowledge_claim_evidence_source_type", table_name="knowledge_claim_evidence")
    op.drop_index("ix_knowledge_claim_evidence_claim_id", table_name="knowledge_claim_evidence")
    op.drop_index("ix_knowledge_claim_evidence_id", table_name="knowledge_claim_evidence")
    op.drop_table("knowledge_claim_evidence")

    op.drop_index("ix_knowledge_claims_strategy_version_id", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_linked_ticker", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_freshness_state", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_status", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_key", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_scope", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_claim_type", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_id", table_name="knowledge_claims")
    op.drop_table("knowledge_claims")
