# External Backtesting Service

## Goal

This document defines the preferred architecture for a dedicated
`research/backtesting` service that lives outside the main trading bot
repository.

The bot in this repo should remain the `trading brain`:

- hypothesis generation
- research orchestration
- skill/rule promotion control
- journal and memory
- runtime decisioning

The external service should own:

- historical experiment execution
- reproducible backtest runs
- experiment artifacts
- dataset/version traceability

## Why a separate repository

Backtesting tends to grow faster than the rest of the trading brain:

- heavier dependencies
- longer-running jobs
- dataset management
- artifact storage
- more experimentation APIs
- potentially different deployment and scaling needs

Keeping it separate provides cleaner boundaries:

- this repo stays smaller and easier to reason about
- the backtesting engine can evolve independently
- other apps can reuse the same service
- failure or maintenance of the backtesting stack does not block the trading
  runtime

## Design principles

- Broker-independent
- Declarative API, not arbitrary remote code execution
- Reproducible runs
- Versioned specs and datasets
- Async job model
- Narrow v1 focused on `1D OHLCV`
- Compatible with future external engines/adapters

## Recommended v1 scope

The first version should support:

- US equities
- daily OHLCV bars
- long-biased setups first
- basic transaction cost model
- `in-sample` and `out-of-sample`
- simple walk-forward later
- persisted trades, equity curve, and metrics

The first version should not try to solve:

- order book simulation
- intraday microstructure
- high-frequency execution
- broad portfolio optimization
- arbitrary user code execution
- broker live-trading orchestration

## Repository structure

Suggested structure for the new repository:

```text
backtesting-service/
  README.md
  pyproject.toml
  app/
    main.py
    api/
      v1/
        router.py
        routers/
          system.py
          backtests.py
          datasets.py
    core/
      config.py
      logging.py
    db/
      base.py
      session.py
      models/
        backtest_run.py
        backtest_artifact.py
        dataset_snapshot.py
    domains/
      backtests/
        api.py
        schemas.py
        services.py
        repositories.py
      datasets/
        api.py
        schemas.py
        services.py
      engines/
        native_daily_ohlcv_replay.py
        base.py
      workers/
        queue.py
        runner.py
    providers/
      market_data/
        base.py
        parquet_store.py
        s3_store.py
  migrations/
  tests/
```

## Engine decision

The preferred first engine is a native one:

- `native_daily_ohlcv_replay`

It should reuse the same kind of assumptions the bot already uses:

- daily bars
- simple signal/setup conditions
- stop/target/trailing logic
- slippage and commission assumptions
- position sizing inputs supplied by spec

This does not prevent later adapters such as:

- `LEAN`
- other internal engines

But the v1 should avoid embedding `Backtesting.py`, `Backtrader`, or `vectorbt`
as a structural dependency of the service core.

## API surface

Recommended base path:

- `/api/v1`

Recommended endpoints:

- `GET /health`
- `GET /capabilities`
- `POST /api/v1/backtests`
- `GET /api/v1/backtests/{run_id}`
- `GET /api/v1/backtests/{run_id}/metrics`
- `GET /api/v1/backtests/{run_id}/trades`
- `GET /api/v1/backtests/{run_id}/equity`
- `GET /api/v1/backtests/{run_id}/artifacts`
- `POST /api/v1/backtests/{run_id}/cancel`
- `POST /api/v1/backtests/{run_id}/rerun`

Optional later:

- `POST /api/v1/backtests/compare`
- `GET /api/v1/datasets`
- `POST /api/v1/datasets/refresh`

## Authentication

Keep v1 simple:

- `Authorization: Bearer <token>`
- service-specific API key
- IP restriction optional at infrastructure level

## Run lifecycle

Suggested states:

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`

Suggested fields on every run:

- `run_id`
- `status`
- `engine`
- `spec_version`
- `dataset_version`
- `submitted_at`
- `started_at`
- `completed_at`
- `requester`
- `source_app`
- `error_code`
- `error_message`

## BacktestSpec contract

The spec should be declarative and versioned.

Top-level fields:

- `spec_version`
- `name`
- `description`
- `source`
- `target`
- `universe`
- `data`
- `strategy`
- `execution_model`
- `validation_plan`
- `outputs`
- `metadata`

### `source`

Who requested the run and why.

- `requested_by`
- `source_app`
- `reason`
- `linked_entity_type`
- `linked_entity_id`

### `target`

What is being validated.

- `type`
  - `hypothesis`
  - `strategy_version`
  - `setup`
  - `skill_candidate`
  - `research_task`
- `code`
- `version`

### `universe`

- `market`
- `tickers`
- `universe_code`
- `filters`

### `data`

- `provider`
- `dataset_version`
- `timeframe`
- `adjust_prices`
- `corporate_actions_mode`
- `start_date`
- `end_date`
- `warmup_bars`

### `strategy`

This section should remain declarative.

- `playbook`
- `bias`
- `setup_code`
- `signal_codes`
- `entry_rules`
- `exit_rules`
- `risk_rules`
- `parameters`
- `context_requirements`

### `execution_model`

- `initial_cash`
- `position_sizing_mode`
- `max_positions`
- `commission_bps`
- `slippage_bps`
- `allow_partial_exits`
- `allow_reentry`

### `validation_plan`

- `mode`
  - `single_split`
  - `walk_forward`
- `in_sample`
- `out_of_sample`
- `min_trade_count`
- `min_distinct_tickers`
- `success_thresholds`

### `outputs`

- `include_trades`
- `include_equity_curve`
- `include_daily_stats`
- `include_artifacts`

### `metadata`

- `tags`
- `notes`
- `trace_id`

## Example BacktestSpec

See [backtest_spec.example.json](/workspaces/trading-research-app/backend/docs/examples/backtest_spec.example.json).

## Response shape

`POST /api/v1/backtests`:

```json
{
  "run_id": "bt_20260420_000123",
  "status": "queued",
  "engine": "native_daily_ohlcv_replay",
  "spec_version": "backtest_spec_v1",
  "submitted_at": "2026-04-20T12:00:00Z"
}
```

`GET /api/v1/backtests/{run_id}`:

```json
{
  "run_id": "bt_20260420_000123",
  "status": "succeeded",
  "engine": "native_daily_ohlcv_replay",
  "dataset_version": "us_equities_eod_2026-04-19",
  "summary": {
    "trade_count": 48,
    "win_rate": 56.25,
    "expectancy_pct": 1.12,
    "profit_factor": 1.48,
    "max_drawdown_pct": -7.34,
    "return_pct": 18.9,
    "out_of_sample_return_pct": 6.2
  },
  "artifacts": {
    "metrics_url": "/api/v1/backtests/bt_20260420_000123/metrics",
    "trades_url": "/api/v1/backtests/bt_20260420_000123/trades",
    "equity_url": "/api/v1/backtests/bt_20260420_000123/equity"
  }
}
```

## Integration back into this bot

This repository should later add a small provider layer, for example:

```text
backend/app/providers/backtesting/
  base.py
  remote_service.py
```

Suggested contract:

- `submit_backtest(spec: dict) -> dict`
- `get_backtest_run(run_id: str) -> dict`
- `get_backtest_metrics(run_id: str) -> dict`
- `get_backtest_trades(run_id: str) -> list[dict]`
- `cancel_backtest(run_id: str) -> dict`

Suggested config:

- `BACKTEST_PROVIDER=remote_service`
- `BACKTEST_SERVICE_BASE_URL=...`
- `BACKTEST_SERVICE_API_KEY=...`
- `BACKTEST_SERVICE_TIMEOUT_SECONDS=...`

Suggested integration points in this repo:

- `research_tasks`
- `skill_candidates`
- `candidate_validation_snapshots`
- future experiment registry
- chat-triggered research requests

## PDCA integration

The intended workflow is:

1. user, chat, review, or autonomous loop proposes a hypothesis/change
2. bot opens or links a `research_task`
3. bot builds a `BacktestSpec`
4. bot submits a remote run
5. bot persists `run_id`, status, and summary locally
6. `CHECK` interprets metrics
7. `ACT` decides whether to reject, keep researching, paper-test, or promote

## Local persistence in this repo

This bot does not need to mirror the whole remote database.

It only needs enough local traceability:

- linked entity
- remote `run_id`
- status
- engine
- dataset version
- summary metrics
- artifact references
- timestamps

## Non-goals for v1

- execute arbitrary Python from the bot
- embed notebooks in the bot backend
- make this repo responsible for heavy historical datasets
- couple runtime trading availability to backtesting availability

## Next implementation slice in this repo

When this design moves from documentation to code here, the first slice should
be:

1. add a backtesting provider contract
2. add a remote-service adapter
3. persist lightweight remote run references
4. expose a minimal API/UI to launch and inspect runs
5. link runs to `research_task` and `skill_candidate`
