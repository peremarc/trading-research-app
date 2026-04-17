from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.domains.execution.schemas import (
    AutoExitBatchResult,
    PositionCloseRequest,
    PositionCreate,
    PositionEventCreate,
    PositionEventRead,
    PositionRead,
    TradeReviewCreate,
    TradeReviewRead,
)
from app.domains.execution.services import ExitManagementService, PositionService, TradeReviewService

positions_router = APIRouter()
exits_router = APIRouter()
trade_reviews_router = APIRouter()

position_service = PositionService()
exit_management_service = ExitManagementService()
trade_review_service = TradeReviewService()


@positions_router.get("", response_model=list[PositionRead])
async def list_positions(session: Session = Depends(get_db_session)) -> list[PositionRead]:
    return position_service.list_positions(session)


@positions_router.post("", response_model=PositionRead, status_code=status.HTTP_201_CREATED)
async def create_position(payload: PositionCreate, session: Session = Depends(get_db_session)) -> PositionRead:
    return position_service.create_position(session, payload)


@positions_router.post("/{position_id}/events", response_model=PositionEventRead, status_code=status.HTTP_201_CREATED)
async def add_position_event(
    position_id: int,
    payload: PositionEventCreate,
    session: Session = Depends(get_db_session),
) -> PositionEventRead:
    try:
        return position_service.add_event(session, position_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@positions_router.post("/{position_id}/close", response_model=PositionRead, status_code=status.HTTP_200_OK)
async def close_position(
    position_id: int,
    payload: PositionCloseRequest,
    session: Session = Depends(get_db_session),
) -> PositionRead:
    try:
        return position_service.close_position(session, position_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@exits_router.post("/evaluate", response_model=AutoExitBatchResult, status_code=status.HTTP_200_OK)
async def evaluate_auto_exits(session: Session = Depends(get_db_session)) -> AutoExitBatchResult:
    return exit_management_service.evaluate_open_positions(session)


@trade_reviews_router.get("/positions/{position_id}", response_model=list[TradeReviewRead])
async def list_trade_reviews(position_id: int, session: Session = Depends(get_db_session)) -> list[TradeReviewRead]:
    return trade_review_service.list_for_position(session, position_id)


@trade_reviews_router.post("/positions/{position_id}", response_model=TradeReviewRead, status_code=status.HTTP_201_CREATED)
async def create_trade_review(
    position_id: int,
    payload: TradeReviewCreate,
    session: Session = Depends(get_db_session),
) -> TradeReviewRead:
    try:
        return trade_review_service.create_review(session, position_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
