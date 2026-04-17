from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun
from app.db.models.position import Position
from app.db.models.strategy import Strategy, StrategyVersion
from app.db.models.trade_review import TradeReview
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.learning.repositories import FailurePatternRepository, JournalRepository, MemoryRepository, PDCACycleRepository
from app.domains.learning.schemas import (
    AutoReviewBatchResult,
    AutoReviewResult,
    DailyPlanRequest,
    JournalEntryCreate,
    MemoryItemCreate,
    OrchestratorActResponse,
    OrchestratorPhaseResponse,
    OrchestratorPlanResponse,
    PDCACycleCreate,
)
from app.schemas.analysis import AnalysisRunCreate
from app.schemas.execution import ExecutionCandidateResult, OrchestratorDoResponse
from app.schemas.exit_management import AutoExitBatchResult
from app.schemas.position import PositionCreate
from app.schemas.signal import SignalCreate
from app.schemas.trade_review import TradeReviewCreate


class JournalService:
    def __init__(self, repository: JournalRepository | None = None) -> None:
        self.repository = repository or JournalRepository()

    def list_entries(self, session: Session):
        return self.repository.list(session)

    def create_entry(self, session: Session, payload: JournalEntryCreate):
        return self.repository.create(session, payload)


class MemoryService:
    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

    def list_items(self, session: Session):
        return self.repository.list(session)

    def create_item(self, session: Session, payload: MemoryItemCreate):
        return self.repository.create(session, payload)

    def retrieve_scope(self, session: Session, scope: str, limit: int = 10):
        return self.repository.retrieve(session, scope=scope, limit=limit)


class FailureAnalysisService:
    def __init__(self, repository: FailurePatternRepository | None = None) -> None:
        self.repository = repository or FailurePatternRepository()

    def refresh_patterns(self, session: Session) -> list:
        reviews = list(
            session.scalars(
                select(TradeReview).where(
                    TradeReview.outcome_label == "loss",
                    TradeReview.strategy_version_id.is_not(None),
                )
            ).all()
        )
        results = []
        for review in reviews:
            strategy_version = session.get(StrategyVersion, review.strategy_version_id)
            if strategy_version is None:
                continue
            failure_mode = review.failure_mode or review.cause_category
            signature = f"{strategy_version.strategy_id}:{review.strategy_version_id}:{failure_mode}"
            pattern = self.repository.get_by_signature(
                session,
                strategy_id=strategy_version.strategy_id,
                strategy_version_id=review.strategy_version_id,
                pattern_signature=signature,
            )
            if pattern is None:
                pattern = self.repository.create(
                    session,
                    {
                        "strategy_id": strategy_version.strategy_id,
                        "strategy_version_id": review.strategy_version_id,
                        "failure_mode": failure_mode,
                        "pattern_signature": signature,
                        "occurrences": 1,
                        "avg_loss_pct": review.observations.get("pnl_pct"),
                        "evidence": {
                            "review_ids": [review.id],
                            "latest_root_cause": review.root_cause,
                        },
                        "recommended_action": review.strategy_update_reason or review.proposed_strategy_change,
                        "status": "open",
                    },
                )
            else:
                review_ids = list(pattern.evidence.get("review_ids", []))
                if review.id not in review_ids:
                    review_ids.append(review.id)
                    losses = [pattern.avg_loss_pct] if pattern.avg_loss_pct is not None else []
                    current_loss = review.observations.get("pnl_pct")
                    if current_loss is not None:
                        losses.append(current_loss)
                    pattern.occurrences = len(review_ids)
                    pattern.avg_loss_pct = round(sum(losses) / len(losses), 2) if losses else None
                    pattern.evidence = {
                        **pattern.evidence,
                        "review_ids": review_ids,
                        "latest_root_cause": review.root_cause,
                    }
                    pattern.recommended_action = review.strategy_update_reason or review.proposed_strategy_change
                    pattern = self.repository.update(session, pattern)
            results.append(pattern)
        return results

    def list_patterns(self, session: Session):
        return self.repository.list(session)

    def list_patterns_for_strategy(self, session: Session, strategy_id: int):
        return self.repository.list_for_strategy(session, strategy_id)


class AutoReviewService:
    def __init__(self, trade_review_service: object | None = None) -> None:
        if trade_review_service is None:
            from app.services.trade_review_service import TradeReviewService

            trade_review_service = TradeReviewService()
        self.trade_review_service = trade_review_service

    def generate_pending_loss_reviews(self, session: Session) -> AutoReviewBatchResult:
        positions = list(
            session.scalars(
                select(Position).where(
                    Position.status == "closed",
                    Position.review_status == "pending",
                    Position.pnl_pct.is_not(None),
                    Position.pnl_pct <= 0,
                )
            ).all()
        )

        generated_reviews = 0
        skipped_positions = 0
        results: list[AutoReviewResult] = []

        for position in positions:
            existing_review = session.scalar(select(TradeReview.id).where(TradeReview.position_id == position.id))
            if existing_review is not None:
                skipped_positions += 1
                results.append(
                    AutoReviewResult(
                        position_id=position.id,
                        generated=False,
                        review_id=existing_review,
                        reason="existing_review",
                    )
                )
                continue

            payload = self._build_review_payload(position)
            review = self.trade_review_service.create_review(session, position.id, payload)
            generated_reviews += 1
            results.append(
                AutoReviewResult(
                    position_id=position.id,
                    generated=True,
                    review_id=review.id,
                    reason="generated_from_loss_heuristic",
                )
            )

        return AutoReviewBatchResult(
            generated_reviews=generated_reviews,
            skipped_positions=skipped_positions,
            results=results,
        )

    @staticmethod
    def _build_review_payload(position: Position) -> TradeReviewCreate:
        cause_category = "setup_failure"
        root_cause = (
            "The trade closed negative without a completed review. Initial heuristic assumes the setup quality or timing was insufficient."
        )
        lesson = "Require stronger confirmation before entry and compare failed setup context against recent winning trades."
        proposed_change = (
            "Tighten entry filters for similar setups and review whether relative volume or trend alignment thresholds should be raised."
        )

        if position.max_drawdown_pct is not None and position.max_drawdown_pct <= -5:
            cause_category = "late_exit_or_weak_invalidation"
            root_cause = (
                "The trade experienced a meaningful drawdown before exit. Initial heuristic suggests invalidation rules were too loose or the exit came too late."
            )
            lesson = "Review invalidation timing and define clearer exit conditions when drawdown expands beyond acceptable behavior for the setup."
            proposed_change = "Reduce tolerance for adverse movement and formalize earlier invalidation on weak follow-through."
        elif position.exit_reason and "breakout" in position.exit_reason.lower():
            cause_category = "false_breakout"
            root_cause = "The exit reason points to a failed breakout dynamic. Initial heuristic suggests insufficient confirmation of continuation."
            lesson = "Demand cleaner breakout confirmation with volume and less extended entries."
            proposed_change = "Increase minimum breakout confirmation requirements before entry."

        return TradeReviewCreate(
            outcome_label="loss",
            outcome="loss",
            cause_category=cause_category,
            failure_mode=cause_category,
            observations={
                "entry_price": position.entry_price,
                "exit_price": position.exit_price,
                "pnl_pct": position.pnl_pct,
                "max_drawdown_pct": position.max_drawdown_pct,
                "max_runup_pct": position.max_runup_pct,
            },
            root_cause=root_cause,
            root_causes=[root_cause],
            lesson_learned=lesson,
            proposed_strategy_change=proposed_change,
            recommended_changes=[proposed_change],
            confidence=0.55,
            review_priority="high",
            should_modify_strategy=True,
            needs_strategy_update=True,
            strategy_update_reason=proposed_change,
        )


class PDCACycleService:
    def __init__(self, repository: PDCACycleRepository | None = None) -> None:
        self.repository = repository or PDCACycleRepository()

    def list_cycles(self, session: Session):
        return self.repository.list(session)

    def create_cycle(self, session: Session, payload: PDCACycleCreate):
        return self.repository.create(session, payload)

    def create_daily_plan(self, session: Session, cycle_date):
        payload = PDCACycleCreate(
            cycle_date=cycle_date,
            phase="plan",
            status="completed",
            summary="Daily PLAN cycle created by orchestrator bootstrap.",
            context={"focus": ["review_active_strategies", "refresh_screeners", "prepare_watchlists"]},
        )
        return self.repository.create(session, payload)


class OrchestratorService:
    def __init__(
        self,
        pdca_service: PDCACycleService | None = None,
        journal_service: JournalService | None = None,
        memory_service: MemoryService | None = None,
        analysis_service: object | None = None,
        market_data_service: object | None = None,
        signal_service: object | None = None,
        position_service: object | None = None,
        auto_review_service: AutoReviewService | None = None,
        strategy_lab_service: object | None = None,
        exit_management_service: object | None = None,
        strategy_scoring_service: object | None = None,
        research_service: object | None = None,
        failure_analysis_service: FailureAnalysisService | None = None,
        work_queue_service: object | None = None,
        strategy_evolution_service: object | None = None,
        opportunity_discovery_service: object | None = None,
    ) -> None:
        self.pdca_service = pdca_service or PDCACycleService()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        if analysis_service is None:
            from app.services.analysis_service import AnalysisService

            analysis_service = AnalysisService()
        if market_data_service is None:
            from app.services.market_data_service import MarketDataService

            market_data_service = MarketDataService()
        if signal_service is None:
            from app.services.signal_service import SignalService

            signal_service = SignalService()
        if position_service is None:
            from app.services.position_service import PositionService

            position_service = PositionService()
        if strategy_lab_service is None:
            from app.services.strategy_lab_service import StrategyLabService

            strategy_lab_service = StrategyLabService()
        if exit_management_service is None:
            from app.services.exit_management_service import ExitManagementService

            exit_management_service = ExitManagementService()
        if strategy_scoring_service is None:
            from app.services.strategy_scoring_service import StrategyScoringService

            strategy_scoring_service = StrategyScoringService()
        if research_service is None:
            from app.services.research_service import ResearchService

            research_service = ResearchService()
        self.auto_review_service = auto_review_service or AutoReviewService()
        self.analysis_service = analysis_service
        self.market_data_service = market_data_service
        self.signal_service = signal_service
        self.position_service = position_service
        self.strategy_lab_service = strategy_lab_service
        self.exit_management_service = exit_management_service
        self.strategy_scoring_service = strategy_scoring_service
        self.research_service = research_service
        if strategy_evolution_service is None:
            from app.services.strategy_evolution_service import StrategyEvolutionService

            strategy_evolution_service = StrategyEvolutionService(research_service=self.research_service)
        if opportunity_discovery_service is None:
            from app.services.opportunity_discovery_service import OpportunityDiscoveryService

            opportunity_discovery_service = OpportunityDiscoveryService(
                market_data_service=self.market_data_service,
                signal_service=self.signal_service,
            )
        self.strategy_evolution_service = strategy_evolution_service
        self.opportunity_discovery_service = opportunity_discovery_service
        self.failure_analysis_service = failure_analysis_service or FailureAnalysisService()
        if work_queue_service is None:
            from app.services.work_queue_service import WorkQueueService

            work_queue_service = WorkQueueService(failure_analysis_service=self.failure_analysis_service)
        self.work_queue_service = work_queue_service

    @staticmethod
    def _get_execution_version(strategy: Strategy | None) -> tuple[int | None, bool]:
        if strategy is None:
            return None, False

        if strategy.status == "degraded":
            candidate_versions = [version for version in strategy.versions if version.lifecycle_stage == "candidate"]
            if candidate_versions:
                candidate_versions.sort(key=lambda version: version.version, reverse=True)
                return candidate_versions[0].id, True

        return strategy.current_version_id, False

    def plan_daily_cycle(self, session: Session, payload: DailyPlanRequest) -> OrchestratorPlanResponse:
        cycle = self.pdca_service.create_daily_plan(session, payload.cycle_date)
        review_backlog = session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        open_research_tasks = len([task for task in self.research_service.list_tasks(session) if task.status in ["open", "in_progress"]])
        work_queue = self.work_queue_service.get_queue(session)
        degraded_candidate_backlog = len([item for item in work_queue.items if item.item_type == "degraded_candidate_validation"])
        cycle.context = {
            **cycle.context,
            **payload.market_context,
            "review_backlog": review_backlog,
            "open_research_tasks": open_research_tasks,
            "degraded_candidate_backlog": degraded_candidate_backlog,
        }
        session.commit()
        session.refresh(cycle)
        return OrchestratorPlanResponse(
            cycle_id=cycle.id,
            phase=cycle.phase,
            status=cycle.status,
            summary=cycle.summary or "",
            market_context=cycle.context,
            work_queue=work_queue,
        )

    def run_do_phase(self, session: Session) -> OrchestratorDoResponse:
        exit_result: AutoExitBatchResult = self.exit_management_service.evaluate_open_positions(session)
        discovery_result = self.opportunity_discovery_service.refresh_active_watchlists(session)
        active_watchlists = session.query(Watchlist).filter(Watchlist.status == "active").count()
        items = list(
            session.scalars(
                select(WatchlistItem)
                .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
                .where(Watchlist.status == "active", WatchlistItem.state.in_(["watching", "active"]))
            ).all()
        )
        items.sort(
            key=lambda item: (
                0
                if (
                    (watchlist := session.get(Watchlist, item.watchlist_id)) is not None
                    and watchlist.strategy_id is not None
                    and (strategy := session.get(Strategy, watchlist.strategy_id)) is not None
                    and strategy.status == "degraded"
                    and any(version.lifecycle_stage == "candidate" for version in strategy.versions)
                )
                else 1,
                item.id,
            )
        )
        candidates: list[ExecutionCandidateResult] = []
        generated_analyses = 0
        generated_signals = 0
        opened_positions = 0
        prioritized_candidate_items = 0

        for item in items:
            watchlist = session.get(Watchlist, item.watchlist_id)
            strategy = session.get(Strategy, watchlist.strategy_id) if watchlist and watchlist.strategy_id is not None else None
            strategy_version_id, using_candidate_version = self._get_execution_version(strategy)
            if using_candidate_version:
                prioritized_candidate_items += 1
            signal = self.signal_service.analyze_ticker(item.ticker)
            analysis = self.analysis_service.create_run(
                session,
                AnalysisRunCreate(
                    ticker=item.ticker,
                    strategy_version_id=strategy_version_id,
                    watchlist_item_id=item.id,
                    quant_summary=signal["quant_summary"],
                    visual_summary=signal["visual_summary"],
                    combined_score=signal["combined_score"],
                    entry_price=signal["entry_price"],
                    stop_price=signal["stop_price"],
                    target_price=signal["target_price"],
                    risk_reward=signal["risk_reward"],
                    decision=signal["decision"],
                    decision_confidence=signal["decision_confidence"],
                    rationale=signal["rationale"],
                ),
            )
            generated_analyses += 1
            signal_record = self.signal_service.create_signal(
                session,
                SignalCreate(
                    strategy_id=strategy.id if strategy is not None else None,
                    strategy_version_id=strategy_version_id,
                    watchlist_item_id=item.id,
                    ticker=item.ticker,
                    timeframe="1D",
                    signal_type="watchlist_analysis",
                    thesis=signal["rationale"],
                    entry_zone={"price": signal["entry_price"]},
                    stop_zone={"price": signal["stop_price"]},
                    target_zone={"price": signal["target_price"]},
                    signal_context={
                        "decision": signal["decision"],
                        "decision_confidence": signal["decision_confidence"],
                        "quant_summary": signal["quant_summary"],
                        "visual_summary": signal["visual_summary"],
                        "risk_reward": signal["risk_reward"],
                        "execution_mode": "candidate_validation" if using_candidate_version else "default",
                    },
                    quality_score=signal["combined_score"],
                    status="new",
                ),
            )
            generated_signals += 1

            existing_open = session.scalar(select(Position).where(Position.ticker == item.ticker, Position.status == "open"))
            position_id: int | None = None

            if signal["decision"] == "paper_enter" and existing_open is None:
                position = self.position_service.create_position(
                    session,
                    PositionCreate(
                        ticker=item.ticker,
                        signal_id=signal_record.id,
                        strategy_version_id=strategy_version_id,
                        analysis_run_id=analysis.id,
                        account_mode="paper",
                        side="long",
                        entry_price=signal["entry_price"],
                        stop_price=signal["stop_price"],
                        target_price=signal["target_price"],
                        size=1,
                        thesis=signal["rationale"],
                        entry_context={
                            "source": "orchestrator_do",
                            "watchlist_item_id": item.id,
                            "quant_summary": signal["quant_summary"],
                            "visual_summary": signal["visual_summary"],
                            "risk_reward": signal["risk_reward"],
                            "execution_mode": "candidate_validation" if using_candidate_version else "default",
                        },
                    ),
                )
                self.signal_service.update_status(session, signal_record.id, "executed")
                position_id = position.id
                opened_positions += 1
                item.state = "entered"
                journal_decision = "open_paper_position"
                journal_outcome = "executed"
            elif signal["decision"] == "discard":
                self.signal_service.update_status(session, signal_record.id, "rejected", "signal_below_threshold")
                item.state = "discarded"
                journal_decision = "discard_signal"
                journal_outcome = "rejected"
            else:
                if existing_open is not None and signal["decision"] == "paper_enter":
                    self.signal_service.update_status(session, signal_record.id, "rejected", "existing_open_position")
                    item.state = "entered"
                    journal_decision = "skip_existing_open_position"
                    journal_outcome = "rejected"
                else:
                    self.signal_service.update_status(session, signal_record.id, "new")
                    item.state = "watching"
                    journal_decision = "keep_on_watchlist"
                    journal_outcome = "watching"

            self.journal_service.create_entry(
                session,
                JournalEntryCreate(
                    entry_type="execution_decision",
                    ticker=item.ticker,
                    strategy_id=strategy.id if strategy is not None else None,
                    strategy_version_id=strategy_version_id,
                    position_id=position_id,
                    market_context={
                        "watchlist_id": watchlist.id if watchlist is not None else None,
                        "watchlist_code": watchlist.code if watchlist is not None else None,
                        "execution_mode": "candidate_validation" if using_candidate_version else "default",
                    },
                    hypothesis=watchlist.hypothesis if watchlist is not None else None,
                    observations={
                        "watchlist_item_id": item.id,
                        "signal_id": signal_record.id,
                        "score": signal["combined_score"],
                        "risk_reward": signal["risk_reward"],
                        "alpha_gap_pct": signal.get("alpha_gap_pct"),
                    },
                    reasoning=signal["rationale"],
                    decision=journal_decision,
                    outcome=journal_outcome,
                    lessons=(
                        f"Base strategy #{strategy.id} v{strategy_version_id}."
                        if strategy is not None and strategy_version_id is not None
                        else "Decision recorded without linked strategy."
                    ),
                ),
            )

            session.add(item)
            session.commit()

            candidates.append(
                ExecutionCandidateResult(
                    ticker=item.ticker,
                    watchlist_item_id=item.id,
                    analysis_run_id=analysis.id,
                    signal_id=signal_record.id,
                    decision=signal["decision"],
                    score=signal["combined_score"],
                    position_id=position_id,
                )
            )

        open_positions = session.query(Position).filter(Position.status == "open").count()
        metrics = {
            "active_watchlists": active_watchlists,
            "watchlist_items": len(items),
            "discovered_items": discovery_result["discovered_items"],
            "watchlists_scanned": discovery_result["watchlists_scanned"],
            "discovery_universe_size": discovery_result["universe_size"],
            "prioritized_candidate_items": prioritized_candidate_items,
            "generated_analyses": generated_analyses,
            "generated_signals": generated_signals,
            "opened_positions": opened_positions,
            "open_positions": open_positions,
            "auto_exit_evaluated": exit_result.evaluated_positions,
            "auto_exit_closed": exit_result.closed_positions,
        }
        summary = (
            f"DO phase processed {len(items)} watchlist items, generated {generated_analyses} analyses "
            f"opened {opened_positions} paper positions, discovered {discovery_result['discovered_items']} new "
            f"opportunities, prioritized {prioritized_candidate_items} candidate-validation items and "
            f"auto-closed {exit_result.closed_positions} positions."
        )
        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_do",
                hypothesis=(
                    "Continuously expand the opportunity set, pursue alpha above the benchmark, and keep drawdown "
                    "contained through risk-aware entries."
                ),
                market_context={
                    "benchmark_ticker": discovery_result["benchmark_ticker"],
                    "top_discovery_candidates": discovery_result["top_candidates"],
                },
                observations=metrics,
                reasoning=summary,
                decision="continue_execution_loop",
            ),
        )
        return OrchestratorDoResponse(
            phase="do",
            status="completed",
            summary=summary,
            metrics=metrics,
            generated_analyses=generated_analyses,
            opened_positions=opened_positions,
            candidates=candidates,
        )

    def run_check_phase(self, session: Session) -> OrchestratorPhaseResponse:
        auto_review_result = self.auto_review_service.generate_pending_loss_reviews(session)
        failure_patterns = self.failure_analysis_service.refresh_patterns(session)
        scorecards = self.strategy_scoring_service.recalculate_all(session)
        benchmark_snapshot = self.market_data_service.get_snapshot("SPY")
        benchmark_return_pct = round(benchmark_snapshot.month_performance * 100, 2)
        research_tasks_opened = 0
        for scorecard in scorecards:
            strategy = session.get(Strategy, scorecard.strategy_id)
            if strategy is None:
                continue
            if scorecard.signals_count < 2 and scorecard.closed_trades_count == 0:
                _, created = self.research_service.ensure_low_activity_task(
                    session,
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    signals_count=scorecard.signals_count,
                    closed_trades_count=scorecard.closed_trades_count,
                )
                if created:
                    research_tasks_opened += 1
            if (
                scorecard.closed_trades_count >= 1
                and (
                    (scorecard.avg_return_pct is not None and scorecard.avg_return_pct < benchmark_return_pct)
                    or (scorecard.max_drawdown_pct is not None and scorecard.max_drawdown_pct <= -5)
                )
            ):
                _, created = self.research_service.ensure_alpha_improvement_task(
                    session,
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    avg_return_pct=scorecard.avg_return_pct,
                    benchmark_return_pct=benchmark_return_pct,
                    max_drawdown_pct=scorecard.max_drawdown_pct,
                )
                if created:
                    research_tasks_opened += 1
        active_strategies = session.query(Strategy).filter(Strategy.status.in_(["paper", "live", "research"])).count()
        total_analyses = session.query(AnalysisRun).count()
        closed_positions = session.query(Position).filter(Position.status == "closed").count()
        open_positions = session.query(Position).filter(Position.status == "open").count()
        winning_positions = session.query(Position).filter(Position.status == "closed", Position.pnl_pct > 0).count()
        losing_positions = session.query(Position).filter(Position.status == "closed", Position.pnl_pct <= 0).count()
        pending_reviews = session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()

        closed_rows = session.query(Position.pnl_pct, Position.max_drawdown_pct).filter(Position.status == "closed").all()
        avg_pnl_pct = round(sum((row[0] or 0.0) for row in closed_rows) / len(closed_rows), 2) if closed_rows else 0.0
        avg_drawdown_pct = round(sum((row[1] or 0.0) for row in closed_rows) / len(closed_rows), 2) if closed_rows else 0.0

        metrics = {
            "active_strategies": active_strategies,
            "total_analyses": total_analyses,
            "closed_positions": closed_positions,
            "open_positions": open_positions,
            "winning_positions": winning_positions,
            "losing_positions": losing_positions,
            "pending_reviews": pending_reviews,
            "avg_pnl_pct": avg_pnl_pct,
            "avg_drawdown_pct": avg_drawdown_pct,
            "benchmark_return_pct": benchmark_return_pct,
            "portfolio_alpha_gap_pct": round(avg_pnl_pct - benchmark_return_pct, 2),
            "auto_generated_reviews": auto_review_result.generated_reviews,
            "failure_patterns_tracked": len(failure_patterns),
            "scorecards_generated": len(scorecards),
            "research_tasks_opened": research_tasks_opened,
        }
        summary = (
            f"CHECK phase evaluated {closed_positions} closed trades with {winning_positions} wins, "
            f"{losing_positions} losses, {pending_reviews} pending reviews and "
            f"{auto_review_result.generated_reviews} auto-generated reviews."
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="episodic",
                scope="pdca_check",
                key="latest_check_summary",
                content=summary,
                meta=metrics,
                importance=0.7,
            ),
        )
        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_check",
                hypothesis=(
                    "The system should outperform the benchmark while containing drawdown and convert repeated "
                    "outcomes into reusable lessons."
                ),
                market_context={"benchmark_ticker": "SPY"},
                observations=metrics,
                reasoning=summary,
                decision="review_outcomes",
                lessons=(
                    "Prioritize strategy changes that improve alpha relative to the benchmark without paying for it "
                    "through excessive drawdown."
                ),
            ),
        )
        return OrchestratorPhaseResponse(phase="check", status="completed", summary=summary, metrics=metrics)

    def run_act_phase(self, session: Session) -> OrchestratorActResponse:
        health_result = self.strategy_evolution_service.evaluate_failure_patterns(session)
        candidate_result = self.strategy_evolution_service.evaluate_candidate_versions(session)
        candidate_research_tasks_opened = 0
        repeated_candidate_rejections = self.strategy_evolution_service.find_repeated_candidate_rejections(session)
        for repeated_rejection in repeated_candidate_rejections:
            strategy = session.get(Strategy, repeated_rejection["strategy_id"])
            if strategy is None:
                continue
            _, created = self.research_service.ensure_candidate_research_task(
                session,
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                rejected_candidate_count=repeated_rejection["rejected_candidate_count"],
                candidate_version_ids=repeated_rejection["candidate_version_ids"],
            )
            if created:
                candidate_research_tasks_opened += 1
        promoted_strategy_ids = {item["strategy_id"] for item in candidate_result.get("promotions", [])}
        lab_result = self.strategy_lab_service.evolve_from_success_patterns(
            session,
            excluded_strategy_ids=promoted_strategy_ids,
        )
        open_research_tasks = len([task for task in self.research_service.list_tasks(session) if task.status in ["open", "in_progress"]])
        metrics = {
            "forked_variants": health_result["forked_variants"],
            "promoted_candidates": candidate_result["promoted_candidates"],
            "rejected_candidates": candidate_result["rejected_candidates"],
            "degraded_strategies": health_result["degraded_strategies"],
            "archived_strategies": health_result["archived_strategies"],
            "candidate_research_tasks_opened": candidate_research_tasks_opened,
            "generated_variants": lab_result["generated_variants"],
            "skipped_candidates": lab_result["skipped_candidates"],
            "open_research_tasks": open_research_tasks,
        }
        summary = (
            f"ACT phase forked {health_result['forked_variants']} candidate variants, promoted "
            f"{candidate_result['promoted_candidates']} candidates, rejected {candidate_result['rejected_candidates']} "
            f"candidates, opened {candidate_research_tasks_opened} candidate-research tasks, "
            f"degraded {health_result['degraded_strategies']} strategies, archived {health_result['archived_strategies']}, "
            f"generated {lab_result['generated_variants']} proactive strategy variants and skipped "
            f"{lab_result['skipped_candidates']} candidates."
        )
        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_act",
                observations=metrics,
                reasoning=summary,
                decision="promote_success_patterns",
                lessons="Use failure-pattern feedback to fork weaker strategies and promote candidates that improve alpha and resilience.",
            ),
        )
        return OrchestratorActResponse(
            phase="act",
            status="completed",
            summary=summary,
            metrics=metrics,
            generated_variants=lab_result["generated_variants"],
        )
