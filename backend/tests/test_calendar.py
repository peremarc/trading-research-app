from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Lock
import time

import pytest

from app.core.config import Settings
from app.domains.learning import api as learning_api
from app.domains.market import api as market_api
from app.domains.market.services import CalendarService
from app.providers.calendar import (
    BEAReleaseScheduleProvider,
    CalendarEvent,
    CalendarProviderError,
    ECBCalendarProvider,
    FREDReleaseDatesProvider,
    FederalReserveCalendarProvider,
    IBKRProxyCorporateEventsProvider,
    OfficialMacroCalendarProvider,
)


class StubCalendarService:
    def list_ticker_events(self, ticker: str, *, days_ahead: int = 21) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                event_type="earnings",
                title=f"Earnings {ticker}",
                event_date="2026-04-24",
                ticker=ticker,
                source="stub",
            )
        ]

    def get_ticker_event_context(self, ticker: str, *, days_ahead: int = 21) -> dict:
        return {
            "ticker": ticker,
            "source": "ibkr_proxy",
            "used_fallback": False,
            "provider_error": None,
            "fallback_reason": None,
            "events": self.list_ticker_events(ticker, days_ahead=days_ahead),
            "cache": {
                "provider": "alpha_vantage",
                "available": True,
                "cached_at": "2026-04-19T08:30:00+00:00",
                "age_seconds": 300,
                "ttl_seconds": 86400,
                "stale": False,
            },
        }

    def list_macro_events(self, *, days_ahead: int = 14) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                event_type="macro",
                title="US CPI",
                event_date="2026-04-20",
                country="US",
                impact="high",
                source="stub",
            )
        ]

    def get_quarterly_expiry_context(self, *, as_of: date | None = None) -> dict:
        del as_of
        return {
            "available": True,
            "source": "stub",
            "quarterly_expiry_date": "2026-06-18",
            "days_to_event": 1,
            "expiration_week": True,
            "pre_expiry_window": True,
            "expiry_day": False,
            "post_expiry_window": False,
            "phase": "tight_pre_expiry_window",
            "risk_penalty": 0.22,
            "reason": "Stub expiry window context.",
        }


class StubEarningsProvider:
    def __init__(self, events: list[CalendarEvent], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls = 0

    def get_earnings_calendar(self, *, symbol: str | None = None, horizon: str = "3month") -> list[CalendarEvent]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.events)


class StubCorporateEventsProvider:
    def __init__(self, events: list[CalendarEvent], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls = 0

    def get_ticker_events(self, *, symbol: str, sec_type: str = "STK") -> list[CalendarEvent]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.events)


class StubIBKRProxyCorporateEventsProvider(IBKRProxyCorporateEventsProvider):
    def __init__(self, payload: dict) -> None:
        super().__init__(base_url="https://example.test")
        self.payload = payload

    def _request_json(self, path: str, params: dict[str, str]) -> dict:
        return dict(self.payload)


class StubOfficialMacroProvider:
    def __init__(self, events: list[CalendarEvent], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.calls = 0

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        self.calls += 1
        del from_date
        del to_date
        if self.error is not None:
            raise self.error
        return list(self.events)


class ConcurrencyTrackingCorporateProvider:
    def __init__(self, *, event_date: str) -> None:
        self.event_date = event_date
        self.calls = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self._lock = Lock()

    def get_ticker_events(self, *, symbol: str, sec_type: str = "STK") -> list[CalendarEvent]:
        del sec_type
        with self._lock:
            self.calls += 1
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(0.05)
            return [
                CalendarEvent(
                    event_type="earnings",
                    title=f"Earnings {symbol}",
                    event_date=self.event_date,
                    ticker=symbol,
                    source="tracked",
                )
            ]
        finally:
            with self._lock:
                self.active_calls -= 1


class ConcurrencyTrackingOfficialMacroProvider:
    def __init__(self, events: list[CalendarEvent]) -> None:
        self.events = events
        self.calls = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self._lock = Lock()

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        del from_date, to_date
        with self._lock:
            self.calls += 1
            self.active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            time.sleep(0.05)
            return list(self.events)
        finally:
            with self._lock:
                self.active_calls -= 1


class StubFedCalendarProvider(FederalReserveCalendarProvider):
    def __init__(self, html: str) -> None:
        super().__init__(url="https://example.test/fed")
        self.html = html

    def _request_text(self, url: str) -> str:
        del url
        return self.html


class StubECBCalendarProvider(ECBCalendarProvider):
    def __init__(self, html: str) -> None:
        super().__init__(url="https://example.test/ecb")
        self.html = html

    def _request_text(self, url: str) -> str:
        del url
        return self.html


class StubBEAReleaseScheduleProvider(BEAReleaseScheduleProvider):
    def __init__(self, html: str) -> None:
        super().__init__(url="https://example.test/bea")
        self.html = html

    def _request_text(self, url: str) -> str:
        del url
        return self.html


class StubFREDReleaseDatesProvider(FREDReleaseDatesProvider):
    def __init__(self, payloads: dict[int, list[dict]], observations: dict[str, list[dict]] | None = None) -> None:
        super().__init__(api_key="demo")
        self.payloads = payloads
        self.observations = observations or {}

    def _request_json(self, path: str, params: dict) -> dict:
        if path == "/release/dates":
            release_id = int(params["release_id"])
            return {"release_dates": list(self.payloads.get(release_id, []))}
        if path == "/series/observations":
            series_id = str(params["series_id"])
            return {"observations": list(self.observations.get(series_id, []))}
        raise AssertionError(f"Unexpected FRED path {path}")


def test_calendar_endpoints_return_stubbed_events(client) -> None:
    original = market_api.calendar_service
    market_api.calendar_service = StubCalendarService()
    try:
        corporate = client.get("/api/v1/calendar/corporate/NVDA")
        corporate_context = client.get("/api/v1/calendar/corporate-context/NVDA")
        macro = client.get("/api/v1/calendar/macro")
    finally:
        market_api.calendar_service = original

    assert corporate.status_code == 200
    assert corporate.json()[0]["title"] == "Earnings NVDA"
    assert corporate_context.status_code == 200
    assert corporate_context.json()["source"] == "ibkr_proxy"
    assert corporate_context.json()["events"][0]["ticker"] == "NVDA"
    assert corporate_context.json()["cache"]["available"] is True
    assert macro.status_code == 200
    assert macro.json()[0]["title"] == "US CPI"


def test_chat_and_tools_can_use_calendar_context(client) -> None:
    original_chat = learning_api.bot_chat_service.calendar_service
    original_tool = learning_api.agent_tool_gateway_service.calendar_service
    stub = StubCalendarService()
    learning_api.bot_chat_service.calendar_service = stub
    learning_api.agent_tool_gateway_service.calendar_service = stub
    try:
        chat = client.post("/api/v1/chat", json={"message": "proximos earnings de NVDA"})
        tool = client.post(
            "/api/v1/agent-tools/execute",
            json={"tool_name": "calendar.get_ticker_events", "arguments": {"ticker": "NVDA"}},
        )
        macro_tool = client.post(
            "/api/v1/agent-tools/execute",
            json={"tool_name": "calendar.get_macro_events", "arguments": {"days_ahead": 7}},
        )
    finally:
        learning_api.bot_chat_service.calendar_service = original_chat
        learning_api.agent_tool_gateway_service.calendar_service = original_tool

    assert chat.status_code == 200
    assert chat.json()["topic"] == "calendar"
    assert "Earnings NVDA" in chat.json()["reply"]

    assert tool.status_code == 200
    assert tool.json()["result"]["events"][0]["ticker"] == "NVDA"

    assert macro_tool.status_code == 200
    assert macro_tool.json()["result"]["events"][0]["title"] == "US CPI"


def test_calendar_service_adjusts_quarterly_expiry_for_juneteenth_2026() -> None:
    service = CalendarService(settings=Settings(market_data_provider="stub"))

    context = service.get_quarterly_expiry_context(as_of=date(2026, 6, 16))

    assert context["quarterly_expiry_date"] == "2026-06-18"
    assert context["nominal_expiry_date"] == "2026-06-19"
    assert context["holiday_adjusted"] is True
    assert context["days_to_event"] == 2
    assert context["pre_expiry_window"] is True
    assert context["expiration_week"] is True
    assert context["source"] == "internal_us_equity_derivatives_expiry_rules_v1"


def test_calendar_service_marks_post_expiry_window_after_event() -> None:
    service = CalendarService(settings=Settings(market_data_provider="stub"))

    context = service.get_quarterly_expiry_context(as_of=date(2026, 6, 19))

    assert context["quarterly_expiry_date"] == "2026-06-18"
    assert context["days_to_event"] == -1
    assert context["post_expiry_window"] is True
    assert context["phase"] == "post_expiry_window"


def test_calendar_service_filters_alpha_vantage_batch_cache_per_ticker(tmp_path) -> None:
    today = date.today()
    provider = StubEarningsProvider(
        [
            CalendarEvent(
                event_type="earnings",
                title="Earnings NVDA",
                event_date=(today + timedelta(days=2)).isoformat(),
                ticker="nvda",
                source="alpha_vantage",
            ),
            CalendarEvent(
                event_type="earnings",
                title="Earnings NVDA Later",
                event_date=(today + timedelta(days=45)).isoformat(),
                ticker="NVDA",
                source="alpha_vantage",
            ),
            CalendarEvent(
                event_type="earnings",
                title="Earnings MSFT",
                event_date=(today + timedelta(days=5)).isoformat(),
                ticker="MSFT",
                source="alpha_vantage",
            ),
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            alpha_vantage_api_key="demo",
            finnhub_api_key="",
            calendar_earnings_cache_ttl_seconds=86400,
        ),
        earnings_provider=provider,
        cache_path=tmp_path / "earnings-calendar.json",
    )

    first_nvda = service.list_ticker_events("nvda", days_ahead=30)
    second_nvda = service.list_ticker_events("NVDA", days_ahead=30)
    msft = service.list_ticker_events("MSFT", days_ahead=30)

    assert provider.calls == 1
    assert [event.title for event in first_nvda] == ["Earnings NVDA"]
    assert [event.title for event in second_nvda] == ["Earnings NVDA"]
    assert [event.title for event in msft] == ["Earnings MSFT"]
    assert service.cache_path.exists()


def test_calendar_service_caches_ticker_event_context_within_ttl() -> None:
    today = date.today()
    corporate_provider = StubCorporateEventsProvider(
        [
            CalendarEvent(
                event_type="earnings",
                title="Earnings NVDA",
                event_date=(today + timedelta(days=3)).isoformat(),
                ticker="NVDA",
                source="ibkr_proxy",
            )
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            calendar_ticker_events_cache_ttl_seconds=300,
        ),
        corporate_provider=corporate_provider,
    )

    first = service.get_ticker_event_context("NVDA", days_ahead=14)
    second = service.get_ticker_event_context("NVDA", days_ahead=14)

    assert first["events"][0].ticker == "NVDA"
    assert second["events"][0].ticker == "NVDA"
    assert corporate_provider.calls == 1


def test_calendar_service_caches_macro_events_within_ttl() -> None:
    official_provider = StubOfficialMacroProvider(
        [
            CalendarEvent(
                event_type="macro",
                title="US CPI",
                event_date=(date.today() + timedelta(days=1)).isoformat(),
                country="US",
                impact="high",
                source="stub",
            )
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            calendar_macro_events_cache_ttl_seconds=300,
        ),
        official_macro_provider=official_provider,
    )

    first = service.list_macro_events(days_ahead=7)
    second = service.list_macro_events(days_ahead=7)

    assert first[0].title == "US CPI"
    assert second[0].title == "US CPI"
    assert official_provider.calls == 1


def test_calendar_service_coalesces_concurrent_macro_event_requests() -> None:
    class SlowOfficialMacroProvider(StubOfficialMacroProvider):
        def __init__(self, events: list[CalendarEvent]) -> None:
            super().__init__(events)
            self._lock = Lock()

        def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
            del from_date, to_date
            with self._lock:
                self.calls += 1
            time.sleep(0.05)
            return list(self.events)

    official_provider = SlowOfficialMacroProvider(
        [
            CalendarEvent(
                event_type="macro",
                title="US CPI",
                event_date=(date.today() + timedelta(days=1)).isoformat(),
                country="US",
                impact="high",
                source="stub",
            )
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            calendar_macro_events_cache_ttl_seconds=300,
        ),
        official_macro_provider=official_provider,
    )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: service.list_macro_events(days_ahead=7), range(4)))

    assert all(result[0].title == "US CPI" for result in results)
    assert official_provider.calls == 1


def test_calendar_service_backpressure_limits_concurrent_corporate_requests_across_instances() -> None:
    event_date = (date.today() + timedelta(days=2)).isoformat()
    provider = ConcurrencyTrackingCorporateProvider(event_date=event_date)
    settings = Settings(
        market_data_provider="stub",
        calendar_ticker_events_cache_ttl_seconds=0,
        calendar_corporate_max_concurrent_requests=1,
    )
    first_service = CalendarService(
        settings=settings,
        corporate_provider=provider,
        earnings_provider=None,
    )
    second_service = CalendarService(
        settings=settings,
        corporate_provider=provider,
        earnings_provider=None,
    )

    requests = [
        ("NVDA", first_service),
        ("MSFT", second_service),
        ("AAPL", first_service),
    ]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda item: item[1].get_ticker_event_context(item[0], days_ahead=21), requests))

    assert [result["events"][0].ticker for result in results] == ["NVDA", "MSFT", "AAPL"]
    assert provider.calls == 3
    assert provider.max_active_calls == 1


def test_calendar_service_backpressure_limits_concurrent_macro_requests_across_instances() -> None:
    provider = ConcurrencyTrackingOfficialMacroProvider(
        [
            CalendarEvent(
                event_type="macro",
                title="US CPI",
                event_date=(date.today() + timedelta(days=1)).isoformat(),
                country="US",
                impact="high",
                source="tracked",
            )
        ]
    )
    settings = Settings(
        market_data_provider="stub",
        calendar_macro_events_cache_ttl_seconds=0,
        calendar_macro_max_concurrent_requests=1,
    )
    first_service = CalendarService(
        settings=settings,
        macro_provider=None,
        official_macro_provider=provider,
    )
    second_service = CalendarService(
        settings=settings,
        macro_provider=None,
        official_macro_provider=provider,
    )

    requests = [
        (7, first_service),
        (8, second_service),
        (9, first_service),
    ]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda item: item[1].list_macro_events(days_ahead=item[0]), requests))

    assert all(result[0].title == "US CPI" for result in results)
    assert provider.calls == 3
    assert provider.max_active_calls == 1


def test_calendar_service_cooldown_skips_repeated_corporate_failures() -> None:
    class RateLimitedCorporateProvider:
        def __init__(self) -> None:
            self.calls = 0

        def get_ticker_events(self, *, symbol: str, sec_type: str = "STK") -> list[CalendarEvent]:
            del symbol, sec_type
            self.calls += 1
            raise CalendarProviderError("IBKR proxy corporate events request failed: HTTP Error 429: Too Many Requests")

    provider = RateLimitedCorporateProvider()
    settings = Settings(
        market_data_provider="stub",
        ibkr_proxy_base_url="https://cooldown-corp-1.test",
        calendar_ticker_events_cache_ttl_seconds=0,
    )
    first_service = CalendarService(settings=settings, corporate_provider=provider, earnings_provider=None)
    second_service = CalendarService(settings=settings, corporate_provider=provider, earnings_provider=None)

    first = first_service.get_ticker_event_context("NVDA", days_ahead=21)
    second = second_service.get_ticker_event_context("MSFT", days_ahead=21)

    assert provider.calls == 1
    assert "429" in str(first["provider_error"])
    assert "cooling down" in str(second["provider_error"]).lower()


def test_calendar_service_cooldown_skips_repeated_earnings_failures(tmp_path) -> None:
    class RateLimitedEarningsProvider:
        def __init__(self) -> None:
            self.calls = 0

        def get_earnings_calendar(self, *, symbol: str | None = None, horizon: str = "3month") -> list[CalendarEvent]:
            del symbol, horizon
            self.calls += 1
            raise CalendarProviderError("Alpha Vantage returned an earnings calendar error. Rate limit exceeded")

    provider = RateLimitedEarningsProvider()
    settings = Settings(
        market_data_provider="stub",
        alpha_vantage_api_key="demo",
        calendar_earnings_cache_ttl_seconds=0,
        calendar_ticker_events_cache_ttl_seconds=0,
    )
    cache_path = tmp_path / "empty-earnings-cache.json"
    first_service = CalendarService(
        settings=settings,
        corporate_provider=None,
        earnings_provider=provider,
        cache_path=cache_path,
    )
    second_service = CalendarService(
        settings=settings,
        corporate_provider=None,
        earnings_provider=provider,
        cache_path=cache_path,
    )

    first = first_service.get_ticker_event_context("NVDA", days_ahead=21)
    second = second_service.get_ticker_event_context("MSFT", days_ahead=21)

    assert provider.calls == 1
    assert "rate limit" in str(first["provider_error"]).lower()
    assert "cooling down" in str(second["provider_error"]).lower()


def test_calendar_service_cooldown_skips_repeated_macro_failures() -> None:
    class RateLimitedOfficialMacroProvider:
        def __init__(self) -> None:
            self.calls = 0

        def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
            del from_date, to_date
            self.calls += 1
            raise CalendarProviderError("Official macro provider request failed: HTTP Error 503: Service Unavailable")

    provider = RateLimitedOfficialMacroProvider()
    settings = Settings(
        market_data_provider="stub",
        calendar_macro_events_cache_ttl_seconds=0,
    )
    first_service = CalendarService(settings=settings, macro_provider=None, official_macro_provider=provider)
    second_service = CalendarService(settings=settings, macro_provider=None, official_macro_provider=provider)

    with pytest.raises(CalendarProviderError, match="503|Service Unavailable"):
        first_service.list_macro_events(days_ahead=7)
    with pytest.raises(CalendarProviderError, match="cooling down"):
        second_service.list_macro_events(days_ahead=9)

    assert provider.calls == 1


def test_calendar_service_falls_back_to_stale_cache_on_alpha_vantage_error(tmp_path) -> None:
    today = date.today()
    cache_path = tmp_path / "earnings-calendar.json"
    seeded_service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            alpha_vantage_api_key="demo",
            finnhub_api_key="",
            calendar_earnings_cache_ttl_seconds=0,
        ),
        earnings_provider=StubEarningsProvider(
            [
                CalendarEvent(
                    event_type="earnings",
                    title="Earnings NVDA",
                    event_date=(today + timedelta(days=3)).isoformat(),
                    ticker="NVDA",
                    source="alpha_vantage",
                )
            ]
        ),
        cache_path=cache_path,
    )
    assert [event.title for event in seeded_service.list_ticker_events("NVDA")] == ["Earnings NVDA"]

    failing_provider = StubEarningsProvider([], error=CalendarProviderError("Alpha Vantage rate limit"))
    fallback_service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            alpha_vantage_api_key="demo",
            finnhub_api_key="",
            calendar_earnings_cache_ttl_seconds=0,
        ),
        earnings_provider=failing_provider,
        cache_path=cache_path,
    )

    events = fallback_service.list_ticker_events("NVDA")

    assert failing_provider.calls == 1
    assert [event.title for event in events] == ["Earnings NVDA"]


def test_ibkr_proxy_corporate_events_provider_parses_payload() -> None:
    today = date.today()
    provider = StubIBKRProxyCorporateEventsProvider(
        {
            "conid": 265598,
            "symbol": "AAPL",
            "next": {"label": "Erng Call", "dateTime": "04/30 Aftr Mkt"},
            "upcoming": {
                "headline": {"label": "Erng Call", "dateTime": "04/30 Aftr Mkt"},
                "analystMeeting": None,
                "earnings": {"dateTime": "04/30 Aftr Mkt"},
                "miscEvent": {"dateTime": "06/08 6 AM"},
            },
            "recent": {
                "analystMeeting": None,
                "earnings": None,
                "miscEvent": {"dateTime": "02/24 6 AM"},
            },
        }
    )

    events = provider.get_ticker_events(symbol="AAPL")

    assert [(event.event_type, event.event_date, event.title) for event in events] == [
        ("misc_event", date(today.year - 1 if date(today.year, 2, 24) > today else today.year, 2, 24).isoformat(), "Corporate event AAPL"),
        ("earnings", date(today.year + 1 if date(today.year, 4, 30) < today else today.year, 4, 30).isoformat(), "Earnings AAPL"),
        ("earnings_call", date(today.year + 1 if date(today.year, 4, 30) < today else today.year, 4, 30).isoformat(), "Earnings call AAPL"),
        ("misc_event", date(today.year + 1 if date(today.year, 6, 8) < today else today.year, 6, 8).isoformat(), "Corporate event AAPL"),
    ]


def test_calendar_service_prefers_ibkr_corporate_events_before_alpha_vantage(tmp_path) -> None:
    today = date.today()
    corporate_provider = StubCorporateEventsProvider(
        [
            CalendarEvent(
                event_type="earnings",
                title="Earnings AAPL",
                event_date=(today + timedelta(days=4)).isoformat(),
                ticker="AAPL",
                source="ibkr_proxy",
            )
        ]
    )
    earnings_provider = StubEarningsProvider(
        [
            CalendarEvent(
                event_type="earnings",
                title="Earnings AAPL alpha",
                event_date=(today + timedelta(days=5)).isoformat(),
                ticker="AAPL",
                source="alpha_vantage",
            )
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="ibkr_proxy",
            ibkr_proxy_base_url="https://example.test",
            alpha_vantage_api_key="demo",
            finnhub_api_key="",
        ),
        corporate_provider=corporate_provider,
        earnings_provider=earnings_provider,
        cache_path=tmp_path / "earnings-calendar.json",
    )

    events = service.list_ticker_events("AAPL", days_ahead=30)

    assert corporate_provider.calls == 1
    assert earnings_provider.calls == 0
    assert [event.title for event in events] == ["Earnings AAPL"]


def test_calendar_service_falls_back_to_alpha_vantage_when_ibkr_corporate_events_fail(tmp_path) -> None:
    today = date.today()
    corporate_provider = StubCorporateEventsProvider([], error=CalendarProviderError("IBKR proxy unavailable"))
    earnings_provider = StubEarningsProvider(
        [
            CalendarEvent(
                event_type="earnings",
                title="Earnings AAPL alpha",
                event_date=(today + timedelta(days=5)).isoformat(),
                ticker="AAPL",
                source="alpha_vantage",
            )
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="ibkr_proxy",
            ibkr_proxy_base_url="https://example.test",
            alpha_vantage_api_key="demo",
            finnhub_api_key="",
        ),
        corporate_provider=corporate_provider,
        earnings_provider=earnings_provider,
        cache_path=tmp_path / "earnings-calendar.json",
    )

    events = service.list_ticker_events("AAPL", days_ahead=30)

    assert corporate_provider.calls == 1
    assert earnings_provider.calls == 1
    assert [event.title for event in events] == ["Earnings AAPL alpha"]


def test_calendar_service_falls_back_to_alpha_vantage_when_ibkr_returns_no_upcoming_events(tmp_path) -> None:
    today = date.today()
    corporate_provider = StubCorporateEventsProvider([])
    earnings_provider = StubEarningsProvider(
        [
            CalendarEvent(
                event_type="earnings",
                title="Earnings TSLA alpha",
                event_date=(today + timedelta(days=3)).isoformat(),
                ticker="TSLA",
                source="alpha_vantage",
            )
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="ibkr_proxy",
            ibkr_proxy_base_url="https://example.test",
            alpha_vantage_api_key="demo",
            finnhub_api_key="",
        ),
        corporate_provider=corporate_provider,
        earnings_provider=earnings_provider,
        cache_path=tmp_path / "earnings-calendar.json",
    )

    events = service.list_ticker_events("TSLA", days_ahead=30)

    assert corporate_provider.calls == 1
    assert earnings_provider.calls == 1
    assert [event.title for event in events] == ["Earnings TSLA alpha"]


def test_official_macro_calendar_provider_parses_fed_ecb_and_bea_sources() -> None:
    today = date.today()
    fomc_start = today + timedelta(days=1)
    fomc_end = fomc_start + timedelta(days=1)
    minutes_date = today + timedelta(days=20)
    pce_date = today + timedelta(days=2)
    gdp_date = today + timedelta(days=7)
    fed_html = f"""
        <h5>Upcoming Dates</h5>
        <p><strong>{fomc_start.strftime("%b.")} {fomc_start.day}-{fomc_end.day}</strong> FOMC Meeting<br><em>Press Conference</em></p>
        <p><strong>{minutes_date.strftime("%b.")} {minutes_date.day}</strong> FOMC Minutes<br>Meeting of {fomc_start.strftime("%b.")} {fomc_start.day}-{fomc_end.day}</p>
        <ul class="list-unstyled"></ul>
    """
    ecb_html = f"""
        <dl>
            <dt>{today.strftime('%d/%m/%Y')}</dt>
            <dd>Governing Council of the ECB: monetary policy meeting in Frankfurt (Day 2), followed by press conference<br></dd>
        </dl>
    """
    bea_html = f"""
        <table>
            <tr class="scheduled-releases-type-press">
                <td class="scheduled-date no-wrap"><div class="release-date">{pce_date.strftime("%B")} {pce_date.day}</div><small class="text-muted">8:30 AM</small></td>
                <td class="release-title views-field views-field-field-scheduled-releases-type">Personal Income and Outlays, Sample Month</td>
            </tr>
            <tr class="scheduled-releases-type-press">
                <td class="scheduled-date no-wrap"><div class="release-date">{gdp_date.strftime("%B")} {gdp_date.day}</div><small class="text-muted">8:30 AM</small></td>
                <td class="release-title views-field views-field-field-scheduled-releases-type">Gross Domestic Product, Sample Quarter</td>
            </tr>
        </table>
    """
    provider = OfficialMacroCalendarProvider(
        fed_provider=StubFedCalendarProvider(fed_html),
        ecb_provider=StubECBCalendarProvider(ecb_html),
        bea_provider=StubBEAReleaseScheduleProvider(bea_html),
    )

    events = provider.get_events(from_date=today, to_date=today + timedelta(days=30))

    assert [event.title for event in events] == [
        "ECB Rate Decision",
        "FOMC Rate Decision",
        "Personal Income and Outlays, Sample Month",
        "Gross Domestic Product, Sample Quarter",
        "FOMC Minutes",
    ]
    assert events[0].source == "ecb"
    assert events[1].source == "federal_reserve"
    assert events[2].source == "bea"


def test_calendar_service_uses_official_macro_provider_without_finnhub() -> None:
    today = date.today()
    official_provider = StubOfficialMacroProvider(
        [
            CalendarEvent(
                event_type="central_bank",
                title="FOMC Rate Decision",
                event_date=(today + timedelta(days=10)).isoformat(),
                country="US",
                impact="high",
                source="federal_reserve",
            ),
            CalendarEvent(
                event_type="macro",
                title="Personal Income and Outlays",
                event_date=(today + timedelta(days=20)).isoformat(),
                country="US",
                impact="high",
                source="bea",
            ),
        ]
    )
    service = CalendarService(
        settings=Settings(
            market_data_provider="stub",
            finnhub_api_key="",
        ),
        macro_provider=None,
        official_macro_provider=official_provider,
    )

    events = service.list_macro_events(days_ahead=45)

    assert official_provider.calls == 1
    assert [event.source for event in events] == ["federal_reserve", "bea"]
    assert events[0].title == "FOMC Rate Decision"


def test_fred_release_dates_provider_maps_bls_macro_events() -> None:
    today = date.today()
    provider = StubFREDReleaseDatesProvider(
        {
            10: [{"date": (today + timedelta(days=3)).isoformat()}],
            46: [{"date": (today + timedelta(days=4)).isoformat()}],
            50: [{"date": (today + timedelta(days=5)).isoformat()}],
        }
    )

    events = provider.get_events(from_date=today, to_date=today + timedelta(days=10))

    assert [event.title for event in events] == [
        "US CPI",
        "US PPI",
        "US Employment Situation",
    ]
    assert all(event.source == "fred" for event in events)
    assert events[0].impact == "high"
    assert events[0].actual is None


def test_fred_release_dates_provider_attaches_actual_values_on_release_day() -> None:
    today = date.today()
    provider = StubFREDReleaseDatesProvider(
        {
            10: [{"date": today.isoformat()}],
            46: [{"date": today.isoformat()}],
            50: [{"date": today.isoformat()}],
        },
        observations={
            "CPIAUCSL": [
                {"realtime_start": today.isoformat(), "date": "2026-03-01", "value": "330.293"},
                {"realtime_start": "2026-03-12", "date": "2026-02-01", "value": "327.460"},
            ],
            "PPIACO": [
                {"realtime_start": today.isoformat(), "date": "2026-03-01", "value": "274.102"},
                {"realtime_start": "2026-03-14", "date": "2026-02-01", "value": "269.296"},
            ],
            "UNRATE": [
                {"realtime_start": today.isoformat(), "date": "2026-03-01", "value": "4.3"},
                {"realtime_start": "2026-03-03", "date": "2026-02-01", "value": "4.4"},
            ],
            "PAYEMS": [
                {"realtime_start": today.isoformat(), "date": "2026-03-01", "value": "158637"},
                {"realtime_start": "2026-03-03", "date": "2026-02-01", "value": "158459"},
            ],
        },
    )

    events = provider.get_events(from_date=today, to_date=today)

    assert events[0].title == "US CPI"
    assert events[0].actual == "330.293 index"
    assert events[0].previous == "327.460 index"
    assert events[1].title == "US PPI"
    assert events[1].actual == "274.102 index"
    assert events[2].title == "US Employment Situation"
    assert events[2].actual == "unemp 4.3% · payroll 158,637k"
    assert events[2].previous == "unemp 4.4% · payroll 158,459k"
