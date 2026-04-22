from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models.memory import MemoryItem
from app.db.models.market_state_snapshot import MarketStateSnapshotRecord
from app.db.models.learning_workflow import LearningWorkflow, LearningWorkflowArtifact, LearningWorkflowRun
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.watchlist import Watchlist
from app.domains.learning.claims import ClaimEvidenceSeed, ClaimSeed, KnowledgeClaimService
from app.domains.learning.repositories import JournalRepository
from app.domains.learning.schemas import (
    JournalEntryCreate,
    KnowledgeClaimCreate,
    LearningWorkflowArtifactRead,
    LearningWorkflowHistoryEntryRead,
    LearningWorkflowRead,
    LearningWorkflowRunRead,
    LearningWorkflowSkillCandidateCreate,
    LearningWorkflowSkillGapCreate,
    MemoryItemCreate,
)
from app.domains.learning.services import MemoryService
from app.domains.learning.skills import (
    SKILL_CANDIDATE_MEMORY_TYPE,
    SKILL_GAP_MEMORY_TYPE,
    SkillGapService,
    SkillLifecycleService,
)
from app.domains.learning.world_state import MarketStateService
from app.domains.market.schemas import ResearchTaskCreate
from app.domains.market.services import ResearchService
from app.domains.system.market_hours import USMarketHoursService


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
        market_state_service: MarketStateService | None = None,
        market_hours_service: USMarketHoursService | None = None,
        research_service: ResearchService | None = None,
        memory_service: MemoryService | None = None,
    ) -> None:
        self.claim_service = claim_service or KnowledgeClaimService()
        self.skill_gap_service = skill_gap_service or SkillGapService()
        self.skill_lifecycle_service = skill_lifecycle_service or SkillLifecycleService()
        self.journal_repository = journal_repository or JournalRepository()
        self.market_state_service = market_state_service or MarketStateService()
        self.market_hours_service = market_hours_service or USMarketHoursService()
        self.research_service = research_service or ResearchService()
        self.memory_service = memory_service or MemoryService()

    def list_workflows(
        self,
        session: Session,
        *,
        limit: int = 20,
        include_resolved: bool = True,
        sync: bool = False,
        sync_trigger_source: str = "api_list_sync",
    ) -> list[LearningWorkflow]:
        if sync:
            self.sync_default_workflows(session, trigger_source=sync_trigger_source)
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

    def to_read_model(
        self,
        session: Session,
        workflow: LearningWorkflow,
        *,
        history_limit: int = 6,
        run_limit: int = 2,
        artifact_limit: int = 4,
    ) -> LearningWorkflowRead:
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
            "recent_runs": [
                self._build_run_read_model(
                    session,
                    run,
                    artifact_limit=artifact_limit,
                ).model_dump()
                for run in self._list_recent_runs(session, workflow_id=workflow.id, limit=run_limit)
            ],
        }
        return LearningWorkflowRead.model_validate(payload)

    def sync_default_workflows(
        self,
        session: Session,
        *,
        record_run: bool = True,
        trigger_source: str = "manual_sync",
    ) -> list[LearningWorkflow]:
        return [
            self.sync_stale_claim_review(session, record_run=record_run, trigger_source=trigger_source),
            self.sync_weekly_skill_audit(session, record_run=record_run, trigger_source=trigger_source),
            self.sync_premarket_review(session, record_run=record_run, trigger_source=trigger_source),
            self.sync_postmarket_review(session, record_run=record_run, trigger_source=trigger_source),
            self.sync_regime_shift_review(session, record_run=record_run, trigger_source=trigger_source),
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
        workflows = self.sync_default_workflows(session, trigger_source="scheduler_governance")
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
        claims: list[KnowledgeClaimCreate] | None = None,
        research_tasks: list[ResearchTaskCreate] | None = None,
        skill_gaps: list[LearningWorkflowSkillGapCreate] | None = None,
        skill_candidates: list[LearningWorkflowSkillCandidateCreate] | None = None,
    ) -> tuple[LearningWorkflow, dict]:
        workflow = session.get(LearningWorkflow, workflow_id)
        if workflow is None:
            raise ValueError("Learning workflow not found.")

        normalized_item_type = str(item_type or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        summary_text = str(summary or "").strip()
        if not summary_text:
            raise ValueError("Workflow action summary is required.")
        claim_outputs = list(claims or [])
        research_task_outputs = list(research_tasks or [])
        skill_gap_outputs = list(skill_gaps or [])
        skill_candidate_outputs = list(skill_candidates or [])

        effect: dict
        if workflow.workflow_type == "premarket_review" and normalized_item_type == "premarket_checklist":
            if normalized_action != "complete":
                raise ValueError("Premarket review workflow action must be complete.")
            effect = self._complete_checklist_cycle(
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
            )
            return self._finalize_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
                claims=claim_outputs,
                research_tasks=research_task_outputs,
                skill_gaps=skill_gap_outputs,
                skill_candidates=skill_candidate_outputs,
                refresh_workflow=lambda: self.sync_premarket_review(session, record_run=False),
            )

        if workflow.workflow_type == "postmarket_review" and normalized_item_type == "postmarket_checklist":
            if normalized_action != "complete":
                raise ValueError("Postmarket review workflow action must be complete.")
            effect = self._complete_checklist_cycle(
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
            )
            return self._finalize_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
                claims=claim_outputs,
                research_tasks=research_task_outputs,
                skill_gaps=skill_gap_outputs,
                skill_candidates=skill_candidate_outputs,
                refresh_workflow=lambda: self.sync_postmarket_review(session, record_run=False),
            )

        if workflow.workflow_type == "regime_shift_review" and normalized_item_type == "regime_shift":
            if normalized_action != "complete":
                raise ValueError("Regime shift review workflow action must be complete.")
            effect = self._complete_checklist_cycle(
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
            )
            return self._finalize_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
                claims=claim_outputs,
                research_tasks=research_task_outputs,
                skill_gaps=skill_gap_outputs,
                skill_candidates=skill_candidate_outputs,
                refresh_workflow=lambda: self.sync_regime_shift_review(session, record_run=False),
            )

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
            return self._finalize_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
                claims=claim_outputs,
                research_tasks=research_task_outputs,
                skill_gaps=skill_gap_outputs,
                skill_candidates=skill_candidate_outputs,
                refresh_workflow=lambda: self.sync_stale_claim_review(session, record_run=False),
            )

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
            return self._finalize_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
                claims=claim_outputs,
                research_tasks=research_task_outputs,
                skill_gaps=skill_gap_outputs,
                skill_candidates=skill_candidate_outputs,
                refresh_workflow=lambda: self.sync_weekly_skill_audit(session, record_run=False),
            )

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
            return self._finalize_action(
                session,
                workflow=workflow,
                item_type=normalized_item_type,
                entity_id=entity_id,
                action=normalized_action,
                summary=summary_text,
                effect=effect,
                claims=claim_outputs,
                research_tasks=research_task_outputs,
                skill_gaps=skill_gap_outputs,
                skill_candidates=skill_candidate_outputs,
                refresh_workflow=lambda: self.sync_weekly_skill_audit(session, record_run=False),
            )

        raise ValueError("Unsupported workflow action for this workflow type.")

    def sync_stale_claim_review(
        self,
        session: Session,
        *,
        record_run: bool = True,
        trigger_source: str = "manual_sync",
    ) -> LearningWorkflow:
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
            record_run=record_run,
            trigger_source=trigger_source,
        )

    def sync_weekly_skill_audit(
        self,
        session: Session,
        *,
        record_run: bool = True,
        trigger_source: str = "manual_sync",
    ) -> LearningWorkflow:
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
            record_run=record_run,
            trigger_source=trigger_source,
        )

    def sync_premarket_review(
        self,
        session: Session,
        *,
        record_run: bool = True,
        trigger_source: str = "manual_sync",
    ) -> LearningWorkflow:
        session_state = self.market_hours_service.get_session_state()
        session_payload = session_state.to_payload()
        review_date = self._review_date_from_session_payload(session_payload)
        review_key = f"premarket:{review_date}"
        entity_id = self._review_entity_id(review_date)
        existing_context = self._existing_workflow_context(session, workflow_type="premarket_review", scope="global")

        open_positions = list(
            session.scalars(
                select(Position)
                .where(Position.status == "open")
                .order_by(desc(Position.entry_date), desc(Position.id))
                .limit(5)
            ).all()
        )
        active_watchlists = list(
            session.scalars(
                select(Watchlist)
                .where(Watchlist.status == "active")
                .order_by(desc(Watchlist.id))
                .limit(5)
            ).all()
        )
        open_research_tasks = int(
            session.query(ResearchTask).filter(ResearchTask.status.in_(("open", "in_progress"))).count()
        )
        pending_review_count = int(
            session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        )
        latest_snapshot = self.market_state_service.get_latest_snapshot(session)
        open_positions_count = int(session.query(Position).filter(Position.status == "open").count())
        active_watchlists_count = int(session.query(Watchlist).filter(Watchlist.status == "active").count())
        focus_tickers = self._collect_focus_tickers(open_positions=open_positions, active_watchlists=active_watchlists)

        review_needed = (
            session_state.is_trading_day
            and session_payload.get("session_label") == "pre_market"
            and (
                open_positions_count > 0
                or active_watchlists_count > 0
                or open_research_tasks > 0
                or pending_review_count > 0
                or latest_snapshot is not None
            )
        )
        already_completed = self._review_cycle_completed(existing_context, review_key=review_key)
        items: list[dict] = []

        if review_needed and not already_completed:
            items.append(
                {
                    "item_type": "premarket_checklist",
                    "entity_id": entity_id,
                    "title": (
                        f"Premarket posture for {review_date}: {open_positions_count} open positions, "
                        f"{active_watchlists_count} active watchlists, {open_research_tasks} open research tasks."
                    ),
                    "status": "pending_review",
                    "priority": "high" if pending_review_count > 0 or open_positions_count > 0 else "normal",
                    "action_hint": "complete",
                    "payload": {
                        "review_key": review_key,
                        "review_date": review_date,
                        "market_session": session_payload.get("session_label"),
                        "open_positions_count": open_positions_count,
                        "active_watchlists_count": active_watchlists_count,
                        "open_research_tasks": open_research_tasks,
                        "pending_review_count": pending_review_count,
                        "focus_tickers": focus_tickers,
                        "latest_market_snapshot": self._market_snapshot_summary(latest_snapshot),
                    },
                }
            )

        if review_needed and already_completed:
            summary = f"Premarket review for {review_date} already completed."
        elif review_needed:
            summary = (
                f"Premarket review due for {review_date}: {open_positions_count} open positions, "
                f"{active_watchlists_count} active watchlists, {open_research_tasks} open research tasks "
                f"and {pending_review_count} pending post-trade reviews."
            )
        else:
            summary = "No premarket review is currently pending."

        return self._upsert_workflow(
            session,
            workflow_type="premarket_review",
            scope="global",
            title="Premarket Review",
            status="open" if items else "resolved",
            priority="high" if items else "normal",
            summary=summary,
            context={
                "workflow_family": "session_review",
                "review_key": review_key,
                "review_date": review_date,
                "market_session": session_payload,
                "open_positions_count": open_positions_count,
                "active_watchlists_count": active_watchlists_count,
                "open_research_tasks": open_research_tasks,
                "pending_review_count": pending_review_count,
                "focus_tickers": focus_tickers,
                "latest_market_snapshot": self._market_snapshot_summary(latest_snapshot),
            },
            items=items,
            record_run=record_run,
            trigger_source=trigger_source,
        )

    def sync_postmarket_review(
        self,
        session: Session,
        *,
        record_run: bool = True,
        trigger_source: str = "manual_sync",
    ) -> LearningWorkflow:
        session_state = self.market_hours_service.get_session_state()
        session_payload = session_state.to_payload()
        review_date = self._review_date_from_session_payload(session_payload)
        review_key = f"postmarket:{review_date}"
        entity_id = self._review_entity_id(review_date)
        existing_context = self._existing_workflow_context(session, workflow_type="postmarket_review", scope="global")

        recent_closed_threshold = datetime.now(UTC) - timedelta(hours=36)
        recent_closed_positions = list(
            session.scalars(
                select(Position)
                .where(
                    Position.status == "closed",
                    Position.exit_date.is_not(None),
                    Position.exit_date >= recent_closed_threshold,
                )
                .order_by(desc(Position.exit_date), desc(Position.id))
                .limit(5)
            ).all()
        )
        open_positions = list(
            session.scalars(
                select(Position)
                .where(Position.status == "open")
                .order_by(desc(Position.entry_date), desc(Position.id))
                .limit(5)
            ).all()
        )
        recent_closed_count = len(recent_closed_positions)
        open_positions_count = int(session.query(Position).filter(Position.status == "open").count())
        pending_review_count = int(
            session.query(Position).filter(Position.status == "closed", Position.review_status == "pending").count()
        )
        open_research_tasks = int(
            session.query(ResearchTask).filter(ResearchTask.status.in_(("open", "in_progress"))).count()
        )
        latest_snapshot = self.market_state_service.get_latest_snapshot(session)
        review_needed = (
            session_state.is_trading_day
            and session_payload.get("session_label") == "after_hours"
            and (recent_closed_count > 0 or pending_review_count > 0 or open_positions_count > 0)
        )
        already_completed = self._review_cycle_completed(existing_context, review_key=review_key)
        items: list[dict] = []

        if review_needed and not already_completed:
            items.append(
                {
                    "item_type": "postmarket_checklist",
                    "entity_id": entity_id,
                    "title": (
                        f"Postmarket recap for {review_date}: {recent_closed_count} recent closed positions, "
                        f"{pending_review_count} pending reviews, {open_positions_count} open positions."
                    ),
                    "status": "pending_review",
                    "priority": "high" if pending_review_count > 0 else "normal",
                    "action_hint": "complete",
                    "payload": {
                        "review_key": review_key,
                        "review_date": review_date,
                        "market_session": session_payload.get("session_label"),
                        "recent_closed_count": recent_closed_count,
                        "pending_review_count": pending_review_count,
                        "open_positions_count": open_positions_count,
                        "open_research_tasks": open_research_tasks,
                        "recent_closed_tickers": [position.ticker for position in recent_closed_positions],
                        "open_position_tickers": [position.ticker for position in open_positions],
                        "latest_market_snapshot": self._market_snapshot_summary(latest_snapshot),
                    },
                }
            )

        if review_needed and already_completed:
            summary = f"Postmarket review for {review_date} already completed."
        elif review_needed:
            summary = (
                f"Postmarket review due for {review_date}: {recent_closed_count} recently closed positions, "
                f"{pending_review_count} pending reviews and {open_positions_count} open positions still exposed."
            )
        else:
            summary = "No postmarket review is currently pending."

        return self._upsert_workflow(
            session,
            workflow_type="postmarket_review",
            scope="global",
            title="Postmarket Review",
            status="open" if items else "resolved",
            priority="high" if items and pending_review_count > 0 else "normal",
            summary=summary,
            context={
                "workflow_family": "session_review",
                "review_key": review_key,
                "review_date": review_date,
                "market_session": session_payload,
                "recent_closed_count": recent_closed_count,
                "pending_review_count": pending_review_count,
                "open_positions_count": open_positions_count,
                "open_research_tasks": open_research_tasks,
                "recent_closed_tickers": [position.ticker for position in recent_closed_positions],
                "open_position_tickers": [position.ticker for position in open_positions],
                "latest_market_snapshot": self._market_snapshot_summary(latest_snapshot),
            },
            items=items,
            record_run=record_run,
            trigger_source=trigger_source,
        )

    def sync_regime_shift_review(
        self,
        session: Session,
        *,
        record_run: bool = True,
        trigger_source: str = "manual_sync",
    ) -> LearningWorkflow:
        existing_context = self._existing_workflow_context(session, workflow_type="regime_shift_review", scope="global")
        snapshots = list(
            session.scalars(
                select(MarketStateSnapshotRecord)
                .order_by(desc(MarketStateSnapshotRecord.created_at), desc(MarketStateSnapshotRecord.id))
                .limit(2)
            ).all()
        )
        items: list[dict] = []
        current = snapshots[0] if snapshots else None
        previous = snapshots[1] if len(snapshots) > 1 else None
        previous_payload = dict(previous.snapshot_payload or {}) if previous is not None else {}
        current_payload = dict(current.snapshot_payload or {}) if current is not None else {}
        previous_active_regimes = self._normalized_string_list(
            (previous_payload.get("macro_context") if isinstance(previous_payload.get("macro_context"), dict) else {}).get("active_regimes")
        )
        current_active_regimes = self._normalized_string_list(
            (current_payload.get("macro_context") if isinstance(current_payload.get("macro_context"), dict) else {}).get("active_regimes")
        )
        regime_changed = previous is not None and current is not None and previous.regime_label != current.regime_label
        confidence_delta = abs(float(current.regime_confidence or 0.0) - float(previous.regime_confidence or 0.0)) if previous is not None and current is not None else 0.0
        macro_regimes_changed = previous_active_regimes != current_active_regimes if previous is not None and current is not None else False
        review_needed = previous is not None and current is not None and (
            regime_changed or macro_regimes_changed or confidence_delta >= 0.2
        )
        review_key = (
            f"regime:{previous.id}:{current.id}:{previous.regime_label}->{current.regime_label}"
            if previous is not None and current is not None
            else "regime:none"
        )
        already_completed = self._review_cycle_completed(existing_context, review_key=review_key)

        if review_needed and not already_completed and current is not None and previous is not None:
            items.append(
                {
                    "item_type": "regime_shift",
                    "entity_id": current.id,
                    "title": (
                        f"Review regime transition {previous.regime_label} -> {current.regime_label} "
                        f"(delta {confidence_delta:.2f})."
                    ),
                    "status": "pending_review",
                    "priority": "high" if regime_changed or current.regime_label in {"macro_uncertainty", "risk_off"} else "normal",
                    "action_hint": "complete",
                    "payload": {
                        "review_key": review_key,
                        "previous_snapshot_id": previous.id,
                        "current_snapshot_id": current.id,
                        "previous_regime": previous.regime_label,
                        "current_regime": current.regime_label,
                        "previous_confidence": previous.regime_confidence,
                        "current_confidence": current.regime_confidence,
                        "confidence_delta": round(confidence_delta, 4),
                        "previous_active_regimes": previous_active_regimes,
                        "current_active_regimes": current_active_regimes,
                        "previous_trigger": previous.trigger,
                        "current_trigger": current.trigger,
                    },
                }
            )

        if review_needed and already_completed and current is not None and previous is not None:
            summary = (
                f"Regime shift review already completed for {previous.regime_label} -> {current.regime_label}."
            )
        elif review_needed and current is not None and previous is not None:
            summary = (
                f"Regime shift review due: {previous.regime_label} -> {current.regime_label} "
                f"with confidence delta {confidence_delta:.2f}."
            )
        else:
            summary = "No regime shift review is currently pending."

        return self._upsert_workflow(
            session,
            workflow_type="regime_shift_review",
            scope="global",
            title="Regime Shift Review",
            status="open" if items else "resolved",
            priority="high" if items else "normal",
            summary=summary,
            context={
                "workflow_family": "regime_review",
                "review_key": review_key if review_needed else None,
                "latest_snapshot_id": current.id if current is not None else None,
                "previous_snapshot_id": previous.id if previous is not None else None,
                "previous_regime": previous.regime_label if previous is not None else None,
                "current_regime": current.regime_label if current is not None else None,
                "previous_confidence": previous.regime_confidence if previous is not None else None,
                "current_confidence": current.regime_confidence if current is not None else None,
                "confidence_delta": round(confidence_delta, 4),
                "previous_active_regimes": previous_active_regimes,
                "current_active_regimes": current_active_regimes,
                "regime_changed": regime_changed,
                "macro_regimes_changed": macro_regimes_changed,
            },
            items=items,
            record_run=record_run,
            trigger_source=trigger_source,
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
        record_run: bool = True,
        trigger_source: str = "manual_sync",
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
        item_diff = self._diff_items(previous_items=previous_items, current_items=normalized_items)
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
            item_diff=item_diff,
            previous_summary=previous_summary,
            summary=str(summary or "").strip(),
            open_item_count=int(workflow.open_item_count or 0),
        )
        if sync_entry is not None:
            context_payload = dict(workflow.context or {})
            sync_log = list(context_payload.get("sync_log") or [])
            sync_log.append(sync_entry)
            context_payload["sync_log"] = sync_log[-30:]
            workflow.context = self._json_ready(context_payload)
        session.add(workflow)
        session.commit()
        session.refresh(workflow)
        if record_run:
            self._create_workflow_run(
                session,
                workflow=workflow,
                run_kind="sync",
                trigger_source=trigger_source,
                status="completed",
                summary=summary,
                input_payload={
                    "workflow_type": workflow.workflow_type,
                    "scope": workflow.scope,
                    "requested_status": status,
                    "priority": priority,
                },
                context_payload=dict(workflow.context or {}),
                output_payload={
                    "status_before": previous_status,
                    "status_after": str(workflow.status or "").strip().lower() or "open",
                    "previous_summary": previous_summary,
                    "summary": str(summary or "").strip(),
                    "item_count_after": int(workflow.item_count or 0),
                    "open_item_count_after": int(workflow.open_item_count or 0),
                    "changed": sync_entry is not None,
                    "added_items": [self._item_history_label(item) for item in item_diff["added_items"]],
                    "removed_items": [self._item_history_label(item) for item in item_diff["removed_items"]],
                    "changed_status_items": [
                        self._item_history_label(item["after"])
                        for item in item_diff["changed_status_items"]
                    ],
                },
                artifacts=self._build_sync_artifacts(item_diff=item_diff),
                started_at=now,
            )
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
    ):
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
        return self.journal_repository.create(
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

    def _finalize_action(
        self,
        session: Session,
        *,
        workflow: LearningWorkflow,
        item_type: str,
        entity_id: int,
        action: str,
        summary: str,
        effect: dict,
        claims: list[KnowledgeClaimCreate] | None = None,
        research_tasks: list[ResearchTaskCreate] | None = None,
        skill_gaps: list[LearningWorkflowSkillGapCreate] | None = None,
        skill_candidates: list[LearningWorkflowSkillCandidateCreate] | None = None,
        refresh_workflow,
    ) -> tuple[LearningWorkflow, dict]:
        started_at = datetime.now(UTC)
        status_before = str(workflow.status or "").strip().lower() or "open"
        open_item_count_before = int(workflow.open_item_count or 0)
        created_outputs, created_output_artifacts = self._materialize_structured_outputs(
            session,
            workflow=workflow,
            item_type=item_type,
            entity_id=entity_id,
            action=action,
            summary=summary,
            effect=effect,
            claims=claims,
            research_tasks=research_tasks,
            skill_gaps=skill_gaps,
            skill_candidates=skill_candidates,
        )
        if created_outputs:
            effect = dict(effect or {})
            effect["created_output_count"] = len(created_outputs)
            effect["created_outputs"] = self._json_ready(created_outputs)
        journal_entry = self._record_action(
            session,
            workflow=workflow,
            item_type=item_type,
            entity_id=entity_id,
            action=action,
            summary=summary,
            effect=effect,
        )
        refreshed = refresh_workflow()
        self._create_workflow_run(
            session,
            workflow=refreshed,
            run_kind="action",
            trigger_source="workflow_action",
            status="completed",
            summary=summary,
            input_payload={
                "item_type": item_type,
                "entity_id": entity_id,
                "action": action,
            },
            context_payload=dict(refreshed.context or {}),
            output_payload={
                "status_before": status_before,
                "status_after": str(refreshed.status or "").strip().lower() or "open",
                "open_item_count_before": open_item_count_before,
                "open_item_count_after": int(refreshed.open_item_count or 0),
                "effect": self._json_ready(dict(effect or {})),
            },
            artifacts=self._build_action_artifacts(
                item_type=item_type,
                entity_id=entity_id,
                summary=summary,
                effect=effect,
                journal_entry_id=getattr(journal_entry, "id", None),
                created_output_artifacts=created_output_artifacts,
            ),
            started_at=started_at,
        )
        return refreshed, effect

    def _list_recent_runs(
        self,
        session: Session,
        *,
        workflow_id: int,
        limit: int,
    ) -> list[LearningWorkflowRun]:
        if int(limit) <= 0:
            return []
        statement = (
            select(LearningWorkflowRun)
            .where(LearningWorkflowRun.workflow_id == workflow_id)
            .order_by(desc(LearningWorkflowRun.started_at), desc(LearningWorkflowRun.id))
            .limit(max(1, min(int(limit), 20)))
        )
        return list(session.scalars(statement).all())

    def _build_run_read_model(
        self,
        session: Session,
        run: LearningWorkflowRun,
        *,
        artifact_limit: int,
    ) -> LearningWorkflowRunRead:
        artifacts: list[LearningWorkflowArtifactRead] = []
        if int(artifact_limit) > 0:
            statement = (
                select(LearningWorkflowArtifact)
                .where(LearningWorkflowArtifact.workflow_run_id == run.id)
                .order_by(desc(LearningWorkflowArtifact.created_at), desc(LearningWorkflowArtifact.id))
                .limit(max(1, min(int(artifact_limit), 25)))
            )
            artifacts = [
                LearningWorkflowArtifactRead.model_validate(item)
                for item in session.scalars(statement).all()
            ]
        return LearningWorkflowRunRead.model_validate(
            {
                "id": run.id,
                "workflow_id": run.workflow_id,
                "run_kind": run.run_kind,
                "trigger_source": run.trigger_source,
                "status": run.status,
                "summary": run.summary,
                "input_payload": dict(run.input_payload or {}),
                "context_payload": dict(run.context_payload or {}),
                "output_payload": dict(run.output_payload or {}),
                "artifact_count": int(run.artifact_count or 0),
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "created_at": run.created_at,
                "artifacts": [artifact.model_dump() for artifact in artifacts],
            }
        )

    def _create_workflow_run(
        self,
        session: Session,
        *,
        workflow: LearningWorkflow,
        run_kind: str,
        trigger_source: str,
        status: str,
        summary: str,
        input_payload: dict,
        context_payload: dict,
        output_payload: dict,
        artifacts: list[dict],
        started_at: datetime,
    ) -> LearningWorkflowRun:
        run = LearningWorkflowRun(
            workflow_id=workflow.id,
            run_kind=run_kind,
            trigger_source=trigger_source,
            status=status,
            summary=summary,
            input_payload=self._json_ready(dict(input_payload or {})),
            context_payload=self._json_ready(dict(context_payload or {})),
            output_payload=self._json_ready(dict(output_payload or {})),
            artifact_count=len(list(artifacts or [])),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        for artifact in list(artifacts or []):
            session.add(
                LearningWorkflowArtifact(
                    workflow_id=workflow.id,
                    workflow_run_id=run.id,
                    artifact_type=str(artifact.get("artifact_type") or "artifact"),
                    entity_type=artifact.get("entity_type"),
                    entity_id=artifact.get("entity_id"),
                    title=artifact.get("title"),
                    summary=artifact.get("summary"),
                    ticker=artifact.get("ticker"),
                    strategy_version_id=artifact.get("strategy_version_id"),
                    payload=self._json_ready(dict(artifact.get("payload") or {})),
                )
            )
        session.commit()
        session.refresh(run)
        return run

    def _build_sync_artifacts(self, *, item_diff: dict) -> list[dict]:
        artifacts: list[dict] = []
        for item in item_diff["added_items"]:
            payload = dict(item.get("payload") or {})
            artifacts.append(
                {
                    "artifact_type": "workflow_item_added",
                    "entity_type": str(item.get("item_type") or "workflow_item"),
                    "entity_id": item.get("entity_id"),
                    "title": item.get("title"),
                    "summary": "Workflow item opened during sync.",
                    "ticker": payload.get("ticker"),
                    "strategy_version_id": payload.get("strategy_version_id"),
                    "payload": dict(item),
                }
            )
        for item in item_diff["removed_items"]:
            payload = dict(item.get("payload") or {})
            artifacts.append(
                {
                    "artifact_type": "workflow_item_removed",
                    "entity_type": str(item.get("item_type") or "workflow_item"),
                    "entity_id": item.get("entity_id"),
                    "title": item.get("title"),
                    "summary": "Workflow item cleared during sync.",
                    "ticker": payload.get("ticker"),
                    "strategy_version_id": payload.get("strategy_version_id"),
                    "payload": dict(item),
                }
            )
        for item in item_diff["changed_status_items"]:
            after_payload = dict((item.get("after") or {}).get("payload") or {})
            artifacts.append(
                {
                    "artifact_type": "workflow_item_status_changed",
                    "entity_type": str((item.get("after") or {}).get("item_type") or "workflow_item"),
                    "entity_id": (item.get("after") or {}).get("entity_id"),
                    "title": (item.get("after") or {}).get("title") or (item.get("before") or {}).get("title"),
                    "summary": "Workflow item status changed during sync.",
                    "ticker": after_payload.get("ticker"),
                    "strategy_version_id": after_payload.get("strategy_version_id"),
                    "payload": dict(item),
                }
            )
        return artifacts

    def _materialize_structured_outputs(
        self,
        session: Session,
        *,
        workflow: LearningWorkflow,
        item_type: str,
        entity_id: int,
        action: str,
        summary: str,
        effect: dict,
        claims: list[KnowledgeClaimCreate] | None = None,
        research_tasks: list[ResearchTaskCreate] | None = None,
        skill_gaps: list[LearningWorkflowSkillGapCreate] | None = None,
        skill_candidates: list[LearningWorkflowSkillCandidateCreate] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        source_meta = self._workflow_output_source_meta(
            workflow=workflow,
            item_type=item_type,
            entity_id=entity_id,
            action=action,
            effect=effect,
        )
        created_outputs: list[dict] = []
        artifacts: list[dict] = []

        for payload in list(claims or []):
            output, output_artifacts = self._create_claim_output(
                session,
                workflow=workflow,
                summary=summary,
                effect=effect,
                payload=payload,
                source_meta=source_meta,
            )
            created_outputs.append(output)
            artifacts.extend(output_artifacts)

        for payload in list(research_tasks or []):
            output, output_artifacts = self._create_research_task_output(
                session,
                payload=payload,
                source_meta=source_meta,
            )
            created_outputs.append(output)
            artifacts.extend(output_artifacts)

        for payload in list(skill_gaps or []):
            output, output_artifacts = self._create_skill_gap_output(
                session,
                payload=payload,
                source_meta=source_meta,
            )
            created_outputs.append(output)
            artifacts.extend(output_artifacts)

        for payload in list(skill_candidates or []):
            output, output_artifacts = self._create_skill_candidate_output(
                session,
                payload=payload,
                source_meta=source_meta,
            )
            created_outputs.append(output)
            artifacts.extend(output_artifacts)

        return created_outputs, artifacts

    def _create_claim_output(
        self,
        session: Session,
        *,
        workflow: LearningWorkflow,
        summary: str,
        effect: dict,
        payload: KnowledgeClaimCreate,
        source_meta: dict,
    ) -> tuple[dict, list[dict]]:
        claim_payload = self._payload_model_dump(payload)
        claim_meta = {**dict(claim_payload.get("meta") or {}), **source_meta}
        claim, created = self.claim_service.upsert_claim(
            session,
            ClaimSeed(
                scope=str(claim_payload.get("scope") or "").strip(),
                key=str(claim_payload.get("key") or "").strip(),
                claim_type=str(claim_payload.get("claim_type") or "").strip(),
                claim_text=str(claim_payload.get("claim_text") or "").strip(),
                linked_ticker=claim_payload.get("linked_ticker"),
                strategy_version_id=claim_payload.get("strategy_version_id"),
                status=str(claim_payload.get("status") or "provisional").strip(),
                confidence=float(claim_payload.get("confidence") or 0.5),
                freshness_state=str(claim_payload.get("freshness_state") or "current").strip(),
                meta=claim_meta,
            ),
        )
        evidence = self.claim_service.add_evidence(
            session,
            claim_id=claim.id,
            seed=ClaimEvidenceSeed(
                source_type="learning_workflow",
                source_key=self._bounded_string(
                    f"workflow:{workflow.id}:{source_meta.get('source_workflow_review_key') or workflow.workflow_type}:claim:{claim.key}",
                    limit=160,
                ),
                stance="support",
                summary=str(claim_payload.get("claim_text") or summary).strip(),
                evidence_payload=self._json_ready(
                    {
                        **source_meta,
                        "workflow_action_summary": summary,
                        "workflow_effect": {
                            "review_key": effect.get("review_key"),
                            "resolution_class": effect.get("resolution_class"),
                            "resolution_outcome": effect.get("resolution_outcome"),
                        },
                    }
                ),
                strength=max(0.0, min(float(claim_payload.get("confidence") or 0.68), 1.0)),
                observed_at=datetime.now(UTC),
            ),
        )
        claim = self.claim_service.get_claim(session, claim.id) or claim
        output = {
            "output_type": "knowledge_claim",
            "entity_type": "knowledge_claim",
            "entity_id": claim.id,
            "created": created,
            "key": claim.key,
            "title": claim.claim_text,
            "status": claim.status,
            "evidence_id": evidence.id,
            "ticker": claim.linked_ticker,
            "strategy_version_id": claim.strategy_version_id,
        }
        artifacts = [
            {
                "artifact_type": "knowledge_claim",
                "entity_type": "knowledge_claim",
                "entity_id": claim.id,
                "title": claim.claim_text[:200],
                "summary": "Workflow action created or refreshed a knowledge claim output.",
                "ticker": claim.linked_ticker,
                "strategy_version_id": claim.strategy_version_id,
                "payload": self._json_ready(
                    {
                        "claim_id": claim.id,
                        "key": claim.key,
                        "claim_type": claim.claim_type,
                        "claim_text": claim.claim_text,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "freshness_state": claim.freshness_state,
                        "created": created,
                    }
                ),
            },
            {
                "artifact_type": "knowledge_claim_evidence",
                "entity_type": "knowledge_claim_evidence",
                "entity_id": evidence.id,
                "title": "Workflow claim evidence",
                "summary": "Workflow action attached supporting evidence to a knowledge claim output.",
                "ticker": claim.linked_ticker,
                "strategy_version_id": claim.strategy_version_id,
                "payload": self._json_ready(
                    {
                        "claim_id": claim.id,
                        "evidence_id": evidence.id,
                        "source_key": evidence.source_key,
                        "summary": evidence.summary,
                    }
                ),
            },
        ]
        return output, artifacts

    def _create_research_task_output(
        self,
        session: Session,
        *,
        payload: ResearchTaskCreate,
        source_meta: dict,
    ) -> tuple[dict, list[dict]]:
        task_payload = self._payload_model_dump(payload)
        strategy_id = task_payload.get("strategy_id")
        task_type = str(task_payload.get("task_type") or "").strip()
        title = str(task_payload.get("title") or "").strip()
        hypothesis = str(task_payload.get("hypothesis") or "").strip()
        scope_payload = dict(task_payload.get("scope") or {})
        scope_payload.setdefault("workflow_context", self._json_ready(dict(source_meta)))
        existing = self.research_service.repository.find_open_by_signature(
            session,
            strategy_id=strategy_id,
            task_type=task_type,
            title=title,
        )
        created = existing is None
        if existing is None:
            task = self.research_service.create_task(
                session,
                ResearchTaskCreate(
                    strategy_id=strategy_id,
                    task_type=task_type,
                    priority=str(task_payload.get("priority") or "normal").strip() or "normal",
                    status=str(task_payload.get("status") or "open").strip() or "open",
                    title=title,
                    hypothesis=hypothesis,
                    scope=self._json_ready(scope_payload),
                ),
            )
        else:
            task = existing
        output = {
            "output_type": "research_task",
            "entity_type": "research_task",
            "entity_id": task.id,
            "created": created,
            "title": task.title,
            "status": task.status,
            "task_type": task.task_type,
            "strategy_id": task.strategy_id,
        }
        artifacts = [
            {
                "artifact_type": "research_task",
                "entity_type": "research_task",
                "entity_id": task.id,
                "title": task.title,
                "summary": "Workflow action created or reused a research task output.",
                "ticker": (
                    scope_payload.get("ticker")
                    if isinstance(scope_payload.get("ticker"), str)
                    else None
                ),
                "strategy_version_id": (
                    scope_payload.get("strategy_version_id")
                    if isinstance(scope_payload.get("strategy_version_id"), int)
                    else None
                ),
                "payload": self._json_ready(
                    {
                        "task_id": task.id,
                        "task_type": task.task_type,
                        "priority": task.priority,
                        "status": task.status,
                        "strategy_id": task.strategy_id,
                        "created": created,
                    }
                ),
            }
        ]
        return output, artifacts

    def _create_skill_gap_output(
        self,
        session: Session,
        *,
        payload: LearningWorkflowSkillGapCreate,
        source_meta: dict,
    ) -> tuple[dict, list[dict]]:
        gap_payload = self._payload_model_dump(payload)
        gap_meta = {
            **dict(gap_payload.get("meta") or {}),
            **source_meta,
            "summary": str(gap_payload.get("summary") or "").strip(),
            "gap_type": str(gap_payload.get("gap_type") or "").strip(),
            "status": str(gap_payload.get("status") or "open").strip() or "open",
            "ticker": gap_payload.get("ticker"),
            "strategy_version_id": gap_payload.get("strategy_version_id"),
            "position_id": gap_payload.get("position_id"),
            "source_type": str(gap_payload.get("source_type") or "learning_workflow").strip(),
            "linked_skill_code": gap_payload.get("linked_skill_code"),
            "target_skill_code": gap_payload.get("target_skill_code"),
            "candidate_action": gap_payload.get("candidate_action"),
            "evidence": self._json_ready(dict(gap_payload.get("evidence") or {})),
        }
        item, created = self._upsert_memory_output_item(
            session,
            memory_type=SKILL_GAP_MEMORY_TYPE,
            scope=str(gap_payload.get("scope") or "").strip(),
            key=str(gap_payload.get("key") or "").strip(),
            content=str(gap_payload.get("summary") or "").strip(),
            meta=gap_meta,
            importance=float(gap_payload.get("importance") or 0.72),
        )
        gap = self.skill_gap_service.get_gap(session, gap_id=item.id) or {}
        output = {
            "output_type": "skill_gap",
            "entity_type": "skill_gap",
            "entity_id": item.id,
            "created": created,
            "key": item.key,
            "title": gap.get("summary") or item.content,
            "status": gap.get("status"),
            "target_skill_code": gap.get("target_skill_code"),
            "ticker": gap.get("ticker"),
            "strategy_version_id": gap.get("strategy_version_id"),
        }
        artifacts = [
            {
                "artifact_type": "skill_gap",
                "entity_type": "skill_gap",
                "entity_id": item.id,
                "title": str(gap.get("summary") or item.content or "")[:200],
                "summary": "Workflow action created or refreshed a skill gap output.",
                "ticker": gap.get("ticker"),
                "strategy_version_id": gap.get("strategy_version_id"),
                "payload": self._json_ready(dict(gap)),
            }
        ]
        return output, artifacts

    def _create_skill_candidate_output(
        self,
        session: Session,
        *,
        payload: LearningWorkflowSkillCandidateCreate,
        source_meta: dict,
    ) -> tuple[dict, list[dict]]:
        candidate_payload = self._payload_model_dump(payload)
        target_skill_code = str(candidate_payload.get("target_skill_code") or "").strip() or None
        candidate_action = str(candidate_payload.get("candidate_action") or "").strip() or None
        if candidate_action is None:
            candidate_action = (
                "update_existing_skill"
                if target_skill_code and self.skill_lifecycle_service.catalog_service.has(target_skill_code)
                else "draft_candidate_skill"
            )
        candidate_meta = {
            **dict(candidate_payload.get("meta") or {}),
            **source_meta,
            "summary": str(candidate_payload.get("summary") or "").strip(),
            "target_skill_code": target_skill_code,
            "candidate_action": candidate_action,
            "candidate_status": str(candidate_payload.get("candidate_status") or "draft").strip() or "draft",
            "activation_status": candidate_payload.get("activation_status"),
            "validation_required": bool(candidate_payload.get("validation_required", True)),
            "source_type": str(candidate_payload.get("source_type") or "learning_workflow").strip(),
            "source_trade_review_id": candidate_payload.get("source_trade_review_id"),
            "ticker": candidate_payload.get("ticker"),
            "strategy_version_id": candidate_payload.get("strategy_version_id"),
        }
        item, created = self._upsert_memory_output_item(
            session,
            memory_type=SKILL_CANDIDATE_MEMORY_TYPE,
            scope=str(candidate_payload.get("scope") or "").strip(),
            key=str(candidate_payload.get("key") or "").strip(),
            content=str(candidate_payload.get("summary") or "").strip(),
            meta=candidate_meta,
            importance=float(candidate_payload.get("importance") or 0.72),
        )
        candidate = SkillLifecycleService._candidate_payload(item)
        output = {
            "output_type": "skill_candidate",
            "entity_type": "skill_candidate",
            "entity_id": item.id,
            "created": created,
            "key": item.key,
            "title": candidate.get("summary") or item.content,
            "status": candidate.get("candidate_status"),
            "target_skill_code": candidate.get("target_skill_code"),
            "ticker": candidate.get("ticker"),
            "strategy_version_id": candidate.get("strategy_version_id"),
        }
        artifacts = [
            {
                "artifact_type": "skill_candidate",
                "entity_type": "skill_candidate",
                "entity_id": item.id,
                "title": str(candidate.get("summary") or item.content or "")[:200],
                "summary": "Workflow action created or refreshed a skill candidate output.",
                "ticker": candidate.get("ticker"),
                "strategy_version_id": candidate.get("strategy_version_id"),
                "payload": self._json_ready(dict(candidate)),
            }
        ]
        return output, artifacts

    def _upsert_memory_output_item(
        self,
        session: Session,
        *,
        memory_type: str,
        scope: str,
        key: str,
        content: str,
        meta: dict,
        importance: float,
    ) -> tuple[MemoryItem, bool]:
        normalized_key = self._bounded_string(key, limit=120)
        item = session.scalar(
            select(MemoryItem).where(
                MemoryItem.memory_type == memory_type,
                MemoryItem.scope == scope,
                MemoryItem.key == normalized_key,
            )
        )
        if item is None:
            created_item = self.memory_service.create_item(
                session,
                MemoryItemCreate(
                    memory_type=memory_type,
                    scope=scope,
                    key=normalized_key,
                    content=content,
                    meta=self._json_ready(dict(meta or {})),
                    importance=max(0.0, min(float(importance), 1.0)),
                ),
            )
            return created_item, True

        item.content = content
        item.meta = self._json_ready({**dict(item.meta or {}), **dict(meta or {})})
        item.importance = max(0.0, min(float(importance), 1.0))
        session.add(item)
        session.commit()
        session.refresh(item)
        return item, False

    @staticmethod
    def _workflow_output_source_meta(
        *,
        workflow: LearningWorkflow,
        item_type: str,
        entity_id: int,
        action: str,
        effect: dict,
    ) -> dict:
        return {
            "source": "learning_workflow",
            "source_workflow_id": workflow.id,
            "source_workflow_type": workflow.workflow_type,
            "source_workflow_item_type": item_type,
            "source_workflow_entity_id": entity_id,
            "source_workflow_action": action,
            "source_workflow_review_key": effect.get("review_key"),
            "source_workflow_review_date": effect.get("review_date"),
            "source_workflow_resolution_class": effect.get("resolution_class"),
            "source_workflow_resolution_outcome": effect.get("resolution_outcome"),
        }

    def _build_action_artifacts(
        self,
        *,
        item_type: str,
        entity_id: int,
        summary: str,
        effect: dict,
        journal_entry_id: int | None,
        created_output_artifacts: list[dict] | None = None,
    ) -> list[dict]:
        artifacts = [
            {
                "artifact_type": "workflow_action_effect",
                "entity_type": effect.get("entity_type") or item_type,
                "entity_id": self._effect_entity_id(effect, fallback=entity_id),
                "title": effect.get("resolution_class") or item_type,
                "summary": summary,
                "ticker": effect.get("ticker"),
                "strategy_version_id": effect.get("strategy_version_id"),
                "payload": dict(effect or {}),
            }
        ]
        artifacts.extend(list(created_output_artifacts or []))
        workflow_completion_artifact = self._build_workflow_completion_artifact(summary=summary, effect=effect)
        if workflow_completion_artifact is not None:
            artifacts.append(workflow_completion_artifact)
        evidence_id = effect.get("evidence_id")
        if evidence_id is not None:
            artifacts.append(
                {
                    "artifact_type": "knowledge_claim_evidence",
                    "entity_type": "knowledge_claim_evidence",
                    "entity_id": evidence_id,
                    "title": "Claim review evidence",
                    "summary": "Workflow action created or updated claim evidence.",
                    "ticker": effect.get("ticker"),
                    "strategy_version_id": effect.get("strategy_version_id"),
                    "payload": {"evidence_id": evidence_id},
                }
            )
        promoted_skill_candidate = effect.get("promoted_skill_candidate")
        if isinstance(promoted_skill_candidate, dict) and promoted_skill_candidate.get("id") is not None:
            artifacts.append(
                {
                    "artifact_type": "skill_candidate",
                    "entity_type": "skill_candidate",
                    "entity_id": promoted_skill_candidate.get("id"),
                    "title": promoted_skill_candidate.get("summary") or "Promoted skill candidate",
                    "summary": "Workflow action promoted a skill candidate.",
                    "ticker": promoted_skill_candidate.get("ticker"),
                    "strategy_version_id": promoted_skill_candidate.get("strategy_version_id"),
                    "payload": dict(promoted_skill_candidate),
                }
            )
        if journal_entry_id is not None:
            artifacts.append(
                {
                    "artifact_type": "journal_entry",
                    "entity_type": "journal_entry",
                    "entity_id": journal_entry_id,
                    "title": "Workflow action journal entry",
                    "summary": "Journal entry created for workflow action.",
                    "ticker": effect.get("ticker"),
                    "strategy_version_id": effect.get("strategy_version_id"),
                    "payload": {"journal_entry_id": journal_entry_id},
                }
            )
        return artifacts

    @staticmethod
    def _build_workflow_completion_artifact(*, summary: str, effect: dict) -> dict | None:
        if str(effect.get("entity_type") or "").strip().lower() != "workflow_cycle":
            return None
        workflow_type = str(effect.get("workflow_type") or "").strip().lower()
        artifact_type_map = {
            "premarket_review": "premarket_review_completion",
            "postmarket_review": "postmarket_review_completion",
            "regime_shift_review": "regime_shift_review_completion",
        }
        artifact_type = artifact_type_map.get(workflow_type)
        if artifact_type is None:
            return None
        title_map = {
            "premarket_review": "Premarket review completion",
            "postmarket_review": "Postmarket review completion",
            "regime_shift_review": "Regime shift review completion",
        }
        return {
            "artifact_type": artifact_type,
            "entity_type": "workflow_cycle",
            "entity_id": None,
            "title": title_map.get(workflow_type) or "Workflow review completion",
            "summary": summary,
            "ticker": effect.get("ticker"),
            "strategy_version_id": effect.get("strategy_version_id"),
            "payload": dict(effect or {}),
        }

    @staticmethod
    def _effect_entity_id(effect: dict, *, fallback: int | None = None) -> int | None:
        for key in ("claim_id", "gap_id", "candidate_id", "revision_id"):
            if effect.get(key) is not None:
                return int(effect[key])
        return fallback

    @staticmethod
    def _payload_model_dump(value: object) -> dict:
        if hasattr(value, "model_dump"):
            return dict(value.model_dump())
        if isinstance(value, dict):
            return dict(value)
        return {}

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

    @staticmethod
    def _bounded_string(value: str, *, limit: int) -> str:
        return str(value or "").strip()[: max(int(limit), 1)]

    def _merge_context(self, *, persisted_context: dict, new_context: dict) -> dict:
        merged = dict(new_context or {})
        for key in (
            "resolution_log",
            "action_counts",
            "last_action",
            "last_action_at",
            "sync_log",
            "last_completed_review_key",
            "last_completed_at",
            "last_completed_summary",
            "last_completed_item_type",
        ):
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
        item_diff: dict,
        previous_summary: str,
        summary: str,
        open_item_count: int,
    ) -> dict | None:
        added_items = [cls._item_history_label(item) for item in item_diff["added_items"]]
        removed_items = [cls._item_history_label(item) for item in item_diff["removed_items"]]
        changed_status_items = [
            cls._item_history_label(item["after"])
            for item in item_diff["changed_status_items"]
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

    @classmethod
    def _diff_items(cls, *, previous_items: list[dict], current_items: list[dict]) -> dict:
        previous_index = cls._index_items(previous_items)
        current_index = cls._index_items(current_items)
        added_items = [
            cls._json_ready(dict(item))
            for key, item in current_index.items()
            if key not in previous_index
        ]
        removed_items = [
            cls._json_ready(dict(item))
            for key, item in previous_index.items()
            if key not in current_index
        ]
        changed_status_items = [
            {
                "before": cls._json_ready(dict(previous_index[key])),
                "after": cls._json_ready(dict(item)),
            }
            for key, item in current_index.items()
            if key in previous_index
            and str(item.get("status") or "").strip().lower()
            != str(previous_index[key].get("status") or "").strip().lower()
        ]
        return {
            "added_items": added_items,
            "removed_items": removed_items,
            "changed_status_items": changed_status_items,
        }

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
            ("premarket_checklist", "complete"): ("premarket_review_completed", "accepted"),
            ("postmarket_checklist", "complete"): ("postmarket_review_completed", "accepted"),
            ("regime_shift", "complete"): ("regime_shift_review_completed", "accepted"),
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

    def _complete_checklist_cycle(
        self,
        *,
        workflow: LearningWorkflow,
        item_type: str,
        entity_id: int,
        action: str,
        summary: str,
    ) -> dict:
        item = self._find_workflow_item(workflow, item_type=item_type, entity_id=entity_id)
        if item is None:
            raise ValueError("Learning workflow item not found.")
        payload = dict(item.get("payload") or {})
        review_key = str(payload.get("review_key") or (workflow.context or {}).get("review_key") or "").strip()
        if not review_key:
            raise ValueError("Learning workflow review key is missing.")

        context = dict(workflow.context or {})
        context["last_completed_review_key"] = review_key
        context["last_completed_at"] = datetime.now(UTC).isoformat()
        context["last_completed_summary"] = summary
        context["last_completed_item_type"] = item_type
        workflow.context = self._json_ready(context)

        resolution_class, resolution_outcome = self._classify_action(item_type=item_type, action=action)
        return {
            "entity_type": "workflow_cycle",
            "workflow_type": workflow.workflow_type,
            "item_type": item_type,
            "review_key": review_key,
            "market_session": payload.get("market_session"),
            "review_date": payload.get("review_date"),
            "open_positions_count": payload.get("open_positions_count"),
            "active_watchlists_count": payload.get("active_watchlists_count"),
            "open_research_tasks": payload.get("open_research_tasks"),
            "pending_review_count": payload.get("pending_review_count"),
            "recent_closed_count": payload.get("recent_closed_count"),
            "focus_tickers": list(payload.get("focus_tickers") or []),
            "recent_closed_tickers": list(payload.get("recent_closed_tickers") or []),
            "open_position_tickers": list(payload.get("open_position_tickers") or []),
            "latest_market_snapshot": self._json_ready(payload.get("latest_market_snapshot")),
            "previous_regime": payload.get("previous_regime"),
            "current_regime": payload.get("current_regime"),
            "previous_snapshot_id": payload.get("previous_snapshot_id"),
            "current_snapshot_id": payload.get("current_snapshot_id"),
            "previous_confidence": payload.get("previous_confidence"),
            "current_confidence": payload.get("current_confidence"),
            "confidence_delta": payload.get("confidence_delta"),
            "previous_active_regimes": list(payload.get("previous_active_regimes") or []),
            "current_active_regimes": list(payload.get("current_active_regimes") or []),
            "previous_trigger": payload.get("previous_trigger"),
            "current_trigger": payload.get("current_trigger"),
            "resolution_class": resolution_class,
            "resolution_outcome": resolution_outcome,
        }

    @staticmethod
    def _find_workflow_item(workflow: LearningWorkflow, *, item_type: str, entity_id: int) -> dict | None:
        for item in list(workflow.items or []):
            if not isinstance(item, dict):
                continue
            if str(item.get("item_type") or "").strip().lower() != str(item_type or "").strip().lower():
                continue
            if item.get("entity_id") != entity_id:
                continue
            return item
        return None

    @staticmethod
    def _review_date_from_session_payload(session_payload: dict) -> str:
        raw = str(session_payload.get("now_local") or session_payload.get("now_utc") or "").strip()
        if "T" in raw:
            return raw.split("T", 1)[0]
        return raw[:10] if raw else datetime.now(UTC).date().isoformat()

    @staticmethod
    def _review_entity_id(review_date: str) -> int:
        normalized = str(review_date or "").strip().replace("-", "")
        return int(normalized) if normalized.isdigit() else int(datetime.now(UTC).strftime("%Y%m%d"))

    @staticmethod
    def _review_cycle_completed(context: dict, *, review_key: str) -> bool:
        return str((context or {}).get("last_completed_review_key") or "").strip() == str(review_key or "").strip()

    @staticmethod
    def _normalized_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            label = str(item or "").strip()
            if label:
                normalized.append(label)
        return normalized

    @staticmethod
    def _collect_focus_tickers(*, open_positions: list[Position], active_watchlists: list[Watchlist]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for position in open_positions:
            ticker = str(position.ticker or "").strip().upper()
            if ticker and ticker not in seen:
                seen.add(ticker)
                ordered.append(ticker)
        for watchlist in active_watchlists:
            for item in list(getattr(watchlist, "items", []) or []):
                ticker = str(getattr(item, "ticker", "") or "").strip().upper()
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    ordered.append(ticker)
                if len(ordered) >= 8:
                    return ordered
        return ordered

    @staticmethod
    def _market_snapshot_summary(snapshot: MarketStateSnapshotRecord | None) -> dict | None:
        if snapshot is None:
            return None
        return {
            "snapshot_id": snapshot.id,
            "trigger": snapshot.trigger,
            "pdca_phase": snapshot.pdca_phase,
            "regime_label": snapshot.regime_label,
            "regime_confidence": snapshot.regime_confidence,
            "summary": snapshot.summary,
            "created_at": snapshot.created_at,
        }

    @staticmethod
    def _existing_workflow_context(session: Session, *, workflow_type: str, scope: str) -> dict:
        workflow = session.scalars(
            select(LearningWorkflow).where(
                LearningWorkflow.workflow_type == workflow_type,
                LearningWorkflow.scope == scope,
            )
        ).first()
        return dict(workflow.context or {}) if workflow is not None else {}
