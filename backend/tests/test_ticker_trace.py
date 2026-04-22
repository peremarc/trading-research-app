from datetime import datetime, timedelta, timezone

from app.db.models.journal import JournalEntry
from app.db.models.knowledge_claim import KnowledgeClaim
from app.db.models.position import Position
from app.db.models.signal import TradeSignal


def test_ticker_trace_unifies_signal_journal_and_positions(client, session) -> None:
    older_signal = TradeSignal(
        ticker="LZM",
        timeframe="1D",
        signal_type="breakout_long",
        thesis="Older signal reviewed by AI.",
        signal_time=datetime(2026, 4, 19, 9, 52, 12, tzinfo=timezone.utc),
        signal_context={
            "decision_trace": {
                "decision_source": "ai_overlay",
                "final_action": "watch",
                "final_reason": "AI prefers waiting for follow-through.",
            },
            "guard_results": {
                "blocked": False,
                "reasons": [],
            },
            "ai_overlay": {
                "provider": "gemini",
                "model": "gemini-2.5-flash",
                "action": "watch",
                "thesis": "Momentum is improving but confirmation is incomplete.",
            },
            "score_breakdown": {"total_score": 0.72},
            "timing_profile": {
                "version": "ticker_analysis_timing_v1",
                "total_ms": 412.0,
                "slowest_stage": "ai_review",
                "slowest_stage_ms": 220.0,
            },
        },
        quality_score=0.66,
        status="watch",
        created_at=datetime(2026, 4, 19, 9, 52, 12, tzinfo=timezone.utc),
    )
    latest_signal = TradeSignal(
        ticker="LZM",
        timeframe="1D",
        signal_type="breakout_long",
        thesis="Latest signal blocked by regime policy.",
        signal_time=datetime(2026, 4, 20, 7, 31, 53, tzinfo=timezone.utc),
        signal_context={
            "decision_trace": {
                "decision_source": "deterministic_regime_policy",
                "final_action": "watch",
                "final_reason": "Playbook is inactive under macro uncertainty.",
            },
            "guard_results": {
                "blocked": True,
                "reasons": ["playbook 'breakout_long' is not active under regime 'macro_uncertainty'"],
            },
            "score_breakdown": {"total_score": 0.58},
            "timing_profile": {
                "version": "ticker_analysis_timing_v1",
                "total_ms": 183.0,
                "slowest_stage": "decision_context",
                "slowest_stage_ms": 71.0,
            },
        },
        quality_score=0.61,
        status="blocked",
        rejection_reason="regime policy blocked the setup",
        created_at=datetime(2026, 4, 20, 7, 31, 53, tzinfo=timezone.utc),
    )
    position = Position(
        ticker="LZM",
        signal_id=older_signal.id,
        account_mode="paper",
        side="long",
        status="closed",
        entry_date=datetime(2026, 4, 19, 10, 5, 0, tzinfo=timezone.utc),
        entry_price=14.5,
        stop_price=13.9,
        target_price=15.8,
        size=1,
        thesis="Opened after the earlier signal.",
        entry_context={"execution_mode": "paper", "source": "ticker-trace-test"},
        exit_date=datetime(2026, 4, 19, 15, 0, 0, tzinfo=timezone.utc),
        exit_price=15.2,
        exit_reason="target trimmed",
        pnl_realized=0.7,
        pnl_pct=4.8,
    )
    journal = JournalEntry(
        entry_type="pdca_do",
        ticker="LZM",
        event_time=datetime(2026, 4, 20, 7, 34, 21, tzinfo=timezone.utc),
        reasoning="Queued for evaluation but blocked before AI review.",
        decision="watch",
        observations={"queue_priority": "high"},
    )
    ai_journal = JournalEntry(
        entry_type="ai_trade_decision",
        ticker="LZM",
        event_time=datetime(2026, 4, 20, 7, 35, 0, tzinfo=timezone.utc),
        reasoning="AI context budget loaded one skill and one claim.",
        decision="watch",
        observations={
            "runtime_skills": [
                {
                    "skill_code": "detect_risk_off_conditions",
                    "selection_reason": "Macro uncertainty is degrading breakout quality.",
                    "instruction_source": "catalog_plus_active_revision",
                    "validated_revision_id": 77,
                }
            ],
            "runtime_distillations": [
                {
                    "key": "distill:skill-gap:lzm-breakout",
                    "distillation_type": "skill_gap_digest",
                    "review_action": "collapse",
                }
            ],
            "context_budget": {
                "runtime_skills": {
                    "available_count": 2,
                    "loaded_count": 1,
                    "truncated_count": 1,
                },
                "runtime_claims": {
                    "available_count": 2,
                    "loaded_count": 1,
                    "truncated_count": 1,
                },
                "runtime_distillations": {
                    "available_count": 1,
                    "loaded_count": 1,
                    "truncated_count": 0,
                },
            }
        },
    )

    session.add_all([older_signal, latest_signal])
    session.flush()
    position.signal_id = older_signal.id
    session.add(position)
    session.add_all([journal, ai_journal])
    session.commit()

    response = client.get("/api/v1/journal/ticker-trace/LZM?limit=12")

    assert response.status_code == 200
    payload = response.json()
    summary = payload["summary"]
    assert payload["ticker"] == "LZM"
    assert summary["total_signals"] == 2
    assert summary["total_journal_entries"] == 2
    assert summary["total_positions"] == 1
    assert summary["latest_signal_status"] == "blocked"
    assert summary["latest_decision"] == "watch"
    assert summary["latest_decision_source"] == "deterministic_regime_policy"
    assert summary["latest_llm_status"] == "not_called_blocked"
    assert summary["latest_score"] == 0.58
    assert summary["latest_timing_total_ms"] == 183.0
    assert summary["latest_timing_slowest_stage"] == "decision_context"
    assert summary["latest_timing_slowest_stage_ms"] == 71.0
    assert summary["latest_available_runtime_skill_count"] == 2
    assert summary["latest_loaded_runtime_skill_count"] == 1
    assert summary["latest_available_runtime_claim_count"] == 2
    assert summary["latest_loaded_runtime_claim_count"] == 1
    assert summary["latest_available_runtime_distillation_count"] == 1
    assert summary["latest_loaded_runtime_distillation_count"] == 1
    assert summary["latest_runtime_budget_truncated"] is True
    assert "macro_uncertainty" in summary["latest_guard_reason"]

    signal_events = [item for item in payload["events"] if item["event_kind"] == "signal"]
    assert len(signal_events) == 2
    assert signal_events[0]["signal_id"] == latest_signal.id
    assert signal_events[0]["llm_status"] == "not_called_blocked"
    assert signal_events[0]["decision_source"] == "deterministic_regime_policy"
    assert signal_events[0]["details"]["timing_profile"]["total_ms"] == 183.0

    reviewed_signal = next(item for item in signal_events if item["signal_id"] == older_signal.id)
    assert reviewed_signal["llm_status"] == "reviewed"
    assert reviewed_signal["llm_provider"] == "gemini"

    ai_journal_event = next(
        item
        for item in payload["events"]
        if item["event_kind"] == "journal" and item["journal_id"] == ai_journal.id
    )
    assert ai_journal_event["details"]["context_budget"]["runtime_skills"]["loaded_count"] == 1
    assert ai_journal_event["details"]["context_budget"]["runtime_claims"]["truncated_count"] == 1
    assert ai_journal_event["details"]["runtime_skills"][0]["skill_code"] == "detect_risk_off_conditions"
    assert ai_journal_event["details"]["runtime_distillations"][0]["key"] == "distill:skill-gap:lzm-breakout"

    journal_event = next(
        item
        for item in payload["events"]
        if item["event_kind"] == "journal" and item["journal_id"] == journal.id
    )
    assert journal_event["decision"] == "watch"

    position_open = next(item for item in payload["events"] if item["event_kind"] == "position_open")
    position_close = next(item for item in payload["events"] if item["event_kind"] == "position_close")
    assert position_open["position_id"] == position.id
    assert position_close["position_id"] == position.id
    assert position_close["details"]["pnl_pct"] == 4.8


def test_ticker_trace_includes_learning_workflow_actions_for_linked_ticker(client, session) -> None:
    claim = client.post(
        "/api/v1/claims",
        json={
            "scope": "strategy:123",
            "key": "claim:ticker-trace:workflow",
            "claim_type": "review_improvement",
            "claim_text": "Breakout review for LZM needs confirmation.",
            "linked_ticker": "LZM",
            "status": "supported",
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
            "source_key": "trade_review:ticker_trace:workflow",
            "stance": "support",
            "summary": "Claim is supported and ready for explicit review.",
            "evidence_payload": {"ticker": "LZM"},
            "strength": 0.7,
        },
    )
    assert evidence.status_code == 200

    workflow_claim = session.get(KnowledgeClaim, claim_id)
    assert workflow_claim is not None
    workflow_claim.last_reviewed_at = datetime.now(timezone.utc) - timedelta(days=45)
    session.add(workflow_claim)
    session.commit()

    workflows = client.get("/api/v1/learning-workflows", params={"sync": "true"}).json()
    stale_review = next(item for item in workflows if item["workflow_type"] == "stale_claim_review")

    action = client.post(
        f"/api/v1/learning-workflows/{stale_review['id']}/actions",
        json={
            "item_type": "claim_review",
            "entity_id": claim_id,
            "action": "confirm",
            "summary": "Workflow review confirms the LZM claim.",
        },
    )
    assert action.status_code == 200

    trace = client.get("/api/v1/journal/ticker-trace/LZM?limit=12")
    assert trace.status_code == 200
    payload = trace.json()

    workflow_event = next(
        item
        for item in payload["events"]
        if item["event_kind"] == "journal" and item["details"].get("workflow", {}).get("workflow_type") == "stale_claim_review"
    )
    assert workflow_event["details"]["workflow"]["resolution_class"] == "claim_confirmed"
    assert workflow_event["details"]["workflow"]["resolution_outcome"] == "accepted"
    assert "workflow:stale_claim_review" in workflow_event["tags"]
    assert "resolution:claim_confirmed" in workflow_event["tags"]
