from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.learning.schemas import (
    AgentToolCallRequest,
    AgentToolCallResponse,
    AgentToolDefinitionRead,
    AutoReviewBatchResult,
    BotChatRequest,
    BotChatResponse,
    DailyPlanRequest,
    FailurePatternRead,
    JournalEntryCreate,
    JournalEntryRead,
    MarketStateSnapshotRead,
    MacroContextRead,
    MacroSignalCreate,
    MacroSignalRead,
    MemoryItemCreate,
    MemoryItemRead,
    OrchestratorActResponse,
    OrchestratorDoResponse,
    OrchestratorPhaseResponse,
    OrchestratorPlanResponse,
    PDCACycleCreate,
    PDCACycleRead,
)
from app.domains.learning.services import (
    AutoReviewService,
    BotChatService,
    FailureAnalysisService,
    JournalService,
    MemoryService,
    OrchestratorService,
    PDCACycleService,
)
from app.domains.learning.macro import MacroContextService
from app.domains.learning.world_state import MarketStateService
from app.domains.learning.tools import AgentToolError, AgentToolGatewayService

journal_router = APIRouter()
memory_router = APIRouter()
macro_router = APIRouter()
failure_patterns_router = APIRouter()
auto_reviews_router = APIRouter()
pdca_router = APIRouter()
orchestrator_router = APIRouter()
chat_router = APIRouter()
tools_router = APIRouter()

journal_service = JournalService()
memory_service = MemoryService()
failure_analysis_service = FailureAnalysisService()
auto_review_service = AutoReviewService()
pdca_service = PDCACycleService()
orchestrator_service = OrchestratorService()
bot_chat_service = BotChatService()
macro_context_service = MacroContextService()
market_state_service = MarketStateService()
agent_tool_gateway_service = AgentToolGatewayService()


@journal_router.get("", response_model=list[JournalEntryRead])
async def list_journal_entries(session: Session = Depends(get_db_session)) -> list[JournalEntryRead]:
    return journal_service.list_entries(session)


@journal_router.post("", response_model=JournalEntryRead, status_code=status.HTTP_201_CREATED)
async def create_journal_entry(payload: JournalEntryCreate, session: Session = Depends(get_db_session)) -> JournalEntryRead:
    return journal_service.create_entry(session, payload)


@memory_router.get("", response_model=list[MemoryItemRead])
async def list_memory_items(session: Session = Depends(get_db_session)) -> list[MemoryItemRead]:
    return memory_service.list_items(session)


@memory_router.get("/context", response_model=list[MemoryItemRead])
async def retrieve_memory_context(
    scope: str,
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> list[MemoryItemRead]:
    return memory_service.retrieve_scope(session, scope=scope, limit=limit)


@memory_router.post("", response_model=MemoryItemRead, status_code=status.HTTP_201_CREATED)
async def create_memory_item(payload: MemoryItemCreate, session: Session = Depends(get_db_session)) -> MemoryItemRead:
    return memory_service.create_item(session, payload)


@macro_router.get("/signals", response_model=list[MacroSignalRead], status_code=status.HTTP_200_OK)
async def list_macro_signals(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> list[MacroSignalRead]:
    return macro_context_service.list_signals(session, limit=limit)


@macro_router.post("/signals", response_model=MacroSignalRead, status_code=status.HTTP_201_CREATED)
async def create_macro_signal(
    payload: MacroSignalCreate,
    session: Session = Depends(get_db_session),
) -> MacroSignalRead:
    return macro_context_service.create_signal(session, payload)


@macro_router.get("/context", response_model=MacroContextRead, status_code=status.HTTP_200_OK)
async def get_macro_context(
    limit: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(get_db_session),
) -> MacroContextRead:
    return macro_context_service.get_context(session, limit=limit)


@macro_router.get("/state-snapshots", response_model=list[MarketStateSnapshotRead], status_code=status.HTTP_200_OK)
async def list_market_state_snapshots(
    limit: int = Query(default=20, ge=1, le=100),
    pdca_phase: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> list[MarketStateSnapshotRead]:
    return [MarketStateSnapshotRead.model_validate(item) for item in market_state_service.list_snapshots(session, limit=limit, pdca_phase=pdca_phase)]


@macro_router.get("/state-snapshots/latest", response_model=MarketStateSnapshotRead, status_code=status.HTTP_200_OK)
async def get_latest_market_state_snapshot(
    pdca_phase: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> MarketStateSnapshotRead:
    snapshot = market_state_service.get_latest_snapshot(session, pdca_phase=pdca_phase)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No market-state snapshot is available.")
    return MarketStateSnapshotRead.model_validate(snapshot)


@failure_patterns_router.get("", response_model=list[FailurePatternRead])
async def list_failure_patterns(session: Session = Depends(get_db_session)) -> list[FailurePatternRead]:
    return failure_analysis_service.list_patterns(session)


@failure_patterns_router.get("/{strategy_id}", response_model=list[FailurePatternRead])
async def list_failure_patterns_for_strategy(
    strategy_id: int,
    session: Session = Depends(get_db_session),
) -> list[FailurePatternRead]:
    return failure_analysis_service.list_patterns_for_strategy(session, strategy_id)


@auto_reviews_router.post("/losses/generate", response_model=AutoReviewBatchResult, status_code=status.HTTP_200_OK)
async def generate_loss_reviews(session: Session = Depends(get_db_session)) -> AutoReviewBatchResult:
    return auto_review_service.generate_pending_loss_reviews(session)


@pdca_router.get("/cycles", response_model=list[PDCACycleRead])
async def list_cycles(session: Session = Depends(get_db_session)) -> list[PDCACycleRead]:
    return pdca_service.list_cycles(session)


@pdca_router.post("/cycles", response_model=PDCACycleRead, status_code=status.HTTP_201_CREATED)
async def create_cycle(payload: PDCACycleCreate, session: Session = Depends(get_db_session)) -> PDCACycleRead:
    return pdca_service.create_cycle(session, payload)


@pdca_router.post("/run-daily", response_model=PDCACycleRead, status_code=status.HTTP_201_CREATED)
async def run_daily_plan(
    cycle_date: date | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> PDCACycleRead:
    return pdca_service.create_daily_plan(session, cycle_date or date.today())


@orchestrator_router.post("/plan", response_model=OrchestratorPlanResponse, status_code=status.HTTP_201_CREATED)
async def plan_daily_cycle(
    payload: DailyPlanRequest,
    session: Session = Depends(get_db_session),
) -> OrchestratorPlanResponse:
    return orchestrator_service.plan_daily_cycle(session, payload)


@orchestrator_router.post("/do", response_model=OrchestratorDoResponse, status_code=status.HTTP_200_OK)
async def run_do_phase(session: Session = Depends(get_db_session)) -> OrchestratorDoResponse:
    return orchestrator_service.run_do_phase(session)


@orchestrator_router.post("/check", response_model=OrchestratorPhaseResponse, status_code=status.HTTP_200_OK)
async def run_check_phase(session: Session = Depends(get_db_session)) -> OrchestratorPhaseResponse:
    return orchestrator_service.run_check_phase(session)


@orchestrator_router.post("/act", response_model=OrchestratorActResponse, status_code=status.HTTP_200_OK)
async def run_act_phase(session: Session = Depends(get_db_session)) -> OrchestratorActResponse:
    return orchestrator_service.run_act_phase(session)


@chat_router.post("", response_model=BotChatResponse, status_code=status.HTTP_200_OK)
async def chat_with_bot(payload: BotChatRequest, session: Session = Depends(get_db_session)) -> BotChatResponse:
    return bot_chat_service.reply(session, payload.message)


@tools_router.get("", response_model=list[AgentToolDefinitionRead], status_code=status.HTTP_200_OK)
async def list_agent_tools() -> list[AgentToolDefinitionRead]:
    return [AgentToolDefinitionRead.model_validate(item) for item in agent_tool_gateway_service.list_tools()]


@tools_router.post("/execute", response_model=AgentToolCallResponse, status_code=status.HTTP_200_OK)
async def execute_agent_tool(
    payload: AgentToolCallRequest,
    session: Session = Depends(get_db_session),
) -> AgentToolCallResponse:
    try:
        result = agent_tool_gateway_service.execute(session, payload.tool_name, payload.arguments)
    except AgentToolError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AgentToolCallResponse(tool_name=payload.tool_name, result=result)
