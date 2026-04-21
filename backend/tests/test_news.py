from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

from app.domains.learning import api as learning_api
from app.domains.market import api as market_api
from app.domains.market.services import NewsService
from app.core.config import Settings
from app.providers.news import NewsArticle, NewsProviderError


class StubNewsService:
    def list_news(self, query: str, *, max_results: int | None = None) -> list[NewsArticle]:
        return [
            NewsArticle(
                title=f"{query} headline",
                description="stubbed article",
                url="https://example.com/article",
                source_name="ExampleWire",
                published_at="2026-04-17T12:00:00Z",
            )
        ]

    def list_news_for_ticker(self, ticker: str, *, max_results: int | None = None) -> list[NewsArticle]:
        return self.list_news(f"{ticker} stock", max_results=max_results)


def test_news_endpoint_returns_articles(client) -> None:
    original = market_api.news_service
    market_api.news_service = StubNewsService()
    try:
        response = client.get("/api/v1/news/NVDA")
    finally:
        market_api.news_service = original

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["title"] == "NVDA stock headline"
    assert payload[0]["source_name"] == "ExampleWire"


def test_bot_chat_can_reply_with_news(client) -> None:
    original = learning_api.bot_chat_service.news_service
    learning_api.bot_chat_service.news_service = StubNewsService()
    try:
        response = client.post("/api/v1/chat", json={"message": "Noticias de NVDA"})
    finally:
        learning_api.bot_chat_service.news_service = original

    assert response.status_code == 200
    payload = response.json()
    assert payload["topic"] == "news"
    assert "NVDA" in payload["reply"]
    assert payload["context"]["articles"][0]["source_name"] == "ExampleWire"


def test_news_service_caches_repeated_queries() -> None:
    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, max_results: int | None = None) -> list[NewsArticle]:
            self.calls += 1
            return [
                NewsArticle(
                    title=f"{query} headline",
                    description="cached article",
                    url="https://example.com/cached",
                    source_name="ExampleWire",
                    published_at="2026-04-17T12:00:00Z",
                )
            ]

    service = NewsService(Settings(gnews_api_key="dummy", gnews_cache_ttl_seconds=300))
    provider = CountingProvider()
    service.provider = provider

    first = service.list_news("NVDA", max_results=3)
    second = service.list_news("NVDA", max_results=3)

    assert len(first) == 1
    assert len(second) == 1
    assert provider.calls == 1


def test_news_service_coalesces_concurrent_queries() -> None:
    class SlowCountingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self._lock = Lock()

        def search(self, query: str, max_results: int | None = None) -> list[NewsArticle]:
            del max_results
            with self._lock:
                self.calls += 1
            time.sleep(0.05)
            return [
                NewsArticle(
                    title=f"{query} headline",
                    description="concurrent cached article",
                    url="https://example.com/concurrent",
                    source_name="ExampleWire",
                    published_at="2026-04-17T12:00:00Z",
                )
            ]

    service = NewsService(Settings(gnews_api_key="dummy", gnews_cache_ttl_seconds=300))
    provider = SlowCountingProvider()
    service.provider = provider

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: service.list_news("NVDA", max_results=3), range(4)))

    assert all(len(result) == 1 for result in results)
    assert provider.calls == 1


def test_news_service_backpressure_limits_concurrent_queries_across_instances() -> None:
    class ConcurrencyTrackingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.max_active = 0
            self.active = 0
            self._lock = Lock()

        def search(self, query: str, max_results: int | None = None) -> list[NewsArticle]:
            del max_results
            with self._lock:
                self.calls += 1
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                return [
                    NewsArticle(
                        title=f"{query} headline",
                        description="backpressure article",
                        url="https://example.com/backpressure",
                        source_name="ExampleWire",
                        published_at="2026-04-17T12:00:00Z",
                    )
                ]
            finally:
                with self._lock:
                    self.active -= 1

    settings = Settings(
        gnews_api_key="dummy",
        gnews_base_url="https://shared-gnews-backpressure.test",
        gnews_cache_ttl_seconds=0,
        gnews_max_concurrent_requests=1,
    )
    provider = ConcurrencyTrackingProvider()
    service_one = NewsService(settings)
    service_two = NewsService(settings)
    service_one.provider = provider
    service_two.provider = provider

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service_one.list_news, "NVDA", max_results=3)
        second = executor.submit(service_two.list_news, "MSFT", max_results=3)
        results = [first.result(), second.result()]

    assert all(len(result) == 1 for result in results)
    assert provider.calls == 2
    assert provider.max_active == 1


def test_news_service_cooldown_skips_repeated_failures() -> None:
    class RateLimitedProvider:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, query: str, max_results: int | None = None) -> list[NewsArticle]:
            del query, max_results
            self.calls += 1
            raise NewsProviderError("GNews request failed: HTTP Error 429: Too Many Requests")

    service = NewsService(
        Settings(
            gnews_api_key="dummy",
            gnews_base_url="https://shared-gnews-cooldown.test",
            gnews_cache_ttl_seconds=0,
            gnews_max_concurrent_requests=1,
        )
    )
    provider = RateLimitedProvider()
    service.provider = provider

    first_message = ""
    second_message = ""
    try:
        service.list_news("NVDA", max_results=3)
    except NewsProviderError as exc:
        first_message = str(exc)
    try:
        service.list_news("NVDA", max_results=3)
    except NewsProviderError as exc:
        second_message = str(exc)

    assert "Too Many Requests" in first_message
    assert "cooling down" in second_message
    assert provider.calls == 1
