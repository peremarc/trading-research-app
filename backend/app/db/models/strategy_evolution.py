from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StrategyChangeEvent(Base):
    __tablename__ = "strategy_change_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    source_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    new_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    trade_review_id: Mapped[int | None] = mapped_column(ForeignKey("trade_reviews.id"), nullable=True)
    change_reason: Mapped[str] = mapped_column(Text)
    proposed_change: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    applied_automatically: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StrategyActivationEvent(Base):
    __tablename__ = "strategy_activation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    activated_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    previous_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    activation_reason: Mapped[str] = mapped_column(Text)
    activated_automatically: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
