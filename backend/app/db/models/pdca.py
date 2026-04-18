from datetime import datetime

from sqlalchemy import Date, DateTime, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PDCACycle(Base):
    __tablename__ = "pdca_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cycle_date: Mapped[datetime.date] = mapped_column(Date, index=True)
    phase: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
