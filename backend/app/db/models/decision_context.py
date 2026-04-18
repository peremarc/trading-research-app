from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DecisionContextSnapshot(Base):
    __tablename__ = "decision_context_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True, index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=True, index=True
    )
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    decision_phase: Mapped[str] = mapped_column(String(20), default="do")
    planner_action: Mapped[str] = mapped_column(String(30), default="watch")
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    quant_features: Mapped[dict] = mapped_column(JSON, default=dict)
    visual_features: Mapped[dict] = mapped_column(JSON, default=dict)
    calendar_context: Mapped[dict] = mapped_column(JSON, default=dict)
    news_context: Mapped[dict] = mapped_column(JSON, default=dict)
    web_context: Mapped[dict] = mapped_column(JSON, default=dict)
    macro_context: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_context: Mapped[dict] = mapped_column(JSON, default=dict)
    position_context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def trade_signal_id(self) -> int | None:
        return self.signal_id


class FeatureOutcomeStat(Base):
    __tablename__ = "feature_outcome_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=True, index=True
    )
    feature_scope: Mapped[str] = mapped_column(String(30), index=True)
    feature_key: Mapped[str] = mapped_column(String(80), index=True)
    feature_value: Mapped[str] = mapped_column(String(120), index=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    executed_count: Mapped[int] = mapped_column(Integer, default=0)
    wins_count: Mapped[int] = mapped_column(Integer, default=0)
    losses_count: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_runup_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    last_recomputed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StrategyContextRule(Base):
    __tablename__ = "strategy_context_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True, index=True)
    strategy_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=True, index=True
    )
    feature_scope: Mapped[str] = mapped_column(String(30), index=True)
    feature_key: Mapped[str] = mapped_column(String(80), index=True)
    feature_value: Mapped[str] = mapped_column(String(120), index=True)
    action_type: Mapped[str] = mapped_column(String(30), default="downgrade_to_watch")
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    source: Mapped[str] = mapped_column(String(30), default="feature_outcome_stat")
    evidence_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
