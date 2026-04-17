from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.system.runtime import scheduler_service
from app.domains.system.schemas import SchedulerJobRead, SchedulerStatusRead, SeedResponse
from app.domains.system.services import SeedService

health_router = APIRouter()
bootstrap_router = APIRouter()
scheduler_router = APIRouter()

seed_service = SeedService()


@health_router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@bootstrap_router.post("/seed", response_model=SeedResponse, status_code=status.HTTP_201_CREATED)
async def seed_initial_data(session: Session = Depends(get_db_session)) -> SeedResponse:
    return SeedResponse.model_validate(seed_service.seed_initial_data(session))


@scheduler_router.get("/status", response_model=SchedulerStatusRead)
async def get_scheduler_status() -> SchedulerStatusRead:
    scheduler_service.configure()
    jobs = [
        SchedulerJobRead(
            job_id=job.id,
            next_run_time=next_run_time.isoformat() if (next_run_time := getattr(job, "next_run_time", None)) else None,
        )
        for job in scheduler_service.scheduler.get_jobs()
    ]
    return SchedulerStatusRead(
        enabled=scheduler_service.settings.scheduler_enabled,
        running=scheduler_service.scheduler.running,
        jobs=jobs,
    )
