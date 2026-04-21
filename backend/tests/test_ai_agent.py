import pytest

from app.core.config import Settings
from app.db.models.memory import MemoryItem
from app.domains.learning.agent import AIDecisionError, AutonomousTradingAgentService, ProviderSlot
from app.domains.learning.claims import ClaimEvidenceSeed, ClaimSeed, KnowledgeClaimService
from app.domains.learning.protocol import (
    build_candidate_decision_system_prompt,
    build_position_management_system_prompt,
    candidate_decision_schema,
    position_management_schema,
)
from app.domains.learning.skills import SkillLifecycleService
from app.domains.learning.world_state import MarketStateService
from app.providers.llm import LLMProviderError


class _FakeDecisionProvider:
    def __init__(self, *, label: str, order: list[str], response: dict | None = None, error: str | None = None) -> None:
        self.label = label
        self.order = order
        self.response = response
        self.error = error

    def generate_json(self, *, system_prompt: str, user_prompt: str, response_json_schema: dict | None = None) -> dict:
        del system_prompt, user_prompt, response_json_schema
        self.order.append(self.label)
        if self.error is not None:
            raise LLMProviderError(self.error)
        assert self.response is not None
        return self.response


def test_agent_status_reports_disabled_by_default() -> None:
    service = AutonomousTradingAgentService(Settings())

    payload = service.get_status_payload()

    assert payload["enabled"] is False
    assert payload["provider"] == "gemini"
    assert payload["model"] == "gemini-2.5-flash"
    assert payload["decision_protocol_version"] == "2026-04-18"
    assert payload["fallback_provider"] == "openai_compatible"
    assert payload["fallback_model"] == "qwen2.5:3b"
    assert payload["ready"] is False
    assert payload["calls_last_hour"] == 0
    assert payload["calls_today"] == 0


def test_agent_raises_when_enabled_without_credentials(session) -> None:
    service = AutonomousTradingAgentService(
        Settings(
            ai_agent_enabled=True,
            ai_primary_provider="gemini",
            ai_primary_model="gemini-2.5-flash",
            gemini_api_key=None,
            ai_fallback_provider="openai_compatible",
            ai_fallback_model="qwen2.5:3b",
            ai_fallback_api_base=None,
            ai_fallback_api_key=None,
        )
    )

    with pytest.raises(AIDecisionError):
        service.advise_trade_candidate(
            session,
            ticker="NVDA",
            strategy_id=None,
            strategy_version_id=None,
            watchlist_code="test",
            signal_payload={
                "combined_score": 0.81,
                "decision": "paper_enter",
                "decision_confidence": 0.81,
                "entry_price": 100.0,
                "stop_price": 95.0,
                "target_price": 112.0,
                "risk_reward": 2.4,
                "quant_summary": {"price": 100.0},
                "visual_summary": {"setup_quality": 0.7},
                "rationale": "Baseline signal.",
            },
            market_context={},
        )


def test_agent_is_ready_when_secondary_gemini_key_is_configured() -> None:
    service = AutonomousTradingAgentService(
        Settings(
            ai_agent_enabled=True,
            ai_primary_provider="gemini",
            ai_primary_model="gemini-2.5-flash",
            gemini_api_key=None,
            gemini_api_key_free1="free1-key",
            gemini_api_key_free2=None,
            ai_fallback_provider="openai_compatible",
            ai_fallback_model="qwen2.5:3b",
            ai_fallback_api_base=None,
            ai_fallback_api_key=None,
        )
    )

    payload = service.get_status_payload()

    assert payload["ready"] is True
    assert payload["active_provider"] == "gemini"
    assert payload["active_model"] == "gemini-2.5-flash"
    assert [slot.slot_label for slot in service.provider_slots] == ["gemini_free1"]


def test_agent_can_switch_primary_runtime_provider_to_codex_gateway() -> None:
    service = AutonomousTradingAgentService(
        Settings(
            ai_agent_enabled=True,
            llm_provider="codex_gateway",
            llm_model="compat-label",
            codex_gateway_base_url="https://dev-codex-gateway.peremarc.com",
            codex_gateway_api_key="codex-gateway",
            codex_gateway_codex_model="gpt-5.3-codex-spark",
            ai_fallback_provider="gemini",
            ai_fallback_model="gemini-2.5-flash",
            gemini_api_key="gem-test",
        )
    )

    payload = service.get_status_payload()

    assert payload["provider"] == "codex_gateway"
    assert payload["model"] == "compat-label"
    assert payload["fallback_provider"] == "gemini"
    assert payload["fallback_model"] == "gemini-2.5-flash"
    assert payload["ready"] is True
    assert payload["active_provider"] == "codex_gateway"
    assert payload["active_model"] == "compat-label"


def test_agent_uses_secondary_gemini_slots_before_qwen() -> None:
    service = AutonomousTradingAgentService(
        Settings(
            ai_agent_enabled=True,
            ai_primary_provider="gemini",
            ai_primary_model="gemini-2.5-flash",
            gemini_api_key="primary-key",
            gemini_api_key_free1="free1-key",
            gemini_api_key_free2="free2-key",
            ai_fallback_provider="openai_compatible",
            ai_fallback_model="qwen2.5:3b",
            ai_fallback_api_base="https://fallback.local/v1",
            ai_fallback_api_key="fallback-key",
        )
    )
    order: list[str] = []
    service.provider_slots = [
        ProviderSlot(
            slot_label="gemini_primary",
            provider_name="gemini",
            model_name="gemini-2.5-flash",
            provider=_FakeDecisionProvider(label="gemini_primary", order=order, error="primary exhausted"),
        ),
        ProviderSlot(
            slot_label="gemini_free1",
            provider_name="gemini",
            model_name="gemini-2.5-flash",
            provider=_FakeDecisionProvider(label="gemini_free1", order=order, error="free1 exhausted"),
            counts_as_fallback=True,
        ),
        ProviderSlot(
            slot_label="gemini_free2",
            provider_name="gemini",
            model_name="gemini-2.5-flash",
            provider=_FakeDecisionProvider(
                label="gemini_free2",
                order=order,
                response={"action": "watch", "confidence": 0.62, "thesis": "backup gemini key worked", "risks": [], "lessons_applied": []},
            ),
            counts_as_fallback=True,
        ),
        ProviderSlot(
            slot_label="openai_compatible_fallback",
            provider_name="openai_compatible",
            model_name="qwen2.5:3b",
            provider=_FakeDecisionProvider(label="openai_compatible_fallback", order=order, response={"action": "discard", "confidence": 0.2, "thesis": "should not run", "risks": [], "lessons_applied": []}),
            counts_as_fallback=True,
        ),
    ]

    payload, provider_name, model_name = service._decide_with_fallback(  # noqa: SLF001
        system_prompt="system",
        user_prompt="user",
    )

    assert order == ["gemini_primary", "gemini_free1", "gemini_free2"]
    assert payload["thesis"] == "backup gemini key worked"
    assert provider_name == "gemini"
    assert model_name == "gemini-2.5-flash"
    assert service.runtime.fallback_count == 1


def test_agent_tries_all_gemini_keys_before_qwen_fallback() -> None:
    service = AutonomousTradingAgentService(
        Settings(
            ai_agent_enabled=True,
            ai_primary_provider="gemini",
            ai_primary_model="gemini-2.5-flash",
            gemini_api_key="primary-key",
            gemini_api_key_free1="free1-key",
            gemini_api_key_free2="free2-key",
            ai_fallback_provider="openai_compatible",
            ai_fallback_model="qwen2.5:3b",
            ai_fallback_api_base="https://fallback.local/v1",
            ai_fallback_api_key="fallback-key",
        )
    )
    order: list[str] = []
    service.provider_slots = [
        ProviderSlot(
            slot_label="gemini_primary",
            provider_name="gemini",
            model_name="gemini-2.5-flash",
            provider=_FakeDecisionProvider(label="gemini_primary", order=order, error="primary exhausted"),
        ),
        ProviderSlot(
            slot_label="gemini_free1",
            provider_name="gemini",
            model_name="gemini-2.5-flash",
            provider=_FakeDecisionProvider(label="gemini_free1", order=order, error="free1 exhausted"),
            counts_as_fallback=True,
        ),
        ProviderSlot(
            slot_label="gemini_free2",
            provider_name="gemini",
            model_name="gemini-2.5-flash",
            provider=_FakeDecisionProvider(label="gemini_free2", order=order, error="free2 exhausted"),
            counts_as_fallback=True,
        ),
        ProviderSlot(
            slot_label="openai_compatible_fallback",
            provider_name="openai_compatible",
            model_name="qwen2.5:3b",
            provider=_FakeDecisionProvider(
                label="openai_compatible_fallback",
                order=order,
                response={"action": "watch", "confidence": 0.44, "thesis": "qwen fallback worked", "risks": [], "lessons_applied": []},
            ),
            counts_as_fallback=True,
        ),
    ]

    payload, provider_name, model_name = service._decide_with_fallback(  # noqa: SLF001
        system_prompt="system",
        user_prompt="user",
    )

    assert order == ["gemini_primary", "gemini_free1", "gemini_free2", "openai_compatible_fallback"]
    assert payload["thesis"] == "qwen fallback worked"
    assert provider_name == "openai_compatible"
    assert model_name == "qwen2.5:3b"
    assert service.runtime.fallback_count == 1


def test_agent_decision_context_includes_structured_scoring_inputs(session) -> None:
    service = AutonomousTradingAgentService(Settings())

    context = service._build_decision_context(  # noqa: SLF001
        session,
        ticker="NVDA",
        strategy_id=3,
        strategy_version_id=7,
        watchlist_code="tech_growth",
        signal_payload={
            "combined_score": 0.78,
            "base_combined_score": 0.86,
            "decision": "watch",
            "base_decision": "paper_enter",
            "decision_confidence": 0.78,
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 112.0,
            "risk_reward": 2.4,
            "quant_summary": {"trend": "uptrend", "setup": "breakout"},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.81},
            "decision_context": {"strategy_rules": {"allowed_setups": ["breakout"]}},
            "research_plan": {"tool_budget": {"max_research_steps": 9}, "selected_tools": [{"tool_name": "market.get_snapshot"}]},
            "decision_trace": {"initial_hypothesis": "Investigate NVDA breakout.", "decision_source": "deterministic_pre_ai"},
            "score_breakdown": {"technical_score": 0.86, "final_score": 0.78},
            "guard_results": {"blocked": False, "reasons": [], "advisories": ["existing ticker exposure"]},
            "rationale": "Deterministic entry score favours caution.",
        },
        market_context={"execution_mode": "default"},
    )

    assert context["signal"]["base_combined_score"] == 0.86
    assert context["signal"]["base_decision"] == "paper_enter"
    assert context["signal"]["decision_context"]["strategy_rules"]["allowed_setups"] == ["breakout"]
    assert context["signal"]["research_plan"]["tool_budget"]["max_research_steps"] == 9
    assert context["signal"]["decision_trace"]["initial_hypothesis"] == "Investigate NVDA breakout."
    assert context["signal"]["score_breakdown"]["final_score"] == 0.78
    assert context["signal"]["guard_results"]["blocked"] is False
    assert context["agent_protocol"]["current_state"] == "DECIDE"
    assert context["agent_protocol"]["objective"]["primary"].startswith("Maximize long-term capital growth")
    assert context["agent_protocol"]["decision_contract"]["execution_actions"] == ["paper_enter", "watch", "discard"]
    assert context["agent_protocol"]["candidate_packet"]["active_playbook"] == "breakout_long"
    assert context["agent_protocol"]["regime_policy"]["policy_version"] == "2026-04-18-regime-policy-1"
    assert context["agent_protocol"]["regime_policy"]["playbook"] == "breakout_long"


def test_agent_builds_research_package_with_budget_and_trace() -> None:
    service = AutonomousTradingAgentService(Settings())

    research_package = service.build_trade_candidate_research_package(
        ticker="NVDA",
        strategy_version_id=7,
        signal_payload={
            "decision": "paper_enter",
            "decision_confidence": 0.82,
            "combined_score": 0.82,
            "risk_reward": 2.4,
            "quant_summary": {"trend": "uptrend", "setup": "breakout", "relative_volume": 1.8},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.79},
            "score_breakdown": {"technical_score": 0.84, "final_score": 0.82},
            "guard_results": {"blocked": False, "reasons": [], "advisories": []},
            "decision_context": {"news_context": {"article_count": 1}},
            "rationale": "Structured planner test.",
        },
        entry_context={"execution_mode": "candidate_validation"},
    )

    research_plan = research_package["research_plan"]
    decision_trace = research_package["decision_trace"]
    selected_tools = [step["tool_name"] for step in research_package["selected_steps"]]

    assert research_plan["tool_budget"]["max_research_steps"] == 10
    assert research_plan["protocol"]["current_state"] == "SCAN"
    assert research_plan["protocol"]["playbook"] == "breakout_long"
    assert "strategies.list_pipelines" in selected_tools
    assert "web.search" in selected_tools
    assert decision_trace["protocol"]["current_state"] == "ANALYZE"
    assert decision_trace["protocol"]["next_state"] == "DECIDE"
    assert decision_trace["initial_hypothesis"].startswith("Investigate NVDA")
    assert decision_trace["tool_plan"]["budget_limit"] == 10
    assert decision_trace["decision_source"] == "deterministic_pre_ai"

    finalized_trace = service.finalize_trade_candidate_trace(
        decision_trace=decision_trace,
        final_action="watch",
        final_reason="Entry remains immature.",
        decision_source="ai_overlay",
        confidence=0.63,
    )

    assert finalized_trace["state_transition"]["next_state"] == "MONITOR"


def test_agent_decision_context_reuses_latest_persisted_market_state(session) -> None:
    service = AutonomousTradingAgentService(Settings())
    market_state_service = MarketStateService(settings=service.settings)
    market_state_service.capture_snapshot(
        session,
        trigger="test_snapshot",
        pdca_phase="plan",
        source_context={"execution_mode": "global"},
    )

    context = service._build_decision_context(  # noqa: SLF001
        session,
        ticker="NVDA",
        strategy_id=None,
        strategy_version_id=None,
        watchlist_code="persisted_state_watchlist",
        signal_payload={
            "combined_score": 0.72,
            "decision": "watch",
            "decision_confidence": 0.72,
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 110.0,
            "risk_reward": 2.0,
            "quant_summary": {"trend": "uptrend", "setup": "breakout"},
            "visual_summary": {"setup_type": "breakout"},
            "decision_context": {},
            "guard_results": {"blocked": False, "reasons": [], "advisories": []},
            "score_breakdown": {"technical_score": 0.72, "final_score": 0.72},
            "rationale": "Persisted state test.",
        },
        market_context={"execution_mode": "default"},
    )

    persisted_market_state = context["agent_protocol"]["market_state_snapshot"]
    assert persisted_market_state["portfolio_state"]["benchmark_ticker"] == "SPY"
    assert persisted_market_state["watchlist_code"] == "persisted_state_watchlist"
    assert persisted_market_state["market_regime_inputs"]["market_regime"]["label"] in {
        "bullish_trend",
        "range_mixed",
        "macro_uncertainty",
        "high_volatility_risk_off",
    }


def test_protocol_prompts_and_schemas_expose_the_decision_contract() -> None:
    candidate_prompt = build_candidate_decision_system_prompt()
    management_prompt = build_position_management_system_prompt()
    candidate_schema = candidate_decision_schema()
    management_schema = position_management_schema()

    assert "Do not produce free-form chain-of-thought" in candidate_prompt
    assert "For this call you are in MONITOR state" in management_prompt
    assert candidate_schema["properties"]["action"]["enum"] == ["paper_enter", "watch", "discard"]
    assert "claims_applied" in candidate_schema["required"]
    assert "claims_applied" in management_schema["required"]
    assert management_schema["properties"]["action"]["enum"] == [
        "hold",
        "tighten_stop",
        "extend_target",
        "tighten_stop_and_extend_target",
        "close_position",
    ]


def test_candidate_prompt_can_embed_runtime_skill_instructions() -> None:
    prompt = build_candidate_decision_system_prompt(
        "Relevant runtime skills are supplied below.\n1. Skill `detect_risk_off_conditions`"
    )

    assert "Relevant runtime skills are supplied below." in prompt
    assert "Skill `detect_risk_off_conditions`" in prompt


def test_candidate_prompt_can_embed_runtime_claim_memory() -> None:
    prompt = build_candidate_decision_system_prompt(
        None,
        "Relevant durable claim memory is supplied below.\n1. Claim `review-claim:test` [supported/current]",
    )

    assert "Relevant durable claim memory is supplied below." in prompt
    assert "Claim `review-claim:test`" in prompt


def test_agent_can_parse_protocol_payload_without_explicit_action() -> None:
    service = AutonomousTradingAgentService(Settings())

    decision = service._parse_decision(  # noqa: SLF001
        {
            "protocol_version": "2026-04-18",
            "operating_state": "DECIDE",
            "decision": "ENTER_LONG",
            "thesis": "Breakout is mature enough and risk remains acceptable.",
            "regime_assessment": {
                "label": "bullish_trend",
                "confidence": 0.72,
                "justification": "Constructive trend context.",
                "supporting_evidence": ["trend intact"],
            },
            "active_playbook": "breakout_long",
            "evidence": {"trend": "uptrend"},
            "entry_trigger": "close above resistance with volume",
            "invalidation": "failed breakout below level",
            "risk_assessment": "acceptable",
            "confidence": 0.74,
            "risks": ["failed breakout"],
            "lessons_applied": ["wait for confirmation"],
            "reasons_not_to_act": [],
            "claims_applied": ["review-claim:breakout-volume-filter"],
            "next_action": "open planned paper position",
        }
    )

    assert decision.action == "paper_enter"
    assert decision.decision_label == "ENTER_LONG"
    assert decision.operating_state == "DECIDE"
    assert decision.next_state == "EXECUTE"
    assert decision.regime == "bullish_trend"
    assert decision.active_playbook == "breakout_long"
    assert decision.claims_applied == ["review-claim:breakout-volume-filter"]


def test_agent_decision_context_loads_runtime_skills_on_demand(session) -> None:
    claim_service = KnowledgeClaimService()
    claim = claim_service.create_claim(
        session,
        ClaimSeed(
            scope="strategy:7",
            key="review-claim:breakout-volume-filter",
            claim_type="review_improvement",
            claim_text="Breakout entries need stronger relative volume confirmation.",
            linked_ticker="NVDA",
            strategy_version_id=7,
            status="supported",
            confidence=0.74,
            freshness_state="current",
            meta={"source": "trade_review"},
        ),
    )
    claim_service.add_evidence(
        session,
        claim_id=claim.id,
        seed=ClaimEvidenceSeed(
            source_type="trade_review",
            source_key="trade_review:88",
            stance="support",
            summary="Recent failed breakout reviews improved after requiring stronger volume.",
            evidence_payload={"sample_size": 3},
            strength=0.74,
        ),
    )

    candidate = MemoryItem(
        memory_type="skill_candidate",
        scope="strategy:7",
        key="skill_candidate:test:agent-runtime",
        content="Tighten risk-off handling around fragile breakout entries.",
        meta={
            "summary": "Tighten risk-off handling around fragile breakout entries.",
            "target_skill_code": "detect_risk_off_conditions",
            "candidate_action": "update_existing_skill",
            "candidate_status": "draft",
            "validation_required": True,
            "source_type": "trade_review",
            "source_trade_review_id": 21,
            "ticker": "NVDA",
            "strategy_version_id": 7,
            "position_id": 10,
        },
        importance=0.8,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)

    SkillLifecycleService().validate_candidate(
        session,
        candidate_id=candidate.id,
        validation_mode="paper",
        validation_outcome="approve",
        summary="Use stricter caution when expiry or event-risk makes breakout quality fragile.",
        sample_size=16,
        win_rate=62.5,
        avg_pnl_pct=1.7,
        max_drawdown_pct=-2.9,
        evidence={"source": "paper_batch"},
        activate=True,
    )

    service = AutonomousTradingAgentService(Settings(ai_runtime_skill_limit=2, ai_runtime_skill_step_limit=4))
    context = service._build_decision_context(  # noqa: SLF001
        session,
        ticker="NVDA",
        strategy_id=3,
        strategy_version_id=7,
        watchlist_code="tech_growth",
        signal_payload={
            "combined_score": 0.78,
            "decision": "watch",
            "decision_confidence": 0.78,
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 112.0,
            "risk_reward": 2.4,
            "quant_summary": {"trend": "uptrend", "setup": "breakout"},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.81},
            "decision_context": {
                "strategy_rules": {"allowed_setups": ["breakout"]},
                "skill_context": {
                    "catalog_version": "skills_v1",
                    "routing_mode": "deterministic_v1",
                    "phase": "do",
                    "considered_skills": [{"code": "detect_risk_off_conditions"}],
                    "applied_skills": [
                        {
                            "code": "detect_risk_off_conditions",
                            "reason": "macro, event-risk or expiry context suggests degraded execution quality",
                            "confidence": 0.88,
                        }
                    ],
                    "primary_skill_code": "detect_risk_off_conditions",
                    "risk_skill_active": True,
                },
            },
            "guard_results": {"blocked": False, "reasons": [], "advisories": []},
            "score_breakdown": {"technical_score": 0.78, "final_score": 0.78},
            "rationale": "Runtime skill loading test.",
        },
        market_context={"execution_mode": "default"},
    )

    runtime_skills = context["agent_protocol"]["runtime_skills"]
    runtime_claims = context["agent_protocol"]["runtime_claims"]
    assert len(runtime_skills) == 1
    assert len(runtime_claims) == 1
    assert runtime_skills[0]["skill_code"] == "detect_risk_off_conditions"
    assert runtime_skills[0]["validated_revision_summary"] == (
        "Use stricter caution when expiry or event-risk makes breakout quality fragile."
    )
    assert runtime_claims[0]["claim_text"] == "Breakout entries need stronger relative volume confirmation."
    assert runtime_claims[0]["evidence_summaries"] == [
        "Recent failed breakout reviews improved after requiring stronger volume."
    ]
    assert context["agent_protocol"]["context_budget"]["available_runtime_skill_count"] == 1
    assert context["agent_protocol"]["context_budget"]["loaded_runtime_skill_count"] == 1
    assert context["agent_protocol"]["context_budget"]["runtime_skills"]["loaded_codes"] == ["detect_risk_off_conditions"]
    assert context["agent_protocol"]["context_budget"]["available_runtime_claim_count"] == 1
    assert context["agent_protocol"]["context_budget"]["loaded_runtime_claim_count"] == 1
    assert context["agent_protocol"]["context_budget"]["runtime_claims"]["loaded_keys"] == ["review-claim:breakout-volume-filter"]
    assert context["agent_protocol"]["context_budget"]["loaded_runtime_item_count"] == 2
    prompt = service._build_runtime_skill_prompt(context["agent_protocol"])  # noqa: SLF001
    claim_prompt = service._build_runtime_claim_prompt(context["agent_protocol"])  # noqa: SLF001
    assert "Skill `detect_risk_off_conditions`" in prompt
    assert "Relevant durable claim memory is supplied below." in claim_prompt
    assert "Breakout entries need stronger relative volume confirmation." in claim_prompt
