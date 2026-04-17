from collections.abc import Generator
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["BOOTSTRAP_SEED_ON_STARTUP"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SCHEDULER_RUN_ON_STARTUP"] = "false"
os.environ["SCHEDULER_MODE"] = "cron"

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app
from app.services.runtime import scheduler_service


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    scheduler_service.shutdown()
    scheduler_service._configured = False
    scheduler_service.scheduler.remove_all_jobs()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def client(session: Session) -> Generator[TestClient, None, None]:
    def override_get_db_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
