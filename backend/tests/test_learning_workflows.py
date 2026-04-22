from datetime import datetime, timedelta, timezone

from app.db.models.market_state_snapshot import MarketStateSnapshotRecord
from app.db.models.journal import JournalEntry
from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.learning_workflow import LearningWorkflow, LearningWorkflowArtifact, LearningWorkflowRun
from app.db.models.memory import MemoryItem
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.learning import api as learning_api
from app.domains.system.market_hours import MarketSessionState


class _FakeMarketHoursService:
    def __init__(self, session_label: str, *, review_date: str = "2026-04-22") -> None:
        self.session_label = session_label
        self.review_date = review_date

    def get_session_state(self, *, now=None) -> MarketSessionState:
        local_clock = {
            "pre_market": "08:15:00-04:00",
            "regular": "11:00:00-04:00",
            "after_hours": "17:10:00-04:00",
            "weekend": "12:00:00-04:00",
        }.get(self.session_label, "11:00:00-04:00")
        is_weekend = self.session_label == "weekend"
        is_regular_session_open = self.session_label == "regular"
        is_extended_hours = self.session_label in {"pre_market", "after_hours"}
        return MarketSessionState(
            market="us_equities",
            timezone="America/New_York",
            session_label=self.session_label,
            is_weekend=is_weekend,
            is_trading_day=not is_weekend,
            is_regular_session_open=is_regular_session_open,
            is_extended_hours=is_extended_hours,
            now_utc=f"{self.review_date}T12:15:00+00:00",
            now_local=f"{self.review_date}T{local_clock}",
            next_regular_open=f"{self.review_date}T13:30:00+00:00",
            next_regular_close=f"{self.review_date}T20:00:00+00:00",
        )


def test_learning_workflows_list_defaults_to_persisted_state_without_sync(client, session) -> None:
    response = client.get("/api/v1/learning-workflows")

    assert response.status_code == 200
    assert response.json() == []
    assert session.query(LearningWorkflow).count() == 0


def test_learning_workflows_sync_claim_review_and_skill_audit(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("regular")
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:55",
            "key": "claim:test-workflow-stale",
            "claim_type": "review_improvement",
            "claim_text": "Old breakout lesson needs explicit review.",
            "linked_ticker": "AAPL",
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]

    evidence = client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:workflow:1",
            "stance": "support",
            "summary": "Historical support evidence for the claim.",
            "evidence_payload": {"pnl_pct": -4.0},
            "strength": 0.72,
        },
    )
    assert evidence.status_code == 200

    stored_claim = session.get(KnowledgeClaim, claim_id)
    assert stored_claim is not None
    stored_claim.last_reviewed_at = datetime.now(timezone.utc) - timedelta(days=45)
    session.add(stored_claim)
    session.commit()

    assert client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:55",
            "key": "skill_gap:test:workflow",
            "content": "Catalog still lacks an explicit procedure for this recurring review pattern.",
            "meta": {
                "summary": "Catalog still lacks an explicit procedure for this recurring review pattern.",
                "gap_type": "missing_catalog_skill",
                "status": "open",
                "ticker": "AAPL",
                "strategy_version_id": 55,
                "position_id": 11,
                "source_type": "trade_review",
                "source_trade_review_id": 71,
                "target_skill_code": "review_false_breakout",
                "candidate_action": "draft_candidate_skill",
            },
            "importance": 0.84,
        },
    ).status_code == 201

    assert client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:55",
            "key": "skill_candidate:test:workflow",
            "content": "Draft candidate waiting for audit.",
            "meta": {
                "summary": "Draft candidate waiting for audit.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "knowledge_claim",
                "source_claim_id": claim_id,
                "ticker": "AAPL",
                "strategy_version_id": 55,
            },
            "importance": 0.75,
        },
    ).status_code == 201

    response = client.get("/api/v1/learning-workflows", params={"sync": "true", "limit": 10})
    assert response.status_code == 200
    payload = response.json()

    stale_review = next(item for item in payload if item["workflow_type"] == "stale_claim_review")
    assert stale_review["status"] == "open"
    assert stale_review["open_item_count"] >= 1
    assert stale_review["items"][0]["item_type"] == "claim_review"
    assert stale_review["items"][0]["entity_id"] == claim_id
    assert stale_review["recent_runs"][0]["run_kind"] == "sync"
    assert stale_review["recent_runs"][0]["trigger_source"] == "api_list_sync"
    assert stale_review["recent_runs"][0]["artifact_count"] >= 1

    weekly_audit = next(item for item in payload if item["workflow_type"] == "weekly_skill_audit")
    assert weekly_audit["status"] == "open"
    assert any(item["item_type"] == "skill_gap" for item in weekly_audit["items"])
    assert any(item["item_type"] == "skill_candidate_audit" for item in weekly_audit["items"])
    assert weekly_audit["context"]["open_skill_gap_count"] == 1
    assert weekly_audit["context"]["draft_skill_candidate_count"] >= 1

    premarket_review = next(item for item in payload if item["workflow_type"] == "premarket_review")
    assert premarket_review["status"] == "resolved"

    postmarket_review = next(item for item in payload if item["workflow_type"] == "postmarket_review")
    assert postmarket_review["status"] == "resolved"

    regime_review = next(item for item in payload if item["workflow_type"] == "regime_shift_review")
    assert regime_review["status"] == "resolved"

    stored_workflows = session.query(LearningWorkflow).all()
    assert len(stored_workflows) == 5
    assert session.query(LearningWorkflowRun).count() == 5
    assert session.query(LearningWorkflowArtifact).count() >= 3


def test_stale_claim_review_workflow_action_updates_claim_and_workflow(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("regular")
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:77",
            "key": "claim:test-workflow-action",
            "claim_type": "review_improvement",
            "claim_text": "This claim needs explicit review.",
            "linked_ticker": "MSFT",
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:workflow:action",
            "stance": "support",
            "summary": "Initial support evidence.",
            "evidence_payload": {},
            "strength": 0.73,
        },
    ).status_code == 200

    stored_claim = session.get(KnowledgeClaim, claim_id)
    assert stored_claim is not None
    stored_claim.last_reviewed_at = datetime.now(timezone.utc) - timedelta(days=45)
    session.add(stored_claim)
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    stale_review = next(item for item in workflows if item["workflow_type"] == "stale_claim_review")

    action = client.post(
        f"/api/v1/learning-workflows/{stale_review['id']}/actions",
        json={
            "item_type": "claim_review",
            "entity_id": claim_id,
            "action": "confirm",
            "summary": "Workflow review confirms the claim remains valid.",
        },
    )
    assert action.status_code == 200
    payload = action.json()
    assert payload["effect"]["claim_status"] == "validated"
    assert payload["effect"]["resolution_class"] == "claim_confirmed"
    assert payload["effect"]["resolution_outcome"] == "accepted"
    assert payload["workflow"]["status"] == "resolved"
    assert payload["workflow"]["open_item_count"] == 0
    assert payload["workflow"]["context"]["resolution_log"][-1]["action"] == "confirm"
    assert payload["workflow"]["history"][0]["event_type"] == "sync"
    assert any(
        entry["event_type"] == "action" and entry["resolution_class"] == "claim_confirmed"
        for entry in payload["workflow"]["history"]
    )
    assert payload["workflow"]["recent_runs"][0]["run_kind"] == "action"
    assert payload["workflow"]["recent_runs"][0]["artifact_count"] >= 2
    artifact_types = {item["artifact_type"] for item in payload["workflow"]["recent_runs"][0]["artifacts"]}
    assert "workflow_action_effect" in artifact_types
    assert "journal_entry" in artifact_types

    action_run = (
        session.query(LearningWorkflowRun)
        .filter(LearningWorkflowRun.workflow_id == stale_review["id"], LearningWorkflowRun.run_kind == "action")
        .one()
    )
    assert action_run.trigger_source == "workflow_action"
    action_artifacts = session.query(LearningWorkflowArtifact).filter(
        LearningWorkflowArtifact.workflow_run_id == action_run.id
    )
    assert action_artifacts.count() >= 2

    detail = client.get(f"/api/v1/learning-workflows/{stale_review['id']}", params={"history_limit": 12})
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert any(entry["change_class"] == "workflow_resolved" for entry in detail_payload["history"])
    assert any(entry["resolution_class"] == "claim_confirmed" for entry in detail_payload["history"])
    assert detail_payload["recent_runs"][0]["run_kind"] == "action"


def test_weekly_skill_audit_workflow_actions_resolve_gap_and_candidate(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("regular")
    assert client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:88",
            "key": "skill_gap:test:action",
            "content": "A workflow gap waiting for review.",
            "meta": {
                "summary": "A workflow gap waiting for review.",
                "gap_type": "missing_catalog_skill",
                "status": "open",
                "ticker": "NVDA",
                "strategy_version_id": 88,
                "position_id": 17,
                "source_type": "trade_review",
                "source_trade_review_id": 91,
                "target_skill_code": "review_false_breakout",
                "candidate_action": "draft_candidate_skill",
            },
            "importance": 0.82,
        },
    ).status_code == 201

    candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:88",
            "key": "skill_candidate:test:action",
            "content": "Audit candidate waiting for validation.",
            "meta": {
                "summary": "Audit candidate waiting for validation.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "knowledge_claim",
                "source_claim_id": 999,
                "ticker": "NVDA",
                "strategy_version_id": 88,
            },
            "importance": 0.79,
        },
    )
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    weekly_audit = next(item for item in workflows if item["workflow_type"] == "weekly_skill_audit")
    gap_item = next(item for item in weekly_audit["items"] if item["item_type"] == "skill_gap")

    resolve_gap = client.post(
        f"/api/v1/learning-workflows/{weekly_audit['id']}/actions",
        json={
            "item_type": "skill_gap",
            "entity_id": gap_item["entity_id"],
            "action": "resolve",
            "summary": "Catalog gap reviewed and addressed.",
        },
    )
    assert resolve_gap.status_code == 200
    resolved_payload = resolve_gap.json()
    assert resolved_payload["effect"]["gap_status"] == "resolved"
    assert resolved_payload["effect"]["resolution_class"] == "gap_resolved"
    assert resolved_payload["workflow"]["status"] == "in_progress"
    assert all(item["item_type"] != "skill_gap" for item in resolved_payload["workflow"]["items"])

    validate_candidate = client.post(
        f"/api/v1/learning-workflows/{weekly_audit['id']}/actions",
        json={
            "item_type": "skill_candidate_audit",
            "entity_id": candidate_id,
            "action": "reject",
            "summary": "Weekly audit rejects this candidate after review.",
        },
    )
    assert validate_candidate.status_code == 200
    validated_payload = validate_candidate.json()
    assert validated_payload["effect"]["candidate_status"] == "rejected"
    assert validated_payload["effect"]["resolution_class"] == "candidate_rejected"
    assert validated_payload["workflow"]["status"] == "resolved"
    assert validated_payload["workflow"]["open_item_count"] == 0
    assert len(validated_payload["workflow"]["context"]["resolution_log"]) >= 2
    assert any(entry["resolution_class"] == "candidate_rejected" for entry in validated_payload["workflow"]["history"])
    disagreement_entries = session.query(JournalEntry).filter(JournalEntry.entry_type == "operator_disagreement").all()
    disagreement_memories = session.query(MemoryItem).filter(MemoryItem.memory_type == "operator_disagreement").all()
    assert len(disagreement_entries) == 1
    assert len(disagreement_memories) == 1
    assert disagreement_entries[0].observations["operator_disagreement"]["disagreement_type"] == "skill_candidate_rejected"


def test_premarket_review_workflow_opens_and_completes_once_per_cycle(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("pre_market")

    watchlist = Watchlist(
        code="premarket-review-test",
        name="Premarket Review Test",
        hypothesis="Review names before the opening bell.",
        status="active",
    )
    watchlist.items.append(
        WatchlistItem(
            ticker="AAPL",
            strategy_hypothesis="Test premarket checklist.",
            state="watching",
        )
    )
    session.add(watchlist)
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    premarket_review = next(item for item in workflows if item["workflow_type"] == "premarket_review")

    assert premarket_review["status"] == "open"
    assert premarket_review["items"][0]["item_type"] == "premarket_checklist"
    assert "AAPL" in premarket_review["context"]["focus_tickers"]

    action = client.post(
        f"/api/v1/learning-workflows/{premarket_review['id']}/actions",
        json={
            "item_type": "premarket_checklist",
            "entity_id": premarket_review["items"][0]["entity_id"],
            "action": "complete",
            "summary": "Reviewed premarket posture and priority names.",
        },
    )
    assert action.status_code == 200
    payload = action.json()
    assert payload["effect"]["resolution_class"] == "premarket_review_completed"
    assert payload["workflow"]["status"] == "resolved"
    assert payload["workflow"]["context"]["last_completed_review_key"] == "premarket:2026-04-22"

    resynced = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    refreshed = next(item for item in resynced if item["workflow_type"] == "premarket_review")
    assert refreshed["status"] == "resolved"
    assert refreshed["summary"] == "Premarket review for 2026-04-22 already completed."


def test_premarket_review_action_can_emit_claim_and_research_task_outputs(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("pre_market")

    watchlist = Watchlist(
        code="premarket-output-test",
        name="Premarket Output Test",
        hypothesis="Generate structured follow-up outputs from the checklist.",
        status="active",
    )
    watchlist.items.append(
        WatchlistItem(
            ticker="TSLA",
            strategy_hypothesis="Opening drive candidate needs explicit tracking.",
            state="watching",
        )
    )
    session.add(watchlist)
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    premarket_review = next(item for item in workflows if item["workflow_type"] == "premarket_review")

    action = client.post(
        f"/api/v1/learning-workflows/{premarket_review['id']}/actions",
        json={
            "item_type": "premarket_checklist",
            "entity_id": premarket_review["items"][0]["entity_id"],
            "action": "complete",
            "summary": "Reviewed premarket posture and opened concrete follow-up research.",
            "claims": [
                {
                    "scope": "strategy:42",
                    "key": "claim:premarket:tsla-opening-drive",
                    "claim_type": "review_improvement",
                    "claim_text": "TSLA needs an explicit opening-drive invalidation checklist when it is already on the premarket focus list.",
                    "linked_ticker": "TSLA",
                    "strategy_version_id": 42,
                    "confidence": 0.74,
                    "meta": {"source": "test"},
                }
            ],
            "research_tasks": [
                {
                    "strategy_id": 42,
                    "task_type": "premarket_follow_up",
                    "priority": "high",
                    "title": "Validate TSLA opening-drive premarket plan",
                    "hypothesis": "A tighter opening-drive checklist may reduce impulsive first-15m entries in TSLA.",
                    "scope": {
                        "ticker": "TSLA",
                        "strategy_version_id": 42,
                        "goal": "decide whether the opening-drive checklist should be formalized",
                    },
                }
            ],
        },
    )
    assert action.status_code == 200
    payload = action.json()

    assert payload["effect"]["resolution_class"] == "premarket_review_completed"
    assert payload["effect"]["created_output_count"] == 2
    assert {item["output_type"] for item in payload["effect"]["created_outputs"]} == {
        "knowledge_claim",
        "research_task",
    }

    stored_claim = session.query(KnowledgeClaim).filter(
        KnowledgeClaim.key == "claim:premarket:tsla-opening-drive"
    ).one()
    assert stored_claim.evidence_count == 1
    assert stored_claim.meta["source_workflow_type"] == "premarket_review"
    assert stored_claim.meta["source_workflow_review_key"] == "premarket:2026-04-22"

    research_task = session.query(ResearchTask).filter(ResearchTask.task_type == "premarket_follow_up").one()
    assert research_task.title == "Validate TSLA opening-drive premarket plan"
    assert research_task.scope["workflow_context"]["source_workflow_type"] == "premarket_review"
    assert research_task.scope["workflow_context"]["source_workflow_review_key"] == "premarket:2026-04-22"

    artifact_types = {item["artifact_type"] for item in payload["workflow"]["recent_runs"][0]["artifacts"]}
    assert "knowledge_claim" in artifact_types
    assert "knowledge_claim_evidence" in artifact_types
    assert "research_task" in artifact_types
    assert "premarket_review_completion" in artifact_types


def test_postmarket_review_workflow_opens_and_completes_once_per_cycle(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("after_hours")

    session.add(
        Position(
            ticker="MSFT",
            account_mode="paper",
            side="long",
            status="closed",
            review_status="pending",
            entry_date=datetime(2026, 4, 22, 13, 35, tzinfo=timezone.utc),
            entry_price=100.0,
            stop_price=96.0,
            target_price=108.0,
            size=1.0,
            thesis="Closed today and still pending review.",
            exit_date=datetime.now(timezone.utc),
            exit_price=103.0,
            pnl_realized=3.0,
            pnl_pct=3.0,
        )
    )
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    postmarket_review = next(item for item in workflows if item["workflow_type"] == "postmarket_review")

    assert postmarket_review["status"] == "open"
    assert postmarket_review["items"][0]["item_type"] == "postmarket_checklist"
    assert postmarket_review["context"]["pending_review_count"] == 1

    action = client.post(
        f"/api/v1/learning-workflows/{postmarket_review['id']}/actions",
        json={
            "item_type": "postmarket_checklist",
            "entity_id": postmarket_review["items"][0]["entity_id"],
            "action": "complete",
            "summary": "Reviewed the day close and queued remaining follow-up.",
        },
    )
    assert action.status_code == 200
    payload = action.json()
    assert payload["effect"]["resolution_class"] == "postmarket_review_completed"
    assert payload["workflow"]["status"] == "resolved"
    assert payload["workflow"]["context"]["last_completed_review_key"] == "postmarket:2026-04-22"

    resynced = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    refreshed = next(item for item in resynced if item["workflow_type"] == "postmarket_review")
    assert refreshed["status"] == "resolved"
    assert refreshed["summary"] == "Postmarket review for 2026-04-22 already completed."


def test_regime_shift_review_workflow_opens_and_completes_once_per_transition(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("regular")

    previous = MarketStateSnapshotRecord(
        trigger="plan",
        pdca_phase="plan",
        execution_mode="global",
        benchmark_ticker="SPY",
        regime_label="bullish_trend",
        regime_confidence=0.84,
        summary="Bullish trend backdrop.",
        snapshot_payload={"macro_context": {"active_regimes": ["bullish_trend"]}},
        source_context={"source": "test"},
        created_at=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
    )
    current = MarketStateSnapshotRecord(
        trigger="do",
        pdca_phase="do",
        execution_mode="global",
        benchmark_ticker="SPY",
        regime_label="macro_uncertainty",
        regime_confidence=0.41,
        summary="Macro uncertainty is dominating the tape.",
        snapshot_payload={"macro_context": {"active_regimes": ["macro_uncertainty"]}},
        source_context={"source": "test"},
        created_at=datetime(2026, 4, 22, 16, 5, tzinfo=timezone.utc),
    )
    session.add_all([previous, current])
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    regime_review = next(item for item in workflows if item["workflow_type"] == "regime_shift_review")

    assert regime_review["status"] == "open"
    assert regime_review["items"][0]["item_type"] == "regime_shift"
    assert regime_review["context"]["previous_regime"] == "bullish_trend"
    assert regime_review["context"]["current_regime"] == "macro_uncertainty"

    action = client.post(
        f"/api/v1/learning-workflows/{regime_review['id']}/actions",
        json={
            "item_type": "regime_shift",
            "entity_id": regime_review["items"][0]["entity_id"],
            "action": "complete",
            "summary": "Acknowledged the regime transition and adjusted operating posture.",
        },
    )
    assert action.status_code == 200
    payload = action.json()
    assert payload["effect"]["resolution_class"] == "regime_shift_review_completed"
    assert payload["effect"]["previous_regime"] == "bullish_trend"
    assert payload["effect"]["current_regime"] == "macro_uncertainty"
    assert payload["workflow"]["status"] == "resolved"

    resynced = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    refreshed = next(item for item in resynced if item["workflow_type"] == "regime_shift_review")
    assert refreshed["status"] == "resolved"
    assert refreshed["summary"] == "Regime shift review already completed for bullish_trend -> macro_uncertainty."


def test_regime_shift_review_action_can_emit_gap_and_candidate_outputs(client, session) -> None:
    learning_api.learning_workflow_service.market_hours_service = _FakeMarketHoursService("regular")

    previous = MarketStateSnapshotRecord(
        trigger="plan",
        pdca_phase="plan",
        execution_mode="global",
        benchmark_ticker="SPY",
        regime_label="bullish_trend",
        regime_confidence=0.81,
        summary="Bullish trend backdrop.",
        snapshot_payload={"macro_context": {"active_regimes": ["bullish_trend"]}},
        source_context={"source": "test"},
        created_at=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
    )
    current = MarketStateSnapshotRecord(
        trigger="do",
        pdca_phase="do",
        execution_mode="global",
        benchmark_ticker="SPY",
        regime_label="risk_off",
        regime_confidence=0.52,
        summary="Risk-off pressure is now dominant.",
        snapshot_payload={"macro_context": {"active_regimes": ["risk_off"]}},
        source_context={"source": "test"},
        created_at=datetime(2026, 4, 22, 16, 30, tzinfo=timezone.utc),
    )
    session.add_all([previous, current])
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    regime_review = next(item for item in workflows if item["workflow_type"] == "regime_shift_review")

    action = client.post(
        f"/api/v1/learning-workflows/{regime_review['id']}/actions",
        json={
            "item_type": "regime_shift",
            "entity_id": regime_review["items"][0]["entity_id"],
            "action": "complete",
            "summary": "Acknowledged the transition and captured the procedural gaps it exposed.",
            "skill_gaps": [
                {
                    "scope": "strategy:macro",
                    "key": "skill_gap:workflow:risk_off_filter_refresh",
                    "summary": "The risk-off transition exposed that the current regime filter playbook is still too implicit.",
                    "gap_type": "missing_catalog_skill",
                    "ticker": "SPY",
                    "target_skill_code": "detect_risk_off_conditions",
                    "candidate_action": "update_existing_skill",
                    "source_type": "learning_workflow",
                    "importance": 0.83,
                    "evidence": {"transition": "bullish_trend->risk_off"},
                }
            ],
            "skill_candidates": [
                {
                    "scope": "strategy:macro",
                    "key": "skill_candidate:workflow:risk_off_filter_refresh",
                    "summary": "Draft a sharper risk-off checklist revision for fast regime transitions.",
                    "target_skill_code": "detect_risk_off_conditions",
                    "candidate_action": "update_existing_skill",
                    "ticker": "SPY",
                    "strategy_version_id": 101,
                    "importance": 0.81,
                }
            ],
        },
    )
    assert action.status_code == 200
    payload = action.json()

    assert payload["effect"]["resolution_class"] == "regime_shift_review_completed"
    assert payload["effect"]["created_output_count"] == 2
    assert {item["output_type"] for item in payload["effect"]["created_outputs"]} == {
        "skill_gap",
        "skill_candidate",
    }

    gap_item = session.query(MemoryItem).filter(
        MemoryItem.memory_type == "skill_gap",
        MemoryItem.key == "skill_gap:workflow:risk_off_filter_refresh",
    ).one()
    assert gap_item.meta["source_workflow_type"] == "regime_shift_review"
    assert gap_item.meta["source_workflow_review_key"].startswith("regime:")
    assert gap_item.meta["target_skill_code"] == "detect_risk_off_conditions"

    candidate_item = session.query(MemoryItem).filter(
        MemoryItem.memory_type == "skill_candidate",
        MemoryItem.key == "skill_candidate:workflow:risk_off_filter_refresh",
    ).one()
    assert candidate_item.meta["source_workflow_type"] == "regime_shift_review"
    assert candidate_item.meta["candidate_action"] == "update_existing_skill"
    assert candidate_item.meta["target_skill_code"] == "detect_risk_off_conditions"

    artifact_types = {item["artifact_type"] for item in payload["workflow"]["recent_runs"][0]["artifacts"]}
    assert "skill_gap" in artifact_types
    assert "skill_candidate" in artifact_types
    assert "regime_shift_review_completion" in artifact_types
