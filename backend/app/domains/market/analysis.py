from __future__ import annotations

from app.domains.market.services import MarketDataService
from app.providers.market_data.base import OHLCVCandle


CHART_TIMEFRAME_WINDOWS = {
    "1M": 22,
    "3M": 66,
    "6M": 132,
    "1Y": 252,
    "5Y": 1260,
}

CHART_TIMEFRAME_ALIASES = {
    "1m": "1M",
    "1mo": "1M",
    "1month": "1M",
    "1 month": "1M",
    "3m": "3M",
    "3mo": "3M",
    "3month": "3M",
    "3 months": "3M",
    "6m": "6M",
    "6mo": "6M",
    "6month": "6M",
    "6 months": "6M",
    "1y": "1Y",
    "1yr": "1Y",
    "1year": "1Y",
    "1 year": "1Y",
    "5y": "5Y",
    "5yr": "5Y",
    "5year": "5Y",
    "5 years": "5Y",
}


def normalize_chart_timeframe(timeframe: str | None) -> tuple[str, int]:
    if timeframe is None:
        canonical = "6M"
    else:
        normalized = str(timeframe).strip()
        if not normalized:
            canonical = "6M"
        else:
            canonical = CHART_TIMEFRAME_ALIASES.get(normalized.lower(), normalized.upper())
    if canonical not in CHART_TIMEFRAME_WINDOWS:
        raise ValueError(
            f"Unsupported timeframe '{timeframe}'. Supported values: {', '.join(CHART_TIMEFRAME_WINDOWS.keys())}"
        )
    return canonical, CHART_TIMEFRAME_WINDOWS[canonical]


class ChartRenderService:
    def render_standard_chart(
        self,
        *,
        ticker: str,
        candles: list[OHLCVCandle],
        quant_summary: dict,
        timeframe_label: str,
    ) -> str:
        width = 960
        height = 540
        pad_left = 56
        pad_right = 28
        pad_top = 36
        pad_bottom = 46
        plot_w = width - pad_left - pad_right
        plot_h = height - pad_top - pad_bottom

        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        min_price = min(lows)
        max_price = max(highs)
        price_span = max(max_price - min_price, 0.01)

        def price_y(price: float) -> float:
            return pad_top + plot_h - (((price - min_price) / price_span) * plot_h)

        candle_w = max(plot_w / max(len(candles), 1), 3)
        line_points = []
        candle_shapes = []

        for idx, candle in enumerate(candles):
            x = pad_left + idx * candle_w + candle_w / 2
            open_y = price_y(candle.open)
            close_y = price_y(candle.close)
            high_y = price_y(candle.high)
            low_y = price_y(candle.low)
            color = "#135b3b" if candle.close >= candle.open else "#8d2242"
            candle_shapes.append(
                f'<line x1="{x:.2f}" y1="{high_y:.2f}" x2="{x:.2f}" y2="{low_y:.2f}" stroke="{color}" stroke-width="1.4" />'
            )
            body_y = min(open_y, close_y)
            body_h = max(abs(close_y - open_y), 1.4)
            candle_shapes.append(
                f'<rect x="{(x - candle_w * 0.28):.2f}" y="{body_y:.2f}" width="{(candle_w * 0.56):.2f}" height="{body_h:.2f}" rx="1.5" fill="{color}" />'
            )
            line_points.append(f"{x:.2f},{close_y:.2f}")

        support_y = price_y(quant_summary["support_level"])
        resistance_y = price_y(quant_summary["resistance_level"])
        entry_y = price_y(quant_summary["entry_price"])
        stop_y = price_y(quant_summary["stop_price"])
        take_profit_y = price_y(quant_summary["take_profit_price"])

        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f7f2e8" />
  <rect x="{pad_left}" y="{pad_top}" width="{plot_w}" height="{plot_h}" fill="#fffaf0" stroke="#d7c8ae" />
  <polyline fill="none" stroke="#244a7c" stroke-width="1.6" points="{' '.join(line_points)}" />
  {''.join(candle_shapes)}
  <line x1="{pad_left}" y1="{support_y:.2f}" x2="{width - pad_right}" y2="{support_y:.2f}" stroke="#996300" stroke-dasharray="6 4" />
  <line x1="{pad_left}" y1="{resistance_y:.2f}" x2="{width - pad_right}" y2="{resistance_y:.2f}" stroke="#8d2242" stroke-dasharray="6 4" />
  <line x1="{pad_left}" y1="{entry_y:.2f}" x2="{width - pad_right}" y2="{entry_y:.2f}" stroke="#244a7c" />
  <line x1="{pad_left}" y1="{stop_y:.2f}" x2="{width - pad_right}" y2="{stop_y:.2f}" stroke="#8d2242" stroke-dasharray="3 3" />
  <line x1="{pad_left}" y1="{take_profit_y:.2f}" x2="{width - pad_right}" y2="{take_profit_y:.2f}" stroke="#135b3b" stroke-dasharray="3 3" />
  <text x="{pad_left}" y="24" fill="#132231" font-size="20" font-family="Bahnschrift, Trebuchet MS, sans-serif">{ticker.upper()} Standardized {timeframe_label} Chart</text>
  <text x="{pad_left}" y="{height - 16}" fill="#53606c" font-size="13" font-family="Bahnschrift, Trebuchet MS, sans-serif">Setup={quant_summary['setup']} · Trend={quant_summary['trend']} · ADX={quant_summary['adx_14']} · R/R={quant_summary['risk_reward']}</text>
</svg>"""


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


class VisualAnalysisService:
    def analyze(self, *, candles: list[OHLCVCandle], quant_summary: dict) -> dict:
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        recent_range = max(highs[-15:]) - min(lows[-15:])
        total_range = max(highs[-60:]) - min(lows[-60:])
        compression_ratio = recent_range / max(total_range, 0.01)
        structure_clarity = round(
            min(max((1 - compression_ratio) * 0.8 + (quant_summary["adx_14"] / 100) * 0.4, 0.0), 1.0),
            2,
        )

        visible_support = round(min(lows[-8:]), 2)
        visible_resistance = round(max(highs[-8:]), 2)
        setup_quality = round(
            min(
                max(
                    structure_clarity * 0.35
                    + min(max((quant_summary["risk_reward"] - 1) / 2, 0.0), 1.0) * 0.25
                    + min(max((quant_summary["relative_volume"] - 1) / 1.5, 0.0), 1.0) * 0.2
                    + min(max((quant_summary["momentum_pct_20"] + 5) / 15, 0.0), 1.0) * 0.2,
                    0.0,
                ),
                1.0,
            ),
            2,
        )

        setup_type = quant_summary["setup"]
        if compression_ratio < 0.35 and quant_summary["trend"] == "uptrend":
            setup_type = "consolidation"
        if quant_summary["entry_price"] >= quant_summary["donchian_high_20"] * 0.995:
            setup_type = "breakout"
        elif quant_summary["entry_price"] <= quant_summary["bollinger_mid"] * 1.02 and quant_summary["trend"] == "uptrend":
            setup_type = "pullback"

        visual_score = round(
            min(
                max(
                    structure_clarity * 0.45
                    + setup_quality * 0.4
                    + (0.15 if setup_type in {"breakout", "pullback"} else 0.05),
                    0.0,
                ),
                1.0,
            ),
            2,
        )

        return {
            "structure_clarity": structure_clarity,
            "setup_quality": setup_quality,
            "visible_support": visible_support,
            "visible_resistance": visible_resistance,
            "setup_type": setup_type,
            "visual_score": visual_score,
            "visual_narrative": (
                f"Chart shows {setup_type} structure with clarity {structure_clarity}, support near {visible_support} "
                f"and resistance near {visible_resistance}."
            ),
        }


class FusedAnalysisService:
    def __init__(
        self,
        market_data_service: MarketDataService | None = None,
        quant_service: QuantAnalysisService | None = None,
        visual_service: VisualAnalysisService | None = None,
        chart_service: ChartRenderService | None = None,
    ) -> None:
        self.market_data_service = market_data_service or MarketDataService()
        self.quant_service = quant_service or QuantAnalysisService()
        self.visual_service = visual_service or VisualAnalysisService()
        self.chart_service = chart_service or ChartRenderService()

    def analyze_ticker(self, ticker: str, benchmark_ticker: str = "SPY", timeframe: str | None = None) -> dict:
        chart_payload = self.build_chart_payload(ticker=ticker, benchmark_ticker=benchmark_ticker, timeframe=timeframe)
        return {
            "ticker": chart_payload["ticker"],
            "quant_summary": chart_payload["quant_summary"],
            "visual_summary": chart_payload["visual_summary"],
            "chart_svg": chart_payload["chart_svg"],
            "combined_score": chart_payload["combined_score"],
            "decision": chart_payload["decision"],
            "decision_confidence": chart_payload["decision_confidence"],
            "entry_price": chart_payload["entry_price"],
            "stop_price": chart_payload["stop_price"],
            "target_price": chart_payload["target_price"],
            "risk_reward": chart_payload["risk_reward"],
            "rationale": chart_payload["rationale"],
            "timeframe": chart_payload["timeframe"],
        }

    def build_chart_payload(self, *, ticker: str, benchmark_ticker: str = "SPY", timeframe: str | None = None) -> dict:
        timeframe_label, visible_window = normalize_chart_timeframe(timeframe)
        history_limit = max(220, visible_window)
        candles = self.market_data_service.get_history(ticker, limit=history_limit)
        benchmark_candles = self.market_data_service.get_history(benchmark_ticker, limit=history_limit)
        visible_candles = candles[-visible_window:]
        visible_benchmark_candles = benchmark_candles[-visible_window:] if len(benchmark_candles) >= visible_window else benchmark_candles
        analysis_candles = visible_candles if len(visible_candles) >= 60 else candles
        analysis_benchmark_candles = visible_benchmark_candles if len(visible_benchmark_candles) >= 60 else benchmark_candles
        quant_summary = self.quant_service.analyze(
            ticker=ticker,
            candles=analysis_candles,
            benchmark_candles=analysis_benchmark_candles,
        )
        visual_summary = self.visual_service.analyze(candles=visible_candles, quant_summary=quant_summary)
        chart_svg = self.chart_service.render_standard_chart(
            ticker=ticker,
            candles=visible_candles,
            quant_summary=quant_summary,
            timeframe_label=timeframe_label,
        )
        combined_score = round((quant_summary["quant_score"] * 0.6) + (visual_summary["visual_score"] * 0.4), 2)
        decision = "discard"
        if combined_score >= 0.78 and quant_summary["risk_reward"] >= 1.8:
            decision = "paper_enter"
        elif combined_score >= 0.58:
            decision = "watch"

        rationale = (
            f"Fusion decision for {ticker.upper()}: quant={quant_summary['quant_score']}, visual={visual_summary['visual_score']}, "
            f"setup={visual_summary['setup_type']}, trend={quant_summary['trend']}, "
            f"alpha_gap={quant_summary['alpha_gap_pct_20']}%, R/R={quant_summary['risk_reward']}."
        )

        return {
            "ticker": ticker.upper(),
            "timeframe": timeframe_label,
            "visible_candles": len(visible_candles),
            "quant_summary": quant_summary,
            "visual_summary": {
                **visual_summary,
                "chart_render_mode": "standardized_svg",
                "timeframe": timeframe_label,
            },
            "chart_svg": chart_svg,
            "combined_score": combined_score,
            "decision": decision,
            "decision_confidence": combined_score,
            "entry_price": quant_summary["entry_price"],
            "stop_price": quant_summary["stop_price"],
            "target_price": quant_summary["take_profit_price"],
            "risk_reward": quant_summary["risk_reward"],
            "rationale": rationale,
        }

    def get_multitimeframe_context(
        self,
        *,
        ticker: str,
        benchmark_ticker: str = "SPY",
        timeframes: list[str] | None = None,
    ) -> dict:
        selected = timeframes or ["1M", "3M", "6M", "1Y", "5Y"]
        charts = [
            self.build_chart_payload(ticker=ticker, benchmark_ticker=benchmark_ticker, timeframe=timeframe)
            for timeframe in selected
        ]
        return {
            "ticker": ticker.upper(),
            "charts": charts,
        }
