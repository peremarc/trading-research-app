from fastapi.testclient import TestClient

from app.domains.system.events import EventLogService


def test_event_dispatch_runs_plan_for_catalog_changes(client: TestClient) -> None:
    hypothesis = client.post(
        "/api/v1/hypotheses",
        json={
            "code": "dispatchable_hypothesis",
            "name": "Dispatchable Hypothesis",
            "description": "Hypothesis used to test event-driven PLAN dispatch.",
            "proposition": "Fresh catalog changes should refresh the planning layer.",
            "market": "US_EQUITIES",
            "horizon": "days_weeks",
            "bias": "long",
            "success_criteria": {"min_win_rate_pct": 55},
            "status": "active",
            "version": 1,
        },
    )
    assert hypothesis.status_code == 201

    dispatch = client.post("/api/v1/events/dispatch")
    assert dispatch.status_code == 200

    payload = dispatch.json()
    assert payload["pending_events_seen"] == 1
    assert payload["processed_events"] == 1
    assert payload["ignored_events"] == 0
    assert payload["phases_run"] == ["plan"]

    cycles = client.get("/api/v1/pdca/cycles")
    assert cycles.status_code == 200
    assert len(cycles.json()) == 1
    assert cycles.json()[0]["phase"] == "plan"
    assert cycles.json()[0]["context"]["trigger"] == "event_dispatch"

    processed_events = client.get("/api/v1/events?dispatch_status=processed")
    assert processed_events.status_code == 200
    assert processed_events.json()[0]["event_type"] == "hypothesis.created"
    assert processed_events.json()[0]["dispatched_phase"] == "plan"


def test_event_dispatch_runs_check_and_act_for_closed_positions(client: TestClient) -> None:
    position = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "entry_price": 100.0,
            "stop_price": 95.0,
            "target_price": 110.0,
            "size": 1.0,
            "thesis": "Event-dispatch lifecycle test.",
            "entry_context": {"source": "test"},
            "opening_reason": "Open a test position for dispatch coverage.",
        },
    )
    assert position.status_code == 201

    closed = client.post(
        f"/api/v1/positions/{position.json()['id']}/close",
        json={
            "exit_price": 94.0,
            "exit_reason": "failed_breakout",
            "max_drawdown_pct": -6.0,
            "max_runup_pct": 1.0,
            "close_context": {"source": "test"},
        },
    )
    assert closed.status_code == 200

    dispatch = client.post("/api/v1/events/dispatch")
    assert dispatch.status_code == 200

    payload = dispatch.json()
    assert payload["pending_events_seen"] == 2
    assert payload["processed_events"] == 1
    assert payload["ignored_events"] == 1
    assert payload["phases_run"] == ["check", "act"]

    reviews = client.get(f"/api/v1/trade-reviews/positions/{position.json()['id']}")
    assert reviews.status_code == 200
    assert len(reviews.json()) == 1

    events = client.get("/api/v1/events?limit=10")
    assert events.status_code == 200
    status_by_type = {item["event_type"]: item["dispatch_status"] for item in events.json()}
    assert status_by_type["position.opened"] == "ignored"
    assert status_by_type["position.closed"] == "processed"
    assert status_by_type["trade_review.created"] == "processed"


def test_event_dispatch_runs_do_for_manual_watchlist_items(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "event_do_strategy",
            "name": "Event DO Strategy",
            "description": "Strategy used to test DO dispatch from manual watchlist additions.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "New manually added watchlist items should be executable work.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )
    assert strategy.status_code == 201

    watchlist = client.post(
        "/api/v1/watchlists",
        json={
            "code": "event_do_watchlist",
            "name": "Event DO Watchlist",
            "strategy_id": strategy.json()["id"],
            "hypothesis": "Manual additions should trigger DO.",
            "status": "active",
        },
    )
    assert watchlist.status_code == 201

    cleared = client.post("/api/v1/events/dispatch")
    assert cleared.status_code == 200
    assert cleared.json()["phases_run"] == ["plan"]

    item = client.post(
        f"/api/v1/watchlists/{watchlist.json()['id']}/items",
        json={
            "ticker": "NVDA",
            "reason": "Manual candidate for event-driven DO.",
            "key_metrics": {"source": "manual_test"},
        },
    )
    assert item.status_code == 201

    dispatch = client.post("/api/v1/events/dispatch")
    assert dispatch.status_code == 200
    assert dispatch.json()["phases_run"] == ["do"]
    assert dispatch.json()["processed_events"] == 1

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    assert len(signals.json()) >= 1

    processed = client.get("/api/v1/events?dispatch_status=processed&event_type=watchlist_item.added")
    assert processed.status_code == 200
    assert processed.json()[0]["source"] == "strategy_catalog"
    assert processed.json()[0]["dispatched_phase"] == "do"

    do_side_effects = client.get("/api/v1/events?dispatch_status=processed&event_type=trade_signal.created")
    assert do_side_effects.status_code == 200
    assert len(do_side_effects.json()) >= 1
    assert do_side_effects.json()[0]["source"] == "orchestrator_do"
    assert do_side_effects.json()[0]["dispatched_phase"] == "do"

    pending = client.get("/api/v1/events?dispatch_status=pending")
    assert pending.status_code == 200
    assert pending.json() == []


def test_event_dispatch_cascades_do_to_check_and_act_for_internal_position_closures(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "event_do_cascade_strategy",
            "name": "Event DO Cascade Strategy",
            "description": "Strategy used to test DO follow-up cascade into CHECK and ACT.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Manual DO work can close an existing paper position and should review it immediately.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )
    assert strategy.status_code == 201

    watchlist = client.post(
        "/api/v1/watchlists",
        json={
            "code": "event_do_cascade_watchlist",
            "name": "Event DO Cascade Watchlist",
            "strategy_id": strategy.json()["id"],
            "hypothesis": "Manual additions should trigger DO and let later phases chain forward.",
            "status": "active",
        },
    )
    assert watchlist.status_code == 201

    cleared = client.post("/api/v1/events/dispatch")
    assert cleared.status_code == 200
    assert cleared.json()["phases_run"] == ["plan"]

    position = client.post(
        "/api/v1/positions",
        json={
            "ticker": "AAPL",
            "entry_price": 10000.0,
            "stop_price": 10001.0,
            "target_price": 10002.0,
            "size": 1.0,
            "thesis": "Force an internal close during DO so the dispatcher can cascade forward.",
            "entry_context": {"source": "cascade_test"},
            "opening_reason": "Create a position that will be closed by the autonomous exit check.",
        },
    )
    assert position.status_code == 201

    item = client.post(
        f"/api/v1/watchlists/{watchlist.json()['id']}/items",
        json={
            "ticker": "NVDA",
            "reason": "Manual candidate to trigger DO.",
            "key_metrics": {"source": "cascade_test"},
        },
    )
    assert item.status_code == 201

    dispatch = client.post("/api/v1/events/dispatch")
    assert dispatch.status_code == 200

    payload = dispatch.json()
    assert payload["pending_events_seen"] == 2
    assert payload["processed_events"] == 1
    assert payload["ignored_events"] == 1
    assert payload["phases_run"] == ["do", "check", "act"]

    reviews = client.get(f"/api/v1/trade-reviews/positions/{position.json()['id']}")
    assert reviews.status_code == 200
    assert len(reviews.json()) == 1

    events = client.get("/api/v1/events?limit=20")
    assert events.status_code == 200
    statuses_by_type = {}
    for item in events.json():
        statuses_by_type.setdefault(item["event_type"], set()).add(item["dispatch_status"])
    assert "processed" in statuses_by_type["position.closed"]
    assert "processed" in statuses_by_type["trade_review.created"]
    assert "ignored" in statuses_by_type["position.opened"]

    pending = client.get("/api/v1/events?dispatch_status=pending")
    assert pending.status_code == 200
    assert pending.json() == []


def test_event_dispatch_runs_plan_then_do_for_watchlists_with_initial_items(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "event_plan_do_watchlist_strategy",
            "name": "Event Plan DO Watchlist Strategy",
            "description": "Strategy used to test watchlist creation with initial items.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Creating a watchlist with curated initial items should plan and execute in sequence.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )
    assert strategy.status_code == 201

    cleared = client.post("/api/v1/events/dispatch")
    assert cleared.status_code == 200
    assert cleared.json()["phases_run"] == ["plan"]

    watchlist = client.post(
        "/api/v1/watchlists",
        json={
            "code": "event_plan_do_watchlist",
            "name": "Event Plan DO Watchlist",
            "strategy_id": strategy.json()["id"],
            "hypothesis": "Initial items should create immediate executable work.",
            "status": "active",
            "initial_items": [
                {
                    "ticker": "NVDA",
                    "reason": "Initial curated candidate for event-driven flow.",
                    "key_metrics": {"source": "initial_batch_test"},
                }
            ],
        },
    )
    assert watchlist.status_code == 201
    assert len(watchlist.json()["items"]) == 1

    dispatch = client.post("/api/v1/events/dispatch")
    assert dispatch.status_code == 200
    payload = dispatch.json()
    assert payload["pending_events_seen"] == 2
    assert payload["processed_events"] == 2
    assert payload["ignored_events"] == 0
    assert payload["phases_run"] == ["plan", "do"]

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    assert len(signals.json()) >= 1

    pending = client.get("/api/v1/events?dispatch_status=pending")
    assert pending.status_code == 200
    assert pending.json() == []


def test_event_log_marks_internal_do_side_effects_as_processed_on_record(session) -> None:
    event_log_service = EventLogService()
    processed_types = [
        "trade_signal.created",
        "trade_signal.status_updated",
        "position.opened",
        "position.managed",
    ]
    for event_type in processed_types:
        event = event_log_service.record(
            session,
            event_type=event_type,
            entity_type="test_entity",
            entity_id=1,
            source="orchestrator_do",
            pdca_phase_hint="do",
            payload={"ticker": "NVDA"},
        )
        assert event.dispatch_status == "processed"
        assert event.dispatched_phase == "do"
        assert event.processed_at is not None

    check_follow_up = event_log_service.record(
        session,
        event_type="position.closed",
        entity_type="position",
        entity_id=2,
        source="orchestrator_do",
        pdca_phase_hint="check",
        payload={"ticker": "NVDA"},
    )
    assert check_follow_up.dispatch_status == "pending"
    assert check_follow_up.dispatched_phase is None


def test_event_log_ignores_discovery_generated_watchlist_items_on_record(session) -> None:
    event_log_service = EventLogService()
    event = event_log_service.record(
        session,
        event_type="watchlist_item.added",
        entity_type="watchlist_item",
        entity_id=1,
        source="opportunity_discovery",
        pdca_phase_hint="do",
        payload={"ticker": "NVDA", "watchlist_id": 1},
    )
    assert event.dispatch_status == "ignored"
    assert event.processed_at is not None

    result = event_log_service.dispatch_pending(session, orchestrator_service=object())

    assert result["pending_events_seen"] == 0
    assert result["ignored_events"] == 0
    assert result["processed_events"] == 0
    assert result["phases_run"] == []

    ignored = event_log_service.list_events(session, limit=5, dispatch_status="ignored")
    assert ignored[0].id == event.id
    assert ignored[0].dispatch_status == "ignored"
