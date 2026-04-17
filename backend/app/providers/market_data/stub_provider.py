from datetime import date, timedelta

from app.providers.market_data.base import MarketDataProvider, MarketSnapshot, OHLCVCandle


class StubMarketDataProvider(MarketDataProvider):
    def get_snapshot(self, ticker: str) -> MarketSnapshot:
        candles = self.get_history(ticker, limit=220)
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        price = closes[-1]
        sma_20 = round(sum(closes[-20:]) / 20, 2)
        sma_50 = round(sum(closes[-50:]) / 50, 2)
        sma_200 = round(sum(closes[-200:]) / 200, 2)
        rsi_14 = round(self._rsi(closes[-15:]), 2)
        relative_volume = round(volumes[-1] / max((sum(volumes[-21:-1]) / 20), 1.0), 2)
        atr_14 = round(self._atr(highs[-15:], lows[-15:], closes[-15:]), 2)
        week_performance = round(((price / closes[-6]) - 1), 4)
        month_performance = round(((price / closes[-22]) - 1), 4)

        return MarketSnapshot(
            ticker=ticker.upper(),
            price=price,
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
        seed = sum(ord(char) for char in ticker.upper())
        base_price = 20 + (seed % 180)
        drift = (seed % 15) / 10
        today = date(2026, 4, 16)
        candles: list[OHLCVCandle] = []

        for idx in range(limit):
            offset = limit - idx
            trend_bias = ((seed % 9) - 2) * 0.18
            seasonal = ((idx % 14) - 7) * 0.22
            pulse = (((seed + idx * 3) % 11) - 5) * 0.15
            close = round(max(5.0, base_price + drift + trend_bias * idx + seasonal + pulse), 2)
            open_price = round(close - (((seed + idx) % 7) - 3) * 0.18, 2)
            high = round(max(open_price, close) + 0.9 + ((seed + idx) % 5) * 0.25, 2)
            low = round(min(open_price, close) - 0.9 - ((seed + idx * 2) % 5) * 0.2, 2)
            volume = float(900_000 + ((seed * (idx + 3)) % 4_500_000))
            candles.append(
                OHLCVCandle(
                    timestamp=(today - timedelta(days=offset)).isoformat(),
                    open=open_price,
                    high=high,
                    low=max(1.0, low),
                    close=close,
                    volume=volume,
                )
            )
        return candles

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
