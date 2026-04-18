from __future__ import annotations

from app.domains.learning.protocol import (
    AgentOperatingState,
    DECISION_PROTOCOL_VERSION,
    candidate_state_transition_for_action,
    infer_candidate_playbook,
)


class ResearchPlannerService:
    BASE_RESEARCH_BUDGET = 9
    CANDIDATE_VALIDATION_BONUS = 1

    def build_trade_candidate_package(
        self,
        *,
        ticker: str,
        strategy_version_id: int | None,
        signal_payload: dict,
        entry_context: dict | None = None,
    ) -> dict:
        entry_context = dict(entry_context or {})
        action = str(signal_payload.get("decision") or "watch").strip() or "watch"
        score_breakdown = (
            signal_payload.get("score_breakdown") if isinstance(signal_payload.get("score_breakdown"), dict) else {}
        )
        decision_context = (
            signal_payload.get("decision_context") if isinstance(signal_payload.get("decision_context"), dict) else {}
        )
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
        guard_results = signal_payload.get("guard_results") if isinstance(signal_payload.get("guard_results"), dict) else {}
        research_budget = self.BASE_RESEARCH_BUDGET + (
            self.CANDIDATE_VALIDATION_BONUS if entry_context.get("execution_mode") == "candidate_validation" else 0
        )
        evidence_confidence = round(
            max(
                float(signal_payload.get("decision_confidence") or 0.0),
                float(signal_payload.get("base_combined_score") or 0.0),
                float(score_breakdown.get("technical_score") or 0.0),
            ),
            2,
        )

        selected_tools = [
            {
                "tool_name": "market.get_snapshot",
                "arguments": {"ticker": ticker},
                "purpose": "refresh_market_snapshot_before_entry",
                "required": True,
                "priority": 10,
                "selection_reason": "Always refresh the market snapshot before taking an execution decision.",
            },
            {
                "tool_name": "calendar.get_ticker_events",
                "arguments": {"ticker": ticker, "days_ahead": 7},
                "purpose": "check_near_term_corporate_calendar_before_entry",
                "required": True,
                "priority": 10,
                "selection_reason": "Always check the near-term corporate calendar before entering.",
            },
            {
                "tool_name": "calendar.get_macro_events",
                "arguments": {"days_ahead": 3},
                "purpose": "check_near_term_macro_calendar_before_entry",
                "required": True,
                "priority": 10,
                "selection_reason": "Always check near-term macro events before entering.",
            },
        ]
        skipped_tools: list[dict] = []
        evidence_targets = [
            "refresh live market state",
            "screen near-term corporate calendar risk",
            "screen near-term macro calendar risk",
        ]

        if strategy_version_id is not None:
            selected_tools.append(
                {
                    "tool_name": "positions.list_open",
                    "arguments": {},
                    "purpose": "review_current_open_positions_before_entry",
                    "required": False,
                    "priority": 8,
                    "selection_reason": "Review open paper exposure before opening a new thesis.",
                }
            )
            evidence_targets.append("review open portfolio overlap")
        else:
            skipped_tools.append(
                {
                    "tool_name": "positions.list_open",
                    "reason": "No strategy version is attached, so portfolio overlap is lower-signal for this candidate.",
                }
            )

        if evidence_confidence >= 0.78 or action == "paper_enter":
            selected_tools.append(
                {
                    "tool_name": "market.get_multitimeframe_context",
                    "arguments": {"ticker": ticker, "timeframes": ["1M", "3M", "6M", "1Y", "5Y"]},
                    "purpose": "review_multi_horizon_chart_context_before_entry",
                    "required": False,
                    "priority": 7,
                    "selection_reason": "High-conviction candidates should confirm structure across timeframes.",
                }
            )
            evidence_targets.append("confirm multi-timeframe structure")
        else:
            skipped_tools.append(
                {
                    "tool_name": "market.get_multitimeframe_context",
                    "reason": "Multi-timeframe visual confirmation was not required at the current confidence level.",
                }
            )

        if (
            evidence_confidence >= 0.72
            or action == "paper_enter"
            or bool(guard_results.get("advisories"))
        ):
            selected_tools.append(
                {
                    "tool_name": "macro.get_context",
                    "arguments": {"limit": 5},
                    "purpose": "review_macro_context_before_entry",
                    "required": False,
                    "priority": 6,
                    "selection_reason": "Macro context matters when confidence is high or there are contextual advisories.",
                }
            )
            evidence_targets.append("check active macro regime fit")
        else:
            skipped_tools.append(
                {
                    "tool_name": "macro.get_context",
                    "reason": "Macro context was not escalated because the candidate has no strong macro conflict or catalyst.",
                }
            )

        news_context = decision_context.get("news_context") if isinstance(decision_context.get("news_context"), dict) else {}
        if evidence_confidence >= 0.76 or int(news_context.get("article_count") or 0) > 0 or action == "paper_enter":
            selected_tools.append(
                {
                    "tool_name": "news.get_ticker_news",
                    "arguments": {"ticker": ticker, "max_results": 3},
                    "purpose": "check_recent_news_before_entry",
                    "required": False,
                    "priority": 5,
                    "selection_reason": "Recent ticker-specific news should be checked for strong or conflicting catalysts.",
                }
            )
            evidence_targets.append("check ticker-specific catalyst flow")
        else:
            skipped_tools.append(
                {
                    "tool_name": "news.get_ticker_news",
                    "reason": "No fresh catalyst signal required an extra news pull before entry.",
                }
            )

        conflicts = self._build_conflicts(signal_payload=signal_payload)
        if evidence_confidence >= 0.8 or conflicts:
            selected_tools.append(
                {
                    "tool_name": "web.search",
                    "arguments": {
                        "query": f"{ticker} earnings outlook guidance stock",
                        "domains": ["reuters.com", "cnbc.com", "finance.yahoo.com", "marketwatch.com"],
                        "max_results": 3,
                    },
                    "purpose": "search_external_web_context_before_entry",
                    "required": False,
                    "priority": 4,
                    "selection_reason": (
                        "External confirmation is useful when conviction is high or the current evidence is conflicting."
                    ),
                }
            )
            evidence_targets.append("seek external confirmation")
            selected_tools.append(
                {
                    "tool_name": "web.fetch_article",
                    "arguments": {"search_result_index": 0, "max_chars": 4000},
                    "purpose": "extract_top_external_article_before_entry",
                    "required": False,
                    "priority": 3,
                    "selection_reason": "Read the top external article when web confirmation is part of the research budget.",
                }
            )
            evidence_targets.append("extract top external article")
        else:
            skipped_tools.append(
                {
                    "tool_name": "web.search",
                    "reason": "External web confirmation was not required because evidence was not strong or conflicting enough.",
                }
            )
            skipped_tools.append(
                {
                    "tool_name": "web.fetch_article",
                    "reason": "No external article fetch was planned because no external search was selected.",
                }
            )

        if entry_context.get("execution_mode") == "candidate_validation":
            selected_tools.append(
                {
                    "tool_name": "strategies.list_pipelines",
                    "arguments": {},
                    "purpose": "inspect_strategy_pipeline_before_candidate_entry",
                    "required": False,
                    "priority": 2,
                    "selection_reason": "Candidate-validation trades should inspect the strategy pipeline before entering.",
                }
            )
            evidence_targets.append("inspect candidate pipeline state")
        else:
            skipped_tools.append(
                {
                    "tool_name": "strategies.list_pipelines",
                    "reason": "Pipeline inspection is reserved for candidate-validation executions.",
                }
            )

        selected_tools, budget_skipped = self._apply_budget(selected_tools, research_budget)
        skipped_tools.extend(budget_skipped)

        research_plan = {
            "ticker": ticker.upper(),
            "entry_action": action,
            "evidence_confidence": evidence_confidence,
            "protocol": {
                "version": DECISION_PROTOCOL_VERSION,
                "current_state": AgentOperatingState.SCAN.value,
                "target_state": AgentOperatingState.ANALYZE.value,
                "playbook": infer_candidate_playbook(signal_payload).code,
            },
            "tool_budget": {
                "max_research_steps": research_budget,
                "selected_research_steps": len(selected_tools),
                "remaining_budget": max(research_budget - len(selected_tools), 0),
                "reserved_execution_steps": 1 if action == "paper_enter" else 0,
            },
            "selected_tools": [
                {
                    "tool_name": item["tool_name"],
                    "required": item["required"],
                    "priority": item["priority"],
                    "selection_reason": item["selection_reason"],
                }
                for item in selected_tools
            ],
            "skipped_tools": skipped_tools,
            "evidence_targets": evidence_targets,
        }

        decision_trace = {
            "protocol": {
                "version": DECISION_PROTOCOL_VERSION,
                "current_state": AgentOperatingState.ANALYZE.value,
                "next_state": AgentOperatingState.DECIDE.value,
                "allowed_actions": ["paper_enter", "watch", "discard"],
                "playbook": infer_candidate_playbook(signal_payload).code,
            },
            "initial_hypothesis": self._build_initial_hypothesis(
                ticker=ticker,
                quant=quant,
                visual=visual,
                signal_payload=signal_payload,
                score_breakdown=score_breakdown,
            ),
            "evidence_used": self._build_initial_evidence_used(
                signal_payload=signal_payload,
                decision_context=decision_context,
            ),
            "evidence_discarded": self._build_initial_evidence_discarded(
                signal_payload=signal_payload,
                decision_context=decision_context,
                skipped_tools=skipped_tools,
            ),
            "conflicts": conflicts,
            "evidence_gaps": self._build_evidence_gaps(selected_tools=selected_tools, skipped_tools=skipped_tools),
            "tool_plan": {
                "selected_tools": [item["tool_name"] for item in selected_tools],
                "skipped_tools": [item["tool_name"] for item in skipped_tools],
                "budget_limit": research_budget,
            },
            "decision_source": "deterministic_pre_ai",
            "final_action": action,
            "final_reason": str(signal_payload.get("rationale") or "No rationale available."),
        }

        return {
            "research_plan": research_plan,
            "decision_trace": decision_trace,
            "selected_steps": [
                {
                    "tool_name": item["tool_name"],
                    "arguments": item["arguments"],
                    "purpose": item["purpose"],
                }
                for item in selected_tools
            ],
        }

    @staticmethod
    def finalize_trade_candidate_trace(
        *,
        decision_trace: dict | None,
        final_action: str,
        final_reason: str,
        decision_source: str,
        confidence: float | None = None,
        ai_thesis: str | None = None,
        execution_outcome: str | None = None,
    ) -> dict:
        trace = dict(decision_trace or {})
        trace["decision_source"] = decision_source
        trace["final_action"] = final_action
        trace["final_reason"] = final_reason
        trace["state_transition"] = candidate_state_transition_for_action(final_action).model_dump(mode="json")
        if confidence is not None:
            trace["final_confidence"] = round(float(confidence), 2)
        if ai_thesis:
            trace["ai_thesis"] = ai_thesis
        if execution_outcome:
            trace["execution_outcome"] = execution_outcome
        return trace

    @staticmethod
    def _apply_budget(selected_tools: list[dict], research_budget: int) -> tuple[list[dict], list[dict]]:
        if len(selected_tools) <= research_budget:
            return selected_tools, []

        trimmed = sorted(selected_tools, key=lambda item: (-int(item["required"]), -int(item["priority"])))
        kept = trimmed[:research_budget]
        dropped = trimmed[research_budget:]
        kept_names = {item["tool_name"] for item in kept}
        ordered_kept = [item for item in selected_tools if item["tool_name"] in kept_names]
        skipped = [
            {
                "tool_name": item["tool_name"],
                "reason": f"Research budget limit reached before scheduling {item['tool_name']}.",
            }
            for item in dropped
        ]
        return ordered_kept, skipped

    @staticmethod
    def _build_initial_hypothesis(
        *,
        ticker: str,
        quant: dict,
        visual: dict,
        signal_payload: dict,
        score_breakdown: dict,
    ) -> str:
        setup = str(quant.get("setup") or visual.get("setup_type") or "unknown setup").strip()
        trend = str(quant.get("trend") or "unknown trend").strip()
        risk_reward = signal_payload.get("risk_reward", quant.get("risk_reward"))
        technical_score = score_breakdown.get("technical_score", signal_payload.get("combined_score"))
        return (
            f"Investigate {ticker.upper()} as a {setup} candidate in {trend} with "
            f"technical score {round(float(technical_score or 0.0), 2)} and "
            f"risk/reward {round(float(risk_reward or 0.0), 2)}."
        )

    @staticmethod
    def _build_initial_evidence_used(*, signal_payload: dict, decision_context: dict) -> list[dict]:
        quant = signal_payload.get("quant_summary") if isinstance(signal_payload.get("quant_summary"), dict) else {}
        visual = signal_payload.get("visual_summary") if isinstance(signal_payload.get("visual_summary"), dict) else {}
        score_breakdown = (
            signal_payload.get("score_breakdown") if isinstance(signal_payload.get("score_breakdown"), dict) else {}
        )
        macro_fit = decision_context.get("macro_fit") if isinstance(decision_context.get("macro_fit"), dict) else {}
        supporting_rules = (
            decision_context.get("supporting_context_rules")
            if isinstance(decision_context.get("supporting_context_rules"), list)
            else []
        )
        evidence_used: list[dict] = []

        if quant:
            evidence_used.append(
                {
                    "source": "quant",
                    "summary": f"trend={quant.get('trend')} setup={quant.get('setup')} relative_volume={quant.get('relative_volume')}",
                }
            )
        if visual:
            evidence_used.append(
                {
                    "source": "visual",
                    "summary": f"setup_type={visual.get('setup_type')} visual_score={visual.get('visual_score')}",
                }
            )
        if score_breakdown:
            evidence_used.append(
                {
                    "source": "scoring",
                    "summary": f"entry_score={score_breakdown.get('final_score')} technical={score_breakdown.get('technical_score')}",
                }
            )
        if macro_fit:
            evidence_used.append(
                {
                    "source": "macro_fit",
                    "summary": f"score={macro_fit.get('score')} conflicts={macro_fit.get('conflicts', [])} alignments={macro_fit.get('alignments', [])}",
                }
            )
        if supporting_rules:
            evidence_used.append(
                {
                    "source": "learned_rules",
                    "summary": "supporting rules: "
                    + ", ".join(
                        f"{item.get('feature_scope')}.{item.get('feature_key')}={item.get('feature_value')}"
                        for item in supporting_rules[:2]
                        if isinstance(item, dict)
                    ),
                }
            )
        return evidence_used

    @staticmethod
    def _build_initial_evidence_discarded(
        *,
        signal_payload: dict,
        decision_context: dict,
        skipped_tools: list[dict],
    ) -> list[dict]:
        discarded: list[dict] = []
        guard_results = signal_payload.get("guard_results") if isinstance(signal_payload.get("guard_results"), dict) else {}
        advisories = guard_results.get("advisories") if isinstance(guard_results.get("advisories"), list) else []
        for advisory in advisories[:3]:
            if isinstance(advisory, str) and advisory:
                discarded.append({"source": "advisory", "summary": advisory})
        for item in skipped_tools[:4]:
            if isinstance(item, dict):
                discarded.append(
                    {
                        "source": "skipped_tool",
                        "summary": f"{item.get('tool_name')}: {item.get('reason')}",
                    }
                )

        learned_rule_guard = (
            decision_context.get("learned_rule_guard")
            if isinstance(decision_context.get("learned_rule_guard"), dict)
            else None
        )
        if learned_rule_guard is not None:
            discarded.append(
                {
                    "source": "learned_rule_guard",
                    "summary": str(learned_rule_guard.get("summary") or "Matched learned rule guard"),
                }
            )
        return discarded

    @staticmethod
    def _build_conflicts(*, signal_payload: dict) -> list[str]:
        score_breakdown = (
            signal_payload.get("score_breakdown") if isinstance(signal_payload.get("score_breakdown"), dict) else {}
        )
        guard_results = signal_payload.get("guard_results") if isinstance(signal_payload.get("guard_results"), dict) else {}
        conflicts: list[str] = []
        technical_score = float(score_breakdown.get("technical_score") or 0.0)
        final_score = float(score_breakdown.get("final_score") or 0.0)
        reasons = [str(item) for item in guard_results.get("reasons", []) if isinstance(item, str)]
        advisories = [str(item) for item in guard_results.get("advisories", []) if isinstance(item, str)]
        if technical_score >= 0.75 and final_score < technical_score:
            conflicts.append("The technical setup is stronger than the final entry score after contextual adjustments.")
        if reasons:
            conflicts.append("Contextual hard guards are active against the candidate.")
        if advisories and not reasons:
            conflicts.append("The setup has contextual advisories that may justify further research before entry.")
        return conflicts

    @staticmethod
    def _build_evidence_gaps(*, selected_tools: list[dict], skipped_tools: list[dict]) -> list[str]:
        gaps: list[str] = []
        skipped_names = {item.get("tool_name") for item in skipped_tools if isinstance(item, dict)}
        selected_names = {item["tool_name"] for item in selected_tools}
        if "news.get_ticker_news" not in selected_names and "news.get_ticker_news" in skipped_names:
            gaps.append("No live ticker-news pull is planned in this research pass.")
        if "web.search" not in selected_names and "web.search" in skipped_names:
            gaps.append("No external article confirmation is planned in this research pass.")
        if "market.get_multitimeframe_context" not in selected_names and "market.get_multitimeframe_context" in skipped_names:
            gaps.append("No multi-timeframe visual confirmation is planned in this research pass.")
        return gaps
