from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.position import Position, PositionEvent
from app.db.models.trade_review import TradeReview
from app.domains.execution.schemas import (
    PositionCloseRequest,
    PositionCreate,
    PositionEventCreate,
    PositionManageRequest,
    TradeReviewCreate,
)


class PositionRepository:
    def list(self, session: Session) -> list[Position]:
        statement = select(Position).options(selectinload(Position.events)).order_by(Position.entry_date.desc())
        return list(session.scalars(statement).all())

    def get(self, session: Session, position_id: int) -> Position | None:
        statement = select(Position).options(selectinload(Position.events)).where(Position.id == position_id)
        return session.scalars(statement).first()

    def create(self, session: Session, payload: PositionCreate) -> Position:
        payload_data = payload.model_dump()
        opening_reason = payload_data.pop("opening_reason", None)
        position = Position(**payload_data)
        session.add(position)
        session.flush()
        session.add(
            PositionEvent(
                position_id=position.id,
                event_type="open",
                payload={
                    "entry_price": position.entry_price,
                    "stop_price": position.stop_price,
                    "target_price": position.target_price,
                    "size": position.size,
                    "thesis": position.thesis,
                    "entry_context": position.entry_context or {},
                    "opening_reason": opening_reason or position.thesis,
                },
                note="Position opened via API",
            )
        )
        session.commit()
        session.refresh(position)
        return self.get(session, position.id) or position

    def add_event(self, session: Session, position_id: int, payload: PositionEventCreate) -> PositionEvent:
        position = self.get(session, position_id)
        if position is None:
            raise ValueError("Position not found")

        event = PositionEvent(position_id=position_id, **payload.model_dump())
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def manage(self, session: Session, position_id: int, payload: PositionManageRequest) -> Position:
        position = self.get(session, position_id)
        if position is None:
            raise ValueError("Position not found")
        if position.status != "open":
            raise ValueError("Only open positions can be managed")

        previous_stop_price = position.stop_price
        previous_target_price = position.target_price
        previous_thesis = position.thesis

        if payload.stop_price is not None:
            position.stop_price = payload.stop_price
        if payload.target_price is not None:
            position.target_price = payload.target_price
        if payload.thesis is not None:
            position.thesis = payload.thesis

        event_payload = {
            "observed_price": payload.observed_price,
            "previous_stop_price": previous_stop_price,
            "new_stop_price": position.stop_price,
            "previous_target_price": previous_target_price,
            "new_target_price": position.target_price,
            "previous_thesis": previous_thesis,
            "new_thesis": position.thesis,
            "rationale": payload.rationale,
            "management_context": payload.management_context,
        }
        session.add(
            PositionEvent(
                position_id=position.id,
                event_type=payload.event_type,
                payload=event_payload,
                note=payload.note or "Position managed via API",
            )
        )
        session.commit()
        session.refresh(position)
        return self.get(session, position.id) or position

    def close(self, session: Session, position_id: int, payload: PositionCloseRequest) -> Position:
        position = self.get(session, position_id)
        if position is None:
            raise ValueError("Position not found")

        position.status = "closed"
        position.exit_date = datetime.now(timezone.utc)
        position.exit_price = payload.exit_price
        position.exit_reason = payload.exit_reason
        position.max_drawdown_pct = payload.max_drawdown_pct
        position.max_runup_pct = payload.max_runup_pct
        position.close_context = payload.close_context
        if position.side == "long":
            position.pnl_realized = (payload.exit_price - position.entry_price) * position.size
            position.pnl_pct = ((payload.exit_price - position.entry_price) / position.entry_price) * 100
        else:
            position.pnl_realized = (position.entry_price - payload.exit_price) * position.size
            position.pnl_pct = ((position.entry_price - payload.exit_price) / position.entry_price) * 100
        position.review_status = "pending"

        session.add(
            PositionEvent(
                position_id=position.id,
                event_type="close",
                payload={
                    "exit_price": payload.exit_price,
                    "exit_reason": payload.exit_reason,
                    "pnl_pct": position.pnl_pct,
                    "max_drawdown_pct": payload.max_drawdown_pct,
                    "max_runup_pct": payload.max_runup_pct,
                },
                note="Position closed via API",
            )
        )
        session.commit()
        session.refresh(position)
        return self.get(session, position.id) or position


class TradeReviewRepository:
    def create(self, session: Session, position_id: int, payload: TradeReviewCreate) -> TradeReview:
        position = session.get(Position, position_id)
        if position is None:
            raise ValueError("Position not found")

        normalized_outcome = payload.outcome or payload.outcome_label
        normalized_failure_mode = payload.failure_mode or payload.cause_category
        normalized_root_causes = payload.root_causes or [payload.root_cause]
        normalized_recommended_changes = payload.recommended_changes or (
            [payload.proposed_strategy_change] if payload.proposed_strategy_change else []
        )
        normalized_priority = payload.review_priority or ("high" if normalized_outcome.lower() == "loss" else "normal")
        normalized_needs_update = (
            payload.needs_strategy_update
            if payload.needs_strategy_update is not None
            else payload.should_modify_strategy
        )

        review = TradeReview(
            position_id=position_id,
            strategy_version_id=position.strategy_version_id,
            outcome_label=payload.outcome_label,
            outcome=normalized_outcome,
            cause_category=payload.cause_category,
            failure_mode=normalized_failure_mode,
            observations=payload.observations,
            root_cause=payload.root_cause,
            root_causes=normalized_root_causes,
            lesson_learned=payload.lesson_learned,
            proposed_strategy_change=payload.proposed_strategy_change,
            recommended_changes=normalized_recommended_changes,
            confidence=payload.confidence,
            review_priority=normalized_priority,
            should_modify_strategy=payload.should_modify_strategy,
            needs_strategy_update=normalized_needs_update,
            strategy_update_reason=payload.strategy_update_reason or payload.proposed_strategy_change,
        )
        session.add(review)
        position.review_status = "completed"
        session.commit()
        session.refresh(review)
        return review

    def list_for_position(self, session: Session, position_id: int) -> list[TradeReview]:
        statement = select(TradeReview).where(TradeReview.position_id == position_id).order_by(TradeReview.created_at.desc())
        return list(session.scalars(statement).all())
