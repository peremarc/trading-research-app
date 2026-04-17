from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CandidateValidationSnapshot(Base):
    __tablename__ = "candidate_validation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    strategy_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id"), index=True)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    avg_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation_status: Mapped[str] = mapped_column(String(20), default="insufficient_data")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
