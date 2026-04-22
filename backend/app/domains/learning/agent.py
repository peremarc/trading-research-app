from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.failure_pattern import FailurePattern
from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.db.models.position import Position
from app.domains.learning.claims import KnowledgeClaimService
from app.domains.learning.planning import ResearchPlannerService
from app.domains.learning.protocol import (
    DECISION_PROTOCOL_VERSION,
    build_candidate_decision_system_prompt,
    build_candidate_protocol_context,
    build_position_management_protocol_context,
    build_position_management_system_prompt,
    candidate_state_transition_for_action,
    candidate_decision_schema,
    management_state_transition_for_action,
    position_management_schema,
)
from app.domains.learning.runtime_memory import LearningRuntimeMemoryService
from app.domains.learning.skills import SkillLifecycleService
from app.domains.learning.world_state import MarketStateService
from app.providers.llm import (
    JSONDecisionProvider,
    LLMProviderError,
    LLMProviderSpec,
    build_json_decision_provider,
    normalize_provider_name,
    provider_is_ready,
)


class AIDecisionError(RuntimeError):
    pass


@dataclass
class AgentDecision:
    action: str
    confidence: float
    thesis: str
    risks: list[str]
    lessons_applied: list[str]
    raw_payload: dict
    decision_label: str | None = None
    operating_state: str | None = None
    next_state: str | None = None
    regime: str | None = None
    active_playbook: str | None = None
    entry_trigger: str | None = None
    invalidation: str | None = None
    risk_assessment: str | None = None
    reasons_not_to_act: list[str] = field(default_factory=list)
    claims_applied: list[str] = field(default_factory=list)


@dataclass
class AgentToolStep:
    tool_name: str | None = None
    arguments: dict | None = None
    purpose: str | None = None


@dataclass
class AgentActionPlan:
    action: str
    confidence: float
    rationale: str
    steps: list[AgentToolStep]
    should_execute: bool = False


@dataclass
class AgentRuntimeState:
    enabled: bool
    provider: str
    model: str | None
    ready: bool
    decision_protocol_version: str = DECISION_PROTOCOL_VERSION
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_ready: bool = False
    active_provider: str | None = None
    active_model: str | None = None
    last_decision_provider: str | None = None
    last_decision_at: datetime | None = None
    last_decision_action: str | None = None
    last_decision_summary: str | None = None
    decision_count: int = 0
    fallback_count: int = 0
    last_error: str | None = None
    cooldown_until: datetime | None = None


@dataclass(frozen=True)
class ProviderSlot:
    slot_label: str
    provider_name: str
    model_name: str
    provider: JSONDecisionProvider
    counts_as_fallback: bool = False


class AutonomousTradingAgentService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.research_planner_service = ResearchPlannerService()
        self.market_state_service = MarketStateService(settings=self.settings)
        self.skill_lifecycle_service = SkillLifecycleService()
        self.knowledge_claim_service = KnowledgeClaimService()
        self.runtime_memory_service = LearningRuntimeMemoryService(
            settings=self.settings,
            skill_lifecycle_service=self.skill_lifecycle_service,
            knowledge_claim_service=self.knowledge_claim_service,
        )
        provider_slots = self._build_provider_slots()
        primary_provider = self._primary_provider_name()
        primary_model = self._primary_model_name(primary_provider)
        fallback_provider_name = self._fallback_provider_name()
        fallback_model_name = self._fallback_model_name(fallback_provider_name)
        primary_ready = any(slot.provider_name == primary_provider for slot in provider_slots)
        fallback_ready = any(slot.provider_name == fallback_provider_name for slot in provider_slots)
        self.runtime = AgentRuntimeState(
            enabled=self.settings.ai_agent_enabled,
            provider=primary_provider,
            model=primary_model,
            ready=primary_ready or fallback_ready,
            fallback_provider=fallback_provider_name,
            fallback_model=fallback_model_name,
            fallback_ready=fallback_ready,
            active_provider=provider_slots[0].provider_name if provider_slots else None,
            active_model=provider_slots[0].model_name if provider_slots else None,
        )
        self.provider_slots = provider_slots

    def reset_runtime_state(self) -> None:
        self.runtime.last_decision_at = None
        self.runtime.last_decision_action = None
        self.runtime.last_decision_summary = None
        self.runtime.decision_count = 0
        self.runtime.fallback_count = 0
        self.runtime.last_decision_provider = None
        self.runtime.last_error = None
        self.runtime.cooldown_until = None
        self.provider_slots = self._build_provider_slots()
        primary_provider = self._primary_provider_name()
        primary_model = self._primary_model_name(primary_provider)
        fallback_provider_name = self._fallback_provider_name()
        fallback_model_name = self._fallback_model_name(fallback_provider_name)
        primary_ready = any(slot.provider_name == primary_provider for slot in self.provider_slots)
        fallback_ready = any(slot.provider_name == fallback_provider_name for slot in self.provider_slots)
        self.runtime.provider = primary_provider
        self.runtime.model = primary_model
        self.runtime.ready = primary_ready or fallback_ready
        self.runtime.fallback_provider = fallback_provider_name
        self.runtime.fallback_model = fallback_model_name
        self.runtime.fallback_ready = fallback_ready
        self.runtime.active_provider = self.provider_slots[0].provider_name if self.provider_slots else None
        self.runtime.active_model = self.provider_slots[0].model_name if self.provider_slots else None

    def get_status_payload(self) -> dict:
        return {
            "enabled": self.runtime.enabled,
            "provider": self.runtime.provider,
            "model": self.runtime.model,
            "ready": self.runtime.ready,
            "decision_protocol_version": self.runtime.decision_protocol_version,
            "fallback_provider": self.runtime.fallback_provider,
            "fallback_model": self.runtime.fallback_model,
            "fallback_ready": self.runtime.fallback_ready,
            "active_provider": self.runtime.active_provider,
            "active_model": self.runtime.active_model,
            "last_decision_provider": self.runtime.last_decision_provider,
            "last_decision_at": self.runtime.last_decision_at.isoformat() if self.runtime.last_decision_at else None,
            "last_decision_action": self.runtime.last_decision_action,
            "last_decision_summary": self.runtime.last_decision_summary,
            "decision_count": self.runtime.decision_count,
            "fallback_count": self.runtime.fallback_count,
            "calls_last_hour": 0,
            "calls_today": 0,
            "last_error": self.runtime.last_error,
            "cooldown_until": self.runtime.cooldown_until.isoformat() if self.runtime.cooldown_until else None,
        }

    def advise_trade_candidate(
        self,
        session: Session,
        *,
        ticker: str,
        strategy_id: int | None,
        strategy_version_id: int | None,
        watchlist_code: str | None,
        signal_payload: dict,
        market_context: dict | None = None,
    ) -> AgentDecision | None:
        if not self.runtime.enabled:
            return None
        if not self.runtime.ready:
            message = "AI decision engine is enabled but not configured. Review AI provider settings and credentials."
            self.runtime.last_error = message
            raise AIDecisionError(message)

        context = self._build_decision_context(
            session,
            ticker=ticker,
            strategy_id=strategy_id,
            strategy_version_id=strategy_version_id,
            watchlist_code=watchlist_code,
            signal_payload=signal_payload,
            market_context=market_context or {},
        )
        user_prompt = json.dumps(context, ensure_ascii=True)

        raw_decision, used_provider, used_model = self._decide_with_fallback(
            system_prompt=build_candidate_decision_system_prompt(
                self._build_runtime_skill_prompt(context.get("agent_protocol")),
                self._build_runtime_supporting_memory_prompt(context.get("agent_protocol")),
            ),
            user_prompt=user_prompt,
            response_json_schema=candidate_decision_schema(),
        )
        decision = self._parse_decision(raw_decision)
        self._persist_decision(
            session,
            ticker=ticker,
            strategy_id=strategy_id,
            strategy_version_id=strategy_version_id,
            watchlist_code=watchlist_code,
            signal_payload=signal_payload,
            market_context=market_context or {},
            decision=decision,
            runtime_skills=self._extract_runtime_skills(context),
            runtime_claims=self._extract_runtime_claims(context),
            runtime_distillations=self._extract_runtime_distillations(context),
            context_budget=self._extract_context_budget(context),
        )
        self.runtime.last_decision_at = datetime.now(timezone.utc)
        self.runtime.last_decision_provider = used_provider
        self.runtime.active_provider = used_provider
        self.runtime.active_model = used_model
        self.runtime.last_decision_action = decision.action
        self.runtime.last_decision_summary = decision.thesis
        self.runtime.decision_count += 1
        self.runtime.last_error = None
        return decision

    def synthesize_macro_research(
        self,
        *,
        theme: dict,
        market_context: dict,
        calendar_events: list[dict],
        news_items: list[dict],
        article_contexts: list[dict],
    ) -> dict:
        fallback = self._heuristic_macro_research(
            theme=theme,
            market_context=market_context,
            calendar_events=calendar_events,
            news_items=news_items,
            article_contexts=article_contexts,
        )
        if not self.runtime.enabled or not self.runtime.ready:
            return fallback

        payload = {
            "theme": theme,
            "market_context": market_context,
            "calendar_events": calendar_events,
            "news_items": news_items,
            "article_contexts": article_contexts,
        }
        try:
            raw_result, used_provider, used_model = self._decide_with_fallback(
                system_prompt=self._build_macro_research_system_prompt(),
                user_prompt=json.dumps(payload, ensure_ascii=True),
                response_json_schema=self._macro_research_schema(),
            )
        except AIDecisionError as exc:
            return {
                **fallback,
                "analysis_mode": "heuristic_fallback",
                "provider": self.runtime.provider,
                "model": self.runtime.model,
                "ai_error": str(exc),
            }

        normalized = self._normalize_macro_research_result(raw_result, fallback=fallback)
        normalized["analysis_mode"] = "ai"
        normalized["provider"] = used_provider
        normalized["model"] = used_model
        return normalized

    def build_trade_candidate_research_package(
        self,
        *,
        ticker: str,
        strategy_version_id: int | None,
        signal_payload: dict,
        entry_context: dict | None = None,
    ) -> dict:
        return self.research_planner_service.build_trade_candidate_package(
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            signal_payload=signal_payload,
            entry_context=entry_context,
        )

    def finalize_trade_candidate_trace(
        self,
        *,
        decision_trace: dict | None,
        final_action: str,
        final_reason: str,
        decision_source: str,
        confidence: float | None = None,
        ai_thesis: str | None = None,
        execution_outcome: str | None = None,
    ) -> dict:
        return self.research_planner_service.finalize_trade_candidate_trace(
            decision_trace=decision_trace,
            final_action=final_action,
            final_reason=final_reason,
            decision_source=decision_source,
            confidence=confidence,
            ai_thesis=ai_thesis,
            execution_outcome=execution_outcome,
        )

    def plan_trade_candidate_execution(
        self,
        *,
        ticker: str,
        strategy_version_id: int | None,
        signal_id: int | None,
        analysis_run_id: int | None,
        signal_payload: dict,
        entry_context: dict,
        opening_reason: str,
    ) -> AgentActionPlan:
        action = str(signal_payload.get("decision") or "watch")
        confidence = round(float(signal_payload.get("decision_confidence") or signal_payload.get("combined_score") or 0.0), 2)
        rationale = str(signal_payload.get("rationale") or "No rationale available.")
        research_package = self.build_trade_candidate_research_package(
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            signal_payload=signal_payload,
            entry_context=entry_context,
        )
        selected_steps = [
            AgentToolStep(
                tool_name=str(step.get("tool_name") or "").strip() or None,
                arguments=dict(step.get("arguments") or {}),
                purpose=str(step.get("purpose") or "").strip() or None,
            )
            for step in research_package.get("selected_steps", [])
            if isinstance(step, dict)
        ]

        if action != "paper_enter":
            return AgentActionPlan(
                action=action,
                confidence=confidence,
                rationale=rationale,
                steps=[],
                should_execute=False,
            )

        return AgentActionPlan(
            action=action,
            confidence=confidence,
            rationale=rationale,
            steps=selected_steps
            + [
                AgentToolStep(
                    tool_name="positions.open",
                    arguments={
                        "ticker": ticker,
                        "hypothesis_id": signal_payload.get("hypothesis_id"),
                        "signal_id": signal_id,
                        "trade_signal_id": signal_id,
                        "setup_id": signal_payload.get("setup_id"),
                        "signal_definition_id": signal_payload.get("signal_definition_id"),
                        "strategy_version_id": strategy_version_id,
                        "analysis_run_id": analysis_run_id,
                        "account_mode": "paper",
                        "side": "long",
                        "entry_price": signal_payload.get("entry_price"),
                        "stop_price": signal_payload.get("stop_price"),
                        "target_price": signal_payload.get("target_price"),
                        "size": (
                            (signal_payload.get("position_sizing") or {}).get("size")
                            if isinstance(signal_payload.get("position_sizing"), dict)
                            else signal_payload.get("size")
                        )
                        or signal_payload.get("size")
                        or 1,
                        "thesis": rationale,
                        "opening_reason": opening_reason,
                        "entry_context": entry_context,
                    },
                    purpose="open_simulated_position",
                ),
            ],
            should_execute=True,
        )

    def advise_open_position_management(
        self,
        session: Session,
        *,
        position: Position,
        market_snapshot: dict,
    ) -> AgentDecision | None:
        if not self.runtime.enabled:
            return None
        if not self.runtime.ready:
            message = "AI decision engine is enabled but not configured. Review AI provider settings and credentials."
            self.runtime.last_error = message
            raise AIDecisionError(message)

        context = self._build_open_position_context(
            session,
            position=position,
            market_snapshot=market_snapshot,
        )
        user_prompt = json.dumps(context, ensure_ascii=True)

        raw_decision, used_provider, used_model = self._decide_with_fallback(
            system_prompt=build_position_management_system_prompt(
                self._build_runtime_skill_prompt(context.get("agent_protocol")),
                self._build_runtime_supporting_memory_prompt(context.get("agent_protocol")),
            ),
            user_prompt=user_prompt,
            response_json_schema=position_management_schema(),
        )
        decision = self._parse_decision(
            raw_decision,
            allowed_actions={
                "hold",
                "tighten_stop",
                "extend_target",
                "tighten_stop_and_extend_target",
                "close_position",
            },
        )
        self._persist_management_decision(
            session,
            position=position,
            market_snapshot=market_snapshot,
            decision=decision,
            provider=used_provider,
            model=used_model,
            runtime_skills=self._extract_runtime_skills(context),
            runtime_claims=self._extract_runtime_claims(context),
            runtime_distillations=self._extract_runtime_distillations(context),
            context_budget=self._extract_context_budget(context),
        )
        self.runtime.last_decision_at = datetime.now(timezone.utc)
        self.runtime.last_decision_provider = used_provider
        self.runtime.active_provider = used_provider
        self.runtime.active_model = used_model
        self.runtime.last_decision_action = decision.action
        self.runtime.last_decision_summary = decision.thesis
        self.runtime.decision_count += 1
        self.runtime.last_error = None
        return decision

    def plan_open_position_management_execution(
        self,
        *,
        position: Position,
        market_snapshot: dict,
        decision: AgentDecision | None,
    ) -> AgentActionPlan | None:
        if decision is None:
            return None

        if decision.action == "hold":
            return AgentActionPlan(
                action=decision.action,
                confidence=decision.confidence,
                rationale=decision.thesis,
                steps=[],
                should_execute=False,
            )

        if decision.action == "close_position":
            pnl_pct = None
            if position.entry_price:
                if position.side == "long":
                    pnl_pct = round(((market_snapshot["price"] - position.entry_price) / position.entry_price) * 100, 2)
                else:
                    pnl_pct = round(((position.entry_price - market_snapshot["price"]) / position.entry_price) * 100, 2)
            return AgentActionPlan(
                action=decision.action,
                confidence=decision.confidence,
                rationale=decision.thesis,
                steps=[
                    AgentToolStep(
                        tool_name="positions.list_open",
                        arguments={},
                        purpose="review_open_positions_before_ai_close",
                    ),
                    AgentToolStep(
                        tool_name="positions.close",
                        arguments={
                            "position_id": position.id,
                            "exit_price": market_snapshot["price"],
                            "exit_reason": "ai_management_exit",
                            "max_drawdown_pct": round(min(pnl_pct or 0.0, 0.0), 2) if pnl_pct is not None else None,
                            "max_runup_pct": round(max(pnl_pct or 0.0, 0.0), 2) if pnl_pct is not None else None,
                            "close_context": {
                                "source": "ai_position_management",
                                "ai_action": decision.action,
                                "ai_thesis": decision.thesis,
                                "ai_risks": decision.risks,
                            },
                        },
                        purpose="close_position_from_ai_management",
                    ),
                ],
                should_execute=True,
            )

        manage_arguments = {
            "position_id": position.id,
            "event_type": "risk_update",
            "observed_price": market_snapshot["price"],
            "rationale": decision.thesis,
            "management_context": {
                "source": "ai_position_management",
                "ai_action": decision.action,
                "ai_risks": decision.risks,
                "market_snapshot": market_snapshot,
            },
        }
        if decision.action in {"tighten_stop", "tighten_stop_and_extend_target"}:
            manage_arguments["stop_price"] = round(max(position.entry_price, market_snapshot["price"] - market_snapshot["atr_14"]), 2)
        if decision.action in {"extend_target", "tighten_stop_and_extend_target"} and position.target_price is not None:
            manage_arguments["target_price"] = round(max(position.target_price, market_snapshot["price"] + (2 * market_snapshot["atr_14"])), 2)

        return AgentActionPlan(
            action=decision.action,
            confidence=decision.confidence,
            rationale=decision.thesis,
            steps=[
                AgentToolStep(
                    tool_name="positions.list_open",
                    arguments={},
                    purpose="review_open_positions_before_ai_management",
                ),
                AgentToolStep(
                    tool_name="positions.manage",
                    arguments=manage_arguments,
                    purpose="apply_ai_position_management",
                ),
            ],
            should_execute=True,
        )

    def _build_provider(
        self,
        *,
        provider: str,
        model: str | None,
        api_key: str | None,
        api_base: str | None,
    ) -> JSONDecisionProvider | None:
        if not self.settings.ai_agent_enabled:
            return None
        spec = LLMProviderSpec(
            provider=provider,
            model=model,
            api_key=api_key,
            api_base=api_base,
            temperature=self.settings.ai_temperature,
            max_output_tokens=self.settings.ai_max_output_tokens,
            request_timeout_seconds=self.settings.ai_request_timeout_seconds,
            codex_model=self._codex_gateway_codex_model() if normalize_provider_name(provider) == "codex_gateway" else None,
        )
        return build_json_decision_provider(spec)

    def _build_provider_slots(self) -> list[ProviderSlot]:
        if not self.settings.ai_agent_enabled:
            return []

        slots: list[ProviderSlot] = []
        primary_provider = self._primary_provider_name()
        primary_model = self._primary_model_name(primary_provider)
        seen_gemini_keys: set[str] = set()
        if primary_provider == "gemini" and primary_model:
            for slot_label, api_key in (
                ("gemini_primary", self.settings.gemini_api_key),
                ("gemini_free1", self.settings.gemini_api_key_free1),
                ("gemini_free2", self.settings.gemini_api_key_free2),
            ):
                normalized_key = (api_key or "").strip()
                if not normalized_key or normalized_key in seen_gemini_keys:
                    continue
                seen_gemini_keys.add(normalized_key)
                provider = self._build_provider(
                    provider="gemini",
                    model=primary_model,
                    api_key=normalized_key,
                    api_base=None,
                )
                if provider is None:
                    continue
                slots.append(
                    ProviderSlot(
                        slot_label=slot_label,
                        provider_name="gemini",
                        model_name=primary_model,
                        provider=provider,
                        counts_as_fallback=slot_label != "gemini_primary",
                    )
                )
        else:
            primary_provider_instance = self._build_provider(
                provider=primary_provider,
                model=primary_model,
                api_key=self._primary_api_key(primary_provider),
                api_base=self._primary_api_base(primary_provider),
            )
            if primary_provider_instance is not None and primary_model:
                slots.append(
                    ProviderSlot(
                        slot_label=f"{primary_provider}_primary",
                        provider_name=primary_provider,
                        model_name=primary_model,
                        provider=primary_provider_instance,
                        counts_as_fallback=False,
                    )
                )

        fallback_provider_name = self._fallback_provider_name()
        fallback_model_name = self._fallback_model_name(fallback_provider_name)
        fallback_provider = self._build_provider(
            provider=fallback_provider_name,
            model=fallback_model_name,
            api_key=self._fallback_api_key(fallback_provider_name),
            api_base=self._fallback_api_base(fallback_provider_name),
        )
        if fallback_provider is not None and fallback_model_name:
            slots.append(
                ProviderSlot(
                    slot_label=f"{fallback_provider_name}_fallback",
                    provider_name=fallback_provider_name,
                    model_name=fallback_model_name,
                    provider=fallback_provider,
                    counts_as_fallback=True,
                )
            )
        return slots

    @staticmethod
    def _slot_is_ready(
        *,
        provider: str,
        model: str | None,
        api_key: str | None,
        api_base: str | None,
    ) -> bool:
        return provider_is_ready(
            LLMProviderSpec(
                provider=provider,
                model=model,
                api_key=api_key,
                api_base=api_base,
            )
        )

    def _primary_provider_name(self) -> str:
        return normalize_provider_name(self.settings.llm_provider or self.settings.ai_primary_provider or "gemini")

    def _primary_model_name(self, provider: str) -> str | None:
        provider_name = normalize_provider_name(provider)
        if provider_name == "codex_gateway":
            return self._first_non_empty(
                self.settings.llm_model,
                self.settings.codex_gateway_model_label,
                self.settings.ai_primary_model,
            )
        return self._first_non_empty(self.settings.llm_model, self.settings.ai_primary_model)

    def _primary_api_key(self, provider: str) -> str | None:
        provider_name = normalize_provider_name(provider)
        if provider_name == "codex_gateway":
            return self._first_non_empty(self.settings.codex_gateway_api_key)
        if provider_name == "gemini":
            return self._first_non_empty(self.settings.gemini_api_key)
        return None

    def _primary_api_base(self, provider: str) -> str | None:
        provider_name = normalize_provider_name(provider)
        if provider_name == "codex_gateway":
            return self._first_non_empty(self.settings.codex_gateway_base_url)
        return None

    def _fallback_provider_name(self) -> str:
        return normalize_provider_name(self.settings.ai_fallback_provider or "openai_compatible")

    def _fallback_model_name(self, provider: str) -> str | None:
        provider_name = normalize_provider_name(provider)
        if provider_name == "codex_gateway":
            return self._first_non_empty(self.settings.codex_gateway_model_label, self.settings.ai_fallback_model)
        return self._first_non_empty(self.settings.ai_fallback_model)

    def _fallback_api_key(self, provider: str) -> str | None:
        provider_name = normalize_provider_name(provider)
        if provider_name == "codex_gateway":
            return self._first_non_empty(self.settings.codex_gateway_api_key)
        return self._first_non_empty(self.settings.ai_fallback_api_key)

    def _fallback_api_base(self, provider: str) -> str | None:
        provider_name = normalize_provider_name(provider)
        if provider_name == "codex_gateway":
            return self._first_non_empty(self.settings.codex_gateway_base_url)
        return self._first_non_empty(self.settings.ai_fallback_api_base)

    def _codex_gateway_codex_model(self) -> str | None:
        return self._first_non_empty(self.settings.codex_gateway_codex_model)

    @staticmethod
    def _first_non_empty(*values: str | None) -> str | None:
        for value in values:
            normalized = (value or "").strip()
            if normalized:
                return normalized
        return None

    @staticmethod
    def _build_macro_research_system_prompt() -> str:
        return (
            "You are the macro and geopolitical research branch of a trading bot. "
            "Use the supplied headlines, macro events, and article extracts to build a falsifiable market thesis. "
            "Focus on likely price impact for liquid US-listed assets or widely used ETF proxies. "
            "Prefer concrete scenario language, acknowledge uncertainty, and propose research or execution ideas "
            "that could realistically be turned into watchlists, hedges, or tactical setups. "
            "Return JSON only."
        )

    @staticmethod
    def _macro_research_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "regime": {"type": "string"},
                "relevance": {"type": "string"},
                "timeframe": {"type": "string"},
                "scenario": {"type": "string"},
                "importance": {"type": "number"},
                "impact_hypothesis": {"type": "string"},
                "affected_assets": {"type": "array", "items": {"type": "string"}},
                "asset_impacts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "bias": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["ticker", "bias", "reason"],
                    },
                },
                "strategy_ideas": {"type": "array", "items": {"type": "string"}},
                "risk_flags": {"type": "array", "items": {"type": "string"}},
                "evidence_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "summary",
                "regime",
                "relevance",
                "timeframe",
                "scenario",
                "importance",
                "impact_hypothesis",
                "affected_assets",
                "asset_impacts",
                "strategy_ideas",
                "risk_flags",
                "evidence_points",
            ],
        }

    @staticmethod
    def _string_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        results: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                results.append(text)
        return results

    @staticmethod
    def _asset_impacts(value) -> list[dict]:
        if not isinstance(value, list):
            return []
        impacts: list[dict] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").strip().upper()
            bias = str(item.get("bias") or "").strip().lower()
            reason = str(item.get("reason") or "").strip()
            if ticker and bias and reason:
                impacts.append({"ticker": ticker, "bias": bias, "reason": reason})
        return impacts

    def _normalize_macro_research_result(self, payload: dict, *, fallback: dict) -> dict:
        normalized = {
            "summary": str(payload.get("summary") or fallback["summary"]).strip(),
            "regime": str(payload.get("regime") or fallback["regime"]).strip() or fallback["regime"],
            "relevance": str(payload.get("relevance") or fallback["relevance"]).strip() or fallback["relevance"],
            "timeframe": str(payload.get("timeframe") or fallback["timeframe"]).strip() or fallback["timeframe"],
            "scenario": str(payload.get("scenario") or fallback["scenario"]).strip() or fallback["scenario"],
            "importance": float(payload.get("importance") if isinstance(payload.get("importance"), (int, float)) else fallback["importance"]),
            "impact_hypothesis": str(payload.get("impact_hypothesis") or fallback["impact_hypothesis"]).strip(),
            "affected_assets": self._string_list(payload.get("affected_assets")) or list(fallback["affected_assets"]),
            "asset_impacts": self._asset_impacts(payload.get("asset_impacts")) or list(fallback["asset_impacts"]),
            "strategy_ideas": self._string_list(payload.get("strategy_ideas")) or list(fallback["strategy_ideas"]),
            "risk_flags": self._string_list(payload.get("risk_flags")) or list(fallback["risk_flags"]),
            "evidence_points": self._string_list(payload.get("evidence_points")) or list(fallback["evidence_points"]),
        }
        normalized["importance"] = round(min(max(normalized["importance"], 0.0), 1.0), 2)
        return normalized

    def _heuristic_macro_research(
        self,
        *,
        theme: dict,
        market_context: dict,
        calendar_events: list[dict],
        news_items: list[dict],
        article_contexts: list[dict],
    ) -> dict:
        title = str(theme.get("title") or "macro theme").strip()
        default_regime = str(theme.get("default_regime") or "macro_uncertainty").strip()
        timeframe = str(theme.get("timeframe") or "1D-1M").strip()
        relevance = str(theme.get("relevance") or "cross_asset").strip()
        focus_assets = self._string_list(theme.get("focus_assets"))
        strategy_templates = self._string_list(theme.get("strategy_templates"))
        evidence_points: list[str] = []
        evidence_points.extend(
            str(item.get("title") or "").strip()
            for item in calendar_events[:2]
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        )
        evidence_points.extend(
            str(item.get("title") or "").strip()
            for item in news_items[:3]
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        )
        evidence_points.extend(
            str(item.get("title") or "").strip()
            for item in article_contexts[:1]
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        )
        evidence_points = evidence_points[:6]
        scenario = evidence_points[0] if evidence_points else title
        importance = round(
            min(
                1.0,
                0.55
                + (0.1 if calendar_events else 0.0)
                + (0.1 if news_items else 0.0)
                + (0.05 if article_contexts else 0.0),
            ),
            2,
        )
        market_regime = str(market_context.get("market_state_regime") or "").strip()
        impact_hypothesis = (
            f"{title} could reprice {', '.join(focus_assets[:4]) or 'liquid macro proxies'} "
            f"through sector rotation, volatility expansion, and changes in risk appetite."
        )
        summary = (
            f"{title}: {impact_hypothesis} "
            f"Current market regime reference: {market_regime or default_regime}."
        )
        asset_impacts = [
            {
                "ticker": asset,
                "bias": "monitor",
                "reason": f"{asset} is a liquid proxy for the {title.lower()} theme.",
            }
            for asset in focus_assets[:6]
        ]
        risk_flags = [
            "headline risk can reverse quickly before price confirms the thesis",
            "macro timing is harder than thematic direction; wait for market confirmation",
        ]
        return {
            "summary": summary,
            "regime": default_regime,
            "relevance": relevance,
            "timeframe": timeframe,
            "scenario": scenario,
            "importance": importance,
            "impact_hypothesis": impact_hypothesis,
            "affected_assets": focus_assets[:8],
            "asset_impacts": asset_impacts,
            "strategy_ideas": strategy_templates[:5],
            "risk_flags": risk_flags,
            "evidence_points": evidence_points,
            "analysis_mode": "heuristic",
            "provider": None,
            "model": None,
        }

    def _decide_with_fallback(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_json_schema: dict | None = None,
    ) -> tuple[dict, str, str]:
        cooldown_error = self._current_cooldown_error()
        if cooldown_error is not None:
            raise AIDecisionError(cooldown_error)

        errors: list[str] = []
        for slot in self.provider_slots:
            try:
                payload = slot.provider.generate_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_json_schema=response_json_schema,
                )
                self.runtime.cooldown_until = None
                if slot.counts_as_fallback:
                    self.runtime.fallback_count += 1
                return payload, slot.provider_name, slot.model_name
            except LLMProviderError as exc:
                errors.append(f"{slot.slot_label}:{slot.model_name}: {exc}")
                continue
        message = " | ".join(errors) if errors else "No AI provider is configured and ready."
        self._activate_failure_cooldown(message)
        raise AIDecisionError(message)

    def _activate_failure_cooldown(self, message: str) -> None:
        self.runtime.last_error = message
        cooldown_seconds = max(int(self.settings.ai_failure_cooldown_seconds), 0)
        if cooldown_seconds <= 0:
            self.runtime.cooldown_until = None
            return
        self.runtime.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)

    def _current_cooldown_error(self) -> str | None:
        cooldown_until = self.runtime.cooldown_until
        if cooldown_until is None:
            return None
        now = datetime.now(timezone.utc)
        if cooldown_until <= now:
            self.runtime.cooldown_until = None
            return None
        remaining = max(int((cooldown_until - now).total_seconds()), 1)
        last_error = self.runtime.last_error or "prior AI provider failure"
        return f"AI decision providers cooling down for {remaining}s after failure: {last_error}"

    def _build_decision_context(
        self,
        session: Session,
        *,
        ticker: str,
        strategy_id: int | None,
        strategy_version_id: int | None,
        watchlist_code: str | None,
        signal_payload: dict,
        market_context: dict,
    ) -> dict:
        decision_context = signal_payload.get("decision_context") if isinstance(signal_payload.get("decision_context"), dict) else {}
        skill_context = (
            signal_payload.get("skill_context")
            if isinstance(signal_payload.get("skill_context"), dict)
            else decision_context.get("skill_context")
            if isinstance(decision_context.get("skill_context"), dict)
            else {}
        )
        protocol_context = build_candidate_protocol_context(
            ticker=ticker,
            watchlist_code=watchlist_code,
            signal_payload=signal_payload,
            market_context=market_context,
            persisted_market_state=self.market_state_service.get_latest_protocol_market_state(session),
        )
        runtime_selection = self.runtime_memory_service.build_selection(
            session,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            skill_context=skill_context,
        )
        protocol_context["runtime_skills"] = list(runtime_selection.get("runtime_skills") or [])
        protocol_context["runtime_claims"] = list(runtime_selection.get("runtime_claims") or [])
        protocol_context["runtime_distillations"] = list(runtime_selection.get("runtime_distillations") or [])
        protocol_context["context_budget"] = dict(runtime_selection.get("context_budget") or {})
        recent_journal = list(
            session.scalars(
                select(JournalEntry).order_by(JournalEntry.event_time.desc()).limit(self.settings.ai_journal_limit)
            ).all()
        )
        memories = list(
            session.scalars(
                select(MemoryItem).order_by(desc(MemoryItem.importance), MemoryItem.created_at.desc()).limit(self.settings.ai_memory_limit)
            ).all()
        )
        failure_patterns = list(
            session.scalars(
                select(FailurePattern)
                .where(FailurePattern.status == "open")
                .order_by(FailurePattern.occurrences.desc(), FailurePattern.updated_at.desc())
                .limit(self.settings.ai_failure_pattern_limit)
            ).all()
        )

        return {
            "ticker": ticker,
            "strategy_id": strategy_id,
            "strategy_version_id": strategy_version_id,
            "watchlist_code": watchlist_code,
            "market_context": market_context,
            "agent_protocol": protocol_context,
            "signal": {
                "combined_score": signal_payload.get("combined_score"),
                "base_combined_score": signal_payload.get("base_combined_score"),
                "decision": signal_payload.get("decision"),
                "base_decision": signal_payload.get("base_decision"),
                "decision_confidence": signal_payload.get("decision_confidence"),
                "entry_price": signal_payload.get("entry_price"),
                "stop_price": signal_payload.get("stop_price"),
                "target_price": signal_payload.get("target_price"),
                "risk_reward": signal_payload.get("risk_reward"),
                "quant_summary": signal_payload.get("quant_summary"),
                "visual_summary": signal_payload.get("visual_summary"),
                "decision_context": decision_context,
                "risk_budget": signal_payload.get("risk_budget"),
                "position_sizing": signal_payload.get("position_sizing"),
                "research_plan": signal_payload.get("research_plan"),
                "decision_trace": signal_payload.get("decision_trace"),
                "score_breakdown": signal_payload.get("score_breakdown"),
                "guard_results": signal_payload.get("guard_results"),
                "skill_context": skill_context,
                "rationale": signal_payload.get("rationale"),
            },
            "recent_journal": [
                {
                    "entry_type": entry.entry_type,
                    "ticker": entry.ticker,
                    "decision": entry.decision,
                    "outcome": entry.outcome,
                    "lessons": entry.lessons,
                    "reasoning": entry.reasoning,
                }
                for entry in recent_journal
            ],
            "memories": [
                {
                    "scope": item.scope,
                    "key": item.key,
                    "content": item.content,
                    "importance": item.importance,
                }
                for item in memories
            ],
            "failure_patterns": [
                {
                    "failure_mode": pattern.failure_mode,
                    "occurrences": pattern.occurrences,
                    "avg_loss_pct": pattern.avg_loss_pct,
                    "recommended_action": pattern.recommended_action,
                }
                for pattern in failure_patterns
            ],
        }

    def _build_open_position_context(
        self,
        session: Session,
        *,
        position: Position,
        market_snapshot: dict,
    ) -> dict:
        entry_context = position.entry_context if isinstance(position.entry_context, dict) else {}
        skill_context = (
            entry_context.get("management_context", {}).get("skill_context")
            if isinstance(entry_context.get("management_context"), dict)
            and isinstance(entry_context.get("management_context", {}).get("skill_context"), dict)
            else entry_context.get("skill_context")
            if isinstance(entry_context.get("skill_context"), dict)
            else {}
        )
        protocol_context = build_position_management_protocol_context(
            position={
                "id": position.id,
                "ticker": position.ticker,
                "strategy_version_id": position.strategy_version_id,
                "entry_price": position.entry_price,
                "stop_price": position.stop_price,
                "target_price": position.target_price,
                "side": position.side,
                "thesis": position.thesis,
                "entry_context": entry_context,
            },
            market_snapshot=market_snapshot,
            persisted_market_state=self.market_state_service.get_latest_protocol_market_state(session),
        )
        runtime_selection = self.runtime_memory_service.build_selection(
            session,
            ticker=position.ticker,
            strategy_version_id=position.strategy_version_id,
            skill_context=skill_context,
        )
        protocol_context["runtime_skills"] = list(runtime_selection.get("runtime_skills") or [])
        protocol_context["runtime_claims"] = list(runtime_selection.get("runtime_claims") or [])
        protocol_context["runtime_distillations"] = list(runtime_selection.get("runtime_distillations") or [])
        protocol_context["context_budget"] = dict(runtime_selection.get("context_budget") or {})
        recent_journal = list(
            session.scalars(
                select(JournalEntry).order_by(JournalEntry.event_time.desc()).limit(self.settings.ai_journal_limit)
            ).all()
        )
        memories = list(
            session.scalars(
                select(MemoryItem).order_by(desc(MemoryItem.importance), MemoryItem.created_at.desc()).limit(self.settings.ai_memory_limit)
            ).all()
        )
        failure_patterns = list(
            session.scalars(
                select(FailurePattern)
                .where(FailurePattern.status == "open")
                .order_by(FailurePattern.occurrences.desc(), FailurePattern.updated_at.desc())
                .limit(self.settings.ai_failure_pattern_limit)
            ).all()
        )

        pnl_pct = None
        if position.entry_price:
            if position.side == "long":
                pnl_pct = round(((market_snapshot["price"] - position.entry_price) / position.entry_price) * 100, 2)
            else:
                pnl_pct = round(((position.entry_price - market_snapshot["price"]) / position.entry_price) * 100, 2)

        return {
            "position": {
                "id": position.id,
                "ticker": position.ticker,
                "strategy_version_id": position.strategy_version_id,
                "entry_price": position.entry_price,
                "stop_price": position.stop_price,
                "target_price": position.target_price,
                "side": position.side,
                "thesis": position.thesis,
                "entry_context": entry_context,
                "skill_context": skill_context,
                "pnl_pct": pnl_pct,
            },
            "market_snapshot": market_snapshot,
            "agent_protocol": protocol_context,
            "recent_journal": [
                {
                    "entry_type": entry.entry_type,
                    "ticker": entry.ticker,
                    "decision": entry.decision,
                    "outcome": entry.outcome,
                    "lessons": entry.lessons,
                    "reasoning": entry.reasoning,
                }
                for entry in recent_journal
            ],
            "memories": [
                {
                    "scope": item.scope,
                    "key": item.key,
                    "content": item.content,
                    "importance": item.importance,
                }
                for item in memories
            ],
            "failure_patterns": [
                {
                    "failure_mode": pattern.failure_mode,
                    "occurrences": pattern.occurrences,
                    "avg_loss_pct": pattern.avg_loss_pct,
                    "recommended_action": pattern.recommended_action,
                }
                for pattern in failure_patterns
            ],
        }

    def _build_runtime_skill_prompt(self, agent_protocol: dict | None) -> str:
        if not isinstance(agent_protocol, dict):
            return ""
        runtime_skills = agent_protocol.get("runtime_skills")
        return self.skill_lifecycle_service.render_runtime_skill_prompt(runtime_skills)

    def _build_runtime_claim_prompt(self, agent_protocol: dict | None) -> str:
        if not isinstance(agent_protocol, dict):
            return ""
        runtime_claims = agent_protocol.get("runtime_claims")
        return self.knowledge_claim_service.render_runtime_claim_prompt(runtime_claims)

    def _build_runtime_distillation_prompt(self, agent_protocol: dict | None) -> str:
        if not isinstance(agent_protocol, dict):
            return ""
        runtime_distillations = agent_protocol.get("runtime_distillations")
        return self._learning_memory_distillation_service().render_runtime_distillation_prompt(runtime_distillations)

    def _build_runtime_supporting_memory_prompt(self, agent_protocol: dict | None) -> str:
        fragments = [
            self._build_runtime_claim_prompt(agent_protocol),
            self._build_runtime_distillation_prompt(agent_protocol),
        ]
        return "\n\n".join(fragment.strip() for fragment in fragments if isinstance(fragment, str) and fragment.strip())

    @staticmethod
    def _extract_runtime_skills(context: dict | None) -> list[dict]:
        if not isinstance(context, dict):
            return []
        agent_protocol = context.get("agent_protocol")
        if not isinstance(agent_protocol, dict):
            return []
        runtime_skills = agent_protocol.get("runtime_skills")
        return [item for item in runtime_skills if isinstance(item, dict)] if isinstance(runtime_skills, list) else []

    @staticmethod
    def _extract_runtime_claims(context: dict | None) -> list[dict]:
        if not isinstance(context, dict):
            return []
        agent_protocol = context.get("agent_protocol")
        if not isinstance(agent_protocol, dict):
            return []
        runtime_claims = agent_protocol.get("runtime_claims")
        return [item for item in runtime_claims if isinstance(item, dict)] if isinstance(runtime_claims, list) else []

    @staticmethod
    def _extract_runtime_distillations(context: dict | None) -> list[dict]:
        if not isinstance(context, dict):
            return []
        agent_protocol = context.get("agent_protocol")
        if not isinstance(agent_protocol, dict):
            return []
        runtime_distillations = agent_protocol.get("runtime_distillations")
        return (
            [item for item in runtime_distillations if isinstance(item, dict)]
            if isinstance(runtime_distillations, list)
            else []
        )

    @staticmethod
    def _extract_context_budget(context: dict | None) -> dict:
        if not isinstance(context, dict):
            return {}
        agent_protocol = context.get("agent_protocol")
        if not isinstance(agent_protocol, dict):
            return {}
        context_budget = agent_protocol.get("context_budget")
        return context_budget if isinstance(context_budget, dict) else {}

    @staticmethod
    def _learning_memory_distillation_service():
        from app.domains.learning.services import LearningMemoryDistillationService

        return LearningMemoryDistillationService()

    def _parse_decision(self, payload: dict, allowed_actions: set[str] | None = None) -> AgentDecision:
        allowed_actions = allowed_actions or {"paper_enter", "watch", "discard"}
        action = self._coerce_action_from_payload(payload, allowed_actions)
        if action not in allowed_actions:
            raise AIDecisionError(f"AI provider returned unsupported action '{action}'.")
        confidence = float(payload.get("confidence", 0.0))
        confidence = round(min(max(confidence, 0.0), 1.0), 2)
        thesis = str(payload.get("thesis", "")).strip()
        if not thesis:
            raise AIDecisionError("AI provider returned an empty thesis.")
        risks = [str(item).strip() for item in payload.get("risks", []) if str(item).strip()]
        lessons_applied = [str(item).strip() for item in payload.get("lessons_applied", []) if str(item).strip()]
        regime_assessment = payload.get("regime_assessment") if isinstance(payload.get("regime_assessment"), dict) else {}
        default_transition = (
            candidate_state_transition_for_action(action)
            if allowed_actions == {"paper_enter", "watch", "discard"}
            else management_state_transition_for_action(action)
        )
        operating_state = str(payload.get("operating_state") or default_transition.current_state.value).strip() or None
        next_state = str(payload.get("next_state") or default_transition.next_state.value).strip() or None
        active_playbook = str(payload.get("active_playbook") or "").strip() or None
        decision_label = str(payload.get("decision") or "").strip() or None
        entry_trigger = str(payload.get("entry_trigger") or "").strip() or None
        invalidation = str(payload.get("invalidation") or "").strip() or None
        risk_assessment = str(payload.get("risk_assessment") or "").strip() or None
        reasons_not_to_act = [
            str(item).strip()
            for item in payload.get("reasons_not_to_act", [])
            if str(item).strip()
        ]
        claims_applied = [
            str(item).strip()
            for item in payload.get("claims_applied", [])
            if str(item).strip()
        ]
        return AgentDecision(
            action=action,
            confidence=confidence,
            thesis=thesis,
            risks=risks,
            lessons_applied=lessons_applied,
            raw_payload=payload,
            decision_label=decision_label,
            operating_state=operating_state,
            next_state=next_state,
            regime=str(regime_assessment.get("label") or "").strip() or None,
            active_playbook=active_playbook,
            entry_trigger=entry_trigger,
            invalidation=invalidation,
            risk_assessment=risk_assessment,
            reasons_not_to_act=reasons_not_to_act,
            claims_applied=claims_applied,
        )

    @staticmethod
    def _coerce_action_from_payload(payload: dict, allowed_actions: set[str]) -> str:
        action = str(payload.get("action", "")).strip().lower()
        if action:
            return action

        decision = str(payload.get("decision") or "").strip().upper()
        if not decision:
            return action

        candidate_mapping = {
            "ENTER_LONG": "paper_enter",
            "WATCH": "watch",
            "SET_ALERT": "watch",
            "IGNORE": "discard",
            "NO_ACTION": "discard",
        }
        management_mapping = {
            "HOLD": "hold",
            "REDUCE": "tighten_stop",
            "EXIT": "close_position",
            "NO_ACTION": "hold",
        }
        mapping = (
            candidate_mapping
            if allowed_actions == {"paper_enter", "watch", "discard"}
            else management_mapping
        )
        return mapping.get(decision, "")

    def _persist_decision(
        self,
        session: Session,
        *,
        ticker: str,
        strategy_id: int | None,
        strategy_version_id: int | None,
        watchlist_code: str | None,
        signal_payload: dict,
        market_context: dict,
        decision: AgentDecision,
        runtime_skills: list[dict] | None = None,
        runtime_claims: list[dict] | None = None,
        runtime_distillations: list[dict] | None = None,
        context_budget: dict | None = None,
    ) -> None:
        skill_payload = [item for item in (runtime_skills or []) if isinstance(item, dict)]
        claim_payload = [item for item in (runtime_claims or []) if isinstance(item, dict)]
        distillation_payload = [item for item in (runtime_distillations or []) if isinstance(item, dict)]
        budget_payload = dict(context_budget or {})
        session.add(
            JournalEntry(
                entry_type="ai_trade_decision",
                ticker=ticker,
                strategy_id=strategy_id,
                strategy_version_id=strategy_version_id,
                market_context={
                    **market_context,
                    "watchlist_code": watchlist_code,
                    "provider": self.runtime.active_provider,
                    "model": self.runtime.active_model,
                },
                observations={
                    "combined_score": signal_payload.get("combined_score"),
                    "risk_reward": signal_payload.get("risk_reward"),
                    "risk_budget": signal_payload.get("risk_budget"),
                    "position_sizing": signal_payload.get("position_sizing"),
                    "research_plan": signal_payload.get("research_plan"),
                    "decision_trace": signal_payload.get("decision_trace"),
                    "protocol_state": decision.operating_state,
                    "next_protocol_state": decision.next_state,
                    "protocol_decision_label": decision.decision_label,
                    "regime": decision.regime,
                    "active_playbook": decision.active_playbook,
                    "entry_trigger": decision.entry_trigger,
                    "invalidation": decision.invalidation,
                    "risk_assessment": decision.risk_assessment,
                    "reasons_not_to_act": decision.reasons_not_to_act,
                    "claims_applied": decision.claims_applied,
                    "risks": decision.risks,
                    "runtime_skills": skill_payload,
                    "runtime_claims": claim_payload,
                    "runtime_distillations": distillation_payload,
                    "context_budget": budget_payload,
                    "raw_payload": decision.raw_payload,
                },
                reasoning=decision.thesis,
                decision=decision.action,
                expectations=f"Confidence {decision.confidence}",
                lessons=" | ".join(decision.lessons_applied) if decision.lessons_applied else None,
            )
        )
        timestamp = int(datetime.now(timezone.utc).timestamp())
        session.add(
            MemoryItem(
                memory_type="agent_decision",
                scope="agent_decisions",
                key=f"{ticker.lower()}_{timestamp}",
                content=decision.thesis,
                meta={
                    "ticker": ticker,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "decision_label": decision.decision_label,
                    "protocol_state": decision.operating_state,
                    "next_state": decision.next_state,
                    "regime": decision.regime,
                    "active_playbook": decision.active_playbook,
                    "claims_applied": decision.claims_applied,
                    "risks": decision.risks,
                    "lessons_applied": decision.lessons_applied,
                    "runtime_skills": skill_payload,
                    "runtime_claims": claim_payload,
                    "runtime_distillations": distillation_payload,
                    "context_budget": budget_payload,
                },
                importance=max(0.55, decision.confidence),
            )
        )
        session.commit()

    def _persist_management_decision(
        self,
        session: Session,
        *,
        position: Position,
        market_snapshot: dict,
        decision: AgentDecision,
        provider: str,
        model: str,
        runtime_skills: list[dict] | None = None,
        runtime_claims: list[dict] | None = None,
        runtime_distillations: list[dict] | None = None,
        context_budget: dict | None = None,
    ) -> None:
        skill_payload = [item for item in (runtime_skills or []) if isinstance(item, dict)]
        claim_payload = [item for item in (runtime_claims or []) if isinstance(item, dict)]
        distillation_payload = [item for item in (runtime_distillations or []) if isinstance(item, dict)]
        budget_payload = dict(context_budget or {})
        session.add(
            JournalEntry(
                entry_type="ai_position_management",
                ticker=position.ticker,
                strategy_version_id=position.strategy_version_id,
                position_id=position.id,
                market_context={
                    "provider": provider,
                    "model": model,
                    "position_status": position.status,
                },
                observations={
                    "market_snapshot": market_snapshot,
                    "entry_price": position.entry_price,
                    "stop_price": position.stop_price,
                    "target_price": position.target_price,
                    "protocol_state": decision.operating_state,
                    "next_protocol_state": decision.next_state,
                    "protocol_decision_label": decision.decision_label,
                    "regime": decision.regime,
                    "active_playbook": decision.active_playbook,
                    "invalidation": decision.invalidation,
                    "risk_assessment": decision.risk_assessment,
                    "reasons_not_to_act": decision.reasons_not_to_act,
                    "claims_applied": decision.claims_applied,
                    "risks": decision.risks,
                    "runtime_skills": skill_payload,
                    "runtime_claims": claim_payload,
                    "runtime_distillations": distillation_payload,
                    "context_budget": budget_payload,
                    "raw_payload": decision.raw_payload,
                },
                reasoning=decision.thesis,
                decision=decision.action,
                expectations=f"Confidence {decision.confidence}",
                lessons=" | ".join(decision.lessons_applied) if decision.lessons_applied else None,
            )
        )
        timestamp = int(datetime.now(timezone.utc).timestamp())
        session.add(
            MemoryItem(
                memory_type="agent_management_decision",
                scope="agent_position_management",
                key=f"{position.ticker.lower()}_{position.id}_{timestamp}",
                content=decision.thesis,
                meta={
                    "position_id": position.id,
                    "ticker": position.ticker,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "decision_label": decision.decision_label,
                    "protocol_state": decision.operating_state,
                    "next_state": decision.next_state,
                    "regime": decision.regime,
                    "active_playbook": decision.active_playbook,
                    "claims_applied": decision.claims_applied,
                    "risks": decision.risks,
                    "lessons_applied": decision.lessons_applied,
                    "runtime_skills": skill_payload,
                    "runtime_claims": claim_payload,
                    "runtime_distillations": distillation_payload,
                    "context_budget": budget_payload,
                },
                importance=max(0.55, decision.confidence),
            )
        )
        session.commit()
