from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.exceptions import DuplicateResourceError, IntegrityConstraintError
from app.db.session import get_db_session
from app.domains.strategy.schemas import (
    HypothesisCreate,
    HypothesisRead,
    SignalDefinitionCreate,
    SignalDefinitionRead,
    ScreenerCreate,
    ScreenerRead,
    ScreenerVersionCreate,
    ScreenerVersionRead,
    SetupCreate,
    SetupRead,
    StrategyCreate,
    StrategyRead,
    StrategyVersionCreate,
    StrategyVersionRead,
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistItemRead,
    WatchlistRead,
)
from app.domains.strategy.services import (
    HypothesisService,
    SignalDefinitionService,
    ScreenerService,
    SetupService,
    StrategyService,
    WatchlistService,
)

hypotheses_router = APIRouter()
signal_definitions_router = APIRouter()
setups_router = APIRouter()
strategies_router = APIRouter()
screeners_router = APIRouter()
watchlists_router = APIRouter()

hypothesis_service = HypothesisService()
signal_definition_service = SignalDefinitionService()
setup_service = SetupService()
strategy_service = StrategyService()
screener_service = ScreenerService()
watchlist_service = WatchlistService()


@hypotheses_router.get("", response_model=list[HypothesisRead])
async def list_hypotheses(session: Session = Depends(get_db_session)) -> list[HypothesisRead]:
    return hypothesis_service.list_hypotheses(session)


@hypotheses_router.post("", response_model=HypothesisRead, status_code=status.HTTP_201_CREATED)
async def create_hypothesis(payload: HypothesisCreate, session: Session = Depends(get_db_session)) -> HypothesisRead:
    try:
        return hypothesis_service.create_hypothesis(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@signal_definitions_router.get("", response_model=list[SignalDefinitionRead])
async def list_signal_definitions(session: Session = Depends(get_db_session)) -> list[SignalDefinitionRead]:
    return signal_definition_service.list_signal_definitions(session)


@signal_definitions_router.post("", response_model=SignalDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_signal_definition(
    payload: SignalDefinitionCreate,
    session: Session = Depends(get_db_session),
) -> SignalDefinitionRead:
    try:
        return signal_definition_service.create_signal_definition(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@setups_router.get("", response_model=list[SetupRead])
async def list_setups(session: Session = Depends(get_db_session)) -> list[SetupRead]:
    return setup_service.list_setups(session)


@setups_router.post("", response_model=SetupRead, status_code=status.HTTP_201_CREATED)
async def create_setup(payload: SetupCreate, session: Session = Depends(get_db_session)) -> SetupRead:
    try:
        return setup_service.create_setup(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@strategies_router.get("", response_model=list[StrategyRead])
async def list_strategies(session: Session = Depends(get_db_session)) -> list[StrategyRead]:
    return strategy_service.list_strategies(session)


@strategies_router.post("", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
async def create_strategy(payload: StrategyCreate, session: Session = Depends(get_db_session)) -> StrategyRead:
    try:
        return strategy_service.create_strategy(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@strategies_router.post("/{strategy_id}/versions", response_model=StrategyVersionRead, status_code=status.HTTP_201_CREATED)
async def create_strategy_version(
    strategy_id: int,
    payload: StrategyVersionCreate,
    session: Session = Depends(get_db_session),
) -> StrategyVersionRead:
    try:
        return strategy_service.create_version(session, strategy_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@screeners_router.get("", response_model=list[ScreenerRead])
async def list_screeners(session: Session = Depends(get_db_session)) -> list[ScreenerRead]:
    return screener_service.list_screeners(session)


@screeners_router.post("", response_model=ScreenerRead, status_code=status.HTTP_201_CREATED)
async def create_screener(payload: ScreenerCreate, session: Session = Depends(get_db_session)) -> ScreenerRead:
    try:
        return screener_service.create_screener(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@screeners_router.post("/{screener_id}/versions", response_model=ScreenerVersionRead, status_code=status.HTTP_201_CREATED)
async def create_screener_version(
    screener_id: int,
    payload: ScreenerVersionCreate,
    session: Session = Depends(get_db_session),
) -> ScreenerVersionRead:
    try:
        return screener_service.create_version(session, screener_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@watchlists_router.get("", response_model=list[WatchlistRead])
async def list_watchlists(session: Session = Depends(get_db_session)) -> list[WatchlistRead]:
    return watchlist_service.list_watchlists(session)


@watchlists_router.post("", response_model=WatchlistRead, status_code=status.HTTP_201_CREATED)
async def create_watchlist(payload: WatchlistCreate, session: Session = Depends(get_db_session)) -> WatchlistRead:
    try:
        return watchlist_service.create_watchlist(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@watchlists_router.post("/{watchlist_id}/items", response_model=WatchlistItemRead, status_code=status.HTTP_201_CREATED)
async def add_watchlist_item(
    watchlist_id: int,
    payload: WatchlistItemCreate,
    session: Session = Depends(get_db_session),
) -> WatchlistItemRead:
    try:
        return watchlist_service.add_item(session, watchlist_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
