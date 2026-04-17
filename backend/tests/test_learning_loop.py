from fastapi.testclient import TestClient


def test_orchestrator_do_persists_signals(client: TestClient) -> None:
    seeded = client.post("/api/v1/bootstrap/seed")
    assert seeded.status_code == 201

    response = client.post("/api/v1/orchestrator/do")
    assert response.status_code == 200

    payload = response.json()
    assert payload["metrics"]["generated_signals"] >= 6
    assert payload["metrics"]["discovered_items"] >= 1
    assert len(payload["candidates"]) >= 6
    assert all(candidate["signal_id"] is not None for candidate in payload["candidates"])

    signals = client.get("/api/v1/signals")
    assert signals.status_code == 200
    assert len(signals.json()) >= 6


def test_check_phase_generates_strategy_health_and_research_tasks(client: TestClient) -> None:
    assert client.post("/api/v1/bootstrap/seed").status_code == 201
    assert client.post("/api/v1/orchestrator/do").status_code == 200

    check = client.post("/api/v1/orchestrator/check")
    assert check.status_code == 200

    scorecards = client.get("/api/v1/strategy-health")
    assert scorecards.status_code == 200
    assert len(scorecards.json()) == 3
    assert any("fitness_score" in item for item in scorecards.json())

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    assert any(task["task_type"] == "improve_signal_flow" for task in research_tasks.json())

    plan = client.post("/api/v1/orchestrator/plan", json={"cycle_date": "2026-04-16", "market_context": {}})
    assert plan.status_code == 201
    assert plan.json()["work_queue"]["total_items"] >= 1

    pipelines = client.get("/api/v1/strategy-health/pipelines")
    assert pipelines.status_code == 200
    assert len(pipelines.json()) >= 1


def test_trade_review_supports_structured_learning_fields(client: TestClient) -> None:
    strategy_payload = {
        "code": "review_strategy",
        "name": "Review Strategy",
        "description": "Strategy for review test.",
        "horizon": "days_weeks",
        "bias": "long",
        "status": "paper",
        "initial_version": {
            "hypothesis": "Test hypothesis.",
            "general_rules": {"price_above_sma50": True},
            "parameters": {"max_risk_per_trade_r": 1.0},
            "state": "approved",
            "is_baseline": True,
        },
    }
    strategy = client.post("/api/v1/strategies", json=strategy_payload)
    assert strategy.status_code == 201
    strategy_version_id = strategy.json()["current_version_id"]

    signal = client.post(
        "/api/v1/signals",
        json={
            "ticker": "NVDA",
            "strategy_id": strategy.json()["id"],
            "strategy_version_id": strategy_version_id,
            "signal_type": "breakout",
            "thesis": "Breakout signal",
            "entry_zone": {"price": 100},
            "stop_zone": {"price": 95},
            "target_zone": {"price": 110},
            "signal_context": {"source": "test"},
            "quality_score": 0.84,
        },
    )
    assert signal.status_code == 201

    position = client.post(
        "/api/v1/positions",
        json={
            "ticker": "NVDA",
            "signal_id": signal.json()["id"],
            "strategy_version_id": strategy_version_id,
            "entry_price": 100,
            "stop_price": 95,
            "target_price": 110,
            "size": 1,
            "thesis": "Breakout signal",
            "entry_context": {"source": "test"},
        },
    )
    assert position.status_code == 201

    closed = client.post(
        f"/api/v1/positions/{position.json()['id']}/close",
        json={
            "exit_price": 94,
            "exit_reason": "false_breakout",
            "max_drawdown_pct": -6.0,
            "max_runup_pct": 1.5,
            "close_context": {"failed_level": "entry_day_high"},
        },
    )
    assert closed.status_code == 200

    review = client.post(
        f"/api/v1/trade-reviews/positions/{position.json()['id']}",
        json={
            "outcome_label": "loss",
            "outcome": "loss",
            "cause_category": "false_breakout",
            "failure_mode": "false_breakout",
            "observations": {"volume_confirmation": False},
            "root_cause": "Breakout lacked confirmation.",
            "root_causes": ["Breakout lacked confirmation.", "Entry was too extended."],
            "lesson_learned": "Demand stronger confirmation.",
            "proposed_strategy_change": "Raise minimum relative volume.",
            "recommended_changes": ["Raise minimum relative volume.", "Avoid extended entries."],
            "confidence": 0.72,
            "review_priority": "high",
            "should_modify_strategy": True,
            "needs_strategy_update": True,
            "strategy_update_reason": "Repeated false breakout profile.",
        },
    )
    assert review.status_code == 201

    payload = review.json()
    assert payload["failure_mode"] == "false_breakout"
    assert payload["root_causes"] == ["Breakout lacked confirmation.", "Entry was too extended."]
    assert payload["recommended_changes"] == ["Raise minimum relative volume.", "Avoid extended entries."]
    assert payload["needs_strategy_update"] is True


def test_check_phase_tracks_failure_patterns(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "pattern_strategy",
            "name": "Pattern Strategy",
            "description": "Failure pattern test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Pattern hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    )
    strategy_version_id = strategy.json()["current_version_id"]

    for ticker in ["AAPL", "MSFT"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy.json()["id"],
                "strategy_version_id": strategy_version_id,
                "signal_type": "breakout",
                "thesis": "Breakout signal",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.8,
            },
        )
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal.json()["id"],
                "strategy_version_id": strategy_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        )
        client.post(
            f"/api/v1/positions/{position.json()['id']}/close",
            json={
                "exit_price": 94,
                "exit_reason": "false_breakout",
                "max_drawdown_pct": -6.0,
                "max_runup_pct": 1.2,
            },
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position.json()['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"volume_confirmation": False, "pnl_pct": -6.0},
                "root_cause": "Breakout lacked confirmation.",
                "root_causes": ["Breakout lacked confirmation."],
                "lesson_learned": "Demand stronger confirmation.",
                "recommended_changes": ["Raise minimum relative volume."],
                "proposed_strategy_change": "Raise minimum relative volume.",
                "should_modify_strategy": False,
                "needs_strategy_update": True,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    check = client.post("/api/v1/orchestrator/check")
    assert check.status_code == 200
    assert check.json()["metrics"]["failure_patterns_tracked"] >= 1

    patterns = client.get(f"/api/v1/failure-patterns/{strategy.json()['id']}")
    assert patterns.status_code == 200
    assert patterns.json()[0]["failure_mode"] == "false_breakout"
    assert patterns.json()[0]["occurrences"] >= 2


def test_act_phase_degrades_strategy_after_repeated_failures(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "degrade_strategy",
            "name": "Degrade Strategy",
            "description": "Degradation test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Weak setup.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    strategy_version_id = strategy["current_version_id"]

    for ticker in ["SHOP", "SQ"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": strategy_version_id,
                "signal_type": "breakout",
                "thesis": "Weak breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.2,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": strategy_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200
    assert act.json()["metrics"]["degraded_strategies"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    degraded = next(item for item in strategies if item["id"] == strategy["id"])
    assert degraded["status"] == "degraded"
    degraded_version = next(version for version in degraded["versions"] if version["id"] == strategy_version_id)
    assert degraded_version["lifecycle_stage"] == "degraded"

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert payload["active_version"] is None
    assert any(version["id"] == strategy_version_id for version in payload["degraded_versions"])


def test_act_phase_forks_candidate_variant_from_failure_pattern(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "fork_strategy",
            "name": "Fork Strategy",
            "description": "Failure fork test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    original_version_id = strategy["current_version_id"]

    for ticker in ["MDB", "DDOG"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": original_version_id,
                "signal_type": "breakout",
                "thesis": "Fork breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.25,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": original_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200
    assert act.json()["metrics"]["forked_variants"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    forked = next(item for item in strategies if item["id"] == strategy["id"])
    assert forked["current_version_id"] == original_version_id
    assert len(forked["versions"]) >= 2
    original_version = next(version for version in forked["versions"] if version["id"] == original_version_id)
    assert original_version["lifecycle_stage"] in {"active", "degraded"}
    candidate_versions = [version for version in forked["versions"] if version["id"] != original_version_id]
    assert candidate_versions[0]["state"] == "draft"
    assert candidate_versions[0]["lifecycle_stage"] == "candidate"

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert any(version["id"] == candidate_versions[0]["id"] for version in payload["candidate_versions"])
    if forked["status"] == "degraded":
        assert payload["active_version"] is None
        assert any(version["id"] == original_version_id for version in payload["degraded_versions"])
    else:
        assert payload["active_version"]["id"] == original_version_id

    plan = client.post("/api/v1/orchestrator/plan", json={"cycle_date": "2026-04-16", "market_context": {}})
    assert plan.status_code == 201
    plan_payload = plan.json()
    queue_items = plan_payload["work_queue"]["items"]
    candidate_validation = next(
        item for item in queue_items if item["item_type"] == "degraded_candidate_validation"
    )
    assert candidate_validation["priority"] == "P3"
    assert candidate_validation["context"]["strategy_id"] == strategy["id"]
    assert plan_payload["market_context"]["degraded_candidate_backlog"] >= 1


def test_act_phase_archives_strategy_when_failures_persist_without_activity(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "archive_strategy",
            "name": "Archive Strategy",
            "description": "Archive test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "research",
            "initial_version": {
                "hypothesis": "Weak inactive setup.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    strategy_version_id = strategy["current_version_id"]

    for ticker in ["U", "SNAP", "PINS"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": strategy_version_id,
                "signal_type": "breakout",
                "thesis": "Weak inactive breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.1,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": strategy_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 93, "exit_reason": "false_breakout", "max_drawdown_pct": -7, "max_runup_pct": 0.5},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -7.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": True,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act1 = client.post("/api/v1/orchestrator/act")
    assert act1.status_code == 200
    act2 = client.post("/api/v1/orchestrator/act")
    assert act2.status_code == 200
    assert (
        act1.json()["metrics"]["archived_strategies"] >= 1
        or act2.json()["metrics"]["archived_strategies"] >= 1
    )

    strategies = client.get("/api/v1/strategies").json()
    archived = next(item for item in strategies if item["id"] == strategy["id"])
    assert archived["status"] == "archived"
    assert all(version["lifecycle_stage"] == "archived" for version in archived["versions"])

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert payload["active_version"] is None
    assert len(payload["archived_versions"]) == payload["total_versions"]


def test_act_phase_promotes_candidate_variant_after_positive_results(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "promote_candidate_strategy",
            "name": "Promote Candidate Strategy",
            "description": "Candidate promotion test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["NET", "CRWD"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "breakout",
                "thesis": "Candidate seed breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.2,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    fork_act = client.post("/api/v1/orchestrator/act")
    assert fork_act.status_code == 200
    assert fork_act.json()["metrics"]["forked_variants"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    strategy_after_fork = next(item for item in strategies if item["id"] == strategy["id"])
    candidate = next(version for version in strategy_after_fork["versions"] if version["id"] != base_version_id)

    for ticker in ["NOW", "SNOW"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": candidate["id"],
                "signal_type": "candidate_breakout",
                "thesis": "Candidate breakout",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.85,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": candidate["id"],
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
                "entry_context": {"execution_mode": "candidate_validation"},
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 105, "exit_reason": "trend_follow_through", "max_drawdown_pct": -1, "max_runup_pct": 6},
        )

    promote_act = client.post("/api/v1/orchestrator/act")
    assert promote_act.status_code == 200
    assert promote_act.json()["metrics"]["promoted_candidates"] >= 1

    strategies = client.get("/api/v1/strategies").json()
    promoted = next(item for item in strategies if item["id"] == strategy["id"])
    assert promoted["current_version_id"] == candidate["id"]
    promoted_version = next(version for version in promoted["versions"] if version["id"] == candidate["id"])
    assert promoted_version["state"] == "approved"
    assert promoted_version["lifecycle_stage"] == "active"
    base_version = next(version for version in promoted["versions"] if version["id"] == base_version_id)
    assert base_version["lifecycle_stage"] == "approved"

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    payload = pipeline.json()
    assert payload["active_version"]["id"] == candidate["id"]
    assert payload["total_versions"] >= 2
    assert len(payload["candidate_versions"]) == 0
    assert any(version["id"] == base_version_id for version in payload["approved_versions"])

    validation_summaries = client.get("/api/v1/strategy-evolution/candidate-validations")
    assert validation_summaries.status_code == 200
    promoted_summary = next(
        item for item in validation_summaries.json() if item["candidate_version_id"] == candidate["id"]
    )
    assert promoted_summary["evaluation_status"] == "promote"
    assert promoted_summary["trade_count"] == 2

    pipeline = client.get(f"/api/v1/strategy-health/{strategy['id']}/pipeline")
    assert pipeline.status_code == 200
    assert any(
        item["candidate_version_id"] == candidate["id"]
        for item in pipeline.json()["latest_candidate_validations"]
    )


def test_act_phase_rejects_candidate_variant_after_negative_validation(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "reject_candidate_strategy",
            "name": "Reject Candidate Strategy",
            "description": "Candidate rejection test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["MDB", "DDOG"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "breakout",
                "thesis": "Seed failure",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.25,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    fork_act = client.post("/api/v1/orchestrator/act")
    assert fork_act.status_code == 200

    strategy_after_fork = next(
        item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
    )
    candidate = next(version for version in strategy_after_fork["versions"] if version["id"] != base_version_id)

    for ticker in ["SNAP", "PINS"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": candidate["id"],
                "signal_type": "candidate_breakout",
                "thesis": "Candidate validation loss",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.55,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": candidate["id"],
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
                "entry_context": {"execution_mode": "candidate_validation"},
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 97, "exit_reason": "failed_follow_through", "max_drawdown_pct": -4, "max_runup_pct": 1},
        )

    reject_act = client.post("/api/v1/orchestrator/act")
    assert reject_act.status_code == 200
    assert reject_act.json()["metrics"]["rejected_candidates"] >= 1

    rejected_strategy = next(
        item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
    )
    rejected_candidate = next(version for version in rejected_strategy["versions"] if version["id"] == candidate["id"])
    assert rejected_candidate["state"] == "rejected"
    assert rejected_candidate["lifecycle_stage"] == "archived"

    validation_summaries = client.get("/api/v1/strategy-evolution/candidate-validations")
    assert validation_summaries.status_code == 200
    rejected_summary = next(
        item for item in validation_summaries.json() if item["candidate_version_id"] == candidate["id"]
    )
    assert rejected_summary["evaluation_status"] == "reject"
    assert rejected_summary["trade_count"] == 2


def test_do_phase_prioritizes_degraded_candidate_versions(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "candidate_do_strategy",
            "name": "Candidate DO Strategy",
            "description": "DO prioritization test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    for ticker in ["MDB", "DDOG"]:
        signal = client.post(
            "/api/v1/signals",
            json={
                "ticker": ticker,
                "strategy_id": strategy["id"],
                "strategy_version_id": base_version_id,
                "signal_type": "breakout",
                "thesis": "Seed failure",
                "entry_zone": {"price": 100},
                "stop_zone": {"price": 95},
                "target_zone": {"price": 110},
                "signal_context": {"source": "test"},
                "quality_score": 0.25,
            },
        ).json()
        position = client.post(
            "/api/v1/positions",
            json={
                "ticker": ticker,
                "signal_id": signal["id"],
                "strategy_version_id": base_version_id,
                "entry_price": 100,
                "stop_price": 95,
                "target_price": 110,
                "size": 1,
            },
        ).json()
        client.post(
            f"/api/v1/positions/{position['id']}/close",
            json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
        )
        client.post(
            f"/api/v1/trade-reviews/positions/{position['id']}",
            json={
                "outcome_label": "loss",
                "outcome": "loss",
                "cause_category": "false_breakout",
                "failure_mode": "false_breakout",
                "observations": {"pnl_pct": -6.0},
                "root_cause": "Weak confirmation.",
                "root_causes": ["Weak confirmation."],
                "lesson_learned": "Need stronger confirmation.",
                "recommended_changes": ["Raise confirmation threshold."],
                "proposed_strategy_change": "Raise confirmation threshold.",
                "should_modify_strategy": False,
                "needs_strategy_update": False,
                "strategy_update_reason": "Repeated false breakout profile.",
            },
        )

    client.post("/api/v1/orchestrator/check")
    act = client.post("/api/v1/orchestrator/act")
    assert act.status_code == 200

    strategy_after_act = next(
        item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
    )
    assert strategy_after_act["status"] == "degraded"
    candidate_version = next(
        version for version in strategy_after_act["versions"] if version["lifecycle_stage"] == "candidate"
    )

    watchlist = client.post(
        "/api/v1/watchlists",
        json={
            "code": "candidate_do_watchlist",
            "name": "Candidate DO Watchlist",
            "strategy_id": strategy["id"],
            "hypothesis": "Validate candidate first.",
            "status": "active",
        },
    )
    assert watchlist.status_code == 201

    for ticker in ["NVDA", "AAPL"]:
        item = client.post(
            f"/api/v1/watchlists/{watchlist.json()['id']}/items",
            json={"ticker": ticker, "state": "watching"},
        )
        assert item.status_code == 201

    do_phase = client.post("/api/v1/orchestrator/do")
    assert do_phase.status_code == 200
    assert do_phase.json()["metrics"]["prioritized_candidate_items"] >= 2

    signals = client.get("/api/v1/signals").json()
    candidate_signals = [
        signal
        for signal in signals
        if signal["strategy_version_id"] == candidate_version["id"]
        and signal["ticker"] in {"NVDA", "AAPL"}
    ]
    assert len(candidate_signals) == 2
    assert all(signal["signal_context"]["execution_mode"] == "candidate_validation" for signal in candidate_signals)


def test_act_phase_opens_research_after_repeated_candidate_rejections(client: TestClient) -> None:
    strategy = client.post(
        "/api/v1/strategies",
        json={
            "code": "candidate_research_strategy",
            "name": "Candidate Research Strategy",
            "description": "Repeated candidate rejection test.",
            "horizon": "days_weeks",
            "bias": "long",
            "status": "paper",
            "initial_version": {
                "hypothesis": "Base hypothesis.",
                "general_rules": {},
                "parameters": {},
                "state": "approved",
                "is_baseline": True,
            },
        },
    ).json()
    base_version_id = strategy["current_version_id"]

    def seed_failure_and_fork(seed_tickers: list[str]) -> int:
        for ticker in seed_tickers:
            signal = client.post(
                "/api/v1/signals",
                json={
                    "ticker": ticker,
                    "strategy_id": strategy["id"],
                    "strategy_version_id": base_version_id,
                    "signal_type": "breakout",
                    "thesis": "Seed failure",
                    "entry_zone": {"price": 100},
                    "stop_zone": {"price": 95},
                    "target_zone": {"price": 110},
                    "signal_context": {"source": "test"},
                    "quality_score": 0.25,
                },
            ).json()
            position = client.post(
                "/api/v1/positions",
                json={
                    "ticker": ticker,
                    "signal_id": signal["id"],
                    "strategy_version_id": base_version_id,
                    "entry_price": 100,
                    "stop_price": 95,
                    "target_price": 110,
                    "size": 1,
                },
            ).json()
            client.post(
                f"/api/v1/positions/{position['id']}/close",
                json={"exit_price": 94, "exit_reason": "false_breakout", "max_drawdown_pct": -6, "max_runup_pct": 1},
            )
            client.post(
                f"/api/v1/trade-reviews/positions/{position['id']}",
                json={
                    "outcome_label": "loss",
                    "outcome": "loss",
                    "cause_category": "false_breakout",
                    "failure_mode": "false_breakout",
                    "observations": {"pnl_pct": -6.0},
                    "root_cause": "Weak confirmation.",
                    "root_causes": ["Weak confirmation."],
                    "lesson_learned": "Need stronger confirmation.",
                    "recommended_changes": ["Raise confirmation threshold."],
                    "proposed_strategy_change": "Raise confirmation threshold.",
                    "should_modify_strategy": False,
                    "needs_strategy_update": False,
                    "strategy_update_reason": "Repeated false breakout profile.",
                },
            )

        client.post("/api/v1/orchestrator/check")
        fork_act = client.post("/api/v1/orchestrator/act")
        assert fork_act.status_code == 200

        strategy_state = next(
            item for item in client.get("/api/v1/strategies").json() if item["id"] == strategy["id"]
        )
        candidate = max(
            (version for version in strategy_state["versions"] if version["id"] != base_version_id),
            key=lambda version: version["version"],
        )
        return candidate["id"]

    def reject_candidate(candidate_version_id: int, validation_tickers: list[str]) -> dict:
        for ticker in validation_tickers:
            signal = client.post(
                "/api/v1/signals",
                json={
                    "ticker": ticker,
                    "strategy_id": strategy["id"],
                    "strategy_version_id": candidate_version_id,
                    "signal_type": "candidate_breakout",
                    "thesis": "Candidate validation loss",
                    "entry_zone": {"price": 100},
                    "stop_zone": {"price": 95},
                    "target_zone": {"price": 110},
                    "signal_context": {"source": "test"},
                    "quality_score": 0.55,
                },
            ).json()
            position = client.post(
                "/api/v1/positions",
                json={
                    "ticker": ticker,
                    "signal_id": signal["id"],
                    "strategy_version_id": candidate_version_id,
                    "entry_price": 100,
                    "stop_price": 95,
                    "target_price": 110,
                    "size": 1,
                    "entry_context": {"execution_mode": "candidate_validation"},
                },
            ).json()
            client.post(
                f"/api/v1/positions/{position['id']}/close",
                json={"exit_price": 97, "exit_reason": "failed_follow_through", "max_drawdown_pct": -4, "max_runup_pct": 1},
            )

        reject_act = client.post("/api/v1/orchestrator/act")
        assert reject_act.status_code == 200
        return reject_act.json()

    first_candidate_id = seed_failure_and_fork(["MDB", "DDOG"])
    reject_candidate(first_candidate_id, ["SNAP", "PINS"])

    second_candidate_id = seed_failure_and_fork(["U", "SHOP"])
    reject_act = client.post("/api/v1/orchestrator/act")
    assert reject_act.status_code == 200
    reject_candidate(second_candidate_id, ["SQ", "ROKU"])

    final_act = client.post("/api/v1/orchestrator/act")
    assert final_act.status_code == 200

    research_tasks = client.get("/api/v1/research/tasks")
    assert research_tasks.status_code == 200
    candidate_research_task = next(
        task for task in research_tasks.json() if task["task_type"] == "candidate_recovery_research"
    )
    assert candidate_research_task["strategy_id"] == strategy["id"]
    assert candidate_research_task["scope"]["rejected_candidate_count"] >= 2
    assert first_candidate_id in candidate_research_task["scope"]["candidate_version_ids"]
    assert second_candidate_id in candidate_research_task["scope"]["candidate_version_ids"]
