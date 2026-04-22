from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models.journal import JournalEntry
from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.learning_workflow import LearningWorkflowArtifact
from app.db.models.memory import MemoryItem
from app.db.models.skill_validation import SkillValidationRecord
from app.domains.learning.operator_feedback import OperatorDisagreementService


SKILL_CATALOG_VERSION = "skills_v1"
SKILL_PROPOSAL_MEMORY_TYPE = "skill_proposal"
SKILL_CANDIDATE_MEMORY_TYPE = "skill_candidate"
VALIDATED_SKILL_REVISION_MEMORY_TYPE = "validated_skill_revision"
SKILL_GAP_MEMORY_TYPE = "skill_gap"


@dataclass(frozen=True)
class SkillDefinition:
    code: str
    name: str
    category: str
    phases: tuple[str, ...]
    objective: str
    description: str
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    priority: int = 50
    dependencies: tuple[str, ...] = ()
    incompatible_with: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "phases": list(self.phases),
            "objective": self.objective,
            "description": self.description,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "requires": list(self.requires),
            "produces": list(self.produces),
            "priority": self.priority,
            "dependencies": list(self.dependencies),
            "incompatible_with": list(self.incompatible_with),
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class SkillMatch:
    skill_code: str
    reason: str
    phase: str
    confidence: float = 0.7
    evidence: dict = field(default_factory=dict)

    def to_payload(self, definition: SkillDefinition) -> dict:
        return {
            "code": definition.code,
            "name": definition.name,
            "category": definition.category,
            "priority": definition.priority,
            "phase": self.phase,
            "confidence": round(min(max(float(self.confidence), 0.0), 1.0), 2),
            "reason": self.reason,
            "evidence": dict(self.evidence or {}),
            "tags": list(definition.tags),
            "requires": list(definition.requires),
            "produces": list(definition.produces),
        }


@dataclass(frozen=True)
class SkillRuntimePacket:
    skill_code: str
    skill_name: str
    category: str
    phase: str
    objective: str
    selection_reason: str
    instruction_source: str
    confidence: float
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    required_context: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    procedure_steps: tuple[str, ...] = ()
    hard_limits: tuple[str, ...] = ()
    validated_revision_id: int | None = None
    validated_revision_summary: str | None = None

    def to_payload(self) -> dict:
        return {
            "skill_code": self.skill_code,
            "skill_name": self.skill_name,
            "category": self.category,
            "phase": self.phase,
            "objective": self.objective,
            "selection_reason": self.selection_reason,
            "instruction_source": self.instruction_source,
            "confidence": round(min(max(float(self.confidence), 0.0), 1.0), 2),
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "required_context": list(self.required_context),
            "expected_outputs": list(self.expected_outputs),
            "procedure_steps": list(self.procedure_steps),
            "hard_limits": list(self.hard_limits),
            "validated_revision_id": self.validated_revision_id,
            "validated_revision_summary": self.validated_revision_summary,
        }


@dataclass(frozen=True)
class SkillGapDefinition:
    gap_type: str
    summary: str
    scope: str
    key: str
    ticker: str | None = None
    strategy_version_id: int | None = None
    position_id: int | None = None
    source_type: str = "trade_review"
    source_trade_review_id: int | None = None
    status: str = "open"
    linked_skill_code: str | None = None
    target_skill_code: str | None = None
    candidate_action: str | None = None
    importance: float = 0.7
    evidence: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "gap_type": self.gap_type,
            "summary": self.summary,
            "scope": self.scope,
            "key": self.key,
            "ticker": self.ticker,
            "strategy_version_id": self.strategy_version_id,
            "position_id": self.position_id,
            "source_type": self.source_type,
            "source_trade_review_id": self.source_trade_review_id,
            "status": self.status,
            "linked_skill_code": self.linked_skill_code,
            "target_skill_code": self.target_skill_code,
            "candidate_action": self.candidate_action,
            "importance": self.importance,
            "evidence": dict(self.evidence or {}),
        }


def skill_catalog() -> tuple[SkillDefinition, ...]:
    return (
        SkillDefinition(
            code="analyze_ticker_post_news",
            name="Analyze Ticker Post News",
            category="analysis",
            phases=("do",),
            objective="Evaluate a ticker when fresh company-specific news may alter setup quality or timing.",
            description="Use fresh news and catalyst context to avoid treating a post-news ticker like a plain technical setup.",
            use_when=(
                "recent ticker news exists",
                "news contains a likely catalyst or guidance change",
            ),
            avoid_when=("no relevant news context is available",),
            requires=("news_context", "quant_summary", "visual_summary"),
            produces=("news-aware bias", "catalyst-aware caution"),
            priority=92,
            tags=("news", "catalyst", "analysis"),
        ),
        SkillDefinition(
            code="evaluate_daily_breakout",
            name="Evaluate Daily Breakout",
            category="analysis",
            phases=("do",),
            objective="Judge whether a daily breakout is structurally tradable instead of merely visible.",
            description="Evaluate breakout quality, extension, volume confirmation and nearby invalidation before entry.",
            use_when=(
                "setup resembles a breakout",
                "playbook or strategy context is breakout-oriented",
            ),
            avoid_when=("the ticker is not in a breakout-like structure",),
            requires=("quant_summary", "visual_summary", "price_action_context"),
            produces=("breakout_quality_assessment", "entry_or_no_trade"),
            priority=90,
            tags=("breakout", "daily", "analysis"),
        ),
        SkillDefinition(
            code="evaluate_support_reclaim_reversal",
            name="Evaluate Support Reclaim Reversal",
            category="analysis",
            phases=("do",),
            objective="Handle reclaim / failed-breakdown / rejection-at-support contexts without pretending to have order-flow data.",
            description="Interpret daily price-action reversal proxies honestly using only OHLCV, structure and relative volume.",
            use_when=(
                "price_action_context shows a reclaim, rejection wick or failed breakdown",
            ),
            avoid_when=("no reversal-style price-action proxy is active",),
            requires=("price_action_context", "quant_summary"),
            produces=("reversal_quality_assessment", "watch_or_entry_filter"),
            priority=86,
            tags=("reversal", "support", "price_action"),
        ),
        SkillDefinition(
            code="detect_risk_off_conditions",
            name="Detect Risk-Off Conditions",
            category="context",
            phases=("do", "monitor"),
            objective="Downgrade confidence when market regime, event risk or expiry noise make execution quality fragile.",
            description="Detect when the broader environment is hostile enough that otherwise-valid setups should be treated more conservatively.",
            use_when=(
                "macro or regime context is hostile",
                "event risk is near",
                "expiry week noise is active",
            ),
            avoid_when=("market and event context are normal and constructive",),
            requires=("macro_context", "calendar_context", "regime_policy"),
            produces=("risk_off_flag", "execution_caution"),
            priority=95,
            tags=("risk_off", "macro", "calendar"),
        ),
        SkillDefinition(
            code="do_trade_post_mortem",
            name="Do Trade Post Mortem",
            category="review",
            phases=("check", "act"),
            objective="Convert a closed trade into structured review instead of a vague conclusion.",
            description="Review trade outcome against plan, invalidation, execution quality and follow-through.",
            use_when=("a position has been closed and reviewed",),
            avoid_when=("there is no completed trade outcome yet",),
            requires=("trade_review", "position", "entry_context"),
            produces=("structured_lesson", "review_trace"),
            priority=94,
            tags=("review", "post_mortem", "pdca"),
        ),
        SkillDefinition(
            code="classify_operational_error",
            name="Classify Operational Error",
            category="review",
            phases=("check", "act"),
            objective="Separate setup failure, execution error and contextual mismatch before changing the system.",
            description="Classify the dominant failure mode so later improvements target the real issue.",
            use_when=(
                "trade review includes a cause category, failure mode or root cause",
            ),
            avoid_when=("no review evidence is available",),
            requires=("trade_review",),
            produces=("error_classification", "diagnostic_trace"),
            priority=88,
            tags=("review", "error", "diagnostic"),
        ),
        SkillDefinition(
            code="propose_pdca_improvement",
            name="Propose PDCA Improvement",
            category="improvement",
            phases=("act",),
            objective="Promote reviewed evidence into a bounded improvement candidate instead of changing live behavior directly.",
            description="Translate lessons and proposed changes into a candidate improvement that still requires validation.",
            use_when=(
                "review evidence suggests a procedural improvement",
                "recommended changes or strategy updates were proposed",
            ),
            avoid_when=("the review produced no actionable improvement candidate",),
            requires=("trade_review", "lesson", "recommended_changes"),
            produces=("improvement_candidate", "promotion_trace"),
            priority=84,
            tags=("improvement", "pdca", "promotion"),
        ),
    )


class SkillCatalogService:
    def __init__(self) -> None:
        self._catalog = {item.code: item for item in skill_catalog()}

    def list_skills(self) -> list[dict]:
        return [item.to_payload() for item in sorted(self._catalog.values(), key=lambda item: (-item.priority, item.code))]

    def get(self, code: str) -> SkillDefinition | None:
        return self._catalog.get(str(code or "").strip())

    def has(self, code: str) -> bool:
        return self.get(code) is not None


class SkillRouterService:
    REVERSAL_SIGNALS = {
        "failed_breakdown_reversal",
        "support_reclaim_confirmation",
        "rejection_wick_at_support",
        "high_relative_volume_reversal",
    }

    def __init__(self, catalog_service: SkillCatalogService | None = None) -> None:
        self.catalog_service = catalog_service or SkillCatalogService()

    def route_trade_candidate(
        self,
        *,
        ticker: str,
        signal_payload: dict,
        strategy_rules: dict,
        market_context: dict,
        macro_context: dict,
        calendar_context: dict,
        news_context: dict,
        price_action_context: dict,
        intermarket_context: dict,
        mstr_context: dict,
        regime_policy: dict,
        risk_budget: dict,
    ) -> dict:
        matches: list[SkillMatch] = []
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
        setup = str(quant.get("setup") or visual.get("setup_type") or "").strip().lower()
        active_regimes = macro_context.get("active_regimes") if isinstance(macro_context.get("active_regimes"), list) else []
        expiry_context = (
            calendar_context.get("expiry_context") if isinstance(calendar_context.get("expiry_context"), dict) else {}
        )
        article_count = int(news_context.get("article_count") or 0) if isinstance(news_context, dict) else 0
        catalyst_hits = int(news_context.get("catalyst_hits") or 0) if isinstance(news_context, dict) else 0
        primary_signal = str(price_action_context.get("primary_signal_code") or "").strip().lower()
        risk_multiplier = float(regime_policy.get("risk_multiplier") or 1.0) if isinstance(regime_policy, dict) else 1.0
        event_risk_flags = (
            risk_budget.get("event_risk_flags") if isinstance(risk_budget.get("event_risk_flags"), list) else []
        )
        if article_count > 0 or catalyst_hits > 0:
            matches.append(
                SkillMatch(
                    skill_code="analyze_ticker_post_news",
                    phase="do",
                    confidence=0.82 if catalyst_hits > 0 else 0.72,
                    reason="fresh ticker news or catalyst context is present",
                    evidence={
                        "article_count": article_count,
                        "catalyst_hits": catalyst_hits,
                        "ticker": ticker.upper(),
                    },
                )
            )
        if "breakout" in setup or str(regime_policy.get("playbook") or "").strip() == "breakout_long":
            matches.append(
                SkillMatch(
                    skill_code="evaluate_daily_breakout",
                    phase="do",
                    confidence=0.85,
                    reason="setup or playbook suggests a daily breakout context",
                    evidence={
                        "setup": setup or None,
                        "playbook": regime_policy.get("playbook"),
                    },
                )
            )
        if primary_signal in self.REVERSAL_SIGNALS or "reversal" in setup or "pullback" in setup:
            matches.append(
                SkillMatch(
                    skill_code="evaluate_support_reclaim_reversal",
                    phase="do",
                    confidence=0.8,
                    reason="price action or setup suggests reclaim / failed-breakdown / support-reversal behavior",
                    evidence={
                        "primary_signal": primary_signal or None,
                        "setup": setup or None,
                    },
                )
            )
        risk_off_detected = bool(
            "high_volatility_risk_off" in active_regimes
            or str(market_context.get("market_state_regime") or "").strip() == "high_volatility_risk_off"
            or risk_multiplier < 0.8
            or bool(event_risk_flags)
            or bool(expiry_context.get("pre_expiry_window"))
            or bool(expiry_context.get("expiry_day"))
            or bool(expiry_context.get("expiration_week"))
            or bool(mstr_context.get("atm_risk_context") == "high")
            or bool(intermarket_context.get("requires_caution"))
        )
        if risk_off_detected:
            matches.append(
                SkillMatch(
                    skill_code="detect_risk_off_conditions",
                    phase="do",
                    confidence=0.9,
                    reason="macro, event-risk or expiry context suggests degraded execution quality",
                    evidence={
                        "active_regimes": list(active_regimes[:3]),
                        "risk_multiplier": round(risk_multiplier, 2),
                        "event_risk_flags": list(event_risk_flags[:4]),
                        "expiry_phase": expiry_context.get("phase"),
                    },
                )
            )
        return self._finalize(
            phase="do",
            matched_skills=matches,
            max_applied=3,
        )

    def route_position_management(
        self,
        *,
        position,
        market_price: float,
        expiry_context: dict,
        price_action_context: dict,
        mstr_context: dict,
        ai_action: str | None,
        ai_error: str | None,
    ) -> dict:
        matches: list[SkillMatch] = []
        primary_signal = str(price_action_context.get("primary_signal_code") or "").strip().lower()
        if (
            bool(expiry_context.get("pre_expiry_window"))
            or bool(expiry_context.get("expiry_day"))
            or bool(expiry_context.get("expiration_week"))
            or primary_signal in self.REVERSAL_SIGNALS
            or str(ai_action or "").strip().lower() in {"tighten_stop", "tighten_stop_and_extend_target", "close_position"}
            or bool(ai_error)
            or bool(mstr_context.get("atm_risk_context") == "high")
        ):
            matches.append(
                SkillMatch(
                    skill_code="detect_risk_off_conditions",
                    phase="monitor",
                    confidence=0.76,
                    reason="position management context shows elevated execution or reversal risk",
                    evidence={
                        "ticker": getattr(position, "ticker", None),
                        "market_price": round(float(market_price), 2),
                        "expiry_phase": expiry_context.get("phase"),
                        "primary_signal": primary_signal or None,
                        "ai_action": ai_action,
                        "ai_error": ai_error,
                    },
                )
            )
        return self._finalize(
            phase="monitor",
            matched_skills=matches,
            max_applied=2,
        )

    def route_trade_review(
        self,
        *,
        position,
        review,
        entry_context: dict | None = None,
    ) -> dict:
        matches: list[SkillMatch] = []
        entry_context = dict(entry_context or {})
        matches.append(
            SkillMatch(
                skill_code="do_trade_post_mortem",
                phase="act",
                confidence=0.92,
                reason="a closed position review always requires a structured post-mortem",
                evidence={
                    "ticker": getattr(position, "ticker", None),
                    "position_id": getattr(position, "id", None),
                    "trade_review_id": getattr(review, "id", None),
                },
            )
        )
        if any(
            bool(str(value or "").strip())
            for value in (
                getattr(review, "cause_category", None),
                getattr(review, "failure_mode", None),
                getattr(review, "root_cause", None),
            )
        ):
            matches.append(
                SkillMatch(
                    skill_code="classify_operational_error",
                    phase="act",
                    confidence=0.84,
                    reason="review carries explicit cause or failure information that should be classified",
                    evidence={
                        "cause_category": getattr(review, "cause_category", None),
                        "failure_mode": getattr(review, "failure_mode", None),
                    },
                )
            )
        if (
            getattr(review, "should_modify_strategy", False)
            or getattr(review, "needs_strategy_update", False)
            or bool(getattr(review, "recommended_changes", None))
            or bool(str(getattr(review, "proposed_strategy_change", "") or "").strip())
        ):
            matches.append(
                SkillMatch(
                    skill_code="propose_pdca_improvement",
                    phase="act",
                    confidence=0.8,
                    reason="review includes actionable changes that should be promoted into a controlled improvement candidate",
                    evidence={
                        "recommended_changes_count": len(getattr(review, "recommended_changes", []) or []),
                        "should_modify_strategy": bool(getattr(review, "should_modify_strategy", False)),
                        "needs_strategy_update": bool(getattr(review, "needs_strategy_update", False)),
                        "entry_primary_skill": (
                            (entry_context.get("skill_context") or {}).get("primary_skill_code")
                            if isinstance(entry_context.get("skill_context"), dict)
                            else None
                        ),
                    },
                )
            )
        return self._finalize(
            phase="act",
            matched_skills=matches,
            max_applied=3,
        )

    def suggested_skill_codes_from_feature(
        self,
        *,
        feature_scope: str,
        feature_key: str,
        feature_value: str,
    ) -> list[str]:
        scope = str(feature_scope or "").strip().lower()
        key = str(feature_key or "").strip().lower()
        value = str(feature_value or "").strip().lower()
        suggestions: list[str] = []

        if key == "primary_skill" and self.catalog_service.has(value):
            suggestions.append(value)
        if "news" in key or (scope == "news" and value == "true"):
            suggestions.append("analyze_ticker_post_news")
        if "breakout" in value or key == "setup" and "breakout" in value or key == "setup__has_news":
            suggestions.append("evaluate_daily_breakout")
        if key == "primary_signal" and value in self.REVERSAL_SIGNALS:
            suggestions.append("evaluate_support_reclaim_reversal")
        if scope == "calendar" or "expiry" in key or key == "primary_regime" and "risk_off" in value:
            suggestions.append("detect_risk_off_conditions")
        deduped: list[str] = []
        for code in suggestions:
            if code not in deduped and self.catalog_service.has(code):
                deduped.append(code)
        return deduped

    def _finalize(
        self,
        *,
        phase: str,
        matched_skills: list[SkillMatch],
        max_applied: int,
    ) -> dict:
        deduped: dict[str, SkillMatch] = {}
        for match in matched_skills:
            existing = deduped.get(match.skill_code)
            if existing is None or match.confidence > existing.confidence:
                deduped[match.skill_code] = match

        ordered = sorted(
            deduped.values(),
            key=lambda item: (
                -int(self.catalog_service.get(item.skill_code).priority if self.catalog_service.get(item.skill_code) else 0),
                -item.confidence,
                item.skill_code,
            ),
        )
        considered: list[dict] = []
        for match in ordered:
            definition = self.catalog_service.get(match.skill_code)
            if definition is None:
                continue
            considered.append(match.to_payload(definition))
        applied = considered[: max(int(max_applied), 0)]
        primary_skill_code = applied[0]["code"] if applied else None
        return {
            "catalog_version": SKILL_CATALOG_VERSION,
            "routing_mode": "deterministic_v1",
            "phase": phase,
            "considered_skills": considered,
            "applied_skills": applied,
            "primary_skill_code": primary_skill_code,
            "risk_skill_active": any(item["code"] == "detect_risk_off_conditions" for item in applied),
            "summary": self._build_summary(primary_skill_code=primary_skill_code, applied_skills=applied),
        }

    @staticmethod
    def _build_summary(*, primary_skill_code: str | None, applied_skills: list[dict]) -> str:
        if not applied_skills:
            return "No procedural skill matched deterministically for the current context."
        if len(applied_skills) == 1:
            return f"Primary skill selected: {primary_skill_code}."
        trailing = ", ".join(item["code"] for item in applied_skills[1:])
        return f"Primary skill selected: {primary_skill_code}; supporting skills: {trailing}."


class SkillPromotionService:
    def __init__(self, catalog_service: SkillCatalogService | None = None) -> None:
        self.catalog_service = catalog_service or SkillCatalogService()

    def build_candidate_from_trade_review(
        self,
        *,
        position,
        review,
        review_skill_context: dict,
        entry_context: dict | None = None,
    ) -> dict | None:
        entry_context = dict(entry_context or {})
        recommended_changes = [
            str(item).strip()
            for item in (getattr(review, "recommended_changes", None) or [])
            if str(item).strip()
        ]
        proposed_change = str(getattr(review, "proposed_strategy_change", "") or "").strip()
        if not (
            getattr(review, "should_modify_strategy", False)
            or getattr(review, "needs_strategy_update", False)
            or recommended_changes
            or proposed_change
        ):
            return None

        entry_skill_context = entry_context.get("skill_context") if isinstance(entry_context.get("skill_context"), dict) else {}
        target_skill_code = str(entry_skill_context.get("primary_skill_code") or "").strip()
        candidate_action = "update_existing_skill"
        if not self.catalog_service.has(target_skill_code):
            target_skill_code = f"review_{str(getattr(review, 'cause_category', '') or 'generic').strip().lower() or 'generic'}"
            candidate_action = "draft_candidate_skill"

        summary = (
            proposed_change
            or (recommended_changes[0] if recommended_changes else "")
            or str(getattr(review, "lesson_learned", "") or "").strip()
            or "Promote reviewed lesson into a candidate procedural skill."
        )
        return {
            "promotion_path_stage": "lesson",
            "candidate_status": "draft",
            "candidate_action": candidate_action,
            "target_skill_code": target_skill_code,
            "source_type": "trade_review",
            "source_trade_review_id": getattr(review, "id", None),
            "position_id": getattr(position, "id", None),
            "ticker": getattr(position, "ticker", None),
            "strategy_version_id": getattr(position, "strategy_version_id", None),
            "cause_category": getattr(review, "cause_category", None),
            "failure_mode": getattr(review, "failure_mode", None),
            "summary": summary,
            "validation_required": True,
            "review_skill_context": dict(review_skill_context or {}),
            "recommended_changes": recommended_changes,
            "proposed_strategy_change": proposed_change or None,
        }

    def build_temporary_rule_trace(
        self,
        *,
        feature_scope: str,
        feature_key: str,
        feature_value: str,
    ) -> dict:
        router = SkillRouterService(catalog_service=self.catalog_service)
        return {
            "promotion_path_stage": "temporary_rule",
            "source_type": "feature_outcome_stat",
            "suggested_skill_codes": router.suggested_skill_codes_from_feature(
                feature_scope=feature_scope,
                feature_key=feature_key,
                feature_value=feature_value,
            ),
            "validation_required": True,
        }


class SkillGapService:
    def build_trade_review_gaps(
        self,
        *,
        position,
        review,
        review_skill_context: dict | None,
        entry_context: dict | None = None,
        skill_candidate: dict | None = None,
    ) -> list[dict]:
        entry_context = dict(entry_context or {})
        review_skill_context = dict(review_skill_context or {})
        skill_candidate = dict(skill_candidate or {}) if isinstance(skill_candidate, dict) else None

        entry_skill_context = entry_context.get("skill_context") if isinstance(entry_context.get("skill_context"), dict) else {}
        entry_primary_skill = str(entry_skill_context.get("primary_skill_code") or "").strip() or None
        setup = self._extract_setup(entry_context)
        recommended_changes = [
            str(item).strip()
            for item in (getattr(review, "recommended_changes", None) or [])
            if str(item).strip()
        ]
        proposed_change = str(getattr(review, "proposed_strategy_change", "") or "").strip()
        review_requires_improvement = bool(
            getattr(review, "should_modify_strategy", False)
            or getattr(review, "needs_strategy_update", False)
            or recommended_changes
            or proposed_change
        )

        gaps: list[SkillGapDefinition] = []

        if review_requires_improvement and not entry_primary_skill:
            gaps.append(
                SkillGapDefinition(
                    gap_type="missing_entry_skill_context",
                    summary="Trade review identified a procedural improvement need, but the original entry carried no routed primary skill.",
                    scope=f"strategy:{getattr(position, 'strategy_version_id', None) or 'unknown'}",
                    key=f"skill_gap:{getattr(position, 'id', 'unknown')}:{getattr(review, 'id', 'unknown')}:missing_entry_skill_context",
                    ticker=getattr(position, "ticker", None),
                    strategy_version_id=getattr(position, "strategy_version_id", None),
                    position_id=getattr(position, "id", None),
                    source_trade_review_id=getattr(review, "id", None),
                    linked_skill_code=None,
                    target_skill_code=(
                        str(skill_candidate.get("target_skill_code") or "").strip() or None
                        if skill_candidate
                        else None
                    ),
                    candidate_action=skill_candidate.get("candidate_action") if skill_candidate else None,
                    importance=0.76,
                    evidence={
                        "cause_category": getattr(review, "cause_category", None),
                        "failure_mode": getattr(review, "failure_mode", None),
                        "setup": setup,
                        "recommended_changes_count": len(recommended_changes),
                        "review_phase_skill": review_skill_context.get("primary_skill_code"),
                    },
                )
            )

        if skill_candidate and str(skill_candidate.get("candidate_action") or "").strip() == "draft_candidate_skill":
            target_skill_code = str(skill_candidate.get("target_skill_code") or "").strip() or None
            gaps.append(
                SkillGapDefinition(
                    gap_type="missing_catalog_skill",
                    summary="Reviewed evidence suggests a procedural gap not covered by the current skill catalog; the improvement remains a draft-only candidate.",
                    scope=f"strategy:{getattr(position, 'strategy_version_id', None) or 'unknown'}",
                    key=f"skill_gap:{getattr(position, 'id', 'unknown')}:{getattr(review, 'id', 'unknown')}:missing_catalog_skill",
                    ticker=getattr(position, "ticker", None),
                    strategy_version_id=getattr(position, "strategy_version_id", None),
                    position_id=getattr(position, "id", None),
                    source_trade_review_id=getattr(review, "id", None),
                    linked_skill_code=entry_primary_skill,
                    target_skill_code=target_skill_code,
                    candidate_action="draft_candidate_skill",
                    importance=0.82,
                    evidence={
                        "cause_category": getattr(review, "cause_category", None),
                        "failure_mode": getattr(review, "failure_mode", None),
                        "setup": setup,
                        "suggested_skill_code": target_skill_code,
                        "recommended_changes_count": len(recommended_changes),
                    },
                )
            )

        return [gap.to_payload() for gap in gaps]

    def list_gaps(self, session: Session, *, limit: int = 50) -> list[dict]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_GAP_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
            .limit(max(1, min(int(limit), 200)))
        )
        return [self._gap_payload(item) for item in session.scalars(statement).all()]

    def get_gap(self, session: Session, *, gap_id: int) -> dict | None:
        item = session.get(MemoryItem, gap_id)
        if item is None or item.memory_type != SKILL_GAP_MEMORY_TYPE:
            return None
        return self._gap_payload(item)

    def review_gap(
        self,
        session: Session,
        *,
        gap_id: int,
        outcome: str,
        summary: str,
    ) -> dict:
        item = session.get(MemoryItem, gap_id)
        if item is None or item.memory_type != SKILL_GAP_MEMORY_TYPE:
            raise ValueError("Skill gap not found.")

        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"resolve", "dismiss"}:
            raise ValueError("Skill gap outcome must be resolve or dismiss.")
        summary_text = str(summary or "").strip()
        if not summary_text:
            raise ValueError("Skill gap review summary is required.")

        from app.domains.learning.schemas import JournalEntryCreate
        from app.domains.learning.services import JournalService

        meta = dict(item.meta or {})
        meta["status"] = "resolved" if normalized_outcome == "resolve" else "dismissed"
        meta["resolution_summary"] = summary_text
        meta["resolved_at"] = datetime.now(UTC).isoformat()
        meta["resolution_source"] = "workflow_action"
        item.meta = meta
        session.add(item)
        session.commit()
        session.refresh(item)

        JournalService().create_entry(
            session,
            JournalEntryCreate(
                entry_type="skill_gap_resolved" if normalized_outcome == "resolve" else "skill_gap_dismissed",
                ticker=meta.get("ticker"),
                strategy_version_id=meta.get("strategy_version_id"),
                position_id=meta.get("position_id"),
                observations={
                    "skill_gap": SkillLifecycleService._json_ready_payload(self._gap_payload(item)),
                    "workflow_action": normalized_outcome,
                },
                reasoning=summary_text,
                decision=normalized_outcome,
                lessons=summary_text,
            ),
        )
        if normalized_outcome == "dismiss":
            OperatorDisagreementService().record(
                session,
                disagreement_type="skill_gap_dismissed",
                entity_type="skill_gap",
                entity_id=item.id,
                action=normalized_outcome,
                summary=summary_text,
                ticker=meta.get("ticker"),
                strategy_version_id=meta.get("strategy_version_id"),
                position_id=meta.get("position_id"),
                source="skill_gap_review",
                details={
                    "gap_type": meta.get("gap_type"),
                    "target_skill_code": meta.get("target_skill_code"),
                    "candidate_action": meta.get("candidate_action"),
                    "gap_status": meta.get("status"),
                },
            )
        return self._gap_payload(item)

    def promote_gap_to_candidate(self, session: Session, *, gap_id: int) -> dict:
        item = session.get(MemoryItem, gap_id)
        if item is None or item.memory_type != SKILL_GAP_MEMORY_TYPE:
            raise ValueError("Skill gap not found.")

        meta = dict(item.meta or {})
        existing_candidate = self._find_existing_candidate(session, gap_id=gap_id)
        if existing_candidate is not None:
            self._link_gap_to_candidate(session, gap=item, candidate=existing_candidate, created=False)
            return SkillLifecycleService._candidate_payload(existing_candidate)

        from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
        from app.domains.learning.services import JournalService, MemoryService

        candidate_meta = self.build_candidate_meta(item)
        summary = str(candidate_meta.get("summary") or item.content or "").strip() or "Promote skill gap into candidate procedural skill."
        candidate_scope = str(item.scope or f"strategy:{meta.get('strategy_version_id') or 'unknown'}")
        candidate_key = f"skill_candidate:gap:{item.id}:{str(candidate_meta.get('target_skill_code') or 'unmapped')}"[:160]

        candidate_item = MemoryService().create_item(
            session,
            MemoryItemCreate(
                memory_type=SKILL_CANDIDATE_MEMORY_TYPE,
                scope=candidate_scope,
                key=candidate_key,
                content=summary,
                meta=candidate_meta,
                importance=min(max(float(item.importance or 0.72), 0.55), 0.95),
            ),
        )

        journal_entry = JournalService().create_entry(
            session,
            JournalEntryCreate(
                entry_type="skill_candidate_from_gap",
                ticker=meta.get("ticker"),
                strategy_version_id=meta.get("strategy_version_id"),
                position_id=meta.get("position_id"),
                observations={
                    "skill_gap": SkillLifecycleService._json_ready_payload(self._gap_payload(item)),
                    "skill_candidate": SkillLifecycleService._json_ready_payload(
                        SkillLifecycleService._candidate_payload(candidate_item)
                    ),
                },
                reasoning=summary,
                decision="promote_gap_to_skill_candidate",
                lessons=summary,
            ),
        )

        self._link_gap_to_candidate(session, gap=item, candidate=candidate_item, created=True, journal_entry_id=journal_entry.id)
        payload = SkillLifecycleService._candidate_payload(candidate_item)
        payload["journal_entry_id"] = journal_entry.id
        return payload

    def build_candidate_meta(self, item: MemoryItem) -> dict:
        meta = dict(item.meta or {})
        target_skill_code = str(meta.get("target_skill_code") or meta.get("linked_skill_code") or "").strip()
        candidate_action = str(meta.get("candidate_action") or "").strip()
        if not target_skill_code:
            anchor = (
                str(meta.get("gap_type") or "").strip()
                or str(meta.get("claim_key") or "").strip()
                or str(item.key or "").strip()
            )
            normalized_anchor = anchor.lower().replace(" ", "_").replace(":", "_")
            target_skill_code = f"gap_{normalized_anchor}"[:80] if normalized_anchor else ""
        if not candidate_action:
            candidate_action = "update_existing_skill" if SkillCatalogService().has(target_skill_code) else "draft_candidate_skill"
        summary = str(meta.get("summary") or item.content or "").strip() or "Promote skill gap into candidate procedural skill."
        return {
            "promotion_path_stage": "gap_bridge",
            "candidate_status": "draft",
            "candidate_action": candidate_action,
            "target_skill_code": target_skill_code or None,
            "source_type": "skill_gap",
            "source_gap_id": item.id,
            "source_gap_key": item.key,
            "source_gap_type": meta.get("gap_type"),
            "source_gap_status": meta.get("status"),
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "position_id": meta.get("position_id"),
            "validation_required": True,
            "summary": summary,
            "gap_meta": meta,
        }

    @staticmethod
    def _extract_setup(entry_context: dict) -> str | None:
        decision_context = entry_context.get("decision_context") if isinstance(entry_context.get("decision_context"), dict) else {}
        quant_summary = decision_context.get("quant_summary") if isinstance(decision_context.get("quant_summary"), dict) else {}
        visual_summary = decision_context.get("visual_summary") if isinstance(decision_context.get("visual_summary"), dict) else {}
        signal_payload = entry_context.get("signal_payload") if isinstance(entry_context.get("signal_payload"), dict) else {}
        for value in (
            quant_summary.get("setup"),
            visual_summary.get("setup_type"),
            signal_payload.get("signal_type"),
            entry_context.get("setup"),
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _gap_payload(item: MemoryItem) -> dict:
        meta = dict(item.meta or {})
        return {
            "id": item.id,
            "scope": item.scope,
            "key": item.key,
            "summary": str(meta.get("summary") or item.content or ""),
            "gap_type": meta.get("gap_type") or "unspecified",
            "status": meta.get("status") or "open",
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "position_id": meta.get("position_id"),
            "source_type": meta.get("source_type"),
            "source_trade_review_id": meta.get("source_trade_review_id"),
            "linked_skill_code": meta.get("linked_skill_code"),
            "target_skill_code": meta.get("target_skill_code"),
            "candidate_action": meta.get("candidate_action"),
            "created_at": item.created_at,
            "importance": item.importance,
            "meta": meta,
        }

    @staticmethod
    def _find_existing_candidate(session: Session, *, gap_id: int) -> MemoryItem | None:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_CANDIDATE_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
        )
        for item in session.scalars(statement).all():
            meta = dict(item.meta or {})
            if meta.get("source_gap_id") == gap_id:
                return item
        return None

    @staticmethod
    def _link_gap_to_candidate(
        session: Session,
        *,
        gap: MemoryItem,
        candidate: MemoryItem,
        created: bool,
        journal_entry_id: int | None = None,
    ) -> None:
        meta = dict(gap.meta or {})
        meta["linked_skill_candidate_id"] = candidate.id
        meta["promotion_status"] = "candidate_created" if created else "candidate_already_exists"
        meta["promotion_source"] = "skill_gap_bridge_v1"
        meta["last_promotion_at"] = datetime.now(UTC).isoformat()
        if journal_entry_id is not None:
            meta["promotion_journal_entry_id"] = journal_entry_id
        gap.meta = meta
        session.add(gap)
        session.commit()
        session.refresh(gap)


class ClaimSkillBridgeService:
    MIN_SUPPORT_FOR_GENERIC_PROMOTION = 2

    def __init__(self, catalog_service: SkillCatalogService | None = None) -> None:
        self.catalog_service = catalog_service or SkillCatalogService()
        self.skill_promotion_service = SkillPromotionService(catalog_service=self.catalog_service)

    def build_candidate_meta(self, *, claim, force: bool = False) -> dict | None:
        return self._build_candidate_meta(claim=claim, force=force)

    def maybe_promote_claim(
        self,
        session: Session,
        *,
        claim,
        force: bool = False,
    ) -> dict | None:
        if claim is None:
            return None

        existing_candidate = self._find_existing_candidate(session, claim_id=getattr(claim, "id", None))
        if existing_candidate is not None:
            self._link_claim_to_candidate(session, claim=claim, candidate=existing_candidate, created=False)
            return SkillLifecycleService._candidate_payload(existing_candidate)

        candidate_meta = self._build_candidate_meta(claim=claim, force=force)
        if candidate_meta is None:
            return None

        from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
        from app.domains.learning.services import JournalService, MemoryService

        memory_service = MemoryService()
        journal_service = JournalService()

        candidate_item = memory_service.create_item(
            session,
            MemoryItemCreate(
                memory_type=SKILL_CANDIDATE_MEMORY_TYPE,
                scope=str(candidate_meta.get("scope") or f"strategy:{getattr(claim, 'strategy_version_id', None) or 'unknown'}"),
                key=str(candidate_meta.get("key") or f"skill_candidate:claim:{getattr(claim, 'id', 'unknown')}"),
                content=str(candidate_meta.get("summary") or getattr(claim, "claim_text", "") or "Promote claim into candidate skill."),
                meta=candidate_meta,
                importance=float(candidate_meta.get("importance") or 0.72),
            ),
        )

        journal_entry = journal_service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="skill_candidate_from_claim",
                ticker=getattr(claim, "linked_ticker", None),
                strategy_version_id=getattr(claim, "strategy_version_id", None),
                observations={
                    "claim_id": getattr(claim, "id", None),
                    "claim_key": getattr(claim, "key", None),
                    "claim_type": getattr(claim, "claim_type", None),
                    "skill_candidate": SkillLifecycleService._json_ready_payload(
                        SkillLifecycleService._candidate_payload(candidate_item)
                    ),
                },
                reasoning=str(getattr(claim, "claim_text", "") or ""),
                decision="promote_claim_to_skill_candidate",
                lessons=str(candidate_meta.get("summary") or ""),
            ),
        )

        self._link_claim_to_candidate(session, claim=claim, candidate=candidate_item, created=True, journal_entry_id=journal_entry.id)
        payload = SkillLifecycleService._candidate_payload(candidate_item)
        payload["journal_entry_id"] = journal_entry.id
        return payload

    def _build_candidate_meta(self, *, claim, force: bool) -> dict | None:
        claim_id = getattr(claim, "id", None)
        status = str(getattr(claim, "status", "") or "").strip().lower()
        if not force and status not in {"supported", "validated"}:
            return None
        if not force and str(getattr(claim, "freshness_state", "") or "").strip().lower() == "stale":
            return None

        meta = dict(getattr(claim, "meta", None) or {})
        claim_type = str(getattr(claim, "claim_type", "") or "").strip().lower()
        support_count = int(getattr(claim, "support_count", 0) or 0)
        evidence_count = int(getattr(claim, "evidence_count", 0) or 0)

        if claim_type == "review_improvement":
            if not force and meta.get("source") == "trade_review":
                return None
            if not force and support_count < self.MIN_SUPPORT_FOR_GENERIC_PROMOTION and status != "validated":
                return None
            target_skill_code = str(meta.get("target_skill_code") or "").strip()
            if not target_skill_code:
                fallback_anchor = str(meta.get("failure_mode") or meta.get("cause_category") or getattr(claim, "key", "") or "")
                target_skill_code = f"claim_{fallback_anchor.strip().lower().replace(' ', '_')}" if fallback_anchor.strip() else ""
            candidate_action = "update_existing_skill" if self.catalog_service.has(target_skill_code) else "draft_candidate_skill"
            summary = (
                str(getattr(claim, "claim_text", "") or "").strip()
                or str(meta.get("proposed_strategy_change") or "").strip()
                or "Promote reviewed claim into candidate procedural skill."
            )
            return {
                "promotion_path_stage": "claim_bridge",
                "candidate_status": "draft",
                "candidate_action": candidate_action,
                "target_skill_code": target_skill_code or None,
                "source_type": "knowledge_claim",
                "source_claim_id": claim_id,
                "source_claim_key": getattr(claim, "key", None),
                "source_claim_type": getattr(claim, "claim_type", None),
                "source_claim_status": getattr(claim, "status", None),
                "source_claim_confidence": getattr(claim, "confidence", None),
                "ticker": getattr(claim, "linked_ticker", None),
                "strategy_version_id": getattr(claim, "strategy_version_id", None),
                "summary": summary,
                "validation_required": True,
                "evidence_count": evidence_count,
                "support_count": support_count,
                "claim_meta": meta,
            }

        if claim_type == "context_rule":
            promotion_trace = self._promotion_trace_from_claim(claim)
            suggested_skill_codes = [
                str(item).strip()
                for item in (promotion_trace.get("suggested_skill_codes") if isinstance(promotion_trace, dict) else [])
                if str(item).strip()
            ]
            if not suggested_skill_codes:
                return None
            target_skill_code = suggested_skill_codes[0]
            summary = (
                str(getattr(claim, "claim_text", "") or "").strip()
                or "Promote durable context rule into a candidate skill revision."
            )
            return {
                "promotion_path_stage": "claim_bridge",
                "candidate_status": "draft",
                "candidate_action": "update_existing_skill",
                "target_skill_code": target_skill_code,
                "source_type": "knowledge_claim",
                "source_claim_id": claim_id,
                "source_claim_key": getattr(claim, "key", None),
                "source_claim_type": getattr(claim, "claim_type", None),
                "source_claim_status": getattr(claim, "status", None),
                "source_claim_confidence": getattr(claim, "confidence", None),
                "ticker": getattr(claim, "linked_ticker", None),
                "strategy_version_id": getattr(claim, "strategy_version_id", None),
                "summary": summary,
                "validation_required": True,
                "evidence_count": evidence_count,
                "support_count": support_count,
                "promotion_trace": promotion_trace,
                "claim_meta": meta,
            }

        return None

    def _promotion_trace_from_claim(self, claim) -> dict:
        meta = dict(getattr(claim, "meta", None) or {})
        if isinstance(meta.get("promotion_trace"), dict):
            return dict(meta["promotion_trace"])
        feature_scope = str(meta.get("feature_scope") or "").strip()
        feature_key = str(meta.get("feature_key") or "").strip()
        feature_value = str(meta.get("feature_value") or "").strip()
        if feature_scope and feature_key:
            return self.skill_promotion_service.build_temporary_rule_trace(
                feature_scope=feature_scope,
                feature_key=feature_key,
                feature_value=feature_value,
            )
        return {}

    @staticmethod
    def _find_existing_candidate(session: Session, *, claim_id: int | None) -> MemoryItem | None:
        if claim_id is None:
            return None
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_CANDIDATE_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
        )
        for item in session.scalars(statement).all():
            meta = dict(item.meta or {})
            if meta.get("source_claim_id") == claim_id:
                return item
        return None

    @staticmethod
    def _link_claim_to_candidate(
        session: Session,
        *,
        claim,
        candidate: MemoryItem,
        created: bool,
        journal_entry_id: int | None = None,
    ) -> None:
        meta = dict(getattr(claim, "meta", None) or {})
        meta["linked_skill_candidate_id"] = candidate.id
        meta["promotion_status"] = "candidate_created" if created else "candidate_already_exists"
        meta["promotion_source"] = "claim_bridge_v1"
        meta["last_promotion_at"] = datetime.now(UTC).isoformat()
        if journal_entry_id is not None:
            meta["promotion_journal_entry_id"] = journal_entry_id
        claim.meta = meta
        session.add(claim)
        session.commit()
        session.refresh(claim)


class SkillWorkshopService:
    WORKFLOW_ARTIFACT_TARGETS: dict[str, dict[str, str]] = {
        "premarket_review_completion": {
            "proposal_type": "workflow_review",
            "target_skill_code": "detect_risk_off_conditions",
            "candidate_action": "update_existing_skill",
        },
        "postmarket_review_completion": {
            "proposal_type": "workflow_review",
            "target_skill_code": "do_trade_post_mortem",
            "candidate_action": "update_existing_skill",
        },
        "regime_shift_review_completion": {
            "proposal_type": "workflow_review",
            "target_skill_code": "detect_risk_off_conditions",
            "candidate_action": "update_existing_skill",
        },
    }

    def __init__(self, catalog_service: SkillCatalogService | None = None) -> None:
        self.catalog_service = catalog_service or SkillCatalogService()
        self.claim_bridge_service = ClaimSkillBridgeService(catalog_service=self.catalog_service)
        self.skill_gap_service = SkillGapService()

    def list_proposals(
        self,
        session: Session,
        *,
        limit: int = 50,
        include_resolved: bool = False,
    ) -> list[dict]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_PROPOSAL_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
            .limit(max(1, min(int(limit), 200)))
        )
        payloads = [self._proposal_payload(item) for item in session.scalars(statement).all()]
        if include_resolved:
            return payloads
        return [
            item
            for item in payloads
            if str(item.get("proposal_status") or "").strip().lower() == "pending"
        ]

    def get_proposal(self, session: Session, *, proposal_id: int) -> dict | None:
        item = session.get(MemoryItem, proposal_id)
        if item is None or item.memory_type != SKILL_PROPOSAL_MEMORY_TYPE:
            return None
        return self._proposal_payload(item)

    def sync_proposals(self, session: Session, *, limit_per_source: int = 40) -> list[dict]:
        rows: list[dict] = []
        rows.extend(self._claim_rows(session, limit=limit_per_source))
        rows.extend(self._gap_rows(session, limit=limit_per_source))
        rows.extend(self._cluster_rows(session, limit=limit_per_source))
        rows.extend(self._workflow_rows(session, limit=limit_per_source))
        for row in rows:
            self._upsert_proposal(session, row=row)
        return self.list_proposals(session, limit=max(limit_per_source * 4, 20), include_resolved=False)

    def review_proposal(
        self,
        session: Session,
        *,
        proposal_id: int,
        outcome: str,
        summary: str,
    ) -> dict:
        item = session.get(MemoryItem, proposal_id)
        if item is None or item.memory_type != SKILL_PROPOSAL_MEMORY_TYPE:
            raise ValueError("Skill proposal not found.")

        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"approve", "reject"}:
            raise ValueError("Skill proposal outcome must be approve or reject.")
        summary_text = str(summary or "").strip()
        if not summary_text:
            raise ValueError("Skill proposal review summary is required.")

        from app.domains.learning.schemas import JournalEntryCreate
        from app.domains.learning.services import JournalService

        meta = dict(item.meta or {})
        candidate_payload: dict | None = None
        if normalized_outcome == "approve":
            candidate_payload = self._apply_proposal(session, proposal_item=item)
            if candidate_payload is None:
                raise ValueError("Skill proposal could not be applied under the current bridge policy.")
            meta["proposal_status"] = "applied"
            meta["linked_skill_candidate_id"] = candidate_payload.get("id") if isinstance(candidate_payload, dict) else None
            meta["linked_skill_candidate_key"] = candidate_payload.get("key") if isinstance(candidate_payload, dict) else None
            meta["last_applied_at"] = datetime.now(UTC).isoformat()
        else:
            meta["proposal_status"] = "rejected"
        meta["review_outcome"] = normalized_outcome
        meta["review_summary"] = summary_text
        meta["reviewed_at"] = datetime.now(UTC).isoformat()
        item.meta = meta
        session.add(item)
        session.commit()
        session.refresh(item)

        proposal_payload = self._proposal_payload(item)
        JournalService().create_entry(
            session,
            JournalEntryCreate(
                entry_type="skill_proposal_applied" if normalized_outcome == "approve" else "skill_proposal_rejected",
                ticker=proposal_payload.get("ticker"),
                strategy_version_id=proposal_payload.get("strategy_version_id"),
                observations={
                    "skill_proposal": SkillLifecycleService._json_ready_payload(proposal_payload),
                    "skill_candidate": SkillLifecycleService._json_ready_payload(candidate_payload),
                },
                reasoning=summary_text,
                decision=f"{normalized_outcome}_skill_proposal",
                lessons=summary_text,
            ),
        )
        return {
            "proposal": proposal_payload,
            "candidate": candidate_payload,
        }

    def _claim_rows(self, session: Session, *, limit: int) -> list[dict]:
        rows: list[dict] = []
        statement = (
            select(KnowledgeClaim)
            .order_by(desc(KnowledgeClaim.updated_at), desc(KnowledgeClaim.id))
            .limit(max(1, min(int(limit), 200)))
        )
        for claim in session.scalars(statement).all():
            claim_meta = dict(claim.meta or {})
            if claim_meta.get("linked_skill_candidate_id") is not None:
                continue
            candidate_meta = self.claim_bridge_service.build_candidate_meta(claim=claim, force=False)
            if not isinstance(candidate_meta, dict):
                continue
            rows.append(
                {
                    "scope": str(claim.scope or f"strategy:{claim.strategy_version_id or 'unknown'}"),
                    "key": f"skill_proposal:claim:{claim.id}"[:160],
                    "summary": str(candidate_meta.get("summary") or claim.claim_text or "").strip(),
                    "importance": min(max(float(getattr(claim, "confidence", 0.7) or 0.7), 0.55), 0.95),
                    "meta": {
                        "proposal_type": "claim_bridge",
                        "proposal_status": "pending",
                        "source_type": "knowledge_claim",
                        "source_claim_id": claim.id,
                        "source_claim_key": claim.key,
                        "target_skill_code": candidate_meta.get("target_skill_code"),
                        "candidate_action": candidate_meta.get("candidate_action"),
                        "ticker": claim.linked_ticker,
                        "strategy_version_id": claim.strategy_version_id,
                        "proposed_candidate_meta": candidate_meta,
                    },
                }
            )
        return rows

    def _gap_rows(self, session: Session, *, limit: int) -> list[dict]:
        rows: list[dict] = []
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_GAP_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
            .limit(max(1, min(int(limit), 200)))
        )
        for item in session.scalars(statement).all():
            gap_meta = dict(item.meta or {})
            if str(gap_meta.get("status") or "open").strip().lower() != "open":
                continue
            if gap_meta.get("linked_skill_candidate_id") is not None:
                continue
            candidate_meta = self.skill_gap_service.build_candidate_meta(item)
            rows.append(
                {
                    "scope": str(item.scope or f"strategy:{gap_meta.get('strategy_version_id') or 'unknown'}"),
                    "key": f"skill_proposal:gap:{item.id}"[:160],
                    "summary": str(candidate_meta.get("summary") or item.content or "").strip(),
                    "importance": min(max(float(item.importance or 0.72), 0.55), 0.95),
                    "meta": {
                        "proposal_type": "gap_bridge",
                        "proposal_status": "pending",
                        "source_type": "skill_gap",
                        "source_gap_id": item.id,
                        "target_skill_code": candidate_meta.get("target_skill_code"),
                        "candidate_action": candidate_meta.get("candidate_action"),
                        "ticker": gap_meta.get("ticker"),
                        "strategy_version_id": gap_meta.get("strategy_version_id"),
                        "proposed_candidate_meta": candidate_meta,
                    },
                }
            )
        return rows

    def _cluster_rows(self, session: Session, *, limit: int) -> list[dict]:
        rows: list[dict] = []
        clusters = OperatorDisagreementService().sync_clusters(session, limit=limit, min_count=2)
        for cluster in clusters:
            if str(cluster.get("status") or "").strip().lower() != "open":
                continue
            if cluster.get("promoted_skill_gap_id") is not None:
                continue
            target_skill_code = str(cluster.get("target_skill_code") or "").strip()
            if not target_skill_code:
                anchor = (
                    str(cluster.get("claim_key") or "").strip()
                    or str(cluster.get("entity_type") or "").strip()
                    or str(cluster.get("cluster_key") or "").strip()
                )
                normalized_anchor = anchor.lower().replace(" ", "_").replace(":", "_")
                target_skill_code = f"cluster_{normalized_anchor}"[:80] if normalized_anchor else ""
            candidate_action = (
                "update_existing_skill" if self.catalog_service.has(target_skill_code) else "draft_candidate_skill"
            )
            summary = (
                f"Repeated operator disagreement suggests reviewing procedure around "
                f"{target_skill_code or cluster.get('claim_key') or cluster.get('entity_type') or 'this area'}."
            )
            rows.append(
                {
                    "scope": (
                        f"strategy:{cluster.get('strategy_version_id')}"
                        if cluster.get("strategy_version_id") is not None
                        else "operator_feedback"
                    ),
                    "key": f"skill_proposal:operator_disagreement_cluster:{cluster['id']}"[:160],
                    "summary": summary,
                    "importance": min(0.7 + (0.03 * int(cluster.get("event_count") or 0)), 0.95),
                    "meta": {
                        "proposal_type": "operator_disagreement_pattern",
                        "proposal_status": "pending",
                        "source_type": "operator_disagreement_cluster",
                        "source_operator_disagreement_cluster_id": cluster["id"],
                        "target_skill_code": target_skill_code or None,
                        "candidate_action": candidate_action,
                        "ticker": cluster.get("ticker"),
                        "strategy_version_id": cluster.get("strategy_version_id"),
                        "proposed_candidate_meta": {
                            "promotion_path_stage": "skill_workshop",
                            "candidate_status": "draft",
                            "candidate_action": candidate_action,
                            "target_skill_code": target_skill_code or None,
                            "source_type": "operator_disagreement_cluster",
                            "source_operator_disagreement_cluster_id": cluster["id"],
                            "source_cluster_key": cluster.get("cluster_key"),
                            "ticker": cluster.get("ticker"),
                            "strategy_version_id": cluster.get("strategy_version_id"),
                            "validation_required": True,
                            "summary": summary,
                            "cluster_meta": cluster.get("meta"),
                        },
                    },
                }
            )
        return rows

    def _workflow_rows(self, session: Session, *, limit: int) -> list[dict]:
        rows: list[dict] = []
        artifact_types = tuple(self.WORKFLOW_ARTIFACT_TARGETS.keys())
        statement = (
            select(LearningWorkflowArtifact)
            .where(LearningWorkflowArtifact.artifact_type.in_(artifact_types))
            .order_by(desc(LearningWorkflowArtifact.created_at), desc(LearningWorkflowArtifact.id))
            .limit(max(1, min(int(limit), 200)))
        )
        for artifact in session.scalars(statement).all():
            spec = self.WORKFLOW_ARTIFACT_TARGETS.get(str(artifact.artifact_type))
            if spec is None:
                continue
            payload = dict(artifact.payload or {})
            summary = self._workflow_summary(artifact=artifact, payload=payload)
            proposed_candidate_meta = {
                "promotion_path_stage": "skill_workshop",
                "candidate_status": "draft",
                "candidate_action": spec["candidate_action"],
                "target_skill_code": spec["target_skill_code"],
                "source_type": "learning_workflow_artifact",
                "source_workflow_id": artifact.workflow_id,
                "source_workflow_run_id": artifact.workflow_run_id,
                "source_workflow_artifact_id": artifact.id,
                "source_workflow_type": payload.get("workflow_type"),
                "ticker": artifact.ticker,
                "strategy_version_id": artifact.strategy_version_id,
                "validation_required": True,
                "summary": summary,
                "workflow_artifact_payload": payload,
            }
            rows.append(
                {
                    "scope": f"workflow:{payload.get('workflow_type') or artifact.artifact_type}",
                    "key": f"skill_proposal:workflow_artifact:{artifact.id}"[:160],
                    "summary": summary,
                    "importance": min(max(float((artifact.payload or {}).get("importance") or 0.7), 0.55), 0.92),
                    "meta": {
                        "proposal_type": spec["proposal_type"],
                        "proposal_status": "pending",
                        "source_type": "learning_workflow_artifact",
                        "source_workflow_id": artifact.workflow_id,
                        "source_workflow_run_id": artifact.workflow_run_id,
                        "source_workflow_artifact_id": artifact.id,
                        "target_skill_code": spec["target_skill_code"],
                        "candidate_action": spec["candidate_action"],
                        "ticker": artifact.ticker,
                        "strategy_version_id": artifact.strategy_version_id,
                        "proposed_candidate_meta": proposed_candidate_meta,
                    },
                }
            )
        return rows

    def _upsert_proposal(self, session: Session, *, row: dict) -> dict:
        from app.domains.learning.schemas import MemoryItemCreate
        from app.domains.learning.services import MemoryService

        normalized_key = str(row.get("key") or "")[:120]
        meta_payload = dict(row.get("meta") or {})
        meta_payload["summary"] = str(row.get("summary") or "").strip()
        existing = session.scalar(
            select(MemoryItem).where(
                MemoryItem.memory_type == SKILL_PROPOSAL_MEMORY_TYPE,
                MemoryItem.key == normalized_key,
            )
        )
        if existing is None:
            item = MemoryService().create_item(
                session,
                MemoryItemCreate(
                    memory_type=SKILL_PROPOSAL_MEMORY_TYPE,
                    scope=str(row.get("scope") or "workflow:global"),
                    key=normalized_key,
                    content=str(row.get("summary") or "Skill proposal").strip(),
                    meta=self._json_ready_payload(meta_payload),
                    importance=float(row.get("importance") or 0.72),
                ),
            )
            return self._proposal_payload(item)

        current_meta = dict(existing.meta or {})
        merged_meta = dict(meta_payload)
        for key in (
            "proposal_status",
            "review_outcome",
            "review_summary",
            "reviewed_at",
            "linked_skill_candidate_id",
            "linked_skill_candidate_key",
            "last_applied_at",
        ):
            if key in current_meta:
                merged_meta[key] = current_meta[key]
        existing.scope = str(row.get("scope") or existing.scope)
        existing.content = str(row.get("summary") or existing.content).strip()
        existing.meta = self._json_ready_payload(merged_meta)
        existing.importance = float(row.get("importance") or existing.importance or 0.72)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return self._proposal_payload(existing)

    def _apply_proposal(self, session: Session, *, proposal_item: MemoryItem) -> dict | None:
        meta = dict(proposal_item.meta or {})
        source_type = str(meta.get("source_type") or "").strip().lower()
        if source_type == "knowledge_claim":
            from app.domains.learning.claims import KnowledgeClaimService

            claim_id = meta.get("source_claim_id")
            if not isinstance(claim_id, int):
                raise ValueError("Skill proposal claim source is missing.")
            return KnowledgeClaimService().maybe_promote_claim_to_skill_candidate(session, claim_id=claim_id, force=True)
        if source_type == "skill_gap":
            gap_id = meta.get("source_gap_id")
            if not isinstance(gap_id, int):
                raise ValueError("Skill proposal gap source is missing.")
            return self.skill_gap_service.promote_gap_to_candidate(session, gap_id=gap_id)
        candidate_payload = self._create_candidate_from_proposal(session, proposal_item=proposal_item)
        if source_type == "operator_disagreement_cluster":
            cluster_id = meta.get("source_operator_disagreement_cluster_id")
            if isinstance(cluster_id, int):
                cluster_item = session.get(MemoryItem, cluster_id)
                if cluster_item is not None:
                    cluster_meta = dict(cluster_item.meta or {})
                    cluster_meta["linked_skill_candidate_id"] = candidate_payload.get("id")
                    cluster_meta["last_promotion_at"] = datetime.now(UTC).isoformat()
                    cluster_item.meta = cluster_meta
                    session.add(cluster_item)
                    session.commit()
        return candidate_payload

    def _create_candidate_from_proposal(self, session: Session, *, proposal_item: MemoryItem) -> dict:
        from app.domains.learning.schemas import MemoryItemCreate
        from app.domains.learning.services import MemoryService

        proposal_meta = dict(proposal_item.meta or {})
        linked_candidate_id = proposal_meta.get("linked_skill_candidate_id")
        if isinstance(linked_candidate_id, int):
            candidate = session.get(MemoryItem, linked_candidate_id)
            if candidate is not None and candidate.memory_type == SKILL_CANDIDATE_MEMORY_TYPE:
                return SkillLifecycleService._candidate_payload(candidate)

        existing_candidate = self._find_existing_candidate_for_proposal(session, proposal_item=proposal_item)
        if existing_candidate is not None:
            return SkillLifecycleService._candidate_payload(existing_candidate)

        candidate_meta = dict(proposal_meta.get("proposed_candidate_meta") or {})
        if not candidate_meta:
            raise ValueError("Skill proposal does not contain candidate metadata.")
        candidate_meta["source_skill_proposal_id"] = proposal_item.id
        candidate_meta["source_skill_proposal_key"] = proposal_item.key
        candidate_meta["source_skill_proposal_type"] = proposal_meta.get("proposal_type")
        candidate_scope = str(
            candidate_meta.get("scope")
            or proposal_item.scope
            or f"strategy:{candidate_meta.get('strategy_version_id') or 'unknown'}"
        )
        candidate_key = (
            str(candidate_meta.get("key") or f"skill_candidate:proposal:{proposal_item.id}:{candidate_meta.get('target_skill_code') or 'unmapped'}")
            [:160]
        )
        candidate_item = MemoryService().create_item(
            session,
            MemoryItemCreate(
                memory_type=SKILL_CANDIDATE_MEMORY_TYPE,
                scope=candidate_scope,
                key=candidate_key,
                content=str(candidate_meta.get("summary") or proposal_item.content or "Skill candidate from workshop."),
                meta=self._json_ready_payload(candidate_meta),
                importance=min(max(float(proposal_item.importance or 0.72), 0.55), 0.95),
            ),
        )
        return SkillLifecycleService._candidate_payload(candidate_item)

    def _find_existing_candidate_for_proposal(self, session: Session, *, proposal_item: MemoryItem) -> MemoryItem | None:
        proposal_meta = dict(proposal_item.meta or {})
        source_artifact_id = proposal_meta.get("source_workflow_artifact_id")
        source_cluster_id = proposal_meta.get("source_operator_disagreement_cluster_id")
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_CANDIDATE_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
        )
        for item in session.scalars(statement).all():
            meta = dict(item.meta or {})
            if meta.get("source_skill_proposal_id") == proposal_item.id:
                return item
            if source_artifact_id is not None and meta.get("source_workflow_artifact_id") == source_artifact_id:
                return item
            if source_cluster_id is not None and meta.get("source_operator_disagreement_cluster_id") == source_cluster_id:
                return item
        return None

    @staticmethod
    def _workflow_summary(*, artifact: LearningWorkflowArtifact, payload: dict) -> str:
        workflow_type = str(payload.get("workflow_type") or "").strip()
        if artifact.artifact_type == "regime_shift_review_completion":
            previous_regime = str(payload.get("previous_regime") or "").strip() or "unknown"
            current_regime = str(payload.get("current_regime") or "").strip() or "unknown"
            return (
                f"Completed regime shift review {previous_regime} -> {current_regime}; "
                "review whether the risk-off procedure needs an explicit revision."
            )
        if artifact.artifact_type == "postmarket_review_completion":
            review_date = str(payload.get("review_date") or "").strip() or "current session"
            return (
                f"Completed postmarket review for {review_date}; "
                "review whether the post-mortem procedure needs a sharper validated revision."
            )
        if artifact.artifact_type == "premarket_review_completion":
            review_date = str(payload.get("review_date") or "").strip() or "current session"
            return (
                f"Completed premarket review for {review_date}; "
                "review whether the premarket risk posture procedure needs refinement."
            )
        return str(artifact.summary or workflow_type or "Workflow artifact suggests a procedural proposal.").strip()

    @staticmethod
    def _proposal_payload(item: MemoryItem) -> dict:
        meta = dict(item.meta or {})
        return {
            "id": item.id,
            "scope": item.scope,
            "key": item.key,
            "summary": str(meta.get("summary") or item.content or ""),
            "proposal_type": meta.get("proposal_type") or "skill_workshop",
            "proposal_status": meta.get("proposal_status") or "pending",
            "target_skill_code": meta.get("target_skill_code"),
            "candidate_action": meta.get("candidate_action"),
            "source_type": meta.get("source_type"),
            "source_claim_id": meta.get("source_claim_id"),
            "source_gap_id": meta.get("source_gap_id"),
            "source_workflow_id": meta.get("source_workflow_id"),
            "source_workflow_run_id": meta.get("source_workflow_run_id"),
            "source_workflow_artifact_id": meta.get("source_workflow_artifact_id"),
            "source_operator_disagreement_cluster_id": meta.get("source_operator_disagreement_cluster_id"),
            "linked_skill_candidate_id": meta.get("linked_skill_candidate_id"),
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "created_at": item.created_at,
            "importance": item.importance,
            "meta": meta,
        }

    @staticmethod
    def _json_ready_payload(payload: dict) -> dict:
        return SkillLifecycleService._json_ready_payload(payload) or {}


class SkillLifecycleService:
    def __init__(self, catalog_service: SkillCatalogService | None = None) -> None:
        self.catalog_service = catalog_service or SkillCatalogService()

    def list_catalog(self, session: Session | None = None) -> list[dict]:
        catalog = [dict(item) for item in self.catalog_service.list_skills()]
        if session is None:
            for item in catalog:
                item["active_revision"] = None
                item["has_active_revision"] = False
            return catalog

        active_revisions = self._active_revision_map(session)
        for item in catalog:
            revision = active_revisions.get(item["code"])
            item["active_revision"] = revision
            item["has_active_revision"] = revision is not None
        return catalog

    def list_candidates(self, session: Session) -> list[dict]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == SKILL_CANDIDATE_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
        )
        return [self._candidate_payload(item) for item in session.scalars(statement).all()]

    def get_candidate(self, session: Session, *, candidate_id: int) -> dict | None:
        item = session.get(MemoryItem, candidate_id)
        if item is None or item.memory_type != SKILL_CANDIDATE_MEMORY_TYPE:
            return None
        return self._candidate_payload(item)

    def find_candidate_by_source_claim_id(self, session: Session, *, claim_id: int) -> dict | None:
        for item in self.list_candidates(session):
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            if meta.get("source_claim_id") == claim_id:
                return item
        return None

    def list_revisions(self, session: Session, *, include_inactive: bool = False) -> list[dict]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == VALIDATED_SKILL_REVISION_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
        )
        items = list(session.scalars(statement).all())
        payloads = [self._revision_payload(item) for item in items]
        if include_inactive:
            return payloads
        return [item for item in payloads if item["activation_status"] == "active"]

    def get_revision(self, session: Session, *, revision_id: int) -> dict | None:
        item = session.get(MemoryItem, revision_id)
        if item is None or item.memory_type != VALIDATED_SKILL_REVISION_MEMORY_TYPE:
            return None
        return self._revision_payload(item)

    def get_validation_record(self, session: Session, *, validation_record_id: int) -> dict | None:
        item = session.get(SkillValidationRecord, validation_record_id)
        return self._validation_record_payload(item, session=session)

    def list_validation_records(
        self,
        session: Session,
        *,
        candidate_id: int | None = None,
        revision_id: int | None = None,
        skill_code: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        statement = select(SkillValidationRecord).order_by(desc(SkillValidationRecord.created_at), desc(SkillValidationRecord.id))
        if candidate_id is not None:
            statement = statement.where(SkillValidationRecord.candidate_id == candidate_id)
        if revision_id is not None:
            statement = statement.where(SkillValidationRecord.revision_id == revision_id)
        items = list(session.scalars(statement).all())
        normalized_skill_code = str(skill_code or "").strip()
        payloads: list[dict] = []
        candidate_cache: dict[int, MemoryItem | None] = {}
        revision_cache: dict[int, MemoryItem | None] = {}
        for item in items:
            payload = self._validation_record_payload(
                item,
                session=session,
                candidate_cache=candidate_cache,
                revision_cache=revision_cache,
            )
            if payload is None:
                continue
            if normalized_skill_code and str(payload.get("skill_code") or "").strip() != normalized_skill_code:
                continue
            payloads.append(payload)
            if len(payloads) >= max(1, int(limit or 1)):
                break
        return payloads

    def summarize_validation_records(
        self,
        session: Session,
        *,
        candidate_id: int | None = None,
        revision_id: int | None = None,
        skill_code: str | None = None,
        limit: int = 50,
    ) -> dict:
        records = self.list_validation_records(
            session,
            candidate_id=candidate_id,
            revision_id=revision_id,
            skill_code=skill_code,
            limit=limit,
        )
        normalized_skill_code = str(skill_code or "").strip()
        if candidate_id is not None:
            scope_type = "candidate"
            scope_value = str(candidate_id)
        elif revision_id is not None:
            scope_type = "revision"
            scope_value = str(revision_id)
        else:
            scope_type = "skill"
            scope_value = normalized_skill_code or "all"

        latest = records[0] if records else {}
        previous = records[1] if len(records) > 1 else {}
        approved_count = sum(1 for item in records if str(item.get("validation_outcome") or "").strip().lower() == "approved")
        rejected_count = sum(1 for item in records if str(item.get("validation_outcome") or "").strip().lower() == "rejected")

        def values_for(field: str) -> list[float]:
            values: list[float] = []
            for item in records:
                value = item.get(field)
                if isinstance(value, (int, float)):
                    values.append(float(value))
            return values

        def metric_delta(field: str) -> dict:
            current = latest.get(field) if isinstance(latest.get(field), (int, float)) else None
            previous_value = previous.get(field) if isinstance(previous.get(field), (int, float)) else None
            delta = None
            if current is not None and previous_value is not None:
                delta = float(current) - float(previous_value)
            return {
                "current": float(current) if current is not None else None,
                "previous": float(previous_value) if previous_value is not None else None,
                "delta": delta,
            }

        win_rates = values_for("win_rate")
        avg_pnls = values_for("avg_pnl_pct")
        drawdowns = values_for("max_drawdown_pct")

        return {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "record_count": len(records),
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "latest_validation_id": latest.get("id"),
            "previous_validation_id": previous.get("id"),
            "latest_run_id": latest.get("run_id"),
            "avg_win_rate": round(sum(win_rates) / len(win_rates), 4) if win_rates else None,
            "avg_avg_pnl_pct": round(sum(avg_pnls) / len(avg_pnls), 4) if avg_pnls else None,
            "avg_max_drawdown_pct": round(sum(drawdowns) / len(drawdowns), 4) if drawdowns else None,
            "best_win_rate": max(win_rates) if win_rates else None,
            "best_avg_pnl_pct": max(avg_pnls) if avg_pnls else None,
            "worst_max_drawdown_pct": min(drawdowns) if drawdowns else None,
            "win_rate_delta": metric_delta("win_rate"),
            "avg_pnl_pct_delta": metric_delta("avg_pnl_pct"),
            "max_drawdown_pct_delta": metric_delta("max_drawdown_pct"),
        }

    def compare_validation_records(
        self,
        session: Session,
        *,
        candidate_id: int | None = None,
        revision_id: int | None = None,
        skill_code: str | None = None,
        baseline_validation_id: int | None = None,
        limit: int = 8,
    ) -> dict:
        records = self.list_validation_records(
            session,
            candidate_id=candidate_id,
            revision_id=revision_id,
            skill_code=skill_code,
            limit=limit,
        )
        normalized_skill_code = str(skill_code or "").strip()
        if candidate_id is not None:
            scope_type = "candidate"
            scope_value = str(candidate_id)
        elif revision_id is not None:
            scope_type = "revision"
            scope_value = str(revision_id)
        else:
            scope_type = "skill"
            scope_value = normalized_skill_code or "all"

        baseline = records[0] if records else {}
        custom_baseline_applied = False
        if baseline_validation_id is not None:
            selected = next((item for item in records if item.get("id") == baseline_validation_id), None)
            if selected is not None:
                baseline = selected
                custom_baseline_applied = True

        def numeric_delta(item: dict, field: str) -> float | None:
            current = item.get(field)
            base = baseline.get(field)
            if not isinstance(current, (int, float)) or not isinstance(base, (int, float)):
                return None
            return float(current) - float(base)

        rows: list[dict] = []
        baseline_id = baseline.get("id")
        ordered_records = []
        if baseline:
            ordered_records.append(baseline)
        ordered_records.extend(item for item in records if item.get("id") != baseline_id)
        for item in ordered_records:
            rows.append(
                {
                    "validation_id": item.get("id"),
                    "created_at": item.get("created_at"),
                    "validation_mode": item.get("validation_mode"),
                    "validation_outcome": item.get("validation_outcome"),
                    "run_id": item.get("run_id"),
                    "sample_size": item.get("sample_size"),
                    "win_rate": item.get("win_rate"),
                    "avg_pnl_pct": item.get("avg_pnl_pct"),
                    "max_drawdown_pct": item.get("max_drawdown_pct"),
                    "win_rate_delta_vs_base": numeric_delta(item, "win_rate"),
                    "avg_pnl_pct_delta_vs_base": numeric_delta(item, "avg_pnl_pct"),
                    "max_drawdown_pct_delta_vs_base": numeric_delta(item, "max_drawdown_pct"),
                    "is_base": item.get("id") == baseline_id,
                }
            )

        return {
            "scope_type": scope_type,
            "scope_value": scope_value,
            "baseline_validation_id": baseline.get("id"),
            "baseline_run_id": baseline.get("run_id"),
            "custom_baseline_applied": custom_baseline_applied,
            "row_count": len(rows),
            "rows": rows,
        }

    def build_dashboard(self, session: Session) -> dict:
        from app.domains.learning.services import LearningMemoryDistillationService

        return {
            "catalog": self.list_catalog(session),
            "proposals": SkillWorkshopService(catalog_service=self.catalog_service).list_proposals(
                session,
                limit=20,
                include_resolved=False,
            ),
            "candidates": self.list_candidates(session),
            "active_revisions": self.list_revisions(session, include_inactive=False),
            "gaps": SkillGapService().list_gaps(session, limit=20),
            "distillations": LearningMemoryDistillationService().list_digests(
                session,
                limit=12,
                include_reviewed=True,
            ),
        }

    def attach_runtime_state(self, session: Session, skill_context: dict | None) -> dict:
        context = deepcopy(skill_context) if isinstance(skill_context, dict) else {}
        if not context:
            return {}

        active_revisions = self._active_revision_map(session)

        def annotate(entries: object) -> list[dict]:
            annotated: list[dict] = []
            if not isinstance(entries, list):
                return annotated
            for item in entries:
                if not isinstance(item, dict):
                    continue
                payload = dict(item)
                code = str(payload.get("code") or "").strip()
                revision = active_revisions.get(code)
                payload["active_revision"] = revision
                payload["has_active_revision"] = revision is not None
                annotated.append(payload)
            return annotated

        context["considered_skills"] = annotate(context.get("considered_skills"))
        context["applied_skills"] = annotate(context.get("applied_skills"))
        relevant_revisions = [
            dict(item["active_revision"])
            for item in context["applied_skills"]
            if isinstance(item.get("active_revision"), dict)
        ]
        context["active_revisions"] = relevant_revisions
        context["active_revision_count"] = len(relevant_revisions)
        context["runtime_instruction_fragments"] = [
            revision["revision_summary"]
            for revision in relevant_revisions
            if isinstance(revision.get("revision_summary"), str) and revision["revision_summary"].strip()
        ]
        if relevant_revisions:
            labels = ", ".join(revision["skill_code"] for revision in relevant_revisions)
            context["summary"] = f'{context.get("summary") or "Skill routing completed."} Active validated revisions: {labels}.'
        return context

    def build_runtime_selection(
        self,
        session: Session,
        *,
        skill_context: dict | None,
        max_packets: int = 3,
        max_steps_per_packet: int = 4,
    ) -> dict:
        context = self.attach_runtime_state(session, skill_context)
        packet_limit = max(0, int(max_packets or 0))
        step_limit = max(2, int(max_steps_per_packet or 2))
        candidates = self._runtime_candidates_from_context(context)
        packets: list[dict] = []

        if context and packet_limit > 0:
            for item in candidates[:packet_limit]:
                packet = self._build_runtime_packet(
                    definition=item["definition"],
                    applied_skill=item["applied_skill"],
                    active_revision=item["active_revision"],
                    phase=str(context.get("phase") or item["applied_skill"].get("phase") or "do"),
                    max_steps=step_limit,
                )
                packets.append(packet.to_payload())

        available_codes = [str(item["definition"].code) for item in candidates]
        loaded_codes = [str(item.get("skill_code") or "").strip() for item in packets if str(item.get("skill_code") or "").strip()]
        skipped_codes = [code for code in available_codes if code not in set(loaded_codes)]

        return {
            "packets": packets,
            "budget": {
                "enabled": bool(context) and packet_limit > 0,
                "available_count": len(available_codes),
                "loaded_count": len(loaded_codes),
                "truncated_count": max(len(available_codes) - len(loaded_codes), 0),
                "max_packets": packet_limit,
                "max_steps_per_packet": step_limit,
                "loaded_codes": loaded_codes,
                "skipped_codes": skipped_codes[:5],
                "loaded_step_count": sum(
                    len([step for step in item.get("procedure_steps", []) if isinstance(step, str) and step.strip()])
                    for item in packets
                    if isinstance(item, dict)
                ),
            },
        }

    def build_runtime_packets(
        self,
        session: Session,
        *,
        skill_context: dict | None,
        max_packets: int = 3,
        max_steps_per_packet: int = 4,
    ) -> list[dict]:
        selection = self.build_runtime_selection(
            session,
            skill_context=skill_context,
            max_packets=max_packets,
            max_steps_per_packet=max_steps_per_packet,
        )
        return [item for item in selection.get("packets", []) if isinstance(item, dict)]

    def _runtime_candidates_from_context(self, context: dict | None) -> list[dict]:
        if not context:
            return []
        applied_skills = context.get("applied_skills") if isinstance(context.get("applied_skills"), list) else []
        candidates: list[dict] = []
        seen_codes: set[str] = set()
        for item in applied_skills:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            if not code or code in seen_codes:
                continue
            definition = self.catalog_service.get(code)
            if definition is None:
                continue
            candidates.append(
                {
                    "definition": definition,
                    "applied_skill": item,
                    "active_revision": item.get("active_revision") if isinstance(item.get("active_revision"), dict) else None,
                }
            )
            seen_codes.add(code)
        return candidates

    @staticmethod
    def render_runtime_skill_prompt(packets: list[dict] | None) -> str:
        if not isinstance(packets, list) or not packets:
            return ""

        lines = [
            "Relevant runtime skills are supplied below. Treat them as conditional procedural guidance.",
            "Hard risk, regime and event-risk constraints still dominate; do not use a skill to override a block.",
            "Load only these skills into the current decision and ignore any unstated procedure.",
        ]
        for index, item in enumerate(packets, start=1):
            if not isinstance(item, dict):
                continue
            code = str(item.get("skill_code") or "").strip()
            if not code:
                continue
            lines.append(f"{index}. Skill `{code}`")
            objective = str(item.get("objective") or "").strip()
            selection_reason = str(item.get("selection_reason") or "").strip()
            if objective:
                lines.append(f"   Objective: {objective}")
            if selection_reason:
                lines.append(f"   Why now: {selection_reason}")
            use_when = [str(part).strip() for part in item.get("use_when", []) if str(part).strip()]
            if use_when:
                lines.append(f"   Use when: {'; '.join(use_when[:3])}")
            avoid_when = [str(part).strip() for part in item.get("avoid_when", []) if str(part).strip()]
            if avoid_when:
                lines.append(f"   Avoid when: {'; '.join(avoid_when[:3])}")
            required_context = [str(part).strip() for part in item.get("required_context", []) if str(part).strip()]
            if required_context:
                lines.append(f"   Required context: {', '.join(required_context[:4])}")
            procedure_steps = [str(part).strip() for part in item.get("procedure_steps", []) if str(part).strip()]
            if procedure_steps:
                lines.append(f"   Procedure: {' | '.join(procedure_steps[:4])}")
            revision_summary = str(item.get("validated_revision_summary") or "").strip()
            if revision_summary:
                lines.append(f"   Validated revision: {revision_summary}")
        return "\n".join(lines)

    def validate_candidate(
        self,
        session: Session,
        *,
        candidate_id: int,
        validation_mode: str,
        validation_outcome: str,
        summary: str | None = None,
        sample_size: int | None = None,
        win_rate: float | None = None,
        avg_pnl_pct: float | None = None,
        max_drawdown_pct: float | None = None,
        evidence: dict | None = None,
        activate: bool = True,
    ) -> dict:
        candidate = session.get(MemoryItem, candidate_id)
        if candidate is None or candidate.memory_type != SKILL_CANDIDATE_MEMORY_TYPE:
            raise ValueError("Skill candidate not found.")

        now = datetime.now(UTC)
        candidate_meta = dict(candidate.meta or {})
        target_skill_code = str(candidate_meta.get("target_skill_code") or "").strip()
        summary_text = (
            str(summary or "").strip()
            or str(candidate_meta.get("summary") or "").strip()
            or candidate.content
            or "Validated procedural improvement candidate."
        )
        known_skill = self.catalog_service.has(target_skill_code)
        normalized_outcome = "approved" if str(validation_outcome).strip().lower() == "approve" else "rejected"
        activation_status = "rejected"
        revision_item: MemoryItem | None = None
        validation_record: SkillValidationRecord | None = None
        evidence_payload = dict(evidence or {})
        run_id = str(evidence_payload.get("run_id") or "").strip() or None
        artifact_url = str(evidence_payload.get("artifact_url") or "").strip() or None
        evidence_note = str(evidence_payload.get("note") or "").strip() or None

        if normalized_outcome == "approved":
            if activate and known_skill:
                activation_status = "active"
            elif activate and not known_skill:
                activation_status = "pending_catalog_integration"
            else:
                activation_status = "validated_not_activated"

            revision_meta = {
                "skill_code": target_skill_code or None,
                "candidate_id": candidate.id,
                "candidate_key": candidate.key,
                "activation_status": activation_status,
                "validation_mode": validation_mode,
                "validation_outcome": normalized_outcome,
                "revision_summary": summary_text,
                "sample_size": sample_size,
                "win_rate": win_rate,
                "avg_pnl_pct": avg_pnl_pct,
                "max_drawdown_pct": max_drawdown_pct,
                "evidence": evidence_payload,
                "validated_at": now.isoformat(),
                "source_trade_review_id": candidate_meta.get("source_trade_review_id"),
                "position_id": candidate_meta.get("position_id"),
                "ticker": candidate_meta.get("ticker"),
                "strategy_version_id": candidate_meta.get("strategy_version_id"),
                "validation_gate": "paper_or_replay_v1",
                "known_catalog_skill": known_skill,
            }
            revision_item = MemoryItem(
                memory_type=VALIDATED_SKILL_REVISION_MEMORY_TYPE,
                scope=f"skill:{target_skill_code or 'unmapped'}",
                key=f"skill_revision:{candidate.id}:{int(now.timestamp())}",
                content=summary_text,
                meta=revision_meta,
                importance=0.85,
                valid_from=now,
            )
            session.add(revision_item)
            session.flush()

        validation_record = SkillValidationRecord(
            candidate_id=candidate.id,
            revision_id=revision_item.id if revision_item is not None else None,
            validation_mode=validation_mode,
            validation_outcome=normalized_outcome,
            summary=summary_text,
            run_id=run_id,
            artifact_url=artifact_url,
            evidence_note=evidence_note,
            sample_size=sample_size,
            win_rate=win_rate,
            avg_pnl_pct=avg_pnl_pct,
            max_drawdown_pct=max_drawdown_pct,
            evidence_payload=evidence_payload,
        )
        session.add(validation_record)
        session.flush()

        if revision_item is not None:
            revision_meta = dict(revision_item.meta or {})
            revision_meta["validation_record_id"] = validation_record.id
            revision_item.meta = revision_meta
            session.add(revision_item)
            if activation_status == "active" and target_skill_code:
                self._supersede_previous_revisions(
                    session,
                    target_skill_code=target_skill_code,
                    keep_revision_id=revision_item.id,
                    superseded_at=now.isoformat(),
                )

        candidate_meta.update(
            {
                "candidate_status": "validated" if normalized_outcome == "approved" else "rejected",
                "validation_mode": validation_mode,
                "validation_outcome": normalized_outcome,
                "last_validation_at": now.isoformat(),
                "last_validation_summary": summary_text,
                "activation_status": activation_status,
                "last_validation_sample_size": sample_size,
                "last_validation_win_rate": win_rate,
                "last_validation_avg_pnl_pct": avg_pnl_pct,
                "last_validation_max_drawdown_pct": max_drawdown_pct,
                "last_validation_evidence": evidence_payload,
                "latest_validation_record_id": validation_record.id if validation_record is not None else None,
                "active_revision_id": revision_item.id if revision_item is not None else None,
            }
        )
        candidate.meta = candidate_meta
        session.add(candidate)

        journal_entry = JournalEntry(
            entry_type="skill_candidate_validated" if normalized_outcome == "approved" else "skill_candidate_rejected",
            ticker=candidate_meta.get("ticker"),
            strategy_version_id=candidate_meta.get("strategy_version_id"),
            position_id=candidate_meta.get("position_id"),
            market_context={
                "validation_gate": "paper_or_replay_v1",
                "validation_mode": validation_mode,
                "activation_status": activation_status,
            },
            observations={
                "skill_candidate": self._json_ready_payload(self._candidate_payload(candidate)),
                "skill_revision": self._json_ready_payload(self._revision_payload(revision_item)) if revision_item is not None else None,
                "skill_validation_record": (
                    self._json_ready_payload(self._validation_record_payload(validation_record))
                    if validation_record is not None
                    else None
                ),
            },
            reasoning=summary_text,
            decision="activate_skill_revision" if activation_status == "active" else normalized_outcome,
            lessons=summary_text,
        )
        session.add(journal_entry)
        session.commit()
        session.refresh(candidate)
        session.refresh(journal_entry)
        if revision_item is not None:
            session.refresh(revision_item)
        if normalized_outcome == "rejected":
            OperatorDisagreementService().record(
                session,
                disagreement_type="skill_candidate_rejected",
                entity_type="skill_candidate",
                entity_id=candidate.id,
                action="reject",
                summary=summary_text,
                ticker=candidate_meta.get("ticker"),
                strategy_version_id=candidate_meta.get("strategy_version_id"),
                position_id=candidate_meta.get("position_id"),
                source="skill_candidate_validation",
                details={
                    "target_skill_code": target_skill_code or None,
                    "validation_mode": validation_mode,
                    "validation_record_id": validation_record.id if validation_record is not None else None,
                    "candidate_status": candidate_meta.get("candidate_status"),
                    "activation_status": activation_status,
                },
            )

        return {
            "candidate": self._candidate_payload(candidate),
            "revision": self._revision_payload(revision_item) if revision_item is not None else None,
            "validation_record": self._validation_record_payload(validation_record),
            "journal_entry_id": journal_entry.id,
            "activation_status": activation_status,
        }

    def _active_revision_map(self, session: Session) -> dict[str, dict]:
        revisions = self.list_revisions(session, include_inactive=False)
        revision_map: dict[str, dict] = {}
        for item in revisions:
            code = str(item.get("skill_code") or "").strip()
            if code and code not in revision_map:
                revision_map[code] = item
        return revision_map

    def _build_runtime_packet(
        self,
        *,
        definition: SkillDefinition,
        applied_skill: dict,
        active_revision: dict | None,
        phase: str,
        max_steps: int,
    ) -> SkillRuntimePacket:
        revision_summary = (
            str(active_revision.get("revision_summary") or "").strip()
            if isinstance(active_revision, dict)
            else ""
        )
        instruction_source = "catalog_plus_active_revision" if revision_summary else "catalog_baseline"
        selection_reason = str(applied_skill.get("reason") or definition.description or definition.objective).strip()
        procedure_steps = [
            "Confirm the current evidence really matches this skill's use conditions.",
            "Refuse to rely on this skill if any avoid condition applies or required context is missing.",
            definition.objective,
        ]
        if revision_summary:
            procedure_steps.append(f"Apply the validated revision note: {revision_summary}")
        if definition.produces:
            procedure_steps.append(f"Produce or verify: {', '.join(definition.produces[:3])}.")
        hard_limits = [
            "Never use this skill to override hard risk, regime, event-risk or liquidity blocks.",
        ]
        if definition.avoid_when:
            hard_limits.append(f"Stand down when: {'; '.join(definition.avoid_when[:3])}")
        return SkillRuntimePacket(
            skill_code=definition.code,
            skill_name=definition.name,
            category=definition.category,
            phase=phase,
            objective=definition.objective,
            selection_reason=selection_reason,
            instruction_source=instruction_source,
            confidence=float(applied_skill.get("confidence") or 0.7),
            use_when=tuple(definition.use_when[:3]),
            avoid_when=tuple(definition.avoid_when[:3]),
            required_context=tuple(definition.requires[:4]),
            expected_outputs=tuple(definition.produces[:4]),
            procedure_steps=tuple(step for step in procedure_steps[:max_steps] if step),
            hard_limits=tuple(limit for limit in hard_limits if limit),
            validated_revision_id=(
                int(active_revision.get("id"))
                if isinstance(active_revision, dict) and str(active_revision.get("id") or "").strip().isdigit()
                else None
            ),
            validated_revision_summary=revision_summary or None,
        )

    def _supersede_previous_revisions(
        self,
        session: Session,
        *,
        target_skill_code: str,
        keep_revision_id: int,
        superseded_at: str,
    ) -> None:
        statement = select(MemoryItem).where(
            MemoryItem.memory_type == VALIDATED_SKILL_REVISION_MEMORY_TYPE,
            MemoryItem.scope == f"skill:{target_skill_code}",
        )
        for item in session.scalars(statement).all():
            if item.id == keep_revision_id:
                continue
            meta = dict(item.meta or {})
            if meta.get("activation_status") != "active":
                continue
            meta["activation_status"] = "superseded"
            meta["superseded_at"] = superseded_at
            item.meta = meta
            session.add(item)

    @staticmethod
    def _candidate_payload(item: MemoryItem) -> dict:
        meta = dict(item.meta or {})
        return {
            "id": item.id,
            "scope": item.scope,
            "key": item.key,
            "summary": str(meta.get("summary") or item.content or ""),
            "target_skill_code": meta.get("target_skill_code"),
            "candidate_action": meta.get("candidate_action"),
            "candidate_status": meta.get("candidate_status") or "draft",
            "activation_status": meta.get("activation_status"),
            "validation_required": bool(meta.get("validation_required", True)),
            "source_type": meta.get("source_type"),
            "source_trade_review_id": meta.get("source_trade_review_id"),
            "latest_validation_record_id": meta.get("latest_validation_record_id"),
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "created_at": item.created_at,
            "importance": item.importance,
            "meta": meta,
        }

    @staticmethod
    def _revision_payload(item: MemoryItem | None) -> dict | None:
        if item is None:
            return None
        meta = dict(item.meta or {})
        return {
            "id": item.id,
            "skill_code": meta.get("skill_code"),
            "candidate_id": meta.get("candidate_id"),
            "validation_record_id": meta.get("validation_record_id"),
            "activation_status": meta.get("activation_status") or "inactive",
            "validation_mode": meta.get("validation_mode"),
            "validation_outcome": meta.get("validation_outcome"),
            "revision_summary": meta.get("revision_summary") or item.content,
            "source_trade_review_id": meta.get("source_trade_review_id"),
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "created_at": item.created_at,
            "meta": meta,
        }

    @staticmethod
    def _validation_record_payload(
        item: SkillValidationRecord | None,
        *,
        session: Session | None = None,
        candidate_cache: dict[int, MemoryItem | None] | None = None,
        revision_cache: dict[int, MemoryItem | None] | None = None,
    ) -> dict | None:
        if item is None:
            return None
        candidate_meta: dict = {}
        revision_meta: dict = {}
        if session is not None:
            if candidate_cache is None:
                candidate_cache = {}
            if revision_cache is None:
                revision_cache = {}
            candidate_item = candidate_cache.get(item.candidate_id)
            if candidate_item is None and item.candidate_id not in candidate_cache:
                candidate_item = session.get(MemoryItem, item.candidate_id)
                candidate_cache[item.candidate_id] = candidate_item
            candidate_meta = dict((candidate_item.meta or {}) if isinstance(candidate_item, MemoryItem) else {})
            if item.revision_id is not None:
                revision_item = revision_cache.get(item.revision_id)
                if revision_item is None and item.revision_id not in revision_cache:
                    revision_item = session.get(MemoryItem, item.revision_id)
                    revision_cache[item.revision_id] = revision_item
                revision_meta = dict((revision_item.meta or {}) if isinstance(revision_item, MemoryItem) else {})
        skill_code = revision_meta.get("skill_code") or candidate_meta.get("target_skill_code")
        ticker = revision_meta.get("ticker") or candidate_meta.get("ticker")
        strategy_version_id = revision_meta.get("strategy_version_id") or candidate_meta.get("strategy_version_id")
        return {
            "id": item.id,
            "candidate_id": item.candidate_id,
            "revision_id": item.revision_id,
            "skill_code": skill_code,
            "ticker": ticker,
            "strategy_version_id": strategy_version_id,
            "validation_mode": item.validation_mode,
            "validation_outcome": item.validation_outcome,
            "summary": item.summary,
            "run_id": item.run_id,
            "artifact_url": item.artifact_url,
            "evidence_note": item.evidence_note,
            "sample_size": item.sample_size,
            "win_rate": item.win_rate,
            "avg_pnl_pct": item.avg_pnl_pct,
            "max_drawdown_pct": item.max_drawdown_pct,
            "created_at": item.created_at,
            "evidence_payload": dict(item.evidence_payload or {}),
        }

    @staticmethod
    def _json_ready_payload(payload: dict | None) -> dict | None:
        if payload is None:
            return None
        sanitized = dict(payload)
        created_at = sanitized.get("created_at")
        if isinstance(created_at, datetime):
            sanitized["created_at"] = created_at.isoformat()
        return sanitized
