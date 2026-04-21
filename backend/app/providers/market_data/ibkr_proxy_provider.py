from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.providers.market_data.base import MarketDataProvider, MarketDataProviderError, MarketSnapshot, OHLCVCandle


class IBKRProxyError(MarketDataProviderError):
    pass


class IBKRProxyProvider(MarketDataProvider):
    _SCANNER_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,9}$")
    _MM_DD_PATTERN = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})\b")

    def __init__(self, base_url: str, api_key: str | None = None, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._contract_cache: dict[str, str] = {}

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        candles = self.get_history(ticker, limit=220)
        conid = self.resolve_conid(ticker)
        snapshot_payload = self._request_json("/marketdata/snapshot", {"conids": conid, "fields": "31"})
        latest_close = candles[-1].close if candles else 0.0
        price = self._extract_snapshot_price(snapshot_payload, fallback=latest_close)
        return self._build_snapshot_from_candles(
            ticker=ticker,
            candles=candles,
            price=price,
        )

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        conid = self.resolve_conid(ticker)
        candles = self._load_daily_history_with_intraday_fallback(conid, limit=limit)
        if not candles:
            raise IBKRProxyError(f"IBKR proxy returned no history for {ticker}")
        return candles[-limit:]

    def resolve_conid(self, ticker: str) -> str:
        normalized = ticker.upper()
        cached = self._contract_cache.get(normalized)
        if cached is not None:
            return cached

        payload = self._request_json(
            "/contracts/search",
            {"symbol": normalized, "name": "true", "secType": "STK"},
        )
        if not isinstance(payload, list):
            raise IBKRProxyError(f"Unexpected contract search payload for {ticker}")

        contract = self._select_contract(payload, normalized)
        if contract is None:
            raise IBKRProxyError(f"IBKR proxy could not resolve a stock contract for {ticker}")

        conid = str(contract.get("conid") or "").strip()
        if not conid or conid == "-1":
            raise IBKRProxyError(f"IBKR proxy returned an invalid contract id for {ticker}")

        self._contract_cache[normalized] = conid
        return conid

    def get_scanner_universe(
        self,
        scan_types: list[str],
        *,
        instrument: str = "STK",
        location: str = "STK.US.MAJOR",
        filters: list[dict] | None = None,
        limit: int = 60,
    ) -> list[str]:
        if limit <= 0:
            return []

        normalized_types = [scan_type.strip().upper() for scan_type in scan_types if scan_type.strip()]
        if not normalized_types:
            return []

        request_filters = [item for item in (filters or []) if isinstance(item, dict)]
        discovered: list[str] = []
        seen: set[str] = set()

        for scan_type in normalized_types:
            payload = self._request_json_post(
                "/scanner/run",
                {
                    "instrument": instrument,
                    "location": location,
                    "type": scan_type,
                    "filter": request_filters,
                },
            )
            for ticker in self._parse_scanner_symbols(payload):
                if ticker in seen:
                    continue
                seen.add(ticker)
                discovered.append(ticker)
                if len(discovered) >= limit:
                    return discovered

        return discovered

    def get_options_sentiment(self, ticker: str, *, sec_type: str = "STK") -> dict:
        normalized_symbol = ticker.strip().upper()
        normalized_sec_type = sec_type.strip().upper() if sec_type else "STK"
        payload = self._request_json(
            f"/options-sentiment/{quote(normalized_symbol, safe='.-')}",
            {"secType": normalized_sec_type},
        )
        if not isinstance(payload, dict):
            raise IBKRProxyError(f"Unexpected options sentiment payload for {ticker}")
        return self._normalize_options_sentiment_payload(
            payload,
            symbol=normalized_symbol,
            sec_type=normalized_sec_type,
        )

    def get_market_overview(self, ticker: str, *, sec_type: str = "STK") -> dict:
        normalized_symbol = ticker.strip().upper()
        normalized_sec_type = sec_type.strip().upper() if sec_type else "STK"
        payload = self._request_json(
            f"/market-overview/{quote(normalized_symbol, safe='.-')}",
            {"secType": normalized_sec_type},
        )
        if not isinstance(payload, dict):
            raise IBKRProxyError(f"Unexpected market overview payload for {ticker}")

        market_signals_payload = payload.get("marketSignals")
        if not isinstance(market_signals_payload, dict):
            market_signals_payload = payload.get("market_signals")
        options_payload = payload.get("optionsSentiment")
        if not isinstance(options_payload, dict):
            options_payload = payload.get("options_sentiment")
        corporate_payload = payload.get("corporateEvents")
        if corporate_payload is None:
            corporate_payload = payload.get("corporate_events")

        provider_source = str(payload.get("providerSource") or payload.get("provider_source") or "").strip()

        return {
            "available": True,
            "symbol": str(payload.get("symbol") or normalized_symbol).strip().upper(),
            "sec_type": str(payload.get("secType") or payload.get("sec_type") or normalized_sec_type).strip().upper(),
            "provider_source": provider_source or "ibkr_proxy_market_overview",
            "market_signals": self._normalize_market_signals_payload(
                market_signals_payload,
                symbol=normalized_symbol,
                sec_type=normalized_sec_type,
            ),
            "options_sentiment": self._normalize_options_sentiment_payload(
                options_payload,
                symbol=normalized_symbol,
                sec_type=normalized_sec_type,
            ),
            "corporate_events": self._normalize_corporate_events(
                corporate_payload,
                symbol=normalized_symbol,
            ),
            "provider_error": str(payload.get("providerError") or payload.get("provider_error") or "").strip() or None,
        }

    def get_options_sentiment_rankings(
        self,
        *,
        basis: str = "volume",
        direction: str = "high",
        instrument: str = "STK",
        location: str = "STK.US.MAJOR",
        limit: int = 20,
    ) -> dict:
        normalized_basis = basis.strip().lower() if basis else "volume"
        normalized_direction = direction.strip().lower() if direction else "high"
        normalized_instrument = instrument.strip().upper() if instrument else "STK"
        normalized_location = location.strip() if location else "STK.US.MAJOR"
        normalized_limit = max(int(limit), 1)
        payload = self._request_json(
            "/options-sentiment/top",
            {
                "basis": normalized_basis,
                "direction": normalized_direction,
                "instrument": normalized_instrument,
                "location": normalized_location,
                "limit": str(normalized_limit),
            },
        )
        if not isinstance(payload, dict):
            raise IBKRProxyError("Unexpected options sentiment ranking payload")

        contracts_payload = payload.get("contracts")
        contracts: list[dict] = []
        if isinstance(contracts_payload, list):
            for item in contracts_payload[:normalized_limit]:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                contracts.append(
                    {
                        "rank": int(item.get("rank") or len(contracts) + 1),
                        "symbol": symbol,
                        "conid": str(item.get("conid") or "").strip() or None,
                        "company_name": str(item.get("companyName") or "").strip() or None,
                        "listing_exchange": str(item.get("listingExchange") or "").strip() or None,
                        "sec_type": str(item.get("secType") or normalized_instrument).strip().upper(),
                        "ratio": self._coerce_float(item.get("ratio") or item.get("rawRatio")),
                        "raw_ratio": str(item.get("rawRatio") or "").strip() or None,
                        "last_price": self._coerce_float(item.get("lastPrice")),
                        "last_price_text": str(item.get("lastPriceText") or "").strip() or None,
                    }
                )

        return {
            "available": True,
            "basis": str(payload.get("basis") or normalized_basis).strip().lower(),
            "direction": str(payload.get("direction") or normalized_direction).strip().lower(),
            "scanner_type": str(payload.get("scannerType") or "").strip() or None,
            "instrument": str(payload.get("instrument") or normalized_instrument).strip().upper(),
            "location": str(payload.get("location") or normalized_location).strip(),
            "scan_data_column_name": str(payload.get("scanDataColumnName") or "").strip() or None,
            "contracts": contracts,
            "provider_error": None,
        }

    @staticmethod
    def _select_contract(payload: list[dict], ticker: str) -> dict | None:
        candidates = [
            item
            for item in payload
            if str(item.get("secType") or "").upper() == "STK" and str(item.get("conid") or "").strip() not in {"", "-1"}
        ]
        if not candidates:
            return None

        exact_symbol = [item for item in candidates if str(item.get("symbol") or "").upper() == ticker]
        if not exact_symbol:
            exact_symbol = candidates

        preferred_exchanges = ("NASDAQ", "NYSE", "ARCA", "BATS", "AMEX", "IEX", "SMART")
        ranked = sorted(
            exact_symbol,
            key=lambda item: (
                0
                if any(exchange in str(item.get("companyHeader") or "").upper() for exchange in preferred_exchanges)
                else 1,
                str(item.get("companyHeader") or ""),
            ),
        )
        return ranked[0]

    def _request_json(self, path: str, params: dict[str, str] | None = None):
        query = f"?{urlencode(params)}" if params else ""
        request = Request(
            f"{self.base_url}{path}{query}",
            headers=self._headers(),
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise IBKRProxyError(self._format_http_error(exc, path)) from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise IBKRProxyError(f"IBKR proxy request failed for {path}: {exc}") from exc

    def _request_json_post(self, path: str, payload: dict):
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                **self._headers(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise IBKRProxyError(self._format_http_error(exc, path)) from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise IBKRProxyError(f"IBKR proxy request failed for {path}: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "trading-research-app/0.1 (+https://localhost)",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    @staticmethod
    def _format_http_error(exc: HTTPError, path: str) -> str:
        detail = exc.reason
        login_url = None
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
                detail = payload.get("detail") or payload.get("message") or payload.get("error") or detail
                login_url = payload.get("login_url") or payload.get("loginUrl")
            elif isinstance(payload, str):
                detail = payload

        message = f"IBKR proxy request failed for {path}: HTTP {exc.code} {detail}"
        if exc.code == 401 and login_url:
            return f"{message}. Interactive login required at {login_url}"
        return message

    def _load_daily_history_with_intraday_fallback(self, conid: str, *, limit: int) -> list[OHLCVCandle]:
        daily_payload = self._request_history_payload(
            conid=conid,
            period=self._period_for_limit(limit),
            bar="1d",
            outside_rth="false",
        )
        candles = self._parse_candles(daily_payload)
        if candles:
            return candles

        intraday_attempts = (
            ("1m", "1h"),
            ("1w", "30min"),
            ("1d", "5min"),
        )
        for period, bar in intraday_attempts:
            intraday_payload = self._request_history_payload(
                conid=conid,
                period=period,
                bar=bar,
                outside_rth="false",
            )
            aggregated = self._aggregate_intraday_payload_to_daily(intraday_payload)
            if aggregated:
                return aggregated
        return []

    def _request_history_payload(self, *, conid: str, period: str, bar: str, outside_rth: str) -> dict:
        payload = self._request_json(
            "/marketdata/history",
            {
                "conid": conid,
                "period": period,
                "bar": bar,
                "outsideRth": outside_rth,
            },
        )
        return payload if isinstance(payload, dict) else {}

    @classmethod
    def _aggregate_intraday_payload_to_daily(cls, payload: dict) -> list[OHLCVCandle]:
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return []

        volume_factor = cls._coerce_float(payload.get("volumeFactor"))
        if volume_factor is None and isinstance(payload.get("meta"), dict):
            volume_factor = cls._coerce_float(payload["meta"].get("volumeFactor"))
        if volume_factor is None or volume_factor <= 0:
            volume_factor = 1.0

        buckets: dict[str, dict] = {}
        order: list[str] = []
        for item in sorted(
            (entry for entry in data if isinstance(entry, dict)),
            key=lambda entry: cls._coerce_float(entry.get("t")) or 0.0,
        ):
            timestamp_value = cls._coerce_float(item.get("t"))
            day = cls._format_timestamp(timestamp_value)
            open_price = cls._coerce_float(item.get("o"))
            high = cls._coerce_float(item.get("h"))
            low = cls._coerce_float(item.get("l"))
            close = cls._coerce_float(item.get("c"))
            volume = cls._coerce_float(item.get("v"))
            if not day or None in {open_price, high, low, close, volume}:
                continue

            if day not in buckets:
                buckets[day] = {
                    "timestamp": day,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume * volume_factor,
                }
                order.append(day)
                continue

            bucket = buckets[day]
            bucket["high"] = max(float(bucket["high"]), high)
            bucket["low"] = min(float(bucket["low"]), low)
            bucket["close"] = close
            bucket["volume"] = float(bucket["volume"]) + (volume * volume_factor)

        return [
            OHLCVCandle(
                timestamp=str(buckets[day]["timestamp"]),
                open=float(buckets[day]["open"]),
                high=float(buckets[day]["high"]),
                low=float(buckets[day]["low"]),
                close=float(buckets[day]["close"]),
                volume=float(buckets[day]["volume"]),
            )
            for day in order
        ]

    @classmethod
    def _build_snapshot_from_candles(cls, *, ticker: str, candles: list[OHLCVCandle], price: float) -> MarketSnapshot:
        if not candles:
            raise IBKRProxyError("IBKR proxy could not build a snapshot because no history or intraday fallback was available")

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        latest_close = closes[-1]
        normalized_price = round(price or latest_close, 2)
        sma_20 = round(cls._sma(closes[-min(len(closes), 20):]), 2)
        sma_50 = round(cls._sma(closes[-min(len(closes), 50):]), 2)
        sma_200 = round(cls._sma(closes[-min(len(closes), 200):]), 2)
        if len(closes) >= 2:
            rsi_14 = round(cls._rsi(closes[-min(len(closes), 15):]), 2)
            atr_14 = round(cls._atr(highs[-min(len(highs), 15):], lows[-min(len(lows), 15):], closes[-min(len(closes), 15):]), 2)
        else:
            rsi_14 = 50.0
            atr_14 = round(max(highs[-1] - lows[-1], normalized_price * 0.02), 2)
        if len(volumes) >= 2:
            baseline_window = volumes[-min(len(volumes), 21):-1]
            baseline_volume = cls._sma(baseline_window) if baseline_window else volumes[-1]
            relative_volume = round(volumes[-1] / max(baseline_volume, 1.0), 2)
        else:
            relative_volume = 1.0
        week_performance = round(((normalized_price / closes[-6]) - 1), 4) if len(closes) >= 6 else 0.0
        month_performance = round(((normalized_price / closes[-22]) - 1), 4) if len(closes) >= 22 else 0.0

        return MarketSnapshot(
            ticker=ticker.upper(),
            price=normalized_price,
            sma_20=sma_20,
            sma_50=sma_50,
            sma_200=sma_200,
            rsi_14=rsi_14,
            relative_volume=relative_volume,
            atr_14=atr_14,
            week_performance=week_performance,
            month_performance=month_performance,
        )

    @classmethod
    def _normalize_options_sentiment_payload(cls, payload: dict | None, *, symbol: str, sec_type: str) -> dict:
        source = payload if isinstance(payload, dict) else {}
        metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
        availability = source.get("availability") if isinstance(source.get("availability"), dict) else {}
        fallback = source.get("fallback") if isinstance(source.get("fallback"), dict) else {}

        requested_fields = [
            str(item).strip()
            for item in availability.get("requestedFields", [])
            if str(item).strip()
        ] if isinstance(availability.get("requestedFields"), list) else []
        returned_fields = [
            str(item).strip()
            for item in availability.get("returnedFields", [])
            if str(item).strip()
        ] if isinstance(availability.get("returnedFields"), list) else []

        put_call_ratio = cls._coerce_float(metrics.get("putCallRatio") if metrics else source.get("putCallRatio"))
        put_call_volume_ratio = cls._coerce_float(
            metrics.get("putCallVolumeRatio") if metrics else source.get("putCallVolumeRatio")
        )
        option_volume_value = metrics.get("optionVolume") if metrics else source.get("optionVolume")
        option_implied_vol_value = (
            metrics.get("optionImpliedVolPct") if metrics else source.get("optionImpliedVolPct")
        )
        last_price_value = metrics.get("lastPrice") if metrics else source.get("lastPrice")

        return {
            "available": bool(source) and bool(source.get("available", True)),
            "symbol": str(source.get("symbol") or symbol).strip().upper(),
            "conid": str(source.get("conid") or "").strip() or None,
            "sec_type": str(source.get("secType") or source.get("sec_type") or sec_type).strip().upper(),
            "company_header": str(source.get("companyHeader") or source.get("company_header") or "").strip() or None,
            "company_name": str(source.get("companyName") or source.get("company_name") or "").strip() or None,
            "last_price": cls._coerce_float(last_price_value),
            "option_implied_vol_pct": cls._coerce_float(option_implied_vol_value),
            "put_call_ratio": put_call_ratio,
            "put_call_volume_ratio": put_call_volume_ratio,
            "option_volume": cls._coerce_float(option_volume_value),
            "put_call_ratio_available": bool(availability.get("putCallRatioAvailable")) or put_call_ratio is not None,
            "put_call_volume_ratio_available": bool(availability.get("putCallVolumeRatioAvailable"))
            or put_call_volume_ratio is not None,
            "option_volume_available": bool(availability.get("optionVolumeAvailable"))
            or option_volume_value is not None,
            "market_data_availability": str(
                availability.get("marketDataAvailability") or source.get("marketDataAvailability") or ""
            ).strip() or None,
            "service_params": availability.get("serviceParams") or source.get("serviceParams"),
            "snapshot_updated_at": availability.get("snapshotUpdatedAt") or source.get("snapshotUpdatedAt"),
            "requested_fields": requested_fields,
            "returned_fields": returned_fields,
            "fallback_reason": str(fallback.get("reason") or source.get("fallbackReason") or "").strip() or None,
            "fallback_top_by_volume_path": str(
                fallback.get("topByVolumePath") or source.get("fallbackTopByVolumePath") or ""
            ).strip() or None,
            "fallback_top_by_open_interest_path": str(
                fallback.get("topByOpenInterestPath") or source.get("fallbackTopByOpenInterestPath") or ""
            ).strip() or None,
            "provider_error": str(source.get("providerError") or source.get("provider_error") or "").strip() or None,
        }

    @classmethod
    def _normalize_market_signals_payload(cls, payload: dict | None, *, symbol: str, sec_type: str) -> dict:
        source = payload if isinstance(payload, dict) else {}
        top_of_book = source.get("topOfBook") if isinstance(source.get("topOfBook"), dict) else {}
        availability = source.get("availability") if isinstance(source.get("availability"), dict) else {}
        return {
            "available": bool(source),
            "symbol": str(source.get("symbol") or symbol).strip().upper(),
            "conid": str(source.get("conid") or "").strip() or None,
            "sec_type": str(source.get("secType") or source.get("sec_type") or sec_type).strip().upper(),
            "company_header": str(source.get("companyHeader") or source.get("company_header") or "").strip() or None,
            "company_name": str(source.get("companyName") or source.get("company_name") or "").strip() or None,
            "last_price": cls._coerce_float(
                source.get("lastPrice") or source.get("price") or top_of_book.get("lastPrice")
            ),
            "close_price": cls._coerce_float(source.get("close") or source.get("closePrice")),
            "net_change": cls._coerce_float(source.get("change") or source.get("netChange")),
            "change_percent": cls._coerce_float(
                str(source.get("changePercent") or source.get("changePct") or "").replace("%", "")
            ),
            "bid": cls._coerce_float(source.get("bid") or top_of_book.get("bidPrice")),
            "ask": cls._coerce_float(source.get("ask") or top_of_book.get("askPrice")),
            "bid_size": cls._coerce_float(source.get("bidSize") or top_of_book.get("bidSize")),
            "ask_size": cls._coerce_float(source.get("askSize") or top_of_book.get("askSize")),
            "open_price": cls._coerce_float(source.get("open")),
            "high_price": cls._coerce_float(source.get("high")),
            "low_price": cls._coerce_float(source.get("low")),
            "volume": cls._coerce_float(source.get("volume") or top_of_book.get("lastSize")),
            "market_data_availability": str(
                source.get("marketDataAvailability") or availability.get("marketDataAvailability") or ""
            ).strip() or None,
            "snapshot_updated_at": source.get("snapshotUpdatedAt") or availability.get("snapshotUpdatedAt"),
            "raw": dict(source) if source else None,
        }

    @classmethod
    def _normalize_corporate_events(cls, payload: object, *, symbol: str) -> list[dict]:
        events: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for item in cls._iter_corporate_event_items(payload):
            normalized = cls._normalize_corporate_event_item(item, symbol=symbol)
            if normalized is None:
                continue
            marker = (
                str(normalized.get("event_type") or "").strip().lower(),
                str(normalized.get("title") or "").strip().lower(),
                str(normalized.get("event_date") or "").strip(),
            )
            if marker in seen:
                continue
            seen.add(marker)
            events.append(normalized)
        events.sort(key=lambda item: (str(item.get("event_date") or "9999-12-31"), str(item.get("title") or "")))
        return events

    @classmethod
    def _iter_corporate_event_items(cls, payload: object) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if not isinstance(payload, dict):
            return []

        items: list[dict] = []
        if cls._looks_like_corporate_event(payload):
            items.append(payload)

        for key in ("next", "upcoming", "events", "items"):
            value = payload.get(key)
            if isinstance(value, dict):
                items.append(value)
            elif isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))

        if items:
            return items

        return [value for value in payload.values() if isinstance(value, dict) and cls._looks_like_corporate_event(value)]

    @classmethod
    def _normalize_corporate_event_item(cls, item: dict, *, symbol: str) -> dict | None:
        title = str(
            item.get("title")
            or item.get("label")
            or item.get("name")
            or item.get("description")
            or item.get("event")
            or "Corporate event"
        ).strip()
        event_date = cls._extract_corporate_event_date(item)
        if not title and not event_date:
            return None

        return {
            "event_type": cls._classify_corporate_event_type(item, fallback_title=title),
            "title": title or "Corporate event",
            "event_date": event_date,
            "ticker": symbol,
            "source": "ibkr_proxy_market_overview",
            "raw": dict(item),
        }

    @classmethod
    def _extract_corporate_event_date(cls, item: dict) -> str:
        for key in ("eventDate", "event_date", "date", "reportDate", "earningsDate", "scheduledDate", "dateTime", "datetime"):
            normalized = cls._normalize_event_date_text(item.get(key))
            if normalized is not None:
                return normalized
        return ""

    @classmethod
    def _normalize_event_date_text(cls, value: object) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None

        iso_date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        if iso_date_match is not None:
            return iso_date_match.group(1)

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed.date().isoformat()

        month_day_match = cls._MM_DD_PATTERN.search(text)
        if month_day_match is None:
            return None

        today = datetime.now(UTC).date()
        try:
            parsed_date = date(today.year, int(month_day_match.group("month")), int(month_day_match.group("day")))
        except ValueError:
            return None
        if parsed_date < today and (today - parsed_date).days > 180:
            parsed_date = date(today.year + 1, parsed_date.month, parsed_date.day)
        return parsed_date.isoformat()

    @staticmethod
    def _looks_like_corporate_event(item: dict) -> bool:
        keys = {"label", "title", "dateTime", "date", "eventDate", "reportDate", "earningsDate"}
        return any(key in item for key in keys)

    @classmethod
    def _classify_corporate_event_type(cls, item: dict, *, fallback_title: str) -> str:
        descriptor = " ".join(
            str(item.get(key) or "")
            for key in ("label", "title", "description", "event", "type", "eventType")
        ).strip().lower()
        if not descriptor:
            descriptor = fallback_title.lower()
        if any(token in descriptor for token in ("erng", "earn", "eps")):
            return "earnings"
        if any(token in descriptor for token in ("dividend", "ex-div", "dvd")):
            return "dividend"
        if "split" in descriptor:
            return "split"
        if any(token in descriptor for token in ("meeting", "shareholder", "agm")):
            return "meeting"
        return "corporate"

    @classmethod
    def _parse_candles(cls, payload: dict) -> list[OHLCVCandle]:
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        volume_factor = cls._coerce_float(payload.get("volumeFactor"))
        if volume_factor is None and isinstance(payload.get("meta"), dict):
            volume_factor = cls._coerce_float(payload["meta"].get("volumeFactor"))
        if volume_factor is None or volume_factor <= 0:
            volume_factor = 1.0

        candles: list[OHLCVCandle] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            timestamp = cls._format_timestamp(item.get("t"))
            open_price = cls._coerce_float(item.get("o"))
            high = cls._coerce_float(item.get("h"))
            low = cls._coerce_float(item.get("l"))
            close = cls._coerce_float(item.get("c"))
            volume = cls._coerce_float(item.get("v"))
            if None in {open_price, high, low, close, volume}:
                continue
            candles.append(
                OHLCVCandle(
                    timestamp=timestamp,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume * volume_factor,
                )
            )
        return candles

    @classmethod
    def _extract_snapshot_price(cls, payload, fallback: float) -> float:
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    price = cls._coerce_float(item.get("31"))
                    if price is not None:
                        return price
        return fallback

    @classmethod
    def _parse_scanner_symbols(cls, payload) -> list[str]:
        if not isinstance(payload, dict):
            return []

        contracts = payload.get("contracts")
        if not isinstance(contracts, list):
            return []

        symbols: list[str] = []
        for item in contracts:
            if not isinstance(item, dict):
                continue
            sec_type = str(item.get("sec_type") or item.get("secType") or "").upper()
            if sec_type and sec_type != "STK":
                continue
            symbol = str(item.get("symbol") or item.get("contract_description_1") or "").strip().upper()
            if not symbol or not cls._SCANNER_SYMBOL_PATTERN.fullmatch(symbol):
                continue
            symbols.append(symbol)
        return symbols

    @staticmethod
    def _period_for_limit(limit: int) -> str:
        if limit <= 22:
            return "1m"
        if limit <= 66:
            return "3m"
        if limit <= 132:
            return "6m"
        if limit <= 252:
            return "1y"
        if limit <= 504:
            return "2y"
        if limit <= 756:
            return "3y"
        return "5y"

    @staticmethod
    def _format_timestamp(value) -> str:
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=UTC).date().isoformat()
        except (TypeError, ValueError, OSError):
            return str(value or "")

    @staticmethod
    def _coerce_float(value) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _sma(values: list[float]) -> float:
        return sum(values) / max(len(values), 1)

    @staticmethod
    def _rsi(closes: list[float]) -> float:
        gains = []
        losses = []
        for idx in range(1, len(closes)):
            delta = closes[idx] - closes[idx - 1]
            gains.append(max(delta, 0.0))
            losses.append(abs(min(delta, 0.0)))

        avg_gain = sum(gains) / max(len(gains), 1)
        avg_loss = sum(losses) / max(len(losses), 1)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _atr(highs: list[float], lows: list[float], closes: list[float]) -> float:
        true_ranges = []
        for idx in range(1, len(highs)):
            true_ranges.append(
                max(
                    highs[idx] - lows[idx],
                    abs(highs[idx] - closes[idx - 1]),
                    abs(lows[idx] - closes[idx - 1]),
                )
            )
        return sum(true_ranges) / max(len(true_ranges), 1)
