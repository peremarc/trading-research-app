from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class WebResearchError(RuntimeError):
    pass


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: str | None = None
    source: str = "unknown"


@dataclass
class WebPage:
    url: str
    title: str | None
    text: str
    source: str = "unknown"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._title_chunks: list[str] = []
        self._text_chunks: list[str] = []
        self._ignored_stack: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self._ignored_stack.append(tag)
        if tag == "title":
            self._in_title = True
        if tag in {"p", "br", "article", "section", "div", "li", "h1", "h2", "h3", "h4"}:
            self._text_chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if self._ignored_stack and self._ignored_stack[-1] == tag:
            self._ignored_stack.pop()
        if tag in {"p", "article", "section", "div", "li"}:
            self._text_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_stack:
            return
        cleaned = self._normalize_text(data)
        if not cleaned:
            return
        if self._in_title:
            self._title_chunks.append(cleaned)
        self._text_chunks.append(cleaned)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def title(self) -> str | None:
        title = " ".join(self._title_chunks).strip()
        return title or None

    def text(self) -> str:
        raw = " ".join(self._text_chunks)
        lines = [re.sub(r"\s+", " ", part).strip() for part in raw.splitlines()]
        cleaned_lines = [part for part in lines if part]
        return "\n".join(cleaned_lines)


class DuckDuckGoSearchProvider:
    def __init__(self, *, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def search(self, query: str, *, max_results: int = 5) -> list[WebSearchResult]:
        params = urlencode({"q": query})
        request = Request(
            f"https://duckduckgo.com/html/?{params}",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise WebResearchError(f"Web search request failed: {exc}") from exc

        results = self._parse_results(html)
        return results[: max(1, min(max_results, 10))]

    @staticmethod
    def _parse_results(html: str) -> list[WebSearchResult]:
        pattern = re.compile(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        results: list[WebSearchResult] = []
        seen_urls: set[str] = set()

        for match in pattern.finditer(html):
            url = unescape(match.group("url")).strip()
            if not url or url in seen_urls:
                continue
            title = DuckDuckGoSearchProvider._strip_tags(match.group("title"))
            if not title:
                continue
            seen_urls.add(url)
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=None,
                    source="duckduckgo",
                )
            )
        return results

    @staticmethod
    def _strip_tags(value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", unescape(text)).strip()


class WebPageFetcher:
    def __init__(self, *, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, url: str, *, max_chars: int = 12000) -> WebPage:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise WebResearchError(f"Unsupported content type for fetch: {content_type or 'unknown'}")
                html = response.read().decode("utf-8", errors="ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise WebResearchError(f"Web fetch request failed: {exc}") from exc

        extractor = _HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.text()
        if not text:
            raise WebResearchError("Web fetch returned no extractable text.")
        return WebPage(
            url=url,
            title=extractor.title(),
            text=text[: max(500, max_chars)],
            source="http_fetch",
        )
