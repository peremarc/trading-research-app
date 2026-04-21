from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SkillValidationRecord(Base):
    __tablename__ = "skill_validation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("memory_items.id"), index=True)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("memory_items.id"), nullable=True, index=True)
    validation_mode: Mapped[str] = mapped_column(String(40), index=True)
    validation_outcome: Mapped[str] = mapped_column(String(24), index=True)
    summary: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    artifact_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

