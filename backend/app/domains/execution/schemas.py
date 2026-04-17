from datetime import datetime

from pydantic import BaseModel, Field


class PositionEventCreate(BaseModel):
    event_type: str
    payload: dict = Field(default_factory=dict)
    note: str | None = None


class PositionEventRead(BaseModel):
    id: int
    event_type: str
    event_time: datetime
    payload: dict
    note: str | None

    model_config = {"from_attributes": True}


class PositionCreate(BaseModel):
    ticker: str
    signal_id: int | None = None
    strategy_version_id: int | None = None
    analysis_run_id: int | None = None
    account_mode: str = "paper"
    side: str = "long"
    entry_price: float
    stop_price: float | None = None
    target_price: float | None = None
    size: float
    thesis: str | None = None
    entry_context: dict | None = None


class PositionCloseRequest(BaseModel):
    exit_price: float
    exit_reason: str
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None
    close_context: dict | None = None


class PositionRead(BaseModel):
    id: int
    ticker: str
    signal_id: int | None
    strategy_version_id: int | None
    analysis_run_id: int | None
    account_mode: str
    side: str
    status: str
    entry_date: datetime
    entry_price: float
    stop_price: float | None
    target_price: float | None
    size: float
    thesis: str | None
    entry_context: dict | None
    exit_date: datetime | None
    exit_price: float | None
    exit_reason: str | None
    close_context: dict | None
    pnl_realized: float | None
    pnl_pct: float | None
    max_drawdown_pct: float | None
    max_runup_pct: float | None
    pnl_unrealized: float | None
    review_status: str
    events: list[PositionEventRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class AutoExitResult(BaseModel):
    position_id: int
    ticker: str
    closed: bool
    exit_price: float | None = None
    exit_reason: str


class AutoExitBatchResult(BaseModel):
    evaluated_positions: int
    closed_positions: int
    results: list[AutoExitResult]


class TradeReviewCreate(BaseModel):
    outcome_label: str
    cause_category: str
    observations: dict = Field(default_factory=dict)
    root_cause: str
    lesson_learned: str
    proposed_strategy_change: str | None = None
    should_modify_strategy: bool = False
    outcome: str | None = None
    failure_mode: str | None = None
    root_causes: list[str] = Field(default_factory=list)
    recommended_changes: list[str] = Field(default_factory=list)
    confidence: float | None = None
    review_priority: str | None = None
    needs_strategy_update: bool | None = None
    strategy_update_reason: str | None = None


class TradeReviewRead(BaseModel):
    id: int
    position_id: int
    strategy_version_id: int | None
    outcome_label: str
    outcome: str | None
    cause_category: str
    failure_mode: str | None
    observations: dict
    root_cause: str
    root_causes: list[str]
    lesson_learned: str
    proposed_strategy_change: str | None
    recommended_changes: list[str]
    confidence: float | None
    review_priority: str
    should_modify_strategy: bool
    needs_strategy_update: bool
    strategy_update_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
