# Trading Research Backend MVP

## Scope

This backend is the initial modular monolith for the single-agent trading research platform.

## API organization

- `app/api/v1/router.py` now acts as a thin root assembler.
- `app/api/v1/routers/system.py` groups platform-facing endpoints such as health, bootstrap, and scheduler.
- `app/api/v1/routers/strategy.py` groups strategy lifecycle, screeners, watchlists, and strategy evolution endpoints.
- `app/api/v1/routers/market.py` groups analysis, market data, signals, research tasks, and work queue endpoints.
- `app/api/v1/routers/execution.py` groups positions, exits, and trade review execution endpoints.
- `app/api/v1/routers/learning.py` groups journal, memory, failure analysis, auto-reviews, PDCA, and orchestrator endpoints.
- `app/domains/strategy/` is the first extracted domain package and now owns the API assembly plus the primary schemas, repositories, and services for strategy, screener, watchlist, strategy health, strategy evolution, and strategy lab.
- `app/domains/market/` now owns the API assembly plus the primary schemas, repositories, and services for analysis, market data, signals, research tasks, and work queue.
- `app/domains/market/analysis.py` now owns fused analysis, quant analysis, visual analysis, chart rendering, and related market-analysis orchestration logic.
- `app/domains/market/discovery.py` now owns autonomous opportunity discovery and watchlist refresh logic.
- `app/domains/learning/` now owns the API assembly plus the primary schemas, repositories, and services for journal, memory, failure patterns, auto-reviews, PDCA, and orchestration.
- `app/domains/execution/` now owns the API assembly plus the primary schemas, repositories, and services for positions, exits, and trade reviews.
- `app/domains/system/` now owns the API assembly plus the primary schemas, runtime, and services for health, bootstrap, seeding, and scheduler status.
- The legacy compatibility bridge packages under `app/services/`, `app/schemas/`, and `app/db/repositories/` have been removed.
- The former `app/api/v1/routes/` compatibility layer has been removed; domain routers now own the HTTP entrypoints directly.

## Included in this bootstrap

- FastAPI application bootstrap
- SQLAlchemy models for strategies, screener versioning, and PDCA cycles
- Service and repository layers for the first core modules
- Initial REST API for strategies, screeners, and PDCA lifecycle
- Minimal orchestrator endpoint aligned with the single-agent architecture
- Watchlists, analysis runs, trade ledger entries, journal entries, and persistent memory modules
- Alembic configuration plus initial migration
- Minimal `PLAN`, `DO`, and `CHECK` orchestration over persisted system state
- Startup decoupled from `create_all`; schema lifecycle now belongs to migrations
- Stub market data provider plus quant-only MVP execution flow from watchlists to paper positions
- Seed bootstrap for strategies, screeners and watchlists
- Basic APScheduler integration for PLAN/DO/CHECK windows
- Post-trade reviews with lessons learned and proposed strategy changes
- Automatic draft review generation for closed losing trades
- Autonomous strategy evolution and activation events triggered from trade reviews
- Proactive strategy variants from repeated successful patterns during ACT
- Automatic exit evaluation for open trades based on stop, target and trend deterioration
- Real market data support via Twelve Data with fallback to stub provider

## Immediate next steps

1. Introduce richer validation and stricter domain-level business rules.
2. Expand integration coverage around orchestrator flows and candidate lifecycle transitions.
3. Add more market data provider adapters and execution-path realism.
4. Keep consolidating domain boundaries where orchestration and strategy lifecycle still intersect too much.

## MVP bootstrap

1. Run Alembic migrations.
2. Call `POST /api/v1/bootstrap/seed` once.
3. Inspect `GET /api/v1/scheduler/status`.
4. Run `POST /api/v1/orchestrator/plan`, `POST /api/v1/orchestrator/do`, `POST /api/v1/orchestrator/check`, and `POST /api/v1/orchestrator/act`.

## Current MVP focus

- The system tracks trades as discrete entries with `entry_price`, `exit_price`, `pnl_pct`, and drawdown fields.
- Open trades can be closed autonomously through `POST /api/v1/exits/evaluate` or during the DO phase.
- Closed losing trades should be reviewed through `POST /api/v1/trade-reviews/positions/{position_id}`.
- Closed losing trades can also generate a first review draft automatically through `POST /api/v1/auto-reviews/losses/generate`.
- Reviews write lessons learned into journal and persistent memory so future strategy revisions can use them.
- When a review marks `should_modify_strategy=true`, the system automatically creates and activates a refined strategy version.
- The ACT phase can also create and activate proactive variants from repeated winning patterns through `POST /api/v1/strategy-lab/evolve-success-patterns`.
