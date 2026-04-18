from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.candidate_validation_snapshot import CandidateValidationSnapshot
from app.db.models.decision_context import StrategyContextRule
from app.db.models.failure_pattern import FailurePattern
from app.db.models.position import Position
from app.db.models.signal import TradeSignal
from app.db.models.strategy import Strategy, StrategyVersion
from app.db.models.strategy_scorecard import StrategyScorecard
from app.db.models.trade_review import TradeReview
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
from app.domains.learning.services import JournalService, MemoryService
from app.domains.market.services import MarketDataService, ResearchService
from app.domains.system.events import EventLogService
from app.domains.strategy.repositories import (
    CandidateValidationSnapshotRepository,
    HypothesisRepository,
    SignalDefinitionRepository,
    ScreenerRepository,
    SetupRepository,
    StrategyEvolutionRepository,
    StrategyRepository,
    StrategyScorecardRepository,
    WatchlistRepository,
)
from app.domains.strategy.schemas import (
    CandidateValidationSnapshotRead,
    CandidateValidationSummaryRead,
    HypothesisCreate,
    SignalDefinitionCreate,
    SetupCreate,
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
from app.domains.strategy.validation import StrategyValidationService


class HypothesisService:
    def __init__(
        self,
        repository: HypothesisRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or HypothesisRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_hypotheses(self, session: Session):
        return self.repository.list(session)

    def create_hypothesis(self, session: Session, payload: HypothesisCreate):
        hypothesis = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="hypothesis.created",
            entity_type="hypothesis",
            entity_id=hypothesis.id,
            source="strategy_catalog",
            pdca_phase_hint="plan",
            payload={"code": hypothesis.code, "name": hypothesis.name, "status": hypothesis.status},
        )
        return hypothesis


class SetupService:
    def __init__(
        self,
        repository: SetupRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or SetupRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_setups(self, session: Session):
        return self.repository.list(session)

    def create_setup(self, session: Session, payload: SetupCreate):
        setup = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="setup.created",
            entity_type="setup",
            entity_id=setup.id,
            source="strategy_catalog",
            pdca_phase_hint="plan",
            payload={"code": setup.code, "name": setup.name, "strategy_id": setup.strategy_id},
        )
        return setup


class SignalDefinitionService:
    def __init__(
        self,
        repository: SignalDefinitionRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or SignalDefinitionRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_signal_definitions(self, session: Session):
        return self.repository.list(session)

    def create_signal_definition(self, session: Session, payload: SignalDefinitionCreate):
        signal_definition = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="signal_definition.created",
            entity_type="signal_definition",
            entity_id=signal_definition.id,
            source="strategy_catalog",
            pdca_phase_hint="plan",
            payload={
                "code": signal_definition.code,
                "name": signal_definition.name,
                "signal_kind": signal_definition.signal_kind,
            },
        )
        return signal_definition


class StrategyService:
    def __init__(
        self,
        repository: StrategyRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or StrategyRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_strategies(self, session: Session):
        return self.repository.list(session)

    def create_strategy(self, session: Session, payload: StrategyCreate):
        strategy = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="strategy.created",
            entity_type="strategy",
            entity_id=strategy.id,
            source="strategy_catalog",
            pdca_phase_hint="plan",
            payload={"code": strategy.code, "name": strategy.name, "status": strategy.status},
        )
        return strategy

    def create_version(self, session: Session, strategy_id: int, payload: StrategyVersionCreate):
        version = self.repository.create_version(session, strategy_id, payload)
        self.event_log_service.record(
            session,
            event_type="strategy.version_created",
            entity_type="strategy_version",
            entity_id=version.id,
            source="strategy_catalog",
            pdca_phase_hint="plan",
            payload={"strategy_id": version.strategy_id, "version": version.version, "state": version.state},
        )
        return version


class ScreenerService:
    def __init__(
        self,
        repository: ScreenerRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or ScreenerRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_screeners(self, session: Session):
        return self.repository.list(session)

    def create_screener(
        self,
        session: Session,
        payload: ScreenerCreate,
        *,
        event_source: str = "strategy_catalog",
    ):
        screener = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="screener.created",
            entity_type="screener",
            entity_id=screener.id,
            source=event_source,
            pdca_phase_hint="plan",
            payload={
                "code": screener.code,
                "name": screener.name,
                "strategy_id": screener.strategy_id,
                "current_version_id": screener.current_version_id,
            },
        )
        if screener.current_version_id is not None:
            self.event_log_service.record(
                session,
                event_type="screener.version_created",
                entity_type="screener_version",
                entity_id=screener.current_version_id,
                source=event_source,
                pdca_phase_hint="plan",
                payload={
                    "screener_id": screener.id,
                    "version": 1,
                },
            )
        return screener

    def create_version(
        self,
        session: Session,
        screener_id: int,
        payload: ScreenerVersionCreate,
        *,
        event_source: str = "strategy_catalog",
    ):
        version = self.repository.create_version(session, screener_id, payload)
        self.event_log_service.record(
            session,
            event_type="screener.version_created",
            entity_type="screener_version",
            entity_id=version.id,
            source=event_source,
            pdca_phase_hint="plan",
            payload={
                "screener_id": version.screener_id,
                "version": version.version,
                "status": version.status,
            },
        )
        return version


class WatchlistService:
    def __init__(
        self,
        repository: WatchlistRepository | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or WatchlistRepository()
        self.event_log_service = event_log_service or EventLogService()

    def list_watchlists(self, session: Session):
        return self.repository.list(session)

    def create_watchlist(
        self,
        session: Session,
        payload: WatchlistCreate,
        *,
        event_source: str = "strategy_catalog",
    ):
        watchlist = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="watchlist.created",
            entity_type="watchlist",
            entity_id=watchlist.id,
            source=event_source,
            pdca_phase_hint="plan",
            payload={
                "code": watchlist.code,
                "name": watchlist.name,
                "strategy_id": watchlist.strategy_id,
                "setup_id": watchlist.setup_id,
                "status": watchlist.status,
            },
        )
        if payload.initial_items:
            for item_payload in payload.initial_items:
                self.add_item(
                    session,
                    watchlist.id,
                    item_payload,
                    event_source=event_source,
                )
            session.expire_all()
            refreshed = self.repository.get(session, watchlist.id)
            if refreshed is not None:
                watchlist = refreshed
        return watchlist

    def add_item(
        self,
        session: Session,
        watchlist_id: int,
        payload: WatchlistItemCreate,
        *,
        event_source: str = "strategy_catalog",
    ):
        item = self.repository.add_item(session, watchlist_id, payload)
        self.event_log_service.record(
            session,
            event_type="watchlist_item.added",
            entity_type="watchlist_item",
            entity_id=item.id,
            source=event_source,
            pdca_phase_hint=self._watchlist_item_phase_hint(event_source),
            payload={
                "watchlist_id": watchlist_id,
                "ticker": item.ticker,
                "setup_id": item.setup_id,
                "state": item.state,
                "key_metrics_source": (item.key_metrics or {}).get("source"),
            },
        )
        return item

    @staticmethod
    def _watchlist_item_phase_hint(event_source: str) -> str | None:
        if event_source == "system_seed":
            return "plan"
        if event_source in {"strategy_catalog", "opportunity_discovery"}:
            return "do"
        return None


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
            profit_factor=snapshot.profit_factor,
            distinct_tickers=snapshot.distinct_tickers,
            window_count=snapshot.window_count,
            rolling_pass_rate=snapshot.rolling_pass_rate,
            replay_score=snapshot.replay_score,
            validation_mode=snapshot.validation_mode,
            evaluation_status=snapshot.evaluation_status,
            decision_reason=snapshot.decision_reason,
            validation_payload=snapshot.validation_payload or {},
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

        signals = list(session.scalars(select(TradeSignal).where(TradeSignal.strategy_id == strategy_id)).all())
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
    HYPOTHESIS_BASE_MAX_CHARS = 900
    HYPOTHESIS_NOTE_MAX_CHARS = 280
    COMPACTED_HYPOTHESIS_MAX_CHARS = 600

    def __init__(
        self,
        repository: StrategyEvolutionRepository | None = None,
        strategy_repository: StrategyRepository | None = None,
        candidate_validation_repository: CandidateValidationSnapshotRepository | None = None,
        validation_service: StrategyValidationService | None = None,
        journal_service: JournalService | None = None,
        memory_service: MemoryService | None = None,
        research_service: ResearchService | None = None,
    ) -> None:
        self.repository = repository or StrategyEvolutionRepository()
        self.strategy_repository = strategy_repository or StrategyRepository()
        self.candidate_validation_repository = candidate_validation_repository or CandidateValidationSnapshotRepository()
        self.validation_service = validation_service or StrategyValidationService()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        self.research_service = research_service or ResearchService()

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _truncate_text(cls, value: str | None, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: max(limit - 3, 0)].rstrip()}..."

    @classmethod
    def _extract_base_hypothesis(cls, version: StrategyVersion) -> str:
        parameters = version.parameters if isinstance(version.parameters, dict) else {}
        stored_base = cls._normalize_text(parameters.get("base_hypothesis"))
        if stored_base:
            return cls._truncate_text(stored_base, cls.HYPOTHESIS_BASE_MAX_CHARS)

        hypothesis = str(version.hypothesis or "").strip()
        if "\n\n" in hypothesis:
            hypothesis = hypothesis.split("\n\n", 1)[0].strip()
        return cls._truncate_text(cls._normalize_text(hypothesis), cls.HYPOTHESIS_BASE_MAX_CHARS)

    @classmethod
    def _build_variant_hypothesis(
        cls,
        source_version: StrategyVersion,
        *,
        trigger: str,
        note: str,
    ) -> tuple[str, str]:
        base_hypothesis = cls._extract_base_hypothesis(source_version)
        compact_note = cls._truncate_text(cls._normalize_text(note), cls.HYPOTHESIS_NOTE_MAX_CHARS)
        return f"{base_hypothesis}\n\nVariant note [{trigger}]: {compact_note}", compact_note

    @classmethod
    def _record_lineage_metadata(
        cls,
        parameters: dict,
        *,
        source_version: StrategyVersion,
        trigger: str,
        note: str,
    ) -> None:
        source_parameters = source_version.parameters if isinstance(source_version.parameters, dict) else {}
        parameters["base_hypothesis"] = cls._extract_base_hypothesis(source_version)
        parameters["evolution_trigger"] = trigger
        parameters["evolution_note"] = note
        parameters["evolution_source_version_id"] = source_version.id
        parameters["evolution_origin_version_id"] = source_parameters.get("evolution_origin_version_id") or source_version.id
        parameters["evolution_lineage_depth"] = int(source_parameters.get("evolution_lineage_depth", 0)) + 1

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

    @staticmethod
    def _describe_context_rule(rule: StrategyContextRule) -> str:
        return f"{rule.feature_scope}.{rule.feature_key}={rule.feature_value}"

    def _list_context_bundles(
        self,
        session: Session,
        *,
        strategy_id: int,
        strategy_version_id: int,
        limit: int = 4,
    ) -> list[dict]:
        rules = list(
            session.scalars(
                select(StrategyContextRule).where(
                    StrategyContextRule.status == "active",
                    (
                        (StrategyContextRule.strategy_version_id == strategy_version_id)
                        | (StrategyContextRule.strategy_id == strategy_id)
                    ),
                )
            ).all()
        )
        rules.sort(
            key=lambda rule: (
                0 if rule.feature_scope == "combo" else 1,
                -(float(rule.confidence or 0.0)),
                rule.id,
            )
        )
        bundles: list[dict] = []
        for rule in rules[:limit]:
            evidence_payload = rule.evidence_payload if isinstance(rule.evidence_payload, dict) else {}
            bundles.append(
                {
                    "rule_id": rule.id,
                    "descriptor": self._describe_context_rule(rule),
                    "action_type": rule.action_type,
                    "confidence": rule.confidence,
                    "sample_size": evidence_payload.get("sample_size"),
                    "rationale": rule.rationale,
                }
            )
        return bundles

    def _apply_context_bundles_to_version(
        self,
        session: Session,
        *,
        strategy_id: int,
        source_version_id: int,
        updated_rules: dict,
        updated_parameters: dict,
        limit: int = 4,
    ) -> list[dict]:
        bundles = self._list_context_bundles(
            session,
            strategy_id=strategy_id,
            strategy_version_id=source_version_id,
            limit=limit,
        )
        if not bundles:
            return []

        preferred = list(updated_rules.get("preferred_context_bundles") or [])
        avoid = list(updated_rules.get("avoid_context_bundles") or [])
        for bundle in bundles:
            descriptor = bundle["descriptor"]
            if bundle["action_type"] == "boost_confidence":
                if descriptor not in preferred:
                    preferred.append(descriptor)
            elif descriptor not in avoid:
                avoid.append(descriptor)

        if preferred:
            updated_rules["preferred_context_bundles"] = preferred[:limit]
        if avoid:
            updated_rules["avoid_context_bundles"] = avoid[:limit]
        updated_parameters["learned_context_bundle_count"] = len(bundles)
        return bundles

    def evolve_from_trade_review(self, session: Session, trade_review: TradeReview):
        if trade_review.strategy_version_id is None:
            raise ValueError("Trade review is not linked to a strategy version")

        source_version = self.repository.get_strategy_version(session, trade_review.strategy_version_id)
        if source_version is None:
            raise ValueError("Source strategy version not found")

        strategy = self.repository.get_strategy(session, source_version.strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")

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
        hypothesis, compact_note = self._build_variant_hypothesis(
            source_version,
            trigger="trade_review_loss",
            note=f"Trade review {trade_review.id}: {trade_review.lesson_learned}",
        )
        self._record_lineage_metadata(
            updated_parameters,
            source_version=source_version,
            trigger="trade_review_loss",
            note=compact_note,
        )
        context_bundles = self._apply_context_bundles_to_version(
            session,
            strategy_id=strategy.id,
            source_version_id=source_version.id,
            updated_rules=updated_rules,
            updated_parameters=updated_parameters,
        )

        new_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=hypothesis,
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

        change_summary = {
            "filters_hardening_level": filters_hardening,
            "auto_evolution_count": evolution_count,
            "cause_category": trade_review.cause_category,
            "failure_mode": failure_mode,
            "validation_required": True,
            "context_bundles": context_bundles,
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

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="strategy_evolution",
                strategy_id=strategy.id,
                strategy_version_id=new_version.id,
                observations=change_summary,
                reasoning=trade_review.root_cause,
                decision="queue_candidate_validation",
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
                    f"Candidate strategy version {new_version.id} forked from version {source_version.id} "
                    f"after trade review {trade_review.id} and queued for validation."
                ),
                meta={
                    "source_version_id": source_version.id,
                    "new_version_id": new_version.id,
                    "trade_review_id": trade_review.id,
                    "validation_required": True,
                    "context_bundles": context_bundles,
                },
                importance=0.9,
            ),
        )

        return {
            "strategy_id": strategy.id,
            "source_version_id": source_version.id,
            "new_version_id": new_version.id,
            "change_event_id": change_event.id,
            "activation_event_id": None,
            "validation_required": True,
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

        updated_rules = dict(source_version.general_rules or {})
        updated_parameters = dict(source_version.parameters or {})
        updated_parameters["success_pattern_evolution_count"] = int(
            updated_parameters.get("success_pattern_evolution_count", 0)
        ) + 1
        updated_parameters["last_success_avg_pnl_pct"] = success_summary["avg_pnl_pct"]
        updated_rules["promote_high_quality_setups"] = True
        updated_rules["min_success_trade_count"] = success_summary["trade_count"]
        hypothesis, compact_note = self._build_variant_hypothesis(
            source_version,
            trigger="success_pattern",
            note=(
                f"{success_summary['trade_count']} winning trades with avg pnl "
                f"{success_summary['avg_pnl_pct']}%."
            ),
        )
        self._record_lineage_metadata(
            updated_parameters,
            source_version=source_version,
            trigger="success_pattern",
            note=compact_note,
        )
        context_bundles = self._apply_context_bundles_to_version(
            session,
            strategy_id=strategy.id,
            source_version_id=source_version.id,
            updated_rules=updated_rules,
            updated_parameters=updated_parameters,
        )

        new_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=hypothesis,
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

        change_summary = {
            "trigger": "success_pattern",
            "trade_count": success_summary["trade_count"],
            "avg_pnl_pct": success_summary["avg_pnl_pct"],
            "avg_drawdown_pct": success_summary["avg_drawdown_pct"],
            "validation_required": True,
            "context_bundles": context_bundles,
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

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="strategy_evolution_success",
                strategy_id=strategy.id,
                strategy_version_id=new_version.id,
                observations=change_summary,
                reasoning="Successful trade cluster triggered proactive strategy amplification.",
                decision="queue_candidate_validation",
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
                    f"Candidate strategy version {new_version.id} queued from version {source_version.id} "
                    "after detecting a repeatable successful trade pattern."
                ),
                meta={
                    "source_version_id": source_version.id,
                    "new_version_id": new_version.id,
                    "avg_pnl_pct": success_summary["avg_pnl_pct"],
                    "validation_required": True,
                    "context_bundles": context_bundles,
                },
                importance=0.85,
            ),
        )

        return {
            "strategy_id": strategy.id,
            "source_version_id": source_version.id,
            "new_version_id": new_version.id,
            "change_event_id": change_event.id,
            "activation_event_id": None,
            "trigger": "success_pattern",
            "validation_required": True,
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
            profit_factor=snapshot.profit_factor,
            distinct_tickers=snapshot.distinct_tickers,
            window_count=snapshot.window_count,
            rolling_pass_rate=snapshot.rolling_pass_rate,
            replay_score=snapshot.replay_score,
            validation_mode=snapshot.validation_mode,
            evaluation_status=snapshot.evaluation_status,
            decision_reason=snapshot.decision_reason,
            validation_payload=snapshot.validation_payload or {},
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
        return self.validation_service.build_candidate_validation_summary(
            session,
            candidate=candidate,
            validation_positions=validation_positions,
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
                    "profit_factor": summary.profit_factor,
                    "distinct_tickers": summary.distinct_tickers,
                    "window_count": summary.window_count,
                    "rolling_pass_rate": summary.rolling_pass_rate,
                    "replay_score": summary.replay_score,
                    "validation_mode": summary.validation_mode,
                    "evaluation_status": summary.evaluation_status,
                    "decision_reason": summary.decision_reason,
                    "validation_payload": summary.validation_payload,
                },
            )
            validation_summaries.append(summary)
            if summary.evaluation_status == "insufficient_data":
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
                        "profit_factor": summary.profit_factor,
                        "rolling_pass_rate": summary.rolling_pass_rate,
                        "replay_score": summary.replay_score,
                        "validation_mode": summary.validation_mode,
                        "decision_reason": summary.decision_reason,
                        "validation_payload": summary.validation_payload,
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
                            "profit_factor": summary.profit_factor,
                            "rolling_pass_rate": summary.rolling_pass_rate,
                            "replay_score": summary.replay_score,
                        },
                        reasoning=summary.decision_reason
                        or "Candidate recovery version outperformed enough in candidate-validation to replace the active version.",
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
                            "validation_mode": summary.validation_mode,
                            "profit_factor": summary.profit_factor,
                            "rolling_pass_rate": summary.rolling_pass_rate,
                            "replay_score": summary.replay_score,
                            "decision_reason": summary.decision_reason,
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
                    "profit_factor": summary.profit_factor,
                    "rolling_pass_rate": summary.rolling_pass_rate,
                    "replay_score": summary.replay_score,
                    "validation_mode": summary.validation_mode,
                    "decision_reason": summary.decision_reason,
                    "validation_payload": summary.validation_payload,
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
                        "profit_factor": summary.profit_factor,
                        "rolling_pass_rate": summary.rolling_pass_rate,
                        "replay_score": summary.replay_score,
                    },
                    reasoning=summary.decision_reason
                    or "Candidate recovery version failed explicit candidate-validation and should not be promoted.",
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
                        "validation_mode": summary.validation_mode,
                        "profit_factor": summary.profit_factor,
                        "rolling_pass_rate": summary.rolling_pass_rate,
                        "replay_score": summary.replay_score,
                        "decision_reason": summary.decision_reason,
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

    def fork_variant_from_context_rule(
        self,
        session: Session,
        *,
        strategy_id: int,
        source_version_id: int,
        context_rule: StrategyContextRule,
    ) -> dict | None:
        strategy = self.repository.get_strategy(session, strategy_id)
        if strategy is None or strategy.current_version_id != source_version_id:
            return None

        source_version = self.repository.get_strategy_version(session, source_version_id)
        if source_version is None:
            return None

        updated_rules = dict(source_version.general_rules or {})
        updated_parameters = dict(source_version.parameters or {})
        bundle_descriptor = self._describe_context_rule(context_rule)
        if context_rule.action_type == "boost_confidence":
            preferred_bundles = list(updated_rules.get("preferred_context_bundles") or [])
            if bundle_descriptor not in preferred_bundles:
                preferred_bundles.append(bundle_descriptor)
            updated_rules["preferred_context_bundles"] = preferred_bundles[:4]
            proposed_change = f"Lean harder into context bundle {bundle_descriptor}."
        else:
            avoid_bundles = list(updated_rules.get("avoid_context_bundles") or [])
            if bundle_descriptor not in avoid_bundles:
                avoid_bundles.append(bundle_descriptor)
            updated_rules["avoid_context_bundles"] = avoid_bundles[:4]
            proposed_change = f"Explicitly avoid context bundle {bundle_descriptor}."
        updated_parameters["context_rule_variant_count"] = int(updated_parameters.get("context_rule_variant_count", 0)) + 1
        updated_parameters["last_context_rule_variant"] = {
            "rule_id": context_rule.id,
            "descriptor": bundle_descriptor,
            "action_type": context_rule.action_type,
            "confidence": context_rule.confidence,
        }
        hypothesis, compact_note = self._build_variant_hypothesis(
            source_version,
            trigger="context_rule_bundle",
            note=f"{bundle_descriptor}: {context_rule.rationale}",
        )
        self._record_lineage_metadata(
            updated_parameters,
            source_version=source_version,
            trigger="context_rule_bundle",
            note=compact_note,
        )

        candidate_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=hypothesis,
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
            change_reason=f"Forked candidate from learned context bundle {bundle_descriptor}.",
            proposed_change=proposed_change,
            change_summary={
                "decision": "fork_variant_from_context_rule",
                "context_rule_id": context_rule.id,
                "candidate_version_id": candidate_version.id,
                "feature_scope": context_rule.feature_scope,
                "feature_key": context_rule.feature_key,
                "feature_value": context_rule.feature_value,
                "action_type": context_rule.action_type,
                "confidence": context_rule.confidence,
                "validation_required": True,
            },
        )

        self.journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="strategy_context_variant",
                strategy_id=strategy.id,
                strategy_version_id=candidate_version.id,
                observations={
                    "context_rule_id": context_rule.id,
                    "candidate_version_id": candidate_version.id,
                    "descriptor": bundle_descriptor,
                    "action_type": context_rule.action_type,
                    "confidence": context_rule.confidence,
                },
                reasoning=context_rule.rationale,
                decision="create_candidate_variant",
                lessons="Learned context bundles should be isolated in candidate variants before activation.",
            ),
        )
        self.memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope=f"strategy:{strategy.id}",
                key=f"context-fork:{change_event.id}",
                content=(
                    f"Candidate strategy version {candidate_version.id} forked from version {source_version.id} "
                    f"using learned context bundle {bundle_descriptor}."
                ),
                meta={
                    "source_version_id": source_version.id,
                    "candidate_version_id": candidate_version.id,
                    "context_rule_id": context_rule.id,
                    "validation_required": True,
                },
                importance=0.78,
            ),
        )
        return {
            "strategy_id": strategy.id,
            "source_version_id": source_version.id,
            "new_version_id": candidate_version.id,
            "change_event_id": change_event.id,
            "activation_event_id": None,
            "trigger": "context_rule_bundle",
            "validation_required": True,
        }

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
        hypothesis, compact_note = self._build_variant_hypothesis(
            source_version,
            trigger="failure_pattern",
            note=(
                f"{pattern.failure_mode}: "
                f"{pattern.recommended_action or 'tighten setup quality.'}"
            ),
        )
        self._record_lineage_metadata(
            updated_parameters,
            source_version=source_version,
            trigger="failure_pattern",
            note=compact_note,
        )
        context_bundles = self._apply_context_bundles_to_version(
            session,
            strategy_id=strategy.id,
            source_version_id=source_version.id,
            updated_rules=updated_rules,
            updated_parameters=updated_parameters,
        )

        candidate_version = self.strategy_repository.create_version(
            session,
            strategy.id,
            StrategyVersionCreate(
                hypothesis=hypothesis,
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
                "validation_required": True,
                "context_bundles": context_bundles,
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
                    "context_bundles": context_bundles,
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
                    "validation_required": True,
                    "context_bundles": context_bundles,
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

    @staticmethod
    def _coerce_datetime(value) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value:
            normalized = value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        return None

    @staticmethod
    def _event_trigger(event) -> str | None:
        summary = event.change_summary if isinstance(getattr(event, "change_summary", None), dict) else {}
        trigger = summary.get("trigger")
        if isinstance(trigger, str) and trigger:
            return trigger
        reason = str(getattr(event, "change_reason", "") or "").lower()
        if "successful pattern" in reason:
            return "success_pattern"
        if "context bundle" in reason:
            return "context_rule_bundle"
        return None

    def _latest_change_event_for_source(
        self,
        latest_events: list,
        *,
        strategy_id: int,
        source_version_id: int,
        trigger: str,
    ):
        for event in latest_events:
            if event.strategy_id != strategy_id or event.source_version_id != source_version_id:
                continue
            if self._event_trigger(event) == trigger:
                return event
        return None

    def evolve_from_success_patterns(self, session: Session, excluded_strategy_ids: set[int] | None = None) -> dict:
        excluded_strategy_ids = excluded_strategy_ids or set()
        results = []
        skipped = 0
        generated_strategy_ids = set(excluded_strategy_ids)
        latest_events = self.evolution_service.repository.list_change_events(session)

        for candidate in self._find_success_candidates(session):
            if self._should_skip_strategy_generation(
                session,
                strategy_id=candidate["strategy_id"],
                excluded_strategy_ids=generated_strategy_ids,
                latest_events=latest_events,
                source_version_id=candidate["source_version_id"],
                trigger="success_pattern",
                latest_trade_at=candidate["latest_trade_at"],
                trade_count=candidate["trade_count"],
            ):
                skipped += 1
                continue
            strategy = session.get(Strategy, candidate["strategy_id"])
            if strategy is None or strategy.current_version_id is None:
                skipped += 1
                continue

            result = self.evolution_service.evolve_from_success_pattern(
                session,
                strategy_id=strategy.id,
                source_version_id=strategy.current_version_id,
                success_summary=candidate,
            )
            results.append(result)
            generated_strategy_ids.add(candidate["strategy_id"])

        context_results = self._generate_context_rule_variants(
            session,
            excluded_strategy_ids=generated_strategy_ids,
            latest_events=latest_events,
        )
        results.extend(context_results["results"])
        skipped += context_results["skipped_candidates"]

        return {
            "generated_variants": len(results),
            "skipped_candidates": skipped,
            "results": results,
        }

    def _generate_context_rule_variants(
        self,
        session: Session,
        *,
        excluded_strategy_ids: set[int],
        latest_events: list,
    ) -> dict:
        results: list[dict] = []
        skipped = 0
        for candidate in self._find_context_rule_candidates(session):
            if self._should_skip_strategy_generation(
                session,
                strategy_id=candidate["strategy_id"],
                excluded_strategy_ids=excluded_strategy_ids,
                latest_events=latest_events,
            ):
                skipped += 1
                continue
            if any(
                event.source_version_id == candidate["source_version_id"]
                and isinstance(getattr(event, "change_summary", None), dict)
                and event.change_summary.get("context_rule_id") == candidate["context_rule_id"]
                for event in latest_events[:10]
            ):
                skipped += 1
                continue
            result = self.evolution_service.fork_variant_from_context_rule(
                session,
                strategy_id=candidate["strategy_id"],
                source_version_id=candidate["source_version_id"],
                context_rule=candidate["context_rule"],
            )
            if result is None:
                skipped += 1
                continue
            results.append(result)
            excluded_strategy_ids.add(candidate["strategy_id"])
        return {"results": results, "skipped_candidates": skipped}

    def _should_skip_strategy_generation(
        self,
        session: Session,
        *,
        strategy_id: int,
        excluded_strategy_ids: set[int],
        latest_events: list,
        source_version_id: int | None = None,
        trigger: str | None = None,
        latest_trade_at=None,
        trade_count: int | None = None,
    ) -> bool:
        if strategy_id in excluded_strategy_ids:
            return True
        strategy = session.get(Strategy, strategy_id)
        if strategy is None or strategy.current_version_id is None:
            return True
        if trigger == "success_pattern" and source_version_id is not None:
            latest_success_event = self._latest_change_event_for_source(
                latest_events,
                strategy_id=strategy.id,
                source_version_id=source_version_id,
                trigger=trigger,
            )
            if latest_success_event is not None:
                event_summary = (
                    latest_success_event.change_summary
                    if isinstance(getattr(latest_success_event, "change_summary", None), dict)
                    else {}
                )
                previous_trade_count = int(event_summary.get("trade_count") or 0)
                latest_trade_dt = self._coerce_datetime(latest_trade_at)
                latest_event_dt = self._coerce_datetime(getattr(latest_success_event, "created_at", None))
                if latest_trade_dt is None:
                    return True
                if latest_event_dt is not None and latest_event_dt >= latest_trade_dt and (
                    trade_count is None or trade_count <= previous_trade_count
                ):
                    return True
        return any(
            event.strategy_id == strategy.id and event.source_version_id == strategy.current_version_id
            for event in latest_events[:5]
        )

    @staticmethod
    def _find_success_candidates(session: Session) -> list[dict]:
        statement = (
            select(
                Position.strategy_version_id,
                func.count(Position.id).label("trade_count"),
                func.avg(Position.pnl_pct).label("avg_pnl_pct"),
                func.avg(Position.max_drawdown_pct).label("avg_drawdown_pct"),
                func.max(Position.exit_date).label("latest_trade_at"),
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
            latest_trade_at = row[4]
            if trade_count < 2 or avg_pnl_pct < 3:
                continue

            strategy_version = session.get(StrategyVersion, strategy_version_id)
            if strategy_version is None or strategy_version.lifecycle_stage != "active":
                continue

            candidates.append(
                {
                    "strategy_id": strategy_version.strategy_id,
                    "source_version_id": strategy_version_id,
                    "trade_count": trade_count,
                    "avg_pnl_pct": round(avg_pnl_pct, 2),
                    "avg_drawdown_pct": round(avg_drawdown_pct, 2),
                    "latest_trade_at": latest_trade_at,
                    "trigger": "success_pattern",
                }
            )
        return candidates

    @staticmethod
    def _find_context_rule_candidates(session: Session) -> list[dict]:
        rules = list(
            session.scalars(
                select(StrategyContextRule).where(
                    StrategyContextRule.status == "active",
                    StrategyContextRule.feature_scope == "combo",
                    StrategyContextRule.confidence.is_not(None),
                    StrategyContextRule.confidence >= 0.45,
                )
            ).all()
        )
        rules.sort(key=lambda rule: (-(float(rule.confidence or 0.0)), rule.id))

        candidates: list[dict] = []
        for rule in rules:
            if rule.strategy_version_id is None:
                continue
            strategy_version = session.get(StrategyVersion, rule.strategy_version_id)
            if strategy_version is None or strategy_version.lifecycle_stage != "active":
                continue
            evidence_payload = rule.evidence_payload if isinstance(rule.evidence_payload, dict) else {}
            sample_size = int(evidence_payload.get("sample_size") or 0)
            if sample_size < 3:
                continue
            candidates.append(
                {
                    "strategy_id": strategy_version.strategy_id,
                    "source_version_id": strategy_version.id,
                    "context_rule_id": rule.id,
                    "context_rule": rule,
                    "trigger": "context_rule_bundle",
                }
            )
        return candidates


class StrategyMaintenanceService:
    def __init__(
        self,
        strategy_repository: StrategyRepository | None = None,
        evolution_service: StrategyEvolutionService | None = None,
    ) -> None:
        self.strategy_repository = strategy_repository or StrategyRepository()
        self.evolution_service = evolution_service or StrategyEvolutionService()

    def compact_historical_hypotheses(
        self,
        session: Session,
        *,
        dry_run: bool = True,
        keep_recent: int = 5,
        max_chars: int = StrategyEvolutionService.COMPACTED_HYPOTHESIS_MAX_CHARS,
    ) -> dict:
        inspected_versions = 0
        compacted_versions = 0
        bytes_before = 0
        bytes_after = 0
        compacted_ids: list[int] = []

        strategies = list(session.execute(select(Strategy.id, Strategy.current_version_id)).all())
        for strategy_id, current_version_id in strategies:
            versions = list(
                session.query(StrategyVersion)
                .filter(StrategyVersion.strategy_id == strategy_id)
                .order_by(StrategyVersion.version.desc(), StrategyVersion.id.desc())
                .all()
            )
            protected_ids = {current_version_id}
            protected_ids.update(version.id for version in versions[: max(keep_recent, 0)])

            for version in versions:
                inspected_versions += 1
                if version.id in protected_ids or version.is_baseline or version.lifecycle_stage == "candidate":
                    continue
                original_hypothesis = str(version.hypothesis or "")
                if len(original_hypothesis) <= max_chars:
                    continue

                compacted_hypothesis = self._build_compacted_hypothesis(version, max_chars=max_chars)
                if compacted_hypothesis == original_hypothesis:
                    continue

                compacted_versions += 1
                bytes_before += len(original_hypothesis)
                bytes_after += len(compacted_hypothesis)
                compacted_ids.append(version.id)

                if dry_run:
                    continue

                parameters = dict(version.parameters or {})
                parameters["hypothesis_compaction_version"] = 1
                parameters["hypothesis_compacted_from_chars"] = len(original_hypothesis)
                parameters["hypothesis_compacted_at"] = datetime.now(timezone.utc).isoformat()
                version.hypothesis = compacted_hypothesis
                version.parameters = parameters

        if not dry_run and compacted_versions:
            session.commit()

        return {
            "dry_run": dry_run,
            "keep_recent": keep_recent,
            "max_chars": max_chars,
            "inspected_versions": inspected_versions,
            "compacted_versions": compacted_versions,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved_estimate": max(bytes_before - bytes_after, 0),
            "compacted_version_ids": compacted_ids,
        }

    def _build_compacted_hypothesis(self, version: StrategyVersion, *, max_chars: int) -> str:
        parameters = version.parameters if isinstance(version.parameters, dict) else {}
        base_hypothesis = parameters.get("base_hypothesis") or self.evolution_service._extract_base_hypothesis(version)
        trigger = str(parameters.get("evolution_trigger") or version.lifecycle_stage or "historical_variant").strip()
        note = str(parameters.get("evolution_note") or "").strip()
        if not note:
            chunks = [chunk.strip() for chunk in str(version.hypothesis or "").split("\n\n") if chunk.strip()]
            if len(chunks) > 1:
                note = chunks[-1]

        compacted = (
            f"{self.evolution_service._truncate_text(base_hypothesis, max(max_chars // 2, 120))}\n\n"
            f"Historical variant [{trigger}] compacted for storage efficiency."
        )
        if note:
            remaining = max_chars - len(compacted) - len("\nNote: ")
            if remaining > 24:
                compacted = (
                    f"{compacted}\nNote: "
                    f"{self.evolution_service._truncate_text(self.evolution_service._normalize_text(note), remaining)}"
                )
        return self.evolution_service._truncate_text(compacted, max_chars)
