from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.domains.market.schemas import WorkQueueRead
from app.domains.execution.schemas import AutoExitBatchResult


class JournalEntryCreate(BaseModel):
    entry_type: str
    ticker: str | None = None
    strategy_id: int | None = None
    strategy_version_id: int | None = None
    position_id: int | None = None
    pdca_cycle_id: int | None = None
    market_context: dict = Field(default_factory=dict)
    hypothesis: str | None = None
    observations: dict = Field(default_factory=dict)
    reasoning: str | None = None
    decision: str | None = None
    expectations: str | None = None
    outcome: str | None = None
    lessons: str | None = None


class JournalEntryRead(BaseModel):
    id: int
    entry_type: str
    event_time: datetime
    ticker: str | None
    strategy_id: int | None
    strategy_version_id: int | None
    position_id: int | None
    pdca_cycle_id: int | None
    market_context: dict
    hypothesis: str | None
    observations: dict
    reasoning: str | None
    decision: str | None
    expectations: str | None
    outcome: str | None
    lessons: str | None

    model_config = {"from_attributes": True}


class MemoryItemCreate(BaseModel):
    memory_type: str
    scope: str
    key: str
    content: str
    meta: dict = Field(default_factory=dict)
    importance: float = 0.5


class MemoryItemRead(BaseModel):
    id: int
    memory_type: str
    scope: str
    key: str
    content: str
    meta: dict
    importance: float
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FailurePatternRead(BaseModel):
    id: int
    strategy_id: int
    strategy_version_id: int | None
    failure_mode: str
    pattern_signature: str
    occurrences: int
    avg_loss_pct: float | None
    evidence: dict
    recommended_action: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AutoReviewResult(BaseModel):
    position_id: int
    generated: bool
    review_id: int | None = None
    reason: str


class AutoReviewBatchResult(BaseModel):
    generated_reviews: int
    skipped_positions: int
    results: list[AutoReviewResult]


class PDCACycleCreate(BaseModel):
    cycle_date: date
    phase: str
    status: str = "pending"
    summary: str | None = None
    context: dict = Field(default_factory=dict)


class PDCACycleRead(BaseModel):
    id: int
    cycle_date: date
    phase: str
    status: str
    summary: str | None
    context: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class DailyPlanRequest(BaseModel):
    cycle_date: date
    market_context: dict = Field(default_factory=dict)


class OrchestratorPlanResponse(BaseModel):
    cycle_id: int
    phase: str
    status: str
    summary: str
    market_context: dict
    market_state_snapshot: MarketStateSnapshotRead | None = None
    work_queue: WorkQueueRead | None = None


class OrchestratorPhaseResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    market_state_snapshot: MarketStateSnapshotRead | None = None


class ExecutionCandidateResult(BaseModel):
    ticker: str
    watchlist_item_id: int
    analysis_run_id: int
    signal_id: int | None = None
    trade_signal_id: int | None = None
    decision: str
    score: float
    position_id: int | None = None


class OrchestratorDoResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    generated_analyses: int
    opened_positions: int
    candidates: list[ExecutionCandidateResult]
    market_state_snapshot: MarketStateSnapshotRead | None = None
    exits: AutoExitBatchResult | None = None
    discovery: dict | None = None


class OrchestratorActResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    generated_variants: int
    market_state_snapshot: MarketStateSnapshotRead | None = None


class BotChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class BotChatResponse(BaseModel):
    topic: str
    reply: str
    suggested_prompts: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


class MacroSignalCreate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    regime: str = Field(default="neutral", min_length=1, max_length=40)
    relevance: str = Field(default="general", min_length=1, max_length=60)
    tickers: list[str] = Field(default_factory=list)
    timeframe: str | None = Field(default=None, max_length=60)
    scenario: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=120)
    evidence: dict = Field(default_factory=dict)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class MacroSignalRead(BaseModel):
    id: int
    memory_type: str
    scope: str
    key: str
    content: str
    meta: dict
    importance: float
    valid_from: datetime | None
    valid_to: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MacroContextRead(BaseModel):
    summary: str
    active_regimes: list[str] = Field(default_factory=list)
    relevance_tags: list[str] = Field(default_factory=list)
    tracked_tickers: list[str] = Field(default_factory=list)
    signals: list[dict] = Field(default_factory=list)


class MarketStateSnapshotRead(BaseModel):
    id: int
    trigger: str
    pdca_phase: str | None
    execution_mode: str
    benchmark_ticker: str
    regime_label: str
    regime_confidence: float | None
    summary: str
    snapshot_payload: dict
    source_context: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentToolDefinitionRead(BaseModel):
    name: str
    category: str
    description: str
    input_schema: dict = Field(default_factory=dict)


class AgentToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict = Field(default_factory=dict)


class AgentToolCallResponse(BaseModel):
    tool_name: str
    result: dict = Field(default_factory=dict)
