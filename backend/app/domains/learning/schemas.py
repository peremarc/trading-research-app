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
    work_queue: WorkQueueRead | None = None


class OrchestratorPhaseResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict


class ExecutionCandidateResult(BaseModel):
    ticker: str
    watchlist_item_id: int
    analysis_run_id: int
    signal_id: int | None = None
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
    exits: AutoExitBatchResult | None = None
    discovery: dict | None = None


class OrchestratorActResponse(BaseModel):
    phase: str
    status: str
    summary: str
    metrics: dict
    generated_variants: int
