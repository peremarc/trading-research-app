from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.decision_context import StrategyContextRule
from app.db.models.knowledge_claim import KnowledgeClaim, KnowledgeClaimEvidence
from app.domains.learning.operator_feedback import OperatorDisagreementService
from app.domains.learning.skills import ClaimSkillBridgeService


def _slugify(value: str, *, fallback: str = "claim") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return normalized[:120] or fallback


@dataclass(frozen=True)
class ClaimSeed:
    scope: str
    key: str
    claim_type: str
    claim_text: str
    linked_ticker: str | None = None
    strategy_version_id: int | None = None
    status: str = "provisional"
    confidence: float = 0.5
    freshness_state: str = "current"
    meta: dict | None = None


@dataclass(frozen=True)
class ClaimEvidenceSeed:
    source_type: str
    source_key: str
    stance: str
    summary: str
    evidence_payload: dict | None = None
    strength: float = 0.6
    observed_at: datetime | None = None


@dataclass(frozen=True)
class KnowledgeClaimRuntimePacket:
    claim_id: int
    scope: str
    key: str
    claim_type: str
    claim_text: str
    status: str
    freshness_state: str
    confidence: float
    linked_ticker: str | None = None
    strategy_version_id: int | None = None
    support_count: int = 0
    contradiction_count: int = 0
    evidence_count: int = 0
    evidence_summaries: tuple[str, ...] = ()

    def to_payload(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "scope": self.scope,
            "key": self.key,
            "claim_type": self.claim_type,
            "claim_text": self.claim_text,
            "status": self.status,
            "freshness_state": self.freshness_state,
            "confidence": round(min(max(float(self.confidence), 0.0), 1.0), 2),
            "linked_ticker": self.linked_ticker,
            "strategy_version_id": self.strategy_version_id,
            "support_count": self.support_count,
            "contradiction_count": self.contradiction_count,
            "evidence_count": self.evidence_count,
            "evidence_summaries": list(self.evidence_summaries),
        }


@dataclass(frozen=True)
class KnowledgeClaimReviewQueueItem:
    claim_id: int
    review_reason: str
    review_priority: int
    claim_text: str
    status: str
    freshness_state: str
    confidence: float
    linked_ticker: str | None = None
    strategy_version_id: int | None = None
    support_count: int = 0
    contradiction_count: int = 0
    evidence_count: int = 0

    def to_payload(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "review_reason": self.review_reason,
            "review_priority": self.review_priority,
            "claim_text": self.claim_text,
            "status": self.status,
            "freshness_state": self.freshness_state,
            "confidence": round(min(max(float(self.confidence), 0.0), 1.0), 2),
            "linked_ticker": self.linked_ticker,
            "strategy_version_id": self.strategy_version_id,
            "support_count": self.support_count,
            "contradiction_count": self.contradiction_count,
            "evidence_count": self.evidence_count,
        }


class KnowledgeClaimService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def list_claims(
        self,
        session: Session,
        *,
        scope: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[KnowledgeClaim]:
        statement = select(KnowledgeClaim).order_by(
            desc(KnowledgeClaim.updated_at),
            desc(KnowledgeClaim.confidence),
            desc(KnowledgeClaim.id),
        )
        if scope:
            statement = statement.where(KnowledgeClaim.scope == scope)
        if status:
            statement = statement.where(KnowledgeClaim.status == status)
        statement = statement.limit(max(1, min(int(limit), 200)))
        claims = list(session.scalars(statement).all())
        self._refresh_freshness_for_claims(session, claims)
        return claims

    def get_claim(self, session: Session, claim_id: int) -> KnowledgeClaim | None:
        claim = session.get(KnowledgeClaim, claim_id)
        if claim is None:
            return None
        self._refresh_freshness_for_claims(session, [claim])
        return claim

    def list_evidence(self, session: Session, *, claim_id: int) -> list[KnowledgeClaimEvidence]:
        statement = (
            select(KnowledgeClaimEvidence)
            .where(KnowledgeClaimEvidence.claim_id == claim_id)
            .order_by(desc(KnowledgeClaimEvidence.created_at), desc(KnowledgeClaimEvidence.id))
        )
        return list(session.scalars(statement).all())

    def refresh_freshness(self, session: Session, *, claim_id: int | None = None) -> int:
        if claim_id is None:
            claims = list(session.scalars(select(KnowledgeClaim)).all())
        else:
            claim = session.get(KnowledgeClaim, claim_id)
            claims = [claim] if claim is not None else []
        return self._refresh_freshness_for_claims(session, claims)

    def list_review_queue(self, session: Session, *, limit: int = 50) -> list[dict]:
        claims = self.list_claims(session, limit=max(limit * 3, 50))
        queue: list[KnowledgeClaimReviewQueueItem] = []
        for claim in claims:
            normalized_status = str(claim.status or "").strip().lower()
            normalized_freshness = str(claim.freshness_state or "").strip().lower()
            if normalized_status == "retired":
                continue
            review_reason: str | None = None
            review_priority = 0
            if normalized_status == "contested":
                review_reason = "contradiction_review_due"
                review_priority = 3
            elif normalized_status == "contradicted":
                review_reason = "claim_refuted_review_due"
                review_priority = 3
            elif normalized_freshness == "stale":
                review_reason = "freshness_review_due"
                review_priority = 2
            elif normalized_freshness == "aging":
                review_reason = "freshness_check_recommended"
                review_priority = 1
            if review_reason is None:
                continue
            queue.append(
                KnowledgeClaimReviewQueueItem(
                    claim_id=claim.id,
                    review_reason=review_reason,
                    review_priority=review_priority,
                    claim_text=claim.claim_text,
                    status=claim.status,
                    freshness_state=claim.freshness_state,
                    confidence=float(claim.confidence or 0.0),
                    linked_ticker=claim.linked_ticker,
                    strategy_version_id=claim.strategy_version_id,
                    support_count=int(claim.support_count or 0),
                    contradiction_count=int(claim.contradiction_count or 0),
                    evidence_count=int(claim.evidence_count or 0),
                )
            )
        queue.sort(
            key=lambda item: (
                item.review_priority,
                item.contradiction_count,
                item.evidence_count,
                item.confidence,
                item.claim_id,
            ),
            reverse=True,
        )
        return [item.to_payload() for item in queue[: max(1, min(int(limit), 100))]]

    def review_claim(
        self,
        session: Session,
        *,
        claim_id: int,
        outcome: str,
        summary: str,
        source_key: str | None = None,
        strength: float = 0.65,
        evidence_payload: dict | None = None,
    ) -> tuple[KnowledgeClaim, KnowledgeClaimEvidence | None, dict | None]:
        claim = session.get(KnowledgeClaim, claim_id)
        if claim is None:
            raise ValueError("Knowledge claim not found.")

        normalized_outcome = str(outcome or "").strip().lower()
        if normalized_outcome not in {"confirm", "contradict", "retire"}:
            raise ValueError("Claim review outcome must be confirm, contradict or retire.")
        summary_text = str(summary or "").strip()
        if not summary_text:
            raise ValueError("Claim review summary is required.")

        evidence: KnowledgeClaimEvidence | None = None
        review_source_key = str(source_key or "").strip() or f"claim_review:{claim_id}:{normalized_outcome}"
        if normalized_outcome in {"confirm", "contradict"}:
            evidence = self.add_evidence(
                session,
                claim_id=claim_id,
                seed=ClaimEvidenceSeed(
                    source_type="claim_review",
                    source_key=review_source_key,
                    stance="support" if normalized_outcome == "confirm" else "contradict",
                    summary=summary_text,
                    evidence_payload=dict(evidence_payload or {}),
                    strength=max(0.0, min(float(strength), 1.0)),
                    observed_at=datetime.now(UTC),
                ),
            )
            claim = session.get(KnowledgeClaim, claim_id) or claim
            claim.freshness_state = "current"
            claim.last_reviewed_at = datetime.now(UTC)
            session.add(claim)
            session.commit()
            session.refresh(claim)
            promoted_skill_candidate = None
            if normalized_outcome == "confirm":
                promoted_skill_candidate = ClaimSkillBridgeService().maybe_promote_claim(session, claim=claim)
                claim = session.get(KnowledgeClaim, claim_id) or claim
            else:
                OperatorDisagreementService().record(
                    session,
                    disagreement_type="claim_contradicted",
                    entity_type="knowledge_claim",
                    entity_id=claim.id,
                    action=normalized_outcome,
                    summary=summary_text,
                    ticker=claim.linked_ticker,
                    strategy_version_id=claim.strategy_version_id,
                    source="claim_review",
                    details={
                        "claim_key": claim.key,
                        "claim_status": claim.status,
                        "freshness_state": claim.freshness_state,
                        "evidence_id": evidence.id if evidence is not None else None,
                    },
                )
            return claim, evidence, promoted_skill_candidate

        claim.status = "retired"
        claim.freshness_state = "stale"
        claim.last_reviewed_at = datetime.now(UTC)
        claim.meta = {
            **dict(claim.meta or {}),
            "review_outcome": "retire",
            "review_summary": summary_text,
            "review_source_key": review_source_key,
        }
        session.add(claim)
        session.commit()
        session.refresh(claim)
        OperatorDisagreementService().record(
            session,
            disagreement_type="claim_retired",
            entity_type="knowledge_claim",
            entity_id=claim.id,
            action=normalized_outcome,
            summary=summary_text,
            ticker=claim.linked_ticker,
            strategy_version_id=claim.strategy_version_id,
            source="claim_review",
            details={
                "claim_key": claim.key,
                "claim_status": claim.status,
                "freshness_state": claim.freshness_state,
            },
        )
        return claim, None, None

    def build_runtime_packets(
        self,
        session: Session,
        *,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
        max_packets: int = 3,
        max_evidence_per_packet: int = 2,
    ) -> list[dict]:
        selection = self.build_runtime_selection(
            session,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            max_packets=max_packets,
            max_evidence_per_packet=max_evidence_per_packet,
        )
        return [item for item in selection.get("packets", []) if isinstance(item, dict)]

    def build_runtime_selection(
        self,
        session: Session,
        *,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
        max_packets: int = 3,
        max_evidence_per_packet: int = 2,
    ) -> dict:
        packet_limit = max(0, int(max_packets or 0))
        packets: list[dict] = []
        evidence_limit = max(1, int(max_evidence_per_packet or 1))
        claims = self._runtime_candidate_claims(
            session,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
        )

        if packet_limit > 0:
            seen: set[int] = set()
            for claim in claims:
                if claim.id in seen:
                    continue
                packet = self._build_runtime_packet(
                    session,
                    claim=claim,
                    max_evidence=evidence_limit,
                )
                if packet is None:
                    continue
                packets.append(packet.to_payload())
                seen.add(claim.id)
                if len(packets) >= packet_limit:
                    break

        available_keys = [str(getattr(item, "key", "") or "").strip() for item in claims if str(getattr(item, "key", "") or "").strip()]
        loaded_keys = [str(item.get("key") or "").strip() for item in packets if str(item.get("key") or "").strip()]
        skipped_keys = [key for key in available_keys if key not in set(loaded_keys)]

        return {
            "packets": packets,
            "budget": {
                "enabled": packet_limit > 0,
                "available_count": len(available_keys),
                "loaded_count": len(loaded_keys),
                "truncated_count": max(len(available_keys) - len(loaded_keys), 0),
                "max_packets": packet_limit,
                "max_evidence_per_packet": evidence_limit,
                "loaded_keys": loaded_keys,
                "skipped_keys": skipped_keys[:5],
                "loaded_evidence_count": sum(
                    len([item for item in packet.get("evidence_summaries", []) if isinstance(item, str) and item.strip()])
                    for packet in packets
                    if isinstance(packet, dict)
                ),
            },
        }

    def _runtime_candidate_claims(
        self,
        session: Session,
        *,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
    ) -> list[KnowledgeClaim]:
        statement = select(KnowledgeClaim)
        filters = []
        normalized_ticker = str(ticker or "").strip().upper() or None
        if strategy_version_id is not None:
            filters.append(
                or_(
                    KnowledgeClaim.strategy_version_id == strategy_version_id,
                    KnowledgeClaim.scope == f"strategy:{strategy_version_id}",
                )
            )
        if normalized_ticker is not None:
            filters.append(KnowledgeClaim.linked_ticker == normalized_ticker)
        if filters:
            statement = statement.where(or_(*filters))
        statement = statement.where(KnowledgeClaim.status.notin_(("contradicted", "retired")))
        claims = list(session.scalars(statement).all())
        self._refresh_freshness_for_claims(session, claims)
        claims.sort(
            key=lambda item: (
                self._status_priority(getattr(item, "status", None)),
                self._freshness_priority(getattr(item, "freshness_state", None)),
                float(getattr(item, "confidence", 0.0) or 0.0),
                int(getattr(item, "evidence_count", 0) or 0),
                getattr(item, "updated_at", None) or getattr(item, "created_at", None) or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return claims

    @staticmethod
    def render_runtime_claim_prompt(packets: list[dict] | None) -> str:
        if not isinstance(packets, list) or not packets:
            return ""

        lines = [
            "Relevant durable claim memory is supplied below.",
            "Treat validated and supported claims as prior evidence, contested claims as caution, and stale claims as lower-trust background.",
            "Do not let a stored claim override fresh live evidence, regime policy or hard risk constraints.",
        ]
        for index, item in enumerate(packets, start=1):
            if not isinstance(item, dict):
                continue
            claim_text = str(item.get("claim_text") or "").strip()
            if not claim_text:
                continue
            lines.append(
                f"{index}. Claim `{item.get('key')}` [{item.get('status')}/{item.get('freshness_state')}]"
            )
            lines.append(f"   Claim: {claim_text}")
            lines.append(
                "   Confidence: "
                f"{item.get('confidence')} | support={item.get('support_count', 0)} "
                f"| contradiction={item.get('contradiction_count', 0)}"
            )
            evidence_summaries = [str(part).strip() for part in item.get("evidence_summaries", []) if str(part).strip()]
            if evidence_summaries:
                lines.append(f"   Evidence: {' | '.join(evidence_summaries[:3])}")
        return "\n".join(lines)

    def create_claim(self, session: Session, seed: ClaimSeed) -> KnowledgeClaim:
        claim = KnowledgeClaim(
            scope=seed.scope,
            key=seed.key,
            claim_type=seed.claim_type,
            claim_text=seed.claim_text,
            status=seed.status,
            confidence=max(0.0, min(float(seed.confidence), 1.0)),
            freshness_state=seed.freshness_state,
            linked_ticker=seed.linked_ticker,
            strategy_version_id=seed.strategy_version_id,
            meta=dict(seed.meta or {}),
            last_reviewed_at=datetime.now(UTC),
        )
        session.add(claim)
        session.commit()
        session.refresh(claim)
        return claim

    def upsert_claim(self, session: Session, seed: ClaimSeed) -> tuple[KnowledgeClaim, bool]:
        statement = select(KnowledgeClaim).where(KnowledgeClaim.scope == seed.scope, KnowledgeClaim.key == seed.key)
        claim = session.scalars(statement).first()
        created = False
        if claim is None:
            created = True
            claim = KnowledgeClaim(
                scope=seed.scope,
                key=seed.key,
                claim_type=seed.claim_type,
                claim_text=seed.claim_text,
                status=seed.status,
                confidence=max(0.0, min(float(seed.confidence), 1.0)),
                freshness_state=seed.freshness_state,
                linked_ticker=seed.linked_ticker,
                strategy_version_id=seed.strategy_version_id,
                meta=dict(seed.meta or {}),
                last_reviewed_at=datetime.now(UTC),
            )
            session.add(claim)
            session.commit()
            session.refresh(claim)
            return claim, created

        claim.claim_type = seed.claim_type
        claim.claim_text = seed.claim_text
        claim.linked_ticker = seed.linked_ticker
        claim.strategy_version_id = seed.strategy_version_id
        claim.freshness_state = seed.freshness_state or claim.freshness_state
        claim.meta = {**dict(claim.meta or {}), **dict(seed.meta or {})}
        claim.last_reviewed_at = datetime.now(UTC)
        session.add(claim)
        session.commit()
        session.refresh(claim)
        return claim, created

    def add_evidence(self, session: Session, *, claim_id: int, seed: ClaimEvidenceSeed) -> KnowledgeClaimEvidence:
        claim = session.get(KnowledgeClaim, claim_id)
        if claim is None:
            raise ValueError("Knowledge claim not found.")

        statement = select(KnowledgeClaimEvidence).where(
            KnowledgeClaimEvidence.claim_id == claim_id,
            KnowledgeClaimEvidence.source_key == seed.source_key,
        )
        evidence = session.scalars(statement).first()
        if evidence is None:
            evidence = KnowledgeClaimEvidence(
                claim_id=claim_id,
                source_type=seed.source_type,
                source_key=seed.source_key,
                stance=seed.stance,
                summary=seed.summary,
                evidence_payload=dict(seed.evidence_payload or {}),
                strength=max(0.0, min(float(seed.strength), 1.0)),
                observed_at=seed.observed_at,
            )
            session.add(evidence)
        else:
            evidence.source_type = seed.source_type
            evidence.stance = seed.stance
            evidence.summary = seed.summary
            evidence.evidence_payload = dict(seed.evidence_payload or {})
            evidence.strength = max(0.0, min(float(seed.strength), 1.0))
            evidence.observed_at = seed.observed_at
            session.add(evidence)
        session.commit()
        session.refresh(evidence)
        self._refresh_claim_rollup(session, claim_id=claim_id)
        return evidence

    def record_trade_review_claim(
        self,
        session: Session,
        *,
        position,
        review,
        skill_candidate: dict | None = None,
    ) -> KnowledgeClaim | None:
        recommendations = [str(item).strip() for item in (getattr(review, "recommended_changes", None) or []) if str(item).strip()]
        proposed_change = str(getattr(review, "proposed_strategy_change", "") or "").strip()
        lesson = str(getattr(review, "lesson_learned", "") or "").strip()
        failure_mode = str(getattr(review, "failure_mode", "") or "").strip()
        root_cause = str(getattr(review, "root_cause", "") or "").strip()
        target_skill_code = (
            str((skill_candidate or {}).get("target_skill_code") or "").strip()
            if isinstance(skill_candidate, dict)
            else ""
        )
        claim_text = proposed_change or (recommendations[0] if recommendations else "") or lesson
        if not claim_text:
            return None

        scope = f"strategy:{getattr(position, 'strategy_version_id', None) or 'unknown'}"
        anchor = target_skill_code or proposed_change or failure_mode or lesson
        claim_key = f"review-claim:{_slugify(anchor, fallback=str(getattr(review, 'id', 'review')))}"
        meta = {
            "source": "trade_review",
            "review_id": getattr(review, "id", None),
            "position_id": getattr(position, "id", None),
            "failure_mode": failure_mode or None,
            "cause_category": getattr(review, "cause_category", None),
            "target_skill_code": target_skill_code or None,
            "recommended_changes": recommendations,
            "proposed_strategy_change": proposed_change or None,
        }
        claim, _ = self.upsert_claim(
            session,
            ClaimSeed(
                scope=scope,
                key=claim_key,
                claim_type="review_improvement",
                claim_text=claim_text,
                linked_ticker=getattr(position, "ticker", None),
                strategy_version_id=getattr(position, "strategy_version_id", None),
                status="provisional",
                confidence=0.58,
                freshness_state="current",
                meta=meta,
            ),
        )
        self.add_evidence(
            session,
            claim_id=claim.id,
            seed=ClaimEvidenceSeed(
                source_type="trade_review",
                source_key=f"trade_review:{getattr(review, 'id', 'unknown')}",
                stance="support",
                summary=root_cause or claim_text,
                evidence_payload={
                    "position_id": getattr(position, "id", None),
                    "position_ticker": getattr(position, "ticker", None),
                    "outcome_label": getattr(review, "outcome_label", None),
                    "cause_category": getattr(review, "cause_category", None),
                    "failure_mode": failure_mode or None,
                    "lesson_learned": lesson or None,
                    "recommended_changes": recommendations,
                    "skill_candidate": dict(skill_candidate or {}) if isinstance(skill_candidate, dict) else None,
                },
                strength=0.72 if getattr(review, "should_modify_strategy", False) else 0.62,
                observed_at=getattr(review, "created_at", None),
            ),
        )
        return claim

    def record_strategy_rule_claim(self, session: Session, *, rule: StrategyContextRule) -> KnowledgeClaim:
        action = str(rule.action_type or "").strip()
        stance = "support"
        claim_text = (
            f"Context feature {rule.feature_scope}.{rule.feature_key}={rule.feature_value} should {action.replace('_', ' ')}."
        )
        claim_key = f"rule-claim:{rule.feature_scope}:{rule.feature_key}:{rule.feature_value}:{action}"
        scope = f"strategy:{rule.strategy_version_id or rule.strategy_id or 'unknown'}"
        claim, _ = self.upsert_claim(
            session,
            ClaimSeed(
                scope=scope,
                key=claim_key,
                claim_type="context_rule",
                claim_text=claim_text,
                linked_ticker=None,
                strategy_version_id=rule.strategy_version_id,
                status="supported",
                confidence=max(0.0, min(float(rule.confidence or 0.0), 1.0)),
                freshness_state="current",
                meta={
                    "source": "feature_outcome_stat",
                    "feature_scope": rule.feature_scope,
                    "feature_key": rule.feature_key,
                    "feature_value": rule.feature_value,
                    "action_type": action,
                    "rule_id": rule.id,
                    "promotion_trace": self._promotion_trace_for_rule(rule),
                },
            ),
        )
        self.add_evidence(
            session,
            claim_id=claim.id,
            seed=ClaimEvidenceSeed(
                source_type="strategy_context_rule",
                source_key=f"rule:{rule.feature_scope}:{rule.feature_key}:{rule.feature_value}:{action}",
                stance=stance,
                summary=str(rule.rationale or claim_text),
                evidence_payload=dict(rule.evidence_payload or {}),
                strength=max(0.0, min(float(rule.confidence or 0.0), 1.0)),
            ),
        )
        return claim

    def maybe_promote_claim_to_skill_candidate(self, session: Session, *, claim_id: int, force: bool = False) -> dict | None:
        claim = session.get(KnowledgeClaim, claim_id)
        if claim is None:
            raise ValueError("Knowledge claim not found.")
        return ClaimSkillBridgeService().maybe_promote_claim(session, claim=claim, force=force)

    def _build_runtime_packet(
        self,
        session: Session,
        *,
        claim: KnowledgeClaim,
        max_evidence: int,
    ) -> KnowledgeClaimRuntimePacket | None:
        evidence_items = self.list_evidence(session, claim_id=claim.id)
        if not evidence_items and str(claim.status or "").strip().lower() == "provisional":
            return None
        evidence_items.sort(
            key=lambda item: (
                max(0.0, min(float(item.strength or 0.0), 1.0)),
                item.observed_at or item.updated_at or item.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        evidence_summaries = tuple(str(item.summary or "").strip() for item in evidence_items[:max_evidence] if str(item.summary or "").strip())
        return KnowledgeClaimRuntimePacket(
            claim_id=claim.id,
            scope=claim.scope,
            key=claim.key,
            claim_type=claim.claim_type,
            claim_text=claim.claim_text,
            status=claim.status,
            freshness_state=claim.freshness_state,
            confidence=float(claim.confidence or 0.0),
            linked_ticker=claim.linked_ticker,
            strategy_version_id=claim.strategy_version_id,
            support_count=int(claim.support_count or 0),
            contradiction_count=int(claim.contradiction_count or 0),
            evidence_count=int(claim.evidence_count or 0),
            evidence_summaries=evidence_summaries,
        )

    @staticmethod
    def _status_priority(status: str | None) -> int:
        normalized = str(status or "").strip().lower()
        if normalized == "validated":
            return 5
        if normalized == "supported":
            return 4
        if normalized == "contested":
            return 3
        if normalized == "provisional":
            return 2
        if normalized == "contradicted":
            return 1
        if normalized == "retired":
            return 0
        return 0

    @staticmethod
    def _freshness_priority(freshness_state: str | None) -> int:
        normalized = str(freshness_state or "").strip().lower()
        if normalized == "current":
            return 3
        if normalized == "aging":
            return 2
        if normalized == "stale":
            return 1
        return 0

    def _refresh_freshness_for_claims(self, session: Session, claims: list[KnowledgeClaim]) -> int:
        changed = 0
        if not claims:
            return changed
        now = datetime.now(UTC)
        aging_days = max(int(self.settings.knowledge_claim_aging_days or 0), 1)
        stale_days = max(int(self.settings.knowledge_claim_stale_days or 0), aging_days + 1)
        dirty = False
        for claim in claims:
            if claim is None:
                continue
            if str(claim.status or "").strip().lower() == "retired":
                if claim.freshness_state != "stale":
                    claim.freshness_state = "stale"
                    session.add(claim)
                    changed += 1
                    dirty = True
                continue
            anchor = claim.last_reviewed_at or claim.updated_at or claim.created_at
            if anchor is None:
                target_state = "current"
            else:
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=UTC)
                age_days = max((now - anchor).days, 0)
                if age_days >= stale_days:
                    target_state = "stale"
                elif age_days >= aging_days:
                    target_state = "aging"
                else:
                    target_state = "current"
            if claim.freshness_state != target_state:
                claim.freshness_state = target_state
                session.add(claim)
                changed += 1
                dirty = True
        if dirty:
            session.commit()
            for claim in claims:
                if claim is not None:
                    session.refresh(claim)
        return changed

    def _refresh_claim_rollup(self, session: Session, *, claim_id: int) -> None:
        claim = session.get(KnowledgeClaim, claim_id)
        if claim is None:
            return
        evidence_items = self.list_evidence(session, claim_id=claim_id)
        support = len([item for item in evidence_items if item.stance == "support"])
        contradict = len([item for item in evidence_items if item.stance == "contradict"])
        total = len(evidence_items)
        avg_strength = (
            sum(max(0.0, min(float(item.strength or 0.0), 1.0)) for item in evidence_items) / total if total else 0.5
        )
        claim.evidence_count = total
        claim.support_count = support
        claim.contradiction_count = contradict
        claim.confidence = round(avg_strength, 2)
        claim.status = self._derive_status(support_count=support, contradiction_count=contradict, evidence_count=total)
        claim.freshness_state = "current"
        claim.last_reviewed_at = datetime.now(UTC)
        session.add(claim)
        session.commit()
        session.refresh(claim)

    @staticmethod
    def _derive_status(*, support_count: int, contradiction_count: int, evidence_count: int) -> str:
        if evidence_count == 0:
            return "provisional"
        if contradiction_count > 0 and support_count == 0:
            return "contradicted"
        if support_count > 0 and contradiction_count > 0:
            return "contested"
        if support_count >= 2:
            return "validated"
        if support_count >= 1:
            return "supported"
        return "provisional"

    def _promotion_trace_for_rule(self, rule: StrategyContextRule) -> dict:
        from app.domains.learning.skills import SkillPromotionService

        return SkillPromotionService().build_temporary_rule_trace(
            feature_scope=rule.feature_scope,
            feature_key=rule.feature_key,
            feature_value=rule.feature_value,
        )
