from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from io import StringIO
import json
import re
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


class FREDReleaseDatesProvider:
    _RELEASES = (
        {
            "release_id": 10,
            "title": "US CPI",
            "event_type": "macro",
            "country": "US",
            "impact": "high",
            "observation_series": ("CPIAUCSL",),
        },
        {
            "release_id": 46,
            "title": "US PPI",
            "event_type": "macro",
            "country": "US",
            "impact": "medium",
            "observation_series": ("PPIACO",),
        },
        {
            "release_id": 50,
            "title": "US Employment Situation",
            "event_type": "macro",
            "country": "US",
            "impact": "high",
            "observation_series": ("UNRATE", "PAYEMS"),
        },
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.stlouisfed.org/fred",
        timeout_seconds: int = 15,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._observations_cache: dict[str, list[dict]] = {}

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        events: list[CalendarEvent] = []
        today = date.today()
        for release in self._RELEASES:
            payload = self._request_json(
                "/release/dates",
                {
                    "release_id": release["release_id"],
                    "sort_order": "desc",
                    "limit": 24,
                    "include_release_dates_with_no_data": "true",
                },
            )
            items = payload.get("release_dates")
            if not isinstance(items, list):
                raise CalendarProviderError("FRED release dates returned an invalid payload.")
            for item in items:
                event_date = _parse_iso_date(str(item.get("date") or ""))
                if event_date is None or event_date < from_date or event_date > to_date:
                    continue
                actual, previous, raw_observations = self._build_release_snapshot(release, event_date=event_date) if event_date <= today else (None, None, None)
                events.append(
                    CalendarEvent(
                        event_type=str(release["event_type"]),
                        title=str(release["title"]),
                        event_date=event_date.isoformat(),
                        country=str(release["country"]),
                        impact=str(release["impact"]),
                        actual=actual,
                        previous=previous,
                        source="fred",
                        raw={
                            "release_id": release["release_id"],
                            "release_date": item.get("date"),
                            "observations": raw_observations,
                        },
                    )
                )
        return events

    def _build_release_snapshot(self, release: dict, *, event_date: date) -> tuple[str | None, str | None, dict | None]:
        event_date_text = event_date.isoformat()
        series_ids = tuple(str(item) for item in release.get("observation_series") or ())
        if not series_ids:
            return None, None, None

        observations_by_series = {
            series_id: self._get_series_observations(series_id)
            for series_id in series_ids
        }
        latest_by_series: dict[str, dict] = {}
        previous_by_series: dict[str, dict] = {}

        for series_id, observations in observations_by_series.items():
            if not observations:
                continue
            latest_by_series[series_id] = observations[0]
            previous_by_series[series_id] = observations[1] if len(observations) > 1 else {}

        same_day_release = any(
            str(item.get("realtime_start") or "") == event_date_text
            for item in latest_by_series.values()
        )
        if not same_day_release:
            return None, None, None

        if int(release.get("release_id") or 0) == 50:
            unrate_latest = latest_by_series.get("UNRATE")
            payems_latest = latest_by_series.get("PAYEMS")
            unrate_previous = previous_by_series.get("UNRATE")
            payems_previous = previous_by_series.get("PAYEMS")
            actual = self._format_employment_snapshot(unrate_latest, payems_latest)
            previous = self._format_employment_snapshot(unrate_previous, payems_previous)
        else:
            primary_series = series_ids[0]
            latest = latest_by_series.get(primary_series)
            previous_item = previous_by_series.get(primary_series)
            actual = self._format_single_observation(primary_series, latest)
            previous = self._format_single_observation(primary_series, previous_item)

        raw = {
            series_id: {
                "latest": latest_by_series.get(series_id),
                "previous": previous_by_series.get(series_id),
            }
            for series_id in series_ids
        }
        return actual, previous, raw

    def _get_series_observations(self, series_id: str) -> list[dict]:
        if series_id in self._observations_cache:
            return [dict(item) for item in self._observations_cache[series_id]]
        payload = self._request_json(
            "/series/observations",
            {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": 3,
            },
        )
        observations = payload.get("observations")
        if not isinstance(observations, list):
            raise CalendarProviderError("FRED observations returned an invalid payload.")
        filtered = [dict(item) for item in observations if str(item.get("value") or ".") != "."]
        self._observations_cache[series_id] = filtered
        return [dict(item) for item in filtered]

    @staticmethod
    def _format_single_observation(series_id: str, observation: dict | None) -> str | None:
        if not observation:
            return None
        value = _as_float(str(observation.get("value") or ""))
        if value is None:
            return None
        if series_id in {"CPIAUCSL", "PPIACO"}:
            return f"{value:.3f} index"
        return f"{value:.2f}"

    @staticmethod
    def _format_employment_snapshot(unrate: dict | None, payems: dict | None) -> str | None:
        parts: list[str] = []
        if unrate:
            value = _as_float(str(unrate.get("value") or ""))
            if value is not None:
                parts.append(f"unemp {value:.1f}%")
        if payems:
            value = _as_float(str(payems.get("value") or ""))
            if value is not None:
                parts.append(f"payroll {value:,.0f}k")
        return " · ".join(parts) or None

    def _request_json(self, path: str, params: dict) -> dict:
        request = Request(
            f"{self.base_url}{path}?{urlencode({**params, 'api_key': self.api_key, 'file_type': 'json'})}",
            headers={
                "Accept": "application/json",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CalendarProviderError(f"FRED release dates request failed for {path}: {exc}") from exc


class AlphaVantageCalendarProvider:
    def __init__(self, *, api_key: str, base_url: str = "https://www.alphavantage.co/query") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get_earnings_calendar(
        self,
        *,
        symbol: str | None = None,
        horizon: str = "3month",
    ) -> list[CalendarEvent]:
        params = {
            "function": "EARNINGS_CALENDAR",
            "horizon": horizon,
            "apikey": self.api_key,
        }
        if symbol:
            params["symbol"] = symbol.upper()
        rows = self._request_csv(params)
        return [
            CalendarEvent(
                event_type="earnings",
                title=f"Earnings {ticker}" if ticker else (self._as_text(row.get('name')) or "Earnings event"),
                event_date=event_date,
                ticker=ticker,
                exchange=self._as_text(row.get("exchange")),
                estimate=self._as_text(row.get("estimate") or row.get("epsEstimate")),
                actual=self._as_text(row.get("actual") or row.get("epsActual")),
                previous=self._as_text(row.get("previous") or row.get("epsPrevious")),
                currency=self._as_text(row.get("currency")),
                source="alpha_vantage",
                raw=row,
            )
            for row in rows
            if (event_date := self._as_text(row.get("reportDate") or row.get("report_date") or row.get("date")))
            if (ticker := self._as_text(row.get("symbol") or row.get("ticker")))
        ]

    def _request_csv(self, params: dict) -> list[dict]:
        request = Request(
            f"{self.base_url}?{urlencode(params)}",
            headers={
                "Accept": "text/csv,application/json",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CalendarProviderError(f"Alpha Vantage earnings calendar request failed: {exc}") from exc

        stripped = payload.strip()
        if not stripped:
            return []
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CalendarProviderError("Alpha Vantage returned an invalid earnings calendar payload.") from exc
            message = (
                data.get("Note")
                or data.get("Information")
                or data.get("Error Message")
                or "Alpha Vantage returned an earnings calendar error."
            )
            raise CalendarProviderError(str(message))

        reader = csv.DictReader(StringIO(payload))
        return [dict(row) for row in reader if isinstance(row, dict)]

    @staticmethod
    def _as_text(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class IBKRProxyCorporateEventsProvider:
    _MONTH_DAY_PATTERN = re.compile(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})")

    def __init__(self, *, base_url: str, api_key: str | None = None, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def get_ticker_events(self, *, symbol: str, sec_type: str = "STK") -> list[CalendarEvent]:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            return []
        payload = self._request_json(
            "/corporate-events/next",
            {"symbol": normalized_symbol, "secType": sec_type.strip().upper() or "STK"},
        )
        if not isinstance(payload, dict):
            raise CalendarProviderError("IBKR proxy corporate events returned an invalid payload.")
        return self._parse_events(payload, symbol=normalized_symbol)

    def _parse_events(self, payload: dict, *, symbol: str) -> list[CalendarEvent]:
        items: list[CalendarEvent] = []
        next_event = payload.get("next")
        if isinstance(next_event, dict):
            parsed = self._build_event(
                event_key="next",
                event_payload=next_event,
                symbol=symbol,
                section="next",
                fallback_label="Next corporate event",
            )
            if parsed is not None:
                items.append(parsed)

        for section in ("upcoming", "recent"):
            section_payload = payload.get(section)
            if not isinstance(section_payload, dict):
                continue
            for event_key, event_payload in section_payload.items():
                if not isinstance(event_payload, dict):
                    continue
                parsed = self._build_event(
                    event_key=event_key,
                    event_payload=event_payload,
                    symbol=symbol,
                    section=section,
                    fallback_label=self._default_label_for_key(event_key),
                )
                if parsed is not None:
                    items.append(parsed)

        deduped: list[CalendarEvent] = []
        seen: set[tuple[str, str, str]] = set()
        for event in items:
            key = (
                str(event.event_type).strip().lower(),
                str(event.event_date).strip(),
                str((event.raw or {}).get("dateTime") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(event)
        deduped.sort(key=lambda item: (item.event_date, item.event_type, item.title))
        return deduped

    def _build_event(
        self,
        *,
        event_key: str,
        event_payload: dict,
        symbol: str,
        section: str,
        fallback_label: str,
    ) -> CalendarEvent | None:
        raw_date_time = self._as_text(event_payload.get("dateTime"))
        if raw_date_time is None:
            return None
        event_date = self._normalize_event_date(raw_date_time, section=section)
        if event_date is None:
            return None
        label = self._as_text(event_payload.get("label")) or fallback_label
        event_type = self._normalize_event_type(event_key=event_key, label=label)
        title = self._normalize_title(event_type=event_type, label=label, symbol=symbol)
        return CalendarEvent(
            event_type=event_type,
            title=title,
            event_date=event_date.isoformat(),
            ticker=symbol,
            source="ibkr_proxy",
            raw={
                "section": section,
                "event_key": event_key,
                "label": label,
                "dateTime": raw_date_time,
                **event_payload,
            },
        )

    def _normalize_event_date(self, raw_date_time: str, *, section: str) -> date | None:
        match = self._MONTH_DAY_PATTERN.search(raw_date_time)
        if match is None:
            return None
        month = int(match.group("month"))
        day = int(match.group("day"))
        today = date.today()
        year = today.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if section in {"next", "upcoming"} and candidate < today:
            try:
                candidate = date(year + 1, month, day)
            except ValueError:
                return None
        elif section == "recent" and candidate > today:
            try:
                candidate = date(year - 1, month, day)
            except ValueError:
                return None
        return candidate

    @staticmethod
    def _normalize_event_type(*, event_key: str, label: str) -> str:
        normalized_key = str(event_key).strip().lower()
        normalized_label = str(label).strip().lower()
        if normalized_key == "earnings":
            return "earnings"
        if normalized_key == "analystmeeting":
            return "analyst_meeting"
        if normalized_key == "miscevent":
            return "misc_event"
        if "erng" in normalized_label or "earn" in normalized_label:
            return "earnings_call" if "call" in normalized_label else "earnings"
        if "analyst" in normalized_label:
            return "analyst_meeting"
        return "corporate_event"

    @staticmethod
    def _normalize_title(*, event_type: str, label: str, symbol: str) -> str:
        if event_type == "earnings":
            return f"Earnings {symbol}"
        if event_type == "earnings_call":
            return f"Earnings call {symbol}"
        if event_type == "analyst_meeting":
            return f"Analyst meeting {symbol}"
        if event_type == "misc_event":
            return f"Corporate event {symbol}"
        cleaned_label = " ".join(str(label).strip().split()) or "Corporate event"
        return f"{cleaned_label} {symbol}"

    @staticmethod
    def _default_label_for_key(event_key: str) -> str:
        mapping = {
            "headline": "Headline event",
            "earnings": "Earnings",
            "analystMeeting": "Analyst meeting",
            "miscEvent": "Corporate event",
        }
        return mapping.get(event_key, "Corporate event")

    def _request_json(self, path: str, params: dict[str, str]) -> dict:
        request = Request(
            f"{self.base_url}{path}?{urlencode(params)}",
            headers=self._headers(),
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise CalendarProviderError(self._format_http_error(exc, path)) from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise CalendarProviderError(f"IBKR proxy corporate events request failed for {path}: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "trading-research-app/0.1",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    @staticmethod
    def _format_http_error(exc: HTTPError, path: str) -> str:
        detail = exc.reason
        try:
            raw_body = exc.read().decode("utf-8")
        except Exception:
            raw_body = ""
        if raw_body:
            try:
                payload = json.loads(raw_body)
            except ValueError:
                payload = raw_body
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("message") or detail
            elif isinstance(payload, str):
                detail = payload
        return f"IBKR proxy corporate events request failed for {path}: HTTP {exc.code} {detail}"

    @staticmethod
    def _as_text(value) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class FederalReserveCalendarProvider:
    _MONTH_MAP = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    _PARAGRAPH_PATTERN = re.compile(r"<p>(?P<body>.*?)</p>", re.S | re.I)
    _DATE_PATTERN = re.compile(
        r"(?P<month>[A-Za-z]+)\.?\s+(?P<start>\d{1,2})(?:-(?P<end>\d{1,2}))?(?:,\s*(?P<year>\d{4}))?",
        re.I,
    )

    def __init__(
        self,
        *,
        url: str = "https://www.federalreserve.gov/monetarypolicy.htm",
        timeout_seconds: int = 15,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        html = self._request_text(self.url)
        match = re.search(r"Upcoming Dates</h5>(?P<body>.*?)<ul class=\"list-unstyled\">", html, re.S | re.I)
        if match is None:
            raise CalendarProviderError("Federal Reserve monetary policy page did not expose an Upcoming Dates block.")

        events: list[CalendarEvent] = []
        for paragraph_match in self._PARAGRAPH_PATTERN.finditer(match.group("body")):
            body = paragraph_match.group("body")
            date_match = self._DATE_PATTERN.search(body)
            if date_match is None:
                continue
            event_date = self._parse_date_text(date_match, anchor_date=from_date)
            if event_date is None or event_date < from_date or event_date > to_date:
                continue

            normalized = _collapse_html_text(body)
            lines = [line.strip() for line in normalized.split("\n") if line.strip()]
            if not lines:
                continue
            lines[0] = self._DATE_PATTERN.sub("", lines[0], count=1).strip()
            lines = [line for line in lines if line]
            if not lines:
                continue

            headline = lines[0]
            if headline == "FOMC Meeting":
                events.append(
                    CalendarEvent(
                        event_type="central_bank",
                        title="FOMC Rate Decision",
                        event_date=event_date.isoformat(),
                        country="US",
                        impact="high",
                        source="federal_reserve",
                        raw={"headline": headline, "lines": lines},
                    )
                )
            elif headline == "FOMC Minutes":
                events.append(
                    CalendarEvent(
                        event_type="central_bank",
                        title="FOMC Minutes",
                        event_date=event_date.isoformat(),
                        country="US",
                        impact="medium",
                        source="federal_reserve",
                        raw={"headline": headline, "lines": lines},
                    )
                )
        return events

    def _parse_date_text(self, match: re.Match[str], *, anchor_date: date) -> date | None:
        month_token = str(match.group("month") or "").strip().lower().rstrip(".")
        month = self._MONTH_MAP.get(month_token)
        if month is None:
            return None
        day = int(match.group("end") or match.group("start"))
        year = int(match.group("year") or anchor_date.year)
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if match.group("year") is None and candidate < anchor_date:
            try:
                candidate = date(year + 1, month, day)
            except ValueError:
                return None
        return candidate

    def _request_text(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (compatible; trading-research-app/0.1)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CalendarProviderError(f"Federal Reserve calendar request failed: {exc}") from exc


class ECBCalendarProvider:
    _ENTRY_PATTERN = re.compile(
        r"<dt>\s*(?P<date>\d{2}/\d{2}/\d{4})\s*</dt>\s*<dd>\s*(?P<desc>.*?)<br>\s*</dd>",
        re.S | re.I,
    )

    def __init__(
        self,
        *,
        url: str = "https://www.ecb.europa.eu/events/calendar/mgcgc/html/index.en.html",
        timeout_seconds: int = 15,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        html = self._request_text(self.url)
        events: list[CalendarEvent] = []
        for match in self._ENTRY_PATTERN.finditer(html):
            event_date = _parse_slash_date(match.group("date"))
            if event_date is None or event_date < from_date or event_date > to_date:
                continue
            description = _collapse_html_text(match.group("desc")).replace("\n", " ").strip()
            normalized = description.lower()
            if "monetary policy meeting" not in normalized or "press conference" not in normalized:
                continue
            events.append(
                CalendarEvent(
                    event_type="central_bank",
                    title="ECB Rate Decision",
                    event_date=event_date.isoformat(),
                    country="EA",
                    impact="high",
                    source="ecb",
                    raw={"description": description},
                )
            )
        return events

    def _request_text(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (compatible; trading-research-app/0.1)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CalendarProviderError(f"ECB calendar request failed: {exc}") from exc


class BEAReleaseScheduleProvider:
    _ROW_PATTERN = re.compile(
        r"<tr class=\"scheduled-releases-type-[^\"]+\">.*?"
        r"<div class=\"release-date\">(?P<date>[^<]+)</div>\s*"
        r"<small class=\"text-muted\">(?P<time>[^<]+)</small>.*?"
        r"<td class=\"release-title[^\"]*\">(?P<title>.*?)</td>",
        re.S | re.I,
    )
    _MONTH_MAP = FederalReserveCalendarProvider._MONTH_MAP

    def __init__(
        self,
        *,
        url: str = "https://www.bea.gov/news/schedule/full",
        timeout_seconds: int = 15,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        html = self._request_text(self.url)
        events: list[CalendarEvent] = []
        for match in self._ROW_PATTERN.finditer(html):
            title = _collapse_html_text(match.group("title")).replace("\n", " ").strip()
            if not self._is_relevant_title(title):
                continue
            event_date = self._parse_month_day(match.group("date"), anchor_date=from_date)
            if event_date is None or event_date < from_date or event_date > to_date:
                continue
            impact = "high" if "Gross Domestic Product" in title or "Personal Income and Outlays" in title else "medium"
            events.append(
                CalendarEvent(
                    event_type="macro",
                    title=title,
                    event_date=event_date.isoformat(),
                    country="US",
                    impact=impact,
                    source="bea",
                    raw={"time": str(match.group("time")).strip()},
                )
            )
        return events

    @staticmethod
    def _is_relevant_title(title: str) -> bool:
        return any(
            marker in title
            for marker in [
                "Gross Domestic Product",
                "Personal Income and Outlays",
            ]
        )

    def _parse_month_day(self, raw_text: str, *, anchor_date: date) -> date | None:
        match = re.search(r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})", raw_text.strip())
        if match is None:
            return None
        month = self._MONTH_MAP.get(str(match.group("month")).strip().lower().rstrip("."))
        if month is None:
            return None
        day = int(match.group("day"))
        year = anchor_date.year
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate < anchor_date:
            try:
                candidate = date(year + 1, month, day)
            except ValueError:
                return None
        return candidate

    def _request_text(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (compatible; trading-research-app/0.1)",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise CalendarProviderError(f"BEA release schedule request failed: {exc}") from exc


class OfficialMacroCalendarProvider:
    def __init__(
        self,
        *,
        fed_provider: FederalReserveCalendarProvider | None = None,
        ecb_provider: ECBCalendarProvider | None = None,
        bea_provider: BEAReleaseScheduleProvider | None = None,
        fred_provider: FREDReleaseDatesProvider | None = None,
    ) -> None:
        self.fed_provider = fed_provider or FederalReserveCalendarProvider()
        self.ecb_provider = ecb_provider or ECBCalendarProvider()
        self.bea_provider = bea_provider or BEAReleaseScheduleProvider()
        self.fred_provider = fred_provider

    def get_events(self, *, from_date: date, to_date: date) -> list[CalendarEvent]:
        combined: list[CalendarEvent] = []
        errors: list[str] = []
        providers = [self.fed_provider, self.ecb_provider, self.bea_provider]
        if self.fred_provider is not None:
            providers.append(self.fred_provider)
        for provider in providers:
            try:
                combined.extend(provider.get_events(from_date=from_date, to_date=to_date))
            except CalendarProviderError as exc:
                errors.append(str(exc))
        deduped = _dedupe_calendar_events(combined)
        if deduped:
            return deduped
        if errors:
            raise CalendarProviderError(" | ".join(errors))
        return []


def _collapse_html_text(raw_text: str) -> str:
    normalized = raw_text.replace("<br />", "\n").replace("<br/>", "\n").replace("<br>", "\n")
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = unescape(normalized)
    return re.sub(r"[ \t\r\f\v]+", " ", normalized)


def _parse_slash_date(raw_text: str) -> date | None:
    try:
        return datetime.strptime(raw_text.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_iso_date(raw_text: str) -> date | None:
    try:
        return date.fromisoformat(raw_text.strip())
    except ValueError:
        return None


def _as_float(raw_value: str) -> float | None:
    try:
        return float(raw_value.strip())
    except (AttributeError, ValueError):
        return None


def _dedupe_calendar_events(events: list[CalendarEvent]) -> list[CalendarEvent]:
    deduped: list[CalendarEvent] = []
    seen: set[tuple[str, str, str, str]] = set()
    for event in sorted(events, key=lambda item: (item.event_date, item.event_type, item.title, item.source)):
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
