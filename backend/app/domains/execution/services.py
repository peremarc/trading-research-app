from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.position import Position
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
from app.domains.learning.services import JournalService, MemoryService
from app.domains.market.services import MarketDataService
from app.domains.execution.repositories import PositionRepository, TradeReviewRepository
from app.domains.execution.schemas import (
    AutoExitBatchResult,
    AutoExitResult,
    PositionCloseRequest,
    PositionCreate,
    PositionEventCreate,
    TradeReviewCreate,
)
from app.domains.strategy.services import StrategyEvolutionService


class PositionService:
    def __init__(self, repository: PositionRepository | None = None) -> None:
        self.repository = repository or PositionRepository()

    def list_positions(self, session: Session):
        return self.repository.list(session)

    def create_position(self, session: Session, payload: PositionCreate):
        return self.repository.create(session, payload)

    def add_event(self, session: Session, position_id: int, payload: PositionEventCreate):
        return self.repository.add_event(session, position_id, payload)

    def close_position(self, session: Session, position_id: int, payload: PositionCloseRequest):
        return self.repository.close(session, position_id, payload)


class ExitManagementService:
    def __init__(
        self,
        market_data_service: MarketDataService | None = None,
        position_service: PositionService | None = None,
    ) -> None:
        self.market_data_service = market_data_service or MarketDataService()
        self.position_service = position_service or PositionService()

    def evaluate_open_positions(self, session: Session) -> AutoExitBatchResult:
        positions = list(session.scalars(select(Position).where(Position.status == "open")).all())
        results: list[AutoExitResult] = []
        closed_positions = 0

        for position in positions:
            snapshot = self.market_data_service.get_snapshot(position.ticker)
            exit_decision = self._should_exit(position, snapshot.price, snapshot.sma_20, snapshot.sma_50)
            if exit_decision["close"]:
                closed = self.position_service.close_position(
                    session,
                    position.id,
                    PositionCloseRequest(
                        exit_price=exit_decision["exit_price"],
                        exit_reason=exit_decision["exit_reason"],
                        max_drawdown_pct=exit_decision["max_drawdown_pct"],
                        max_runup_pct=exit_decision["max_runup_pct"],
                    ),
                )
                closed_positions += 1
                results.append(
                    AutoExitResult(
                        position_id=closed.id,
                        ticker=closed.ticker,
                        closed=True,
                        exit_price=closed.exit_price,
                        exit_reason=closed.exit_reason or exit_decision["exit_reason"],
                    )
                )
            else:
                results.append(
                    AutoExitResult(
                        position_id=position.id,
                        ticker=position.ticker,
                        closed=False,
                        exit_reason=exit_decision["exit_reason"],
                    )
                )

        return AutoExitBatchResult(
            evaluated_positions=len(positions),
            closed_positions=closed_positions,
            results=results,
        )

    @staticmethod
    def _should_exit(position: Position, market_price: float, sma_20: float, sma_50: float) -> dict:
        pnl_pct = ((market_price - position.entry_price) / position.entry_price) * 100 if position.side == "long" else (
            (position.entry_price - market_price) / position.entry_price
        ) * 100
        max_drawdown_pct = round(min(pnl_pct, 0.0), 2)
        max_runup_pct = round(max(pnl_pct, 0.0), 2)

        if position.stop_price is not None:
            if position.side == "long" and market_price <= position.stop_price:
                return {
                    "close": True,
                    "exit_price": market_price,
                    "exit_reason": "stop_loss_hit",
                    "max_drawdown_pct": max_drawdown_pct,
                    "max_runup_pct": max_runup_pct,
                }
            if position.side == "short" and market_price >= position.stop_price:
                return {
                    "close": True,
                    "exit_price": market_price,
                    "exit_reason": "stop_loss_hit",
                    "max_drawdown_pct": max_drawdown_pct,
                    "max_runup_pct": max_runup_pct,
                }

        if position.target_price is not None:
            if position.side == "long" and market_price >= position.target_price:
                return {
                    "close": True,
                    "exit_price": market_price,
                    "exit_reason": "target_hit",
                    "max_drawdown_pct": max_drawdown_pct,
                    "max_runup_pct": max_runup_pct,
                }
            if position.side == "short" and market_price <= position.target_price:
                return {
                    "close": True,
                    "exit_price": market_price,
                    "exit_reason": "target_hit",
                    "max_drawdown_pct": max_drawdown_pct,
                    "max_runup_pct": max_runup_pct,
                }

        if position.side == "long" and market_price < sma_20 and sma_20 < sma_50:
            return {
                "close": True,
                "exit_price": market_price,
                "exit_reason": "trend_deterioration",
                "max_drawdown_pct": max_drawdown_pct,
                "max_runup_pct": max_runup_pct,
            }

        return {
            "close": False,
            "exit_price": None,
            "exit_reason": "hold_position",
            "max_drawdown_pct": max_drawdown_pct,
            "max_runup_pct": max_runup_pct,
        }


class TradeReviewService:
    def __init__(
        self,
        repository: TradeReviewRepository | None = None,
        journal_service: JournalService | None = None,
        memory_service: MemoryService | None = None,
        strategy_evolution_service: StrategyEvolutionService | None = None,
    ) -> None:
        self.repository = repository or TradeReviewRepository()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        self.strategy_evolution_service = strategy_evolution_service or StrategyEvolutionService()

    def create_review(self, session: Session, position_id: int, payload: TradeReviewCreate):
        review = self.repository.create(session, position_id, payload)
        position = session.get(Position, position_id)
        if position is None:
            raise ValueError("Position not found")

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="post_trade_review",
                ticker=position.ticker,
                strategy_version_id=position.strategy_version_id,
                position_id=position.id,
                observations={
                    "outcome_label": review.outcome_label,
                    "outcome": review.outcome,
                    "cause_category": review.cause_category,
                    "failure_mode": review.failure_mode,
                    "pnl_pct": position.pnl_pct,
                    "max_drawdown_pct": position.max_drawdown_pct,
                },
                reasoning=review.root_cause,
                outcome=position.exit_reason,
                lessons=review.lesson_learned,
            ),
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="lesson",
                scope=f"strategy:{position.strategy_version_id or 'unknown'}",
                key=f"trade_review:{position.id}:{review.id}",
                content=review.lesson_learned,
                meta={
                    "ticker": position.ticker,
                    "cause_category": review.cause_category,
                    "failure_mode": review.failure_mode,
                    "pnl_pct": position.pnl_pct,
                    "should_modify_strategy": review.should_modify_strategy,
                    "proposed_strategy_change": review.proposed_strategy_change,
                    "recommended_changes": review.recommended_changes,
                },
                importance=0.8 if review.outcome_label.lower() == "loss" else 0.6,
            ),
        )
        if review.needs_strategy_update and review.strategy_version_id is not None:
            self.strategy_evolution_service.evolve_from_trade_review(session, review)
        return review

    def list_for_position(self, session: Session, position_id: int):
        return self.repository.list_for_position(session, position_id)
