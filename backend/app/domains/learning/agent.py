from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.failure_pattern import FailurePattern
from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.db.models.position import Position
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
from app.domains.learning.world_state import MarketStateService


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
    provider: GeminiDecisionProvider | OpenAICompatibleDecisionProvider
    counts_as_fallback: bool = False


class GeminiDecisionProvider:
    def __init__(self, *, model: str, api_key: str, temperature: float, request_timeout_seconds: int) -> None:
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.request_timeout_seconds = max(int(request_timeout_seconds), 1)

    def decide(self, *, system_prompt: str, user_prompt: str, response_json_schema: dict | None = None) -> dict:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseJsonSchema": response_json_schema
                or {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "confidence": {"type": "number"},
                        "thesis": {"type": "string"},
                        "risks": {"type": "array", "items": {"type": "string"}},
                        "lessons_applied": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["action", "confidence", "thesis", "risks", "lessons_applied"],
                },
            },
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "trading-research-app/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw_payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AIDecisionError(f"Gemini request failed: {exc}") from exc

        content = self._extract_gemini_text(raw_payload)
        if not content:
            raise AIDecisionError("Gemini returned no decision content.")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIDecisionError("Gemini returned malformed JSON decision content.") from exc

    @staticmethod
    def _extract_gemini_text(payload: dict) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))


class OpenAICompatibleDecisionProvider:
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str | None,
        temperature: float,
        max_output_tokens: int,
        request_timeout_seconds: int,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.request_timeout_seconds = max(int(request_timeout_seconds), 1)

    def decide(self, *, system_prompt: str, user_prompt: str, response_json_schema: dict | None = None) -> dict:
        endpoint = f"{self.api_base.rstrip('/')}/chat/completions"
        del response_json_schema
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "trading-research-app/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw_payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise AIDecisionError(f"OpenAI-compatible provider request failed: {exc}") from exc

        content = self._extract_message_content(raw_payload)
        if not content:
            raise AIDecisionError("OpenAI-compatible provider returned no decision content.")
        try:
            return self._extract_json_object(content)
        except json.JSONDecodeError as exc:
            raise AIDecisionError("OpenAI-compatible provider returned malformed JSON decision content.") from exc

    @staticmethod
    def _extract_message_content(payload: dict) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return ""

    @staticmethod
    def _extract_json_object(content: str) -> dict:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise json.JSONDecodeError("No JSON object found.", content, 0)
        return json.loads(content[start : end + 1])


class AutonomousTradingAgentService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.research_planner_service = ResearchPlannerService()
        self.market_state_service = MarketStateService(settings=self.settings)
        provider_slots = self._build_provider_slots()
        primary_ready = any(slot.provider_name == self.settings.ai_primary_provider for slot in provider_slots)
        fallback_ready = any(slot.provider_name == self.settings.ai_fallback_provider for slot in provider_slots)
        self.runtime = AgentRuntimeState(
            enabled=self.settings.ai_agent_enabled,
            provider=self.settings.ai_primary_provider,
            model=self.settings.ai_primary_model,
            ready=primary_ready or fallback_ready,
            fallback_provider=self.settings.ai_fallback_provider,
            fallback_model=self.settings.ai_fallback_model,
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
        primary_ready = any(slot.provider_name == self.settings.ai_primary_provider for slot in self.provider_slots)
        fallback_ready = any(slot.provider_name == self.settings.ai_fallback_provider for slot in self.provider_slots)
        self.runtime.ready = primary_ready or fallback_ready
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

        user_prompt = json.dumps(
            self._build_decision_context(
                session,
                ticker=ticker,
                strategy_id=strategy_id,
                strategy_version_id=strategy_version_id,
                watchlist_code=watchlist_code,
                signal_payload=signal_payload,
                market_context=market_context or {},
            ),
            ensure_ascii=True,
        )

        raw_decision, used_provider, used_model = self._decide_with_fallback(
            system_prompt=build_candidate_decision_system_prompt(),
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

        user_prompt = json.dumps(
            self._build_open_position_context(
                session,
                position=position,
                market_snapshot=market_snapshot,
            ),
            ensure_ascii=True,
        )

        raw_decision, used_provider, used_model = self._decide_with_fallback(
            system_prompt=build_position_management_system_prompt(),
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
    ) -> GeminiDecisionProvider | OpenAICompatibleDecisionProvider | None:
        if not self.settings.ai_agent_enabled or provider == "disabled" or not model:
            return None
        if provider == "gemini" and api_key:
            return GeminiDecisionProvider(
                model=model,
                api_key=api_key,
                temperature=self.settings.ai_temperature,
                request_timeout_seconds=self.settings.ai_request_timeout_seconds,
            )
        if provider == "openai_compatible" and api_base:
            return OpenAICompatibleDecisionProvider(
                model=model,
                api_base=api_base,
                api_key=api_key,
                temperature=self.settings.ai_temperature,
                max_output_tokens=self.settings.ai_max_output_tokens,
                request_timeout_seconds=self.settings.ai_request_timeout_seconds,
            )
        return None

    def _build_provider_slots(self) -> list[ProviderSlot]:
        if not self.settings.ai_agent_enabled:
            return []

        slots: list[ProviderSlot] = []
        seen_gemini_keys: set[str] = set()
        if self.settings.ai_primary_provider == "gemini" and self.settings.ai_primary_model:
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
                    model=self.settings.ai_primary_model,
                    api_key=normalized_key,
                    api_base=None,
                )
                if provider is None:
                    continue
                slots.append(
                    ProviderSlot(
                        slot_label=slot_label,
                        provider_name="gemini",
                        model_name=self.settings.ai_primary_model,
                        provider=provider,
                        counts_as_fallback=slot_label != "gemini_primary",
                    )
                )

        fallback_provider = self._build_provider(
            provider=self.settings.ai_fallback_provider,
            model=self.settings.ai_fallback_model,
            api_key=self.settings.ai_fallback_api_key,
            api_base=self.settings.ai_fallback_api_base,
        )
        if fallback_provider is not None and self.settings.ai_fallback_model:
            slots.append(
                ProviderSlot(
                    slot_label=f"{self.settings.ai_fallback_provider}_fallback",
                    provider_name=self.settings.ai_fallback_provider,
                    model_name=self.settings.ai_fallback_model,
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
        if provider == "disabled" or not model:
            return False
        if provider == "gemini":
            return bool(api_key)
        if provider == "openai_compatible":
            return bool(api_base)
        return False

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
                payload = slot.provider.decide(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_json_schema=response_json_schema,
                )
                self.runtime.cooldown_until = None
                if slot.counts_as_fallback:
                    self.runtime.fallback_count += 1
                return payload, slot.provider_name, slot.model_name
            except AIDecisionError as exc:
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
        protocol_context = build_candidate_protocol_context(
            ticker=ticker,
            watchlist_code=watchlist_code,
            signal_payload=signal_payload,
            market_context=market_context,
            persisted_market_state=self.market_state_service.get_latest_protocol_market_state(session),
        )
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
                "decision_context": signal_payload.get("decision_context"),
                "risk_budget": signal_payload.get("risk_budget"),
                "position_sizing": signal_payload.get("position_sizing"),
                "research_plan": signal_payload.get("research_plan"),
                "decision_trace": signal_payload.get("decision_trace"),
                "score_breakdown": signal_payload.get("score_breakdown"),
                "guard_results": signal_payload.get("guard_results"),
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
                "entry_context": position.entry_context or {},
            },
            market_snapshot=market_snapshot,
            persisted_market_state=self.market_state_service.get_latest_protocol_market_state(session),
        )
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
                "entry_context": position.entry_context or {},
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
    ) -> None:
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
                    "risks": decision.risks,
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
                    "risks": decision.risks,
                    "lessons_applied": decision.lessons_applied,
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
    ) -> None:
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
                    "risks": decision.risks,
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
                    "risks": decision.risks,
                    "lessons_applied": decision.lessons_applied,
                },
                importance=max(0.55, decision.confidence),
            )
        )
        session.commit()
