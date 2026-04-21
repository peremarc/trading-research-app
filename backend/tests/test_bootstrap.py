from contextlib import nullcontext

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domains.system.services import SeedService
from app.main import maybe_seed_on_startup


def test_seed_bootstrap_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/bootstrap/seed")
    second = client.post("/api/v1/bootstrap/seed")

    assert first.status_code == 201
    assert first.json() == {
        "hypotheses": 3,
        "setups": 3,
        "signal_definitions": 7,
        "strategies": 3,
        "screeners": 2,
        "watchlists": 2,
        "watchlist_items": 6,
    }
    assert second.status_code == 201
    assert second.json() == {
        "hypotheses": 0,
        "setups": 0,
        "signal_definitions": 0,
        "strategies": 0,
        "screeners": 0,
        "watchlists": 0,
        "watchlist_items": 0,
    }


def test_startup_seed_only_runs_for_empty_catalog(session) -> None:
    service = SeedService()

    assert service.should_seed_on_startup(session) is True

    created = maybe_seed_on_startup(
        Settings(bootstrap_seed_on_startup=True),
        session_factory=lambda: nullcontext(session),
        seed_service=service,
    )

    assert created == {
        "hypotheses": 3,
        "setups": 3,
        "signal_definitions": 7,
        "strategies": 3,
        "screeners": 2,
        "watchlists": 2,
        "watchlist_items": 6,
    }
    assert service.should_seed_on_startup(session) is False
    assert maybe_seed_on_startup(
        Settings(bootstrap_seed_on_startup=True),
        session_factory=lambda: nullcontext(session),
        seed_service=service,
    ) is None
