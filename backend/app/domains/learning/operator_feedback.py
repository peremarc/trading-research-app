from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models.memory import MemoryItem


OPERATOR_DISAGREEMENT_MEMORY_TYPE = "operator_disagreement"
OPERATOR_DISAGREEMENT_CLUSTER_MEMORY_TYPE = "operator_disagreement_cluster"


class OperatorDisagreementService:
    def record(
        self,
        session: Session,
        *,
        disagreement_type: str,
        entity_type: str,
        entity_id: int,
        action: str,
        summary: str,
        ticker: str | None = None,
        strategy_version_id: int | None = None,
        position_id: int | None = None,
        source: str = "operator_review",
        details: dict | None = None,
    ) -> dict:
        from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
        from app.domains.learning.services import JournalService, MemoryService

        timestamp = int(datetime.now(UTC).timestamp())
        payload = {
            "disagreement_type": str(disagreement_type or "").strip() or "operator_disagreement",
            "entity_type": str(entity_type or "").strip(),
            "entity_id": int(entity_id),
            "action": str(action or "").strip(),
            "summary": str(summary or "").strip(),
            "ticker": str(ticker or "").strip().upper() or None,
            "strategy_version_id": strategy_version_id,
            "position_id": position_id,
            "source": str(source or "").strip() or "operator_review",
            "recorded_at": datetime.now(UTC).isoformat(),
            "details": dict(details or {}),
        }
        payload["details"].setdefault("entity_type", payload["entity_type"])
        payload["details"].setdefault("entity_id", payload["entity_id"])

        journal_entry = JournalService().create_entry(
            session,
            JournalEntryCreate(
                entry_type="operator_disagreement",
                ticker=payload["ticker"],
                strategy_version_id=payload["strategy_version_id"],
                position_id=payload["position_id"],
                market_context={"source": payload["source"]},
                observations={"operator_disagreement": payload},
                reasoning=payload["summary"],
                decision=payload["action"],
                lessons=payload["summary"],
            ),
        )
        memory_item = MemoryService().create_item(
            session,
            MemoryItemCreate(
                memory_type=OPERATOR_DISAGREEMENT_MEMORY_TYPE,
                scope="operator_feedback",
                key=f"operator_disagreement:{payload['entity_type']}:{payload['entity_id']}:{timestamp}",
                content=payload["summary"],
                meta={
                    **payload,
                    "journal_entry_id": journal_entry.id,
                },
                importance=0.72,
            ),
        )
        return {
            **payload,
            "journal_entry_id": journal_entry.id,
            "memory_item_id": memory_item.id,
        }

    def list_items(self, session: Session, *, limit: int = 100) -> list[dict]:
        statement = (
            select(MemoryItem)
            .where(MemoryItem.memory_type == OPERATOR_DISAGREEMENT_MEMORY_TYPE)
            .order_by(desc(MemoryItem.created_at), desc(MemoryItem.importance), desc(MemoryItem.id))
            .limit(max(1, min(int(limit), 200)))
        )
        return [self._payload_from_item(item) for item in session.scalars(statement).all()]

    def summarize(self, session: Session, *, limit: int = 200) -> dict:
        items = self.list_items(session, limit=limit)
        return {
            "total_events": len(items),
            "by_disagreement_type": self._bucketize(items, lambda item: item.get("disagreement_type")),
            "by_entity_type": self._bucketize(items, lambda item: item.get("entity_type")),
            "by_ticker": self._bucketize(items, lambda item: item.get("ticker")),
            "by_target_skill_code": self._bucketize(
                items,
                lambda item: (
                    self._as_dict(item.get("details")).get("target_skill_code")
                    or self._as_dict(item.get("details")).get("claim_key")
                ),
            ),
        }

    def sync_clusters(self, session: Session, *, limit: int = 200, min_count: int = 2) -> list[dict]:
        source_items = self.list_items(session, limit=limit)
        grouped = self._group_clusters(source_items)
        existing = list(
            session.scalars(
                select(MemoryItem).where(MemoryItem.memory_type == OPERATOR_DISAGREEMENT_CLUSTER_MEMORY_TYPE)
            ).all()
        )
        existing_by_key = {str(item.key): item for item in existing}
        active_keys: set[str] = set()

        for cluster in grouped:
            if int(cluster.get("event_count") or 0) < max(1, int(min_count or 1)):
                continue
            cluster_key = str(cluster["cluster_key"])
            active_keys.add(cluster_key)
            item = existing_by_key.get(cluster_key)
            promoted_claim_id = self._as_dict(item.meta if item is not None else {}).get("promoted_claim_id")
            promoted_skill_gap_id = self._as_dict(item.meta if item is not None else {}).get("promoted_skill_gap_id")
            status = "promoted" if promoted_claim_id or promoted_skill_gap_id else "open"
            summary = (
                f"Repeated operator disagreement: {cluster.get('disagreement_type') or 'pattern'}"
                + (f" on {cluster.get('ticker')}" if cluster.get("ticker") else "")
            )
            meta = {
                **cluster,
                "last_seen_at": (
                    cluster.get("last_seen_at").isoformat()
                    if isinstance(cluster.get("last_seen_at"), datetime)
                    else cluster.get("last_seen_at")
                ),
                "status": status,
                "promoted_claim_id": promoted_claim_id,
                "promoted_skill_gap_id": promoted_skill_gap_id,
            }
            importance = min(0.68 + (0.04 * int(cluster.get("event_count") or 0)), 0.95)
            if item is None:
                item = MemoryItem(
                    memory_type=OPERATOR_DISAGREEMENT_CLUSTER_MEMORY_TYPE,
                    scope="operator_feedback",
                    key=cluster_key,
                    content=summary,
                    meta=meta,
                    importance=importance,
                )
            else:
                item.content = summary
                item.meta = meta
                item.importance = importance
            session.add(item)

        for item in existing:
            if str(item.key) in active_keys:
                continue
            meta = dict(item.meta or {})
            meta["status"] = "inactive"
            item.meta = meta
            session.add(item)

        session.commit()
        return self.list_clusters(session, limit=limit)

    def list_clusters(self, session: Session, *, limit: int = 100) -> list[dict]:
        statement = select(MemoryItem).where(MemoryItem.memory_type == OPERATOR_DISAGREEMENT_CLUSTER_MEMORY_TYPE)
        items = list(session.scalars(statement).all())
        payloads = [self._cluster_payload_from_item(item) for item in items]
        payloads.sort(
            key=lambda item: (
                int(item.get("event_count") or 0),
                self._coerce_datetime(item.get("last_seen_at")) or datetime.min.replace(tzinfo=UTC),
                float(item.get("importance") or 0.0),
            ),
            reverse=True,
        )
        return payloads[: max(1, min(int(limit), 200))]

    def promote_cluster_to_claim(self, session: Session, *, cluster_id: int) -> dict:
        item = session.get(MemoryItem, cluster_id)
        if item is None or item.memory_type != OPERATOR_DISAGREEMENT_CLUSTER_MEMORY_TYPE:
            raise ValueError("Operator disagreement cluster not found.")

        from app.domains.learning.claims import ClaimEvidenceSeed, ClaimSeed, KnowledgeClaimService
        from app.domains.learning.schemas import JournalEntryCreate
        from app.domains.learning.services import JournalService

        meta = dict(item.meta or {})
        cluster_payload = self._cluster_payload_from_item(item)
        cluster_key = str(meta.get("cluster_key") or item.key)
        strategy_version_id = meta.get("strategy_version_id")
        ticker = meta.get("ticker")
        event_count = int(meta.get("event_count") or 0)
        scope = f"strategy:{strategy_version_id}" if strategy_version_id else "operator_feedback"
        claim_text = (
            f"Repeated operator disagreement suggests the current procedure or belief around "
            f"{meta.get('target_skill_code') or meta.get('claim_key') or meta.get('entity_type') or 'this area'} "
            f"needs explicit review."
        )
        claim_service = KnowledgeClaimService()
        claim, _ = claim_service.upsert_claim(
            session,
            ClaimSeed(
                scope=scope,
                key=f"operator_disagreement_cluster:{cluster_key}"[:160],
                claim_type="operator_disagreement_pattern",
                claim_text=claim_text,
                linked_ticker=ticker,
                strategy_version_id=strategy_version_id,
                status="provisional",
                confidence=min(0.55 + (0.03 * event_count), 0.9),
                freshness_state="current",
                meta={
                    "source": "operator_disagreement_cluster",
                    "cluster_id": item.id,
                    "cluster_key": cluster_key,
                    "disagreement_type": meta.get("disagreement_type"),
                    "target_skill_code": meta.get("target_skill_code"),
                    "claim_key": meta.get("claim_key"),
                    "event_count": event_count,
                },
            ),
        )
        claim_service.add_evidence(
            session,
            claim_id=claim.id,
            seed=ClaimEvidenceSeed(
                source_type="operator_disagreement_cluster",
                source_key=f"operator_disagreement_cluster:{item.id}",
                stance="support",
                summary=(
                    f"Cluster observed {event_count} operator disagreement events. "
                    + " | ".join(list(meta.get("sample_summaries") or [])[:2])
                ).strip(),
                evidence_payload={
                    "cluster_id": item.id,
                    "event_count": event_count,
                    "source_memory_ids": list(meta.get("source_memory_ids") or []),
                },
                strength=min(0.6 + (0.03 * event_count), 0.9),
                observed_at=self._coerce_datetime(meta.get("last_seen_at")),
            ),
        )

        meta["promoted_claim_id"] = claim.id
        meta["status"] = "promoted"
        meta["promoted_at"] = datetime.now(UTC).isoformat()
        item.meta = meta
        session.add(item)
        session.commit()
        session.refresh(item)

        JournalService().create_entry(
            session,
            JournalEntryCreate(
                entry_type="operator_disagreement_cluster_promoted",
                ticker=ticker,
                strategy_version_id=strategy_version_id,
                observations={
                    "operator_disagreement_cluster": self._cluster_payload_from_item(item),
                    "claim_id": claim.id,
                },
                reasoning=claim_text,
                decision="promote_to_claim",
                lessons=claim_text,
            ),
        )

        return {
            "cluster": self._cluster_payload_from_item(item),
            "claim": claim,
        }

    def promote_cluster_to_skill_gap(self, session: Session, *, cluster_id: int) -> dict:
        item = session.get(MemoryItem, cluster_id)
        if item is None or item.memory_type != OPERATOR_DISAGREEMENT_CLUSTER_MEMORY_TYPE:
            raise ValueError("Operator disagreement cluster not found.")

        from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
        from app.domains.learning.services import JournalService, MemoryService
        from app.domains.learning.skills import SkillGapService, SkillLifecycleService

        meta = dict(item.meta or {})
        existing_gap_id = meta.get("promoted_skill_gap_id")
        gap_service = SkillGapService()
        if existing_gap_id is not None:
            existing_gap = gap_service.get_gap(session, gap_id=int(existing_gap_id))
            if existing_gap is not None:
                return {
                    "cluster": self._cluster_payload_from_item(item),
                    "gap": existing_gap,
                }

        cluster_key = str(meta.get("cluster_key") or item.key)
        strategy_version_id = meta.get("strategy_version_id")
        ticker = meta.get("ticker")
        event_count = int(meta.get("event_count") or 0)
        target_skill_code = str(meta.get("target_skill_code") or "").strip() or None
        claim_key = str(meta.get("claim_key") or "").strip() or None
        scope = f"strategy:{strategy_version_id}" if strategy_version_id else "operator_feedback"
        summary = (
            f"Repeated operator disagreement cluster suggests a procedural gap around "
            f"{target_skill_code or claim_key or meta.get('entity_type') or 'the current procedure'}."
        )
        gap_key = f"skill_gap:operator_disagreement_cluster:{cluster_key}"[:160]

        existing_gap_item = session.scalar(
            select(MemoryItem).where(
                MemoryItem.memory_type == "skill_gap",
                MemoryItem.key == gap_key,
            )
        )
        if existing_gap_item is None:
            gap_item = MemoryService().create_item(
                session,
                MemoryItemCreate(
                    memory_type="skill_gap",
                    scope=scope,
                    key=gap_key,
                    content=summary,
                    meta={
                        "summary": summary,
                        "gap_type": "repeated_operator_disagreement",
                        "status": "open",
                        "ticker": ticker,
                        "strategy_version_id": strategy_version_id,
                        "source_type": "operator_disagreement_cluster",
                        "source_operator_disagreement_cluster_id": item.id,
                        "source_cluster_key": cluster_key,
                        "linked_skill_code": target_skill_code,
                        "target_skill_code": target_skill_code,
                        "candidate_action": "update_existing_skill" if target_skill_code else None,
                        "event_count": event_count,
                        "claim_key": claim_key,
                        "evidence": {
                            "sample_summaries": list(meta.get("sample_summaries") or []),
                            "source_memory_ids": list(meta.get("source_memory_ids") or []),
                            "source_journal_ids": list(meta.get("source_journal_ids") or []),
                        },
                    },
                    importance=min(0.7 + (0.03 * event_count), 0.93),
                ),
            )
        else:
            existing_meta = dict(existing_gap_item.meta or {})
            existing_meta.update(
                {
                    "summary": summary,
                    "gap_type": "repeated_operator_disagreement",
                    "status": existing_meta.get("status") or "open",
                    "ticker": ticker,
                    "strategy_version_id": strategy_version_id,
                    "source_type": "operator_disagreement_cluster",
                    "source_operator_disagreement_cluster_id": item.id,
                    "source_cluster_key": cluster_key,
                    "linked_skill_code": target_skill_code,
                    "target_skill_code": target_skill_code,
                    "candidate_action": "update_existing_skill" if target_skill_code else None,
                    "event_count": event_count,
                    "claim_key": claim_key,
                    "evidence": {
                        "sample_summaries": list(meta.get("sample_summaries") or []),
                        "source_memory_ids": list(meta.get("source_memory_ids") or []),
                        "source_journal_ids": list(meta.get("source_journal_ids") or []),
                    },
                }
            )
            existing_gap_item.content = summary
            existing_gap_item.meta = existing_meta
            existing_gap_item.importance = min(0.7 + (0.03 * event_count), 0.93)
            session.add(existing_gap_item)
            session.commit()
            session.refresh(existing_gap_item)
            gap_item = existing_gap_item

        gap_payload = gap_service.get_gap(session, gap_id=gap_item.id)
        if gap_payload is None:
            raise ValueError("Skill gap promotion failed.")

        meta["promoted_skill_gap_id"] = gap_item.id
        meta["status"] = "promoted"
        meta["promoted_to_gap_at"] = datetime.now(UTC).isoformat()
        item.meta = meta
        session.add(item)
        session.commit()
        session.refresh(item)

        JournalService().create_entry(
            session,
            JournalEntryCreate(
                entry_type="operator_disagreement_cluster_promoted_to_gap",
                ticker=ticker,
                strategy_version_id=strategy_version_id,
                observations={
                    "operator_disagreement_cluster": self._cluster_payload_from_item(item),
                    "skill_gap": SkillLifecycleService._json_ready_payload(gap_payload),
                },
                reasoning=summary,
                decision="promote_to_skill_gap",
                lessons=summary,
            ),
        )

        return {
            "cluster": self._cluster_payload_from_item(item),
            "gap": gap_payload,
        }

    @staticmethod
    def _payload_from_item(item: MemoryItem) -> dict:
        meta = dict(item.meta or {})
        details = dict(meta.get("details") or {})
        return {
            "id": item.id,
            "disagreement_type": meta.get("disagreement_type"),
            "entity_type": meta.get("entity_type"),
            "entity_id": meta.get("entity_id"),
            "action": meta.get("action"),
            "summary": item.content or meta.get("summary"),
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "position_id": meta.get("position_id"),
            "source": meta.get("source"),
            "journal_entry_id": meta.get("journal_entry_id"),
            "importance": item.importance,
            "created_at": item.created_at,
            "details": details,
        }

    @staticmethod
    def _cluster_payload_from_item(item: MemoryItem) -> dict:
        meta = dict(item.meta or {})
        return {
            "id": item.id,
            "cluster_key": meta.get("cluster_key") or item.key,
            "status": meta.get("status") or "open",
            "disagreement_type": meta.get("disagreement_type"),
            "entity_type": meta.get("entity_type"),
            "ticker": meta.get("ticker"),
            "strategy_version_id": meta.get("strategy_version_id"),
            "target_skill_code": meta.get("target_skill_code"),
            "claim_key": meta.get("claim_key"),
            "event_count": int(meta.get("event_count") or 0),
            "last_seen_at": meta.get("last_seen_at"),
            "sample_summaries": list(meta.get("sample_summaries") or []),
            "source_memory_ids": list(meta.get("source_memory_ids") or []),
            "source_journal_ids": list(meta.get("source_journal_ids") or []),
            "promoted_claim_id": meta.get("promoted_claim_id"),
            "promoted_skill_gap_id": meta.get("promoted_skill_gap_id"),
            "importance": item.importance,
            "created_at": item.created_at.isoformat() if isinstance(item.created_at, datetime) else item.created_at,
            "meta": meta,
        }

    @classmethod
    def _group_clusters(cls, items: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for item in items:
            details = cls._as_dict(item.get("details"))
            cluster_key = cls._cluster_key(
                disagreement_type=item.get("disagreement_type"),
                entity_type=item.get("entity_type"),
                ticker=item.get("ticker"),
                strategy_version_id=item.get("strategy_version_id"),
                target_skill_code=details.get("target_skill_code"),
                claim_key=details.get("claim_key"),
            )
            current = grouped.get(cluster_key)
            if current is None:
                grouped[cluster_key] = {
                    "cluster_key": cluster_key,
                    "disagreement_type": item.get("disagreement_type"),
                    "entity_type": item.get("entity_type"),
                    "ticker": item.get("ticker"),
                    "strategy_version_id": item.get("strategy_version_id"),
                    "target_skill_code": details.get("target_skill_code"),
                    "claim_key": details.get("claim_key"),
                    "event_count": 1,
                    "last_seen_at": item.get("created_at"),
                    "sample_summaries": [item.get("summary")] if item.get("summary") else [],
                    "source_memory_ids": [item.get("id")] if item.get("id") is not None else [],
                    "source_journal_ids": [item.get("journal_entry_id")] if item.get("journal_entry_id") is not None else [],
                }
                continue
            current["event_count"] += 1
            if cls._is_newer(item.get("created_at"), current.get("last_seen_at")):
                current["last_seen_at"] = item.get("created_at")
            if item.get("summary") and item["summary"] not in current["sample_summaries"] and len(current["sample_summaries"]) < 3:
                current["sample_summaries"].append(item["summary"])
            if item.get("id") is not None:
                current["source_memory_ids"].append(item["id"])
            if item.get("journal_entry_id") is not None:
                current["source_journal_ids"].append(item["journal_entry_id"])
        rows = list(grouped.values())
        rows.sort(key=lambda item: (int(item.get("event_count") or 0), item.get("last_seen_at") or datetime.min.replace(tzinfo=UTC)), reverse=True)
        return rows

    @staticmethod
    def _cluster_key(
        *,
        disagreement_type: object,
        entity_type: object,
        ticker: object,
        strategy_version_id: object,
        target_skill_code: object,
        claim_key: object,
    ) -> str:
        parts = [
            str(disagreement_type or "").strip().lower() or "unknown",
            str(entity_type or "").strip().lower() or "unknown",
            str(ticker or "").strip().upper() or "global",
            str(strategy_version_id or "").strip() or "none",
            str(target_skill_code or "").strip().lower() or "no-skill",
            str(claim_key or "").strip().lower() or "no-claim",
        ]
        return ("operator_disagreement_cluster:" + ":".join(parts))[:120]

    @classmethod
    def _bucketize(cls, items: list[dict], key_fn) -> list[dict]:
        buckets: dict[str, dict] = {}
        for item in items:
            label = str(key_fn(item) or "").strip()
            if not label:
                continue
            current = buckets.get(label)
            created_at = item.get("created_at")
            if current is None:
                buckets[label] = {
                    "label": label,
                    "count": 1,
                    "last_seen_at": created_at,
                }
                continue
            current["count"] += 1
            if cls._is_newer(created_at, current.get("last_seen_at")):
                current["last_seen_at"] = created_at
        rows = list(buckets.values())
        rows.sort(key=lambda item: (int(item.get("count") or 0), item.get("last_seen_at") or datetime.min.replace(tzinfo=UTC)), reverse=True)
        return rows[:8]

    @staticmethod
    def _is_newer(left: object, right: object) -> bool:
        if isinstance(left, datetime) and isinstance(right, datetime):
            return left > right
        return isinstance(left, datetime) and right is None

    @staticmethod
    def _coerce_datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _as_dict(value: object) -> dict:
        return value if isinstance(value, dict) else {}
