from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class CalendarProviderError(RuntimeError):
    pass


@dataclass
class CalendarEvent:
    event_type: str
    title: str
    event_date: str
    ticker: str | None = None
    exchange: str | None = None
    country: str | None = None
    impact: str | None = None
    estimate: str | None = None
    actual: str | None = None
    previous: str | None = None
    currency: str | None = None
    source: str = "unknown"
    raw: dict | None = None


class FinnhubCalendarProvider:
    def __init__(self, *, api_key: str, base_url: str = "https://finnhub.io/api/v1") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get_earnings_calendar(
        self,
        *,
        from_date: date,
        to_date: date,
        symbol: str | None = None,
    ) -> list[CalendarEvent]:
        params = {
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": self.api_key,
        }
        if symbol:
            params["symbol"] = symbol.upper()
        payload = self._request_json("/calendar/earnings", params)
        items = payload.get("earningsCalendar")
        if not isinstance(items, list):
            raise CalendarProviderError("Finnhub earnings calendar returned an invalid payload.")
        return [
            CalendarEvent(
                event_type="earnings",
                title=f"Earnings {str(item.get('symbol') or '').upper()}",
                event_date=str(item.get("date") or ""),
                ticker=str(item.get("symbol") or "").upper() or None,
                estimate=self._as_text(item.get("epsEstimate")),
                actual=self._as_text(item.get("epsActual")),
                source="finnhub",
                raw=item,
            )
            for item in items
            if item.get("date")
        ]

    def get_economic_calendar(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        payload = self._request_json(
            "/calendar/economic",
            {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "token": self.api_key,
            },
        )
        items = payload.get("economicCalendar")
        if not isinstance(items, list):
            raise CalendarProviderError("Finnhub economic calendar returned an invalid payload.")
        return [
            CalendarEvent(
                event_type="macro",
                title=str(item.get("event") or item.get("indicator") or "Macro event"),
                event_date=str(item.get("date") or ""),
                country=self._as_text(item.get("country")),
                impact=self._as_text(item.get("impact")),
                actual=self._as_text(item.get("actual")),
                previous=self._as_text(item.get("prev")),
                estimate=self._as_text(item.get("estimate")),
                currency=self._as_text(item.get("unit")),
                source="finnhub",
                raw=item,
            )
            for item in items
            if item.get("date")
        ]

    def _request_json(self, path: str, params: dict) -> dict:
        request = Request(
            f"{self.base_url}{path}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CalendarProviderError(f"Finnhub calendar request failed: {exc}") from exc

    @staticmethod
    def _as_text(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
