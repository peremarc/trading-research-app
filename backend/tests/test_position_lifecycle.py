def test_position_creation_records_open_event(client) -> None:
    response = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
            "size": 1,
            "thesis": "Breakout through resistance",
            "entry_context": {"source": "test"},
            "opening_reason": "Strategy breakout_long saw expanding volume",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event_type"] == "open"
    assert payload["events"][0]["payload"]["opening_reason"] == "Strategy breakout_long saw expanding volume"


def test_manage_position_updates_risk_and_records_decision_event(client) -> None:
    created = client.post(
        "/api/v1/positions",
        json={
            "ticker": "AAPL",
            "entry_price": 200,
            "stop_price": 192,
            "target_price": 216,
            "size": 1,
            "thesis": "Pullback entry",
            "entry_context": {"source": "test"},
        },
    )
    position_id = created.json()["id"]

    response = client.post(
        f"/api/v1/positions/{position_id}/manage",
        json={
            "event_type": "risk_update",
            "observed_price": 205,
            "stop_price": 198,
            "target_price": 220,
            "thesis": "Trend intact, tightening stop after follow-through",
            "rationale": "Price reclaimed prior breakout level and reduced downside risk.",
            "management_context": {"setup_quality": 0.82, "relative_volume": 1.6},
            "note": "Tighten risk after confirmation",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["stop_price"] == 198
    assert payload["target_price"] == 220
    assert payload["thesis"] == "Trend intact, tightening stop after follow-through"
    assert len(payload["events"]) == 2
    manage_event = payload["events"][-1]
    assert manage_event["event_type"] == "risk_update"
    assert manage_event["payload"]["previous_stop_price"] == 192
    assert manage_event["payload"]["new_stop_price"] == 198
    assert manage_event["payload"]["rationale"] == "Price reclaimed prior breakout level and reduced downside risk."


def test_manage_closed_position_is_rejected(client) -> None:
    created = client.post(
        "/api/v1/positions",
        json={
            "ticker": "MSFT",
            "entry_price": 300,
            "stop_price": 290,
            "target_price": 330,
            "size": 1,
        },
    )
    position_id = created.json()["id"]

    closed = client.post(
        f"/api/v1/positions/{position_id}/close",
        json={
            "exit_price": 310,
            "exit_reason": "target_hit",
        },
    )
    assert closed.status_code == 200

    response = client.post(
        f"/api/v1/positions/{position_id}/manage",
        json={
            "event_type": "risk_update",
            "stop_price": 305,
            "rationale": "Should not be allowed.",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only open positions can be managed"
