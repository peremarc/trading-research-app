from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class NewsProviderError(RuntimeError):
    pass


@dataclass
class NewsArticle:
    title: str
    description: str | None
    url: str
    source_name: str
    published_at: str
    image: str | None = None


class GNewsProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        language: str,
        country: str,
        max_results: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.country = country
        self.max_results = max_results

    def search(self, query: str, *, max_results: int | None = None) -> list[NewsArticle]:
        limit = max(1, min(max_results or self.max_results, 10))
        params = urlencode(
            {
                "q": query,
                "lang": self.language,
                "country": self.country,
                "max": limit,
                "apikey": self.api_key,
            }
        )
        request = Request(
            f"{self.base_url}/search?{params}",
            headers={
                "Accept": "application/json",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise NewsProviderError(f"GNews request failed: {exc}") from exc

        articles = payload.get("articles")
        if not isinstance(articles, list):
            raise NewsProviderError("GNews returned an invalid payload.")

        return [
            NewsArticle(
                title=str(article.get("title") or ""),
                description=article.get("description"),
                url=str(article.get("url") or ""),
                source_name=str((article.get("source") or {}).get("name") or "unknown"),
                published_at=str(article.get("publishedAt") or ""),
                image=article.get("image"),
            )
            for article in articles
            if article.get("title") and article.get("url")
        ]
