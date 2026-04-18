from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.system_event import SystemEvent
from app.domains.learning.schemas import DailyPlanRequest

if TYPE_CHECKING:
    from app.domains.learning.services import OrchestratorService


class EventLogService:
    _PRIMARY_PHASE_BY_EVENT_TYPE = {
        "hypothesis.created": "plan",
        "setup.created": "plan",
        "signal_definition.created": "plan",
        "strategy.created": "plan",
        "strategy.version_created": "plan",
        "screener.created": "plan",
        "screener.version_created": "plan",
        "watchlist.created": "plan",
        "position.closed": "check",
        "trade_review.created": "act",
    }
    _AUTO_SETTLED_RECORD_POLICIES: dict[tuple[str, str], dict[str, str | None]] = {
        (
            "watchlist_item.added",
            "opportunity_discovery",
        ): {
            "status": "ignored",
            "phase": None,
            "note": "Discovery-generated watchlist items do not trigger standalone PDCA dispatch.",
        },
        (
            "trade_signal.created",
            "orchestrator_do",
        ): {
            "status": "processed",
            "phase": "do",
            "note": "Generated inside the orchestrator DO phase; no standalone dispatch is required.",
        },
        (
            "trade_signal.status_updated",
            "orchestrator_do",
        ): {
            "status": "processed",
            "phase": "do",
            "note": "Signal status update was already satisfied inside the orchestrator DO phase.",
        },
        (
            "position.opened",
            "orchestrator_do",
        ): {
            "status": "processed",
            "phase": "do",
            "note": "Position opening was already satisfied inside the orchestrator DO phase.",
        },
        (
            "position.managed",
            "orchestrator_do",
        ): {
            "status": "processed",
            "phase": "do",
            "note": "Position management was already satisfied inside the orchestrator DO phase.",
        },
    }
    _PHASE_ORDER = ("plan", "do", "check", "act")
    _PHASE_INDEX = {
        "plan": 0,
        "do": 1,
        "check": 2,
        "act": 3,
    }

    def list_events(
        self,
        session: Session,
        *,
        limit: int = 50,
        event_type: str | None = None,
        entity_type: str | None = None,
        pdca_phase_hint: str | None = None,
        dispatch_status: str | None = None,
    ) -> list[SystemEvent]:
        statement = select(SystemEvent).order_by(SystemEvent.created_at.desc(), SystemEvent.id.desc()).limit(limit)
        if event_type:
            statement = statement.where(SystemEvent.event_type == event_type)
        if entity_type:
            statement = statement.where(SystemEvent.entity_type == entity_type)
        if pdca_phase_hint:
            statement = statement.where(SystemEvent.pdca_phase_hint == pdca_phase_hint)
        if dispatch_status:
            statement = statement.where(SystemEvent.dispatch_status == dispatch_status)
        return list(session.scalars(statement).all())

    def list_pending_events(self, session: Session, *, limit: int = 50) -> list[SystemEvent]:
        statement = (
            select(SystemEvent)
            .where(SystemEvent.dispatch_status == "pending")
            .order_by(SystemEvent.created_at.asc(), SystemEvent.id.asc())
            .limit(limit)
        )
        return list(session.scalars(statement).all())

    def record(
        self,
        session: Session,
        *,
        event_type: str,
        entity_type: str,
        entity_id: int | None = None,
        source: str = "system",
        pdca_phase_hint: str | None = None,
        payload: dict | None = None,
    ) -> SystemEvent:
        settlement = self._resolve_record_settlement(event_type=event_type, source=source)
        processed_at = datetime.now(timezone.utc) if settlement is not None else None
        event = SystemEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            source=source,
            pdca_phase_hint=pdca_phase_hint,
            dispatch_status=settlement["status"] if settlement is not None else "pending",
            dispatched_phase=settlement["phase"] if settlement is not None else None,
            dispatch_note=settlement["note"] if settlement is not None else None,
            processed_at=processed_at,
            payload=dict(payload or {}),
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        return event

    def dispatch_pending(
        self,
        session: Session,
        *,
        orchestrator_service: OrchestratorService,
        cycle_date: date | None = None,
        limit: int = 50,
        on_phase_start: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        pending_events = self.list_pending_events(session, limit=limit)
        result: dict[str, Any] = {
            "pending_events_seen": len(pending_events),
            "processed_events": 0,
            "ignored_events": 0,
            "failed_events": 0,
            "phases_run": [],
            "processed_event_ids": [],
            "ignored_event_ids": [],
        }
        if not pending_events:
            return result

        dispatch_started_at = datetime.now(timezone.utc)
        max_seen_event_id = max(event.id for event in pending_events)
        claimed_events: list[SystemEvent] = []
        initially_claimed_events: list[SystemEvent] = []
        primary_phases: set[str] = set()

        for event in pending_events:
            primary_phase = self._resolve_primary_phase(event)
            if primary_phase is None:
                self._mark_event(
                    event,
                    status="ignored",
                    note="No event-driven PDCA mapping is defined for this event type yet.",
                    processed_at=dispatch_started_at,
                )
                result["ignored_events"] += 1
                result["ignored_event_ids"].append(event.id)
                continue

            self._mark_event(
                event,
                status="processing",
                phase=primary_phase,
                note="Claimed for event-driven PDCA dispatch.",
                dispatched_at=dispatch_started_at,
            )
            claimed_events.append(event)
            initially_claimed_events.append(event)
            primary_phases.add(primary_phase)

        session.commit()

        phases_to_run = self._build_phase_sequence(primary_phases)
        result["phases_run"] = phases_to_run
        if not phases_to_run:
            return result

        try:
            phase_cursor = 0
            while phase_cursor < len(phases_to_run):
                phase = phases_to_run[phase_cursor]
                if on_phase_start is not None:
                    on_phase_start(phase)
                self._run_phase(
                    session,
                    orchestrator_service=orchestrator_service,
                    phase=phase,
                    cycle_date=cycle_date or date.today(),
                    triggering_events=claimed_events,
                )
                max_seen_event_id, follow_up_phases = self._claim_follow_up_events(
                    session,
                    min_event_id=max_seen_event_id,
                    claimed_events=claimed_events,
                    current_phase=phase,
                    planned_phases=set(phases_to_run),
                    dispatch_started_at=dispatch_started_at,
                    result=result,
                )
                if follow_up_phases:
                    phases_to_run = self._build_phase_sequence(set(phases_to_run) | follow_up_phases)
                    result["phases_run"] = phases_to_run
                phase_cursor += 1
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            for event in claimed_events:
                self._mark_event(
                    event,
                    status="failed",
                    note=f"Event-driven dispatch failed: {exc}",
                    processed_at=failed_at,
                )
            session.commit()
            result["failed_events"] = len(claimed_events)
            raise

        completed_at = datetime.now(timezone.utc)
        note = f"Triggered phases: {', '.join(phases_to_run)}."
        for event in claimed_events:
            self._mark_event(
                event,
                status="processed",
                note=note,
                processed_at=completed_at,
            )
        session.commit()

        result["processed_events"] = len(initially_claimed_events)
        result["processed_event_ids"] = [event.id for event in initially_claimed_events]
        return result

    def _resolve_primary_phase(self, event: SystemEvent) -> str | None:
        if event.event_type == "watchlist_item.added":
            if event.source == "strategy_catalog":
                return "do"
            if event.source == "system_seed":
                return "plan"
            return None
        return self._PRIMARY_PHASE_BY_EVENT_TYPE.get(event.event_type)

    def _resolve_record_settlement(self, *, event_type: str, source: str) -> dict[str, str | None] | None:
        return self._AUTO_SETTLED_RECORD_POLICIES.get((event_type, source))

    def _build_phase_sequence(self, primary_phases: set[str]) -> list[str]:
        if not primary_phases:
            return []

        phases = [phase for phase in self._PHASE_ORDER if phase in primary_phases]
        if "check" in primary_phases and "act" not in phases:
            phases.append("act")
        return phases

    def _claim_follow_up_events(
        self,
        session: Session,
        *,
        min_event_id: int,
        claimed_events: list[SystemEvent],
        current_phase: str,
        planned_phases: set[str],
        dispatch_started_at: datetime,
        result: dict[str, Any],
    ) -> tuple[int, set[str]]:
        follow_up_events = list(
            session.scalars(
                select(SystemEvent)
                .where(SystemEvent.dispatch_status == "pending", SystemEvent.id > min_event_id)
                .order_by(SystemEvent.id.asc())
            ).all()
        )
        if not follow_up_events:
            return min_event_id, set()

        current_phase_index = self._PHASE_INDEX[current_phase]
        next_min_event_id = max(event.id for event in follow_up_events)
        follow_up_phases: set[str] = set()
        claimed_follow_up_ids = {event.id for event in claimed_events}

        for event in follow_up_events:
            primary_phase = self._resolve_primary_phase(event)
            if primary_phase is None:
                self._mark_event(
                    event,
                    status="ignored",
                    note="No event-driven PDCA mapping is defined for this follow-up event type yet.",
                    processed_at=dispatch_started_at,
                )
                result["ignored_events"] += 1
                result["ignored_event_ids"].append(event.id)
                continue

            if self._PHASE_INDEX[primary_phase] <= current_phase_index:
                continue
            if event.id in claimed_follow_up_ids:
                continue

            self._mark_event(
                event,
                status="processing",
                phase=primary_phase,
                note="Claimed as a follow-up event inside the same event-driven PDCA chain.",
                dispatched_at=dispatch_started_at,
            )
            claimed_events.append(event)
            claimed_follow_up_ids.add(event.id)
            if primary_phase not in planned_phases:
                follow_up_phases.add(primary_phase)

        session.commit()
        return next_min_event_id, follow_up_phases

    @staticmethod
    def _mark_event(
        event: SystemEvent,
        *,
        status: str,
        phase: str | None = None,
        note: str | None = None,
        dispatched_at: datetime | None = None,
        processed_at: datetime | None = None,
    ) -> None:
        event.dispatch_status = status
        if phase is not None:
            event.dispatched_phase = phase
        if note is not None:
            event.dispatch_note = note
        if dispatched_at is not None:
            event.dispatched_at = dispatched_at
        if processed_at is not None:
            event.processed_at = processed_at

    def _run_phase(
        self,
        session: Session,
        *,
        orchestrator_service: OrchestratorService,
        phase: str,
        cycle_date: date,
        triggering_events: list[SystemEvent],
    ) -> None:
        if phase == "plan":
            orchestrator_service.plan_daily_cycle(
                session,
                DailyPlanRequest(
                    cycle_date=cycle_date,
                    market_context={
                        "trigger": "event_dispatch",
                        "event_ids": [event.id for event in triggering_events],
                        "event_types": [event.event_type for event in triggering_events],
                    },
                ),
            )
            return
        if phase == "do":
            orchestrator_service.run_do_phase(session)
            return
        if phase == "check":
            orchestrator_service.run_check_phase(session)
            return
        if phase == "act":
            orchestrator_service.run_act_phase(session)
            return
        raise ValueError(f"Unsupported PDCA phase for event dispatch: {phase}")
