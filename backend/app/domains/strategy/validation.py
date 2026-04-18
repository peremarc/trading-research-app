from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models.decision_context import StrategyContextRule
from app.db.models.position import Position
from app.db.models.strategy import StrategyVersion
from app.domains.strategy.schemas import CandidateValidationSummaryRead


@dataclass
class CandidateValidationThresholds:
    min_trade_count: int = 4
    min_distinct_tickers: int = 2
    min_avg_pnl_pct: float = 1.0
    min_win_rate: float = 60.0
    min_profit_factor: float = 1.2
    max_avg_drawdown_pct: float = -4.0
    rolling_window_size: int = 3
    min_rolling_pass_rate: float = 0.5
    min_windows: int = 2
    min_replay_score: float = 0.58
    reject_trade_count: int = 4
    reject_max_avg_pnl_pct: float = 0.0
    reject_max_profit_factor: float = 0.95
    reject_max_rolling_pass_rate: float = 0.34
    reject_max_replay_score: float = 0.45


class StrategyValidationService:
    def build_candidate_validation_summary(
        self,
        session: Session,
        *,
        candidate: StrategyVersion,
        validation_positions: list[Position],
    ) -> CandidateValidationSummaryRead:
        thresholds = self._resolve_thresholds(candidate)
        ordered_positions = sorted(
            validation_positions,
            key=lambda position: (
                position.exit_date or position.entry_date,
                position.id,
            ),
        )
        wins = len([position for position in ordered_positions if (position.pnl_pct or 0.0) > 0])
        losses = len(ordered_positions) - wins
        trade_count = len(ordered_positions)
        distinct_tickers = len({position.ticker for position in ordered_positions})
        avg_pnl_pct = self._round(
            sum((position.pnl_pct or 0.0) for position in ordered_positions) / trade_count if trade_count else None
        )
        drawdowns = [position.max_drawdown_pct for position in ordered_positions if position.max_drawdown_pct is not None]
        avg_drawdown_pct = self._round(sum(drawdowns) / len(drawdowns) if drawdowns else None)
        win_rate = self._round((wins / trade_count) * 100 if trade_count else None)
        profit_factor = self._calculate_profit_factor(ordered_positions)

        replay = self._build_trade_replay(ordered_positions)
        rolling_windows = self._build_rolling_windows(ordered_positions, thresholds)
        rolling_pass_count = len([window for window in rolling_windows if window["status"] == "pass"])
        window_count = len(rolling_windows)
        rolling_pass_rate = self._round(rolling_pass_count / window_count if window_count else None)
        replay_score = replay["replay_score"]
        context_bundles = self._list_context_bundles(session, candidate=candidate)

        evaluation_status, reasons = self._classify_candidate(
            trade_count=trade_count,
            wins=wins,
            losses=losses,
            distinct_tickers=distinct_tickers,
            avg_pnl_pct=avg_pnl_pct,
            avg_drawdown_pct=avg_drawdown_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            rolling_pass_rate=rolling_pass_rate,
            replay_score=replay_score,
            window_count=window_count,
            thresholds=thresholds,
        )
        decision_reason = "; ".join(reasons)

        return CandidateValidationSummaryRead(
            strategy_id=candidate.strategy_id,
            candidate_version_id=candidate.id,
            candidate_version_number=candidate.version,
            trade_count=trade_count,
            wins=wins,
            losses=losses,
            avg_pnl_pct=avg_pnl_pct,
            avg_drawdown_pct=avg_drawdown_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            distinct_tickers=distinct_tickers,
            window_count=window_count,
            rolling_pass_rate=rolling_pass_rate,
            replay_score=replay_score,
            validation_mode="trade_replay_rolling",
            evaluation_status=evaluation_status,
            decision_reason=decision_reason,
            validation_payload={
                "thresholds": asdict(thresholds),
                "replay": replay,
                "rolling_windows": rolling_windows,
                "trade_ids": [position.id for position in ordered_positions],
                "context_bundles": context_bundles,
            },
        )

    def _resolve_thresholds(self, candidate: StrategyVersion) -> CandidateValidationThresholds:
        general_rules = candidate.general_rules if isinstance(candidate.general_rules, dict) else {}
        parameters = candidate.parameters if isinstance(candidate.parameters, dict) else {}

        def int_override(key: str, default: int) -> int:
            raw = parameters.get(key, general_rules.get(key, default))
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                return default

        def float_override(key: str, default: float) -> float:
            raw = parameters.get(key, general_rules.get(key, default))
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        return CandidateValidationThresholds(
            min_trade_count=int_override("candidate_validation_min_trades", 4),
            min_distinct_tickers=int_override("candidate_validation_min_distinct_tickers", 2),
            min_avg_pnl_pct=float_override("candidate_validation_min_avg_pnl_pct", 1.0),
            min_win_rate=float_override("candidate_validation_min_win_rate", 60.0),
            min_profit_factor=float_override("candidate_validation_min_profit_factor", 1.2),
            max_avg_drawdown_pct=float_override("candidate_validation_max_avg_drawdown_pct", -4.0),
            rolling_window_size=int_override("candidate_validation_window_size", 3),
            min_rolling_pass_rate=float_override("candidate_validation_min_rolling_pass_rate", 0.5),
            min_windows=int_override("candidate_validation_min_windows", 2),
            min_replay_score=float_override("candidate_validation_min_replay_score", 0.58),
            reject_trade_count=int_override("candidate_validation_reject_min_trades", 4),
            reject_max_avg_pnl_pct=float_override("candidate_validation_reject_max_avg_pnl_pct", 0.0),
            reject_max_profit_factor=float_override("candidate_validation_reject_max_profit_factor", 0.95),
            reject_max_rolling_pass_rate=float_override("candidate_validation_reject_max_rolling_pass_rate", 0.34),
            reject_max_replay_score=float_override("candidate_validation_reject_max_replay_score", 0.45),
        )

    @staticmethod
    def _calculate_profit_factor(positions: list[Position]) -> float | None:
        if not positions:
            return None
        gross_profit = sum(max(position.pnl_pct or 0.0, 0.0) for position in positions)
        gross_loss = abs(sum(min(position.pnl_pct or 0.0, 0.0) for position in positions))
        if gross_loss == 0:
            return round(gross_profit, 2) if gross_profit else None
        return round(gross_profit / gross_loss, 2)

    def _build_trade_replay(self, positions: list[Position]) -> dict:
        equity = 100.0
        peak_equity = equity
        worst_drawdown_pct = 0.0
        max_loss_streak = 0
        max_win_streak = 0
        loss_streak = 0
        win_streak = 0
        curve: list[dict] = []

        for index, position in enumerate(positions, start=1):
            pnl_pct = float(position.pnl_pct or 0.0)
            equity = equity * (1.0 + (pnl_pct / 100.0))
            peak_equity = max(peak_equity, equity)
            equity_drawdown_pct = ((equity / peak_equity) - 1.0) * 100 if peak_equity else 0.0
            worst_drawdown_pct = min(worst_drawdown_pct, equity_drawdown_pct)
            if pnl_pct > 0:
                win_streak += 1
                loss_streak = 0
            elif pnl_pct < 0:
                loss_streak += 1
                win_streak = 0
            else:
                loss_streak = 0
                win_streak = 0
            max_loss_streak = max(max_loss_streak, loss_streak)
            max_win_streak = max(max_win_streak, win_streak)
            curve.append(
                {
                    "trade_index": index,
                    "position_id": position.id,
                    "ticker": position.ticker,
                    "pnl_pct": round(pnl_pct, 2),
                    "equity": round(equity, 2),
                    "equity_drawdown_pct": round(equity_drawdown_pct, 2),
                }
            )

        total_return_pct = round(((equity / 100.0) - 1.0) * 100.0, 2) if positions else None
        win_rate = (len([position for position in positions if (position.pnl_pct or 0.0) > 0]) / len(positions)) * 100 if positions else 0.0
        return_component = min(max(((total_return_pct or 0.0) + 5.0) / 20.0, 0.0), 1.0)
        win_component = min(max(win_rate / 100.0, 0.0), 1.0)
        drawdown_component = 1.0 - min(abs(worst_drawdown_pct) / 12.0, 1.0)
        streak_component = 1.0 - min(max_loss_streak / 4.0, 1.0)
        replay_score = self._round(
            ((return_component * 0.35) + (win_component * 0.25) + (drawdown_component * 0.25) + (streak_component * 0.15))
            if positions
            else None
        )
        return {
            "starting_equity": 100.0,
            "ending_equity": round(equity, 2),
            "total_return_pct": total_return_pct,
            "max_equity_drawdown_pct": round(worst_drawdown_pct, 2),
            "max_loss_streak": max_loss_streak,
            "max_win_streak": max_win_streak,
            "replay_score": replay_score,
            "equity_curve": curve,
        }

    def _build_rolling_windows(
        self,
        positions: list[Position],
        thresholds: CandidateValidationThresholds,
    ) -> list[dict]:
        if len(positions) < thresholds.rolling_window_size:
            return []

        windows: list[dict] = []
        target_win_rate = max(50.0, thresholds.min_win_rate - 5.0)
        target_avg_pnl = max(0.5, thresholds.min_avg_pnl_pct - 0.25)
        target_profit_factor = max(1.0, thresholds.min_profit_factor - 0.15)

        for start in range(0, len(positions) - thresholds.rolling_window_size + 1):
            window_positions = positions[start : start + thresholds.rolling_window_size]
            wins = len([position for position in window_positions if (position.pnl_pct or 0.0) > 0])
            losses = len(window_positions) - wins
            avg_pnl_pct = self._round(
                sum((position.pnl_pct or 0.0) for position in window_positions) / len(window_positions)
            )
            win_rate = self._round((wins / len(window_positions)) * 100)
            profit_factor = self._calculate_profit_factor(window_positions)
            distinct_tickers = len({position.ticker for position in window_positions})
            passes = (
                avg_pnl_pct is not None
                and avg_pnl_pct >= target_avg_pnl
                and win_rate is not None
                and win_rate >= target_win_rate
                and profit_factor is not None
                and profit_factor >= target_profit_factor
                and distinct_tickers >= min(2, len(window_positions))
            )
            windows.append(
                {
                    "window_index": len(windows) + 1,
                    "from_trade": start + 1,
                    "to_trade": start + len(window_positions),
                    "trade_count": len(window_positions),
                    "wins": wins,
                    "losses": losses,
                    "avg_pnl_pct": avg_pnl_pct,
                    "win_rate": win_rate,
                    "profit_factor": profit_factor,
                    "distinct_tickers": distinct_tickers,
                    "status": "pass" if passes else "fail",
                }
            )
        return windows

    def _classify_candidate(
        self,
        *,
        trade_count: int,
        wins: int,
        losses: int,
        distinct_tickers: int,
        avg_pnl_pct: float | None,
        avg_drawdown_pct: float | None,
        win_rate: float | None,
        profit_factor: float | None,
        rolling_pass_rate: float | None,
        replay_score: float | None,
        window_count: int,
        thresholds: CandidateValidationThresholds,
    ) -> tuple[str, list[str]]:
        insufficient_reasons: list[str] = []
        if trade_count < thresholds.min_trade_count:
            insufficient_reasons.append(
                f"needs at least {thresholds.min_trade_count} validation trades and only has {trade_count}"
            )
        if distinct_tickers < thresholds.min_distinct_tickers:
            insufficient_reasons.append(
                f"needs at least {thresholds.min_distinct_tickers} distinct tickers and only has {distinct_tickers}"
            )
        if window_count < thresholds.min_windows:
            insufficient_reasons.append(
                f"needs at least {thresholds.min_windows} rolling windows and only has {window_count}"
            )
        if insufficient_reasons:
            return "insufficient_data", insufficient_reasons

        promote_checks = [
            (avg_pnl_pct is not None and avg_pnl_pct >= thresholds.min_avg_pnl_pct, f"avg pnl {avg_pnl_pct}% >= {thresholds.min_avg_pnl_pct}%"),
            (win_rate is not None and win_rate >= thresholds.min_win_rate, f"win rate {win_rate}% >= {thresholds.min_win_rate}%"),
            (profit_factor is not None and profit_factor >= thresholds.min_profit_factor, f"profit factor {profit_factor} >= {thresholds.min_profit_factor}"),
            (
                avg_drawdown_pct is not None and avg_drawdown_pct >= thresholds.max_avg_drawdown_pct,
                f"avg drawdown {avg_drawdown_pct}% >= {thresholds.max_avg_drawdown_pct}%",
            ),
            (
                rolling_pass_rate is not None and rolling_pass_rate >= thresholds.min_rolling_pass_rate,
                f"rolling pass rate {rolling_pass_rate} >= {thresholds.min_rolling_pass_rate}",
            ),
            (
                replay_score is not None and replay_score >= thresholds.min_replay_score,
                f"replay score {replay_score} >= {thresholds.min_replay_score}",
            ),
        ]
        if all(check for check, _ in promote_checks) and wins > losses:
            return "promote", [detail for _, detail in promote_checks]

        reject_checks = [
            trade_count >= thresholds.reject_trade_count,
            avg_pnl_pct is not None and avg_pnl_pct <= thresholds.reject_max_avg_pnl_pct,
            profit_factor is not None and profit_factor <= thresholds.reject_max_profit_factor,
            rolling_pass_rate is not None and rolling_pass_rate <= thresholds.reject_max_rolling_pass_rate,
            replay_score is not None and replay_score <= thresholds.reject_max_replay_score,
            losses >= ceil(trade_count / 2),
        ]
        if all(reject_checks):
            return "reject", [
                f"avg pnl {avg_pnl_pct}% <= {thresholds.reject_max_avg_pnl_pct}%",
                f"profit factor {profit_factor} <= {thresholds.reject_max_profit_factor}",
                f"rolling pass rate {rolling_pass_rate} <= {thresholds.reject_max_rolling_pass_rate}",
                f"replay score {replay_score} <= {thresholds.reject_max_replay_score}",
                f"losses {losses} dominate wins {wins}",
            ]

        observe_reasons = [
            f"avg pnl={avg_pnl_pct}%",
            f"win rate={win_rate}%",
            f"profit factor={profit_factor}",
            f"rolling pass rate={rolling_pass_rate}",
            f"replay score={replay_score}",
        ]
        return "observe", observe_reasons

    def _list_context_bundles(
        self,
        session: Session,
        *,
        candidate: StrategyVersion,
        limit: int = 4,
    ) -> list[dict]:
        rules = list(
            session.scalars(
                select(StrategyContextRule).where(
                    StrategyContextRule.status == "active",
                    or_(
                        StrategyContextRule.strategy_version_id == candidate.id,
                        StrategyContextRule.strategy_id == candidate.strategy_id,
                    ),
                )
            ).all()
        )
        rules.sort(
            key=lambda rule: (
                0 if rule.feature_scope == "combo" else 1,
                -(float(rule.confidence or 0.0)),
                rule.id,
            )
        )
        bundles: list[dict] = []
        for rule in rules[:limit]:
            evidence_payload = rule.evidence_payload if isinstance(rule.evidence_payload, dict) else {}
            bundles.append(
                {
                    "rule_id": rule.id,
                    "feature_scope": rule.feature_scope,
                    "feature_key": rule.feature_key,
                    "feature_value": rule.feature_value,
                    "action_type": rule.action_type,
                    "confidence": rule.confidence,
                    "sample_size": evidence_payload.get("sample_size"),
                    "rationale": rule.rationale,
                }
            )
        return bundles

    @staticmethod
    def _round(value: float | None) -> float | None:
        return round(value, 2) if value is not None else None
