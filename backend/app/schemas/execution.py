from pydantic import BaseModel


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
