from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from app.providers.market_data.base import MarketDataProvider, MarketSnapshot, OHLCVCandle


class TwelveDataError(RuntimeError):
    pass


class TwelveDataProvider(MarketDataProvider):
    base_url = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        candles = self.get_history(ticker, limit=220)
        if len(candles) < 20:
            raise TwelveDataError("Insufficient candle history returned by Twelve Data")

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        price = closes[-1]
        sma_20 = round(self._sma(closes[-20:]), 2)
        sma_50 = round(self._sma(closes[-50:]), 2)
        sma_200 = round(self._sma(closes[-200:]), 2)
        rsi_14 = round(self._rsi(closes[-15:]), 2)
        relative_volume = round(volumes[-1] / max(self._sma(volumes[-21:-1]), 1.0), 2)
        atr_14 = round(self._atr(highs[-15:], lows[-15:], closes[-15:]), 2)
        week_performance = round(((price / closes[-6]) - 1), 4)
        month_performance = round(((price / closes[-22]) - 1), 4)

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
        query = urlencode(
            {
                "symbol": ticker.upper(),
                "interval": "1day",
                "outputsize": max(limit, 220),
                "apikey": self.api_key,
                "format": "JSON",
            }
        )
        url = f"{self.base_url}?{query}"
        try:
            with urlopen(url, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise TwelveDataError(f"Twelve Data request failed for {ticker}: {exc}") from exc

        values = payload.get("values")
        if not values:
            message = payload.get("message") or payload.get("status") or "Unknown Twelve Data error"
            raise TwelveDataError(f"Twelve Data returned no values for {ticker}: {message}")

        candles: list[OHLCVCandle] = []
        for item in values:
            candles.append(
                OHLCVCandle(
                    timestamp=item["datetime"],
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(item.get("volume") or 0.0),
                )
            )
        candles.reverse()
        return candles[-limit:]

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
