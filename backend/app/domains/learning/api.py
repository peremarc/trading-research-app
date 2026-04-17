from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.learning.schemas import (
    AutoReviewBatchResult,
    DailyPlanRequest,
    FailurePatternRead,
    JournalEntryCreate,
    JournalEntryRead,
    MemoryItemCreate,
    MemoryItemRead,
    OrchestratorActResponse,
    OrchestratorPhaseResponse,
    OrchestratorPlanResponse,
    PDCACycleCreate,
    PDCACycleRead,
)
from app.domains.learning.services import (
    AutoReviewService,
    FailureAnalysisService,
    JournalService,
    MemoryService,
    OrchestratorService,
    PDCACycleService,
)
from app.schemas.execution import OrchestratorDoResponse

journal_router = APIRouter()
memory_router = APIRouter()
failure_patterns_router = APIRouter()
auto_reviews_router = APIRouter()
pdca_router = APIRouter()
orchestrator_router = APIRouter()

journal_service = JournalService()
memory_service = MemoryService()
failure_analysis_service = FailureAnalysisService()
auto_review_service = AutoReviewService()
pdca_service = PDCACycleService()
orchestrator_service = OrchestratorService()


@journal_router.get("", response_model=list[JournalEntryRead])
def list_journal_entries(session: Session = Depends(get_db_session)) -> list[JournalEntryRead]:
    return journal_service.list_entries(session)


@journal_router.post("", response_model=JournalEntryRead, status_code=status.HTTP_201_CREATED)
def create_journal_entry(payload: JournalEntryCreate, session: Session = Depends(get_db_session)) -> JournalEntryRead:
    return journal_service.create_entry(session, payload)


@memory_router.get("", response_model=list[MemoryItemRead])
def list_memory_items(session: Session = Depends(get_db_session)) -> list[MemoryItemRead]:
    return memory_service.list_items(session)


@memory_router.get("/context", response_model=list[MemoryItemRead])
def retrieve_memory_context(
    scope: str,
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> list[MemoryItemRead]:
    return memory_service.retrieve_scope(session, scope=scope, limit=limit)


@memory_router.post("", response_model=MemoryItemRead, status_code=status.HTTP_201_CREATED)
def create_memory_item(payload: MemoryItemCreate, session: Session = Depends(get_db_session)) -> MemoryItemRead:
    return memory_service.create_item(session, payload)


@failure_patterns_router.get("", response_model=list[FailurePatternRead])
def list_failure_patterns(session: Session = Depends(get_db_session)) -> list[FailurePatternRead]:
    return failure_analysis_service.list_patterns(session)


@failure_patterns_router.get("/{strategy_id}", response_model=list[FailurePatternRead])
def list_failure_patterns_for_strategy(
    strategy_id: int,
    session: Session = Depends(get_db_session),
) -> list[FailurePatternRead]:
    return failure_analysis_service.list_patterns_for_strategy(session, strategy_id)


@auto_reviews_router.post("/losses/generate", response_model=AutoReviewBatchResult, status_code=status.HTTP_200_OK)
def generate_loss_reviews(session: Session = Depends(get_db_session)) -> AutoReviewBatchResult:
    return auto_review_service.generate_pending_loss_reviews(session)


@pdca_router.get("/cycles", response_model=list[PDCACycleRead])
def list_cycles(session: Session = Depends(get_db_session)) -> list[PDCACycleRead]:
    return pdca_service.list_cycles(session)


@pdca_router.post("/cycles", response_model=PDCACycleRead, status_code=status.HTTP_201_CREATED)
def create_cycle(payload: PDCACycleCreate, session: Session = Depends(get_db_session)) -> PDCACycleRead:
    return pdca_service.create_cycle(session, payload)


@pdca_router.post("/run-daily", response_model=PDCACycleRead, status_code=status.HTTP_201_CREATED)
def run_daily_plan(
    cycle_date: date | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PDCACycleRead:
    return pdca_service.create_daily_plan(session, cycle_date or date.today())


@orchestrator_router.post("/plan", response_model=OrchestratorPlanResponse, status_code=status.HTTP_201_CREATED)
def plan_daily_cycle(
    payload: DailyPlanRequest,
    session: Session = Depends(get_db_session),
) -> OrchestratorPlanResponse:
    return orchestrator_service.plan_daily_cycle(session, payload)


@orchestrator_router.post("/do", response_model=OrchestratorDoResponse, status_code=status.HTTP_200_OK)
def run_do_phase(session: Session = Depends(get_db_session)) -> OrchestratorDoResponse:
    return orchestrator_service.run_do_phase(session)


@orchestrator_router.post("/check", response_model=OrchestratorPhaseResponse, status_code=status.HTTP_200_OK)
def run_check_phase(session: Session = Depends(get_db_session)) -> OrchestratorPhaseResponse:
    return orchestrator_service.run_check_phase(session)


@orchestrator_router.post("/act", response_model=OrchestratorActResponse, status_code=status.HTTP_200_OK)
def run_act_phase(session: Session = Depends(get_db_session)) -> OrchestratorActResponse:
    return orchestrator_service.run_act_phase(session)
