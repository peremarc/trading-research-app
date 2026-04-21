from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.learning.services import OrchestratorService
from app.domains.system.events import EventLogService
from app.domains.system.runtime import scheduler_service
from app.domains.system.schemas import SchedulerStatusRead, SeedResponse, SystemEventDispatchRead, SystemEventRead
from app.domains.system.services import SeedService

health_router = APIRouter()
bootstrap_router = APIRouter()
scheduler_router = APIRouter()
events_router = APIRouter()

seed_service = SeedService()
event_log_service = EventLogService()


@health_router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@bootstrap_router.post("/seed", response_model=SeedResponse, status_code=status.HTTP_201_CREATED)
async def seed_initial_data(session: Session = Depends(get_db_session)) -> SeedResponse:
    return SeedResponse.model_validate(seed_service.seed_initial_data(session))


@scheduler_router.get("/status", response_model=SchedulerStatusRead)
async def get_scheduler_status(session: Session = Depends(get_db_session)) -> SchedulerStatusRead:
    scheduler_service.configure()
    return SchedulerStatusRead.model_validate(scheduler_service.get_status_payload(session=session))


@scheduler_router.post("/start", response_model=SchedulerStatusRead, status_code=status.HTTP_200_OK)
async def start_scheduler_bot(session: Session = Depends(get_db_session)) -> SchedulerStatusRead:
    return SchedulerStatusRead.model_validate(scheduler_service.start_bot(session=session))


@scheduler_router.post("/pause", response_model=SchedulerStatusRead, status_code=status.HTTP_200_OK)
async def pause_scheduler_bot(session: Session = Depends(get_db_session)) -> SchedulerStatusRead:
    return SchedulerStatusRead.model_validate(scheduler_service.pause_bot(session=session))


@events_router.get("", response_model=list[SystemEventRead], status_code=status.HTTP_200_OK)
async def list_system_events(
    limit: int = Query(default=50, ge=1, le=200),
    event_type: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    pdca_phase_hint: str | None = Query(default=None),
    dispatch_status: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> list[SystemEventRead]:
    return event_log_service.list_events(
        session,
        limit=limit,
        event_type=event_type,
        entity_type=entity_type,
        pdca_phase_hint=pdca_phase_hint,
        dispatch_status=dispatch_status,
    )


@events_router.post("/dispatch", response_model=SystemEventDispatchRead, status_code=status.HTTP_200_OK)
async def dispatch_pending_system_events(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> SystemEventDispatchRead:
    return SystemEventDispatchRead.model_validate(
        event_log_service.dispatch_pending(
            session,
            orchestrator_service=OrchestratorService(),
            limit=limit,
        )
    )
