from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.db.models.analysis import AnalysisRun
from app.db.models.decision_context import DecisionContextSnapshot, FeatureOutcomeStat, StrategyContextRule
from app.db.models.position import Position
from app.db.models.signal import TradeSignal
from app.db.models.strategy import StrategyVersion


@dataclass(frozen=True)
class FeatureObservation:
    scope: str
    key: str
    value: str


COMBO_FEATURE_DEFINITIONS: tuple[tuple[tuple[str, str], tuple[str, str], str], ...] = (
    (("quant", "setup"), ("news", "has_news"), "setup__has_news"),
    (("quant", "setup"), ("calendar", "near_earnings"), "setup__near_earnings"),
    (("quant", "setup"), ("calendar", "near_macro_high_impact"), "setup__near_macro_high_impact"),
    (("quant", "setup"), ("macro", "primary_regime"), "setup__primary_regime"),
    (("quant", "trend"), ("macro", "primary_regime"), "trend__primary_regime"),
)
COMBO_FEATURE_DEFINITIONS_BY_KEY = {definition[2]: definition for definition in COMBO_FEATURE_DEFINITIONS}
ACTIONABLE_COMBO_KEYS = set(COMBO_FEATURE_DEFINITIONS_BY_KEY)


def _build_combo_features(features: list[FeatureObservation]) -> list[FeatureObservation]:
    feature_map = {
        (feature.scope, feature.key): feature.value
        for feature in features
        if feature.value not in {"", "unknown", "none"}
    }
    combos: list[FeatureObservation] = []
    seen: set[tuple[str, str, str]] = set()

    for left, right, combo_key in COMBO_FEATURE_DEFINITIONS:
        left_value = feature_map.get(left)
        right_value = feature_map.get(right)
        if left_value is None or right_value is None:
            continue
        combo_feature = ("combo", combo_key, f"{left_value}|{right_value}")
        if combo_feature in seen:
            continue
        seen.add(combo_feature)
        combos.append(FeatureObservation(*combo_feature))

    return combos


def _parse_combo_components(feature_key: str, feature_value: str) -> list[dict]:
    definition = COMBO_FEATURE_DEFINITIONS_BY_KEY.get(feature_key)
    if definition is None:
        return []

    values = feature_value.split("|", 1)
    if len(values) != 2:
        return []

    left, right, _ = definition
    return [
        {"scope": left[0], "key": left[1], "value": values[0]},
        {"scope": right[0], "key": right[1], "value": values[1]},
    ]


class DecisionContextService:
    def record_trade_candidate_context(
        self,
        session: Session,
        *,
        signal: TradeSignal,
        analysis_run: AnalysisRun,
        ticker: str,
        planned_entry_action: str,
        final_decision: str,
        step_results: list[dict],
        position_id: int | None,
        execution_guard: dict | None,
    ) -> DecisionContextSnapshot:
        signal_context = dict(signal.signal_context or {})
        position = session.get(Position, position_id) if position_id is not None else None
        executed_entry_context = dict(position.entry_context or {}) if position is not None and isinstance(position.entry_context, dict) else {}
        snapshot = DecisionContextSnapshot(
            signal_id=signal.id,
            analysis_run_id=analysis_run.id,
            position_id=position_id,
            strategy_id=signal.strategy_id,
            strategy_version_id=signal.strategy_version_id,
            ticker=ticker.upper(),
            decision_phase="do",
            planner_action=planned_entry_action,
            executed=position_id is not None,
            execution_outcome=final_decision,
            quant_features=dict(analysis_run.quant_summary or {}),
            visual_features=dict(analysis_run.visual_summary or {}),
            calendar_context=self._build_calendar_context(step_results, execution_guard),
            news_context=self._extract_tool_result(step_results, "news.get_ticker_news"),
            web_context={
                "search": self._extract_tool_result(step_results, "web.search"),
                "article": self._extract_tool_result(step_results, "web.fetch_article"),
            },
            macro_context=self._extract_tool_result(step_results, "macro.get_context"),
            ai_context=dict(signal_context.get("ai_overlay") or {}),
            position_context={
                "entry_context": signal_context,
                "executed_entry_context": executed_entry_context,
                "research_plan": dict(signal_context.get("research_plan") or {}),
                "decision_trace": dict(signal_context.get("decision_trace") or {}),
                "executed_research_plan": dict(executed_entry_context.get("research_plan") or {}),
                "executed_decision_trace": dict(executed_entry_context.get("decision_trace") or {}),
                "decision_context": dict(signal_context.get("decision_context") or {}),
                "score_breakdown": dict(signal_context.get("score_breakdown") or {}),
                "guard_results": dict(signal_context.get("guard_results") or {}),
                "execution_guard": execution_guard or {},
            },
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot

    @staticmethod
    def _extract_tool_result(step_results: list[dict], tool_name: str) -> dict:
        for step in step_results:
            if step.get("tool_name") == tool_name:
                result = step.get("result")
                return dict(result) if isinstance(result, dict) else {}
        return {}

    def _build_calendar_context(self, step_results: list[dict], execution_guard: dict | None) -> dict:
        corporate = self._extract_tool_result(step_results, "calendar.get_ticker_events")
        macro = self._extract_tool_result(step_results, "calendar.get_macro_events")
        return {
            "corporate": corporate,
            "macro": macro,
            "guard": execution_guard or {},
        }


class FeatureRelevanceService:
    def recompute_all(self, session: Session) -> int:
        snapshots = list(
            session.scalars(
                select(DecisionContextSnapshot).where(DecisionContextSnapshot.strategy_version_id.is_not(None))
            ).all()
        )
        version_ids = sorted({snapshot.strategy_version_id for snapshot in snapshots if snapshot.strategy_version_id is not None})
        generated = 0
        for strategy_version_id in version_ids:
            generated += self.recompute_for_strategy_version(session, strategy_version_id)
        return generated

    def recompute_for_strategy_version(self, session: Session, strategy_version_id: int) -> int:
        snapshots = list(
            session.scalars(
                select(DecisionContextSnapshot).where(DecisionContextSnapshot.strategy_version_id == strategy_version_id)
            ).all()
        )

        session.query(FeatureOutcomeStat).filter(FeatureOutcomeStat.strategy_version_id == strategy_version_id).delete()
        session.commit()

        aggregates: dict[tuple[str, str, str], dict] = defaultdict(
            lambda: {
                "strategy_id": None,
                "strategy_version_id": strategy_version_id,
                "sample_size": 0,
                "executed_count": 0,
                "wins_count": 0,
                "losses_count": 0,
                "pnl_values": [],
                "drawdown_values": [],
                "runup_values": [],
                "components": None,
            }
        )

        for snapshot in snapshots:
            if snapshot.position_id is None:
                continue
            position = session.get(Position, snapshot.position_id)
            if position is None or position.status != "closed":
                continue

            features = self._extract_features(snapshot)
            for feature in features:
                key = (feature.scope, feature.key, feature.value)
                bucket = aggregates[key]
                bucket["strategy_id"] = snapshot.strategy_id
                if bucket["components"] is None and feature.scope == "combo":
                    bucket["components"] = _parse_combo_components(feature.key, feature.value)
                bucket["sample_size"] += 1
                bucket["executed_count"] += 1
                pnl_pct = position.pnl_pct or 0.0
                if pnl_pct > 0:
                    bucket["wins_count"] += 1
                else:
                    bucket["losses_count"] += 1
                bucket["pnl_values"].append(pnl_pct)
                if position.max_drawdown_pct is not None:
                    bucket["drawdown_values"].append(position.max_drawdown_pct)
                if position.max_runup_pct is not None:
                    bucket["runup_values"].append(position.max_runup_pct)

        created = 0
        for (scope, key, value), bucket in aggregates.items():
            sample_size = bucket["sample_size"]
            avg_pnl_pct = round(sum(bucket["pnl_values"]) / sample_size, 2) if sample_size else None
            avg_drawdown_pct = (
                round(sum(bucket["drawdown_values"]) / len(bucket["drawdown_values"]), 2)
                if bucket["drawdown_values"]
                else None
            )
            avg_runup_pct = (
                round(sum(bucket["runup_values"]) / len(bucket["runup_values"]), 2)
                if bucket["runup_values"]
                else None
            )
            win_rate = round((bucket["wins_count"] / sample_size) * 100, 2) if sample_size else None
            expectancy = avg_pnl_pct
            relevance_score = self._calculate_relevance_score(avg_pnl_pct, sample_size)
            session.add(
                FeatureOutcomeStat(
                    strategy_id=bucket["strategy_id"],
                    strategy_version_id=bucket["strategy_version_id"],
                    feature_scope=scope,
                    feature_key=key,
                    feature_value=value,
                    sample_size=sample_size,
                    executed_count=bucket["executed_count"],
                    wins_count=bucket["wins_count"],
                    losses_count=bucket["losses_count"],
                    win_rate=win_rate,
                    avg_pnl_pct=avg_pnl_pct,
                    avg_drawdown_pct=avg_drawdown_pct,
                    avg_runup_pct=avg_runup_pct,
                    expectancy=expectancy,
                    relevance_score=relevance_score,
                    evidence_payload={
                        "position_count": sample_size,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "components": bucket["components"] or [],
                    },
                )
            )
            created += 1

        session.commit()
        return created

    def _extract_features(self, snapshot: DecisionContextSnapshot) -> list[FeatureObservation]:
        quant = snapshot.quant_features or {}
        visual = snapshot.visual_features or {}
        position_context = snapshot.position_context or {}
        decision_context = (
            position_context.get("decision_context") if isinstance(position_context.get("decision_context"), dict) else {}
        )
        decision_calendar = (
            decision_context.get("calendar_context") if isinstance(decision_context.get("calendar_context"), dict) else {}
        )
        decision_news = (
            decision_context.get("news_context") if isinstance(decision_context.get("news_context"), dict) else {}
        )
        decision_macro = (
            decision_context.get("macro_context") if isinstance(decision_context.get("macro_context"), dict) else {}
        )
        calendar = snapshot.calendar_context or {}
        if not calendar:
            calendar = decision_calendar
        news = snapshot.news_context or {}
        if not news:
            news = decision_news
        web = snapshot.web_context or {}
        macro = snapshot.macro_context or {}
        if not macro:
            macro = decision_macro
        ai = snapshot.ai_context or {}

        active_regimes = macro.get("active_regimes") if isinstance(macro.get("active_regimes"), list) else []
        if not active_regimes:
            active_regimes = (
                decision_macro.get("active_regimes") if isinstance(decision_macro.get("active_regimes"), list) else []
            )
        corporate_events = self._extract_event_count(calendar.get("corporate"))
        if corporate_events == 0:
            if isinstance(calendar.get("corporate_event_count"), int):
                corporate_events = int(calendar.get("corporate_event_count") or 0)
            elif isinstance(decision_calendar.get("corporate_event_count"), int):
                corporate_events = int(decision_calendar.get("corporate_event_count") or 0)
            elif isinstance(decision_calendar.get("near_earnings_days"), int):
                corporate_events = 1
        macro_events = self._extract_event_count(calendar.get("macro"))
        if macro_events == 0:
            if isinstance(calendar.get("macro_event_count"), int):
                macro_events = int(calendar.get("macro_event_count") or 0)
            elif isinstance(decision_calendar.get("macro_event_count"), int):
                macro_events = int(decision_calendar.get("macro_event_count") or 0)
            elif isinstance(decision_calendar.get("near_macro_high_impact_days"), int):
                macro_events = 1
        news_articles = news.get("articles") if isinstance(news.get("articles"), list) else []
        if not news_articles:
            if isinstance(news.get("article_count"), int):
                news_articles = [{}] * int(news.get("article_count") or 0)
            elif isinstance(decision_news.get("articles"), list):
                news_articles = list(decision_news.get("articles") or [])
            elif isinstance(decision_news.get("article_count"), int):
                news_articles = [{}] * int(decision_news.get("article_count") or 0)
        web_search_results = web.get("search", {}).get("results") if isinstance(web.get("search"), dict) else []
        web_articles = 1 if isinstance(web.get("article"), dict) and web.get("article", {}).get("text") else 0

        base_features = [
            FeatureObservation("quant", "trend", self._stringify(quant.get("trend"), fallback="unknown")),
            FeatureObservation("quant", "setup", self._stringify(quant.get("setup"), fallback="unknown")),
            FeatureObservation(
                "quant",
                "relative_volume_bucket",
                self._bucketize_float(quant.get("relative_volume"), [(1.0, "subdued"), (1.5, "normal"), (2.5, "high")], fallback="extreme"),
            ),
            FeatureObservation(
                "quant",
                "risk_reward_bucket",
                self._bucketize_float(quant.get("risk_reward"), [(1.5, "poor"), (2.5, "acceptable"), (4.0, "good")], fallback="strong"),
            ),
            FeatureObservation("visual", "setup_type", self._stringify(visual.get("setup_type"), fallback="unknown")),
            FeatureObservation("calendar", "near_earnings", "true" if corporate_events > 0 else "false"),
            FeatureObservation("calendar", "near_macro_high_impact", "true" if macro_events > 0 else "false"),
            FeatureObservation("news", "has_news", "true" if len(news_articles) > 0 else "false"),
            FeatureObservation("web", "has_external_confirmation", "true" if len(web_search_results or []) > 0 or web_articles > 0 else "false"),
            FeatureObservation("macro", "primary_regime", self._stringify(active_regimes[0] if active_regimes else None, fallback="none")),
            FeatureObservation("ai", "action", self._stringify(ai.get("action"), fallback="none")),
            FeatureObservation("planner", "execution_outcome", self._stringify(snapshot.execution_outcome, fallback="unknown")),
        ]
        return base_features + _build_combo_features(base_features)

    @staticmethod
    def _extract_event_count(payload: object) -> int:
        if not isinstance(payload, dict):
            return 0
        events = payload.get("events")
        return len(events) if isinstance(events, list) else 0

    @staticmethod
    def _stringify(value: object, *, fallback: str) -> str:
        if value is None:
            return fallback
        text = str(value).strip()
        return text or fallback

    @staticmethod
    def _bucketize_float(value: object, ranges: list[tuple[float, str]], *, fallback: str) -> str:
        if not isinstance(value, (int, float)):
            return "unknown"
        numeric = float(value)
        for threshold, label in ranges:
            if numeric < threshold:
                return label
        return fallback

    @staticmethod
    def _calculate_relevance_score(avg_pnl_pct: float | None, sample_size: int) -> float | None:
        if avg_pnl_pct is None or sample_size <= 0:
            return None
        return round(abs(avg_pnl_pct) * math.log(sample_size + 1), 2)


class StrategyContextAdaptationService:
    MIN_SAMPLE_SIZE = 2
    MAX_AVG_PNL_PCT_FOR_AVOID = -1.0
    MIN_AVG_PNL_PCT_FOR_SUPPORT = 1.0
    MIN_WIN_RATE_FOR_SUPPORT = 60.0

    def refresh_rules(self, session: Session) -> int:
        session.query(StrategyContextRule).filter(StrategyContextRule.source == "feature_outcome_stat").delete()
        session.commit()

        negative_stats = list(
            session.scalars(
                select(FeatureOutcomeStat).where(
                    FeatureOutcomeStat.strategy_version_id.is_not(None),
                    FeatureOutcomeStat.sample_size >= self.MIN_SAMPLE_SIZE,
                    FeatureOutcomeStat.avg_pnl_pct.is_not(None),
                    FeatureOutcomeStat.avg_pnl_pct <= self.MAX_AVG_PNL_PCT_FOR_AVOID,
                )
            ).all()
        )
        positive_stats = list(
            session.scalars(
                select(FeatureOutcomeStat).where(
                    FeatureOutcomeStat.strategy_version_id.is_not(None),
                    FeatureOutcomeStat.sample_size >= self.MIN_SAMPLE_SIZE,
                    FeatureOutcomeStat.avg_pnl_pct.is_not(None),
                    FeatureOutcomeStat.avg_pnl_pct >= self.MIN_AVG_PNL_PCT_FOR_SUPPORT,
                    FeatureOutcomeStat.win_rate.is_not(None),
                    FeatureOutcomeStat.win_rate >= self.MIN_WIN_RATE_FOR_SUPPORT,
                )
            ).all()
        )

        created = 0
        for stat in negative_stats:
            if not self._is_actionable_feature(stat):
                continue
            session.add(
                StrategyContextRule(
                    strategy_id=stat.strategy_id,
                    strategy_version_id=stat.strategy_version_id,
                    feature_scope=stat.feature_scope,
                    feature_key=stat.feature_key,
                    feature_value=stat.feature_value,
                    action_type="downgrade_to_watch",
                    rationale=self._build_rule_rationale(stat, supportive=False),
                    confidence=min(max((stat.relevance_score or 0.0) / 10.0, 0.0), 1.0),
                    status="active",
                    source="feature_outcome_stat",
                    evidence_payload={
                        "sample_size": stat.sample_size,
                        "avg_pnl_pct": stat.avg_pnl_pct,
                        "wins_count": stat.wins_count,
                        "losses_count": stat.losses_count,
                        "relevance_score": stat.relevance_score,
                    },
                )
            )
            created += 1
        for stat in positive_stats:
            if not self._is_actionable_feature(stat):
                continue
            session.add(
                StrategyContextRule(
                    strategy_id=stat.strategy_id,
                    strategy_version_id=stat.strategy_version_id,
                    feature_scope=stat.feature_scope,
                    feature_key=stat.feature_key,
                    feature_value=stat.feature_value,
                    action_type="boost_confidence",
                    rationale=self._build_rule_rationale(stat, supportive=True),
                    confidence=min(max((stat.relevance_score or 0.0) / 10.0, 0.0), 1.0),
                    status="active",
                    source="feature_outcome_stat",
                    evidence_payload={
                        "sample_size": stat.sample_size,
                        "avg_pnl_pct": stat.avg_pnl_pct,
                        "win_rate": stat.win_rate,
                        "wins_count": stat.wins_count,
                        "losses_count": stat.losses_count,
                        "relevance_score": stat.relevance_score,
                    },
                )
            )
            created += 1
        session.commit()
        return created

    def evaluate_entry(self, session: Session, *, strategy_version_id: int | None, signal_payload: dict) -> dict | None:
        matched_rules = self.list_matching_rules(
            session,
            strategy_version_id=strategy_version_id,
            signal_payload=signal_payload,
        )
        blocking_rules = [rule for rule in matched_rules if rule.action_type == "downgrade_to_watch"]
        if not blocking_rules:
            return None
        blocking_rules.sort(key=lambda rule: float(rule.confidence or 0.0), reverse=True)
        top_rule = blocking_rules[0]
        return {
            "reason": "strategy_context_rule",
            "summary": top_rule.rationale,
            "action_type": top_rule.action_type,
            "matched_rules": [
                {
                    "id": rule.id,
                    "feature_scope": rule.feature_scope,
                    "feature_key": rule.feature_key,
                    "feature_value": rule.feature_value,
                    "action_type": rule.action_type,
                    "confidence": rule.confidence,
                }
                for rule in blocking_rules
            ],
        }

    def list_supporting_rules(self, session: Session, *, strategy_version_id: int | None, signal_payload: dict) -> list[dict]:
        matched_rules = self.list_matching_rules(
            session,
            strategy_version_id=strategy_version_id,
            signal_payload=signal_payload,
        )
        supporting_rules = [
            rule
            for rule in matched_rules
            if rule.action_type in {"boost_confidence", "upgrade_to_enter"}
        ]
        supporting_rules.sort(key=lambda rule: float(rule.confidence or 0.0), reverse=True)
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
            for rule in supporting_rules
        ]

    def list_matching_rules(self, session: Session, *, strategy_version_id: int | None, signal_payload: dict) -> list[StrategyContextRule]:
        if strategy_version_id is None:
            return []
        current_version = session.get(StrategyVersion, strategy_version_id)
        strategy_id = current_version.strategy_id if current_version is not None else None
        rules = list(
            session.scalars(
                select(StrategyContextRule).where(
                    StrategyContextRule.status == "active",
                    or_(
                        StrategyContextRule.strategy_version_id == strategy_version_id,
                        StrategyContextRule.strategy_id == strategy_id if strategy_id is not None else false(),
                    ),
                )
            ).all()
        )
        if not rules:
            return []

        features = {(feature.scope, feature.key, feature.value) for feature in self._extract_signal_features(signal_payload)}
        matched_rules = [
            rule
            for rule in rules
            if (rule.feature_scope, rule.feature_key, rule.feature_value) in features
        ]
        matched_rules.sort(
            key=lambda rule: (
                0 if rule.action_type == "downgrade_to_watch" else 1,
                -(float(rule.confidence or 0.0)),
                rule.id,
            )
        )
        return matched_rules

    def _extract_signal_features(self, signal_payload: dict) -> list[FeatureObservation]:
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
        ai_overlay = signal_payload.get("ai_overlay") if isinstance(signal_payload.get("ai_overlay"), dict) else {}
        decision_context = (
            signal_payload.get("decision_context") if isinstance(signal_payload.get("decision_context"), dict) else {}
        )
        calendar_context = (
            decision_context.get("calendar_context") if isinstance(decision_context.get("calendar_context"), dict) else {}
        )
        news_context = decision_context.get("news_context") if isinstance(decision_context.get("news_context"), dict) else {}
        macro_context = (
            decision_context.get("macro_context") if isinstance(decision_context.get("macro_context"), dict) else {}
        )
        active_regimes = macro_context.get("active_regimes") if isinstance(macro_context.get("active_regimes"), list) else []
        base_features = [
            FeatureObservation("quant", "trend", FeatureRelevanceService._stringify(quant.get("trend"), fallback="unknown")),
            FeatureObservation("quant", "setup", FeatureRelevanceService._stringify(quant.get("setup"), fallback="unknown")),
            FeatureObservation(
                "quant",
                "relative_volume_bucket",
                FeatureRelevanceService._bucketize_float(
                    quant.get("relative_volume"),
                    [(1.0, "subdued"), (1.5, "normal"), (2.5, "high")],
                    fallback="extreme",
                ),
            ),
            FeatureObservation(
                "quant",
                "risk_reward_bucket",
                FeatureRelevanceService._bucketize_float(
                    signal_payload.get("risk_reward", quant.get("risk_reward")),
                    [(1.5, "poor"), (2.5, "acceptable"), (4.0, "good")],
                    fallback="strong",
                ),
            ),
            FeatureObservation("visual", "setup_type", FeatureRelevanceService._stringify(visual.get("setup_type"), fallback="unknown")),
            FeatureObservation(
                "calendar",
                "near_earnings",
                "true" if isinstance(calendar_context.get("near_earnings_days"), int) else "false",
            ),
            FeatureObservation(
                "calendar",
                "near_macro_high_impact",
                "true" if isinstance(calendar_context.get("near_macro_high_impact_days"), int) else "false",
            ),
            FeatureObservation(
                "news",
                "has_news",
                "true" if int(news_context.get("article_count") or 0) > 0 else "false",
            ),
            FeatureObservation(
                "macro",
                "primary_regime",
                FeatureRelevanceService._stringify(active_regimes[0] if active_regimes else None, fallback="none"),
            ),
            FeatureObservation("ai", "action", FeatureRelevanceService._stringify(ai_overlay.get("action"), fallback="none")),
        ]
        return base_features + _build_combo_features(base_features)

    @staticmethod
    def _build_rule_rationale(stat: FeatureOutcomeStat, *, supportive: bool) -> str:
        feature_label = StrategyContextAdaptationService._describe_feature_stat(stat)
        if supportive:
            return (
                f"Lean into entries when {feature_label} because historical average PnL is "
                f"{stat.avg_pnl_pct}% with win rate {stat.win_rate}% across {stat.sample_size} trades."
            )
        return (
            f"Avoid entries when {feature_label} because historical average PnL is "
            f"{stat.avg_pnl_pct}% across {stat.sample_size} trades."
        )

    @staticmethod
    def _describe_feature_stat(stat: FeatureOutcomeStat) -> str:
        if stat.feature_scope != "combo":
            return f"{stat.feature_scope}.{stat.feature_key}={stat.feature_value}"

        evidence_payload = stat.evidence_payload if isinstance(stat.evidence_payload, dict) else {}
        components = evidence_payload.get("components") if isinstance(evidence_payload.get("components"), list) else []
        if not components:
            components = _parse_combo_components(stat.feature_key, stat.feature_value)
        if not components:
            return f"combo.{stat.feature_key}={stat.feature_value}"
        return " + ".join(
            f"{item['scope']}.{item['key']}={item['value']}"
            for item in components
            if isinstance(item, dict)
        )

    @staticmethod
    def _is_actionable_feature(stat: FeatureOutcomeStat) -> bool:
        return (stat.feature_scope, stat.feature_key) in {
            ("quant", "setup"),
            ("quant", "trend"),
            ("quant", "relative_volume_bucket"),
            ("quant", "risk_reward_bucket"),
            ("visual", "setup_type"),
            ("calendar", "near_earnings"),
            ("calendar", "near_macro_high_impact"),
            ("news", "has_news"),
            ("macro", "primary_regime"),
            ("ai", "action"),
        } or (stat.feature_scope == "combo" and stat.feature_key in ACTIONABLE_COMBO_KEYS)
