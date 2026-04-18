from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.providers.market_data.base import MarketDataProvider, MarketDataProviderError, MarketSnapshot, OHLCVCandle


class IBKRProxyError(MarketDataProviderError):
    pass


class IBKRProxyProvider(MarketDataProvider):
    _SCANNER_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,9}$")

    def __init__(self, base_url: str, api_key: str | None = None, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._contract_cache: dict[str, str] = {}

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        candles = self.get_history(ticker, limit=220)
        if len(candles) < 20:
            raise IBKRProxyError("Insufficient candle history returned by IBKR proxy")

        conid = self.resolve_conid(ticker)
        snapshot_payload = self._request_json("/marketdata/snapshot", {"conids": conid, "fields": "31"})

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        latest_close = closes[-1]
        price = self._extract_snapshot_price(snapshot_payload, fallback=latest_close)
        sma_20 = round(self._sma(closes[-20:]), 2)
        sma_50 = round(self._sma(closes[-50:]), 2)
        sma_200 = round(self._sma(closes[-200:]), 2) if len(closes) >= 200 else round(self._sma(closes), 2)
        rsi_14 = round(self._rsi(closes[-15:]), 2)
        relative_volume = round(volumes[-1] / max(self._sma(volumes[-21:-1]), 1.0), 2) if len(volumes) >= 21 else 1.0
        atr_14 = round(self._atr(highs[-15:], lows[-15:], closes[-15:]), 2)
        week_performance = round(((price / closes[-6]) - 1), 4) if len(closes) >= 6 else 0.0
        month_performance = round(((price / closes[-22]) - 1), 4) if len(closes) >= 22 else 0.0

        return MarketSnapshot(
            ticker=ticker.upper(),
            price=round(price, 2),
            sma_20=sma_20,
            sma_50=sma_50,
            sma_200=sma_200,
            rsi_14=rsi_14,
            relative_volume=relative_volume,
            atr_14=atr_14,
            week_performance=week_performance,
            month_performance=month_performance,
        )

    def get_history(self, ticker: str, limit: int = 120) -> list[OHLCVCandle]:
        conid = self.resolve_conid(ticker)
        payload = self._request_json(
            "/marketdata/history",
            {
                "conid": conid,
                "period": self._period_for_limit(limit),
                "bar": "1d",
                "outsideRth": "false",
            },
        )

        candles = self._parse_candles(payload)
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
                detail = payload.get("detail") or payload.get("message") or detail
                login_url = payload.get("login_url") or payload.get("loginUrl")
            elif isinstance(payload, str):
                detail = payload

        message = f"IBKR proxy request failed for {path}: HTTP {exc.code} {detail}"
        if exc.code == 401 and login_url:
            return f"{message}. Interactive login required at {login_url}"
        return message

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
