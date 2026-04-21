from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


DECISION_PROTOCOL_VERSION = "2026-04-18"
PRIMARY_OBJECTIVE = (
    "Maximize long-term capital growth subject to strict survival, drawdown, concentration, liquidity and event-risk "
    "constraints."
)
OBJECTIVE_PRIORITY_STACK = [
    "Survive first: avoid ruin, forced concentration and oversized event risk.",
    "Preserve capital second: protect the portfolio when edge is unclear or correlations rise.",
    "Grow only when the evidence supports a real playbook with acceptable risk and liquidity.",
]
RISK_CONSTRAINTS = [
    "Cash is a valid position when no clear edge exists.",
    "No position without thesis, invalidation, exit plan and acceptable liquidity.",
    "Size from risk budget, never from subjective conviction or FOMO.",
    "Do not rewrite a thesis without a new market event or materially new evidence.",
    "Do not promote strategy changes directly to live behaviour without validation.",
]
DOCTRINE_PRINCIPLES = [
    "Use one global decision-maker and deterministic tools; never let tools invent narrative.",
    "Interpret only evidence returned by trusted tools; never fabricate indicators, prices or catalysts.",
    "Scan only through active playbooks; do not search the market without an operative frame.",
    "Prefer no trade, watch or alert when the setup is immature, crowded or contextually weak.",
    "A trade must always include trigger, invalidation, sizing and exit plan before execution.",
    "Learning must be review-driven, sample-aware and validated before strategy promotion.",
]
DECISION_PROTOCOL_STEPS = [
    "Update world state into a Market State Snapshot.",
    "Classify the market regime before prioritizing strategies.",
    "Activate only the playbooks compatible with that regime.",
    "Scan watchlists and screeners in service of those playbooks.",
    "Build a Candidate Packet with evidence, trigger, invalidation and risk.",
    "Choose from a constrained action set instead of inventing actions.",
    "Execute with risk controls and monitor only on relevant events.",
    "Review outcomes and learn through validated improvements.",
]
REGIME_POLICY_VERSION = "2026-04-18-regime-policy-1"


class AgentOperatingState(StrEnum):
    OBSERVE = "OBSERVE"
    SCAN = "SCAN"
    ANALYZE = "ANALYZE"
    DECIDE = "DECIDE"
    EXECUTE = "EXECUTE"
    MONITOR = "MONITOR"
    REVIEW = "REVIEW"
    IMPROVE = "IMPROVE"


class CandidateDecisionLabel(StrEnum):
    IGNORE = "IGNORE"
    WATCH = "WATCH"
    SET_ALERT = "SET_ALERT"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    NO_ACTION = "NO_ACTION"


class ManagementDecisionLabel(StrEnum):
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    NO_ACTION = "NO_ACTION"


class StateTransition(BaseModel):
    current_state: AgentOperatingState
    next_state: AgentOperatingState
    trigger: str


class ObjectivePolicy(BaseModel):
    primary: str = PRIMARY_OBJECTIVE
    priority_stack: list[str] = Field(default_factory=lambda: list(OBJECTIVE_PRIORITY_STACK))
    risk_constraints: list[str] = Field(default_factory=lambda: list(RISK_CONSTRAINTS))


class PlaybookDefinition(BaseModel):
    code: str
    name: str
    priority: int = Field(ge=1, le=5)
    context: list[str] = Field(default_factory=list)
    setup_focus: list[str] = Field(default_factory=list)
    confirmations: list[str] = Field(default_factory=list)
    invalidations: list[str] = Field(default_factory=list)
    exits: list[str] = Field(default_factory=list)


class RegimePolicyDefinition(BaseModel):
    regime_label: str
    bias: str
    allowed_playbooks: list[str] = Field(default_factory=list)
    blocked_playbooks: list[str] = Field(default_factory=list)
    risk_multiplier: float = Field(ge=0.0, le=1.5)
    max_new_positions: int = Field(ge=0, le=10)
    block_on_event_risk: bool = False
    rationale: str


class RegimePolicyContext(BaseModel):
    policy_version: str = REGIME_POLICY_VERSION
    regime_label: str
    bias: str
    allowed_playbooks: list[str] = Field(default_factory=list)
    blocked_playbooks: list[str] = Field(default_factory=list)
    risk_multiplier: float = Field(ge=0.0, le=1.5)
    max_new_positions: int = Field(ge=0, le=10)
    block_on_event_risk: bool = False
    playbook: str | None = None
    playbook_allowed: bool = True
    blocked_reason: str | None = None
    rationale: str


class MarketStateSnapshot(BaseModel):
    execution_mode: str = "default"
    watchlist_code: str | None = None
    portfolio_state: dict = Field(default_factory=dict)
    open_positions: list[dict] = Field(default_factory=list)
    recent_alerts: list[str] = Field(default_factory=list)
    macro_context: dict = Field(default_factory=dict)
    corporate_calendar: list[dict] = Field(default_factory=list)
    market_regime_inputs: dict = Field(default_factory=dict)
    active_watchlists: list[dict] = Field(default_factory=list)


class RegimeAssessment(BaseModel):
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    justification: str
    supporting_evidence: list[str] = Field(default_factory=list)


class ActivePlaybook(BaseModel):
    code: str
    name: str
    priority: int = Field(ge=1, le=5)
    why_active: str
    setup_focus: list[str] = Field(default_factory=list)


class CandidatePacket(BaseModel):
    symbol: str
    active_playbook: str
    thesis_seed: str
    evidence: dict = Field(default_factory=dict)
    entry_trigger: str
    invalidation: str
    risk_assessment: str
    liquidity_assessment: str | None = None
    catalyst_risk: str | None = None
    alternatives_considered: list[str] = Field(default_factory=list)


class CandidateDecisionEnvelope(BaseModel):
    protocol_version: str = DECISION_PROTOCOL_VERSION
    operating_state: AgentOperatingState = AgentOperatingState.DECIDE
    action: str
    decision: CandidateDecisionLabel
    thesis: str
    regime_assessment: RegimeAssessment
    active_playbook: str
    evidence: dict = Field(default_factory=dict)
    entry_trigger: str
    invalidation: str
    risk_assessment: str
    sizing: dict = Field(default_factory=dict)
    exit_plan: dict = Field(default_factory=dict)
    reasons_not_to_act: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    lessons_applied: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    next_action: str
    next_state: AgentOperatingState = AgentOperatingState.OBSERVE


class PositionManagementEnvelope(BaseModel):
    protocol_version: str = DECISION_PROTOCOL_VERSION
    operating_state: AgentOperatingState = AgentOperatingState.MONITOR
    action: str
    decision: ManagementDecisionLabel
    thesis: str
    regime_assessment: RegimeAssessment
    active_playbook: str
    evidence: dict = Field(default_factory=dict)
    invalidation: str
    risk_assessment: str
    reasons_not_to_act: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    lessons_applied: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    next_action: str
    next_state: AgentOperatingState = AgentOperatingState.MONITOR


def playbook_catalog() -> list[PlaybookDefinition]:
    return [
        PlaybookDefinition(
            code="breakout_long",
            name="Breakout Long",
            priority=1,
            context=["bullish trend", "stable breadth", "liquid leadership"],
            setup_focus=["base or compression near resistance", "volume expansion on breakout"],
            confirmations=["price above key moving averages", "clean structure", "expanding relative volume"],
            invalidations=["failed breakout", "immediate loss of breakout zone", "poor follow-through"],
            exits=["take partial into expansion", "trail under structure", "exit failed breakout quickly"],
        ),
        PlaybookDefinition(
            code="pullback_long",
            name="Pullback Long",
            priority=1,
            context=["primary uptrend", "orderly pullback", "constructive market regime"],
            setup_focus=["support at SMA20/SMA50", "loss of downside momentum", "reclaim after pullback"],
            confirmations=["trend intact", "pullback not too deep", "buyers reappear near support"],
            invalidations=["trend break", "support failure", "heavy distribution volume"],
            exits=["trim into prior highs", "trail below reclaimed support", "exit if trend deteriorates"],
        ),
        PlaybookDefinition(
            code="position_long",
            name="Position Long",
            priority=2,
            context=["structural uptrend", "quality business", "macro not hostile"],
            setup_focus=["multi-month leadership", "relative strength persistence", "acceptable entry timing"],
            confirmations=["price above structural trend support", "fundamental quality acceptable", "liquidity strong"],
            invalidations=["structural trend break", "thesis-changing event", "crowded exposure"],
            exits=["stage out on structural weakness", "rebalance on extension", "exit on thesis failure"],
        ),
    ]


def regime_policy_catalog() -> list[RegimePolicyDefinition]:
    playbook_codes = [item.code for item in playbook_catalog()]
    return [
        RegimePolicyDefinition(
            regime_label="bullish_trend",
            bias="long_only",
            allowed_playbooks=playbook_codes,
            blocked_playbooks=[],
            risk_multiplier=1.0,
            max_new_positions=3,
            block_on_event_risk=False,
            rationale="Constructive trend regime: longs are allowed, but they still require clean structure and liquidity.",
        ),
        RegimePolicyDefinition(
            regime_label="bullish_but_selective",
            bias="long_only_selective",
            allowed_playbooks=playbook_codes,
            blocked_playbooks=[],
            risk_multiplier=0.85,
            max_new_positions=2,
            block_on_event_risk=True,
            rationale="Trend remains constructive, but new exposure should be selective and slightly de-risked.",
        ),
        RegimePolicyDefinition(
            regime_label="range_mixed",
            bias="selective_long_or_cash",
            allowed_playbooks=playbook_codes,
            blocked_playbooks=[],
            risk_multiplier=0.65,
            max_new_positions=1,
            block_on_event_risk=True,
            rationale="Mixed regime: keep activity low, prefer selective longs and avoid stacking fresh risk.",
        ),
        RegimePolicyDefinition(
            regime_label="macro_uncertainty",
            bias="capital_preservation",
            allowed_playbooks=["position_long"],
            blocked_playbooks=["breakout_long", "pullback_long"],
            risk_multiplier=0.4,
            max_new_positions=1,
            block_on_event_risk=True,
            rationale="Macro uncertainty favors patience; only the highest-quality structural exposure is allowed.",
        ),
        RegimePolicyDefinition(
            regime_label="high_volatility_risk_off",
            bias="cash",
            allowed_playbooks=[],
            blocked_playbooks=playbook_codes,
            risk_multiplier=0.0,
            max_new_positions=0,
            block_on_event_risk=True,
            rationale="Risk-off regime: survival dominates and new exposure should be blocked.",
        ),
        RegimePolicyDefinition(
            regime_label="position_monitoring",
            bias="manage_only",
            allowed_playbooks=playbook_codes,
            blocked_playbooks=[],
            risk_multiplier=0.0,
            max_new_positions=0,
            block_on_event_risk=False,
            rationale="Monitoring mode manages existing risk and does not authorize fresh entries.",
        ),
        RegimePolicyDefinition(
            regime_label="default",
            bias="selective_long_or_cash",
            allowed_playbooks=playbook_codes,
            blocked_playbooks=[],
            risk_multiplier=0.75,
            max_new_positions=1,
            block_on_event_risk=True,
            rationale="Fallback regime policy stays conservative until a clearer regime is available.",
        ),
    ]


def resolve_regime_policy(regime_label: str | None) -> RegimePolicyDefinition:
    normalized = str(regime_label or "").strip().lower() or "default"
    catalog = {item.regime_label: item for item in regime_policy_catalog()}
    return catalog.get(normalized, catalog["default"])


def build_regime_policy_context(*, regime_label: str | None, playbook_code: str | None = None) -> dict:
    policy = resolve_regime_policy(regime_label)
    normalized_playbook = str(playbook_code or "").strip() or None
    blocked_reason: str | None = None
    playbook_allowed = True
    if normalized_playbook is not None:
        if policy.allowed_playbooks and normalized_playbook not in policy.allowed_playbooks:
            playbook_allowed = False
            blocked_reason = (
                f"playbook '{normalized_playbook}' is not active under regime '{policy.regime_label}'"
            )
        elif normalized_playbook in policy.blocked_playbooks:
            playbook_allowed = False
            blocked_reason = (
                f"playbook '{normalized_playbook}' is explicitly blocked under regime '{policy.regime_label}'"
            )
    return RegimePolicyContext(
        regime_label=policy.regime_label,
        bias=policy.bias,
        allowed_playbooks=list(policy.allowed_playbooks),
        blocked_playbooks=list(policy.blocked_playbooks),
        risk_multiplier=policy.risk_multiplier,
        max_new_positions=policy.max_new_positions,
        block_on_event_risk=policy.block_on_event_risk,
        playbook=normalized_playbook,
        playbook_allowed=playbook_allowed,
        blocked_reason=blocked_reason,
        rationale=policy.rationale,
    ).model_dump(mode="json")


def protocol_manifest() -> dict:
    return {
        "version": DECISION_PROTOCOL_VERSION,
        "regime_policy_version": REGIME_POLICY_VERSION,
        "objective": ObjectivePolicy().model_dump(mode="json"),
        "doctrine": list(DOCTRINE_PRINCIPLES),
        "protocol_steps": list(DECISION_PROTOCOL_STEPS),
        "states": [state.value for state in AgentOperatingState],
        "playbooks": [item.model_dump(mode="json") for item in playbook_catalog()],
        "regime_policies": [item.model_dump(mode="json") for item in regime_policy_catalog()],
    }


def candidate_decision_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "protocol_version": {"type": "string"},
            "operating_state": {"type": "string", "enum": [item.value for item in AgentOperatingState]},
            "action": {"type": "string", "enum": ["paper_enter", "watch", "discard"]},
            "decision": {"type": "string", "enum": [item.value for item in CandidateDecisionLabel]},
            "thesis": {"type": "string"},
            "regime_assessment": _regime_assessment_schema(),
            "active_playbook": {"type": "string"},
            "evidence": {"type": "object"},
            "entry_trigger": {"type": "string"},
            "invalidation": {"type": "string"},
            "risk_assessment": {"type": "string"},
            "sizing": {"type": "object"},
            "exit_plan": {"type": "object"},
            "reasons_not_to_act": {"type": "array", "items": {"type": "string"}},
            "claims_applied": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "lessons_applied": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "next_action": {"type": "string"},
            "next_state": {"type": "string", "enum": [item.value for item in AgentOperatingState]},
        },
        "required": [
            "protocol_version",
            "operating_state",
            "action",
            "decision",
            "thesis",
            "regime_assessment",
            "active_playbook",
            "evidence",
            "entry_trigger",
            "invalidation",
            "risk_assessment",
            "reasons_not_to_act",
            "claims_applied",
            "risks",
            "lessons_applied",
            "confidence",
            "next_action",
            "next_state",
        ],
    }


def position_management_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "protocol_version": {"type": "string"},
            "operating_state": {"type": "string", "enum": [item.value for item in AgentOperatingState]},
            "action": {
                "type": "string",
                "enum": ["hold", "tighten_stop", "extend_target", "tighten_stop_and_extend_target", "close_position"],
            },
            "decision": {"type": "string", "enum": [item.value for item in ManagementDecisionLabel]},
            "thesis": {"type": "string"},
            "regime_assessment": _regime_assessment_schema(),
            "active_playbook": {"type": "string"},
            "evidence": {"type": "object"},
            "invalidation": {"type": "string"},
            "risk_assessment": {"type": "string"},
            "reasons_not_to_act": {"type": "array", "items": {"type": "string"}},
            "claims_applied": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "lessons_applied": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
            "next_action": {"type": "string"},
            "next_state": {"type": "string", "enum": [item.value for item in AgentOperatingState]},
        },
        "required": [
            "protocol_version",
            "operating_state",
            "action",
            "decision",
            "thesis",
            "regime_assessment",
            "active_playbook",
            "evidence",
            "invalidation",
            "risk_assessment",
            "reasons_not_to_act",
            "claims_applied",
            "risks",
            "lessons_applied",
            "confidence",
            "next_action",
            "next_state",
        ],
    }


def infer_candidate_playbook(signal_payload: dict) -> PlaybookDefinition:
    quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
    visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
    setup_text = " ".join(
        [
            str(quant.get("setup") or ""),
            str(visual.get("setup_type") or ""),
            str(visual.get("pattern") or ""),
        ]
    ).lower()
    catalog = {item.code: item for item in playbook_catalog()}
    if any(token in setup_text for token in ["pullback", "sma20", "sma50", "resume"]):
        return catalog["pullback_long"]
    if any(token in setup_text for token in ["momentum", "weekly", "position", "leader"]):
        return catalog["position_long"]
    return catalog["breakout_long"]


def infer_regime_assessment(*, signal_payload: dict, market_context: dict) -> RegimeAssessment:
    decision_context = signal_payload.get("decision_context") if isinstance(signal_payload.get("decision_context"), dict) else {}
    macro_fit = decision_context.get("macro_fit") if isinstance(decision_context.get("macro_fit"), dict) else {}
    guard_results = signal_payload.get("guard_results") if isinstance(signal_payload.get("guard_results"), dict) else {}
    score_breakdown = signal_payload.get("score_breakdown") if isinstance(signal_payload.get("score_breakdown"), dict) else {}
    reasons = [str(item) for item in guard_results.get("reasons", []) if isinstance(item, str) and item.strip()]
    advisories = [str(item) for item in guard_results.get("advisories", []) if isinstance(item, str) and item.strip()]
    alignments = [str(item) for item in macro_fit.get("alignments", []) if isinstance(item, str) and item.strip()]
    conflicts = [str(item) for item in macro_fit.get("conflicts", []) if isinstance(item, str) and item.strip()]
    execution_mode = str(market_context.get("execution_mode") or "default")
    technical_score = float(score_breakdown.get("technical_score") or signal_payload.get("combined_score") or 0.0)
    final_score = float(score_breakdown.get("final_score") or signal_payload.get("decision_confidence") or technical_score)

    if reasons:
        label = "macro_uncertainty" if conflicts else "high_volatility_risk_off"
        confidence = 0.74
        justification = "Hard guards are active, so capital preservation takes precedence over new exposure."
        evidence = reasons[:3]
    elif conflicts:
        label = "macro_uncertainty"
        confidence = 0.68
        justification = "Macro fit shows conflicts, so only selective or delayed exposure is appropriate."
        evidence = conflicts[:3]
    elif technical_score >= 0.75 and final_score >= 0.7:
        label = "bullish_trend" if execution_mode == "default" else "bullish_but_selective"
        confidence = 0.71
        justification = "Technical structure is constructive and no major contextual block is present."
        evidence = (alignments or advisories or ["constructive technical structure"])[:3]
    else:
        label = "range_mixed"
        confidence = 0.58
        justification = "Evidence is mixed, so the playbook should stay selective and patient."
        evidence = (advisories or ["mixed contextual evidence"])[:3]

    return RegimeAssessment(
        label=label,
        confidence=confidence,
        justification=justification,
        supporting_evidence=evidence,
    )


def build_candidate_packet(
    *,
    ticker: str,
    signal_payload: dict,
    market_context: dict,
    watchlist_code: str | None,
) -> CandidatePacket:
    quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
    visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
    decision_context = signal_payload.get("decision_context") if isinstance(signal_payload.get("decision_context"), dict) else {}
    playbook = infer_candidate_playbook(signal_payload)
    risk_budget = signal_payload.get("risk_budget") if isinstance(signal_payload.get("risk_budget"), dict) else {}
    signal_quality = float(signal_payload.get("decision_confidence") or signal_payload.get("combined_score") or 0.0)
    liquidity = (
        "acceptable"
        if float(quant.get("relative_volume") or 1.0) >= 1.0
        else "needs confirmation"
    )
    catalyst_risk = "event risk not yet escalated"
    if isinstance(decision_context.get("news_context"), dict) and int(decision_context["news_context"].get("article_count") or 0) > 0:
        catalyst_risk = "recent ticker-specific news exists and should be checked before entry"
    if risk_budget.get("blocked"):
        catalyst_risk = "risk budget already blocks new exposure"

    entry_trigger = (
        str(decision_context.get("entry_trigger") or "").strip()
        or _default_entry_trigger(playbook.code)
    )
    invalidation = (
        str(decision_context.get("invalidation") or "").strip()
        or _default_invalidation(playbook.code)
    )
    risk_assessment = (
        "acceptable"
        if signal_quality >= 0.7 and not risk_budget.get("blocked")
        else "selective"
        if signal_quality >= 0.55
        else "weak"
    )

    return CandidatePacket(
        symbol=ticker.upper(),
        active_playbook=playbook.code,
        thesis_seed=str(signal_payload.get("rationale") or f"{ticker.upper()} candidate under {playbook.name}."),
        evidence={
            "quant": quant,
            "visual": visual,
            "score_breakdown": signal_payload.get("score_breakdown"),
            "guard_results": signal_payload.get("guard_results"),
            "market_context": market_context,
        },
        entry_trigger=entry_trigger,
        invalidation=invalidation,
        risk_assessment=risk_assessment,
        liquidity_assessment=liquidity,
        catalyst_risk=catalyst_risk,
        alternatives_considered=[f"watchlist:{watchlist_code}"] if watchlist_code else [],
    )


def build_candidate_protocol_context(
    *,
    ticker: str,
    watchlist_code: str | None,
    signal_payload: dict,
    market_context: dict,
    persisted_market_state: dict | None = None,
) -> dict:
    playbook = infer_candidate_playbook(signal_payload)
    regime = infer_regime_assessment(signal_payload=signal_payload, market_context=market_context)
    regime_policy = build_regime_policy_context(regime_label=regime.label, playbook_code=playbook.code)
    packet = build_candidate_packet(
        ticker=ticker,
        signal_payload=signal_payload,
        market_context=market_context,
        watchlist_code=watchlist_code,
    )
    market_state_snapshot = _merge_market_state_snapshot(
        persisted_market_state=persisted_market_state,
        default_state=MarketStateSnapshot(
            execution_mode=str(market_context.get("execution_mode") or "default"),
            watchlist_code=watchlist_code,
            portfolio_state={"risk_budget": signal_payload.get("risk_budget")},
            market_regime_inputs={
                "macro_fit": (signal_payload.get("decision_context") or {}).get("macro_fit")
                if isinstance(signal_payload.get("decision_context"), dict)
                else {},
                "guard_results": signal_payload.get("guard_results"),
                "score_breakdown": signal_payload.get("score_breakdown"),
            },
            active_watchlists=(
                [{"code": watchlist_code, "ticker": ticker.upper()}]
                if watchlist_code
                else [{"ticker": ticker.upper()}]
            ),
        ).model_dump(mode="json"),
        watchlist_code=watchlist_code,
        market_context=market_context,
        signal_payload=signal_payload,
        ticker=ticker,
    )
    return {
        **protocol_manifest(),
        "current_state": AgentOperatingState.DECIDE.value,
        "decision_contract": {
            "execution_actions": ["paper_enter", "watch", "discard"],
            "decision_labels": [item.value for item in CandidateDecisionLabel],
            "execution_mapping": {
                "ENTER_LONG": "paper_enter",
                "WATCH": "watch",
                "SET_ALERT": "watch",
                "IGNORE": "discard",
                "NO_ACTION": "discard",
            },
            "required_fields": [
                "protocol_version",
                "operating_state",
                "action",
                "decision",
                "thesis",
                "regime_assessment",
                "active_playbook",
                "evidence",
                "entry_trigger",
                "invalidation",
                "risk_assessment",
                "confidence",
                "risks",
                "lessons_applied",
                "reasons_not_to_act",
                "next_action",
                "next_state",
            ],
        },
        "market_state_snapshot": market_state_snapshot,
        "regime_assessment": regime.model_dump(mode="json"),
        "regime_policy": regime_policy,
        "active_playbooks": [
            ActivePlaybook(
                code=playbook.code,
                name=playbook.name,
                priority=playbook.priority,
                why_active=f"{playbook.name} best matches the current setup and regime evidence.",
                setup_focus=playbook.setup_focus,
            ).model_dump(mode="json")
        ],
        "candidate_packet": packet.model_dump(mode="json"),
    }


def build_position_management_protocol_context(
    *,
    position: dict,
    market_snapshot: dict,
    persisted_market_state: dict | None = None,
) -> dict:
    monitor_event = market_snapshot.get("monitor_event") if isinstance(market_snapshot, dict) else None
    playbook_code = _infer_playbook_from_position(position)
    playbook = next((item for item in playbook_catalog() if item.code == playbook_code), playbook_catalog()[0])
    regime = RegimeAssessment(
        label="position_monitoring",
        confidence=0.66,
        justification="The agent is already in MONITOR mode and should react only to relevant position events.",
        supporting_evidence=[
            "open position",
            "live snapshot received",
            *(["relevant market event received"] if isinstance(monitor_event, dict) else []),
        ],
    )
    market_state_snapshot = _merge_position_management_market_state(
        persisted_market_state=persisted_market_state,
        position=position,
        market_snapshot=market_snapshot,
    )
    regime_policy = build_regime_policy_context(regime_label=regime.label, playbook_code=playbook.code)
    return {
        **protocol_manifest(),
        "current_state": AgentOperatingState.MONITOR.value,
        "decision_contract": {
            "execution_actions": [
                "hold",
                "tighten_stop",
                "extend_target",
                "tighten_stop_and_extend_target",
                "close_position",
            ],
            "decision_labels": [item.value for item in ManagementDecisionLabel],
            "execution_mapping": {
                "HOLD": "hold",
                "REDUCE": "tighten_stop",
                "EXIT": "close_position",
                "NO_ACTION": "hold",
            },
            "required_fields": [
                "protocol_version",
                "operating_state",
                "action",
                "decision",
                "thesis",
                "regime_assessment",
                "active_playbook",
                "evidence",
                "invalidation",
                "risk_assessment",
                "confidence",
                "risks",
                "lessons_applied",
                "reasons_not_to_act",
                "next_action",
                "next_state",
            ],
        },
        "market_state_snapshot": market_state_snapshot,
        "regime_assessment": regime.model_dump(mode="json"),
        "regime_policy": regime_policy,
        "active_playbooks": [
            ActivePlaybook(
                code=playbook.code,
                name=playbook.name,
                priority=playbook.priority,
                why_active="This is the originating playbook for the open position under management.",
                setup_focus=playbook.setup_focus,
            ).model_dump(mode="json")
        ],
        "position_packet": {
            "position": position,
            "market_snapshot": market_snapshot,
            "monitor_event": monitor_event,
        },
    }


def candidate_state_transition_for_action(action: str) -> StateTransition:
    normalized = str(action or "").strip().lower()
    if normalized == "paper_enter":
        next_state = AgentOperatingState.EXECUTE
    elif normalized == "watch":
        next_state = AgentOperatingState.MONITOR
    else:
        next_state = AgentOperatingState.OBSERVE
    return StateTransition(
        current_state=AgentOperatingState.DECIDE,
        next_state=next_state,
        trigger=f"candidate_decision:{normalized or 'unknown'}",
    )


def management_state_transition_for_action(action: str) -> StateTransition:
    normalized = str(action or "").strip().lower()
    if normalized == "close_position":
        next_state = AgentOperatingState.REVIEW
    else:
        next_state = AgentOperatingState.MONITOR
    return StateTransition(
        current_state=AgentOperatingState.MONITOR,
        next_state=next_state,
        trigger=f"position_management:{normalized or 'unknown'}",
    )


def build_candidate_decision_system_prompt(
    runtime_skill_prompt: str | None = None,
    runtime_claim_prompt: str | None = None,
) -> str:
    prompt = (
        "You are the single decision-making trading research agent for this system. "
        "Do not produce free-form chain-of-thought. Follow the operating doctrine exactly. "
        f"Primary objective: {PRIMARY_OBJECTIVE} "
        "Priority stack: survive first, preserve capital second, grow only when edge is clear. "
        "Doctrine rules: cash is valid; no position without thesis, invalidation, exit plan and acceptable liquidity; "
        "size from risk not conviction; do not improvise because of FOMO; only interpret trusted tool outputs. "
        "Protocol: update world state, classify regime, activate compatible playbooks, scan, analyze, decide, execute, "
        "monitor, review and improve. For this call you are in DECIDE state. "
        "Use only the supplied evidence. Do not invent indicators, prices, catalysts or fundamentals. "
        "Choose one execution action only: paper_enter, watch or discard. "
        "Also choose one protocol decision label only: IGNORE, WATCH, SET_ALERT, ENTER_LONG, ENTER_SHORT or NO_ACTION. "
        "If durable claim memory materially influenced the decision, list the relevant claim keys in claims_applied; otherwise return an empty list. "
        "Prefer watch, set_alert, discard or cash when the setup is immature, crowded, illiquid or contextually weak. "
        "Return JSON only. Required fields are protocol_version, operating_state, action, decision, thesis, "
        "regime_assessment, active_playbook, evidence, entry_trigger, invalidation, risk_assessment, confidence, risks, "
        "lessons_applied, reasons_not_to_act, claims_applied, next_action and next_state."
    )
    if isinstance(runtime_skill_prompt, str) and runtime_skill_prompt.strip():
        prompt = f"{prompt}\n\n{runtime_skill_prompt.strip()}"
    if isinstance(runtime_claim_prompt, str) and runtime_claim_prompt.strip():
        prompt = f"{prompt}\n\n{runtime_claim_prompt.strip()}"
    return prompt


def build_position_management_system_prompt(
    runtime_skill_prompt: str | None = None,
    runtime_claim_prompt: str | None = None,
) -> str:
    prompt = (
        "You are the single decision-making trading research agent managing an already open paper position. "
        "Do not produce free-form chain-of-thought. Follow the operating doctrine exactly and remain conservative. "
        f"Primary objective: {PRIMARY_OBJECTIVE} "
        "When a position is open, react only to relevant events and do not re-underwrite the whole market from scratch. "
        "For this call you are in MONITOR state. Choose one execution action only: hold, tighten_stop, extend_target, "
        "tighten_stop_and_extend_target, or close_position. Also choose one protocol decision label only: HOLD, REDUCE, EXIT or NO_ACTION. "
        "If durable claim memory materially influenced the decision, list the relevant claim keys in claims_applied; otherwise return an empty list. "
        "Only intervene when the evidence is clear and the action improves risk-adjusted behaviour. "
        "Return JSON only. Required fields are protocol_version, operating_state, action, decision, thesis, "
        "regime_assessment, active_playbook, evidence, invalidation, risk_assessment, confidence, risks, "
        "lessons_applied, reasons_not_to_act, claims_applied, next_action and next_state."
    )
    if isinstance(runtime_skill_prompt, str) and runtime_skill_prompt.strip():
        prompt = f"{prompt}\n\n{runtime_skill_prompt.strip()}"
    if isinstance(runtime_claim_prompt, str) and runtime_claim_prompt.strip():
        prompt = f"{prompt}\n\n{runtime_claim_prompt.strip()}"
    return prompt


def _default_entry_trigger(playbook_code: str) -> str:
    if playbook_code == "pullback_long":
        return "reclaim of support with evidence that downside momentum is fading"
    if playbook_code == "position_long":
        return "constructive entry near structural support without excessive extension"
    return "clean breakout through resistance with confirming volume"


def _default_invalidation(playbook_code: str) -> str:
    if playbook_code == "pullback_long":
        return "loss of trend support and failure to reclaim it promptly"
    if playbook_code == "position_long":
        return "structural trend break or thesis-changing event"
    return "failed breakout and rejection back below the breakout zone"


def _infer_playbook_from_position(position: dict) -> str:
    entry_context = position.get("entry_context") if isinstance(position.get("entry_context"), dict) else {}
    decision_trace = entry_context.get("decision_trace") if isinstance(entry_context.get("decision_trace"), dict) else {}
    protocol = decision_trace.get("protocol") if isinstance(decision_trace.get("protocol"), dict) else {}
    playbook = protocol.get("playbook") or entry_context.get("playbook")
    if isinstance(playbook, str) and playbook.strip():
        return playbook
    return "breakout_long"


def _merge_market_state_snapshot(
    *,
    persisted_market_state: dict | None,
    default_state: dict,
    watchlist_code: str | None,
    market_context: dict,
    signal_payload: dict,
    ticker: str,
) -> dict:
    merged = dict(persisted_market_state or {})
    merged["execution_mode"] = str(market_context.get("execution_mode") or merged.get("execution_mode") or "default")
    merged["watchlist_code"] = watchlist_code or merged.get("watchlist_code")
    portfolio_state = dict(merged.get("portfolio_state") or {})
    portfolio_state["risk_budget"] = signal_payload.get("risk_budget")
    merged["portfolio_state"] = portfolio_state or dict(default_state.get("portfolio_state") or {})
    market_regime_inputs = dict(merged.get("market_regime_inputs") or {})
    market_regime_inputs.update(
        {
            "macro_fit": (signal_payload.get("decision_context") or {}).get("macro_fit")
            if isinstance(signal_payload.get("decision_context"), dict)
            else {},
            "guard_results": signal_payload.get("guard_results"),
            "score_breakdown": signal_payload.get("score_breakdown"),
        }
    )
    merged["market_regime_inputs"] = market_regime_inputs
    active_watchlists = merged.get("active_watchlists")
    if not isinstance(active_watchlists, list) or not active_watchlists:
        merged["active_watchlists"] = (
            [{"code": watchlist_code, "ticker": ticker.upper()}]
            if watchlist_code
            else [{"ticker": ticker.upper()}]
        )
    if "macro_context" not in merged:
        merged["macro_context"] = dict(default_state.get("macro_context") or {})
    if "corporate_calendar" not in merged:
        merged["corporate_calendar"] = list(default_state.get("corporate_calendar") or [])
    if "open_positions" not in merged:
        merged["open_positions"] = list(default_state.get("open_positions") or [])
    if "recent_alerts" not in merged:
        merged["recent_alerts"] = list(default_state.get("recent_alerts") or [])
    return MarketStateSnapshot.model_validate({**default_state, **merged}).model_dump(mode="json")


def _merge_position_management_market_state(
    *,
    persisted_market_state: dict | None,
    position: dict,
    market_snapshot: dict,
) -> dict:
    default_state = MarketStateSnapshot(
        execution_mode="position_management",
        portfolio_state={"managed_position_id": position.get("id")},
        open_positions=[position],
        market_regime_inputs={"market_snapshot": market_snapshot},
    ).model_dump(mode="json")
    merged = dict(persisted_market_state or {})
    merged["execution_mode"] = "position_management"
    portfolio_state = dict(merged.get("portfolio_state") or {})
    portfolio_state["managed_position_id"] = position.get("id")
    merged["portfolio_state"] = portfolio_state
    open_positions = list(merged.get("open_positions") or [])
    if not any(
        isinstance(item, dict)
        and (item.get("position_id") == position.get("id") or item.get("id") == position.get("id"))
        for item in open_positions
    ):
        open_positions.insert(0, position)
    merged["open_positions"] = open_positions
    market_regime_inputs = dict(merged.get("market_regime_inputs") or {})
    market_regime_inputs["market_snapshot"] = market_snapshot
    merged["market_regime_inputs"] = market_regime_inputs
    if "macro_context" not in merged:
        merged["macro_context"] = {}
    if "corporate_calendar" not in merged:
        merged["corporate_calendar"] = []
    if "recent_alerts" not in merged:
        merged["recent_alerts"] = []
    return MarketStateSnapshot.model_validate({**default_state, **merged}).model_dump(mode="json")


def _regime_assessment_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "confidence": {"type": "number"},
            "justification": {"type": "string"},
            "supporting_evidence": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["label", "confidence", "justification", "supporting_evidence"],
    }
