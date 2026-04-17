from datetime import date, datetime

from pydantic import BaseModel, Field


class StrategyVersionCreate(BaseModel):
    hypothesis: str
    general_rules: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)
    state: str = "draft"
    lifecycle_stage: str | None = None
    is_baseline: bool = False


class StrategyCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    market: str = "US_EQUITIES"
    horizon: str
    bias: str
    status: str = "research"
    initial_version: StrategyVersionCreate


class StrategyVersionRead(BaseModel):
    id: int
    version: int
    hypothesis: str
    general_rules: dict
    parameters: dict
    state: str
    lifecycle_stage: str
    is_baseline: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategyRead(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    market: str
    horizon: str
    bias: str
    status: str
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime
    versions: list[StrategyVersionRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ScreenerVersionCreate(BaseModel):
    definition: dict = Field(default_factory=dict)
    universe: str = "US_EQUITIES"
    timeframe: str = "1D"
    sorting: dict = Field(default_factory=dict)
    status: str = "draft"


class ScreenerCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    strategy_id: int | None = None
    initial_version: ScreenerVersionCreate


class ScreenerVersionRead(BaseModel):
    id: int
    version: int
    definition: dict
    universe: str
    timeframe: str
    sorting: dict
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ScreenerRead(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    strategy_id: int | None
    current_version_id: int | None
    created_at: datetime
    updated_at: datetime
    versions: list[ScreenerVersionRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class WatchlistItemCreate(BaseModel):
    ticker: str
    strategy_hypothesis: str | None = None
    score: float | None = None
    reason: str | None = None
    key_metrics: dict = Field(default_factory=dict)
    state: str = "watching"


class WatchlistItemRead(BaseModel):
    id: int
    ticker: str
    strategy_hypothesis: str | None
    score: float | None
    added_at: datetime
    reason: str | None
    key_metrics: dict
    state: str

    model_config = {"from_attributes": True}


class WatchlistCreate(BaseModel):
    code: str
    name: str
    strategy_id: int | None = None
    hypothesis: str
    status: str = "active"


class WatchlistRead(BaseModel):
    id: int
    code: str
    name: str
    strategy_id: int | None
    hypothesis: str
    status: str
    created_at: datetime
    items: list[WatchlistItemRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class CandidateValidationSummaryRead(BaseModel):
    strategy_id: int
    candidate_version_id: int
    candidate_version_number: int
    trade_count: int
    wins: int
    losses: int
    avg_pnl_pct: float | None
    avg_drawdown_pct: float | None
    win_rate: float | None
    evaluation_status: str


class CandidateValidationSnapshotRead(CandidateValidationSummaryRead):
    id: int
    generated_at: datetime

    model_config = {"from_attributes": True}


class StrategyChangeEventRead(BaseModel):
    id: int
    strategy_id: int
    source_version_id: int | None
    new_version_id: int | None
    trade_review_id: int | None
    change_reason: str
    proposed_change: str | None
    change_summary: dict
    applied_automatically: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategyActivationEventRead(BaseModel):
    id: int
    strategy_id: int
    activated_version_id: int
    previous_version_id: int | None
    activation_reason: str
    activated_automatically: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StrategyLabResult(BaseModel):
    strategy_id: int
    source_version_id: int
    new_version_id: int
    change_event_id: int
    activation_event_id: int
    trigger: str


class StrategyLabBatchResult(BaseModel):
    generated_variants: int
    skipped_candidates: int
    results: list[StrategyLabResult]


class StrategyScorecardRead(BaseModel):
    id: int
    strategy_id: int
    strategy_version_id: int | None
    period_start: date | None
    period_end: date | None
    signals_count: int
    executed_trades_count: int
    closed_trades_count: int
    wins_count: int
    losses_count: int
    win_rate: float | None
    avg_return_pct: float | None
    expectancy: float | None
    profit_factor: float | None
    avg_holding_days: float | None
    max_drawdown_pct: float | None
    activity_score: float
    quality_score: float
    fitness_score: float
    generated_at: datetime

    model_config = {"from_attributes": True}


class StrategyPipelineRead(BaseModel):
    strategy_id: int
    strategy_code: str
    strategy_name: str
    strategy_status: str
    active_version: StrategyVersionRead | None = None
    candidate_versions: list[StrategyVersionRead] = Field(default_factory=list)
    degraded_versions: list[StrategyVersionRead] = Field(default_factory=list)
    approved_versions: list[StrategyVersionRead] = Field(default_factory=list)
    archived_versions: list[StrategyVersionRead] = Field(default_factory=list)
    total_versions: int
    latest_scorecard: StrategyScorecardRead | None = None
    latest_candidate_validations: list[CandidateValidationSnapshotRead] = Field(default_factory=list)
