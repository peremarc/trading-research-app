from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TradeReview(Base):
    __tablename__ = "trade_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    outcome_label: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cause_category: Mapped[str] = mapped_column(String(40))
    failure_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observations: Mapped[dict] = mapped_column(JSON, default=dict)
    root_cause: Mapped[str] = mapped_column(Text)
    root_causes: Mapped[list] = mapped_column(JSON, default=list)
    lesson_learned: Mapped[str] = mapped_column(Text)
    proposed_strategy_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_changes: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_priority: Mapped[str] = mapped_column(String(20), default="normal")
    should_modify_strategy: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_strategy_update: Mapped[bool] = mapped_column(Boolean, default=False)
    strategy_update_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
