from __future__ import annotations

from app.domains.market.services import MarketDataService
from app.services.chart_render_service import ChartRenderService
from app.services.quant_analysis_service import QuantAnalysisService
from app.services.visual_analysis_service import VisualAnalysisService


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

    def analyze_ticker(self, ticker: str, benchmark_ticker: str = "SPY") -> dict:
        candles = self.market_data_service.get_history(ticker, limit=120)
        benchmark_candles = self.market_data_service.get_history(benchmark_ticker, limit=120)
        quant_summary = self.quant_service.analyze(
            ticker=ticker,
            candles=candles,
            benchmark_candles=benchmark_candles,
        )
        visual_summary = self.visual_service.analyze(candles=candles, quant_summary=quant_summary)
        chart_svg = self.chart_service.render_standard_chart(
            ticker=ticker,
            candles=candles[-90:],
            quant_summary=quant_summary,
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
            "quant_summary": quant_summary,
            "visual_summary": {
                **visual_summary,
                "chart_render_mode": "standardized_svg",
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
