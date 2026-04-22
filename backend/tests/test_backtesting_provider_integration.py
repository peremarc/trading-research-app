from __future__ import annotations

from app.core.config import Settings
from app.db.models.memory import MemoryItem
from app.db.models.research_task import ResearchTask
from app.db.models.strategy import Strategy, StrategyVersion
from app.domains.market.backtesting import ResearchBacktestService
from app.domains.system.services import SchedulerService


class _FakeBacktestingProvider:
    def __init__(self) -> None:
        self.base_url = "http://backtesting.test"
        self.submitted_specs: list[dict] = []

    def get_capabilities(self) -> dict:
        return {
            "service": "Backtesting Service",
            "engines": ["native_daily_ohlcv_replay"],
            "supported_data_sources": ["yahoo_chart"],
        }

    def get_ai_context(self) -> dict:
        return {
            "service": "Backtesting Service",
            "contract": {
                "spec_version": "backtest_spec.v1",
                "run_statuses": ["queued", "running", "completed", "failed", "cancelled"],
            },
        }

    def submit_backtest(self, spec: dict) -> dict:
        self.submitted_specs.append(spec)
        return {
            "run_id": "bt_demo_001",
            "status": "queued",
            "name": spec["name"],
            "engine": spec.get("strategy", {}).get("engine", "native_daily_ohlcv_replay"),
            "spec_version": spec.get("spec_version", "backtest_spec.v1"),
            "dataset_version": "pending",
            "requested_by": spec.get("source", {}).get("requested_by"),
            "source_app": spec.get("source", {}).get("source_app"),
            "cancel_requested": False,
            "submitted_at": "2026-04-22T21:30:00+00:00",
            "started_at": None,
            "completed_at": None,
            "error_code": None,
            "error_message": None,
            "result_summary": None,
        }

    def get_backtest_run(self, run_id: str) -> dict:
        assert run_id == "bt_demo_001"
        return {
            "run_id": run_id,
            "status": "completed",
            "name": "AAPL MA Validation",
            "engine": "native_daily_ohlcv_replay",
            "spec_version": "backtest_spec.v1",
            "dataset_version": "yahoo_chart:AAPL:2025-04-01:2026-04-01:1D",
            "requested_by": "research-bot",
            "source_app": "trading-research-app",
            "cancel_requested": False,
            "submitted_at": "2026-04-22T21:30:00+00:00",
            "started_at": "2026-04-22T21:30:02+00:00",
            "completed_at": "2026-04-22T21:30:05+00:00",
            "error_code": None,
            "error_message": None,
            "result_summary": {"trade_count": 7, "total_return_pct": 12.4},
        }

    def get_backtest_metrics(self, run_id: str) -> dict:
        assert run_id == "bt_demo_001"
        return {
            "run_id": run_id,
            "status": "completed",
            "dataset_version": "yahoo_chart:AAPL:2025-04-01:2026-04-01:1D",
            "split_summary": {
                "train_ratio": 0.7,
                "in_sample_end": "2025-12-20",
                "out_of_sample_start": "2025-12-21",
            },
            "items": [
                {
                    "scope": "overall",
                    "payload": {
                        "trade_count": 7,
                        "win_rate_pct": 57.14,
                        "profit_factor": 1.41,
                        "total_return_pct": 12.4,
                    },
                },
                {
                    "scope": "out_of_sample",
                    "payload": {
                        "trade_count": 3,
                        "win_rate_pct": 66.67,
                        "profit_factor": 1.88,
                        "total_return_pct": 5.1,
                    },
                },
            ],
        }

    def get_backtest_artifacts(self, run_id: str) -> dict:
        assert run_id == "bt_demo_001"
        return {
            "run_id": run_id,
            "status": "completed",
            "items": [
                {
                    "artifact_type": "dataset_snapshot",
                    "content_type": "application/json",
                    "file_path": "artifacts/bt_demo_001/dataset_snapshot.json",
                    "size_bytes": 512,
                    "download_url": "http://backtesting.test/api/v1/backtests/bt_demo_001/artifacts/dataset_snapshot",
                }
            ],
        }

    def cancel_backtest(self, run_id: str) -> dict:
        assert run_id == "bt_demo_001"
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "name": "AAPL MA Validation",
            "engine": "native_daily_ohlcv_replay",
            "spec_version": "backtest_spec.v1",
            "dataset_version": "pending",
            "requested_by": "research-bot",
            "source_app": "trading-research-app",
            "cancel_requested": True,
            "submitted_at": "2026-04-22T21:30:00+00:00",
            "started_at": None,
            "completed_at": None,
            "error_code": None,
            "error_message": None,
            "result_summary": None,
        }


def _create_strategy_context(session) -> tuple[Strategy, StrategyVersion, ResearchTask]:
    strategy = Strategy(
        code="ma_cross_alpha",
        name="MA Cross Alpha",
        description="Cross-based swing strategy",
        horizon="swing",
        bias="long",
        status="research",
    )
    session.add(strategy)
    session.flush()

    strategy_version = StrategyVersion(
        strategy_id=strategy.id,
        version=3,
        hypothesis="Bullish moving average crossover continuation",
        general_rules={},
        parameters={"fast_window": 20, "slow_window": 50},
        state="candidate",
        lifecycle_stage="candidate",
        is_baseline=False,
    )
    session.add(strategy_version)
    session.flush()

    strategy.current_version_id = strategy_version.id
    research_task = ResearchTask(
        strategy_id=strategy.id,
        task_type="hypothesis_validation",
        priority="high",
        status="open",
        title="Validate AAPL MA crossover hypothesis",
        hypothesis="AAPL performs better on daily moving average crossover entries.",
        scope={"ticker": "AAPL"},
    )
    session.add(research_task)
    session.commit()
    session.refresh(strategy)
    session.refresh(strategy_version)
    session.refresh(research_task)
    return strategy, strategy_version, research_task


def _create_skill_candidate(session, strategy_version_id: int) -> MemoryItem:
    candidate = MemoryItem(
        memory_type="skill_candidate",
        scope=f"strategy:{strategy_version_id}",
        key="skill_candidate:test:aapl_ma",
        content="Validate whether the MA crossover candidate survives historical replay.",
        meta={
            "summary": "Validate MA crossover candidate",
            "target_skill_code": "validate_ma_crossover_candidate",
            "candidate_action": "draft_candidate_skill",
            "candidate_status": "draft",
            "strategy_version_id": strategy_version_id,
            "ticker": "AAPL",
        },
        importance=0.76,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def test_create_research_backtest_enriches_remote_spec(client, session) -> None:
    strategy, strategy_version, research_task = _create_strategy_context(session)
    fake_provider = _FakeBacktestingProvider()
    client.app.state.market_backtest_service = ResearchBacktestService(
        provider=fake_provider,
        settings=Settings(backtesting_base_url="http://backtesting.test"),
    )

    response = client.post(
        "/api/v1/research/backtests",
        json={
            "requested_by": "research-bot",
            "reason": "Validate the current MA crossover hypothesis.",
            "strategy_version_id": strategy_version.id,
            "research_task_id": research_task.id,
            "spec": {
                "spec_version": "backtest_spec.v1",
                "name": "AAPL MA Validation",
                "universe": {"symbols": ["AAPL"]},
                "data": {
                    "source_type": "yahoo_chart",
                    "timeframe": "1D",
                    "start_date": "2025-04-01",
                    "end_date": "2026-04-01",
                },
                "strategy": {
                    "engine": "native_daily_ohlcv_replay",
                    "entry_rule": {"kind": "moving_average_cross", "fast_window": 20, "slow_window": 50},
                    "exit_rule": {"kind": "moving_average_crossunder"},
                },
            },
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["remote_run_id"] == "bt_demo_001"
    assert payload["strategy_id"] == strategy.id
    assert payload["strategy_version_id"] == strategy_version.id
    assert payload["research_task_id"] == research_task.id
    assert payload["status"] == "queued"
    assert payload["remote_urls"]["metrics"].endswith("/api/v1/backtests/bt_demo_001/metrics")

    submitted_spec = fake_provider.submitted_specs[0]
    assert submitted_spec["source"]["source_app"] == "trading-research-app"
    assert submitted_spec["source"]["requested_by"] == "research-bot"
    assert submitted_spec["source"]["reason"] == "Validate the current MA crossover hypothesis."
    assert submitted_spec["source"]["linked_entity_type"] == "research_task"
    assert submitted_spec["source"]["linked_entity_id"] == str(research_task.id)
    assert submitted_spec["target"]["type"] == "strategy_version"
    assert submitted_spec["target"]["code"] == strategy.code
    assert submitted_spec["target"]["version"] == str(strategy_version.version)
    assert submitted_spec["target"]["research_task_code"] == f"research_task:{research_task.id}"
    assert submitted_spec["metadata"]["trading_research_app"]["strategy_version_id"] == strategy_version.id


def test_create_research_backtest_links_skill_candidate_and_derives_strategy_version(client, session) -> None:
    strategy, strategy_version, _ = _create_strategy_context(session)
    skill_candidate = _create_skill_candidate(session, strategy_version.id)
    fake_provider = _FakeBacktestingProvider()
    client.app.state.market_backtest_service = ResearchBacktestService(
        provider=fake_provider,
        settings=Settings(
            backtesting_enabled=True,
            backtesting_base_url="http://backtesting.test",
        ),
    )

    response = client.post(
        "/api/v1/research/backtests",
        json={
            "requested_by": "research-bot",
            "skill_candidate_id": skill_candidate.id,
            "spec": {
                "spec_version": "backtest_spec.v1",
                "name": "Skill candidate replay",
                "universe": {"symbols": ["AAPL"]},
                "data": {
                    "source_type": "yahoo_chart",
                    "timeframe": "1D",
                    "start_date": "2025-04-01",
                    "end_date": "2026-04-01",
                },
                "strategy": {
                    "entry_rule": {"kind": "moving_average_cross", "fast_window": 20, "slow_window": 50},
                    "exit_rule": {"kind": "moving_average_crossunder"},
                },
            },
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["skill_candidate_id"] == skill_candidate.id
    assert payload["strategy_id"] == strategy.id
    assert payload["strategy_version_id"] == strategy_version.id
    assert payload["linked_entity_type"] == "skill_candidate"
    assert payload["linked_entity_id"] == str(skill_candidate.id)
    assert payload["target_type"] == "skill_candidate"
    assert payload["target_code"] == "validate_ma_crossover_candidate"

    submitted_spec = fake_provider.submitted_specs[0]
    assert submitted_spec["source"]["linked_entity_type"] == "skill_candidate"
    assert submitted_spec["source"]["linked_entity_id"] == str(skill_candidate.id)
    assert submitted_spec["target"]["type"] == "skill_candidate"
    assert submitted_spec["target"]["skill_candidate_code"] == "validate_ma_crossover_candidate"
    assert submitted_spec["metadata"]["trading_research_app"]["skill_candidate_id"] == skill_candidate.id


def test_sync_research_backtest_persists_remote_metrics_and_artifacts(client, session) -> None:
    _, strategy_version, research_task = _create_strategy_context(session)
    fake_provider = _FakeBacktestingProvider()
    client.app.state.market_backtest_service = ResearchBacktestService(
        provider=fake_provider,
        settings=Settings(backtesting_base_url="http://backtesting.test"),
    )

    create_response = client.post(
        "/api/v1/research/backtests",
        json={
            "requested_by": "research-bot",
            "strategy_version_id": strategy_version.id,
            "research_task_id": research_task.id,
            "spec": {
                "spec_version": "backtest_spec.v1",
                "name": "AAPL MA Validation",
                "universe": {"symbols": ["AAPL"]},
                "data": {
                    "source_type": "yahoo_chart",
                    "timeframe": "1D",
                    "start_date": "2025-04-01",
                    "end_date": "2026-04-01",
                },
                "strategy": {
                    "entry_rule": {"kind": "moving_average_cross", "fast_window": 20, "slow_window": 50},
                    "exit_rule": {"kind": "moving_average_crossunder"},
                },
            },
        },
    )
    backtest_id = create_response.json()["id"]

    sync_response = client.post(f"/api/v1/research/backtests/{backtest_id}/sync")

    assert sync_response.status_code == 200
    payload = sync_response.json()
    assert payload["status"] == "completed"
    assert payload["dataset_version"] == "yahoo_chart:AAPL:2025-04-01:2026-04-01:1D"
    assert payload["summary_metrics"]["scopes"]["overall"]["trade_count"] == 7
    assert payload["summary_metrics"]["scopes"]["out_of_sample"]["profit_factor"] == 1.88
    assert payload["artifact_refs"][0]["artifact_type"] == "dataset_snapshot"
    assert payload["completed_at"].startswith("2026-04-22T21:30:05")


def test_research_backtesting_provider_context_proxies_remote_discovery(client) -> None:
    fake_provider = _FakeBacktestingProvider()
    client.app.state.market_backtest_service = ResearchBacktestService(
        provider=fake_provider,
        settings=Settings(backtesting_enabled=True, backtesting_base_url="http://backtesting.test"),
    )

    response = client.get("/api/v1/research/backtests/provider/context")

    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["provider"] == "remote_service"
    assert payload["base_url"] == "http://backtesting.test"
    assert payload["capabilities"]["engines"] == ["native_daily_ohlcv_replay"]
    assert payload["ai_context"]["contract"]["spec_version"] == "backtest_spec.v1"


def test_sync_pending_endpoint_batches_non_terminal_runs(client, session) -> None:
    _, strategy_version, research_task = _create_strategy_context(session)
    fake_provider = _FakeBacktestingProvider()
    client.app.state.market_backtest_service = ResearchBacktestService(
        provider=fake_provider,
        settings=Settings(backtesting_enabled=True, backtesting_base_url="http://backtesting.test"),
    )

    create_response = client.post(
        "/api/v1/research/backtests",
        json={
            "requested_by": "research-bot",
            "strategy_version_id": strategy_version.id,
            "research_task_id": research_task.id,
            "spec": {
                "spec_version": "backtest_spec.v1",
                "name": "AAPL MA Validation",
                "universe": {"symbols": ["AAPL"]},
                "data": {
                    "source_type": "yahoo_chart",
                    "timeframe": "1D",
                    "start_date": "2025-04-01",
                    "end_date": "2026-04-01",
                },
                "strategy": {
                    "entry_rule": {"kind": "moving_average_cross", "fast_window": 20, "slow_window": 50},
                    "exit_rule": {"kind": "moving_average_crossunder"},
                },
            },
        },
    )
    assert create_response.status_code == 202

    sync_response = client.post("/api/v1/research/backtests/sync-pending")

    assert sync_response.status_code == 200
    payload = sync_response.json()
    assert payload["attempted"] == 1
    assert payload["updated"] == 1
    assert payload["terminal"] == 1
    assert payload["failed"] == 0
    assert payload["items"][0]["status"] == "completed"


def test_scheduler_runs_backtesting_reconciliation_job() -> None:
    class _FakeBacktestService:
        def __init__(self) -> None:
            self.calls = 0

        def sync_non_terminal_runs(self, session, *, limit=None, emit_events=False):
            self.calls += 1
            return type(
                "_Result",
                (),
                {
                    "attempted": 2,
                    "updated": 1,
                    "terminal": 1,
                    "failed": 0,
                    "errors": [],
                },
            )()

    fake_service = _FakeBacktestService()
    scheduler = SchedulerService(
        Settings(
            scheduler_mode="continuous",
            scheduler_continuous_idle_seconds=1,
            backtesting_enabled=True,
            backtesting_reconcile_enabled=True,
            backtesting_reconcile_interval_seconds=5,
            backtesting_reconcile_batch_size=3,
        ),
        research_backtest_service=fake_service,
    )

    scheduler.configure()
    payload = scheduler.get_status_payload()
    assert "backtesting_reconciliation_job" in {job["job_id"] for job in payload["jobs"]}
    assert payload["backtesting_reconciliation"]["enabled"] is True
    assert payload["backtesting_reconciliation"]["interval_seconds"] == 5
    assert payload["backtesting_reconciliation"]["batch_size"] == 3

    scheduler._run_backtesting_reconciliation_job()

    reconciler_payload = scheduler.get_status_payload()["backtesting_reconciliation"]
    assert fake_service.calls == 1
    assert reconciler_payload["status"] == "idle"
    assert reconciler_payload["sync_runs"] == 1
    assert reconciler_payload["last_attempted"] == 2
    assert reconciler_payload["last_updated"] == 1
    assert reconciler_payload["last_terminal"] == 1
    assert reconciler_payload["last_failed"] == 0
    scheduler.shutdown()
