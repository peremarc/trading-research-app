from datetime import datetime, timezone

from app.db.models.journal import JournalEntry
from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.market_state_snapshot import MarketStateSnapshotRecord
from app.db.models.memory import MemoryItem
from app.domains.learning import api as learning_api
from app.domains.learning.skills import SkillLifecycleService, SkillRouterService
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


def test_skill_catalog_endpoint_lists_initial_skills(client) -> None:
    response = client.get("/api/v1/skills/catalog")

    assert response.status_code == 200
    payload = response.json()
    codes = {item["code"] for item in payload}
    assert {
        "analyze_ticker_post_news",
        "evaluate_daily_breakout",
        "evaluate_support_reclaim_reversal",
        "detect_risk_off_conditions",
        "do_trade_post_mortem",
        "classify_operational_error",
        "propose_pdca_improvement",
    } <= codes


def test_skill_router_routes_news_breakout_and_risk_off_contexts() -> None:
    router = SkillRouterService()

    routed = router.route_trade_candidate(
        ticker="AAL",
        signal_payload={
            "quant_summary": {"setup": "breakout", "trend": "uptrend"},
            "visual_summary": {"setup_type": "breakout"},
        },
        strategy_rules={"preferred_setups": ["breakout"]},
        market_context={"market_state_regime": "high_volatility_risk_off"},
        macro_context={"active_regimes": ["high_volatility_risk_off"]},
        calendar_context={"expiry_context": {"expiration_week": True, "phase": "pre_expiry"}},
        news_context={"article_count": 2, "catalyst_hits": 1},
        price_action_context={"available": True, "primary_signal_code": "failed_breakdown_reversal"},
        intermarket_context={"applicable": True, "available": True, "requires_caution": True},
        mstr_context={"applicable": False, "available": False},
        regime_policy={"playbook": "breakout_long", "risk_multiplier": 0.7},
        risk_budget={"event_risk_flags": ["earnings_near"]},
    )

    assert routed["catalog_version"] == "skills_v1"
    assert routed["routing_mode"] == "deterministic_v1"
    assert routed["phase"] == "do"
    applied_codes = [item["code"] for item in routed["applied_skills"]]
    assert applied_codes[0] == "detect_risk_off_conditions"
    assert "analyze_ticker_post_news" in applied_codes
    assert "evaluate_daily_breakout" in applied_codes or "evaluate_support_reclaim_reversal" in applied_codes
    assert routed["risk_skill_active"] is True


def test_skill_candidate_validation_creates_active_revision_and_runtime_overlay(client, session) -> None:
    created = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:42",
            "key": "skill_candidate:test:1",
            "content": "Tighten risk-off handling around fragile breakout contexts.",
            "meta": {
                "summary": "Tighten risk-off handling around fragile breakout contexts.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "source_trade_review_id": 7,
                "ticker": "NVDA",
                "strategy_version_id": 42,
                "position_id": 9,
            },
            "importance": 0.8,
        },
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "paper",
            "validation_outcome": "approve",
            "summary": "Paper results support a stricter risk-off overlay for breakout entries.",
            "sample_size": 18,
            "win_rate": 61.5,
            "avg_pnl_pct": 1.8,
            "max_drawdown_pct": -2.4,
            "evidence": {"source": "paper_batch"},
            "activate": True,
        },
    )
    assert validated.status_code == 200
    payload = validated.json()
    assert payload["activation_status"] == "active"
    assert payload["candidate"]["candidate_status"] == "validated"
    assert payload["revision"]["skill_code"] == "detect_risk_off_conditions"
    assert payload["revision"]["activation_status"] == "active"
    assert payload["validation_record"]["candidate_id"] == candidate_id
    assert payload["validation_record"]["revision_id"] == payload["revision"]["id"]

    dashboard = client.get("/api/v1/skills/dashboard")
    assert dashboard.status_code == 200
    dashboard_payload = dashboard.json()
    assert dashboard_payload["candidates"][0]["candidate_status"] == "validated"
    assert dashboard_payload["active_revisions"][0]["skill_code"] == "detect_risk_off_conditions"

    runtime = SkillLifecycleService().attach_runtime_state(
        session,
        {
            "catalog_version": "skills_v1",
            "routing_mode": "deterministic_v1",
            "phase": "do",
            "considered_skills": [{"code": "detect_risk_off_conditions"}],
            "applied_skills": [{"code": "detect_risk_off_conditions"}],
            "primary_skill_code": "detect_risk_off_conditions",
            "risk_skill_active": True,
            "summary": "Primary skill selected: detect_risk_off_conditions.",
        },
    )
    assert runtime["active_revision_count"] == 1
    assert runtime["active_revisions"][0]["skill_code"] == "detect_risk_off_conditions"


def test_skill_runtime_packets_build_compact_on_demand_instructions(client, session) -> None:
    created = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:7",
            "key": "skill_candidate:test:runtime-packet",
            "content": "Tighten risk-off handling during expiration week and event-risk windows.",
            "meta": {
                "summary": "Tighten risk-off handling during expiration week and event-risk windows.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "source_trade_review_id": 11,
                "ticker": "AAL",
                "strategy_version_id": 7,
                "position_id": 3,
            },
            "importance": 0.85,
        },
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "replay",
            "validation_outcome": "approve",
            "summary": "Validated tighter execution caution around expiry and event-risk windows.",
            "sample_size": 22,
            "win_rate": 59.1,
            "avg_pnl_pct": 1.4,
            "max_drawdown_pct": -3.1,
            "evidence": {"source": "replay_batch"},
            "activate": True,
        },
    )
    assert validated.status_code == 200

    lifecycle = SkillLifecycleService()
    packets = lifecycle.build_runtime_packets(
        session,
        skill_context={
            "catalog_version": "skills_v1",
            "routing_mode": "deterministic_v1",
            "phase": "do",
            "applied_skills": [
                {
                    "code": "detect_risk_off_conditions",
                    "reason": "macro, event-risk or expiry context suggests degraded execution quality",
                    "confidence": 0.9,
                }
            ],
            "primary_skill_code": "detect_risk_off_conditions",
            "risk_skill_active": True,
        },
        max_packets=2,
        max_steps_per_packet=4,
    )

    assert len(packets) == 1
    packet = packets[0]
    assert packet["skill_code"] == "detect_risk_off_conditions"
    assert packet["instruction_source"] == "catalog_plus_active_revision"
    assert packet["validated_revision_summary"] == "Validated tighter execution caution around expiry and event-risk windows."
    assert packet["procedure_steps"]
    prompt = lifecycle.render_runtime_skill_prompt(packets)
    assert "Skill `detect_risk_off_conditions`" in prompt
    assert "Validated tighter execution caution around expiry and event-risk windows." in prompt


def test_skill_dashboard_includes_learning_distillation_digests(client) -> None:
    first_gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:88",
            "key": "skill_gap:test:distill-dashboard:1",
            "content": "Repeated breakout review gap still unresolved.",
            "meta": {
                "summary": "Repeated breakout review gap still unresolved.",
                "gap_type": "missing_catalog_skill",
                "status": "open",
                "ticker": "AAPL",
                "strategy_version_id": 88,
                "source_type": "trade_review",
                "target_skill_code": "breakout_review",
                "candidate_action": "draft_candidate_skill",
            },
            "importance": 0.77,
        },
    )
    assert first_gap.status_code == 201

    second_gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:88",
            "key": "skill_gap:test:distill-dashboard:2",
            "content": "Another breakout review gap confirms the same backlog cluster.",
            "meta": {
                "summary": "Another breakout review gap confirms the same backlog cluster.",
                "gap_type": "repeated_operator_disagreement",
                "status": "open",
                "ticker": "AAPL",
                "strategy_version_id": 88,
                "source_type": "operator_disagreement_cluster",
                "target_skill_code": "breakout_review",
                "candidate_action": "update_existing_skill",
            },
            "importance": 0.81,
        },
    )
    assert second_gap.status_code == 201

    distilled = client.post(
        "/api/v1/memory/maintenance/distill",
        json={
            "dry_run": False,
            "include_claim_reviews": False,
            "include_operator_feedback": False,
            "include_skill_gaps": True,
            "include_skill_candidates": False,
            "skill_gap_limit": 50,
            "min_group_size": 2,
        },
    )
    assert distilled.status_code == 200

    dashboard = client.get("/api/v1/skills/dashboard")
    assert dashboard.status_code == 200
    payload = dashboard.json()
    distillations = payload["distillations"]
    assert any(
        item["meta"].get("distillation_type") == "skill_gap_digest"
        and item["meta"].get("target_skill_code") == "breakout_review"
        for item in distillations
    )


def test_skill_gaps_endpoint_lists_persisted_gap_items(client) -> None:
    created = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:99",
            "key": "skill_gap:test:missing_catalog",
            "content": "Current catalog lacks a procedure for this recurring review pattern.",
            "meta": {
                "summary": "Current catalog lacks a procedure for this recurring review pattern.",
                "gap_type": "missing_catalog_skill",
                "status": "open",
                "ticker": "AAL",
                "strategy_version_id": 99,
                "position_id": 12,
                "source_type": "trade_review",
                "source_trade_review_id": 33,
                "target_skill_code": "review_false_breakout",
                "candidate_action": "draft_candidate_skill",
            },
            "importance": 0.81,
        },
    )
    assert created.status_code == 201

    response = client.get("/api/v1/skills/gaps")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["gap_type"] == "missing_catalog_skill"
    assert payload[0]["target_skill_code"] == "review_false_breakout"


def test_skill_gap_and_candidate_detail_endpoints_return_single_items(client) -> None:
    gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:66",
            "key": "skill_gap:test:detail",
            "content": "Detail endpoint gap.",
            "meta": {
                "summary": "Detail endpoint gap.",
                "gap_type": "missing_entry_skill_context",
                "status": "open",
                "ticker": "LZM",
                "strategy_version_id": 66,
                "position_id": 9,
                "source_type": "trade_review",
                "source_trade_review_id": 18,
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
            },
            "importance": 0.7,
        },
    )
    assert gap.status_code == 201
    gap_id = gap.json()["id"]

    candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:66",
            "key": "skill_candidate:test:detail",
            "content": "Detail endpoint candidate.",
            "meta": {
                "summary": "Detail endpoint candidate.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "knowledge_claim",
                "source_claim_id": 1,
                "ticker": "LZM",
                "strategy_version_id": 66,
            },
            "importance": 0.75,
        },
    )
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]

    gap_response = client.get(f"/api/v1/skills/gaps/{gap_id}")
    assert gap_response.status_code == 200
    assert gap_response.json()["id"] == gap_id
    assert gap_response.json()["ticker"] == "LZM"

    candidate_response = client.get(f"/api/v1/skills/candidates/{candidate_id}")
    assert candidate_response.status_code == 200
    assert candidate_response.json()["id"] == candidate_id
    assert candidate_response.json()["ticker"] == "LZM"


def test_skill_gap_review_endpoint_updates_gap_status(client, session) -> None:
    gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:77",
            "key": "skill_gap:test:review",
            "content": "Reviewable gap.",
            "meta": {
                "summary": "Reviewable gap.",
                "gap_type": "missing_catalog_skill",
                "status": "open",
                "ticker": "AAL",
                "strategy_version_id": 77,
                "position_id": 4,
                "source_type": "trade_review",
                "source_trade_review_id": 21,
                "target_skill_code": "review_false_breakout",
                "candidate_action": "draft_candidate_skill",
            },
            "importance": 0.72,
        },
    )
    assert gap.status_code == 201
    gap_id = gap.json()["id"]

    review = client.post(
        f"/api/v1/skills/gaps/{gap_id}/review",
        json={
            "outcome": "resolve",
            "summary": "Gap reviewed and absorbed into the current operator playbook.",
        },
    )
    assert review.status_code == 200
    payload = review.json()
    assert payload["id"] == gap_id
    assert payload["status"] == "resolved"
    assert payload["meta"]["resolution_summary"] == "Gap reviewed and absorbed into the current operator playbook."

    dismissed_gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:77",
            "key": "skill_gap:test:reviewable-dismiss",
            "content": "Dismissible gap.",
            "meta": {
                "summary": "Dismissible gap.",
                "gap_type": "missing_entry_skill_context",
                "status": "open",
                "ticker": "AAL",
                "strategy_version_id": 77,
                "position_id": 5,
                "source_type": "trade_review",
                "source_trade_review_id": 22,
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
            },
            "importance": 0.7,
        },
    )
    assert dismissed_gap.status_code == 201
    dismissed_gap_id = dismissed_gap.json()["id"]

    dismiss_review = client.post(
        f"/api/v1/skills/gaps/{dismissed_gap_id}/review",
        json={
            "outcome": "dismiss",
            "summary": "Gap dismissed because the operator considers the current procedure sufficient.",
        },
    )
    assert dismiss_review.status_code == 200
    disagreement_entries = session.query(JournalEntry).filter(JournalEntry.entry_type == "operator_disagreement").all()
    disagreement_memories = session.query(MemoryItem).filter(MemoryItem.memory_type == "operator_disagreement").all()
    assert len(disagreement_entries) == 1
    assert len(disagreement_memories) == 1
    assert disagreement_entries[0].observations["operator_disagreement"]["disagreement_type"] == "skill_gap_dismissed"


def test_skill_gap_can_be_promoted_to_skill_candidate(client, session) -> None:
    gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:88",
            "key": "skill_gap:test:promote",
            "content": "Promotable skill gap.",
            "meta": {
                "summary": "Repeated evidence suggests this procedural gap should become a candidate.",
                "gap_type": "repeated_operator_disagreement",
                "status": "open",
                "ticker": "AAPL",
                "strategy_version_id": 88,
                "position_id": 9,
                "source_type": "operator_disagreement_cluster",
                "source_operator_disagreement_cluster_id": 41,
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
            },
            "importance": 0.79,
        },
    )
    assert gap.status_code == 201
    gap_id = gap.json()["id"]

    promoted = client.post(f"/api/v1/skills/gaps/{gap_id}/promote")
    assert promoted.status_code == 200
    payload = promoted.json()
    assert payload["source_type"] == "skill_gap"
    assert payload["target_skill_code"] == "evaluate_daily_breakout"
    assert payload["meta"]["source_gap_id"] == gap_id
    assert payload["candidate_status"] == "draft"

    gap_item = session.get(MemoryItem, gap_id)
    assert gap_item is not None
    assert gap_item.meta["linked_skill_candidate_id"] == payload["id"]
    assert gap_item.meta["promotion_status"] == "candidate_created"


def test_skill_workshop_syncs_claim_proposal_and_review_promotes_candidate(client, session) -> None:
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:64",
            "key": "claim:test:skill-workshop",
            "claim_type": "review_improvement",
            "claim_text": "Breakout confirmation needs a clearer procedural filter.",
            "linked_ticker": "AAPL",
            "strategy_version_id": 64,
            "status": "validated",
            "freshness_state": "current",
            "meta": {
                "source": "test",
                "target_skill_code": "evaluate_daily_breakout",
            },
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]

    synced = client.post("/api/v1/skills/proposals/sync", params={"limit_per_source": 10})
    assert synced.status_code == 200
    proposals = synced.json()
    proposal = next(item for item in proposals if item["source_claim_id"] == claim_id)
    assert proposal["source_type"] == "knowledge_claim"
    assert proposal["proposal_status"] == "pending"
    assert proposal["target_skill_code"] == "evaluate_daily_breakout"

    dashboard = client.get("/api/v1/skills/dashboard")
    assert dashboard.status_code == 200
    assert any(item["id"] == proposal["id"] for item in dashboard.json()["proposals"])

    reviewed = client.post(
        f"/api/v1/skills/proposals/{proposal['id']}/review",
        json={
            "outcome": "approve",
            "summary": "Promote this reviewed claim into a draft candidate before validation.",
        },
    )
    assert reviewed.status_code == 200
    reviewed_payload = reviewed.json()
    assert reviewed_payload["proposal"]["proposal_status"] == "applied"
    assert reviewed_payload["candidate"]["meta"]["source_claim_id"] == claim_id

    stored_claim = session.get(KnowledgeClaim, claim_id)
    assert stored_claim is not None
    assert stored_claim.meta["linked_skill_candidate_id"] == reviewed_payload["candidate"]["id"]


def test_skill_workshop_syncs_gap_proposal_and_review_promotes_candidate(client, session) -> None:
    gap = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_gap",
            "scope": "strategy:77",
            "key": "skill_gap:test:workshop",
            "content": "Gap ready for workshop promotion.",
            "meta": {
                "summary": "A repeated review pattern should become a candidate skill through the workshop.",
                "gap_type": "missing_catalog_skill",
                "status": "open",
                "ticker": "MSFT",
                "strategy_version_id": 77,
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
            },
            "importance": 0.8,
        },
    )
    assert gap.status_code == 201
    gap_id = gap.json()["id"]

    synced = client.post("/api/v1/skills/proposals/sync", params={"limit_per_source": 10})
    assert synced.status_code == 200
    proposal = next(item for item in synced.json() if item["source_gap_id"] == gap_id)
    assert proposal["source_type"] == "skill_gap"
    assert proposal["proposal_status"] == "pending"
    assert proposal["target_skill_code"] == "detect_risk_off_conditions"

    reviewed = client.post(
        f"/api/v1/skills/proposals/{proposal['id']}/review",
        json={
            "outcome": "approve",
            "summary": "Promote the workshop gap proposal into a draft candidate.",
        },
    )
    assert reviewed.status_code == 200
    reviewed_payload = reviewed.json()
    assert reviewed_payload["proposal"]["proposal_status"] == "applied"
    assert reviewed_payload["candidate"]["meta"]["source_gap_id"] == gap_id

    gap_item = session.get(MemoryItem, gap_id)
    assert gap_item is not None
    assert gap_item.meta["linked_skill_candidate_id"] == reviewed_payload["candidate"]["id"]


def test_skill_workshop_syncs_operator_disagreement_cluster_proposal(client, session) -> None:
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:91",
            "key": "claim:test:workshop-cluster",
            "claim_type": "review_improvement",
            "claim_text": "Operator keeps disagreeing with this breakout rule.",
            "linked_ticker": "AAPL",
            "strategy_version_id": 91,
            "freshness_state": "current",
            "meta": {"source": "test", "target_skill_code": "evaluate_daily_breakout"},
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]

    assert client.post(
        f"/api/v1/claims/{claim_id}/evidence",
        json={
            "source_type": "trade_review",
            "source_key": "trade_review:workshop-cluster:1",
            "stance": "support",
            "summary": "Initial support evidence.",
            "evidence_payload": {},
            "strength": 0.7,
        },
    ).status_code == 200

    for suffix in ("a", "b"):
        reviewed = client.post(
            f"/api/v1/claims/{claim_id}/review",
            json={
                "outcome": "contradict",
                "summary": f"Operator disagreement event {suffix}.",
                "source_key": f"claim_review:workshop-cluster:{suffix}",
                "strength": 0.72,
                "evidence_payload": {"suffix": suffix},
            },
        )
        assert reviewed.status_code == 200

    synced = client.post("/api/v1/skills/proposals/sync", params={"limit_per_source": 10})
    assert synced.status_code == 200
    proposal = next(
        item
        for item in synced.json()
        if item["source_type"] == "operator_disagreement_cluster"
    )
    assert proposal["proposal_status"] == "pending"
    assert proposal["source_operator_disagreement_cluster_id"] is not None

    reviewed = client.post(
        f"/api/v1/skills/proposals/{proposal['id']}/review",
        json={
            "outcome": "approve",
            "summary": "Promote the repeated operator disagreement into a draft candidate.",
        },
    )
    assert reviewed.status_code == 200
    reviewed_payload = reviewed.json()
    assert reviewed_payload["proposal"]["proposal_status"] == "applied"
    assert (
        reviewed_payload["candidate"]["meta"]["source_operator_disagreement_cluster_id"]
        == proposal["source_operator_disagreement_cluster_id"]
    )


def test_skill_workshop_syncs_regime_workflow_artifact_proposal(client, session) -> None:
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
    action = client.post(
        f"/api/v1/learning-workflows/{regime_review['id']}/actions",
        json={
            "item_type": "regime_shift",
            "entity_id": regime_review["items"][0]["entity_id"],
            "action": "complete",
            "summary": "Acknowledged the regime transition and reviewed risk posture.",
        },
    )
    assert action.status_code == 200
    action_payload = action.json()
    artifact_id = next(
        item["id"]
        for item in action_payload["workflow"]["recent_runs"][0]["artifacts"]
        if item["artifact_type"] == "regime_shift_review_completion"
    )

    synced = client.post("/api/v1/skills/proposals/sync", params={"limit_per_source": 10})
    assert synced.status_code == 200
    proposal = next(
        item
        for item in synced.json()
        if item["source_workflow_artifact_id"] == artifact_id
    )
    assert proposal["source_type"] == "learning_workflow_artifact"
    assert proposal["target_skill_code"] == "detect_risk_off_conditions"
    assert proposal["proposal_status"] == "pending"

    reviewed = client.post(
        f"/api/v1/skills/proposals/{proposal['id']}/review",
        json={
            "outcome": "approve",
            "summary": "Promote the regime-review proposal into a draft skill candidate.",
        },
    )
    assert reviewed.status_code == 200
    reviewed_payload = reviewed.json()
    assert reviewed_payload["proposal"]["proposal_status"] == "applied"
    assert reviewed_payload["candidate"]["meta"]["source_workflow_artifact_id"] == artifact_id


def test_skill_revision_detail_endpoint_returns_validated_revision(client) -> None:
    created = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:55",
            "key": "skill_candidate:test:revision-detail",
            "content": "Candidate for revision detail endpoint.",
            "meta": {
                "summary": "Candidate for revision detail endpoint.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "source_trade_review_id": 17,
                "ticker": "SPY",
                "strategy_version_id": 55,
            },
            "importance": 0.74,
        },
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "paper",
            "validation_outcome": "approve",
            "summary": "Revision detail validation payload.",
            "sample_size": 15,
            "win_rate": 60.0,
            "avg_pnl_pct": 1.2,
            "max_drawdown_pct": -1.9,
            "evidence": {
                "source": "paper_batch",
                "run_id": "paper-55",
                "artifact_url": "https://example.test/artifacts/paper-55",
            },
            "activate": True,
        },
    )
    assert validated.status_code == 200
    revision_id = validated.json()["revision"]["id"]

    response = client.get(f"/api/v1/skills/revisions/{revision_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == revision_id
    assert payload["candidate_id"] == candidate_id
    assert payload["skill_code"] == "detect_risk_off_conditions"
    assert isinstance(payload["validation_record_id"], int)
    assert payload["meta"]["evidence"]["run_id"] == "paper-55"


def test_skill_revision_portable_export_endpoint_returns_yaml_and_skill_markdown(client) -> None:
    created = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:72",
            "key": "skill_candidate:test:portable-export",
            "content": "Candidate for portable export endpoint.",
            "meta": {
                "summary": "Candidate for portable export endpoint.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "source_trade_review_id": 31,
                "ticker": "SPY",
                "strategy_version_id": 72,
            },
            "importance": 0.8,
        },
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "replay",
            "validation_outcome": "approve",
            "summary": "Portable export validation payload.",
            "sample_size": 27,
            "win_rate": 63.2,
            "avg_pnl_pct": 1.7,
            "max_drawdown_pct": -2.1,
            "evidence": {
                "source": "replay_batch",
                "run_id": "replay-72",
                "artifact_url": "https://example.test/artifacts/replay-72",
                "note": "portable export batch",
            },
            "activate": True,
        },
    )
    assert validated.status_code == 200
    revision_id = validated.json()["revision"]["id"]

    response = client.get(f"/api/v1/skills/revisions/{revision_id}/portable")
    assert response.status_code == 200
    payload = response.json()
    assert payload["artifact_version"] == "skill_portable_v1"
    assert payload["artifact_type"] == "validated_skill_revision_export"
    assert payload["skill_code"] == "detect_risk_off_conditions"
    assert payload["document"]["origin"]["revision_id"] == revision_id
    assert payload["document"]["validation"]["run_id"] == "replay-72"
    assert "artifact_version: skill_portable_v1" in payload["yaml_text"]
    assert "# Detect Risk-Off Conditions" in payload["skill_md"]
    assert "## Validated Revision" in payload["skill_md"]
    assert "Portable export validation payload." in payload["skill_md"]


def test_portable_skill_yaml_import_creates_draft_candidate(client, session) -> None:
    source_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:73",
            "key": "skill_candidate:test:portable-import-source",
            "content": "Source candidate for YAML import round-trip.",
            "meta": {
                "summary": "Source candidate for YAML import round-trip.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "ticker": "QQQ",
                "strategy_version_id": 73,
            },
            "importance": 0.79,
        },
    )
    assert source_candidate.status_code == 201
    source_candidate_id = source_candidate.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{source_candidate_id}/validate",
        json={
            "validation_mode": "paper",
            "validation_outcome": "approve",
            "summary": "YAML import source validation payload.",
            "sample_size": 19,
            "win_rate": 59.8,
            "avg_pnl_pct": 1.3,
            "max_drawdown_pct": -2.0,
            "evidence": {"run_id": "paper-73"},
            "activate": True,
        },
    )
    assert validated.status_code == 200
    revision_id = validated.json()["revision"]["id"]

    exported = client.get(f"/api/v1/skills/revisions/{revision_id}/portable")
    assert exported.status_code == 200

    imported = client.post(
        "/api/v1/skills/portable/import",
        json={
            "format": "yaml",
            "import_as": "candidate",
            "content": exported.json()["yaml_text"],
            "scope": "strategy:173",
            "key": "skill_candidate:test:portable-imported",
        },
    )
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["import_as"] == "candidate"
    assert payload["candidate"]["candidate_status"] == "draft"
    assert payload["candidate"]["target_skill_code"] == "detect_risk_off_conditions"
    assert payload["candidate"]["source_type"] == "portable_skill_artifact"
    assert isinstance(payload["journal_entry_id"], int)

    stored_candidate = session.query(MemoryItem).filter(
        MemoryItem.memory_type == "skill_candidate",
        MemoryItem.key == "skill_candidate:test:portable-imported",
    ).one()
    portable_meta = stored_candidate.meta["portable_skill_artifact"]
    assert portable_meta["format"] == "yaml"
    assert portable_meta["origin"]["revision_id"] == revision_id

    journal_entry = session.get(JournalEntry, payload["journal_entry_id"])
    assert journal_entry is not None
    assert journal_entry.entry_type == "skill_portable_candidate_imported"


def test_portable_skill_markdown_import_can_create_inactive_revision(client) -> None:
    source_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:74",
            "key": "skill_candidate:test:portable-md-source",
            "content": "Source candidate for SKILL.md import round-trip.",
            "meta": {
                "summary": "Source candidate for SKILL.md import round-trip.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "ticker": "IWM",
                "strategy_version_id": 74,
            },
            "importance": 0.77,
        },
    )
    assert source_candidate.status_code == 201
    source_candidate_id = source_candidate.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{source_candidate_id}/validate",
        json={
            "validation_mode": "paper",
            "validation_outcome": "approve",
            "summary": "SKILL.md import source validation payload.",
            "sample_size": 16,
            "win_rate": 57.5,
            "avg_pnl_pct": 0.9,
            "max_drawdown_pct": -1.7,
            "evidence": {"run_id": "paper-74"},
            "activate": True,
        },
    )
    assert validated.status_code == 200
    source_revision_id = validated.json()["revision"]["id"]

    exported = client.get(f"/api/v1/skills/revisions/{source_revision_id}/portable")
    assert exported.status_code == 200

    local_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:174",
            "key": "skill_candidate:test:portable-md-linked",
            "content": "Local candidate linked to imported revision.",
            "meta": {
                "summary": "Local candidate linked to imported revision.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "portable_skill_artifact",
                "ticker": "IWM",
                "strategy_version_id": 174,
            },
            "importance": 0.71,
        },
    )
    assert local_candidate.status_code == 201
    local_candidate_id = local_candidate.json()["id"]

    imported = client.post(
        "/api/v1/skills/portable/import",
        json={
            "format": "skill_md",
            "import_as": "revision",
            "content": exported.json()["skill_md"],
            "key": "skill_revision:test:portable-md-imported",
            "candidate_id": local_candidate_id,
        },
    )
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["import_as"] == "revision"
    assert payload["revision"]["activation_status"] == "imported_inactive"
    assert payload["revision"]["candidate_id"] == local_candidate_id
    assert payload["revision"]["skill_code"] == "evaluate_daily_breakout"
    assert payload["revision"]["validation_record_id"] is None

    active_revisions = client.get("/api/v1/skills/revisions")
    assert active_revisions.status_code == 200
    assert payload["revision"]["id"] not in {item["id"] for item in active_revisions.json()}

    all_revisions = client.get("/api/v1/skills/revisions", params={"include_inactive": "true"})
    assert all_revisions.status_code == 200
    assert payload["revision"]["id"] in {item["id"] for item in all_revisions.json()}


def test_skill_validation_record_detail_endpoint_returns_structured_validation_entity(client) -> None:
    created = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:58",
            "key": "skill_candidate:test:validation-record",
            "content": "Candidate for validation record detail endpoint.",
            "meta": {
                "summary": "Candidate for validation record detail endpoint.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "source_trade_review_id": 22,
                "ticker": "DIA",
                "strategy_version_id": 58,
            },
            "importance": 0.76,
        },
    )
    assert created.status_code == 201
    candidate_id = created.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "replay",
            "validation_outcome": "approve",
            "summary": "Structured validation record payload.",
            "sample_size": 21,
            "win_rate": 62.0,
            "avg_pnl_pct": 1.5,
            "max_drawdown_pct": -2.2,
            "evidence": {
                "source": "replay_batch",
                "run_id": "replay-58",
                "artifact_url": "https://example.test/artifacts/replay-58",
                "note": "Walk-forward batch 3",
            },
            "activate": True,
        },
    )
    assert validated.status_code == 200
    validation_record_id = validated.json()["validation_record"]["id"]
    revision_id = validated.json()["revision"]["id"]

    response = client.get(f"/api/v1/skills/validations/{validation_record_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == validation_record_id
    assert payload["candidate_id"] == candidate_id
    assert payload["revision_id"] == revision_id
    assert payload["run_id"] == "replay-58"
    assert payload["artifact_url"] == "https://example.test/artifacts/replay-58"
    assert payload["evidence_note"] == "Walk-forward batch 3"


def test_skill_validation_record_list_endpoint_filters_by_candidate_and_skill_code(client) -> None:
    first_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:59",
            "key": "skill_candidate:test:validation-list:1",
            "content": "First validation-list candidate.",
            "meta": {
                "summary": "First validation-list candidate.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "ticker": "SPY",
                "strategy_version_id": 59,
            },
            "importance": 0.7,
        },
    )
    assert first_candidate.status_code == 201
    first_candidate_id = first_candidate.json()["id"]

    second_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:60",
            "key": "skill_candidate:test:validation-list:2",
            "content": "Second validation-list candidate.",
            "meta": {
                "summary": "Second validation-list candidate.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "ticker": "QQQ",
                "strategy_version_id": 60,
            },
            "importance": 0.7,
        },
    )
    assert second_candidate.status_code == 201
    second_candidate_id = second_candidate.json()["id"]

    third_candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:61",
            "key": "skill_candidate:test:validation-list:3",
            "content": "Third validation-list candidate.",
            "meta": {
                "summary": "Third validation-list candidate.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "trade_review",
                "ticker": "IWM",
                "strategy_version_id": 61,
            },
            "importance": 0.7,
        },
    )
    assert third_candidate.status_code == 201
    third_candidate_id = third_candidate.json()["id"]

    for candidate_id, run_id in [
        (first_candidate_id, "paper-59"),
        (second_candidate_id, "paper-60"),
        (third_candidate_id, "paper-61"),
    ]:
        validated = client.post(
            f"/api/v1/skills/candidates/{candidate_id}/validate",
            json={
                "validation_mode": "paper",
                "validation_outcome": "approve",
                "summary": f"Validation for {run_id}.",
                "sample_size": 12,
                "win_rate": 58.0,
                "avg_pnl_pct": 1.0,
                "max_drawdown_pct": -1.5,
                "evidence": {"run_id": run_id},
                "activate": True,
            },
        )
        assert validated.status_code == 200

    by_candidate = client.get(f"/api/v1/skills/validations?candidate_id={first_candidate_id}&limit=10")
    assert by_candidate.status_code == 200
    by_candidate_payload = by_candidate.json()
    assert len(by_candidate_payload) == 1
    assert by_candidate_payload[0]["candidate_id"] == first_candidate_id
    assert by_candidate_payload[0]["skill_code"] == "detect_risk_off_conditions"
    assert by_candidate_payload[0]["ticker"] == "SPY"
    assert by_candidate_payload[0]["strategy_version_id"] == 59

    by_skill = client.get("/api/v1/skills/validations?skill_code=detect_risk_off_conditions&limit=10")
    assert by_skill.status_code == 200
    by_skill_payload = by_skill.json()
    assert len(by_skill_payload) == 2
    assert {item["candidate_id"] for item in by_skill_payload} == {first_candidate_id, second_candidate_id}
    assert {item["skill_code"] for item in by_skill_payload} == {"detect_risk_off_conditions"}

    summary = client.get(f"/api/v1/skills/validations/summary?candidate_id={first_candidate_id}&limit=10")
    assert summary.status_code == 200
    summary_payload = summary.json()
    assert summary_payload["scope_type"] == "candidate"
    assert summary_payload["scope_value"] == str(first_candidate_id)
    assert summary_payload["record_count"] == 1
    assert summary_payload["approved_count"] == 1
    assert summary_payload["latest_run_id"] == "paper-59"

    skill_summary = client.get("/api/v1/skills/validations/summary?skill_code=detect_risk_off_conditions&limit=10")
    assert skill_summary.status_code == 200
    skill_summary_payload = skill_summary.json()
    assert skill_summary_payload["scope_type"] == "skill"
    assert skill_summary_payload["scope_value"] == "detect_risk_off_conditions"
    assert skill_summary_payload["record_count"] == 2
    assert skill_summary_payload["approved_count"] == 2
    assert skill_summary_payload["avg_win_rate"] == 58.0

    compare = client.get("/api/v1/skills/validations/compare?skill_code=detect_risk_off_conditions&limit=10")
    assert compare.status_code == 200
    compare_payload = compare.json()
    assert compare_payload["scope_type"] == "skill"
    assert compare_payload["scope_value"] == "detect_risk_off_conditions"
    assert compare_payload["row_count"] == 2
    assert compare_payload["rows"][0]["is_base"] is True
    assert compare_payload["rows"][0]["validation_outcome"] == "approved"
    assert compare_payload["rows"][1]["validation_id"] != compare_payload["rows"][0]["validation_id"]

    custom_baseline_id = by_skill_payload[1]["id"]
    custom_compare = client.get(
        f"/api/v1/skills/validations/compare?skill_code=detect_risk_off_conditions&baseline_validation_id={custom_baseline_id}&limit=10"
    )
    assert custom_compare.status_code == 200
    custom_compare_payload = custom_compare.json()
    assert custom_compare_payload["custom_baseline_applied"] is True
    assert custom_compare_payload["baseline_validation_id"] == custom_baseline_id
    assert custom_compare_payload["rows"][0]["validation_id"] == custom_baseline_id
    assert custom_compare_payload["rows"][0]["is_base"] is True


def test_skill_candidate_provenance_endpoint_returns_claim_candidate_revision_chain(client) -> None:
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:88",
            "key": "claim:test:provenance",
            "claim_type": "context_rule",
            "claim_text": "Risk-off revisions need explicit provenance.",
            "linked_ticker": "QQQ",
            "strategy_version_id": 88,
            "status": "supported",
            "confidence": 0.74,
            "meta": {},
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]

    candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:88",
            "key": "skill_candidate:test:provenance-chain",
            "content": "Candidate with provenance chain.",
            "meta": {
                "summary": "Candidate with provenance chain.",
                "target_skill_code": "detect_risk_off_conditions",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "knowledge_claim",
                "source_claim_id": claim_id,
                "ticker": "QQQ",
                "strategy_version_id": 88,
            },
            "importance": 0.79,
        },
    )
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "replay",
            "validation_outcome": "approve",
            "summary": "Chain validation payload.",
            "sample_size": 19,
            "win_rate": 57.8,
            "avg_pnl_pct": 1.1,
            "max_drawdown_pct": -2.0,
            "evidence": {
                "source": "replay_batch",
                "run_id": "replay-88",
            },
            "activate": True,
        },
    )
    assert validated.status_code == 200
    revision_id = validated.json()["revision"]["id"]

    claim_with_link = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:88",
            "key": "claim:test:provenance-linked",
            "claim_type": "context_rule",
            "claim_text": "Linked candidate provenance.",
            "linked_ticker": "QQQ",
            "strategy_version_id": 88,
            "status": "supported",
            "confidence": 0.74,
            "meta": {
                "linked_skill_candidate_id": candidate_id,
            },
        },
    )
    assert claim_with_link.status_code == 201

    response = client.get(f"/api/v1/skills/candidates/{candidate_id}/provenance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["origin_entity_type"] == "skill_candidate"
    assert payload["origin_entity_id"] == candidate_id
    assert payload["claim"]["id"] == claim_id
    assert payload["candidate"]["id"] == candidate_id
    assert payload["revision"]["id"] == revision_id


def test_claim_provenance_endpoint_resolves_reverse_linked_candidate_chain(client) -> None:
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:89",
            "key": "claim:test:reverse-provenance",
            "claim_type": "review_improvement",
            "claim_text": "Reverse-linked provenance should still resolve.",
            "linked_ticker": "IWM",
            "strategy_version_id": 89,
            "status": "supported",
            "confidence": 0.7,
            "meta": {},
        },
    )
    assert claim.status_code == 201
    claim_id = claim.json()["id"]

    candidate = client.post(
        "/api/v1/memory",
        json={
            "memory_type": "skill_candidate",
            "scope": "strategy:89",
            "key": "skill_candidate:test:reverse-provenance",
            "content": "Reverse-linked provenance candidate.",
            "meta": {
                "summary": "Reverse-linked provenance candidate.",
                "target_skill_code": "evaluate_daily_breakout",
                "candidate_action": "update_existing_skill",
                "candidate_status": "draft",
                "validation_required": True,
                "source_type": "knowledge_claim",
                "source_claim_id": claim_id,
                "ticker": "IWM",
                "strategy_version_id": 89,
            },
            "importance": 0.73,
        },
    )
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]

    validated = client.post(
        f"/api/v1/skills/candidates/{candidate_id}/validate",
        json={
            "validation_mode": "paper",
            "validation_outcome": "approve",
            "summary": "Reverse-linked revision.",
            "sample_size": 14,
            "win_rate": 58.0,
            "avg_pnl_pct": 0.9,
            "max_drawdown_pct": -1.8,
            "evidence": {"run_id": "paper-89"},
            "activate": True,
        },
    )
    assert validated.status_code == 200

    response = client.get(f"/api/v1/claims/{claim_id}/provenance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["claim"]["id"] == claim_id
    assert payload["candidate"]["id"] == candidate_id
    assert payload["revision"]["candidate_id"] == candidate_id
