from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import mean, pstdev
import time
import re
from threading import BoundedSemaphore, Event, RLock
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, Settings
from app.db.models.position import Position
from app.db.models.research_task import ResearchTask
from app.db.models.signal import TradeSignal
from app.db.models.strategy import Strategy
from app.db.models.watchlist import Watchlist, WatchlistItem
from app.domains.system.events import EventLogService
from app.domains.market.repositories import AnalysisRepository, ResearchTaskRepository, TradeSignalRepository
from app.domains.market.schemas import (
    AnalysisRunCreate,
    ProviderRuntimeStatusRead,
    ResearchTaskCreate,
    TradeSignalCreate,
    WorkItemRead,
    WorkQueueRead,
    WorkQueueSummaryRead,
)
from app.providers.market_data.base import MarketDataProviderError, MarketSnapshot, OHLCVCandle
from app.providers.market_data.ibkr_proxy_provider import IBKRProxyProvider
from app.providers.market_data.stub_provider import StubMarketDataProvider
from app.providers.market_data.twelve_data_provider import TwelveDataProvider
from app.core.config import get_settings
from app.providers.calendar import (
    AlphaVantageCalendarProvider,
    CalendarEvent,
    CalendarProviderError,
    FREDReleaseDatesProvider,
    FinnhubCalendarProvider,
    IBKRProxyCorporateEventsProvider,
    OfficialMacroCalendarProvider,
)
from app.providers.news import GNewsProvider, NewsArticle, NewsProviderError
from app.providers.strategy_company import StrategyCompanyProvider
from app.providers.web_research import DuckDuckGoSearchProvider, WebPage, WebPageFetcher, WebResearchError, WebSearchResult


class MarketDataUnavailableError(RuntimeError):
    pass


@dataclass
class _InFlightCall:
    event: Event = field(default_factory=Event)
    result: object | None = None
    error: BaseException | None = None


class _InFlightCallRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._calls: dict[object, _InFlightCall] = {}

    def run(self, key: object, fn) -> object:
        with self._lock:
            call = self._calls.get(key)
            if call is None:
                call = _InFlightCall()
                self._calls[key] = call
                owner = True
            else:
                owner = False
        if owner:
            try:
                call.result = fn()
            except BaseException as exc:
                call.error = exc
            finally:
                call.event.set()
                with self._lock:
                    self._calls.pop(key, None)
            if call.error is not None:
                raise call.error
            return call.result
        call.event.wait()
        if call.error is not None:
            raise call.error
        return call.result


class _BackpressureGateRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._gates: dict[tuple[str, int], BoundedSemaphore] = {}

    def run(self, key: str, *, limit: int, fn) -> object:
        if limit <= 0:
            return fn()
        semaphore = self._get_gate(key=key, limit=limit)
        semaphore.acquire()
        try:
            return fn()
        finally:
            semaphore.release()

    def _get_gate(self, *, key: str, limit: int) -> BoundedSemaphore:
        cache_key = (key, limit)
        with self._lock:
            gate = self._gates.get(cache_key)
            if gate is None:
                gate = BoundedSemaphore(limit)
                self._gates[cache_key] = gate
            return gate


class _ProviderCooldownRegistry:
    def __init__(self) -> None:
        self._lock = RLock()
        self._cooldowns: dict[str, float] = {}

    def is_in_cooldown(self, key: str) -> bool:
        return self.remaining_seconds(key) > 0

    def remaining_seconds(self, key: str) -> float:
        with self._lock:
            until = self._cooldowns.get(key)
            if until is None:
                return 0.0
            remaining = until - time.monotonic()
            if remaining <= 0:
                self._cooldowns.pop(key, None)
                return 0.0
            return remaining

    def enter(self, key: str, *, seconds: float) -> None:
        duration = max(float(seconds), 1.0)
        with self._lock:
            self._cooldowns[key] = max(self._cooldowns.get(key, 0.0), time.monotonic() + duration)


_SHARED_BACKPRESSURE_GATES = _BackpressureGateRegistry()
_SHARED_PROVIDER_COOLDOWNS = _ProviderCooldownRegistry()


class AnalysisService:
    def __init__(self, repository: AnalysisRepository | None = None) -> None:
        self.repository = repository or AnalysisRepository()

    def list_runs(self, session: Session):
        return self.repository.list(session)

    def create_run(self, session: Session, payload: AnalysisRunCreate):
        return self.repository.create(session, payload)


class MarketDataService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        raise_on_provider_error: bool = False,
        cache_ttl_seconds: int = 300,
    ) -> None:
        settings = settings or get_settings()
        self.settings = settings
        self.raise_on_provider_error = raise_on_provider_error
        self.provider_name = settings.market_data_provider
        self.fallback_provider = StubMarketDataProvider()
        self.provider = self.fallback_provider
        self.cache_ttl_seconds = cache_ttl_seconds
        self.rate_limit_cooldown_seconds = 65
        self.max_concurrent_requests = max(int(settings.market_data_max_concurrent_requests), 0)
        self._snapshot_cache: dict[str, tuple[float, MarketSnapshot]] = {}
        self._history_cache: dict[tuple[str, int], tuple[float, list[OHLCVCandle]]] = {}
        self._market_overview_cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self._options_sentiment_cache: dict[tuple[str, str], tuple[float, dict]] = {}
        self._options_sentiment_rankings_cache: dict[tuple[str, str, str, str, int], tuple[float, dict]] = {}
        self._provider_cooldown_until: float | None = None
        self._lock = RLock()
        self._inflight_calls = _InFlightCallRegistry()
        self._backpressure_gates = _SHARED_BACKPRESSURE_GATES
        self._provider_cooldowns = _SHARED_PROVIDER_COOLDOWNS

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
        return self._inflight_calls.run(
            ("snapshot", cache_key),
            lambda: self._load_snapshot(cache_key=cache_key, ticker=ticker),
        )

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        cache_key = ticker.upper()
        if (cached_history := self._get_cached_history(cache_key, limit)) is not None:
            return cached_history[-limit:]
        return self._inflight_calls.run(
            ("history", cache_key, limit),
            lambda: self._load_history(cache_key=cache_key, ticker=ticker, limit=limit),
        )

    def get_options_sentiment(self, ticker: str, *, sec_type: str = "STK") -> dict:
        normalized_ticker = ticker.strip().upper()
        normalized_sec_type = sec_type.strip().upper() if sec_type else "STK"
        cache_key = (normalized_ticker, normalized_sec_type)
        if (cached_payload := self._get_cached_options_sentiment(cache_key)) is not None:
            return dict(cached_payload)
        return dict(
            self._inflight_calls.run(
                ("options_sentiment", cache_key),
                lambda: self._load_options_sentiment(
                    cache_key=cache_key,
                    ticker=normalized_ticker,
                    sec_type=normalized_sec_type,
                ),
            )
        )

    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        normalized_ticker = ticker.strip().upper()
        normalized_sec_type = sec_type.strip().upper() if sec_type else "STK"
        cache_key = (normalized_ticker, normalized_sec_type)
        if (cached_payload := self._get_cached_market_overview(cache_key)) is not None:
            return dict(cached_payload)
        return dict(
            self._inflight_calls.run(
                ("market_overview", cache_key),
                lambda: self._load_market_overview_payload(
                    cache_key=cache_key,
                    ticker=normalized_ticker,
                    sec_type=normalized_sec_type,
                ),
            )
        )

    def get_options_sentiment_rankings(
        self,
        *,
        basis: str = "volume",
        direction: str = "high",
        instrument: str = "STK",
        location: str | None = None,
        limit: int = 20,
    ) -> dict:
        normalized_basis = basis.strip().lower() if basis else "volume"
        normalized_direction = direction.strip().lower() if direction else "high"
        normalized_instrument = instrument.strip().upper() if instrument else "STK"
        normalized_location = (
            location.strip()
            if isinstance(location, str) and location.strip()
            else self.settings.opportunity_discovery_scanner_location
        )
        normalized_limit = max(int(limit), 1)
        cache_key = (
            normalized_basis,
            normalized_direction,
            normalized_instrument,
            normalized_location,
            normalized_limit,
        )
        if (cached_payload := self._get_cached_options_sentiment_rankings(cache_key)) is not None:
            return dict(cached_payload)
        return dict(
            self._inflight_calls.run(
                ("options_sentiment_rankings", cache_key),
                lambda: self._load_options_sentiment_rankings(
                    cache_key=cache_key,
                    basis=normalized_basis,
                    direction=normalized_direction,
                    instrument=normalized_instrument,
                    location=normalized_location,
                    limit=normalized_limit,
                ),
            )
        )

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
        return self._provider_cooldown_remaining_seconds() > 0

    def _enter_provider_cooldown(self, seconds: float | None = None) -> None:
        duration = max(float(seconds if seconds is not None else self.rate_limit_cooldown_seconds), 1.0)
        with self._lock:
            self._provider_cooldown_until = time.monotonic() + duration
        self._provider_cooldowns.enter(self._provider_cooldown_key(), seconds=duration)

    def _provider_cooldown_remaining_seconds(self) -> float:
        with self._lock:
            local_remaining = (
                max(self._provider_cooldown_until - time.monotonic(), 0.0)
                if self._provider_cooldown_until is not None
                else 0.0
            )
            if local_remaining <= 0:
                self._provider_cooldown_until = None
        shared_remaining = self._provider_cooldowns.remaining_seconds(self._provider_cooldown_key())
        return max(local_remaining, shared_remaining)

    def _provider_backpressure_key(self) -> str:
        provider_target = self._provider_target()
        return f"market_data:{self.provider_name}:{provider_target}"

    def _provider_cooldown_key(self) -> str:
        provider_target = self._provider_target()
        return f"market_data:{self.provider_name}:{provider_target}"

    def _provider_target(self) -> str:
        if self.provider_name == "ibkr_proxy":
            return self.settings.ibkr_proxy_base_url or self._provider_type_name(self.provider)
        if self.provider_name == "twelve_data":
            return self.settings.twelve_data_api_key or self._provider_type_name(self.provider)
        return self._provider_type_name(self.provider)

    @staticmethod
    def _provider_type_name(provider: object | None) -> str:
        if provider is None:
            return "none"
        return type(provider).__qualname__

    def _run_with_provider_backpressure(self, fn):
        if self.provider is self.fallback_provider or self.provider_name == "stub":
            return fn()
        return self._backpressure_gates.run(
            self._provider_backpressure_key(),
            limit=self.max_concurrent_requests,
            fn=fn,
        )

    def get_provider_runtime_status(self) -> dict[str, ProviderRuntimeStatusRead]:
        return {
            "market_data": self._provider_runtime_status_entry(
                provider=self.provider,
                cooldown_key=self._provider_cooldown_key(),
                concurrency_limit=self.max_concurrent_requests,
            )
        }

    def _provider_runtime_status_entry(
        self,
        *,
        provider: object | None,
        cooldown_key: str,
        concurrency_limit: int,
    ) -> ProviderRuntimeStatusRead:
        remaining_seconds = round(
            max(self._provider_cooldown_remaining_seconds(), self._provider_cooldowns.remaining_seconds(cooldown_key)),
            1,
        )
        return ProviderRuntimeStatusRead(
            provider=self._provider_type_name(provider),
            configured=provider is not None and (provider is not self.fallback_provider or self.provider_name == "stub"),
            cooling_down=remaining_seconds > 0,
            cooldown_remaining_seconds=remaining_seconds,
            concurrency_limit=max(int(concurrency_limit), 0),
        )

    def _store_snapshot(self, ticker: str, snapshot: MarketSnapshot) -> None:
        with self._lock:
            self._snapshot_cache[ticker] = (time.monotonic(), snapshot)

    def _store_history(self, ticker: str, limit: int, candles: list[OHLCVCandle]) -> None:
        with self._lock:
            self._history_cache[(ticker, limit)] = (time.monotonic(), candles)

    def _store_market_overview(self, cache_key: tuple[str, str], payload: dict) -> None:
        with self._lock:
            self._market_overview_cache[cache_key] = (time.monotonic(), dict(payload))

    def _store_options_sentiment(self, cache_key: tuple[str, str], payload: dict) -> None:
        with self._lock:
            self._options_sentiment_cache[cache_key] = (time.monotonic(), dict(payload))

    def _store_options_sentiment_rankings(
        self,
        cache_key: tuple[str, str, str, str, int],
        payload: dict,
    ) -> None:
        with self._lock:
            self._options_sentiment_rankings_cache[cache_key] = (time.monotonic(), dict(payload))

    def _load_snapshot(self, *, cache_key: str, ticker: str) -> MarketSnapshot:
        if (cached_snapshot := self._get_cached_snapshot(cache_key)) is not None:
            return cached_snapshot
        if (cached_history := self._get_cached_history(cache_key, 220)) is not None:
            snapshot = self._build_snapshot_from_candles(cache_key, cached_history)
            self._store_snapshot(cache_key, snapshot)
            return snapshot
        if self._provider_is_in_cooldown():
            snapshot = self._build_snapshot_from_fallback(cache_key)
            self._store_snapshot(cache_key, snapshot)
            return snapshot

        self._ensure_provider_ready()
        try:
            snapshot = self._run_with_provider_backpressure(lambda: self.provider.get_snapshot(ticker))
        except MarketDataProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_error(exc)) is not None:
                self._enter_provider_cooldown(cooldown_seconds)
                snapshot = self._build_snapshot_from_fallback(cache_key)
                self._store_snapshot(cache_key, snapshot)
                return snapshot
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    f"Market data provider '{self.provider_name}' failed while loading snapshot for {ticker}: {exc}"
                ) from exc
            snapshot = self.fallback_provider.get_snapshot(ticker)
        self._store_snapshot(cache_key, snapshot)
        return snapshot

    def _load_history(self, *, cache_key: str, ticker: str, limit: int) -> list[OHLCVCandle]:
        if (cached_history := self._get_cached_history(cache_key, limit)) is not None:
            return cached_history[-limit:]
        if self._provider_is_in_cooldown():
            candles = self._build_history_from_fallback(cache_key, limit)
            self._store_history(cache_key, limit, candles)
            return candles

        self._ensure_provider_ready()
        try:
            candles = self._run_with_provider_backpressure(lambda: self.provider.get_history(ticker, limit=limit))
        except MarketDataProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_error(exc)) is not None:
                self._enter_provider_cooldown(cooldown_seconds)
                candles = self._build_history_from_fallback(cache_key, limit)
                self._store_history(cache_key, limit, candles)
                return candles
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    f"Market data provider '{self.provider_name}' failed while loading history for {ticker}: {exc}"
                ) from exc
            candles = self.fallback_provider.get_history(ticker, limit=limit)
        self._store_history(cache_key, limit, candles)
        return candles

    def _load_options_sentiment(
        self,
        *,
        cache_key: tuple[str, str],
        ticker: str,
        sec_type: str,
    ) -> dict:
        if (cached_payload := self._get_cached_options_sentiment(cache_key)) is not None:
            return dict(cached_payload)

        get_options_sentiment = getattr(self.provider, "get_options_sentiment", None)
        if not callable(get_options_sentiment):
            payload = self._build_unsupported_options_sentiment(
                ticker=ticker,
                sec_type=sec_type,
            )
            self._store_options_sentiment(cache_key, payload)
            return payload

        if self._provider_is_in_cooldown():
            payload = self._build_options_sentiment_from_fallback(
                ticker=ticker,
                sec_type=sec_type,
                provider_error="Market data provider is cooling down after a transient upstream error.",
            )
            self._store_options_sentiment(cache_key, payload)
            return payload

        try:
            payload = self._run_with_provider_backpressure(lambda: get_options_sentiment(ticker, sec_type=sec_type))
        except MarketDataProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_error(exc)) is not None:
                self._enter_provider_cooldown(cooldown_seconds)
                payload = self._build_options_sentiment_from_fallback(
                    ticker=ticker,
                    sec_type=sec_type,
                    provider_error=str(exc),
                )
                self._store_options_sentiment(cache_key, payload)
                return payload
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    f"Market data provider '{self.provider_name}' failed while loading options sentiment for {ticker}: {exc}"
                ) from exc
            payload = self._build_unavailable_options_sentiment(
                ticker=ticker,
                sec_type=sec_type,
                provider_error=str(exc),
            )

        normalized_payload = dict(payload or {})
        if "available" not in normalized_payload:
            normalized_payload["available"] = True
        normalized_payload.setdefault("symbol", ticker)
        normalized_payload.setdefault("sec_type", sec_type)
        normalized_payload.setdefault("provider_error", None)
        self._store_options_sentiment(cache_key, normalized_payload)
        return normalized_payload

    def _load_market_overview_payload(
        self,
        *,
        cache_key: tuple[str, str],
        ticker: str,
        sec_type: str,
    ) -> dict:
        if (cached_payload := self._get_cached_market_overview(cache_key)) is not None:
            return dict(cached_payload)

        get_market_overview = getattr(self.provider, "get_market_overview", None)
        if not callable(get_market_overview):
            payload = self._build_market_overview_from_fallback(
                ticker=ticker,
                sec_type=sec_type,
                provider_error="Market overview is not supported by the current market data provider.",
            )
            self._store_market_overview(cache_key, payload)
            return payload

        if self._provider_is_in_cooldown():
            payload = self._build_market_overview_from_fallback(
                ticker=ticker,
                sec_type=sec_type,
                provider_error="Market data provider is cooling down after a transient upstream error.",
            )
            self._store_market_overview(cache_key, payload)
            return payload

        try:
            payload = self._run_with_provider_backpressure(lambda: get_market_overview(ticker, sec_type=sec_type))
        except MarketDataProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_error(exc)) is not None:
                self._enter_provider_cooldown(cooldown_seconds)
                payload = self._build_market_overview_from_fallback(
                    ticker=ticker,
                    sec_type=sec_type,
                    provider_error=str(exc),
                )
                self._store_market_overview(cache_key, payload)
                return payload
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    f"Market data provider '{self.provider_name}' failed while loading market overview for {ticker}: {exc}"
                ) from exc
            payload = self._build_market_overview_from_fallback(
                ticker=ticker,
                sec_type=sec_type,
                provider_error=str(exc),
            )

        normalized_payload = dict(payload or {})
        normalized_payload.setdefault("available", True)
        normalized_payload.setdefault("symbol", ticker)
        normalized_payload.setdefault("sec_type", sec_type)
        normalized_payload.setdefault("provider_source", "ibkr_proxy_market_overview")
        normalized_payload.setdefault("market_signals", {})
        normalized_payload.setdefault(
            "options_sentiment",
            self._build_unavailable_options_sentiment(
                ticker=ticker,
                sec_type=sec_type,
                provider_error="Market overview did not include options sentiment.",
            ),
        )
        normalized_payload.setdefault("corporate_events", [])
        normalized_payload.setdefault("provider_error", None)
        self._store_market_overview(cache_key, normalized_payload)
        return normalized_payload

    def _load_options_sentiment_rankings(
        self,
        *,
        cache_key: tuple[str, str, str, str, int],
        basis: str,
        direction: str,
        instrument: str,
        location: str,
        limit: int,
    ) -> dict:
        if (cached_payload := self._get_cached_options_sentiment_rankings(cache_key)) is not None:
            return dict(cached_payload)

        get_rankings = getattr(self.provider, "get_options_sentiment_rankings", None)
        if not callable(get_rankings):
            payload = self._build_unavailable_options_sentiment_rankings(
                basis=basis,
                direction=direction,
                instrument=instrument,
                location=location,
                provider_error="Options sentiment rankings are not supported by the current market data provider.",
            )
            self._store_options_sentiment_rankings(cache_key, payload)
            return payload

        if self._provider_is_in_cooldown():
            payload = self._build_options_sentiment_rankings_from_fallback(
                basis=basis,
                direction=direction,
                instrument=instrument,
                location=location,
                limit=limit,
                provider_error="Market data provider is cooling down after a transient upstream error.",
            )
            self._store_options_sentiment_rankings(cache_key, payload)
            return payload

        try:
            payload = self._run_with_provider_backpressure(
                lambda: get_rankings(
                    basis=basis,
                    direction=direction,
                    instrument=instrument,
                    location=location,
                    limit=limit,
                )
            )
        except MarketDataProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_error(exc)) is not None:
                self._enter_provider_cooldown(cooldown_seconds)
                payload = self._build_options_sentiment_rankings_from_fallback(
                    basis=basis,
                    direction=direction,
                    instrument=instrument,
                    location=location,
                    limit=limit,
                    provider_error=str(exc),
                )
                self._store_options_sentiment_rankings(cache_key, payload)
                return payload
            if self.raise_on_provider_error:
                raise MarketDataUnavailableError(
                    "Market data provider "
                    f"'{self.provider_name}' failed while loading options sentiment rankings: {exc}"
                ) from exc
            payload = self._build_unavailable_options_sentiment_rankings(
                basis=basis,
                direction=direction,
                instrument=instrument,
                location=location,
                provider_error=str(exc),
            )

        normalized_payload = dict(payload or {})
        if "available" not in normalized_payload:
            normalized_payload["available"] = True
        normalized_payload.setdefault("basis", basis)
        normalized_payload.setdefault("direction", direction)
        normalized_payload.setdefault("instrument", instrument)
        normalized_payload.setdefault("location", location)
        normalized_payload.setdefault("contracts", [])
        normalized_payload.setdefault("provider_error", None)
        self._store_options_sentiment_rankings(cache_key, normalized_payload)
        return normalized_payload

    def _get_cached_snapshot(self, ticker: str) -> MarketSnapshot | None:
        with self._lock:
            cached = self._snapshot_cache.get(ticker)
            if cached is None:
                return None
            cached_at, snapshot = cached
            if time.monotonic() - cached_at > self.cache_ttl_seconds:
                self._snapshot_cache.pop(ticker, None)
                return None
            return snapshot

    def _get_cached_history(self, ticker: str, minimum_limit: int) -> list[OHLCVCandle] | None:
        with self._lock:
            now = time.monotonic()
            candidates = [
                (cached_limit, candles)
                for (cached_ticker, cached_limit), (cached_at, candles) in self._history_cache.items()
                if cached_ticker == ticker and cached_limit >= minimum_limit and now - cached_at <= self.cache_ttl_seconds
            ]
            if not candidates:
                expired_keys = [
                    key
                    for key, (cached_at, _) in self._history_cache.items()
                    if now - cached_at > self.cache_ttl_seconds
                ]
                for key in expired_keys:
                    self._history_cache.pop(key, None)
                return None
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]

    def _get_cached_market_overview(self, cache_key: tuple[str, str]) -> dict | None:
        with self._lock:
            cached = self._market_overview_cache.get(cache_key)
            if cached is None:
                return None
            cached_at, payload = cached
            if time.monotonic() - cached_at > self.cache_ttl_seconds:
                self._market_overview_cache.pop(cache_key, None)
                return None
            return dict(payload)

    def _get_cached_options_sentiment(self, cache_key: tuple[str, str]) -> dict | None:
        with self._lock:
            cached = self._options_sentiment_cache.get(cache_key)
            if cached is None:
                return None
            cached_at, payload = cached
            if time.monotonic() - cached_at > self.cache_ttl_seconds:
                self._options_sentiment_cache.pop(cache_key, None)
                return None
            return dict(payload)

    def _get_cached_options_sentiment_rankings(self, cache_key: tuple[str, str, str, str, int]) -> dict | None:
        with self._lock:
            cached = self._options_sentiment_rankings_cache.get(cache_key)
            if cached is None:
                return None
            cached_at, payload = cached
            if time.monotonic() - cached_at > self.cache_ttl_seconds:
                self._options_sentiment_rankings_cache.pop(cache_key, None)
                return None
            return dict(payload)

    def _get_any_cached_snapshot(self, ticker: str) -> MarketSnapshot | None:
        with self._lock:
            cached = self._snapshot_cache.get(ticker)
            if cached is None:
                return None
            return cached[1]

    def _get_any_cached_history(self, ticker: str, minimum_limit: int) -> list[OHLCVCandle] | None:
        with self._lock:
            candidates = [
                (cached_limit, candles)
                for (cached_ticker, cached_limit), (_, candles) in self._history_cache.items()
                if cached_ticker == ticker and cached_limit >= minimum_limit
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]

    def _get_any_cached_market_overview(self, cache_key: tuple[str, str]) -> dict | None:
        with self._lock:
            cached = self._market_overview_cache.get(cache_key)
            if cached is None:
                return None
            return dict(cached[1])

    def _get_any_cached_options_sentiment(self, cache_key: tuple[str, str]) -> dict | None:
        with self._lock:
            cached = self._options_sentiment_cache.get(cache_key)
            if cached is None:
                return None
            return dict(cached[1])

    def _get_any_cached_options_sentiment_rankings(self, cache_key: tuple[str, str, str, str, int]) -> dict | None:
        with self._lock:
            cached = self._options_sentiment_rankings_cache.get(cache_key)
            if cached is None:
                return None
            return dict(cached[1])

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

    def _build_options_sentiment_from_fallback(self, *, ticker: str, sec_type: str, provider_error: str) -> dict:
        cache_key = (ticker, sec_type)
        if (cached_payload := self._get_any_cached_options_sentiment(cache_key)) is not None:
            return cached_payload
        return self._build_unavailable_options_sentiment(
            ticker=ticker,
            sec_type=sec_type,
            provider_error=provider_error,
        )

    def _build_market_overview_from_fallback(self, *, ticker: str, sec_type: str, provider_error: str) -> dict:
        cache_key = (ticker, sec_type)
        if (cached_payload := self._get_any_cached_market_overview(cache_key)) is not None:
            if provider_error and not cached_payload.get("provider_error"):
                cached_payload["provider_error"] = provider_error
            return cached_payload

        return {
            "available": True,
            "symbol": ticker,
            "sec_type": sec_type,
            "provider_source": "composed_fallback",
            "market_signals": self._build_market_signals_from_fallback(ticker),
            "options_sentiment": self.get_options_sentiment(ticker, sec_type=sec_type),
            "corporate_events": [],
            "provider_error": provider_error,
        }

    def _build_options_sentiment_rankings_from_fallback(
        self,
        *,
        basis: str,
        direction: str,
        instrument: str,
        location: str,
        limit: int,
        provider_error: str,
    ) -> dict:
        cache_key = (basis, direction, instrument, location, limit)
        if (cached_payload := self._get_any_cached_options_sentiment_rankings(cache_key)) is not None:
            return cached_payload
        return self._build_unavailable_options_sentiment_rankings(
            basis=basis,
            direction=direction,
            instrument=instrument,
            location=location,
            provider_error=provider_error,
        )

    def _build_market_signals_from_fallback(self, ticker: str) -> dict:
        snapshot = self.get_snapshot(ticker)
        payload = asdict(snapshot)
        return {
            "available": True,
            "symbol": snapshot.ticker,
            "sec_type": "STK",
            "last_price": payload.get("price"),
            "relative_volume": payload.get("relative_volume"),
            "atr_14": payload.get("atr_14"),
            "week_performance": payload.get("week_performance"),
            "month_performance": payload.get("month_performance"),
            "sma_20": payload.get("sma_20"),
            "sma_50": payload.get("sma_50"),
            "sma_200": payload.get("sma_200"),
            "rsi_14": payload.get("rsi_14"),
            "raw": payload,
            "source": "derived_snapshot",
        }

    @staticmethod
    def _build_unavailable_options_sentiment(*, ticker: str, sec_type: str, provider_error: str) -> dict:
        return {
            "available": False,
            "symbol": ticker,
            "sec_type": sec_type,
            "put_call_ratio": None,
            "put_call_volume_ratio": None,
            "option_implied_vol_pct": None,
            "provider_error": provider_error,
            "contracts": [],
        }

    @staticmethod
    def _build_unsupported_options_sentiment(*, ticker: str, sec_type: str) -> dict:
        return {
            "available": False,
            "symbol": ticker,
            "sec_type": sec_type,
            "put_call_ratio": None,
            "put_call_volume_ratio": None,
            "option_implied_vol_pct": None,
            "provider_error": "Options sentiment is not supported by the current market data provider.",
            "contracts": [],
        }

    @staticmethod
    def _build_unavailable_options_sentiment_rankings(
        *,
        basis: str,
        direction: str,
        instrument: str,
        location: str,
        provider_error: str,
    ) -> dict:
        return {
            "available": False,
            "basis": basis,
            "direction": direction,
            "instrument": instrument,
            "location": location,
            "contracts": [],
            "provider_error": provider_error,
        }

    @staticmethod
    def _transient_cooldown_seconds_for_error(exc: MarketDataProviderError) -> float | None:
        message = str(exc).lower()
        if "run out of api credits" in message or "wait for the next minute" in message:
            return 65.0
        if "no bridge" in message or "gateway unavailable" in message or "upstream unavailable" in message:
            return 15.0
        if "cooling down" in message or "retry_after_seconds" in message:
            retry_after_match = re.search(r"retry_after_seconds['\"]?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", str(exc))
            if retry_after_match is not None:
                return min(max(float(retry_after_match.group(1)) + 0.5, 1.0), 30.0)
            return 5.0
        return None

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

    def ensure_market_scouting_task(
        self,
        session: Session,
        *,
        ticker: str,
        market_regime: str,
        setup_type: str | None,
        combined_score: float | None,
        relative_volume: float | None,
        month_performance: float | None,
        news_titles: list[str],
        event_titles: list[str],
        universe_source: str,
    ):
        normalized_ticker = ticker.strip().upper()
        title = f"Scout ticker {normalized_ticker} for watchlist expansion"
        existing = self.repository.find_open_by_signature(
            session,
            strategy_id=None,
            task_type="market_scouting",
            title=title,
        )
        if existing is not None:
            return existing, False

        score_text = f"{combined_score:.2f}" if isinstance(combined_score, (int, float)) else "n/a"
        setup_label = (setup_type or "unspecified setup").replace("_", " ").strip()
        relative_volume_value = float(relative_volume or 0.0)
        month_performance_pct = round(float(month_performance or 0.0) * 100, 2)
        priority = "high" if (combined_score or 0.0) >= 0.65 or news_titles or event_titles else "normal"
        return self.repository.create(
            session,
            ResearchTaskCreate(
                strategy_id=None,
                task_type="market_scouting",
                priority=priority,
                title=title,
                hypothesis=(
                    f"{normalized_ticker} may deserve a fresh watchlist hypothesis under regime "
                    f"{market_regime}. Current structure reads as {setup_label} with combined score "
                    f"{score_text} and relative volume {relative_volume_value:.2f}."
                ),
                scope={
                    "ticker": normalized_ticker,
                    "source": "idle_research_fallback",
                    "market_state_regime": market_regime,
                    "setup_type": setup_type,
                    "combined_score": combined_score,
                    "relative_volume": relative_volume,
                    "month_performance_pct": month_performance_pct,
                    "news_titles": news_titles[:3],
                    "calendar_events": event_titles[:3],
                    "universe_source": universe_source,
                    "goal": "decide whether to add the ticker to a watchlist, formulate a scenario map, or reject it",
                },
            ),
        ), True

    def ensure_macro_strategy_task(
        self,
        session: Session,
        *,
        theme_slug: str,
        theme_title: str,
        regime: str,
        scenario: str | None,
        timeframe: str | None,
        importance: float,
        focus_assets: list[str],
        impact_hypothesis: str,
        strategy_ideas: list[str],
        evidence_points: list[str],
        macro_signal_key: str,
        linked_watchlist_code: str | None = None,
        linked_watchlist_id: int | None = None,
    ):
        normalized_slug = re.sub(r"[^a-z0-9_]+", "_", theme_slug.strip().lower()).strip("_") or "macro_theme"
        title = f"Exploit macro theme {normalized_slug}"
        existing = self.repository.find_open_by_signature(
            session,
            strategy_id=None,
            task_type="macro_strategy_research",
            title=title,
        )
        if existing is not None:
            return existing, False

        priority = "high" if importance >= 0.75 else "normal"
        return self.repository.create(
            session,
            ResearchTaskCreate(
                strategy_id=None,
                task_type="macro_strategy_research",
                priority=priority,
                title=title,
                hypothesis=(
                    f"{theme_title} may be creating a tradable macro edge. {impact_hypothesis}"
                ),
                scope={
                    "theme_slug": normalized_slug,
                    "theme_title": theme_title,
                    "source": "macro_research_lane",
                    "regime": regime,
                    "scenario": scenario,
                    "timeframe": timeframe,
                    "importance": round(float(importance), 2),
                    "focus_assets": focus_assets[:8],
                    "strategy_ideas": strategy_ideas[:5],
                    "evidence_points": evidence_points[:6],
                    "macro_signal_key": macro_signal_key,
                    "linked_watchlist_code": linked_watchlist_code,
                    "linked_watchlist_id": linked_watchlist_id,
                    "goal": (
                        "translate the macro thesis into watchlists, scenario trees, "
                        "hedges, and executable entry or risk-management playbooks"
                    ),
                },
            ),
        ), True


class MSTRContextService:
    MSTR_TICKERS = {"MSTR"}
    LOW_ATM_THRESHOLD = 2.5
    HIGH_ATM_THRESHOLD = 4.0
    RECENT_BTC_PURCHASE_DAYS = 14
    RECENT_CAPITAL_RAISE_DAYS = 21

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        market_data_service: MarketDataService | None = None,
        strategy_company_provider: StrategyCompanyProvider | None = None,
        btc_proxy_symbol: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.market_data_service = market_data_service or MarketDataService()
        self.strategy_company_provider = strategy_company_provider or StrategyCompanyProvider(settings=self.settings)
        self.btc_proxy_symbol = (
            str(btc_proxy_symbol or self.settings.strategy_company_btc_proxy_symbol).strip().upper() or "IBIT"
        )

    def build_context(
        self,
        *,
        ticker: str,
        market_context: dict | None = None,
        signal_payload: dict | None = None,
    ) -> dict:
        del market_context, signal_payload
        normalized_ticker = ticker.strip().upper()
        if normalized_ticker not in self.MSTR_TICKERS:
            return self._not_applicable_context(ticker=normalized_ticker)

        company_metrics = dict(self.strategy_company_provider.get_mstr_metrics() or {})
        if not company_metrics.get("available"):
            return self._unavailable_context(
                ticker=normalized_ticker,
                provider_error=str(company_metrics.get("provider_error") or "Strategy company metrics unavailable."),
                company_metrics=company_metrics,
            )

        try:
            mstr_snapshot = self.market_data_service.get_snapshot(normalized_ticker)
            btc_proxy_snapshot = self.market_data_service.get_snapshot(self.btc_proxy_symbol)
            mstr_history = self.market_data_service.get_history(normalized_ticker, limit=120)
        except (MarketDataUnavailableError, RuntimeError, ValueError) as exc:
            return self._unavailable_context(
                ticker=normalized_ticker,
                provider_error=str(exc),
                company_metrics=company_metrics,
            )

        stats = dict(company_metrics.get("stats") or {})
        purchases_history = list(company_metrics.get("purchases_history") or [])
        shares_history = list(company_metrics.get("shares_history") or [])
        latest_purchase = dict(company_metrics.get("latest_purchase") or (purchases_history[-1] if purchases_history else {}))
        latest_shares = dict(company_metrics.get("latest_shares") or (shares_history[-1] if shares_history else {}))
        previous_purchase = dict(purchases_history[-2]) if len(purchases_history) >= 2 else {}
        previous_shares = dict(shares_history[-2]) if len(shares_history) >= 2 else {}

        basic_shares_outstanding = (
            self._coerce_positive_float(stats.get("basic_shares_outstanding"))
            or self._coerce_positive_float(latest_purchase.get("basic_shares_outstanding"))
            or self._coerce_positive_float(latest_shares.get("basic_shares_outstanding"))
        )
        assumed_diluted_shares = (
            self._coerce_positive_float(latest_shares.get("assumed_diluted_shares_outstanding"))
            or self._coerce_positive_float(latest_purchase.get("assumed_diluted_shares_outstanding"))
        )
        btc_holdings = (
            self._coerce_positive_float(latest_purchase.get("btc_holdings"))
            or self._coerce_positive_float(stats.get("btc_holdings"))
            or self._coerce_positive_float(latest_shares.get("total_bitcoin_holdings"))
        )
        btc_reserve_millions = self._coerce_positive_float(latest_purchase.get("btc_reserve_millions"))
        btc_reserve_value = btc_reserve_millions * 1_000_000 if btc_reserve_millions is not None else None
        debt = self._coerce_non_negative_float(stats.get("debt")) or 0.0
        pref = self._coerce_non_negative_float(stats.get("pref")) or 0.0
        cash = self._coerce_non_negative_float(stats.get("cash")) or 0.0

        current_btc_price = (
            round(btc_reserve_value / btc_holdings, 2)
            if btc_reserve_value is not None and btc_holdings not in {None, 0.0}
            else None
        )
        enterprise_value = (
            (float(mstr_snapshot.price) * basic_shares_outstanding) + debt + pref - cash
            if basic_shares_outstanding is not None
            else None
        )
        current_mnav = (
            round(enterprise_value / btc_reserve_value, 4)
            if enterprise_value is not None and btc_reserve_value not in {None, 0.0}
            else None
        )
        mnav_bucket = self._classify_mnav_bucket(current_mnav)
        atm_risk_context = self._classify_atm_risk(current_mnav)
        capital_raise_mode = self._classify_capital_raise_mode(current_mnav)

        btc_holdings_change = self._delta(
            btc_holdings,
            self._coerce_positive_float(previous_purchase.get("btc_holdings")),
        )
        btc_holdings_change_pct = self._delta_pct(
            btc_holdings,
            self._coerce_positive_float(previous_purchase.get("btc_holdings")),
        )
        assumed_diluted_shares_change = self._delta(
            assumed_diluted_shares,
            self._coerce_positive_float(previous_shares.get("assumed_diluted_shares_outstanding")),
        )
        assumed_diluted_shares_change_pct = self._delta_pct(
            assumed_diluted_shares,
            self._coerce_positive_float(previous_shares.get("assumed_diluted_shares_outstanding")),
        )
        basic_shares_change_pct = self._delta_pct(
            basic_shares_outstanding,
            self._coerce_positive_float(previous_shares.get("basic_shares_outstanding"))
            or self._coerce_positive_float(previous_purchase.get("basic_shares_outstanding")),
        )

        bps = (
            round(btc_holdings / assumed_diluted_shares, 8)
            if btc_holdings is not None and assumed_diluted_shares not in {None, 0.0}
            else None
        )
        previous_bps = self._safe_bps(
            btc_holdings=self._coerce_positive_float(previous_shares.get("total_bitcoin_holdings"))
            or self._coerce_positive_float(previous_purchase.get("btc_holdings")),
            diluted_shares=self._coerce_positive_float(previous_shares.get("assumed_diluted_shares_outstanding"))
            or self._coerce_positive_float(previous_purchase.get("assumed_diluted_shares_outstanding")),
        )
        bps_trend = self._classify_bps_trend(current_bps=bps, previous_bps=previous_bps)
        bps_change_pct = self._delta_pct(bps, previous_bps)

        last_btc_purchase_date = self._parse_date(latest_purchase.get("date_of_purchase"))
        days_since_last_btc_purchase = (
            (date.today() - last_btc_purchase_date).days
            if last_btc_purchase_date is not None
            else None
        )
        recent_btc_purchase = (
            isinstance(days_since_last_btc_purchase, int)
            and 0 <= days_since_last_btc_purchase <= self.RECENT_BTC_PURCHASE_DAYS
        )

        latest_shares_date = self._parse_date(latest_shares.get("date"))
        days_since_latest_shares_update = (
            (date.today() - latest_shares_date).days
            if latest_shares_date is not None
            else None
        )
        recent_capital_raise = bool(
            isinstance(days_since_latest_shares_update, int)
            and 0 <= days_since_latest_shares_update <= self.RECENT_CAPITAL_RAISE_DAYS
            and (
                (assumed_diluted_shares_change_pct or 0.0) > 0.0025
                or (basic_shares_change_pct or 0.0) > 0.0025
            )
        )
        share_dilution_accelerating = bool(
            (assumed_diluted_shares_change_pct or 0.0) > max((btc_holdings_change_pct or 0.0), 0.0)
            and (assumed_diluted_shares_change_pct or 0.0) > 0.005
        )

        btc_proxy_state = self._classify_btc_proxy_state(btc_proxy_snapshot)
        mstr_vs_btc_proxy_month_spread = round(
            float(mstr_snapshot.month_performance) - float(btc_proxy_snapshot.month_performance),
            4,
        )
        mnav_zscore_30d, mnav_zscore_method, mnav_zscore_points = self._compute_mnav_zscore(
            current_mnav=current_mnav,
            purchases_history=purchases_history,
            mstr_history=mstr_history,
            debt=debt,
            pref=pref,
            cash=cash,
        )

        supportive_signals: list[str] = []
        risk_flags: list[str] = []
        score = 0.5

        if btc_proxy_state == "strong":
            score += 0.12
            supportive_signals.append("btc_proxy_trend_supportive")
        elif btc_proxy_state == "weak":
            score -= 0.12
            risk_flags.append("btc_proxy_trend_weak")

        if atm_risk_context == "low":
            score += 0.12
            supportive_signals.append("mnav_below_common_atm_threshold")
        elif atm_risk_context == "moderate":
            score -= 0.04
            risk_flags.append("mnav_in_opportunistic_common_atm_band")
        elif atm_risk_context == "high":
            score -= 0.18
            risk_flags.append("mnav_in_active_common_atm_band")

        if recent_btc_purchase:
            score += 0.04
            supportive_signals.append("recent_btc_purchase_disclosed")

        if bps_trend == "rising":
            score += 0.05
            supportive_signals.append("bps_trend_rising")
        elif bps_trend == "deteriorating":
            score -= 0.05
            risk_flags.append("bps_trend_deteriorating")

        if recent_capital_raise:
            score -= 0.05
            risk_flags.append("recent_share_count_expansion")

        if share_dilution_accelerating:
            score -= 0.08
            risk_flags.append("share_dilution_outpacing_btc_accumulation")

        if atm_risk_context == "high" and btc_proxy_state == "weak":
            score -= 0.08
            risk_flags.append("btc_weak_with_high_mnav")
        elif atm_risk_context == "high" and btc_proxy_state == "strong":
            risk_flags.append("btc_strong_but_high_mnav")

        if company_metrics.get("stale"):
            score -= 0.05
            risk_flags.append("strategy_metrics_stale")

        bias = "mixed"
        if score >= 0.66:
            bias = "supportive"
        elif score <= 0.34:
            bias = "headwind"

        exposure_preference = "neutral"
        if btc_proxy_state == "strong" and atm_risk_context == "low":
            exposure_preference = "prefer_mstr_over_btc_proxy"
        elif btc_proxy_state == "weak" and atm_risk_context in {"moderate", "high"}:
            exposure_preference = "prefer_btc_proxy_or_wait"
        elif atm_risk_context == "high":
            exposure_preference = "prefer_btc_proxy_or_smaller_mstr"

        evidence_points = self._dedupe(
            [
                f"mNAV={round(current_mnav, 2)}x" if isinstance(current_mnav, float) else "",
                f"ATM={atm_risk_context} ({capital_raise_mode})" if atm_risk_context != "unavailable" else "",
                f"BTC holdings={int(btc_holdings):,}" if isinstance(btc_holdings, float) else "",
                f"BTC holdings change={int(btc_holdings_change):,}" if isinstance(btc_holdings_change, float) else "",
                (
                    f"diluted shares change={int(assumed_diluted_shares_change):,}"
                    if isinstance(assumed_diluted_shares_change, float)
                    else ""
                ),
                f"BPS={bps:.6f}" if isinstance(bps, float) else "",
                f"BTC proxy {self.btc_proxy_symbol} 20d={round(float(btc_proxy_snapshot.month_performance) * 100, 2)}%",
                f"MSTR vs {self.btc_proxy_symbol} 20d={round(mstr_vs_btc_proxy_month_spread * 100, 2)}%",
                (
                    f"days_since_last_btc_purchase={days_since_last_btc_purchase}"
                    if isinstance(days_since_last_btc_purchase, int)
                    else ""
                ),
            ]
            + supportive_signals
            + risk_flags
        )

        return {
            "applicable": True,
            "available": True,
            "theme": "strategy_company_mstr_context",
            "ticker": normalized_ticker,
            "provider_source": company_metrics.get("source"),
            "as_of": company_metrics.get("as_of"),
            "stale": bool(company_metrics.get("stale")),
            "used_fallback": bool(company_metrics.get("used_fallback")),
            "cache": dict(company_metrics.get("cache") or {}),
            "score": round(max(min(score, 1.0), 0.0), 2),
            "bias": bias,
            "btc_proxy_symbol": self.btc_proxy_symbol,
            "btc_proxy_state": btc_proxy_state,
            "btc_proxy_month_performance": round(float(btc_proxy_snapshot.month_performance), 4),
            "mstr_month_performance": round(float(mstr_snapshot.month_performance), 4),
            "mstr_vs_btc_proxy_month_spread": mstr_vs_btc_proxy_month_spread,
            "current_mnav": current_mnav,
            "mnav_bucket": mnav_bucket,
            "mnav_distance_to_2_5x": self._distance_to_threshold(current_mnav, self.LOW_ATM_THRESHOLD),
            "mnav_distance_to_4_0x": self._distance_to_threshold(current_mnav, self.HIGH_ATM_THRESHOLD),
            "mnav_zscore_30d": mnav_zscore_30d,
            "mnav_zscore_method": mnav_zscore_method,
            "mnav_zscore_points": mnav_zscore_points,
            "btc_holdings": int(btc_holdings) if isinstance(btc_holdings, float) else None,
            "btc_holdings_change": int(btc_holdings_change) if isinstance(btc_holdings_change, float) else None,
            "btc_holdings_change_pct": round(btc_holdings_change_pct, 4)
            if isinstance(btc_holdings_change_pct, float)
            else None,
            "assumed_diluted_shares": int(assumed_diluted_shares) if isinstance(assumed_diluted_shares, float) else None,
            "assumed_diluted_shares_change": int(assumed_diluted_shares_change)
            if isinstance(assumed_diluted_shares_change, float)
            else None,
            "assumed_diluted_shares_change_pct": round(assumed_diluted_shares_change_pct, 4)
            if isinstance(assumed_diluted_shares_change_pct, float)
            else None,
            "bps": bps,
            "bps_change_pct": round(bps_change_pct, 4) if isinstance(bps_change_pct, float) else None,
            "bps_trend": bps_trend,
            "btc_yield": self._coerce_float(latest_purchase.get("btc_yield_ytd"))
            or self._coerce_float(stats.get("btc_yield_ytd")),
            "btc_gain": self._coerce_float(latest_purchase.get("btc_gain_ytd"))
            or self._coerce_float(stats.get("btc_gain_ytd")),
            "btc_dollar_gain": None,
            "btc_reserve_millions": btc_reserve_millions,
            "current_btc_price": current_btc_price,
            "days_since_last_btc_purchase": days_since_last_btc_purchase,
            "recent_btc_purchase": recent_btc_purchase,
            "recent_capital_raise": recent_capital_raise,
            "capital_raise_mode": capital_raise_mode,
            "atm_risk_context": atm_risk_context,
            "share_dilution_accelerating": share_dilution_accelerating,
            "exposure_preference": exposure_preference,
            "supportive_signals": supportive_signals,
            "risk_flags": risk_flags,
            "evidence_points": evidence_points,
            "summary": self._build_summary(
                ticker=normalized_ticker,
                btc_proxy_state=btc_proxy_state,
                atm_risk_context=atm_risk_context,
                bps_trend=bps_trend,
                exposure_preference=exposure_preference,
            ),
            "limitations": [
                "Use mNAV and capital-markets metrics as valuation/risk context, not as standalone trade triggers.",
                "BTC Yield and BTC Gain are company KPIs, not classical shareholder-return metrics.",
            ],
            "provider_error": company_metrics.get("provider_error"),
        }

    @classmethod
    def _not_applicable_context(cls, *, ticker: str) -> dict:
        return {
            "applicable": False,
            "available": False,
            "theme": "not_applicable",
            "ticker": ticker,
            "score": 0.5,
            "bias": "neutral",
            "summary": "No Strategy/MSTR-specific context applied.",
            "provider_error": None,
        }

    @classmethod
    def _unavailable_context(cls, *, ticker: str, provider_error: str, company_metrics: dict) -> dict:
        return {
            "applicable": True,
            "available": False,
            "theme": "strategy_company_mstr_context",
            "ticker": ticker,
            "provider_source": company_metrics.get("source"),
            "as_of": company_metrics.get("as_of"),
            "stale": bool(company_metrics.get("stale")),
            "used_fallback": bool(company_metrics.get("used_fallback")),
            "cache": dict(company_metrics.get("cache") or {}),
            "score": 0.5,
            "bias": "neutral",
            "btc_proxy_symbol": None,
            "btc_proxy_state": "unavailable",
            "mnav_bucket": "unavailable",
            "atm_risk_context": "unavailable",
            "supportive_signals": [],
            "risk_flags": [],
            "evidence_points": [],
            "summary": "MSTR context is applicable, but Strategy metrics or supporting market data are unavailable.",
            "provider_error": provider_error,
        }

    @classmethod
    def _classify_mnav_bucket(cls, value: float | None) -> str:
        if value is None:
            return "unavailable"
        if value < 2.0:
            return "lt_2_0"
        if value < cls.LOW_ATM_THRESHOLD:
            return "2_0_to_2_5"
        if value <= cls.HIGH_ATM_THRESHOLD:
            return "2_5_to_4_0"
        return "gt_4_0"

    @classmethod
    def _classify_atm_risk(cls, value: float | None) -> str:
        if value is None:
            return "unavailable"
        if value < cls.LOW_ATM_THRESHOLD:
            return "low"
        if value <= cls.HIGH_ATM_THRESHOLD:
            return "moderate"
        return "high"

    @classmethod
    def _classify_capital_raise_mode(cls, value: float | None) -> str:
        if value is None:
            return "unavailable"
        if value < cls.LOW_ATM_THRESHOLD:
            return "restricted_or_exception_only"
        if value <= cls.HIGH_ATM_THRESHOLD:
            return "opportunistic_common_atm"
        return "active_common_atm"

    @staticmethod
    def _classify_btc_proxy_state(snapshot: MarketSnapshot) -> str:
        if snapshot.price > snapshot.sma_20 > snapshot.sma_50 and snapshot.month_performance >= 0.05:
            return "strong"
        if snapshot.price < snapshot.sma_20 and snapshot.month_performance <= -0.05:
            return "weak"
        return "neutral"

    @staticmethod
    def _classify_bps_trend(*, current_bps: float | None, previous_bps: float | None) -> str:
        if current_bps is None or previous_bps in {None, 0.0}:
            return "unknown"
        ratio = (current_bps / previous_bps) - 1
        if ratio >= 0.01:
            return "rising"
        if ratio <= -0.01:
            return "deteriorating"
        return "flat"

    def _compute_mnav_zscore(
        self,
        *,
        current_mnav: float | None,
        purchases_history: list[dict],
        mstr_history: list[OHLCVCandle],
        debt: float,
        pref: float,
        cash: float,
    ) -> tuple[float | None, str | None, int]:
        if current_mnav is None or not purchases_history or not mstr_history:
            return None, None, 0

        cutoff = date.today() - timedelta(days=60)
        history_values: list[float] = []
        for row in purchases_history:
            purchase_date = self._parse_date(row.get("date_of_purchase"))
            if purchase_date is None or purchase_date < cutoff:
                continue
            reserve_millions = self._coerce_positive_float(row.get("btc_reserve_millions"))
            basic_shares = self._coerce_positive_float(row.get("basic_shares_outstanding"))
            close_price = self._close_on_or_before(mstr_history, purchase_date)
            if reserve_millions in {None, 0.0} or basic_shares in {None, 0.0} or close_price is None:
                continue
            reserve_value = reserve_millions * 1_000_000
            enterprise_value = (close_price * basic_shares) + debt + pref - cash
            if reserve_value <= 0:
                continue
            history_values.append(enterprise_value / reserve_value)

        if len(history_values) < 4:
            return None, None, len(history_values)

        std_dev = pstdev(history_values)
        if std_dev <= 0:
            return 0.0, "purchase_event_mnav_lookback_approx_v1", len(history_values)
        zscore = (current_mnav - mean(history_values)) / std_dev
        return round(zscore, 2), "purchase_event_mnav_lookback_approx_v1", len(history_values)

    @staticmethod
    def _close_on_or_before(history: list[OHLCVCandle], target_date: date) -> float | None:
        candidate_close: float | None = None
        for candle in history:
            candle_date = MSTRContextService._parse_date(candle.timestamp)
            if candle_date is None or candle_date > target_date:
                continue
            candidate_close = float(candle.close)
        return candidate_close

    @staticmethod
    def _safe_bps(*, btc_holdings: float | None, diluted_shares: float | None) -> float | None:
        if btc_holdings is None or diluted_shares in {None, 0.0}:
            return None
        return round(btc_holdings / diluted_shares, 8)

    @staticmethod
    def _distance_to_threshold(value: float | None, threshold: float) -> float | None:
        if value is None:
            return None
        return round(value - threshold, 4)

    @staticmethod
    def _delta(current: float | None, previous: float | None) -> float | None:
        if current is None or previous is None:
            return None
        return current - previous

    @staticmethod
    def _delta_pct(current: float | None, previous: float | None) -> float | None:
        if current is None or previous in {None, 0.0}:
            return None
        return (current - previous) / previous

    @staticmethod
    def _build_summary(
        *,
        ticker: str,
        btc_proxy_state: str,
        atm_risk_context: str,
        bps_trend: str,
        exposure_preference: str,
    ) -> str:
        return (
            f"Strategy-specific context for {ticker} is anchored on BTC proxy state={btc_proxy_state}, "
            f"ATM/dilution risk={atm_risk_context}, BPS trend={bps_trend}, "
            f"exposure_preference={exposure_preference}."
        )

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            marker = text.lower()
            if marker in seen:
                continue
            seen.add(marker)
            results.append(text)
        return results

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_positive_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _coerce_non_negative_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) >= 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None


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
        self.max_concurrent_requests = max(int(self.settings.gnews_max_concurrent_requests), 0)
        self._cache: dict[tuple[str, int], tuple[float, list[NewsArticle]]] = {}
        self._lock = RLock()
        self._inflight_calls = _InFlightCallRegistry()
        self._backpressure_gates = _SHARED_BACKPRESSURE_GATES
        self._provider_cooldowns = _SHARED_PROVIDER_COOLDOWNS

    def list_news(self, query: str, *, max_results: int | None = None) -> list[NewsArticle]:
        if self.provider is None:
            return []
        limit = max_results or self.settings.gnews_max_results
        cache_key = (query.strip().lower(), limit)
        if (cached := self._get_cached_news(cache_key)) is not None:
            return cached
        return self._inflight_calls.run(
            ("news", cache_key),
            lambda: self._load_news(query=query, limit=limit, cache_key=cache_key),
        )

    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None) -> list[NewsArticle]:
        query = self._build_ticker_query(ticker)
        return self.list_news(query, max_results=max_results)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def _prune_expired_cache(self) -> None:
        with self._lock:
            now = time.monotonic()
            expired_keys = [
                key
                for key, (cached_at, _) in self._cache.items()
                if now - cached_at > self.cache_ttl_seconds
            ]
            for key in expired_keys:
                self._cache.pop(key, None)

    def _get_cached_news(self, cache_key: tuple[str, int]) -> list[NewsArticle] | None:
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            cached_at, articles = cached
            if time.monotonic() - cached_at <= self.cache_ttl_seconds:
                return articles
            self._cache.pop(cache_key, None)
            return None

    def _get_any_cached_news(self, cache_key: tuple[str, int]) -> list[NewsArticle] | None:
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            return cached[1]

    def _store_news(self, cache_key: tuple[str, int], articles: list[NewsArticle]) -> None:
        with self._lock:
            self._cache[cache_key] = (time.monotonic(), articles)

    def _load_news(self, *, query: str, limit: int, cache_key: tuple[str, int]) -> list[NewsArticle]:
        if (cached := self._get_cached_news(cache_key)) is not None:
            return cached
        cooldown_key = self._provider_cooldown_key()
        if self._provider_cooldowns.is_in_cooldown(cooldown_key):
            if (cached_any := self._get_any_cached_news(cache_key)) is not None:
                return cached_any
            raise NewsProviderError(
                self._cooldown_message_for_provider(provider_label="GNews", cooldown_key=cooldown_key)
            )
        try:
            articles = self._backpressure_gates.run(
                self._provider_backpressure_key(),
                limit=self.max_concurrent_requests,
                fn=lambda: self.provider.search(query, max_results=limit),
            )
        except NewsProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_news_error(exc)) is not None:
                self._provider_cooldowns.enter(cooldown_key, seconds=cooldown_seconds)
                if (cached_any := self._get_any_cached_news(cache_key)) is not None:
                    return cached_any
            raise
        self._store_news(cache_key, articles)
        self._prune_expired_cache()
        return articles

    def get_provider_runtime_status(self) -> dict[str, ProviderRuntimeStatusRead]:
        return {
            "gnews": self._provider_runtime_status_entry(
                provider=self.provider,
                cooldown_key=self._provider_cooldown_key(),
                concurrency_limit=self.max_concurrent_requests,
            )
        }

    def _provider_backpressure_key(self) -> str:
        provider_name = self._provider_type_name(self.provider)
        provider_target = self.settings.gnews_base_url or provider_name
        return f"news_gnews:{provider_target}"

    def _provider_cooldown_key(self) -> str:
        provider_name = self._provider_type_name(self.provider)
        provider_target = self.settings.gnews_base_url or provider_name
        return f"news_gnews:{provider_target}"

    @staticmethod
    def _provider_type_name(provider: object | None) -> str:
        if provider is None:
            return "none"
        return type(provider).__qualname__

    def _cooldown_message_for_provider(self, *, provider_label: str, cooldown_key: str) -> str:
        remaining = self._provider_cooldowns.remaining_seconds(cooldown_key)
        if remaining > 0:
            return f"{provider_label} temporarily cooling down for {remaining:.1f}s after recent transient failures."
        return f"{provider_label} temporarily cooling down after recent transient failures."

    def _provider_runtime_status_entry(
        self,
        *,
        provider: object | None,
        cooldown_key: str,
        concurrency_limit: int,
    ) -> ProviderRuntimeStatusRead:
        remaining_seconds = round(self._provider_cooldowns.remaining_seconds(cooldown_key), 1)
        return ProviderRuntimeStatusRead(
            provider=self._provider_type_name(provider),
            configured=provider is not None,
            cooling_down=remaining_seconds > 0,
            cooldown_remaining_seconds=remaining_seconds,
            concurrency_limit=max(int(concurrency_limit), 0),
        )

    @staticmethod
    def _transient_cooldown_seconds_for_news_error(exc: NewsProviderError) -> float | None:
        raw_message = str(exc)
        message = raw_message.lower()
        if "retry_after_seconds" in message:
            retry_after_match = re.search(r"retry_after_seconds['\"]?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", raw_message)
            if retry_after_match is not None:
                return min(max(float(retry_after_match.group(1)) + 0.5, 1.0), 60.0)
            return 5.0
        if "too many requests" in message or "http error 429" in message or "rate limit" in message:
            return 65.0
        if "cooling down" in message:
            return 5.0
        if "timeout" in message or "timed out" in message:
            return 15.0
        if (
            "http error 503" in message
            or "service unavailable" in message
            or "temporarily unavailable" in message
            or "upstream unavailable" in message
        ):
            return 15.0
        return None

    @staticmethod
    def _build_ticker_query(ticker: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", ticker.upper())
        return f"{cleaned} stock OR {cleaned} earnings OR {cleaned} guidance"


class CalendarService:
    QUARTERLY_EXPIRY_MONTHS = (3, 6, 9, 12)
    QUARTERLY_EXPIRY_SOURCE = "internal_us_equity_derivatives_expiry_rules_v1"
    PRE_EXPIRY_WINDOW_DAYS = 3
    POST_EXPIRY_WINDOW_DAYS = 2

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        corporate_provider: IBKRProxyCorporateEventsProvider | None = None,
        earnings_provider: AlphaVantageCalendarProvider | None = None,
        macro_provider: FinnhubCalendarProvider | None = None,
        official_macro_provider: OfficialMacroCalendarProvider | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.corporate_provider = corporate_provider or (
            IBKRProxyCorporateEventsProvider(
                base_url=self.settings.ibkr_proxy_base_url,
                api_key=self.settings.ibkr_proxy_api_key,
                timeout_seconds=self.settings.ibkr_proxy_timeout_seconds,
            )
            if self.settings.market_data_provider == "ibkr_proxy" and self.settings.ibkr_proxy_base_url
            else None
        )
        self.earnings_provider = earnings_provider or (
            AlphaVantageCalendarProvider(api_key=self.settings.alpha_vantage_api_key)
            if self.settings.alpha_vantage_api_key
            else None
        )
        self.macro_provider = macro_provider or (
            FinnhubCalendarProvider(api_key=self.settings.finnhub_api_key)
            if self.settings.finnhub_api_key
            else None
        )
        self.official_macro_provider = official_macro_provider or OfficialMacroCalendarProvider()
        if official_macro_provider is None and self.settings.fred_api_key:
            self.official_macro_provider = OfficialMacroCalendarProvider(
                fred_provider=FREDReleaseDatesProvider(
                    api_key=self.settings.fred_api_key,
                    timeout_seconds=self.settings.fred_request_timeout_seconds,
                )
            )
        self.earnings_cache_ttl_seconds = max(int(self.settings.calendar_earnings_cache_ttl_seconds), 0)
        self.ticker_events_cache_ttl_seconds = max(int(self.settings.calendar_ticker_events_cache_ttl_seconds), 0)
        self.macro_events_cache_ttl_seconds = max(int(self.settings.calendar_macro_events_cache_ttl_seconds), 0)
        self.corporate_max_concurrent_requests = max(int(self.settings.calendar_corporate_max_concurrent_requests), 0)
        self.earnings_max_concurrent_requests = max(int(self.settings.calendar_earnings_max_concurrent_requests), 0)
        self.macro_max_concurrent_requests = max(int(self.settings.calendar_macro_max_concurrent_requests), 0)
        self.cache_path = cache_path or (BACKEND_DIR / ".cache" / "alpha_vantage_earnings_calendar.json")
        self._earnings_cache: tuple[float, list[CalendarEvent]] | None = None
        self._ticker_event_context_cache: dict[tuple[str, int], tuple[float, dict]] = {}
        self._macro_events_cache: dict[int, tuple[float, list[CalendarEvent], str | None]] = {}
        self._lock = RLock()
        self._inflight_calls = _InFlightCallRegistry()
        self._backpressure_gates = _SHARED_BACKPRESSURE_GATES
        self._provider_cooldowns = _SHARED_PROVIDER_COOLDOWNS

    def list_ticker_events(
        self,
        ticker: str,
        *,
        days_ahead: int = 21,
    ) -> list[CalendarEvent]:
        return self.get_ticker_event_context(ticker, days_ahead=days_ahead)["events"]

    def get_ticker_event_context(
        self,
        ticker: str,
        *,
        days_ahead: int = 21,
    ) -> dict:
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            return {
                "ticker": "",
                "source": "none",
                "used_fallback": False,
                "provider_error": None,
                "fallback_reason": "empty_ticker",
                "events": [],
                "cache": self._build_earnings_cache_status(),
            }
        horizon = max(1, min(days_ahead, 90))
        if (cached_context := self._get_cached_ticker_event_context(ticker=normalized_ticker, days_ahead=horizon)) is not None:
            return cached_context
        return self._inflight_calls.run(
            ("ticker_events", normalized_ticker, horizon),
            lambda: self._load_ticker_event_context(ticker=normalized_ticker, horizon=horizon),
        )

    def _load_ticker_event_context(self, *, ticker: str, horizon: int) -> dict:
        if (cached_context := self._get_cached_ticker_event_context(ticker=ticker, days_ahead=horizon)) is not None:
            return cached_context
        today = date.today()
        cutoff = today + timedelta(days=horizon)
        primary_error: str | None = None
        if self.corporate_provider is not None:
            corporate_cooldown_key = self._provider_cooldown_key("calendar_corporate")
            try:
                if self._provider_cooldowns.is_in_cooldown(corporate_cooldown_key):
                    corporate_events = []
                    primary_error = self._cooldown_message_for_provider(
                        provider_label="Corporate calendar provider",
                        cooldown_key=corporate_cooldown_key,
                    )
                else:
                    corporate_events = list(
                        self._backpressure_gates.run(
                            self._provider_backpressure_key("calendar_corporate"),
                            limit=self.corporate_max_concurrent_requests,
                            fn=lambda: self.corporate_provider.get_ticker_events(symbol=ticker, sec_type="STK"),
                        )
                    )
            except CalendarProviderError as exc:
                corporate_events = []
                primary_error = str(exc)
                if (cooldown_seconds := self._transient_cooldown_seconds_for_calendar_error(exc)) is not None:
                    self._provider_cooldowns.enter(corporate_cooldown_key, seconds=cooldown_seconds)
            else:
                filtered_corporate_events = [
                    event
                    for event in corporate_events
                    if self._event_date_in_window(event.event_date, from_date=today, to_date=cutoff)
                ]
                if filtered_corporate_events:
                    context = {
                        "ticker": ticker,
                        "source": "ibkr_proxy",
                        "used_fallback": False,
                        "provider_error": None,
                        "fallback_reason": None,
                        "events": filtered_corporate_events,
                        "cache": self._build_earnings_cache_status(),
                    }
                    self._cache_ticker_event_context(
                        ticker=ticker,
                        days_ahead=horizon,
                        context=context,
                    )
                    return self._clone_ticker_event_context(context)
        try:
            fallback_events = self._filter_cached_earnings_events(
                ticker=ticker,
                from_date=today,
                to_date=cutoff,
            )
        except CalendarProviderError as exc:
            fallback_error = str(exc)
            fallback_events = []
        else:
            fallback_error = None
        if fallback_events:
            context = {
                "ticker": ticker,
                "source": "alpha_vantage",
                "used_fallback": self.corporate_provider is not None,
                "provider_error": primary_error,
                "fallback_reason": "proxy_unavailable_or_empty" if self.corporate_provider is not None else None,
                "events": fallback_events,
                "cache": self._build_earnings_cache_status(),
            }
            self._cache_ticker_event_context(
                ticker=ticker,
                days_ahead=horizon,
                context=context,
            )
            return self._clone_ticker_event_context(context)
        context = {
            "ticker": ticker,
            "source": (
                "ibkr_proxy"
                if self.corporate_provider is not None
                else ("alpha_vantage" if self.earnings_provider is not None else "none")
            ),
            "used_fallback": False,
            "provider_error": primary_error or fallback_error,
            "fallback_reason": "no_events_in_window" if self.corporate_provider is not None and not primary_error else None,
            "events": [],
            "cache": self._build_earnings_cache_status(),
        }
        self._cache_ticker_event_context(
            ticker=ticker,
            days_ahead=horizon,
            context=context,
        )
        return self._clone_ticker_event_context(context)

    def _filter_cached_earnings_events(
        self,
        *,
        ticker: str,
        from_date: date,
        to_date: date,
    ) -> list[CalendarEvent]:
        return [
            event
            for event in self._load_earnings_calendar()
            if (event.ticker or "").upper() == ticker
            if self._event_date_in_window(event.event_date, from_date=from_date, to_date=to_date)
        ]

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        horizon = max(1, min(days_ahead, 180))
        if (cached_result := self._get_cached_macro_events(days_ahead=horizon)) is not None:
            cached_events, cached_error = cached_result
            if cached_events:
                return list(cached_events)
            if cached_error:
                raise CalendarProviderError(cached_error)
            return []
        return list(
            self._inflight_calls.run(
                ("macro_events", horizon),
                lambda: self._load_macro_events(days_ahead=horizon),
            )
        )

    def _load_macro_events(self, *, days_ahead: int) -> list[CalendarEvent]:
        if (cached_result := self._get_cached_macro_events(days_ahead=days_ahead)) is not None:
            cached_events, cached_error = cached_result
            if cached_events:
                return list(cached_events)
            if cached_error:
                raise CalendarProviderError(cached_error)
            return []
        from_date = date.today()
        to_date = from_date + timedelta(days=days_ahead)
        combined: list[CalendarEvent] = []
        errors: list[str] = []

        if self.macro_provider is not None:
            macro_cooldown_key = self._provider_cooldown_key("calendar_macro_finnhub")
            if self._provider_cooldowns.is_in_cooldown(macro_cooldown_key):
                errors.append(
                    self._cooldown_message_for_provider(
                        provider_label="Finnhub macro calendar provider",
                        cooldown_key=macro_cooldown_key,
                    )
                )
            else:
                try:
                    combined.extend(
                        list(
                            self._backpressure_gates.run(
                                self._provider_backpressure_key("calendar_macro"),
                                limit=self.macro_max_concurrent_requests,
                                fn=lambda: self.macro_provider.get_economic_calendar(
                                    from_date=from_date,
                                    to_date=to_date,
                                ),
                            )
                        )
                    )
                except CalendarProviderError as exc:
                    errors.append(str(exc))
                    if (cooldown_seconds := self._transient_cooldown_seconds_for_calendar_error(exc)) is not None:
                        self._provider_cooldowns.enter(macro_cooldown_key, seconds=cooldown_seconds)

        if self.official_macro_provider is not None:
            official_macro_cooldown_key = self._provider_cooldown_key("calendar_macro_official")
            if self._provider_cooldowns.is_in_cooldown(official_macro_cooldown_key):
                errors.append(
                    self._cooldown_message_for_provider(
                        provider_label="Official macro calendar provider",
                        cooldown_key=official_macro_cooldown_key,
                    )
                )
            else:
                try:
                    combined.extend(
                        list(
                            self._backpressure_gates.run(
                                self._provider_backpressure_key("calendar_macro"),
                                limit=self.macro_max_concurrent_requests,
                                fn=lambda: self.official_macro_provider.get_events(
                                    from_date=from_date,
                                    to_date=to_date,
                                ),
                            )
                        )
                    )
                except CalendarProviderError as exc:
                    errors.append(str(exc))
                    if (cooldown_seconds := self._transient_cooldown_seconds_for_calendar_error(exc)) is not None:
                        self._provider_cooldowns.enter(official_macro_cooldown_key, seconds=cooldown_seconds)

        deduped = self._dedupe_macro_events(combined)
        if deduped:
            self._cache_macro_events(days_ahead=days_ahead, events=deduped, error=None)
            return list(deduped)
        if errors:
            error_message = " | ".join(errors)
            self._cache_macro_events(days_ahead=days_ahead, events=[], error=error_message)
            raise CalendarProviderError(error_message)
        self._cache_macro_events(days_ahead=days_ahead, events=[], error=None)
        return []

    def get_quarterly_expiry_context(self, *, as_of: date | None = None) -> dict:
        reference_date = as_of or date.today()
        expiries = self._build_quarterly_expiry_schedule(reference_date.year)
        previous_expiry = max(
            (item for item in expiries if item["expiry_date"] <= reference_date),
            default=None,
            key=lambda item: item["expiry_date"],
        )
        next_expiry = min(
            (item for item in expiries if item["expiry_date"] >= reference_date),
            default=None,
            key=lambda item: item["expiry_date"],
        )

        relevant_expiry = next_expiry or previous_expiry
        days_to_event: int | None = None
        phase = "normal"

        if previous_expiry is not None:
            days_since_previous = (reference_date - previous_expiry["expiry_date"]).days
            if 0 < days_since_previous <= self.POST_EXPIRY_WINDOW_DAYS:
                relevant_expiry = previous_expiry
                days_to_event = -days_since_previous
                phase = "post_expiry_window"

        if relevant_expiry is not None and days_to_event is None:
            days_to_event = (relevant_expiry["expiry_date"] - reference_date).days
            if days_to_event == 0:
                phase = "expiry_day"
            elif 0 < days_to_event <= 1:
                phase = "tight_pre_expiry_window"
            elif 0 < days_to_event <= self.PRE_EXPIRY_WINDOW_DAYS:
                phase = "pre_expiry_window"
            elif self._is_same_expiry_week(reference_date, relevant_expiry["expiry_date"]):
                phase = "expiration_week"

        expiry_date = relevant_expiry["expiry_date"] if relevant_expiry is not None else None
        expiration_week = expiry_date is not None and self._is_same_expiry_week(reference_date, expiry_date)
        pre_expiry_window = isinstance(days_to_event, int) and 0 < days_to_event <= self.PRE_EXPIRY_WINDOW_DAYS
        expiry_day = days_to_event == 0
        post_expiry_window = (
            isinstance(days_to_event, int)
            and days_to_event < 0
            and abs(days_to_event) <= self.POST_EXPIRY_WINDOW_DAYS
        )

        return {
            "available": True,
            "source": self.QUARTERLY_EXPIRY_SOURCE,
            "reference_date": reference_date.isoformat(),
            "quarterly_expiry_date": expiry_date.isoformat() if expiry_date is not None else None,
            "days_to_event": days_to_event,
            "expiration_week": expiration_week,
            "pre_expiry_window": pre_expiry_window,
            "expiry_day": expiry_day,
            "post_expiry_window": post_expiry_window,
            "phase": phase,
            "risk_penalty": self._expiry_risk_penalty(
                days_to_event=days_to_event,
                phase=phase,
                expiration_week=expiration_week,
            ),
            "reason": self._build_expiry_reason(
                days_to_event=days_to_event,
                phase=phase,
                expiration_week=expiration_week,
            ),
            "holiday_adjusted": bool(relevant_expiry["holiday_adjusted"]) if relevant_expiry is not None else False,
            "nominal_expiry_date": (
                relevant_expiry["nominal_expiry_date"].isoformat() if relevant_expiry is not None else None
            ),
            "quarter_code": str(relevant_expiry["quarter_code"]) if relevant_expiry is not None else None,
        }

    @staticmethod
    def _dedupe_macro_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
        deduped: list[CalendarEvent] = []
        seen: set[tuple[str, str, str, str]] = set()
        for event in sorted(events, key=lambda item: (item.event_date, item.title, item.source, item.event_type)):
            key = (
                str(event.event_type).strip().lower(),
                str(event.title).strip().lower(),
                str(event.event_date).strip(),
                str(event.source).strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        return deduped

    def _get_cached_ticker_event_context(self, *, ticker: str, days_ahead: int) -> dict | None:
        if self.ticker_events_cache_ttl_seconds <= 0:
            return None
        cache_key = (ticker, days_ahead)
        with self._lock:
            cached = self._ticker_event_context_cache.get(cache_key)
            if cached is None:
                return None
            cached_at, context = cached
            if (time.time() - cached_at) > self.ticker_events_cache_ttl_seconds:
                self._ticker_event_context_cache.pop(cache_key, None)
                return None
            return self._clone_ticker_event_context(context)

    def _cache_ticker_event_context(self, *, ticker: str, days_ahead: int, context: dict) -> None:
        if self.ticker_events_cache_ttl_seconds <= 0:
            return
        with self._lock:
            self._ticker_event_context_cache[(ticker, days_ahead)] = (
                time.time(),
                self._clone_ticker_event_context(context),
            )

    def _get_cached_macro_events(self, *, days_ahead: int) -> tuple[list[CalendarEvent], str | None] | None:
        if self.macro_events_cache_ttl_seconds <= 0:
            return None
        with self._lock:
            cached = self._macro_events_cache.get(days_ahead)
            if cached is None:
                return None
            cached_at, events, error = cached
            if (time.time() - cached_at) > self.macro_events_cache_ttl_seconds:
                self._macro_events_cache.pop(days_ahead, None)
                return None
            return (list(events), error)

    def _cache_macro_events(self, *, days_ahead: int, events: list[CalendarEvent], error: str | None) -> None:
        if self.macro_events_cache_ttl_seconds <= 0:
            return
        with self._lock:
            self._macro_events_cache[days_ahead] = (time.time(), list(events), error)

    @staticmethod
    def _clone_ticker_event_context(context: dict) -> dict:
        cloned = dict(context)
        events = context.get("events")
        if isinstance(events, list):
            cloned["events"] = list(events)
        cache = context.get("cache")
        if isinstance(cache, dict):
            cloned["cache"] = dict(cache)
        return cloned

    def _provider_backpressure_key(self, family: str) -> str:
        if family == "calendar_corporate":
            provider_name = self._provider_type_name(self.corporate_provider)
            provider_target = self.settings.ibkr_proxy_base_url or provider_name
            return f"{family}:{provider_target}"
        if family == "calendar_earnings":
            provider_name = self._provider_type_name(self.earnings_provider)
            return f"{family}:{provider_name}"
        if family == "calendar_macro":
            macro_name = self._provider_type_name(self.macro_provider)
            official_name = self._provider_type_name(self.official_macro_provider)
            return f"{family}:{macro_name}:{official_name}"
        return family

    def _provider_cooldown_key(self, family: str) -> str:
        if family == "calendar_corporate":
            provider_name = self._provider_type_name(self.corporate_provider)
            provider_target = self.settings.ibkr_proxy_base_url or provider_name
            return f"{family}:{provider_target}"
        if family == "calendar_earnings":
            provider_name = self._provider_type_name(self.earnings_provider)
            return f"{family}:{provider_name}"
        if family == "calendar_macro_finnhub":
            provider_name = self._provider_type_name(self.macro_provider)
            return f"{family}:{provider_name}"
        if family == "calendar_macro_official":
            provider_name = self._provider_type_name(self.official_macro_provider)
            return f"{family}:{provider_name}"
        return family

    @staticmethod
    def _provider_type_name(provider: object | None) -> str:
        if provider is None:
            return "none"
        return type(provider).__qualname__

    def _cooldown_message_for_provider(self, *, provider_label: str, cooldown_key: str) -> str:
        remaining = self._provider_cooldowns.remaining_seconds(cooldown_key)
        if remaining > 0:
            return f"{provider_label} temporarily cooling down for {remaining:.1f}s after recent transient failures."
        return f"{provider_label} temporarily cooling down after recent transient failures."

    def get_provider_runtime_status(self) -> dict[str, ProviderRuntimeStatusRead]:
        return {
            "corporate": self._provider_runtime_status_entry(
                provider=self.corporate_provider,
                cooldown_key=self._provider_cooldown_key("calendar_corporate"),
                concurrency_limit=self.corporate_max_concurrent_requests,
            ),
            "earnings": self._provider_runtime_status_entry(
                provider=self.earnings_provider,
                cooldown_key=self._provider_cooldown_key("calendar_earnings"),
                concurrency_limit=self.earnings_max_concurrent_requests,
            ),
            "macro_finnhub": self._provider_runtime_status_entry(
                provider=self.macro_provider,
                cooldown_key=self._provider_cooldown_key("calendar_macro_finnhub"),
                concurrency_limit=self.macro_max_concurrent_requests,
            ),
            "macro_official": self._provider_runtime_status_entry(
                provider=self.official_macro_provider,
                cooldown_key=self._provider_cooldown_key("calendar_macro_official"),
                concurrency_limit=self.macro_max_concurrent_requests,
            ),
        }

    def _provider_runtime_status_entry(
        self,
        *,
        provider: object | None,
        cooldown_key: str,
        concurrency_limit: int,
    ) -> ProviderRuntimeStatusRead:
        remaining_seconds = round(self._provider_cooldowns.remaining_seconds(cooldown_key), 1)
        return ProviderRuntimeStatusRead(
            provider=self._provider_type_name(provider),
            configured=provider is not None,
            cooling_down=remaining_seconds > 0,
            cooldown_remaining_seconds=remaining_seconds,
            concurrency_limit=max(int(concurrency_limit), 0),
        )

    @staticmethod
    def _transient_cooldown_seconds_for_calendar_error(exc: CalendarProviderError) -> float | None:
        raw_message = str(exc)
        message = raw_message.lower()
        if "retry_after_seconds" in message:
            retry_after_match = re.search(r"retry_after_seconds['\"]?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", raw_message)
            if retry_after_match is not None:
                return min(max(float(retry_after_match.group(1)) + 0.5, 1.0), 60.0)
            return 5.0
        if "too many requests" in message or "http error 429" in message or "rate limit" in message or "api credits" in message:
            return 65.0
        if "cooling down" in message:
            return 5.0
        if "timeout" in message or "timed out" in message:
            return 15.0
        if (
            "http error 503" in message
            or "service unavailable" in message
            or "gateway unavailable" in message
            or "upstream unavailable" in message
            or "temporarily unavailable" in message
        ):
            return 15.0
        return None

    def _load_earnings_calendar(self) -> list[CalendarEvent]:
        return list(
            self._inflight_calls.run(
                ("earnings_calendar",),
                self._load_earnings_calendar_uncached,
            )
        )

    def _load_earnings_calendar_uncached(self) -> list[CalendarEvent]:
        now = time.time()
        with self._lock:
            if self._earnings_cache is not None:
                cached_at, events = self._earnings_cache
                if self._is_cache_fresh(cached_at, now):
                    return list(events)

        disk_cache = self._read_earnings_cache_file() if self.earnings_provider is not None else None
        if disk_cache is not None:
            with self._lock:
                self._earnings_cache = disk_cache
            cached_at, cached_events = disk_cache
            if self._is_cache_fresh(cached_at, now):
                return list(cached_events)

        if self.earnings_provider is None:
            return []
        earnings_cooldown_key = self._provider_cooldown_key("calendar_earnings")
        if self._provider_cooldowns.is_in_cooldown(earnings_cooldown_key):
            cooldown_error = CalendarProviderError(
                self._cooldown_message_for_provider(
                    provider_label="Earnings calendar provider",
                    cooldown_key=earnings_cooldown_key,
                )
            )
            with self._lock:
                if self._earnings_cache is not None:
                    return list(self._earnings_cache[1])
            if disk_cache is not None:
                return list(disk_cache[1])
            raise cooldown_error

        try:
            events = self._normalize_earnings_events(
                list(
                    self._backpressure_gates.run(
                        self._provider_backpressure_key("calendar_earnings"),
                        limit=self.earnings_max_concurrent_requests,
                        fn=lambda: self.earnings_provider.get_earnings_calendar(horizon="3month"),
                    )
                )
            )
        except CalendarProviderError as exc:
            if (cooldown_seconds := self._transient_cooldown_seconds_for_calendar_error(exc)) is not None:
                self._provider_cooldowns.enter(earnings_cooldown_key, seconds=cooldown_seconds)
            with self._lock:
                if self._earnings_cache is not None:
                    return list(self._earnings_cache[1])
            if disk_cache is not None:
                return list(disk_cache[1])
            raise exc

        with self._lock:
            self._earnings_cache = (now, events)
        self._write_earnings_cache_file(cached_at=now, events=events)
        return list(events)

    def _read_earnings_cache_file(self) -> tuple[float, list[CalendarEvent]] | None:
        if not self.cache_path.exists():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            cached_at = float(payload.get("cached_at") or 0.0)
            raw_events = payload.get("events") or []
            if not isinstance(raw_events, list):
                return None
            events = self._normalize_earnings_events(
                [
                    CalendarEvent(**raw_event)
                    for raw_event in raw_events
                    if isinstance(raw_event, dict)
                ]
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return (cached_at, events)

    def _write_earnings_cache_file(self, *, cached_at: float, events: list[CalendarEvent]) -> None:
        payload = {
            "cached_at": cached_at,
            "provider": "alpha_vantage",
            "events": [event.__dict__ for event in events],
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.cache_path.with_suffix(f"{self.cache_path.suffix}.tmp")
            temporary_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            temporary_path.replace(self.cache_path)
        except OSError:
            return

    def _normalize_earnings_events(self, events: list[CalendarEvent]) -> list[CalendarEvent]:
        normalized: list[CalendarEvent] = []
        for event in events:
            event_date = self._parse_event_date(event.event_date)
            ticker = (event.ticker or "").strip().upper()
            if event_date is None or not ticker:
                continue
            normalized.append(
                CalendarEvent(
                    event_type=event.event_type,
                    title=event.title,
                    event_date=event_date.isoformat(),
                    ticker=ticker,
                    exchange=event.exchange,
                    country=event.country,
                    impact=event.impact,
                    estimate=event.estimate,
                    actual=event.actual,
                    previous=event.previous,
                    currency=event.currency,
                    source=event.source,
                    raw=event.raw,
                )
            )
        normalized.sort(key=lambda item: (item.event_date, item.ticker or ""))
        return normalized

    def _is_cache_fresh(self, cached_at: float, now: float) -> bool:
        return (now - cached_at) <= self.earnings_cache_ttl_seconds

    def _build_earnings_cache_status(self) -> dict:
        if self.earnings_provider is None:
            return {
                "provider": "alpha_vantage",
                "available": False,
                "cached_at": None,
                "age_seconds": None,
                "ttl_seconds": self.earnings_cache_ttl_seconds,
                "stale": False,
            }
        now = time.time()
        cached_at: float | None = None
        with self._lock:
            if self._earnings_cache is not None:
                cached_at = self._earnings_cache[0]
        if cached_at is None:
            disk_cache = self._read_earnings_cache_file()
            if disk_cache is not None:
                cached_at = disk_cache[0]
        if cached_at is None:
            return {
                "provider": "alpha_vantage",
                "available": False,
                "cached_at": None,
                "age_seconds": None,
                "ttl_seconds": self.earnings_cache_ttl_seconds,
                "stale": False,
            }
        age_seconds = max(int(now - cached_at), 0)
        return {
            "provider": "alpha_vantage",
            "available": True,
            "cached_at": datetime.fromtimestamp(cached_at, tz=timezone.utc).isoformat(),
            "age_seconds": age_seconds,
            "ttl_seconds": self.earnings_cache_ttl_seconds,
            "stale": not self._is_cache_fresh(cached_at, now),
        }

    @classmethod
    def _build_quarterly_expiry_schedule(cls, year: int) -> list[dict]:
        schedule: list[dict] = []
        for candidate_year in (year - 1, year, year + 1):
            for month in cls.QUARTERLY_EXPIRY_MONTHS:
                nominal_expiry = cls._nth_weekday_of_month(candidate_year, month, weekday=4, occurrence=3)
                expiry_date = cls._adjust_to_previous_trading_day(nominal_expiry)
                schedule.append(
                    {
                        "quarter_code": f"{candidate_year}Q{((month - 1) // 3) + 1}",
                        "nominal_expiry_date": nominal_expiry,
                        "expiry_date": expiry_date,
                        "holiday_adjusted": expiry_date != nominal_expiry,
                    }
                )
        schedule.sort(key=lambda item: item["expiry_date"])
        return schedule

    @classmethod
    def _adjust_to_previous_trading_day(cls, target_date: date) -> date:
        candidate = target_date
        while not cls._is_us_equity_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    @classmethod
    def _is_us_equity_trading_day(cls, target_date: date) -> bool:
        return target_date.weekday() < 5 and target_date not in cls._us_equity_market_holidays(target_date.year)

    @classmethod
    def _us_equity_market_holidays(cls, year: int) -> set[date]:
        return {
            cls._observed_fixed_holiday(date(year, 1, 1)),
            cls._nth_weekday_of_month(year, 1, weekday=0, occurrence=3),
            cls._nth_weekday_of_month(year, 2, weekday=0, occurrence=3),
            cls._easter_sunday(year) - timedelta(days=2),
            cls._last_weekday_of_month(year, 5, weekday=0),
            cls._observed_fixed_holiday(date(year, 6, 19)),
            cls._observed_fixed_holiday(date(year, 7, 4)),
            cls._nth_weekday_of_month(year, 9, weekday=0, occurrence=1),
            cls._nth_weekday_of_month(year, 11, weekday=3, occurrence=4),
            cls._observed_fixed_holiday(date(year, 12, 25)),
        }

    @staticmethod
    def _observed_fixed_holiday(raw_date: date) -> date:
        if raw_date.weekday() == 5:
            return raw_date - timedelta(days=1)
        if raw_date.weekday() == 6:
            return raw_date + timedelta(days=1)
        return raw_date

    @staticmethod
    def _nth_weekday_of_month(year: int, month: int, *, weekday: int, occurrence: int) -> date:
        first_day_weekday, days_in_month = calendar.monthrange(year, month)
        first_occurrence = 1 + ((weekday - first_day_weekday) % 7)
        day = first_occurrence + ((occurrence - 1) * 7)
        if day > days_in_month:
            raise ValueError(f"weekday occurrence out of range for {year}-{month:02d}")
        return date(year, month, day)

    @staticmethod
    def _last_weekday_of_month(year: int, month: int, *, weekday: int) -> date:
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        delta = (last_day.weekday() - weekday) % 7
        return last_day - timedelta(days=delta)

    @staticmethod
    def _easter_sunday(year: int) -> date:
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = ((19 * a) + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + (2 * e) + (2 * i) - h - k) % 7
        m = (a + (11 * h) + (22 * l)) // 451
        month = (h + l - (7 * m) + 114) // 31
        day = ((h + l - (7 * m) + 114) % 31) + 1
        return date(year, month, day)

    @staticmethod
    def _is_same_expiry_week(reference_date: date, expiry_date: date) -> bool:
        week_start = expiry_date - timedelta(days=expiry_date.weekday())
        week_end = week_start + timedelta(days=4)
        return week_start <= reference_date <= week_end

    @staticmethod
    def _expiry_risk_penalty(*, days_to_event: int | None, phase: str, expiration_week: bool) -> float:
        if phase == "expiry_day":
            return 0.3
        if phase == "tight_pre_expiry_window":
            return 0.22
        if phase == "pre_expiry_window":
            return 0.14
        if phase == "post_expiry_window":
            return 0.05
        if expiration_week and isinstance(days_to_event, int) and days_to_event > 0:
            return 0.08
        return 0.0

    @staticmethod
    def _build_expiry_reason(*, days_to_event: int | None, phase: str, expiration_week: bool) -> str:
        if phase == "expiry_day":
            return (
                "Quarterly derivatives expiry is active today; treat it as an execution-noise and roll-flow context, "
                "not as a directional signal."
            )
        if phase == "tight_pre_expiry_window":
            return (
                f"Quarterly derivatives expiry is {days_to_event} day away; tighten selectivity because roll and "
                "hedging flows can distort short-term execution."
            )
        if phase == "pre_expiry_window":
            return (
                f"Quarterly derivatives expiry is {days_to_event} days away; reduce appetite for marginal entries "
                "because expiry-week noise tends to rise."
            )
        if phase == "post_expiry_window":
            return (
                "The market is in the immediate post-expiry cleanup window; prefer re-evaluation over assuming the "
                "prior noise regime is still informative."
            )
        if expiration_week:
            return (
                "Expiration week is active, but the tighter T-3 to T risk window is not yet active; keep this as a "
                "mild execution-noise overlay only."
            )
        return "No quarterly derivatives expiry window is active."

    @staticmethod
    def _parse_event_date(value: str | None) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    def _event_date_in_window(self, value: str | None, *, from_date: date, to_date: date) -> bool:
        event_date = self._parse_event_date(value)
        if event_date is None:
            return False
        return from_date <= event_date <= to_date


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
    ACTIVE_WATCHLIST_STATES = ("watching", "active")
    TIMING_SAMPLE_LIMIT = 60

    def __init__(
        self,
        failure_analysis_service: object | None = None,
        market_data_service: MarketDataService | None = None,
        calendar_service: CalendarService | None = None,
        news_service: NewsService | None = None,
    ) -> None:
        if failure_analysis_service is None:
            from app.domains.learning.services import FailureAnalysisService

            failure_analysis_service = FailureAnalysisService()
        self.failure_analysis_service = failure_analysis_service
        self.market_data_service = market_data_service or MarketDataService()
        self.calendar_service = calendar_service or CalendarService()
        self.news_service = news_service or NewsService()

    def get_queue(self, session: Session) -> WorkQueueRead:
        items: list[WorkItemRead] = []
        summary = WorkQueueSummaryRead()

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

        self._append_watchlist_reanalysis_items(session, items, summary)

        degraded_strategies = list(session.scalars(select(Strategy).where(Strategy.status == "degraded")).all())
        for strategy in degraded_strategies:
            candidate_versions = [version for version in strategy.versions if version.lifecycle_stage == "candidate"]
            for candidate in candidate_versions:
                items.append(
                    WorkItemRead(
                        priority="P4",
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
                priority="P5",
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
                priority="P6",
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
                priority="P7",
                item_type="research_task",
                reference_id=task.id,
                title=task.title,
                context={"task_type": task.task_type, "strategy_id": task.strategy_id},
            )
            for task in open_research_tasks
        )

        timing_summary = self._build_recent_timing_summary(session)
        for field_name, value in timing_summary.items():
            setattr(summary, field_name, value)
        summary.market_data_provider_status = self.market_data_service.get_provider_runtime_status()
        summary.calendar_provider_status = self.calendar_service.get_provider_runtime_status()
        summary.news_provider_status = self.news_service.get_provider_runtime_status()

        priority_order = {"P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6, "P7": 7}
        items.sort(key=lambda item: (priority_order[item.priority], item.reference_id or 0))
        return WorkQueueRead(total_items=len(items), items=items, summary=summary)

    def _append_watchlist_reanalysis_items(
        self,
        session: Session,
        items: list[WorkItemRead],
        summary: WorkQueueSummaryRead,
    ) -> None:
        now = datetime.now(timezone.utc)
        watchlist_rows = list(
            session.execute(
                select(WatchlistItem, Watchlist)
                .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
                .where(Watchlist.status == "active", WatchlistItem.state.in_(self.ACTIVE_WATCHLIST_STATES))
            ).all()
        )
        earliest_next_due_at: datetime | None = None
        earliest_next_due_ticker: str | None = None

        for item, watchlist in watchlist_rows:
            key_metrics = dict(item.key_metrics or {}) if isinstance(item.key_metrics, dict) else {}
            runtime_state = (
                dict(key_metrics.get("reanalysis_runtime"))
                if isinstance(key_metrics.get("reanalysis_runtime"), dict)
                else {}
            )
            next_reanalysis_at = self._parse_iso_datetime(runtime_state.get("next_reanalysis_at"))
            if next_reanalysis_at is not None:
                summary.runtime_aware_watchlist_items += 1

            if next_reanalysis_at is not None and next_reanalysis_at > now:
                summary.deferred_reanalysis_items += 1
                if earliest_next_due_at is None or next_reanalysis_at < earliest_next_due_at:
                    earliest_next_due_at = next_reanalysis_at
                    earliest_next_due_ticker = item.ticker
                continue

            summary.due_reanalysis_items += 1
            context = {
                "ticker": item.ticker,
                "watchlist_code": watchlist.code,
                "watchlist_name": watchlist.name,
                "item_state": item.state,
                "gate_reason": str(runtime_state.get("last_gate_reason") or "first_review"),
            }
            if next_reanalysis_at is not None:
                context["next_reanalysis_at"] = next_reanalysis_at.isoformat()
            if runtime_state.get("last_evaluated_at"):
                context["last_evaluated_at"] = str(runtime_state.get("last_evaluated_at"))
            if runtime_state.get("check_interval_seconds") is not None:
                context["check_interval_seconds"] = runtime_state.get("check_interval_seconds")
            if runtime_state.get("market_session_label"):
                context["market_session_label"] = str(runtime_state.get("market_session_label"))
            items.append(
                WorkItemRead(
                    priority="P3",
                    item_type="watchlist_reanalysis_due",
                    reference_id=item.id,
                    title=f"Reanalyze {item.ticker}",
                    context=context,
                )
            )

        if earliest_next_due_at is not None:
            summary.next_reanalysis_at = earliest_next_due_at.isoformat()
            summary.next_reanalysis_ticker = earliest_next_due_ticker

    def _build_recent_timing_summary(self, session: Session) -> dict[str, object]:
        recent_signals = list(
            session.scalars(
                select(TradeSignal).order_by(TradeSignal.id.desc()).limit(self.TIMING_SAMPLE_LIMIT)
            ).all()
        )
        total_ms_values: list[float] = []
        decision_context_values: list[float] = []
        reanalysis_gate_values: list[float] = []
        stage_samples: dict[str, list[float]] = {}
        decision_context_stage_samples: dict[str, list[float]] = {}
        latest_signal_at: datetime | None = None

        for signal in recent_signals:
            signal_context = dict(signal.signal_context or {}) if isinstance(signal.signal_context, dict) else {}
            timing_profile = (
                dict(signal_context.get("timing_profile"))
                if isinstance(signal_context.get("timing_profile"), dict)
                else {}
            )
            total_ms = self._coerce_float(timing_profile.get("total_ms"))
            if total_ms is None:
                continue
            total_ms_values.append(total_ms)
            if latest_signal_at is None:
                latest_signal_at = signal.signal_time or signal.created_at
            stage_timings = timing_profile.get("stages_ms") if isinstance(timing_profile.get("stages_ms"), dict) else {}
            for stage_name, raw_value in stage_timings.items():
                stage_ms = self._coerce_float(raw_value)
                if stage_ms is None:
                    continue
                stage_samples.setdefault(str(stage_name), []).append(stage_ms)
            decision_context_ms = self._coerce_float(stage_timings.get("decision_context"))
            if decision_context_ms is not None:
                decision_context_values.append(decision_context_ms)
            reanalysis_gate_ms = self._coerce_float(stage_timings.get("reanalysis_gate"))
            if reanalysis_gate_ms is not None:
                reanalysis_gate_values.append(reanalysis_gate_ms)
            decision_context_timing = (
                dict(timing_profile.get("decision_context_timing"))
                if isinstance(timing_profile.get("decision_context_timing"), dict)
                else {}
            )
            decision_context_stages = (
                decision_context_timing.get("stages_ms")
                if isinstance(decision_context_timing.get("stages_ms"), dict)
                else {}
            )
            for stage_name, raw_value in decision_context_stages.items():
                stage_ms = self._coerce_float(raw_value)
                if stage_ms is None:
                    continue
                decision_context_stage_samples.setdefault(str(stage_name), []).append(stage_ms)

        summary: dict[str, object] = {
            "timing_samples": len(total_ms_values),
            "timing_last_signal_at": (
                latest_signal_at.replace(tzinfo=timezone.utc).isoformat()
                if isinstance(latest_signal_at, datetime) and latest_signal_at.tzinfo is None
                else latest_signal_at.astimezone(timezone.utc).isoformat()
                if isinstance(latest_signal_at, datetime)
                else None
            ),
            "avg_total_ms": round(mean(total_ms_values), 1) if total_ms_values else None,
            "avg_decision_context_ms": round(mean(decision_context_values), 1) if decision_context_values else None,
            "avg_reanalysis_gate_ms": round(mean(reanalysis_gate_values), 1) if reanalysis_gate_values else None,
            "dominant_stage": None,
            "dominant_stage_avg_ms": None,
            "dominant_decision_context_stage": None,
            "dominant_decision_context_stage_avg_ms": None,
        }

        if stage_samples:
            dominant_stage, dominant_stage_avg = max(
                ((stage_name, mean(values)) for stage_name, values in stage_samples.items()),
                key=lambda item: item[1],
            )
            summary["dominant_stage"] = dominant_stage
            summary["dominant_stage_avg_ms"] = round(dominant_stage_avg, 1)

        if decision_context_stage_samples:
            dominant_stage, dominant_stage_avg = max(
                ((stage_name, mean(values)) for stage_name, values in decision_context_stage_samples.items()),
                key=lambda item: item[1],
            )
            summary["dominant_decision_context_stage"] = dominant_stage
            summary["dominant_decision_context_stage_avg_ms"] = round(dominant_stage_avg, 1)

        return summary

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_iso_datetime(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
