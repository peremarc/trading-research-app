from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.signal import Signal
from app.db.models.strategy import Strategy
from app.domains.market.repositories import AnalysisRepository, ResearchTaskRepository, SignalRepository
from app.domains.market.schemas import AnalysisRunCreate, ResearchTaskCreate, WorkItemRead, WorkQueueRead, SignalCreate
from app.providers.market_data.base import MarketSnapshot, OHLCVCandle
from app.providers.market_data.stub_provider import StubMarketDataProvider
from app.providers.market_data.twelve_data_provider import TwelveDataError, TwelveDataProvider
from app.core.config import get_settings


class AnalysisService:
    def __init__(self, repository: AnalysisRepository | None = None) -> None:
        self.repository = repository or AnalysisRepository()

    def list_runs(self, session: Session):
        return self.repository.list(session)

    def create_run(self, session: Session, payload: AnalysisRunCreate):
        return self.repository.create(session, payload)


class MarketDataService:
    def __init__(self) -> None:
        settings = get_settings()
        self.fallback_provider = StubMarketDataProvider()
        self.provider = self.fallback_provider

        if settings.market_data_provider == "twelve_data" and settings.twelve_data_api_key:
            self.provider = TwelveDataProvider(settings.twelve_data_api_key)

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        try:
            return self.provider.get_snapshot(ticker)
        except TwelveDataError:
            return self.fallback_provider.get_snapshot(ticker)

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        try:
            return self.provider.get_history(ticker, limit=limit)
        except TwelveDataError:
            return self.fallback_provider.get_history(ticker, limit=limit)


class SignalService:
    def __init__(
        self,
        repository: SignalRepository | None = None,
        fused_analysis_service: object | None = None,
    ) -> None:
        self.repository = repository or SignalRepository()
        self.fused_analysis_service = fused_analysis_service

    def analyze_snapshot(self, snapshot: MarketSnapshot, benchmark_snapshot: MarketSnapshot | None = None) -> dict:
        trend_score = 0.0
        if snapshot.price > snapshot.sma_20:
            trend_score += 0.2
        if snapshot.price > snapshot.sma_50:
            trend_score += 0.25
        if snapshot.price > snapshot.sma_200:
            trend_score += 0.25
        if snapshot.sma_20 > snapshot.sma_50:
            trend_score += 0.1
        if snapshot.relative_volume >= 1.5:
            trend_score += 0.1
        if 55 <= snapshot.rsi_14 <= 70:
            trend_score += 0.1

        alpha_gap_pct = (
            round((snapshot.month_performance - benchmark_snapshot.month_performance) * 100, 2)
            if benchmark_snapshot is not None
            else round(snapshot.month_performance * 100, 2)
        )
        volatility_penalty = min((snapshot.atr_14 / max(snapshot.price, 1.0)) / 0.08, 1.0)
        alpha_bonus = min(max((alpha_gap_pct + 2.0) / 8.0, 0.0), 1.0) * 0.15
        drawdown_guard = (1.0 - volatility_penalty) * 0.1
        score = round(min(max(trend_score + alpha_bonus + drawdown_guard, 0.0), 1.0), 2)
        decision = "watch"
        if score >= 0.8:
            decision = "paper_enter"
        elif score < 0.6:
            decision = "discard"

        entry_price = snapshot.price
        stop_price = round(snapshot.price - (1.5 * snapshot.atr_14), 2)
        target_price = round(snapshot.price + (3.0 * snapshot.atr_14), 2)
        risk = max(entry_price - stop_price, 0.01)
        reward = max(target_price - entry_price, 0.01)

        return {
            "quant_summary": {
                "price": snapshot.price,
                "sma_20": snapshot.sma_20,
                "sma_50": snapshot.sma_50,
                "sma_200": snapshot.sma_200,
                "rsi_14": snapshot.rsi_14,
                "relative_volume": snapshot.relative_volume,
                "atr_14": snapshot.atr_14,
                "week_performance": snapshot.week_performance,
                "month_performance": snapshot.month_performance,
            },
            "combined_score": score,
            "decision": decision,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "risk_reward": round(reward / risk, 2),
            "decision_confidence": score,
            "alpha_gap_pct": alpha_gap_pct,
            "rationale": (
                "Risk-adjusted signal based on trend alignment, alpha vs benchmark and drawdown control. "
                f"Snapshot score={score}, alpha gap={alpha_gap_pct}%."
            ),
        }

    def analyze_ticker(self, ticker: str) -> dict:
        if self.fused_analysis_service is None:
            from app.domains.market.analysis import FusedAnalysisService

            self.fused_analysis_service = FusedAnalysisService()
        return self.fused_analysis_service.analyze_ticker(ticker)

    def list_signals(self, session: Session):
        return self.repository.list(session)

    def create_signal(self, session: Session, payload: SignalCreate):
        return self.repository.create(session, payload)

    def update_status(self, session: Session, signal_id: int, status: str, rejection_reason: str | None = None):
        return self.repository.update_status(session, signal_id, status=status, rejection_reason=rejection_reason)


class ResearchService:
    def __init__(self, repository: ResearchTaskRepository | None = None) -> None:
        self.repository = repository or ResearchTaskRepository()

    def list_tasks(self, session: Session):
        return self.repository.list(session)

    def create_task(self, session: Session, payload: ResearchTaskCreate):
        return self.repository.create(session, payload)

    def complete_task(self, session: Session, task_id: int, result_summary: str):
        return self.repository.complete(session, task_id, result_summary)

    def ensure_low_activity_task(
        self,
        session: Session,
        *,
        strategy_id: int,
        strategy_name: str,
        signals_count: int,
        closed_trades_count: int,
    ):
        title = f"Expand signal generation for {strategy_name}"
        existing = self.repository.find_open_by_signature(
            session,
            strategy_id=strategy_id,
            task_type="improve_signal_flow",
            title=title,
        )
        if existing is not None:
            return existing, False

        return self.repository.create(
            session,
            ResearchTaskCreate(
                strategy_id=strategy_id,
                task_type="improve_signal_flow",
                priority="high",
                title=title,
                hypothesis=(
                    "The strategy is not producing enough actionable flow. Investigate broader universes, "
                    "new filters or alternative entry definitions."
                ),
                scope={
                    "signals_count": signals_count,
                    "closed_trades_count": closed_trades_count,
                    "goal": "increase high-quality signal frequency without degrading expectancy",
                },
            ),
        ), True

    def ensure_recovery_task(
        self,
        session: Session,
        *,
        strategy_id: int,
        strategy_name: str,
        reason: str,
        failure_mode: str | None = None,
    ):
        title = f"Recover strategy health for {strategy_name}"
        existing = self.repository.find_open_by_signature(
            session,
            strategy_id=strategy_id,
            task_type="strategy_recovery",
            title=title,
        )
        if existing is not None:
            return existing, False

        return self.repository.create(
            session,
            ResearchTaskCreate(
                strategy_id=strategy_id,
                task_type="strategy_recovery",
                priority="high",
                title=title,
                hypothesis=(
                    "The strategy is showing repeated failure or poor fitness. Investigate corrective filters, "
                    "market regime constraints or whether the strategy should remain active."
                ),
                scope={
                    "reason": reason,
                    "failure_mode": failure_mode,
                    "goal": "decide whether to recover, fork or retire the strategy",
                },
            ),
        ), True

    def ensure_candidate_research_task(
        self,
        session: Session,
        *,
        strategy_id: int,
        strategy_name: str,
        rejected_candidate_count: int,
        candidate_version_ids: list[int],
    ):
        title = f"Reframe candidate recovery for {strategy_name}"
        existing = self.repository.find_open_by_signature(
            session,
            strategy_id=strategy_id,
            task_type="candidate_recovery_research",
            title=title,
        )
        if existing is not None:
            return existing, False

        return self.repository.create(
            session,
            ResearchTaskCreate(
                strategy_id=strategy_id,
                task_type="candidate_recovery_research",
                priority="high",
                title=title,
                hypothesis=(
                    "Recent recovery candidates were rejected repeatedly. Investigate whether the strategy needs "
                    "a broader redesign, a different market regime filter, or a new signal family."
                ),
                scope={
                    "rejected_candidate_count": rejected_candidate_count,
                    "candidate_version_ids": candidate_version_ids,
                    "goal": "identify why recovery variants keep failing and define a new recovery direction",
                },
            ),
        ), True

    def ensure_alpha_improvement_task(
        self,
        session: Session,
        *,
        strategy_id: int,
        strategy_name: str,
        avg_return_pct: float | None,
        benchmark_return_pct: float,
        max_drawdown_pct: float | None,
    ):
        title = f"Improve alpha efficiency for {strategy_name}"
        existing = self.repository.find_open_by_signature(
            session,
            strategy_id=strategy_id,
            task_type="alpha_improvement",
            title=title,
        )
        if existing is not None:
            return existing, False

        return self.repository.create(
            session,
            ResearchTaskCreate(
                strategy_id=strategy_id,
                task_type="alpha_improvement",
                priority="high",
                title=title,
                hypothesis=(
                    "The strategy is not outperforming the benchmark with enough margin relative to its drawdown. "
                    "Investigate stronger regime filters, better entries, and tighter risk controls."
                ),
                scope={
                    "avg_return_pct": avg_return_pct,
                    "benchmark_return_pct": benchmark_return_pct,
                    "max_drawdown_pct": max_drawdown_pct,
                    "goal": "increase alpha while reducing drawdown and preserving scalable opportunity flow",
                },
            ),
        ), True


class WorkQueueService:
    def __init__(self, failure_analysis_service: object | None = None) -> None:
        if failure_analysis_service is None:
            from app.domains.learning.services import FailureAnalysisService

            failure_analysis_service = FailureAnalysisService()
        self.failure_analysis_service = failure_analysis_service

    def get_queue(self, session: Session) -> WorkQueueRead:
        items: list[WorkItemRead] = []

        pending_reviews = list(
            session.scalars(
                select(Position).where(Position.status == "closed", Position.review_status == "pending")
            ).all()
        )
        items.extend(
            WorkItemRead(
                priority="P1",
                item_type="closed_position_review",
                reference_id=position.id,
                title=f"Review closed trade {position.ticker}",
                context={"ticker": position.ticker, "pnl_pct": position.pnl_pct},
            )
            for position in pending_reviews
        )

        open_positions = list(session.scalars(select(Position).where(Position.status == "open")).all())
        items.extend(
            WorkItemRead(
                priority="P2",
                item_type="open_position_monitor",
                reference_id=position.id,
                title=f"Monitor open trade {position.ticker}",
                context={
                    "ticker": position.ticker,
                    "stop_price": position.stop_price,
                    "target_price": position.target_price,
                },
            )
            for position in open_positions
        )

        degraded_strategies = list(session.scalars(select(Strategy).where(Strategy.status == "degraded")).all())
        for strategy in degraded_strategies:
            candidate_versions = [version for version in strategy.versions if version.lifecycle_stage == "candidate"]
            for candidate in candidate_versions:
                items.append(
                    WorkItemRead(
                        priority="P3",
                        item_type="degraded_candidate_validation",
                        reference_id=candidate.id,
                        title=f"Validate recovery candidate v{candidate.version} for {strategy.code}",
                        context={
                            "strategy_id": strategy.id,
                            "strategy_code": strategy.code,
                            "strategy_status": strategy.status,
                            "candidate_version_id": candidate.id,
                            "degraded_version_id": strategy.current_version_id,
                        },
                    )
                )

        new_signals = list(session.scalars(select(Signal).where(Signal.status == "new")).all())
        latest_signals_by_ticker: dict[str, Signal] = {}
        for signal in new_signals:
            current = latest_signals_by_ticker.get(signal.ticker)
            if current is None or signal.id > current.id:
                latest_signals_by_ticker[signal.ticker] = signal
        items.extend(
            WorkItemRead(
                priority="P4",
                item_type="signal_review",
                reference_id=signal.id,
                title=f"Evaluate signal {signal.ticker}",
                context={"ticker": signal.ticker, "quality_score": signal.quality_score},
            )
            for signal in latest_signals_by_ticker.values()
        )

        failure_patterns = [
            pattern for pattern in self.failure_analysis_service.list_patterns(session) if pattern.occurrences >= 2
        ]
        items.extend(
            WorkItemRead(
                priority="P5",
                item_type="failure_pattern",
                reference_id=pattern.id,
                title=f"Investigate repeated {pattern.failure_mode}",
                context={"strategy_id": pattern.strategy_id, "occurrences": pattern.occurrences},
            )
            for pattern in failure_patterns
        )

        open_research_tasks = list(
            session.scalars(select(ResearchTask).where(ResearchTask.status.in_(["open", "in_progress"]))).all()
        )
        items.extend(
            WorkItemRead(
                priority="P6",
                item_type="research_task",
                reference_id=task.id,
                title=task.title,
                context={"task_type": task.task_type, "strategy_id": task.strategy_id},
            )
            for task in open_research_tasks
        )

        priority_order = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}
        items.sort(key=lambda item: (priority_order[item.priority], item.reference_id or 0))
        return WorkQueueRead(total_items=len(items), items=items)
