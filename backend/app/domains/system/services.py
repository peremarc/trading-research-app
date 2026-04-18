from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.hypothesis import Hypothesis
from app.db.models.signal_definition import SignalDefinition
from app.db.models.setup import Setup
from app.db.models.strategy import Strategy
from app.db.models.watchlist import Watchlist
from app.db.session import SessionLocal
from app.domains.learning.schemas import DailyPlanRequest
from app.domains.learning.agent import AIDecisionError, AutonomousTradingAgentService
from app.domains.learning.services import OrchestratorService
from app.domains.execution.monitoring import IBKRRealtimePositionMonitorService
from app.domains.market.services import MarketDataUnavailableError
from app.domains.system.events import EventLogService
from app.domains.strategy.schemas import (
    HypothesisCreate,
    SignalDefinitionCreate,
    ScreenerCreate,
    ScreenerVersionCreate,
    SetupCreate,
    StrategyCreate,
    StrategyVersionCreate,
    WatchlistCreate,
    WatchlistItemCreate,
)
from app.domains.strategy.services import (
    HypothesisService,
    SignalDefinitionService,
    ScreenerService,
    SetupService,
    StrategyService,
    WatchlistService,
)


class SeedService:
    STARTUP_SEED_MODELS = (
        Strategy,
        Hypothesis,
        Setup,
        SignalDefinition,
        Watchlist,
    )

    def __init__(
        self,
        hypothesis_service: HypothesisService | None = None,
        signal_definition_service: SignalDefinitionService | None = None,
        setup_service: SetupService | None = None,
        strategy_service: StrategyService | None = None,
        screener_service: ScreenerService | None = None,
        watchlist_service: WatchlistService | None = None,
    ) -> None:
        self.hypothesis_service = hypothesis_service or HypothesisService()
        self.signal_definition_service = signal_definition_service or SignalDefinitionService()
        self.setup_service = setup_service or SetupService()
        self.strategy_service = strategy_service or StrategyService()
        self.screener_service = screener_service or ScreenerService()
        self.watchlist_service = watchlist_service or WatchlistService()

    def should_seed_on_startup(self, session: Session) -> bool:
        # Startup seeding should only hydrate a brand-new catalog.
        return all(session.query(model.id).first() is None for model in self.STARTUP_SEED_MODELS)

    def seed_initial_data(self, session: Session) -> dict:
        created = {
            "hypotheses": 0,
            "setups": 0,
            "signal_definitions": 0,
            "strategies": 0,
            "screeners": 0,
            "watchlists": 0,
            "watchlist_items": 0,
        }

        if session.query(Strategy).count() == 0:
            breakout_hypothesis = self.hypothesis_service.create_hypothesis(
                session,
                HypothesisCreate(
                    code="breakout_continuation",
                    name="Breakout Continuation",
                    description="Continuation hypothesis for liquid US equities making clean breakouts.",
                    proposition="Stocks above major moving averages with expanding volume tend to continue higher after clean breakouts.",
                    horizon="days_weeks",
                    bias="long",
                    success_criteria={
                        "min_win_rate_pct": 55,
                        "min_avg_pnl_pct": 1.5,
                        "max_avg_drawdown_pct": -4.0,
                    },
                    status="active",
                ),
            )
            created["hypotheses"] += 1

            pullback_hypothesis = self.hypothesis_service.create_hypothesis(
                session,
                HypothesisCreate(
                    code="trend_pullback_continuation",
                    name="Trend Pullback Continuation",
                    description="Trend-following pullback hypothesis for strong US equities.",
                    proposition="Pullbacks into rising trend support offer asymmetric continuation entries when the broader context stays constructive.",
                    horizon="days_weeks",
                    bias="long",
                    success_criteria={
                        "min_win_rate_pct": 55,
                        "min_avg_pnl_pct": 1.0,
                        "max_avg_drawdown_pct": -4.5,
                    },
                    status="active",
                ),
            )
            created["hypotheses"] += 1

            momentum_hypothesis = self.hypothesis_service.create_hypothesis(
                session,
                HypothesisCreate(
                    code="long_term_momentum_persistence",
                    name="Long Term Momentum Persistence",
                    description="Position-trading hypothesis for persistent multi-month leaders.",
                    proposition="Stocks with persistent 52-week strength and trend confirmation can outperform over longer holding periods.",
                    horizon="long_term",
                    bias="long",
                    success_criteria={
                        "min_win_rate_pct": 50,
                        "min_avg_pnl_pct": 3.0,
                        "max_avg_drawdown_pct": -8.0,
                    },
                    status="active",
                ),
            )
            created["hypotheses"] += 1

            breakout = self.strategy_service.create_strategy(
                session,
                StrategyCreate(
                    code="breakout_long",
                    name="Breakout Long",
                    description="Long setup for stocks breaking above recent consolidation with volume.",
                    hypothesis_id=breakout_hypothesis.id,
                    horizon="days_weeks",
                    bias="long",
                    status="paper",
                    initial_version=StrategyVersionCreate(
                        hypothesis=breakout_hypothesis.proposition,
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
                    hypothesis_id=pullback_hypothesis.id,
                    horizon="days_weeks",
                    bias="long",
                    status="paper",
                    initial_version=StrategyVersionCreate(
                        hypothesis=pullback_hypothesis.proposition,
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
                    hypothesis_id=momentum_hypothesis.id,
                    horizon="long_term",
                    bias="long",
                    status="research",
                    initial_version=StrategyVersionCreate(
                        hypothesis=momentum_hypothesis.proposition,
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

            breakout_setup = self.setup_service.create_setup(
                session,
                SetupCreate(
                    code="breakout_consolidation_20d",
                    name="20D Breakout After Consolidation",
                    description="Breakout through recent resistance after tight consolidation and volume expansion.",
                    hypothesis_id=breakout_hypothesis.id,
                    strategy_id=breakout.id,
                    ideal_context={
                        "trend": "uptrend",
                        "price_location": "near_20d_high",
                        "volume_profile": "expanding",
                    },
                    conditions={
                        "price_above_sma50": True,
                        "price_above_sma200": True,
                        "relative_volume_min": 1.5,
                    },
                    parameters={"breakout_window_days": 20},
                    status="active",
                ),
            )
            created["setups"] += 1

            pullback_setup = self.setup_service.create_setup(
                session,
                SetupCreate(
                    code="pullback_sma20_resume",
                    name="SMA20 Pullback Resume",
                    description="Ordered pullback toward rising short-term support inside an uptrend.",
                    hypothesis_id=pullback_hypothesis.id,
                    strategy_id=pullback.id,
                    ideal_context={
                        "trend": "uptrend",
                        "price_location": "near_sma20_or_sma50",
                        "momentum": "still_positive",
                    },
                    conditions={
                        "sma50_above_sma200": True,
                        "rsi_range": [50, 65],
                    },
                    parameters={"max_extension_at_entry_pct": 4},
                    status="active",
                ),
            )
            created["setups"] += 1

            momentum_setup = self.setup_service.create_setup(
                session,
                SetupCreate(
                    code="momentum_leader_weekly_hold",
                    name="Weekly Momentum Leader Hold",
                    description="Position-trading setup for strong leaders holding structural trend support.",
                    hypothesis_id=momentum_hypothesis.id,
                    strategy_id=momentum.id,
                    timeframe="1W",
                    ideal_context={
                        "trend": "persistent_uptrend",
                        "relative_strength": "positive",
                    },
                    conditions={
                        "price_above_sma200": True,
                        "monthly_performance_positive": True,
                    },
                    parameters={"rebalance_frequency": "weekly"},
                    status="active",
                ),
            )
            created["setups"] += 1

            self.signal_definition_service.create_signal_definition(
                session,
                SignalDefinitionCreate(
                    code="trend_context_filter",
                    name="Trend Context Filter",
                    description="Context filter to keep long setups aligned with the broader trend.",
                    hypothesis_id=breakout_hypothesis.id,
                    strategy_id=breakout.id,
                    setup_id=breakout_setup.id,
                    signal_kind="filter",
                    definition="Price above major moving averages with trend structure intact.",
                    parameters={"required_moving_averages": ["sma50", "sma200"]},
                    activation_conditions={"price_above_sma50": True, "price_above_sma200": True},
                    intended_usage="Use as a directional filter before evaluating breakout or pullback triggers.",
                    status="active",
                ),
            )
            created["signal_definitions"] += 1

            self.signal_definition_service.create_signal_definition(
                session,
                SignalDefinitionCreate(
                    code="breakout_trigger",
                    name="Breakout Trigger",
                    description="Trigger for expansion through recent resistance with confirming participation.",
                    hypothesis_id=breakout_hypothesis.id,
                    strategy_id=breakout.id,
                    setup_id=breakout_setup.id,
                    signal_kind="trigger",
                    definition="Break above recent resistance with expanding volume and constructive structure.",
                    parameters={"lookback_days": 20},
                    activation_conditions={"relative_volume_min": 1.5, "near_recent_high": True},
                    intended_usage="Primary trigger inside breakout-continuation setups.",
                    status="active",
                ),
            )
            created["signal_definitions"] += 1

            self.signal_definition_service.create_signal_definition(
                session,
                SignalDefinitionCreate(
                    code="pullback_resume_confirmation",
                    name="Pullback Resume Confirmation",
                    description="Confirmation signal for pullbacks that hold support and resume the prevailing trend.",
                    hypothesis_id=pullback_hypothesis.id,
                    strategy_id=pullback.id,
                    setup_id=pullback_setup.id,
                    signal_kind="confirmation",
                    definition="Orderly pullback into support with momentum stabilization before trend resumption.",
                    parameters={"support_reference": "sma20"},
                    activation_conditions={"rsi_range": [50, 65], "trend_intact": True},
                    intended_usage="Use as confirmation before entering trend pullback setups.",
                    status="active",
                ),
            )
            created["signal_definitions"] += 1

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
                event_source="system_seed",
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
                event_source="system_seed",
            )
            created["screeners"] += 1

            breakout_watchlist = self.watchlist_service.create_watchlist(
                session,
                WatchlistCreate(
                    code="breakout_long_candidates",
                    name="Breakout Long Candidates",
                    hypothesis_id=breakout_hypothesis.id,
                    strategy_id=breakout.id,
                    setup_id=breakout_setup.id,
                    hypothesis="Breakout continuation candidates for paper trading.",
                ),
                event_source="system_seed",
            )
            created["watchlists"] += 1

            pullback_watchlist = self.watchlist_service.create_watchlist(
                session,
                WatchlistCreate(
                    code="pullback_long_candidates",
                    name="Pullback Long Candidates",
                    hypothesis_id=pullback_hypothesis.id,
                    strategy_id=pullback.id,
                    setup_id=pullback_setup.id,
                    hypothesis="Trend pullback candidates for paper trading.",
                ),
                event_source="system_seed",
            )
            created["watchlists"] += 1

            for watchlist_id, setup_id, tickers in [
                (breakout_watchlist.id, breakout_setup.id, ["NVDA", "MSFT", "META"]),
                (pullback_watchlist.id, pullback_setup.id, ["AAPL", "AMZN", "UBER"]),
            ]:
                for ticker in tickers:
                    self.watchlist_service.add_item(
                        session,
                        watchlist_id,
                        WatchlistItemCreate(
                            ticker=ticker,
                            setup_id=setup_id,
                            score=0.5,
                            reason="Seed candidate for MVP workflow.",
                            key_metrics={"source": "seed"},
                            state="watching",
                        ),
                        event_source="system_seed",
                    )
                    created["watchlist_items"] += 1

        elif session.query(Watchlist).count() == 0:
            created["watchlists"] = 0
        elif session.query(Hypothesis).count() == 0:
            created["hypotheses"] = 0
        elif session.query(Setup).count() == 0:
            created["setups"] = 0
        elif session.query(SignalDefinition).count() == 0:
            created["signal_definitions"] = 0

        return created


@dataclass
class BotIncident:
    incident_id: int
    source: str
    title: str
    detail: str
    status: str = "open"
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


@dataclass
class BotRuntimeState:
    status: str = "paused"
    current_phase: str | None = None
    pause_reason: str | None = "Bot paused until the user starts it."
    last_cycle_started_at: datetime | None = None
    last_cycle_completed_at: datetime | None = None
    last_successful_phase: str | None = None
    last_error: str | None = None
    cycle_runs: int = 0
    incidents: list[BotIncident] = field(default_factory=list)
    next_incident_id: int = 1
    cycle_in_progress: bool = False


class SchedulerService:
    def __init__(
        self,
        settings: Settings,
        orchestrator_service: OrchestratorService | None = None,
        trading_agent_service: AutonomousTradingAgentService | None = None,
        event_log_service: EventLogService | None = None,
        realtime_monitor_service: IBKRRealtimePositionMonitorService | None = None,
    ) -> None:
        self.settings = settings
        self.trading_agent_service = trading_agent_service or AutonomousTradingAgentService(settings)
        self.orchestrator_service = orchestrator_service or OrchestratorService(
            trading_agent_service=self.trading_agent_service
        )
        self.event_log_service = event_log_service or EventLogService()
        self.realtime_monitor_service = realtime_monitor_service or IBKRRealtimePositionMonitorService(settings)
        self.scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)
        self._configured = False
        self.runtime = BotRuntimeState()

    def configure(self) -> None:
        if self._configured:
            return

        if self.settings.scheduler_mode == "continuous":
            self.scheduler.add_job(
                self._run_autonomous_bot_job,
                "date",
                run_date=None,
                id="autonomous_bot_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                next_run_time=None,
            )
        else:
            self.scheduler.add_job(
                self._run_autonomous_bot_job,
                "interval",
                minutes=self.settings.scheduler_interval_minutes,
                id="autonomous_bot_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        self._configured = True

    def boot(self) -> None:
        self.configure()
        if not self.scheduler.running:
            self.scheduler.start()

    def shutdown(self) -> None:
        self.realtime_monitor_service.stop()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.runtime.cycle_in_progress = False

    def reset_runtime_state(self) -> None:
        self.runtime = BotRuntimeState()
        self.trading_agent_service.reset_runtime_state()

    def start_bot(self) -> dict:
        self.boot()
        self._resolve_open_incidents()
        self.runtime.status = "running"
        self.runtime.pause_reason = None
        self.runtime.last_error = None
        self.realtime_monitor_service.start()
        self._request_cycle_run()
        return self.get_status_payload()

    def pause_bot(self, reason: str = "Bot paused by user.") -> dict:
        self.runtime.status = "paused"
        self.runtime.pause_reason = reason
        self.runtime.cycle_in_progress = False
        self.realtime_monitor_service.stop()
        self._unschedule_next_cycle()
        return self.get_status_payload()

    def get_status_payload(self) -> dict:
        incidents = [
            {
                "incident_id": incident.incident_id,
                "source": incident.source,
                "title": incident.title,
                "detail": incident.detail,
                "status": incident.status,
                "detected_at": incident.detected_at.isoformat(),
                "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            }
            for incident in sorted(self.runtime.incidents, key=lambda item: item.incident_id, reverse=True)
        ]
        return {
            "enabled": True,
            "running": self.scheduler.running,
            "jobs": [
                {
                    "job_id": job.id,
                    "next_run_time": next_run_time.isoformat()
                    if (next_run_time := getattr(job, "next_run_time", None))
                    else None,
                }
                for job in self.scheduler.get_jobs()
            ],
            "bot": {
                "status": self.runtime.status,
                "current_phase": self.runtime.current_phase,
                "pause_reason": self.runtime.pause_reason,
                "requires_attention": any(incident["status"] == "open" for incident in incidents),
                "last_cycle_started_at": self.runtime.last_cycle_started_at.isoformat()
                if self.runtime.last_cycle_started_at
                else None,
                "last_cycle_completed_at": self.runtime.last_cycle_completed_at.isoformat()
                if self.runtime.last_cycle_completed_at
                else None,
                "last_successful_phase": self.runtime.last_successful_phase,
                "last_error": self.runtime.last_error,
                "cadence_mode": self.settings.scheduler_mode,
                "interval_minutes": self.settings.scheduler_interval_minutes,
                "continuous_idle_seconds": self.settings.scheduler_continuous_idle_seconds,
                "cycle_runs": self.runtime.cycle_runs,
                "incidents": incidents,
            },
            "ai": self.trading_agent_service.get_status_payload(),
            "monitor": self.realtime_monitor_service.get_status_payload(),
        }

    def run_automation_cycle_once(self) -> None:
        if self.runtime.status != "running" or self.runtime.cycle_in_progress:
            return

        self.runtime.cycle_in_progress = True
        self.runtime.last_cycle_started_at = datetime.now(timezone.utc)
        self.runtime.last_error = None
        self.runtime.current_phase = "starting"

        try:
            self._execute_automation_cycle()
            self.runtime.last_cycle_completed_at = datetime.now(timezone.utc)
            self.runtime.cycle_runs += 1
            self.runtime.current_phase = None
        except MarketDataUnavailableError as exc:
            self._register_incident(
                source="market_data",
                title="Market data API failure",
                detail=str(exc),
            )
        except AIDecisionError as exc:
            self._register_incident(
                source="ai_model",
                title="AI decision engine failure",
                detail=str(exc),
            )
        except Exception as exc:
            self._register_incident(
                source="system",
                title="Autonomous cycle failure",
                detail=str(exc),
            )
        finally:
            self.runtime.cycle_in_progress = False
            self._schedule_next_cycle()

    def _run_autonomous_bot_job(self) -> None:
        self.run_automation_cycle_once()

    def _schedule_next_cycle(self) -> None:
        if self.settings.scheduler_mode != "continuous":
            return
        if self.runtime.status != "running":
            self._unschedule_next_cycle()
            return

        idle_seconds = max(int(self.settings.scheduler_continuous_idle_seconds), 0)
        next_run_time = datetime.now(timezone.utc) + timedelta(seconds=idle_seconds)
        self.scheduler.add_job(
            self._run_autonomous_bot_job,
            "date",
            run_date=next_run_time,
            id="autonomous_bot_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def _unschedule_next_cycle(self) -> None:
        try:
            self.scheduler.remove_job("autonomous_bot_job")
        except Exception:
            pass
        try:
            self.scheduler.remove_job("autonomous_bot_job_bootstrap")
        except Exception:
            return

    def _request_cycle_run(self) -> None:
        run_date = datetime.now(timezone.utc)
        if self.settings.scheduler_mode == "continuous":
            self.scheduler.add_job(
                self._run_autonomous_bot_job,
                "date",
                run_date=run_date,
                id="autonomous_bot_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            return
        self.scheduler.add_job(
            self._run_autonomous_bot_job,
            "date",
            run_date=run_date,
            id="autonomous_bot_job_bootstrap",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def _execute_automation_cycle(self) -> None:
        autonomous_orchestrator = OrchestratorService(
            halt_on_market_data_failure=True,
            trading_agent_service=self.trading_agent_service,
        )
        with SessionLocal() as session:
            dispatch_result = self.event_log_service.dispatch_pending(
                session,
                orchestrator_service=autonomous_orchestrator,
                cycle_date=date.today(),
                on_phase_start=self._mark_phase_started,
            )
            if dispatch_result["phases_run"]:
                self.runtime.last_successful_phase = dispatch_result["phases_run"][-1]
                return
            self.runtime.current_phase = "plan"
            autonomous_orchestrator.plan_daily_cycle(
                session,
                DailyPlanRequest(cycle_date=date.today(), market_context={"trigger": "autonomous_bot"}),
            )
            self.runtime.last_successful_phase = "plan"
            self.runtime.current_phase = "do"
            autonomous_orchestrator.run_do_phase(session)
            self.runtime.last_successful_phase = "do"
            self.runtime.current_phase = "check"
            autonomous_orchestrator.run_check_phase(session)
            self.runtime.last_successful_phase = "check"
            self.runtime.current_phase = "act"
            autonomous_orchestrator.run_act_phase(session)
            self.runtime.last_successful_phase = "act"

    def _mark_phase_started(self, phase: str) -> None:
        self.runtime.current_phase = phase

    def _register_incident(self, *, source: str, title: str, detail: str) -> None:
        incident = BotIncident(
            incident_id=self.runtime.next_incident_id,
            source=source,
            title=title,
            detail=detail,
        )
        self.runtime.next_incident_id += 1
        self.runtime.incidents.append(incident)
        self.runtime.status = "paused"
        self.runtime.pause_reason = f"Paused after incident: {title}"
        self.runtime.last_error = detail
        self.runtime.current_phase = None
        self.realtime_monitor_service.stop()

    def _resolve_open_incidents(self) -> None:
        resolved_at = datetime.now(timezone.utc)
        for incident in self.runtime.incidents:
            if incident.status == "open":
                incident.status = "resolved"
                incident.resolved_at = resolved_at
