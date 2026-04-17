from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Screener(Base):
    __tablename__ = "screeners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("screener_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list[ScreenerVersion]] = relationship(
        "ScreenerVersion",
        foreign_keys="ScreenerVersion.screener_id",
        back_populates="screener",
        cascade="all, delete-orphan",
    )
    current_version: Mapped[ScreenerVersion | None] = relationship(
        "ScreenerVersion",
        foreign_keys=[current_version_id],
        post_update=True,
    )


class ScreenerVersion(Base):
    __tablename__ = "screener_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    screener_id: Mapped[int] = mapped_column(ForeignKey("screeners.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    universe: Mapped[str] = mapped_column(String(50), default="US_EQUITIES")
    timeframe: Mapped[str] = mapped_column(String(20), default="1D")
    sorting: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    screener: Mapped[Screener] = relationship(
        "Screener",
        foreign_keys=[screener_id],
        back_populates="versions",
    )
