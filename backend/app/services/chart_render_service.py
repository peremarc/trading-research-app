from __future__ import annotations

from app.providers.market_data.base import OHLCVCandle


class ChartRenderService:
    def render_standard_chart(self, *, ticker: str, candles: list[OHLCVCandle], quant_summary: dict) -> str:
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
  <text x="{pad_left}" y="24" fill="#132231" font-size="20" font-family="Bahnschrift, Trebuchet MS, sans-serif">{ticker.upper()} Standardized Daily Chart</text>
  <text x="{pad_left}" y="{height - 16}" fill="#53606c" font-size="13" font-family="Bahnschrift, Trebuchet MS, sans-serif">Setup={quant_summary['setup']} · Trend={quant_summary['trend']} · ADX={quant_summary['adx_14']} · R/R={quant_summary['risk_reward']}</text>
</svg>"""
