from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.position import Position
from app.db.models.signal import TradeSignal
from app.domains.learning.claims import KnowledgeClaimService
from app.domains.learning.skills import SkillLifecycleService


@dataclass(frozen=True)
class RuntimeMemoryRequest:
    ticker: str | None = None
    strategy_version_id: int | None = None
    skill_context: dict | None = None


@dataclass(frozen=True)
class RuntimeMemorySource:
    key: str
    packet_key: str
    budget_key: str
    loader: Callable[[Session, RuntimeMemoryRequest], dict]


class LearningRuntimeMemoryService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        skill_lifecycle_service: SkillLifecycleService | None = None,
        knowledge_claim_service: KnowledgeClaimService | None = None,
        sources: list[RuntimeMemorySource] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.skill_lifecycle_service = skill_lifecycle_service or SkillLifecycleService()
        self.knowledge_claim_service = knowledge_claim_service or KnowledgeClaimService()
        self.sources = list(sources or self._default_sources())

    def build_selection(
        self,
        session: Session,
        *,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
        skill_context: dict | None = None,
    ) -> dict:
        request = RuntimeMemoryRequest(
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            skill_context=skill_context,
        )
        packets_by_key: dict[str, list[dict]] = {}
        budgets_by_key: dict[str, dict] = {}

        for source in self.sources:
            selection = source.loader(session, request)
            packets_by_key[source.packet_key] = [
                item for item in selection.get("packets", []) if isinstance(item, dict)
            ]
            budgets_by_key[source.budget_key] = dict(selection.get("budget") or {})

        return {
            **packets_by_key,
            "context_budget": self._build_context_budget(budgets_by_key),
        }

    def inspect_selection(
        self,
        session: Session,
        *,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
        skill_codes: list[str] | None = None,
        phase: str | None = None,
    ) -> dict:
        normalized_ticker = str(ticker or "").strip().upper() or None
        normalized_skill_codes = self._normalize_skill_codes(skill_codes)
        resolved_skill_context, source = self._resolve_skill_context(
            session,
            ticker=normalized_ticker,
            strategy_version_id=strategy_version_id,
        )
        resolved_skill_context = self._merge_explicit_skill_codes(
            resolved_skill_context,
            skill_codes=normalized_skill_codes,
            phase=phase,
        )
        effective_strategy_version_id = strategy_version_id
        if effective_strategy_version_id is None and isinstance(source.get("strategy_version_id"), int):
            effective_strategy_version_id = int(source["strategy_version_id"])
        selection = self.build_selection(
            session,
            ticker=normalized_ticker,
            strategy_version_id=effective_strategy_version_id,
            skill_context=resolved_skill_context,
        )
        return {
            "ticker": normalized_ticker,
            "strategy_version_id": effective_strategy_version_id,
            "requested_skill_codes": normalized_skill_codes,
            "resolved_skill_context": resolved_skill_context,
            "skill_context_source": source,
            **selection,
        }

    def _default_sources(self) -> list[RuntimeMemorySource]:
        return [
            RuntimeMemorySource(
                key="skills",
                packet_key="runtime_skills",
                budget_key="runtime_skills",
                loader=self._load_runtime_skills,
            ),
            RuntimeMemorySource(
                key="claims",
                packet_key="runtime_claims",
                budget_key="runtime_claims",
                loader=self._load_runtime_claims,
            ),
            RuntimeMemorySource(
                key="distillations",
                packet_key="runtime_distillations",
                budget_key="runtime_distillations",
                loader=self._load_runtime_distillations,
            ),
        ]

    def _load_runtime_skills(self, session: Session, request: RuntimeMemoryRequest) -> dict:
        return self.skill_lifecycle_service.build_runtime_selection(
            session,
            skill_context=request.skill_context,
            max_packets=self.settings.ai_runtime_skill_limit,
            max_steps_per_packet=self.settings.ai_runtime_skill_step_limit,
        )

    def _load_runtime_claims(self, session: Session, request: RuntimeMemoryRequest) -> dict:
        return self.knowledge_claim_service.build_runtime_selection(
            session,
            ticker=request.ticker,
            strategy_version_id=request.strategy_version_id,
            max_packets=self.settings.ai_runtime_claim_limit,
            max_evidence_per_packet=self.settings.ai_runtime_claim_evidence_limit,
        )

    def _load_runtime_distillations(self, session: Session, request: RuntimeMemoryRequest) -> dict:
        from app.domains.learning.services import LearningMemoryDistillationService

        return LearningMemoryDistillationService().build_runtime_selection(
            session,
            ticker=request.ticker,
            strategy_version_id=request.strategy_version_id,
            max_packets=min(max(int(self.settings.ai_runtime_claim_limit or 0), 0), 2),
        )

    def _build_context_budget(self, budgets: dict[str, dict]) -> dict:
        normalized_skill_budget = dict(budgets.get("runtime_skills") or {})
        normalized_claim_budget = dict(budgets.get("runtime_claims") or {})
        normalized_distillation_budget = dict(budgets.get("runtime_distillations") or {})
        available_runtime_skill_count = int(normalized_skill_budget.get("available_count") or 0)
        loaded_runtime_skill_count = int(normalized_skill_budget.get("loaded_count") or 0)
        available_runtime_claim_count = int(normalized_claim_budget.get("available_count") or 0)
        loaded_runtime_claim_count = int(normalized_claim_budget.get("loaded_count") or 0)
        available_runtime_distillation_count = int(normalized_distillation_budget.get("available_count") or 0)
        loaded_runtime_distillation_count = int(normalized_distillation_budget.get("loaded_count") or 0)
        return {
            "runtime_skills_enabled": bool(normalized_skill_budget.get("enabled")),
            "max_runtime_skills": self.settings.ai_runtime_skill_limit,
            "max_steps_per_skill": self.settings.ai_runtime_skill_step_limit,
            "available_runtime_skill_count": available_runtime_skill_count,
            "loaded_runtime_skill_count": loaded_runtime_skill_count,
            "truncated_runtime_skill_count": max(available_runtime_skill_count - loaded_runtime_skill_count, 0),
            "runtime_claims_enabled": bool(normalized_claim_budget.get("enabled")),
            "max_runtime_claims": self.settings.ai_runtime_claim_limit,
            "max_evidence_per_claim": self.settings.ai_runtime_claim_evidence_limit,
            "available_runtime_claim_count": available_runtime_claim_count,
            "loaded_runtime_claim_count": loaded_runtime_claim_count,
            "truncated_runtime_claim_count": max(available_runtime_claim_count - loaded_runtime_claim_count, 0),
            "runtime_distillations_enabled": bool(normalized_distillation_budget.get("enabled")),
            "max_runtime_distillations": int(normalized_distillation_budget.get("max_packets") or 0),
            "available_runtime_distillation_count": available_runtime_distillation_count,
            "loaded_runtime_distillation_count": loaded_runtime_distillation_count,
            "truncated_runtime_distillation_count": max(
                available_runtime_distillation_count - loaded_runtime_distillation_count,
                0,
            ),
            "runtime_skills": normalized_skill_budget,
            "runtime_claims": normalized_claim_budget,
            "runtime_distillations": normalized_distillation_budget,
            "available_runtime_item_count": (
                available_runtime_skill_count
                + available_runtime_claim_count
                + available_runtime_distillation_count
            ),
            "loaded_runtime_item_count": (
                loaded_runtime_skill_count
                + loaded_runtime_claim_count
                + loaded_runtime_distillation_count
            ),
            "policy": (
                "load only context-relevant procedural skills, durable claims and reviewed distillation digests; "
                "reserve most model context for live market evidence"
            ),
        }

    def _resolve_skill_context(
        self,
        session: Session,
        *,
        ticker: str | None,
        strategy_version_id: int | None,
    ) -> tuple[dict, dict]:
        signal = self._latest_signal_with_skill_context(
            session,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
        )
        if signal is not None:
            return (
                self._extract_signal_skill_context(signal),
                {
                    "source_type": "signal_context",
                    "signal_id": signal.id,
                    "position_id": None,
                    "ticker": signal.ticker,
                    "strategy_version_id": signal.strategy_version_id,
                    "timestamp": self._datetime_to_iso(signal.signal_time or signal.created_at),
                    "summary": str(signal.signal_type or "signal").strip() or "signal",
                },
            )

        position = self._latest_position_with_skill_context(
            session,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
        )
        if position is not None:
            return (
                self._extract_position_skill_context(position),
                {
                    "source_type": "position_context",
                    "signal_id": None,
                    "position_id": position.id,
                    "ticker": position.ticker,
                    "strategy_version_id": position.strategy_version_id,
                    "timestamp": self._datetime_to_iso(position.entry_date),
                    "summary": str(position.thesis or "position").strip() or "position",
                },
            )

        return (
            {},
            {
                "source_type": "none",
                "signal_id": None,
                "position_id": None,
                "ticker": ticker,
                "strategy_version_id": strategy_version_id,
                "timestamp": None,
                "summary": "No persisted skill_context found for this request.",
            },
        )

    @staticmethod
    def _normalize_skill_codes(skill_codes: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for code in skill_codes or []:
            value = str(code or "").strip()
            if not value or value in normalized:
                continue
            normalized.append(value)
        return normalized

    def _merge_explicit_skill_codes(
        self,
        skill_context: dict | None,
        *,
        skill_codes: list[str],
        phase: str | None,
    ) -> dict:
        context = dict(skill_context or {})
        if not skill_codes:
            return context

        considered = [dict(item) for item in context.get("considered_skills", []) if isinstance(item, dict)]
        applied = [dict(item) for item in context.get("applied_skills", []) if isinstance(item, dict)]
        seen_considered = {str(item.get("code") or "").strip() for item in considered}
        seen_applied = {str(item.get("code") or "").strip() for item in applied}

        for code in skill_codes:
            if code not in seen_considered:
                considered.append({"code": code})
                seen_considered.add(code)
            if code not in seen_applied:
                applied.append(
                    {
                        "code": code,
                        "reason": "operator runtime memory inspection",
                        "confidence": 0.5,
                    }
                )
                seen_applied.add(code)

        normalized_phase = str(phase or context.get("phase") or "do").strip() or "do"
        return {
            "catalog_version": context.get("catalog_version") or "skills_v1",
            "routing_mode": context.get("routing_mode") or "runtime_memory_inspect_v1",
            "phase": normalized_phase,
            "considered_skills": considered,
            "applied_skills": applied,
            "primary_skill_code": context.get("primary_skill_code") or (skill_codes[0] if skill_codes else None),
            "summary": context.get("summary") or "Runtime memory inspection context.",
            **context,
        }

    @staticmethod
    def _latest_signal_with_skill_context(
        session: Session,
        *,
        ticker: str | None,
        strategy_version_id: int | None,
    ) -> TradeSignal | None:
        statement = select(TradeSignal)
        if ticker:
            statement = statement.where(TradeSignal.ticker == ticker)
        if strategy_version_id is not None:
            statement = statement.where(TradeSignal.strategy_version_id == strategy_version_id)
        statement = statement.order_by(
            desc(TradeSignal.signal_time),
            desc(TradeSignal.created_at),
            desc(TradeSignal.id),
        )
        for signal in session.scalars(statement.limit(20)).all():
            if LearningRuntimeMemoryService._extract_signal_skill_context(signal):
                return signal
        return None

    @staticmethod
    def _latest_position_with_skill_context(
        session: Session,
        *,
        ticker: str | None,
        strategy_version_id: int | None,
    ) -> Position | None:
        statement = select(Position)
        if ticker:
            statement = statement.where(Position.ticker == ticker)
        if strategy_version_id is not None:
            statement = statement.where(Position.strategy_version_id == strategy_version_id)
        statement = statement.order_by(desc(Position.entry_date), desc(Position.id))
        for position in session.scalars(statement.limit(20)).all():
            if LearningRuntimeMemoryService._extract_position_skill_context(position):
                return position
        return None

    @staticmethod
    def _extract_signal_skill_context(signal: TradeSignal) -> dict:
        payload = signal.signal_context if isinstance(signal.signal_context, dict) else {}
        direct = payload.get("skill_context")
        if isinstance(direct, dict) and direct:
            return dict(direct)
        decision_context = payload.get("decision_context")
        if isinstance(decision_context, dict):
            nested = decision_context.get("skill_context")
            if isinstance(nested, dict) and nested:
                return dict(nested)
        return {}

    @staticmethod
    def _extract_position_skill_context(position: Position) -> dict:
        payload = position.entry_context if isinstance(position.entry_context, dict) else {}
        management_context = payload.get("management_context")
        if isinstance(management_context, dict):
            nested = management_context.get("skill_context")
            if isinstance(nested, dict) and nested:
                return dict(nested)
        direct = payload.get("skill_context")
        if isinstance(direct, dict) and direct:
            return dict(direct)
        return {}

    @staticmethod
    def _datetime_to_iso(value: datetime | None) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else None
