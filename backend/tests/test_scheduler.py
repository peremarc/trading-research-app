from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domains.system.services import SchedulerService


def test_scheduler_status_exposes_pdca_jobs(client: TestClient) -> None:
    response = client.get("/api/v1/scheduler/status")

    assert response.status_code == 200

    payload = response.json()
    assert payload["enabled"] is False
    assert payload["running"] is False
    assert {job["job_id"] for job in payload["jobs"]} == {
        "pdca_plan_job",
        "pdca_do_job",
        "pdca_check_job",
        "pdca_act_job",
    }


def test_scheduler_interval_mode_exposes_single_cycle_job() -> None:
    scheduler = SchedulerService(
        Settings(
            scheduler_enabled=True,
            scheduler_mode="interval",
            scheduler_interval_minutes=5,
        )
    )

    scheduler.configure()

    try:
        jobs = scheduler.scheduler.get_jobs()
        assert {job.id for job in jobs} == {"pdca_cycle_job"}
    finally:
        scheduler.shutdown()
