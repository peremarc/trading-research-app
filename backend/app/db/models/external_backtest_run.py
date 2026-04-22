from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExternalBacktestRun(Base):
    __tablename__ = "external_backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    remote_run_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), default="backtesting", index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    engine: Mapped[str | None] = mapped_column(String(80), nullable=True)
    spec_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True, index=True)
    research_task_id: Mapped[int | None] = mapped_column(ForeignKey("research_tasks.id"), nullable=True, index=True)
    skill_candidate_id: Mapped[int | None] = mapped_column(ForeignKey("memory_items.id"), nullable=True, index=True)
    linked_entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    linked_entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    target_code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    target_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_app: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latest_run_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    summary_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_refs: Mapped[list] = mapped_column(JSON, default=list)
    backtest_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
