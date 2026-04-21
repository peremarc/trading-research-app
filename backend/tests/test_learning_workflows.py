from datetime import datetime, timedelta, timezone

from app.db.models.journal import JournalEntry
from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.learning_workflow import LearningWorkflow
from app.db.models.memory import MemoryItem


def test_learning_workflows_list_defaults_to_persisted_state_without_sync(client, session) -> None:
    response = client.get("/api/v1/learning-workflows")

    assert response.status_code == 200
    assert response.json() == []
    assert session.query(LearningWorkflow).count() == 0


def test_learning_workflows_sync_claim_review_and_skill_audit(client, session) -> None:
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

    weekly_audit = next(item for item in payload if item["workflow_type"] == "weekly_skill_audit")
    assert weekly_audit["status"] == "open"
    assert any(item["item_type"] == "skill_gap" for item in weekly_audit["items"])
    assert any(item["item_type"] == "skill_candidate_audit" for item in weekly_audit["items"])
    assert weekly_audit["context"]["open_skill_gap_count"] == 1
    assert weekly_audit["context"]["draft_skill_candidate_count"] >= 1

    stored_workflows = session.query(LearningWorkflow).all()
    assert len(stored_workflows) == 2


def test_stale_claim_review_workflow_action_updates_claim_and_workflow(client, session) -> None:
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

    detail = client.get(f"/api/v1/learning-workflows/{stale_review['id']}", params={"history_limit": 12})
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert any(entry["change_class"] == "workflow_resolved" for entry in detail_payload["history"])
    assert any(entry["resolution_class"] == "claim_confirmed" for entry in detail_payload["history"])


def test_weekly_skill_audit_workflow_actions_resolve_gap_and_candidate(client, session) -> None:
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
