from __future__ import annotations

from app.providers.market_data.base import OHLCVCandle


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
