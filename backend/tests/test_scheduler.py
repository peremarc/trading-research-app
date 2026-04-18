from app.core.config import Settings
from app.domains.market.services import MarketDataUnavailableError
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


def test_scheduler_status_exposes_autonomous_job_and_paused_bot(client) -> None:
    response = client.get("/api/v1/scheduler/status")

    assert response.status_code == 200

    payload = response.json()
    assert payload["running"] is False
    assert {job["job_id"] for job in payload["jobs"]} == {"autonomous_bot_job"}
    assert payload["bot"]["status"] == "paused"
    assert payload["bot"]["cadence_mode"] == "continuous"
    assert payload["bot"]["continuous_idle_seconds"] == 5
    assert payload["bot"]["current_phase"] is None
    assert payload["bot"]["requires_attention"] is False
    assert payload["ai"]["enabled"] is False
    assert payload["ai"]["provider"] == "gemini"
    assert payload["ai"]["fallback_provider"] == "openai_compatible"
    assert payload["monitor"]["enabled"] is False


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


def test_continuous_scheduler_reschedules_next_cycle_after_completion() -> None:
    scheduler = SchedulerService(Settings(scheduler_mode="continuous", scheduler_continuous_idle_seconds=1))
    scheduler._execute_automation_cycle = lambda: None  # type: ignore[method-assign]

    scheduler.start_bot()
    scheduler.run_automation_cycle_once()

    payload = scheduler.get_status_payload()
    assert payload["bot"]["status"] == "running"
    assert payload["jobs"][0]["job_id"] == "autonomous_bot_job"
    assert payload["jobs"][0]["next_run_time"] is not None
    scheduler.shutdown()
