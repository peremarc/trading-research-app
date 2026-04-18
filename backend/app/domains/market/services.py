from __future__ import annotations

from datetime import date, timedelta
import time
import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.signal import TradeSignal
from app.db.models.strategy import Strategy
from app.domains.system.events import EventLogService
from app.domains.market.repositories import AnalysisRepository, ResearchTaskRepository, TradeSignalRepository
from app.domains.market.schemas import (
    AnalysisRunCreate,
    ResearchTaskCreate,
    TradeSignalCreate,
    WorkItemRead,
    WorkQueueRead,
)
from app.providers.market_data.base import MarketDataProviderError, MarketSnapshot, OHLCVCandle
from app.providers.market_data.ibkr_proxy_provider import IBKRProxyProvider
from app.providers.market_data.stub_provider import StubMarketDataProvider
from app.providers.market_data.twelve_data_provider import TwelveDataProvider
from app.core.config import get_settings
from app.providers.calendar import CalendarEvent, CalendarProviderError, FinnhubCalendarProvider
from app.providers.news import GNewsProvider, NewsArticle, NewsProviderError
from app.providers.web_research import DuckDuckGoSearchProvider, WebPage, WebPageFetcher, WebResearchError, WebSearchResult


class MarketDataUnavailableError(RuntimeError):
    pass


class AnalysisService:
    def __init__(self, repository: AnalysisRepository | None = None) -> None:
        self.repository = repository or AnalysisRepository()

    def list_runs(self, session: Session):
        return self.repository.list(session)

    def create_run(self, session: Session, payload: AnalysisRunCreate):
        return self.repository.create(session, payload)


class MarketDataService:
    def __init__(self, *, raise_on_provider_error: bool = False, cache_ttl_seconds: int = 300) -> None:
        settings = get_settings()
        self.raise_on_provider_error = raise_on_provider_error
        self.provider_name = settings.market_data_provider
        self.fallback_provider = StubMarketDataProvider()
        self.provider = self.fallback_provider
        self.cache_ttl_seconds = cache_ttl_seconds
        self.rate_limit_cooldown_seconds = 65
        self._snapshot_cache: dict[str, tuple[float, MarketSnapshot]] = {}
        self._history_cache: dict[tuple[str, int], tuple[float, list[OHLCVCandle]]] = {}
        self._provider_cooldown_until: float | None = None

        if settings.market_data_provider == "ibkr_proxy":
            self.provider = IBKRProxyProvider(
                settings.ibkr_proxy_base_url,
                api_key=settings.ibkr_proxy_api_key,
                timeout_seconds=settings.ibkr_proxy_timeout_seconds,
            )
        elif settings.market_data_provider == "twelve_data" and settings.twelve_data_api_key:
            self.provider = TwelveDataProvider(settings.twelve_data_api_key)

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        cache_key = ticker.upper()
        if (cached_snapshot := self._get_cached_snapshot(cache_key)) is not None:
            return cached_snapshot

        if (cached_history := self._get_cached_history(cache_key, 220)) is not None:
            snapshot = self._build_snapshot_from_candles(cache_key, cached_history)
            self._snapshot_cache[cache_key] = (time.monotonic(), snapshot)
            return snapshot

        if self._provider_is_in_cooldown():
            snapshot = self._build_snapshot_from_fallback(cache_key)
            self._snapshot_cache[cache_key] = (time.monotonic(), snapshot)
            return snapshot

        self._ensure_provider_ready()
        try:
            snapshot = self.provider.get_snapshot(ticker)
        except MarketDataProviderError as exc:
            if self._is_rate_limit_error(exc):
                self._enter_provider_cooldown()
                snapshot = self._build_snapshot_from_fallback(cache_key)
                self._snapshot_cache[cache_key] = (time.monotonic(), snapshot)
                return snapshot
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    f"Market data provider '{self.provider_name}' failed while loading snapshot for {ticker}: {exc}"
                ) from exc
            snapshot = self.fallback_provider.get_snapshot(ticker)
        self._snapshot_cache[cache_key] = (time.monotonic(), snapshot)
        return snapshot

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        cache_key = ticker.upper()
        if (cached_history := self._get_cached_history(cache_key, limit)) is not None:
            return cached_history[-limit:]

        if self._provider_is_in_cooldown():
            candles = self._build_history_from_fallback(cache_key, limit)
            self._history_cache[(cache_key, limit)] = (time.monotonic(), candles)
            return candles

        self._ensure_provider_ready()
        try:
            candles = self.provider.get_history(ticker, limit=limit)
        except MarketDataProviderError as exc:
            if self._is_rate_limit_error(exc):
                self._enter_provider_cooldown()
                candles = self._build_history_from_fallback(cache_key, limit)
                self._history_cache[(cache_key, limit)] = (time.monotonic(), candles)
                return candles
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    f"Market data provider '{self.provider_name}' failed while loading history for {ticker}: {exc}"
                ) from exc
            candles = self.fallback_provider.get_history(ticker, limit=limit)
        self._history_cache[(cache_key, limit)] = (time.monotonic(), candles)
        return candles

    def _ensure_provider_ready(self) -> None:
        if (
            self.raise_on_provider_error
            and self.provider_name != "stub"
            and self.provider is self.fallback_provider
        ):
            raise MarketDataUnavailableError(
                f"Market data provider '{self.provider_name}' is configured but not ready. Review API credentials before resuming."
            )

    def _provider_is_in_cooldown(self) -> bool:
        return self._provider_cooldown_until is not None and time.monotonic() < self._provider_cooldown_until

    def _enter_provider_cooldown(self) -> None:
        self._provider_cooldown_until = time.monotonic() + self.rate_limit_cooldown_seconds

    def _get_cached_snapshot(self, ticker: str) -> MarketSnapshot | None:
        cached = self._snapshot_cache.get(ticker)
        if cached is None:
            return None
        cached_at, snapshot = cached
        if time.monotonic() - cached_at > self.cache_ttl_seconds:
            self._snapshot_cache.pop(ticker, None)
            return None
        return snapshot

    def _get_cached_history(self, ticker: str, minimum_limit: int) -> list[OHLCVCandle] | None:
        candidates = [
            (cached_limit, candles)
            for (cached_ticker, cached_limit), (cached_at, candles) in self._history_cache.items()
            if cached_ticker == ticker and cached_limit >= minimum_limit and time.monotonic() - cached_at <= self.cache_ttl_seconds
        ]
        if not candidates:
            expired_keys = [
                key
                for key, (cached_at, _) in self._history_cache.items()
                if time.monotonic() - cached_at > self.cache_ttl_seconds
            ]
            for key in expired_keys:
                self._history_cache.pop(key, None)
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _get_any_cached_snapshot(self, ticker: str) -> MarketSnapshot | None:
        cached = self._snapshot_cache.get(ticker)
        if cached is None:
            return None
        return cached[1]

    def _get_any_cached_history(self, ticker: str, minimum_limit: int) -> list[OHLCVCandle] | None:
        candidates = [
            (cached_limit, candles)
            for (cached_ticker, cached_limit), (_, candles) in self._history_cache.items()
            if cached_ticker == ticker and cached_limit >= minimum_limit
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _build_snapshot_from_fallback(self, ticker: str) -> MarketSnapshot:
        if (cached_snapshot := self._get_any_cached_snapshot(ticker)) is not None:
            return cached_snapshot
        if (cached_history := self._get_any_cached_history(ticker, 220)) is not None:
            return self._build_snapshot_from_candles(ticker, cached_history)
        return self.fallback_provider.get_snapshot(ticker)

    def _build_history_from_fallback(self, ticker: str, limit: int) -> list[OHLCVCandle]:
        if (cached_history := self._get_any_cached_history(ticker, limit)) is not None:
            return cached_history[-limit:]
        return self.fallback_provider.get_history(ticker, limit=limit)

    @staticmethod
    def _is_rate_limit_error(exc: MarketDataProviderError) -> bool:
        message = str(exc).lower()
        return "run out of api credits" in message or "wait for the next minute" in message

    @staticmethod
    def _build_snapshot_from_candles(ticker: str, candles: list[OHLCVCandle]) -> MarketSnapshot:
        if len(candles) < 200:
            raise MarketDataUnavailableError(
                f"Market data provider cache for {ticker} does not contain enough candles to build a snapshot."
            )

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        price = closes[-1]
        sma_20 = round(sum(closes[-20:]) / 20, 2)
        sma_50 = round(sum(closes[-50:]) / 50, 2)
        sma_200 = round(sum(closes[-200:]) / 200, 2)

        gains = []
        losses = []
        for idx in range(1, len(closes[-15:])):
            delta = closes[-15:][idx] - closes[-15:][idx - 1]
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))
        avg_gain = sum(gains) / max(len(gains), 1)
        avg_loss = sum(losses) / max(len(losses), 1)
        rsi_14 = round(100.0 if avg_loss == 0 else 100 - (100 / (1 + (avg_gain / avg_loss))), 2)

        true_ranges = []
        recent_highs = highs[-15:]
        recent_lows = lows[-15:]
        recent_closes = closes[-15:]
        for idx in range(1, len(recent_highs)):
            true_ranges.append(
                max(
                    recent_highs[idx] - recent_lows[idx],
                    abs(recent_highs[idx] - recent_closes[idx - 1]),
                    abs(recent_lows[idx] - recent_closes[idx - 1]),
                )
            )
        atr_14 = round(sum(true_ranges) / max(len(true_ranges), 1), 2)

        return MarketSnapshot(
            ticker=ticker,
            price=round(price, 2),
            sma_20=sma_20,
            sma_50=sma_50,
            sma_200=sma_200,
            rsi_14=rsi_14,
            relative_volume=round(volumes[-1] / max(sum(volumes[-21:-1]) / 20, 1.0), 2),
            atr_14=atr_14,
            week_performance=round(((price / closes[-6]) - 1), 4),
            month_performance=round(((price / closes[-22]) - 1), 4),
        )


class SignalService:
    def __init__(
        self,
        repository: TradeSignalRepository | None = None,
        fused_analysis_service: object | None = None,
        event_log_service: EventLogService | None = None,
    ) -> None:
        self.repository = repository or TradeSignalRepository()
        self.fused_analysis_service = fused_analysis_service
        self.event_log_service = event_log_service or EventLogService()

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
        return self.list_trade_signals(session)

    def list_trade_signals(self, session: Session):
        return self.repository.list(session)

    def create_signal(self, session: Session, payload: TradeSignalCreate):
        return self.create_trade_signal(session, payload)

    def create_trade_signal(self, session: Session, payload: TradeSignalCreate):
        return self.create_trade_signal_with_source(session, payload, event_source="market_analysis")

    def create_signal_with_source(
        self,
        session: Session,
        payload: TradeSignalCreate,
        *,
        event_source: str,
    ):
        return self.create_trade_signal_with_source(session, payload, event_source=event_source)

    def create_trade_signal_with_source(
        self,
        session: Session,
        payload: TradeSignalCreate,
        *,
        event_source: str,
    ):
        signal = self.repository.create(session, payload)
        self.event_log_service.record(
            session,
            event_type="trade_signal.created",
            entity_type="trade_signal",
            entity_id=signal.id,
            source=event_source,
            pdca_phase_hint="do",
            payload={
                "ticker": signal.ticker,
                "signal_type": signal.signal_type,
                "status": signal.status,
                "strategy_id": signal.strategy_id,
                "setup_id": signal.setup_id,
                "signal_definition_id": signal.signal_definition_id,
            },
        )
        return signal

    def update_status(self, session: Session, signal_id: int, status: str, rejection_reason: str | None = None):
        return self.update_trade_signal_status(session, signal_id, status, rejection_reason)

    def update_trade_signal_status(
        self,
        session: Session,
        signal_id: int,
        status: str,
        rejection_reason: str | None = None,
    ):
        return self.update_status_with_source(
            session,
            signal_id,
            status=status,
            rejection_reason=rejection_reason,
            event_source="market_analysis",
        )

    def update_status_with_source(
        self,
        session: Session,
        signal_id: int,
        *,
        status: str,
        rejection_reason: str | None = None,
        event_source: str,
    ):
        return self.update_trade_signal_status_with_source(
            session,
            signal_id,
            status=status,
            rejection_reason=rejection_reason,
            event_source=event_source,
        )

    def update_trade_signal_status_with_source(
        self,
        session: Session,
        signal_id: int,
        *,
        status: str,
        rejection_reason: str | None = None,
        event_source: str,
    ):
        signal = self.repository.update_status(session, signal_id, status=status, rejection_reason=rejection_reason)
        self.event_log_service.record(
            session,
            event_type="trade_signal.status_updated",
            entity_type="trade_signal",
            entity_id=signal.id,
            source=event_source,
            pdca_phase_hint="do",
            payload={"ticker": signal.ticker, "status": signal.status, "rejection_reason": signal.rejection_reason},
        )
        return signal


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


class NewsService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = (
            GNewsProvider(
                api_key=self.settings.gnews_api_key,
                base_url=self.settings.gnews_base_url,
                language=self.settings.gnews_language,
                country=self.settings.gnews_country,
                max_results=self.settings.gnews_max_results,
            )
            if self.settings.gnews_api_key
            else None
        )
        self.cache_ttl_seconds = self.settings.gnews_cache_ttl_seconds
        self._cache: dict[tuple[str, int], tuple[float, list[NewsArticle]]] = {}

    def list_news(self, query: str, *, max_results: int | None = None) -> list[NewsArticle]:
        if self.provider is None:
            return []
        limit = max_results or self.settings.gnews_max_results
        cache_key = (query.strip().lower(), limit)
        cached = self._cache.get(cache_key)
        if cached is not None:
            cached_at, articles = cached
            if time.monotonic() - cached_at <= self.cache_ttl_seconds:
                return articles
            self._cache.pop(cache_key, None)

        articles = self.provider.search(query, max_results=limit)
        self._cache[cache_key] = (time.monotonic(), articles)
        self._prune_expired_cache()
        return articles

    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None) -> list[NewsArticle]:
        query = self._build_ticker_query(ticker)
        return self.list_news(query, max_results=max_results)

    def clear_cache(self) -> None:
        self._cache.clear()

    def _prune_expired_cache(self) -> None:
        expired_keys = [
            key
            for key, (cached_at, _) in self._cache.items()
            if time.monotonic() - cached_at > self.cache_ttl_seconds
        ]
        for key in expired_keys:
            self._cache.pop(key, None)

    @staticmethod
    def _build_ticker_query(ticker: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", ticker.upper())
        return f"{cleaned} stock OR {cleaned} earnings OR {cleaned} guidance"


class CalendarService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = (
            FinnhubCalendarProvider(api_key=self.settings.finnhub_api_key)
            if self.settings.finnhub_api_key
            else None
        )

    def list_ticker_events(
        self,
        ticker: str,
        *,
        days_ahead: int = 21,
    ) -> list[CalendarEvent]:
        if self.provider is None:
            return []
        horizon = max(1, min(days_ahead, 90))
        return self.provider.get_earnings_calendar(
            from_date=date.today(),
            to_date=date.today() + timedelta(days=horizon),
            symbol=ticker,
        )

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        if self.provider is None:
            return []
        horizon = max(1, min(days_ahead, 60))
        return self.provider.get_economic_calendar(
            from_date=date.today(),
            to_date=date.today() + timedelta(days=horizon),
        )


class WebResearchService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.allowed_domains = self._parse_allowed_domains(self.settings.web_allowed_domains)
        self.search_provider = DuckDuckGoSearchProvider(timeout_seconds=self.settings.web_request_timeout_seconds)
        self.page_fetcher = WebPageFetcher(timeout_seconds=self.settings.web_request_timeout_seconds)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        domains: list[str] | None = None,
    ) -> list[WebSearchResult]:
        self._ensure_enabled()
        cleaned_query = query.strip()
        if not cleaned_query:
            raise WebResearchError("Web search query must be a non-empty string.")

        requested_domains = self._normalize_requested_domains(domains)
        raw_results = self.search_provider.search(
            cleaned_query,
            max_results=max_results or self.settings.web_search_max_results,
        )
        filtered = [
            result
            for result in raw_results
            if self._is_url_allowed(result.url, requested_domains=requested_domains)
        ]
        return filtered[: max_results or self.settings.web_search_max_results]

    def fetch_article(self, url: str, *, max_chars: int | None = None) -> WebPage:
        self._ensure_enabled()
        cleaned_url = url.strip()
        if not cleaned_url:
            raise WebResearchError("Web fetch URL must be a non-empty string.")
        if not self._is_url_allowed(cleaned_url, requested_domains=None):
            raise WebResearchError("URL domain is not allowed by web research policy.")
        return self.page_fetcher.fetch(
            cleaned_url,
            max_chars=max_chars or self.settings.web_fetch_max_chars,
        )

    def _ensure_enabled(self) -> None:
        if not self.settings.web_research_enabled:
            raise WebResearchError("Web research is disabled in the active backend settings.")

    def _normalize_requested_domains(self, domains: list[str] | None) -> list[str]:
        if not domains:
            return []
        normalized = [self._normalize_domain(domain) for domain in domains if self._normalize_domain(domain)]
        if not normalized:
            return []
        disallowed = [domain for domain in normalized if not self._domain_in_allowlist(domain)]
        if disallowed:
            raise WebResearchError(
                "Requested domains are outside the allowed web research policy: " + ", ".join(sorted(set(disallowed)))
            )
        return normalized

    def _is_url_allowed(self, url: str, *, requested_domains: list[str] | None) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        host = self._normalize_domain(parsed.netloc)
        if not host:
            return False
        candidates = requested_domains or self.allowed_domains
        return any(host == domain or host.endswith(f".{domain}") for domain in candidates)

    def _domain_in_allowlist(self, domain: str) -> bool:
        return any(domain == allowed or domain.endswith(f".{allowed}") for allowed in self.allowed_domains)

    @staticmethod
    def _parse_allowed_domains(raw: str) -> list[str]:
        domains = [WebResearchService._normalize_domain(part) for part in raw.split(",")]
        cleaned = [domain for domain in domains if domain]
        return cleaned or ["reuters.com"]

    @staticmethod
    def _normalize_domain(value: str) -> str:
        normalized = re.sub(r"^https?://", "", value.strip().lower())
        normalized = normalized.split("/", 1)[0]
        return normalized.removeprefix("www.")


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

        new_signals = list(session.scalars(select(TradeSignal).where(TradeSignal.status == "new")).all())
        latest_signals_by_ticker: dict[str, TradeSignal] = {}
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
