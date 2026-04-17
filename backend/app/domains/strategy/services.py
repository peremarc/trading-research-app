from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.candidate_validation_snapshot import CandidateValidationSnapshot
from app.db.models.failure_pattern import FailurePattern
from app.db.models.position import Position
from app.db.models.signal import Signal
from app.db.models.strategy import Strategy, StrategyVersion
from app.db.models.strategy_scorecard import StrategyScorecard
from app.db.models.trade_review import TradeReview
from app.db.repositories.candidate_validation_snapshot_repository import CandidateValidationSnapshotRepository
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
from app.domains.learning.services import JournalService, MemoryService
from app.domains.market.services import MarketDataService, ResearchService
from app.domains.strategy.repositories import (
    ScreenerRepository,
    StrategyEvolutionRepository,
    StrategyRepository,
    StrategyScorecardRepository,
    WatchlistRepository,
)
from app.domains.strategy.schemas import (
    CandidateValidationSnapshotRead,
    CandidateValidationSummaryRead,
    ScreenerCreate,
    ScreenerVersionCreate,
    StrategyLabBatchResult,
    StrategyPipelineRead,
    StrategyScorecardRead,
    StrategyCreate,
    StrategyLabResult,
    StrategyVersionCreate,
    StrategyVersionRead,
    WatchlistCreate,
    WatchlistItemCreate,
)


class StrategyService:
    def __init__(self, repository: StrategyRepository | None = None) -> None:
        self.repository = repository or StrategyRepository()

    def list_strategies(self, session: Session):
        return self.repository.list(session)

    def create_strategy(self, session: Session, payload: StrategyCreate):
        return self.repository.create(session, payload)

    def create_version(self, session: Session, strategy_id: int, payload: StrategyVersionCreate):
        return self.repository.create_version(session, strategy_id, payload)


class ScreenerService:
    def __init__(self, repository: ScreenerRepository | None = None) -> None:
        self.repository = repository or ScreenerRepository()

    def list_screeners(self, session: Session):
        return self.repository.list(session)

    def create_screener(self, session: Session, payload: ScreenerCreate):
        return self.repository.create(session, payload)

    def create_version(self, session: Session, screener_id: int, payload: ScreenerVersionCreate):
        return self.repository.create_version(session, screener_id, payload)


class WatchlistService:
    def __init__(self, repository: WatchlistRepository | None = None) -> None:
        self.repository = repository or WatchlistRepository()

    def list_watchlists(self, session: Session):
        return self.repository.list(session)

    def create_watchlist(self, session: Session, payload: WatchlistCreate):
        return self.repository.create(session, payload)

    def add_item(self, session: Session, watchlist_id: int, payload: WatchlistItemCreate):
        return self.repository.add_item(session, watchlist_id, payload)


class StrategyScoringService:
    def __init__(
        self,
        repository: StrategyScorecardRepository | None = None,
        candidate_validation_repository: CandidateValidationSnapshotRepository | None = None,
        market_data_service: MarketDataService | None = None,
    ) -> None:
        self.repository = repository or StrategyScorecardRepository()
        self.candidate_validation_repository = (
            candidate_validation_repository or CandidateValidationSnapshotRepository()
        )
        self.market_data_service = market_data_service or MarketDataService()

    @staticmethod
    def _candidate_validation_to_read(
        session: Session,
        snapshot: CandidateValidationSnapshot,
    ) -> CandidateValidationSnapshotRead:
        version = session.get(StrategyVersion, snapshot.strategy_version_id)
        return CandidateValidationSnapshotRead(
            id=snapshot.id,
            strategy_id=snapshot.strategy_id,
            candidate_version_id=snapshot.strategy_version_id,
            candidate_version_number=version.version if version is not None else 0,
            trade_count=snapshot.trade_count,
            wins=snapshot.wins,
            losses=snapshot.losses,
            avg_pnl_pct=snapshot.avg_pnl_pct,
            avg_drawdown_pct=snapshot.avg_drawdown_pct,
            win_rate=snapshot.win_rate,
            evaluation_status=snapshot.evaluation_status,
            generated_at=snapshot.generated_at,
        )

    def recalculate_all(self, session: Session) -> list:
        strategies = list(session.scalars(select(Strategy)).all())
        return [self.recalculate_for_strategy(session, strategy.id) for strategy in strategies]

    def recalculate_for_strategy(self, session: Session, strategy_id: int):
        strategy = session.get(Strategy, strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")
        benchmark_snapshot = self.market_data_service.get_snapshot("SPY")
        benchmark_return_pct = round(benchmark_snapshot.month_performance * 100, 2)

        signals = list(session.scalars(select(Signal).where(Signal.strategy_id == strategy_id)).all())
        positions = list(
            session.scalars(
                select(Position)
                .join(StrategyVersion, Position.strategy_version_id == StrategyVersion.id)
                .where(StrategyVersion.strategy_id == strategy_id)
            ).all()
        )

        executed_positions = [position for position in positions]
        closed_positions = [position for position in positions if position.status == "closed"]
        winning_positions = [position for position in closed_positions if (position.pnl_pct or 0.0) > 0]
        losing_positions = [position for position in closed_positions if (position.pnl_pct or 0.0) <= 0]

        signal_count = len(signals)
        executed_trades_count = len(executed_positions)
        closed_trades_count = len(closed_positions)
        wins_count = len(winning_positions)
        losses_count = len(losing_positions)
        win_rate = round((wins_count / closed_trades_count) * 100, 2) if closed_trades_count else None

        avg_return_pct = round(
            sum((position.pnl_pct or 0.0) for position in closed_positions) / closed_trades_count,
            2,
        ) if closed_trades_count else None
        expectancy = avg_return_pct

        gross_profit = sum(max(position.pnl_pct or 0.0, 0.0) for position in closed_positions)
        gross_loss = abs(sum(min(position.pnl_pct or 0.0, 0.0) for position in closed_positions))
        profit_factor = (
            round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else None)
        )

        holding_days = [
            (position.exit_date - position.entry_date).total_seconds() / 86400
            for position in closed_positions
            if position.exit_date is not None and position.entry_date is not None
        ]
        avg_holding_days = round(sum(holding_days) / len(holding_days), 2) if holding_days else None

        drawdowns = [position.max_drawdown_pct for position in closed_positions if position.max_drawdown_pct is not None]
        max_drawdown_pct = round(min(drawdowns), 2) if drawdowns else None

        activity_score = round(min(1.0, (signal_count / 5) * 0.6 + (executed_trades_count / 3) * 0.4), 2)
        alpha_gap_pct = (avg_return_pct or 0.0) - benchmark_return_pct
        alpha_score = min(max((alpha_gap_pct + 5.0) / 10.0, 0.0), 1.0) if (avg_return_pct or 0.0) > 0 else 0.0
        drawdown_score = 1.0 - min(abs(min(max_drawdown_pct or 0.0, 0.0)) / 12.0, 1.0)
        win_component = (win_rate or 0.0) / 100
        expectancy_component = min(max(((avg_return_pct or 0.0) + 5) / 10, 0.0), 1.0)
        profit_factor_component = min(max((((profit_factor or 1.0) - 1.0) / 1.5), 0.0), 1.0)
        quality_score = round(
            min(
                1.0,
                max(
                    0.0,
                    (alpha_score * 0.35)
                    + (drawdown_score * 0.25)
                    + (win_component * 0.2)
                    + (expectancy_component * 0.1)
                    + (profit_factor_component * 0.1),
                ),
            ),
            2,
        )
        fitness_score = round((activity_score * 0.25) + (quality_score * 0.75), 2)

        return self.repository.create(
            session,
            {
                "strategy_id": strategy.id,
                "strategy_version_id": strategy.current_version_id,
                "period_start": None,
                "period_end": datetime.now(timezone.utc).date(),
                "signals_count": signal_count,
                "executed_trades_count": executed_trades_count,
                "closed_trades_count": closed_trades_count,
                "wins_count": wins_count,
                "losses_count": losses_count,
                "win_rate": win_rate,
                "avg_return_pct": avg_return_pct,
                "expectancy": expectancy,
                "profit_factor": profit_factor,
                "avg_holding_days": avg_holding_days,
                "max_drawdown_pct": max_drawdown_pct,
                "activity_score": activity_score,
                "quality_score": quality_score,
                "fitness_score": fitness_score,
            },
        )

    def list_latest(self, session: Session):
        return self.repository.list_latest(session)

    def get_latest(self, session: Session, strategy_id: int):
        return self.repository.get_latest_for_strategy(session, strategy_id)

    def get_pipeline(self, session: Session, strategy_id: int) -> StrategyPipelineRead:
        strategy = session.get(Strategy, strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")

        refreshed = session.scalars(select(Strategy).where(Strategy.id == strategy_id)).first() or strategy
        versions = list(refreshed.versions)
        versions.sort(key=lambda version: version.version, reverse=True)

        active_version = next((version for version in versions if version.lifecycle_stage == "active"), None)
        candidate_versions = [version for version in versions if version.lifecycle_stage == "candidate"]
        degraded_versions = [version for version in versions if version.lifecycle_stage == "degraded"]
        approved_versions = [version for version in versions if version.lifecycle_stage == "approved"]
        archived_versions = [version for version in versions if version.lifecycle_stage == "archived"]
        latest_scorecard = self.get_latest(session, strategy_id)
        latest_candidate_validations = self.candidate_validation_repository.list_latest_for_strategy(session, strategy_id)

        return StrategyPipelineRead(
            strategy_id=refreshed.id,
            strategy_code=refreshed.code,
            strategy_name=refreshed.name,
            strategy_status=refreshed.status,
            active_version=StrategyVersionRead.model_validate(active_version) if active_version is not None else None,
            candidate_versions=[StrategyVersionRead.model_validate(version) for version in candidate_versions],
            degraded_versions=[StrategyVersionRead.model_validate(version) for version in degraded_versions],
            approved_versions=[StrategyVersionRead.model_validate(version) for version in approved_versions],
            archived_versions=[StrategyVersionRead.model_validate(version) for version in archived_versions],
            total_versions=len(versions),
            latest_scorecard=StrategyScorecardRead.model_validate(latest_scorecard) if latest_scorecard is not None else None,
            latest_candidate_validations=[
                self._candidate_validation_to_read(session, snapshot)
                for snapshot in latest_candidate_validations
            ],
        )

    def list_pipelines(self, session: Session) -> list[StrategyPipelineRead]:
        strategies = list(session.scalars(select(Strategy)).all())
        return [self.get_pipeline(session, strategy.id) for strategy in strategies]


class StrategyEvolutionService:
    def __init__(
        self,
        repository: StrategyEvolutionRepository | None = None,
        strategy_repository: StrategyRepository | None = None,
        candidate_validation_repository: CandidateValidationSnapshotRepository | None = None,
        journal_service: JournalService | None = None,
        memory_service: MemoryService | None = None,
        research_service: ResearchService | None = None,
    ) -> None:
        self.repository = repository or StrategyEvolutionRepository()
        self.strategy_repository = strategy_repository or StrategyRepository()
        self.candidate_validation_repository = candidate_validation_repository or CandidateValidationSnapshotRepository()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        self.research_service = research_service or ResearchService()

    @staticmethod
    def _sync_lifecycle_stages(
        session: Session,
        strategy_id: int,
        active_version_id: int,
        approved_version_ids: set[int] | None = None,
    ) -> None:
        approved_version_ids = approved_version_ids or set()
        versions = list(session.query(StrategyVersion).filter(StrategyVersion.strategy_id == strategy_id).all())
        for version in versions:
            if version.id == active_version_id:
                version.lifecycle_stage = "active"
                if version.state == "draft":
                    version.state = "approved"
            elif version.id in approved_version_ids:
                version.lifecycle_stage = "approved"
            elif version.state == "draft":
                version.lifecycle_stage = "candidate"
            elif version.lifecycle_stage == "active":
                version.lifecycle_stage = "approved"
        session.commit()

    @staticmethod
    def _set_version_stage(session: Session, version_id: int | None, lifecycle_stage: str) -> None:
        if version_id is None:
            return

        version = session.get(StrategyVersion, version_id)
        if version is None:
            return

        version.lifecycle_stage = lifecycle_stage
        if lifecycle_stage in {"approved", "active"} and version.state == "draft":
            version.state = "approved"
        session.commit()

    @staticmethod
    def _archive_strategy_versions(session: Session, strategy_id: int) -> None:
        versions = list(session.query(StrategyVersion).filter(StrategyVersion.strategy_id == strategy_id).all())
        for version in versions:
            version.lifecycle_stage = "archived"
            if version.state == "draft":
                version.state = "approved"
        session.commit()

    def evolve_from_trade_review(self, session: Session, trade_review: TradeReview):
        if trade_review.strategy_version_id is None:
            raise ValueError("Trade review is not linked to a strategy version")

        source_version = self.repository.get_strategy_version(session, trade_review.strategy_version_id)
        if source_version is None:
            raise ValueError("Source strategy version not found")

        strategy = self.repository.get_strategy(session, source_version.strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")
        previous_version_id = strategy.current_version_id

        updated_rules = dict(source_version.general_rules or {})
        updated_parameters = dict(source_version.parameters or {})

        evolution_count = int(updated_parameters.get("auto_evolution_count", 0)) + 1
        updated_parameters["auto_evolution_count"] = evolution_count
        updated_parameters["last_evolution_trigger"] = "trade_review_loss"
        updated_parameters["last_review_id"] = trade_review.id

        filters_hardening = updated_rules.get("filters_hardening_level", 0) + 1
        updated_rules["filters_hardening_level"] = filters_hardening
        updated_rules["last_lesson_applied"] = trade_review.lesson_learned
        failure_mode = trade_review.failure_mode or trade_review.cause_category
        if failure_mode == "false_breakout":
            updated_rules["require_stronger_breakout_confirmation"] = True
        if failure_mode == "late_exit_or_weak_invalidation":
            updated_rules["earlier_invalidation_required"] = True

        new_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=(
                    f"{source_version.hypothesis}\n\n"
                    f"Autonomous refinement from trade review {trade_review.id}: {trade_review.lesson_learned}"
                ),
                general_rules=updated_rules,
                parameters=updated_parameters,
                state="approved",
                lifecycle_stage="active",
                is_baseline=False,
            ),
        )

        strategy.current_version_id = new_version.id
        session.commit()
        session.refresh(strategy)
        self._sync_lifecycle_stages(session, strategy.id, new_version.id, approved_version_ids={source_version.id})

        change_summary = {
            "filters_hardening_level": filters_hardening,
            "auto_evolution_count": evolution_count,
            "cause_category": trade_review.cause_category,
            "failure_mode": failure_mode,
        }
        change_event = self.repository.create_change_event(
            session,
            strategy_id=strategy.id,
            source_version_id=source_version.id,
            new_version_id=new_version.id,
            trade_review_id=trade_review.id,
            change_reason=trade_review.root_cause,
            proposed_change=trade_review.proposed_strategy_change,
            change_summary=change_summary,
        )
        activation_event = self.repository.create_activation_event(
            session,
            strategy_id=strategy.id,
            activated_version_id=new_version.id,
            previous_version_id=previous_version_id,
            activation_reason=(
                f"Autonomous activation after trade review {trade_review.id} "
                f"with cause category {trade_review.cause_category}."
            ),
        )

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="strategy_evolution",
                strategy_id=strategy.id,
                strategy_version_id=new_version.id,
                observations=change_summary,
                reasoning=trade_review.root_cause,
                decision="activate_new_strategy_version",
                lessons=trade_review.lesson_learned,
            ),
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope=f"strategy:{strategy.id}",
                key=f"evolution:{change_event.id}",
                content=(
                    f"Strategy evolved from version {source_version.id} to {new_version.id} "
                    f"after trade review {trade_review.id}."
                ),
                meta={
                    "source_version_id": source_version.id,
                    "new_version_id": new_version.id,
                    "trade_review_id": trade_review.id,
                    "activation_event_id": activation_event.id,
                },
                importance=0.9,
            ),
        )

        return {
            "strategy_id": strategy.id,
            "source_version_id": source_version.id,
            "new_version_id": new_version.id,
            "change_event_id": change_event.id,
            "activation_event_id": activation_event.id,
        }

    def evolve_from_success_pattern(
        self,
        session: Session,
        *,
        strategy_id: int,
        source_version_id: int,
        success_summary: dict,
    ):
        source_version = self.repository.get_strategy_version(session, source_version_id)
        if source_version is None:
            raise ValueError("Source strategy version not found")

        strategy = self.repository.get_strategy(session, strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")
        previous_version_id = strategy.current_version_id

        updated_rules = dict(source_version.general_rules or {})
        updated_parameters = dict(source_version.parameters or {})
        updated_parameters["success_pattern_evolution_count"] = int(
            updated_parameters.get("success_pattern_evolution_count", 0)
        ) + 1
        updated_parameters["last_success_avg_pnl_pct"] = success_summary["avg_pnl_pct"]
        updated_rules["promote_high_quality_setups"] = True
        updated_rules["min_success_trade_count"] = success_summary["trade_count"]

        new_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=(
                    f"{source_version.hypothesis}\n\n"
                    "Autonomous refinement from successful trade pattern: "
                    f"{success_summary['trade_count']} winning trades with avg pnl "
                    f"{success_summary['avg_pnl_pct']}%."
                ),
                general_rules=updated_rules,
                parameters=updated_parameters,
                state="approved",
                lifecycle_stage="active",
                is_baseline=False,
            ),
        )

        strategy.current_version_id = new_version.id
        session.commit()
        session.refresh(strategy)
        self._sync_lifecycle_stages(session, strategy.id, new_version.id, approved_version_ids={source_version.id})

        change_summary = {
            "trigger": "success_pattern",
            "trade_count": success_summary["trade_count"],
            "avg_pnl_pct": success_summary["avg_pnl_pct"],
            "avg_drawdown_pct": success_summary["avg_drawdown_pct"],
        }
        change_event = self.repository.create_change_event(
            session,
            strategy_id=strategy.id,
            source_version_id=source_version.id,
            new_version_id=new_version.id,
            trade_review_id=None,
            change_reason="Autonomous amplification of repeated successful pattern.",
            proposed_change="Promote filters and parameter settings associated with recent winners.",
            change_summary=change_summary,
        )
        activation_event = self.repository.create_activation_event(
            session,
            strategy_id=strategy.id,
            activated_version_id=new_version.id,
            previous_version_id=previous_version_id,
            activation_reason="Autonomous activation after success-pattern detection.",
        )

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="strategy_evolution_success",
                strategy_id=strategy.id,
                strategy_version_id=new_version.id,
                observations=change_summary,
                reasoning="Successful trade cluster triggered proactive strategy amplification.",
                decision="activate_new_strategy_version",
                lessons="Successful setups should be codified and promoted, not only failures corrected.",
            ),
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope=f"strategy:{strategy.id}",
                key=f"success-evolution:{change_event.id}",
                content=(
                    f"Strategy evolved proactively from version {source_version.id} to {new_version.id} "
                    "after detecting a repeatable successful trade pattern."
                ),
                meta={
                    "source_version_id": source_version.id,
                    "new_version_id": new_version.id,
                    "activation_event_id": activation_event.id,
                    "avg_pnl_pct": success_summary["avg_pnl_pct"],
                },
                importance=0.85,
            ),
        )

        return {
            "strategy_id": strategy.id,
            "source_version_id": source_version.id,
            "new_version_id": new_version.id,
            "change_event_id": change_event.id,
            "activation_event_id": activation_event.id,
            "trigger": "success_pattern",
        }

    def list_change_events(self, session: Session):
        return self.repository.list_change_events(session)

    def list_activation_events(self, session: Session):
        return self.repository.list_activation_events(session)

    @staticmethod
    def _snapshot_to_read(
        session: Session,
        snapshot: CandidateValidationSnapshot,
    ) -> CandidateValidationSnapshotRead:
        version = session.get(StrategyVersion, snapshot.strategy_version_id)
        return CandidateValidationSnapshotRead(
            id=snapshot.id,
            strategy_id=snapshot.strategy_id,
            candidate_version_id=snapshot.strategy_version_id,
            candidate_version_number=version.version if version is not None else 0,
            trade_count=snapshot.trade_count,
            wins=snapshot.wins,
            losses=snapshot.losses,
            avg_pnl_pct=snapshot.avg_pnl_pct,
            avg_drawdown_pct=snapshot.avg_drawdown_pct,
            win_rate=snapshot.win_rate,
            evaluation_status=snapshot.evaluation_status,
            generated_at=snapshot.generated_at,
        )

    @staticmethod
    def _get_candidate_validation_positions(session: Session, candidate_version_id: int) -> list[Position]:
        positions = list(
            session.query(Position)
            .filter(
                Position.strategy_version_id == candidate_version_id,
                Position.status == "closed",
                Position.pnl_pct.is_not(None),
            )
            .all()
        )
        return [
            position
            for position in positions
            if (position.entry_context or {}).get("execution_mode") == "candidate_validation"
        ]

    def _build_candidate_validation_summary(
        self,
        session: Session,
        candidate: StrategyVersion,
    ) -> CandidateValidationSummaryRead:
        validation_positions = self._get_candidate_validation_positions(session, candidate.id)
        wins = len([position for position in validation_positions if (position.pnl_pct or 0.0) > 0])
        losses = len(validation_positions) - wins
        trade_count = len(validation_positions)
        avg_pnl_pct = (
            round(sum((position.pnl_pct or 0.0) for position in validation_positions) / trade_count, 2)
            if trade_count
            else None
        )
        drawdowns = [position.max_drawdown_pct for position in validation_positions if position.max_drawdown_pct is not None]
        avg_drawdown_pct = round(sum(drawdowns) / len(drawdowns), 2) if drawdowns else None
        win_rate = round((wins / trade_count) * 100, 2) if trade_count else None

        evaluation_status = "insufficient_data"
        if trade_count >= 2:
            if avg_pnl_pct is not None and avg_pnl_pct >= 3 and wins >= 2:
                evaluation_status = "promote"
            elif losses >= 2 and (avg_pnl_pct is None or avg_pnl_pct <= 0):
                evaluation_status = "reject"
            else:
                evaluation_status = "observe"

        return CandidateValidationSummaryRead(
            strategy_id=candidate.strategy_id,
            candidate_version_id=candidate.id,
            candidate_version_number=candidate.version,
            trade_count=trade_count,
            wins=wins,
            losses=losses,
            avg_pnl_pct=avg_pnl_pct,
            avg_drawdown_pct=avg_drawdown_pct,
            win_rate=win_rate,
            evaluation_status=evaluation_status,
        )

    def list_candidate_validation_summaries(self, session: Session) -> list[CandidateValidationSnapshotRead]:
        snapshots = self.candidate_validation_repository.list_latest(session)
        return [self._snapshot_to_read(session, snapshot) for snapshot in snapshots]

    def evaluate_candidate_versions(self, session: Session) -> dict:
        candidates = list(
            session.query(StrategyVersion)
            .filter(StrategyVersion.state == "draft")
            .all()
        )
        promotions = []
        rejections = []
        validation_summaries = []

        for candidate in candidates:
            strategy = self.repository.get_strategy(session, candidate.strategy_id)
            if strategy is None:
                continue

            summary = self._build_candidate_validation_summary(session, candidate)
            snapshot = self.candidate_validation_repository.create(
                session,
                {
                    "strategy_id": candidate.strategy_id,
                    "strategy_version_id": candidate.id,
                    "trade_count": summary.trade_count,
                    "wins": summary.wins,
                    "losses": summary.losses,
                    "avg_pnl_pct": summary.avg_pnl_pct,
                    "avg_drawdown_pct": summary.avg_drawdown_pct,
                    "win_rate": summary.win_rate,
                    "evaluation_status": summary.evaluation_status,
                },
            )
            validation_summaries.append(summary)
            if summary.trade_count < 2:
                continue

            if summary.evaluation_status == "promote":
                previous_version_id = strategy.current_version_id
                candidate.state = "approved"
                candidate.lifecycle_stage = "active"
                strategy.current_version_id = candidate.id
                session.commit()
                session.refresh(strategy)
                session.refresh(candidate)
                self._sync_lifecycle_stages(
                    session,
                    strategy.id,
                    candidate.id,
                    approved_version_ids={previous_version_id} if previous_version_id is not None else set(),
                )

                change_event = self.repository.create_change_event(
                    session,
                    strategy_id=strategy.id,
                    source_version_id=previous_version_id,
                    new_version_id=candidate.id,
                    trade_review_id=None,
                    change_reason=(
                        f"Candidate version {candidate.id} promoted after {summary.trade_count} candidate-validation "
                        f"trades with avg pnl {summary.avg_pnl_pct}%."
                    ),
                    proposed_change="Promote candidate recovery version to active.",
                    change_summary={
                        "decision": "promote_candidate",
                        "candidate_version_id": candidate.id,
                        "trade_count": summary.trade_count,
                        "avg_pnl_pct": summary.avg_pnl_pct,
                        "wins": summary.wins,
                        "validation_mode": "candidate_validation",
                    },
                )
                activation_event = self.repository.create_activation_event(
                    session,
                    strategy_id=strategy.id,
                    activated_version_id=candidate.id,
                    previous_version_id=previous_version_id,
                    activation_reason="Candidate version promoted after successful candidate-validation results.",
                )
                self.journal_service.create_entry(
                    session,
                    JournalEntryCreate(
                        entry_type="strategy_candidate_promotion",
                        strategy_id=strategy.id,
                        strategy_version_id=candidate.id,
                        observations={
                            "candidate_version_id": candidate.id,
                            "trade_count": summary.trade_count,
                            "avg_pnl_pct": summary.avg_pnl_pct,
                            "wins": summary.wins,
                        },
                        reasoning="Candidate recovery version outperformed enough in candidate-validation to replace the active version.",
                        decision="promote_candidate_version",
                        lessons="Candidate variants should only be promoted from explicit validation trades.",
                    ),
                )
                self.memory_service.create_item(
                    session,
                    MemoryItemCreate(
                        memory_type="strategy_evolution",
                        scope=f"strategy:{strategy.id}",
                        key=f"candidate-promotion:{change_event.id}",
                        content=(
                            f"Candidate strategy version {candidate.id} promoted to active after "
                            f"{summary.trade_count} candidate-validation trades."
                        ),
                        meta={
                            "candidate_version_id": candidate.id,
                            "previous_version_id": previous_version_id,
                            "activation_event_id": activation_event.id,
                            "avg_pnl_pct": summary.avg_pnl_pct,
                            "trade_count": summary.trade_count,
                            "validation_mode": "candidate_validation",
                        },
                        importance=0.88,
                    ),
                )
                promotions.append(
                    {
                        "decision": "promote_candidate",
                        "strategy_id": strategy.id,
                        "candidate_version_id": candidate.id,
                        "change_event_id": change_event.id,
                        "activation_event_id": activation_event.id,
                    }
                )
                continue

            if summary.evaluation_status != "reject":
                continue

            candidate.state = "rejected"
            candidate.lifecycle_stage = "archived"
            session.commit()
            session.refresh(candidate)

            patterns = list(
                session.query(FailurePattern)
                .filter(FailurePattern.strategy_id == strategy.id, FailurePattern.status == "open")
                .all()
            )
            for pattern in patterns:
                evidence = dict(pattern.evidence or {})
                if evidence.get("candidate_version_id") != candidate.id:
                    continue
                evidence["last_rejected_candidate_version_id"] = candidate.id
                evidence.pop("candidate_version_id", None)
                pattern.evidence = evidence
                session.add(pattern)
            session.commit()

            change_event = self.repository.create_change_event(
                session,
                strategy_id=strategy.id,
                source_version_id=strategy.current_version_id,
                new_version_id=candidate.id,
                trade_review_id=None,
                change_reason=(
                    f"Candidate version {candidate.id} rejected after {summary.trade_count} candidate-validation "
                    f"trades with avg pnl {summary.avg_pnl_pct}%."
                ),
                proposed_change="Archive candidate recovery version and continue research.",
                change_summary={
                    "decision": "reject_candidate",
                    "candidate_version_id": candidate.id,
                    "trade_count": summary.trade_count,
                    "avg_pnl_pct": summary.avg_pnl_pct,
                    "wins": summary.wins,
                    "losses": summary.losses,
                    "validation_mode": "candidate_validation",
                },
            )
            self.journal_service.create_entry(
                session,
                JournalEntryCreate(
                    entry_type="strategy_candidate_rejection",
                    strategy_id=strategy.id,
                    strategy_version_id=candidate.id,
                    observations={
                        "candidate_version_id": candidate.id,
                        "trade_count": summary.trade_count,
                        "avg_pnl_pct": summary.avg_pnl_pct,
                        "losses": summary.losses,
                    },
                    reasoning="Candidate recovery version failed explicit candidate-validation and should not be promoted.",
                    decision="reject_candidate_version",
                    lessons="Candidate variants that fail validation should be archived to avoid endless retesting.",
                ),
            )
            self.memory_service.create_item(
                session,
                MemoryItemCreate(
                    memory_type="strategy_evolution",
                    scope=f"strategy:{strategy.id}",
                    key=f"candidate-rejection:{change_event.id}",
                    content=(
                        f"Candidate strategy version {candidate.id} rejected after "
                        f"{summary.trade_count} candidate-validation trades."
                    ),
                    meta={
                        "candidate_version_id": candidate.id,
                        "current_active_version_id": strategy.current_version_id,
                        "avg_pnl_pct": summary.avg_pnl_pct,
                        "trade_count": summary.trade_count,
                        "validation_mode": "candidate_validation",
                    },
                    importance=0.72,
                ),
            )
            rejections.append(
                {
                    "decision": "reject_candidate",
                    "strategy_id": strategy.id,
                    "candidate_version_id": candidate.id,
                    "change_event_id": change_event.id,
                }
            )

        return {
            "promoted_candidates": len(promotions),
            "rejected_candidates": len(rejections),
            "promotions": promotions,
            "rejections": rejections,
            "validation_summaries": [summary.model_dump() for summary in validation_summaries],
        }

    def find_repeated_candidate_rejections(self, session: Session, threshold: int = 2) -> list[dict]:
        snapshots = self.candidate_validation_repository.list_latest(session)
        rejected_by_strategy: dict[int, list[CandidateValidationSnapshot]] = {}
        for snapshot in snapshots:
            if snapshot.evaluation_status != "reject":
                continue
            rejected_by_strategy.setdefault(snapshot.strategy_id, []).append(snapshot)

        repeated_rejections: list[dict] = []
        for strategy_id, strategy_snapshots in rejected_by_strategy.items():
            unique_version_ids = sorted({snapshot.strategy_version_id for snapshot in strategy_snapshots})
            if len(unique_version_ids) < threshold:
                continue
            repeated_rejections.append(
                {
                    "strategy_id": strategy_id,
                    "rejected_candidate_count": len(unique_version_ids),
                    "candidate_version_ids": unique_version_ids,
                }
            )
        return repeated_rejections

    def fork_variant_from_failure_pattern(self, session: Session, pattern: FailurePattern) -> dict | None:
        strategy = self.repository.get_strategy(session, pattern.strategy_id)
        if strategy is None or strategy.current_version_id is None:
            return None

        source_version = self.repository.get_strategy_version(session, strategy.current_version_id)
        if source_version is None:
            return None

        evidence = dict(pattern.evidence or {})
        if evidence.get("candidate_version_id") is not None:
            return None

        updated_rules = dict(source_version.general_rules or {})
        updated_parameters = dict(source_version.parameters or {})
        updated_parameters["candidate_failure_pattern"] = pattern.failure_mode
        updated_parameters["candidate_pattern_occurrences"] = pattern.occurrences
        updated_parameters["candidate_recovery_version_count"] = int(
            updated_parameters.get("candidate_recovery_version_count", 0)
        ) + 1

        if pattern.failure_mode == "false_breakout":
            updated_rules["require_stronger_breakout_confirmation"] = True
            updated_rules["avoid_extended_entries"] = True
        elif pattern.failure_mode == "late_exit_or_weak_invalidation":
            updated_rules["earlier_invalidation_required"] = True
            updated_rules["max_allowed_drawdown_pct"] = 3
        else:
            updated_rules["filters_hardening_level"] = int(updated_rules.get("filters_hardening_level", 0)) + 1

        candidate_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=(
                    f"{source_version.hypothesis}\n\n"
                    "Candidate recovery variant from repeated failure pattern "
                    f"'{pattern.failure_mode}': {pattern.recommended_action or 'tighten setup quality.'}"
                ),
                general_rules=updated_rules,
                parameters=updated_parameters,
                state="draft",
                lifecycle_stage="candidate",
                is_baseline=False,
            ),
        )

        strategy.current_version_id = source_version.id
        session.commit()
        session.refresh(strategy)
        self._sync_lifecycle_stages(session, strategy.id, source_version.id)

        change_event = self.repository.create_change_event(
            session,
            strategy_id=strategy.id,
            source_version_id=source_version.id,
            new_version_id=candidate_version.id,
            trade_review_id=None,
            change_reason=(
                f"Forked candidate variant from repeated failure pattern '{pattern.failure_mode}' "
                f"after {pattern.occurrences} occurrences."
            ),
            proposed_change=pattern.recommended_action,
            change_summary={
                "decision": "fork_variant",
                "failure_mode": pattern.failure_mode,
                "occurrences": pattern.occurrences,
                "candidate_version_id": candidate_version.id,
            },
        )

        evidence["candidate_version_id"] = candidate_version.id
        evidence["candidate_change_event_id"] = change_event.id
        pattern.evidence = evidence
        session.commit()
        session.refresh(pattern)

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="strategy_candidate_variant",
                strategy_id=strategy.id,
                strategy_version_id=candidate_version.id,
                observations={
                    "failure_mode": pattern.failure_mode,
                    "occurrences": pattern.occurrences,
                    "candidate_version_id": candidate_version.id,
                },
                reasoning=pattern.recommended_action or "Repeated failure pattern triggered candidate fork.",
                decision="create_candidate_variant",
                lessons="Repeated failure modes should produce isolated candidate variants before full activation.",
            ),
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope=f"strategy:{strategy.id}",
                key=f"failure-fork:{change_event.id}",
                content=(
                    f"Candidate recovery variant {candidate_version.id} forked from strategy {strategy.id} "
                    f"after repeated failure pattern '{pattern.failure_mode}'."
                ),
                meta={
                    "source_version_id": source_version.id,
                    "candidate_version_id": candidate_version.id,
                    "failure_mode": pattern.failure_mode,
                    "occurrences": pattern.occurrences,
                },
                importance=0.8,
            ),
        )

        return {
            "strategy_id": strategy.id,
            "source_version_id": source_version.id,
            "candidate_version_id": candidate_version.id,
            "change_event_id": change_event.id,
            "decision": "fork_variant",
        }

    def evaluate_failure_patterns(self, session: Session) -> dict:
        patterns = list(session.query(FailurePattern).filter(FailurePattern.status == "open").all())
        decisions = []

        for pattern in patterns:
            strategy = self.repository.get_strategy(session, pattern.strategy_id)
            if strategy is None:
                continue

            scorecard = (
                session.query(StrategyScorecard)
                .filter(StrategyScorecard.strategy_id == strategy.id)
                .order_by(StrategyScorecard.generated_at.desc())
                .first()
            )
            if scorecard is None:
                continue

            if (
                pattern.occurrences >= 2
                and pattern.recommended_action
                and strategy.status in ["paper", "research", "degraded", "active", "live"]
            ):
                fork_result = self.fork_variant_from_failure_pattern(session, pattern)
                if fork_result is not None:
                    decisions.append(fork_result)

            if pattern.occurrences >= 2 and scorecard.fitness_score <= 0.45 and strategy.status in ["paper", "active", "live"]:
                previous_status = strategy.status
                strategy.status = "degraded"
                session.commit()
                session.refresh(strategy)
                self._set_version_stage(session, strategy.current_version_id, "degraded")
                change_event = self.repository.create_change_event(
                    session,
                    strategy_id=strategy.id,
                    source_version_id=strategy.current_version_id,
                    new_version_id=None,
                    trade_review_id=None,
                    change_reason=(
                        f"Repeated failure pattern '{pattern.failure_mode}' with weak fitness score "
                        f"{scorecard.fitness_score} triggered strategy degradation."
                    ),
                    proposed_change=pattern.recommended_action,
                    change_summary={
                        "decision": "degrade",
                        "previous_status": previous_status,
                        "new_status": strategy.status,
                        "failure_mode": pattern.failure_mode,
                        "occurrences": pattern.occurrences,
                        "fitness_score": scorecard.fitness_score,
                    },
                )
                _, created = self.research_service.ensure_recovery_task(
                    session,
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    reason="Repeated failure pattern with weak fitness.",
                    failure_mode=pattern.failure_mode,
                )
                decisions.append(
                    {
                        "decision": "degrade",
                        "strategy_id": strategy.id,
                        "event_id": change_event.id,
                        "created_research_task": created,
                    }
                )

            if (
                pattern.occurrences >= 3
                and (scorecard.fitness_score <= 0.15 or (scorecard.avg_return_pct is not None and scorecard.avg_return_pct <= -5))
                and scorecard.losses_count >= 3
                and strategy.status in ["degraded", "research", "paper"]
            ):
                previous_status = strategy.status
                strategy.status = "archived"
                pattern.status = "addressed"
                session.commit()
                session.refresh(strategy)
                self._archive_strategy_versions(session, strategy.id)
                change_event = self.repository.create_change_event(
                    session,
                    strategy_id=strategy.id,
                    source_version_id=strategy.current_version_id,
                    new_version_id=None,
                    trade_review_id=None,
                    change_reason=(
                        f"Strategy archived after persistent '{pattern.failure_mode}' failures and "
                        f"low activity score {scorecard.activity_score}."
                    ),
                    proposed_change="Archive strategy and redirect effort to new research.",
                    change_summary={
                        "decision": "archive",
                        "previous_status": previous_status,
                        "new_status": strategy.status,
                        "failure_mode": pattern.failure_mode,
                        "occurrences": pattern.occurrences,
                        "fitness_score": scorecard.fitness_score,
                        "avg_return_pct": scorecard.avg_return_pct,
                        "losses_count": scorecard.losses_count,
                    },
                )
                decisions.append({"decision": "archive", "strategy_id": strategy.id, "event_id": change_event.id})

        return {
            "forked_variants": len([item for item in decisions if item["decision"] == "fork_variant"]),
            "degraded_strategies": len([item for item in decisions if item["decision"] == "degrade"]),
            "archived_strategies": len([item for item in decisions if item["decision"] == "archive"]),
            "decisions": decisions,
        }


class StrategyLabService:
    def __init__(self, evolution_service: StrategyEvolutionService | None = None) -> None:
        self.evolution_service = evolution_service or StrategyEvolutionService()

    def evolve_from_success_patterns(self, session: Session, excluded_strategy_ids: set[int] | None = None) -> dict:
        excluded_strategy_ids = excluded_strategy_ids or set()
        candidates = self._find_success_candidates(session)
        results = []
        skipped = 0

        for candidate in candidates:
            if candidate["strategy_id"] in excluded_strategy_ids:
                skipped += 1
                continue
            strategy = session.get(Strategy, candidate["strategy_id"])
            if strategy is None or strategy.current_version_id is None:
                skipped += 1
                continue

            latest_event = self.evolution_service.repository.list_change_events(session)
            recently_evolved = any(
                event.strategy_id == strategy.id and event.source_version_id == strategy.current_version_id
                for event in latest_event[:5]
            )
            if recently_evolved:
                skipped += 1
                continue

            result = self.evolution_service.evolve_from_success_pattern(
                session,
                strategy_id=strategy.id,
                source_version_id=strategy.current_version_id,
                success_summary=candidate,
            )
            results.append(result)

        return {
            "generated_variants": len(results),
            "skipped_candidates": skipped,
            "results": results,
        }

    @staticmethod
    def _find_success_candidates(session: Session) -> list[dict]:
        statement = (
            select(
                Position.strategy_version_id,
                func.count(Position.id).label("trade_count"),
                func.avg(Position.pnl_pct).label("avg_pnl_pct"),
                func.avg(Position.max_drawdown_pct).label("avg_drawdown_pct"),
            )
            .where(
                Position.status == "closed",
                Position.pnl_pct.is_not(None),
                Position.pnl_pct > 0,
                Position.strategy_version_id.is_not(None),
            )
            .group_by(Position.strategy_version_id)
        )

        rows = session.execute(statement).all()
        candidates: list[dict] = []
        for row in rows:
            strategy_version_id = row[0]
            trade_count = int(row[1] or 0)
            avg_pnl_pct = float(row[2] or 0.0)
            avg_drawdown_pct = float(row[3] or 0.0)
            if trade_count < 2 or avg_pnl_pct < 3:
                continue

            strategy_version = session.get(StrategyVersion, strategy_version_id)
            if strategy_version is None:
                continue

            candidates.append(
                {
                    "strategy_id": strategy_version.strategy_id,
                    "source_version_id": strategy_version_id,
                    "trade_count": trade_count,
                    "avg_pnl_pct": round(avg_pnl_pct, 2),
                    "avg_drawdown_pct": round(avg_drawdown_pct, 2),
                    "trigger": "success_pattern",
                }
            )
        return candidates
