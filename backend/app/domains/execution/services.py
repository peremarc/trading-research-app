from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.position import Position
from app.domains.learning.agent import AIDecisionError, AutonomousTradingAgentService
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
from app.domains.learning.services import JournalService, MemoryService
from app.domains.learning.tools import AgentToolGatewayService
from app.domains.market.services import MarketDataService
from app.domains.system.events import EventLogService
from app.domains.execution.repositories import PositionRepository, TradeReviewRepository
from app.domains.execution.schemas import (
    AutoExitBatchResult,
    AutoExitResult,
    PositionCloseRequest,
    PositionCreate,
    PositionEventCreate,
    PositionManageRequest,
    TradeReviewCreate,
)
from app.domains.strategy.services import StrategyEvolutionService


class PositionService:
    def __init__(
        self,
        repository: PositionRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or PositionRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_positions(self, session: Session):
        return self.repository.list(session)

    def create_position(self, session: Session, payload: PositionCreate):
        return self.create_position_with_source(session, payload, event_source="execution")

    def create_position_with_source(
        self,
        session: Session,
        payload: PositionCreate,
        *,
        event_source: str,
    ):
        position = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="position.opened",
            entity_type="position",
            entity_id=position.id,
            source=event_source,
            pdca_phase_hint="do",
            payload={
                "ticker": position.ticker,
                "signal_id": position.signal_id,
                "trade_signal_id": position.trade_signal_id,
                "strategy_version_id": position.strategy_version_id,
                "setup_id": position.setup_id,
                "signal_definition_id": position.signal_definition_id,
            },
        )
        return position

    def add_event(self, session: Session, position_id: int, payload: PositionEventCreate):
        return self.repository.add_event(session, position_id, payload)

    def manage_position(self, session: Session, position_id: int, payload: PositionManageRequest):
        return self.manage_position_with_source(session, position_id, payload, event_source="execution")

    def manage_position_with_source(
        self,
        session: Session,
        position_id: int,
        payload: PositionManageRequest,
        *,
        event_source: str,
    ):
        position = self.repository.manage(session, position_id, payload)
        self.event_log_service.record(
            session,
            event_type="position.managed",
            entity_type="position",
            entity_id=position.id,
            source=event_source,
            pdca_phase_hint="do",
            payload={
                "ticker": position.ticker,
                "event_type": payload.event_type,
                "stop_price": position.stop_price,
                "target_price": position.target_price,
            },
        )
        return position

    def close_position(self, session: Session, position_id: int, payload: PositionCloseRequest):
        return self.close_position_with_source(session, position_id, payload, event_source="execution")

    def close_position_with_source(
        self,
        session: Session,
        position_id: int,
        payload: PositionCloseRequest,
        *,
        event_source: str,
    ):
        position = self.repository.close(session, position_id, payload)
        self.event_log_service.record(
            session,
            event_type="position.closed",
            entity_type="position",
            entity_id=position.id,
            source=event_source,
            pdca_phase_hint="check",
            payload={
                "ticker": position.ticker,
                "exit_reason": position.exit_reason,
                "pnl_pct": position.pnl_pct,
            },
        )
        return position


class ExitManagementService:
    def __init__(
        self,
        market_data_service: MarketDataService | None = None,
        position_service: PositionService | None = None,
        trading_agent_service: AutonomousTradingAgentService | None = None,
        agent_tool_gateway_service: AgentToolGatewayService | None = None,
        execution_event_source: str = "orchestrator_do",
    ) -> None:
        self.market_data_service = market_data_service or MarketDataService()
        self.position_service = position_service or PositionService()
        self.trading_agent_service = trading_agent_service or AutonomousTradingAgentService()
        self.execution_event_source = execution_event_source
        self.agent_tool_gateway_service = agent_tool_gateway_service or AgentToolGatewayService(
            market_data_service=self.market_data_service,
            position_service=self.position_service,
            execution_event_source=execution_event_source,
        )

    def evaluate_open_positions(self, session: Session) -> AutoExitBatchResult:
        positions = list(session.scalars(select(Position).where(Position.status == "open")).all())
        return self._evaluate_positions(session, positions=positions, realtime_quote=None)

    def evaluate_positions_for_market_event(
        self,
        session: Session,
        *,
        ticker: str,
        realtime_quote: dict,
    ) -> AutoExitBatchResult:
        positions = list(
            session.scalars(
                select(Position).where(Position.status == "open", Position.ticker == ticker.upper())
            ).all()
        )
        return self._evaluate_positions(session, positions=positions, realtime_quote=realtime_quote)

    def _evaluate_positions(
        self,
        session: Session,
        *,
        positions: list[Position],
        realtime_quote: dict | None,
    ) -> AutoExitBatchResult:
        results: list[AutoExitResult] = []
        closed_positions = 0
        adjusted_positions = 0

        for position in positions:
            immediate_exit = self._should_exit_from_realtime_quote(position, realtime_quote)
            if immediate_exit["close"]:
                closed = self.position_service.close_position_with_source(
                    session,
                    position.id,
                    PositionCloseRequest(
                        exit_price=immediate_exit["exit_price"],
                        exit_reason=immediate_exit["exit_reason"],
                        max_drawdown_pct=immediate_exit["max_drawdown_pct"],
                        max_runup_pct=immediate_exit["max_runup_pct"],
                        close_context=self._build_close_context(realtime_quote, reason=immediate_exit["exit_reason"]),
                    ),
                    event_source=self.execution_event_source,
                )
                closed_positions += 1
                results.append(
                    AutoExitResult(
                        position_id=closed.id,
                        ticker=closed.ticker,
                        closed=True,
                        adjusted=False,
                        exit_price=closed.exit_price,
                        exit_reason=closed.exit_reason or immediate_exit["exit_reason"],
                        stop_price=closed.stop_price,
                        target_price=closed.target_price,
                    )
                )
                continue

            snapshot = self._get_effective_snapshot(position.ticker, realtime_quote=realtime_quote)
            exit_decision = self._should_exit(position, snapshot.price, snapshot.sma_20, snapshot.sma_50)
            if exit_decision["close"]:
                closed = self.position_service.close_position_with_source(
                    session,
                    position.id,
                    PositionCloseRequest(
                        exit_price=exit_decision["exit_price"],
                        exit_reason=exit_decision["exit_reason"],
                        max_drawdown_pct=exit_decision["max_drawdown_pct"],
                        max_runup_pct=exit_decision["max_runup_pct"],
                        close_context=self._build_close_context(realtime_quote, reason=exit_decision["exit_reason"]),
                    ),
                    event_source=self.execution_event_source,
                )
                closed_positions += 1
                results.append(
                    AutoExitResult(
                        position_id=closed.id,
                        ticker=closed.ticker,
                        closed=True,
                        adjusted=False,
                        exit_price=closed.exit_price,
                        exit_reason=closed.exit_reason or exit_decision["exit_reason"],
                        stop_price=closed.stop_price,
                        target_price=closed.target_price,
                    )
                )
                continue

            market_snapshot_payload = {
                "price": snapshot.price,
                "sma_20": snapshot.sma_20,
                "sma_50": snapshot.sma_50,
                "sma_200": snapshot.sma_200,
                "rsi_14": snapshot.rsi_14,
                "relative_volume": snapshot.relative_volume,
                "atr_14": snapshot.atr_14,
                "week_performance": snapshot.week_performance,
                "month_performance": snapshot.month_performance,
            }
            if realtime_quote is not None:
                market_snapshot_payload["monitor_event"] = realtime_quote

            agent_decision = None
            agent_plan = None
            agent_error: str | None = None
            try:
                agent_decision = self.trading_agent_service.advise_open_position_management(
                    session,
                    position=position,
                    market_snapshot=market_snapshot_payload,
                )
                agent_plan = self.trading_agent_service.plan_open_position_management_execution(
                    position=position,
                    market_snapshot=market_snapshot_payload,
                    decision=agent_decision,
                )
            except AIDecisionError as exc:
                agent_error = str(exc)
            if agent_plan is not None and agent_plan.should_execute and any(
                step.tool_name == "positions.close" for step in agent_plan.steps
            ):
                step_results = self.agent_tool_gateway_service.execute_plan(session, agent_plan)
                closed_result = next(step["result"] for step in step_results if step["tool_name"] == "positions.close")
                closed_positions += 1
                results.append(
                    AutoExitResult(
                        position_id=closed_result["id"],
                        ticker=closed_result["ticker"],
                        closed=True,
                        adjusted=False,
                        exit_price=closed_result["exit_price"],
                        exit_reason=closed_result["exit_reason"] or "ai_management_exit",
                        stop_price=closed_result["stop_price"],
                        target_price=closed_result["target_price"],
                    )
                )
                continue
            if agent_plan is not None and agent_plan.should_execute and any(
                step.tool_name == "positions.manage" for step in agent_plan.steps
            ):
                step_results = self.agent_tool_gateway_service.execute_plan(session, agent_plan)
                managed_result = next(step["result"] for step in step_results if step["tool_name"] == "positions.manage")
                adjusted_positions += 1
                results.append(
                    AutoExitResult(
                        position_id=managed_result["id"],
                        ticker=managed_result["ticker"],
                        closed=False,
                        adjusted=True,
                        exit_reason="position_risk_updated",
                        stop_price=managed_result["stop_price"],
                        target_price=managed_result["target_price"],
                    )
                )
                continue

            management_update = self._build_management_update(
                position,
                snapshot.price,
                snapshot.atr_14,
                snapshot.sma_20,
                agent_action=agent_decision.action if agent_decision is not None else None,
                agent_rationale=agent_decision.thesis if agent_decision is not None else None,
                agent_risks=agent_decision.risks if agent_decision is not None else None,
                ai_error=agent_error,
                realtime_quote=realtime_quote,
            )
            if management_update is not None:
                managed = self.position_service.manage_position_with_source(
                    session,
                    position.id,
                    PositionManageRequest(
                        event_type=management_update["event_type"],
                        observed_price=snapshot.price,
                        stop_price=management_update["stop_price"],
                        target_price=management_update["target_price"],
                        rationale=management_update["rationale"],
                        management_context=management_update["management_context"],
                        note=management_update["note"],
                    ),
                    event_source=self.execution_event_source,
                )
                adjusted_positions += 1
                results.append(
                    AutoExitResult(
                        position_id=managed.id,
                        ticker=managed.ticker,
                        closed=False,
                        adjusted=True,
                        exit_reason="position_risk_updated",
                        stop_price=managed.stop_price,
                        target_price=managed.target_price,
                    )
                )
                continue

            results.append(
                AutoExitResult(
                    position_id=position.id,
                    ticker=position.ticker,
                    closed=False,
                    adjusted=False,
                    exit_reason=exit_decision["exit_reason"],
                    stop_price=position.stop_price,
                    target_price=position.target_price,
                )
            )

        return AutoExitBatchResult(
            evaluated_positions=len(positions),
            closed_positions=closed_positions,
            adjusted_positions=adjusted_positions,
            results=results,
        )

    def _get_effective_snapshot(self, ticker: str, *, realtime_quote: dict | None):
        snapshot = self.market_data_service.get_snapshot(ticker)
        if realtime_quote is None:
            return snapshot
        realtime_price = self._extract_realtime_price(realtime_quote)
        if realtime_price is not None:
            snapshot.price = round(realtime_price, 2)
        return snapshot

    @classmethod
    def _extract_realtime_price(cls, realtime_quote: dict | None) -> float | None:
        if not isinstance(realtime_quote, dict):
            return None
        for candidate in (
            realtime_quote.get("last_price"),
            realtime_quote.get("bid_price"),
            realtime_quote.get("ask_price"),
        ):
            if candidate is None:
                continue
            try:
                return float(candidate)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _should_exit_from_realtime_quote(cls, position: Position, realtime_quote: dict | None) -> dict:
        market_price = cls._extract_realtime_price(realtime_quote)
        if market_price is None:
            return {
                "close": False,
                "exit_price": None,
                "exit_reason": "hold_position",
                "max_drawdown_pct": None,
                "max_runup_pct": None,
            }

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

        return {
            "close": False,
            "exit_price": None,
            "exit_reason": "hold_position",
            "max_drawdown_pct": max_drawdown_pct,
            "max_runup_pct": max_runup_pct,
        }

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

    @staticmethod
    def _build_close_context(realtime_quote: dict | None, *, reason: str) -> dict | None:
        if realtime_quote is None:
            return None
        return {
            "source": "realtime_monitor",
            "reason": reason,
            "monitor_event": realtime_quote,
        }

    @staticmethod
    def _build_management_update(
        position: Position,
        market_price: float,
        atr_14: float,
        sma_20: float,
        *,
        agent_action: str | None = None,
        agent_rationale: str | None = None,
        agent_risks: list[str] | None = None,
        ai_error: str | None = None,
        realtime_quote: dict | None = None,
    ) -> dict | None:
        if position.side != "long":
            return None

        pnl_pct = ((market_price - position.entry_price) / position.entry_price) * 100
        new_stop_price = position.stop_price
        new_target_price = position.target_price
        reasons: list[str] = []

        def add_reason(reason: str) -> None:
            if reason and reason not in reasons:
                reasons.append(reason)

        breakeven_stop = round(position.entry_price, 2)
        trailing_stop = round(max(breakeven_stop, market_price - atr_14), 2)

        ai_directed = agent_action is not None and agent_action != "hold"

        if agent_action in {"tighten_stop", "tighten_stop_and_extend_target"} and (
            position.stop_price is None or trailing_stop > position.stop_price
        ):
            new_stop_price = trailing_stop
            add_reason(agent_rationale or "ai requested tighter stop")
        elif not ai_directed and pnl_pct >= 2 and (position.stop_price is None or position.stop_price < breakeven_stop):
            new_stop_price = breakeven_stop
            add_reason("move stop to breakeven after initial favorable move")

        if not ai_directed and pnl_pct >= 4 and sma_20 >= position.entry_price:
            candidate_stop = round(max(sma_20, trailing_stop), 2)
            if new_stop_price is None or candidate_stop > new_stop_price:
                new_stop_price = candidate_stop
                add_reason("trail stop under strengthening trend support")

        if agent_action in {"extend_target", "tighten_stop_and_extend_target"} and position.target_price is not None:
            candidate_target = round(max(position.target_price, market_price + (2 * atr_14)), 2)
            if candidate_target > position.target_price:
                new_target_price = candidate_target
                add_reason(agent_rationale or "ai requested target extension")
        elif not ai_directed and position.target_price is not None and market_price >= position.target_price * 0.95:
            candidate_target = round(market_price + (2 * atr_14), 2)
            if candidate_target > position.target_price:
                new_target_price = candidate_target
                add_reason("extend target after near-target continuation")

        if new_stop_price == position.stop_price and new_target_price == position.target_price:
            return None

        event_type = "stream_risk_update" if realtime_quote is not None else "risk_update"
        if ai_error is not None:
            add_reason("AI unavailable; applied heuristic risk management")
        management_context = {
            "market_price": market_price,
            "atr_14": atr_14,
            "sma_20": sma_20,
            "pnl_pct": round(pnl_pct, 2),
            "ai_action": agent_action,
            "ai_risks": agent_risks or [],
            "ai_error": ai_error,
        }
        if realtime_quote is not None:
            management_context["monitor_event"] = realtime_quote

        note = "Autonomous risk update during realtime monitor" if realtime_quote is not None else "Autonomous risk update during exit evaluation"
        return {
            "event_type": event_type,
            "stop_price": new_stop_price,
            "target_price": new_target_price,
            "rationale": "; ".join(reasons),
            "management_context": management_context,
            "note": note,
        }


class TradeReviewService:
    def __init__(
        self,
        repository: TradeReviewRepository | None = None,
        journal_service: JournalService | None = None,
        memory_service: MemoryService | None = None,
        strategy_evolution_service: StrategyEvolutionService | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or TradeReviewRepository()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        self.strategy_evolution_service = strategy_evolution_service or StrategyEvolutionService()
        self.event_log_service = event_log_service or EventLogService()

    def create_review(self, session: Session, position_id: int, payload: TradeReviewCreate):
        review = self.repository.create(session, position_id, payload)
        position = session.get(Position, position_id)
        if position is None:
            raise ValueError("Position not found")

        self.event_log_service.record(
            session,
            event_type="trade_review.created",
            entity_type="trade_review",
            entity_id=review.id,
            source="execution_review",
            pdca_phase_hint="act",
            payload={
                "position_id": position.id,
                "ticker": position.ticker,
                "outcome_label": review.outcome_label,
                "cause_category": review.cause_category,
            },
        )

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
