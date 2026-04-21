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
os.environ["SCHEDULER_MODE"] = "continuous"
os.environ["SCHEDULER_CONTINUOUS_IDLE_SECONDS"] = "5"
os.environ["SCHEDULER_MARKET_CLOSED_IDLE_SECONDS"] = "1800"
os.environ["MARKET_DATA_PROVIDER"] = "stub"
os.environ["IBKR_PROXY_BASE_URL"] = ""
os.environ["ALPHA_VANTAGE_API_KEY"] = ""
os.environ["TWELVE_DATA_API_KEY"] = ""
os.environ["FINNHUB_API_KEY"] = ""
os.environ["FRED_API_KEY"] = ""
os.environ["MACRO_INDICATORS_ENABLED"] = "false"
os.environ["ORCHESTRATOR_SCAN_WHEN_MARKET_CLOSED"] = "true"
os.environ["OPPORTUNITY_DISCOVERY_RUN_WHEN_MARKET_CLOSED"] = "true"
os.environ["AI_AGENT_ENABLED"] = "false"
os.environ["AI_MARKET_CLOSED_ENABLED"] = "true"
os.environ["AI_PRIMARY_PROVIDER"] = "gemini"
os.environ["AI_PRIMARY_MODEL"] = "gemini-2.5-flash"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GEMINI_API_KEY_FREE1"] = ""
os.environ["GEMINI_API_KEY_FREE2"] = ""
os.environ["AI_FALLBACK_PROVIDER"] = "openai_compatible"
os.environ["AI_FALLBACK_MODEL"] = "qwen2.5:3b"
os.environ["AI_FALLBACK_API_KEY"] = ""
os.environ["AI_FALLBACK_API_BASE"] = ""

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.session import get_db_session
from app.domains.execution.services import ExitManagementService, PositionService, TradeReviewService
from app.domains.learning.macro import MacroContextService
from app.domains.learning.world_state import MarketStateService
from app.domains.learning.tools import AgentToolGatewayService
from app.domains.learning.services import (
    AutoReviewService,
    FailureAnalysisService,
    JournalService,
    MemoryService,
    OrchestratorService,
    PDCACycleService,
)
from app.domains.market.services import AnalysisService, CalendarService, MarketDataService, ResearchService, SignalService, WorkQueueService
from app.domains.market.services import NewsService
from app.domains.strategy.services import (
    HypothesisService,
    SignalDefinitionService,
    ScreenerService,
    SetupService,
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
    learning_api.ticker_trace_service = learning_api.TickerDecisionTraceService()
    learning_api.memory_service = MemoryService()
    learning_api.failure_analysis_service = FailureAnalysisService()
    learning_api.auto_review_service = AutoReviewService()
    learning_api.pdca_service = PDCACycleService()
    learning_api.orchestrator_service = OrchestratorService()
    learning_api.bot_chat_service = learning_api.BotChatService()
    learning_api.chat_conversation_service = learning_api.ChatConversationService()
    learning_api.macro_context_service = MacroContextService()
    learning_api.market_state_service = MarketStateService()
    learning_api.agent_tool_gateway_service = AgentToolGatewayService()
    learning_api.skill_catalog_service = learning_api.SkillCatalogService()
    learning_api.skill_lifecycle_service = learning_api.SkillLifecycleService(
        catalog_service=learning_api.skill_catalog_service
    )
    learning_api.knowledge_claim_service = learning_api.KnowledgeClaimService()
    learning_api.learning_workflow_service = learning_api.LearningWorkflowService()
    learning_api.operator_disagreement_service = learning_api.OperatorDisagreementService()

    market_api.analysis_service = AnalysisService()
    market_api.market_data_service = MarketDataService()
    market_api.news_service = NewsService()
    market_api.calendar_service = CalendarService()
    market_api.signal_service = SignalService()
    market_api.research_service = ResearchService()
    market_api.work_queue_service = WorkQueueService()

    strategy_api.hypothesis_service = HypothesisService()
    strategy_api.signal_definition_service = SignalDefinitionService()
    strategy_api.setup_service = SetupService()
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
    system_api.event_log_service = system_api.EventLogService()


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    scheduler_service.shutdown()
    scheduler_service._configured = False
    scheduler_service.scheduler.remove_all_jobs()
    scheduler_service.reset_runtime_state()
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
