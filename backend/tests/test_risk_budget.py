from fastapi.testclient import TestClient

from app.db.models.market_state_snapshot import MarketStateSnapshotRecord
from app.domains.learning import api as learning_api
from app.domains.learning.decisioning import EntryScoringService, PositionSizingService


def test_position_sizing_produces_sized_trade_from_budget() -> None:
    service = PositionSizingService()

    result = service.size_trade_candidate(
        signal_payload={
            "entry_price": 100.0,
            "stop_price": 95.0,
            "risk_reward": 2.5,
            "decision_confidence": 0.82,
            "quant_summary": {"atr_14": 2.0},
        },
        decision_context={
            "strategy_rules": {"default_stop_atr_multiple": 1.5},
            "portfolio": {
                "same_ticker_open_positions": 0,
                "same_sector_open_positions": 0,
                "same_regime_open_positions": 0,
            },
            "risk_budget": {
                "capital_base": 100000.0,
                "per_trade_risk_amount": 1000.0,
                "used_portfolio_risk_amount": 0.0,
                "remaining_portfolio_risk_amount": 5000.0,
                "max_notional_fraction_per_trade": 0.2,
                "candidate_profile": {"event_risk_flags": []},
            },
        },
    )

    assert result["blocked"] is False
    assert result["position_sizing"]["status"] == "ready"
    assert result["position_sizing"]["size"] == 200.0
    assert result["position_sizing"]["risk_amount"] == 1000.0
    assert result["position_sizing"]["effective_stop_price"] == 95.0


def test_position_sizing_applies_regime_policy_risk_multiplier() -> None:
    service = PositionSizingService()

    result = service.size_trade_candidate(
        signal_payload={
            "entry_price": 100.0,
            "stop_price": 95.0,
            "risk_reward": 2.5,
            "decision_confidence": 0.82,
            "quant_summary": {"atr_14": 2.0},
        },
        decision_context={
            "strategy_rules": {"default_stop_atr_multiple": 1.5},
            "portfolio": {
                "same_ticker_open_positions": 0,
                "same_sector_open_positions": 0,
                "same_regime_open_positions": 0,
            },
            "regime_policy": {
                "policy_version": "2026-04-18-regime-policy-1",
                "entry_allowed": True,
                "risk_multiplier": 0.5,
            },
            "risk_budget": {
                "capital_base": 100000.0,
                "per_trade_risk_amount": 1000.0,
                "used_portfolio_risk_amount": 0.0,
                "remaining_portfolio_risk_amount": 5000.0,
                "max_notional_fraction_per_trade": 0.2,
                "candidate_profile": {"event_risk_flags": []},
            },
        },
    )

    assert result["blocked"] is False
    assert result["position_sizing"]["size"] == 100.0
    assert result["position_sizing"]["risk_amount"] == 500.0
    assert result["position_sizing"]["regime_multiplier"] == 0.5
    assert result["position_sizing"]["regime_policy_version"] == "2026-04-18-regime-policy-1"


def test_entry_scoring_blocks_when_risk_budget_kill_switch_triggers() -> None:
    service = EntryScoringService()

    result = service.evaluate(
        signal_payload={
            "combined_score": 0.86,
            "risk_reward": 2.5,
            "quant_summary": {"trend": "uptrend", "setup": "breakout"},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.8},
        },
        decision_context={
            "strategy_rules": {},
            "macro_fit": {},
            "calendar_context": {},
            "news_context": {},
            "portfolio": {},
            "risk_budget": {
                "kill_switch": {
                    "triggered": True,
                    "reasons": ["daily realized pnl -5.5% breached limit -4.0%"],
                }
            },
        },
    )

    assert result["recommended_action"] == "watch"
    assert result["guard_results"]["blocked"] is True
    assert "risk_budget" in result["guard_results"]["types"]
    assert result["score_breakdown"]["risk_budget_score"] == 0.05


def test_entry_scoring_blocks_when_regime_policy_disallows_playbook() -> None:
    service = EntryScoringService()

    result = service.evaluate(
        signal_payload={
            "combined_score": 0.86,
            "risk_reward": 2.5,
            "quant_summary": {"trend": "uptrend", "setup": "breakout"},
            "visual_summary": {"setup_type": "breakout", "visual_score": 0.8},
        },
        decision_context={
            "strategy_rules": {},
            "macro_fit": {},
            "calendar_context": {},
            "news_context": {},
            "portfolio": {},
            "risk_budget": {"candidate_profile": {"event_risk_flags": []}},
            "regime_policy": {
                "policy_version": "2026-04-18-regime-policy-1",
                "regime_label": "macro_uncertainty",
                "playbook": "breakout_long",
                "allowed_playbooks": ["position_long"],
                "blocked_playbooks": ["breakout_long", "pullback_long"],
                "risk_multiplier": 0.4,
                "max_new_positions": 1,
                "opened_positions_so_far": 0,
                "entry_allowed": False,
                "blocked_reason": "playbook 'breakout_long' is not active under regime 'macro_uncertainty'",
            },
        },
    )

    assert result["recommended_action"] == "watch"
    assert result["guard_results"]["blocked"] is True
    assert "regime_policy" in result["guard_results"]["types"]
    assert "macro_uncertainty" in result["guard_results"]["reasons"][0]
    assert result["score_breakdown"]["regime_policy_score"] == 0.12


def test_orchestrator_do_persists_risk_budget_and_position_sizing(client: TestClient) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
            "trend": "uptrend",
            "setup": "breakout",
            "risk_reward": 2.0,
        },
        "visual_summary": {
            "setup_type": "breakout",
            "setup_quality": 0.84,
            "visual_score": 0.81,
        },
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "target_price": 110.0,
        "risk_reward": 2.0,
        "decision_confidence": 0.86,
        "rationale": f"Risk sizing test signal for {ticker}.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    try:
        strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "risk_budget_strategy",
                "name": "Risk Budget Strategy",
                "description": "Strategy used to test position sizing persistence.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "Size every trade from a fixed paper risk budget.",
                    "general_rules": {},
                    "parameters": {
                        "risk_per_trade_fraction": 0.01,
                        "max_portfolio_risk_fraction": 0.05,
                        "max_notional_fraction_per_trade": 0.2,
                    },
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()
        watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "risk_budget_watchlist",
                "name": "Risk Budget Watchlist",
                "strategy_id": strategy["id"],
                "hypothesis": "Only enter when the paper risk budget is available.",
                "status": "active",
            },
        ).json()
        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "Risk sizing candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery

    assert response.status_code == 200
    assert response.json()["opened_positions"] == 1

    position = client.get("/api/v1/positions").json()[0]
    sizing = position["entry_context"]["position_sizing"]
    risk_budget = position["entry_context"]["risk_budget"]
    regime_policy = position["entry_context"]["regime_policy"]
    assert sizing["size"] > 1.0
    assert position["size"] == sizing["size"]
    assert sizing["risk_amount"] == round(sizing["size"] * 5.0, 2)
    assert risk_budget["per_trade_risk_amount"] == 1000.0
    assert risk_budget["kill_switch"]["triggered"] is False
    assert position["entry_context"]["policy_version"] == regime_policy["policy_version"]
    assert position["entry_context"]["allowed_playbooks"] == regime_policy["allowed_playbooks"]
    assert position["entry_context"]["risk_multiplier"] == regime_policy["risk_multiplier"]

    signal = client.get("/api/v1/signals").json()[0]
    assert signal["signal_context"]["position_sizing"]["size"] == sizing["size"]
    assert signal["signal_context"]["risk_budget"]["remaining_portfolio_risk_amount"] == 5000.0
    assert signal["signal_context"]["policy_version"] == regime_policy["policy_version"]


def test_orchestrator_do_blocks_entries_when_regime_policy_conflicts(client: TestClient) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    original_capture_snapshot = learning_api.orchestrator_service.market_state_service.capture_snapshot

    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
            "trend": "uptrend",
            "setup": "breakout",
            "risk_reward": 2.4,
        },
        "visual_summary": {
            "setup_type": "breakout",
            "setup_quality": 0.84,
            "visual_score": 0.81,
        },
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "target_price": 112.0,
        "risk_reward": 2.4,
        "decision_confidence": 0.86,
        "rationale": f"Regime policy test signal for {ticker}.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }

    def capture_macro_uncertainty_snapshot(session, *, trigger: str, pdca_phase: str | None = None, source_context: dict | None = None):
        payload = {
            "summary": "World state for do phase: regime macro_uncertainty with selective exposure only.",
            "market_state_snapshot": {
                "execution_mode": "global",
                "watchlist_code": None,
                "portfolio_state": {
                    "benchmark_ticker": "SPY",
                    "benchmark_price": 100.0,
                    "benchmark_month_performance": 0.01,
                    "market_state_trigger": trigger,
                    "market_state_phase": pdca_phase,
                },
                "open_positions": [],
                "recent_alerts": [],
                "macro_context": {
                    "summary": "Macro regime is uncertain and should stay selective.",
                    "active_regimes": ["macro_uncertainty"],
                    "global_regime": "macro_uncertainty",
                    "global_regime_confidence": 0.74,
                },
                "corporate_calendar": [],
                "market_regime_inputs": {
                    "market_regime": {"label": "macro_uncertainty", "confidence": 0.74},
                },
                "active_watchlists": [],
            },
            "market_regime": {"label": "macro_uncertainty", "confidence": 0.74},
            "benchmark_snapshot": {"price": 100.0, "month_performance": 0.01},
            "macro_context": {"summary": "Macro regime is uncertain.", "active_regimes": ["macro_uncertainty"]},
            "calendar_events": [],
            "calendar_error": None,
            "backlog": {
                "open_positions_count": 0,
                "pending_reviews": 0,
                "open_research_tasks": 0,
                "active_watchlists_count": 1,
            },
            "trigger": trigger,
            "pdca_phase": pdca_phase,
            "source_context": dict(source_context or {}),
        }
        record = MarketStateSnapshotRecord(
            trigger=trigger,
            pdca_phase=pdca_phase,
            execution_mode=str((source_context or {}).get("execution_mode") or "global"),
            benchmark_ticker="SPY",
            regime_label="macro_uncertainty",
            regime_confidence=0.74,
            summary=str(payload["summary"]),
            snapshot_payload=payload,
            source_context=dict(source_context or {}),
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_macro_uncertainty_snapshot

    try:
        strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "regime_policy_guard",
                "name": "Regime Policy Guard",
                "description": "Strategy used to test regime-policy blocking.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "Breakouts should be blocked when the market regime is macro uncertainty.",
                    "general_rules": {},
                    "parameters": {},
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()
        watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "regime_policy_watchlist",
                "name": "Regime Policy Watchlist",
                "strategy_id": strategy["id"],
                "hypothesis": "Block fresh breakout risk in macro uncertainty.",
                "status": "active",
            },
        ).json()
        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "Regime policy candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot

    assert response.status_code == 200
    payload = response.json()
    assert payload["opened_positions"] == 0
    assert payload["metrics"]["regime_policy_blocked_entries"] == 1
    assert payload["candidates"][0]["decision"] == "watch"

    signal = client.get("/api/v1/signals").json()[0]
    signal_context = signal["signal_context"]
    assert signal_context["policy_version"] == "2026-04-18-regime-policy-1"
    assert signal_context["risk_multiplier"] == 0.4
    assert signal_context["allowed_playbooks"] == ["position_long"]
    assert "macro_uncertainty" in signal_context["blocked_reason"]
    assert signal_context["guard_results"]["blocked"] is True
    assert "regime_policy" in signal_context["guard_results"]["types"]
    assert signal_context["regime_policy"]["entry_allowed"] is False

    journal = client.get("/api/v1/journal").json()
    regime_entries = [entry for entry in journal if entry["decision"] == "skip_regime_policy"]
    assert len(regime_entries) == 1
    assert regime_entries[0]["observations"]["policy_version"] == "2026-04-18-regime-policy-1"
    assert regime_entries[0]["observations"]["allowed_playbooks"] == ["position_long"]

    positions = client.get("/api/v1/positions").json()
    assert positions == []


def test_orchestrator_do_blocks_entries_when_risk_budget_kill_switch_is_active(client: TestClient) -> None:
    original_analyze_ticker = learning_api.orchestrator_service.signal_service.analyze_ticker
    original_discovery = learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists
    learning_api.orchestrator_service.signal_service.analyze_ticker = lambda ticker: {
        "quant_summary": {
            "price": 100.0,
            "sma_20": 98.0,
            "sma_50": 95.0,
            "sma_200": 90.0,
            "rsi_14": 61.0,
            "relative_volume": 1.9,
            "atr_14": 2.0,
            "week_performance": 0.03,
            "month_performance": 0.08,
            "trend": "uptrend",
            "setup": "breakout",
            "risk_reward": 2.2,
        },
        "visual_summary": {
            "setup_type": "breakout",
            "setup_quality": 0.84,
            "visual_score": 0.81,
        },
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 95.0,
        "target_price": 110.0,
        "risk_reward": 2.2,
        "decision_confidence": 0.86,
        "rationale": f"Kill switch test signal for {ticker}.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }
    try:
        strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "risk_kill_switch_strategy",
                "name": "Risk Kill Switch Strategy",
                "description": "Strategy used to test global drawdown kill switch.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "Stand down after large daily losses.",
                    "general_rules": {},
                    "parameters": {"daily_drawdown_limit_pct": -4.0},
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()
        watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "risk_kill_switch_watchlist",
                "name": "Risk Kill Switch Watchlist",
                "strategy_id": strategy["id"],
                "hypothesis": "Do not enter after breaching the daily loss limit.",
                "status": "active",
            },
        ).json()
        assert client.post(
            f"/api/v1/watchlists/{watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "Kill switch candidate"},
        ).status_code == 201

        loss_position = client.post(
            "/api/v1/positions",
            json={
                "ticker": "AAPL",
                "entry_price": 100.0,
                "stop_price": 95.0,
                "target_price": 110.0,
                "size": 1.0,
                "entry_context": {"source": "kill_switch_seed"},
            },
        ).json()
        assert client.post(
            f"/api/v1/positions/{loss_position['id']}/close",
            json={
                "exit_price": 95.0,
                "exit_reason": "seed_loss",
                "max_drawdown_pct": -5.0,
                "max_runup_pct": 0.5,
            },
        ).status_code == 200

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery

    assert response.status_code == 200
    payload = response.json()
    assert payload["opened_positions"] == 0
    assert payload["metrics"]["risk_budget_blocked_entries"] == 1
    assert payload["candidates"][0]["decision"] == "watch"

    signal = client.get("/api/v1/signals").json()[0]
    assert signal["signal_context"]["risk_budget"]["kill_switch"]["triggered"] is True
    assert "risk_budget" in signal["signal_context"]["guard_results"]["types"]
