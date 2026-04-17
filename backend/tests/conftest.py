from collections.abc import AsyncGenerator, Generator
import asyncio
import os

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["BOOTSTRAP_SEED_ON_STARTUP"] = "false"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["SCHEDULER_RUN_ON_STARTUP"] = "false"
os.environ["SCHEDULER_MODE"] = "cron"
os.environ["MARKET_DATA_PROVIDER"] = "stub"
os.environ["TWELVE_DATA_API_KEY"] = ""

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db_session
from app.domains.execution.services import ExitManagementService, PositionService, TradeReviewService
from app.domains.learning.services import (
    AutoReviewService,
    FailureAnalysisService,
    JournalService,
    MemoryService,
    OrchestratorService,
    PDCACycleService,
)
from app.domains.market.services import AnalysisService, MarketDataService, ResearchService, SignalService, WorkQueueService
from app.domains.strategy.services import (
    ScreenerService,
    StrategyEvolutionService,
    StrategyLabService,
    StrategyScoringService,
    StrategyService,
    WatchlistService,
)
from app.domains.system.runtime import scheduler_service
from app.domains.system.services import SeedService
from app.main import app


class SyncASGITestClient:
    def __init__(self, app) -> None:
        self.app = app
        self.base_url = "http://testserver"

    async def _request_async(self, method: str, path: str, **kwargs) -> httpx.Response:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url=self.base_url) as client:
            return await client.request(method, path, **kwargs)

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return asyncio.run(self._request_async(method, path, **kwargs))

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self.request("POST", path, **kwargs)


def _reset_api_service_singletons() -> None:
    from app.domains.learning import api as learning_api
    from app.domains.market import api as market_api
    from app.domains.strategy import api as strategy_api
    from app.domains.strategy import evolution as strategy_evolution_api
    from app.domains.execution import api as execution_api
    from app.domains.system import api as system_api

    learning_api.journal_service = JournalService()
    learning_api.memory_service = MemoryService()
    learning_api.failure_analysis_service = FailureAnalysisService()
    learning_api.auto_review_service = AutoReviewService()
    learning_api.pdca_service = PDCACycleService()
    learning_api.orchestrator_service = OrchestratorService()

    market_api.analysis_service = AnalysisService()
    market_api.market_data_service = MarketDataService()
    market_api.signal_service = SignalService()
    market_api.research_service = ResearchService()
    market_api.work_queue_service = WorkQueueService()

    strategy_api.strategy_service = StrategyService()
    strategy_api.screener_service = ScreenerService()
    strategy_api.watchlist_service = WatchlistService()

    strategy_evolution_api.strategy_scoring_service = StrategyScoringService()
    strategy_evolution_api.strategy_evolution_service = StrategyEvolutionService()
    strategy_evolution_api.strategy_lab_service = StrategyLabService()

    execution_api.position_service = PositionService()
    execution_api.exit_management_service = ExitManagementService()
    execution_api.trade_review_service = TradeReviewService()

    system_api.seed_service = SeedService()


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    scheduler_service.shutdown()
    scheduler_service._configured = False
    scheduler_service.scheduler.remove_all_jobs()
    _reset_api_service_singletons()

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
def client(session: Session) -> Generator[SyncASGITestClient, None, None]:
    async def override_get_db_session() -> AsyncGenerator[Session, None]:
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        yield SyncASGITestClient(app)
    finally:
        app.dependency_overrides.clear()
