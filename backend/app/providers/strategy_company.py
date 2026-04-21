from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import BACKEND_DIR, Settings, get_settings


class StrategyCompanyProviderError(RuntimeError):
    pass


class StrategyCompanyProvider:
    NEXT_DATA_PATTERN = re.compile(
        r'<script id="__NEXT_DATA__" type="application/json">(?P<payload>.*?)</script>',
        re.DOTALL,
    )
    PAGE_URLS = {
        "btc": "https://www.strategy.com/btc",
        "purchases": "https://www.strategy.com/purchases",
        "shares": "https://www.strategy.com/shares",
    }
    SOURCE = "strategy.com_next_data_v1"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout_seconds: int | None = None,
        cache_ttl_seconds: int | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.enabled = bool(self.settings.strategy_company_enabled)
        self.timeout_seconds = timeout_seconds or int(self.settings.strategy_company_request_timeout_seconds)
        self.cache_ttl_seconds = (
            int(cache_ttl_seconds)
            if cache_ttl_seconds is not None
            else int(self.settings.strategy_company_cache_ttl_seconds)
        )
        self.cache_path = cache_path or (BACKEND_DIR / ".cache" / "strategy_company_mstr_context.json")
        self._cache: tuple[float, dict] | None = None

    def get_mstr_metrics(self) -> dict:
        if not self.enabled:
            return self._unavailable_payload("Strategy company context is disabled by settings.")

        now = time.time()
        if self._cache is not None and self._is_cache_fresh(self._cache[0], now):
            return self._decorate_payload(
                self._cache[1],
                cached_at=self._cache[0],
                now=now,
                cache_hit="memory",
                used_fallback=False,
                provider_error=None,
            )

        disk_cache = self._read_cache_file()
        if disk_cache is not None and self._is_cache_fresh(disk_cache[0], now):
            self._cache = disk_cache
            return self._decorate_payload(
                disk_cache[1],
                cached_at=disk_cache[0],
                now=now,
                cache_hit="disk",
                used_fallback=False,
                provider_error=None,
            )

        try:
            normalized_payload = self._fetch_live_payload()
        except StrategyCompanyProviderError as exc:
            fallback = self._cache or disk_cache
            if fallback is not None:
                fallback_hit = "memory" if self._cache is not None and fallback == self._cache else "disk"
                self._cache = fallback
                return self._decorate_payload(
                    fallback[1],
                    cached_at=fallback[0],
                    now=now,
                    cache_hit=fallback_hit,
                    used_fallback=True,
                    provider_error=str(exc),
                )
            return self._unavailable_payload(str(exc))

        self._cache = (now, normalized_payload)
        self._write_cache_file(cached_at=now, payload=normalized_payload)
        return self._decorate_payload(
            normalized_payload,
            cached_at=now,
            now=now,
            cache_hit="live",
            used_fallback=False,
            provider_error=None,
        )

    def _fetch_live_payload(self) -> dict:
        btc_props = self._load_page_props(self.PAGE_URLS["btc"])
        purchases_props = self._load_page_props(self.PAGE_URLS["purchases"])
        shares_props = self._load_page_props(self.PAGE_URLS["shares"])

        raw_stats = self._first_dict(btc_props.get("btcTrackerData"))
        raw_purchases = purchases_props.get("bitcoinData")
        raw_shares = shares_props.get("shares")
        if raw_stats is None or not isinstance(raw_purchases, list) or not isinstance(raw_shares, list):
            raise StrategyCompanyProviderError("Strategy pages did not expose the expected metric payloads.")

        stats = self._normalize_stats(raw_stats)
        purchases_history = self._normalize_purchases_history(raw_purchases)
        shares_history = self._normalize_shares_history(raw_shares)
        latest_purchase = purchases_history[-1] if purchases_history else {}
        latest_shares = shares_history[-1] if shares_history else {}
        as_of = (
            str(stats.get("as_of_date") or "").strip()
            or str(latest_purchase.get("date_of_purchase") or "").strip()
            or str(latest_shares.get("date") or "").strip()
            or None
        )

        return {
            "available": True,
            "source": self.SOURCE,
            "as_of": as_of,
            "stats": stats,
            "latest_purchase": latest_purchase,
            "latest_shares": latest_shares,
            "purchases_history": purchases_history,
            "shares_history": shares_history,
            "source_urls": dict(self.PAGE_URLS),
        }

    def _load_page_props(self, url: str) -> dict:
        html = self._request_text(url)
        match = self.NEXT_DATA_PATTERN.search(html)
        if match is None:
            raise StrategyCompanyProviderError(f"Strategy page did not expose __NEXT_DATA__: {url}")
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError as exc:
            raise StrategyCompanyProviderError(f"Strategy page returned invalid __NEXT_DATA__ JSON: {url}") from exc
        props = payload.get("props", {}).get("pageProps")
        if not isinstance(props, dict):
            raise StrategyCompanyProviderError(f"Strategy page payload was missing pageProps: {url}")
        return props

    def _request_text(self, url: str) -> str:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise StrategyCompanyProviderError(f"Strategy request failed for {url}: HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise StrategyCompanyProviderError(f"Strategy request failed for {url}: {exc}") from exc

    def _read_cache_file(self) -> tuple[float, dict] | None:
        if not self.cache_path.exists():
            return None
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            cached_at = float(payload.get("cached_at") or 0.0)
            normalized_payload = dict(payload.get("payload") or {})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if cached_at <= 0 or not normalized_payload:
            return None
        return cached_at, normalized_payload

    def _write_cache_file(self, *, cached_at: float, payload: dict) -> None:
        serialized = {
            "cached_at": cached_at,
            "source": self.SOURCE,
            "payload": payload,
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.cache_path.with_suffix(f"{self.cache_path.suffix}.tmp")
            temporary_path.write_text(json.dumps(serialized, ensure_ascii=True), encoding="utf-8")
            temporary_path.replace(self.cache_path)
        except OSError:
            return

    def _decorate_payload(
        self,
        payload: dict,
        *,
        cached_at: float,
        now: float,
        cache_hit: str,
        used_fallback: bool,
        provider_error: str | None,
    ) -> dict:
        age_seconds = max(int(now - cached_at), 0)
        stale = not self._is_cache_fresh(cached_at, now)
        return {
            **dict(payload),
            "available": bool(payload.get("available", True)),
            "source": str(payload.get("source") or self.SOURCE),
            "as_of": payload.get("as_of"),
            "stale": stale,
            "used_fallback": used_fallback,
            "provider_error": provider_error,
            "cache": {
                "cached_at": datetime.fromtimestamp(cached_at, tz=UTC).isoformat(),
                "age_seconds": age_seconds,
                "ttl_seconds": self.cache_ttl_seconds,
                "stale": stale,
                "hit": cache_hit,
            },
        }

    def _unavailable_payload(self, provider_error: str) -> dict:
        return {
            "available": False,
            "source": self.SOURCE,
            "as_of": None,
            "stale": True,
            "used_fallback": False,
            "provider_error": provider_error,
            "stats": {},
            "latest_purchase": {},
            "latest_shares": {},
            "purchases_history": [],
            "shares_history": [],
            "source_urls": dict(self.PAGE_URLS),
            "cache": {
                "cached_at": None,
                "age_seconds": None,
                "ttl_seconds": self.cache_ttl_seconds,
                "stale": True,
                "hit": "none",
            },
        }

    def _normalize_stats(self, raw: dict) -> dict:
        preferred_metrics: dict[str, dict] = {}
        for key in ("strc_metrics", "strd_metrics", "stre_metrics", "strf_metrics", "strk_metrics"):
            value = raw.get(key)
            if not isinstance(value, dict):
                continue
            preferred_metrics[key.replace("_metrics", "")] = {
                "shares": self._as_int(value.get("shares")),
                "cumulative_notional": self._as_float(value.get("cumulative_notional")),
                "dividend": self._as_float(value.get("dividend")),
                "next_payout_date": self._as_date(value.get("next_payout_date")),
                "next_record_date": self._as_date(value.get("next_record_date")),
            }

        return {
            "as_of_date": self._as_date(raw.get("as_of_date")),
            "btc_holdings": self._as_int(raw.get("btc_holdings")),
            "basic_shares_outstanding": self._as_int(raw.get("basic_shares_outstanding")),
            "cash": self._as_float(raw.get("cash")),
            "debt": self._as_float(raw.get("debt")),
            "pref": self._as_float(raw.get("pref")),
            "btc_yield_qtd": self._as_float(raw.get("btc_yield_qtd")),
            "btc_yield_ytd": self._as_float(raw.get("btc_yield_ytd")),
            "btc_gain_qtd": self._as_float(raw.get("btc_gain_qtd")),
            "btc_gain_ytd": self._as_float(raw.get("btc_gain_ytd")),
            "btc_gain_2024_dollars": self._as_float(raw.get("btc_gain_2024_dollars")),
            "btc_gain_2023_dollars": self._as_float(raw.get("btc_gain_2023_dollars")),
            "preferred_metrics": preferred_metrics,
        }

    def _normalize_purchases_history(self, raw_rows: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            sec = row.get("sec") if isinstance(row.get("sec"), dict) else {}
            normalized.append(
                {
                    "date_of_purchase": self._as_date(row.get("date_of_purchase")),
                    "btc_holdings": self._as_int(row.get("btc_holdings")),
                    "btc_count_change": self._as_int(row.get("count")),
                    "assumed_diluted_shares_outstanding": self._normalize_share_count(
                        row.get("assumed_diluted_shares_outstanding")
                    ),
                    "basic_shares_outstanding": self._normalize_share_count(row.get("basic_shares_outstanding")),
                    "btc_yield_qtd": self._as_float(row.get("btc_yield_qtd")),
                    "btc_yield_ytd": self._as_float(row.get("btc_yield_ytd")),
                    "btc_gain_qtd": self._as_float(row.get("btc_gain_qtd")),
                    "btc_gain_ytd": self._as_float(row.get("btc_gain_ytd")),
                    "btc_reserve_millions": self._as_float(row.get("btc_nav")),
                    "purchase_price": self._as_float(row.get("purchase_price")),
                    "average_price": self._as_float(row.get("average_price")),
                    "total_purchase_price": self._as_float(row.get("total_purchase_price")),
                    "total_acquisition_cost": self._as_float(row.get("total_acquisition_cost")),
                    "title": self._as_string(row.get("title")),
                    "sec_filename": self._as_string(sec.get("filename")),
                    "sec_url": self._as_string(sec.get("url")),
                }
            )
        normalized = [row for row in normalized if row.get("date_of_purchase")]
        normalized.sort(key=lambda row: str(row.get("date_of_purchase")))
        return normalized

    def _normalize_shares_history(self, raw_rows: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "date": self._as_date(row.get("date")),
                    "assumed_diluted_shares_outstanding": self._normalize_share_count(
                        row.get("assumed_diluted_shares_outstanding")
                    ),
                    "basic_shares_outstanding": self._normalize_share_count(row.get("basic_shares_outstanding")),
                    "total_bitcoin_holdings": self._as_int(row.get("total_bitcoin_holdings")),
                    "btc_yield_qtd": self._as_float(row.get("btc_yield_qtd")),
                    "btc_yield_ytd": self._as_float(row.get("btc_yield_ytd")),
                    "btc_gain_qtd": self._as_float(row.get("btc_gain_qtd")),
                    "btc_gain_ytd": self._as_float(row.get("btc_gain_ytd")),
                }
            )
        normalized = [row for row in normalized if row.get("date")]
        normalized.sort(key=lambda row: str(row.get("date")))
        return normalized

    @staticmethod
    def _first_dict(value: object) -> dict | None:
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return None

    def _is_cache_fresh(self, cached_at: float, now: float) -> bool:
        return (now - cached_at) <= self.cache_ttl_seconds

    @staticmethod
    def _as_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "")
            if not cleaned:
                return None
            try:
                return int(float(cleaned))
            except ValueError:
                return None
        return None

    @staticmethod
    def _as_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip().replace(",", "")
            if not cleaned:
                return None
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    @classmethod
    def _normalize_share_count(cls, value: object) -> int | None:
        parsed = cls._as_int(value)
        if parsed is None:
            return None
        return parsed * 1000 if 0 < parsed < 10_000_000 else parsed

    @staticmethod
    def _as_date(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned[:10] if cleaned else None

    @staticmethod
    def _as_string(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None
