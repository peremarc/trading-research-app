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
- For frontend-only implementation work, record follow-up in
  `UI_IMPLEMENTATION_JOURNAL.md` and keep this journal focused on backend +
  architecture-level changes.

## Current Focus

Run the doctrine-driven paper-trading stack on the internal IBKR proxy with a
live `MONITOR` loop, while continuing to turn the agent protocol into
enforceable execution policy.

Current gap under review:

- keeping the runtime workspace on a clean post-reset SQLite baseline while
  doctrine and monitor hardening continue toward production handoff
- keeping the bot productive outside regular US market hours by allowing
  closed-market research/discovery/AI review while still forbidding new paper
  entries until the next regular session
- keeping the bot productive when all active watchlist items are waiting on
  explicit triggers by opening bounded fresh-ticker scouting tasks instead of
  looping on the same names
- adding a dedicated macro/geopolitical research lane that turns macro
  calendar events, geopolitical headlines and external articles into persisted
  market theses, linked thematic watchlists and actionable strategy research
  tasks
- making `discovery` context-aware so new tickers are ranked not only by
  technical structure but also by fresh news, near corporate events and active
  macro-theme alignment
- routing corporate earnings context through the internal `IBKR proxy`
  `/corporate-events/next` endpoint first, with the local `Alpha Vantage`
  batch cache kept only as fallback when the proxy path is unavailable, and
  surfacing source/fallback/cache state in the operator UI
- removing the false global `PLAN -> DO -> CHECK -> ACT` completion semantics
  from the scheduler so periodic automation acts as an operational `DO` loop
  while `CHECK/ACT` advance only from evidence-bearing events
- surfacing per-ticker reanalysis status and wake-up reasons in the operator UI
  so deferred `WATCH` items are distinguishable from genuine event-driven
  wakeups
- keeping autonomous execution resilient to transient AI-provider failures so
  provider `429`s or timeouts degrade to deterministic behavior instead of
  pausing the whole bot
- making the new IBKR-driven `MONITOR` loop doctrine-aware so position
  management uses explicit regime policy instead of only heuristics plus agent
  advice
- turning sector-aware intermarket context into a first-class daily decision
  input, including new IBKR proxy options-sentiment endpoints, while keeping
  intraday-only concepts (`VWAP`, sweeps, gap fades) out of the current
  daily-bar stack
- deciding how validated `skills` revisions should influence the agent/runtime
  beyond backend metadata now that catalog, routing, operator visibility and a
  paper/replay validation gate already exist
- turning the new curated `KnowledgeClaim / KnowledgeClaimEvidence` layer from
  simple persisted storage into durable knowledge the agent can actually use,
  with contradictions, freshness review and controlled runtime retrieval
- turning the current `skills + claims` foundation into explicit learning
  workflows, especially deeper operator drill-down and cross-surface
  visibility now that richer resolution classes, history and a scheduled
  governance lane exist for `stale_claim_review` and `weekly_skill_audit`
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
- reducing per-ticker runtime latency by removing duplicated market/calendar
  fetches, preserving hot caches across scheduler cycles and now skipping
  watchlist items until a persisted `next_reanalysis_at` is due so the bot
  stops polling the same names every cycle while bounded read-side
  parallelism and request coalescing are rolled out inside the shared
  market/news/calendar services before any heavier provider-specific
  backpressure pass

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
- lightweight procedural `skills` layer in code with catalog, deterministic
  router, persisted `skill_context` through candidate/review/management flows,
  catalog API exposure and promotion of reviewed lessons into
  `skill_candidate` memory/journal artifacts
- simple frontend console for runtime, pipeline, positions, journal, chat and
  market-state visibility
- closed-market research policy now supports watchlist analysis, opportunity
  discovery and AI review while the market is closed, while downgrading any
  would-be `paper_enter` decisions to `watch` until the next regular session
- idle `DO` cycles can now open bounded `market_scouting` research tasks from
  fresh scanner/configured-universe names when the active watchlist is fully
  deferred and discovery produced nothing new
- `DO` now also has a dedicated macro/geopolitical research lane that reviews
  curated themes, combines macro calendar events with headlines and web
  articles, synthesizes impact hypotheses through AI with heuristic fallback,
  persists `macro_signal` memory, and opens deduplicated
  `macro_strategy_research` tasks
- the operator console now surfaces that macro/geopolitical lane explicitly and
  shows the thematic watchlists it creates, so macro research is no longer
  hidden only in journal or memory tables
- the autonomous scheduler now treats its cadence as an operational scan loop:
  without pending system events it runs `DO` only, then immediately dispatches
  any evidence-bearing follow-up events generated by that `DO` pass

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
- the skills layer is now routed, persisted, inspectable and promotable via a
  bounded paper/replay gate, and validated revisions now load on demand into
  the agent runtime as compact procedural packets; the surrounding memory
  layer now also supports bounded runtime claim retrieval plus explicit
  contradiction/freshness review, plus a scheduled governance lane that keeps
  stale-claim and weekly-skill workflows fresh without dashboard-triggered
  syncs; workflow history is now typed and surfaced in the operator dashboard,
  journal feed and ticker trace when an affected ticker exists, and the UI now
  supports linked detail drill-down for workflow/claim/gap/candidate entities,
  but it still lacks richer drawers/detail screens and more opinionated
  dismissal/review taxonomies
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
- `backend/app/domains/learning/claims.py`
- `backend/app/domains/learning/planning.py`
- `backend/app/domains/learning/world_state.py`
- `backend/app/domains/learning/decisioning.py`
- `backend/app/domains/learning/repositories.py`
- `backend/app/domains/learning/services.py`
- `backend/app/db/models/knowledge_claim.py`
- `backend/app/db/models/watchlist.py`
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
- `backend/tests/test_knowledge_claims.py`
- `backend/tests/test_autonomous_position_management.py`
- `backend/tests/test_scheduler.py`
- `backend/tests/test_relevance_engine.py`
- `backend/tests/test_ibkr_proxy_provider.py`
- `backend/tests/test_risk_budget.py`
- `backend/tests/test_learning_loop.py`
- `backend/tests/test_bot_chat.py`
- `backend/tests/test_bootstrap.py`

## Next Step

Use the new OpenClaw/Hermes-style `skills + claims + workflows` base to
implement the next real learning-loop slice:

1. enrich workflow resolution semantics beyond `open/in_progress/resolved`,
   including more opinionated dismissal/review taxonomies and clearer
   cross-links between workflow history, journal entries and the affected
   claims/gaps/candidates
2. surface the scheduled governance lane more deeply in operator views
   (richer workflow detail, journal drill-down, eventual drawers from
   trace/feed into affected entities) so the cadence and outcomes are visible
   outside the main dashboard cards
3. keep runtime risk/execution behavior unchanged while workflow governance and
   auditability are hardened

## Resume Checklist

Before continuing implementation:

- read this file
- read `PLAN.md`
- inspect pending git changes
- review `config`, `market/services` and `providers/market_data` before
  changing data-source behavior
- review `market/discovery`, `market/services` and `learning/services` before
  changing the idle research/scouting lane
- review `providers/calendar` and `market/services` before changing corporate
  calendar sourcing, cache TTL or earnings-event filtering behavior
- review `db/models/watchlist.py`, `learning/services` and
  `tests/test_learning_loop.py` before changing persisted reanalysis runtime,
  `key_metrics`, or due-queue scheduling behavior
- review `providers/market_data/ibkr_proxy_provider.py`, `market/services` and
  `learning/decisioning` before changing options sentiment, sector
  intermarket logic, or any new proxy-derived decision feature
- review `system/events`, `system/services` and `learning/services` before
  changing PDCA dispatch or scheduler semantics
- review `learning/decisioning`, `learning/tools` and `market/services`
  together before changing cache sharing, per-ticker context loading or
  scheduler/runtime performance behavior
- review `learning/claims.py`, `db/models/knowledge_claim.py`,
  `execution/services.py` and `learning/relevance.py` together before changing
  curated knowledge promotion, evidence rollups, or rule-to-claim propagation
- review `learning/decisioning.py` and `tests/test_intermarket_context.py`
  before changing bounded I/O parallelism, calendar/news loading order, or
  decision-context timing semantics
- review `market/services.py`, `tests/test_market_analysis.py`,
  `tests/test_news.py` and `tests/test_calendar.py` before changing cache
  locking, in-flight request coalescing, or provider cooldown behavior
- review `frontend/app.js` and signal/watchlist payloads before changing
  operator-facing reanalysis visibility
- review `execution/monitoring` and `execution/services` before changing
  runtime monitoring or position-management behavior
- review `protocol`, `world_state`, `decisioning` and `services` before
  changing agent behavior
- review `learning/tools.py`, `learning/relevance.py` and
  `execution/services.py` before introducing any new skill-routing or
  lesson-promotion path
- rerun targeted pytest coverage for policy and orchestrator paths after
  touching doctrine or execution guards
- continue from the latest entry in `Session Log`

## Current Focus (Frontend Session)

Frontend implementation tracking moved to
[`UI_IMPLEMENTATION_JOURNAL.md`](/workspaces/trading-research-app/UI_IMPLEMENTATION_JOURNAL.md)
to avoid mixing backend-architecture changes with UI iteration.

Backend work continues under this file's existing focus items.

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

### 2026-04-18 22:15 UTC

- changed:
  introduced an explicit event-driven reanalysis contract for watchlist items.
  `DO` no longer reanalyzes every active ticker on every loop. Each generated
  signal now persists a `reanalysis_policy` with concrete triggers
  (regime shift, significant price move, stop/target breach, technical-state
  change, fresh news, and earnings-window transition), and subsequent `DO`
  passes only run full analysis when one of those triggers fires. In parallel,
  the scheduler now slows down automatically while the regular US market is
  closed, discovery is suppressed by default outside session, and AI overlays
  are suppressed outside session unless explicitly enabled. Open-position AI
  management is also skipped while the market is closed unless a realtime event
  arrives.
- reason:
  the autonomous bot was burning tokens by revisiting the same watchlist names
  every few minutes even when nothing material had changed, which is especially
  wasteful on weekends and inconsistent with a swing-trading workflow
- pending:
  news and calendar can already trigger reanalysis opportunistically through the
  current providers, but the cleaner next step is to promote them to first-class
  system events so `WATCH` items can wake up directly from event ingestion
- next:
  restart the live backend so the runtime picks up the new event-driven
  reanalysis policy, then observe that unchanged watchlist items stop consuming
  AI calls across closed-market cycles
- blockers:
  none
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/system/market_hours.py backend/app/domains/system/services.py backend/app/domains/learning/services.py backend/app/domains/execution/services.py backend/tests/conftest.py backend/tests/test_learning_loop.py backend/tests/test_scheduler.py backend/tests/test_autonomous_position_management.py`
  `pytest backend/tests/test_learning_loop.py -q` (19 passed)
  `pytest backend/tests/test_scheduler.py -q` (5 passed)
  `pytest backend/tests/test_autonomous_position_management.py -q` (6 passed)

### 2026-04-19 06:45 UTC

- changed:
  enabled closed-market research by default in config/env, added an explicit
  `paper_entry_when_market_closed` guard so weekend/after-hours research can
  continue without opening paper positions, persisted the new execution-policy
  traceability on generated signals/journal context, and added regression
  coverage for closed-market analysis that downgrades `paper_enter` to `watch`
- reason:
  the previous runtime treated a closed market as a reason to stop researching,
  which left the bot mostly idle during the exact window that should be used
  for ticker research, scenario analysis and watchlist refresh
- pending:
  the live backend now has the right policy, but the broader research side is
  still candidate/watchlist-centric; strategy/hypothesis generation remains a
  separate future expansion
- next:
  observe a few closed-market cycles in the live runtime, confirm the new
  `market_closed_execution_policy` traces appear when a candidate would
  otherwise enter, and then return to monitor doctrine hardening
- blockers:
  none
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/services.py backend/tests/test_learning_loop.py`
  `pytest backend/tests/test_learning_loop.py -q -k "closed_market or reanalysis"` (2 passed)
  `pytest backend/tests/test_scheduler.py -q` (5 passed)

### 2026-04-19 07:05 UTC

- changed:
  fixed the event-driven reanalysis baseline for fused ticker analysis. The
  persisted `reanalysis_policy.technical_state` now uses a live market snapshot
  instead of the partial fused `quant_summary`, so the next `DO` cycle no
  longer treats unchanged watchlist items as fresh
  `technical_state_changed` events. Added a regression test proving that the
  second `DO` pass defers unchanged items instead of analyzing them again.
- reason:
  the live runtime was still revisiting the same watchlist names because the
  previous baseline stored `false/null` technical flags, which made stable
  tickers look like fresh technical events every few minutes
- pending:
  surface each watchlist item's reanalysis status and last wake-up reason in
  the frontend so operators can distinguish
  `awaiting_reanalysis_trigger`, `fresh_news`,
  `technical_state_changed`, `calendar_window_transition` and other valid
  event-driven wakeups
- next:
  keep monitor-doctrine hardening as the primary implementation track, then add
  this trigger visibility to the frontend console so idle cycles are visibly
  distinguishable from real event-driven reanalysis
- blockers:
  none
- verification:
  `pytest backend/tests/test_learning_loop.py -q -k reanalysis_policy_uses_live_snapshot_for_technical_state` (1 passed)
  live runtime: cycle `2026-04-19 06:55:58 UTC` generated 10 analyses to
  refresh the invalid baseline; cycle `2026-04-19 07:02:02 UTC` then generated
  0 analyses and deferred 10 entries awaiting explicit reanalysis triggers

### 2026-04-19 07:27 UTC

- changed:
  added an idle research fallback to `DO`. When all active watchlist items are
  deferred waiting for explicit reanalysis triggers and discovery adds nothing
  new, the orchestrator now scans a bounded fresh-ticker universe, scores a
  small batch without AI, enriches it with news/calendar context, and opens
  deduplicated `market_scouting` research tasks instead of reprocessing the
  same watchlist names. Added explicit config knobs for the lane, a public
  discovery-universe accessor, a new `ResearchService.ensure_market_scouting_task`
  helper, regression coverage, and test-date fixes for near-term calendar stubs.
- reason:
  after the event-driven reanalysis fix, the bot correctly stopped reanalyzing
  unchanged names, but that also made fully deferred cycles look idle even
  though they were the right moment to scout fresh tickers and scenarios
- pending:
  the idle research lane now creates useful work, but the frontend still does
  not distinguish that activity clearly from an otherwise quiet cycle; operator
  visibility remains the next missing piece
- next:
  keep monitor-doctrine hardening on the main path, then expose
  `idle_research_tasks_opened`, `idle_research_focus_tickers` and per-ticker
  wake-up reasons in the frontend console
- blockers:
  none
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/market/discovery.py backend/app/domains/market/services.py backend/app/domains/learning/services.py backend/tests/test_learning_loop.py backend/tests/test_agent_planner.py`
  `pytest backend/tests/test_learning_loop.py -q` (22 passed)
  `pytest backend/tests/test_scheduler.py -q` (5 passed)
  `pytest backend/tests/test_agent_planner.py -q` (10 passed)
  live runtime after backend restart: cycle `2026-04-19 07:26:42 UTC`
  generated `0` analyses, deferred `10` reanalysis entries, reviewed `6`
  fresh tickers, and opened `2` `market_scouting` tasks (`BYND`, `BMNU`)

### 2026-04-19 07:40 UTC

- changed:
  removed the scheduler's implicit global `plan -> do -> check -> act`
  completion path. The periodic automation loop now dispatches pending
  event-driven phases first; if no event requires work, it runs only `DO` as
  the ongoing operational scan loop, then immediately dispatches any
  evidence-bearing follow-up events produced by that `DO` pass (for example a
  `position.closed` generated during `DO` can still advance into `CHECK/ACT`
  inside the same automation run).
- reason:
  a cadence-driven global PDCA completion was conceptually wrong for this
  system. A hypothesis or position cannot finish `CHECK/ACT` without evidence,
  and many PDCA threads may be active in parallel. The scheduler should not
  claim that the bot completed `CHECK/ACT` for the whole system just because a
  timer fired.
- pending:
  runtime semantics are now aligned at the scheduler layer, but the persistence
  model still exposes PDCA mostly as global phase history rather than explicit
  per-subject cycles linked to hypotheses, signals, positions and research
  tasks
- next:
  restart the live backend so the runtime stops reporting false global
  `last_successful_phase=act` completions on timer ticks, then add explicit
  per-subject PDCA visibility in the API/UI
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/system/services.py backend/tests/test_scheduler.py backend/tests/test_event_dispatch.py`
  `pytest backend/tests/test_scheduler.py -q` (7 passed)
  `pytest backend/tests/test_event_dispatch.py -q` (7 passed)

### 2026-04-19 08:25 UTC

- changed:
  added a dedicated macro/geopolitical research lane to `DO`. The orchestrator
  now reviews curated macro themes, pulls matching macro calendar events plus
  news and web evidence, synthesizes impact and strategy ideas through the AI
  agent with heuristic fallback, persists deduplicated `auto_macro:*`
  `macro_signal` records, and opens bounded `macro_strategy_research` tasks.
  Added config knobs for the lane, a new
  `ResearchService.ensure_macro_strategy_task` helper, and regression coverage
  for signal/task creation and no-change deduplication.
- reason:
  the bot needed a separate research branch for macroeconomics and geopolitics
  so it can keep building cross-asset theses and exploitable scenario maps
  outside the narrow per-ticker entry loop.
- pending:
  the new lane is visible in persisted macro context, journal, and research
  tasks, but it is not yet surfaced clearly in the frontend or wired directly
  into watchlist promotion and strategy-selection flows.
- next:
  expose macro-research metrics, themes, and focus assets in the frontend, then
  let validated macro themes propose watchlist additions or strategy tasks for
  specific assets automatically.
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/learning/services.py backend/app/domains/learning/agent.py backend/app/domains/market/services.py backend/tests/test_learning_loop.py`
  `pytest backend/tests/test_learning_loop.py -q` (24 passed)
  `pytest backend/tests/test_scheduler.py -q` (7 passed)

### 2026-04-19 09:05 UTC

- changed:
  connected the new macro/geopolitical research lane to real watchlist
  expansion and the operator console. Each new macro thesis now creates or
  refreshes a thematic `macro_*` watchlist with its focus assets, links that
  watchlist back into the persisted macro evidence and `macro_strategy_research`
  task scope, and exposes both the latest macro lane metrics and the thematic
  watchlists in the frontend.
- reason:
  macro research was already being recorded, but it still looked invisible in
  the UI and it was not yet feeding the practical next step of the trading
  loop: a concrete watchlist the bot can analyze in later `DO` cycles.
- pending:
  the macro lane now generates visible thematic watchlists, but it still does
  not automatically promote the strongest themes into first-class
  `hypothesis/setup/strategy` candidates.
- next:
  define the promotion rule from `macro_*` watchlists into explicit hypothesis
  or strategy-candidate creation so validated macro themes can enter the
  broader strategy lab instead of staying only as watchlists plus research.
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/learning/services.py backend/app/domains/market/services.py backend/app/domains/learning/agent.py`
  `node --check backend/app/frontend/app.js`
  `pytest backend/tests/test_learning_loop.py -q` (24 passed)
  `pytest backend/tests/test_scheduler.py -q` (7 passed)
  `pytest backend/tests/test_ai_agent.py -q` (10 passed)

### 2026-04-19 09:40 UTC

- changed:
  replaced the old per-ticker `Finnhub` corporate-calendar dependency with an
  `Alpha Vantage` earnings-calendar path that fetches a single upcoming batch,
  persists it to a local cache file, and serves later ticker lookups by local
  filtering instead of new provider calls. `macro` calendar behavior stays on
  `Finnhub` when configured. Added regression coverage for batch filtering,
  provider-call deduplication and stale-cache fallback on provider errors, and
  isolated tests from local calendar credentials/cache state.
- reason:
  the user added `ALPHA_VANTAGE_API_KEY`, but the free tier is too small for
  live per-ticker lookups inside the bot loop. A daily batch cache keeps
  earnings context usable while staying inside quota constraints.
- pending:
  the new corporate-calendar cache is now operational, but its freshness is not
  yet surfaced in the UI or operator APIs, and the bot does not yet expose when
  an earnings-context decision came from cached vs freshly refreshed data.
- next:
  surface earnings-cache freshness in the operator UI/API, then thread cached
  earnings provenance into candidate reasoning so operators can see when a
  calendar-driven filter used stale fallback data.
- blockers:
  none
- verification:
  `pytest backend/tests/test_calendar.py -q` (4 passed)
  `pytest backend/tests/test_learning_loop.py -q` (24 passed)
  `pytest backend/tests/test_scheduler.py -q` (7 passed)

### 2026-04-19 09:50 UTC

- changed:
  switched ticker-level corporate-event lookup to prefer the new internal
  `IBKR proxy` `/corporate-events/next` endpoint, translating its
  `next/upcoming/recent` payload into normalized `CalendarEvent` entries for
  earnings, earnings calls, analyst meetings and miscellaneous corporate
  events. Kept the `Alpha Vantage` batch cache as fallback only when the proxy
  path errors, returns no usable in-window events, or is not configured, and
  added regression coverage for proxy payload parsing, provider precedence and
  proxy-to-fallback failover.
- reason:
  the proxy now exposes a dedicated normalized corporate-events endpoint, so it
  is a better primary source than consuming free-tier `Alpha Vantage` quota for
  every ticker that needs corporate-event context.
- pending:
  operator-facing payloads still do not expose which source produced a given
  corporate-event context (`ibkr_proxy` vs `alpha_vantage` fallback), and the
  UI still cannot show freshness or fallback state for that evidence.
- next:
  thread corporate-event source/freshness through the API and operator UI so
  users can see whether a decision used live proxy evidence or cached fallback
  data.
- blockers:
  none
- verification:
  `pytest backend/tests/test_calendar.py -q` (8 passed)
  `pytest backend/tests/test_learning_loop.py -q` (24 passed)
  `pytest backend/tests/test_scheduler.py -q` (7 passed)

### 2026-04-19 09:55 UTC

- changed:
  added a dedicated `calendar/corporate-context/{ticker}` API endpoint that
  returns normalized corporate events plus source selection metadata
  (`ibkr_proxy` vs `alpha_vantage`), fallback usage, fallback reason and cache
  freshness. The frontend now fetches that context for a bounded set of
  runtime-relevant tickers and renders a `Corporate Event Context` panel so the
  operator can see which names are running on live proxy data and which ones
  are falling back to cached `Alpha Vantage` earnings data.
- reason:
  after switching the primary corporate-event source to the internal `IBKR`
  proxy, the console still did not make the evidence path visible, so there was
  no operator feedback about whether a ticker used live proxy context or cached
  fallback data.
- pending:
  the operator UI now shows source/fallback/cache status live, but the same
  provenance is not yet threaded into persisted signal and position context for
  later forensic review in journal or trade review flows.
- next:
  persist corporate-event provenance inside decision/signal/position context so
  historical reviews can explain whether a trade thesis was built on proxy
  events or fallback cache data.
- blockers:
  none
- verification:
  `pytest backend/tests/test_calendar.py -q` (8 passed)
  `pytest backend/tests/test_learning_loop.py -q` (24 passed)
  `node --check backend/app/frontend/app.js`
  live runtime:
  `/api/v1/calendar/corporate-context/AAPL?days_ahead=45` -> `source=ibkr_proxy`
  `/api/v1/calendar/corporate-context/TSLA?days_ahead=45` -> `source=alpha_vantage`, `used_fallback=true`

### 2026-04-19 10:00 UTC

- changed:
  upgraded `OpportunityDiscoveryService` so discovery ranking now stays
  technically anchored but adds bounded contextual enrichment from ticker news,
  upcoming corporate events and active macro-theme tracked tickers. Discovery
  first builds a short technical preselection, then applies small contextual
  bonuses and stores the resulting reasoning in watchlist item `key_metrics`
  (`base_combined_score`, `discovery_score`, `news_titles`,
  `calendar_events`, `macro_tracked`, `contextual_bonus`,
  `contextual_reasons`).
- reason:
  discovery previously selected names using only the fused technical score, so
  a ticker with a live catalyst or explicit macro-theme alignment was treated
  the same as a technically similar name with no immediate context.
- pending:
  the new contextual ranking is already persisted on watchlist items, but the
  operator console still does not surface those discovery-specific boosts when
  reviewing automatically added names.
- next:
  expose discovery scoring traceability in the UI and any watchlist inspector
  so operators can see why one auto-added ticker outranked another.
- blockers:
  none
- verification:
  `python -m py_compile backend/app/domains/market/discovery.py backend/tests/test_opportunity_discovery.py`
  `pytest backend/tests/test_opportunity_discovery.py -q` (4 passed)
  `pytest backend/tests/test_learning_loop.py -q` (24 passed)

### 2026-04-19 10:24 UTC

- changed:
  created a dedicated frontend workspace under `frontend/` with React + TypeScript
  + Vite + Tailwind, including a multi-panel UI for:
  `hypotheses`, `strategies`, `setups`, `signals`, `screeners`,
  `watchlists`, `positions`, `journal`, `pdca_reviews`, `proposed_improvements`.
  Added a generic API client that consumes existing backend paths and marks
  missing endpoints and missing response fields with explicit panel notices.
  Added `npm run build:backend` to generate assets into `backend/app/frontend`
  without modifying backend runtime code, and documented deployment steps in
  `frontend/README.md`.
- reason:
  the user requested a modern UI on a separate frontend ownership track so backend
  and frontend can advance in parallel.
- pending:
  optional API-field enrichments if needed by the UI:
  confirm whether panel fields should be standardized across all entities
  (`hypothesis`, `setup`, `strategy`, `signal`, `journal`, `pdca` schemas) and
  whether the new `proposed_improvements` endpoint should be explicit instead of
  aggregating from existing sources.
- next:
  continue frontend refinement in `frontend/`, add focused field-format
  normalization per panel, then request any required backend field additions in
  the other Codex session.
- blockers:
  none.
- verification:
  `npm run dev -- --host 0.0.0.0 --port 5173` serves the UI.
  `npm run build` and `npm run build:backend` completed after adding
  `frontend/src/vite-env.d.ts`.

### 2026-04-19 10:30 UTC

- changed:
  moved frontend implementation tracking to a dedicated journal file:
  `UI_IMPLEMENTATION_JOURNAL.md`.
- reason:
  keep frontend progress and backend architecture work clearly separated for faster
  context recovery across parallel sessions.
- pending:
  continue recording future UI work in `UI_IMPLEMENTATION_JOURNAL.md`; no changes
  in backend architecture were required for this split.
- next:
  when resuming frontend tasks, open the dedicated UI journal first.
- blockers:
  none.

### 2026-04-19 10:32 UTC

- changed:
  recorded as an explicit pending task that the operator UI must surface
  `discovery` scoring traceability for automatically added tickers, including
  `base_combined_score`, contextual boosts from news/calendar/macro alignment,
  and the final `discovery_score`.
- reason:
  `discovery` now ranks candidates with contextual enrichment, so operators need
  a visible explanation of why one new ticker was selected over another.
- pending:
  implement that traceability in the UI and any watchlist inspector so the
  discovery reasoning is inspectable without reading raw `key_metrics`.
- next:
  expose discovery scoring breakdown in the frontend and bind it to watchlist
  items created by `opportunity_discovery`.
- blockers:
  none.

### 2026-04-19 11:10 UTC

- changed:
  added persisted `LLM` usage counters to the operator UI via
  `/api/v1/scheduler/status`, showing registered AI calls over the last hour
  and accumulated over the current UTC day.
- reason:
  runtime-only counters reset on backend restart and did not answer the
  operational question of how much LLM traffic the bot has generated recently.
- pending:
  if the bot starts persisting more AI-driven workflows, extend the counter to
  include those entry types explicitly instead of keeping the current set
  limited to `ai_trade_decision`, `ai_position_management`, and AI-backed
  `macro_signal`.
- next:
  consider exposing the same counts with a breakdown by workflow so the operator
  can distinguish execution, management, and macro research usage.
- blockers:
  none.

### 2026-04-19 11:28 UTC

- changed:
  hardened transient `IBKR proxy` cooling-down failures so they no longer pause
  the whole bot on first occurrence. `MarketDataService` now recognizes proxy
  `503` cooldown responses with `retry_after_seconds`, enters a bounded
  provider cooldown, and serves fallback data during that window. The scheduler
  also treats any remaining market-data cooldown errors as transient and keeps
  the bot running instead of opening an incident immediately.
- reason:
  the bot had paused on an `ibkr_proxy /contracts/search` cooldown even though
  the upstream explicitly signaled a short retry window and recovered on its
  own a few seconds later.
- pending:
  expose transient provider cooldown state in the UI if operators need to know
  when the bot is temporarily running on degraded market-data inputs.
- next:
  if this pattern repeats often, add explicit metrics for provider cooldown
  entries and fallback-usage frequency by upstream provider.
- blockers:
  none.

### 2026-04-19 12:05 UTC

- changed:
  extended the macro context with a structured indicator layer for
  `CNN Fear & Greed`, `VIX`, and the `US 10Y` Treasury yield, exposed through
  the existing `/api/v1/macro/context` payload and rendered in the operator UI
  inside the `Macro y calendario` panel.
- reason:
  macro research was only showing narrative signals and calendar items, which
  left out high-signal regime gauges that the operator explicitly wants to
  monitor while framing market risk and scenario selection.
- pending:
  decide whether the macro indicator lane should also feed automatic regime
  scoring and not stay limited to observation/context. `CNN Fear & Greed`
  remains best-effort because CNN currently blocks direct automated access from
  this environment with `HTTP 418`, so the UI may show it as unavailable.
- next:
  if the indicator lane proves useful, expose freshness/last refresh metadata
  more explicitly and wire the same values into discovery or decision
  explanations.
- blockers:
  none.

### 2026-04-19 12:18 UTC

- changed:
  expanded the macro-indicator lane with key commodity gauges from Yahoo
  Finance: `Gold` (`GC=F`), `WTI Crude` (`CL=F`), and `Copper` (`HG=F`).
  The macro summary now includes up to six live indicators and the frontend
  macro panel renders all six instead of truncating after four.
- reason:
  rates, volatility, and sentiment alone were not enough to frame inflation,
  safe-haven flows, or cyclic-growth tone. The operator explicitly wanted
  commodity context visible alongside the existing macro dashboard.
- pending:
  if these indicators start affecting automated regime or discovery logic,
  define explicit policy thresholds instead of keeping the current lightweight
  daily-change interpretations (`safe_haven_bid`, `inflationary_pressure`,
  `growth_stress`, etc.).
- next:
  consider adding a second macro row or small dedicated panel if the indicator
  set grows further, so the market-state card does not become too dense.
- blockers:
  none.

### 2026-04-19 12:34 UTC

- changed:
  added an official public macro-calendar fallback so the bot no longer depends
  only on `Finnhub` for macro events. `CalendarService.list_macro_events()` now
  merges any available Finnhub economic calendar data with official schedules
  parsed from the `Federal Reserve` monetary-policy page (`FOMC` meetings and
  minutes), the `ECB` Governing Council meeting calendar (rate-decision days),
  and the `BEA` release schedule (`GDP` and `Personal Income and Outlays` /
  `PCE`). Also widened the macro-research horizon default from `14` to `45`
  days so slower monthly/quarterly catalysts stay visible to the research lane.
- reason:
  major macro catalysts such as rate decisions, `GDP`, and `PCE` are too
  important to miss, and the previously implemented macro calendar was weak
  whenever `Finnhub` was unavailable or out of quota.
- pending:
  direct `BLS` schedules for `CPI`, `PPI`, and `Employment Situation` are still
  not wired through this environment because `bls.gov` is currently returning
  anti-bot `403 Access Denied` responses to server-side requests here. Those
  events remain desirable, but they need either an alternative source path or a
  user-provided feed/API that is operable from the runtime.
- next:
  if the operator wants full US macro coverage without third-party quotas, add
  a dedicated ingestion path for `BLS` release dates once a runtime-compatible
  source is available, then surface source provenance in the UI for macro
  calendar rows just like the corporate-event context already does.
- blockers:
  none.

### 2026-04-19 12:48 UTC

- changed:
  integrated `FRED` release dates into the macro calendar using the configured
  `FRED_API_KEY`. The official macro calendar lane now augments `Fed`, `ECB`,
  and `BEA` events with `BLS`-style release dates for `US CPI`
  (`release_id=10`), `US PPI` (`46`), and `US Employment Situation` (`50`),
  which restores the missing inflation and labor-market catalysts that were not
  reliably fetchable directly from `bls.gov` in this runtime.
- reason:
  the operator added `FRED_API_KEY`, which gives us a stable and API-native way
  to recover the most important scheduled `BLS` publication dates without
  depending on blocked `bls.gov` pages or scraping.
- pending:
  `FRED` gives us release dates cleanly, but not the richer release metadata
  such as exact published values/estimates in the same structure as a live
  economic-calendar vendor. If the bot later needs actual-vs-estimate surprise
  analysis on release day, that will still need a separate ingestion path.
- next:
  surface macro-calendar source provenance in the UI so the operator can see
  which upcoming events came from `fred`, `federal_reserve`, `ecb`, `bea`, or
  `finnhub`.
- blockers:
  none.

### 2026-04-19 13:00 UTC

- changed:
  enriched `FRED` macro release events with same-day published values when
  available. `US CPI`, `US PPI`, and `US Employment Situation` now populate
  `actual/previous` from stable `FRED` series snapshots when the observation's
  `realtime_start` matches the event date, and the frontend macro calendar now
  shows source provenance plus any available `actual`, `previous`, or
  `estimate` fields instead of only a raw source slug.
- reason:
  after adding `FRED` release dates, the missing next step was to make release
  provenance more legible in the UI and to prepare the bot to attach the actual
  published data on release day rather than treating every macro event as only
  a future placeholder.
- pending:
  this captures released values but not economist consensus or surprise versus
  estimate. If post-release evaluation should compare actual versus expected,
  the bot will still need an estimate source or pre-release snapshot store.
- next:
  if the operator wants richer macro event handling, persist released
  `actual/previous` snapshots to journal/memory on event day and expose a
  dedicated macro-calendar panel in the UI instead of only the condensed
  market-state card.
- blockers:
  none.

### 2026-04-19 13:05 UTC

- changed:
  made the macro/geopolitical branch explicit in the web console. The frontend
  now fetches persisted `macro/signals` and `watchlists`, shows a dedicated
  `Macro Research Lane` panel with synthesized theses, regime, relevance, and
  LLM/fallback mode, and adds a `Thematic Watchlists` panel for `macro_*`
  watchlists created from those theses. The main metrics strip now also counts
  persisted macro signals and macro watchlists.
- reason:
  the backend already had a dedicated macro/geopolitical research lane, but in
  the operator UI it still looked like generic background activity. Making it
  visible as its own lane makes it clear that the bot is forming macro theses,
  mapping them to assets, and turning them into operable universes.
- pending:
  the UI now exposes the lane, but if the bot is paused or no new macro thesis
  has been persisted yet, the panel will be empty. The next useful step would
  be to surface per-theme freshness, last evidence update, and whether the
  thesis has already spawned concrete trade candidates.
- next:
  connect macro signals to downstream execution traces so the operator can see
  which watchlist items, trades, or hedges were born from each macro thesis.
- blockers:
  none.

### 2026-04-19 13:40 UTC

- changed:
  added a bounded airline-specific `intermarket_context` to the deterministic
  decision layer and learning loop. The bot now builds daily context from
  `JETS/SPY`, `USO`, ticker-vs-sector relative strength, close-location, and
  the new IBKR proxy `options-sentiment` endpoints. Direct symbol sentiment is
  consumed first, then `put/call` extremes fall back to `top` rankings when
  the snapshot ratio is absent. The resulting context is persisted into signal,
  entry and journal snapshots, scored in `EntryScoringService`, and exposed as
  learnable features/combo features in `relevance.py`.
- reason:
  the earlier airline doctrine review showed that the highest-signal
  improvement for the current daily-bar stack was not intraday execution logic
  but sector-aware intermarket context plus bounded options sentiment. The new
  proxy endpoints made the `put/call` part feasible without leaking IBKR field
  ids into the bot.
- pending:
  this is still a daily context layer, not a full airline playbook. The bot
  does not yet consume `HO1!/CL1!`, direct jet-fuel proxies, or intraday
  microstructure. `options-sentiment/top` is only used as a fallback for
  per-ticker classification, not yet as a discovery input.
- next:
  decide whether to propagate the new intermarket/options-sentiment signals
  into `discovery` ranking and thematic watchlist generation, or keep them as a
  pure decision/learning overlay until enough paper-trade evidence accumulates.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/providers/market_data/ibkr_proxy_provider.py backend/app/domains/market/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/relevance.py backend/tests/test_intermarket_context.py backend/tests/test_ibkr_proxy_provider.py`
  `pytest -q backend/tests/test_ibkr_proxy_provider.py backend/tests/test_intermarket_context.py backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals` -> `11 passed`

### 2026-04-19 14:15 UTC

- changed:
  added a v1 `price_action / volume proxies` layer based only on daily
  `OHLCV`. The fused market analysis now computes and returns a structured
  `price_action_context`, the learning loop persists it into `signal_context`,
  `entry_context`, journal observations, and execution `management_context`,
  and the relevance engine now tracks price-action features plus the combo
  `setup__price_action_primary`. The seeded catalog also grows four explicit
  auxiliary signal definitions:
  `failed_breakdown_reversal`, `rejection_wick_at_support`,
  `high_relative_volume_reversal`, and `breakout_failure_reclaim`.
- reason:
  the bot needed a traceable timing/confirmation layer that captures useful
  candle-and-volume behavior without pretending to see `Level 2`, footprint,
  or true order flow. Persisting the proxy context makes it measurable instead
  of hard-coding it as hidden discretionary logic.
- pending:
  this is still daily-bar logic only. There is no intraday `VWAP`,
  microstructure, or direct order-book evidence, and the execution layer uses
  the proxy context for transparency and learning before it uses it for
  stronger autonomous management decisions.
- next:
  once enough paper-trade samples accumulate, decide whether any of the new
  price-action features deserve stronger weighting in scoring/risk management,
  or whether they should remain observational overlays.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/analysis.py backend/app/domains/learning/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/relevance.py backend/app/domains/execution/services.py backend/app/domains/system/services.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_bootstrap.py backend/tests/test_strategy_catalog.py backend/tests/test_market_analysis.py backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_relevance_engine.py backend/tests/test_autonomous_position_management.py` -> `26 passed`

### 2026-04-19 15:33 UTC

- changed:
  integrated the new IBKR proxy `market-overview` endpoint into the bot as a
  conservative ticker-context aggregator. The market-data provider and
  `MarketDataService` now expose `get_market_overview()` with cache plus
  graceful fallback composition, the agent tool gateway now exposes
  `market.get_overview`, and the decision layer reuses overview data for
  ticker-scoped corporate events and options sentiment before falling back to
  the older separate calls. The provider also normalizes `corporateEvents`
  into calendar-style dictionaries and classifies earnings-like labels such as
  `Erng Call`.
- reason:
  the proxy can now deliver `marketSignals`, `optionsSentiment`, and
  `corporateEvents` in one response, including a corporate-event backfill path
  that avoids a real upstream blind spot. Wiring that into the bot reduces
  ticker-specific call fan-out while keeping the internal bot model stable:
  `calendar_context`, `intermarket_context`, and `options_sentiment` stay
  separate and learnable.
- pending:
  the bot still does not persist or learn directly from the raw
  `market_signals` portion of the overview. `market-overview` is currently
  used as a hydration source for decision-time ticker context, not as a new
  discovery or scoring feature family. Macro and multi-symbol intermarket
  logic still depend on the existing dedicated services.
- next:
  decide whether the overview's `market_signals` block deserves a small
  derived feature set for `DecisionContextSnapshot` and `relevance.py`, or
  whether it should remain a human/agent research aid until more paper-trade
  samples accumulate.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/providers/market_data/ibkr_proxy_provider.py backend/app/domains/market/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/tools.py backend/tests/test_ibkr_proxy_provider.py backend/tests/test_intermarket_context.py backend/tests/test_agent_planner.py`
  `pytest -q backend/tests/test_ibkr_proxy_provider.py backend/tests/test_intermarket_context.py backend/tests/test_agent_planner.py` -> `23 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals -vv` -> `1 passed`

### 2026-04-19 15:53 UTC

- changed:
  extended the existing daily `price_action_context` into a tighter reversal
  support layer instead of creating a separate strategy. The `OHLCV` proxy
  engine now detects `support_reclaim_confirmation`, emits structured reversal
  facts such as `support_level`, `reclaim_level`, `close_vs_support_pct`,
  `rejection_wick_ratio`, `higher_timeframe_bias`, `follow_through_state`,
  `reversal_signal_flags`, and `structural_invalidation_level`, and persists
  them automatically anywhere `price_action_context` already travels. The
  seeded signal-definition catalog now includes
  `support_reclaim_confirmation`, the deterministic decision layer now
  suppresses or blocks reversal bonuses when the context is hostile
  (`downtrend`, hostile higher timeframe, imminent earnings, or
  `high_volatility_risk_off`), and the relevance engine now tracks the new
  reversal context fields plus the combo
  `price_action_primary__higher_timeframe_bias`.
- reason:
  the existing proxy layer already covered most of the requested reversal
  doctrine, but it lacked an explicit reclaim-followup signal, higher-timeframe
  context, and a conservative notion of follow-through risk. This update makes
  the reversal family more measurable and safer without changing the bot's
  core horizon or turning reversals into a dominant standalone strategy.
- pending:
  the system still does not infer a dedicated `failed_breakdown_reversal_long`
  playbook or strategy version. Reversal execution still rides on the current
  long frameworks (`pullback_long` / `breakout_long`) and the stop logic is
  only documented structurally via context, not yet fully re-parameterized per
  reversal subtype.
- next:
  decide whether to promote `higher_timeframe_bias` and
  `follow_through_state` from observational features into stronger sizing or
  regime-policy hooks once enough paper-trade samples exist, and only then
  revisit whether a dedicated reversal playbook is justified.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/analysis.py backend/app/domains/system/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/relevance.py backend/tests/test_market_analysis.py backend/tests/test_intermarket_context.py backend/tests/test_strategy_catalog.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_market_analysis.py backend/tests/test_intermarket_context.py backend/tests/test_strategy_catalog.py backend/tests/test_relevance_engine.py backend/tests/test_autonomous_position_management.py` -> `30 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals` -> `1 passed`

### 2026-04-19 16:21 UTC

- changed:
  added explicit quarterly-derivatives expiry context as a preventive
  calendar/risk overlay. `CalendarService` now computes a traceable
  `expiry_context` with holiday-adjusted quarterly expiry dates, including
  the Juneteenth-shifted June 2026 case. The decision layer now injects this
  into `calendar_context` as `quarterly_expiry_date`,
  `days_to_quarterly_expiry`, `expiration_week`, `pre_expiry_window`,
  `expiry_day`, `post_expiry_window`, and nested `expiry_context`, then uses
  it to degrade calendar fit, tag event-risk flags, and reduce event sizing
  more aggressively on `T-1` / `T`. The context is also propagated into
  `signal_context`, `entry_context`, execution `management_context`, world
  state snapshots, relevance features, and the operator reasoning chain in
  the UI.
- reason:
  quarterly expiry matters here as an execution-noise and roll/hedging-flow
  regime, not as a directional signal. The bot needed a lightweight internal
  way to anticipate those windows, explain why confidence/risk were degraded,
  and measure later whether certain setups or timing decisions behave worse
  near expiry.
- pending:
  this v1 does not consume a broker/exchange-native derivatives calendar and
  does not model special one-off market closures beyond the internal US
  equity holiday rules. Discovery itself is still only indirectly affected via
  downstream scoring/risk overlays rather than a dedicated expiry-aware
  candidate-ranking layer.
- next:
  decide after more paper-trade samples whether any expiry-aware relevance
  stats should graduate into stronger setup-specific context rules, and
  whether an agent/tool surface for querying expiry context is worthwhile for
  research workflows.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/world_state.py backend/app/domains/learning/relevance.py backend/app/domains/learning/services.py backend/app/domains/execution/services.py backend/tests/test_calendar.py backend/tests/test_risk_budget.py backend/tests/test_intermarket_context.py backend/tests/test_relevance_engine.py backend/tests/test_autonomous_position_management.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_calendar.py backend/tests/test_risk_budget.py backend/tests/test_intermarket_context.py backend/tests/test_relevance_engine.py backend/tests/test_autonomous_position_management.py` -> `41 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_learning_loop.py::test_orchestrator_persists_market_state_snapshots_and_exposes_latest` -> `2 passed`

### 2026-04-19 16:58 UTC

- changed:
  added a specialized `MSTR` company-context lane driven by metrics published
  on `strategy.com`, with a robust `__NEXT_DATA__` provider and local cache
  fallback in
  `backend/app/providers/strategy_company.py`. The bot now computes a
  traceable `mstr_context` for `MSTR` only, including `current_mnav`,
  `mnav_bucket`, distance-to-threshold fields, approximate
  `mnav_zscore_30d` from purchase-event lookback, BTC holdings and diluted
  share deltas, `bps` and `bps_trend`, `days_since_last_btc_purchase`,
  `recent_capital_raise`, `capital_raise_mode`, `atm_risk_context`, and a
  conservative `exposure_preference` versus the configured BTC proxy. That
  context now feeds `DecisionContextAssemblerService`, scoring, candidate risk
  profiles, position sizing, signal/entry persistence, execution
  `management_context`, relevance features/combos, and the operator reasoning
  chain in the UI.
- reason:
  `MSTR` does not behave like a generic equity. It needs a dedicated overlay
  that combines BTC regime with Strategy-specific capital-structure and
  treasury metrics, especially `mNAV` and implied ATM/dilution risk. The goal
  is not to turn `mNAV` into a trade trigger, but to make the bot more honest
  about when `MSTR` should be preferred versus a BTC proxy, and when elevated
  valuation/capital-markets context should reduce confidence or size.
- pending:
  this v1 still treats `BTC Yield`, `BTC Gain`, and dollar-gain style metrics
  as descriptive fields only, not scoring drivers. The `mNAV` z-score is an
  explicitly approximate lookback based on purchase-event history plus market
  prices, not a pristine daily IR time series. There is also no dedicated
  agent tool yet for querying `mstr_context` directly; the main path is via
  decision context, persistence, and UI visibility.
- next:
  decide after more paper-trade samples whether `mstr_context` deserves a
  small dedicated tool surface or research-task integration, and whether any
  of the new features should graduate into stronger learned context rules for
  `MSTR`-specific sizing, playbook selection, or preference between `MSTR`
  and the BTC proxy.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/providers/strategy_company.py backend/app/domains/market/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/relevance.py backend/app/domains/learning/services.py backend/app/domains/execution/services.py backend/tests/test_mstr_context.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_mstr_context.py` -> `6 passed`
  `pytest -q backend/tests/test_intermarket_context.py backend/tests/test_relevance_engine.py backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_autonomous_position_management.py backend/tests/test_risk_budget.py` -> `28 passed`

### 2026-04-19 17:22 UTC

- changed:
  replaced the old ephemeral keyword-chat UX with a first-class persistent
  conversation system. Added `chat_conversations` and `chat_messages` tables
  plus migration `20260419_0016`, new chat schemas, and a dedicated
  `ChatConversationService` that supports listing, creating, opening,
  updating, archiving, and replying inside persistent threads. The new chat
  flow persists structured message context (`intent`, `classification`,
  `tickers`, `suggested_action`, `confidence`, linked entities, and model
  metadata), supports `ticker_review` and `investment_idea_discussion`,
  creates controlled follow-up actions (`memory_saved`,
  `research_task_created`), and keeps the legacy `POST /chat` endpoint as a
  compatibility wrapper. Added a chat preset layer with per-conversation
  `preferred_llm`, availability endpoint, UI selector, and explicit tracing
  of `requested_llm`, `used_provider`, `used_model`, `reasoning_effort`,
  `fallback_used`, and `provider_error`. Extended the OpenAI-compatible
  provider so `gpt-5.4 xhigh` actually sends `reasoning_effort=xhigh`. The
  frontend chat panel was rebuilt into a thread-based workbench with sidebar,
  archived-thread toggle, model selector, per-message model/fallback badges,
  and persistent loading of existing conversations.
- reason:
  the old chat was only a thin in-memory wrapper over a few canned summaries.
  It could not carry investment debates over time, could not promote useful
  user input into the system in a controlled way, and could not expose which
  LLM produced each answer. The new design keeps chat inside the existing
  architecture instead of inventing a parallel notebook: conversations can
  become memory or research, but not execution, and every promotion remains
  explicit and traceable.
- pending:
  this MVP does not yet auto-create hypotheses, watchlists, or proposed
  workflow-improvement entities from chat; it currently persists memory and
  research only. The LLM layer is used as an optional response-enrichment
  pass on top of deterministic system reasoning, so the chat remains useful
  even when a preset is not configured, but deeper thread summarization and
  richer long-context model prompting are still minimal. The legacy
  compatibility endpoint remains in place and the conversation titles are
  still heuristic rather than model-generated.
- next:
  decide whether strong chat ideas should graduate into explicit
  `hypothesis_candidate` / `watchlist_candidate` records, whether the agent
  tool surface should be able to open or inspect chat threads directly, and
  whether conversation summaries should be periodically compacted into a
  dedicated thread-memory layer for longer discussions.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/conversations.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/agent.py backend/app/core/config.py backend/app/db/models/chat_conversation.py backend/app/db/models/chat_message.py backend/tests/test_chat_conversations.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_chat_conversations.py backend/tests/test_bot_chat.py backend/tests/test_agent_tools.py backend/tests/test_calendar.py backend/tests/test_news.py` -> `33 passed`

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

### 2026-04-20 10:20 UTC

- changed:
  added a dedicated per-ticker decision trace so operators no longer need to
  reconstruct a ticker's flow manually from separate signals, journal
  entries, and positions. The backend now exposes
  `/api/v1/journal/ticker-trace/{ticker}` backed by
  `TickerDecisionTraceService`, which merges the latest signal context
  (`decision_trace`, `guard_results`, `ai_overlay`, score), ticker journal
  entries, and position open/close events into one ordered timeline plus a
  compact summary. The frontend gained a new `Ticker Trace` panel with a
  ticker input, headline summary, and event feed so the latest decision,
  blocking reason, LLM status, and relevant position/journal events are
  visible in one place.
- reason:
  runtime observability had become too fragmented. Understanding why a
  ticker ended in `watch`, `blocked`, or `paper_enter` often required
  jumping across the journal panel, open positions, raw signal context, and
  scheduler state. This slice makes the decision path inspectable with one
  query and one UI panel instead of manual reconstruction.
- pending:
  the trace is read-only in this v1. It does not yet deep-link into the
  originating signal, position, or journal entity, and it does not yet
  expose decision-context snapshots or market-state snapshots as separate
  timeline events. The summary is optimized for speed rather than full
  forensic detail.
- next:
  decide whether to add direct links from each trace event to the underlying
  signal/position/journal record and whether a second-level detail drawer is
  worth the extra UI surface once the current summary view has been used in
  real runtime debugging.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/services.py backend/app/domains/learning/api.py backend/tests/conftest.py backend/tests/test_ticker_trace.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_ticker_trace.py backend/tests/test_bot_chat.py backend/tests/test_chat_conversations.py` -> `11 passed`

### 2026-04-20 11:05 UTC

- changed:
  added explicit latency instrumentation for per-ticker analysis so the bot
  can explain where time is being spent while evaluating a candidate. The
  `DO` loop now records a `timing_profile` per ticker with stage timings for
  reanalysis gating, signal analysis, decision-context assembly,
  deterministic scoring, research-package assembly, AI review, position
  sizing, persistence, execution-plan build/run, decision-context snapshot
  recording, and journal persistence. `DecisionContextAssemblerService`
  now emits its own nested timing breakdown, and `AgentToolGatewayService`
  records `elapsed_ms` for each execution-plan step plus aggregated research
  execution totals and slowest tool. These timing payloads are persisted in
  `signal_context`, journal observations, and decision-context snapshots.
  The `Ticker Trace` UI now surfaces total latency, slowest stage, and, when
  present, the slowest execution tool.
- reason:
  runtime debugging showed that “the bot is slow on this ticker” was a real
  complaint, but the system could not distinguish whether the cost came from
  deterministic context building, AI review, or downstream tool execution.
  This slice turns that into inspectable evidence rather than guesswork.
- pending:
  the current UI is optimized for fast operational diagnosis, not deep
  profiling. It does not yet show a collapsible per-step waterfall, and the
  orchestration metrics do not yet aggregate average or percentile latency
  across all tickers in a cycle.
- next:
  decide whether to promote the timing data into scheduler-level aggregate
  metrics, and whether the slowest execution tools should be surfaced in a
  compact runtime panel without requiring a ticker-specific trace lookup.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/services.py backend/app/domains/learning/decisioning.py backend/app/domains/learning/tools.py backend/app/domains/learning/relevance.py backend/app/domains/learning/schemas.py backend/tests/test_learning_loop.py backend/tests/test_agent_planner.py backend/tests/test_ticker_trace.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_ticker_trace.py backend/tests/test_agent_planner.py backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals` -> `13 passed`

### 2026-04-20 08:28 UTC

- changed:
  introduced a minimal, provider-agnostic LLM adapter in
  `backend/app/providers/llm.py` and moved the app's structured AI calls to
  that single contract: `generate_json(system_prompt, user_prompt,
  response_json_schema=None)`. The autonomous agent and persistent chat now
  build providers through `LLMProviderSpec` plus one selection point instead
  of instantiating Gemini-specific clients inline. Added a first-class
  `codex_gateway` adapter that talks to
  `/v1/chat/completions`, keeps the external compatibility label separate
  from optional internal `codex_model`, and remains swappable with Gemini by
  configuration only. Runtime selection now supports `LLM_PROVIDER` /
  `LLM_MODEL`, chat defaults can follow that provider when
  `CHAT_LLM_DEFAULT` is unset, and `.env.example` documents the new
  `CODEX_GATEWAY_*` knobs.
- reason:
  Gemini usage had started to leak into the runtime agent and chat
  orchestration directly, which made switching to `codex-gateway` look like
  a one-off migration rather than a reversible provider choice. The app
  only needs a small contract today: prompt in, structured JSON out, no
  streaming. A narrow adapter layer keeps the change small while making the
  provider swap explicit and reversible.
- pending:
  the abstraction is intentionally narrow. It does not yet cover streaming,
  tool calling, or arbitrary multimodal payloads because the app does not
  depend on them today. The generic runtime path is robust for Gemini and
  Codex Gateway, but a future OpenAI-compatible primary provider would still
  need a small extension in runtime config helpers for primary `api_base` /
  `api_key` resolution rather than relying on the fallback fields. Keep this
  as an explicit follow-up task so the bot can switch not only between Gemini
  and Codex Gateway but also to any OpenAI-compatible primary provider without
  code changes when subscriptions, quotas, or preferred models change.
- next:
  decide whether chat presets should be partly derived from the same central
  provider registry instead of remaining a separate preset list, and whether
  the runtime status UI should expose the active `LLM_PROVIDER` override so
  provider swaps are visible without reading environment configuration.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/providers/llm.py backend/app/domains/learning/agent.py backend/app/domains/learning/conversations.py backend/app/core/config.py backend/tests/test_ai_agent.py backend/tests/test_chat_conversations.py`
  `pytest -q backend/tests/test_ai_agent.py backend/tests/test_chat_conversations.py backend/tests/test_scheduler.py` -> `31 passed`

### 2026-04-20 08:37 UTC

- changed:
  updated `PLAN.md` and `IMPLEMENTATION_JOURNAL.md` to formalize a new
  lightweight `skills` layer for the trading bot. The plan now distinguishes
  explicitly between `tools`, `skills`, `playbooks` and learned rules,
  defines a compact/on-demand skill model, adds a procedural-learning section
  (`tools + memory + skills + improvement policy`), and extends journal,
  PDCA, data-model and MVP expectations so reviewed experience can be
  promoted into reusable procedures. The journal now tracks this as an
  explicit architecture focus and changes the immediate next step from
  monitor hardening to a first bounded `skills` implementation slice.
- reason:
  the system already has tools, journal, memory, playbooks and
  feature-derived context rules, but it still lacks a first-class way to turn
  validated experience into reusable operating procedures. Without that
  intermediate layer, the bot can record lessons yet still fail to reuse them
  consistently.
- pending:
  no backend implementation exists yet for the skill catalog, routing,
  persistence or promotion flow. The new concept is now documented, but the
  first v1 still needs concrete models, router logic and review integration.
- next:
  implement a small v1 skill slice: skill metadata catalog, deterministic
  router, persistence of applied skill traces, and a controlled promotion
  path from reviewed lessons to candidate skills.
- blockers:
  none.
- verification:
  documentation update only; no runtime code or tests changed in this slice.

### 2026-04-20 09:24 UTC

- changed:
  wired the existing lightweight `skills` module into the live backend flow.
  `DecisionContextAssemblerService` now routes deterministic procedural skills
  and persists `skill_context` inside `decision_context`. The orchestrator now
  carries that same `skill_context` through `signal_context`,
  `entry_context` and journal observations. `DecisionContextSnapshot` now
  records skill context alongside price-action/intermarket/MSTR context, and
  feature extraction now emits `skill.primary_skill`,
  `skill.risk_skill_active` plus the combo `setup__primary_skill`. The
  strategy-context adaptation layer now annotates feature-derived temporary
  rules with a promotion trace toward suggested skills. On the execution side,
  `management_context` now includes routed skills, and trade reviews now
  produce both `review_skill_context` and a bounded `skill_candidate`
  promotion artifact persisted in `memory` and `journal`. Also added a small
  `GET /api/v1/skills/catalog` endpoint plus tests for catalog exposure,
  routing, persistence and review promotion.
- reason:
  the repo already had a solid base of tools, journal, memory, playbooks and
  feature-derived rules, and it even already contained a code-level skill
  catalog/router. The missing piece was integration: without wiring it into
  candidate decisions, management updates, post-trade review and feature
  learning, the new layer remained conceptual rather than operational.
- pending:
  the skill layer is still deterministic-only and mostly backend-visible.
  There is no operator-first UI for inspecting routed skills or skill
  candidates yet, and no replay/paper validation path that can promote a
  `skill_candidate` into a validated active skill revision. Skills are also
  not yet loaded on-demand into the agent prompt/runtime as compact procedural
  instructions.
- next:
  expose `skill_context` and `skill_candidate` traces in the UI/ticker trace,
  then add a bounded validation workflow so promoted skills require replay or
  paper evidence before they can materially change live behavior.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/decisioning.py backend/app/domains/learning/services.py backend/app/domains/learning/relevance.py backend/app/domains/execution/services.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/api/v1/routers/learning.py backend/tests/test_learning_loop.py backend/tests/test_relevance_engine.py backend/tests/test_autonomous_position_management.py backend/tests/test_skills.py backend/tests/conftest.py`
  `pytest -q backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot backend/tests/test_relevance_engine.py::test_check_phase_recomputes_feature_outcome_stats backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules backend/tests/test_autonomous_position_management.py::test_auto_exit_evaluation_can_adjust_open_position_risk` -> `8 passed`

### 2026-04-20 09:41 UTC

- changed:
  extended the v1 skills slice into an operator-visible and gate-controlled
  lifecycle. Added `SkillLifecycleService` in
  `backend/app/domains/learning/skills.py` to manage `skill_candidate`
  memory, validate candidates through an explicit `paper|replay` gate,
  create `validated_skill_revision` memory artifacts, supersede older active
  revisions and inject active revision overlays back into `skill_context` at
  runtime. Exposed new skills endpoints:
  `GET /api/v1/skills/dashboard`, `GET /api/v1/skills/candidates`,
  `GET /api/v1/skills/revisions` and
  `POST /api/v1/skills/candidates/{candidate_id}/validate`. The runtime now
  attaches active validated revisions to entry/management skill routing, and
  `Ticker Trace` plus the operator console surface both `skill_context` and
  candidate/revision state. The frontend now includes dedicated `Skill
  Candidates` and `Validated Skills` panels with direct validation actions.
- reason:
  after wiring skills into decision/review persistence, the next bottleneck
  was operational usability. Without a visible candidate queue and an explicit
  gate to validate or reject proposed procedural changes, the skills layer
  remained mostly backend metadata. This slice makes the promotion path
  inspectable, reversible and still conservative.
- pending:
  validated revisions are still runtime overlays, not prompt-loaded procedural
  instructions. Unknown draft candidates that do not map to a base catalog
  skill can now be validated honestly but remain `pending_catalog_integration`
  rather than becoming first-class routed skills. Revision governance is also
  intentionally light: newest active revision supersedes the previous one, but
  there is not yet an explicit review board, expiry policy or replay-batch
  archive model.
- next:
  decide whether validated skills should be injected into the agent runtime as
  compact on-demand procedural instructions, and if so define the exact
  loading policy, revision precedence and safety boundaries for live use.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/skills.py backend/app/domains/learning/decisioning.py backend/app/domains/execution/services.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/services.py backend/tests/test_skills.py backend/tests/conftest.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot backend/tests/test_autonomous_position_management.py::test_auto_exit_evaluation_can_adjust_open_position_risk backend/tests/test_ticker_trace.py` -> `7 passed`

### 2026-04-20 12:05 UTC

- changed:
  started a dedicated runtime-performance slice focused on the scheduler and
  hot path services. `SchedulerService` now keeps a persistent
  `OrchestratorService` instead of constructing a fresh one every cycle, so
  hot in-memory caches can survive across continuous runs. `OrchestratorService`
  now builds a shared `market/news/calendar/macro` service graph for
  `AgentToolGatewayService` and `DecisionContextAssemblerService` instead of
  letting each instantiate its own copies. `CalendarService` now has explicit
  TTL caches for per-ticker event context and macro-event windows, with new
  settings/env knobs for both caches. Added targeted tests covering the new
  calendar caching and persistent-orchestrator behavior.
- reason:
  latency inspection of persisted `timing_profile` data showed that the bot is
  dominated by repeated external I/O, not CPU: about `6.3s` per analyzed
  ticker came from `reanalysis_gate + market_overview + calendar_context`,
  and the prior runtime recreated enough service layers that hot caches were
  frequently fragmented or lost between scheduler cycles.
- pending:
  this first slice does not yet add bounded parallelism, in-flight request
  deduplication, or a persisted due queue (`next_reanalysis_at`). Reanalysis
  still walks the active watchlist sequentially, and ticker analysis still
  performs some duplicated reads inside the same cycle even though the shared
  caches now make those duplicates much cheaper.
- next:
  run targeted tests, then re-measure runtime latency on fresh cycles to see
  how much `calendar_context`, `market_overview` and `reanalysis_gate` drop.
  If the improvement is material, the next bounded slice should be a
  persisted due queue and only then small read-side concurrency with provider
  semaphores.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/market/services.py backend/app/domains/learning/services.py backend/app/domains/system/services.py backend/tests/test_calendar.py backend/tests/test_scheduler.py`
  `pytest -q backend/tests/test_calendar.py backend/tests/test_scheduler.py` -> `29 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_autonomous_position_management.py::test_auto_exit_evaluation_can_adjust_open_position_risk backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot` -> `3 passed`

### 2026-04-20 12:33 UTC

- changed:
  documented a concrete architectural decision for research/backtesting in
  `PLAN.md`. The plan now defines a dedicated `research/backtest` lane, a
  small internal contract (`BacktestSpec`, `BacktestRun`, `BacktestTrade`,
  `BacktestMetricSnapshot`, `BacktestEngine`), and an explicit MVP centered on
  `1D` `OHLCV` validation rather than broker-native tooling. The documented
  decision is to ship a first engine as `native_daily_ohlcv_replay`, keep it
  aligned with the current deterministic stack and reuse existing strategy
  replay/candidate-validation pieces where possible. The plan also now records
  that external free tools were evaluated (`Backtesting.py`, `Backtrader`,
  `LEAN`, `vectorbt`) but should not be embedded into the core runtime in the
  first slice.
- reason:
  the repo already has a meaningful `trade_replay_rolling` validation path for
  strategy candidates, but it still lacks a first-class way for the bot to
  turn hypotheses, lessons and skill candidates into reproducible historical
  experiments. Without that explicit lane, "learning" remains partially
  manual: the system can review and replay known behavior, but it cannot yet
  run bounded backtests as a standard part of the PDCA loop.
- pending:
  no backtest-specific models, services or APIs exist yet. There is still no
  persisted `BacktestSpec/Run` entity, no out-of-sample runner, no historical
  experiment registry and no operator UI to launch or compare backtests. The
  current replay validation remains strategy-candidate-centric rather than a
  general research lane.
- next:
  implement a bounded backtesting MVP around the documented native engine:
  introduce experiment entities, wire a `BacktestService` to the existing
  market data/history stack, support `in-sample` plus `out-of-sample` runs for
  daily setups, and connect the results to `research_tasks`,
  `skill_candidates` and `candidate_validation_snapshots` before evaluating
  whether an external adapter such as `LEAN` is worth adding later.
- blockers:
  none.
- verification:
  documentation update only; no runtime code or tests changed in this slice.

### 2026-04-20 12:43 UTC

- changed:
  added a concrete design spec for a separate backtesting repository/service in
  `backend/docs/backtesting_service.md`, plus a versioned example payload in
  `backend/docs/examples/backtest_spec.example.json`. The spec now defines the
  external service boundary, repository structure, API surface, run lifecycle,
  authentication model, declarative `BacktestSpec` contract, and the exact
  integration shape expected back in this repo through a future
  `BacktestProvider` / remote-service adapter. Also linked that direction from
  `backend/docs/architecture.md`.
- reason:
  after deciding that backtesting should not bloat the trading-brain repo, the
  next missing piece was specificity. Without a concrete contract and service
  shape, "use another repo/VPS" stays aspirational and does not reduce design
  risk. This slice makes the separation actionable: the bot can now be treated
  as a client/orchestrator while the future backtesting repo is scoped as a
  narrow, versioned historical-experiments service.
- pending:
  no code integration exists yet. This repo still lacks a backtesting provider
  contract, remote client, local persistence for remote runs, and any UI/API
  to submit or inspect external backtests. The new external service repository
  itself also still needs to be created.
- next:
  implement the first integration slice inside this repo only:
  add a provider contract for backtesting, a remote-service adapter using the
  documented API, and lightweight local storage for `run_id`, status, summary
  metrics and artifact references linked to `research_tasks`,
  `skill_candidates`, and future experiment entities.
- blockers:
  none.
- verification:
  `python -m json.tool backend/docs/examples/backtest_spec.example.json`

### 2026-04-20 14:05 UTC

- changed:
  added the first persisted watchlist due-queue slice inside
  `backend/app/domains/learning/services.py`. `OrchestratorService` now writes
  a `reanalysis_runtime` block into `WatchlistItem.key_metrics`, including
  `next_reanalysis_at`, `last_evaluated_at`, `check_interval_seconds`,
  `last_gate_reason`, session label and policy version. `_assess_reanalysis_need`
  now short-circuits immediately when that timestamp is still in the future,
  so the loop skips `market_data/news/calendar` work for not-yet-due items.
  When an item is due but no trigger fires, the service reschedules the next
  review instead of rechecking again on the next cycle. Added/updated tests in
  `backend/tests/test_learning_loop.py` to verify runtime persistence, early
  skip behavior and expired-item rescheduling without a trigger.
- reason:
  the first cache-sharing slice removed repeated provider setup and duplicated
  calendar fetches, but the runtime still touched every active watchlist item
  every cycle. That meant the bot kept paying for `reanalysis_gate` and
  context-loading checks even when nothing meaningful had changed. Persisting a
  minimal `next_reanalysis_at` schedule cuts those repeated reads while
  keeping the event-driven regime-shift override and existing paper-execution
  semantics intact.
- pending:
  the scheduler is still cycle-based rather than a true priority queue, and
  the new due queue is intentionally minimal: it lives in `key_metrics`,
  remains single-process/local-state aware, and does not yet expose
  `next_reanalysis_at` or wake reasons in the UI. External I/O inside
  `decision_context` is still mostly sequential and still needs bounded
  parallelism plus explicit provider backpressure if timings remain too high.
- next:
  re-measure the latency profile with the new persisted schedule enabled, then
  add a small read-side concurrency layer for `market/news/calendar` only if
  the slow path is still dominated by upstream wait time. After that, expose
  the new reanalysis runtime state in the operator console.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/services.py backend/tests/test_learning_loop.py`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires backend/tests/test_learning_loop.py::test_assess_reanalysis_need_reschedules_expired_watchlist_item_without_trigger backend/tests/test_learning_loop.py::test_reanalysis_policy_uses_live_snapshot_for_technical_state` -> `3 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_learning_loop.py::test_orchestrator_do_opens_idle_market_scouting_tasks_when_watchlist_is_fully_deferred backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot backend/tests/test_autonomous_position_management.py::test_auto_exit_evaluation_can_adjust_open_position_risk` -> `4 passed`

### 2026-04-20 15:00 UTC

- changed:
  added bounded read-side parallelism inside
  `backend/app/domains/learning/decisioning.py`. `DecisionContextAssemblerService`
  now uses a small configurable thread pool
  (`DECISION_CONTEXT_IO_PARALLELISM_ENABLED`,
  `DECISION_CONTEXT_IO_MAX_WORKERS`) to overlap independent I/O-heavy reads:
  `market_overview`, `news_context` and `mstr_context` are loaded together,
  then `corporate_calendar_context`, `macro_calendar_context` and
  `intermarket_context` are loaded together once `market_overview` is
  available. Calendar assembly was split into corporate + macro subcontexts so
  provider work can overlap without changing the final payload shape. The
  timing profile now records whether bounded I/O parallelism was enabled and
  exposes the new calendar substages. Added regression coverage in
  `backend/tests/test_intermarket_context.py` to prove that independent reads
  actually overlap, while preserving existing intermarket/MSTR behavior.
- reason:
  after preserving caches across scheduler cycles and stopping unnecessary
  reanalysis, the remaining slow path was still dominated by independent
  external reads inside `decision_context`. Those reads were previously fully
  serialized even though they did not share DB state and could be overlapped
  safely under a low worker cap.
- pending:
  concurrency is now bounded, but provider access is still best-effort. There
  is no explicit token bucket, in-flight deduplication or provider-specific
  cooldown beyond what `MarketDataService` already does locally. The operator
  console also still does not show the persisted `next_reanalysis_at` schedule
  or the new decision-context timing metadata.
- next:
  re-measure live timings with the new bounded parallelism enabled. If the
  slowest path is still dominated by upstream waits, add provider-specific
  backpressure/request coalescing before increasing worker counts, then expose
  the reanalysis runtime and timing visibility in the UI.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/decisioning.py backend/tests/test_intermarket_context.py`
  `pytest -q backend/tests/test_intermarket_context.py::test_decision_context_parallelizes_independent_io_reads backend/tests/test_intermarket_context.py::test_decision_context_builds_supportive_airline_intermarket_context backend/tests/test_intermarket_context.py::test_decision_context_prefers_market_overview_for_calendar_and_options backend/tests/test_mstr_context.py::test_decision_context_wires_mstr_overlay_into_candidate_budget` -> `4 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot backend/tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` -> `4 passed`

### 2026-04-20 15:35 UTC

- changed:
  added in-flight request coalescing and cache locking to the shared
  `MarketDataService`, `NewsService` and `CalendarService` in
  `backend/app/domains/market/services.py`. Concurrent requests for the same
  `market_overview`, `options_sentiment`, `history`, `news` query, macro
  window, or ticker-event context now share one upstream call instead of
  racing each other. The service caches that are reused across scheduler
  cycles and decision-context threads are now guarded by locks so reads/writes
  stop iterating mutable dicts concurrently. Added targeted concurrency tests
  in `backend/tests/test_market_analysis.py`, `backend/tests/test_news.py` and
  `backend/tests/test_calendar.py`.
- reason:
  bounded parallelism inside `decision_context` improves latency only if the
  shared services do not turn that overlap into duplicated provider traffic or
  thread races. Without coalescing, the new threaded reads could still hit the
  same upstream endpoint more than once for the same key and could iterate
  shared caches while another thread was mutating them.
- pending:
  service-level coalescing is now in place, but there is still no explicit
  token bucket or semaphore-based backpressure by provider family. If live
  timings still show upstream waits or rate-limit pressure after this slice,
  the next step is to cap provider concurrency intentionally instead of just
  sharing duplicate calls. The operator UI also still does not expose the
  persisted reanalysis runtime or the richer timing metadata.
- next:
  re-measure live timings with the new coalescing enabled. If provider waits
  remain dominant, add provider-specific backpressure/cooldown policy, then
  surface reanalysis runtime and timing visibility in the operator console.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/services.py backend/tests/test_market_analysis.py backend/tests/test_news.py backend/tests/test_calendar.py`
  `pytest -q backend/tests/test_market_analysis.py::test_market_data_service_coalesces_concurrent_market_overview_requests backend/tests/test_news.py::test_news_service_coalesces_concurrent_queries backend/tests/test_calendar.py::test_calendar_service_coalesces_concurrent_macro_event_requests backend/tests/test_market_analysis.py::test_market_data_service_degrades_to_fallback_during_ibkr_proxy_cooldown backend/tests/test_news.py::test_news_service_caches_repeated_queries backend/tests/test_calendar.py::test_calendar_service_caches_macro_events_within_ttl` -> `6 passed`
  `pytest -q backend/tests/test_intermarket_context.py::test_decision_context_parallelizes_independent_io_reads backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot backend/tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` -> `5 passed`

### 2026-04-20 15:10 UTC

- changed:
  recorded a follow-up implementation plan for the remaining OpenClaw/Hermes
  adaptations that are still missing in the trading bot. The new plan makes
  explicit that the project has already captured the governance side of
  `tools != skills`, persistent memory, and conservative skill promotion, but
  still lacks the more valuable runtime-cognitive pieces: on-demand procedural
  skill loading, curated knowledge claims, explicit context-budget policy, and
  first-class multi-skill workflows. This plan is now the tracked path for the
  next architecture slices after the current runtime-performance work.
- reason:
  the current repo already routes, persists, validates, and surfaces skills,
  but validated revisions are still mostly runtime overlays rather than
  compact procedural instructions loaded into the agent at decision time.
  Memory is still generic `MemoryItem` storage rather than a curated
  claims/evidence model, and scheduled automation exists mostly as loop logic
  rather than as explicit procedural workflows. Without tracking these gaps
  explicitly, the project risks overestimating how much of the
  OpenClaw/Hermes pattern has actually been implemented.
- pending:
  the following work remains open and should be treated as a coherent program
  rather than isolated tweaks:
  1. `SkillRuntimePacket` + `SkillPromptAssembler` so the agent receives only
     the 1-3 relevant validated skills as compact on-demand instructions.
  2. `KnowledgeClaim` / `ClaimEvidence` style memory so durable knowledge can
     be stored as claims with evidence, contradictions, review state and
     freshness instead of only generic notes and metadata.
  3. explicit context-budget policy so procedural knowledge is compressed and
     loaded intentionally, reserving model context for what changed in market
     state.
  4. first-class multi-skill workflows such as premarket review, postmarket
     review, weekly rule audit, and regime-shift review.
  5. more portable/versionable skill artifacts so validated procedures can be
     reviewed, diffed and tested more independently from backend routing code.
- next:
  implement the first and highest-value slice:
  add `SkillRuntimePacket`, a small runtime-skill selection policy, and a
  prompt/context assembler that injects only context-relevant validated skill
  revisions into the agent decision call. Once that is live and measurable,
  add the curated claims memory layer before expanding into workflow objects or
  more portable skill packaging.
- blockers:
  none.
- verification:
  planning update only; no runtime code or tests changed in this slice.

### 2026-04-20 15:22 UTC

- changed:
  implemented the first runtime-oriented OpenClaw/Hermes slice by turning
  routed skills into compact on-demand procedural packets for the agent.
  Added `SkillRuntimePacket` support in
  `backend/app/domains/learning/skills.py`, plus
  `SkillLifecycleService.build_runtime_packets()` and
  `render_runtime_skill_prompt()`. The lifecycle layer now converts applied
  skills into compact runtime instructions, overlays active validated
  revisions when they exist, and produces a bounded prompt block instead of
  exposing only passive metadata. In `backend/app/domains/learning/agent.py`,
  candidate and position-management contexts now load runtime skill packets
  from `skill_context`, add a small explicit `context_budget` policy, and pass
  the compact skill instructions into the LLM system prompt via
  `build_candidate_decision_system_prompt(...)` and
  `build_position_management_system_prompt(...)` in
  `backend/app/domains/learning/protocol.py`. Added new AI settings in
  `backend/app/core/config.py` to keep runtime skill loading bounded.
- reason:
  the repo already had cataloged, routed, validated and inspectable skills,
  but they still behaved mostly like backend governance artifacts. The missing
  runtime value was that the agent did not actually receive the selected skill
  procedures at decision time. This slice closes that gap in a conservative
  way: only context-relevant skills are loaded, revision overlays remain
  bounded, and hard risk/regime rules still dominate.
- pending:
  this slice does not yet implement curated `KnowledgeClaim / ClaimEvidence`
  memory, contradiction tracking, or first-class workflow objects such as
  premarket/postmarket reviews. Skills are now runtime-visible, but still live
  as Python-backed definitions rather than fully portable `SKILL.md`-style
  artifacts. There is also no prompt-side measurement yet for how much the new
  runtime skill packets improve decision quality or reduce context waste.
- next:
  add the curated claims memory layer next, then instrument the agent/runtime
  so skill packet usage can be measured explicitly (`skill_code`,
  `revision_id`, impact on final action, and whether a complementary skill was
  missing). After that, decide whether to materialize validated skills into
  more portable/versionable artifacts.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/skills.py backend/app/domains/learning/protocol.py backend/app/domains/learning/agent.py backend/tests/test_skills.py backend/tests/test_ai_agent.py`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ai_agent.py` -> `17 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_autonomous_position_management.py::test_auto_exit_evaluation_can_adjust_open_position_risk backend/tests/test_relevance_engine.py::test_do_phase_records_decision_context_snapshot` -> `3 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 16:03 UTC

- changed:
  implemented the first curated-memory slice with explicit `KnowledgeClaim`
  and `KnowledgeClaimEvidence` persistence wired into the live learning loop.
  Added the new models in
  `backend/app/db/models/knowledge_claim.py`, migration
  `backend/migrations/versions/20260420_0017_knowledge_claims.py`, and the
  service layer in `backend/app/domains/learning/claims.py`. Exposed the new
  storage through `/api/v1/claims` in
  `backend/app/domains/learning/api.py` and
  `backend/app/api/v1/routers/learning.py`. `TradeReviewService` now promotes
  structured post-trade lessons into `review_improvement` claims with support
  evidence, and `StrategyContextAdaptationService.refresh_rules()` now
  promotes learned `StrategyContextRule` outputs into `context_rule` claims
  with stable rule evidence. Added API coverage in
  `backend/tests/test_knowledge_claims.py` and integration assertions in
  `backend/tests/test_learning_loop.py` and
  `backend/tests/test_relevance_engine.py`.
- reason:
  the runtime-skill slice made skills procedurally useful at decision time,
  but the memory side was still mostly generic notes plus metadata. The next
  missing OpenClaw/Hermes-inspired piece was a curated knowledge surface that
  can store durable claims separately from raw journal/memory text and can be
  traced back to concrete supporting evidence.
- pending:
  claims are now persisted and fed automatically from trade reviews and learned
  context rules, but this is still only the first half of the layer. The model
  does not yet support explicit contradiction ingest beyond raw evidence,
  freshness aging/review workflows, or runtime retrieval into the agent
  prompt. There is also no operator UI yet for browsing claims, and no direct
  claim-impact instrumentation in final decisions.
- next:
  use the new claim storage as runtime knowledge: add bounded claim retrieval
  to decision assembly, define how contradiction/freshness states should affect
  selection, and persist whether a claim influenced the final action. After
  that, add first-class workflows that review stale or contested claims.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/claims.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/relevance.py backend/app/domains/execution/services.py backend/app/api/v1/routers/learning.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py backend/tests/test_relevance_engine.py backend/tests/conftest.py`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `3 passed`
  `pytest -q backend/tests/test_relevance_engine.py::test_check_phase_recomputes_feature_outcome_stats backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_ai_agent.py::test_agent_decision_context_loads_runtime_skills_on_demand` -> `3 passed`

### 2026-04-20 16:18 UTC

- changed:
  completed the next claims-runtime slice by loading curated knowledge claims
  directly into the agent decision context and prompt. Added bounded runtime
  claim retrieval and prompt rendering in
  `backend/app/domains/learning/claims.py`, then wired that retrieval into
  candidate and position-management contexts in
  `backend/app/domains/learning/agent.py`. The runtime context budget now
  tracks both skills and claims, protocol prompts in
  `backend/app/domains/learning/protocol.py` now ask the model to declare
  `claims_applied`, and persisted AI decision journal/memory records now store
  both the loaded runtime claims and the subset the model said influenced the
  decision. Added configuration for bounded claim loading in
  `backend/app/core/config.py`, documented the new env knobs in
  `backend/.env.example`, and extended `backend/tests/test_ai_agent.py` to
  cover claim prompt embedding, runtime claim loading and `claims_applied`
  parsing.
- reason:
  after introducing `KnowledgeClaim` storage, the next missing piece was
  making that curated knowledge operational instead of merely persistent. The
  agent can now receive a small set of relevant durable claims alongside
  runtime skills, while still keeping the prompt bounded and preserving
  explicit traceability about what prior knowledge influenced a decision.
- pending:
  runtime claim retrieval is now live, but contradiction/freshness handling is
  still shallow: claims do not age automatically, there is no review workflow
  for stale/contested claims, and the operator UI still does not expose claim
  state or claim usage. The claim impact signal also relies on model-declared
  `claims_applied`, so there is not yet a stronger cross-check between loaded
  claims and actual decision deltas.
- next:
  implement contradiction/freshness governance for claims, then add a review
  workflow that revisits stale or contested claims before expanding into
  first-class multi-skill workflows.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/claims.py backend/app/domains/learning/agent.py backend/app/domains/learning/protocol.py backend/app/core/config.py backend/tests/test_ai_agent.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py backend/tests/test_relevance_engine.py`
  `pytest -q backend/tests/test_ai_agent.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules backend/tests/test_relevance_engine.py::test_check_phase_recomputes_feature_outcome_stats backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals` -> `19 passed`

### 2026-04-20 16:31 UTC

- changed:
  completed the claim-governance slice on top of runtime claim retrieval.
  `KnowledgeClaimService` in `backend/app/domains/learning/claims.py` now
  applies automatic freshness aging (`current -> aging -> stale`) using new
  settings in `backend/app/core/config.py`, exposes a `review_queue` for
  stale/contested/contradicted claims, and supports explicit review outcomes
  `confirm`, `contradict`, and `retire`. Added API schemas and routes in
  `backend/app/domains/learning/schemas.py` and
  `backend/app/domains/learning/api.py` for `/api/v1/claims/review-queue` and
  `/api/v1/claims/{claim_id}/review`, documented the new env knobs in
  `backend/.env.example`, and expanded
  `backend/tests/test_knowledge_claims.py` to cover stale-claim queueing and
  contradiction/retirement behaviour. The route ordering was also adjusted so
  `review-queue` does not get swallowed by the dynamic `/{claim_id}` path.
- reason:
  runtime claim loading alone was not enough; durable knowledge also needed a
  lightweight governance loop so stale or contradicted claims can be revisited
  and degraded without deleting history. This is the minimum viable
  `claims/evidence/freshness/review` loop before adding any UI or scheduled
  workflows.
- pending:
  claims can now age and be reviewed, but that governance is still API-only.
  There is no operator console for browsing the review queue, no one-click
  review actions in the UI, and no scheduled workflow that proactively revisits
  stale/contested claims. Runtime decisions still rely on the model to declare
  `claims_applied`, so deeper causal measurement of claim impact is still open.
- next:
  surface claim usage, stale/contested state and review actions in the
  operator UI, then decide whether stale-claim review should also run as an
  explicit scheduled workflow before moving on to larger multi-skill
  automations.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/claims.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/core/config.py backend/tests/test_knowledge_claims.py`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_ai_agent.py::test_agent_decision_context_loads_runtime_skills_on_demand` -> `4 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules backend/tests/test_relevance_engine.py::test_check_phase_recomputes_feature_outcome_stats backend/tests/test_learning_loop.py::test_orchestrator_do_persists_signals backend/tests/test_ai_agent.py::test_agent_decision_context_loads_runtime_skills_on_demand` -> `5 passed`

### 2026-04-20 16:52 UTC

- changed:
  recorded a concrete follow-on roadmap for the remaining OpenClaw/Hermes
  learning-loop adaptations in `PLAN.md` and aligned this journal with that
  order of implementation. The roadmap now explicitly prioritizes:
  `learning workflows`, `skill-gap detection`, the formal
  `claim -> skill_candidate` bridge, cognitive-budget observability, more
  portable skill artifacts, and memory distillation/compaction. This slice is
  documentation-only and intended to stop the repo from drifting into
  opportunistic changes without a clear order.
- reason:
  after adding runtime skills, curated claims, claim governance and operator
  claim review, the project now has enough primitives that the next risk is
  losing sequencing discipline. The missing value is no longer “more memory”
  but turning those primitives into explicit learning workflows and promotion
  paths in a controlled order.
- pending:
  none of the new roadmap items are implemented yet. In particular, there is
  still no explicit `skill gap` object, no formal bridge from validated claims
  to `skill_candidate`, and no first-class workflow entity such as
  `stale_claim_review` or `weekly_skill_audit`.
- next:
  implement `skill gap` detection first, then the formal
  `claim -> skill_candidate` bridge, and only then promote those mechanics into
  explicit workflow objects.
- blockers:
  none.
- verification:
  documentation update only; no runtime code or tests changed in this slice.

### 2026-04-20 10:35 UTC

- changed:
  stayed in the runtime-performance lane only and added operator visibility for
  the new due-queue/timing work without touching the parallel skills files.
  `backend/app/domains/market/schemas.py` now exposes a typed
  `WorkQueueSummaryRead`. `backend/app/domains/market/services.py` now adds
  actionable `watchlist_reanalysis_due` items to the work queue for active
  watchlist entries whose persisted `reanalysis_runtime.next_reanalysis_at` is
  missing or already due, while summarizing deferred reanalysis backlog,
  runtime-aware watchlist coverage, and the next scheduled ticker. The same
  service also derives a lightweight runtime timing summary from the latest 60
  signals with `signal_context.timing_profile`, including average total time,
  average `decision_context`, average `reanalysis_gate`, and the dominant
  decision-context bottleneck stage. `backend/app/frontend/app.js` now renders
  that summary in the work-queue panel and formats due reanalysis entries in a
  human-readable way instead of dumping raw context JSON.
- reason:
  the live database still shows the old slow path baseline and confirms that
  upstream wait time remains the main problem. Querying
  `signals.signal_context.timing_profile` on `backend/trading_research.db`
  showed `141` persisted timing samples overall and, on the latest `60`
  signals, an average total of `7144.4 ms`, average `decision_context` of
  `5359.0 ms`, average `reanalysis_gate` of `1683.6 ms`, average
  `market_overview` of `649.6 ms`, average `calendar_context` of `4424.6 ms`,
  and average `news_context` of `277.6 ms`. Those numbers are still the
  pre-rerun baseline for this optimization branch, so the most useful next
  slice was to expose the persisted runtime/backlog state directly in the
  console so the next bot run can be validated without ad hoc SQL.
- pending:
  provider-specific backpressure is still not implemented; service-level
  coalescing and bounded overlap exist, but there is no explicit token bucket
  or semaphore by provider family yet. The current database also does not yet
  contain `reanalysis_runtime` for existing watchlist rows, so the new queue
  visibility will become more informative after the next scheduler cycles run
  under the updated code.
- next:
  run the bot for a few real cycles and inspect the operator console for three
  things: whether deferred watchlist counts begin to dominate as expected,
  whether `decision_context` timing drops relative to the current baseline,
  and whether the dominant stage remains calendar-heavy. If upstream waits are
  still dominant after that rerun, add provider-family backpressure/cooldown
  caps before increasing any worker counts further.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/schemas.py backend/app/domains/market/services.py backend/tests/test_market_analysis.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_market_analysis.py::test_work_queue_surfaces_due_reanalysis_items_and_runtime_summary backend/tests/test_market_analysis.py::test_work_queue_timing_summary_ignores_signals_without_timing_profile backend/tests/test_market_analysis.py::test_market_data_service_coalesces_concurrent_market_overview_requests backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires backend/tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` -> `5 passed`

### 2026-04-20 10:42 UTC

- changed:
  ran two real `DO` passes against the live `backend/trading_research.db`
  runtime from the `backend/` working directory so the updated due-queue,
  shared caches and coalescing logic could be measured on the active bot state
  instead of only through tests or ad hoc SQL. The first pass seeded
  `reanalysis_runtime` across the active watchlist and produced new timing
  samples; the second pass then hit the new deferred path almost entirely.
- reason:
  after adding queue visibility, the missing piece was proof that the bot was
  actually changing behavior on the live database. We needed to see whether
  active watchlist items were being converted into deferred work and whether
  the second cycle became materially cheaper once `next_reanalysis_at` was in
  place.
- results:
  before the rerun, the live queue summary showed `44` due reanalysis items,
  `0` deferred items and `0` runtime-aware watchlist entries. Rolling timing
  telemetry over the latest `60` signals was still expensive: average total
  `7284.2 ms`, average `decision_context` `5679.6 ms`, average
  `reanalysis_gate` `1497.6 ms`, and dominant decision-context stage
  `calendar_context` at `4712.9 ms`.
  The first real `DO` pass on `2026-04-20 10:39 UTC` processed `44` watchlist
  items, generated `11` analyses/signals, deferred `33` entries by explicit
  reanalysis trigger logic, and took `193867.6 ms` wall time while seeding the
  runtime state. The second real `DO` pass on `2026-04-20 10:42 UTC`
  processed the same `44` watchlist items, generated `0` analyses, deferred
  all `44` entries, and completed in `8051.3 ms` wall time.
  After the rerun, the live queue summary showed `0` due reanalysis items,
  `44` deferred items and `44` runtime-aware watchlist entries, with the next
  scheduled ticker at `QQQ` for `2026-04-20T11:41:22.670971+00:00`. The
  rolling timing window also improved modestly from the new persisted samples:
  average total `6500.8 ms`, average `decision_context` `5097.2 ms`, average
  `reanalysis_gate` `1291.7 ms`, dominant decision-context stage still
  `calendar_context` at `4156.6 ms`.
- pending:
  the due-queue behavior is now confirmed on the live database, but the slow
  path for actually-due items is still calendar-heavy and the first expensive
  pass remains dominated by upstream wait time. There is still no explicit
  provider-family semaphore or token-bucket backpressure.
- next:
  treat provider-level backpressure as the next performance slice, focusing on
  the calendar-heavy read path first. The goal should be to cap concurrent
  corporate/macro calendar fetches intentionally so first-pass due work keeps
  latency bounded without undoing the gains from shared caches and deferred
  reanalysis scheduling.
- blockers:
  none.
- verification:
  live runtime execution only; two direct `OrchestratorService.run_do_phase(...)`
  passes completed against `backend/trading_research.db` and the resulting
  queue/timing state was inspected from the same database immediately after.

### 2026-04-20 10:50 UTC

- changed:
  implemented explicit provider-family backpressure for the calendar-heavy
  path in `backend/app/domains/market/services.py`. `CalendarService` now uses
  shared process-level semaphore gates by family (`calendar_corporate`,
  `calendar_earnings`, `calendar_macro`) so upstream corporate-event, earnings
  fallback, and macro-calendar fetches cannot fan out without limit across
  multiple `CalendarService` instances or concurrent callers. The limits are
  configurable through new settings in `backend/app/core/config.py` and
  documented in `backend/.env.example`:
  `CALENDAR_CORPORATE_MAX_CONCURRENT_REQUESTS`,
  `CALENDAR_EARNINGS_MAX_CONCURRENT_REQUESTS`,
  `CALENDAR_MACRO_MAX_CONCURRENT_REQUESTS`.
  Added concurrency tests in `backend/tests/test_calendar.py` that verify the
  new gates serialize distinct corporate and macro requests across two service
  instances, while keeping the earlier same-key coalescing behavior intact.
- reason:
  the live rerun confirmed that the due-queue now works, but also showed that
  the first expensive pass is still dominated by the calendar path. Coalescing
  only removes duplicate requests for the same key; it does not stop different
  tickers or horizons from hitting the same provider family concurrently when
  the process has multiple callers. The new backpressure layer makes that
  concurrency intentional instead of accidental.
- pending:
  this slice is protective rather than magically faster: it caps provider
  pressure but still needs a new live rerun to quantify any latency impact on
  the first-pass `calendar_context` path. There is still no rate-limit-aware
  cooldown policy specific to calendar providers beyond the concurrency caps.
- next:
  rerun a few real `DO` cycles against `backend/trading_research.db` with the
  new calendar backpressure enabled and compare the work-queue timing summary
  against the `10:42 UTC` baseline. If `calendar_context` remains dominant,
  consider adding provider-specific transient cooldown policy on calendar
  errors before revisiting worker counts elsewhere.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/market/services.py backend/tests/test_calendar.py`
  `pytest -q backend/tests/test_calendar.py::test_calendar_service_coalesces_concurrent_macro_event_requests backend/tests/test_calendar.py::test_calendar_service_backpressure_limits_concurrent_corporate_requests_across_instances backend/tests/test_calendar.py::test_calendar_service_backpressure_limits_concurrent_macro_requests_across_instances backend/tests/test_calendar.py::test_calendar_service_caches_ticker_event_context_within_ttl backend/tests/test_calendar.py::test_calendar_service_caches_macro_events_within_ttl` -> `5 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires backend/tests/test_intermarket_context.py::test_decision_context_parallelizes_independent_io_reads backend/tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` -> `3 passed`

### 2026-04-20 11:02 UTC

- changed:
  added transient provider cooldown handling on top of the new calendar
  backpressure layer in `backend/app/domains/market/services.py`.
  `CalendarService` now keeps shared process-level cooldown state per provider
  path (`calendar_corporate`, `calendar_earnings`,
  `calendar_macro_finnhub`, `calendar_macro_official`) and uses it to skip
  immediate retries after transient upstream failures. Corporate calendar
  fetches now fall back without re-hitting the proxy while the corporate
  provider is cooling down; earnings fallback stops re-querying Alpha Vantage
  during temporary throttling and serves cached/disk data when available; and
  macro calendar loading skips whichever macro provider is cooling down while
  still allowing the other provider to contribute data. The cooldown parser is
  heuristic and covers the error shapes currently seen in the providers:
  `429`, `rate limit`, `retry_after_seconds`, `503/service unavailable`,
  `timeout`, and explicit `cooling down` strings.
- reason:
  backpressure limits concurrency, but by itself it does not stop the process
  from repeatedly sending the same doomed request sequence after a provider has
  already told us to back off. The calendar path was still the confirmed
  bottleneck, so adding transient cooldowns there reduces avoidable retry
  pressure before the next live rerun.
- pending:
  this cooldown policy is heuristic rather than fully provider-specific. It
  does not yet expose cooldown state in the operator UI, and it still needs a
  live rerun against due watchlist items to quantify any impact on the first
  expensive `calendar_context` pass.
- next:
  once the next `next_reanalysis_at` window opens, rerun `DO` on the live
  database and compare queue/timing telemetry against the `10:42 UTC`
  baseline. If `calendar_context` is still dominant, the next refinement is to
  surface provider cooldown state in the console and then decide whether any
  calendar subprovider deserves a longer, explicit policy.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/services.py backend/tests/test_calendar.py`
  `pytest -q backend/tests/test_calendar.py::test_calendar_service_cooldown_skips_repeated_corporate_failures backend/tests/test_calendar.py::test_calendar_service_cooldown_skips_repeated_earnings_failures backend/tests/test_calendar.py::test_calendar_service_cooldown_skips_repeated_macro_failures backend/tests/test_calendar.py::test_calendar_service_backpressure_limits_concurrent_corporate_requests_across_instances backend/tests/test_calendar.py::test_calendar_service_backpressure_limits_concurrent_macro_requests_across_instances` -> `5 passed`
  `pytest -q backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires backend/tests/test_intermarket_context.py::test_decision_context_parallelizes_independent_io_reads backend/tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` -> `3 passed`

### 2026-04-20 11:09 UTC

- changed:
  surfaced the new calendar runtime protections in the operator work-queue
  summary. `backend/app/domains/market/schemas.py` now includes typed
  `ProviderRuntimeStatusRead` entries under
  `WorkQueueSummaryRead.calendar_provider_status`.
  `backend/app/domains/market/services.py` now lets `WorkQueueService` receive
  a `CalendarService`, includes calendar runtime status in the queue summary,
  and emits typed provider runtime entries showing whether each calendar
  provider family is configured, cooling down, how many seconds remain, and
  what concurrency cap is active. `backend/app/frontend/app.js` now renders
  both configured calendar gates and active cooldowns in the runtime card of
  the work queue, so the next live rerun can be interpreted from the console
  without extra SQL or logs. Added an API-level regression in
  `backend/tests/test_market_analysis.py` to verify that `/api/v1/work-queue`
  exposes calendar cooldown state after a transient provider failure.
- reason:
  after adding calendar backpressure and transient cooldowns, the protections
  were still invisible from the operator side. That would make the next live
  rerun ambiguous: we could see a quieter loop without knowing whether the bot
  was deferring work, respecting cooldowns, or simply not touching the
  provider path. Surfacing the runtime status closes that observability gap.
- pending:
  the console now shows configured gates and active cooldowns, but it still
  does not visualize per-provider error counts or cumulative time spent in
  cooldown across cycles. Those are only worth adding if the next live rerun
  shows repeated provider suppression.
- next:
  wait for the next `next_reanalysis_at` window, rerun `DO`, and inspect the
  queue summary for three signals together: due/deferred backlog, timing
  movement in `decision_context`, and whether any calendar provider family
  entered cooldown. That should tell us whether the next slice belongs in
  provider policy or elsewhere.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/market/schemas.py backend/app/domains/market/services.py backend/tests/test_market_analysis.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_market_analysis.py::test_work_queue_summary_exposes_calendar_cooldown_state backend/tests/test_market_analysis.py::test_work_queue_surfaces_due_reanalysis_items_and_runtime_summary backend/tests/test_calendar.py::test_calendar_service_cooldown_skips_repeated_corporate_failures backend/tests/test_calendar.py::test_calendar_service_cooldown_skips_repeated_macro_failures backend/tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires` -> `5 passed`

### 2026-04-20 11:17 UTC

- changed:
  extended the same protective runtime policy from `CalendarService` into
  `NewsService`. GNews reads in
  `backend/app/domains/market/services.py` now use a shared process-level
  semaphore gate keyed by provider target, plus shared transient cooldown
  state so repeated `429`, `503`, `timeout`, or `retry_after_seconds` errors
  stop hammering the upstream immediately. While a cooldown is active,
  `NewsService` serves cached query results when available and otherwise raises
  a cooldown-specific `NewsProviderError` without re-hitting GNews. Added the
  new `GNEWS_MAX_CONCURRENT_REQUESTS` setting in
  `backend/app/core/config.py` and `backend/.env.example`. Also surfaced news
  runtime status in the operator work-queue summary through
  `backend/app/domains/market/schemas.py`,
  `backend/app/domains/market/services.py`, and
  `backend/app/frontend/app.js`, so the console now shows both configured news
  gates and active news cooldowns next to the existing calendar status.
- reason:
  calendar was still the measured dominant bottleneck, but the new parallel
  decision-context path can also burst on GNews when multiple due items open
  together. Coalescing only removes duplicate identical queries; it does not
  limit distinct news lookups across instances or stop fast retries after a
  rate-limit response. This slice hardens that second external dependency
  before the next due-window rerun.
- pending:
  this change protects GNews pressure and improves observability, but it has
  not yet been measured on a fresh live due window. We still need to compare
  real `decision_context` timings once the current `next_reanalysis_at`
  entries start expiring again.
- next:
  when the next due window opens, rerun `DO`, inspect the work-queue runtime
  card for both `calendar` and `news` cooldown/gate state, and compare the
  new timing summary against the `10:42 UTC` and `11:09 UTC` baselines. If the
  loop is still dominated by external waits, the next likely slice is provider
  budgeting or smarter prefetch ordering rather than more raw parallelism.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/market/schemas.py backend/app/domains/market/services.py backend/tests/test_news.py backend/tests/test_market_analysis.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q tests/test_news.py tests/test_market_analysis.py::test_work_queue_summary_exposes_news_cooldown_state tests/test_market_analysis.py::test_work_queue_summary_exposes_calendar_cooldown_state tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires` from `backend/` -> `9 passed`

### 2026-04-20 11:29 UTC

- changed:
  added the same shared runtime protection pattern to
  `MarketDataService` in `backend/app/domains/market/services.py`.
  Market-data reads now use a shared process-level semaphore gate keyed by
  provider target, so `snapshot`, `history`, `market_overview`,
  `options_sentiment`, and `options_sentiment_rankings` cannot fan out
  unbounded across multiple service instances. The existing transient cooldown
  logic was also upgraded to use the shared cooldown registry, so a provider
  that just rate-limited one caller is now respected by the next caller too,
  instead of only by the same instance. Added
  `MARKET_DATA_MAX_CONCURRENT_REQUESTS` in
  `backend/app/core/config.py` and `backend/.env.example`.
  Surfaced market-data runtime status in the operator work-queue summary via
  `backend/app/domains/market/schemas.py`,
  `backend/app/domains/market/services.py`, and
  `backend/app/frontend/app.js`, so the runtime card now shows market-data
  gates and active cooldowns alongside calendar and news.
- reason:
  after hardening `calendar` and `news`, the other remaining external
  dependency inside `decision_context` was `market_overview` and related
  market-data reads. Those calls already had fallback/cooldown behavior, but
  they still lacked shared backpressure and operator visibility. This slice
  closes that gap without touching the `skills` lane.
- pending:
  this still needs live measurement during the next due window. The new
  protection reduces accidental provider pressure and makes suppression
  visible, but it does not by itself guarantee lower first-pass wall time if
  `calendar_context` remains the dominant external wait.
- next:
  when `next_reanalysis_at` starts expiring again, rerun `DO` and compare the
  queue runtime summary for all three external families together:
  `market_data`, `calendar`, and `news`. If the first expensive pass is still
  too bursty, the next slice should likely be runtime budgeting or paced due
  draining rather than another increase in concurrency.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/market/schemas.py backend/app/domains/market/services.py backend/tests/test_market_analysis.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q tests/test_market_analysis.py::test_market_data_service_degrades_to_fallback_during_twelve_data_rate_limit tests/test_market_analysis.py::test_market_data_service_degrades_to_fallback_during_ibkr_proxy_cooldown tests/test_market_analysis.py::test_market_data_service_backpressure_limits_concurrent_requests_across_instances tests/test_market_analysis.py::test_market_data_service_shared_cooldown_skips_repeated_failures_across_instances tests/test_market_analysis.py::test_work_queue_summary_exposes_market_data_cooldown_state tests/test_market_analysis.py::test_work_queue_summary_exposes_calendar_cooldown_state tests/test_market_analysis.py::test_work_queue_summary_exposes_news_cooldown_state tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires` from `backend/` -> `8 passed`

### 2026-04-20 11:41 UTC

- changed:
  added runtime budgeting for scheduled watchlist reanalysis in
  `backend/app/domains/learning/services.py`. The `DO` loop now builds a small
  per-cycle budget for expired scheduled reanalysis checks and applies it
  inside `_assess_reanalysis_need` before the expensive
  `snapshot/news/calendar` branch. First reviews, missing-policy items, and
  regime-shift checks still flow normally, but pure scheduled rechecks can now
  be deferred once the cycle budget is exhausted. Deferred items are
  rescheduled a short time into the future with a small spacing offset, using
  the existing `reanalysis_runtime` state instead of re-entering the same
  expired backlog on the very next 5-second cycle. The active watchlist sort
  order was also refined so the loop drains degraded candidate work first,
  then items without runtime history, then the oldest scheduled reanalysis
  deadlines. Added the new settings in `backend/app/core/config.py` and
  `backend/.env.example`:
  `ORCHESTRATOR_SCHEDULED_REANALYSIS_MAX_CHECKS_PER_CYCLE`,
  `ORCHESTRATOR_SCHEDULED_REANALYSIS_BUDGET_SECONDS`,
  `ORCHESTRATOR_SCHEDULED_REANALYSIS_BUDGET_DEFERRAL_SECONDS`,
  `ORCHESTRATOR_SCHEDULED_REANALYSIS_BUDGET_SPACING_SECONDS`.
  The `DO` response metrics now include
  `runtime_budget_deferred_entries`,
  `scheduled_reanalysis_checks_started`, and
  `scheduled_reanalysis_checks_deferred`.
- reason:
  after hardening `market_data`, `calendar`, and `news`, the next risk was not
  only provider pressure but backlog shape: if many `next_reanalysis_at`
  deadlines land together, the loop can still spend an entire cycle walking
  scheduled checks that are not urgent. This slice makes that drainage paced
  instead of bursty, without suppressing genuinely event-driven reanalysis.
- pending:
  this has test coverage but still needs a live rerun against the real due
  window to see how much first-pass wall time it shaves off in practice.
- next:
  rerun `DO` on the active database as the expired `next_reanalysis_at`
  window opens and compare the new `runtime_budget_*` counters plus the work
  queue timing summary. If the wall time is still too lumpy, the next step is
  probably dynamic budgeting based on recent measured gate cost rather than a
  fixed per-cycle cap.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/services.py backend/tests/test_learning_loop.py`
  `pytest -q tests/test_learning_loop.py::test_orchestrator_do_runtime_budget_defers_scheduled_reanalysis_backlog tests/test_learning_loop.py::test_orchestrator_do_skips_tickers_until_reanalysis_trigger_fires tests/test_learning_loop.py::test_assess_reanalysis_need_reschedules_expired_watchlist_item_without_trigger tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` from `backend/` -> `4 passed`

### 2026-04-20 11:47 UTC

- changed:
  added deterministic stagger to scheduled reanalysis timing in
  `backend/app/domains/learning/services.py`. The existing
  `_schedule_watchlist_reanalysis()` path now applies a small stable offset per
  watchlist item before writing `next_reanalysis_at`, so items that share the
  same policy interval no longer collapse onto the exact same due second after
  a broad first pass. The offset is deterministic for a given item, bounded by
  a new setting, and stored in runtime state as
  `schedule_jitter_seconds` together with `base_interval_seconds`, while
  `check_interval_seconds` now reflects the real scheduled delay. Added
  `ORCHESTRATOR_SCHEDULED_REANALYSIS_JITTER_SECONDS` in
  `backend/app/core/config.py` and `backend/.env.example`.
- reason:
  the runtime budget protects the loop once a due wave has already formed, but
  it is better to avoid forming such a sharp wave in the first place. The live
  run showed many watchlist items converging toward nearly the same
  `next_reanalysis_at`; this slice spreads those deadlines earlier in the
  pipeline with no randomness and no change in trigger semantics.
- pending:
  this still needs confirmation against the live database to see how much it
  widens the real due window once the currently scheduled items are refreshed.
- next:
  rerun `DO` during the next real due window and compare whether
  `next_reanalysis_at` values now fan out more naturally, reducing the number
  of items that become due in the same minute before runtime budgeting even
  kicks in.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/services.py backend/tests/test_learning_loop.py`
  `pytest -q tests/test_learning_loop.py::test_schedule_watchlist_reanalysis_applies_deterministic_stagger tests/test_learning_loop.py::test_orchestrator_do_runtime_budget_defers_scheduled_reanalysis_backlog tests/test_learning_loop.py::test_assess_reanalysis_need_reschedules_expired_watchlist_item_without_trigger tests/test_scheduler.py::test_scheduler_reuses_persistent_orchestrator_between_cycles` from `backend/` -> `4 passed`

### 2026-04-20 11:31 UTC

- changed:
  implemented the first explicit `skill gap` slice in the learning loop.
  `backend/app/domains/learning/skills.py` now defines
  `SKILL_GAP_MEMORY_TYPE` plus `SkillGapService`, which detects retrospective
  procedural gaps from `trade_review` evidence and exposes them through the
  skills dashboard and a new `GET /api/v1/skills/gaps` endpoint.
  The first two gap types are deliberately narrow:
  `missing_entry_skill_context` when a trade later needed procedural change
  but its original entry had no routed primary skill, and
  `missing_catalog_skill` when review evidence produces only a
  `draft_candidate_skill` because the current catalog has no matching
  procedure. `backend/app/domains/execution/services.py` now records those
  gaps during review handling, persists them as `memory_type=skill_gap`,
  mirrors them into `skill_gap_detected` journal entries, and attaches them to
  the review lesson payload alongside `skill_candidate` and `knowledge_claim`.
  `backend/app/domains/learning/schemas.py` extends the typed dashboard
  response with `SkillGapRead`, while `backend/app/frontend/index.html` and
  `backend/app/frontend/app.js` now render a `Skill Gaps` operator panel.
  `backend/app/domains/learning/services.py` also adds retention policy for
  the new durable memory type.
- reason:
  the roadmap called for explicit `skill gap detection` before building richer
  workflow automation. Without it, the system could promote lessons and claims
  but still fail to say clearly when the real problem was the absence of a
  usable procedure or the absence of a cataloged skill. This slice makes that
  deficiency first-class and traceable without changing execution behavior.
- pending:
  gap detection is still retrospective and review-driven. It does not yet
  detect operator/manual corrections, complementary-skill absences during
  runtime, or consolidate repeated gaps into a stronger `claim ->
  skill_candidate` promotion path. Gap status is also still one-way (`open`)
  rather than reviewed or resolved.
- next:
  use repeated `skill_gap` evidence and durable `KnowledgeClaim` support to
  implement the formal `claim -> skill_candidate` bridge, so validated claims
  and repeated gaps can promote procedural candidates outside the
  trade-review-only path.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/skills.py backend/app/domains/execution/services.py backend/app/domains/learning/services.py backend/app/domains/learning/schemas.py backend/app/domains/learning/api.py backend/tests/test_skills.py backend/tests/test_learning_loop.py`
  `pytest -q backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields` -> `6 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 11:52 UTC

- changed:
  implemented the formal `claim -> skill_candidate` bridge.
  `backend/app/domains/learning/skills.py` now includes
  `ClaimSkillBridgeService`, which evaluates durable claims, avoids duplicate
  promotions, derives candidate targets from claim metadata or promotion
  traces, creates `memory_type=skill_candidate` items sourced from claims, and
  links the originating claim back to the created candidate. The bridge is
  conservative: it only promotes claims that are at least `supported`,
  non-stale, and actually map to a procedural target, with stricter behavior
  for generic review claims and direct promotion for actionable `context_rule`
  claims that already carry a valid `promotion_trace`.
  `backend/app/domains/learning/claims.py` now stores
  `promotion_trace` inside `context_rule` claim metadata, returns
  `promoted_skill_candidate` from claim reviews, and exposes a new
  `maybe_promote_claim_to_skill_candidate()` path used by both the review API
  and manual operator promotion. `backend/app/domains/learning/api.py` now
  extends `POST /api/v1/claims/{claim_id}/review` with the promoted candidate
  when one is created and adds `POST /api/v1/claims/{claim_id}/promote` for a
  forced manual promotion path. `backend/app/domains/learning/relevance.py`
  now runs the bridge automatically after each generated `context_rule` claim,
  so strong learned context rules can seed procedural candidates without going
  through trade-review heuristics. `backend/app/frontend/app.js` now shows a
  `Promote` action on eligible durable claims and surfaces linked candidate
  ids once a claim has already been bridged.
- reason:
  after `skill_gap` detection, the main missing link in the OpenClaw/Hermes
  style learning loop was a controlled way for durable knowledge to become a
  reusable procedural candidate. Without that bridge, claims, rules and
  reviews could accumulate evidence but still depended too heavily on the
  original trade-review path to create actionable procedural changes.
- pending:
  the bridge now exists, but workflows are still event-driven rather than
  first-class objects. There is not yet a dedicated `stale_claim_review` or
  `weekly_skill_audit` workflow entity coordinating queues, ownership and
  resolution. Claim promotion also still relies on simple eligibility rules,
  not richer contradiction-aware or cadence-aware workflow policies.
- next:
  implement the first explicit learning workflow object, starting with
  `stale_claim_review`, then `weekly_skill_audit`, and use it to manage both
  stale durable claims and unresolved `skill_gap` items through typed,
  operator-visible workflow state.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/skills.py backend/app/domains/learning/claims.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/relevance.py backend/tests/test_knowledge_claims.py backend/tests/test_relevance_engine.py`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields` -> `11 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 12:07 UTC

- changed:
  implemented the first explicit `LearningWorkflow` object and wired the first
  two workflow types into the operator loop. Added
  `backend/app/db/models/learning_workflow.py` plus migration
  `backend/migrations/versions/20260420_0018_learning_workflows.py` with a
  small persistent model for derived-but-stored learning workflow state:
  `workflow_type`, `scope`, status, priority, summary, structured `context`,
  structured `items`, sync timestamps and resolution timestamp.
  `backend/app/domains/learning/workflows.py` now provides
  `LearningWorkflowService`, which synchronizes:
  `stale_claim_review` from the durable claim review queue, and
  `weekly_skill_audit` from unresolved `skill_gap` items plus draft
  `skill_candidate` backlog. `backend/app/domains/learning/api.py`,
  `backend/app/domains/learning/schemas.py` and
  `backend/app/api/v1/routers/learning.py` now expose
  `GET /api/v1/learning-workflows` and `POST /api/v1/learning-workflows/sync`
  with typed workflow items. `backend/app/frontend/index.html` and
  `backend/app/frontend/app.js` now render a `Learning Workflows` panel fed by
  the synchronized API, and `backend/tests/test_learning_workflows.py` covers
  end-to-end workflow creation from stale claims, open skill gaps and draft
  skill candidates.
- reason:
  after adding skills on-demand, durable claims, skill gaps and the
  `claim -> skill_candidate` bridge, the next missing piece from the intended
  OpenClaw/Hermes-style loop was a first-class workflow layer. Without it, the
  system still had queues and heuristics, but no explicit persistent object
  representing learning work that needs review or audit.
- pending:
  these workflows are currently synchronized snapshots, not yet stateful task
  managers. They do not have explicit transitions like `in_progress`,
  `completed_with_actions`, or `dismissed`, and they do not yet record which
  claim/gap actions resolved a workflow item. Sync also currently happens on
  demand from the API rather than through dedicated scheduled workflow jobs.
- next:
  add lightweight workflow actions and transitions so claims reviewed,
  promoted, retired, or gap items addressed can advance workflow state instead
  of only disappearing on the next sync. The first target should be
  `stale_claim_review`, then `weekly_skill_audit`.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/db/models/learning_workflow.py backend/app/db/models/__init__.py backend/app/domains/learning/workflows.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/api/v1/routers/learning.py backend/tests/conftest.py backend/tests/test_learning_workflows.py`
  `pytest -q backend/tests/test_learning_workflows.py backend/tests/test_knowledge_claims.py backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields` -> `12 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 12:18 UTC

- changed:
  turned the new `LearningWorkflow` objects into actionable workflow surfaces
  instead of read-only synced snapshots. `backend/app/domains/learning/workflows.py`
  now supports `apply_action()` with typed actions for:
  `stale_claim_review/claim_review` (`confirm`, `contradict`, `retire`),
  `weekly_skill_audit/skill_gap` (`resolve`, `dismiss`) and
  `weekly_skill_audit/skill_candidate_audit`
  (`paper_approve`, `replay_approve`, `reject`). These actions call the real
  underlying services (`KnowledgeClaimService.review_claim`,
  `SkillGapService.review_gap`, `SkillLifecycleService.validate_candidate`),
  append a persistent `resolution_log` plus action counters into workflow
  context, and then resync the workflow so status can move through
  `open -> in_progress -> resolved` without losing traceability.
  `backend/app/domains/learning/skills.py` now adds `review_gap()` so skill
  gaps can be resolved or dismissed explicitly and mirrored into new journal
  entries. `backend/app/domains/learning/api.py` and
  `backend/app/domains/learning/schemas.py` now expose
  `POST /api/v1/learning-workflows/{workflow_id}/actions` with typed request
  and response payloads. `backend/app/frontend/app.js` now renders workflow
  item action buttons directly inside the `Learning Workflows` panel and calls
  the new workflow-action endpoint with operator summaries. The workflow tests
  now cover both state transitions and action effects end-to-end.
- reason:
  a synchronized workflow object without actions was still too passive: items
  would vanish on the next sync, but the workflow itself would not show that
  concrete review or audit work had happened. This slice makes workflows part
  of the operator loop and gives them visible, persistent forward progress.
- pending:
  workflow sync is still primarily driven by API access rather than a scheduled
  governance lane. Resolution history now exists, but it is still embedded in
  workflow context JSON rather than broken out into dedicated audit records or
  surfaced deeply in the UI. There is also still no ownership, SLA or cadence
  metadata beyond `last_synced_at`.
- next:
  move workflow synchronization and refresh into an explicit scheduled
  governance lane, then enrich workflow audit history/metadata so operators can
  tell not only that an item was resolved, but why, when and by which action
  policy over time.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/workflows.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/skills.py backend/tests/test_learning_workflows.py`
  `pytest -q backend/tests/test_learning_workflows.py backend/tests/test_knowledge_claims.py backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields` -> `14 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 12:39 UTC

- changed:
  moved `LearningWorkflow` maintenance off the dashboard refresh path and into
  a dedicated scheduled governance lane. `backend/app/core/config.py` and
  `backend/.env.example` now define
  `LEARNING_WORKFLOW_GOVERNANCE_ENABLED` plus
  `LEARNING_WORKFLOW_GOVERNANCE_INTERVAL_MINUTES`. In
  `backend/app/domains/learning/workflows.py`,
  `LearningWorkflowService` now exposes `sync_default_workflows_with_report()`,
  which compares pre/post workflow state and produces a compact report of open,
  changed, opened and resolved workflows. `backend/app/domains/system/services.py`
  now schedules a separate APScheduler job
  `learning_workflow_governance_job`, tracks its own runtime state, records
  non-blocking success/failure journal entries
  (`learning_workflow_sync`, `learning_workflow_sync_failed`), and surfaces the
  lane through `scheduler/status` without pausing the trading bot if workflow
  sync fails. `backend/app/domains/system/schemas.py` now exposes a typed
  `learning_governance` block in scheduler status, and
  `backend/app/frontend/app.js` now reads workflows from persisted state by
  default and shows a `Learning loop` metric sourced from that governance lane
  instead of forcing `sync=true` on every dashboard refresh. Tests now cover
  successful and failing governance syncs plus the new default non-syncing
  workflow listing behavior.
- reason:
  workflows were still too dependent on operator/API traffic. Even after adding
  workflow actions, the system did not truly behave like a governed learning
  loop because stale claims and skill audits only refreshed when someone opened
  the dashboard or called the sync endpoint. This slice gives those workflows
  their own cadence and a lightweight audit trail while keeping live runtime
  trading behavior unchanged.
- pending:
  workflow resolution history is still embedded in workflow context and
  sync-level journal entries; it is not yet modeled as richer per-item audit
  records or shown deeply in operator views. Workflow state is still
  coarse-grained (`open/in_progress/resolved`), with no first-class dismissal
  taxonomy or ownership/cadence metadata per item.
- next:
  enrich workflow auditability: add clearer item-resolution classes and more
  explicit operator history so claims, gaps and candidate audits can be traced
  over time without opening raw workflow context JSON.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/core/config.py backend/app/domains/learning/workflows.py backend/app/domains/system/services.py backend/app/domains/system/schemas.py backend/app/domains/learning/api.py backend/app/domains/learning/services.py backend/tests/test_scheduler.py backend/tests/test_learning_workflows.py`
  `pytest -q backend/tests/test_scheduler.py backend/tests/test_learning_workflows.py backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `30 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 12:58 UTC

- changed:
  enriched workflow auditability without adding a new table. In
  `backend/app/domains/learning/workflows.py`, workflow actions now classify
  outcomes explicitly (`claim_confirmed`, `gap_resolved`,
  `candidate_rejected`, etc.), persist them into normalized history entries,
  and also emit dedicated `learning_workflow_action` journal entries so
  operator actions are traceable outside raw workflow JSON. The workflow sync
  path now appends typed `sync` history entries that explain openings,
  resolutions and item deltas (`added_items`, `removed_items`,
  `open_item_count_after`). `backend/app/domains/learning/schemas.py` now
  exposes `LearningWorkflowHistoryEntryRead`, and
  `backend/app/domains/learning/api.py` now serves richer workflow payloads
  plus `GET /api/v1/learning-workflows/{workflow_id}` for deeper inspection.
  `backend/app/frontend/app.js` now renders recent workflow history directly in
  the dashboard cards so operators can see why a workflow changed without
  opening `context.sync_log` or `context.resolution_log` manually. Added
  retention for `learning_workflow_action` journal entries in
  `backend/app/domains/learning/services.py`.
- reason:
  scheduled sync solved freshness, but workflows were still too opaque:
  resolved items disappeared correctly, yet the operator still had to infer why
  from raw JSON blobs. This slice turns that into typed history and reusable
  UI/API primitives.
- pending:
  history now exists, but it is still primarily shown in the workflow cards.
  The same audit trail is not yet threaded into `Ticker Trace`, journal detail
  exploration or deeper operator drill-down between workflow events and the
  underlying claims/gaps/candidates.
- next:
  surface workflow/journal cross-links in the operator console, especially in
  `Ticker Trace` and any future workflow-detail view, so learning governance is
  visible alongside per-ticker decision history.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/workflows.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/services.py backend/tests/test_learning_workflows.py`
  `pytest -q backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `30 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 13:10 UTC

- changed:
  threaded workflow audit events into the operator surfaces that already
  explain ticker and journal history. `backend/app/domains/learning/workflows.py`
  now enriches workflow-action effects with `ticker` and
  `strategy_version_id` when available, and writes those into the
  `learning_workflow_action` journal entries so affected-ticker actions can be
  recovered naturally from existing journal/ticker views. In
  `backend/app/domains/learning/services.py`, `TickerDecisionTraceService`
  now recognizes workflow-linked journal entries, adds workflow tags
  (`workflow:*`, `resolution:*`), and exposes structured workflow detail in the
  trace event payload. `backend/app/frontend/app.js` now surfaces workflow
  pills and resolution metadata in the `Journal` feed and `Ticker Trace`
  summary lines, so operator drill-down no longer stops at the dedicated
  workflow panel. Added regression coverage in
  `backend/tests/test_ticker_trace.py` to prove that a workflow action against
  a ticker-linked claim appears in that ticker's trace.
- reason:
  workflow history had become readable in its own panel, but it still lived in
  a separate operational lane. This slice makes governance actions visible in
  the two places where an operator is most likely to investigate behavior:
  ticker-level trace and the main journal feed.
- pending:
  there is still no first-class detail screen that links directly from a trace
  event or journal entry to the underlying claim/gap/candidate/workflow object.
  Resolution taxonomy is clearer now, but still intentionally light.
- next:
  if deeper operator drill-down is still needed, add linked entity detail views
  or richer event drawers instead of expanding dashboard cards indefinitely.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/workflows.py backend/app/domains/learning/services.py backend/tests/test_ticker_trace.py`
  `pytest -q backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `32 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 13:24 UTC

- changed:
  added linked entity drill-down for the learning-governance layer. In
  `backend/app/domains/learning/skills.py` and
  `backend/app/domains/learning/api.py`, the API now exposes single-item
  detail reads for `skill_gap` and `skill_candidate`
  (`GET /api/v1/skills/gaps/{gap_id}`, `GET /api/v1/skills/candidates/{candidate_id}`),
  complementing the existing claim/workflow detail endpoints. In
  `backend/app/domains/learning/workflows.py`, workflow actions now carry
  `ticker` and `strategy_version_id` into their journal effects when available.
  `backend/app/domains/learning/services.py` now threads those workflow action
  links through `Ticker Trace` event details. On the frontend,
  `backend/app/frontend/index.html` and `backend/app/frontend/app.js` now add a
  dedicated `Learning Detail` panel plus reusable `Open` actions from
  workflows, journal entries and ticker-trace events into the linked entity:
  workflow, claim, skill gap or skill candidate. The panel auto-refreshes after
  dashboard actions so the operator does not stare at stale detail payloads.
  Added endpoint/detail regression coverage in `backend/tests/test_skills.py`
  and richer ticker-trace coverage in `backend/tests/test_ticker_trace.py`.
- reason:
  pills and summaries made governance visible, but the operator still needed to
  mentally join multiple feeds. This slice adds a lightweight but explicit
  drill-down path so evidence, workflow state and promoted entities can be
  inspected from the same console without opening raw JSON or chasing IDs by
  hand.
- pending:
  the current detail view is still a shared panel rather than a richer drawer
  or modal, and it focuses on linked entities rather than deep journal-event
  inspection. Resolution taxonomy also remains intentionally conservative.
- next:
  if deeper operator UX is still needed, evolve `Learning Detail` into richer
  drawers/entity pages instead of adding more summary rows to the main
  dashboard.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/app/domains/learning/services.py backend/tests/test_skills.py backend/tests/test_ticker_trace.py`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `33 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 13:38 UTC

- changed:
  turned `Learning Detail` from a read-only drill-down panel into an
  operational surface. In `backend/app/domains/learning/api.py`, the
  `skill_gap` lane now exposes `POST /api/v1/skills/gaps/{gap_id}/review`,
  backed by the typed `SkillGapReviewRequest` schema in
  `backend/app/domains/learning/schemas.py`. In
  `backend/app/frontend/app.js`, the shared learning-operator wiring now
  attaches not only `Open` links but also direct actions for claims, skill
  gaps, skill candidates and workflow items. The detail renderers now show
  inline controls to confirm/contradict/retire claims, promote eligible
  claims, resolve/dismiss gaps, validate/reject skill candidates, and act on
  workflow items without forcing the operator back to the summary cards.
  `workflow` detail items now also expose the same item actions as the main
  workflow cards. Added regression coverage in `backend/tests/test_skills.py`
  for the new gap-review endpoint.
- reason:
  linked drill-down was useful for reading the provenance chain, but it still
  forced operators to switch back to the dashboard summaries for every action.
  This slice closes that UX gap so the learning-governance entities can be
  inspected and acted on from the same panel.
- pending:
  the detail panel is now actionable, but it still uses `prompt()` for review
  summaries and remains embedded in the main dashboard rather than a richer
  drawer or dedicated entity page. The action UX is functional, not polished.
- next:
  if governance UX still needs work, replace the `prompt()` flow with inline
  review forms or drawers and add richer provenance views for
  `claim -> gap/candidate -> workflow -> ticker decision`.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/tests/test_skills.py`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `34 passed`
  `node --check backend/app/frontend/app.js`

### 2026-04-20 13:52 UTC

- changed:
  replaced the most disruptive governance `prompt()` loop in `Learning Detail`
  with an inline review composer. In `backend/app/frontend/app.js`, the detail
  panel now maintains a persistent `draft` state for governance actions and can
  open a structured inline composer for claim reviews, skill-gap reviews and
  workflow item actions. The underlying action helpers
  (`reviewClaim`, `reviewSkillGap`, `applyWorkflowAction`) now accept explicit
  summaries programmatically and only fall back to `prompt()` outside the
  detail-panel flow. The detail renderers for claims, gaps and workflow items
  now route those actions through inline review buttons, while
  `backend/app/frontend/styles.css` adds lightweight form styling so the
  composer reads as part of the dashboard rather than a raw text area.
- reason:
  `Learning Detail` had become operational, but the review UX still depended on
  blocking browser prompts. That broke operator flow and made the new detail
  panel feel like a thin wrapper over the old interaction model instead of a
  real governance surface.
- pending:
  summary capture is now inline in the detail panel, but the surrounding cards
  outside that panel still use `prompt()` as fallback. The UI also still uses a
  shared embedded panel rather than drawers or modal entity pages.
- next:
  if operator UX needs another pass, migrate the remaining non-detail actions
  off `prompt()`, add richer provenance around the draft composer, and
  potentially move `Learning Detail` to drawers or split entity pages.
- blockers:
  none.
- verification:
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `34 passed`

### 2026-04-20 14:03 UTC

- changed:
  unified the dashboard governance actions around the `Learning Detail` review
  composer instead of keeping separate prompt-driven paths. In
  `backend/app/frontend/app.js`, `claim-review-button`,
  `workflow-action-button` and `skill-gap-review-button` now open the linked
  detail entity with a preloaded review draft instead of immediately calling
  the fallback action helpers. `loadLearningDetail()` now accepts a draft
  override, so the detail panel can be opened directly into a specific review
  action. `Claim Review Queue` now delegates to the same shared learning-panel
  wiring instead of maintaining its own bespoke click handler. The old
  `prompt()` path remains only as a fallback inside the action helpers
  themselves, which means the primary operator workflow is now consistently
  routed through the embedded review composer.
- reason:
  the previous slice improved `Learning Detail`, but the main dashboard cards
  were still bypassing that surface and dropping back to browser prompts. That
  left the governance UX split in two. This slice makes `Learning Detail` the
  canonical action lane for claims, workflow items and skill-gap review.
- pending:
  prompt fallbacks still exist in the low-level action helpers, and candidate
  validation still executes immediately because it does not require a summary.
  The dashboard still uses a shared embedded panel rather than drawers or
  dedicated entity pages.
- next:
  if the governance UX needs another pass, remove the remaining prompt
  fallbacks entirely, add richer inline validation context for skill
  candidates, and consider migrating the learning detail lane into drawers or
  entity-specific views.
- blockers:
  none.
- verification:
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `34 passed`

### 2026-04-20 14:15 UTC

- changed:
  removed the last prompt-driven governance path from the frontend and folded
  `skill_candidate` validation into the same inline review lane. In
  `backend/app/frontend/app.js`, candidate validation buttons from both the
  dashboard cards and `Learning Detail` now open the linked detail panel with a
  preloaded `skill_candidate_validation` draft instead of validating
  immediately. `validateSkillCandidate()` now accepts an explicit summary and
  no longer depends on browser prompts. The other governance helpers
  (`reviewClaim`, `reviewSkillGap`, `applyWorkflowAction`) also no longer fall
  back to prompts; they require an explicit summary passed by the inline
  composer. The shared composer now supports candidate validation, custom
  placeholders and unified submit/cancel behavior across claims, gaps,
  workflow items and skill candidates.
- reason:
  after the previous slice, the primary governance path had moved into
  `Learning Detail`, but there were still two remaining sources of split
  behavior: residual prompt fallbacks in the low-level helpers and direct
  candidate validation outside the composer. Removing both makes governance
  fully consistent and traceable from one surface.
- pending:
  the flow is now prompt-free, but the detail panel is still a shared embedded
  surface and not yet a richer drawer or entity page. Candidate validation also
  still uses a lightweight summary rather than a deeper evidence form.
- next:
  if we keep pushing this UX, the next step is to enrich candidate validation
  with structured evidence fields and/or move the learning-governance lane into
  drawers or dedicated entity views.
- blockers:
  none.
- verification:
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `34 passed`

### 2026-04-20 14:27 UTC

- changed:
  enriched the `skill_candidate` validation composer with structured evidence
  fields instead of a free-text-only review. In
  `backend/app/frontend/app.js`, the inline governance composer now renders
  dedicated numeric inputs for `sample_size`, `win_rate`, `avg_pnl_pct` and
  `max_drawdown_pct` when the draft is a `skill_candidate_validation`. Those
  values persist in the detail draft state, are prefilled from the latest
  validation metadata when available, and are sent through
  `validateSkillCandidate()` to the existing backend contract. The candidate
  detail view now also surfaces a compact summary of the last validation
  metrics so the operator can see current evidence before approving or
  rejecting a candidate. `backend/app/frontend/styles.css` now styles the
  evidence grid and inputs.
- reason:
  candidate validation had been routed through the unified governance surface,
  but it still behaved like a text-only confirmation step even though the
  backend already supports richer validation metrics. This slice makes the UI
  capture the evidence that actually matters for paper/replay gates.
- pending:
  validation evidence is now structured, but still lightweight. There is no
  deeper evidence object editor yet, no attachment of run IDs or artifact links,
  and no dedicated compare view across candidate validations.
- next:
  if we keep strengthening this lane, the next step is to attach validation
  artifacts and run references directly to candidate approvals, or add
  side-by-side provenance views for claim -> candidate -> revision.
- blockers:
  none.
- verification:
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `34 passed`

### 2026-04-20 14:39 UTC

- changed:
  extended `skill_candidate` validation with explicit artifact/run references in
  the inline governance composer. In `backend/app/frontend/app.js`, the
  candidate-validation draft now captures `run_id`, `artifact_url` and an
  evidence note alongside the validation metrics, persists them in the draft
  state, and sends them through the existing `evidence` payload of
  `validateSkillCandidate()`. The candidate detail panel now surfaces a compact
  summary of the last validation evidence plus a direct artifact link when one
  is available. `backend/app/frontend/styles.css` now spans the validation
  form correctly for those wider reference fields.
- reason:
  the previous slice captured the numeric quality of a validation run, but not
  its provenance. Candidate approval is much more useful when the operator can
  tie it back to a concrete replay/paper run and the artifact that justified
  the decision.
- pending:
  evidence references are now captured, but they are still opaque free-form
  fields inside the existing payload. There is no dedicated selector for known
  run IDs or a richer artifact browser yet.
- next:
  if we continue down this lane, the next step is to integrate real validation
  run identifiers/artifacts more formally into the candidate lifecycle, or add
  a provenance view that links claim -> candidate -> revision -> run artifacts.
- blockers:
  none.
- verification:
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `34 passed`

### 2026-04-20 14:52 UTC

- changed:
  added navigable `skill_revision` detail so validation provenance can be
  inspected as a first-class entity instead of staying embedded inside
  candidate metadata. In `backend/app/domains/learning/skills.py`, the
  lifecycle service now exposes `get_revision()`, and
  `backend/app/domains/learning/api.py` now serves
  `GET /api/v1/skills/revisions/{revision_id}`. In
  `backend/tests/test_skills.py`, coverage now verifies that a validated
  candidate produces a revision detail payload with the expected evidence.
  On the frontend, `backend/app/frontend/app.js` now supports
  `skill_revision` in `Learning Detail`, adds `Open` links from both the
  active revisions list and candidate detail, and renders a dedicated revision
  panel with activation status, validation metrics and evidence/artifact
  references.
- reason:
  candidate detail had become much better, but the provenance chain still
  stopped one level too early. This slice makes the validated revision itself
  inspectable, which is the right anchor for `candidate -> revision -> evidence`.
- pending:
  revisions are now inspectable, but they are still fed by free-form evidence
  payloads and not yet linked to a formal validation-run object. There is still
  no side-by-side provenance view spanning claim, candidate and revision.
- next:
  if we continue deepening provenance, the next step is to add a joined
  provenance view (`claim -> candidate -> revision`) or formal run/artifact
  entities instead of opaque evidence payloads.
- blockers:
  none. A test-only scheduler background job can still emit noisy SQLite errors
  in isolated runs when governance tables are absent, but the learning slice
  itself passes its full regression set.
- verification:
  `python -m py_compile backend/app/domains/learning/api.py backend/app/domains/learning/skills.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `35 passed`

### 2026-04-20 15:07 UTC

- changed:
  added a unified provenance bundle for the learning lane so the operator can
  inspect `claim -> skill_candidate -> skill_revision` as one chain instead of
  hopping manually between entities. In
  `backend/app/domains/learning/schemas.py`, the API now exposes
  `SkillProvenanceRead`. In `backend/app/domains/learning/skills.py`, the
  lifecycle service can now resolve a candidate from its source claim through
  `find_candidate_by_source_claim_id()`. In
  `backend/app/domains/learning/api.py`, new provenance endpoints now serve
  joined views for claims, candidates and revisions:
  `GET /api/v1/claims/{claim_id}/provenance`,
  `GET /api/v1/skills/candidates/{candidate_id}/provenance`, and
  `GET /api/v1/skills/revisions/{revision_id}/provenance`. On the frontend,
  `backend/app/frontend/app.js` now fetches and renders that joined provenance
  inside `Learning Detail`, with direct `Open` actions to move between the
  linked entities and matching coverage in `backend/tests/test_skills.py`.
- reason:
  by this point the system already had claim review, candidate governance and
  revision detail, but the operator still had to reconstruct provenance across
  multiple panels. This slice makes the lineage itself a first-class object and
  closes the loop for the learning-governance UX.
- pending:
  provenance is now joined and navigable, but `run_id` and validation artifacts
  are still metadata inside the revision/candidate evidence rather than formal
  linked entities. There is also still no side-by-side compare view for
  multiple revisions of the same skill.
- next:
  if we keep strengthening provenance, the next step is to formalize
  validation-run/artifact entities or build a richer side-by-side lineage view
  spanning claim, candidate, revision and run evidence.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/api.py backend/app/domains/learning/skills.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `37 passed`

### 2026-04-20 15:28 UTC

- changed:
  formalized skill validation evidence as a first-class entity instead of
  keeping `run_id` and artifact references only inside opaque metadata. Added
  `SkillValidationRecord` in
  `backend/app/db/models/skill_validation.py` plus migration
  `20260420_0019_skill_validation_records.py`. In
  `backend/app/domains/learning/skills.py`, `validate_candidate()` now creates
  a structured validation record for every candidate review, links it back into
  the candidate (`latest_validation_record_id`) and revision
  (`validation_record_id`), and includes it in the validation result payload.
  In `backend/app/domains/learning/api.py`, the new endpoint
  `GET /api/v1/skills/validations/{validation_id}` exposes that entity. On the
  frontend, `backend/app/frontend/app.js` now supports `skill_validation` in
  `Learning Detail`, with direct navigation from both candidate and revision
  detail.
- reason:
  the previous slices already captured validation evidence, but it still lived
  inside JSON blobs. That made provenance better than before but still too
  implicit. A dedicated validation entity is the right bridge between
  `candidate -> revision` and future `run/artifact` integration.
- pending:
  validation records are now first-class, but `run_id` and artifact references
  are still external strings rather than linked domain entities. There is still
  no browser or selector for known validation runs/artifacts.
- next:
  if we continue down this path, the next step is to connect
  `SkillValidationRecord` to real replay/backtest run objects when they exist,
  or enrich `Learning Detail` with side-by-side compare across multiple
  validation records for the same skill.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/db/models/skill_validation.py backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `38 passed`

### 2026-04-20 15:41 UTC

- changed:
  turned `SkillValidationRecord` into a comparable history surface instead of a
  detail-only entity. In `backend/app/domains/learning/skills.py`, the
  lifecycle service now exposes `list_validation_records()` and enriches each
  validation record with derived `skill_code`, `ticker` and
  `strategy_version_id` from its linked candidate/revision. In
  `backend/app/domains/learning/api.py`, `GET /api/v1/skills/validations`
  now supports filtering by `candidate_id`, `revision_id` and `skill_code`.
  In `backend/app/frontend/app.js`, `Learning Detail` for both
  `skill_candidate` and `skill_revision` now loads validation history and
  renders compact comparison panels with direct links back to individual
  validation records.
- reason:
  once validation records became first-class entities, the next bottleneck was
  that they were still inspectable only one by one. Operators need to compare
  repeated validations for the same candidate or skill to judge whether a
  revision is stabilizing or drifting.
- pending:
  validation history is now queryable and visible, but there is still no
  side-by-side metric diff, trend chart or linkage to real replay/backtest run
  entities. The compare view is still a compact list, not a dedicated analysis
  surface.
- next:
  if we continue deepening this lane, the next step is either a richer compare
  view across multiple validation records, or formal links from validations to
  real run/artifact entities once those exist.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/schemas.py backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `39 passed`

### 2026-04-20 15:53 UTC

- changed:
  added aggregated validation summaries and latest-vs-previous trend signals on
  top of the raw validation history. In
  `backend/app/domains/learning/skills.py`, the lifecycle service now exposes
  `summarize_validation_records()`, which aggregates repeated validations by
  candidate/revision/skill and computes approval counts, average metrics and
  deltas between the latest and previous validation. In
  `backend/app/domains/learning/api.py`, `GET /api/v1/skills/validations/summary`
  exposes that summary. In `backend/app/frontend/app.js`, `Learning Detail` for
  both `skill_candidate` and `skill_revision` now loads and renders summary
  panels before the history list, so the operator can quickly see whether a
  skill is improving, flat or degrading without manually comparing individual
  records.
- reason:
  a flat history list was already better than opaque metadata, but it still
  required manual comparison. The learning/governance loop needs a fast
  “state of evidence” view, not only drill-down.
- pending:
  the compare surface is still summary-first and list-based. There is no
  dedicated trend chart, no side-by-side diff view, and no linkage yet to
  external replay/backtest run entities.
- next:
  if we continue deepening this lane, the next step is either a richer compare
  surface across multiple validations of the same skill, or formal links from
  summaries/records to real run artifacts when the external backtesting service
  exists.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/schemas.py backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `39 passed`

### 2026-04-20 16:04 UTC

- changed:
  added an explicit validation compare surface instead of relying only on
  summary + history. In `backend/app/domains/learning/skills.py`, the
  lifecycle service now exposes `compare_validation_records()`, which returns
  comparable rows anchored to a baseline validation and computes per-row deltas
  versus that baseline. In `backend/app/domains/learning/api.py`,
  `GET /api/v1/skills/validations/compare` now exposes that structure. In
  `backend/app/frontend/app.js`, `Learning Detail` for both candidates and
  revisions now loads and renders a `Validation Compare` panel with direct
  links back to the underlying validation records.
- reason:
  the previous slice made validation history readable, but not truly
  comparable. Operators still had to compare metrics by eye across separate
  rows. This slice turns that into an explicit compare surface with visible
  deltas against a chosen baseline.
- pending:
  compare is now explicit, but still list-based and baseline-relative. There is
  no charted trend line, no alternative baseline selection, and no side-by-side
  deep diff for evidence payloads.
- next:
  if we continue down this lane, the next step is either a richer compare UI
  with selectable baselines/trend charts, or linking compare rows to real
  replay/backtest run entities once those exist.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/schemas.py backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `39 passed`

### 2026-04-20 16:16 UTC

- changed:
  added selectable baselines for validation comparison. In
  `backend/app/domains/learning/skills.py`, `compare_validation_records()`
  now accepts `baseline_validation_id`, reorders the compare rows around the
  selected baseline, and reports whether a custom baseline is active. In
  `backend/app/domains/learning/api.py`,
  `GET /api/v1/skills/validations/compare` now accepts
  `baseline_validation_id`. On the frontend,
  `backend/app/frontend/app.js` now preserves per-detail compare baselines in
  `state.learningDetail.compareBaselines`, reloads compare panels with the
  chosen baseline, and exposes `Use as base` / `Reset` actions directly inside
  the compare surface.
- reason:
  the compare view was already explicit, but still anchored to the latest
  validation. That is useful by default, but not enough when the operator wants
  to compare a current revision against an older but more trustworthy run.
- pending:
  baseline selection is now operator-driven, but still session-local UI state.
  It is not persisted, not shareable across sessions, and there is still no
  visual charting layer for compare trajectories.
- next:
  if we keep improving this lane, the next step is either persistent compare
  presets/baselines, or a richer trend visualization on top of these compare
  rows.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/schemas.py backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `39 passed`

### 2026-04-20 16:24 UTC

- changed:
  persisted compare baselines locally in the operator UI so they survive page
  reloads and reopening the same learning entity. In
  `backend/app/frontend/app.js`, the learning detail state now carries an
  explicit `learningCompareBaselineStore`, backed by `localStorage` under
  `trading-research.learning-compare-baselines.v1`. Baselines are restored
  per-entity (`entityType:id`) when a learning detail panel is reopened, and
  updates/resets from the compare UI now persist automatically.
- reason:
  the previous slice made baselines selectable, but they were session-local UI
  state. That made the feature useful for a quick inspection, but weak as an
  operator workflow because every reload lost the chosen reference point.
- pending:
  baseline persistence is now durable in the browser, but still local to one
  browser/device. It is not synced through backend state and cannot yet be
  shared between operators or surfaced in journal/workflow context.
- next:
  if we keep going down this lane, the next step is either backend-persisted
  compare presets or a richer charting layer built on top of the now-stable
  baseline selection.
- blockers:
  none.
- verification:
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `39 passed`

### 2026-04-20 18:02 UTC

- changed:
  refreshed the OpenClaw/Hermes roadmap status in this journal so it matches
  the codebase that exists today, not the older planning snapshot. The
  roadmap recorded earlier at `2026-04-20 16:52 UTC` is now effectively in a
  different state:
  `learning workflows` are implemented,
  `skill-gap detection` is implemented,
  the formal `claim -> skill_candidate` bridge is implemented, and
  `cognitive-budget` work is partially implemented through bounded runtime
  loading of skills and claims plus prompt-level traceability. The main
  remaining roadmap items are now:
  `cognitive-budget observability` at operator level,
  more `portable skill artifacts`,
  `memory distillation/compaction`,
  and a stronger `operator disagreement` capture path as structured evidence.
- reason:
  the repo has moved far enough since the original roadmap note that leaving
  it unqualified would now be misleading. The useful next steps are no longer
  the early OpenClaw/Hermes primitives, but the pieces that still deepen
  runtime cognition, portability and memory hygiene.
- pending:
  there is still no explicit operator-facing context-budget dashboard showing
  what was loaded vs dropped, no portable `SKILL.md`-style serialized skill
  artifacts, no systematic memory distillation/compaction pass, and no strong
  first-class capture of `operator disagreement` as reusable evidence.
- next:
  focus the next OpenClaw/Hermes slice on one of:
  `context-budget observability`,
  `portable skill artifacts`,
  `memory distillation`,
  or `operator disagreement capture`,
  instead of revisiting already-implemented workflow/skill-gap/claim-bridge
  foundations.
- blockers:
  none.
- verification:
  documentation update only; no runtime code or tests changed in this slice.

### 2026-04-20 18:26 UTC

- changed:
  implemented the operator-facing slice of `cognitive-budget observability`.
  The AI runtime now builds structured selection summaries for both
  `runtime_skills` and `runtime_claims`, including:
  available count,
  loaded count,
  truncated count,
  active limits,
  loaded ids/codes,
  and a small measure of how much procedural/evidence content actually entered
  the prompt.
  In `backend/app/domains/learning/skills.py` and
  `backend/app/domains/learning/claims.py`, the runtime builders now expose
  `build_runtime_selection()` so the agent can see what was selected versus
  what was left out by budget. In
  `backend/app/domains/learning/agent.py`, that selection is normalized into a
  richer `context_budget`, then persisted into AI decision journal entries and
  AI memory records. In
  `backend/app/domains/learning/services.py` and
  `backend/app/domains/learning/schemas.py`, `Ticker Trace` now surfaces the
  latest runtime budget counts from AI journal entries. In
  `backend/app/frontend/app.js`, both `Decision Journal` and `Ticker Trace`
  now render concise runtime-budget summaries so the operator can see how many
  skills/claims were available, how many were actually loaded, and whether the
  runtime trimmed context.
- reason:
  bounded runtime loading already existed, but it was still effectively a
  hidden backend concern. That meant we were closer to OpenClaw/Hermes in
  runtime behavior than in operator visibility: the agent knew its prompt
  budget, but the operator could not easily inspect what procedural memory was
  loaded or dropped for a decision.
- pending:
  the budget is now visible in operator surfaces, but it is still mostly a
  per-decision runtime snapshot. There is not yet a dedicated dashboard lane
  for aggregate budget pressure over time, nor a stronger attribution layer
  connecting `loaded vs applied` claims/skills to downstream outcome quality.
- next:
  the next OpenClaw/Hermes slice should move to one of:
  `portable skill artifacts`,
  `memory distillation/compaction`,
  or structured `operator disagreement` capture.
  If we stay on the budget lane, the next increment would be aggregate budget
  telemetry instead of more per-decision UI.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/skills.py backend/app/domains/learning/claims.py backend/app/domains/learning/agent.py backend/app/domains/learning/services.py backend/app/domains/learning/schemas.py backend/tests/test_ai_agent.py backend/tests/test_ticker_trace.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_ai_agent.py backend/tests/test_ticker_trace.py backend/tests/test_learning_workflows.py backend/tests/test_scheduler.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `41 passed`

### 2026-04-20 18:40 UTC

- changed:
  implemented the first structured `operator disagreement` capture path. Added
  `OperatorDisagreementService` in
  `backend/app/domains/learning/operator_feedback.py`, which persists
  disagreement events into both `journal` and `memory` as first-class
  artifacts instead of leaving them implicit inside workflow actions or UI
  clicks. The service is now wired into:
  `KnowledgeClaimService.review_claim()` for `contradict` and `retire`,
  `SkillLifecycleService.review_gap()` for `dismiss`,
  and `SkillLifecycleService.validate_candidate()` for rejected candidates.
  That means claim contradiction/retirement, gap dismissal and candidate
  rejection now all emit explicit `operator_disagreement` evidence with:
  disagreement type,
  entity type/id,
  action,
  ticker / strategy context,
  and detail payload.
  Also added retention rules for this new memory/journal lane in
  `backend/app/domains/learning/services.py`, and extended
  `Ticker Trace` meta rendering so disagreement entries are visible as a
  distinct audit signal rather than just another generic journal note.
- reason:
  this closes an important OpenClaw/Hermes-style gap: the system was already
  good at promoting evidence forward, but it still treated operator rejection
  and contradiction mostly as control flow. For a real learning loop, human
  disagreement needs to become reusable evidence, not just a dismissed UI
  action.
- pending:
  disagreement is now captured structurally, but it is still mostly a passive
  evidence lane. The runtime does not yet retrieve disagreement history as a
  dedicated context source, and the UI does not yet expose a focused operator
  disagreement panel or aggregate disagreement patterns by ticker / skill /
  claim family.
- next:
  continue with one of:
  disagreement aggregation/patterning,
  `portable skill artifacts`,
  or `memory distillation/compaction`.
  If we stay on disagreement, the next useful step is turning repeated
  disagreement into searchable learning patterns rather than isolated events.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/operator_feedback.py backend/app/domains/learning/claims.py backend/app/domains/learning/skills.py backend/app/domains/learning/services.py backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_workflows.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_workflows.py backend/tests/test_ticker_trace.py backend/tests/test_scheduler.py backend/tests/test_ai_agent.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `53 passed`

### 2026-04-20 18:49 UTC

- changed:
  extended the new `operator disagreement` lane from raw event capture into a
  usable operator-facing summary surface. In
  `backend/app/domains/learning/operator_feedback.py`, the disagreement
  service now supports:
  listing recent disagreement records and
  summarizing them by disagreement type, entity type, ticker, and
  `target_skill_code` / `claim_key`.
  This is exposed via new API endpoints in
  `backend/app/domains/learning/api.py`:
  `GET /api/v1/operator-disagreements`
  and
  `GET /api/v1/operator-disagreements/summary`,
  with typed contracts added in
  `backend/app/domains/learning/schemas.py`
  and router registration in
  `backend/app/api/v1/routers/learning.py`.
  In the operator console, a new `Operator Disagreement` panel was added in
  `backend/app/frontend/index.html` and wired in
  `backend/app/frontend/app.js` to show both:
  recent disagreement events and
  lightweight aggregate patterns such as repeated disagreement type or ticker.
- reason:
  after capturing disagreement structurally, the next missing piece was
  discoverability. Without a focused lane, the disagreement evidence existed
  but was still buried across journal/memory. This step makes the signal
  inspectable as a pattern, which is the part that actually helps a learning
  loop improve.
- pending:
  disagreement is now aggregated and visible, but it is still a descriptive
  lane. The system does not yet convert repeated disagreement clusters into
  claims, skill gaps or workflow items automatically, and there is still no
  long-horizon compaction/distillation pass over this evidence.
- next:
  if we stay on this OpenClaw/Hermes lane, the next logical step is one of:
  promote repeated disagreement clusters into learning candidates,
  add disagreement-aware memory distillation,
  or move on to `portable skill artifacts`.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/operator_feedback.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/api/v1/routers/learning.py backend/tests/conftest.py backend/tests/test_knowledge_claims.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_workflows.py backend/tests/test_ticker_trace.py backend/tests/test_scheduler.py backend/tests/test_ai_agent.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `54 passed`

### 2026-04-20 19:00 UTC

- changed:
  promoted the new `operator disagreement` lane from descriptive telemetry into
  a reusable learning source. In
  `backend/app/domains/learning/operator_feedback.py`, the disagreement service
  now supports:
  clustering repeated disagreement events,
  persisting those clusters as first-class `MemoryItem`s of type
  `operator_disagreement_cluster`,
  and promoting a repeated cluster into a durable `KnowledgeClaim` of type
  `operator_disagreement_pattern`.
  The promotion path is now exposed by API in
  `backend/app/domains/learning/api.py`:
  `GET /api/v1/operator-disagreements/clusters`
  and
  `POST /api/v1/operator-disagreements/clusters/{cluster_id}/promote`,
  with typed responses added in
  `backend/app/domains/learning/schemas.py`.
  The operator console now surfaces repeated disagreement clusters inside the
  `Operator Disagreement` panel and allows direct promotion from UI via
  `backend/app/frontend/app.js`.
  This closes the loop from:
  disagreement event
  -> repeated disagreement pattern
  -> durable claim.
- reason:
  the previous slice made disagreement visible, but it was still mostly a
  dashboard/reporting surface. OpenClaw/Hermes are most useful when repeated
  friction becomes structured memory that can influence future behavior. This
  slice turns repeated operator pushback into promotable knowledge instead of
  leaving it as a pile of isolated events.
- pending:
  repeated disagreement can now become a durable claim, but the system still
  does not:
  promote disagreement patterns into `skill_gap` or `skill_candidate`
  automatically,
  distill disagreement history over longer horizons,
  or feed disagreement aggregates back into runtime context selection as a
  first-class source.
- next:
  continue this OpenClaw/Hermes lane with one of:
  disagreement-aware `memory distillation/compaction`,
  promotion from repeated disagreement into `skill_gap`,
  or `portable skill artifacts`.
  If we stay on disagreement, the next useful step is deriving stronger,
  reviewable procedural gaps from repeated disagreement clusters rather than
  stopping at durable claims.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/operator_feedback.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/api/v1/routers/learning.py backend/app/domains/learning/services.py backend/tests/test_knowledge_claims.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_workflows.py backend/tests/test_ticker_trace.py backend/tests/test_scheduler.py backend/tests/test_ai_agent.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `55 passed`

### 2026-04-20 19:09 UTC

- changed:
  extended the `operator disagreement` lane one step further by letting a
  repeated disagreement cluster promote directly into a formal `skill_gap`,
  not just into a durable claim. In
  `backend/app/domains/learning/operator_feedback.py`, disagreement clusters
  now track `promoted_skill_gap_id` and support
  `promote_cluster_to_skill_gap()`, which:
  upserts a `skill_gap` sourced from the disagreement cluster,
  links that gap back to the cluster,
  and journals the promotion as
  `operator_disagreement_cluster_promoted_to_gap`.
  This is exposed via a new API route in
  `backend/app/domains/learning/api.py`:
  `POST /api/v1/operator-disagreements/clusters/{cluster_id}/promote-gap`,
  with typed response contracts in
  `backend/app/domains/learning/schemas.py`.
  The operator console now shows both promotion paths on repeated clusters:
  `Promote Claim`
  and
  `Promote Gap`,
  plus a visible pill when a cluster already has a linked gap.
- reason:
  repeated disagreement should not stop at “durable belief under tension”.
  In many cases the more useful operational outcome is a reviewable procedural
  gap. This slice converts persistent operator friction into something the
  existing governance lane already knows how to inspect, resolve or dismiss.
- pending:
  repeated disagreement can now become:
  a durable claim and/or
  a formal skill gap.
  What is still missing is a richer distillation step that can summarize or
  merge these disagreement-derived gaps over longer horizons, and a more
  explicit bridge from these gaps into candidate skill revisions when the
  pattern is procedurally stable enough.
- next:
  continue with one of:
  `memory distillation/compaction` over disagreement-derived evidence,
  richer promotion from disagreement-driven `skill_gap` into
  `skill_candidate`,
  or `portable skill artifacts`.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/operator_feedback.py backend/app/domains/learning/api.py backend/app/domains/learning/schemas.py backend/app/domains/learning/services.py backend/tests/test_knowledge_claims.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_knowledge_claims.py backend/tests/test_skills.py backend/tests/test_learning_workflows.py backend/tests/test_ticker_trace.py backend/tests/test_scheduler.py backend/tests/test_ai_agent.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `56 passed`

### 2026-04-20 19:18 UTC

- changed:
  extended the procedural bridge one level further so a formal `skill_gap` can
  now promote into a `skill_candidate`. In
  `backend/app/domains/learning/skills.py`, `SkillGapService` now supports
  `promote_gap_to_candidate()`, which:
  creates or reuses a candidate sourced from the gap,
  links the gap to the candidate through `linked_skill_candidate_id`,
  and journals the promotion as `skill_candidate_from_gap`.
  This is exposed through a new API route in
  `backend/app/domains/learning/api.py`:
  `POST /api/v1/skills/gaps/{gap_id}/promote`.
  The operator console now surfaces `Promote` actions directly on skill gap
  cards and in `Learning Detail` via
  `backend/app/frontend/app.js`, so the chain:
  repeated operator disagreement
  -> disagreement cluster
  -> skill gap
  -> skill candidate
  can now be driven without dropping out of the existing governance lane.
- reason:
  the previous slice converted repeated disagreement into a reviewable gap, but
  the learning loop still stopped one step before procedural experimentation.
  This closes that gap and makes the disagreement-driven branch converge back
  into the same candidate validation system used by the rest of the bot.
- pending:
  disagreement-derived gaps can now become candidates, but there is still no
  compaction/distillation pass that merges repeated disagreement-derived claims,
  gaps and candidates over longer horizons. Portable skill artifacts are also
  still pending.
- next:
  continue with one of:
  `memory distillation/compaction`,
  disagreement-aware candidate aggregation/merge,
  or `portable skill artifacts`.
- blockers:
  none.
- verification:
  `python -m py_compile backend/app/domains/learning/skills.py backend/app/domains/learning/api.py backend/tests/test_skills.py`
  `node --check backend/app/frontend/app.js`
  `pytest -q backend/tests/test_skills.py backend/tests/test_knowledge_claims.py backend/tests/test_learning_workflows.py backend/tests/test_ticker_trace.py backend/tests/test_scheduler.py backend/tests/test_ai_agent.py backend/tests/test_learning_loop.py::test_trade_review_supports_structured_learning_fields backend/tests/test_relevance_engine.py::test_check_phase_generates_positive_strategy_context_rules` -> `57 passed`

### 2026-04-21 00:05 UTC

- changed:
  paused the OpenClaw/Hermes implementation lane at the point where the
  disagreement-driven branch already supports:
  `operator_disagreement`
  -> `operator_disagreement_cluster`
  -> `skill_gap`
  -> `skill_candidate`.
  No new runtime behavior was added in this slice; this entry records the
  current stopping point and the agreed next backlog item.
- reason:
  the current chain is coherent enough to checkpoint and publish. The next work
  should not be another small bridge in the same lane, but a broader
  distillation step so the learning system does not keep growing only by adding
  more entities.
- pending:
  keep as next pending tasks:
  `memory distillation/compaction`,
  disagreement-aware merge/aggregation of candidates,
  and later `portable skill artifacts`.
- next:
  resume from `memory distillation/compaction` unless a higher-priority runtime
  issue appears first.
- blockers:
  none.
