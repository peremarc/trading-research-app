from fastapi.testclient import TestClient


def test_hypotheses_setups_and_signal_definitions_can_be_created_and_listed(client: TestClient) -> None:
    hypothesis_response = client.post(
        "/api/v1/hypotheses",
        json={
            "code": "trend_pullback_quality",
            "name": "Trend Pullback Quality",
            "description": "Hypothesis for quality names resuming after orderly pullbacks.",
            "proposition": "Quality stocks in primary uptrends often resume after orderly pullbacks with constructive context.",
            "market": "US_EQUITIES",
            "horizon": "days_weeks",
            "bias": "long",
            "success_criteria": {"min_win_rate_pct": 55, "min_avg_pnl_pct": 1.2},
            "status": "active",
            "version": 1,
        },
    )

    assert hypothesis_response.status_code == 201
    hypothesis_payload = hypothesis_response.json()
    assert hypothesis_payload["code"] == "trend_pullback_quality"

    strategy_response = client.post(
        "/api/v1/strategies",
        json={
            "code": "quality_pullback_long",
            "name": "Quality Pullback Long",
            "description": "Long strategy for quality stocks pulling back into support.",
            "hypothesis_id": hypothesis_payload["id"],
            "market": "US_EQUITIES",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": hypothesis_payload["proposition"],
                "general_rules": {"price_above_sma50": True},
                "parameters": {"max_extension_at_entry_pct": 4},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )

    assert strategy_response.status_code == 201
    strategy_payload = strategy_response.json()
    assert strategy_payload["hypothesis_id"] == hypothesis_payload["id"]

    setup_response = client.post(
        "/api/v1/setups",
        json={
            "code": "quality_pullback_sma20",
            "name": "Quality Pullback To SMA20",
            "description": "Pullback into rising SMA20 with constructive trend structure.",
            "hypothesis_id": hypothesis_payload["id"],
            "strategy_id": strategy_payload["id"],
            "timeframe": "1D",
            "ideal_context": {"trend": "uptrend"},
            "conditions": {"price_above_sma50": True, "rsi_range": [48, 62]},
            "parameters": {"support_reference": "sma20"},
            "status": "active",
            "version": 1,
        },
    )

    assert setup_response.status_code == 201
    setup_payload = setup_response.json()
    assert setup_payload["strategy_id"] == strategy_payload["id"]
    assert setup_payload["hypothesis_id"] == hypothesis_payload["id"]

    signal_definition_response = client.post(
        "/api/v1/signal-definitions",
        json={
            "code": "quality_pullback_confirmation",
            "name": "Quality Pullback Confirmation",
            "description": "Confirmation signal for quality pullback resumptions.",
            "hypothesis_id": hypothesis_payload["id"],
            "strategy_id": strategy_payload["id"],
            "setup_id": setup_payload["id"],
            "signal_kind": "confirmation",
            "definition": "Pullback holds support and resumes with constructive structure.",
            "parameters": {"support_reference": "sma20"},
            "activation_conditions": {"trend_intact": True, "rsi_range": [48, 62]},
            "intended_usage": "Use before opening a pullback-continuation trade.",
            "status": "active",
            "version": 1,
        },
    )

    assert signal_definition_response.status_code == 201
    signal_definition_payload = signal_definition_response.json()
    assert signal_definition_payload["setup_id"] == setup_payload["id"]
    assert signal_definition_payload["hypothesis_id"] == hypothesis_payload["id"]

    screener_response = client.post(
        "/api/v1/screeners",
        json={
            "code": "quality_pullback_daily",
            "name": "Quality Pullback Daily",
            "description": "Screener for quality pullback candidates.",
            "strategy_id": strategy_payload["id"],
            "initial_version": {
                "definition": {"filters": ["price > sma50", "rsi_14 between 48 and 62"]},
                "sorting": {"field": "month_performance", "direction": "desc"},
                "status": "approved",
            },
        },
    )

    assert screener_response.status_code == 201

    screener_version_response = client.post(
        f"/api/v1/screeners/{screener_response.json()['id']}/versions",
        json={
            "definition": {"filters": ["price > sma20", "price > sma50"]},
            "sorting": {"field": "relative_volume", "direction": "desc"},
            "status": "approved",
        },
    )

    assert screener_version_response.status_code == 201

    watchlist_response = client.post(
        "/api/v1/watchlists",
        json={
            "code": "quality_pullback_watchlist",
            "name": "Quality Pullback Watchlist",
            "hypothesis_id": hypothesis_payload["id"],
            "strategy_id": strategy_payload["id"],
            "setup_id": setup_payload["id"],
            "hypothesis": "Watch quality pullback resumptions.",
            "status": "active",
        },
    )

    assert watchlist_response.status_code == 201

    watchlist_item_response = client.post(
        f"/api/v1/watchlists/{watchlist_response.json()['id']}/items",
        json={
            "ticker": "NVDA",
            "setup_id": setup_payload["id"],
            "score": 0.72,
            "reason": "Manual quality pullback candidate.",
            "key_metrics": {"source": "manual_test"},
            "state": "watching",
        },
    )

    assert watchlist_item_response.status_code == 201

    hypotheses_response = client.get("/api/v1/hypotheses")
    setups_response = client.get("/api/v1/setups")
    signal_definitions_response = client.get("/api/v1/signal-definitions")
    events_response = client.get("/api/v1/events?limit=20")

    assert hypotheses_response.status_code == 200
    assert setups_response.status_code == 200
    assert signal_definitions_response.status_code == 200
    assert events_response.status_code == 200
    assert [item["code"] for item in hypotheses_response.json()] == ["trend_pullback_quality"]
    assert [item["code"] for item in setups_response.json()] == ["quality_pullback_sma20"]
    assert [item["code"] for item in signal_definitions_response.json()] == ["quality_pullback_confirmation"]
    assert {item["event_type"] for item in events_response.json()} >= {
        "hypothesis.created",
        "setup.created",
        "signal_definition.created",
        "strategy.created",
        "screener.created",
        "screener.version_created",
        "watchlist.created",
        "watchlist_item.added",
    }


def test_seed_exposes_hypotheses_setups_and_signal_definitions_catalog(client: TestClient) -> None:
    seed_response = client.post("/api/v1/bootstrap/seed")

    assert seed_response.status_code == 201

    hypotheses_response = client.get("/api/v1/hypotheses")
    setups_response = client.get("/api/v1/setups")
    signal_definitions_response = client.get("/api/v1/signal-definitions")

    assert hypotheses_response.status_code == 200
    assert setups_response.status_code == 200
    assert signal_definitions_response.status_code == 200
    assert len(hypotheses_response.json()) == 3
    assert len(setups_response.json()) == 3
    assert len(signal_definitions_response.json()) == 3


def test_watchlist_creation_accepts_initial_items_and_records_catalog_events(client: TestClient) -> None:
    strategy_response = client.post(
        "/api/v1/strategies",
        json={
            "code": "initial_item_watchlist_strategy",
            "name": "Initial Item Watchlist Strategy",
            "description": "Strategy used to test watchlist creation with initial items.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Operator-curated watchlists can start with an initial batch of items.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )
    assert strategy_response.status_code == 201

    watchlist_response = client.post(
        "/api/v1/watchlists",
        json={
            "code": "initial_item_watchlist",
            "name": "Initial Item Watchlist",
            "strategy_id": strategy_response.json()["id"],
            "hypothesis": "Manual watchlist with initial curated items.",
            "status": "active",
            "initial_items": [
                {
                    "ticker": "NVDA",
                    "score": 0.74,
                    "reason": "Curated initial candidate.",
                    "key_metrics": {"source": "initial_batch"},
                    "state": "watching",
                },
                {
                    "ticker": "MSFT",
                    "score": 0.69,
                    "reason": "Second curated initial candidate.",
                    "key_metrics": {"source": "initial_batch"},
                    "state": "watching",
                },
            ],
        },
    )

    assert watchlist_response.status_code == 201
    payload = watchlist_response.json()
    assert payload["code"] == "initial_item_watchlist"
    assert [item["ticker"] for item in payload["items"]] == ["NVDA", "MSFT"]

    events_response = client.get("/api/v1/events?limit=10")
    assert events_response.status_code == 200
    event_types = [item["event_type"] for item in events_response.json()]
    assert event_types.count("watchlist.created") >= 1
    assert event_types.count("watchlist_item.added") >= 2


def test_generated_signals_and_positions_accept_traceability_ids(client: TestClient) -> None:
    seed_response = client.post("/api/v1/bootstrap/seed")
    assert seed_response.status_code == 201

    hypotheses = client.get("/api/v1/hypotheses").json()
    setups = client.get("/api/v1/setups").json()
    signal_definitions = client.get("/api/v1/signal-definitions").json()

    signal_response = client.post(
        "/api/v1/signals",
        json={
            "hypothesis_id": hypotheses[0]["id"],
            "strategy_id": 1,
            "strategy_version_id": 1,
            "setup_id": setups[0]["id"],
            "signal_definition_id": signal_definitions[0]["id"],
            "ticker": "NVDA",
            "timeframe": "1D",
            "signal_type": "watchlist_analysis",
            "thesis": "Test signal with explicit traceability IDs.",
            "entry_zone": {"price": 100.0},
            "stop_zone": {"price": 95.0},
            "target_zone": {"price": 110.0},
            "signal_context": {"source": "test"},
            "quality_score": 0.82,
            "status": "new",
        },
    )

    assert signal_response.status_code == 201
    signal_payload = signal_response.json()
    assert signal_payload["hypothesis_id"] == hypotheses[0]["id"]
    assert signal_payload["setup_id"] == setups[0]["id"]
    assert signal_payload["signal_definition_id"] == signal_definitions[0]["id"]

    trade_signals_response = client.get("/api/v1/trade-signals")
    assert trade_signals_response.status_code == 200
    trade_signals_payload = trade_signals_response.json()
    assert len(trade_signals_payload) == 1
    assert trade_signals_payload[0]["id"] == signal_payload["id"]

    trade_signal_status_response = client.post(
        f"/api/v1/trade-signals/{signal_payload['id']}/status",
        json={"status": "reviewed", "rejection_reason": None},
    )
    assert trade_signal_status_response.status_code == 200
    assert trade_signal_status_response.json()["status"] == "reviewed"

    position_response = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "hypothesis_id": hypotheses[0]["id"],
            "trade_signal_id": signal_payload["id"],
            "setup_id": setups[0]["id"],
            "signal_definition_id": signal_definitions[0]["id"],
            "strategy_version_id": 1,
            "analysis_run_id": None,
            "account_mode": "paper",
            "side": "long",
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 110.0,
            "size": 1.5,
            "thesis": "Traceable seeded test position.",
            "entry_context": {"source": "test_signal"},
            "opening_reason": "Test open",
        },
    )

    assert position_response.status_code == 201
    position_payload = position_response.json()
    assert position_payload["hypothesis_id"] == hypotheses[0]["id"]
    assert position_payload["signal_id"] == signal_payload["id"]
    assert position_payload["trade_signal_id"] == signal_payload["id"]
    assert position_payload["setup_id"] == setups[0]["id"]
    assert position_payload["signal_definition_id"] == signal_definitions[0]["id"]

    review_response = client.post(
        f"/api/v1/trade-reviews/positions/{position_payload['id']}",
        json={
            "outcome_label": "loss",
            "cause_category": "timing",
            "observations": {"source": "test"},
            "root_cause": "Entry was too early for the setup quality available.",
            "lesson_learned": "Wait for cleaner confirmation before entering.",
            "should_modify_strategy": False,
        },
    )

    assert review_response.status_code == 201

    events_response = client.get("/api/v1/events?limit=20")
    assert events_response.status_code == 200
    event_types = {item["event_type"] for item in events_response.json()}
    assert {"trade_signal.created", "position.opened", "trade_review.created"} <= event_types
