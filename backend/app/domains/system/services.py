from __future__ import annotations

from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.strategy import Strategy
from app.db.models.watchlist import Watchlist
from app.db.session import SessionLocal
from app.domains.learning.schemas import DailyPlanRequest
from app.domains.learning.services import OrchestratorService
from app.domains.strategy.schemas import (
    ScreenerCreate,
    ScreenerVersionCreate,
    StrategyCreate,
    StrategyVersionCreate,
    WatchlistCreate,
    WatchlistItemCreate,
)
from app.domains.strategy.services import ScreenerService, StrategyService, WatchlistService


class SeedService:
    def __init__(
        self,
        strategy_service: StrategyService | None = None,
        screener_service: ScreenerService | None = None,
        watchlist_service: WatchlistService | None = None,
    ) -> None:
        self.strategy_service = strategy_service or StrategyService()
        self.screener_service = screener_service or ScreenerService()
        self.watchlist_service = watchlist_service or WatchlistService()

    def seed_initial_data(self, session: Session) -> dict:
        created = {
            "strategies": 0,
            "screeners": 0,
            "watchlists": 0,
            "watchlist_items": 0,
        }

        if session.query(Strategy).count() == 0:
            breakout = self.strategy_service.create_strategy(
                session,
                StrategyCreate(
                    code="breakout_long",
                    name="Breakout Long",
                    description="Long setup for stocks breaking above recent consolidation with volume.",
                    horizon="days_weeks",
                    bias="long",
                    status="paper",
                    initial_version=StrategyVersionCreate(
                        hypothesis="Stocks above major moving averages with expanding volume tend to continue higher after clean breakouts.",
                        general_rules={
                            "price_above_sma50": True,
                            "price_above_sma200": True,
                            "relative_volume_min": 1.5,
                        },
                        parameters={"max_risk_per_trade_r": 1.0},
                        state="approved",
                        is_baseline=True,
                    ),
                ),
            )
            created["strategies"] += 1

            pullback = self.strategy_service.create_strategy(
                session,
                StrategyCreate(
                    code="pullback_long",
                    name="Pullback Long",
                    description="Trend-following pullback entries in strong US equities.",
                    horizon="days_weeks",
                    bias="long",
                    status="paper",
                    initial_version=StrategyVersionCreate(
                        hypothesis="Pullbacks into rising trend support offer asymmetric continuation entries.",
                        general_rules={
                            "trend_filter": "price_above_sma50_and_sma200",
                            "rsi_range": [50, 65],
                        },
                        parameters={"max_extension_at_entry_pct": 4},
                        state="approved",
                        is_baseline=True,
                    ),
                ),
            )
            created["strategies"] += 1

            momentum = self.strategy_service.create_strategy(
                session,
                StrategyCreate(
                    code="long_term_momentum",
                    name="Long Term Momentum",
                    description="Position strategy for strong multi-month momentum leaders.",
                    horizon="long_term",
                    bias="long",
                    status="research",
                    initial_version=StrategyVersionCreate(
                        hypothesis="Stocks with persistent 52-week strength and trend confirmation can outperform over longer holding periods.",
                        general_rules={
                            "price_above_sma200": True,
                            "monthly_performance_positive": True,
                        },
                        parameters={"rebalance_frequency": "weekly"},
                        state="approved",
                        is_baseline=True,
                    ),
                ),
            )
            created["strategies"] += 1

            self.screener_service.create_screener(
                session,
                ScreenerCreate(
                    code="breakout_daily",
                    name="Breakout Daily Screener",
                    description="Daily breakout candidates for liquid US equities.",
                    strategy_id=breakout.id,
                    initial_version=ScreenerVersionCreate(
                        definition={
                            "filters": [
                                "price > sma50",
                                "price > sma200",
                                "relative_volume > 1.5",
                                "rsi_14 between 55 and 70",
                            ]
                        },
                        sorting={"field": "relative_volume", "direction": "desc"},
                        status="approved",
                    ),
                ),
            )
            created["screeners"] += 1

            self.screener_service.create_screener(
                session,
                ScreenerCreate(
                    code="pullback_daily",
                    name="Pullback Daily Screener",
                    description="Daily pullback candidates in uptrends.",
                    strategy_id=pullback.id,
                    initial_version=ScreenerVersionCreate(
                        definition={
                            "filters": [
                                "price > sma50",
                                "sma50 > sma200",
                                "rsi_14 between 50 and 65",
                            ]
                        },
                        sorting={"field": "month_performance", "direction": "desc"},
                        status="approved",
                    ),
                ),
            )
            created["screeners"] += 1

            breakout_watchlist = self.watchlist_service.create_watchlist(
                session,
                WatchlistCreate(
                    code="breakout_long_candidates",
                    name="Breakout Long Candidates",
                    strategy_id=breakout.id,
                    hypothesis="Breakout continuation candidates for paper trading.",
                ),
            )
            created["watchlists"] += 1

            pullback_watchlist = self.watchlist_service.create_watchlist(
                session,
                WatchlistCreate(
                    code="pullback_long_candidates",
                    name="Pullback Long Candidates",
                    strategy_id=pullback.id,
                    hypothesis="Trend pullback candidates for paper trading.",
                ),
            )
            created["watchlists"] += 1

            for watchlist_id, tickers in [
                (breakout_watchlist.id, ["NVDA", "MSFT", "META"]),
                (pullback_watchlist.id, ["AAPL", "AMZN", "UBER"]),
            ]:
                for ticker in tickers:
                    self.watchlist_service.add_item(
                        session,
                        watchlist_id,
                        WatchlistItemCreate(
                            ticker=ticker,
                            score=0.5,
                            reason="Seed candidate for MVP workflow.",
                            key_metrics={"source": "seed"},
                            state="watching",
                        ),
                    )
                    created["watchlist_items"] += 1

        elif session.query(Watchlist).count() == 0:
            created["watchlists"] = 0

        return created


class SchedulerService:
    def __init__(self, settings: Settings, orchestrator_service: OrchestratorService | None = None) -> None:
        self.settings = settings
        self.orchestrator_service = orchestrator_service or OrchestratorService()
        self.scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)
        self._configured = False

    def configure(self) -> None:
        if self._configured:
            return

        if self.settings.scheduler_mode == "interval":
            self.scheduler.add_job(
                self._run_full_cycle_job,
                "interval",
                minutes=self.settings.scheduler_interval_minutes,
                id="pdca_cycle_job",
                replace_existing=True,
            )
            self._configured = True
            return

        self.scheduler.add_job(
            self._run_plan_job,
            "cron",
            hour=self.settings.scheduler_plan_hour,
            minute=0,
            id="pdca_plan_job",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._run_do_job,
            "cron",
            hour=self.settings.scheduler_do_hour,
            minute=0,
            id="pdca_do_job",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._run_check_job,
            "cron",
            hour=self.settings.scheduler_check_hour,
            minute=0,
            id="pdca_check_job",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._run_act_job,
            "cron",
            hour=(self.settings.scheduler_check_hour + 1) % 24,
            minute=0,
            id="pdca_act_job",
            replace_existing=True,
        )
        self._configured = True

    def start(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        self.configure()
        if not self.scheduler.running:
            self.scheduler.start()
        if self.settings.scheduler_run_on_startup:
            self.run_cycle_once()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _run_plan_job(self) -> None:
        with SessionLocal() as session:
            self._run_plan(session)

    def _run_do_job(self) -> None:
        with SessionLocal() as session:
            self._run_do(session)

    def _run_check_job(self) -> None:
        with SessionLocal() as session:
            self._run_check(session)

    def _run_act_job(self) -> None:
        with SessionLocal() as session:
            self._run_act(session)

    def _run_full_cycle_job(self) -> None:
        self.run_cycle_once()

    def run_cycle_once(self) -> None:
        with SessionLocal() as session:
            self._run_plan(session)
            self._run_do(session)
            self._run_check(session)
            self._run_act(session)

    def _run_plan(self, session: Session) -> None:
        self.orchestrator_service.plan_daily_cycle(
            session,
            DailyPlanRequest(cycle_date=date.today(), market_context={"trigger": "scheduler"}),
        )

    def _run_do(self, session: Session) -> None:
        self.orchestrator_service.run_do_phase(session)

    def _run_check(self, session: Session) -> None:
        self.orchestrator_service.run_check_phase(session)

    def _run_act(self, session: Session) -> None:
        self.orchestrator_service.run_act_phase(session)
