from __future__ import annotations

from app.providers.market_data.base import OHLCVCandle


class QuantAnalysisService:
    def analyze(
        self,
        *,
        ticker: str,
        candles: list[OHLCVCandle],
        benchmark_candles: list[OHLCVCandle] | None = None,
    ) -> dict:
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        latest_close = closes[-1]

        sma_20 = self._sma(closes[-20:])
        sma_50 = self._sma(closes[-50:])
        momentum_20 = self._pct_change(closes[-21], latest_close)
        relative_volume = volumes[-1] / max(self._sma(volumes[-21:-1]), 1.0)
        atr_14 = self._atr(highs[-15:], lows[-15:], closes[-15:])
        adx_14 = self._adx(highs[-15:], lows[-15:], closes[-15:])
        bb_mid = sma_20
        bb_std = self._std(closes[-20:])
        bb_upper = bb_mid + (2 * bb_std)
        bb_lower = bb_mid - (2 * bb_std)
        donchian_high = max(highs[-20:])
        donchian_low = min(lows[-20:])
        proximity_to_high = round(((latest_close / max(max(highs[-55:]), 0.01)) - 1) * 100, 2)
        proximity_to_low = round(((latest_close / max(min(lows[-55:]), 0.01)) - 1) * 100, 2)

        benchmark_momentum = 0.0
        if benchmark_candles and len(benchmark_candles) >= 21:
            benchmark_closes = [c.close for c in benchmark_candles]
            benchmark_momentum = self._pct_change(benchmark_closes[-21], benchmark_closes[-1])
        alpha_gap = momentum_20 - benchmark_momentum

        trend = "sideways"
        if latest_close > sma_20 > sma_50:
            trend = "uptrend"
        elif latest_close < sma_20 < sma_50:
            trend = "downtrend"

        setup = "consolidation"
        if latest_close >= donchian_high * 0.995 and momentum_20 > 4:
            setup = "breakout"
        elif latest_close > sma_20 and lows[-1] <= sma_20 and momentum_20 > 1:
            setup = "pullback"
        elif adx_14 < 18:
            setup = "range"

        support_level = round(min(lows[-10:]), 2)
        resistance_level = round(max(highs[-10:]), 2)
        entry_price = round(latest_close, 2)
        stop_price = round(max(min(support_level, latest_close - (1.5 * atr_14)), 0.01), 2)
        if setup == "breakout":
            take_profit = round(entry_price + max(2.8 * atr_14, resistance_level - support_level), 2)
        elif setup == "pullback":
            take_profit = round(entry_price + max(2.2 * atr_14, resistance_level - entry_price), 2)
        else:
            take_profit = round(entry_price + (1.8 * atr_14), 2)

        risk = max(entry_price - stop_price, 0.01)
        reward = max(take_profit - entry_price, 0.01)
        risk_reward = round(reward / risk, 2)

        trend_score = 1.0 if trend == "uptrend" else (0.25 if trend == "sideways" else 0.0)
        momentum_score = min(max((momentum_20 + 5) / 15, 0.0), 1.0)
        volume_score = min(max((relative_volume - 1.0) / 1.5, 0.0), 1.0)
        structure_score = min(max((adx_14 - 15) / 20, 0.0), 1.0)
        alpha_score = min(max((alpha_gap + 3) / 10, 0.0), 1.0)
        rr_score = min(max((risk_reward - 1.0) / 2.0, 0.0), 1.0)
        quant_score = round(
            trend_score * 0.2
            + momentum_score * 0.18
            + volume_score * 0.14
            + structure_score * 0.16
            + alpha_score * 0.14
            + rr_score * 0.18,
            2,
        )

        return {
            "ticker": ticker.upper(),
            "trend": trend,
            "setup": setup,
            "momentum_pct_20": round(momentum_20, 2),
            "benchmark_momentum_pct_20": round(benchmark_momentum, 2),
            "alpha_gap_pct_20": round(alpha_gap, 2),
            "relative_volume": round(relative_volume, 2),
            "proximity_to_55d_high_pct": proximity_to_high,
            "proximity_to_55d_low_pct": proximity_to_low,
            "atr_14": round(atr_14, 2),
            "adx_14": round(adx_14, 2),
            "bollinger_mid": round(bb_mid, 2),
            "bollinger_upper": round(bb_upper, 2),
            "bollinger_lower": round(bb_lower, 2),
            "donchian_high_20": round(donchian_high, 2),
            "donchian_low_20": round(donchian_low, 2),
            "entry_price": entry_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit,
            "risk_reward": risk_reward,
            "support_level": support_level,
            "resistance_level": resistance_level,
            "quant_score": quant_score,
            "narrative": (
                f"{ticker.upper()} shows {trend} with {setup} context, momentum {round(momentum_20, 2)}%, "
                f"relative volume {round(relative_volume, 2)}x, ADX {round(adx_14, 1)} and R/R {risk_reward}."
            ),
        }

    @staticmethod
    def _sma(values: list[float]) -> float:
        return sum(values) / max(len(values), 1)

    @staticmethod
    def _std(values: list[float]) -> float:
        mean = sum(values) / max(len(values), 1)
        variance = sum((value - mean) ** 2 for value in values) / max(len(values), 1)
        return variance ** 0.5

    @staticmethod
    def _pct_change(old: float, new: float) -> float:
        return ((new / max(old, 0.01)) - 1) * 100

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

    @staticmethod
    def _adx(highs: list[float], lows: list[float], closes: list[float]) -> float:
        plus_dm = []
        minus_dm = []
        tr_values = []
        for idx in range(1, len(highs)):
            up_move = highs[idx] - highs[idx - 1]
            down_move = lows[idx - 1] - lows[idx]
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
            tr_values.append(
                max(
                    highs[idx] - lows[idx],
                    abs(highs[idx] - closes[idx - 1]),
                    abs(lows[idx] - closes[idx - 1]),
                )
            )
        atr = sum(tr_values) / max(len(tr_values), 1)
        if atr == 0:
            return 0.0
        plus_di = 100 * (sum(plus_dm) / max(len(plus_dm), 1)) / atr
        minus_di = 100 * (sum(minus_dm) / max(len(minus_dm), 1)) / atr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0.0
        return abs(plus_di - minus_di) / di_sum * 100
