from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalysisRunCreate(BaseModel):
    ticker: str
    strategy_version_id: int | None = None
    watchlist_item_id: int | None = None
    quant_summary: dict = Field(default_factory=dict)
    visual_summary: dict = Field(default_factory=dict)
    combined_score: float | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    risk_reward: float | None = None
    decision: str = "watch"
    decision_confidence: float | None = None
    rationale: str | None = None


class AnalysisRunRead(BaseModel):
    id: int
    ticker: str
    strategy_version_id: int | None
    watchlist_item_id: int | None
    quant_summary: dict
    visual_summary: dict
    combined_score: float | None
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    risk_reward: float | None
    decision: str
    decision_confidence: float | None
    rationale: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MarketSnapshotRead(BaseModel):
    ticker: str
    price: float
    sma_20: float
    sma_50: float
    sma_200: float
    rsi_14: float
    relative_volume: float
    atr_14: float
    week_performance: float
    month_performance: float


class OHLCVCandleRead(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class TradeSignalCreate(BaseModel):
    hypothesis_id: int | None = None
    strategy_id: int | None = None
    strategy_version_id: int | None = None
    setup_id: int | None = None
    signal_definition_id: int | None = None
    watchlist_item_id: int | None = None
    ticker: str
    timeframe: str = "1D"
    signal_type: str = "trend_following"
    thesis: str | None = None
    entry_zone: dict = Field(default_factory=dict)
    stop_zone: dict = Field(default_factory=dict)
    target_zone: dict = Field(default_factory=dict)
    signal_context: dict = Field(default_factory=dict)
    quality_score: float | None = None
    status: str = "new"
    rejection_reason: str | None = None


class TradeSignalStatusUpdate(BaseModel):
    status: str
    rejection_reason: str | None = None


class TradeSignalRead(BaseModel):
    id: int
    hypothesis_id: int | None
    strategy_id: int | None
    strategy_version_id: int | None
    setup_id: int | None
    signal_definition_id: int | None
    watchlist_item_id: int | None
    ticker: str
    timeframe: str
    signal_type: str
    signal_time: datetime
    thesis: str | None
    entry_zone: dict
    stop_zone: dict
    target_zone: dict
    signal_context: dict
    quality_score: float | None
    status: str
    rejection_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# Backward-compatible aliases while the API migrates to the clearer naming.
SignalCreate = TradeSignalCreate
SignalStatusUpdate = TradeSignalStatusUpdate
SignalRead = TradeSignalRead


class ResearchTaskCreate(BaseModel):
    strategy_id: int | None = None
    task_type: str
    priority: str = "normal"
    status: str = "open"
    title: str
    hypothesis: str
    scope: dict = Field(default_factory=dict)


class ResearchTaskComplete(BaseModel):
    result_summary: str


class ResearchTaskRead(BaseModel):
    id: int
    strategy_id: int | None
    task_type: str
    priority: str
    status: str
    title: str
    hypothesis: str
    scope: dict
    result_summary: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class ResearchBacktestCreate(BaseModel):
    requested_by: str | None = None
    reason: str | None = None
    strategy_id: int | None = None
    strategy_version_id: int | None = None
    research_task_id: int | None = None
    skill_candidate_id: int | None = None
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)


class ResearchBacktestRead(BaseModel):
    id: int
    remote_run_id: str
    provider: str
    status: str
    engine: str | None = None
    spec_version: str | None = None
    dataset_version: str | None = None
    strategy_id: int | None = None
    strategy_version_id: int | None = None
    research_task_id: int | None = None
    skill_candidate_id: int | None = None
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    target_type: str | None = None
    target_code: str | None = None
    target_version: str | None = None
    requested_by: str | None = None
    source_app: str | None = None
    latest_run_payload: dict[str, Any] = Field(default_factory=dict)
    summary_metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    backtest_spec: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    submitted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_synced_at: datetime | None = None
    updated_at: datetime
    remote_urls: dict[str, str] = Field(default_factory=dict)


class ResearchBacktestProviderContextRead(BaseModel):
    configured: bool
    provider: str
    base_url: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    ai_context: dict[str, Any] = Field(default_factory=dict)


class ResearchBacktestSyncErrorRead(BaseModel):
    backtest_id: int
    remote_run_id: str
    error: str


class ResearchBacktestBatchSyncRead(BaseModel):
    attempted: int
    updated: int
    terminal: int
    failed: int
    items: list[ResearchBacktestRead] = Field(default_factory=list)
    errors: list[ResearchBacktestSyncErrorRead] = Field(default_factory=list)


class NewsArticleRead(BaseModel):
    title: str
    description: str | None = None
    url: str
    source_name: str
    published_at: str
    image: str | None = None


class CalendarEventRead(BaseModel):
    event_type: str
    title: str
    event_date: str
    ticker: str | None = None
    exchange: str | None = None
    country: str | None = None
    impact: str | None = None
    estimate: str | None = None
    actual: str | None = None
    previous: str | None = None
    currency: str | None = None
    source: str
    raw: dict | None = None


class CalendarCacheStatusRead(BaseModel):
    provider: str | None = None
    available: bool = False
    cached_at: str | None = None
    age_seconds: int | None = None
    ttl_seconds: int | None = None
    stale: bool = False


class CorporateCalendarContextRead(BaseModel):
    ticker: str
    source: str
    used_fallback: bool = False
    provider_error: str | None = None
    fallback_reason: str | None = None
    events: list[CalendarEventRead]
    cache: CalendarCacheStatusRead | None = None


class WorkItemRead(BaseModel):
    priority: str
    item_type: str
    reference_id: int | None = None
    title: str
    context: dict = Field(default_factory=dict)


class ProviderRuntimeStatusRead(BaseModel):
    provider: str
    configured: bool = False
    cooling_down: bool = False
    cooldown_remaining_seconds: float = 0.0
    concurrency_limit: int = 0


class WorkQueueSummaryRead(BaseModel):
    due_reanalysis_items: int = 0
    deferred_reanalysis_items: int = 0
    runtime_aware_watchlist_items: int = 0
    next_reanalysis_at: str | None = None
    next_reanalysis_ticker: str | None = None
    timing_samples: int = 0
    timing_last_signal_at: str | None = None
    avg_total_ms: float | None = None
    avg_decision_context_ms: float | None = None
    avg_reanalysis_gate_ms: float | None = None
    dominant_stage: str | None = None
    dominant_stage_avg_ms: float | None = None
    dominant_decision_context_stage: str | None = None
    dominant_decision_context_stage_avg_ms: float | None = None
    market_data_provider_status: dict[str, ProviderRuntimeStatusRead] = Field(default_factory=dict)
    calendar_provider_status: dict[str, ProviderRuntimeStatusRead] = Field(default_factory=dict)
    news_provider_status: dict[str, ProviderRuntimeStatusRead] = Field(default_factory=dict)


class WorkQueueRead(BaseModel):
    total_items: int
    items: list[WorkItemRead]
    summary: WorkQueueSummaryRead = Field(default_factory=WorkQueueSummaryRead)
