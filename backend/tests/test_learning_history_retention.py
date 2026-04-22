from datetime import datetime, timedelta, timezone

from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.memory import MemoryItem
from app.domains.learning.repositories import JournalRepository, MemoryRepository
from app.domains.learning.operator_feedback import OperatorDisagreementService
from app.domains.learning.schemas import JournalEntryCreate, MemoryItemCreate
from app.domains.learning.services import (
    JournalService,
    LearningHistoryMaintenanceService,
    LearningMemoryDistillationService,
    MemoryService,
)


def test_journal_service_prunes_noisy_entry_types_automatically(session, monkeypatch) -> None:
    monkeypatch.setattr(JournalService, "RETENTION_LIMITS", {"pdca_do": 3})
    service = JournalService()

    for idx in range(5):
        service.create_entry(
            session,
            JournalEntryCreate(
                entry_type="pdca_do",
                reasoning=f"cycle {idx}",
                observations={"idx": idx},
                decision="observe",
            ),
        )

    entries = service.list_entries(session)
    assert len(entries) == 3
    assert [entry.observations["idx"] for entry in reversed(entries)] == [2, 3, 4]


def test_memory_service_prunes_exact_and_prefix_scopes_automatically(session, monkeypatch) -> None:
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_EXACT", {("episodic", "pdca_check"): 2})
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_PREFIX", {("strategy_evolution", "strategy:"): 2})
    service = MemoryService()

    for idx in range(4):
        service.create_item(
            session,
            MemoryItemCreate(
                memory_type="episodic",
                scope="pdca_check",
                key=f"episodic:{idx}",
                content=f"episodic {idx}",
                importance=0.6,
            ),
        )
    for idx in range(4):
        service.create_item(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope="strategy:1",
                key=f"evolution:{idx}",
                content=f"evolution {idx}",
                importance=0.8,
            ),
        )
    service.create_item(
        session,
        MemoryItemCreate(
            memory_type="lesson",
            scope="global",
            key="lesson:1",
            content="keep me",
            importance=0.95,
        ),
    )

    items = service.list_items(session)
    exact_items = [item for item in items if item.memory_type == "episodic" and item.scope == "pdca_check"]
    scoped_items = [item for item in items if item.memory_type == "strategy_evolution" and item.scope == "strategy:1"]
    lessons = [item for item in items if item.memory_type == "lesson"]

    assert len(exact_items) == 2
    assert {item.key for item in exact_items} == {"episodic:2", "episodic:3"}
    assert len(scoped_items) == 2
    assert {item.key for item in scoped_items} == {"evolution:2", "evolution:3"}
    assert len(lessons) == 1


def test_learning_history_maintenance_reports_and_applies_bulk_pruning(session, monkeypatch) -> None:
    monkeypatch.setattr(JournalService, "RETENTION_LIMITS", {"pdca_act": 2})
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_EXACT", {("episodic", "pdca_act"): 2})
    monkeypatch.setattr(MemoryService, "RETENTION_LIMITS_PREFIX", {("strategy_evolution", "strategy:"): 1})

    journal_repository = JournalRepository()
    memory_repository = MemoryRepository()
    for idx in range(4):
        journal_repository.create(
            session,
            JournalEntryCreate(
                entry_type="pdca_act",
                reasoning=f"act {idx}",
                observations={"idx": idx},
                decision="act",
            ),
        )
    for idx in range(4):
        memory_repository.create(
            session,
            MemoryItemCreate(
                memory_type="episodic",
                scope="pdca_act",
                key=f"act:{idx}",
                content=f"episodic act {idx}",
                importance=0.5,
            ),
        )
    for idx in range(3):
        memory_repository.create(
            session,
            MemoryItemCreate(
                memory_type="strategy_evolution",
                scope="strategy:2",
                key=f"strategy:{idx}",
                content=f"evolution {idx}",
                importance=0.8,
            ),
        )

    maintenance = LearningHistoryMaintenanceService()
    dry_run = maintenance.trim_history(session, dry_run=True)
    assert dry_run["journal"]["deleted_count"] == 2
    assert dry_run["memory"]["deleted_count"] == 4

    applied = maintenance.trim_history(session, dry_run=False)
    assert applied["deleted_total"] == 6
    assert len(JournalService().list_entries(session)) == 2

    items = MemoryService().list_items(session)
    assert len([item for item in items if item.memory_type == "episodic" and item.scope == "pdca_act"]) == 2
    assert len([item for item in items if item.memory_type == "strategy_evolution" and item.scope == "strategy:2"]) == 1


def test_learning_memory_distillation_previews_applies_and_updates_digests(session) -> None:
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            KnowledgeClaim(
                claim_type="review_improvement",
                scope="strategy:14",
                key="claim:distill:stale:1",
                claim_text="Old breakout filter around weak closes needs review.",
                status="validated",
                confidence=0.72,
                freshness_state="current",
                linked_ticker="AAPL",
                evidence_count=2,
                support_count=2,
                contradiction_count=0,
                meta={"source": "test"},
                last_reviewed_at=now - timedelta(days=45),
            ),
            KnowledgeClaim(
                claim_type="review_improvement",
                scope="strategy:14",
                key="claim:distill:stale:2",
                claim_text="A second stale breakout refinement should collapse into the same digest.",
                status="validated",
                confidence=0.67,
                freshness_state="current",
                linked_ticker="AAPL",
                evidence_count=1,
                support_count=1,
                contradiction_count=0,
                meta={"source": "test"},
                last_reviewed_at=now - timedelta(days=40),
            ),
            MemoryItem(
                memory_type="skill_gap",
                scope="strategy:14",
                key="skill_gap:distill:1",
                content="Missing explicit breakout review procedure.",
                meta={
                    "summary": "Missing explicit breakout review procedure.",
                    "gap_type": "missing_catalog_skill",
                    "status": "open",
                    "ticker": "AAPL",
                    "strategy_version_id": 14,
                    "source_type": "trade_review",
                    "target_skill_code": "breakout_review",
                    "candidate_action": "draft_candidate_skill",
                },
                importance=0.79,
            ),
            MemoryItem(
                memory_type="skill_gap",
                scope="strategy:14",
                key="skill_gap:distill:2",
                content="Repeated disagreement still suggests an unresolved breakout review gap.",
                meta={
                    "summary": "Repeated disagreement still suggests an unresolved breakout review gap.",
                    "gap_type": "repeated_operator_disagreement",
                    "status": "open",
                    "ticker": "AAPL",
                    "strategy_version_id": 14,
                    "source_type": "operator_disagreement_cluster",
                    "target_skill_code": "breakout_review",
                    "candidate_action": "update_existing_skill",
                },
                importance=0.82,
            ),
            MemoryItem(
                memory_type="skill_candidate",
                scope="strategy:14",
                key="skill_candidate:distill:1",
                content="Candidate breakout review refinement.",
                meta={
                    "summary": "Candidate breakout review refinement.",
                    "target_skill_code": "breakout_review",
                    "candidate_action": "update_existing_skill",
                    "candidate_status": "draft",
                    "validation_required": True,
                    "source_type": "skill_gap",
                    "source_gap_id": 1,
                    "ticker": "AAPL",
                    "strategy_version_id": 14,
                },
                importance=0.78,
            ),
            MemoryItem(
                memory_type="skill_candidate",
                scope="strategy:14",
                key="skill_candidate:distill:2",
                content="Approved breakout review refinement awaiting cleanup of the older draft.",
                meta={
                    "summary": "Approved breakout review refinement awaiting cleanup of the older draft.",
                    "target_skill_code": "breakout_review",
                    "candidate_action": "update_existing_skill",
                    "candidate_status": "validated",
                    "activation_status": "validated_not_activated",
                    "validation_required": True,
                    "latest_validation_record_id": 33,
                    "source_type": "knowledge_claim",
                    "source_claim_id": 71,
                    "ticker": "AAPL",
                    "strategy_version_id": 14,
                },
                importance=0.84,
            ),
        ]
    )
    session.commit()

    disagreement_service = OperatorDisagreementService()
    disagreement_service.record(
        session,
        disagreement_type="claim_contradicted",
        entity_type="knowledge_claim",
        entity_id=9001,
        action="review",
        summary="Operator rejected the breakout read after a failed follow-through candle.",
        ticker="AAPL",
        source="test",
        details={"target_skill_code": "breakout_review"},
    )
    disagreement_service.record(
        session,
        disagreement_type="claim_contradicted",
        entity_type="knowledge_claim",
        entity_id=9002,
        action="review",
        summary="Another review found the same breakout procedure too optimistic.",
        ticker="AAPL",
        source="test",
        details={"target_skill_code": "breakout_review"},
    )

    service = LearningMemoryDistillationService()
    dry_run = service.distill_memory(session, dry_run=True, min_group_size=2)
    sections = {item["distillation_type"]: item for item in dry_run["sections"]}

    assert dry_run["created_count"] == 4
    assert sections["claim_review_digest"]["digest_count"] == 1
    assert sections["claim_review_digest"]["digests"][0]["action"] == "create"
    assert sections["claim_review_digest"]["digests"][0]["source_count"] == 2
    assert sections["claim_review_digest"]["digests"][0]["memory_id"] is None
    assert sections["operator_disagreement_digest"]["digest_count"] == 1
    assert sections["operator_disagreement_digest"]["digests"][0]["meta"]["total_event_count"] == 2
    assert sections["skill_gap_digest"]["digest_count"] == 1
    assert sections["skill_gap_digest"]["digests"][0]["meta"]["open_gap_count"] == 2
    assert sections["skill_candidate_digest"]["digest_count"] == 1
    assert sections["skill_candidate_digest"]["digests"][0]["meta"]["collapse_backlog_recommended"] is True

    applied = service.distill_memory(session, dry_run=False, min_group_size=2)
    assert applied["created_count"] == 4
    distillations = session.query(MemoryItem).filter(MemoryItem.memory_type == "learning_distillation").all()
    assert len(distillations) == 4

    claim_digest = next(item for item in distillations if item.meta.get("distillation_type") == "claim_review_digest")
    disagreement_digest = next(
        item for item in distillations if item.meta.get("distillation_type") == "operator_disagreement_digest"
    )
    gap_digest = next(item for item in distillations if item.meta.get("distillation_type") == "skill_gap_digest")
    candidate_digest = next(item for item in distillations if item.meta.get("distillation_type") == "skill_candidate_digest")
    assert set(claim_digest.meta["claim_ids"]) == {
        claim.id
        for claim in session.query(KnowledgeClaim).filter(KnowledgeClaim.key.like("claim:distill:stale:%")).all()
    }
    assert disagreement_digest.meta["total_event_count"] == 2
    assert gap_digest.meta["open_gap_count"] == 2
    assert candidate_digest.meta["collapse_backlog_recommended"] is True

    disagreement_service.record(
        session,
        disagreement_type="claim_contradicted",
        entity_type="knowledge_claim",
        entity_id=9003,
        action="review",
        summary="A third disagreement confirmed the same breakout issue again.",
        ticker="AAPL",
        source="test",
        details={"target_skill_code": "breakout_review"},
    )

    updated = service.distill_memory(session, dry_run=False, min_group_size=2)
    updated_sections = {item["distillation_type"]: item for item in updated["sections"]}
    assert updated["updated_count"] >= 1
    assert updated_sections["operator_disagreement_digest"]["digests"][0]["action"] == "update"

    refreshed_disagreement_digest = session.get(MemoryItem, disagreement_digest.id)
    assert refreshed_disagreement_digest is not None
    assert refreshed_disagreement_digest.meta["total_event_count"] == 3


def test_memory_distillation_api_previews_and_applies(client, session) -> None:
    create_one = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:31",
            "key": "claim:api-distill:1",
            "claim_type": "review_improvement",
            "claim_text": "First stale claim for API distillation.",
            "linked_ticker": "MSFT",
            "status": "validated",
            "meta": {"source": "test"},
        },
    )
    create_two = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:31",
            "key": "claim:api-distill:2",
            "claim_type": "review_improvement",
            "claim_text": "Second stale claim for API distillation.",
            "linked_ticker": "MSFT",
            "status": "validated",
            "meta": {"source": "test"},
        },
    )
    assert create_one.status_code == 201
    assert create_two.status_code == 201

    claim_ids = [create_one.json()["id"], create_two.json()["id"]]
    for claim_id in claim_ids:
        claim = session.get(KnowledgeClaim, claim_id)
        assert claim is not None
        claim.last_reviewed_at = datetime.now(timezone.utc) - timedelta(days=50)
        session.add(claim)
    session.commit()

    preview = client.post(
        "/api/v1/memory/maintenance/distill",
        json={
            "dry_run": True,
            "include_claim_reviews": True,
            "include_operator_feedback": False,
            "include_skill_gaps": False,
            "include_skill_candidates": False,
            "min_group_size": 2,
        },
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    preview_sections = {item["distillation_type"]: item for item in preview_payload["sections"]}
    assert preview_payload["created_count"] == 1
    assert preview_sections["claim_review_digest"]["digests"][0]["memory_id"] is None

    apply = client.post(
        "/api/v1/memory/maintenance/distill",
        json={
            "dry_run": False,
            "include_claim_reviews": True,
            "include_operator_feedback": False,
            "include_skill_gaps": False,
            "include_skill_candidates": False,
            "min_group_size": 2,
        },
    )
    assert apply.status_code == 200
    apply_payload = apply.json()
    apply_sections = {item["distillation_type"]: item for item in apply_payload["sections"]}
    assert apply_payload["created_count"] == 1
    assert apply_sections["claim_review_digest"]["digests"][0]["memory_id"] is not None

    distillations = session.query(MemoryItem).filter(MemoryItem.memory_type == "learning_distillation").all()
    assert len(distillations) == 1
    assert distillations[0].meta["review_reason"] == "freshness_review_due"


def test_skill_gap_digest_review_can_collapse_group_into_candidate(client, session) -> None:
    for idx in range(2):
        created = client.post(
            "/api/v1/memory",
            json={
                "memory_type": "skill_gap",
                "scope": "strategy:81",
                "key": f"skill_gap:digest-review:{idx}",
                "content": f"Repeated breakout review gap {idx}.",
                "meta": {
                    "summary": f"Repeated breakout review gap {idx}.",
                    "gap_type": "missing_catalog_skill",
                    "status": "open",
                    "ticker": "NVDA",
                    "strategy_version_id": 81,
                    "source_type": "trade_review",
                    "source_trade_review_id": 300 + idx,
                    "target_skill_code": "review_false_breakout",
                    "candidate_action": "draft_candidate_skill",
                },
                "importance": 0.8 + (idx * 0.01),
            },
        )
        assert created.status_code == 201

    distilled = client.post(
        "/api/v1/memory/maintenance/distill",
        json={
            "dry_run": False,
            "include_claim_reviews": False,
            "include_operator_feedback": False,
            "include_skill_gaps": True,
            "include_skill_candidates": False,
            "min_group_size": 2,
        },
    )
    assert distilled.status_code == 200
    digest_id = distilled.json()["sections"][0]["digests"][0]["memory_id"]

    listing = client.get("/api/v1/memory/maintenance/digests", params={"distillation_type": "skill_gap_digest", "include_reviewed": False})
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["id"] == digest_id

    reviewed = client.post(
        f"/api/v1/memory/maintenance/digests/{digest_id}/review",
        json={
            "action": "collapse",
            "summary": "Collapse repeated gap backlog into a single candidate for explicit review.",
        },
    )
    assert reviewed.status_code == 200
    reviewed_payload = reviewed.json()
    candidate_id = reviewed_payload["effect"]["candidate_id"]
    assert candidate_id is not None
    assert reviewed_payload["digest"]["meta"]["review_action"] == "collapse"
    assert reviewed_payload["digest"]["meta"]["review_effect"]["candidate_id"] == candidate_id

    gaps = session.query(MemoryItem).filter(MemoryItem.memory_type == "skill_gap", MemoryItem.scope == "strategy:81").all()
    assert len(gaps) == 2
    assert all(gap.meta["status"] == "collapsed" for gap in gaps)
    assert {gap.meta["collapsed_into_candidate_id"] for gap in gaps} == {candidate_id}

    candidate = session.get(MemoryItem, candidate_id)
    assert candidate is not None
    assert candidate.memory_type == "skill_candidate"

    pending_listing = client.get(
        "/api/v1/memory/maintenance/digests",
        params={"distillation_type": "skill_gap_digest", "include_reviewed": False},
    )
    assert pending_listing.status_code == 200
    assert pending_listing.json() == []


def test_skill_candidate_digest_review_can_collapse_or_retire_backlog(client, session) -> None:
    keep_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:91",
            "key": "skill_candidate:digest-collapse:keep",
            "content": "Validated candidate that should survive collapse.",
            "meta": {
                "summary": "Validated candidate that should survive collapse.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "validated",
                "activation_status": "validated_not_activated",
                "validation_required": True,
                "latest_validation_record_id": 501,
                "source_type": "knowledge_claim",
                "source_claim_id": 18,
                "ticker": "QQQ",
                "strategy_version_id": 91,
            },
            "importance": 0.88,
        },
    )
    retire_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:91",
            "key": "skill_candidate:digest-collapse:retire",
            "content": "Older draft candidate that should be retired.",
            "meta": {
                "summary": "Older draft candidate that should be retired.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "skill_gap",
                "source_gap_id": 22,
                "ticker": "QQQ",
                "strategy_version_id": 91,
            },
            "importance": 0.74,
        },
    )
    assert keep_candidate.status_code == 201
    assert retire_candidate.status_code == 201
    keep_candidate_id = keep_candidate.json()["id"]
    retire_candidate_id = retire_candidate.json()["id"]

    collapsed_digest = client.post(
        "/api/v1/memory/maintenance/distill",
        json={
            "dry_run": False,
            "include_claim_reviews": False,
            "include_operator_feedback": False,
            "include_skill_gaps": False,
            "include_skill_candidates": True,
            "min_group_size": 2,
        },
    )
    assert collapsed_digest.status_code == 200
    collapse_digest_id = collapsed_digest.json()["sections"][0]["digests"][0]["memory_id"]

    collapse_review = client.post(
        f"/api/v1/memory/maintenance/digests/{collapse_digest_id}/review",
        json={
            "action": "collapse",
            "summary": "Keep the validated candidate and retire the leftover draft backlog.",
        },
    )
    assert collapse_review.status_code == 200
    collapse_payload = collapse_review.json()
    assert collapse_payload["effect"]["kept_candidate_id"] == keep_candidate_id
    assert retire_candidate_id in collapse_payload["effect"]["retired_candidate_ids"]

    kept = session.get(MemoryItem, keep_candidate_id)
    retired = session.get(MemoryItem, retire_candidate_id)
    assert kept is not None and retired is not None
    assert kept.meta["collapse_digest_id"] == collapse_digest_id
    assert retired.meta["candidate_status"] == "retired"
    assert retired.meta["retired_in_favor_of_candidate_id"] == keep_candidate_id

    first_rejected = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:92",
            "key": "skill_candidate:digest-retire:1",
            "content": "Rejected backlog candidate 1.",
            "meta": {
                "summary": "Rejected backlog candidate 1.",
                "target_skill_code": "review_false_breakout",
                "candidate_action": "draft_candidate_skill",
                "candidate_status": "rejected",
                "activation_status": "rejected",
                "validation_required": True,
                "source_type": "knowledge_claim",
                "source_claim_id": 201,
                "ticker": "AAPL",
                "strategy_version_id": 92,
            },
            "importance": 0.62,
        },
    )
    second_rejected = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:92",
            "key": "skill_candidate:digest-retire:2",
            "content": "Rejected backlog candidate 2.",
            "meta": {
                "summary": "Rejected backlog candidate 2.",
                "target_skill_code": "review_false_breakout",
                "candidate_action": "draft_candidate_skill",
                "candidate_status": "rejected",
                "activation_status": "rejected",
                "validation_required": True,
                "source_type": "skill_gap",
                "source_gap_id": 202,
                "ticker": "AAPL",
                "strategy_version_id": 92,
            },
            "importance": 0.61,
        },
    )
    assert first_rejected.status_code == 201
    assert second_rejected.status_code == 201

    retired_digest = client.post(
        "/api/v1/memory/maintenance/distill",
        json={
            "dry_run": False,
            "include_claim_reviews": False,
            "include_operator_feedback": False,
            "include_skill_gaps": False,
            "include_skill_candidates": True,
            "min_group_size": 2,
        },
    )
    assert retired_digest.status_code == 200
    retire_sections = {
        item["distillation_type"]: item
        for item in retired_digest.json()["sections"]
    }
    retire_digest_id = next(
        digest["memory_id"]
        for digest in retire_sections["skill_candidate_digest"]["digests"]
        if digest["meta"].get("candidate_anchor") == "review_false_breakout"
    )

    retire_review = client.post(
        f"/api/v1/memory/maintenance/digests/{retire_digest_id}/review",
        json={
            "action": "retire",
            "summary": "Retire fully rejected backlog that has no validated survivor.",
        },
    )
    assert retire_review.status_code == 200
    retire_payload = retire_review.json()
    assert sorted(retire_payload["effect"]["retired_candidate_ids"]) == sorted(
        [first_rejected.json()["id"], second_rejected.json()["id"]]
    )

    retired_items = (
        session.query(MemoryItem)
        .filter(MemoryItem.scope == "strategy:92", MemoryItem.memory_type == "skill_candidate")
        .all()
    )
    assert all(item.meta["candidate_status"] == "retired" for item in retired_items)
