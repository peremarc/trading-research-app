from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketStateSnapshotRecord(Base):
    __tablename__ = "market_state_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trigger: Mapped[str] = mapped_column(String(40), index=True)
    pdca_phase: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    execution_mode: Mapped[str] = mapped_column(String(30), default="global")
    benchmark_ticker: Mapped[str] = mapped_column(String(12), default="SPY")
    regime_label: Mapped[str] = mapped_column(String(40), index=True)
    regime_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    snapshot_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    source_context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
