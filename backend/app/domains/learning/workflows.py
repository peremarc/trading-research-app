from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models.learning_workflow import LearningWorkflow
from app.domains.learning.claims import KnowledgeClaimService
from app.domains.learning.repositories import JournalRepository
from app.domains.learning.schemas import (
    JournalEntryCreate,
    LearningWorkflowHistoryEntryRead,
    LearningWorkflowRead,
)
from app.domains.learning.skills import SkillGapService, SkillLifecycleService


@dataclass
class LearningWorkflowSyncReport:
    workflows: list[LearningWorkflow]
    workflow_count: int
    open_workflow_count: int
    open_item_count: int
    changed_workflow_count: int
    opened_workflow_count: int
    resolved_workflow_count: int
    changes: list[dict]
    summary: str


class LearningWorkflowService:
    def __init__(
        self,
        claim_service: KnowledgeClaimService | None = None,
        skill_gap_service: SkillGapService | None = None,
        skill_lifecycle_service: SkillLifecycleService | None = None,
        journal_repository: JournalRepository | None = None,
    ) -> None:
        self.claim_service = claim_service or KnowledgeClaimService()
        self.skill_gap_service = skill_gap_service or SkillGapService()
        self.skill_lifecycle_service = skill_lifecycle_service or SkillLifecycleService()
        self.journal_repository = journal_repository or JournalRepository()

    def list_workflows(
        self,
        session: Session,
        *,
        limit: int = 20,
        include_resolved: bool = True,
        sync: bool = False,
    ) -> list[LearningWorkflow]:
        if sync:
            self.sync_default_workflows(session)
        statement = select(LearningWorkflow).order_by(
            desc(LearningWorkflow.last_synced_at),
            desc(LearningWorkflow.updated_at),
            desc(LearningWorkflow.id),
        )
        if not include_resolved:
            statement = statement.where(LearningWorkflow.status != "resolved")
        statement = statement.limit(max(1, min(int(limit), 100)))
        return list(session.scalars(statement).all())

    def get_workflow(self, session: Session, *, workflow_id: int) -> LearningWorkflow | None:
        return session.get(LearningWorkflow, workflow_id)

    def to_read_model(self, workflow: LearningWorkflow, *, history_limit: int = 6) -> LearningWorkflowRead:
        payload = {
            "id": workflow.id,
            "workflow_type": workflow.workflow_type,
            "scope": workflow.scope,
            "title": workflow.title,
            "status": workflow.status,
            "priority": workflow.priority,
            "summary": workflow.summary,
            "context": dict(workflow.context or {}),
            "items": list(workflow.items or []),
            "item_count": int(workflow.item_count or 0),
            "open_item_count": int(workflow.open_item_count or 0),
            "created_at": workflow.created_at,
            "updated_at": workflow.updated_at,
            "last_synced_at": workflow.last_synced_at,
            "resolved_at": workflow.resolved_at,
            "history": [entry.model_dump() for entry in self._build_history_entries(workflow, limit=history_limit)],
        }
        return LearningWorkflowRead.model_validate(payload)

    def sync_default_workflows(self, session: Session) -> list[LearningWorkflow]:
        return [
            self.sync_stale_claim_review(session),
            self.sync_weekly_skill_audit(session),
        ]

    def sync_default_workflows_with_report(self, session: Session) -> LearningWorkflowSyncReport:
        existing = {
            (item.workflow_type, item.scope): {
                "status": str(item.status or "").strip().lower() or "open",
                "open_item_count": int(item.open_item_count or 0),
                "item_count": int(item.item_count or 0),
                "priority": str(item.priority or "").strip().lower() or "normal",
                "summary": str(item.summary or "").strip(),
            }
            for item in session.scalars(select(LearningWorkflow)).all()
        }
        workflows = self.sync_default_workflows(session)
        changes: list[dict] = []
        opened_workflows = 0
        resolved_workflows = 0

        for workflow in workflows:
            key = (workflow.workflow_type, workflow.scope)
            previous = existing.get(key)
            current_status = str(workflow.status or "").strip().lower() or "open"
            current_open_items = int(workflow.open_item_count or 0)
            current_item_count = int(workflow.item_count or 0)
            current_priority = str(workflow.priority or "").strip().lower() or "normal"
            current_summary = str(workflow.summary or "").strip()
            previous_status = str((previous or {}).get("status") or "").strip().lower() or None
            changed = (
                previous is None
                or previous_status != current_status
                or int((previous or {}).get("open_item_count") or 0) != current_open_items
                or int((previous or {}).get("item_count") or 0) != current_item_count
                or str((previous or {}).get("priority") or "").strip().lower() != current_priority
                or str((previous or {}).get("summary") or "").strip() != current_summary
            )
            if not changed:
                continue
            if current_status != "resolved" and previous_status in {None, "resolved"}:
                opened_workflows += 1
            if current_status == "resolved" and previous_status not in {None, "resolved"}:
                resolved_workflows += 1
            changes.append(
                self._json_ready(
                    {
                        "workflow_type": workflow.workflow_type,
                        "scope": workflow.scope,
                        "previous_status": previous_status,
                        "status": current_status,
                        "previous_open_item_count": int((previous or {}).get("open_item_count") or 0),
                        "open_item_count": current_open_items,
                        "item_count": current_item_count,
                        "priority": current_priority,
                        "summary": current_summary,
                    }
                )
            )

        workflow_count = len(workflows)
        open_workflow_count = sum(
            1 for workflow in workflows if str(workflow.status or "").strip().lower() != "resolved"
        )
        open_item_count = sum(int(workflow.open_item_count or 0) for workflow in workflows)
        summary = (
            f"Synced {workflow_count} learning workflows; "
            f"{open_workflow_count} remain open with {open_item_count} open items; "
            f"{len(changes)} workflows changed."
        )
        return LearningWorkflowSyncReport(
            workflows=workflows,
            workflow_count=workflow_count,
            open_workflow_count=open_workflow_count,
            open_item_count=open_item_count,
            changed_workflow_count=len(changes),
            opened_workflow_count=opened_workflows,
            resolved_workflow_count=resolved_workflows,
            changes=changes,
            summary=summary,
        )

    def apply_action(
        self,
        session: Session,
        *,
        workflow_id: int,
        item_type: str,
        entity_id: int,
        action: str,
        summary: str,
    ) -> tuple[LearningWorkflow, dict]:
        workflow = session.get(LearningWorkflow, workflow_id)
        if workflow is None:
            raise ValueError("Learning workflow not found.")

        normalized_item_type = str(item_type or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        summary_text = str(summary or "").strip()
        if not summary_text:
            raise ValueError("Workflow action summary is required.")

        effect: dict
        if workflow.workflow_type == "stale_claim_review" and normalized_item_type == "claim_review":
            if normalized_action not in {"confirm", "contradict", "retire"}:
                raise ValueError("Claim review workflow action must be confirm, contradict or retire.")
            claim, evidence, promoted_skill_candidate = self.claim_service.review_claim(
                session,
                claim_id=entity_id,
                outcome=normalized_action,
                summary=summary_text,
                source_key=f"workflow:{workflow.id}:claim:{entity_id}:{normalized_action}",
                strength=0.8 if normalized_action == "contradict" else 0.65,
                evidence_payload={"source": "learning_workflow", "workflow_id": workflow.id},
            )
            effect = {
                "entity_type": "knowledge_claim",
                "claim_id": entity_id,
                "claim_status": claim.status,
                "claim_freshness_state": claim.freshness_state,
                "ticker": getattr(claim, "linked_ticker", None),
                "strategy_version_id": getattr(claim, "strategy_version_id", None),
                "promoted_skill_candidate": promoted_skill_candidate,
                "evidence_id": getattr(evidence, "id", None) if evidence is not None else None,
            }
            resolution_class, resolution_outcome = self._classify_action(
                item_type=normalized_item_type,
                action=normalized_action,
            )
            effect["resolution_class"] = resolution_class
            effect["resolution_outcome"] = resolution_outcome
            self._record_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
            )
            refreshed = self.sync_stale_claim_review(session)
            return refreshed, effect

        if workflow.workflow_type == "weekly_skill_audit" and normalized_item_type == "skill_gap":
            if normalized_action not in {"resolve", "dismiss"}:
                raise ValueError("Skill gap audit action must be resolve or dismiss.")
            gap = self.skill_gap_service.review_gap(
                session,
                gap_id=entity_id,
                outcome=normalized_action,
                summary=summary_text,
            )
            effect = {
                "entity_type": "skill_gap",
                "gap_id": entity_id,
                "gap_status": gap.get("status"),
                "ticker": gap.get("ticker"),
                "strategy_version_id": gap.get("strategy_version_id"),
            }
            resolution_class, resolution_outcome = self._classify_action(
                item_type=normalized_item_type,
                action=normalized_action,
            )
            effect["resolution_class"] = resolution_class
            effect["resolution_outcome"] = resolution_outcome
            self._record_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
            )
            refreshed = self.sync_weekly_skill_audit(session)
            return refreshed, effect

        if workflow.workflow_type == "weekly_skill_audit" and normalized_item_type == "skill_candidate_audit":
            validation_mapping = {
                "paper_approve": ("paper", "approve"),
                "replay_approve": ("replay", "approve"),
                "reject": ("paper", "reject"),
            }
            if normalized_action not in validation_mapping:
                raise ValueError("Skill candidate audit action must be paper_approve, replay_approve or reject.")
            validation_mode, validation_outcome = validation_mapping[normalized_action]
            result = self.skill_lifecycle_service.validate_candidate(
                session,
                candidate_id=entity_id,
                validation_mode=validation_mode,
                validation_outcome=validation_outcome,
                summary=summary_text,
                activate=validation_outcome == "approve",
            )
            effect = {
                "entity_type": "skill_candidate",
                "candidate_id": entity_id,
                "activation_status": result.get("activation_status"),
                "candidate_status": (result.get("candidate") or {}).get("candidate_status"),
                "revision_id": (result.get("revision") or {}).get("id"),
                "ticker": (result.get("candidate") or {}).get("ticker"),
                "strategy_version_id": (result.get("candidate") or {}).get("strategy_version_id"),
            }
            resolution_class, resolution_outcome = self._classify_action(
                item_type=normalized_item_type,
                action=normalized_action,
            )
            effect["resolution_class"] = resolution_class
            effect["resolution_outcome"] = resolution_outcome
            self._record_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
            )
            refreshed = self.sync_weekly_skill_audit(session)
            return refreshed, effect

        raise ValueError("Unsupported workflow action for this workflow type.")

    def sync_stale_claim_review(self, session: Session) -> LearningWorkflow:
        queue_items = self.claim_service.list_review_queue(session, limit=100)
        items = [
            {
                "item_type": "claim_review",
                "entity_id": item.get("claim_id"),
                "title": item.get("claim_text") or item.get("review_reason") or "Claim review",
                "status": "pending_review",
                "priority": self._map_priority(item.get("review_priority")),
                "action_hint": item.get("review_reason"),
                "payload": dict(item),
            }
            for item in queue_items
        ]
        counts_by_reason: dict[str, int] = {}
        counts_by_freshness: dict[str, int] = {}
        for item in queue_items:
            reason = str(item.get("review_reason") or "unknown")
            freshness = str(item.get("freshness_state") or "unknown")
            counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1
            counts_by_freshness[freshness] = counts_by_freshness.get(freshness, 0) + 1

        summary = (
            f"{len(queue_items)} durable claims need explicit review."
            if queue_items
            else "No durable claims currently require explicit review."
        )
        priority = "high" if any(int(item.get("review_priority") or 0) >= 3 for item in queue_items) else "normal"
        return self._upsert_workflow(
            session,
            workflow_type="stale_claim_review",
            scope="global",
            title="Stale Claim Review",
            status="open" if items else "resolved",
            priority=priority,
            summary=summary,
            context={
                "workflow_family": "learning_review",
                "counts_by_reason": counts_by_reason,
                "counts_by_freshness": counts_by_freshness,
            },
            items=items,
        )

    def sync_weekly_skill_audit(self, session: Session) -> LearningWorkflow:
        gaps = [item for item in self.skill_gap_service.list_gaps(session, limit=100) if item.get("status") == "open"]
        dashboard = self.skill_lifecycle_service.build_dashboard(session)
        draft_candidates = [
            item for item in (dashboard.get("candidates") if isinstance(dashboard, dict) else []) or []
            if item.get("candidate_status") == "draft"
        ]
        active_revisions = (dashboard.get("active_revisions") if isinstance(dashboard, dict) else []) or []

        items: list[dict] = []
        for gap in gaps:
            items.append(
                {
                    "item_type": "skill_gap",
                    "entity_id": gap.get("id"),
                    "title": gap.get("summary") or gap.get("gap_type") or "Skill gap",
                    "status": gap.get("status") or "open",
                    "priority": "high" if gap.get("gap_type") == "missing_catalog_skill" else "normal",
                    "action_hint": gap.get("gap_type"),
                    "payload": dict(gap),
                }
            )
        for candidate in draft_candidates[:20]:
            items.append(
                {
                    "item_type": "skill_candidate_audit",
                    "entity_id": candidate.get("id"),
                    "title": candidate.get("summary") or candidate.get("target_skill_code") or "Skill candidate audit",
                    "status": candidate.get("candidate_status") or "draft",
                    "priority": "normal",
                    "action_hint": candidate.get("candidate_action"),
                    "payload": dict(candidate),
                }
            )

        summary = (
            f"{len(gaps)} unresolved skill gaps and {len(draft_candidates)} draft skill candidates need audit."
            if items
            else "No unresolved skill gaps or draft skill candidates require weekly audit."
        )
        priority = "high" if gaps else "normal"
        return self._upsert_workflow(
            session,
            workflow_type="weekly_skill_audit",
            scope="global",
            title="Weekly Skill Audit",
            status="open" if items else "resolved",
            priority=priority,
            summary=summary,
            context={
                "workflow_family": "learning_audit",
                "open_skill_gap_count": len(gaps),
                "draft_skill_candidate_count": len(draft_candidates),
                "active_revision_count": len(active_revisions),
            },
            items=items,
        )

    def _upsert_workflow(
        self,
        session: Session,
        *,
        workflow_type: str,
        scope: str,
        title: str,
        status: str,
        priority: str,
        summary: str,
        context: dict,
        items: list[dict],
    ) -> LearningWorkflow:
        statement = select(LearningWorkflow).where(
            LearningWorkflow.workflow_type == workflow_type,
            LearningWorkflow.scope == scope,
        )
        workflow = session.scalars(statement).first()
        now = datetime.now(UTC)
        previous_items = list(workflow.items or []) if workflow is not None and isinstance(workflow.items, list) else []
        previous_status = str(workflow.status or "").strip().lower() if workflow is not None else None
        previous_summary = str(workflow.summary or "").strip() if workflow is not None else ""
        if workflow is None:
            workflow = LearningWorkflow(
                workflow_type=workflow_type,
                scope=scope,
                title=title,
            )
            session.add(workflow)
            session.flush()

        persisted_context = dict(workflow.context or {})
        workflow.title = title
        workflow.status = self._merge_status(status=status, persisted_context=persisted_context)
        workflow.priority = priority
        workflow.summary = summary
        workflow.context = self._merge_context(persisted_context=persisted_context, new_context=dict(context or {}))
        normalized_items = self._json_ready(list(items or []))
        workflow.items = normalized_items
        workflow.item_count = len(workflow.items)
        workflow.open_item_count = sum(
            1 for item in workflow.items if str(item.get("status") or "").strip().lower() not in {"resolved", "retired"}
        )
        workflow.last_synced_at = now
        workflow.resolved_at = now if status == "resolved" else None
        sync_entry = self._build_sync_history_entry(
            timestamp=now,
            workflow_type=workflow.workflow_type,
            previous_status=previous_status,
            status=str(workflow.status or "").strip().lower() or "open",
            previous_items=previous_items,
            current_items=normalized_items,
            previous_summary=previous_summary,
            summary=str(summary or "").strip(),
            open_item_count=int(workflow.open_item_count or 0),
        )
        if sync_entry is not None:
            context = dict(workflow.context or {})
            sync_log = list(context.get("sync_log") or [])
            sync_log.append(sync_entry)
            context["sync_log"] = sync_log[-30:]
            workflow.context = self._json_ready(context)
        session.add(workflow)
        session.commit()
        session.refresh(workflow)
        return workflow

    def _record_action(
        self,
        session: Session,
        *,
        workflow: LearningWorkflow,
        item_type: str,
        entity_id: int,
        action: str,
        summary: str,
        effect: dict,
    ) -> None:
        context = dict(workflow.context or {})
        logs = list(context.get("resolution_log") or [])
        now = datetime.now(UTC).isoformat()
        resolution_class, resolution_outcome = self._classify_action(item_type=item_type, action=action)
        logs.append(
            self._json_ready(
                {
                    "timestamp": now,
                    "event_type": "action",
                    "item_type": item_type,
                    "entity_id": entity_id,
                    "action": action,
                    "resolution_class": resolution_class,
                    "resolution_outcome": resolution_outcome,
                    "summary": summary,
                    "effect": dict(effect or {}),
                }
            )
        )
        context["resolution_log"] = logs[-30:]
        action_counts = dict(context.get("action_counts") or {})
        action_counts[action] = int(action_counts.get(action) or 0) + 1
        context["action_counts"] = action_counts
        context["last_action_at"] = now
        context["last_action"] = {
            "item_type": item_type,
            "entity_id": entity_id,
            "action": action,
            "summary": summary,
        }
        workflow.context = self._json_ready(context)
        if workflow.status != "resolved":
            workflow.status = "in_progress"
        session.add(workflow)
        session.commit()
        session.refresh(workflow)
        self.journal_repository.create(
            session,
            JournalEntryCreate(
                entry_type="learning_workflow_action",
                ticker=effect.get("ticker"),
                strategy_version_id=effect.get("strategy_version_id"),
                market_context={
                    "workflow_governance": True,
                    "workflow_id": workflow.id,
                    "workflow_type": workflow.workflow_type,
                },
                observations={
                    "workflow_id": workflow.id,
                    "workflow_type": workflow.workflow_type,
                    "item_type": item_type,
                    "entity_id": entity_id,
                    "action": action,
                    "resolution_class": resolution_class,
                    "resolution_outcome": resolution_outcome,
                    "effect": self._json_ready(dict(effect or {})),
                },
                reasoning=f"Workflow action {action} applied to {item_type} {entity_id}.",
                decision="learning_workflow_action",
                outcome=summary,
            ),
        )

    @classmethod
    def _json_ready(cls, value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): cls._json_ready(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._json_ready(item) for item in value]
        if isinstance(value, tuple):
            return [cls._json_ready(item) for item in value]
        return value

    def _merge_context(self, *, persisted_context: dict, new_context: dict) -> dict:
        merged = dict(new_context or {})
        for key in ("resolution_log", "action_counts", "last_action", "last_action_at", "sync_log"):
            if key in persisted_context:
                merged[key] = self._json_ready(persisted_context[key])
        return self._json_ready(merged)

    @staticmethod
    def _merge_status(*, status: str, persisted_context: dict) -> str:
        normalized_status = str(status or "").strip().lower() or "open"
        if normalized_status == "resolved":
            return "resolved"
        resolution_log = persisted_context.get("resolution_log")
        if isinstance(resolution_log, list) and resolution_log:
            return "in_progress"
        return normalized_status

    @staticmethod
    def _map_priority(value: object) -> str:
        try:
            numeric = int(value or 0)
        except (TypeError, ValueError):
            numeric = 0
        if numeric >= 3:
            return "high"
        if numeric == 2:
            return "normal"
        return "low"

    def _build_history_entries(self, workflow: LearningWorkflow, *, limit: int = 6) -> list[LearningWorkflowHistoryEntryRead]:
        context = dict(workflow.context or {})
        history = []
        for collection_name in ("resolution_log", "sync_log"):
            for item in list(context.get(collection_name) or []):
                normalized = self._normalize_history_entry(item)
                if normalized is not None:
                    history.append(normalized)
        history.sort(key=lambda item: item.timestamp, reverse=True)
        return history[: max(1, min(int(limit), 50))]

    def _normalize_history_entry(self, payload: dict | None) -> LearningWorkflowHistoryEntryRead | None:
        if not isinstance(payload, dict):
            return None
        timestamp_raw = payload.get("timestamp")
        if not timestamp_raw:
            return None
        try:
            timestamp = datetime.fromisoformat(str(timestamp_raw))
        except ValueError:
            return None
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            summary = str(payload.get("change_class") or payload.get("resolution_class") or "workflow_event").strip()
        return LearningWorkflowHistoryEntryRead.model_validate(
            {
                "timestamp": timestamp,
                "event_type": payload.get("event_type") or "unknown",
                "summary": summary,
                "change_class": payload.get("change_class"),
                "resolution_class": payload.get("resolution_class"),
                "resolution_outcome": payload.get("resolution_outcome"),
                "item_type": payload.get("item_type"),
                "entity_id": payload.get("entity_id"),
                "action": payload.get("action"),
                "status_before": payload.get("status_before"),
                "status_after": payload.get("status_after"),
                "open_item_count_after": payload.get("open_item_count_after"),
                "added_items": list(payload.get("added_items") or []),
                "removed_items": list(payload.get("removed_items") or []),
                "effect": dict(payload.get("effect") or {}),
            }
        )

    @classmethod
    def _build_sync_history_entry(
        cls,
        *,
        timestamp: datetime,
        workflow_type: str,
        previous_status: str | None,
        status: str,
        previous_items: list[dict],
        current_items: list[dict],
        previous_summary: str,
        summary: str,
        open_item_count: int,
    ) -> dict | None:
        previous_index = cls._index_items(previous_items)
        current_index = cls._index_items(current_items)
        added_items = [
            cls._item_history_label(item)
            for key, item in current_index.items()
            if key not in previous_index
        ]
        removed_items = [
            cls._item_history_label(item)
            for key, item in previous_index.items()
            if key not in current_index
        ]
        changed_status_items = [
            cls._item_history_label(current_index[key])
            for key, item in current_index.items()
            if key in previous_index
            and str(item.get("status") or "").strip().lower()
            != str(previous_index[key].get("status") or "").strip().lower()
        ]
        normalized_previous = str(previous_status or "").strip().lower() or None
        normalized_current = str(status or "").strip().lower() or "open"
        changed = (
            normalized_previous is None
            or normalized_previous != normalized_current
            or added_items
            or removed_items
            or changed_status_items
            or str(previous_summary or "").strip() != str(summary or "").strip()
        )
        if not changed:
            return None
        if normalized_previous in {None, "resolved"} and normalized_current != "resolved":
            change_class = "workflow_opened"
        elif normalized_current == "resolved" and normalized_previous not in {None, "resolved"}:
            change_class = "workflow_resolved"
        elif added_items and not removed_items:
            change_class = "items_added"
        elif removed_items and not added_items:
            change_class = "items_removed"
        else:
            change_class = "workflow_refreshed"
        detail_bits = []
        if added_items:
            detail_bits.append(f"+{len(added_items)}")
        if removed_items:
            detail_bits.append(f"-{len(removed_items)}")
        if changed_status_items:
            detail_bits.append(f"~{len(changed_status_items)}")
        detail = " ".join(detail_bits)
        normalized_summary = str(summary or "").strip() or f"{workflow_type} synced"
        if detail:
            normalized_summary = f"{normalized_summary} ({detail})"
        return cls._json_ready(
            {
                "timestamp": timestamp,
                "event_type": "sync",
                "change_class": change_class,
                "summary": normalized_summary,
                "status_before": normalized_previous,
                "status_after": normalized_current,
                "open_item_count_after": int(open_item_count or 0),
                "added_items": added_items[:5],
                "removed_items": removed_items[:5],
                "effect": {
                    "changed_status_items": changed_status_items[:5],
                },
            }
        )

    @staticmethod
    def _index_items(items: list[dict]) -> dict[tuple[str, int | None], dict]:
        indexed: dict[tuple[str, int | None], dict] = {}
        for item in list(items or []):
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("item_type") or "").strip().lower(),
                int(item.get("entity_id")) if item.get("entity_id") is not None else None,
            )
            indexed[key] = item
        return indexed

    @staticmethod
    def _item_history_label(item: dict) -> str:
        title = str(item.get("title") or "").strip()
        item_type = str(item.get("item_type") or "item").strip()
        entity_id = item.get("entity_id")
        if title:
            return title
        if entity_id is not None:
            return f"{item_type}:{entity_id}"
        return item_type

    @staticmethod
    def _classify_action(*, item_type: str, action: str) -> tuple[str, str]:
        normalized_item_type = str(item_type or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        mapping = {
            ("claim_review", "confirm"): ("claim_confirmed", "accepted"),
            ("claim_review", "contradict"): ("claim_contradicted", "rejected"),
            ("claim_review", "retire"): ("claim_retired", "retired"),
            ("skill_gap", "resolve"): ("gap_resolved", "accepted"),
            ("skill_gap", "dismiss"): ("gap_dismissed", "dismissed"),
            ("skill_candidate_audit", "paper_approve"): ("candidate_approved_paper", "accepted"),
            ("skill_candidate_audit", "replay_approve"): ("candidate_approved_replay", "accepted"),
            ("skill_candidate_audit", "reject"): ("candidate_rejected", "rejected"),
        }
        return mapping.get((normalized_item_type, normalized_action), ("workflow_action", "accepted"))
