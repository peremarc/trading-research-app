from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LearningWorkflow(Base):
    __tablename__ = "learning_workflows"
    __table_args__ = (UniqueConstraint("workflow_type", "scope", name="uq_learning_workflow_type_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workflow_type: Mapped[str] = mapped_column(String(40), index=True)
    scope: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    items: Mapped[list] = mapped_column(JSON, default=list)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    open_item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LearningWorkflowRun(Base):
    __tablename__ = "learning_workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("learning_workflows.id"), index=True)
    run_kind: Mapped[str] = mapped_column(String(24), index=True)
    trigger_source: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="completed", index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    context_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    output_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningWorkflowArtifact(Base):
    __tablename__ = "learning_workflow_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("learning_workflows.id"), index=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("learning_workflow_runs.id"), index=True)
    artifact_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticker: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
