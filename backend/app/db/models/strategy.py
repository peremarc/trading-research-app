from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypothesis_id: Mapped[int | None] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(50), default="US_EQUITIES")
    horizon: Mapped[str] = mapped_column(String(50))
    bias: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="research")
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list[StrategyVersion]] = relationship(
        "StrategyVersion",
        foreign_keys="StrategyVersion.strategy_id",
        back_populates="strategy",
        cascade="all, delete-orphan",
    )
    current_version: Mapped[StrategyVersion | None] = relationship(
        "StrategyVersion",
        foreign_keys=[current_version_id],
        post_update=True,
    )


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    hypothesis: Mapped[str] = mapped_column(Text)
    general_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(20), default="draft")
    lifecycle_stage: Mapped[str] = mapped_column(String(20), default="candidate")
    is_baseline: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    strategy: Mapped[Strategy] = relationship(
        "Strategy",
        foreign_keys=[strategy_id],
        back_populates="versions",
    )
