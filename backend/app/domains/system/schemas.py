from datetime import datetime

from pydantic import BaseModel


class SeedResponse(BaseModel):
    hypotheses: int
    setups: int
    signal_definitions: int
    strategies: int
    screeners: int
    watchlists: int
    watchlist_items: int


class SystemEventRead(BaseModel):
    id: int
    event_type: str
    entity_type: str
    entity_id: int | None
    source: str
    pdca_phase_hint: str | None
    dispatch_status: str
    dispatched_phase: str | None
    dispatch_note: str | None
    dispatched_at: datetime | None
    processed_at: datetime | None
    payload: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class SystemEventDispatchRead(BaseModel):
    pending_events_seen: int
    processed_events: int
    ignored_events: int
    failed_events: int
    phases_run: list[str]
    processed_event_ids: list[int]
    ignored_event_ids: list[int]


class SchedulerJobRead(BaseModel):
    job_id: str
    next_run_time: str | None


class BotIncidentRead(BaseModel):
    incident_id: int
    source: str
    title: str
    detail: str
    status: str
    detected_at: str
    resolved_at: str | None = None


class BotRuntimeRead(BaseModel):
    status: str
    current_phase: str | None = None
    pause_reason: str | None = None
    requires_attention: bool
    last_cycle_started_at: str | None = None
    last_cycle_completed_at: str | None = None
    last_successful_phase: str | None = None
    last_error: str | None = None
    cadence_mode: str
    interval_minutes: int
    continuous_idle_seconds: int
    cycle_runs: int
    incidents: list[BotIncidentRead]


class AIAgentRuntimeRead(BaseModel):
    enabled: bool
    provider: str
    model: str | None = None
    ready: bool
    decision_protocol_version: str | None = None
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_ready: bool = False
    active_provider: str | None = None
    active_model: str | None = None
    last_decision_provider: str | None = None
    last_decision_at: str | None = None
    last_decision_action: str | None = None
    last_decision_summary: str | None = None
    decision_count: int
    fallback_count: int = 0
    calls_last_hour: int = 0
    calls_today: int = 0
    last_error: str | None = None
    cooldown_until: str | None = None


class MarketMonitorRuntimeRead(BaseModel):
    enabled: bool
    active: bool
    transport: str
    subscribed_tickers: list[str]
    subscribed_conids: list[str]
    last_connected_at: str | None = None
    last_event_at: str | None = None
    processed_events: int
    adjusted_positions: int
    closed_positions: int
    reconnect_count: int
    last_error: str | None = None
    last_event_summary: str | None = None


class MarketDataRuntimeRead(BaseModel):
    provider: str
    probe_ticker: str
    status: str
    ready: bool
    using_fallback: bool
    source: str | None = None
    last_price: float | None = None
    provider_error: str | None = None
    last_checked_at: str | None = None


class LearningGovernanceRuntimeRead(BaseModel):
    enabled: bool
    status: str
    interval_minutes: int
    last_sync_started_at: str | None = None
    last_sync_completed_at: str | None = None
    sync_runs: int
    last_summary: str | None = None
    last_error: str | None = None
    last_changed_workflows: int = 0
    last_open_workflows: int = 0
    last_open_items: int = 0


class SchedulerStatusRead(BaseModel):
    enabled: bool
    running: bool
    jobs: list[SchedulerJobRead]
    bot: BotRuntimeRead
    ai: AIAgentRuntimeRead
    learning_governance: LearningGovernanceRuntimeRead
    market_data: MarketDataRuntimeRead
    monitor: MarketMonitorRuntimeRead
