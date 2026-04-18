from app.domains.learning.agent import AIDecisionError, AgentDecision, AgentToolStep
from app.domains.execution import api as execution_api
from app.domains.execution.services import ExitManagementService
from app.domains.learning import api as learning_api
from app.providers.market_data.base import MarketSnapshot


class FixedMarketDataService:
    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(
            ticker=ticker,
            price=104.0,
            sma_20=103.0,
            sma_50=100.0,
            sma_200=95.0,
            rsi_14=62.0,
            relative_volume=1.8,
            atr_14=2.0,
            week_performance=0.04,
            month_performance=0.1,
        )


class StubManagementAgent:
    def advise_open_position_management(self, session, *, position, market_snapshot):
        return AgentDecision(
            action="tighten_stop_and_extend_target",
            confidence=0.81,
            thesis="Momentum remains constructive, so protect gains and allow more upside.",
            risks=["reversal after extension"],
            lessons_applied=["let winners run with tighter risk"],
            raw_payload={"source": "test"},
        )

    def plan_open_position_management_execution(self, *, position, market_snapshot, decision):
        return type(
            "Plan",
            (),
            {
                "action": decision.action,
                "confidence": decision.confidence,
                "rationale": decision.thesis,
                "steps": [
                    AgentToolStep(
                        tool_name="positions.manage",
                        arguments={
                            "position_id": position.id,
                            "event_type": "risk_update",
                            "observed_price": market_snapshot["price"],
                            "stop_price": 102.0,
                            "target_price": 108.0,
                            "rationale": decision.thesis,
                            "management_context": {
                                "source": "ai_position_management",
                                "ai_action": decision.action,
                                "ai_risks": decision.risks,
                                "market_snapshot": market_snapshot,
                            },
                        },
                        purpose="apply_ai_position_management",
                    )
                ],
                "should_execute": True,
            },
        )()


class FailingManagementAgent:
    def advise_open_position_management(self, session, *, position, market_snapshot):
        raise AIDecisionError("provider chain unavailable in test")

    def plan_open_position_management_execution(self, *, position, market_snapshot, decision):
        raise AssertionError("plan_open_position_management_execution should not be called when advise fails")


def test_auto_exit_evaluation_can_adjust_open_position_risk(client) -> None:
    original_market_data = execution_api.exit_management_service.market_data_service
    execution_api.exit_management_service.market_data_service = FixedMarketDataService()
    try:
        created = client.post(
            "/api/v1/positions",
            json={
                "ticker": "NVDA",
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 105,
                "size": 1,
                "thesis": "Momentum entry",
            },
        )
        assert created.status_code == 201

        response = client.post("/api/v1/exits/evaluate")
    finally:
        execution_api.exit_management_service.market_data_service = original_market_data

    assert response.status_code == 200
    payload = response.json()
    assert payload["closed_positions"] == 0
    assert payload["adjusted_positions"] == 1
    assert payload["results"][0]["adjusted"] is True
    assert payload["results"][0]["stop_price"] == 103.0
    assert payload["results"][0]["target_price"] == 108.0

    positions = client.get("/api/v1/positions").json()
    assert positions[0]["stop_price"] == 103.0
    assert positions[0]["target_price"] == 108.0
    assert positions[0]["events"][-1]["event_type"] == "risk_update"


def test_auto_exit_evaluation_can_apply_agent_management_decision(client) -> None:
    original_market_data = execution_api.exit_management_service.market_data_service
    original_agent = execution_api.exit_management_service.trading_agent_service
    execution_api.exit_management_service.market_data_service = FixedMarketDataService()
    execution_api.exit_management_service.trading_agent_service = StubManagementAgent()
    try:
        created = client.post(
            "/api/v1/positions",
            json={
                "ticker": "AAPL",
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 105,
                "size": 1,
                "thesis": "Trend continuation",
            },
        )
        assert created.status_code == 201

        response = client.post("/api/v1/exits/evaluate")
    finally:
        execution_api.exit_management_service.market_data_service = original_market_data
        execution_api.exit_management_service.trading_agent_service = original_agent

    assert response.status_code == 200
    payload = response.json()
    assert payload["adjusted_positions"] == 1
    assert payload["results"][0]["adjusted"] is True
    assert payload["results"][0]["stop_price"] == 102.0
    assert payload["results"][0]["target_price"] == 108.0

    positions = client.get("/api/v1/positions").json()
    event_payload = positions[0]["events"][-1]["payload"]
    assert event_payload["rationale"] == "Momentum remains constructive, so protect gains and allow more upside."
    assert event_payload["management_context"]["ai_action"] == "tighten_stop_and_extend_target"


def test_auto_exit_evaluation_falls_back_to_heuristics_when_ai_management_fails(client) -> None:
    original_market_data = execution_api.exit_management_service.market_data_service
    original_agent = execution_api.exit_management_service.trading_agent_service
    execution_api.exit_management_service.market_data_service = FixedMarketDataService()
    execution_api.exit_management_service.trading_agent_service = FailingManagementAgent()
    try:
        created = client.post(
            "/api/v1/positions",
            json={
                "ticker": "AMD",
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 105,
                "size": 1,
                "thesis": "Trend continuation",
            },
        )
        assert created.status_code == 201

        response = client.post("/api/v1/exits/evaluate")
    finally:
        execution_api.exit_management_service.market_data_service = original_market_data
        execution_api.exit_management_service.trading_agent_service = original_agent

    assert response.status_code == 200
    payload = response.json()
    assert payload["adjusted_positions"] == 1
    assert payload["results"][0]["adjusted"] is True
    assert payload["results"][0]["stop_price"] == 103.0
    assert payload["results"][0]["target_price"] == 108.0

    positions = client.get("/api/v1/positions").json()
    event_payload = positions[0]["events"][-1]["payload"]
    assert "AI unavailable" in event_payload["rationale"]
    assert event_payload["management_context"]["ai_error"] == "provider chain unavailable in test"


def test_realtime_market_event_can_close_open_position_immediately(client, session) -> None:
    created = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
            "size": 1,
            "thesis": "Realtime monitor candidate",
        },
    )
    assert created.status_code == 201

    service = ExitManagementService(market_data_service=FixedMarketDataService(), execution_event_source="monitor_stream")
    result = service.evaluate_positions_for_market_event(
        session,
        ticker="NVDA",
        realtime_quote={
            "source": "ibkr_realtime_sse",
            "ticker": "NVDA",
            "conid": "12345",
            "last_price": 94.5,
            "bid_price": 94.4,
            "ask_price": 94.6,
        },
    )

    assert result.closed_positions == 1
    positions = client.get("/api/v1/positions").json()
    assert positions[0]["status"] == "closed"
    assert positions[0]["exit_reason"] == "stop_loss_hit"
    assert positions[0]["close_context"]["source"] == "realtime_monitor"
    assert positions[0]["close_context"]["monitor_event"]["source"] == "ibkr_realtime_sse"


def test_do_phase_can_open_same_ticker_for_different_strategies(client) -> None:
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
        },
        "visual_summary": {"setup_type": "breakout", "setup_quality": 0.84},
        "combined_score": 0.86,
        "decision": "paper_enter",
        "entry_price": 100.0,
        "stop_price": 96.0,
        "target_price": 110.0,
        "risk_reward": 2.5,
        "decision_confidence": 0.86,
        "alpha_gap_pct": 4.2,
        "rationale": "Deterministic test signal.",
    }
    learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = lambda session: {
        "discovered_items": 0,
        "watchlists_scanned": 0,
        "universe_size": 0,
        "top_candidates": [],
        "benchmark_ticker": "SPY",
    }

    def capture_bullish_snapshot(session, *, trigger: str, pdca_phase: str | None = None, source_context: dict | None = None):
        from app.db.models.market_state_snapshot import MarketStateSnapshotRecord

        payload = {
            "summary": "World state for do phase: regime bullish_trend with room for multiple selective entries.",
            "market_state_snapshot": {
                "execution_mode": "global",
                "watchlist_code": None,
                "portfolio_state": {
                    "benchmark_ticker": "SPY",
                    "benchmark_price": 100.0,
                    "benchmark_month_performance": 0.08,
                    "market_state_trigger": trigger,
                    "market_state_phase": pdca_phase,
                },
                "open_positions": [],
                "recent_alerts": [],
                "macro_context": {
                    "summary": "Bullish trend regime.",
                    "active_regimes": ["bullish_trend"],
                    "global_regime": "bullish_trend",
                    "global_regime_confidence": 0.82,
                },
                "corporate_calendar": [],
                "market_regime_inputs": {
                    "benchmark_snapshot": {
                        "ticker": "SPY",
                        "price": 100.0,
                        "month_performance": 0.08,
                    },
                    "market_regime": {
                        "label": "bullish_trend",
                        "confidence": 0.82,
                        "justification": "Test bullish trend regime.",
                    },
                },
                "active_watchlists": [],
            },
            "market_regime": {
                "label": "bullish_trend",
                "confidence": 0.82,
                "justification": "Test bullish trend regime.",
            },
            "benchmark_snapshot": {
                "ticker": "SPY",
                "price": 100.0,
                "month_performance": 0.08,
            },
            "macro_context": {
                "summary": "Bullish trend regime.",
                "active_regimes": ["bullish_trend"],
            },
            "calendar_events": [],
            "calendar_error": None,
            "backlog": {
                "open_positions_count": 0,
                "pending_reviews": 0,
                "open_research_tasks": 0,
                "active_watchlists_count": 2,
            },
            "trigger": trigger,
            "pdca_phase": pdca_phase,
            "source_context": source_context or {},
        }
        snapshot = MarketStateSnapshotRecord(
            trigger=trigger,
            pdca_phase=pdca_phase,
            execution_mode=(source_context or {}).get("execution_mode"),
            benchmark_ticker="SPY",
            regime_label="bullish_trend",
            regime_confidence=0.82,
            summary=payload["summary"],
            snapshot_payload=payload,
            source_context=source_context or {},
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot

    learning_api.orchestrator_service.market_state_service.capture_snapshot = capture_bullish_snapshot
    try:
        first_strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "same_ticker_strategy_one",
                "name": "Same Ticker One",
                "description": "First strategy.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "First hypothesis.",
                    "general_rules": {},
                    "parameters": {},
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()
        second_strategy = client.post(
            "/api/v1/strategies",
            json={
                "code": "same_ticker_strategy_two",
                "name": "Same Ticker Two",
                "description": "Second strategy.",
                "horizon": "days_weeks",
                "bias": "long",
                "status": "paper",
                "initial_version": {
                    "hypothesis": "Second hypothesis.",
                    "general_rules": {},
                    "parameters": {},
                    "state": "approved",
                    "is_baseline": True,
                },
            },
        ).json()

        first_watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "same_ticker_watchlist_one",
                "name": "Same Ticker Watchlist One",
                "strategy_id": first_strategy["id"],
                "hypothesis": "First watchlist",
                "status": "active",
            },
        ).json()
        second_watchlist = client.post(
            "/api/v1/watchlists",
            json={
                "code": "same_ticker_watchlist_two",
                "name": "Same Ticker Watchlist Two",
                "strategy_id": second_strategy["id"],
                "hypothesis": "Second watchlist",
                "status": "active",
            },
        ).json()

        assert client.post(
            f"/api/v1/watchlists/{first_watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "First strategy candidate"},
        ).status_code == 201
        assert client.post(
            f"/api/v1/watchlists/{second_watchlist['id']}/items",
            json={"ticker": "NVDA", "reason": "Second strategy candidate"},
        ).status_code == 201

        response = client.post("/api/v1/orchestrator/do")
    finally:
        learning_api.orchestrator_service.signal_service.analyze_ticker = original_analyze_ticker
        learning_api.orchestrator_service.opportunity_discovery_service.refresh_active_watchlists = original_discovery
        learning_api.orchestrator_service.market_state_service.capture_snapshot = original_capture_snapshot

    assert response.status_code == 200
    positions = client.get("/api/v1/positions").json()
    nvda_positions = [position for position in positions if position["ticker"] == "NVDA"]
    assert len(nvda_positions) == 2
    assert {position["strategy_version_id"] for position in nvda_positions} == {
        first_strategy["current_version_id"],
        second_strategy["current_version_id"],
    }

    journal = client.get("/api/v1/journal").json()
    assert any(
        entry["entry_type"] == "agent_tool_call" and entry["decision"] == "positions.open"
        for entry in journal
    )
