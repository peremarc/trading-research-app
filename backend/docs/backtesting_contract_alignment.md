# Backtesting Contract Alignment

Este documento alinea `trading-research-app` con el contrato **ejecutable hoy**
del repo externo `backtesting`.

## Rule

Para integración real:

- la **verdad ejecutable** es el repo `backtesting`
- este repo debe tratar su wire contract actual como canónico
- el documento `backtesting_service.md` sigue siendo útil como dirección de diseño, pero no debe usarse como si ya describiera la API completa implementada

## Canonical Service Today

Resumen corto del contrato actual:

- `spec_version`: `backtest_spec.v1`
- timeframe: `1D`
- side: `long`
- engine: `native_daily_ohlcv_replay`
- data sources: `synthetic_demo`, `inline_bars`, `yahoo_chart`
- validation: `date_ratio`
- estados del run:
  - `queued`
  - `running`
  - `cancel_requested`
  - `completed`
  - `failed`
  - `cancelled`

## Request Mapping

| Trading-research-app doc / intent | Backtesting canonical v1 | Nota |
|---|---|---|
| `spec_version=backtest_spec_v1` | `spec_version=backtest_spec.v1` | el servicio acepta el alias temporal |
| `source.requested_by` | `source.requested_by` | igual |
| `source.source_app` | `source.source_app` | igual |
| `source.reason` | `source.reason` | aceptado para trazabilidad |
| `source.linked_entity_type/id` | `source.linked_entity_type/id` | aceptado para trazabilidad |
| `target.type` | `target.type` | aceptado para trazabilidad |
| `target.code` | `target.code` | aceptado para trazabilidad |
| `target.version` | `target.version` | aceptado para trazabilidad |
| `target.research_task` | `target.research_task_code` | hoy usar código/plano, no objeto rico |
| `target.skill_candidate` | `target.skill_candidate_code` | usar código específico |
| `universe.tickers` | `universe.symbols` | el servicio acepta `tickers` como alias temporal |
| `data.provider` | `data.source_type` | el servicio acepta `provider` como alias temporal |
| `data.dataset_version` input | no canónico en request | el servicio calcula `dataset_version` |
| `validation_plan.mode` | `validation_plan.split_mode` | alias temporal aceptado |
| `outputs.include_equity_curve` | `outputs.include_equity` | alias temporal aceptado |
| `run.status=succeeded` | `run.status=completed` | el bot debe mapear esto ya |
| `requester` | `requested_by` | el nombre correcto del campo es `requested_by` |

## Fields Still Future, Not Current Wire Contract

No deben exigirse al servicio actual:

- `data.adjust_prices`
- `data.corporate_actions_mode`
- `data.warmup_bars`
- `strategy.playbook`
- `strategy.signal_codes`
- `strategy.risk_rules`
- `strategy.context_requirements`
- `execution_model.position_sizing_mode`
- `execution_model.max_positions`
- `execution_model.allow_partial_exits`
- `execution_model.allow_reentry`
- `validation_plan.walk_forward`
- `validation_plan.success_thresholds`
- `outputs.include_daily_stats`
- `outputs.include_artifacts`
- auth `Bearer` como requisito de aplicación ya implementado

## Local Integration Status

Este repo ya implementa una integración mínima real:

- provider remoto en `backend/app/providers/backtesting/`
- persistencia ligera en `external_backtest_runs`
- API local en `GET/POST /api/v1/research/backtests...`
- discovery proxy en `GET /api/v1/research/backtests/provider/context`
- reconciliación periódica de runs no terminales desde el scheduler
- vínculo explícito opcional con `skill_candidate`

## Implication For `BacktestProvider`

El `BacktestProvider` de este repo debe:

1. construir el shape canónico del servicio, no el aspiracional
2. normalizar nombres legacy antes del envío
3. tratar `dataset_version` como output del run
4. mapear `completed` al vocabulario interno que convenga, pero sin esperar `succeeded` del servicio
5. persistir al menos:
   - `remote_run_id`
   - `status`
   - `engine`
   - `dataset_version`
   - summary metrics
   - artifact refs
   - relación con `research_tasks`, `skill_candidates` o entidades futuras

## Source of Truth

Referencias que deben mantenerse sincronizadas:

- `backtesting/docs/CONTRACT_V1.md`
- `backtesting/docs/API.md`
- `backtesting/README.md`
- `backtesting /ai/context`
