from app.db.models.journal import JournalEntry
from app.db.models.memory import MemoryItem
from app.domains.learning.skills import SkillLifecycleService, SkillRouterService


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
