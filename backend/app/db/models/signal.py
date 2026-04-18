from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TradeSignal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    hypothesis_id: Mapped[int | None] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True, index=True)
    setup_id: Mapped[int | None] = mapped_column(ForeignKey("setups.id"), nullable=True, index=True)
    signal_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_definitions.id"), nullable=True, index=True
    )
    watchlist_item_id: Mapped[int | None] = mapped_column(ForeignKey("watchlist_items.id"), nullable=True, index=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    timeframe: Mapped[str] = mapped_column(String(20), default="1D")
    signal_type: Mapped[str] = mapped_column(String(30), default="trend_following")
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_zone: Mapped[dict] = mapped_column(JSON, default=dict)
    stop_zone: Mapped[dict] = mapped_column(JSON, default=dict)
    target_zone: Mapped[dict] = mapped_column(JSON, default=dict)
    signal_context: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Backward-compatible alias while the codebase migrates to the clearer name.
Signal = TradeSignal
