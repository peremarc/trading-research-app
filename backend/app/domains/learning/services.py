from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
import unicodedata
import re
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.analysis import AnalysisRun
from app.db.models.candidate_validation_snapshot import CandidateValidationSnapshot
from app.db.models.decision_context import DecisionContextSnapshot
from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.signal import TradeSignal
from app.db.models.signal_definition import SignalDefinition
from app.db.models.strategy import Strategy, StrategyVersion
from app.db.models.strategy_evolution import StrategyActivationEvent, StrategyChangeEvent
from app.db.models.trade_review import TradeReview
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.learning.agent import AIDecisionError, AutonomousTradingAgentService
from app.domains.learning.decisioning import DecisionContextAssemblerService, EntryScoringService, PositionSizingService
from app.domains.learning.relevance import DecisionContextService, FeatureRelevanceService, StrategyContextAdaptationService
from app.domains.learning.world_state import MarketStateService
from app.domains.learning.tools import AgentToolGatewayService
from app.domains.learning.repositories import FailurePatternRepository, JournalRepository, MemoryRepository, PDCACycleRepository
from app.domains.system.market_hours import USMarketHoursService
from app.domains.learning.schemas import (
    AutoReviewBatchResult,
    AutoReviewResult,
    BotChatResponse,
    DailyPlanRequest,
    ExecutionCandidateResult,
    JournalEntryCreate,
    MemoryItemCreate,
    MarketStateSnapshotRead,
    MacroSignalCreate,
    OrchestratorActResponse,
    OrchestratorDoResponse,
    OrchestratorPhaseResponse,
    OrchestratorPlanResponse,
    PDCACycleCreate,
    TickerTraceEventRead,
    TickerTraceRead,
    TickerTraceSummaryRead,
)
from app.domains.market.schemas import AnalysisRunCreate, SignalCreate
from app.domains.execution.schemas import AutoExitBatchResult, TradeReviewCreate
from app.domains.learning.macro import MacroContextService
from app.domains.strategy.schemas import WatchlistCreate, WatchlistItemCreate
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError
from app.providers.web_research import WebResearchError


class JournalService:
    RETENTION_LIMITS: dict[str, int] = {
        "pdca_do": 288,
        "pdca_check": 288,
        "pdca_act": 288,
        "strategy_evolution_success": 160,
        "operator_disagreement": 240,
        "learning_workflow_sync": 240,
        "learning_workflow_sync_failed": 120,
        "learning_workflow_action": 240,
    }

    def __init__(self, repository: JournalRepository | None = None) -> None:
        self.repository = repository or JournalRepository()

    def list_entries(self, session: Session):
        return self.repository.list(session)

    def create_entry(self, session: Session, payload: JournalEntryCreate):
        entry = self.repository.create(session, payload)
        self._apply_retention(session, entry.entry_type)
        return entry

    @classmethod
    def _stale_entry_ids(cls, session: Session, *, entry_type: str, keep_latest: int) -> list[int]:
        if keep_latest <= 0:
            return [
                item_id
                for (item_id,) in session.query(JournalEntry.id)
                .filter(JournalEntry.entry_type == entry_type)
                .all()
            ]
        rows = (
            session.query(JournalEntry.id)
            .filter(JournalEntry.entry_type == entry_type)
            .order_by(JournalEntry.event_time.desc(), JournalEntry.id.desc())
            .offset(keep_latest)
            .all()
        )
        return [item_id for (item_id,) in rows]

    @classmethod
    def _apply_retention(cls, session: Session, entry_type: str) -> int:
        keep_latest = cls.RETENTION_LIMITS.get(entry_type)
        if keep_latest is None:
            return 0
        stale_ids = cls._stale_entry_ids(session, entry_type=entry_type, keep_latest=keep_latest)
        if not stale_ids:
            return 0
        session.query(JournalEntry).filter(JournalEntry.id.in_(stale_ids)).delete(synchronize_session=False)
        session.commit()
        return len(stale_ids)


class TickerDecisionTraceService:
    DEFAULT_LIMIT = 24

    def get_trace(self, session: Session, ticker: str, *, limit: int = DEFAULT_LIMIT) -> TickerTraceRead:
        normalized_ticker = str(ticker or "").strip().upper()
        if not normalized_ticker:
            return TickerTraceRead(
                ticker="",
                summary=TickerTraceSummaryRead(ticker=""),
                events=[],
            )

        event_limit = max(1, min(int(limit), 100))
        signals = list(
            session.scalars(
                select(TradeSignal)
                .where(TradeSignal.ticker == normalized_ticker)
                .order_by(TradeSignal.signal_time.desc(), TradeSignal.created_at.desc(), TradeSignal.id.desc())
                .limit(event_limit)
            ).all()
        )
        journal_entries = list(
            session.scalars(
                select(JournalEntry)
                .where(JournalEntry.ticker == normalized_ticker)
                .order_by(JournalEntry.event_time.desc(), JournalEntry.id.desc())
                .limit(event_limit)
            ).all()
        )
        positions = list(
            session.scalars(
                select(Position)
                .where(Position.ticker == normalized_ticker)
                .order_by(Position.entry_date.desc(), Position.id.desc())
                .limit(event_limit)
            ).all()
        )

        events: list[TickerTraceEventRead] = []
        events.extend(self._build_signal_event(signal) for signal in signals)
        events.extend(self._build_journal_event(entry) for entry in journal_entries)
        for position in positions:
            events.extend(self._build_position_events(position))
        events.sort(key=lambda item: (item.timestamp, item.signal_id or 0, item.position_id or 0, item.journal_id or 0), reverse=True)
        events = events[:event_limit]

        latest_signal = signals[0] if signals else None
        latest_signal_context = self._as_dict(latest_signal.signal_context if latest_signal is not None else None)
        latest_decision_trace = self._as_dict(latest_signal_context.get("decision_trace"))
        latest_guard_results = self._as_dict(latest_signal_context.get("guard_results"))
        latest_ai_overlay = self._as_dict(latest_signal_context.get("ai_overlay"))
        latest_timing_profile = self._as_dict(latest_signal_context.get("timing_profile"))
        latest_skill_context = self._as_dict(latest_signal_context.get("skill_context"))
        latest_timing_stage = self._slowest_timing_stage(latest_timing_profile)
        latest_budget_entry = next(
            (
                entry
                for entry in journal_entries
                if self._extract_context_budget(self._as_dict(entry.observations))
            ),
            None,
        )
        latest_context_budget = self._extract_context_budget(
            self._as_dict(latest_budget_entry.observations) if latest_budget_entry is not None else {}
        )

        summary = TickerTraceSummaryRead(
            ticker=normalized_ticker,
            total_signals=self._count_rows(session, TradeSignal, normalized_ticker),
            total_journal_entries=self._count_rows(session, JournalEntry, normalized_ticker),
            total_positions=self._count_rows(session, Position, normalized_ticker),
            open_positions=self._count_open_positions(session, normalized_ticker),
            latest_signal_id=latest_signal.id if latest_signal is not None else None,
            latest_signal_at=self._signal_timestamp(latest_signal) if latest_signal is not None else None,
            latest_signal_status=latest_signal.status if latest_signal is not None else None,
            latest_signal_type=latest_signal.signal_type if latest_signal is not None else None,
            latest_decision=self._first_non_empty(
                latest_decision_trace.get("final_action"),
                latest_signal.status if latest_signal is not None else None,
            ),
            latest_decision_source=self._first_non_empty(latest_decision_trace.get("decision_source")),
            latest_guard_reason=self._first_guard_reason(latest_guard_results, latest_signal.rejection_reason if latest_signal is not None else None),
            latest_llm_status=self._derive_llm_status(latest_ai_overlay, latest_guard_results),
            latest_llm_provider=self._derive_llm_provider(latest_ai_overlay),
            latest_primary_skill=self._first_non_empty(latest_skill_context.get("primary_skill_code")),
            latest_active_skill_revision=self._latest_active_skill_revision_label(latest_skill_context),
            latest_available_runtime_skill_count=self._runtime_budget_count(latest_context_budget, "runtime_skills", "available_count"),
            latest_loaded_runtime_skill_count=self._runtime_budget_count(latest_context_budget, "runtime_skills", "loaded_count"),
            latest_available_runtime_claim_count=self._runtime_budget_count(latest_context_budget, "runtime_claims", "available_count"),
            latest_loaded_runtime_claim_count=self._runtime_budget_count(latest_context_budget, "runtime_claims", "loaded_count"),
            latest_runtime_budget_truncated=self._runtime_budget_truncated(latest_context_budget),
            latest_score=self._derive_score(latest_signal, latest_signal_context),
            latest_timing_total_ms=self._coerce_float(latest_timing_profile.get("total_ms")),
            latest_timing_slowest_stage=latest_timing_stage[0] if latest_timing_stage is not None else None,
            latest_timing_slowest_stage_ms=latest_timing_stage[1] if latest_timing_stage is not None else None,
        )
        return TickerTraceRead(
            ticker=normalized_ticker,
            summary=summary,
            events=events,
        )

    @staticmethod
    def _count_rows(session: Session, model, ticker: str) -> int:
        return len(
            list(
                session.scalars(
                    select(model.id)
                    .where(model.ticker == ticker)
                ).all()
            )
        )

    @staticmethod
    def _count_open_positions(session: Session, ticker: str) -> int:
        return len(
            list(
                session.scalars(
                    select(Position.id)
                    .where(Position.ticker == ticker, Position.status == "open")
                ).all()
            )
        )

    @classmethod
    def _build_signal_event(cls, signal: TradeSignal) -> TickerTraceEventRead:
        context = cls._as_dict(signal.signal_context)
        decision_trace = cls._as_dict(context.get("decision_trace"))
        guard_results = cls._as_dict(context.get("guard_results"))
        ai_overlay = cls._as_dict(context.get("ai_overlay"))
        skill_context = cls._as_dict(context.get("skill_context"))
        summary = cls._first_non_empty(
            decision_trace.get("final_reason"),
            cls._first_guard_reason(guard_results, signal.rejection_reason),
            cls._skill_summary(skill_context),
            signal.thesis,
            "Signal captured without extra narrative.",
        )
        tags = [
            signal.signal_type or "",
            signal.timeframe or "",
            "blocked" if guard_results.get("blocked") else "",
            decision_trace.get("decision_source") or "",
            f"skill:{skill_context.get('primary_skill_code')}" if skill_context.get("primary_skill_code") else "",
        ]
        details = {
            "signal_type": signal.signal_type,
            "timeframe": signal.timeframe,
            "quality_score": signal.quality_score,
            "decision_trace": decision_trace,
            "guard_results": guard_results,
            "ai_overlay": ai_overlay,
            "skill_context": skill_context,
            "context_budget": cls._extract_context_budget(context),
            "timing_profile": cls._as_dict(context.get("timing_profile")),
            "execution_plan_timing": cls._as_dict(context.get("execution_plan_timing")),
        }
        return TickerTraceEventRead(
            timestamp=cls._signal_timestamp(signal),
            event_kind="signal",
            title=f"Signal #{signal.id} · {signal.signal_type}",
            summary=summary,
            status=signal.status,
            decision=cls._first_non_empty(decision_trace.get("final_action"), signal.status),
            decision_source=cls._first_non_empty(decision_trace.get("decision_source")),
            llm_status=cls._derive_llm_status(ai_overlay, guard_results),
            llm_provider=cls._derive_llm_provider(ai_overlay),
            signal_id=signal.id,
            tags=[tag for tag in tags if tag],
            details=details,
        )

    @classmethod
    def _build_journal_event(cls, entry: JournalEntry) -> TickerTraceEventRead:
        observations = cls._as_dict(entry.observations)
        market_context = cls._as_dict(entry.market_context)
        workflow_id = observations.get("workflow_id") or market_context.get("workflow_id")
        workflow_type = observations.get("workflow_type") or market_context.get("workflow_type")
        resolution_class = observations.get("resolution_class")
        resolution_outcome = observations.get("resolution_outcome")
        workflow_item_type = observations.get("item_type")
        workflow_entity_id = observations.get("entity_id")
        operator_disagreement = cls._as_dict(observations.get("operator_disagreement"))
        skill_context = cls._as_dict(
            observations.get("skill_context")
            or cls._as_dict(observations.get("decision_context")).get("skill_context")
        )
        skill_candidate = cls._as_dict(observations.get("skill_candidate"))
        summary = cls._first_non_empty(
            cls._workflow_summary(
                entry_type=entry.entry_type,
                workflow_type=workflow_type,
                resolution_class=resolution_class,
                outcome=entry.outcome,
            ),
            skill_candidate.get("summary"),
            cls._skill_summary(skill_context),
            entry.reasoning,
            entry.outcome,
            entry.lessons,
            "Journal note recorded.",
        )
        tags = [
            entry.entry_type or "",
            f"strategy:{entry.strategy_id}" if entry.strategy_id else "",
            f"v{entry.strategy_version_id}" if entry.strategy_version_id else "",
            f"skill:{skill_context.get('primary_skill_code')}" if skill_context.get("primary_skill_code") else "",
            f"candidate:{skill_candidate.get('target_skill_code')}" if skill_candidate.get("target_skill_code") else "",
            f"workflow:{workflow_type}" if workflow_type else "",
            f"workflow_id:{workflow_id}" if workflow_id else "",
            f"resolution:{resolution_class}" if resolution_class else "",
            f"disagreement:{operator_disagreement.get('disagreement_type')}" if operator_disagreement.get("disagreement_type") else "",
        ]
        return TickerTraceEventRead(
            timestamp=entry.event_time,
            event_kind="journal",
            title=f"Journal · {entry.entry_type}",
            summary=summary,
            status=entry.decision,
            decision=entry.decision,
            journal_id=entry.id,
            tags=[tag for tag in tags if tag],
            details={
                "market_context": market_context,
                "observations": observations,
                "workflow": {
                    "workflow_id": workflow_id,
                    "workflow_type": workflow_type,
                    "resolution_class": resolution_class,
                    "resolution_outcome": resolution_outcome,
                    "item_type": workflow_item_type,
                    "entity_id": workflow_entity_id,
                },
                "operator_disagreement": operator_disagreement,
                "skill_context": skill_context,
                "context_budget": cls._extract_context_budget(observations),
                "skill_candidate": skill_candidate,
                "timing_profile": cls._as_dict(observations.get("timing_profile")),
                "execution_plan_timing": cls._as_dict(observations.get("execution_plan_timing")),
                "expectations": entry.expectations,
                "outcome": entry.outcome,
                "lessons": entry.lessons,
            },
        )

    @classmethod
    def _build_position_events(cls, position: Position) -> list[TickerTraceEventRead]:
        events = [
            TickerTraceEventRead(
                timestamp=position.entry_date,
                event_kind="position_open",
                title=f"Position opened #{position.id}",
                summary=cls._first_non_empty(
                    cls._skill_summary(
                        cls._as_dict((position.entry_context or {}).get("skill_context") if isinstance(position.entry_context, dict) else None)
                    ),
                    position.thesis,
                    "Paper position opened.",
                ),
                status=position.status,
                decision="enter",
                position_id=position.id,
                signal_id=position.signal_id,
                tags=[tag for tag in [position.side, position.account_mode, f"signal:{position.signal_id}" if position.signal_id else "", f"skill:{cls._as_dict((position.entry_context or {}).get('skill_context') if isinstance(position.entry_context, dict) else None).get('primary_skill_code')}" if cls._as_dict((position.entry_context or {}).get("skill_context") if isinstance(position.entry_context, dict) else None).get("primary_skill_code") else ""] if tag],
                details={
                    "entry_price": position.entry_price,
                    "stop_price": position.stop_price,
                    "target_price": position.target_price,
                    "size": position.size,
                    "entry_context": cls._as_dict(position.entry_context),
                    "skill_context": cls._as_dict((position.entry_context or {}).get("skill_context") if isinstance(position.entry_context, dict) else None),
                    "context_budget": cls._extract_context_budget(
                        cls._as_dict((position.entry_context or {}).get("management_context") if isinstance(position.entry_context, dict) else None)
                    ),
                    "timing_profile": cls._as_dict((position.entry_context or {}).get("timing_profile") if isinstance(position.entry_context, dict) else None),
                    "execution_plan_timing": cls._as_dict((position.entry_context or {}).get("execution_plan_timing") if isinstance(position.entry_context, dict) else None),
                },
            )
        ]
        if position.exit_date is not None:
            events.append(
                TickerTraceEventRead(
                    timestamp=position.exit_date,
                    event_kind="position_close",
                    title=f"Position closed #{position.id}",
                    summary=cls._first_non_empty(
                        position.exit_reason,
                        f"Position closed at {position.exit_price:.2f}" if position.exit_price is not None else None,
                        "Position closed.",
                    ),
                    status=position.status,
                    decision="exit",
                    position_id=position.id,
                    signal_id=position.signal_id,
                    tags=[tag for tag in ["closed", position.side, position.account_mode] if tag],
                    details={
                        "exit_price": position.exit_price,
                        "exit_reason": position.exit_reason,
                        "pnl_realized": position.pnl_realized,
                        "pnl_pct": position.pnl_pct,
                        "close_context": cls._as_dict(position.close_context),
                    },
                )
            )
        return events

    @staticmethod
    def _signal_timestamp(signal: TradeSignal) -> datetime:
        return signal.signal_time or signal.created_at or datetime.now(timezone.utc)

    @staticmethod
    def _derive_llm_status(ai_overlay: dict, guard_results: dict) -> str | None:
        if ai_overlay.get("status"):
            return str(ai_overlay["status"])
        if ai_overlay.get("action") or ai_overlay.get("thesis") or ai_overlay.get("provider") or ai_overlay.get("model"):
            return "reviewed"
        if guard_results.get("blocked"):
            return "not_called_blocked"
        if ai_overlay:
            return "recorded"
        return None

    @staticmethod
    def _derive_llm_provider(ai_overlay: dict) -> str | None:
        return TickerDecisionTraceService._first_non_empty(
            ai_overlay.get("provider"),
            ai_overlay.get("used_provider"),
        )

    @staticmethod
    def _derive_score(signal: TradeSignal | None, context: dict) -> float | None:
        if signal is None:
            return None
        score_breakdown = TickerDecisionTraceService._as_dict(context.get("score_breakdown"))
        entry_score = TickerDecisionTraceService._as_dict(context.get("entry_score"))
        for value in (
            score_breakdown.get("total_score"),
            entry_score.get("score"),
            context.get("score"),
            signal.quality_score,
        ):
            if isinstance(value, (int, float)):
                return float(value)
        return None

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _slowest_timing_stage(timing_profile: dict) -> tuple[str, float] | None:
        explicit_stage = TickerDecisionTraceService._first_non_empty(timing_profile.get("slowest_stage"))
        explicit_ms = TickerDecisionTraceService._coerce_float(timing_profile.get("slowest_stage_ms"))
        if explicit_stage and explicit_ms is not None:
            return (explicit_stage, explicit_ms)
        stages = timing_profile.get("stages_ms")
        if not isinstance(stages, dict):
            return None
        candidates = [
            (str(stage), float(value))
            for stage, value in stages.items()
            if isinstance(stage, str) and isinstance(value, (int, float))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[1])

    @staticmethod
    def _latest_active_skill_revision_label(skill_context: dict) -> str | None:
        active_revisions = skill_context.get("active_revisions")
        if not isinstance(active_revisions, list) or not active_revisions:
            return None
        first = active_revisions[0]
        if not isinstance(first, dict):
            return None
        return TickerDecisionTraceService._first_non_empty(
            first.get("skill_code"),
            first.get("revision_summary"),
        )

    @staticmethod
    def _skill_summary(skill_context: dict) -> str | None:
        primary_skill = str(skill_context.get("primary_skill_code") or "").strip()
        if not primary_skill:
            return None
        active_revisions = skill_context.get("active_revisions")
        if isinstance(active_revisions, list) and active_revisions:
            first = active_revisions[0]
            if isinstance(first, dict) and first.get("revision_summary"):
                return f"Primary skill {primary_skill} with active revision: {first['revision_summary']}"
        return f"Primary skill {primary_skill}."

    @staticmethod
    def _workflow_summary(*, entry_type: str | None, workflow_type: object, resolution_class: object, outcome: str | None) -> str | None:
        normalized_entry_type = str(entry_type or "").strip().lower()
        normalized_workflow = str(workflow_type or "").strip()
        normalized_resolution = str(resolution_class or "").strip()
        if normalized_entry_type != "learning_workflow_action" and not normalized_workflow:
            return None
        parts = []
        if normalized_workflow:
            parts.append(f"Workflow {normalized_workflow}")
        else:
            parts.append("Workflow action")
        if normalized_resolution:
            parts.append(normalized_resolution)
        if isinstance(outcome, str) and outcome.strip():
            parts.append(outcome.strip())
        return " · ".join(parts)

    @staticmethod
    def _extract_context_budget(payload: dict) -> dict:
        budget = payload.get("context_budget")
        if isinstance(budget, dict):
            return budget
        decision_context = payload.get("decision_context")
        if isinstance(decision_context, dict):
            nested_budget = decision_context.get("context_budget")
            if isinstance(nested_budget, dict):
                return nested_budget
        return {}

    @classmethod
    def _runtime_budget_count(cls, context_budget: dict, section: str, key: str) -> int | None:
        section_payload = cls._as_dict(context_budget.get(section))
        value = section_payload.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return None

    @classmethod
    def _runtime_budget_truncated(cls, context_budget: dict) -> bool:
        skill_truncated = cls._runtime_budget_count(context_budget, "runtime_skills", "truncated_count") or 0
        claim_truncated = cls._runtime_budget_count(context_budget, "runtime_claims", "truncated_count") or 0
        return (skill_truncated + claim_truncated) > 0

    @staticmethod
    def _first_guard_reason(guard_results: dict, fallback: str | None = None) -> str | None:
        reasons = guard_results.get("reasons")
        if isinstance(reasons, list):
            for item in reasons:
                if isinstance(item, str) and item.strip():
                    return item.strip()
        return TickerDecisionTraceService._first_non_empty(fallback)

    @staticmethod
    def _first_non_empty(*values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _as_dict(value: object) -> dict:
        return value if isinstance(value, dict) else {}


class MemoryService:
    RETENTION_LIMITS_EXACT: dict[tuple[str, str], int] = {
        ("episodic", "pdca_check"): 288,
        ("episodic", "pdca_act"): 288,
        ("operator_disagreement", "operator_feedback"): 240,
        ("operator_disagreement_cluster", "operator_feedback"): 120,
        ("skill_gap", "operator_feedback"): 120,
    }
    RETENTION_LIMITS_PREFIX: dict[tuple[str, str], int] = {
        ("strategy_evolution", "strategy:"): 120,
        ("skill_candidate", "strategy:"): 120,
        ("skill_gap", "strategy:"): 120,
        ("validated_skill_revision", "skill:"): 60,
    }

    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

    def list_items(self, session: Session):
        return self.repository.list(session)

    def create_item(self, session: Session, payload: MemoryItemCreate):
        item = self.repository.create(session, payload)
        self._apply_retention(session, item.memory_type, item.scope)
        return item

    def retrieve_scope(self, session: Session, scope: str, limit: int = 10):
        return self.repository.retrieve(session, scope=scope, limit=limit)

    @classmethod
    def _retention_limit(cls, memory_type: str, scope: str) -> int | None:
        exact = cls.RETENTION_LIMITS_EXACT.get((memory_type, scope))
        if exact is not None:
            return exact
        for (rule_type, prefix), keep_latest in cls.RETENTION_LIMITS_PREFIX.items():
            if memory_type == rule_type and scope.startswith(prefix):
                return keep_latest
        return None

    @classmethod
    def _stale_item_ids(cls, session: Session, *, memory_type: str, scope: str, keep_latest: int) -> list[int]:
        if keep_latest <= 0:
            return [
                item_id
                for (item_id,) in session.query(MemoryItem.id)
                .filter(MemoryItem.memory_type == memory_type, MemoryItem.scope == scope)
                .all()
            ]
        rows = (
            session.query(MemoryItem.id)
            .filter(MemoryItem.memory_type == memory_type, MemoryItem.scope == scope)
            .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
            .offset(keep_latest)
            .all()
        )
        return [item_id for (item_id,) in rows]

    @classmethod
    def _apply_retention(cls, session: Session, memory_type: str, scope: str) -> int:
        keep_latest = cls._retention_limit(memory_type, scope)
        if keep_latest is None:
            return 0
        stale_ids = cls._stale_item_ids(
            session,
            memory_type=memory_type,
            scope=scope,
            keep_latest=keep_latest,
        )
        if not stale_ids:
            return 0
        session.query(MemoryItem).filter(MemoryItem.id.in_(stale_ids)).delete(synchronize_session=False)
        session.commit()
        return len(stale_ids)


class LearningHistoryMaintenanceService:
    def trim_history(self, session: Session, *, dry_run: bool = True) -> dict:
        journal_summary = self.prune_journal_entries(session, dry_run=dry_run)
        memory_summary = self.prune_memory_items(session, dry_run=dry_run)
        return {
            "dry_run": dry_run,
            "journal": journal_summary,
            "memory": memory_summary,
            "deleted_total": journal_summary["deleted_count"] + memory_summary["deleted_count"],
        }

    def prune_journal_entries(self, session: Session, *, dry_run: bool = True) -> dict:
        deleted_ids: list[int] = []
        rules: list[dict] = []
        for entry_type, keep_latest in JournalService.RETENTION_LIMITS.items():
            stale_ids = JournalService._stale_entry_ids(session, entry_type=entry_type, keep_latest=keep_latest)
            if stale_ids and not dry_run:
                session.query(JournalEntry).filter(JournalEntry.id.in_(stale_ids)).delete(synchronize_session=False)
            deleted_ids.extend(stale_ids)
            rules.append(
                {
                    "entry_type": entry_type,
                    "keep_latest": keep_latest,
                    "deleted_count": len(stale_ids),
                }
            )
        if deleted_ids and not dry_run:
            session.commit()
        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "rules": rules,
        }

    def prune_memory_items(self, session: Session, *, dry_run: bool = True) -> dict:
        deleted_ids: list[int] = []
        rules: list[dict] = []

        for (memory_type, scope), keep_latest in MemoryService.RETENTION_LIMITS_EXACT.items():
            stale_ids = MemoryService._stale_item_ids(
                session,
                memory_type=memory_type,
                scope=scope,
                keep_latest=keep_latest,
            )
            if stale_ids and not dry_run:
                session.query(MemoryItem).filter(MemoryItem.id.in_(stale_ids)).delete(synchronize_session=False)
            deleted_ids.extend(stale_ids)
            rules.append(
                {
                    "memory_type": memory_type,
                    "scope": scope,
                    "keep_latest": keep_latest,
                    "deleted_count": len(stale_ids),
                }
            )

        for (memory_type, prefix), keep_latest in MemoryService.RETENTION_LIMITS_PREFIX.items():
            scopes = [
                scope
                for (scope,) in session.query(MemoryItem.scope)
                .filter(MemoryItem.memory_type == memory_type, MemoryItem.scope.like(f"{prefix}%"))
                .distinct()
                .all()
            ]
            for scope in scopes:
                stale_ids = MemoryService._stale_item_ids(
                    session,
                    memory_type=memory_type,
                    scope=scope,
                    keep_latest=keep_latest,
                )
                if stale_ids and not dry_run:
                    session.query(MemoryItem).filter(MemoryItem.id.in_(stale_ids)).delete(synchronize_session=False)
                deleted_ids.extend(stale_ids)
                rules.append(
                    {
                        "memory_type": memory_type,
                        "scope": scope,
                        "keep_latest": keep_latest,
                        "deleted_count": len(stale_ids),
                    }
                )

        if deleted_ids and not dry_run:
            session.commit()
        return {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "rules": rules,
        }


class BotChatService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        research_service: object | None = None,
        work_queue_service: object | None = None,
        news_service: object | None = None,
        calendar_service: object | None = None,
        macro_context_service: MacroContextService | None = None,
        market_state_service: MarketStateService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if research_service is None:
            from app.domains.market.services import ResearchService

            research_service = ResearchService()
        if work_queue_service is None:
            from app.domains.market.services import WorkQueueService

            work_queue_service = WorkQueueService()
        if news_service is None:
            from app.domains.market.services import NewsService

            news_service = NewsService()
        if calendar_service is None:
            from app.domains.market.services import CalendarService

            calendar_service = CalendarService()
        if macro_context_service is None:
            macro_context_service = MacroContextService()
        if market_state_service is None:
            market_state_service = MarketStateService(settings=self.settings)
        self.research_service = research_service
        self.work_queue_service = work_queue_service
        self.news_service = news_service
        self.calendar_service = calendar_service
        self.macro_context_service = macro_context_service
        self.market_state_service = market_state_service

    def _get_latest_market_state_context(self, session: Session) -> dict | None:
        snapshot = self.market_state_service.get_latest_snapshot(session)
        if snapshot is None:
            return None
        payload = snapshot.snapshot_payload if isinstance(snapshot.snapshot_payload, dict) else {}
        backlog = payload.get("backlog") if isinstance(payload.get("backlog"), dict) else {}
        macro_context = payload.get("macro_context") if isinstance(payload.get("macro_context"), dict) else {}
        active_regimes = macro_context.get("active_regimes") if isinstance(macro_context.get("active_regimes"), list) else []
        return {
            "snapshot_id": snapshot.id,
            "trigger": snapshot.trigger,
            "pdca_phase": snapshot.pdca_phase,
            "regime_label": snapshot.regime_label,
            "regime_confidence": snapshot.regime_confidence,
            "summary": snapshot.summary,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at is not None else None,
            "open_positions_count": backlog.get("open_positions_count"),
            "active_watchlists_count": backlog.get("active_watchlists_count"),
            "open_research_tasks": backlog.get("open_research_tasks"),
            "active_regimes": active_regimes,
        }

    def reply(self, session: Session, message: str) -> BotChatResponse:
        topic = self._classify_topic(message)

        if topic == "discoveries":
            reply, context = self._build_discoveries_reply(session)
        elif topic == "news":
            reply, context = self._build_news_reply(session, message)
        elif topic == "calendar":
            reply, context = self._build_calendar_reply(session, message)
        elif topic == "macro":
            reply, context = self._build_macro_reply(session)
        elif topic == "status":
            reply, context = self._build_status_reply(session)
        elif topic == "tools":
            reply, context = self._build_tools_reply(session)
        elif topic == "operations":
            reply, context = self._build_operations_reply(session)
        else:
            reply, context = self._build_overview_reply(session)

        return BotChatResponse(
            topic=topic,
            reply=reply,
            suggested_prompts=self._suggested_prompts(topic),
            context=context,
        )

    def _build_status_reply(self, session: Session) -> tuple[str, dict]:
        from app.domains.system.runtime import scheduler_service

        status = scheduler_service.get_status_payload()
        bot = status["bot"]
        queue = self.work_queue_service.get_queue(session)
        open_research = [task for task in self.research_service.list_tasks(session) if task.status != "completed"]
        open_positions = session.query(Position).filter(Position.status == "open").count()
        latest_incident = next((item for item in bot["incidents"] if item["status"] == "open"), None)
        top_item = queue.items[0] if queue.items else None
        latest_market_state = self._get_latest_market_state_context(session)

        lines = [
            f"Ahora mismo el bot está {bot['status'].upper()}.",
            (
                f"Fase actual: {bot['current_phase']}."
                if bot["current_phase"]
                else f"Última fase correcta: {bot['last_successful_phase'] or 'ninguna'}."
            ),
            f"Ciclos completados: {bot['cycle_runs']}. Posiciones abiertas: {open_positions}.",
        ]
        if latest_market_state is not None:
            lines.append(
                "Último market state: "
                f"régimen {latest_market_state['regime_label']} en fase {latest_market_state['pdca_phase'] or 'general'}."
            )
        if latest_incident is not None:
            lines.append(f"Está bloqueado por una incidencia: {latest_incident['title']}.")
        elif top_item is not None:
            lines.append(f"El foco inmediato es: {top_item.title}.")
        else:
            lines.append("No veo una incidencia abierta ni una cola prioritaria urgente.")
        if open_research:
            lines.append(f"Tiene {len(open_research)} tareas de research activas.")

        return " ".join(lines), {
            "bot_status": bot["status"],
            "current_phase": bot["current_phase"],
            "cycle_runs": bot["cycle_runs"],
            "open_positions": open_positions,
            "open_research_tasks": len(open_research),
            "top_queue_item": top_item.title if top_item is not None else None,
            "latest_incident": latest_incident["title"] if latest_incident is not None else None,
            "market_state": latest_market_state,
        }

    def _build_discoveries_reply(self, session: Session) -> tuple[str, dict]:
        open_tasks = list(
            session.scalars(
                select(ResearchTask).where(ResearchTask.status != "completed").order_by(ResearchTask.created_at.desc()).limit(3)
            ).all()
        )
        latest_changes = list(
            session.scalars(select(StrategyChangeEvent).order_by(StrategyChangeEvent.created_at.desc()).limit(3)).all()
        )
        latest_activations = list(
            session.scalars(
                select(StrategyActivationEvent).order_by(StrategyActivationEvent.created_at.desc()).limit(3)
            ).all()
        )
        latest_validations = list(
            session.scalars(
                select(CandidateValidationSnapshot).order_by(CandidateValidationSnapshot.generated_at.desc()).limit(3)
            ).all()
        )

        discoveries: list[str] = []
        if latest_validations:
            for snapshot in latest_validations:
                discoveries.append(
                    f"validación candidata v{snapshot.strategy_version_id} de estrategia {snapshot.strategy_id}: "
                    f"{snapshot.evaluation_status} con win rate {snapshot.win_rate or 0:.1f}% y {snapshot.trade_count} trades"
                )
        if latest_activations:
            discoveries.append(f"última activación automática: {latest_activations[0].activation_reason}")
        if latest_changes:
            discoveries.append(f"último cambio de estrategia: {latest_changes[0].change_reason}")
        if open_tasks:
            discoveries.append(f"research abierto: {open_tasks[0].title}")

        if not discoveries:
            reply = (
                "Todavía no tengo descubrimientos materiales persistidos. "
                "Ahora mismo conviene ejecutar ciclos DO/CHECK o arrancar el scheduler para generar señales, research y cambios."
            )
        else:
            reply = "Lo más relevante que he descubierto o dejado preparado es: " + "; ".join(discoveries[:4]) + "."

        return reply, {
            "open_research_titles": [task.title for task in open_tasks],
            "latest_change_reasons": [item.change_reason for item in latest_changes],
            "latest_activation_reasons": [item.activation_reason for item in latest_activations],
            "candidate_validation_statuses": [item.evaluation_status for item in latest_validations],
        }

    def _build_tools_reply(self, session: Session) -> tuple[str, dict]:
        from app.domains.system.runtime import scheduler_service

        ai_status = scheduler_service.get_status_payload()["ai"]
        gaps: list[str] = []

        if not ai_status["enabled"]:
            gaps.append("activar un proveedor de IA operativo para que el bot pueda razonar y explicar mejor sus decisiones")
        elif not ai_status["ready"]:
            gaps.append("credenciales válidas para el proveedor de IA configurado")

        if self.settings.market_data_provider == "stub":
            gaps.append("activar el proxy interno de IBKR para dejar de depender del proveedor stub")
        elif self.settings.market_data_provider == "twelve_data" and not self.settings.twelve_data_api_key:
            gaps.append("una API key de Twelve Data para dejar de depender del proveedor stub")

        if session.query(Position).filter(Position.account_mode != "paper").count() == 0:
            gaps.append("integración de broker o execution gateway, porque ahora mismo solo veo paper trading")

        if session.query(AnalysisRun).count() == 0:
            gaps.append("más flujo de análisis persistido para comparar setups y medir mejor qué funciona")

        gaps.append("alguna fuente externa de noticias, catalysts o sentimiento, que hoy no aparece integrada en esta MVP")
        gaps.append("un módulo de backtesting/replay más explícito para validar cambios antes de promover estrategias")

        reply = (
            "Viendo el código y la configuración actual, las herramientas que más faltan para mejorar resultados son: "
            + "; ".join(gaps[:5])
            + "."
        )
        return reply, {
            "ai_enabled": ai_status["enabled"],
            "ai_ready": ai_status["ready"],
            "market_data_provider": self.settings.market_data_provider,
            "using_stub_market_data": self.settings.market_data_provider == "stub",
            "has_twelve_data_key": bool(self.settings.twelve_data_api_key),
            "paper_only_positions": session.query(Position).filter(Position.account_mode != "paper").count() == 0,
        }

    def _build_news_reply(self, session: Session, message: str) -> tuple[str, dict]:
        ticker = self._extract_ticker_candidate(message)
        query = ticker if ticker else message

        try:
            articles = (
                self.news_service.list_news_for_ticker(ticker, max_results=5)
                if ticker
                else self.news_service.list_news(query, max_results=5)
            )
        except NewsProviderError as exc:
            return (
                f"No puedo traer noticias ahora mismo: {exc}.",
                {"query": query, "articles": [], "ticker": ticker, "error": str(exc)},
            )

        if not articles:
            if not self.settings.gnews_api_key:
                return (
                    "No puedo traer noticias porque GNews no está configurado todavía en el backend activo.",
                    {"query": query, "articles": [], "ticker": ticker},
                )
            return (
                f"No encontré noticias recientes para {ticker or query}.",
                {"query": query, "articles": [], "ticker": ticker},
            )

        summaries = [
            f"{article.title} ({article.source_name}, {article.published_at[:10]})"
            for article in articles[:3]
        ]
        prefix = f"Noticias recientes para {ticker}: " if ticker else f"Noticias recientes para '{query}': "
        return prefix + "; ".join(summaries) + ".", {
            "query": query,
            "ticker": ticker,
            "articles": [
                {
                    "title": article.title,
                    "source_name": article.source_name,
                    "published_at": article.published_at,
                    "url": article.url,
                }
                for article in articles
            ],
        }

    def _build_operations_reply(self, session: Session) -> tuple[str, dict]:
        latest_positions = list(
            session.scalars(
                select(Position).order_by(Position.exit_date.desc().nullslast(), Position.entry_date.desc()).limit(5)
            ).all()
        )
        closed_positions = list(session.scalars(select(Position).where(Position.status == "closed")).all())
        open_positions = [position for position in latest_positions if position.status == "open"]
        wins = [position for position in closed_positions if (position.pnl_pct or 0.0) > 0]
        losses = [position for position in closed_positions if (position.pnl_pct or 0.0) <= 0]
        avg_realized = (
            round(sum((position.pnl_pct or 0.0) for position in closed_positions) / len(closed_positions), 2)
            if closed_positions
            else None
        )

        if not latest_positions:
            return (
                "Todavía no hay operaciones registradas. En cuanto el bot abra o cierre posiciones, aquí podré resumirte entradas, salidas y PnL.",
                {
                    "latest_positions": [],
                    "closed_positions": 0,
                    "open_positions": 0,
                    "avg_realized_pnl_pct": None,
                },
            )

        summaries = []
        for position in latest_positions[:4]:
            status = "abierta" if position.status == "open" else f"cerrada {position.pnl_pct or 0:.2f}%"
            reason = position.exit_reason or position.thesis or "sin detalle"
            summaries.append(f"{position.ticker}: {status} ({reason})")

        reply = (
            f"Resumen rápido de las últimas operaciones: {'; '.join(summaries)}. "
            f"Acumulado cerrado: {len(closed_positions)} trades, {len(wins)} ganadoras, {len(losses)} perdedoras"
            + (f", PnL medio {avg_realized:.2f}%." if avg_realized is not None else ".")
        )
        return reply, {
            "latest_positions": [
                {
                    "ticker": position.ticker,
                    "status": position.status,
                    "pnl_pct": position.pnl_pct,
                    "exit_reason": position.exit_reason,
                }
                for position in latest_positions
            ],
            "closed_positions": len(closed_positions),
            "open_positions": len(open_positions),
            "wins": len(wins),
            "losses": len(losses),
            "avg_realized_pnl_pct": avg_realized,
        }

    def _build_macro_reply(self, session: Session) -> tuple[str, dict]:
        context = self.macro_context_service.get_context(session, limit=6).model_dump(mode="json")
        latest_market_state = self._get_latest_market_state_context(session)
        dominant_regimes = context["active_regimes"][:3] or (latest_market_state or {}).get("active_regimes", [])[:3]
        lines = [context["summary"]]
        if latest_market_state is not None:
            confidence_suffix = (
                f" con confianza {latest_market_state['regime_confidence']:.2f}"
                if latest_market_state.get("regime_confidence") is not None
                else ""
            )
            lines.append(
                "Último Market State Snapshot: "
                f"régimen {latest_market_state['regime_label']} en fase {latest_market_state['pdca_phase'] or 'general'}{confidence_suffix}."
            )
        if dominant_regimes:
            lines.append(f"Regimenes dominantes: {', '.join(dominant_regimes)}.")
        if context["signals"]:
            top_lines = [
                f"{signal['key']}: {signal['content']}"
                for signal in context["signals"][:3]
            ]
            lines.append(f"Señales más relevantes: {'; '.join(top_lines)}.")
        return " ".join(lines), {
            **context,
            "market_state": latest_market_state,
        }

    def _build_calendar_reply(self, session: Session, message: str) -> tuple[str, dict]:
        del session
        ticker = self._extract_ticker_candidate(message)
        try:
            events = (
                self.calendar_service.list_ticker_events(ticker, days_ahead=30)
                if ticker
                else self.calendar_service.list_macro_events(days_ahead=14)
            )
        except CalendarProviderError as exc:
            return (
                f"No puedo traer calendario ahora mismo: {exc}.",
                {"ticker": ticker, "events": [], "error": str(exc)},
            )

        if ticker:
            if not events:
                return (
                    f"No veo eventos corporativos próximos para {ticker} o el calendario externo no está configurado.",
                    {"ticker": ticker, "events": []},
                )
            reply = (
                f"Próximos eventos corporativos para {ticker}: "
                + "; ".join(f"{event.title} ({event.event_date})" for event in events[:3])
                + "."
            )
            return reply, {
                "ticker": ticker,
                "events": [event.__dict__ for event in events],
            }

        if not events:
            return (
                "No veo eventos macro próximos o el calendario externo no está configurado.",
                {"events": []},
            )
        reply = (
            "Próximos eventos macro relevantes: "
            + "; ".join(f"{event.title} ({event.event_date})" for event in events[:4])
            + "."
        )
        return reply, {"events": [event.__dict__ for event in events]}

    def _build_overview_reply(self, session: Session) -> tuple[str, dict]:
        status_reply, status_context = self._build_status_reply(session)
        discoveries_reply, discoveries_context = self._build_discoveries_reply(session)
        operations_reply, operations_context = self._build_operations_reply(session)
        macro_reply, macro_context = self._build_macro_reply(session)
        reply = f"{status_reply} {discoveries_reply} {operations_reply} {macro_reply}"
        return reply, {
            "status": status_context,
            "discoveries": discoveries_context,
            "operations": operations_context,
            "macro": macro_context,
        }

    @staticmethod
    def _normalize_message(message: str) -> str:
        normalized = unicodedata.normalize("NFKD", message.lower())
        return "".join(char for char in normalized if not unicodedata.combining(char))

    def _classify_topic(self, message: str) -> str:
        text = self._normalize_message(message)

        if any(token in text for token in ["noticia", "noticias", "news", "catalyst", "catalysts", "titulares"]):
            return "news"
        if any(
            token in text
            for token in ["earnings", "calendario", "evento", "eventos", "ipc", "cpi", "fomc", "dividendo", "split"]
        ):
            return "calendar"
        if any(
            token in text
            for token in [
                "macro",
                "geopolit",
                "fed",
                "tipos",
                "inflacion",
                "petroleo",
                "guerra",
                "eleccion",
                "regimen",
                "rates",
            ]
        ):
            return "macro"
        if any(token in text for token in ["operacion", "trade", "trades", "pnl", "ultimo", "ultimas"]):
            return "operations"
        if any(token in text for token in ["descubr", "detect", "hall", "research", "oportun"]):
            return "discoveries"
        if any(token in text for token in ["herramient", "falta", "faltan", "mejorar", "improve", "tool"]):
            return "tools"
        if any(token in text for token in ["haciendo", "doing", "estado", "status", "ahora", "runtime"]):
            return "status"
        return "overview"

    @staticmethod
    def _suggested_prompts(topic: str) -> list[str]:
        suggestions = {
            "news": [
                "Noticias de NVDA",
                "Catalysts recientes de AAPL",
                "Ultimas noticias del mercado",
            ],
            "calendar": [
                "Proximos earnings de NVDA",
                "Que eventos macro hay esta semana",
                "Calendario corporativo de AAPL",
            ],
            "discoveries": [
                "Que has descubierto hoy",
                "Que candidatos merecen promocion",
                "Que research sigue abierto",
            ],
            "macro": [
                "Cual es el contexto macro actual",
                "Que regimen de mercado estas viendo",
                "Que riesgos geopoliticos importan ahora",
            ],
            "status": [
                "Que estas haciendo ahora",
                "Cual es tu siguiente foco",
                "Por que estas en pausa",
            ],
            "tools": [
                "Que herramientas te faltan",
                "Como mejorarias el stack actual",
                "Que integracion aporta mas valor",
            ],
            "operations": [
                "Resumen de las ultimas operaciones",
                "Cuantas posiciones abiertas hay",
                "Cuales fueron las ultimas perdidas",
            ],
            "overview": [
                "Dame un resumen general",
                "Que has descubierto",
                "Que estas haciendo ahora",
            ],
        }
        return suggestions[topic]

    @staticmethod
    def _extract_ticker_candidate(message: str) -> str | None:
        matches = re.findall(r"\b[A-Z]{2,6}\b", message)
        return matches[0] if matches else None


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
            from app.domains.execution.services import TradeReviewService

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
    REANALYSIS_RUNTIME_KEY = "reanalysis_runtime"
    REANALYSIS_RUNTIME_VERSION = "watchlist_reanalysis_runtime_v1"
    REANALYSIS_OPEN_SHORT_INTERVAL_SECONDS = 900
    REANALYSIS_OPEN_MEDIUM_INTERVAL_SECONDS = 1800
    REANALYSIS_OPEN_LONG_INTERVAL_SECONDS = 2700
    REANALYSIS_CLOSED_SHORT_INTERVAL_SECONDS = 3600
    REANALYSIS_CLOSED_MEDIUM_INTERVAL_SECONDS = 7200
    REANALYSIS_CLOSED_LONG_INTERVAL_SECONDS = 14400
    MACRO_RESEARCH_TOPICS = (
        {
            "slug": "us_rates_inflation",
            "title": "US rates, inflation, and growth repricing",
            "query": "Fed rates inflation CPI PCE treasury yields US economy",
            "keywords": ["fed", "rates", "inflation", "cpi", "pce", "treasury", "yield", "payroll", "jobs"],
            "domains": ["reuters.com", "cnbc.com", "finance.yahoo.com"],
            "focus_assets": ["QQQ", "SPY", "IWM", "TLT", "XLF", "UUP"],
            "default_regime": "macro_uncertainty",
            "relevance": "cross_asset",
            "timeframe": "1D-1M",
            "strategy_templates": [
                "build a scenario tree for dovish versus hawkish repricing and map confirmation levels on QQQ, TLT, and XLF",
                "prepare rotation watchlists for growth-duration winners versus value and financial beneficiaries",
                "design hedge logic around CPI, PCE, or FOMC event windows instead of forcing directional trades early",
            ],
        },
        {
            "slug": "energy_supply_geopolitics",
            "title": "Oil, shipping, and Middle East supply shocks",
            "query": "oil middle east shipping opec red sea supply disruptions",
            "keywords": ["oil", "opec", "middle east", "red sea", "shipping", "crude", "brent", "supply", "iran"],
            "domains": ["reuters.com", "cnbc.com", "marketwatch.com"],
            "focus_assets": ["USO", "XLE", "XOP", "XOM", "CVX", "JETS"],
            "default_regime": "macro_uncertainty",
            "relevance": "geopolitics",
            "timeframe": "1D-3M",
            "strategy_templates": [
                "map crude breakout confirmation levels before expressing the thesis through USO or XLE",
                "pair energy longs against transport or discretionary weakness when supply shock evidence strengthens",
                "prepare contingency hedges for sudden de-escalation that could unwind the risk premium fast",
            ],
        },
        {
            "slug": "china_taiwan_semiconductors",
            "title": "China-Taiwan tension and semiconductor supply chain stress",
            "query": "China Taiwan semiconductors TSMC export controls chips supply chain",
            "keywords": ["china", "taiwan", "semiconductor", "chips", "tsmc", "export", "nvidia", "amd", "asml"],
            "domains": ["reuters.com", "cnbc.com", "nasdaq.com"],
            "focus_assets": ["SMH", "SOXX", "NVDA", "AMD", "TSM", "ASML"],
            "default_regime": "macro_uncertainty",
            "relevance": "geopolitics",
            "timeframe": "1D-3M",
            "strategy_templates": [
                "prepare semiconductor beta reduction and hedge plans if supply-chain risk starts translating into price weakness",
                "track whether geopolitical stress creates a buy-the-dip or avoid-the-group regime in SMH and NVDA",
                "separate short-lived headline spikes from persistent export-control or logistics deterioration",
            ],
        },
        {
            "slug": "usd_yields_defensive_rotation",
            "title": "Dollar, yields, and defensive rotation",
            "query": "dollar treasury yields recession safe haven defensive rotation",
            "keywords": ["dollar", "usd", "yield", "treasury", "recession", "safe haven", "defensive", "credit"],
            "domains": ["reuters.com", "cnbc.com", "marketwatch.com"],
            "focus_assets": ["UUP", "TLT", "GLD", "XLP", "XLV", "IWM"],
            "default_regime": "high_volatility_risk_off",
            "relevance": "cross_asset",
            "timeframe": "1D-1M",
            "strategy_templates": [
                "track whether risk-off flows are favoring bonds, gold, or dollar strength and avoid mixing regimes prematurely",
                "build defensive-rotation watchlists around staples, healthcare, and duration proxies",
                "prepare relative-strength trades that short weaker cyclicals only after defensive leadership is confirmed",
            ],
        },
    )

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
        watchlist_service: object | None = None,
        failure_analysis_service: FailureAnalysisService | None = None,
        work_queue_service: object | None = None,
        strategy_evolution_service: object | None = None,
        opportunity_discovery_service: object | None = None,
        trading_agent_service: AutonomousTradingAgentService | None = None,
        agent_tool_gateway_service: AgentToolGatewayService | None = None,
        market_state_service: MarketStateService | None = None,
        decision_context_service: DecisionContextService | None = None,
        feature_relevance_service: FeatureRelevanceService | None = None,
        strategy_context_adaptation_service: StrategyContextAdaptationService | None = None,
        decision_context_assembler_service: DecisionContextAssemblerService | None = None,
        entry_scoring_service: EntryScoringService | None = None,
        position_sizing_service: PositionSizingService | None = None,
        halt_on_market_data_failure: bool = False,
    ) -> None:
        self.settings = (
            trading_agent_service.settings
            if trading_agent_service is not None
            else get_settings()
        )
        self.pdca_service = pdca_service or PDCACycleService()
        self.journal_service = journal_service or JournalService()
        self.memory_service = memory_service or MemoryService()
        if analysis_service is None:
            from app.domains.market.services import AnalysisService

            analysis_service = AnalysisService()
        if market_data_service is None:
            from app.domains.market.services import MarketDataService

            market_data_service = MarketDataService(raise_on_provider_error=halt_on_market_data_failure)
        if signal_service is None:
            from app.domains.market.analysis import FusedAnalysisService
            from app.domains.market.services import SignalService

            signal_service = SignalService(
                fused_analysis_service=FusedAnalysisService(market_data_service=market_data_service)
            )
        if position_service is None:
            from app.domains.execution.services import PositionService

            position_service = PositionService()
        if strategy_lab_service is None:
            from app.domains.strategy.services import StrategyLabService

            strategy_lab_service = StrategyLabService()
        if exit_management_service is None:
            from app.domains.execution.services import ExitManagementService

            exit_management_service = ExitManagementService()
        if strategy_scoring_service is None:
            from app.domains.strategy.services import StrategyScoringService

            strategy_scoring_service = StrategyScoringService()
        if research_service is None:
            from app.domains.market.services import ResearchService

            research_service = ResearchService()
        if watchlist_service is None:
            from app.domains.strategy.services import WatchlistService

            watchlist_service = WatchlistService()
        self.auto_review_service = auto_review_service or AutoReviewService()
        self.analysis_service = analysis_service
        self.market_data_service = market_data_service
        self.signal_service = signal_service
        self.position_service = position_service
        self.strategy_lab_service = strategy_lab_service
        self.exit_management_service = exit_management_service
        self.strategy_scoring_service = strategy_scoring_service
        self.research_service = research_service
        self.watchlist_service = watchlist_service
        if strategy_evolution_service is None:
            from app.domains.strategy.services import StrategyEvolutionService

            strategy_evolution_service = StrategyEvolutionService(research_service=self.research_service)
        if opportunity_discovery_service is None:
            from app.domains.market.discovery import OpportunityDiscoveryService

            opportunity_discovery_service = OpportunityDiscoveryService(
                market_data_service=self.market_data_service,
                signal_service=self.signal_service,
            )
        self.strategy_evolution_service = strategy_evolution_service
        self.opportunity_discovery_service = opportunity_discovery_service
        self.failure_analysis_service = failure_analysis_service or FailureAnalysisService()
        if work_queue_service is None:
            from app.domains.market.services import WorkQueueService

            work_queue_service = WorkQueueService(failure_analysis_service=self.failure_analysis_service)
        self.work_queue_service = work_queue_service
        self.trading_agent_service = trading_agent_service or AutonomousTradingAgentService()
        self.strategy_context_adaptation_service = strategy_context_adaptation_service or StrategyContextAdaptationService()
        if agent_tool_gateway_service is None:
            from app.domains.market.services import CalendarService, NewsService

            shared_macro_context_service = MacroContextService()
            shared_news_service = NewsService(settings=self.settings)
            shared_calendar_service = CalendarService(settings=self.settings)
            agent_tool_gateway_service = AgentToolGatewayService(
                market_data_service=self.market_data_service,
                news_service=shared_news_service,
                calendar_service=shared_calendar_service,
                macro_context_service=shared_macro_context_service,
                strategy_context_adaptation_service=self.strategy_context_adaptation_service,
            )
        self.agent_tool_gateway_service = agent_tool_gateway_service
        self.macro_context_service = self.agent_tool_gateway_service.macro_context_service
        self.market_state_service = market_state_service or MarketStateService(
            settings=self.trading_agent_service.settings,
            market_data_service=self.market_data_service,
        )
        self.decision_context_service = decision_context_service or DecisionContextService()
        self.feature_relevance_service = feature_relevance_service or FeatureRelevanceService()
        self.decision_context_assembler_service = decision_context_assembler_service or DecisionContextAssemblerService(
            settings=self.settings,
            macro_context_service=self.macro_context_service,
            strategy_context_adaptation_service=self.strategy_context_adaptation_service,
            news_service=self.agent_tool_gateway_service.news_service,
            calendar_service=self.agent_tool_gateway_service.calendar_service,
            market_data_service=self.market_data_service,
        )
        self.entry_scoring_service = entry_scoring_service or EntryScoringService()
        self.position_sizing_service = position_sizing_service or PositionSizingService()
        self.market_hours_service = USMarketHoursService()

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

    @staticmethod
    def _classify_guard_results(guard_results: dict | None) -> tuple[str | None, str]:
        if not isinstance(guard_results, dict) or not guard_results.get("blocked"):
            return None, "keep_on_watchlist"

        guard_types = {
            str(item).strip()
            for item in guard_results.get("types", [])
            if isinstance(item, str) and str(item).strip()
        }
        if "regime_policy" in guard_types:
            return "regime_policy", "skip_regime_policy"
        if "learned_rule" in guard_types:
            return "learned_rule", "skip_strategy_context_rule"
        if "portfolio_limit" in guard_types:
            return "portfolio_limit", "skip_portfolio_limit"
        if "risk_budget" in guard_types:
            return "risk_budget", "skip_risk_budget_limit"
        return "decision_layer", "keep_on_watchlist"

    @staticmethod
    def _to_market_state_read(record) -> MarketStateSnapshotRead | None:
        if record is None:
            return None
        return MarketStateSnapshotRead.model_validate(record)

    @staticmethod
    def _deferred_discovery_result(*, benchmark_ticker: str, reason: str) -> dict:
        return {
            "discovered_items": 0,
            "watchlists_scanned": 0,
            "universe_size": 0,
            "top_candidates": [],
            "benchmark_ticker": benchmark_ticker,
            "suppressed": True,
            "suppressed_reason": reason,
        }

    def _open_idle_market_scouting_tasks(
        self,
        session: Session,
        *,
        market_state_snapshot,
        items: list[WatchlistItem],
    ) -> dict:
        result = {
            "triggered": False,
            "tasks_opened": 0,
            "candidates_reviewed": 0,
            "universe_size": 0,
            "universe_source": "unavailable",
            "focus_tickers": [],
            "reason": "idle_research_disabled",
        }
        if not self.settings.idle_research_enabled:
            return result

        result["triggered"] = True
        universe_provider = getattr(self.opportunity_discovery_service, "get_candidate_universe", None)
        if callable(universe_provider):
            universe_payload = universe_provider()
            universe = [
                str(ticker).strip().upper()
                for ticker in universe_payload.get("universe", [])
                if str(ticker).strip()
            ]
            universe_source = str(universe_payload.get("universe_source") or "configured_list")
        else:
            universe = [
                ticker.strip().upper()
                for ticker in str(self.settings.opportunity_discovery_universe).split(",")
                if ticker.strip()
            ]
            universe_source = "configured_list"
        result["universe_size"] = len(universe)
        result["universe_source"] = universe_source
        if not universe:
            result["reason"] = "idle_research_no_universe"
            return result

        open_market_scouting_tasks = [
            task
            for task in self.research_service.list_tasks(session)
            if task.status in ["open", "in_progress"] and task.task_type == "market_scouting"
        ]
        available_slots = max(self.settings.idle_research_max_open_tasks - len(open_market_scouting_tasks), 0)
        if available_slots <= 0:
            result["reason"] = "idle_research_task_limit_reached"
            return result

        tracked_tickers = {item.ticker.upper() for item in items if item.ticker}
        open_tickers = {
            ticker.upper()
            for ticker in session.query(Position.ticker).filter(Position.status == "open").all()
            for ticker in ticker
            if ticker is not None
        }
        existing_task_tickers = {
            str((task.scope or {}).get("ticker") or "").strip().upper()
            for task in open_market_scouting_tasks
            if isinstance(task.scope, dict)
        }
        candidate_pool = [
            ticker
            for ticker in universe
            if ticker not in tracked_tickers and ticker not in open_tickers and ticker not in existing_task_tickers
        ]
        if not candidate_pool:
            result["reason"] = "idle_research_no_fresh_tickers"
            return result

        scan_limit = min(max(int(self.settings.idle_research_scan_limit), 1), len(candidate_pool))
        review_limit = min(max(int(self.settings.idle_research_per_cycle), 1), available_slots, scan_limit)
        reviewed_candidates: list[dict] = []
        for ticker in candidate_pool[:scan_limit]:
            try:
                snapshot = self.market_data_service.get_snapshot(ticker)
                signal = self.signal_service.analyze_ticker(ticker)
            except Exception:
                continue

            quant_summary = signal.get("quant_summary") if isinstance(signal.get("quant_summary"), dict) else {}
            visual_summary = signal.get("visual_summary") if isinstance(signal.get("visual_summary"), dict) else {}
            news_titles = self._list_idle_research_news_titles(ticker)
            event_titles = self._list_idle_research_event_titles(ticker)
            combined_score = self._coerce_float(signal.get("combined_score"))
            relative_volume = self._coerce_float(quant_summary.get("relative_volume")) or getattr(snapshot, "relative_volume", 0.0)
            month_performance = self._coerce_float(getattr(snapshot, "month_performance", None))
            setup_type = str(
                visual_summary.get("setup_type") or quant_summary.get("setup") or "unspecified"
            ).strip().lower()
            priority_score = (
                float(combined_score or 0.0)
                + min(max(float(relative_volume or 0.0), 0.0), 3.0) * 0.05
                + min(max(float(month_performance or 0.0), -0.2), 0.2) * 0.5
                + (0.03 if news_titles else 0.0)
                + (0.02 if event_titles else 0.0)
            )
            reviewed_candidates.append(
                {
                    "ticker": ticker,
                    "combined_score": combined_score,
                    "setup_type": setup_type,
                    "relative_volume": relative_volume,
                    "month_performance": month_performance,
                    "news_titles": news_titles,
                    "event_titles": event_titles,
                    "priority_score": priority_score,
                }
            )

        result["candidates_reviewed"] = len(reviewed_candidates)
        if not reviewed_candidates:
            result["reason"] = "idle_research_candidate_scan_empty"
            return result

        reviewed_candidates.sort(
            key=lambda item: (
                float(item["priority_score"]),
                float(item["combined_score"] or 0.0),
            ),
            reverse=True,
        )

        for candidate in reviewed_candidates[:review_limit]:
            _, created = self.research_service.ensure_market_scouting_task(
                session,
                ticker=candidate["ticker"],
                market_regime=market_state_snapshot.regime_label,
                setup_type=candidate["setup_type"],
                combined_score=candidate["combined_score"],
                relative_volume=candidate["relative_volume"],
                month_performance=candidate["month_performance"],
                news_titles=candidate["news_titles"],
                event_titles=candidate["event_titles"],
                universe_source=universe_source,
            )
            if created:
                result["tasks_opened"] += 1
                result["focus_tickers"].append(candidate["ticker"])

        result["reason"] = "idle_research_tasks_opened" if result["tasks_opened"] else "idle_research_no_new_tasks"
        return result

    def _list_idle_research_news_titles(self, ticker: str) -> list[str]:
        news_service = getattr(self.agent_tool_gateway_service, "news_service", None)
        if news_service is None:
            return []
        try:
            articles = news_service.list_news_for_ticker(ticker, max_results=3)
        except NewsProviderError:
            return []
        return [
            str(article.title).strip()
            for article in articles
            if getattr(article, "title", None)
        ]

    def _list_idle_research_event_titles(self, ticker: str) -> list[str]:
        calendar_service = getattr(self.agent_tool_gateway_service, "calendar_service", None)
        if calendar_service is None:
            return []
        try:
            events = calendar_service.list_ticker_events(ticker, days_ahead=14)
        except CalendarProviderError:
            return []
        return [
            f"{event.event_type}:{event.event_date}"
            for event in events
            if getattr(event, "event_type", None) and getattr(event, "event_date", None)
        ]

    @classmethod
    def _ordered_macro_research_topics(cls, session: Session) -> list[dict]:
        topics = [dict(topic) for topic in cls.MACRO_RESEARCH_TOPICS]
        if not topics:
            return []
        auto_signal_count = session.query(MemoryItem).filter(
            MemoryItem.scope == "macro",
            MemoryItem.memory_type == "macro_signal",
            MemoryItem.key.like("auto_macro:%"),
        ).count()
        offset = auto_signal_count % len(topics)
        return topics[offset:] + topics[:offset]

    @staticmethod
    def _topic_matches_macro_event(topic: dict, event) -> bool:
        keywords = [
            str(item).strip().lower()
            for item in topic.get("keywords", [])
            if str(item).strip()
        ]
        if not keywords:
            return False
        haystack = " ".join(
            [
                str(getattr(event, "title", "") or ""),
                str(getattr(event, "country", "") or ""),
                str(getattr(event, "impact", "") or ""),
                str(getattr(event, "actual", "") or ""),
                str(getattr(event, "estimate", "") or ""),
                str(getattr(event, "previous", "") or ""),
            ]
        ).lower()
        return any(keyword in haystack for keyword in keywords)

    @staticmethod
    def _serialize_macro_event(event) -> dict:
        return {
            "title": str(getattr(event, "title", "") or ""),
            "event_type": str(getattr(event, "event_type", "") or ""),
            "event_date": str(getattr(event, "event_date", "") or ""),
            "country": getattr(event, "country", None),
            "impact": getattr(event, "impact", None),
            "actual": getattr(event, "actual", None),
            "estimate": getattr(event, "estimate", None),
            "previous": getattr(event, "previous", None),
            "currency": getattr(event, "currency", None),
            "source": getattr(event, "source", None),
        }

    @staticmethod
    def _serialize_news_article(article) -> dict:
        return {
            "title": str(getattr(article, "title", "") or ""),
            "description": getattr(article, "description", None),
            "url": getattr(article, "url", None),
            "source_name": getattr(article, "source_name", None),
            "published_at": getattr(article, "published_at", None),
        }

    @staticmethod
    def _serialize_web_search_result(result) -> dict:
        return {
            "title": str(getattr(result, "title", "") or ""),
            "url": getattr(result, "url", None),
            "source": getattr(result, "source", None),
            "snippet": getattr(result, "snippet", None),
            "text_excerpt": str(getattr(result, "snippet", "") or "")[:1200],
        }

    @staticmethod
    def _serialize_article_context(result, page) -> dict:
        return {
            "title": str(getattr(page, "title", None) or getattr(result, "title", "") or ""),
            "url": getattr(result, "url", None),
            "source": getattr(page, "source", None) or getattr(result, "source", None),
            "snippet": getattr(result, "snippet", None),
            "text_excerpt": str(getattr(page, "text", "") or "")[:1200],
        }

    @staticmethod
    def _build_macro_signal_key(
        *,
        topic_slug: str,
        calendar_events: list[dict],
        news_items: list[dict],
        article_contexts: list[dict],
    ) -> str:
        fingerprint_payload = {
            "events": [
                f"{item.get('event_date')}|{item.get('title')}"
                for item in calendar_events[:3]
            ],
            "news": [
                f"{item.get('published_at')}|{item.get('title')}"
                for item in news_items[:3]
            ],
            "articles": [
                f"{item.get('url')}|{item.get('title')}"
                for item in article_contexts[:2]
            ],
        }
        fingerprint = hashlib.sha1(
            json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return f"auto_macro:{topic_slug}:{fingerprint}"

    @staticmethod
    def _macro_signal_exists(session: Session, *, signal_key: str) -> bool:
        return (
            session.query(MemoryItem.id)
            .filter(
                MemoryItem.scope == "macro",
                MemoryItem.memory_type == "macro_signal",
                MemoryItem.key == signal_key,
            )
            .first()
            is not None
        )

    def _find_watchlist_by_code(self, session: Session, *, code: str) -> Watchlist | None:
        normalized = code.strip().lower()
        for watchlist in self.watchlist_service.list_watchlists(session):
            if str(watchlist.code or "").strip().lower() == normalized:
                return watchlist
        return None

    def _ensure_macro_theme_watchlist(
        self,
        session: Session,
        *,
        theme_slug: str,
        theme_title: str,
        summary: str,
        regime: str,
        focus_assets: list[str],
        strategy_ideas: list[str],
        macro_signal_key: str,
        importance: float,
    ) -> dict:
        normalized_assets = self._dedupe_strings(
            [str(asset).strip().upper() for asset in focus_assets],
            limit=8,
        )
        if not normalized_assets:
            return {
                "watchlist": None,
                "created": False,
                "items_added": 0,
                "code": None,
            }

        normalized_slug = re.sub(r"[^a-z0-9_]+", "_", theme_slug.strip().lower()).strip("_") or "macro_theme"
        watchlist_code = f"macro_{normalized_slug}"[:50]
        watchlist_name = f"Macro Theme · {theme_title}"[:120]
        existing = self._find_watchlist_by_code(session, code=watchlist_code)
        watchlist_created = False
        if existing is None:
            existing = self.watchlist_service.create_watchlist(
                session,
                WatchlistCreate(
                    code=watchlist_code,
                    name=watchlist_name,
                    hypothesis=summary,
                    status="active",
                ),
                event_source="macro_research_lane",
            )
            watchlist_created = True

        existing_tickers = {
            str(item.ticker or "").strip().upper()
            for item in getattr(existing, "items", [])
            if str(item.ticker or "").strip()
        }
        items_added = 0
        strategy_context = self._dedupe_strings(strategy_ideas, limit=2)
        hypothesis_suffix = f" Regime: {regime}." if regime else ""
        for ticker in normalized_assets:
            normalized_ticker = str(ticker).strip().upper()
            if not normalized_ticker or normalized_ticker in existing_tickers:
                continue
            self.watchlist_service.add_item(
                session,
                existing.id,
                WatchlistItemCreate(
                    ticker=normalized_ticker,
                    strategy_hypothesis=(summary[:500] + hypothesis_suffix)[:1000],
                    reason=f"Auto-added from macro theme {theme_slug}.",
                    key_metrics={
                        "source": "macro_research_lane",
                        "theme_slug": normalized_slug,
                        "theme_title": theme_title,
                        "macro_signal_key": macro_signal_key,
                        "importance": round(float(importance), 2),
                        "regime": regime,
                        "strategy_ideas": strategy_context,
                    },
                    state="watching",
                ),
                event_source="macro_research_lane",
            )
            existing_tickers.add(normalized_ticker)
            items_added += 1

        refreshed = self._find_watchlist_by_code(session, code=watchlist_code)
        return {
            "watchlist": refreshed or existing,
            "created": watchlist_created,
            "items_added": items_added,
            "code": watchlist_code,
        }

    @staticmethod
    def _dedupe_strings(values, *, limit: int | None = None) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []
        results: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            marker = text.lower()
            if marker in seen:
                continue
            seen.add(marker)
            results.append(text)
            if limit is not None and len(results) >= limit:
                break
        return results

    def _run_macro_geopolitical_research(
        self,
        session: Session,
        *,
        market_state_snapshot,
    ) -> dict:
        result = {
            "triggered": False,
            "topics_reviewed": 0,
            "signals_recorded": 0,
            "tasks_opened": 0,
            "watchlists_created": 0,
            "watchlists_refreshed": 0,
            "watchlist_codes": [],
            "focus_themes": [],
            "focus_assets": [],
            "reason": "macro_research_disabled",
        }
        if not self.settings.macro_research_enabled:
            return result

        result["triggered"] = True
        calendar_service = getattr(self.agent_tool_gateway_service, "calendar_service", None)
        news_service = getattr(self.agent_tool_gateway_service, "news_service", None)
        web_research_service = getattr(self.agent_tool_gateway_service, "web_research_service", None)
        if calendar_service is None:
            result["reason"] = "macro_research_services_unavailable"
            return result

        try:
            macro_events = calendar_service.list_macro_events(days_ahead=self.settings.macro_research_days_ahead)
        except CalendarProviderError:
            macro_events = []

        topics = self._ordered_macro_research_topics(session)
        if not topics:
            result["reason"] = "macro_research_no_topics"
            return result

        per_cycle = max(int(self.settings.macro_research_per_cycle), 1)
        open_macro_tasks = [
            task
            for task in self.research_service.list_tasks(session)
            if task.status in ["open", "in_progress"] and task.task_type == "macro_strategy_research"
        ]
        available_task_slots = max(int(self.settings.macro_research_max_open_tasks) - len(open_macro_tasks), 0)

        for topic in topics:
            if result["topics_reviewed"] >= per_cycle:
                break

            matching_events = [
                self._serialize_macro_event(event)
                for event in macro_events
                if self._topic_matches_macro_event(topic, event)
            ][:3]
            if news_service is not None and hasattr(news_service, "list_news"):
                try:
                    news_items = [
                        self._serialize_news_article(article)
                        for article in news_service.list_news(
                            str(topic.get("query") or ""),
                            max_results=self.settings.macro_research_max_news_per_topic,
                        )
                    ]
                except NewsProviderError:
                    news_items = []
            else:
                news_items = []

            if web_research_service is not None and hasattr(web_research_service, "search"):
                try:
                    web_results = web_research_service.search(
                        str(topic.get("query") or ""),
                        max_results=self.settings.macro_research_max_web_results,
                        domains=list(topic.get("domains") or []),
                    )
                except WebResearchError:
                    web_results = []
            else:
                web_results = []

            article_contexts: list[dict] = []
            for web_result in web_results[:1]:
                if web_research_service is None or not hasattr(web_research_service, "fetch_article"):
                    article_contexts.append(self._serialize_web_search_result(web_result))
                    continue
                try:
                    page = web_research_service.fetch_article(
                        web_result.url,
                        max_chars=self.settings.macro_research_max_article_chars,
                    )
                except WebResearchError:
                    article_contexts.append(self._serialize_web_search_result(web_result))
                    continue
                article_contexts.append(self._serialize_article_context(web_result, page))

            if not matching_events and not news_items and not article_contexts:
                continue

            result["topics_reviewed"] += 1
            signal_key = self._build_macro_signal_key(
                topic_slug=str(topic.get("slug") or "macro_theme"),
                calendar_events=matching_events,
                news_items=news_items,
                article_contexts=article_contexts,
            )
            if self._macro_signal_exists(session, signal_key=signal_key):
                continue

            synthesis = self.trading_agent_service.synthesize_macro_research(
                theme=topic,
                market_context={
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                    "market_summary": market_state_snapshot.summary,
                },
                calendar_events=matching_events,
                news_items=news_items,
                article_contexts=article_contexts,
            )
            affected_assets = self._dedupe_strings(
                [str(item).strip().upper() for item in synthesis.get("affected_assets", [])],
                limit=8,
            )
            evidence_points = self._dedupe_strings(
                synthesis.get("evidence_points", [])
                + [item.get("title", "") for item in matching_events]
                + [item.get("title", "") for item in news_items],
                limit=8,
            )
            strategy_ideas = self._dedupe_strings(synthesis.get("strategy_ideas", []), limit=5)
            risk_flags = self._dedupe_strings(synthesis.get("risk_flags", []), limit=5)
            asset_impacts = [
                item
                for item in synthesis.get("asset_impacts", [])
                if isinstance(item, dict)
            ][:8]
            importance = round(
                min(
                    max(self._coerce_float(synthesis.get("importance")) or 0.6, 0.0),
                    1.0,
                ),
                2,
            )
            watchlist_result = self._ensure_macro_theme_watchlist(
                session,
                theme_slug=str(topic.get("slug") or "macro_theme"),
                theme_title=str(topic.get("title") or "Macro theme"),
                summary=str(synthesis.get("summary") or topic.get("title") or "Macro thesis"),
                regime=str(synthesis.get("regime") or topic.get("default_regime") or "macro_uncertainty"),
                focus_assets=affected_assets,
                strategy_ideas=strategy_ideas,
                macro_signal_key=signal_key,
                importance=importance,
            )
            linked_watchlist = watchlist_result["watchlist"]
            if watchlist_result["created"]:
                result["watchlists_created"] += 1
            if watchlist_result["created"] or watchlist_result["items_added"] > 0:
                result["watchlists_refreshed"] += 1
            if watchlist_result.get("code"):
                result["watchlist_codes"].append(str(watchlist_result["code"]))
            signal_payload = MacroSignalCreate(
                key=signal_key,
                content=str(synthesis.get("summary") or topic.get("title") or "Macro thesis").strip()[:4000],
                regime=str(synthesis.get("regime") or topic.get("default_regime") or "macro_uncertainty").strip()[:40],
                relevance=str(synthesis.get("relevance") or topic.get("relevance") or "cross_asset").strip()[:60],
                tickers=affected_assets,
                timeframe=str(synthesis.get("timeframe") or topic.get("timeframe") or "1D-1M").strip()[:60],
                scenario=str(synthesis.get("scenario") or topic.get("title") or "").strip()[:500] or None,
                source="macro_research_lane",
                evidence={
                    "theme_slug": topic.get("slug"),
                    "theme_title": topic.get("title"),
                    "query": topic.get("query"),
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                    "analysis_mode": synthesis.get("analysis_mode"),
                    "provider": synthesis.get("provider"),
                    "model": synthesis.get("model"),
                    "impact_hypothesis": synthesis.get("impact_hypothesis"),
                    "asset_impacts": asset_impacts,
                    "strategy_ideas": strategy_ideas,
                    "risk_flags": risk_flags,
                    "linked_watchlist_id": linked_watchlist.id if linked_watchlist is not None else None,
                    "linked_watchlist_code": getattr(linked_watchlist, "code", None),
                    "calendar_events": matching_events,
                    "news_items": news_items[:3],
                    "article_contexts": article_contexts[:1],
                    "evidence_points": evidence_points,
                },
                importance=importance,
            )
            self.macro_context_service.create_signal(session, signal_payload)
            result["signals_recorded"] += 1
            result["focus_themes"].append(str(topic.get("slug") or "macro_theme"))
            result["focus_assets"].extend(affected_assets)

            if available_task_slots > 0:
                _, created = self.research_service.ensure_macro_strategy_task(
                    session,
                    theme_slug=str(topic.get("slug") or "macro_theme"),
                    theme_title=str(topic.get("title") or "Macro theme"),
                    regime=signal_payload.regime,
                    scenario=signal_payload.scenario,
                    timeframe=signal_payload.timeframe,
                    importance=importance,
                    focus_assets=affected_assets,
                    impact_hypothesis=str(synthesis.get("impact_hypothesis") or signal_payload.content),
                    strategy_ideas=strategy_ideas,
                    evidence_points=evidence_points,
                    macro_signal_key=signal_key,
                    linked_watchlist_code=getattr(linked_watchlist, "code", None),
                    linked_watchlist_id=getattr(linked_watchlist, "id", None),
                )
                if created:
                    result["tasks_opened"] += 1
                    available_task_slots -= 1

        result["watchlist_codes"] = self._dedupe_strings(result["watchlist_codes"], limit=8)
        result["focus_assets"] = self._dedupe_strings(
            [item.upper() for item in result["focus_assets"]],
            limit=10,
        )
        if result["signals_recorded"] > 0:
            result["reason"] = "macro_research_signals_recorded"
        elif result["topics_reviewed"] > 0:
            result["reason"] = "macro_research_no_new_signal"
        else:
            result["reason"] = "macro_research_no_evidence"
        return result

    @staticmethod
    def _coerce_float(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @classmethod
    def _rsi_bucket(cls, value) -> str | None:
        rsi = cls._coerce_float(value)
        if rsi is None:
            return None
        if rsi < 40:
            return "weak"
        if rsi < 55:
            return "neutral"
        if rsi <= 70:
            return "momentum"
        return "extended"

    @classmethod
    def _build_technical_state(cls, quant_summary: dict | None) -> dict:
        payload = dict(quant_summary or {})
        price = cls._coerce_float(payload.get("price"))
        sma_20 = cls._coerce_float(payload.get("sma_20"))
        sma_50 = cls._coerce_float(payload.get("sma_50"))
        sma_200 = cls._coerce_float(payload.get("sma_200"))
        return {
            "price_above_sma20": price is not None and sma_20 is not None and price >= sma_20,
            "price_above_sma50": price is not None and sma_50 is not None and price >= sma_50,
            "price_above_sma200": price is not None and sma_200 is not None and price >= sma_200,
            "sma20_above_sma50": sma_20 is not None and sma_50 is not None and sma_20 >= sma_50,
            "sma50_above_sma200": sma_50 is not None and sma_200 is not None and sma_50 >= sma_200,
            "rsi_bucket": cls._rsi_bucket(payload.get("rsi_14")),
        }

    def _build_reanalysis_policy(
        self,
        *,
        signal_payload: dict,
        market_state_snapshot,
        due_reason: str,
    ) -> dict:
        quant_summary = dict(signal_payload.get("quant_summary") or {})
        ticker = str(signal_payload.get("ticker") or quant_summary.get("ticker") or "").strip().upper()
        entry_price = self._coerce_float(signal_payload.get("entry_price")) or self._coerce_float(quant_summary.get("price")) or 0.0
        atr_14 = self._coerce_float(quant_summary.get("atr_14")) or 0.0
        stop_price = self._coerce_float(signal_payload.get("stop_price"))
        target_price = self._coerce_float(signal_payload.get("target_price"))
        raw_threshold_pct = (atr_14 / max(entry_price, 1.0)) * 1.25 if entry_price > 0 else 0.0
        price_move_threshold_pct = round(min(max(raw_threshold_pct, 0.02), 0.08), 4)
        technical_state_source: dict = quant_summary
        if ticker:
            try:
                current_snapshot = self.market_data_service.get_snapshot(ticker)
                technical_state_source = {
                    "price": current_snapshot.price,
                    "sma_20": current_snapshot.sma_20,
                    "sma_50": current_snapshot.sma_50,
                    "sma_200": current_snapshot.sma_200,
                    "rsi_14": current_snapshot.rsi_14,
                }
            except Exception:
                technical_state_source = quant_summary
        return {
            "policy_version": "event_driven_v1",
            "criteria_summary": (
                f"Reanalyze only after a regime shift, >= {price_move_threshold_pct * 100:.1f}% price move, "
                "target/stop breach, technical structure change, fresh ticker news, or an earnings window transition."
            ),
            "initial_due_reason": due_reason,
            "anchor_time": datetime.now(timezone.utc).isoformat(),
            "anchor_price": round(entry_price, 2) if entry_price > 0 else None,
            "price_move_threshold_pct": price_move_threshold_pct,
            "recheck_above_price": round(target_price, 2) if target_price is not None else None,
            "recheck_below_price": round(stop_price, 2) if stop_price is not None else None,
            "technical_state": self._build_technical_state(technical_state_source),
            "recheck_on_regime_change": True,
            "recheck_on_technical_state_change": True,
            "recheck_on_news": True,
            "recheck_on_calendar_window": True,
            "market_state_regime": market_state_snapshot.regime_label if market_state_snapshot is not None else None,
        }

    @classmethod
    def _item_key_metrics(cls, item: WatchlistItem) -> dict:
        return dict(item.key_metrics or {}) if isinstance(item.key_metrics, dict) else {}

    @classmethod
    def _reanalysis_runtime_state(cls, item: WatchlistItem) -> dict:
        key_metrics = cls._item_key_metrics(item)
        runtime = key_metrics.get(cls.REANALYSIS_RUNTIME_KEY)
        return dict(runtime) if isinstance(runtime, dict) else {}

    def _reanalysis_interval_seconds(
        self,
        *,
        policy: dict,
        is_regular_session_open: bool,
    ) -> int:
        threshold = self._coerce_float(policy.get("price_move_threshold_pct")) or 0.0
        if is_regular_session_open:
            if threshold <= 0.025:
                return self.REANALYSIS_OPEN_SHORT_INTERVAL_SECONDS
            if threshold <= 0.04:
                return self.REANALYSIS_OPEN_MEDIUM_INTERVAL_SECONDS
            return self.REANALYSIS_OPEN_LONG_INTERVAL_SECONDS
        if threshold <= 0.025:
            return self.REANALYSIS_CLOSED_SHORT_INTERVAL_SECONDS
        if threshold <= 0.04:
            return self.REANALYSIS_CLOSED_MEDIUM_INTERVAL_SECONDS
        return self.REANALYSIS_CLOSED_LONG_INTERVAL_SECONDS

    def _scheduled_reanalysis_jitter_seconds(self, item: WatchlistItem, *, interval_seconds: int) -> int:
        configured_max_jitter = max(int(self.settings.orchestrator_scheduled_reanalysis_jitter_seconds), 0)
        if configured_max_jitter <= 0 or interval_seconds <= 0:
            return 0
        effective_max_jitter = min(configured_max_jitter, max(interval_seconds // 6, 0))
        if effective_max_jitter <= 0:
            return 0
        if item.id is not None:
            return max(int(item.id) - 1, 0) % (effective_max_jitter + 1)
        digest = hashlib.sha1(str(item.ticker or "").upper().encode("utf-8")).digest()
        return int.from_bytes(digest[:2], "big") % (effective_max_jitter + 1)

    def _schedule_watchlist_reanalysis(
        self,
        item: WatchlistItem,
        *,
        policy: dict,
        scheduled_reason: str,
        evaluated_at: datetime | None = None,
        latest_signal_created_at: datetime | None = None,
    ) -> dict:
        session_state = self.market_hours_service.get_session_state()
        evaluated_at_utc = evaluated_at.astimezone(timezone.utc) if evaluated_at is not None else datetime.now(timezone.utc)
        interval_seconds = self._reanalysis_interval_seconds(
            policy=policy,
            is_regular_session_open=bool(session_state.is_regular_session_open),
        )
        schedule_jitter_seconds = self._scheduled_reanalysis_jitter_seconds(
            item,
            interval_seconds=interval_seconds,
        )
        scheduled_delay_seconds = interval_seconds + schedule_jitter_seconds
        next_reanalysis_at = evaluated_at_utc + timedelta(seconds=scheduled_delay_seconds)
        runtime_state = {
            "version": self.REANALYSIS_RUNTIME_VERSION,
            "last_evaluated_at": evaluated_at_utc.isoformat(),
            "next_reanalysis_at": next_reanalysis_at.isoformat(),
            "check_interval_seconds": scheduled_delay_seconds,
            "base_interval_seconds": interval_seconds,
            "schedule_jitter_seconds": schedule_jitter_seconds,
            "last_gate_reason": scheduled_reason,
            "policy_version": str(policy.get("policy_version") or ""),
            "market_session_label": str(session_state.session_label or ""),
            "market_state_regime": str(policy.get("market_state_regime") or ""),
        }
        if latest_signal_created_at is not None:
            latest_signal_at_utc = (
                latest_signal_created_at.replace(tzinfo=timezone.utc)
                if latest_signal_created_at.tzinfo is None
                else latest_signal_created_at.astimezone(timezone.utc)
            )
            runtime_state["last_signal_created_at"] = latest_signal_at_utc.isoformat()
        key_metrics = self._item_key_metrics(item)
        key_metrics[self.REANALYSIS_RUNTIME_KEY] = runtime_state
        item.key_metrics = key_metrics
        return runtime_state

    def _build_scheduled_reanalysis_runtime_budget(self) -> dict:
        max_checks = max(int(self.settings.orchestrator_scheduled_reanalysis_max_checks_per_cycle), 0)
        budget_seconds = max(float(self.settings.orchestrator_scheduled_reanalysis_budget_seconds), 0.0)
        return {
            "enabled": max_checks > 0 or budget_seconds > 0,
            "cycle_started_at": perf_counter(),
            "scheduled_checks_started": 0,
            "scheduled_checks_deferred": 0,
            "max_checks_per_cycle": max_checks,
            "budget_seconds": budget_seconds,
        }

    def _maybe_defer_scheduled_reanalysis_for_runtime_budget(
        self,
        item: WatchlistItem,
        *,
        now_utc: datetime,
        runtime_state: dict,
        runtime_budget: dict | None,
    ) -> dict | None:
        if not isinstance(runtime_budget, dict) or not runtime_budget.get("enabled"):
            return None
        elapsed_seconds = max(perf_counter() - float(runtime_budget.get("cycle_started_at") or 0.0), 0.0)
        max_checks = max(int(runtime_budget.get("max_checks_per_cycle") or 0), 0)
        budget_seconds = max(float(runtime_budget.get("budget_seconds") or 0.0), 0.0)
        checks_started = max(int(runtime_budget.get("scheduled_checks_started") or 0), 0)
        budget_exhausted = (max_checks > 0 and checks_started >= max_checks) or (
            budget_seconds > 0 and elapsed_seconds >= budget_seconds
        )
        if not budget_exhausted:
            runtime_budget["scheduled_checks_started"] = checks_started + 1
            return None
        overflow_index = max(int(runtime_budget.get("scheduled_checks_deferred") or 0), 0)
        runtime_budget["scheduled_checks_deferred"] = overflow_index + 1
        deferred_state = self._defer_watchlist_reanalysis_for_runtime_budget(
            item,
            runtime_state=runtime_state,
            evaluated_at=now_utc,
            overflow_index=overflow_index,
        )
        return {
            "due": False,
            "reason": "runtime_budget_deferred",
            "details": (
                "Scheduled reanalysis deferred by runtime budget until "
                f"{deferred_state['next_reanalysis_at']}."
            ),
            "runtime_updated": True,
            "runtime_state": deferred_state,
        }

    def _defer_watchlist_reanalysis_for_runtime_budget(
        self,
        item: WatchlistItem,
        *,
        runtime_state: dict,
        evaluated_at: datetime | None = None,
        overflow_index: int = 0,
    ) -> dict:
        session_state = self.market_hours_service.get_session_state()
        evaluated_at_utc = evaluated_at.astimezone(timezone.utc) if evaluated_at is not None else datetime.now(timezone.utc)
        base_delay_seconds = max(int(self.settings.orchestrator_scheduled_reanalysis_budget_deferral_seconds), 1)
        spacing_seconds = max(int(self.settings.orchestrator_scheduled_reanalysis_budget_spacing_seconds), 0)
        delay_seconds = base_delay_seconds + (max(int(overflow_index), 0) * spacing_seconds)
        next_reanalysis_at = evaluated_at_utc + timedelta(seconds=delay_seconds)
        updated_runtime_state = {
            "version": self.REANALYSIS_RUNTIME_VERSION,
            "last_evaluated_at": evaluated_at_utc.isoformat(),
            "next_reanalysis_at": next_reanalysis_at.isoformat(),
            "check_interval_seconds": delay_seconds,
            "last_gate_reason": "runtime_budget_deferred",
            "policy_version": str(runtime_state.get("policy_version") or ""),
            "market_session_label": str(session_state.session_label or ""),
            "market_state_regime": str(runtime_state.get("market_state_regime") or ""),
            "budget_delay_seconds": delay_seconds,
            "budget_overflow_index": max(int(overflow_index), 0),
        }
        if runtime_state.get("last_signal_created_at"):
            updated_runtime_state["last_signal_created_at"] = str(runtime_state.get("last_signal_created_at"))
        key_metrics = self._item_key_metrics(item)
        key_metrics[self.REANALYSIS_RUNTIME_KEY] = updated_runtime_state
        item.key_metrics = key_metrics
        return updated_runtime_state

    @staticmethod
    def _parse_iso_datetime(value: str | None) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _parse_iso_date(value: str | None) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    def _latest_signal_for_watchlist_item(self, session: Session, *, watchlist_item_id: int) -> TradeSignal | None:
        statement = (
            select(TradeSignal)
            .where(TradeSignal.watchlist_item_id == watchlist_item_id)
            .order_by(TradeSignal.created_at.desc(), TradeSignal.id.desc())
            .limit(1)
        )
        return session.scalars(statement).first()

    def _has_fresh_news_since_signal(self, ticker: str, *, signal_created_at: datetime) -> bool:
        try:
            articles = self.agent_tool_gateway_service.news_service.list_news_for_ticker(ticker, max_results=5)
        except NewsProviderError:
            return False
        for article in articles:
            published_at = self._parse_iso_datetime(article.published_at)
            if published_at is not None and published_at > signal_created_at:
                return True
        return False

    def _has_calendar_window_transition_since_signal(self, ticker: str, *, signal_created_at: datetime) -> bool:
        try:
            events = self.agent_tool_gateway_service.calendar_service.list_ticker_events(ticker, days_ahead=14)
        except CalendarProviderError:
            return False
        for event in events:
            event_date = self._parse_iso_date(event.event_date)
            if event_date is None:
                continue
            window_open = datetime.combine(event_date - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if window_open <= now_utc and signal_created_at < window_open:
                return True
        return False

    def _assess_reanalysis_need(
        self,
        session: Session,
        *,
        item: WatchlistItem,
        market_state_snapshot,
        runtime_budget: dict | None = None,
    ) -> dict:
        latest_signal = self._latest_signal_for_watchlist_item(session, watchlist_item_id=item.id)
        if latest_signal is None:
            return {"due": True, "reason": "first_review", "details": "No prior watchlist analysis exists for this item."}

        signal_context = latest_signal.signal_context if isinstance(latest_signal.signal_context, dict) else {}
        policy = signal_context.get("reanalysis_policy") if isinstance(signal_context.get("reanalysis_policy"), dict) else {}
        if not policy:
            return {"due": True, "reason": "missing_reanalysis_policy", "details": "Latest signal has no reanalysis policy."}

        previous_regime = str(signal_context.get("market_state_regime") or policy.get("market_state_regime") or "").strip()
        if (
            policy.get("recheck_on_regime_change")
            and previous_regime
            and previous_regime != market_state_snapshot.regime_label
        ):
            return {
                "due": True,
                "reason": "regime_shift",
                "details": f"Market regime moved from {previous_regime} to {market_state_snapshot.regime_label}.",
            }

        now_utc = datetime.now(timezone.utc)
        runtime_state = self._reanalysis_runtime_state(item)
        next_reanalysis_at = self._parse_iso_datetime(runtime_state.get("next_reanalysis_at"))
        if next_reanalysis_at is not None and next_reanalysis_at > now_utc:
            return {
                "due": False,
                "reason": "scheduled_reanalysis_pending",
                "details": f"Next scheduled reanalysis is due at {next_reanalysis_at.isoformat()}.",
            }
        if next_reanalysis_at is not None:
            if (
                budget_deferral := self._maybe_defer_scheduled_reanalysis_for_runtime_budget(
                    item,
                    now_utc=now_utc,
                    runtime_state=runtime_state,
                    runtime_budget=runtime_budget,
                )
            ) is not None:
                return budget_deferral

        current_snapshot = self.market_data_service.get_snapshot(item.ticker)
        anchor_price = self._coerce_float(policy.get("anchor_price"))
        price_move_threshold_pct = self._coerce_float(policy.get("price_move_threshold_pct")) or 0.0
        if anchor_price and price_move_threshold_pct > 0:
            price_move_pct = abs((current_snapshot.price - anchor_price) / anchor_price)
            if price_move_pct >= price_move_threshold_pct:
                return {
                    "due": True,
                    "reason": "price_move_threshold",
                    "details": (
                        f"Price moved {price_move_pct * 100:.2f}% versus the last analyzed anchor "
                        f"({price_move_threshold_pct * 100:.2f}% threshold)."
                    ),
                }

        recheck_above_price = self._coerce_float(policy.get("recheck_above_price"))
        if recheck_above_price is not None and current_snapshot.price >= recheck_above_price:
            return {
                "due": True,
                "reason": "technical_trigger_above",
                "details": f"Price reached the upper reanalysis trigger at {recheck_above_price:.2f}.",
            }

        recheck_below_price = self._coerce_float(policy.get("recheck_below_price"))
        if recheck_below_price is not None and current_snapshot.price <= recheck_below_price:
            return {
                "due": True,
                "reason": "technical_trigger_below",
                "details": f"Price reached the lower reanalysis trigger at {recheck_below_price:.2f}.",
            }

        previous_technical_state = policy.get("technical_state") if isinstance(policy.get("technical_state"), dict) else {}
        current_technical_state = self._build_technical_state(
            {
                "price": current_snapshot.price,
                "sma_20": current_snapshot.sma_20,
                "sma_50": current_snapshot.sma_50,
                "sma_200": current_snapshot.sma_200,
                "rsi_14": current_snapshot.rsi_14,
            }
        )
        changed_flags = [
            key
            for key, value in current_technical_state.items()
            if previous_technical_state.get(key) != value
        ]
        if policy.get("recheck_on_technical_state_change") and changed_flags:
            return {
                "due": True,
                "reason": "technical_state_changed",
                "details": "Technical state changed for " + ", ".join(changed_flags) + ".",
            }

        signal_created_at = latest_signal.created_at
        if signal_created_at is not None and signal_created_at.tzinfo is None:
            signal_created_at = signal_created_at.replace(tzinfo=timezone.utc)
        if signal_created_at is not None and policy.get("recheck_on_news") and self._has_fresh_news_since_signal(
            item.ticker,
            signal_created_at=signal_created_at,
        ):
            return {
                "due": True,
                "reason": "fresh_news",
                "details": "Fresh ticker news arrived after the last recorded analysis.",
            }

        if signal_created_at is not None and policy.get("recheck_on_calendar_window") and self._has_calendar_window_transition_since_signal(
            item.ticker,
            signal_created_at=signal_created_at,
        ):
            return {
                "due": True,
                "reason": "calendar_window_transition",
                "details": "A ticker event entered the near-term review window after the last analysis.",
            }

        runtime_state = self._schedule_watchlist_reanalysis(
            item,
            policy=policy,
            scheduled_reason="awaiting_reanalysis_trigger",
            evaluated_at=now_utc,
            latest_signal_created_at=signal_created_at,
        )
        return {
            "due": False,
            "reason": "awaiting_reanalysis_trigger",
            "details": str(policy.get("criteria_summary") or "No reanalysis trigger fired."),
            "runtime_updated": True,
            "runtime_state": runtime_state,
        }

    def plan_daily_cycle(self, session: Session, payload: DailyPlanRequest) -> OrchestratorPlanResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_plan",
            pdca_phase="plan",
            source_context=payload.market_context,
        )
        cycle = self.pdca_service.create_daily_plan(session, payload.cycle_date)
        review_backlog = session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        open_research_tasks = len([task for task in self.research_service.list_tasks(session) if task.status in ["open", "in_progress"]])
        work_queue = self.work_queue_service.get_queue(session)
        degraded_candidate_backlog = len([item for item in work_queue.items if item.item_type == "degraded_candidate_validation"])
        cycle.context = {
            **cycle.context,
            **payload.market_context,
            "market_state_snapshot_id": market_state_snapshot.id,
            "market_state_regime": market_state_snapshot.regime_label,
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
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
            work_queue=work_queue,
        )

    def run_do_phase(self, session: Session) -> OrchestratorDoResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_do",
            pdca_phase="do",
            source_context={"execution_mode": "global"},
        )
        market_session = self.market_hours_service.get_session_state()
        market_closed = not market_session.is_regular_session_open
        scan_allowed = market_session.is_regular_session_open or self.settings.orchestrator_scan_when_market_closed
        ai_allowed = market_session.is_regular_session_open or self.settings.ai_market_closed_enabled
        discovery_allowed = market_session.is_regular_session_open or self.settings.opportunity_discovery_run_when_market_closed
        exit_result: AutoExitBatchResult = self.exit_management_service.evaluate_open_positions(session)
        if discovery_allowed:
            discovery_result = self.opportunity_discovery_service.refresh_active_watchlists(session)
        else:
            discovery_result = self._deferred_discovery_result(
                benchmark_ticker=self.settings.benchmark_ticker,
                reason=f"Opportunity discovery deferred while US market session is {market_session.session_label}.",
            )
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
                0
                if self._parse_iso_datetime(self._reanalysis_runtime_state(item).get("next_reanalysis_at")) is None
                else 1,
                self._parse_iso_datetime(self._reanalysis_runtime_state(item).get("next_reanalysis_at"))
                or datetime.min.replace(tzinfo=timezone.utc),
                item.id,
            )
        )
        candidates: list[ExecutionCandidateResult] = []
        generated_analyses = 0
        generated_signals = 0
        opened_positions = 0
        prioritized_candidate_items = 0
        ai_decisions = 0
        ai_unavailable_entries = 0
        calendar_blocked_entries = 0
        learned_rule_blocked_entries = 0
        regime_policy_blocked_entries = 0
        decision_layer_blocked_entries = 0
        portfolio_blocked_entries = 0
        risk_budget_blocked_entries = 0
        decision_context_snapshots = 0
        deferred_reanalysis_entries = 0
        deferred_market_closed_entries = 0
        ai_suppressed_market_closed_entries = 0
        market_closed_entry_deferred_entries = 0
        deferred_reanalysis_runtime_updates = 0
        idle_research_result = {
            "triggered": False,
            "tasks_opened": 0,
            "candidates_reviewed": 0,
            "universe_size": 0,
            "universe_source": "unavailable",
            "focus_tickers": [],
            "reason": "idle_research_not_needed",
        }
        macro_research_result = {
            "triggered": False,
            "topics_reviewed": 0,
            "signals_recorded": 0,
            "tasks_opened": 0,
            "watchlists_created": 0,
            "watchlists_refreshed": 0,
            "watchlist_codes": [],
            "focus_themes": [],
            "focus_assets": [],
            "reason": "macro_research_not_run",
        }
        scheduled_reanalysis_runtime_budget = self._build_scheduled_reanalysis_runtime_budget()
        runtime_budget_deferred_entries = 0

        for item in items:
            ticker_started_at = perf_counter()
            timing_profile: dict[str, object] = {
                "version": "ticker_analysis_timing_v1",
                "stages_ms": {},
            }

            def record_stage_timing(stage: str, stage_started_at: float) -> None:
                stage_timings = timing_profile.get("stages_ms")
                if not isinstance(stage_timings, dict):
                    stage_timings = {}
                    timing_profile["stages_ms"] = stage_timings
                stage_timings[stage] = round((perf_counter() - stage_started_at) * 1000, 2)

            stage_started_at = perf_counter()
            reanalysis_gate = self._assess_reanalysis_need(
                session,
                item=item,
                market_state_snapshot=market_state_snapshot,
                runtime_budget=scheduled_reanalysis_runtime_budget,
            )
            record_stage_timing("reanalysis_gate", stage_started_at)
            if not reanalysis_gate["due"]:
                if reanalysis_gate.get("runtime_updated"):
                    session.add(item)
                    deferred_reanalysis_runtime_updates += 1
                if reanalysis_gate["reason"] == "runtime_budget_deferred":
                    runtime_budget_deferred_entries += 1
                deferred_reanalysis_entries += 1
                continue
            if market_closed and not scan_allowed and reanalysis_gate["reason"] in {
                "first_review",
                "missing_reanalysis_policy",
            }:
                deferred_market_closed_entries += 1
                continue

            watchlist = session.get(Watchlist, item.watchlist_id)
            strategy = session.get(Strategy, watchlist.strategy_id) if watchlist and watchlist.strategy_id is not None else None
            strategy_version_id, using_candidate_version = self._get_execution_version(strategy)
            if using_candidate_version:
                prioritized_candidate_items += 1
            stage_started_at = perf_counter()
            signal = self.signal_service.analyze_ticker(item.ticker)
            record_stage_timing("signal_analysis", stage_started_at)
            signal["base_combined_score"] = signal.get("combined_score")
            signal["base_decision"] = signal.get("decision")
            market_context = {
                "watchlist_id": watchlist.id if watchlist is not None else None,
                "execution_mode": "candidate_validation" if using_candidate_version else "default",
                "market_state_snapshot_id": market_state_snapshot.id,
                "market_state_regime": market_state_snapshot.regime_label,
                "opened_positions_so_far": opened_positions,
            }
            stage_started_at = perf_counter()
            decision_context = self.decision_context_assembler_service.build_trade_candidate_context(
                session,
                ticker=item.ticker,
                strategy_id=strategy.id if strategy is not None else None,
                strategy_version_id=strategy_version_id,
                signal_payload=signal,
                market_context=market_context,
            )
            record_stage_timing("decision_context", stage_started_at)
            timing_profile["decision_context_timing"] = (
                dict(decision_context.get("timing_profile") or {})
                if isinstance(decision_context.get("timing_profile"), dict)
                else {}
            )
            stage_started_at = perf_counter()
            entry_score = self.entry_scoring_service.evaluate(
                signal_payload=signal,
                decision_context=decision_context,
            )
            record_stage_timing("deterministic_scoring", stage_started_at)
            signal["decision_context"] = decision_context
            signal["price_action_context"] = decision_context.get("price_action_context") or signal.get("price_action_context")
            signal["intermarket_context"] = decision_context.get("intermarket_context")
            signal["mstr_context"] = decision_context.get("mstr_context")
            signal["skill_context"] = decision_context.get("skill_context")
            signal["expiry_context"] = (
                (decision_context.get("calendar_context") or {}).get("expiry_context")
                if isinstance(decision_context.get("calendar_context"), dict)
                else None
            )
            signal["risk_budget"] = decision_context.get("risk_budget")
            signal["regime_policy"] = decision_context.get("regime_policy")
            signal["score_breakdown"] = entry_score["score_breakdown"]
            signal["guard_results"] = entry_score["guard_results"]
            signal["combined_score"] = entry_score["final_score"]
            signal["decision_confidence"] = entry_score["final_score"]
            signal["decision"] = entry_score["recommended_action"]
            signal["rationale"] = f"{signal['rationale']} {entry_score['summary']}"
            signal["reanalysis_due_reason"] = reanalysis_gate["reason"]
            signal["reanalysis_due_details"] = reanalysis_gate["details"]
            stage_started_at = perf_counter()
            research_package = self.trading_agent_service.build_trade_candidate_research_package(
                ticker=item.ticker,
                strategy_version_id=strategy_version_id,
                signal_payload=signal,
                entry_context=market_context,
            )
            record_stage_timing("research_package", stage_started_at)
            signal["research_plan"] = research_package.get("research_plan")
            signal["decision_trace"] = research_package.get("decision_trace")
            initial_decision_source = "deterministic_scoring"
            pre_entry_guard_category = None
            if entry_score["guard_results"]["blocked"]:
                pre_entry_guard_category, _ = self._classify_guard_results(entry_score["guard_results"])
                if pre_entry_guard_category == "learned_rule":
                    learned_rule_blocked_entries += 1
                elif pre_entry_guard_category == "regime_policy":
                    regime_policy_blocked_entries += 1
                elif pre_entry_guard_category == "portfolio_limit":
                    portfolio_blocked_entries += 1
                elif pre_entry_guard_category == "risk_budget":
                    risk_budget_blocked_entries += 1
                else:
                    decision_layer_blocked_entries += 1
                initial_decision_source = f"deterministic_{pre_entry_guard_category or 'guard'}"

            ai_decision = None
            ai_decision_error: str | None = None
            if not entry_score["guard_results"]["blocked"]:
                if ai_allowed:
                    stage_started_at = perf_counter()
                    try:
                        ai_decision = self.trading_agent_service.advise_trade_candidate(
                            session,
                            ticker=item.ticker,
                            strategy_id=strategy.id if strategy is not None else None,
                            strategy_version_id=strategy_version_id,
                            watchlist_code=watchlist.code if watchlist is not None else None,
                            signal_payload=signal,
                            market_context=market_context,
                        )
                    except AIDecisionError as exc:
                        ai_decision_error = str(exc)
                        ai_unavailable_entries += 1
                        signal["rationale"] = f"{signal['rationale']} AI unavailable; kept deterministic decision."
                        signal["ai_overlay"] = {
                            "provider": self.trading_agent_service.runtime.provider,
                            "model": self.trading_agent_service.runtime.model,
                            "status": "unavailable",
                            "error": ai_decision_error,
                            "fallback_to": signal["decision"],
                        }
                    finally:
                        record_stage_timing("ai_review", stage_started_at)
                else:
                    ai_suppressed_market_closed_entries += 1
                    timing_profile["ai_review_state"] = "suppressed_market_closed"
                    signal["ai_overlay"] = {
                        "provider": self.trading_agent_service.runtime.provider,
                        "model": self.trading_agent_service.runtime.model,
                        "status": "suppressed_market_closed",
                        "reason": (
                            "AI candidate review deferred while the regular US market session is closed."
                        ),
                        "fallback_to": signal["decision"],
                    }
            else:
                timing_profile["ai_review_state"] = "skipped_guard_blocked"
            if ai_decision is not None:
                ai_decisions += 1
                timing_profile["ai_review_state"] = "completed"
                signal["decision"] = ai_decision.action
                signal["decision_confidence"] = round(
                    min(
                        max(
                            (
                                float(signal.get("decision_confidence", signal["combined_score"]))
                                + ai_decision.confidence
                            )
                            / 2,
                            0.0,
                        ),
                        1.0,
                    ),
                    2,
                )
                signal["rationale"] = f"{signal['rationale']} AI thesis: {ai_decision.thesis}"
                signal["ai_overlay"] = {
                    "provider": self.trading_agent_service.runtime.provider,
                    "model": self.trading_agent_service.runtime.model,
                    "action": ai_decision.action,
                    "confidence": ai_decision.confidence,
                    "thesis": ai_decision.thesis,
                    "risks": ai_decision.risks,
                    "lessons_applied": ai_decision.lessons_applied,
                }
            market_closed_entry_guard: dict | None = None
            if signal["decision"] == "paper_enter" and market_closed and not self.settings.paper_entry_when_market_closed:
                market_closed_entry_deferred_entries += 1
                market_closed_entry_guard = {
                    "reason": "market_closed_execution_policy",
                    "summary": (
                        f"Entry deferred because the US market session is {market_session.session_label}; "
                        "research remains enabled, but paper entries are disabled while the market is closed."
                    ),
                    "session_label": market_session.session_label,
                    "next_regular_open": market_session.next_regular_open,
                }
                signal["decision"] = "watch"
                signal["rationale"] = f"{signal['rationale']} {market_closed_entry_guard['summary']}"
                signal["market_closed_execution_policy"] = {
                    "entry_allowed": False,
                    **market_closed_entry_guard,
                }
                initial_decision_source = "market_closed_policy"
            sizing_decision_source: str | None = None
            if signal["decision"] == "paper_enter":
                stage_started_at = perf_counter()
                sizing_result = self.position_sizing_service.size_trade_candidate(
                    signal_payload=signal,
                    decision_context=decision_context,
                )
                record_stage_timing("position_sizing", stage_started_at)
                signal["risk_budget"] = sizing_result.get("risk_budget")
                signal["position_sizing"] = sizing_result.get("position_sizing")
                signal["rationale"] = f"{signal['rationale']} {sizing_result['summary']}"
                if sizing_result.get("blocked"):
                    signal["decision"] = "watch"
                    guard_results = signal.get("guard_results") if isinstance(signal.get("guard_results"), dict) else {}
                    existing_reasons = [
                        str(item)
                        for item in guard_results.get("reasons", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    existing_types = [
                        str(item)
                        for item in guard_results.get("types", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    existing_advisories = [
                        str(item)
                        for item in guard_results.get("advisories", [])
                        if isinstance(item, str) and item.strip()
                    ]
                    signal["guard_results"] = {
                        "blocked": True,
                        "reasons": existing_reasons + [str(sizing_result["summary"])],
                        "types": existing_types + ["risk_budget"],
                        "advisories": existing_advisories,
                    }
                    risk_budget_blocked_entries += 1
                    sizing_decision_source = "risk_budget"
                else:
                    signal["size"] = signal["position_sizing"]["size"]
                    effective_stop_price = signal["position_sizing"].get("effective_stop_price")
                    if isinstance(effective_stop_price, (int, float)):
                        signal["stop_price"] = float(effective_stop_price)
            else:
                stage_timings = timing_profile.get("stages_ms")
                if isinstance(stage_timings, dict) and "position_sizing" not in stage_timings:
                    stage_timings["position_sizing"] = 0.0
            stage_timings = timing_profile.get("stages_ms")
            if isinstance(stage_timings, dict) and "ai_review" not in stage_timings:
                stage_timings["ai_review"] = 0.0
            signal["decision_trace"] = self.trading_agent_service.finalize_trade_candidate_trace(
                decision_trace=signal.get("decision_trace"),
                final_action=signal["decision"],
                final_reason=signal["rationale"],
                decision_source=(
                    sizing_decision_source
                    or (
                        "ai_overlay"
                        if ai_decision is not None
                        else ("deterministic_ai_unavailable" if ai_decision_error is not None else initial_decision_source)
                    )
                ),
                confidence=signal.get("decision_confidence"),
                ai_thesis=ai_decision.thesis if ai_decision is not None else None,
            )
            stage_started_at = perf_counter()
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
            record_stage_timing("analysis_persist", stage_started_at)
            generated_analyses += 1
            primary_setup_id = item.setup_id or (watchlist.setup_id if watchlist is not None else None)
            primary_hypothesis_id = (
                watchlist.hypothesis_id
                if watchlist is not None and watchlist.hypothesis_id is not None
                else (strategy.hypothesis_id if strategy is not None else None)
            )
            primary_signal_definition_id = self._resolve_primary_signal_definition_id(
                session,
                setup_type=str(signal["visual_summary"].get("setup_type") or signal["quant_summary"].get("setup") or ""),
                price_action_context=signal.get("price_action_context"),
            )
            signal["reanalysis_policy"] = self._build_reanalysis_policy(
                signal_payload=signal,
                market_state_snapshot=market_state_snapshot,
                due_reason=reanalysis_gate["reason"],
            )
            elapsed_before_signal_ms = round((perf_counter() - ticker_started_at) * 1000, 2)
            stage_timings = timing_profile.get("stages_ms")
            if isinstance(stage_timings, dict):
                slowest_stage = max(stage_timings.items(), key=lambda item: item[1]) if stage_timings else None
                timing_profile["total_ms"] = elapsed_before_signal_ms
                timing_profile["slowest_stage"] = slowest_stage[0] if slowest_stage is not None else None
                timing_profile["slowest_stage_ms"] = slowest_stage[1] if slowest_stage is not None else None
            signal["hypothesis_id"] = primary_hypothesis_id
            signal["setup_id"] = primary_setup_id
            signal["signal_definition_id"] = primary_signal_definition_id
            signal["timing_profile"] = timing_profile
            stage_started_at = perf_counter()
            signal_record = self.signal_service.create_trade_signal_with_source(
                session,
                SignalCreate(
                    hypothesis_id=primary_hypothesis_id,
                    strategy_id=strategy.id if strategy is not None else None,
                    strategy_version_id=strategy_version_id,
                    setup_id=primary_setup_id,
                    signal_definition_id=primary_signal_definition_id,
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
                        "base_combined_score": signal.get("base_combined_score"),
                        "base_decision": signal.get("base_decision"),
                        "decision_context": signal.get("decision_context"),
                        "expiry_context": signal.get("expiry_context"),
                        "price_action_context": signal.get("price_action_context"),
                        "intermarket_context": signal.get("intermarket_context"),
                        "mstr_context": signal.get("mstr_context"),
                        "skill_context": signal.get("skill_context"),
                        "risk_budget": signal.get("risk_budget"),
                        "position_sizing": signal.get("position_sizing"),
                        "research_plan": signal.get("research_plan"),
                        "decision_trace": signal.get("decision_trace"),
                        "reanalysis_due_reason": signal.get("reanalysis_due_reason"),
                        "reanalysis_due_details": signal.get("reanalysis_due_details"),
                        "reanalysis_policy": signal.get("reanalysis_policy"),
                        "score_breakdown": signal.get("score_breakdown"),
                        "guard_results": signal.get("guard_results"),
                        "ai_overlay": signal.get("ai_overlay"),
                        "timing_profile": signal.get("timing_profile"),
                        "market_closed_execution_policy": signal.get("market_closed_execution_policy"),
                        "regime_policy": signal.get("regime_policy"),
                        "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                        "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                        "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                        "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                        "market_state_snapshot_id": market_state_snapshot.id,
                        "market_state_regime": market_state_snapshot.regime_label,
                        "execution_mode": "candidate_validation" if using_candidate_version else "default",
                    },
                    quality_score=signal["combined_score"],
                    status="new",
                ),
                event_source="orchestrator_do",
            )
            self._schedule_watchlist_reanalysis(
                item,
                policy=signal["reanalysis_policy"],
                scheduled_reason=signal["reanalysis_policy"]["initial_due_reason"],
                latest_signal_created_at=signal_record.created_at if signal_record.created_at is not None else None,
            )
            record_stage_timing("signal_persist", stage_started_at)
            generated_signals += 1

            existing_open = session.scalar(
                select(Position).where(
                    Position.ticker == item.ticker,
                    Position.status == "open",
                    Position.strategy_version_id == strategy_version_id,
                )
            )
            position_id: int | None = None
            execution_guard: dict | None = None
            step_results: list[dict] = []
            execution_plan_timing: dict[str, object] = {}
            opening_reason = (
                "Autonomous entry from candidate validation."
                if using_candidate_version
                else "Autonomous entry from orchestrator DO phase."
            )
            stage_started_at = perf_counter()
            planned_entry = self.trading_agent_service.plan_trade_candidate_execution(
                ticker=item.ticker,
                strategy_version_id=strategy_version_id,
                signal_id=signal_record.id,
                analysis_run_id=analysis.id,
                signal_payload=signal,
                entry_context={
                    "source": "orchestrator_do",
                    "watchlist_item_id": item.id,
                    "quant_summary": signal["quant_summary"],
                    "visual_summary": signal["visual_summary"],
                    "risk_reward": signal["risk_reward"],
                    "decision_context": signal.get("decision_context"),
                    "expiry_context": signal.get("expiry_context"),
                    "price_action_context": signal.get("price_action_context"),
                    "intermarket_context": signal.get("intermarket_context"),
                    "mstr_context": signal.get("mstr_context"),
                    "skill_context": signal.get("skill_context"),
                    "risk_budget": signal.get("risk_budget"),
                    "position_sizing": signal.get("position_sizing"),
                    "research_plan": signal.get("research_plan"),
                    "decision_trace": signal.get("decision_trace"),
                    "score_breakdown": signal.get("score_breakdown"),
                    "guard_results": signal.get("guard_results"),
                    "ai_overlay": signal.get("ai_overlay"),
                    "timing_profile": signal.get("timing_profile"),
                    "regime_policy": signal.get("regime_policy"),
                    "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                    "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                    "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                    "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                    "execution_mode": "candidate_validation" if using_candidate_version else "default",
                },
                opening_reason=opening_reason,
            )
            record_stage_timing("execution_plan_build", stage_started_at)
            stage_timings = timing_profile.get("stages_ms")
            if isinstance(stage_timings, dict) and "execution_plan_run" not in stage_timings:
                stage_timings["execution_plan_run"] = 0.0
            final_decision = signal["decision"]
            if planned_entry.should_execute and any(step.tool_name == "positions.open" for step in planned_entry.steps) and existing_open is None:
                stage_started_at = perf_counter()
                step_results = self.agent_tool_gateway_service.execute_plan(session, planned_entry)
                record_stage_timing("execution_plan_run", stage_started_at)
                elapsed_values = [
                    float(step.get("elapsed_ms"))
                    for step in step_results
                    if isinstance(step.get("elapsed_ms"), (int, float))
                ]
                slowest_step = max(
                    (
                        (str(step.get("tool_name")), float(step.get("elapsed_ms")))
                        for step in step_results
                        if isinstance(step.get("tool_name"), str) and isinstance(step.get("elapsed_ms"), (int, float))
                    ),
                    key=lambda item: item[1],
                    default=None,
                )
                execution_plan_timing = {
                    "total_ms": round(sum(elapsed_values), 2),
                    "step_count": len(step_results),
                    "slowest_tool": slowest_step[0] if slowest_step is not None else None,
                    "slowest_tool_ms": slowest_step[1] if slowest_step is not None else None,
                    "steps": [
                        {
                            "tool_name": step.get("tool_name"),
                            "status": step.get("status"),
                            "elapsed_ms": step.get("elapsed_ms"),
                        }
                        for step in step_results
                    ],
                }
                open_step = next((step for step in step_results if step["tool_name"] == "positions.open"), None)
                position_result = open_step["result"] if open_step is not None else None
                if position_result is not None and not position_result.get("skipped"):
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="executed",
                        event_source="orchestrator_do",
                    )
                    position_id = position_result["id"]
                    opened_positions += 1
                    item.state = "entered"
                    journal_decision = "open_paper_position"
                    journal_outcome = "executed"
                else:
                    execution_guard = position_result
                    guard_summary = (
                        execution_guard.get("summary")
                        if isinstance(execution_guard, dict)
                        else "Entry plan did not open a position."
                    )
                    if isinstance(execution_guard, dict):
                        signal["rationale"] = f"{signal['rationale']} {guard_summary}"
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="new",
                        event_source="orchestrator_do",
                    )
                    item.state = "watching"
                    final_decision = "watch"
                    guard_reason = execution_guard.get("reason") if isinstance(execution_guard, dict) else None
                    if guard_reason == "strategy_context_rule":
                        learned_rule_blocked_entries += 1
                        journal_decision = "skip_strategy_context_rule"
                    elif guard_reason == "regime_policy":
                        regime_policy_blocked_entries += 1
                        journal_decision = "skip_regime_policy"
                    elif guard_reason == "portfolio_limit":
                        portfolio_blocked_entries += 1
                        journal_decision = "skip_portfolio_limit"
                    elif guard_reason == "risk_budget_limit":
                        risk_budget_blocked_entries += 1
                        journal_decision = "skip_risk_budget_limit"
                    else:
                        calendar_blocked_entries += 1
                        journal_decision = (
                            "skip_calendar_check_failed"
                            if guard_reason == "calendar_check_failed"
                            else "skip_calendar_risk"
                        )
                    journal_outcome = "watching"
            elif planned_entry.action == "discard":
                self.signal_service.update_trade_signal_status_with_source(
                    session,
                    signal_record.id,
                    status="rejected",
                    rejection_reason="signal_below_threshold",
                    event_source="orchestrator_do",
                )
                item.state = "discarded"
                final_decision = "discard"
                journal_decision = "discard_signal"
                journal_outcome = "rejected"
            else:
                if market_closed_entry_guard is not None:
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="new",
                        event_source="orchestrator_do",
                    )
                    item.state = "watching"
                    final_decision = "watch"
                    journal_decision = "defer_market_closed_entry"
                    journal_outcome = "watching"
                elif existing_open is not None and planned_entry.action == "paper_enter":
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="rejected",
                        rejection_reason="existing_open_position",
                        event_source="orchestrator_do",
                    )
                    item.state = "entered"
                    final_decision = "watch"
                    journal_decision = "skip_existing_open_position"
                    journal_outcome = "rejected"
                else:
                    pre_entry_guard_category, pre_entry_guard_journal_decision = self._classify_guard_results(
                        signal.get("guard_results")
                    )
                    self.signal_service.update_trade_signal_status_with_source(
                        session,
                        signal_record.id,
                        status="new",
                        event_source="orchestrator_do",
                    )
                    item.state = "watching"
                    final_decision = "watch"
                    journal_decision = pre_entry_guard_journal_decision
                    journal_outcome = "watching"

            signal["decision_trace"] = self.trading_agent_service.finalize_trade_candidate_trace(
                decision_trace=signal.get("decision_trace"),
                final_action=final_decision,
                final_reason=signal["rationale"],
                decision_source=(
                    "market_closed_policy"
                    if market_closed_entry_guard is not None
                    else
                    "execution_guard"
                    if execution_guard is not None
                    else "ai_overlay"
                    if ai_decision is not None
                    else initial_decision_source
                ),
                confidence=signal.get("decision_confidence"),
                ai_thesis=(signal.get("ai_overlay") or {}).get("thesis") if isinstance(signal.get("ai_overlay"), dict) else None,
                execution_outcome=final_decision,
            )
            signal_record.signal_context = {
                **dict(signal_record.signal_context or {}),
                "decision": signal.get("decision"),
                "decision_confidence": signal.get("decision_confidence"),
                "risk_budget": signal.get("risk_budget"),
                "position_sizing": signal.get("position_sizing"),
                "research_plan": signal.get("research_plan"),
                "decision_trace": signal.get("decision_trace"),
                "expiry_context": signal.get("expiry_context"),
                "price_action_context": signal.get("price_action_context"),
                "intermarket_context": signal.get("intermarket_context"),
                "mstr_context": signal.get("mstr_context"),
                "skill_context": signal.get("skill_context"),
                "reanalysis_due_reason": signal.get("reanalysis_due_reason"),
                "reanalysis_due_details": signal.get("reanalysis_due_details"),
                "reanalysis_policy": signal.get("reanalysis_policy"),
                "timing_profile": timing_profile,
                "execution_plan_timing": execution_plan_timing,
                "market_closed_execution_policy": signal.get("market_closed_execution_policy"),
                "regime_policy": signal.get("regime_policy"),
                "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                "market_state_snapshot_id": market_state_snapshot.id,
                "market_state_regime": market_state_snapshot.regime_label,
                "final_decision": final_decision,
            }
            session.add(signal_record)
            stage_started_at = perf_counter()
            self.decision_context_service.record_trade_candidate_context(
                session,
                signal=signal_record,
                analysis_run=analysis,
                ticker=item.ticker,
                planned_entry_action=planned_entry.action,
                final_decision=final_decision,
                step_results=step_results,
                position_id=position_id,
                execution_guard=execution_guard,
            )
            record_stage_timing("decision_context_record", stage_started_at)
            decision_context_snapshots += 1

            total_elapsed_ms = round((perf_counter() - ticker_started_at) * 1000, 2)
            stage_timings = timing_profile.get("stages_ms")
            if isinstance(stage_timings, dict):
                timing_profile["total_ms_before_journal"] = total_elapsed_ms
                slowest_stage = max(stage_timings.items(), key=lambda item: item[1]) if stage_timings else None
                timing_profile["slowest_stage"] = slowest_stage[0] if slowest_stage is not None else None
                timing_profile["slowest_stage_ms"] = slowest_stage[1] if slowest_stage is not None else None

            stage_started_at = perf_counter()
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
                        "market_state_snapshot_id": market_state_snapshot.id,
                        "market_state_regime": market_state_snapshot.regime_label,
                        "execution_mode": "candidate_validation" if using_candidate_version else "default",
                        "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                        "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                        "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                    },
                    hypothesis=watchlist.hypothesis if watchlist is not None else None,
                    observations={
                        "watchlist_item_id": item.id,
                        "signal_id": signal_record.id,
                        "score": signal["combined_score"],
                        "risk_reward": signal["risk_reward"],
                        "alpha_gap_pct": signal.get("alpha_gap_pct"),
                        "risk_budget": signal.get("risk_budget"),
                        "position_sizing": signal.get("position_sizing"),
                        "research_plan": signal.get("research_plan"),
                        "decision_trace": signal.get("decision_trace"),
                        "expiry_context": signal.get("expiry_context"),
                        "price_action_context": signal.get("price_action_context"),
                        "intermarket_context": signal.get("intermarket_context"),
                        "mstr_context": signal.get("mstr_context"),
                        "skill_context": signal.get("skill_context"),
                        "reanalysis_due_reason": signal.get("reanalysis_due_reason"),
                        "reanalysis_due_details": signal.get("reanalysis_due_details"),
                        "reanalysis_policy": signal.get("reanalysis_policy"),
                        "market_closed_execution_policy": signal.get("market_closed_execution_policy"),
                        "score_breakdown": signal.get("score_breakdown"),
                        "guard_results": signal.get("guard_results"),
                        "decision_context": signal.get("decision_context"),
                        "ai_overlay": signal.get("ai_overlay"),
                        "timing_profile": timing_profile,
                        "execution_plan_timing": execution_plan_timing,
                        "regime_policy": signal.get("regime_policy"),
                        "policy_version": (signal.get("regime_policy") or {}).get("policy_version"),
                        "allowed_playbooks": (signal.get("regime_policy") or {}).get("allowed_playbooks"),
                        "blocked_reason": (signal.get("regime_policy") or {}).get("blocked_reason"),
                        "risk_multiplier": (signal.get("regime_policy") or {}).get("risk_multiplier"),
                        "execution_guard": execution_guard,
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
            record_stage_timing("journal_persist", stage_started_at)

            total_elapsed_ms = round((perf_counter() - ticker_started_at) * 1000, 2)
            stage_timings = timing_profile.get("stages_ms")
            if isinstance(stage_timings, dict):
                slowest_stage = max(stage_timings.items(), key=lambda item: item[1]) if stage_timings else None
                timing_profile["total_ms"] = total_elapsed_ms
                timing_profile["slowest_stage"] = slowest_stage[0] if slowest_stage is not None else None
                timing_profile["slowest_stage_ms"] = slowest_stage[1] if slowest_stage is not None else None
            timing_profile["execution_plan_timing"] = execution_plan_timing
            signal_record.signal_context = {
                **dict(signal_record.signal_context or {}),
                "timing_profile": timing_profile,
                "execution_plan_timing": execution_plan_timing,
            }
            session.add(signal_record)

            session.add(item)
            session.commit()

            candidates.append(
                ExecutionCandidateResult(
                    ticker=item.ticker,
                    watchlist_item_id=item.id,
                    analysis_run_id=analysis.id,
                    signal_id=signal_record.id,
                    trade_signal_id=signal_record.id,
                    decision=final_decision,
                    score=signal["combined_score"],
                    position_id=position_id,
                )
            )

        if deferred_reanalysis_runtime_updates > 0:
            session.commit()

        if (
            discovery_allowed
            and generated_analyses == 0
            and discovery_result["discovered_items"] == 0
            and (not items or deferred_reanalysis_entries == len(items))
        ):
            idle_research_result = self._open_idle_market_scouting_tasks(
                session,
                market_state_snapshot=market_state_snapshot,
                items=items,
            )
        macro_research_result = self._run_macro_geopolitical_research(
            session,
            market_state_snapshot=market_state_snapshot,
        )

        open_positions = session.query(Position).filter(Position.status == "open").count()
        metrics = {
            "active_watchlists": active_watchlists,
            "watchlist_items": len(items),
            "discovered_items": discovery_result["discovered_items"],
            "watchlists_scanned": discovery_result["watchlists_scanned"],
            "discovery_universe_size": discovery_result["universe_size"],
            "prioritized_candidate_items": prioritized_candidate_items,
            "ai_decisions": ai_decisions,
            "ai_unavailable_entries": ai_unavailable_entries,
            "decision_layer_blocked_entries": decision_layer_blocked_entries,
            "calendar_blocked_entries": calendar_blocked_entries,
            "learned_rule_blocked_entries": learned_rule_blocked_entries,
            "regime_policy_blocked_entries": regime_policy_blocked_entries,
            "portfolio_blocked_entries": portfolio_blocked_entries,
            "risk_budget_blocked_entries": risk_budget_blocked_entries,
            "decision_context_snapshots": decision_context_snapshots,
            "deferred_reanalysis_entries": deferred_reanalysis_entries,
            "runtime_budget_deferred_entries": runtime_budget_deferred_entries,
            "scheduled_reanalysis_checks_started": scheduled_reanalysis_runtime_budget["scheduled_checks_started"],
            "scheduled_reanalysis_checks_deferred": scheduled_reanalysis_runtime_budget["scheduled_checks_deferred"],
            "deferred_market_closed_entries": deferred_market_closed_entries,
            "ai_suppressed_market_closed_entries": ai_suppressed_market_closed_entries,
            "market_closed_entry_deferred_entries": market_closed_entry_deferred_entries,
            "idle_research_triggered": idle_research_result["triggered"],
            "idle_research_tasks_opened": idle_research_result["tasks_opened"],
            "idle_research_candidates_reviewed": idle_research_result["candidates_reviewed"],
            "idle_research_universe_size": idle_research_result["universe_size"],
            "idle_research_universe_source": idle_research_result["universe_source"],
            "idle_research_reason": idle_research_result["reason"],
            "idle_research_focus_tickers": idle_research_result["focus_tickers"],
            "macro_research_triggered": macro_research_result["triggered"],
            "macro_research_topics_reviewed": macro_research_result["topics_reviewed"],
            "macro_research_signals_recorded": macro_research_result["signals_recorded"],
            "macro_research_tasks_opened": macro_research_result["tasks_opened"],
            "macro_research_watchlists_created": macro_research_result["watchlists_created"],
            "macro_research_watchlists_refreshed": macro_research_result["watchlists_refreshed"],
            "macro_research_watchlist_codes": macro_research_result["watchlist_codes"],
            "macro_research_reason": macro_research_result["reason"],
            "macro_research_focus_themes": macro_research_result["focus_themes"],
            "macro_research_focus_assets": macro_research_result["focus_assets"],
            "generated_analyses": generated_analyses,
            "generated_signals": generated_signals,
            "opened_positions": opened_positions,
            "open_positions": open_positions,
            "auto_exit_evaluated": exit_result.evaluated_positions,
            "auto_exit_closed": exit_result.closed_positions,
            "auto_exit_adjusted": exit_result.adjusted_positions,
            "market_session": market_session.to_payload(),
        }
        summary = (
            f"DO phase processed {len(items)} watchlist items, generated {generated_analyses} analyses "
            f"opened {opened_positions} paper positions, discovered {discovery_result['discovered_items']} new "
            f"opportunities, prioritized {prioritized_candidate_items} candidate-validation items and "
            f"applied {ai_decisions} AI overlays, degraded {ai_unavailable_entries} entries due to AI unavailability, "
            f"deferred {deferred_reanalysis_entries} entries awaiting explicit reanalysis triggers, "
            f"deferred {runtime_budget_deferred_entries} scheduled reanalysis checks under runtime budget, "
            f"deferred {deferred_market_closed_entries} entries because the US market session is {market_session.session_label}, "
            f"deferred {market_closed_entry_deferred_entries} paper entries until the market reopens, "
            f"and suppressed {ai_suppressed_market_closed_entries} AI reviews while the market was closed, "
            f"blocked {decision_layer_blocked_entries} decision-layer entries, "
            f"blocked {calendar_blocked_entries} calendar-risk entries, blocked {learned_rule_blocked_entries} "
            f"learned-rule entries, blocked {portfolio_blocked_entries} portfolio-limit entries, "
            f"blocked {risk_budget_blocked_entries} risk-budget entries, "
            f"opened {idle_research_result['tasks_opened']} idle market-scouting tasks after reviewing "
            f"{idle_research_result['candidates_reviewed']} fresh tickers, "
            f"recorded {macro_research_result['signals_recorded']} macro research signals across "
            f"{macro_research_result['topics_reviewed']} themes, opened "
            f"{macro_research_result['tasks_opened']} macro strategy tasks, and created or refreshed "
            f"{macro_research_result['watchlists_refreshed']} thematic watchlists, "
            f"while auto-closing {exit_result.closed_positions} positions and updating risk on "
            f"{exit_result.adjusted_positions} open positions."
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
                    "idle_research_focus_tickers": idle_research_result["focus_tickers"],
                    "macro_research_focus_themes": macro_research_result["focus_themes"],
                    "macro_research_focus_assets": macro_research_result["focus_assets"],
                    "macro_research_watchlist_codes": macro_research_result["watchlist_codes"],
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                    "market_session": market_session.to_payload(),
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
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
            exits=exit_result,
            discovery=discovery_result,
        )

    @staticmethod
    def _resolve_primary_signal_definition_id(
        session: Session,
        *,
        setup_type: str,
        price_action_context: dict | None = None,
    ) -> int | None:
        if isinstance(price_action_context, dict):
            primary_signal_code = str(price_action_context.get("primary_signal_code") or "").strip()
            if primary_signal_code:
                signal_definition = session.scalars(
                    select(SignalDefinition).where(SignalDefinition.code == primary_signal_code)
                ).first()
                if signal_definition is not None:
                    return signal_definition.id

        normalized = setup_type.strip().lower()
        code_map = {
            "breakout": "breakout_trigger",
            "pullback": "pullback_resume_confirmation",
            "consolidation": "trend_context_filter",
            "range": "trend_context_filter",
        }
        code = code_map.get(normalized)
        if code is None:
            return None
        signal_definition = session.scalars(select(SignalDefinition).where(SignalDefinition.code == code)).first()
        return signal_definition.id if signal_definition is not None else None

    def run_check_phase(self, session: Session) -> OrchestratorPhaseResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_check",
            pdca_phase="check",
            source_context={"execution_mode": "global"},
        )
        auto_review_result = self.auto_review_service.generate_pending_loss_reviews(session)
        failure_patterns = self.failure_analysis_service.refresh_patterns(session)
        scorecards = self.strategy_scoring_service.recalculate_all(session)
        feature_stats_generated = self.feature_relevance_service.recompute_all(session)
        strategy_context_rules_generated = self.strategy_context_adaptation_service.refresh_rules(session)
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
        decision_context_snapshots = session.query(DecisionContextSnapshot).count()

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
            "decision_context_snapshots": decision_context_snapshots,
            "feature_stats_generated": feature_stats_generated,
            "strategy_context_rules_generated": strategy_context_rules_generated,
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
                market_context={
                    "benchmark_ticker": "SPY",
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                },
                observations=metrics,
                reasoning=summary,
                decision="review_outcomes",
                lessons=(
                    "Prioritize strategy changes that improve alpha relative to the benchmark without paying for it "
                    "through excessive drawdown."
                ),
            ),
        )
        return OrchestratorPhaseResponse(
            phase="check",
            status="completed",
            summary=summary,
            metrics=metrics,
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
        )

    def run_act_phase(self, session: Session) -> OrchestratorActResponse:
        market_state_snapshot = self.market_state_service.capture_snapshot(
            session,
            trigger="orchestrator_act",
            pdca_phase="act",
            source_context={"execution_mode": "global"},
        )
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
                market_context={
                    "market_state_snapshot_id": market_state_snapshot.id,
                    "market_state_regime": market_state_snapshot.regime_label,
                },
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
            market_state_snapshot=self._to_market_state_read(market_state_snapshot),
        )
