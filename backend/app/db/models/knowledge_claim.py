from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeClaim(Base):
    __tablename__ = "knowledge_claims"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_knowledge_claim_scope_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    claim_type: Mapped[str] = mapped_column(String(40), index=True)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    key: Mapped[str] = mapped_column(String(160), index=True)
    claim_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="provisional", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    freshness_state: Mapped[str] = mapped_column(String(20), default="current", index=True)
    linked_ticker: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True, index=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    support_count: Mapped[int] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeClaimEvidence(Base):
    __tablename__ = "knowledge_claim_evidence"
    __table_args__ = (UniqueConstraint("claim_id", "source_key", name="uq_knowledge_claim_evidence_claim_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("knowledge_claims.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_key: Mapped[str] = mapped_column(String(160), index=True)
    stance: Mapped[str] = mapped_column(String(20), default="support", index=True)
    summary: Mapped[str] = mapped_column(Text)
    evidence_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    strength: Mapped[float] = mapped_column(Float, default=0.6)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
