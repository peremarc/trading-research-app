# Implementation Journal

## Purpose

This file is the operational journal for implementation work in this repository.
It exists to preserve enough context to resume work from the exact same point if
the session is interrupted.

Update this file after every meaningful implementation step.

## Update Rules

- Record what changed, why it changed, and what remains pending.
- Keep entries short, factual, and chronological.
- Always update `Current Focus`, `Next Step`, and `Resume Checklist`.
- When a plan changes, note the reason explicitly.
- When work is blocked, write the blocker and the unblock condition.

## Current Focus

Run the doctrine-driven paper-trading stack on the internal IBKR proxy with a
live `MONITOR` loop, while continuing to turn the agent protocol into
enforceable execution policy.

Current gap under review:

- keeping the runtime workspace on a clean post-reset SQLite baseline while
  doctrine and monitor hardening continue toward production handoff
- keeping autonomous execution resilient to transient AI-provider failures so
  provider `429`s or timeouts degrade to deterministic behavior instead of
  pausing the whole bot
- making the new IBKR-driven `MONITOR` loop doctrine-aware so position
  management uses explicit regime policy instead of only heuristics plus agent
  advice
- defining a dedicated position-management regime policy surface:
  allowed actions, tighten/extend permissions, forced reduce/exit rules and
  cooldown behavior by regime
- extending the persisted `MarketStateSnapshot` from an observational artifact
  into a hard execution constraint across the whole lifecycle
- consolidating the remaining market integrations still outside the internal
  IBKR proxy layer, now that discovery universe selection is scanner-backed
- propagating regime policy beyond candidate entry into open-position
  management, scheduler pacing and review/improvement gates
- adding a replay/backtest promotion path so playbook or policy changes are
  validated before they can affect live paper execution

## Current State Snapshot

What already exists in the codebase:

- FastAPI backend with modular domains: `system`, `strategy`, `market`,
  `execution`, `learning`
- first-class `hypotheses`, `setups` and `signal_definitions` catalogs with API
  endpoints and seed data
- persisted strategies, strategy versions, screeners, watchlists, trade
  signals, analysis runs, positions, trade reviews, journal and memory
- `hypothesis/setup/signal_definition` traceability on generated trade signals
  and positions
- explicit `system_events` event log with bounded PDCA dispatch for selected
  operational transitions
- operator-curated watchlist creation with `initial_items`, plus source-aware
  event policy so manual additions can trigger `DO` while discovery-generated
  additions are ignored by the dispatcher
- `TradeSignal` naming introduced in the market/API layer while keeping
  compatibility aliases for `Signal` and `/signals`
- explicit agent protocol with objective, doctrine, playbooks, operational
  states and structured decision schemas
- persisted `market_state_snapshots` with capture service, orchestrator
  integration, listing endpoints and reuse in bot context
- frontend snapshot panel and bot-chat responses that expose the latest market
  state and regime
- deterministic regime-policy catalog that maps regime labels to allowed
  playbooks, bias, sizing multipliers, entry caps and event-risk blocks
- regime policy injected into candidate decision context, scoring, position
  sizing, signal persistence, position entry context and journal observations
- `ibkr_proxy` market-data provider support with `symbol -> conid` resolution,
  internal snapshot/history parsing, live-price overlay on computed snapshots
  and `.env` defaults pointing at the internal proxy
- `opportunity_discovery` can now source a dynamic ticker universe from the
  internal IBKR scanner endpoints, with deterministic fallback to a configured
  list when scanner data is unavailable
- strategy evolution now stores bounded variant hypotheses plus lineage metadata
  instead of recursively concatenating the full prior hypothesis on every fork,
  and the repository includes a CLI utility to compact oversized historical
  hypotheses in-place
- journal and memory retention now auto-prune noisy PDCA/evolution history on
  write, and the repository includes a bulk maintenance CLI to trim existing
  databases in-place
- the workspace SQLite runtime has been reset to a clean migrated+seeded
  baseline: strategy catalog present, operational history tables empty
- realtime SSE monitor for open positions, started/stopped with the scheduler
  bot runtime, with IBKR subscription management, quote parsing, immediate
  stop/target handling and event-driven handoff into `ExitManagementService`
- paper trading flow with position lifecycle management
- research planner with tool budget and structured `decision_trace`
- risk budget, position sizing, candidate validation, replay-style checks
- simple frontend console for runtime, pipeline, positions, journal, chat and
  market-state visibility

Main structural gaps versus `PLAN.md`:

- regime policy currently governs new-entry candidate flow, but not yet
  open-position management or scheduler throttling
- the realtime monitor currently subscribes open positions only; it does not
  yet consume watch/alert candidates or promote a richer alert state machine
- the realtime monitor uses SSE and feeds the existing exit/management engine,
  but regime policy is not yet a hard guard on `HOLD`, `REDUCE`, `EXIT` and
  stop/target adjustments
- news/calendar are still split across mixed providers rather than
  consistently routed through the internal IBKR layer where available
- learning can record and propose improvements, but there is still no explicit
  replay/backtest promotion gate before a policy or playbook change is adopted
- `TradeSignal` naming now exists in the market/API layer, but persistence and
  downstream references still use older names such as `signals`, `signal_id`
  and `SignalService`
- PDCA has a bounded event-driven path for selected transitions, but still
  falls back to the sequential full cycle for the general case
- visual analysis is heuristic, not model-based computer vision
- `analysis_runs`, journals and reviews still rely partly on embedded text/json
  rather than fully normalized references for every concept

## Last Confirmed Working Area

Files reviewed during the latest architecture and policy pass:

- `PLAN.md`
- `IMPLEMENTATION_JOURNAL.md`
- `backend/.env.example`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/domains/system/schemas.py`
- `backend/app/domains/learning/protocol.py`
- `backend/app/domains/learning/agent.py`
- `backend/app/domains/learning/planning.py`
- `backend/app/domains/learning/world_state.py`
- `backend/app/domains/learning/decisioning.py`
- `backend/app/domains/learning/repositories.py`
- `backend/app/domains/learning/services.py`
- `backend/app/domains/learning/tools.py`
- `backend/app/domains/learning/api.py`
- `backend/app/domains/learning/schemas.py`
- `backend/app/providers/market_data/base.py`
- `backend/app/providers/market_data/ibkr_proxy_provider.py`
- `backend/app/providers/market_data/ibkr_realtime_client.py`
- `backend/app/providers/market_data/twelve_data_provider.py`
- `backend/app/domains/market/discovery.py`
- `backend/app/domains/strategy/services.py`
- `backend/app/domains/execution/monitoring.py`
- `backend/app/domains/execution/services.py`
- `backend/app/domains/market/services.py`
- `backend/scripts/compact_strategy_hypotheses.py`
- `backend/scripts/prune_learning_history.py`
- `backend/app/frontend/index.html`
- `backend/app/frontend/app.js`
- `backend/app/frontend/styles.css`
- `backend/tests/test_ibkr_realtime_client.py`
- `backend/tests/test_opportunity_discovery.py`
- `backend/tests/test_learning_history_retention.py`
- `backend/tests/test_strategy_history_compaction.py`
- `backend/tests/test_ai_agent.py`
- `backend/tests/test_autonomous_position_management.py`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_ibkr_proxy_provider.py`
- `backend/tests/test_risk_budget.py`
- `backend/tests/test_learning_loop.py`
- `backend/tests/test_bot_chat.py`
- `backend/tests/test_bootstrap.py`

## Next Step

Make the new realtime monitor doctrinal instead of merely reactive:

1. define a position-management regime policy in `protocol.py` that maps each
   regime to permitted actions (`HOLD`, `REDUCE`, `EXIT`, tighten stop, extend
   target), forced de-risking rules and monitor cooldown behavior
2. apply that policy in `execution/services.py` before any position-management
   adjustment so the monitor cannot extend or maintain risk when the active
   regime forbids it
3. persist policy traceability for monitor-driven decisions and events:
   `policy_version`, `management_policy`, `blocked_reason`, `monitor_event`
   and `decision_source`
4. cover the behavior with regime-specific tests (`bullish_trend`,
   `range_mixed`, `macro_uncertainty`, `high_volatility_risk_off`)
5. once that is stable, extend realtime subscriptions from open positions to
   explicit watch/alert entities so `WATCH` becomes an event-driven state

## Resume Checklist

Before continuing implementation:

- read this file
- read `PLAN.md`
- inspect pending git changes
- review `config`, `market/services` and `providers/market_data` before
  changing data-source behavior
- review `execution/monitoring` and `execution/services` before changing
  runtime monitoring or position-management behavior
- review `protocol`, `world_state`, `decisioning` and `services` before
  changing agent behavior
- rerun targeted pytest coverage for policy and orchestrator paths after
  touching doctrine or execution guards
- continue from the latest entry in `Session Log`

## Open Decisions

- whether regime policy should remain a code-defined global catalog or move
  into persisted configuration layered by strategy
- whether open-position management should use the same regime labels as entry
  policy or a stricter dedicated policy surface
- whether the current compatibility bridge (`TradeSignal` plus `Signal`
  aliases) is sufficient for now or should continue into a deeper persistence
  rename
- whether replay/backtest results should live as dedicated experiment entities
  or be attached to the existing journal/review tables

## Blockers

- none at the moment
- note: the working tree already contains many unrelated local changes that were
  present before this slice; do not use them as rollback targets

## Session Log

### 2026-04-18

- Reviewed the repository to explain the project structure and intent.
- Read `PLAN.md` and compared it with the current implementation.
- Confirmed that the repository already has a solid backend MVP for autonomous
  paper trading, but it does not yet model `hypotheses` and `setups` as
  first-class entities.
- Confirmed that PDCA exists but still behaves mostly like a sequential cycle,
  even when scheduled continuously.
- Created this file to preserve implementation context across interrupted
  sessions.

### 2026-04-18 07:39 UTC

- changed:
  added first-class backend entities for `Hypothesis` and `Setup`, exposed them
  through `/api/v1/hypotheses` and `/api/v1/setups`, linked them into
  `Strategy`, `Watchlist` and `WatchlistItem`, updated seed data, and added an
  Alembic migration plus API tests
- reason:
  start closing the biggest modeling gap between the current MVP and `PLAN.md`
  without breaking the existing paper-trading flow
- pending:
  `Signal` still mixes trade opportunities with signal taxonomy, and the new
  catalog is not yet propagated through the whole decision lifecycle
- next:
  design the smallest safe split or reshape of `Signal`, then attach
  `hypothesis/setup` references downstream where they materially improve
  traceability
- blockers:
  none
- verification:
  `pytest tests` passed in the backend environment after this change

### 2026-04-18 07:49 UTC

- changed:
  added `SignalDefinition` as a first-class catalog entity, exposed
  `/api/v1/signal-definitions`, seeded core signal definitions, linked
  `hypothesis/setup/signal_definition` into generated signals and positions, and
  added Alembic migration plus API tests
- reason:
  separate reusable signal taxonomy from generated trade signals while improving
  downstream traceability
- pending:
  the existing `Signal` entity is still the generated trade signal record and
  may need a clearer semantic split later
- next:
  build the event-driven layer now that catalog and traceability foundations
  exist
- blockers:
  none
- verification:
  `pytest tests` passed in the backend environment after this change

### 2026-04-18 07:53 UTC

- changed:
  added `system_events` as an explicit append-only event log, exposed
  `/api/v1/events`, and started emitting events from catalog creation plus main
  signal/position/review lifecycle operations
- reason:
  create the minimal foundation required to evolve PDCA from a sequential loop
  into an event-driven workflow
- pending:
  events are currently recorded and queryable, but they are not yet driving
  orchestrator phase execution
- next:
  decide which events should trigger PLAN / DO / CHECK / ACT work and implement
  the smallest dispatcher or pending-event queue to consume them
- blockers:
  none
- verification:
  `pytest tests` passed in the backend environment after this change

### 2026-04-18 08:31 UTC

- changed:
  extended `system_events` with dispatch state metadata, added a minimal
  event-driven dispatcher plus `POST /api/v1/events/dispatch`, wired the
  scheduler to consume pending events before falling back to the full sequential
  cycle, and added regression tests for `plan` plus `check -> act` dispatch
- reason:
  move the PDCA loop from "events are only logged" to "selected events can
  already trigger targeted orchestrator work" without removing the stable
  sequential automation path
- pending:
  only a narrow event subset is dispatched today; `trade_signal.created`,
  `position.opened` and `position.managed` are intentionally ignored to avoid
  self-triggered loops until the next policy pass is defined
- next:
  decide which additional event types deserve dispatch coverage, then revisit
  whether `Signal` should be formally renamed or split now that the dispatcher
  foundation exists
- blockers:
  none
- verification:
  targeted regression suite passed and full backend suite passed
  (`pytest tests`, 81 passed)

### 2026-04-18 08:46 UTC

- changed:
  added event emission for `screener.created`, `screener.version_created`,
  `watchlist.created` and `watchlist_item.added`, introduced source-aware
  dispatch policy so manual watchlist item additions can trigger `DO` while
  discovery-generated additions are ignored, and expanded tests around the new
  catalog and dispatch behavior
- reason:
  broaden event-driven coverage to the operational catalog without letting the
  autonomous discovery loop continuously re-trigger itself
- pending:
  `trade_signal.created`, `position.opened` and `position.managed` are still
  ignored on purpose and need an explicit orchestration policy before they can
  drive follow-up work safely
- next:
  decide whether those remaining lifecycle events should stay ignored, map to
  downstream phases, or be batched behind a more explicit work-queue policy;
  then revisit the `Signal` vs `TradeSignal` rename decision
- blockers:
  none
- verification:
  targeted regression suite passed and full backend suite passed
  (`pytest tests`, 83 passed)

### 2026-04-18 16:06 UTC

- changed:
  introduced source-aware auto-settlement for internal `DO` side effects in
  `system_events`, so orchestrator-generated `trade_signal.created`,
  `trade_signal.status_updated`, `position.opened` and `position.managed` are
  recorded as already satisfied by `DO`, while discovery-generated
  `watchlist_item.added` records are ignored on insert; propagated
  `event_source="orchestrator_do"` through internal signal/position write paths
  and extended event-dispatch regression tests
- reason:
  reduce pointless pending-event noise, avoid reprocessing internal `DO` side
  effects on later cycles, and make event policy more explicitly source-aware
  without changing the stable paper-trading flow
- pending:
  `position.closed` produced during `DO` still remains pending by design so the
  next cycle can trigger `CHECK -> ACT`; `watchlist.created` is still `PLAN`
  only; `Signal` is still the generated trade-signal record under a generic
  name
- next:
  decide whether `position.closed` created during `DO` should cascade into the
  same event-driven chain or stay deferred, then revisit `watchlist.created`
  and the `Signal` vs `TradeSignal` rename/split decision
- blockers:
  none
- verification:
  targeted regression suite passed
  (`pytest backend/tests/test_event_dispatch.py backend/tests/test_scheduler.py backend/tests/test_position_lifecycle.py backend/tests/test_learning_loop.py`, 27 passed)

### 2026-04-18 16:11 UTC

- changed:
  extended the event dispatcher so later-phase follow-up events created during a
  running chain can be claimed and appended forward in the same dispatch, which
  now allows `DO -> CHECK -> ACT` to complete in one call when `DO` emits
  `position.closed`; added a regression test for that cascade path
- reason:
  keep the PDCA flow event-driven end to end for position-lifecycle outcomes
  without waiting for a later scheduler cycle when the next required phase is
  already unambiguous and strictly later in the PDCA order
- pending:
  `watchlist.created` still maps only to `PLAN`; the `Signal` entity still
  represents generated trade signals under a generic name; backward-phase events
  created during a later phase are still deferred intentionally instead of
  re-opening an earlier phase inside the same chain
- next:
  decide whether `watchlist.created` should get a safe optional `DO` tail for
  operator-curated flows, then evaluate the scope of a `Signal` to `TradeSignal`
  rename or split
- blockers:
  none
- verification:
  targeted regression suite passed
  (`pytest backend/tests/test_event_dispatch.py backend/tests/test_scheduler.py backend/tests/test_position_lifecycle.py backend/tests/test_learning_loop.py`, 28 passed)

### 2026-04-18 16:14 UTC

- changed:
  added `initial_items` support to watchlist creation, refreshed the returned
  watchlist after inserting those items, and added regression coverage for both
  catalog persistence and event-dispatch behavior so a curated watchlist create
  now drives `PLAN -> DO` through `watchlist.created` plus
  `watchlist_item.added`
- reason:
  support a realistic operator flow where a watchlist is created with an
  initial curated batch of candidates, without changing the meaning of an empty
  `watchlist.created` event or inventing `DO` work for blank lists
- pending:
  empty `watchlist.created` remains `PLAN` only by design; the `Signal` entity
  still represents generated trade signals under a generic name; backward-phase
  follow-up events are still deferred intentionally instead of reopening earlier
  PDCA phases in the same chain
- next:
  decide the scope of the `Signal` to `TradeSignal` rename/split and whether
  that should be done first at the API/service layer or as a full persistence
  rename
- blockers:
  none
- verification:
  targeted regression suite passed
  (`pytest backend/tests/test_strategy_catalog.py backend/tests/test_event_dispatch.py backend/tests/test_scheduler.py backend/tests/test_learning_loop.py`, 30 passed)

### 2026-04-18 16:21 UTC

- changed:
  introduced `TradeSignal` as the primary name in the signal model aliases,
  market schemas, repository and API responses, exposed `/api/v1/trade-signals`
  alongside the existing `/api/v1/signals`, and updated core market/orchestrator
  write paths plus regression coverage to keep both routes compatible during the
  transition
- reason:
  reduce the conceptual mismatch between signal taxonomy (`SignalDefinition`)
  and generated trade opportunities, while keeping the current MVP stable and
  avoiding a wide persistence rename in the same slice
- pending:
  persistence-facing names like the `signals` table and `signal_id` foreign keys
  are still unchanged; `SignalService` is still the compatibility-facing service
  name; empty `watchlist.created` remains `PLAN` only
- next:
  decide whether to stop at this compatibility bridge for now or continue into
  a deeper rename of persistence-facing identifiers, starting with `signal_id`
  references in positions and decision-context snapshots
- blockers:
  none
- verification:
  targeted regression suite passed
  (`pytest backend/tests/test_strategy_catalog.py backend/tests/test_learning_loop.py backend/tests/test_event_dispatch.py backend/tests/test_scheduler.py`, 30 passed)

### 2026-04-18 16:34 UTC

- changed:
  exposed `trade_signal_id` as a first-class compatibility alias on positions,
  decision-context snapshots, orchestrator execution candidates and planner tool
  payloads, while still persisting through the existing `signal_id` column and
  foreign-key path; added regression coverage for API input/output, planner
  step generation, orchestrator candidate payloads and decision-context
  snapshots
- reason:
  keep the `TradeSignal` rename moving forward across execution-facing
  contracts without taking on an immediate persistence migration or breaking
  existing `signal_id` consumers
- pending:
  storage-level names are still unchanged (`signals` table, `signal_id` columns
  and journal/internal observation payload keys); `SignalService` remains the
  compatibility-facing service name; empty `watchlist.created` still stays in
  `PLAN`
- next:
  decide whether to stop at this contract-level aliasing or continue with a
  deeper persistence rename, starting with journal/event payload internals and
  then the database-facing identifiers
- blockers:
  none

### 2026-04-18 18:01 UTC

- changed:
  formalized the agent doctrine in code (`protocol`, playbooks, state machine
  and structured schemas), added persisted `MarketStateSnapshot` capture plus
  API/UI/chat exposure, and implemented deterministic regime-policy guardrails
  that map snapshot regimes to allowed playbooks, sizing multipliers, entry
  caps and event-risk blocks; propagated that policy into decision contexts,
  scoring, sizing, signal persistence, position entry context and journal
  observations, and added regression coverage
- reason:
  turn the snapshot and doctrine into hard execution constraints so the agent
  is governed by explicit policy before the LLM can recommend an entry
- pending:
  regime policy still governs candidate-entry flow only; open-position
  management, scheduler throttling and promotion gating for policy changes are
  not yet implemented
- next:
  extend the policy contract into position management and add replay/backtest
  validation before policy or playbook changes can be promoted
- blockers:
  none
- verification:
  `pytest backend/tests/test_ai_agent.py -q` (7 passed)
  `pytest backend/tests/test_agent_tools.py -q` (7 passed)
  `pytest backend/tests/test_bot_chat.py -q` (3 passed)
  `pytest backend/tests/test_risk_budget.py -q` (7 passed)
  `pytest backend/tests/test_learning_loop.py -q` (16 passed)

### 2026-04-18 18:13 UTC

- changed:
  added `ibkr_proxy` as the default market-data provider, with internal
  `symbol -> conid` resolution, history parsing from the proxy, live-price
  overlay from proxy snapshots, generic provider-error handling in
  `MarketDataService`, updated backend config / `.env.example`, a small chat
  readiness improvement for `stub`, and unit coverage for the new provider
- reason:
  stop depending on a public market-data integration as the primary live source
  and align the backend with the internal IBKR proxy contract that the bot
  should actually use
- pending:
  realtime `ws/market` / `sse/market` is still not wired into monitoring;
  scanner/news/calendar remain split; doctrine still needs to absorb the live
  data path in position management and replay gating
- next:
  use IBKR realtime transport for monitoring, then extend regime policy into
  position management and decide which remaining market integrations should
  migrate to the proxy
- blockers:
  none
- verification:
  `python -m py_compile backend/app/providers/market_data/base.py backend/app/providers/market_data/twelve_data_provider.py backend/app/providers/market_data/ibkr_proxy_provider.py backend/app/core/config.py backend/app/domains/market/services.py backend/app/domains/learning/services.py backend/tests/test_ibkr_proxy_provider.py backend/tests/test_bot_chat.py`
  `pytest backend/tests/test_ibkr_proxy_provider.py -q` (3 passed)
  `pytest backend/tests/test_market_analysis.py -q` (6 passed)
  `pytest backend/tests/test_bot_chat.py -q` (3 passed)
  `pytest backend/tests/test_learning_loop.py -q` (16 passed)
  live smoke against the internal proxy returned `AAPL` snapshot/history successfully

### 2026-04-18 18:51 UTC

- changed:
  added a realtime SSE client for the internal IBKR proxy, a scheduler-bound
  realtime position monitor that subscribes open positions by `conid`, event-
  driven evaluation in `ExitManagementService`, monitor runtime status in the
  scheduler payload, and regression coverage for SSE quote parsing, scheduler
  monitor lifecycle and immediate stop handling from live events
- reason:
  move `MONITOR` from periodic pull-only evaluation to a real event-driven loop
  fed by IBKR market data, while reusing the existing paper-execution engine
- pending:
  the monitor is reactive but not yet doctrine-hard: regime policy still does
  not directly govern position-management actions, and watch/alert entities are
  not yet first-class realtime subscriptions
- next:
  make position management obey regime policy and market-state doctrine, then
  expand subscriptions beyond open positions and decide which remaining market
  integrations should migrate into the proxy
- blockers:
  none
- verification:
  `python -m py_compile backend/app/providers/market_data/ibkr_realtime_client.py backend/app/domains/execution/monitoring.py backend/app/domains/execution/services.py backend/app/domains/learning/tools.py backend/app/core/config.py backend/app/domains/system/services.py backend/app/domains/system/schemas.py backend/app/domains/learning/protocol.py backend/tests/test_ibkr_realtime_client.py backend/tests/test_scheduler.py backend/tests/test_autonomous_position_management.py`
  `pytest backend/tests/test_ibkr_realtime_client.py -q` (2 passed)
  `pytest backend/tests/test_scheduler.py -q` (4 passed)
  `pytest backend/tests/test_autonomous_position_management.py -q` (4 passed)
  `pytest backend/tests/test_learning_loop.py -q` (16 passed)
  live smoke against the internal proxy SSE returned expected `system` events for `AAPL` (`conid 265598`)

### 2026-04-18 19:10 UTC

- changed:
  replaced the static-only discovery universe with an IBKR-scanner-backed
  dynamic universe resolver, added scanner config knobs and fallback handling,
  exposed the chosen universe source in discovery metrics, updated `.env`
  defaults, and added provider/discovery tests
- reason:
  remove the operational bottleneck where autonomous discovery could only scan
  a manually maintained ticker list even though the internal IBKR proxy already
  exposes scanner endpoints
- pending:
  the new dynamic universe improves candidate breadth, but it still feeds the
  existing watchlist-first discovery loop rather than a full screener-to-signal
  pipeline with dedicated alert entities
- next:
  resume the doctrinal position-management work so the realtime `MONITOR` loop
  becomes policy-governed, now that scanner discovery is also on the internal
  IBKR layer
- blockers:
  none
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/providers/market_data/ibkr_proxy_provider.py backend/app/domains/market/discovery.py backend/tests/test_ibkr_proxy_provider.py backend/tests/test_opportunity_discovery.py`
  `pytest backend/tests/test_ibkr_proxy_provider.py -q` (4 passed)
  `pytest backend/tests/test_opportunity_discovery.py -q` (2 passed)
  `pytest backend/tests/test_learning_loop.py -q` (16 passed)

### 2026-04-18 19:30 UTC

- changed:
  bounded strategy-evolution hypothesis generation so candidate variants keep a
  stable base hypothesis plus concise lineage note, added a success-pattern
  guard that refuses to regenerate variants without new qualifying trades,
  added `StrategyMaintenanceService` plus
  `backend/scripts/compact_strategy_hypotheses.py`, and compacted the existing
  SQLite strategy-history payload in place
- reason:
  stop recursive `strategy_versions` text growth from re-inflating the
  database, and recover space already consumed by historical success-pattern
  variants
- pending:
  the monitor doctrine work is still next on the critical path; strategy-history
  compaction is now available, but the broader retention policy for journals,
  memory and scorecards has not yet been formalized
- next:
  resume doctrinal position-management policy in `execution/services.py`, then
  decide whether additional historical tables need bounded retention similar to
  `strategy_versions`
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/strategy/services.py backend/scripts/compact_strategy_hypotheses.py backend/tests/test_strategy_history_compaction.py`
  `pytest backend/tests/test_strategy_history_compaction.py -q` (3 passed)
  `pytest backend/tests/test_learning_loop.py -q` (16 passed)
  dry run on `backend/trading_research.db` estimated ~11.8 MB of hypothesis-text savings
  applied compaction on `backend/trading_research.db`, backed up the pre-compaction DB to `/tmp/trading_research.db.pre_compaction.20260418_192202.bak`, and reduced the file from 17.29 MiB to 5.58 MiB after `VACUUM`

### 2026-04-18 19:35 UTC

- changed:
  added bounded retention rules for noisy `journal_entries` and `memory_items`,
  wired auto-pruning into `JournalService` and `MemoryService`, added
  `LearningHistoryMaintenanceService` plus
  `backend/scripts/prune_learning_history.py`, added retention coverage, and
  applied the pruning to the working SQLite database
- reason:
  stop the remaining high-churn learning history tables from re-inflating the
  local SQLite file after strategy-history compaction
- pending:
  the critical path is still doctrinal position management for the realtime
  monitor; retention is now bounded for the noisiest learning tables, but other
  historical tables may still need explicit lifecycle rules later
- next:
  resume regime-policy enforcement in `execution/services.py`, then profile the
  remaining largest tables again after more live runtime to see whether any
  other retention policies are justified
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/learning/repositories.py backend/app/domains/learning/services.py backend/scripts/prune_learning_history.py`
  `pytest backend/tests/test_learning_history_retention.py -q` (3 passed)
  `pytest backend/tests/test_learning_loop.py -q` (16 passed)
  applied pruning on `backend/trading_research.db`, backed up the pre-prune DB to `/tmp/trading_research.db.pre_history_prune.20260418_192839.bak`, deleted 2,367 rows, and reduced the file from 5.58 MiB to 4.36 MiB after `VACUUM`
  post-prune table sizes on `backend/trading_research.db`: `journal_entries` 0.85 MiB, `memory_items` 0.43 MiB, `strategy_versions` 1.13 MiB
  post-prune dry run on `backend/trading_research.db` returned `deleted_total = 0`

### 2026-04-18 19:45 UTC

- changed:
  made startup seeding explicitly one-shot for brand-new databases by adding a
  `SeedService.should_seed_on_startup` guard, routing `main` through a small
  `maybe_seed_on_startup` helper, enabling `BOOTSTRAP_SEED_ON_STARTUP` in
  `.env.example`, and adding bootstrap tests for empty-vs-initialized startup
- reason:
  production should be able to keep startup seeding enabled for first
  initialization without risking repeated catalog hydration on subsequent
  restarts
- pending:
  the production reset itself is still an operational step; code now supports a
  clean first boot safely, but the realtime monitor doctrine work remains the
  main product gap
- next:
  when the current SQLite is retired, start the app against a fresh database
  and let the first boot run migrations plus one-shot seed automatically
- blockers:
  none
- verification:
  `python -m py_compile backend/app/main.py backend/app/domains/system/services.py backend/tests/test_bootstrap.py`
  `pytest backend/tests/test_bootstrap.py -q` (2 passed)
  `pytest backend/tests/test_startup_migrations.py -q` (1 passed)

### 2026-04-18 19:50 UTC

- changed:
  archived the old workspace SQLite database, rebuilt `backend/trading_research.db`
  from scratch with migrations plus the new one-shot startup seed, and left the
  runtime on a clean baseline with only catalog/bootstrap data
- reason:
  retire all accumulated test trades, journals, memories and experimental
  runtime history before production handoff while preserving a recoverable copy
  of the prior local database
- pending:
  the data reset is complete; the remaining product-critical work is still the
  doctrinal position-management policy for the realtime monitor
- next:
  continue from the monitor-doctrine implementation on top of the fresh DB, and
  only generate new runtime history from intentional validation or prod-like
  paper execution
- blockers:
  none
- verification:
  backed up the pre-reset DB to `/tmp/trading_research.db.pre_prod_reset.20260418_194232.bak`
  rebuilt `backend/trading_research.db` to 0.56 MiB with counts:
  `hypotheses=3`, `setups=3`, `signal_definitions=3`, `strategies=3`,
  `screeners=2`, `watchlists=2`, `watchlist_items=6`,
  `positions=0`, `signals=0`, `journal_entries=0`, `memory_items=0`,
  `market_state_snapshots=0`, `trade_reviews=0`
  reran `maybe_seed_on_startup(Settings())` on the rebuilt DB and it returned
  `null`, confirming the seed will not rerun on subsequent startups

### 2026-04-18 20:05 UTC

- changed:
  hardened AI decision execution so candidate analysis and open-position
  management degrade locally when AI providers fail, added provider-chain
  cooldown and configurable AI request timeout/cooldown settings, exposed AI
  cooldown in scheduler status, and added regression coverage for AI-unavailable
  `DO` and heuristic fallback in autonomous exit management
- reason:
  the live runtime paused the whole bot after a Gemini `429` plus fallback
  timeout; transient provider issues should not stop deterministic paper
  execution or heuristic risk management
- pending:
  the realtime monitor still needs doctrine-hard position-management policy, but
  AI-provider instability no longer needs to be treated as a fatal scheduler
  incident for the main execution paths
- next:
  restart the backend with this patch, resume the bot, and observe whether the
  runtime now keeps cycling with deterministic/heuristic degradation under AI
  cooldown instead of pausing
- blockers:
  none
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/agent.py backend/app/domains/learning/services.py backend/app/domains/execution/services.py backend/app/domains/system/schemas.py backend/tests/test_learning_loop.py backend/tests/test_autonomous_position_management.py`
  `pytest backend/tests/test_autonomous_position_management.py -q` (5 passed)
  `pytest backend/tests/test_learning_loop.py -q` (17 passed)

### 2026-04-18 20:12 UTC

- changed:
  made `SchedulerService.start_bot()` non-blocking by scheduling the first
  autonomous cycle immediately in the APScheduler background worker instead of
  running it inline inside the HTTP request, and updated scheduler tests
- reason:
  after enabling graceful AI degradation, the `/scheduler/start` endpoint still
  blocked the whole server while the first `DO` cycle consumed external calls;
  operational observability requires the server to stay responsive while the bot
  is working
- pending:
  the runtime now stays observable, but `DO` can still take a long time because
  market/AI work is serial and blocking; doctrinal monitor hardening is still
  the main functional gap
- next:
  observe live cycles through the UI and status endpoints, then decide whether
  the next bottleneck to attack is `DO` latency, AI provider reliability, or
  position-management doctrine
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/system/services.py backend/tests/test_scheduler.py`
  `pytest backend/tests/test_scheduler.py -q` (4 passed)
  restarted `uvicorn`, confirmed `/api/v1/health` responds, and confirmed
  `POST /api/v1/scheduler/start` now returns in ~0.03s while the bot keeps
  cycling in background

### 2026-04-18 20:18 UTC

- changed:
  extended the AI provider chain so Gemini now tries `GEMINI_API_KEY`, then
  `GEMINI_API_KEY_FREE1`, then `GEMINI_API_KEY_FREE2`, and only after all three
  fail does it fall back to `qwen2.5`; updated config/env example and test
  isolation for the new env vars, and added agent tests for slot ordering and
  secondary-key readiness
- reason:
  the shared Ollama fallback is still a bottleneck under heavy prompts, so the
  bot should exhaust the available free Gemini keys before sending work to Qwen
- pending:
  the backend still needs a restart to pick up the new env vars in the live
  runtime; after restart we need to observe whether `ai_overlay` starts landing
  from Gemini more often and whether Qwen usage drops
- next:
  restart `uvicorn`, recheck scheduler status, and observe live signals to
  confirm the Gemini slot chain is being used before the Qwen fallback
- blockers:
  none
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/agent.py backend/tests/conftest.py backend/tests/test_ai_agent.py`
  `pytest backend/tests/test_ai_agent.py -q` (10 passed)
  `pytest backend/tests/test_scheduler.py -q` (4 passed)

## Template For Future Entries

Use this format for the next updates:

```md
### YYYY-MM-DD HH:MM UTC

- changed:
- reason:
- pending:
- next:
- blockers:
```
