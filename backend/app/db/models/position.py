from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    hypothesis_id: Mapped[int | None] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    setup_id: Mapped[int | None] = mapped_column(ForeignKey("setups.id"), nullable=True, index=True)
    signal_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_definitions.id"), nullable=True, index=True
    )
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    account_mode: Mapped[str] = mapped_column(String(20), default="paper")
    side: Mapped[str] = mapped_column(String(10), default="long")
    status: Mapped[str] = mapped_column(String(20), default="open")
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    entry_price: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    size: Mapped[float] = mapped_column(Float)
    thesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    exit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    close_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pnl_realized: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_runup_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_unrealized: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending")

    events: Mapped[list[PositionEvent]] = relationship(
        "PositionEvent",
        back_populates="position",
        cascade="all, delete-orphan",
    )

    @property
    def trade_signal_id(self) -> int | None:
        return self.signal_id


class PositionEvent(Base):
    __tablename__ = "position_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    position: Mapped[Position] = relationship("Position", back_populates="events")
