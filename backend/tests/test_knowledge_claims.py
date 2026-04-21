from datetime import datetime, timedelta, timezone

from app.db.models.journal import JournalEntry
from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.memory import MemoryItem
from app.domains.learning.claims import KnowledgeClaimService


def test_claims_api_supports_create_and_evidence_upsert(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:42",
            "key": "claim:test-breakout-filter",
            "claim_type": "review_improvement",
            "claim_text": "Breakout entries need stronger volume confirmation.",
            "linked_ticker": "NVDA",
            "strategy_version_id": None,
            "status": "provisional",
            "confidence": 0.55,
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    first_evidence = client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:123",
            "stance": "support",
            "summary": "Trade review showed the breakout failed on weak volume.",
            "evidence_payload": {"pnl_pct": -6.0},
            "strength": 0.72,
        },
    )
    assert first_evidence.status_code == 200

    second_evidence = client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:123",
            "stance": "support",
            "summary": "Updated evidence payload for the same review.",
            "evidence_payload": {"pnl_pct": -5.5, "note": "updated"},
            "strength": 0.78,
        },
    )
    assert second_evidence.status_code == 200
    assert second_evidence.json()["id"] == first_evidence.json()["id"]

    listing = client.get("/api/v1/claims", params={"scope": "strategy:42"})
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    claim_payload = listing.json()[0]
    assert claim_payload["status"] == "supported"
    assert claim_payload["evidence_count"] == 1
    assert claim_payload["support_count"] == 1

    evidence_listing = client.get(f"/api/v1/claims/{claim_id}/evidence")
    assert evidence_listing.status_code == 200
    assert len(evidence_listing.json()) == 1
    assert evidence_listing.json()[0]["evidence_payload"]["note"] == "updated"

    claim = session.get(KnowledgeClaim, claim_id)
    assert claim is not None
    assert claim.confidence == 0.78

    promoted = client.post(
        f"/api/v1/claims/{claim_id}/review",
        json={
            "outcome": "confirm",
            "summary": "A second confirmed review supports promoting this into a procedural candidate.",
            "source_key": "claim_review:confirm:bridge",
            "strength": 0.74,
            "evidence_payload": {"reviewer": "test"},
        },
    )
    assert promoted.status_code == 200
    promoted_payload = promoted.json()
    assert promoted_payload["claim"]["status"] == "validated"
    assert promoted_payload["promoted_skill_candidate"] is not None
    assert promoted_payload["promoted_skill_candidate"]["source_type"] == "knowledge_claim"
    assert promoted_payload["promoted_skill_candidate"]["meta"]["source_claim_id"] == claim_id
    assert promoted_payload["promoted_skill_candidate"]["candidate_status"] == "draft"

    promoted_claim = session.get(KnowledgeClaim, claim_id)
    assert promoted_claim is not None
    assert promoted_claim.meta["linked_skill_candidate_id"] is not None


def test_claims_review_queue_marks_stale_claims(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:77",
            "key": "claim:test-stale-filter",
            "claim_type": "review_improvement",
            "claim_text": "Old breakout filter needs review.",
            "linked_ticker": "AAPL",
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:900",
            "stance": "support",
            "summary": "Historical support evidence.",
            "evidence_payload": {},
            "strength": 0.7,
        },
    ).status_code == 200

    claim = session.get(KnowledgeClaim, claim_id)
    assert claim is not None
    claim.last_reviewed_at = datetime.now(timezone.utc) - timedelta(days=45)
    session.add(claim)
    session.commit()

    queue = client.get("/api/v1/claims/review-queue")
    assert queue.status_code == 200
    queue_payload = queue.json()
    stale_item = next(item for item in queue_payload if item["claim_id"] == claim_id)
    assert stale_item["review_reason"] == "freshness_review_due"
    assert stale_item["freshness_state"] == "stale"

    refreshed = client.get(f"/api/v1/claims/{claim_id}")
    assert refreshed.status_code == 200
    assert refreshed.json()["freshness_state"] == "stale"


def test_claim_review_can_contradict_and_retire_claim(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:88",
            "key": "claim:test-retire-filter",
            "claim_type": "review_improvement",
            "claim_text": "Breakout filter under review.",
            "linked_ticker": "MSFT",
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:901",
            "stance": "support",
            "summary": "Initial support evidence.",
            "evidence_payload": {},
            "strength": 0.76,
        },
    ).status_code == 200

    contradiction = client.post(
        f"/api/v1/claims/{claim_id}/review",
        json={
            "outcome": "contradict",
            "summary": "New evidence shows the filter fails under current volatility regime.",
            "source_key": "claim_review:contradict:1",
            "strength": 0.8,
            "evidence_payload": {"reviewer": "test"},
        },
    )
    assert contradiction.status_code == 200
    contradiction_payload = contradiction.json()
    assert contradiction_payload["claim"]["status"] == "contested"
    assert contradiction_payload["claim"]["freshness_state"] == "current"
    assert contradiction_payload["evidence"] is not None
    assert contradiction_payload["evidence"]["source_type"] == "claim_review"
    assert contradiction_payload["evidence"]["stance"] == "contradict"
    disagreement_entries = session.query(JournalEntry).filter(JournalEntry.entry_type == "operator_disagreement").all()
    disagreement_memories = session.query(MemoryItem).filter(MemoryItem.memory_type == "operator_disagreement").all()
    assert len(disagreement_entries) == 1
    assert len(disagreement_memories) == 1
    assert disagreement_entries[0].observations["operator_disagreement"]["disagreement_type"] == "claim_contradicted"

    retire = client.post(
        f"/api/v1/claims/{claim_id}/review",
        json={
            "outcome": "retire",
            "summary": "Claim retired after contradictory evidence persisted.",
            "source_key": "claim_review:retire:1",
            "strength": 0.6,
            "evidence_payload": {"reviewer": "test"},
        },
    )
    assert retire.status_code == 200
    retire_payload = retire.json()
    assert retire_payload["claim"]["status"] == "retired"
    assert retire_payload["claim"]["freshness_state"] == "stale"
    assert retire_payload["evidence"] is None
    disagreement_entries = session.query(JournalEntry).filter(JournalEntry.entry_type == "operator_disagreement").all()
    disagreement_memories = session.query(MemoryItem).filter(MemoryItem.memory_type == "operator_disagreement").all()
    assert len(disagreement_entries) == 2
    assert len(disagreement_memories) == 2
    assert any(
        entry.observations["operator_disagreement"]["disagreement_type"] == "claim_retired"
        for entry in disagreement_entries
    )

    runtime_packets = KnowledgeClaimService().build_runtime_packets(
        session,
        ticker="MSFT",
        strategy_version_id=None,
        max_packets=3,
    )
    assert runtime_packets == []


def test_claim_can_be_promoted_manually_to_skill_candidate(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:101",
            "key": "claim:test-context-rule-promotion",
            "claim_type": "context_rule",
            "claim_text": "Breakout contexts with strong evidence should reinforce the breakout evaluation procedure.",
            "linked_ticker": None,
            "strategy_version_id": 101,
            "status": "provisional",
            "confidence": 0.61,
            "freshness_state": "current",
            "meta": {
                "source": "feature_outcome_stat",
                "feature_scope": "quant",
                "feature_key": "setup",
                "feature_value": "breakout",
            },
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "strategy_context_rule",
            "source_key": "rule:test:breakout",
            "stance": "support",
            "summary": "Aggregated breakout evidence supports reinforcing this procedure.",
            "evidence_payload": {"sample_size": 5, "avg_pnl_pct": 2.4},
            "strength": 0.81,
        },
    ).status_code == 200

    promoted = client.post(f"/api/v1/claims/{claim_id}/promote")
    assert promoted.status_code == 200
    payload = promoted.json()
    assert payload["target_skill_code"] == "evaluate_daily_breakout"
    assert payload["candidate_action"] == "update_existing_skill"
    assert payload["source_type"] == "knowledge_claim"
    assert payload["meta"]["source_claim_id"] == claim_id

    promoted_claim = session.get(KnowledgeClaim, claim_id)
    assert promoted_claim is not None
    assert promoted_claim.meta["linked_skill_candidate_id"] is not None


def test_operator_disagreements_api_lists_and_summarizes_structured_disagreements(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:88",
            "key": "claim:test-operator-disagreement-summary",
            "claim_type": "review_improvement",
            "claim_text": "Breakout filter under review.",
            "linked_ticker": "MSFT",
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:902",
            "stance": "support",
            "summary": "Initial support evidence.",
            "evidence_payload": {},
            "strength": 0.7,
        },
    ).status_code == 200

    contradiction = client.post(
        f"/api/v1/claims/{claim_id}/review",
        json={
            "outcome": "contradict",
            "summary": "Operator disagrees with the current claim under the latest volatility regime.",
            "source_key": "claim_review:contradict:summary-test",
            "strength": 0.75,
            "evidence_payload": {"reviewer": "test"},
        },
    )
    assert contradiction.status_code == 200

    disagreements = client.get("/api/v1/operator-disagreements?limit=10")
    assert disagreements.status_code == 200
    disagreement_payload = disagreements.json()
    assert len(disagreement_payload) == 1
    assert disagreement_payload[0]["disagreement_type"] == "claim_contradicted"
    assert disagreement_payload[0]["entity_type"] == "knowledge_claim"
    assert disagreement_payload[0]["ticker"] == "MSFT"

    summary = client.get("/api/v1/operator-disagreements/summary")
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["total_events"] == 1
    assert summary_payload["by_disagreement_type"][0]["label"] == "claim_contradicted"
    assert summary_payload["by_entity_type"][0]["label"] == "knowledge_claim"
    assert summary_payload["by_ticker"][0]["label"] == "MSFT"
    assert summary_payload["by_target_skill_code"][0]["label"] == "claim:test-operator-disagreement-summary"


def test_operator_disagreement_clusters_can_sync_and_promote_to_claim(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:91",
            "key": "claim:test-operator-disagreement-cluster",
            "claim_type": "review_improvement",
            "claim_text": "Breakout filter under review.",
            "linked_ticker": "AAPL",
            "strategy_version_id": 91,
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:cluster:1",
            "stance": "support",
            "summary": "Initial support evidence.",
            "evidence_payload": {},
            "strength": 0.7,
        },
    ).status_code == 200

    for suffix in ("a", "b"):
        response = client.post(
            f"/api/v1/claims/{claim_id}/review",
            json={
                "outcome": "contradict",
                "summary": f"Operator disagreement event {suffix}.",
                "source_key": f"claim_review:contradict:cluster:{suffix}",
                "strength": 0.74,
                "evidence_payload": {"reviewer": "test", "suffix": suffix},
            },
        )
        assert response.status_code == 200

    clusters = client.get("/api/v1/operator-disagreements/clusters?sync=true&limit=10&min_count=2")
    assert clusters.status_code == 200
    cluster_payload = clusters.json()
    assert len(cluster_payload) == 1
    cluster_id = cluster_payload[0]["id"]
    assert cluster_payload[0]["event_count"] == 2
    assert cluster_payload[0]["claim_key"] == "claim:test-operator-disagreement-cluster"

    promoted = client.post(f"/api/v1/operator-disagreements/clusters/{cluster_id}/promote")
    assert promoted.status_code == 200
    promoted_payload = promoted.json()
    assert promoted_payload["cluster"]["status"] == "promoted"
    assert promoted_payload["cluster"]["promoted_claim_id"] is not None
    assert promoted_payload["claim"]["claim_type"] == "operator_disagreement_pattern"
    assert promoted_payload["claim"]["meta"]["cluster_id"] == cluster_id


def test_operator_disagreement_clusters_can_promote_to_skill_gap(client, session) -> None:
    create = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:92",
            "key": "claim:test-operator-disagreement-gap",
            "claim_type": "review_improvement",
            "claim_text": "Operator keeps disagreeing with this claim.",
            "linked_ticker": "AAPL",
            "strategy_version_id": 92,
            "freshness_state": "current",
            "meta": {"source": "test"},
        },
    )
    assert create.status_code == 201
    claim_id = create.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:cluster-gap:1",
            "stance": "support",
            "summary": "Initial support evidence.",
            "evidence_payload": {},
            "strength": 0.7,
        },
    ).status_code == 200

    for suffix in ("a", "b"):
        response = client.post(
            f"/api/v1/claims/{claim_id}/review",
            json={
                "outcome": "contradict",
                "summary": f"Operator disagreement gap event {suffix}.",
                "source_key": f"claim_review:contradict:gap:{suffix}",
                "strength": 0.72,
                "evidence_payload": {"reviewer": "test", "suffix": suffix},
            },
        )
        assert response.status_code == 200

    clusters = client.get("/api/v1/operator-disagreements/clusters?sync=true&limit=10&min_count=2")
    assert clusters.status_code == 200
    cluster_payload = clusters.json()
    assert len(cluster_payload) == 1
    cluster_id = cluster_payload[0]["id"]

    promoted = client.post(f"/api/v1/operator-disagreements/clusters/{cluster_id}/promote-gap")
    assert promoted.status_code == 200
    promoted_payload = promoted.json()
    assert promoted_payload["cluster"]["status"] == "promoted"
    assert promoted_payload["cluster"]["promoted_skill_gap_id"] is not None
    assert promoted_payload["gap"]["gap_type"] == "repeated_operator_disagreement"
    assert promoted_payload["gap"]["source_type"] == "operator_disagreement_cluster"
    assert promoted_payload["gap"]["meta"]["source_operator_disagreement_cluster_id"] == cluster_id
