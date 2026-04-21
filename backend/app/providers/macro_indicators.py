from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from app.core.config import Settings, get_settings


class MacroIndicatorError(RuntimeError):
    pass


@dataclass
class MacroIndicatorSnapshot:
    key: str
    label: str
    value: float | None
    unit: str | None
    previous_value: float | None
    change: float | None
    change_pct: float | None
    as_of: str | None
    source: str
    status: str = "available"
    interpretation: str | None = None
    detail: str | None = None

    def to_payload(self) -> dict:
        return asdict(self)


class YahooFinanceMacroProvider:
    def __init__(
        self,
        *,
        base_url: str = "https://query1.finance.yahoo.com/v8/finance/chart",
        timeout_seconds: int = 12,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_index_snapshot(
        self,
        *,
        symbol: str,
        key: str,
        label: str,
        detail: str | None = None,
        unit: str | None = "pts",
    ) -> MacroIndicatorSnapshot:
        request = Request(
            f"{self.base_url}/{symbol}?interval=1d&range=5d",
            headers={
                "Accept": "application/json",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MacroIndicatorError(f"Yahoo Finance request failed for {symbol}: {exc}") from exc

        try:
            meta = payload["chart"]["result"][0]["meta"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MacroIndicatorError(f"Yahoo Finance returned an invalid payload for {symbol}.") from exc

        value = _as_float(meta.get("regularMarketPrice"))
        previous_value = _as_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
        if value is None:
            raise MacroIndicatorError(f"Yahoo Finance did not return a market price for {symbol}.")
        change = value - previous_value if previous_value is not None else None
        change_pct = ((change / previous_value) * 100.0) if change is not None and previous_value not in (None, 0.0) else None
        return MacroIndicatorSnapshot(
            key=key,
            label=label,
            value=value,
            unit=unit,
            previous_value=previous_value,
            change=change,
            change_pct=change_pct,
            as_of=_coerce_timestamp(meta.get("regularMarketTime")),
            source="yahoo_finance",
            detail=detail,
        )


class TreasuryYieldCurveProvider:
    def __init__(
        self,
        *,
        url: str = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve",
        timeout_seconds: int = 12,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def fetch_latest_10y(self) -> MacroIndicatorSnapshot:
        request = Request(
            self.url,
            headers={
                "Accept": "application/xml,text/xml",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise MacroIndicatorError(f"US Treasury yield curve request failed: {exc}") from exc

        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise MacroIndicatorError("US Treasury yield curve payload is invalid XML.") from exc

        rows: list[tuple[str, float]] = []
        for entry in root.iter():
            if _xml_local_name(entry.tag) != "entry":
                continue
            event_date: str | None = None
            value: float | None = None
            for child in entry.iter():
                local_name = _xml_local_name(child.tag)
                if local_name == "NEW_DATE" and child.text:
                    event_date = child.text.strip()
                elif local_name == "BC_10YEAR" and child.text:
                    value = _as_float(child.text)
            if event_date and value is not None:
                rows.append((event_date, value))

        if not rows:
            raise MacroIndicatorError("US Treasury yield curve feed did not contain a 10Y series.")

        rows.sort(key=lambda item: item[0])
        latest_date, latest_value = rows[-1]
        previous_value = rows[-2][1] if len(rows) > 1 else None
        change = latest_value - previous_value if previous_value is not None else None
        change_pct = ((change / previous_value) * 100.0) if change is not None and previous_value not in (None, 0.0) else None
        return MacroIndicatorSnapshot(
            key="us10y",
            label="US 10Y",
            value=latest_value,
            unit="%",
            previous_value=previous_value,
            change=change,
            change_pct=change_pct,
            as_of=_coerce_dateish(latest_date),
            source="us_treasury",
            detail="Cierre oficial del Treasury a 10 anos",
        )


class CNNFearGreedProvider:
    _DATA_URL_PATTERN = re.compile(r'data-data-url="(?P<url>[^"]+fearandgreed[^"]+)"')

    def __init__(
        self,
        *,
        page_url: str = "https://edition.cnn.com/markets/fear-and-greed",
        timeout_seconds: int = 12,
    ) -> None:
        self.page_url = page_url
        self.timeout_seconds = timeout_seconds

    def fetch(self) -> MacroIndicatorSnapshot:
        html = self._request_text(
            self.page_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "trading-research-app/0.1",
            },
        )
        match = self._DATA_URL_PATTERN.search(html)
        if match is None:
            raise MacroIndicatorError("CNN Fear & Greed page did not expose its graph data URL.")

        data_url = match.group("url").replace("&amp;", "&")
        payload_text = self._request_text(
            data_url,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Referer": self.page_url,
                "User-Agent": "Mozilla/5.0 (compatible; trading-research-app/0.1)",
            },
        )
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise MacroIndicatorError("CNN Fear & Greed graph payload is not valid JSON.") from exc

        raw = payload.get("fear_and_greed") if isinstance(payload, dict) and isinstance(payload.get("fear_and_greed"), dict) else payload
        if not isinstance(raw, dict):
            raise MacroIndicatorError("CNN Fear & Greed graph payload is malformed.")

        score = _as_float(raw.get("score"))
        if score is None:
            raise MacroIndicatorError("CNN Fear & Greed graph payload did not include a score.")
        previous_value = _as_float(raw.get("previous_close") or raw.get("previousClose"))
        change = score - previous_value if previous_value is not None else None
        change_pct = ((change / previous_value) * 100.0) if change is not None and previous_value not in (None, 0.0) else None
        rating = str(raw.get("rating") or _fear_greed_rating(score))
        return MacroIndicatorSnapshot(
            key="fear_greed",
            label="CNN Fear & Greed",
            value=score,
            unit="score",
            previous_value=previous_value,
            change=change,
            change_pct=change_pct,
            as_of=_coerce_timestamp(raw.get("timestamp")),
            source="cnn",
            interpretation=rating,
            detail="Sentimiento agregado de mercado",
        )

    def _request_text(self, url: str, *, headers: dict[str, str]) -> str:
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code == 418:
                raise MacroIndicatorError("CNN Fear & Greed bloquea esta consulta automatizada (HTTP 418).") from exc
            raise MacroIndicatorError(f"CNN Fear & Greed request failed: HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise MacroIndicatorError(f"CNN Fear & Greed request failed: {exc}") from exc


class MacroIndicatorsService:
    _cache_payload: list[dict] | None = None
    _cache_until: datetime | None = None

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fear_greed_provider: CNNFearGreedProvider | None = None,
        yahoo_provider: YahooFinanceMacroProvider | None = None,
        treasury_provider: TreasuryYieldCurveProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.enabled = bool(self.settings.macro_indicators_enabled)
        self.cache_ttl_seconds = max(int(self.settings.macro_indicators_cache_ttl_seconds), 0)
        self.fear_greed_provider = fear_greed_provider or CNNFearGreedProvider(
            timeout_seconds=self.settings.macro_indicators_request_timeout_seconds,
        )
        self.yahoo_provider = yahoo_provider or YahooFinanceMacroProvider(
            timeout_seconds=self.settings.macro_indicators_request_timeout_seconds,
        )
        self.treasury_provider = treasury_provider or TreasuryYieldCurveProvider(
            timeout_seconds=self.settings.macro_indicators_request_timeout_seconds,
        )

    def list_indicators(self) -> list[dict]:
        if not self.enabled:
            return []

        now = datetime.now(UTC)
        if self.__class__._cache_payload is not None and self.__class__._cache_until is not None and now < self.__class__._cache_until:
            return [dict(item) for item in self.__class__._cache_payload]

        indicators = [
            self._build_fear_greed_indicator(),
            self._build_vix_indicator(),
            self._build_us10y_indicator(),
            self._build_gold_indicator(),
            self._build_oil_indicator(),
            self._build_copper_indicator(),
        ]
        self.__class__._cache_payload = [dict(item) for item in indicators]
        self.__class__._cache_until = now + timedelta(seconds=self.cache_ttl_seconds)
        return [dict(item) for item in indicators]

    def _build_fear_greed_indicator(self) -> dict:
        try:
            snapshot = self.fear_greed_provider.fetch()
            snapshot.interpretation = snapshot.interpretation or _fear_greed_rating(snapshot.value)
        except MacroIndicatorError as exc:
            return self._unavailable_indicator(
                key="fear_greed",
                label="CNN Fear & Greed",
                source="cnn",
                detail=str(exc),
            )
        return snapshot.to_payload()

    def _build_vix_indicator(self) -> dict:
        try:
            snapshot = self.yahoo_provider.fetch_index_snapshot(
                symbol="^VIX",
                key="vix",
                label="VIX",
                detail="Volatilidad implícita del S&P 500",
            )
            if snapshot.value is not None:
                snapshot.interpretation = _interpret_vix(snapshot.value)
        except MacroIndicatorError as exc:
            return self._unavailable_indicator(
                key="vix",
                label="VIX",
                source="yahoo_finance",
                detail=str(exc),
            )
        return snapshot.to_payload()

    def _build_us10y_indicator(self) -> dict:
        try:
            snapshot = self.treasury_provider.fetch_latest_10y()
        except MacroIndicatorError as treasury_exc:
            try:
                snapshot = self.yahoo_provider.fetch_index_snapshot(
                    symbol="^TNX",
                    key="us10y",
                    label="US 10Y",
                    detail="Rendimiento de mercado del Treasury a 10 anos",
                )
                snapshot.unit = "%"
                snapshot.detail = f"{snapshot.detail} · fallback tras Treasury: {treasury_exc}"
            except MacroIndicatorError as yahoo_exc:
                return self._unavailable_indicator(
                    key="us10y",
                    label="US 10Y",
                    source="us_treasury",
                    detail=f"{treasury_exc} | Yahoo fallback failed: {yahoo_exc}",
                )
        if snapshot.value is not None:
            snapshot.interpretation = _interpret_us10y(snapshot.value)
        return snapshot.to_payload()

    def _build_gold_indicator(self) -> dict:
        return self._build_yahoo_commodity_indicator(
            symbol="GC=F",
            key="gold",
            label="Gold",
            detail="Futuros del oro como termometro de refugio e inflacion",
            interpretation_builder=_interpret_gold,
        )

    def _build_oil_indicator(self) -> dict:
        return self._build_yahoo_commodity_indicator(
            symbol="CL=F",
            key="oil",
            label="WTI Crude",
            detail="Futuros del petroleo WTI como proxy de energia e inflacion",
            interpretation_builder=_interpret_oil,
        )

    def _build_copper_indicator(self) -> dict:
        return self._build_yahoo_commodity_indicator(
            symbol="HG=F",
            key="copper",
            label="Copper",
            detail="Futuros del cobre como proxy de ciclo industrial y crecimiento",
            interpretation_builder=_interpret_copper,
        )

    def _build_yahoo_commodity_indicator(
        self,
        *,
        symbol: str,
        key: str,
        label: str,
        detail: str,
        interpretation_builder,
    ) -> dict:
        try:
            snapshot = self.yahoo_provider.fetch_index_snapshot(
                symbol=symbol,
                key=key,
                label=label,
                detail=detail,
                unit="USD",
            )
            if snapshot.value is not None and snapshot.change_pct is not None:
                snapshot.interpretation = interpretation_builder(snapshot.value, snapshot.change_pct)
        except MacroIndicatorError as exc:
            return self._unavailable_indicator(
                key=key,
                label=label,
                source="yahoo_finance",
                detail=str(exc),
            )
        return snapshot.to_payload()

    @staticmethod
    def _unavailable_indicator(*, key: str, label: str, source: str, detail: str) -> dict:
        return MacroIndicatorSnapshot(
            key=key,
            label=label,
            value=None,
            unit=None,
            previous_value=None,
            change=None,
            change_pct=None,
            as_of=None,
            source=source,
            status="unavailable",
            interpretation=None,
            detail=detail,
        ).to_payload()


def _fear_greed_rating(score: float | None) -> str | None:
    if score is None:
        return None
    if score < 25:
        return "Extreme Fear"
    if score < 45:
        return "Fear"
    if score < 56:
        return "Neutral"
    if score < 75:
        return "Greed"
    return "Extreme Greed"


def _interpret_vix(value: float) -> str:
    if value >= 25:
        return "risk_off"
    if value <= 15:
        return "risk_on"
    return "neutral"


def _interpret_us10y(value: float) -> str:
    if value >= 4.5:
        return "hawkish_rates"
    if value <= 3.5:
        return "supportive_rates"
    return "neutral_rates"


def _interpret_gold(value: float, change_pct: float) -> str:
    del value
    if change_pct >= 1.5:
        return "safe_haven_bid"
    if change_pct <= -1.5:
        return "risk_on_rotation"
    return "neutral"


def _interpret_oil(value: float, change_pct: float) -> str:
    del value
    if change_pct >= 2.0:
        return "inflationary_pressure"
    if change_pct <= -2.0:
        return "demand_cooling"
    return "neutral"


def _interpret_copper(value: float, change_pct: float) -> str:
    del value
    if change_pct >= 1.5:
        return "growth_supportive"
    if change_pct <= -1.5:
        return "growth_stress"
    return "neutral"


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC).isoformat()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return text


def _coerce_dateish(value) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return text


def _xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag
