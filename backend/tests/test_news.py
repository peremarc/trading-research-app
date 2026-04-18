from app.domains.learning import api as learning_api
from app.domains.market import api as market_api
from app.domains.market.services import NewsService
from app.core.config import Settings
from app.providers.news import NewsArticle


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
