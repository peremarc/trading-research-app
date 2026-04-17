from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.market.schemas import (
    AnalysisRunCreate,
    AnalysisRunRead,
    MarketSnapshotRead,
    OHLCVCandleRead,
    ResearchTaskComplete,
    ResearchTaskCreate,
    ResearchTaskRead,
    SignalCreate,
    SignalRead,
    SignalStatusUpdate,
    WorkQueueRead,
)
from app.domains.market.services import (
    AnalysisService,
    MarketDataService,
    ResearchService,
    SignalService,
    WorkQueueService,
)

analysis_router = APIRouter()
market_data_router = APIRouter()
signals_router = APIRouter()
research_router = APIRouter()
work_queue_router = APIRouter()

analysis_service = AnalysisService()
market_data_service = MarketDataService()
signal_service = SignalService()
research_service = ResearchService()
work_queue_service = WorkQueueService()


def _get_fused_analysis_service():
    from app.domains.market.analysis import FusedAnalysisService

    return FusedAnalysisService()


@analysis_router.get("", response_model=list[AnalysisRunRead])
async def list_analysis_runs(session: Session = Depends(get_db_session)) -> list[AnalysisRunRead]:
    return analysis_service.list_runs(session)


@analysis_router.post("", response_model=AnalysisRunRead, status_code=status.HTTP_201_CREATED)
async def create_analysis_run(
    payload: AnalysisRunCreate,
    session: Session = Depends(get_db_session),
) -> AnalysisRunRead:
    return analysis_service.create_run(session, payload)


@market_data_router.get("/{ticker}", response_model=MarketSnapshotRead)
async def get_market_snapshot(ticker: str) -> MarketSnapshotRead:
    return MarketSnapshotRead.model_validate(market_data_service.get_snapshot(ticker).__dict__)


@market_data_router.get("/{ticker}/history", response_model=list[OHLCVCandleRead])
async def get_market_history(ticker: str) -> list[OHLCVCandleRead]:
    return [
        OHLCVCandleRead.model_validate(candle.__dict__)
        for candle in market_data_service.get_history(ticker, limit=120)
    ]


@market_data_router.get("/{ticker}/analysis")
async def get_fused_analysis(ticker: str) -> dict:
    analysis = _get_fused_analysis_service().analyze_ticker(ticker)
    return {key: value for key, value in analysis.items() if key != "chart_svg"}


@market_data_router.get("/{ticker}/chart")
async def get_standard_chart(ticker: str) -> Response:
    analysis = _get_fused_analysis_service().analyze_ticker(ticker)
    return Response(content=analysis["chart_svg"], media_type="image/svg+xml")


@signals_router.get("", response_model=list[SignalRead])
async def list_signals(session: Session = Depends(get_db_session)) -> list[SignalRead]:
    return signal_service.list_signals(session)


@signals_router.post("", response_model=SignalRead, status_code=status.HTTP_201_CREATED)
async def create_signal(payload: SignalCreate, session: Session = Depends(get_db_session)) -> SignalRead:
    return signal_service.create_signal(session, payload)


@signals_router.post("/{signal_id}/status", response_model=SignalRead, status_code=status.HTTP_200_OK)
async def update_signal_status(
    signal_id: int,
    payload: SignalStatusUpdate,
    session: Session = Depends(get_db_session),
) -> SignalRead:
    try:
        return signal_service.update_status(session, signal_id, payload.status, payload.rejection_reason)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@research_router.get("/tasks", response_model=list[ResearchTaskRead])
async def list_research_tasks(session: Session = Depends(get_db_session)) -> list[ResearchTaskRead]:
    return research_service.list_tasks(session)


@research_router.post("/tasks", response_model=ResearchTaskRead, status_code=status.HTTP_201_CREATED)
async def create_research_task(payload: ResearchTaskCreate, session: Session = Depends(get_db_session)) -> ResearchTaskRead:
    return research_service.create_task(session, payload)


@research_router.post("/tasks/{task_id}/complete", response_model=ResearchTaskRead, status_code=status.HTTP_200_OK)
async def complete_research_task(
    task_id: int,
    payload: ResearchTaskComplete,
    session: Session = Depends(get_db_session),
) -> ResearchTaskRead:
    try:
        return research_service.complete_task(session, task_id, payload.result_summary)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@work_queue_router.get("", response_model=WorkQueueRead)
async def get_work_queue(session: Session = Depends(get_db_session)) -> WorkQueueRead:
    return work_queue_service.get_queue(session)
