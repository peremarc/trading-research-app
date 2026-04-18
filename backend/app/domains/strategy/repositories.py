from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.exceptions import DuplicateResourceError, IntegrityConstraintError
from app.db.models.candidate_validation_snapshot import CandidateValidationSnapshot
from app.db.models.hypothesis import Hypothesis
from app.db.models.screener import Screener, ScreenerVersion
from app.db.models.signal_definition import SignalDefinition
from app.db.models.setup import Setup
from app.db.models.strategy import Strategy, StrategyVersion
from app.db.models.strategy_evolution import StrategyActivationEvent, StrategyChangeEvent
from app.db.models.strategy_scorecard import StrategyScorecard
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.strategy.schemas import (
    HypothesisCreate,
    SignalDefinitionCreate,
    ScreenerCreate,
    ScreenerVersionCreate,
    SetupCreate,
    StrategyCreate,
    StrategyVersionCreate,
    WatchlistCreate,
    WatchlistItemCreate,
)


class CandidateValidationSnapshotRepository:
    def create(self, session: Session, payload: dict) -> CandidateValidationSnapshot:
        snapshot = CandidateValidationSnapshot(**payload)
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot

    def list_latest(self, session: Session) -> list[CandidateValidationSnapshot]:
        statement = select(CandidateValidationSnapshot).order_by(
            CandidateValidationSnapshot.strategy_version_id.asc(),
            CandidateValidationSnapshot.generated_at.desc(),
            CandidateValidationSnapshot.id.desc(),
        )
        snapshots = list(session.scalars(statement).all())
        latest: dict[int, CandidateValidationSnapshot] = {}
        for snapshot in snapshots:
            latest.setdefault(snapshot.strategy_version_id, snapshot)
        return list(latest.values())

    def list_latest_for_strategy(self, session: Session, strategy_id: int) -> list[CandidateValidationSnapshot]:
        snapshots = [snapshot for snapshot in self.list_latest(session) if snapshot.strategy_id == strategy_id]
        snapshots.sort(key=lambda snapshot: snapshot.generated_at, reverse=True)
        return snapshots

    def list_for_strategy(self, session: Session, strategy_id: int) -> list[CandidateValidationSnapshot]:
        statement = (
            select(CandidateValidationSnapshot)
            .where(CandidateValidationSnapshot.strategy_id == strategy_id)
            .order_by(
                CandidateValidationSnapshot.generated_at.desc(),
                CandidateValidationSnapshot.id.desc(),
            )
        )
        return list(session.scalars(statement).all())


class HypothesisRepository:
    def list(self, session: Session) -> list[Hypothesis]:
        statement = select(Hypothesis).order_by(Hypothesis.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: HypothesisCreate) -> Hypothesis:
        hypothesis = Hypothesis(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            proposition=payload.proposition,
            market=payload.market,
            horizon=payload.horizon,
            bias=payload.bias,
            success_criteria=payload.success_criteria,
            status=payload.status,
            version=payload.version,
        )
        try:
            session.add(hypothesis)
            session.commit()
            session.refresh(hypothesis)
            return hypothesis
        except IntegrityError as exc:
            session.rollback()
            self._raise_integrity_error(payload.code, exc)

    @staticmethod
    def _raise_integrity_error(code: str, exc: IntegrityError) -> None:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        if "unique" in message and "hypotheses.code" in message:
            raise DuplicateResourceError(f"Hypothesis with code '{code}' already exists") from exc
        raise IntegrityConstraintError("Hypothesis could not be saved because of a database constraint") from exc


class SetupRepository:
    def list(self, session: Session) -> list[Setup]:
        statement = select(Setup).order_by(Setup.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: SetupCreate) -> Setup:
        setup = Setup(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            hypothesis_id=payload.hypothesis_id,
            strategy_id=payload.strategy_id,
            timeframe=payload.timeframe,
            ideal_context=payload.ideal_context,
            conditions=payload.conditions,
            parameters=payload.parameters,
            status=payload.status,
            version=payload.version,
        )
        try:
            session.add(setup)
            session.commit()
            session.refresh(setup)
            return setup
        except IntegrityError as exc:
            session.rollback()
            self._raise_integrity_error(payload.code, exc)

    @staticmethod
    def _raise_integrity_error(code: str, exc: IntegrityError) -> None:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        if "unique" in message and "setups.code" in message:
            raise DuplicateResourceError(f"Setup with code '{code}' already exists") from exc
        raise IntegrityConstraintError("Setup could not be saved because of a database constraint") from exc


class SignalDefinitionRepository:
    def list(self, session: Session) -> list[SignalDefinition]:
        statement = select(SignalDefinition).order_by(SignalDefinition.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: SignalDefinitionCreate) -> SignalDefinition:
        signal_definition = SignalDefinition(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            hypothesis_id=payload.hypothesis_id,
            strategy_id=payload.strategy_id,
            setup_id=payload.setup_id,
            signal_kind=payload.signal_kind,
            definition=payload.definition,
            parameters=payload.parameters,
            activation_conditions=payload.activation_conditions,
            intended_usage=payload.intended_usage,
            status=payload.status,
            version=payload.version,
        )
        try:
            session.add(signal_definition)
            session.commit()
            session.refresh(signal_definition)
            return signal_definition
        except IntegrityError as exc:
            session.rollback()
            self._raise_integrity_error(payload.code, exc)

    def find_by_code(self, session: Session, code: str) -> SignalDefinition | None:
        statement = select(SignalDefinition).where(SignalDefinition.code == code)
        return session.scalars(statement).first()

    @staticmethod
    def _raise_integrity_error(code: str, exc: IntegrityError) -> None:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        if "unique" in message and "signal_definitions.code" in message:
            raise DuplicateResourceError(f"Signal definition with code '{code}' already exists") from exc
        raise IntegrityConstraintError("Signal definition could not be saved because of a database constraint") from exc


class StrategyRepository:
    def list(self, session: Session) -> list[Strategy]:
        statement = select(Strategy).options(selectinload(Strategy.versions)).order_by(Strategy.created_at.desc())
        return list(session.scalars(statement).all())

    def get(self, session: Session, strategy_id: int) -> Strategy | None:
        statement = select(Strategy).options(selectinload(Strategy.versions)).where(Strategy.id == strategy_id)
        return session.scalars(statement).first()

    def create(self, session: Session, payload: StrategyCreate) -> Strategy:
        strategy = Strategy(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            hypothesis_id=payload.hypothesis_id,
            market=payload.market,
            horizon=payload.horizon,
            bias=payload.bias,
            status=payload.status,
        )
        try:
            session.add(strategy)
            session.flush()

            version = self._build_version(strategy.id, 1, payload.initial_version)
            session.add(version)
            session.flush()

            strategy.current_version_id = version.id
            session.commit()
            session.refresh(strategy)
            return self.get(session, strategy.id) or strategy
        except IntegrityError as exc:
            session.rollback()
            self._raise_integrity_error(payload.code, exc)

    def create_version(self, session: Session, strategy_id: int, payload: StrategyVersionCreate) -> StrategyVersion:
        strategy = self.get(session, strategy_id)
        if strategy is None:
            raise ValueError("Strategy not found")

        next_version = max((item.version for item in strategy.versions), default=0) + 1
        version = self._build_version(strategy_id, next_version, payload)
        session.add(version)
        session.flush()

        strategy.current_version_id = version.id
        session.commit()
        session.refresh(version)
        return version

    @staticmethod
    def _build_version(strategy_id: int, version_number: int, payload: StrategyVersionCreate) -> StrategyVersion:
        lifecycle_stage = payload.lifecycle_stage
        if lifecycle_stage is None:
            if version_number == 1 and payload.is_baseline:
                lifecycle_stage = "active"
            elif payload.state == "draft":
                lifecycle_stage = "candidate"
            else:
                lifecycle_stage = "approved"
        return StrategyVersion(
            strategy_id=strategy_id,
            version=version_number,
            hypothesis=payload.hypothesis,
            general_rules=payload.general_rules,
            parameters=payload.parameters,
            state=payload.state,
            lifecycle_stage=lifecycle_stage,
            is_baseline=payload.is_baseline,
        )

    @staticmethod
    def _raise_integrity_error(code: str, exc: IntegrityError) -> None:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        if "unique" in message and "strategies.code" in message:
            raise DuplicateResourceError(f"Strategy with code '{code}' already exists") from exc
        raise IntegrityConstraintError("Strategy could not be saved because of a database constraint") from exc


class ScreenerRepository:
    def list(self, session: Session) -> list[Screener]:
        statement = select(Screener).options(selectinload(Screener.versions)).order_by(Screener.created_at.desc())
        return list(session.scalars(statement).all())

    def get(self, session: Session, screener_id: int) -> Screener | None:
        statement = select(Screener).options(selectinload(Screener.versions)).where(Screener.id == screener_id)
        return session.scalars(statement).first()

    def create(self, session: Session, payload: ScreenerCreate) -> Screener:
        screener = Screener(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            strategy_id=payload.strategy_id,
        )
        try:
            session.add(screener)
            session.flush()

            version = self._build_version(screener.id, 1, payload.initial_version)
            session.add(version)
            session.flush()

            screener.current_version_id = version.id
            session.commit()
            session.refresh(screener)
            return self.get(session, screener.id) or screener
        except IntegrityError as exc:
            session.rollback()
            self._raise_integrity_error(payload.code, exc)

    def create_version(self, session: Session, screener_id: int, payload: ScreenerVersionCreate) -> ScreenerVersion:
        screener = self.get(session, screener_id)
        if screener is None:
            raise ValueError("Screener not found")

        next_version = max((item.version for item in screener.versions), default=0) + 1
        version = self._build_version(screener_id, next_version, payload)
        session.add(version)
        session.flush()

        screener.current_version_id = version.id
        session.commit()
        session.refresh(version)
        return version

    @staticmethod
    def _build_version(screener_id: int, version_number: int, payload: ScreenerVersionCreate) -> ScreenerVersion:
        return ScreenerVersion(
            screener_id=screener_id,
            version=version_number,
            definition=payload.definition,
            universe=payload.universe,
            timeframe=payload.timeframe,
            sorting=payload.sorting,
            status=payload.status,
        )

    @staticmethod
    def _raise_integrity_error(code: str, exc: IntegrityError) -> None:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        if "unique" in message and "screeners.code" in message:
            raise DuplicateResourceError(f"Screener with code '{code}' already exists") from exc
        raise IntegrityConstraintError("Screener could not be saved because of a database constraint") from exc


class WatchlistRepository:
    def list(self, session: Session) -> list[Watchlist]:
        statement = select(Watchlist).options(selectinload(Watchlist.items)).order_by(Watchlist.created_at.desc())
        return list(session.scalars(statement).all())

    def create(self, session: Session, payload: WatchlistCreate) -> Watchlist:
        watchlist = Watchlist(
            code=payload.code,
            name=payload.name,
            hypothesis_id=payload.hypothesis_id,
            strategy_id=payload.strategy_id,
            setup_id=payload.setup_id,
            hypothesis=payload.hypothesis,
            status=payload.status,
        )
        try:
            session.add(watchlist)
            session.commit()
            session.refresh(watchlist)
            return self.get(session, watchlist.id) or watchlist
        except IntegrityError as exc:
            session.rollback()
            self._raise_integrity_error(payload.code, exc)

    def get(self, session: Session, watchlist_id: int) -> Watchlist | None:
        statement = select(Watchlist).options(selectinload(Watchlist.items)).where(Watchlist.id == watchlist_id)
        return session.scalars(statement).first()

    def add_item(self, session: Session, watchlist_id: int, payload: WatchlistItemCreate) -> WatchlistItem:
        watchlist = self.get(session, watchlist_id)
        if watchlist is None:
            raise ValueError("Watchlist not found")

        item = WatchlistItem(
            watchlist_id=watchlist_id,
            ticker=payload.ticker,
            setup_id=payload.setup_id,
            strategy_hypothesis=payload.strategy_hypothesis,
            score=payload.score,
            reason=payload.reason,
            key_metrics=payload.key_metrics,
            state=payload.state,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    @staticmethod
    def _raise_integrity_error(code: str, exc: IntegrityError) -> None:
        message = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
        if "unique" in message and "watchlists.code" in message:
            raise DuplicateResourceError(f"Watchlist with code '{code}' already exists") from exc
        raise IntegrityConstraintError("Watchlist could not be saved because of a database constraint") from exc


class StrategyScorecardRepository:
    def create(self, session: Session, payload: dict) -> StrategyScorecard:
        scorecard = StrategyScorecard(**payload)
        session.add(scorecard)
        session.commit()
        session.refresh(scorecard)
        return scorecard

    def list_latest(self, session: Session) -> list[StrategyScorecard]:
        statement = select(StrategyScorecard).order_by(
            StrategyScorecard.strategy_id.asc(),
            StrategyScorecard.generated_at.desc(),
        )
        scorecards = list(session.scalars(statement).all())
        latest: dict[int, StrategyScorecard] = {}
        for scorecard in scorecards:
            latest.setdefault(scorecard.strategy_id, scorecard)
        return list(latest.values())

    def get_latest_for_strategy(self, session: Session, strategy_id: int) -> StrategyScorecard | None:
        statement = (
            select(StrategyScorecard)
            .where(StrategyScorecard.strategy_id == strategy_id)
            .order_by(StrategyScorecard.generated_at.desc())
        )
        return session.scalars(statement).first()


class StrategyEvolutionRepository:
    def get_strategy(self, session: Session, strategy_id: int) -> Strategy | None:
        return session.get(Strategy, strategy_id)

    def get_strategy_version(self, session: Session, version_id: int) -> StrategyVersion | None:
        return session.get(StrategyVersion, version_id)

    def create_change_event(
        self,
        session: Session,
        *,
        strategy_id: int,
        source_version_id: int | None,
        new_version_id: int | None,
        trade_review_id: int | None,
        change_reason: str,
        proposed_change: str | None,
        change_summary: dict,
    ) -> StrategyChangeEvent:
        event = StrategyChangeEvent(
            strategy_id=strategy_id,
            source_version_id=source_version_id,
            new_version_id=new_version_id,
            trade_review_id=trade_review_id,
            change_reason=change_reason,
            proposed_change=proposed_change,
            change_summary=change_summary,
            applied_automatically=True,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def create_activation_event(
        self,
        session: Session,
        *,
        strategy_id: int,
        activated_version_id: int,
        previous_version_id: int | None,
        activation_reason: str,
    ) -> StrategyActivationEvent:
        event = StrategyActivationEvent(
            strategy_id=strategy_id,
            activated_version_id=activated_version_id,
            previous_version_id=previous_version_id,
            activation_reason=activation_reason,
            activated_automatically=True,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def list_change_events(self, session: Session) -> list[StrategyChangeEvent]:
        statement = select(StrategyChangeEvent).order_by(StrategyChangeEvent.created_at.desc())
        return list(session.scalars(statement).all())

    def list_activation_events(self, session: Session) -> list[StrategyActivationEvent]:
        statement = select(StrategyActivationEvent).order_by(StrategyActivationEvent.created_at.desc())
        return list(session.scalars(statement).all())
