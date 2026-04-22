from datetime import datetime, timedelta, timezone

from app.db.models.chat_conversation import ChatConversation
from app.db.models.chat_message import ChatMessage
from app.db.models.journal import JournalEntry
from app.db.models.learning_workflow import LearningWorkflowRun
from app.core.config import Settings
from app.domains.learning.workflows import LearningWorkflowService, LearningWorkflowSyncReport
from app.domains.market.services import MarketDataUnavailableError
from app.domains.system import services as system_services
from app.domains.system.services import SchedulerService


class _FakeMonitorService:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.active = False

    def start(self) -> dict:
        self.started += 1
        self.active = True
        return self.get_status_payload()

    def stop(self) -> dict:
        self.stopped += 1
        self.active = False
        return self.get_status_payload()

    def get_status_payload(self) -> dict:
        return {
            "enabled": True,
            "active": self.active,
            "transport": "sse",
            "subscribed_tickers": [],
            "subscribed_conids": [],
            "last_connected_at": None,
            "last_event_at": None,
            "processed_events": 0,
            "adjusted_positions": 0,
            "closed_positions": 0,
            "reconnect_count": 0,
            "last_error": None,
            "last_event_summary": None,
        }


class _ClosedMarketHoursService:
    class _Session:
        is_regular_session_open = False
        session_label = "weekend"

    def get_session_state(self):
        return self._Session()


class _ReadyMarketDataService:
    def get_market_overview(self, ticker: str, sec_type: str = "STK") -> dict:
        return {
            "available": True,
            "symbol": ticker,
            "sec_type": sec_type,
            "provider_source": "ibkr_proxy_market_overview",
            "market_signals": {"last_price": 710.14},
            "provider_error": None,
        }

    class _Snapshot:
        price = 710.14

    def get_snapshot(self, ticker: str):
        return self._Snapshot()


class _AuthRequiredMarketDataService:
    def get_market_overview(self, ticker: str, sec_type: str = "STK") -> dict:
        return {
            "available": True,
            "symbol": ticker,
            "sec_type": sec_type,
            "provider_source": "composed_fallback",
            "market_signals": {"last_price": 707.01},
            "provider_error": "IBKR proxy request failed for /market-overview/SPY: HTTP 401 Unauthorized. Interactive login required at https://dev-ibkr.example/login",
        }


def test_scheduler_status_exposes_autonomous_job_and_paused_bot(client) -> None:
    response = client.get("/api/v1/scheduler/status")

    assert response.status_code == 200

    payload = response.json()
    assert payload["running"] is False
    assert {job["job_id"] for job in payload["jobs"]} == {
        "autonomous_bot_job",
        "learning_workflow_governance_job",
    }
    assert payload["bot"]["status"] == "paused"
    assert payload["bot"]["cadence_mode"] == "continuous"
    assert payload["bot"]["continuous_idle_seconds"] == 5
    assert payload["bot"]["current_phase"] is None
    assert payload["bot"]["requires_attention"] is False
    assert payload["ai"]["enabled"] is False
    assert payload["ai"]["provider"] == "gemini"
    assert payload["ai"]["fallback_provider"] == "openai_compatible"
    assert payload["ai"]["calls_last_hour"] == 0
    assert payload["ai"]["calls_today"] == 0
    assert payload["learning_governance"]["enabled"] is True
    assert payload["learning_governance"]["status"] == "idle"
    assert payload["learning_governance"]["sync_runs"] == 0
    assert payload["market_data"]["provider"] == "stub"
    assert payload["market_data"]["probe_ticker"] == "SPY"
    assert payload["monitor"]["enabled"] is False


def test_scheduler_status_reports_persisted_llm_call_counts(client, session, monkeypatch) -> None:
    frozen_now = datetime(2026, 4, 19, 15, 0, tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return frozen_now.replace(tzinfo=None)
            return frozen_now.astimezone(tz)

    monkeypatch.setattr(system_services, "datetime", _FrozenDateTime)

    session.add_all(
        [
            ChatConversation(
                id=1,
                title="Chat usage test",
                topic="general",
                status="active",
                labels=[],
                preferred_llm="gemini-2.5-flash",
            ),
            JournalEntry(
                entry_type="ai_trade_decision",
                event_time=frozen_now - timedelta(minutes=20),
                market_context={},
                observations={},
            ),
            JournalEntry(
                entry_type="ai_position_management",
                event_time=frozen_now - timedelta(hours=3),
                market_context={},
                observations={},
            ),
            JournalEntry(
                entry_type="macro_signal",
                event_time=frozen_now - timedelta(minutes=10),
                market_context={},
                observations={"evidence": {"analysis_mode": "ai"}},
            ),
            JournalEntry(
                entry_type="macro_signal",
                event_time=frozen_now - timedelta(minutes=5),
                market_context={},
                observations={"evidence": {"analysis_mode": "heuristic_fallback"}},
            ),
            JournalEntry(
                entry_type="ai_trade_decision",
                event_time=frozen_now - timedelta(days=1),
                market_context={},
                observations={},
            ),
            ChatMessage(
                conversation_id=1,
                role="assistant",
                content="LLM chat reply",
                message_type="chat",
                context={"used_provider": "gemini", "used_model": "gemini-2.5-flash"},
                actions_taken=[],
                created_at=frozen_now - timedelta(minutes=15),
            ),
            ChatMessage(
                conversation_id=1,
                role="assistant",
                content="Local fallback reply",
                message_type="chat",
                context={"used_provider": "local_rules", "used_model": "deterministic_draft"},
                actions_taken=[],
                created_at=frozen_now - timedelta(minutes=5),
            ),
        ]
    )
    session.commit()

    response = client.get("/api/v1/scheduler/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ai"]["calls_last_hour"] == 3
    assert payload["ai"]["calls_today"] == 4


def test_scheduler_start_and_pause_toggle_bot_status(client) -> None:
    fake_monitor = _FakeMonitorService()
    scheduler = SchedulerService(
        Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1),
        realtime_monitor_service=fake_monitor,
    )
    scheduler._execute_automation_cycle = lambda: None  # type: ignore[method-assign]

    started = scheduler.start_bot()
    assert started["running"] is True
    assert started["bot"]["status"] == "running"
    assert started["bot"]["cycle_runs"] == 0
    assert started["ai"]["enabled"] is False
    assert started["monitor"]["active"] is True
    assert fake_monitor.started == 1

    scheduler.run_automation_cycle_once()
    payload_after_cycle = scheduler.get_status_payload()
    assert payload_after_cycle["bot"]["cycle_runs"] == 1

    paused = scheduler.pause_bot()
    assert paused["bot"]["status"] == "paused"
    assert paused["bot"]["pause_reason"] == "Bot paused by user."
    assert paused["monitor"]["active"] is False
    assert fake_monitor.stopped >= 1
    scheduler.shutdown()


def test_scheduler_status_reports_market_data_ready_when_probe_is_live() -> None:
    scheduler = SchedulerService(
        Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1),
        market_data_service=_ReadyMarketDataService(),
    )

    payload = scheduler.get_status_payload()

    assert payload["market_data"]["status"] == "ready"
    assert payload["market_data"]["ready"] is True
    assert payload["market_data"]["using_fallback"] is False
    assert payload["market_data"]["source"] == "ibkr_proxy_market_overview"
    assert payload["market_data"]["last_price"] == 710.14


def test_scheduler_status_reports_market_data_auth_required_when_ibkr_login_expires() -> None:
    scheduler = SchedulerService(
        Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1),
        market_data_service=_AuthRequiredMarketDataService(),
    )

    payload = scheduler.get_status_payload()

    assert payload["market_data"]["status"] == "auth_required"
    assert payload["market_data"]["ready"] is False
    assert payload["market_data"]["using_fallback"] is True
    assert "Interactive login required" in (payload["market_data"]["provider_error"] or "")


def test_scheduler_registers_incident_and_pauses_on_provider_failure() -> None:
    scheduler = SchedulerService(Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1))
    scheduler._execute_automation_cycle = lambda: None  # type: ignore[method-assign]
    scheduler.start_bot()

    def raise_provider_failure() -> None:
        raise MarketDataUnavailableError("Twelve Data timeout")

    scheduler._execute_automation_cycle = raise_provider_failure  # type: ignore[method-assign]
    scheduler.run_automation_cycle_once()

    payload = scheduler.get_status_payload()
    assert payload["bot"]["status"] == "paused"
    assert payload["bot"]["requires_attention"] is True
    assert payload["bot"]["incidents"][0]["source"] == "market_data"
    assert "Twelve Data timeout" in payload["bot"]["last_error"]
    scheduler.shutdown()


def test_scheduler_keeps_running_on_transient_market_data_cooldown() -> None:
    fake_monitor = _FakeMonitorService()
    scheduler = SchedulerService(
        Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1),
        realtime_monitor_service=fake_monitor,
    )

    def raise_transient_failure() -> None:
        raise MarketDataUnavailableError(
            "Market data provider 'ibkr_proxy' failed while loading snapshot for TOVX: "
            "IBKR proxy request failed for /contracts/search: HTTP 503 {'message': 'IBKR upstream is cooling down "
            "after recent failures', 'gateway_url': 'https://ibkr-gateway:5000', 'policy': 'reference', "
            "'retry_after_seconds': 2.612}"
        )

    scheduler._execute_automation_cycle = raise_transient_failure  # type: ignore[method-assign]
    scheduler.start_bot()

    scheduler.run_automation_cycle_once()

    payload = scheduler.get_status_payload()
    assert payload["bot"]["status"] == "running"
    assert payload["bot"]["requires_attention"] is False
    assert "cooling down" in (payload["bot"]["last_error"] or "")
    assert {job["job_id"] for job in payload["jobs"]} == {
        "autonomous_bot_job",
        "learning_workflow_governance_job",
    }
    assert fake_monitor.active is True
    scheduler.shutdown()


def test_scheduler_keeps_running_when_ibkr_proxy_reports_no_bridge() -> None:
    fake_monitor = _FakeMonitorService()
    scheduler = SchedulerService(
        Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1),
        realtime_monitor_service=fake_monitor,
    )

    def raise_transient_failure() -> None:
        raise MarketDataUnavailableError(
            "Market data provider 'ibkr_proxy' failed while loading snapshot for SPY: "
            "IBKR proxy request failed for /contracts/search: HTTP 400 Bad Request "
            "{'error':'Bad Request: no bridge','statusCode':400}"
        )

    scheduler._execute_automation_cycle = raise_transient_failure  # type: ignore[method-assign]
    scheduler.start_bot()

    scheduler.run_automation_cycle_once()

    payload = scheduler.get_status_payload()
    assert payload["bot"]["status"] == "running"
    assert payload["bot"]["requires_attention"] is False
    assert "no bridge" in (payload["bot"]["last_error"] or "")
    assert fake_monitor.active is True
    scheduler.shutdown()


def test_continuous_scheduler_reschedules_next_cycle_after_completion() -> None:
    scheduler = SchedulerService(Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1))
    scheduler._execute_automation_cycle = lambda: None  # type: ignore[method-assign]

    scheduler.start_bot()
    scheduler.run_automation_cycle_once()

    payload = scheduler.get_status_payload()
    assert payload["bot"]["status"] == "running"
    jobs = {job["job_id"]: job for job in payload["jobs"]}
    assert set(jobs) == {"autonomous_bot_job", "learning_workflow_governance_job"}
    assert jobs["autonomous_bot_job"]["next_run_time"] is not None
    scheduler.shutdown()


def test_scheduler_uses_slower_idle_when_market_is_closed() -> None:
    scheduler = SchedulerService(
        Settings(
            scheduler_mode="continuous",
            scheduler_continuous_idle_seconds=5,
            scheduler_market_closed_idle_seconds=1800,
        )
    )
    scheduler.market_hours_service = _ClosedMarketHoursService()

    assert scheduler._next_idle_seconds() == 1800


def test_scheduler_periodic_cycle_runs_only_do_when_no_event_phases_are_pending(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def plan_daily_cycle(self, session, payload) -> None:
            calls.append("plan")

        def run_do_phase(self, session) -> None:
            calls.append("do")

        def run_check_phase(self, session) -> None:
            calls.append("check")

        def run_act_phase(self, session) -> None:
            calls.append("act")

    class _FakeEventLogService:
        def __init__(self) -> None:
            self.calls = 0

        def dispatch_pending(self, session, *, orchestrator_service, cycle_date, on_phase_start=None):
            self.calls += 1
            return {
                "pending_events_seen": 0,
                "processed_events": 0,
                "ignored_events": 0,
                "failed_events": 0,
                "phases_run": [],
                "processed_event_ids": [],
                "ignored_event_ids": [],
            }

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(system_services, "OrchestratorService", _FakeOrchestrator)
    monkeypatch.setattr(system_services, "SessionLocal", lambda: _SessionContext())

    scheduler = SchedulerService(Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1))
    scheduler.event_log_service = _FakeEventLogService()

    scheduler._execute_automation_cycle()

    assert calls == ["do"]
    assert scheduler.runtime.last_successful_phase == "do"
    assert scheduler.event_log_service.calls == 2


def test_scheduler_dispatches_follow_up_event_phases_after_periodic_do(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_do_phase(self, session) -> None:
            calls.append("do")

    class _FakeEventLogService:
        def __init__(self) -> None:
            self.calls = 0

        def dispatch_pending(self, session, *, orchestrator_service, cycle_date, on_phase_start=None):
            self.calls += 1
            if self.calls == 1:
                return {
                    "pending_events_seen": 0,
                    "processed_events": 0,
                    "ignored_events": 0,
                    "failed_events": 0,
                    "phases_run": [],
                    "processed_event_ids": [],
                    "ignored_event_ids": [],
                }
            if on_phase_start is not None:
                on_phase_start("check")
                on_phase_start("act")
            return {
                "pending_events_seen": 1,
                "processed_events": 1,
                "ignored_events": 0,
                "failed_events": 0,
                "phases_run": ["check", "act"],
                "processed_event_ids": [1],
                "ignored_event_ids": [],
            }

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(system_services, "OrchestratorService", _FakeOrchestrator)
    monkeypatch.setattr(system_services, "SessionLocal", lambda: _SessionContext())

    scheduler = SchedulerService(Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1))
    scheduler.event_log_service = _FakeEventLogService()

    scheduler._execute_automation_cycle()

    assert calls == ["do"]
    assert scheduler.runtime.last_successful_phase == "act"
    assert scheduler.runtime.current_phase == "act"
    assert scheduler.event_log_service.calls == 2


def test_scheduler_reuses_persistent_orchestrator_between_cycles(monkeypatch) -> None:
    class _FakeOrchestrator:
        def __init__(self, *args, **kwargs) -> None:
            self.run_do_calls = 0

        def run_do_phase(self, session) -> None:
            self.run_do_calls += 1

    class _FakeEventLogService:
        def dispatch_pending(self, session, *, orchestrator_service, cycle_date, on_phase_start=None):
            del session, cycle_date, on_phase_start
            return {
                "pending_events_seen": 0,
                "processed_events": 0,
                "ignored_events": 0,
                "failed_events": 0,
                "phases_run": [],
                "processed_event_ids": [],
                "ignored_event_ids": [],
            }

    class _SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(system_services, "OrchestratorService", _FakeOrchestrator)
    monkeypatch.setattr(system_services, "SessionLocal", lambda: _SessionContext())

    scheduler = SchedulerService(Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1))
    scheduler.event_log_service = _FakeEventLogService()

    orchestrator = scheduler.orchestrator_service

    scheduler._execute_automation_cycle()
    scheduler._execute_automation_cycle()

    assert scheduler.orchestrator_service is orchestrator
    assert orchestrator.run_do_calls == 2


def test_scheduler_runs_learning_governance_lane_and_records_journal(monkeypatch, session) -> None:
    class _FakeWorkflowService:
        def __init__(self) -> None:
            self.calls = 0

        def sync_default_workflows_with_report(self, db_session):
            self.calls += 1
            assert db_session is session
            return LearningWorkflowSyncReport(
                workflows=[],
                workflow_count=2,
                open_workflow_count=1,
                open_item_count=3,
                changed_workflow_count=1,
                opened_workflow_count=1,
                resolved_workflow_count=0,
                changes=[
                    {
                        "workflow_type": "stale_claim_review",
                        "scope": "global",
                        "previous_status": "resolved",
                        "status": "open",
                        "open_item_count": 1,
                    }
                ],
                summary="Synced 2 learning workflows; 1 remain open with 3 open items; 1 workflows changed.",
            )

    class _SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(system_services, "SessionLocal", lambda: _SessionContext())
    workflow_service = _FakeWorkflowService()
    scheduler = SchedulerService(
        Settings(
            scheduler_mode="continuous",
            scheduler_continuous_idle_seconds=1,
            learning_workflow_governance_enabled=True,
            learning_workflow_governance_interval_minutes=15,
        ),
        learning_workflow_service=workflow_service,
    )

    scheduler._run_learning_governance_job()

    payload = scheduler.get_status_payload(session=session)
    assert workflow_service.calls == 1
    assert payload["learning_governance"]["status"] == "idle"
    assert payload["learning_governance"]["sync_runs"] == 1
    assert payload["learning_governance"]["last_changed_workflows"] == 1
    assert payload["learning_governance"]["last_open_workflows"] == 1
    assert payload["learning_governance"]["last_open_items"] == 3
    assert payload["bot"]["requires_attention"] is False

    journal_entry = session.query(JournalEntry).filter(JournalEntry.entry_type == "learning_workflow_sync").one()
    assert journal_entry.observations["changed_workflow_count"] == 1
    assert journal_entry.observations["open_item_count"] == 3


def test_scheduler_learning_governance_failure_does_not_pause_bot(monkeypatch, session) -> None:
    class _FailingWorkflowService:
        def sync_default_workflows_with_report(self, db_session):
            assert db_session is session
            raise RuntimeError("learning workflow sync failed")

    class _SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(system_services, "SessionLocal", lambda: _SessionContext())
    scheduler = SchedulerService(
        Settings(
            scheduler_mode="continuous",
            scheduler_continuous_idle_seconds=1,
            learning_workflow_governance_enabled=True,
            learning_workflow_governance_interval_minutes=15,
        ),
        learning_workflow_service=_FailingWorkflowService(),
    )

    scheduler._run_learning_governance_job()

    payload = scheduler.get_status_payload(session=session)
    assert payload["learning_governance"]["status"] == "error"
    assert "learning workflow sync failed" in (payload["learning_governance"]["last_error"] or "")
    assert payload["bot"]["requires_attention"] is False
    assert payload["bot"]["status"] == "paused"

    journal_entry = session.query(JournalEntry).filter(JournalEntry.entry_type == "learning_workflow_sync_failed").one()
    assert "learning workflow sync failed" in (journal_entry.outcome or "")


def test_scheduler_learning_governance_records_workflow_runs(monkeypatch, session) -> None:
    class _SessionContext:
        def __enter__(self):
            return session

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(system_services, "SessionLocal", lambda: _SessionContext())
    scheduler = SchedulerService(
        Settings(
            scheduler_mode="continuous",
            scheduler_continuous_idle_seconds=1,
            learning_workflow_governance_enabled=True,
            learning_workflow_governance_interval_minutes=15,
        ),
        learning_workflow_service=LearningWorkflowService(),
    )

    scheduler._run_learning_governance_job()

    runs = session.query(LearningWorkflowRun).all()
    assert len(runs) == 5
    assert {run.run_kind for run in runs} == {"sync"}
    assert {run.trigger_source for run in runs} == {"scheduler_governance"}
