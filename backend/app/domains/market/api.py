from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.market.schemas import (
    AnalysisRunCreate,
    AnalysisRunRead,
    CorporateCalendarContextRead,
    CalendarEventRead,
    MarketSnapshotRead,
    NewsArticleRead,
    OHLCVCandleRead,
    ResearchBacktestBatchSyncRead,
    ResearchBacktestCreate,
    ResearchBacktestProviderContextRead,
    ResearchBacktestRead,
    ResearchTaskComplete,
    ResearchTaskCreate,
    ResearchTaskRead,
    TradeSignalCreate,
    TradeSignalRead,
    TradeSignalStatusUpdate,
    WorkQueueRead,
)
from app.domains.market.backtesting import (
    ResearchBacktestDependencyError,
    ResearchBacktestNotFoundError,
    ResearchBacktestService,
)
from app.domains.market.analysis import FusedAnalysisService, normalize_chart_timeframe
from app.domains.market.services import (
    AnalysisService,
    CalendarService,
    MarketDataService,
    NewsService,
    ResearchService,
    SignalService,
    WorkQueueService,
)
from app.providers.calendar import CalendarProviderError
from app.providers.backtesting import BacktestingProviderError
from app.providers.news import NewsProviderError

analysis_router = APIRouter()
market_data_router = APIRouter()
signals_router = APIRouter()
trade_signals_router = APIRouter()
research_router = APIRouter()
work_queue_router = APIRouter()
news_router = APIRouter()
calendar_router = APIRouter()

analysis_service = AnalysisService()
market_data_service = MarketDataService()
signal_service = SignalService()
research_service = ResearchService()
work_queue_service = WorkQueueService()
news_service = NewsService()
research_backtest_service = ResearchBacktestService()
calendar_service = CalendarService()


def _get_fused_analysis_service():
    return FusedAnalysisService()


def get_research_backtest_service(request: Request) -> ResearchBacktestService:
    service = getattr(request.app.state, "market_backtest_service", None)
    if service is None:
        service = research_backtest_service
        request.app.state.market_backtest_service = service
    return service


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
async def get_market_history(ticker: str, timeframe: str | None = None) -> list[OHLCVCandleRead]:
    limit = 120 if timeframe is None else normalize_chart_timeframe(timeframe)[1]
    return [
        OHLCVCandleRead.model_validate(candle.__dict__)
        for candle in market_data_service.get_history(ticker, limit=limit)
    ]


@market_data_router.get("/{ticker}/analysis")
async def get_fused_analysis(ticker: str, timeframe: str | None = None) -> dict:
    analysis = _get_fused_analysis_service().analyze_ticker(ticker, timeframe=timeframe)
    return {key: value for key, value in analysis.items() if key != "chart_svg"}


@market_data_router.get("/{ticker}/chart")
async def get_standard_chart(ticker: str, timeframe: str | None = None) -> Response:
    analysis = _get_fused_analysis_service().analyze_ticker(ticker, timeframe=timeframe)
    return Response(content=analysis["chart_svg"], media_type="image/svg+xml")


@market_data_router.get("/{ticker}/chart-pack")
async def get_chart_pack(ticker: str, timeframes: str | None = None) -> dict:
    selected = [item.strip() for item in timeframes.split(",")] if isinstance(timeframes, str) and timeframes.strip() else None
    return _get_fused_analysis_service().get_multitimeframe_context(ticker=ticker, timeframes=selected)


@signals_router.get("", response_model=list[TradeSignalRead])
@trade_signals_router.get("", response_model=list[TradeSignalRead])
async def list_signals(session: Session = Depends(get_db_session)) -> list[TradeSignalRead]:
    return signal_service.list_trade_signals(session)


@signals_router.post("", response_model=TradeSignalRead, status_code=status.HTTP_201_CREATED)
@trade_signals_router.post("", response_model=TradeSignalRead, status_code=status.HTTP_201_CREATED)
async def create_signal(payload: TradeSignalCreate, session: Session = Depends(get_db_session)) -> TradeSignalRead:
    return signal_service.create_trade_signal(session, payload)


@signals_router.post("/{signal_id}/status", response_model=TradeSignalRead, status_code=status.HTTP_200_OK)
@trade_signals_router.post("/{signal_id}/status", response_model=TradeSignalRead, status_code=status.HTTP_200_OK)
async def update_signal_status(
    signal_id: int,
    payload: TradeSignalStatusUpdate,
    session: Session = Depends(get_db_session),
) -> TradeSignalRead:
    try:
        return signal_service.update_trade_signal_status(session, signal_id, payload.status, payload.rejection_reason)
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


@research_router.get("/backtests", response_model=list[ResearchBacktestRead])
async def list_research_backtests(
    status_filter: str | None = Query(default=None, alias="status"),
    strategy_id: int | None = None,
    research_task_id: int | None = None,
    skill_candidate_id: int | None = None,
    session: Session = Depends(get_db_session),
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> list[ResearchBacktestRead]:
    return backtest_service.list_runs(
        session,
        status=status_filter,
        strategy_id=strategy_id,
        research_task_id=research_task_id,
        skill_candidate_id=skill_candidate_id,
    )


@research_router.post("/backtests", response_model=ResearchBacktestRead, status_code=status.HTTP_202_ACCEPTED)
async def create_research_backtest(
    payload: ResearchBacktestCreate,
    session: Session = Depends(get_db_session),
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> ResearchBacktestRead:
    try:
        return backtest_service.submit_run(session, payload)
    except ResearchBacktestDependencyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except BacktestingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@research_router.get("/backtests/provider/context", response_model=ResearchBacktestProviderContextRead)
async def get_research_backtesting_context(
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> ResearchBacktestProviderContextRead:
    try:
        return backtest_service.provider_context()
    except BacktestingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@research_router.post("/backtests/sync-pending", response_model=ResearchBacktestBatchSyncRead)
async def sync_pending_research_backtests(
    limit: int | None = Query(default=None, ge=1, le=100),
    session: Session = Depends(get_db_session),
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> ResearchBacktestBatchSyncRead:
    try:
        return backtest_service.sync_non_terminal_runs(session, limit=limit, emit_events=True)
    except BacktestingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@research_router.get("/backtests/{backtest_id}", response_model=ResearchBacktestRead)
async def get_research_backtest(
    backtest_id: int,
    session: Session = Depends(get_db_session),
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> ResearchBacktestRead:
    try:
        return backtest_service.get_run(session, backtest_id)
    except ResearchBacktestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@research_router.post("/backtests/{backtest_id}/sync", response_model=ResearchBacktestRead)
async def sync_research_backtest(
    backtest_id: int,
    session: Session = Depends(get_db_session),
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> ResearchBacktestRead:
    try:
        return backtest_service.sync_run(session, backtest_id)
    except ResearchBacktestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BacktestingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@research_router.post("/backtests/{backtest_id}/cancel", response_model=ResearchBacktestRead)
async def cancel_research_backtest(
    backtest_id: int,
    session: Session = Depends(get_db_session),
    backtest_service: ResearchBacktestService = Depends(get_research_backtest_service),
) -> ResearchBacktestRead:
    try:
        return backtest_service.cancel_run(session, backtest_id)
    except ResearchBacktestNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BacktestingProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@news_router.get("", response_model=list[NewsArticleRead])
async def search_news(query: str) -> list[NewsArticleRead]:
    try:
        return [NewsArticleRead.model_validate(article.__dict__) for article in news_service.list_news(query)]
    except NewsProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@news_router.get("/{ticker}", response_model=list[NewsArticleRead])
async def get_ticker_news(ticker: str) -> list[NewsArticleRead]:
    try:
        return [NewsArticleRead.model_validate(article.__dict__) for article in news_service.list_news_for_ticker(ticker)]
    except NewsProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@calendar_router.get("/corporate/{ticker}", response_model=list[CalendarEventRead])
async def get_ticker_calendar(ticker: str, days_ahead: int = 21) -> list[CalendarEventRead]:
    try:
        return [
            CalendarEventRead.model_validate(event.__dict__)
            for event in calendar_service.list_ticker_events(ticker, days_ahead=days_ahead)
        ]
    except CalendarProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@calendar_router.get("/corporate-context/{ticker}", response_model=CorporateCalendarContextRead)
async def get_ticker_calendar_context(ticker: str, days_ahead: int = 21) -> CorporateCalendarContextRead:
    try:
        context = calendar_service.get_ticker_event_context(ticker, days_ahead=days_ahead)
    except CalendarProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return CorporateCalendarContextRead.model_validate(
        {
            **context,
            "events": [
                CalendarEventRead.model_validate(event.__dict__ if hasattr(event, "__dict__") else event)
                for event in context.get("events", [])
            ],
        }
    )


@calendar_router.get("/macro", response_model=list[CalendarEventRead])
async def get_macro_calendar(days_ahead: int = 14) -> list[CalendarEventRead]:
    try:
        return [
            CalendarEventRead.model_validate(event.__dict__)
            for event in calendar_service.list_macro_events(days_ahead=days_ahead)
        ]
    except CalendarProviderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@work_queue_router.get("", response_model=WorkQueueRead)
async def get_work_queue(session: Session = Depends(get_db_session)) -> WorkQueueRead:
    return work_queue_service.get_queue(session)
