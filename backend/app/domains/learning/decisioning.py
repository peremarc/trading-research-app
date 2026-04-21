from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from time import perf_counter

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models.decision_context import StrategyContextRule
from app.db.models.position import Position
from app.db.models.strategy import StrategyVersion
from app.domains.learning.macro import MacroContextService
from app.domains.learning.protocol import build_regime_policy_context, infer_candidate_playbook
from app.domains.learning.relevance import StrategyContextAdaptationService
from app.domains.learning.skills import SkillLifecycleService, SkillRouterService
from app.domains.market.services import (
    CalendarService,
    MSTRContextService,
    MarketDataService,
    MarketDataUnavailableError,
    NewsService,
)
from app.providers.calendar import CalendarProviderError
from app.providers.news import NewsProviderError


class RiskBudgetService:
    SECTOR_HINTS = {
        "AAPL": "technology",
        "AAL": "airlines",
        "AMD": "technology",
        "ALK": "airlines",
        "AMZN": "consumer_discretionary",
        "AVGO": "technology",
        "CRM": "technology",
        "DAL": "airlines",
        "DDOG": "technology",
        "GOOGL": "communication_services",
        "JBLU": "airlines",
        "JETS": "airlines",
        "MDB": "technology",
        "META": "communication_services",
        "MSFT": "technology",
        "NFLX": "communication_services",
        "NOW": "technology",
        "NVDA": "technology",
        "PINS": "communication_services",
        "PLTR": "technology",
        "ROKU": "communication_services",
        "SHOP": "technology",
        "SKYW": "airlines",
        "SNAP": "communication_services",
        "SNOW": "technology",
        "SQ": "financials",
        "TSLA": "consumer_discretionary",
        "UAL": "airlines",
        "U": "technology",
        "UBER": "consumer_discretionary",
        "LUV": "airlines",
    }

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_trade_candidate_budget(
        self,
        session: Session,
        *,
        ticker: str,
        strategy_version_id: int | None,
        strategy_rules: dict,
        macro_fit: dict,
        calendar_context: dict,
        mstr_context: dict | None = None,
        market_context: dict | None = None,
        signal_payload: dict | None = None,
    ) -> dict:
        market_context = dict(market_context or {})
        signal_payload = dict(signal_payload or {})
        candidate_profile = self._build_candidate_profile(
            ticker=ticker,
            strategy_rules=strategy_rules,
            macro_fit=macro_fit,
            calendar_context=calendar_context,
            mstr_context=mstr_context,
            market_context=market_context,
            signal_payload=signal_payload,
        )
        open_positions = list(session.scalars(select(Position).where(Position.status == "open")).all())
        open_profiles = [self._extract_position_profile(position) for position in open_positions]

        same_ticker_profiles = [item for item in open_profiles if item["ticker"] == ticker.upper()]
        same_strategy_profiles = [
            item for item in open_profiles if strategy_version_id is not None and item["strategy_version_id"] == strategy_version_id
        ]
        same_sector_profiles = [
            item
            for item in open_profiles
            if candidate_profile["sector_tag"] != "unknown" and item["sector_tag"] == candidate_profile["sector_tag"]
        ]
        same_regime_profiles = [
            item for item in open_profiles if set(item["regime_tags"]).intersection(candidate_profile["regime_tags"])
        ]
        event_risk_profiles = [
            item
            for item in open_profiles
            if candidate_profile["event_risk_flags"]
            and set(item["event_risk_flags"]).intersection(candidate_profile["event_risk_flags"])
        ]

        capital_base = self._coerce_positive_float(strategy_rules.get("paper_capital_base")) or self.settings.paper_portfolio_capital_base
        per_trade_risk_fraction = (
            self._coerce_positive_float(strategy_rules.get("risk_per_trade_fraction"))
            or self.settings.paper_risk_per_trade_fraction
        )
        max_portfolio_risk_fraction = (
            self._coerce_positive_float(strategy_rules.get("max_portfolio_risk_fraction"))
            or self.settings.paper_max_portfolio_risk_fraction
        )
        max_notional_fraction_per_trade = (
            self._coerce_positive_float(strategy_rules.get("max_notional_fraction_per_trade"))
            or self.settings.paper_max_notional_fraction_per_trade
        )
        daily_drawdown_limit_pct = (
            self._coerce_float(strategy_rules.get("daily_drawdown_limit_pct"))
            if self._coerce_float(strategy_rules.get("daily_drawdown_limit_pct")) is not None
            else self.settings.paper_daily_drawdown_limit_pct
        )
        weekly_drawdown_limit_pct = (
            self._coerce_float(strategy_rules.get("weekly_drawdown_limit_pct"))
            if self._coerce_float(strategy_rules.get("weekly_drawdown_limit_pct")) is not None
            else self.settings.paper_weekly_drawdown_limit_pct
        )
        max_same_sector_positions = self._coerce_optional_positive_int(strategy_rules.get("max_same_sector_positions"))
        max_positions_per_regime = self._coerce_optional_positive_int(strategy_rules.get("max_positions_per_regime"))
        max_event_risk_positions = self._coerce_optional_positive_int(strategy_rules.get("max_event_risk_positions"))

        used_portfolio_risk_amount = round(sum(item["open_risk_amount"] for item in open_profiles), 2)
        used_portfolio_notional = round(sum(item["open_notional"] for item in open_profiles), 2)
        max_portfolio_risk_amount = round(capital_base * max_portfolio_risk_fraction, 2)
        remaining_portfolio_risk_amount = round(max(max_portfolio_risk_amount - used_portfolio_risk_amount, 0.0), 2)
        per_trade_risk_amount = round(min(capital_base * per_trade_risk_fraction, remaining_portfolio_risk_amount), 2)

        now = datetime.now(UTC)
        daily_cutoff = now - timedelta(days=1)
        weekly_cutoff = now - timedelta(days=7)
        closed_positions = list(session.scalars(select(Position).where(Position.status == "closed")).all())
        daily_realized_pnl_pct = round(
            sum((position.pnl_pct or 0.0) for position in closed_positions if self._is_after_cutoff(position.exit_date, daily_cutoff)),
            2,
        )
        weekly_realized_pnl_pct = round(
            sum((position.pnl_pct or 0.0) for position in closed_positions if self._is_after_cutoff(position.exit_date, weekly_cutoff)),
            2,
        )

        kill_switch_reasons: list[str] = []
        if daily_realized_pnl_pct <= daily_drawdown_limit_pct:
            kill_switch_reasons.append(
                f"daily realized pnl {daily_realized_pnl_pct}% breached limit {daily_drawdown_limit_pct}%"
            )
        if weekly_realized_pnl_pct <= weekly_drawdown_limit_pct:
            kill_switch_reasons.append(
                f"weekly realized pnl {weekly_realized_pnl_pct}% breached limit {weekly_drawdown_limit_pct}%"
            )

        exposure_block_reasons: list[str] = []
        advisories: list[str] = []
        if (
            max_same_sector_positions is not None
            and candidate_profile["sector_tag"] != "unknown"
            and len(same_sector_profiles) >= max_same_sector_positions
        ):
            exposure_block_reasons.append(
                f"sector exposure already has {len(same_sector_profiles)} open positions and max is {max_same_sector_positions}"
            )
        elif same_sector_profiles:
            advisories.append(
                f"sector '{candidate_profile['sector_tag']}' already has {len(same_sector_profiles)} open paper position(s)"
            )

        if max_positions_per_regime is not None and candidate_profile["regime_tags"] and len(same_regime_profiles) >= max_positions_per_regime:
            exposure_block_reasons.append(
                f"macro regime exposure already has {len(same_regime_profiles)} open positions and max is {max_positions_per_regime}"
            )
        elif same_regime_profiles:
            advisories.append(
                f"active regime overlap already covers {len(same_regime_profiles)} open position(s)"
            )

        if max_event_risk_positions is not None and candidate_profile["event_risk_flags"] and len(event_risk_profiles) >= max_event_risk_positions:
            exposure_block_reasons.append(
                f"event-risk exposure already has {len(event_risk_profiles)} open positions and max is {max_event_risk_positions}"
            )
        elif event_risk_profiles and candidate_profile["event_risk_flags"]:
            advisories.append(
                f"event-risk overlap already covers {len(event_risk_profiles)} open position(s)"
            )
        mstr_context_payload = (
            candidate_profile.get("mstr_context") if isinstance(candidate_profile.get("mstr_context"), dict) else {}
        )
        if mstr_context_payload.get("applicable"):
            atm_risk = str(mstr_context_payload.get("atm_risk_context") or "").strip()
            preference = str(mstr_context_payload.get("exposure_preference") or "").strip()
            if atm_risk == "high":
                advisories.append("MSTR carries elevated ATM/dilution risk; keep fresh size conservative")
            elif atm_risk == "moderate":
                advisories.append("MSTR is in the opportunistic ATM band; avoid treating valuation as benign")
            if preference and preference != "neutral":
                advisories.append(f"MSTR context preference: {preference}")
        expiry_context = (
            candidate_profile.get("expiry_context") if isinstance(candidate_profile.get("expiry_context"), dict) else {}
        )
        expiry_reason = str(expiry_context.get("reason") or "").strip()
        if expiry_context.get("expiry_day"):
            advisories.append("quarterly expiry day active; keep fresh risk very selective")
        elif expiry_context.get("phase") == "tight_pre_expiry_window":
            advisories.append("quarterly expiry is within T-1; tighten fresh-risk appetite")
        elif expiry_context.get("pre_expiry_window"):
            advisories.append("quarterly expiry window active; penalize marginal entries")
        elif expiry_context.get("post_expiry_window"):
            advisories.append("post-expiry cleanup window active; prefer re-evaluation over forcing entries")
        if expiry_reason and expiry_reason not in advisories:
            advisories.append(expiry_reason)

        return {
            "candidate_profile": candidate_profile,
            "capital_base": round(capital_base, 2),
            "per_trade_risk_fraction": round(per_trade_risk_fraction, 4),
            "max_portfolio_risk_fraction": round(max_portfolio_risk_fraction, 4),
            "max_notional_fraction_per_trade": round(max_notional_fraction_per_trade, 4),
            "per_trade_risk_amount": per_trade_risk_amount,
            "max_portfolio_risk_amount": max_portfolio_risk_amount,
            "used_portfolio_risk_amount": used_portfolio_risk_amount,
            "remaining_portfolio_risk_amount": remaining_portfolio_risk_amount,
            "used_portfolio_notional": used_portfolio_notional,
            "kill_switch": {
                "triggered": bool(kill_switch_reasons),
                "reasons": kill_switch_reasons,
                "daily_realized_pnl_pct": daily_realized_pnl_pct,
                "weekly_realized_pnl_pct": weekly_realized_pnl_pct,
            },
            "limits": {
                "max_same_sector_positions": max_same_sector_positions,
                "max_positions_per_regime": max_positions_per_regime,
                "max_event_risk_positions": max_event_risk_positions,
                "daily_drawdown_limit_pct": daily_drawdown_limit_pct,
                "weekly_drawdown_limit_pct": weekly_drawdown_limit_pct,
            },
            "exposure_block_reasons": exposure_block_reasons,
            "advisories": advisories,
            "portfolio": {
                "open_positions_total": len(open_profiles),
                "same_ticker_open_positions": len(same_ticker_profiles),
                "same_strategy_open_positions": len(same_strategy_profiles),
                "same_sector_open_positions": len(same_sector_profiles),
                "same_regime_open_positions": len(same_regime_profiles),
                "event_risk_open_positions": len(event_risk_profiles),
                "same_ticker_position_ids": [item["position_id"] for item in same_ticker_profiles],
                "same_strategy_position_ids": [item["position_id"] for item in same_strategy_profiles],
                "same_sector_position_ids": [item["position_id"] for item in same_sector_profiles],
                "same_regime_position_ids": [item["position_id"] for item in same_regime_profiles],
                "event_risk_position_ids": [item["position_id"] for item in event_risk_profiles],
                "candidate_sector_tag": candidate_profile["sector_tag"],
                "candidate_regime_tags": candidate_profile["regime_tags"],
                "candidate_event_risk_flags": candidate_profile["event_risk_flags"],
                "candidate_context_risk_flags": candidate_profile.get("context_risk_flags", []),
                "used_portfolio_risk_amount": used_portfolio_risk_amount,
                "remaining_portfolio_risk_amount": remaining_portfolio_risk_amount,
                "same_ticker_open_risk_amount": round(sum(item["open_risk_amount"] for item in same_ticker_profiles), 2),
                "same_strategy_open_risk_amount": round(sum(item["open_risk_amount"] for item in same_strategy_profiles), 2),
                "same_sector_open_risk_amount": round(sum(item["open_risk_amount"] for item in same_sector_profiles), 2),
                "same_regime_open_risk_amount": round(sum(item["open_risk_amount"] for item in same_regime_profiles), 2),
            },
        }

    def _build_candidate_profile(
        self,
        *,
        ticker: str,
        strategy_rules: dict,
        macro_fit: dict,
        calendar_context: dict,
        mstr_context: dict | None,
        market_context: dict,
        signal_payload: dict,
    ) -> dict:
        sector_tag = (
            self._coerce_string(market_context.get("sector_tag"))
            or self._coerce_string(signal_payload.get("sector_tag"))
            or self._coerce_string(strategy_rules.get("sector_tag"))
            or self.SECTOR_HINTS.get(ticker.upper(), "unknown")
        )
        active_regimes = [
            item
            for item in macro_fit.get("active_regimes", [])
            if isinstance(item, str) and item.strip()
        ]
        expiry_context = (
            calendar_context.get("expiry_context") if isinstance(calendar_context.get("expiry_context"), dict) else {}
        )
        event_risk_flags: list[str] = []
        if isinstance(calendar_context.get("near_earnings_days"), int):
            event_risk_flags.append("near_earnings")
        if isinstance(calendar_context.get("near_macro_high_impact_days"), int):
            event_risk_flags.append("near_macro_high_impact")
        if expiry_context.get("pre_expiry_window"):
            event_risk_flags.append("quarterly_expiry_window")
        if expiry_context.get("phase") == "tight_pre_expiry_window":
            event_risk_flags.append("quarterly_expiry_tight_window")
        if expiry_context.get("expiry_day"):
            event_risk_flags.append("quarterly_expiry_day")
        context_risk_flags: list[str] = []
        if isinstance(mstr_context, dict) and mstr_context.get("applicable"):
            if str(mstr_context.get("atm_risk_context") or "").strip() == "high":
                context_risk_flags.append("mstr_atm_risk_high")
            if bool(mstr_context.get("share_dilution_accelerating")):
                context_risk_flags.append("mstr_dilution_accelerating")

        return {
            "ticker": ticker.upper(),
            "sector_tag": sector_tag,
            "regime_tags": active_regimes[:3],
            "event_risk_flags": event_risk_flags,
            "context_risk_flags": context_risk_flags,
            "expiry_context": dict(expiry_context),
            "mstr_context": dict(mstr_context or {}),
            "execution_mode": self._coerce_string(market_context.get("execution_mode")) or "default",
        }

    @staticmethod
    def _extract_position_profile(position: Position) -> dict:
        entry_context = dict(position.entry_context or {})
        risk_budget = dict(entry_context.get("risk_budget") or {})
        candidate_profile = dict(risk_budget.get("candidate_profile") or {})
        sizing = dict(entry_context.get("position_sizing") or {})
        risk_amount = RiskBudgetService._coerce_positive_float(sizing.get("risk_amount"))
        if risk_amount is None:
            risk_amount = RiskBudgetService._estimate_open_risk_amount(position)
        notional = RiskBudgetService._coerce_positive_float(sizing.get("notional")) or round(position.entry_price * position.size, 2)
        return {
            "position_id": position.id,
            "ticker": position.ticker.upper(),
            "strategy_version_id": position.strategy_version_id,
            "sector_tag": RiskBudgetService._coerce_string(candidate_profile.get("sector_tag")) or "unknown",
            "regime_tags": [
                item
                for item in candidate_profile.get("regime_tags", [])
                if isinstance(item, str) and item.strip()
            ],
            "event_risk_flags": [
                item
                for item in candidate_profile.get("event_risk_flags", [])
                if isinstance(item, str) and item.strip()
            ],
            "context_risk_flags": [
                item
                for item in candidate_profile.get("context_risk_flags", [])
                if isinstance(item, str) and item.strip()
            ],
            "open_risk_amount": round(risk_amount or 0.0, 2),
            "open_notional": round(notional, 2),
        }

    @staticmethod
    def _estimate_open_risk_amount(position: Position) -> float:
        if position.stop_price is None:
            return 0.0
        risk_per_unit = abs(position.entry_price - position.stop_price)
        return round(risk_per_unit * position.size, 2)

    @staticmethod
    def _is_after_cutoff(raw: datetime | None, cutoff: datetime) -> bool:
        if raw is None:
            return False
        timestamp = raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC) >= cutoff

    @staticmethod
    def _coerce_positive_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_optional_positive_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value.is_integer() and value > 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _coerce_string(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None


class RegimePolicyService:
    def evaluate_trade_candidate_policy(
        self,
        *,
        signal_payload: dict,
        market_context: dict,
        portfolio: dict,
        risk_budget: dict,
    ) -> dict:
        playbook_code = infer_candidate_playbook(signal_payload).code
        policy = build_regime_policy_context(
            regime_label=market_context.get("market_state_regime"),
            playbook_code=playbook_code,
        )
        candidate_profile = risk_budget.get("candidate_profile") if isinstance(risk_budget.get("candidate_profile"), dict) else {}
        event_risk_flags = [
            str(item).strip()
            for item in candidate_profile.get("event_risk_flags", [])
            if isinstance(item, str) and str(item).strip()
        ]
        opened_positions_so_far = self._coerce_non_negative_int(market_context.get("opened_positions_so_far")) or 0
        allowed_playbooks = [
            str(item).strip()
            for item in policy.get("allowed_playbooks", [])
            if isinstance(item, str) and str(item).strip()
        ]
        blocked_playbooks = [
            str(item).strip()
            for item in policy.get("blocked_playbooks", [])
            if isinstance(item, str) and str(item).strip()
        ]
        risk_multiplier = self._coerce_non_negative_float(policy.get("risk_multiplier"))
        if risk_multiplier is None:
            risk_multiplier = 1.0
        max_new_positions = self._coerce_non_negative_int(policy.get("max_new_positions")) or 0
        playbook_allowed = bool(policy.get("playbook_allowed", True))
        blocked_reason = str(policy.get("blocked_reason") or "").strip() or None

        if not playbook_allowed and blocked_reason is None:
            blocked_reason = (
                f"playbook '{playbook_code}' is blocked under regime '{policy.get('regime_label') or 'default'}'"
            )
        if blocked_reason is None and max_new_positions <= 0:
            blocked_reason = "regime policy blocks new entries in the current market regime"
        if blocked_reason is None and opened_positions_so_far >= max_new_positions and max_new_positions > 0:
            blocked_reason = (
                f"regime policy already used {opened_positions_so_far} new position(s) and max is {max_new_positions}"
            )
        if blocked_reason is None and bool(policy.get("block_on_event_risk")) and event_risk_flags:
            blocked_reason = "regime policy blocks fresh risk while near event risk: " + ", ".join(event_risk_flags)
        if blocked_reason is None and risk_multiplier <= 0:
            blocked_reason = "regime policy sets the risk multiplier to zero in the current market regime"

        entry_allowed = blocked_reason is None
        advisories: list[str] = []
        if entry_allowed and risk_multiplier < 1.0:
            advisories.append(f"regime policy reduces fresh risk to {round(risk_multiplier * 100)}% of baseline")
        if entry_allowed and max_new_positions > 0:
            remaining_slots = max(max_new_positions - opened_positions_so_far, 0)
            advisories.append(f"regime policy leaves {remaining_slots} new position slot(s) in this cycle")
        if entry_allowed and bool(policy.get("block_on_event_risk")) and not event_risk_flags:
            advisories.append("regime policy would block fresh exposure if near-event risk appears")

        return {
            **policy,
            "playbook": playbook_code,
            "allowed_playbooks": allowed_playbooks,
            "blocked_playbooks": blocked_playbooks,
            "risk_multiplier": round(risk_multiplier, 2),
            "max_new_positions": max_new_positions,
            "opened_positions_so_far": opened_positions_so_far,
            "candidate_event_risk_flags": event_risk_flags,
            "entry_allowed": entry_allowed,
            "blocked_reason": blocked_reason,
            "advisories": advisories,
            "open_positions_total": int(portfolio.get("open_positions_total") or 0),
        }

    @staticmethod
    def _coerce_non_negative_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, float) and value.is_integer() and value >= 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @staticmethod
    def _coerce_non_negative_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) >= 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None


class PositionSizingService:
    MIN_POSITION_SIZE = 0.01
    DEFAULT_STOP_ATR_MULTIPLE = 1.5

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def size_trade_candidate(self, *, signal_payload: dict, decision_context: dict) -> dict:
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        strategy_rules = decision_context.get("strategy_rules") if isinstance(decision_context.get("strategy_rules"), dict) else {}
        portfolio = decision_context.get("portfolio") if isinstance(decision_context.get("portfolio"), dict) else {}
        risk_budget = decision_context.get("risk_budget") if isinstance(decision_context.get("risk_budget"), dict) else {}
        regime_policy = (
            decision_context.get("regime_policy")
            if isinstance(decision_context.get("regime_policy"), dict)
            else risk_budget.get("regime_policy")
            if isinstance(risk_budget.get("regime_policy"), dict)
            else {}
        )
        kill_switch = risk_budget.get("kill_switch") if isinstance(risk_budget.get("kill_switch"), dict) else {}
        blocked_reasons = list(risk_budget.get("exposure_block_reasons") or [])

        if kill_switch.get("triggered"):
            blocked_reasons.extend(
                str(item) for item in kill_switch.get("reasons", []) if isinstance(item, str) and item.strip()
            )
        regime_blocked_reason = str(regime_policy.get("blocked_reason") or "").strip()
        if regime_policy.get("entry_allowed") is False and regime_blocked_reason:
            blocked_reasons.append(regime_blocked_reason)

        entry_price = self._coerce_positive_float(signal_payload.get("entry_price"))
        if entry_price is None:
            return self._blocked_result(risk_budget=risk_budget, summary="Position sizing blocked because entry price is missing or invalid.")

        effective_stop_price, risk_per_unit, stop_source = self._resolve_stop(
            entry_price=entry_price,
            stop_price=signal_payload.get("stop_price"),
            atr_14=quant.get("atr_14"),
            default_stop_atr_multiple=self._coerce_positive_float(strategy_rules.get("default_stop_atr_multiple"))
            or self.DEFAULT_STOP_ATR_MULTIPLE,
        )
        if effective_stop_price is None or risk_per_unit is None:
            return self._blocked_result(risk_budget=risk_budget, summary="Position sizing blocked because no valid stop distance is available.")

        remaining_portfolio_risk_amount = self._coerce_positive_float(risk_budget.get("remaining_portfolio_risk_amount")) or 0.0
        target_risk_amount = self._coerce_positive_float(risk_budget.get("per_trade_risk_amount")) or 0.0
        if remaining_portfolio_risk_amount <= 0 or target_risk_amount <= 0:
            blocked_reasons.append("no remaining portfolio risk budget is available")

        conviction = self._coerce_float(signal_payload.get("decision_confidence")) or self._coerce_float(signal_payload.get("combined_score")) or 0.0
        risk_reward = self._coerce_positive_float(signal_payload.get("risk_reward") or quant.get("risk_reward")) or 0.0
        conviction_multiplier = self._conviction_multiplier(conviction)
        reward_multiplier = self._reward_multiplier(risk_reward)
        exposure_multiplier = self._exposure_multiplier(portfolio)
        event_multiplier = self._event_multiplier(risk_budget)
        specialized_context_multiplier = self._specialized_context_multiplier(risk_budget)
        regime_multiplier = self._coerce_non_negative_float(regime_policy.get("risk_multiplier"))
        if regime_multiplier is None:
            regime_multiplier = 1.0

        adjusted_risk_amount = (
            target_risk_amount
            * conviction_multiplier
            * reward_multiplier
            * exposure_multiplier
            * event_multiplier
            * specialized_context_multiplier
            * regime_multiplier
        )
        adjusted_risk_amount = round(min(adjusted_risk_amount, remaining_portfolio_risk_amount), 2)
        if adjusted_risk_amount <= 0:
            blocked_reasons.append("adjusted risk budget is not large enough to size the trade")

        capital_base = self._coerce_positive_float(risk_budget.get("capital_base")) or self.settings.paper_portfolio_capital_base
        max_notional_fraction = (
            self._coerce_positive_float(risk_budget.get("max_notional_fraction_per_trade"))
            or self.settings.paper_max_notional_fraction_per_trade
        )
        max_notional_amount = round(capital_base * max_notional_fraction, 2)

        size_by_risk = adjusted_risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
        size_by_notional = max_notional_amount / entry_price if entry_price > 0 else 0.0
        raw_size = min(size_by_risk, size_by_notional)
        size = round(raw_size, 4)
        if size < self.MIN_POSITION_SIZE:
            blocked_reasons.append("calculated size falls below the minimum meaningful position size")

        if blocked_reasons:
            summary = "Position sizing blocked because " + "; ".join(blocked_reasons[:3]) + "."
            return self._blocked_result(risk_budget=risk_budget, summary=summary, reasons=blocked_reasons)

        notional = round(size * entry_price, 2)
        risk_amount = round(size * risk_per_unit, 2)
        status = "ready"
        reasons = [f"risk per unit {round(risk_per_unit, 2)} from {stop_source} stop"]
        if size_by_notional < size_by_risk:
            status = "capped_by_notional"
            reasons.append("size capped by per-trade notional limit")
        if exposure_multiplier < 1.0:
            reasons.append("size reduced by aggregate exposure overlap")
        if event_multiplier < 1.0:
            reasons.append("size reduced by near-event risk")
        if specialized_context_multiplier < 1.0:
            reasons.append("size reduced by specialized context risk")
        if regime_multiplier < 1.0:
            reasons.append("size reduced by regime policy")
        if stop_source == "atr_fallback":
            reasons.append("signal stop was replaced by ATR fallback for sizing")

        sizing = {
            "status": status,
            "size": size,
            "risk_amount": risk_amount,
            "risk_per_unit": round(risk_per_unit, 4),
            "notional": notional,
            "entry_price": round(entry_price, 2),
            "effective_stop_price": round(effective_stop_price, 2),
            "stop_source": stop_source,
            "capital_base": round(capital_base, 2),
            "target_risk_amount": round(target_risk_amount, 2),
            "adjusted_risk_amount": adjusted_risk_amount,
            "conviction_multiplier": round(conviction_multiplier, 2),
            "reward_multiplier": round(reward_multiplier, 2),
            "exposure_multiplier": round(exposure_multiplier, 2),
            "event_multiplier": round(event_multiplier, 2),
            "specialized_context_multiplier": round(specialized_context_multiplier, 2),
            "regime_multiplier": round(regime_multiplier, 2),
            "regime_policy_version": regime_policy.get("policy_version"),
            "portfolio_risk_after_trade": round(
                (self._coerce_positive_float(risk_budget.get("used_portfolio_risk_amount")) or 0.0) + risk_amount,
                2,
            ),
            "reasons": reasons,
        }
        summary = (
            f"Position sized to {size} units with {risk_amount} risk dollars and notional {notional} "
            f"using a {stop_source} stop at {round(effective_stop_price, 2)}."
        )
        return {
            "blocked": False,
            "summary": summary,
            "risk_budget": risk_budget,
            "position_sizing": sizing,
        }

    def _resolve_stop(
        self,
        *,
        entry_price: float,
        stop_price: object,
        atr_14: object,
        default_stop_atr_multiple: float,
    ) -> tuple[float | None, float | None, str]:
        normalized_stop = self._coerce_positive_float(stop_price)
        if normalized_stop is not None and normalized_stop < entry_price:
            risk_per_unit = round(entry_price - normalized_stop, 4)
            return normalized_stop, risk_per_unit, "signal_stop"

        atr_value = self._coerce_positive_float(atr_14)
        if atr_value is None:
            return None, None, "invalid_stop"
        fallback_stop = round(max(entry_price - (atr_value * default_stop_atr_multiple), 0.01), 2)
        risk_per_unit = round(entry_price - fallback_stop, 4)
        if risk_per_unit <= 0:
            return None, None, "invalid_stop"
        return fallback_stop, risk_per_unit, "atr_fallback"

    @staticmethod
    def _conviction_multiplier(confidence: float) -> float:
        if confidence >= 0.88:
            return 1.15
        if confidence >= 0.8:
            return 1.0
        if confidence >= 0.7:
            return 0.85
        return 0.7

    @staticmethod
    def _reward_multiplier(risk_reward: float) -> float:
        if risk_reward >= 3.0:
            return 1.08
        if risk_reward >= 2.0:
            return 1.0
        if risk_reward >= 1.5:
            return 0.9
        return 0.75

    @staticmethod
    def _exposure_multiplier(portfolio: dict) -> float:
        multiplier = 1.0
        if int(portfolio.get("same_ticker_open_positions") or 0) > 0:
            multiplier *= 0.82
        if int(portfolio.get("same_sector_open_positions") or 0) > 0:
            multiplier *= 0.9
        if int(portfolio.get("same_regime_open_positions") or 0) > 0:
            multiplier *= 0.92
        return multiplier

    @staticmethod
    def _event_multiplier(risk_budget: dict) -> float:
        candidate_profile = risk_budget.get("candidate_profile") if isinstance(risk_budget.get("candidate_profile"), dict) else {}
        event_flags = candidate_profile.get("event_risk_flags") if isinstance(candidate_profile.get("event_risk_flags"), list) else []
        expiry_context = (
            candidate_profile.get("expiry_context") if isinstance(candidate_profile.get("expiry_context"), dict) else {}
        )
        if expiry_context.get("expiry_day"):
            return 0.65
        if expiry_context.get("phase") == "tight_pre_expiry_window":
            return 0.72
        if expiry_context.get("pre_expiry_window"):
            return 0.82
        return 0.8 if event_flags else 1.0

    @staticmethod
    def _specialized_context_multiplier(risk_budget: dict) -> float:
        candidate_profile = risk_budget.get("candidate_profile") if isinstance(risk_budget.get("candidate_profile"), dict) else {}
        mstr_context = candidate_profile.get("mstr_context") if isinstance(candidate_profile.get("mstr_context"), dict) else {}
        if not mstr_context or not mstr_context.get("applicable") or not mstr_context.get("available", True):
            return 1.0

        atm_risk_context = str(mstr_context.get("atm_risk_context") or "").strip()
        btc_proxy_state = str(mstr_context.get("btc_proxy_state") or "").strip()
        if atm_risk_context == "high" and btc_proxy_state == "weak":
            return 0.68
        if atm_risk_context == "high":
            return 0.8
        if atm_risk_context == "moderate":
            return 0.9
        return 1.0

    @staticmethod
    def _blocked_result(*, risk_budget: dict, summary: str, reasons: list[str] | None = None) -> dict:
        return {
            "blocked": True,
            "summary": summary,
            "risk_budget": risk_budget,
            "position_sizing": {
                "status": "blocked",
                "size": 0.0,
                "risk_amount": 0.0,
                "risk_per_unit": None,
                "notional": 0.0,
                "reasons": reasons or [],
            },
        }

    @staticmethod
    def _coerce_positive_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_non_negative_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) >= 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None


class IntermarketContextService:
    AIRLINE_SECTOR_TAGS = {"airlines", "airline", "air_travel", "air_transport"}
    OPTIONS_RANKING_BASIS = "volume"
    OPTIONS_RANKING_LIMIT = 60
    AIRLINE_TICKER_HINTS = {
        "AAL",
        "ALK",
        "DAL",
        "JBLU",
        "JETS",
        "LUV",
        "SKYW",
        "UAL",
    }
    AIRLINE_PROXY_SYMBOLS = {
        "sector_etf": "JETS",
        "benchmark": "SPY",
        "oil_proxy": "USO",
    }

    def __init__(self, market_data_service: MarketDataService | None = None) -> None:
        self.market_data_service = market_data_service or MarketDataService()

    def build_context(
        self,
        *,
        ticker: str,
        strategy_rules: dict,
        market_context: dict | None = None,
        signal_payload: dict | None = None,
        market_overview: dict | None = None,
    ) -> dict:
        market_context = dict(market_context or {})
        signal_payload = dict(signal_payload or {})
        sector_tag = self._resolve_sector_tag(
            ticker=ticker,
            strategy_rules=strategy_rules,
            market_context=market_context,
            signal_payload=signal_payload,
        )
        if sector_tag not in self.AIRLINE_SECTOR_TAGS:
            return self._not_applicable_context(sector_tag=sector_tag)

        try:
            ticker_snapshot = self.market_data_service.get_snapshot(ticker)
            sector_snapshot = self.market_data_service.get_snapshot(self.AIRLINE_PROXY_SYMBOLS["sector_etf"])
            benchmark_snapshot = self.market_data_service.get_snapshot(self.AIRLINE_PROXY_SYMBOLS["benchmark"])
            oil_snapshot = self.market_data_service.get_snapshot(self.AIRLINE_PROXY_SYMBOLS["oil_proxy"])
            ticker_history = self.market_data_service.get_history(ticker, limit=25)
            sector_history = self.market_data_service.get_history(self.AIRLINE_PROXY_SYMBOLS["sector_etf"], limit=25)
        except (MarketDataUnavailableError, RuntimeError, ValueError) as exc:
            return self._unavailable_context(sector_tag=sector_tag, provider_error=str(exc))

        sector_rs = round(float(sector_snapshot.month_performance) - float(benchmark_snapshot.month_performance), 4)
        ticker_vs_sector = round(float(ticker_snapshot.month_performance) - float(sector_snapshot.month_performance), 4)
        oil_proxy_change = round(float(oil_snapshot.month_performance), 4)
        ticker_close_location = round(self._close_location(ticker_history), 2)
        sector_close_location = round(self._close_location(sector_history), 2)

        sector_strength_state = self._strength_state(sector_rs)
        oil_pressure_state = self._oil_pressure_state(oil_proxy_change)
        ticker_vs_sector_state = self._relative_state(ticker_vs_sector)
        ticker_close_state = self._close_state(ticker_close_location)
        sector_close_state = self._close_state(sector_close_location)
        options_sentiment = self._build_options_sentiment(ticker, market_overview=market_overview)
        put_call_state = str(options_sentiment.get("put_call_state") or "unavailable").strip()

        supportive_signals: list[str] = []
        risk_flags: list[str] = []
        score = 0.5

        if sector_strength_state == "strong":
            score += 0.18
            supportive_signals.append("sector_outperforming_spy")
        elif sector_strength_state == "weak":
            score -= 0.18
            risk_flags.append("sector_underperforming_spy")

        if oil_pressure_state == "tailwind":
            score += 0.16
            supportive_signals.append("oil_proxy_easing")
        elif oil_pressure_state == "headwind":
            score -= 0.16
            risk_flags.append("oil_proxy_rising")

        if ticker_vs_sector_state == "leading":
            score += 0.08
            supportive_signals.append("ticker_leading_sector")
        elif ticker_vs_sector_state == "lagging":
            score -= 0.08
            risk_flags.append("ticker_lagging_sector")

        if ticker_close_state == "strong_close":
            score += 0.05
            supportive_signals.append("ticker_closed_near_range_high")
        elif ticker_close_state == "weak_close":
            score -= 0.05
            risk_flags.append("ticker_closed_near_range_low")

        if sector_close_state == "strong_close":
            score += 0.04
            supportive_signals.append("sector_closed_near_range_high")
        elif sector_close_state == "weak_close":
            score -= 0.04
            risk_flags.append("sector_closed_near_range_low")

        if put_call_state == "fearful":
            score += 0.06
            supportive_signals.append("options_put_call_fear")
        elif put_call_state == "complacent":
            score -= 0.06
            risk_flags.append("options_put_call_complacency")

        bias = "mixed"
        if score >= 0.66:
            bias = "supportive"
        elif score <= 0.34:
            bias = "headwind"

        evidence_points = self._dedupe(
            [
                f"JETS vs SPY 20d={round(sector_rs * 100, 2)}%",
                f"{ticker.upper()} vs JETS 20d={round(ticker_vs_sector * 100, 2)}%",
                f"USO 20d={round(oil_proxy_change * 100, 2)}%",
                f"{ticker.upper()} close_location_20d={round(ticker_close_location * 100, 1)}%",
                f"JETS close_location_20d={round(sector_close_location * 100, 1)}%",
            ]
            + self._build_options_evidence(ticker=ticker, options_sentiment=options_sentiment)
            + supportive_signals
            + risk_flags
        )

        return {
            "applicable": True,
            "available": True,
            "theme": "airline_sector_intermarket",
            "sector_tag": sector_tag,
            "proxy_symbols": dict(self.AIRLINE_PROXY_SYMBOLS),
            "score": round(max(min(score, 1.0), 0.0), 2),
            "bias": bias,
            "sector_relative_strength_20d": sector_rs,
            "ticker_relative_strength_vs_sector_20d": ticker_vs_sector,
            "oil_proxy_month_performance": oil_proxy_change,
            "ticker_month_performance": round(float(ticker_snapshot.month_performance), 4),
            "sector_month_performance": round(float(sector_snapshot.month_performance), 4),
            "benchmark_month_performance": round(float(benchmark_snapshot.month_performance), 4),
            "ticker_close_location_20d": ticker_close_location,
            "sector_close_location_20d": sector_close_location,
            "sector_strength_state": sector_strength_state,
            "oil_pressure_state": oil_pressure_state,
            "ticker_vs_sector_state": ticker_vs_sector_state,
            "ticker_close_state": ticker_close_state,
            "sector_close_state": sector_close_state,
            "options_sentiment": options_sentiment,
            "put_call_state": put_call_state,
            "put_call_ratio": options_sentiment.get("put_call_ratio"),
            "put_call_ratio_source": options_sentiment.get("ratio_source"),
            "option_implied_vol_pct": options_sentiment.get("option_implied_vol_pct"),
            "supportive_signals": supportive_signals,
            "risk_flags": risk_flags,
            "evidence_points": evidence_points,
            "summary": self._build_summary(
                ticker=ticker,
                bias=bias,
                sector_strength_state=sector_strength_state,
                oil_pressure_state=oil_pressure_state,
                ticker_vs_sector_state=ticker_vs_sector_state,
                put_call_state=put_call_state,
            ),
            "provider_error": None,
        }

    @classmethod
    def _resolve_sector_tag(
        cls,
        *,
        ticker: str,
        strategy_rules: dict,
        market_context: dict,
        signal_payload: dict,
    ) -> str:
        explicit = str(
            market_context.get("sector_tag")
            or signal_payload.get("sector_tag")
            or strategy_rules.get("sector_tag")
            or ""
        ).strip().lower()
        if explicit:
            return explicit
        if ticker.upper() in cls.AIRLINE_TICKER_HINTS:
            return "airlines"
        return "unknown"

    @classmethod
    def _not_applicable_context(cls, *, sector_tag: str) -> dict:
        return {
            "applicable": False,
            "available": False,
            "theme": "not_applicable",
            "sector_tag": sector_tag,
            "score": 0.5,
            "bias": "neutral",
            "options_sentiment": cls._default_options_sentiment(),
            "put_call_state": "none",
            "put_call_ratio": None,
            "put_call_ratio_source": None,
            "option_implied_vol_pct": None,
            "supportive_signals": [],
            "risk_flags": [],
            "evidence_points": [],
            "summary": "No sector-specific intermarket model applied.",
            "provider_error": None,
        }

    @classmethod
    def _unavailable_context(cls, *, sector_tag: str, provider_error: str) -> dict:
        return {
            "applicable": True,
            "available": False,
            "theme": "airline_sector_intermarket",
            "sector_tag": sector_tag,
            "proxy_symbols": dict(cls.AIRLINE_PROXY_SYMBOLS),
            "score": 0.5,
            "bias": "neutral",
            "options_sentiment": cls._default_options_sentiment(provider_error=provider_error),
            "put_call_state": "unavailable",
            "put_call_ratio": None,
            "put_call_ratio_source": None,
            "option_implied_vol_pct": None,
            "supportive_signals": [],
            "risk_flags": [],
            "evidence_points": [],
            "summary": "Airline intermarket context is applicable, but the proxy data is unavailable.",
            "provider_error": provider_error,
        }

    def _build_options_sentiment(self, ticker: str, *, market_overview: dict | None = None) -> dict:
        payload = self._default_options_sentiment()
        upstream = self._load_options_sentiment_from_overview(ticker=ticker, market_overview=market_overview)
        if upstream is None:
            get_options_sentiment = getattr(self.market_data_service, "get_options_sentiment", None)
            if not callable(get_options_sentiment):
                payload["provider_error"] = "Options sentiment is not supported by the current market data service."
                return payload

            try:
                upstream = dict(get_options_sentiment(ticker, sec_type="STK") or {})
            except (MarketDataUnavailableError, RuntimeError, ValueError) as exc:
                payload["provider_error"] = str(exc)
                return payload

        payload["available"] = bool(upstream.get("available", True))
        payload["option_implied_vol_pct"] = self._coerce_non_negative_float(upstream.get("option_implied_vol_pct"))
        payload["fallback_reason"] = str(upstream.get("fallback_reason") or "").strip() or None
        payload["provider_error"] = str(upstream.get("provider_error") or "").strip() or None

        put_call_ratio = self._coerce_non_negative_float(upstream.get("put_call_ratio"))
        ratio_source = str(upstream.get("put_call_ratio_source") or "snapshot").strip() or "snapshot"
        if put_call_ratio is None:
            put_call_ratio = self._coerce_non_negative_float(upstream.get("put_call_volume_ratio"))
            if put_call_ratio is not None:
                ratio_source = "snapshot_volume_ratio"

        if put_call_ratio is not None:
            payload["ratio_available"] = True
            payload["put_call_ratio"] = round(put_call_ratio, 4)
            payload["put_call_state"] = self._classify_put_call_state(put_call_ratio)
            payload["ratio_source"] = ratio_source
            return payload

        if payload["available"] and not payload["provider_error"]:
            ranking_hit = self._lookup_put_call_ranking_hit(ticker)
            if ranking_hit is not None:
                payload["ratio_available"] = True
                payload["put_call_ratio"] = ranking_hit.get("put_call_ratio")
                payload["put_call_state"] = str(ranking_hit.get("put_call_state") or "unavailable")
                payload["ratio_source"] = str(ranking_hit.get("ratio_source") or "")
                payload["ranking_rank"] = ranking_hit.get("ranking_rank")
                payload["ranking_basis"] = ranking_hit.get("ranking_basis")
                return payload

        return payload

    def _lookup_put_call_ranking_hit(self, ticker: str) -> dict | None:
        get_rankings = getattr(self.market_data_service, "get_options_sentiment_rankings", None)
        if not callable(get_rankings):
            return None

        normalized_ticker = ticker.strip().upper()
        for direction, state in (("high", "fearful"), ("low", "complacent")):
            try:
                rankings = dict(
                    get_rankings(
                        basis=self.OPTIONS_RANKING_BASIS,
                        direction=direction,
                        instrument="STK",
                        location=None,
                        limit=self.OPTIONS_RANKING_LIMIT,
                    )
                    or {}
                )
            except (MarketDataUnavailableError, RuntimeError, ValueError):
                continue
            if not rankings.get("available"):
                continue
            contracts = rankings.get("contracts") if isinstance(rankings.get("contracts"), list) else []
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                if str(contract.get("symbol") or "").strip().upper() != normalized_ticker:
                    continue
                return {
                    "put_call_ratio": self._coerce_non_negative_float(contract.get("ratio")),
                    "put_call_state": state,
                    "ratio_source": f"{self.OPTIONS_RANKING_BASIS}_ranking_{direction}",
                    "ranking_rank": contract.get("rank"),
                    "ranking_basis": self.OPTIONS_RANKING_BASIS,
                }
        return None

    def _load_options_sentiment_from_overview(self, *, ticker: str, market_overview: dict | None = None) -> dict | None:
        overview = dict(market_overview or {})
        if not overview:
            get_market_overview = getattr(self.market_data_service, "get_market_overview", None)
            if not callable(get_market_overview):
                return None
            try:
                overview = dict(get_market_overview(ticker, sec_type="STK") or {})
            except (MarketDataUnavailableError, RuntimeError, ValueError):
                return None

        options_sentiment = overview.get("options_sentiment")
        if not isinstance(options_sentiment, dict):
            return None
        if not options_sentiment:
            return None
        return dict(options_sentiment)

    @staticmethod
    def _default_options_sentiment(provider_error: str | None = None) -> dict:
        return {
            "available": False,
            "ratio_available": False,
            "put_call_ratio": None,
            "put_call_state": "unavailable",
            "ratio_source": None,
            "option_implied_vol_pct": None,
            "fallback_reason": None,
            "ranking_rank": None,
            "ranking_basis": None,
            "provider_error": provider_error,
        }

    @staticmethod
    def _classify_put_call_state(put_call_ratio: float) -> str:
        if put_call_ratio >= 1.2:
            return "fearful"
        if put_call_ratio <= 0.6:
            return "complacent"
        return "neutral"

    @staticmethod
    def _build_options_evidence(*, ticker: str, options_sentiment: dict) -> list[str]:
        evidence: list[str] = []
        put_call_ratio = options_sentiment.get("put_call_ratio")
        if isinstance(put_call_ratio, (int, float)):
            source = str(options_sentiment.get("ratio_source") or "snapshot").strip() or "snapshot"
            evidence.append(f"{ticker.upper()} put_call_ratio={round(float(put_call_ratio), 2)} via {source}")
        if isinstance(options_sentiment.get("option_implied_vol_pct"), (int, float)):
            evidence.append(
                f"{ticker.upper()} option_iv={round(float(options_sentiment['option_implied_vol_pct']), 1)}%"
            )
        if not evidence:
            fallback_reason = str(options_sentiment.get("fallback_reason") or "").strip()
            if fallback_reason:
                evidence.append(f"{ticker.upper()} options fallback active")
        return evidence

    @staticmethod
    def _coerce_non_negative_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)) and float(value) >= 0:
            return float(value)
        if isinstance(value, str):
            try:
                parsed = float(value.strip())
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    def _close_location(candles: list) -> float:
        if not candles:
            return 0.5
        lows = [float(candle.low) for candle in candles[-20:]]
        highs = [float(candle.high) for candle in candles[-20:]]
        last_close = float(candles[-1].close)
        low = min(lows)
        high = max(highs)
        price_range = max(high - low, 0.01)
        return max(min((last_close - low) / price_range, 1.0), 0.0)

    @staticmethod
    def _strength_state(relative_strength: float) -> str:
        if relative_strength >= 0.03:
            return "strong"
        if relative_strength <= -0.03:
            return "weak"
        return "neutral"

    @staticmethod
    def _oil_pressure_state(oil_proxy_change: float) -> str:
        if oil_proxy_change >= 0.04:
            return "headwind"
        if oil_proxy_change <= -0.04:
            return "tailwind"
        return "neutral"

    @staticmethod
    def _relative_state(relative_strength: float) -> str:
        if relative_strength >= 0.02:
            return "leading"
        if relative_strength <= -0.02:
            return "lagging"
        return "in_line"

    @staticmethod
    def _close_state(close_location: float) -> str:
        if close_location >= 0.7:
            return "strong_close"
        if close_location <= 0.3:
            return "weak_close"
        return "mid_range"

    @staticmethod
    def _build_summary(
        *,
        ticker: str,
        bias: str,
        sector_strength_state: str,
        oil_pressure_state: str,
        ticker_vs_sector_state: str,
        put_call_state: str,
    ) -> str:
        return (
            f"Airline intermarket context for {ticker.upper()} is {bias}: "
            f"sector_strength={sector_strength_state}, oil_pressure={oil_pressure_state}, "
            f"ticker_vs_sector={ticker_vs_sector_state}, put_call_state={put_call_state}."
        )

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            marker = text.lower()
            if marker in seen:
                continue
            seen.add(marker)
            results.append(text)
        return results


class DecisionContextAssemblerService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        macro_context_service: MacroContextService | None = None,
        strategy_context_adaptation_service: StrategyContextAdaptationService | None = None,
        news_service: NewsService | None = None,
        calendar_service: CalendarService | None = None,
        market_data_service: MarketDataService | None = None,
        risk_budget_service: RiskBudgetService | None = None,
        regime_policy_service: RegimePolicyService | None = None,
        intermarket_context_service: IntermarketContextService | None = None,
        mstr_context_service: MSTRContextService | None = None,
        skill_router_service: SkillRouterService | None = None,
        skill_lifecycle_service: SkillLifecycleService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.macro_context_service = macro_context_service or MacroContextService()
        self.strategy_context_adaptation_service = (
            strategy_context_adaptation_service or StrategyContextAdaptationService()
        )
        self.news_service = news_service or NewsService()
        self.calendar_service = calendar_service or CalendarService()
        self.market_data_service = market_data_service or MarketDataService()
        self.risk_budget_service = risk_budget_service or RiskBudgetService(settings=self.settings)
        self.regime_policy_service = regime_policy_service or RegimePolicyService()
        self.intermarket_context_service = intermarket_context_service or IntermarketContextService(
            market_data_service=self.market_data_service
        )
        self.mstr_context_service = mstr_context_service or MSTRContextService(
            settings=self.settings,
            market_data_service=self.market_data_service,
        )
        self.skill_router_service = skill_router_service or SkillRouterService()
        self.skill_lifecycle_service = skill_lifecycle_service or SkillLifecycleService()
        self.io_parallelism_enabled = bool(self.settings.decision_context_io_parallelism_enabled)
        self.io_max_workers = max(int(self.settings.decision_context_io_max_workers), 1)
        self._io_executor: ThreadPoolExecutor | None = None
        self._io_executor_lock = Lock()

    def build_trade_candidate_context(
        self,
        session: Session,
        *,
        ticker: str,
        strategy_id: int | None,
        strategy_version_id: int | None,
        signal_payload: dict,
        market_context: dict | None = None,
    ) -> dict:
        started_at = perf_counter()
        timings: dict[str, float] = {}

        def record_timing(stage: str, stage_started_at: float) -> None:
            timings[stage] = round((perf_counter() - stage_started_at) * 1000, 2)

        version = session.get(StrategyVersion, strategy_version_id) if strategy_version_id is not None else None
        stage_started_at = perf_counter()
        strategy_rules = self._build_strategy_rules(version)
        record_timing("strategy_rules", stage_started_at)

        stage_started_at = perf_counter()
        macro_context = self.macro_context_service.get_context(session, limit=6).model_dump(mode="json")
        record_timing("macro_context", stage_started_at)

        initial_io_results, initial_io_timings = self._run_io_tasks(
            {
                "market_overview": lambda: self._load_market_overview(ticker=ticker),
                "news_context": lambda: self._build_news_context(ticker=ticker),
                "mstr_context": lambda: self.mstr_context_service.build_context(
                    ticker=ticker,
                    market_context=dict(market_context or {}),
                    signal_payload=signal_payload,
                ),
            }
        )
        market_overview = dict(initial_io_results.get("market_overview") or {})
        news_context = dict(initial_io_results.get("news_context") or {})
        mstr_context = dict(initial_io_results.get("mstr_context") or {})
        timings.update(initial_io_timings)

        price_action_context = (
            dict(signal_payload.get("price_action_context") or {})
            if isinstance(signal_payload.get("price_action_context"), dict)
            else {}
        )
        secondary_io_results, secondary_io_timings = self._run_io_tasks(
            {
                "corporate_calendar_context": lambda: self._build_corporate_calendar_context(
                    ticker=ticker,
                    market_overview=market_overview,
                ),
                "macro_calendar_context": self._build_macro_calendar_context,
                "intermarket_context": lambda: self.intermarket_context_service.build_context(
                    ticker=ticker,
                    strategy_rules=strategy_rules,
                    market_context=dict(market_context or {}),
                    signal_payload=signal_payload,
                    market_overview=market_overview,
                ),
            }
        )
        intermarket_context = dict(secondary_io_results.get("intermarket_context") or {})
        corporate_calendar_context = dict(secondary_io_results.get("corporate_calendar_context") or {})
        macro_calendar_context = dict(secondary_io_results.get("macro_calendar_context") or {})
        timings["intermarket_context"] = secondary_io_timings.get("intermarket_context", 0.0)
        timings["corporate_calendar_context"] = secondary_io_timings.get("corporate_calendar_context", 0.0)
        timings["macro_calendar_context"] = secondary_io_timings.get("macro_calendar_context", 0.0)
        timings["calendar_context"] = round(
            max(
                secondary_io_timings.get("corporate_calendar_context", 0.0),
                secondary_io_timings.get("macro_calendar_context", 0.0),
            ),
            2,
        )
        calendar_context = self._compose_calendar_context(
            corporate_context=corporate_calendar_context,
            macro_context=macro_calendar_context,
        )
        stage_started_at = perf_counter()
        macro_fit = self._build_macro_fit(
            ticker=ticker,
            macro_context=macro_context,
            strategy_rules=strategy_rules,
        )
        record_timing("macro_fit", stage_started_at)

        stage_started_at = perf_counter()
        risk_budget = self.risk_budget_service.build_trade_candidate_budget(
            session,
            ticker=ticker,
            strategy_version_id=strategy_version_id,
            strategy_rules=strategy_rules,
            macro_fit=macro_fit,
            calendar_context=calendar_context,
            mstr_context=mstr_context,
            market_context=market_context,
            signal_payload=signal_payload,
        )
        record_timing("risk_budget", stage_started_at)

        stage_started_at = perf_counter()
        regime_policy = self.regime_policy_service.evaluate_trade_candidate_policy(
            signal_payload=signal_payload,
            market_context=dict(market_context or {}),
            portfolio=dict(risk_budget.get("portfolio") or {}),
            risk_budget=risk_budget,
        )
        record_timing("regime_policy", stage_started_at)
        risk_budget["regime_policy"] = regime_policy

        stage_started_at = perf_counter()
        skill_context = self.skill_router_service.route_trade_candidate(
            ticker=ticker,
            signal_payload=signal_payload,
            strategy_rules=strategy_rules,
            market_context=dict(market_context or {}),
            macro_context=macro_context,
            calendar_context=calendar_context,
            news_context=news_context,
            price_action_context=price_action_context,
            intermarket_context=intermarket_context,
            mstr_context=mstr_context,
            regime_policy=regime_policy,
            risk_budget=risk_budget,
        )
        skill_context = self.skill_lifecycle_service.attach_runtime_state(session, skill_context)
        record_timing("skill_routing", stage_started_at)

        learned_rule_signal_payload = {
            **signal_payload,
            "decision_context": {
                "calendar_context": calendar_context,
                "news_context": news_context,
                "macro_context": macro_context,
                "price_action_context": price_action_context,
                "intermarket_context": intermarket_context,
                "mstr_context": mstr_context,
                "skill_context": skill_context,
            },
        }
        stage_started_at = perf_counter()
        learned_rule_guard = self.strategy_context_adaptation_service.evaluate_entry(
            session,
            strategy_version_id=strategy_version_id,
            signal_payload=learned_rule_signal_payload,
        )
        record_timing("learned_rule_guard", stage_started_at)

        stage_started_at = perf_counter()
        supporting_rules = self.strategy_context_adaptation_service.list_supporting_rules(
            session,
            strategy_version_id=strategy_version_id,
            signal_payload=learned_rule_signal_payload,
        )
        record_timing("supporting_rules", stage_started_at)

        stage_started_at = perf_counter()
        matched_rules = self._list_active_strategy_rules(
            session,
            strategy_id=strategy_id,
            strategy_version_id=strategy_version_id,
        )
        record_timing("matched_strategy_rules", stage_started_at)

        total_ms = round((perf_counter() - started_at) * 1000, 2)
        slowest_stage = max(timings.items(), key=lambda item: item[1]) if timings else None

        return {
            "ticker": ticker.upper(),
            "strategy_id": strategy_id,
            "strategy_version_id": strategy_version_id,
            "market_context": dict(market_context or {}),
            "strategy_rules": strategy_rules,
            "macro_context": macro_context,
            "calendar_context": calendar_context,
            "news_context": news_context,
            "price_action_context": price_action_context,
            "intermarket_context": intermarket_context,
            "mstr_context": mstr_context,
            "skill_context": skill_context,
            "macro_fit": macro_fit,
            "portfolio": dict(risk_budget.get("portfolio") or {}),
            "risk_budget": risk_budget,
            "regime_policy": regime_policy,
            "learned_rule_guard": learned_rule_guard,
            "supporting_context_rules": supporting_rules,
            "matched_strategy_context_rules": matched_rules,
            "timing_profile": {
                "version": "decision_context_timing_v1",
                "io_parallelism_enabled": self.io_parallelism_enabled and self.io_max_workers > 1,
                "io_max_workers": self.io_max_workers,
                "total_ms": total_ms,
                "stages_ms": timings,
                "slowest_stage": slowest_stage[0] if slowest_stage is not None else None,
                "slowest_stage_ms": slowest_stage[1] if slowest_stage is not None else None,
            },
        }

    def _get_io_executor(self) -> ThreadPoolExecutor | None:
        if not self.io_parallelism_enabled or self.io_max_workers <= 1:
            return None
        with self._io_executor_lock:
            if self._io_executor is None:
                self._io_executor = ThreadPoolExecutor(
                    max_workers=self.io_max_workers,
                    thread_name_prefix="decision-context-io",
                )
            return self._io_executor

    def _run_io_tasks(self, tasks: dict[str, Callable[[], object]]) -> tuple[dict[str, object], dict[str, float]]:
        if not tasks:
            return {}, {}

        executor = self._get_io_executor()
        if executor is None or len(tasks) == 1:
            results: dict[str, object] = {}
            timings: dict[str, float] = {}
            for stage, task in tasks.items():
                stage_started_at = perf_counter()
                results[stage] = task()
                timings[stage] = round((perf_counter() - stage_started_at) * 1000, 2)
            return results, timings

        futures = {
            executor.submit(self._timed_io_task, stage=stage, task=task): stage
            for stage, task in tasks.items()
        }
        results: dict[str, object] = {}
        timings: dict[str, float] = {}
        for future in futures:
            stage, result, elapsed_ms = future.result()
            results[stage] = result
            timings[stage] = elapsed_ms
        return results, timings

    @staticmethod
    def _timed_io_task(*, stage: str, task: Callable[[], object]) -> tuple[str, object, float]:
        started_at = perf_counter()
        result = task()
        return stage, result, round((perf_counter() - started_at) * 1000, 2)

    @staticmethod
    def _build_strategy_rules(version: StrategyVersion | None) -> dict:
        if version is None:
            return {}

        general_rules = dict(version.general_rules or {})
        parameters = dict(version.parameters or {})

        return {
            "allowed_setups": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("allowed_setups")
            ),
            "blocked_setups": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("blocked_setups")
            ),
            "preferred_setups": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("preferred_setups")
            ),
            "required_trends": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("required_trends") or general_rules.get("required_trend")
            ),
            "preferred_trends": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("preferred_trends")
            ),
            "preferred_macro_regimes": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("preferred_macro_regimes")
            ),
            "blocked_macro_regimes": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("blocked_macro_regimes")
            ),
            "tracked_tickers": DecisionContextAssemblerService._normalize_string_list(
                general_rules.get("tracked_tickers")
            ),
            "sector_tag": str(
                parameters.get("sector_tag")
                or general_rules.get("sector_tag")
                or general_rules.get("sector")
                or ""
            ).strip()
            or None,
            "avoid_near_earnings_days": DecisionContextAssemblerService._coerce_int(
                general_rules.get("avoid_near_earnings_days")
            ),
            "avoid_near_macro_days": DecisionContextAssemblerService._coerce_int(
                general_rules.get("avoid_near_macro_days")
            ),
            "max_same_ticker_positions": DecisionContextAssemblerService._coerce_int(
                parameters.get("max_same_ticker_positions")
                or general_rules.get("max_same_ticker_positions")
            ),
            "max_same_strategy_open_positions": DecisionContextAssemblerService._coerce_int(
                parameters.get("max_same_strategy_open_positions")
                or general_rules.get("max_same_strategy_open_positions")
            ),
            "max_open_positions_total": DecisionContextAssemblerService._coerce_int(
                parameters.get("max_open_positions_total")
                or general_rules.get("max_open_positions_total")
            ),
            "max_same_sector_positions": DecisionContextAssemblerService._coerce_int(
                parameters.get("max_same_sector_positions")
                or general_rules.get("max_same_sector_positions")
            ),
            "max_positions_per_regime": DecisionContextAssemblerService._coerce_int(
                parameters.get("max_positions_per_regime")
                or general_rules.get("max_positions_per_regime")
            ),
            "max_event_risk_positions": DecisionContextAssemblerService._coerce_int(
                parameters.get("max_event_risk_positions")
                or general_rules.get("max_event_risk_positions")
            ),
            "min_risk_reward": DecisionContextAssemblerService._coerce_float(
                parameters.get("min_risk_reward") or general_rules.get("min_risk_reward")
            ),
            "min_quant_score": DecisionContextAssemblerService._coerce_float(
                parameters.get("min_quant_score") or general_rules.get("min_quant_score")
            ),
            "min_visual_score": DecisionContextAssemblerService._coerce_float(
                parameters.get("min_visual_score") or general_rules.get("min_visual_score")
            ),
            "risk_per_trade_fraction": DecisionContextAssemblerService._coerce_float(
                parameters.get("risk_per_trade_fraction")
                or general_rules.get("risk_per_trade_fraction")
            ),
            "max_portfolio_risk_fraction": DecisionContextAssemblerService._coerce_float(
                parameters.get("max_portfolio_risk_fraction")
                or general_rules.get("max_portfolio_risk_fraction")
            ),
            "max_notional_fraction_per_trade": DecisionContextAssemblerService._coerce_float(
                parameters.get("max_notional_fraction_per_trade")
                or general_rules.get("max_notional_fraction_per_trade")
            ),
            "daily_drawdown_limit_pct": DecisionContextAssemblerService._coerce_float(
                parameters.get("daily_drawdown_limit_pct")
                or general_rules.get("daily_drawdown_limit_pct")
            ),
            "weekly_drawdown_limit_pct": DecisionContextAssemblerService._coerce_float(
                parameters.get("weekly_drawdown_limit_pct")
                or general_rules.get("weekly_drawdown_limit_pct")
            ),
            "paper_capital_base": DecisionContextAssemblerService._coerce_float(
                parameters.get("paper_capital_base")
                or general_rules.get("paper_capital_base")
            ),
            "default_stop_atr_multiple": DecisionContextAssemblerService._coerce_float(
                parameters.get("default_stop_atr_multiple")
                or general_rules.get("default_stop_atr_multiple")
            ),
        }

    def _build_corporate_calendar_context(self, *, ticker: str, market_overview: dict | None = None) -> dict:
        calendar_error: str | None = None
        corporate_events = self._extract_corporate_events_from_overview(
            market_overview=market_overview,
            ticker=ticker,
            days_ahead=14,
        )
        if not corporate_events:
            try:
                corporate_events = [
                    event.__dict__ for event in self.calendar_service.list_ticker_events(ticker, days_ahead=14)
                ]
            except CalendarProviderError as exc:
                corporate_events = []
                calendar_error = str(exc)

        return {
            "corporate_events": corporate_events[:5],
            "corporate_event_count": len(corporate_events),
            "near_earnings_days": self._nearest_event_days(corporate_events, event_types={"earnings"}),
            "provider_error": calendar_error,
        }

    def _build_macro_calendar_context(self) -> dict:
        calendar_error: str | None = None
        try:
            macro_events = [event.__dict__ for event in self.calendar_service.list_macro_events(days_ahead=7)]
        except CalendarProviderError as exc:
            macro_events = []
            calendar_error = str(exc)
        expiry_context = self._build_expiry_context()
        return {
            "macro_events": macro_events[:5],
            "macro_event_count": len(macro_events),
            "near_macro_high_impact_days": self._nearest_high_impact_macro_days(macro_events),
            "quarterly_expiry_date": expiry_context.get("quarterly_expiry_date"),
            "days_to_quarterly_expiry": expiry_context.get("days_to_event"),
            "expiration_week": bool(expiry_context.get("expiration_week")),
            "pre_expiry_window": bool(expiry_context.get("pre_expiry_window")),
            "expiry_day": bool(expiry_context.get("expiry_day")),
            "post_expiry_window": bool(expiry_context.get("post_expiry_window")),
            "expiry_context": expiry_context,
            "provider_error": calendar_error,
        }

    @classmethod
    def _compose_calendar_context(cls, *, corporate_context: dict, macro_context: dict) -> dict:
        corporate_payload = dict(corporate_context or {})
        macro_payload = dict(macro_context or {})
        return {
            "corporate_events": list(corporate_payload.get("corporate_events") or []),
            "macro_events": list(macro_payload.get("macro_events") or []),
            "corporate_event_count": int(corporate_payload.get("corporate_event_count") or 0),
            "macro_event_count": int(macro_payload.get("macro_event_count") or 0),
            "near_earnings_days": corporate_payload.get("near_earnings_days"),
            "near_macro_high_impact_days": macro_payload.get("near_macro_high_impact_days"),
            "quarterly_expiry_date": macro_payload.get("quarterly_expiry_date"),
            "days_to_quarterly_expiry": macro_payload.get("days_to_quarterly_expiry"),
            "expiration_week": bool(macro_payload.get("expiration_week")),
            "pre_expiry_window": bool(macro_payload.get("pre_expiry_window")),
            "expiry_day": bool(macro_payload.get("expiry_day")),
            "post_expiry_window": bool(macro_payload.get("post_expiry_window")),
            "expiry_context": dict(macro_payload.get("expiry_context") or {}),
            "provider_error": cls._merge_provider_errors(
                corporate_payload.get("provider_error"),
                macro_payload.get("provider_error"),
            ),
        }

    @staticmethod
    def _merge_provider_errors(*errors: object) -> str | None:
        cleaned = [
            str(error).strip()
            for error in errors
            if isinstance(error, str) and str(error).strip()
        ]
        if not cleaned:
            return None
        if len(cleaned) == 1:
            return cleaned[0]
        return "; ".join(cleaned)

    def _build_expiry_context(self) -> dict:
        get_expiry_context = getattr(self.calendar_service, "get_quarterly_expiry_context", None)
        if not callable(get_expiry_context):
            return {
                "available": False,
                "source": "unavailable",
                "quarterly_expiry_date": None,
                "days_to_event": None,
                "expiration_week": False,
                "pre_expiry_window": False,
                "expiry_day": False,
                "post_expiry_window": False,
                "phase": "unavailable",
                "risk_penalty": 0.0,
                "reason": "Quarterly expiry context is unavailable from the active calendar service.",
            }
        try:
            payload = dict(get_expiry_context() or {})
        except Exception as exc:
            return {
                "available": False,
                "source": "error",
                "quarterly_expiry_date": None,
                "days_to_event": None,
                "expiration_week": False,
                "pre_expiry_window": False,
                "expiry_day": False,
                "post_expiry_window": False,
                "phase": "error",
                "risk_penalty": 0.0,
                "reason": f"Quarterly expiry context failed: {exc}",
            }
        payload.setdefault("available", True)
        payload.setdefault("source", "internal")
        payload.setdefault("quarterly_expiry_date", None)
        payload.setdefault("days_to_event", None)
        payload.setdefault("expiration_week", False)
        payload.setdefault("pre_expiry_window", False)
        payload.setdefault("expiry_day", False)
        payload.setdefault("post_expiry_window", False)
        payload.setdefault("phase", "normal")
        payload.setdefault("risk_penalty", 0.0)
        payload.setdefault("reason", "No quarterly expiry context available.")
        return payload

    def _load_market_overview(self, *, ticker: str) -> dict:
        get_market_overview = getattr(self.market_data_service, "get_market_overview", None)
        if not callable(get_market_overview):
            return {}
        try:
            return dict(get_market_overview(ticker, sec_type="STK") or {})
        except (MarketDataUnavailableError, RuntimeError, ValueError):
            return {}

    @classmethod
    def _extract_corporate_events_from_overview(
        cls,
        *,
        market_overview: dict | None,
        ticker: str,
        days_ahead: int,
    ) -> list[dict]:
        overview = dict(market_overview or {})
        events = overview.get("corporate_events")
        if not isinstance(events, list):
            return []

        today = date.today()
        cutoff = today + timedelta(days=max(int(days_ahead), 1))
        filtered: list[dict] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_ticker = str(event.get("ticker") or ticker).strip().upper()
            if event_ticker and event_ticker != ticker.upper():
                continue
            event_date = cls._parse_event_date(event.get("event_date"))
            if event_date is None or event_date < today or event_date > cutoff:
                continue
            filtered.append(dict(event))
        return filtered

    def _build_news_context(self, *, ticker: str) -> dict:
        news_error: str | None = None
        try:
            articles = self.news_service.list_news_for_ticker(ticker, max_results=4)
        except NewsProviderError as exc:
            articles = []
            news_error = str(exc)
        serialized_articles = [
            {
                "title": article.title,
                "description": article.description,
                "url": article.url,
                "source_name": article.source_name,
                "published_at": article.published_at,
            }
            for article in articles
        ]

        positive_hits = 0
        negative_hits = 0
        catalyst_hits = 0
        for article in serialized_articles:
            headline = " ".join(
                part for part in [article.get("title"), article.get("description")] if isinstance(part, str)
            ).lower()
            if any(keyword in headline for keyword in ("beats", "beat", "raises", "upgrade", "partnership", "surge", "strong demand")):
                positive_hits += 1
            if any(keyword in headline for keyword in ("miss", "cuts", "downgrade", "lawsuit", "probe", "weak demand", "delay")):
                negative_hits += 1
            if any(keyword in headline for keyword in ("earnings", "guidance", "forecast", "launch", "deal", "acquisition")):
                catalyst_hits += 1

        sentiment_bias = "neutral"
        if positive_hits > negative_hits:
            sentiment_bias = "positive"
        elif negative_hits > positive_hits:
            sentiment_bias = "negative"

        freshness_hours = self._freshest_article_hours(serialized_articles)
        return {
            "articles": serialized_articles,
            "article_count": len(serialized_articles),
            "positive_hits": positive_hits,
            "negative_hits": negative_hits,
            "catalyst_hits": catalyst_hits,
            "sentiment_bias": sentiment_bias,
            "freshness_hours": freshness_hours,
            "provider_error": news_error,
        }

    @staticmethod
    def _build_macro_fit(*, ticker: str, macro_context: dict, strategy_rules: dict) -> dict:
        active_regimes = [
            item for item in macro_context.get("active_regimes", []) if isinstance(item, str) and item.strip()
        ]
        tracked_tickers = {
            item.strip().upper()
            for item in macro_context.get("tracked_tickers", [])
            if isinstance(item, str) and item.strip()
        }
        preferred_regimes = set(strategy_rules.get("preferred_macro_regimes") or [])
        blocked_regimes = set(strategy_rules.get("blocked_macro_regimes") or [])
        conflicts = sorted(blocked_regimes.intersection(active_regimes))
        alignments = sorted(preferred_regimes.intersection(active_regimes))

        score = 0.5
        relevance_to_ticker = 0.7 if ticker.upper() in tracked_tickers else 0.45
        if active_regimes:
            score = 0.55
        if alignments:
            score = 0.85
        if conflicts:
            score = 0.15

        return {
            "score": round(score, 2),
            "active_regimes": active_regimes,
            "alignments": alignments,
            "conflicts": conflicts,
            "relevance_to_ticker": round(relevance_to_ticker, 2),
        }

    @staticmethod
    def _nearest_event_days(events: list[dict], event_types: set[str] | None = None) -> int | None:
        today = date.today()
        deltas: list[int] = []
        for event in events:
            if event_types is not None:
                event_type = str(event.get("event_type") or "").strip().lower()
                if event_type not in event_types:
                    continue
            event_date = DecisionContextAssemblerService._parse_event_date(event.get("event_date"))
            if event_date is None:
                continue
            delta = (event_date - today).days
            if delta >= 0:
                deltas.append(delta)
        return min(deltas) if deltas else None

    @staticmethod
    def _nearest_high_impact_macro_days(events: list[dict]) -> int | None:
        today = date.today()
        deltas: list[int] = []
        for event in events:
            impact = str(event.get("impact") or "").strip().lower()
            title = str(event.get("title") or "").strip().lower()
            if impact and impact not in {"high", "medium"} and "cpi" not in title and "fed" not in title and "fomc" not in title:
                continue
            event_date = DecisionContextAssemblerService._parse_event_date(event.get("event_date"))
            if event_date is None:
                continue
            delta = (event_date - today).days
            if delta >= 0:
                deltas.append(delta)
        return min(deltas) if deltas else None

    @staticmethod
    def _parse_event_date(raw: object) -> date | None:
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    @staticmethod
    def _freshest_article_hours(articles: list[dict]) -> float | None:
        now = datetime.now(UTC)
        deltas: list[float] = []
        for article in articles:
            published_at = article.get("published_at")
            if not isinstance(published_at, str) or not published_at.strip():
                continue
            normalized = published_at.strip().replace("Z", "+00:00")
            try:
                published = datetime.fromisoformat(normalized)
            except ValueError:
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            deltas.append(max((now - published.astimezone(UTC)).total_seconds() / 3600, 0.0))
        return round(min(deltas), 2) if deltas else None

    @staticmethod
    def _list_active_strategy_rules(
        session: Session,
        *,
        strategy_id: int | None,
        strategy_version_id: int | None,
    ) -> list[dict]:
        if strategy_id is None and strategy_version_id is None:
            return []

        rules = list(
            session.scalars(
                select(StrategyContextRule).where(
                    StrategyContextRule.status == "active",
                    or_(
                        StrategyContextRule.strategy_version_id == strategy_version_id
                        if strategy_version_id is not None
                        else false(),
                        StrategyContextRule.strategy_id == strategy_id if strategy_id is not None else false(),
                    ),
                )
            ).all()
        )
        return [
            {
                "id": rule.id,
                "feature_scope": rule.feature_scope,
                "feature_key": rule.feature_key,
                "feature_value": rule.feature_value,
                "action_type": rule.action_type,
                "confidence": rule.confidence,
                "rationale": rule.rationale,
            }
            for rule in rules
        ]

    @staticmethod
    def _normalize_string_list(value: object) -> list[str]:
        if isinstance(value, str):
            cleaned = value.strip()
            return [cleaned] if cleaned else []
        if not isinstance(value, list):
            return []
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if cleaned:
                items.append(cleaned)
        return items

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None


class EntryScoringService:
    ENTER_THRESHOLD = 0.72
    WATCH_THRESHOLD = 0.55
    REVERSAL_PRICE_ACTION_SIGNALS = {
        "failed_breakdown_reversal",
        "support_reclaim_confirmation",
        "rejection_wick_at_support",
        "high_relative_volume_reversal",
    }

    def evaluate(self, *, signal_payload: dict, decision_context: dict) -> dict:
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
        strategy_rules = (
            decision_context.get("strategy_rules") if isinstance(decision_context.get("strategy_rules"), dict) else {}
        )
        macro_fit = decision_context.get("macro_fit") if isinstance(decision_context.get("macro_fit"), dict) else {}
        calendar_context = (
            decision_context.get("calendar_context") if isinstance(decision_context.get("calendar_context"), dict) else {}
        )
        news_context = decision_context.get("news_context") if isinstance(decision_context.get("news_context"), dict) else {}
        price_action_context = (
            decision_context.get("price_action_context")
            if isinstance(decision_context.get("price_action_context"), dict)
            else {}
        )
        intermarket_context = (
            decision_context.get("intermarket_context")
            if isinstance(decision_context.get("intermarket_context"), dict)
            else {}
        )
        mstr_context = (
            decision_context.get("mstr_context")
            if isinstance(decision_context.get("mstr_context"), dict)
            else {}
        )
        portfolio = decision_context.get("portfolio") if isinstance(decision_context.get("portfolio"), dict) else {}
        risk_budget = decision_context.get("risk_budget") if isinstance(decision_context.get("risk_budget"), dict) else {}
        regime_policy = decision_context.get("regime_policy") if isinstance(decision_context.get("regime_policy"), dict) else {}
        learned_rule_guard = (
            decision_context.get("learned_rule_guard")
            if isinstance(decision_context.get("learned_rule_guard"), dict)
            else None
        )
        supporting_rules = (
            decision_context.get("supporting_context_rules")
            if isinstance(decision_context.get("supporting_context_rules"), list)
            else []
        )

        technical_score = self._clamp(signal_payload.get("combined_score") or quant.get("quant_score") or 0.0)
        visual_score = self._clamp(visual.get("visual_score") or technical_score)
        strategy_fit_score, strategy_guard_reasons, strategy_advisories = self._score_strategy_fit(
            signal_payload=signal_payload,
            quant=quant,
            visual=visual,
            strategy_rules=strategy_rules,
            technical_score=technical_score,
            visual_score=visual_score,
        )
        macro_fit_score, macro_guard_reasons, macro_advisories = self._score_macro_fit(
            strategy_rules=strategy_rules,
            macro_fit=macro_fit,
        )
        regime_policy_score, regime_policy_guard_reasons, regime_policy_advisories = self._score_regime_policy(
            regime_policy=regime_policy,
        )
        calendar_score, calendar_advisories = self._score_calendar_fit(
            strategy_rules=strategy_rules,
            calendar_context=calendar_context,
            signal_payload=signal_payload,
        )
        news_score, news_advisories = self._score_news_fit(news_context)
        price_action_bonus, price_action_advisories, price_action_guard_reasons = self._score_price_action_confirmation(
            price_action_context=price_action_context,
            quant=quant,
            calendar_context=calendar_context,
            regime_policy=regime_policy,
        )
        intermarket_score, intermarket_guard_reasons, intermarket_advisories = self._score_intermarket_fit(
            intermarket_context=intermarket_context,
        )
        mstr_score, mstr_guard_reasons, mstr_advisories = self._score_mstr_fit(
            mstr_context=mstr_context,
        )
        portfolio_fit_score, portfolio_guard_reasons, portfolio_advisories = self._score_portfolio_fit(
            strategy_rules=strategy_rules,
            portfolio=portfolio,
        )
        risk_budget_score, risk_budget_guard_reasons, risk_budget_advisories = self._score_risk_budget_fit(
            risk_budget=risk_budget,
        )
        learned_rule_penalty, learned_rule_guard_reasons = self._score_learned_rule_guard(learned_rule_guard)
        learned_rule_bonus, supporting_rule_advisories = self._score_supporting_rules(supporting_rules)

        raw_score = (
            (technical_score * 0.30)
            + (visual_score * 0.13)
            + (strategy_fit_score * 0.11)
            + (macro_fit_score * 0.06)
            + (intermarket_score * 0.05)
            + (mstr_score * 0.05)
            + (regime_policy_score * 0.07)
            + (calendar_score * 0.06)
            + (news_score * 0.04)
            + (portfolio_fit_score * 0.06)
            + (risk_budget_score * 0.07)
        )
        final_score = self._clamp(raw_score + learned_rule_bonus - learned_rule_penalty + price_action_bonus)
        guard_reasons = (
            strategy_guard_reasons
            + macro_guard_reasons
            + price_action_guard_reasons
            + intermarket_guard_reasons
            + mstr_guard_reasons
            + regime_policy_guard_reasons
            + portfolio_guard_reasons
            + risk_budget_guard_reasons
            + learned_rule_guard_reasons
        )
        guard_types = (
            (["strategy_rule"] * len(strategy_guard_reasons))
            + (["macro_conflict"] * len(macro_guard_reasons))
            + (["price_action_conflict"] * len(price_action_guard_reasons))
            + (["intermarket_conflict"] * len(intermarket_guard_reasons))
            + (["mstr_context_conflict"] * len(mstr_guard_reasons))
            + (["regime_policy"] * len(regime_policy_guard_reasons))
            + (["portfolio_limit"] * len(portfolio_guard_reasons))
            + (["risk_budget"] * len(risk_budget_guard_reasons))
            + (["learned_rule"] * len(learned_rule_guard_reasons))
        )
        blocked = bool(guard_reasons)

        recommended_action = "discard"
        if blocked:
            recommended_action = "watch"
        elif final_score >= self.ENTER_THRESHOLD:
            recommended_action = "paper_enter"
        elif final_score >= self.WATCH_THRESHOLD:
            recommended_action = "watch"

        advisories = (
            strategy_advisories
            + macro_advisories
            + price_action_advisories
            + intermarket_advisories
            + mstr_advisories
            + regime_policy_advisories
            + portfolio_advisories
            + risk_budget_advisories
        )
        advisories += calendar_advisories + news_advisories + supporting_rule_advisories
        summary_parts = [
            f"entry_score={round(final_score, 2)}",
            f"technical={round(technical_score, 2)}",
            f"visual={round(visual_score, 2)}",
            f"strategy_fit={round(strategy_fit_score, 2)}",
            f"macro_fit={round(macro_fit_score, 2)}",
            f"price_action_bonus={round(price_action_bonus, 2)}",
            f"intermarket_fit={round(intermarket_score, 2)}",
            f"mstr_fit={round(mstr_score, 2)}",
            f"regime_policy={round(regime_policy_score, 2)}",
            f"calendar_fit={round(calendar_score, 2)}",
            f"news_fit={round(news_score, 2)}",
            f"portfolio_fit={round(portfolio_fit_score, 2)}",
            f"risk_budget_fit={round(risk_budget_score, 2)}",
        ]
        if learned_rule_bonus > 0:
            summary_parts.append(f"learned_bonus={round(learned_rule_bonus, 2)}")
        if learned_rule_penalty > 0:
            summary_parts.append(f"learned_penalty={round(learned_rule_penalty, 2)}")
        if guard_reasons:
            summary_parts.append("guards=" + "; ".join(guard_reasons))
        elif advisories:
            summary_parts.append("advisories=" + "; ".join(advisories[:3]))

        return {
            "recommended_action": recommended_action,
            "final_score": round(final_score, 2),
            "score_breakdown": {
                "technical_score": round(technical_score, 2),
                "visual_score": round(visual_score, 2),
                "strategy_fit_score": round(strategy_fit_score, 2),
                "macro_fit_score": round(macro_fit_score, 2),
                "price_action_bonus": round(price_action_bonus, 2),
                "intermarket_score": round(intermarket_score, 2),
                "mstr_score": round(mstr_score, 2),
                "regime_policy_score": round(regime_policy_score, 2),
                "calendar_score": round(calendar_score, 2),
                "news_score": round(news_score, 2),
                "portfolio_fit_score": round(portfolio_fit_score, 2),
                "risk_budget_score": round(risk_budget_score, 2),
                "learned_rule_bonus": round(learned_rule_bonus, 2),
                "learned_rule_penalty": round(learned_rule_penalty, 2),
                "final_score": round(final_score, 2),
            },
            "guard_results": {
                "blocked": blocked,
                "reasons": guard_reasons,
                "types": guard_types,
                "advisories": advisories,
            },
            "summary": "Deterministic decision layer: " + ", ".join(summary_parts) + ".",
        }

    @staticmethod
    def _score_strategy_fit(
        *,
        signal_payload: dict,
        quant: dict,
        visual: dict,
        strategy_rules: dict,
        technical_score: float,
        visual_score: float,
    ) -> tuple[float, list[str], list[str]]:
        score = 0.7
        guard_reasons: list[str] = []
        advisories: list[str] = []

        setup = str(quant.get("setup") or visual.get("setup_type") or "").strip()
        trend = str(quant.get("trend") or "").strip()
        risk_reward = signal_payload.get("risk_reward", quant.get("risk_reward"))

        allowed_setups = set(strategy_rules.get("allowed_setups") or [])
        blocked_setups = set(strategy_rules.get("blocked_setups") or [])
        preferred_setups = set(strategy_rules.get("preferred_setups") or [])
        required_trends = set(strategy_rules.get("required_trends") or [])
        preferred_trends = set(strategy_rules.get("preferred_trends") or [])
        min_risk_reward = strategy_rules.get("min_risk_reward")
        min_quant_score = strategy_rules.get("min_quant_score")
        min_visual_score = strategy_rules.get("min_visual_score")

        if setup and allowed_setups and setup not in allowed_setups:
            score = min(score, 0.2)
            guard_reasons.append(f"setup '{setup}' is outside allowed strategy setups")
        if setup and setup in blocked_setups:
            score = min(score, 0.1)
            guard_reasons.append(f"setup '{setup}' is explicitly blocked by strategy rules")
        if setup and setup in preferred_setups:
            score = min(score + 0.12, 1.0)
            advisories.append(f"setup '{setup}' is preferred by the strategy")
        if trend and required_trends and trend not in required_trends:
            score = min(score, 0.2)
            guard_reasons.append(f"trend '{trend}' does not satisfy required strategy trend")
        if trend and trend in preferred_trends:
            score = min(score + 0.08, 1.0)
            advisories.append(f"trend '{trend}' aligns with preferred strategy trend")
        if isinstance(risk_reward, (int, float)) and isinstance(min_risk_reward, (int, float)) and risk_reward < min_risk_reward:
            score = min(score, 0.25)
            guard_reasons.append(
                f"risk/reward {round(float(risk_reward), 2)} is below the strategy minimum {round(float(min_risk_reward), 2)}"
            )
        if isinstance(min_quant_score, (int, float)) and technical_score < float(min_quant_score):
            score = min(score, 0.25)
            guard_reasons.append(
                f"technical score {round(technical_score, 2)} is below the strategy minimum {round(float(min_quant_score), 2)}"
            )
        if isinstance(min_visual_score, (int, float)) and visual_score < float(min_visual_score):
            score = min(score, 0.25)
            guard_reasons.append(
                f"visual score {round(visual_score, 2)} is below the strategy minimum {round(float(min_visual_score), 2)}"
            )

        return EntryScoringService._clamp(score), guard_reasons, advisories

    @staticmethod
    def _score_macro_fit(*, strategy_rules: dict, macro_fit: dict) -> tuple[float, list[str], list[str]]:
        score = EntryScoringService._clamp(macro_fit.get("score", 0.5))
        guard_reasons: list[str] = []
        advisories: list[str] = []
        active_regimes = macro_fit.get("active_regimes") if isinstance(macro_fit.get("active_regimes"), list) else []
        alignments = macro_fit.get("alignments") if isinstance(macro_fit.get("alignments"), list) else []
        conflicts = macro_fit.get("conflicts") if isinstance(macro_fit.get("conflicts"), list) else []

        if conflicts:
            guard_reasons.append(
                "active macro regime conflicts with strategy assumptions: " + ", ".join(conflicts[:3])
            )
        elif alignments:
            advisories.append(
                "macro regime aligns with strategy assumptions: " + ", ".join(alignments[:3])
            )
        elif active_regimes and strategy_rules.get("preferred_macro_regimes"):
            advisories.append(
                "active macro regime does not strongly align with preferred strategy regimes"
            )

        return score, guard_reasons, advisories

    @staticmethod
    def _score_regime_policy(regime_policy: dict) -> tuple[float, list[str], list[str]]:
        if not regime_policy:
            return 0.7, [], []

        guard_reasons: list[str] = []
        advisories: list[str] = []
        risk_multiplier = float(regime_policy.get("risk_multiplier") or 0.0)
        blocked_reason = str(regime_policy.get("blocked_reason") or "").strip()
        allowed_playbooks = regime_policy.get("allowed_playbooks") if isinstance(regime_policy.get("allowed_playbooks"), list) else []
        playbook = str(regime_policy.get("playbook") or "").strip()
        max_new_positions = int(regime_policy.get("max_new_positions") or 0)
        opened_positions_so_far = int(regime_policy.get("opened_positions_so_far") or 0)

        if regime_policy.get("entry_allowed") is False:
            guard_reasons.append(blocked_reason or "regime policy blocks fresh exposure in the current market state")
            return 0.05 if risk_multiplier <= 0 else 0.12, guard_reasons, advisories

        if risk_multiplier >= 1.0:
            score = 0.9
        elif risk_multiplier >= 0.8:
            score = 0.78
        elif risk_multiplier >= 0.6:
            score = 0.66
        elif risk_multiplier > 0:
            score = 0.52
        else:
            score = 0.05

        if playbook and playbook in allowed_playbooks:
            advisories.append(f"playbook '{playbook}' is active for the current regime")
        if risk_multiplier < 1.0:
            advisories.append(f"regime policy reduces fresh risk to {round(risk_multiplier * 100)}% of baseline")
        if max_new_positions > 0:
            advisories.append(
                f"regime policy allows {max(max_new_positions - opened_positions_so_far, 0)} new position slot(s) for this cycle"
            )

        return EntryScoringService._clamp(score), guard_reasons, advisories

    @staticmethod
    def _score_calendar_fit(*, strategy_rules: dict, calendar_context: dict, signal_payload: dict) -> tuple[float, list[str]]:
        score = 0.7
        advisories: list[str] = []
        near_earnings_days = calendar_context.get("near_earnings_days")
        near_macro_days = calendar_context.get("near_macro_high_impact_days")
        expiry_context = (
            calendar_context.get("expiry_context") if isinstance(calendar_context.get("expiry_context"), dict) else {}
        )
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
        setup = str(quant.get("setup") or visual.get("setup_type") or "").strip().lower()
        breakout_sensitive = "breakout" in setup

        if isinstance(near_earnings_days, int):
            if near_earnings_days <= 1:
                score = min(score, 0.15)
                advisories.append(f"earnings are very close ({near_earnings_days} day away)")
            elif near_earnings_days <= 3:
                score = min(score, 0.35)
                advisories.append(f"earnings are near ({near_earnings_days} days away)")
            elif near_earnings_days <= 7:
                score = min(score, 0.55)
                advisories.append(f"earnings are within a week ({near_earnings_days} days away)")

        if isinstance(near_macro_days, int):
            if near_macro_days <= 1:
                score = min(score, 0.25)
                advisories.append(f"high-impact macro event is very close ({near_macro_days} day away)")
            elif near_macro_days <= 3:
                score = min(score, 0.45)
                advisories.append(f"high-impact macro event is near ({near_macro_days} days away)")

        if expiry_context.get("available"):
            days_to_expiry = expiry_context.get("days_to_event")
            phase = str(expiry_context.get("phase") or "").strip().lower()
            reason = str(expiry_context.get("reason") or "").strip()
            if expiry_context.get("expiry_day"):
                score = min(score, 0.32 if breakout_sensitive else 0.38)
                advisories.append("quarterly derivatives expiry is active today; do not treat the session as directional")
            elif phase == "tight_pre_expiry_window":
                score = min(score, 0.4 if breakout_sensitive else 0.46)
                advisories.append(
                    f"quarterly derivatives expiry is very close ({days_to_expiry} day away); keep new entries selective"
                )
            elif expiry_context.get("pre_expiry_window"):
                score = min(score, 0.5 if breakout_sensitive else 0.56)
                advisories.append(
                    f"quarterly derivatives expiry is near ({days_to_expiry} days away); penalize marginal setups"
                )
            elif expiry_context.get("expiration_week"):
                score = min(score, 0.62)
                advisories.append("expiration week is active; short-term execution noise can rise")
            elif expiry_context.get("post_expiry_window"):
                advisories.append("post-expiry cleanup window active; re-evaluate once roll noise clears")
            if breakout_sensitive and (
                expiry_context.get("expiry_day")
                or phase == "tight_pre_expiry_window"
                or expiry_context.get("pre_expiry_window")
            ):
                advisories.append("breakout-style entries are more vulnerable to expiry-week noise")
            if reason and phase in {"expiry_day", "tight_pre_expiry_window", "pre_expiry_window"}:
                advisories.append(reason)

        avoid_near_earnings = strategy_rules.get("avoid_near_earnings_days")
        if isinstance(avoid_near_earnings, int) and isinstance(near_earnings_days, int) and near_earnings_days <= avoid_near_earnings:
            score = min(score, 0.25)
        avoid_near_macro = strategy_rules.get("avoid_near_macro_days")
        if isinstance(avoid_near_macro, int) and isinstance(near_macro_days, int) and near_macro_days <= avoid_near_macro:
            score = min(score, 0.30)

        return EntryScoringService._clamp(score), advisories

    @staticmethod
    def _score_news_fit(news_context: dict) -> tuple[float, list[str]]:
        score = 0.5
        advisories: list[str] = []
        article_count = int(news_context.get("article_count") or 0)
        positive_hits = int(news_context.get("positive_hits") or 0)
        negative_hits = int(news_context.get("negative_hits") or 0)
        catalyst_hits = int(news_context.get("catalyst_hits") or 0)
        freshness_hours = news_context.get("freshness_hours")

        if article_count == 0:
            return score, advisories
        if positive_hits > negative_hits:
            score = 0.72 if catalyst_hits > 0 else 0.64
            advisories.append("recent news flow is net positive")
        elif negative_hits > positive_hits:
            score = 0.28 if catalyst_hits > 0 else 0.36
            advisories.append("recent news flow is net negative")
        else:
            score = 0.52 if catalyst_hits > 0 else 0.48
            advisories.append("recent news flow is mixed")

        if isinstance(freshness_hours, (int, float)) and freshness_hours <= 24 and article_count > 0:
            score = min(score + 0.04, 1.0) if positive_hits >= negative_hits else max(score - 0.04, 0.0)

        return EntryScoringService._clamp(score), advisories

    @staticmethod
    def _score_price_action_confirmation(
        *,
        price_action_context: dict,
        quant: dict,
        calendar_context: dict,
        regime_policy: dict,
    ) -> tuple[float, list[str], list[str]]:
        if not price_action_context or price_action_context.get("available") is False:
            return 0.0, [], []

        confirmation_bonus = EntryScoringService._clamp(price_action_context.get("confirmation_bonus", 0.0))
        primary_signal_code = str(price_action_context.get("primary_signal_code") or "").strip()
        signal_count = int(price_action_context.get("signal_count") or 0)
        summary = str(price_action_context.get("summary") or "").strip()
        if confirmation_bonus <= 0 or not primary_signal_code:
            return 0.0, [], []

        advisories: list[str] = []
        guard_reasons: list[str] = []
        is_reversal_signal = primary_signal_code in EntryScoringService.REVERSAL_PRICE_ACTION_SIGNALS
        higher_timeframe_bias = str(price_action_context.get("higher_timeframe_bias") or "neutral").strip()
        follow_through_state = str(price_action_context.get("follow_through_state") or "none").strip()
        near_earnings_days = calendar_context.get("near_earnings_days")
        regime_label = str(regime_policy.get("regime_label") or "").strip()
        trend = str(quant.get("trend") or "").strip()

        if is_reversal_signal and trend == "downtrend":
            guard_reasons.append("reversal proxy fired against a clear daily downtrend")
            return 0.0, advisories, guard_reasons

        if is_reversal_signal and higher_timeframe_bias == "hostile":
            guard_reasons.append("reversal proxy fired while higher timeframe bias remains hostile")
            return 0.0, advisories, guard_reasons

        if is_reversal_signal and isinstance(near_earnings_days, int) and near_earnings_days <= 3:
            advisories.append(f"reversal proxy ignored because earnings are imminent ({near_earnings_days} day(s))")
            return 0.0, advisories, guard_reasons

        if is_reversal_signal and regime_label == "high_volatility_risk_off":
            advisories.append("reversal proxy ignored in high_volatility_risk_off regime")
            return 0.0, advisories, guard_reasons

        adjusted_bonus = confirmation_bonus
        if is_reversal_signal and follow_through_state == "at_risk":
            adjusted_bonus = min(adjusted_bonus, 0.01)
            advisories.append("reversal proxy fired but follow-through risk remains elevated")
        elif is_reversal_signal and follow_through_state == "uncertain":
            adjusted_bonus = min(adjusted_bonus, 0.03)
            advisories.append("reversal proxy fired but follow-through confirmation is still limited")

        advisory = summary or f"daily price action proxy confirms entry timing via {primary_signal_code}"
        if signal_count > 1:
            advisory = f"{advisory} ({signal_count} proxies active)"
        advisories.insert(0, advisory)
        return round(adjusted_bonus, 2), advisories, guard_reasons

    @staticmethod
    def _score_intermarket_fit(intermarket_context: dict) -> tuple[float, list[str], list[str]]:
        if not intermarket_context or not intermarket_context.get("applicable"):
            return 0.5, [], []
        if intermarket_context.get("available") is False:
            provider_error = str(intermarket_context.get("provider_error") or "").strip()
            advisory = "intermarket proxy data is unavailable"
            if provider_error:
                advisory += f": {provider_error}"
            return 0.5, [], [advisory]

        score = EntryScoringService._clamp(intermarket_context.get("score", 0.5))
        bias = str(intermarket_context.get("bias") or "mixed").strip()
        guard_reasons: list[str] = []
        advisories: list[str] = []
        summary = str(intermarket_context.get("summary") or "").strip()
        risk_flags = [
            str(item)
            for item in intermarket_context.get("risk_flags", [])
            if isinstance(item, str) and str(item).strip()
        ]
        supportive_signals = [
            str(item)
            for item in intermarket_context.get("supportive_signals", [])
            if isinstance(item, str) and str(item).strip()
        ]

        if score <= 0.25 or (bias == "headwind" and {"oil_proxy_rising", "sector_underperforming_spy"} <= set(risk_flags)):
            guard_reasons.append(summary or "sector-specific intermarket context is strongly hostile")
        elif bias == "headwind":
            advisories.append(summary or "sector-specific intermarket context remains hostile")
        elif bias == "supportive":
            advisories.append(summary or "sector-specific intermarket context is supportive")

        if supportive_signals and bias != "supportive":
            advisories.append("intermarket support exists but is not yet decisive: " + ", ".join(supportive_signals[:3]))
        if risk_flags and bias != "headwind":
            advisories.append("intermarket risks remain active: " + ", ".join(risk_flags[:3]))

        return score, guard_reasons, advisories

    @staticmethod
    def _score_mstr_fit(mstr_context: dict) -> tuple[float, list[str], list[str]]:
        if not mstr_context or not mstr_context.get("applicable"):
            return 0.5, [], []
        if mstr_context.get("available") is False:
            provider_error = str(mstr_context.get("provider_error") or "").strip()
            advisory = "MSTR-specific Strategy metrics are unavailable"
            if provider_error:
                advisory += f": {provider_error}"
            return 0.5, [], [advisory]

        score = EntryScoringService._clamp(mstr_context.get("score", 0.5))
        bias = str(mstr_context.get("bias") or "mixed").strip()
        atm_risk_context = str(mstr_context.get("atm_risk_context") or "unavailable").strip()
        btc_proxy_state = str(mstr_context.get("btc_proxy_state") or "unavailable").strip()
        summary = str(mstr_context.get("summary") or "").strip()
        risk_flags = [
            str(item)
            for item in mstr_context.get("risk_flags", [])
            if isinstance(item, str) and str(item).strip()
        ]
        guard_reasons: list[str] = []
        advisories: list[str] = []

        if atm_risk_context == "high" and btc_proxy_state == "weak":
            guard_reasons.append(
                summary or "BTC proxy is weak while MSTR carries elevated ATM/dilution risk"
            )
        elif score <= 0.22 and bias == "headwind":
            guard_reasons.append(summary or "MSTR-specific context is strongly hostile")
        elif atm_risk_context == "high":
            advisories.append("MSTR context implies elevated ATM/dilution risk; reduce confidence or size")
        elif atm_risk_context == "moderate":
            advisories.append("MSTR is in the opportunistic ATM band; treat upside capture more selectively")
        elif bias == "supportive":
            advisories.append(summary or "MSTR-specific context is supportive")

        if bool(mstr_context.get("recent_btc_purchase")):
            advisories.append("recent BTC purchase disclosure supports the treasury accumulation thesis")
        if bool(mstr_context.get("recent_capital_raise")):
            advisories.append("recent share-count expansion suggests active capital-markets activity")
        if bool(mstr_context.get("share_dilution_accelerating")):
            advisories.append("share dilution is outpacing BTC accumulation at the moment")
        if bool(mstr_context.get("stale")):
            advisories.append("Strategy metrics context is stale; use it as a weaker overlay")
        if risk_flags and not guard_reasons:
            advisories.append("MSTR-specific risks: " + ", ".join(risk_flags[:3]))

        return score, guard_reasons, advisories

    @staticmethod
    def _score_portfolio_fit(*, strategy_rules: dict, portfolio: dict) -> tuple[float, list[str], list[str]]:
        score = 0.75
        guard_reasons: list[str] = []
        advisories: list[str] = []
        open_positions_total = int(portfolio.get("open_positions_total") or 0)
        same_ticker = int(portfolio.get("same_ticker_open_positions") or 0)
        same_strategy = int(portfolio.get("same_strategy_open_positions") or 0)

        max_total = strategy_rules.get("max_open_positions_total")
        max_same_ticker = strategy_rules.get("max_same_ticker_positions")
        max_same_strategy = strategy_rules.get("max_same_strategy_open_positions")

        if isinstance(max_total, int) and open_positions_total >= max_total:
            score = 0.1
            guard_reasons.append(
                f"portfolio already has {open_positions_total} open positions and strategy max is {max_total}"
            )
        if isinstance(max_same_ticker, int) and same_ticker >= max_same_ticker:
            score = 0.1
            guard_reasons.append(
                f"ticker exposure already has {same_ticker} open positions and strategy max is {max_same_ticker}"
            )
        if isinstance(max_same_strategy, int) and same_strategy >= max_same_strategy:
            score = 0.1
            guard_reasons.append(
                f"strategy already has {same_strategy} open positions and max is {max_same_strategy}"
            )

        if not guard_reasons and same_ticker > 0:
            score = 0.68
            advisories.append(
                f"ticker already has {same_ticker} open paper position(s); keep thesis separation explicit"
            )
        elif not guard_reasons and int(portfolio.get("same_sector_open_positions") or 0) > 0:
            score = min(score, 0.66)
            advisories.append(
                f"sector exposure already has {int(portfolio.get('same_sector_open_positions') or 0)} open position(s)"
            )
        elif not guard_reasons and int(portfolio.get("same_regime_open_positions") or 0) > 0:
            score = min(score, 0.67)
            advisories.append(
                f"macro regime overlap already covers {int(portfolio.get('same_regime_open_positions') or 0)} open position(s)"
            )
        elif not guard_reasons and open_positions_total >= 8:
            score = 0.62
            advisories.append(
                f"portfolio already has {open_positions_total} open paper positions; monitor aggregate risk"
            )

        return EntryScoringService._clamp(score), guard_reasons, advisories

    @staticmethod
    def _score_risk_budget_fit(risk_budget: dict) -> tuple[float, list[str], list[str]]:
        score = 0.76
        guard_reasons: list[str] = []
        advisories: list[str] = []

        kill_switch = risk_budget.get("kill_switch") if isinstance(risk_budget.get("kill_switch"), dict) else {}
        if kill_switch.get("triggered"):
            reasons = [
                str(item)
                for item in kill_switch.get("reasons", [])
                if isinstance(item, str) and item.strip()
            ]
            guard_reasons.extend(reasons or ["risk kill switch triggered"])
            return 0.05, guard_reasons, advisories

        exposure_block_reasons = [
            str(item)
            for item in risk_budget.get("exposure_block_reasons", [])
            if isinstance(item, str) and item.strip()
        ]
        if exposure_block_reasons:
            guard_reasons.extend(exposure_block_reasons)
            return 0.08, guard_reasons, advisories

        remaining_risk_amount = float(risk_budget.get("remaining_portfolio_risk_amount") or 0.0)
        per_trade_risk_amount = float(risk_budget.get("per_trade_risk_amount") or 0.0)
        max_portfolio_risk_amount = float(risk_budget.get("max_portfolio_risk_amount") or 0.0)
        if remaining_risk_amount <= 0 or per_trade_risk_amount <= 0:
            guard_reasons.append("no remaining portfolio risk budget is available")
            return 0.05, guard_reasons, advisories

        if max_portfolio_risk_amount > 0:
            remaining_ratio = remaining_risk_amount / max_portfolio_risk_amount
            if remaining_ratio <= 0.15:
                score = min(score, 0.25)
                advisories.append("portfolio risk budget is nearly exhausted")
            elif remaining_ratio <= 0.3:
                score = min(score, 0.45)
                advisories.append("portfolio risk budget is getting tight")

        advisories.extend(
            str(item)
            for item in risk_budget.get("advisories", [])
            if isinstance(item, str) and item.strip()
        )
        return EntryScoringService._clamp(score), guard_reasons, advisories

    @staticmethod
    def _score_learned_rule_guard(learned_rule_guard: dict | None) -> tuple[float, list[str]]:
        if learned_rule_guard is None:
            return 0.0, []

        matched_rules = learned_rule_guard.get("matched_rules")
        if not isinstance(matched_rules, list) or not matched_rules:
            return 0.0, []

        average_confidence = sum(
            float(rule.get("confidence") or 0.0)
            for rule in matched_rules
            if isinstance(rule, dict)
        ) / max(len(matched_rules), 1)
        combo_penalty = 0.01 if any(rule.get("feature_scope") == "combo" for rule in matched_rules if isinstance(rule, dict)) else 0.0
        penalty = min(
            0.06,
            0.02 + (average_confidence * 0.02) + (0.005 * max(len(matched_rules) - 1, 0)) + combo_penalty,
        )
        return round(penalty, 2), [str(learned_rule_guard.get("summary") or "matched learned rule")]

    @staticmethod
    def _score_supporting_rules(supporting_rules: list[dict]) -> tuple[float, list[str]]:
        if not supporting_rules:
            return 0.0, []
        average_confidence = sum(
            float(rule.get("confidence") or 0.0)
            for rule in supporting_rules
            if isinstance(rule, dict)
        ) / max(len(supporting_rules), 1)
        combo_bonus = 0.01 if any(rule.get("feature_scope") == "combo" for rule in supporting_rules if isinstance(rule, dict)) else 0.0
        bonus = min(0.08, 0.02 + (average_confidence * 0.03) + combo_bonus)
        return round(bonus, 2), [
            "matched positive learned context rule: "
            + ", ".join(
                f"{rule.get('feature_scope')}.{rule.get('feature_key')}={rule.get('feature_value')}"
                for rule in supporting_rules[:2]
                if isinstance(rule, dict)
            )
        ]

    @staticmethod
    def _clamp(value: object) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return round(min(max(numeric, 0.0), 1.0), 4)
