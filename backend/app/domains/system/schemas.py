from pydantic import BaseModel


class SeedResponse(BaseModel):
    strategies: int
    screeners: int
    watchlists: int
    watchlist_items: int


class SchedulerJobRead(BaseModel):
    job_id: str
    next_run_time: str | None


class SchedulerStatusRead(BaseModel):
    enabled: bool
    running: bool
    jobs: list[SchedulerJobRead]
