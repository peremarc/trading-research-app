from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.exceptions import DuplicateResourceError, IntegrityConstraintError
from app.db.session import get_db_session
from app.domains.strategy.schemas import (
    ScreenerCreate,
    ScreenerRead,
    ScreenerVersionCreate,
    ScreenerVersionRead,
    StrategyCreate,
    StrategyRead,
    StrategyVersionCreate,
    StrategyVersionRead,
    WatchlistCreate,
    WatchlistItemCreate,
    WatchlistItemRead,
    WatchlistRead,
)
from app.domains.strategy.services import ScreenerService, StrategyService, WatchlistService

strategies_router = APIRouter()
screeners_router = APIRouter()
watchlists_router = APIRouter()

strategy_service = StrategyService()
screener_service = ScreenerService()
watchlist_service = WatchlistService()


@strategies_router.get("", response_model=list[StrategyRead])
def list_strategies(session: Session = Depends(get_db_session)) -> list[StrategyRead]:
    return strategy_service.list_strategies(session)


@strategies_router.post("", response_model=StrategyRead, status_code=status.HTTP_201_CREATED)
def create_strategy(payload: StrategyCreate, session: Session = Depends(get_db_session)) -> StrategyRead:
    try:
        return strategy_service.create_strategy(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@strategies_router.post("/{strategy_id}/versions", response_model=StrategyVersionRead, status_code=status.HTTP_201_CREATED)
def create_strategy_version(
    strategy_id: int,
    payload: StrategyVersionCreate,
    session: Session = Depends(get_db_session),
) -> StrategyVersionRead:
    try:
        return strategy_service.create_version(session, strategy_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@screeners_router.get("", response_model=list[ScreenerRead])
def list_screeners(session: Session = Depends(get_db_session)) -> list[ScreenerRead]:
    return screener_service.list_screeners(session)


@screeners_router.post("", response_model=ScreenerRead, status_code=status.HTTP_201_CREATED)
def create_screener(payload: ScreenerCreate, session: Session = Depends(get_db_session)) -> ScreenerRead:
    try:
        return screener_service.create_screener(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@screeners_router.post("/{screener_id}/versions", response_model=ScreenerVersionRead, status_code=status.HTTP_201_CREATED)
def create_screener_version(
    screener_id: int,
    payload: ScreenerVersionCreate,
    session: Session = Depends(get_db_session),
) -> ScreenerVersionRead:
    try:
        return screener_service.create_version(session, screener_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@watchlists_router.get("", response_model=list[WatchlistRead])
def list_watchlists(session: Session = Depends(get_db_session)) -> list[WatchlistRead]:
    return watchlist_service.list_watchlists(session)


@watchlists_router.post("", response_model=WatchlistRead, status_code=status.HTTP_201_CREATED)
def create_watchlist(payload: WatchlistCreate, session: Session = Depends(get_db_session)) -> WatchlistRead:
    try:
        return watchlist_service.create_watchlist(session, payload)
    except DuplicateResourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IntegrityConstraintError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@watchlists_router.post("/{watchlist_id}/items", response_model=WatchlistItemRead, status_code=status.HTTP_201_CREATED)
def add_watchlist_item(
    watchlist_id: int,
    payload: WatchlistItemCreate,
    session: Session = Depends(get_db_session),
) -> WatchlistItemRead:
    try:
        return watchlist_service.add_item(session, watchlist_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
