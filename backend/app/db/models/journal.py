from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entry_type: Mapped[str] = mapped_column(String(30))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ticker: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    pdca_cycle_id: Mapped[int | None] = mapped_column(ForeignKey("pdca_cycles.id"), nullable=True)
    market_context: Mapped[dict] = mapped_column(JSON, default=dict)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    observations: Mapped[dict] = mapped_column(JSON, default=dict)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    expectations: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons: Mapped[str | None] = mapped_column(Text, nullable=True)
