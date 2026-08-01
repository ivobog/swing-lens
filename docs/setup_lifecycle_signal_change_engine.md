# Setup Lifecycle and Signal-Change Engine

The Setup Lifecycle and Signal-Change Engine (SLSE) turns each completed daily run into immutable setup evidence, lifecycle episodes, signal-change events, alerts, replay output, and point-in-time features for the Winner Probability Engine.

SLSE is research infrastructure only. It does not place, modify, cancel, or route broker orders.

## Configuration

The source configuration is `config/setup_lifecycle.yaml`; environment defaults are in `.env.example` and `app/settings.py`.

Runtime feature flags default off:

- `SETUP_LIFECYCLE_ENABLED=false`
- `SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED=false`
- `SETUP_LIFECYCLE_ALERTS_ENABLED=false`
- `SETUP_LIFECYCLE_REPLAY_ENABLED=false`
- `SETUP_LIFECYCLE_RECONSTRUCTION_ENABLED=false`

Core engine defaults:

- Engine version: `slse-1.0.0`
- Snapshot schema version: `slse-snapshot-1.0.0`
- Timeframe: `1d`
- Origin mode: `forward_only`
- Trigger authority: `COMPLETED_DAILY_CLOSE`
- Intraday high-cross support: diagnostic only

State model:

- Supported states: `DISCOVERED`, `DEVELOPING`, `TIGHTENING`, `READY`, `TRIGGERED`, `CONFIRMED`, `EXTENDED`, `FAILED`, `EXPIRED`
- Terminal states: `FAILED`, `EXPIRED`
- One active episode is allowed per ticker, timeframe, and setup family.

Setup families:

- `BREAKOUT`
- `PULLBACK`
- `VCP`
- `CONTINUATION`
- `GENERIC`

Default score thresholds:

- Breakout, pullback, VCP, continuation: tracking score `5.5`, ready score `7.5`
- Generic fallback: tracking score `5.0`, ready score `7.0`
- Generic fallback is enabled but cannot shadow a supported family once that family reaches configured confidence.

Confidence and actionability:

- Confidence labels use `HIGH >= 85`, `NORMAL >= 70`, `LOW >= 50`, otherwise insufficient.
- Minimum actionable confidence is `70`.
- Earnings risk, liquidity risk, insufficient required data, and blocked/risk-off market policy block actionability.
- Low-quality or stale evidence can keep an episode visible while marking it `LOW_CONFIDENCE`.
- Reconstructed origin is excluded from live alerts, live alert statistics, and OWPE export by default.

Retention and replay:

- Immutable evidence retention is indefinite by default.
- Purge is disabled and requires preview, confirmation, and audit if enabled later.
- Replay output is not authoritative by default.
- Persisted replay creates a parallel version and promotion requires explicit admin confirmation.

## Pipeline Behavior

SLSE is staged behind separate flags so each release step can be rolled back independently.

1. Shadow capture: enable `SETUP_LIFECYCLE_ENABLED` and `SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED` to capture snapshots from daily completed-run data.
2. Lifecycle preview: evaluate canonical snapshots into episodes and signal-change events while alerts remain disabled.
3. Dashboard release: expose `/setup-lifecycle`, ticker timelines, episode detail, operations, diagnostics, and exports.
4. Alert release: enable built-in alert rules with `SETUP_LIFECYCLE_ALERTS_ENABLED`.
5. Replay release: enable replay workflows with `SETUP_LIFECYCLE_REPLAY_ENABLED`; replay remains non-authoritative unless explicitly promoted.
6. OWPE bridge: consume point-in-time lifecycle features through `setup_lifecycle_point_in_time_features`.

Pipeline inserts use immutable source identity and canonicalization precedence:

- completed daily bar
- successful source pipeline
- required feature coverage
- context completeness
- latest calculated timestamp
- highest snapshot id

The canonicalization index keeps one canonical snapshot per ticker, timeframe, and date. Dashboard/event queries are indexed by ticker/date, episode, status, family/state, evaluation run, and alert status/severity.

## Routes And APIs

HTML routes:

- `GET /setup-lifecycle`
- `GET /setup-lifecycle/ticker/{ticker}`
- `GET /setup-lifecycle/episodes/{episode_id}`
- `GET /setup-lifecycle/alerts`
- `GET /setup-lifecycle/operations`
- `GET /runs/{run_id}/setup-lifecycle`

Dashboard exports:

- `GET /setup-lifecycle/export.csv`
- `GET /setup-lifecycle/export.json`

API routes:

- `GET /api/setup-lifecycle/changes`
- `GET /api/setup-lifecycle/tickers/{ticker}`
- `GET /api/setup-lifecycle/tickers/{ticker}/timeline`
- `GET /api/setup-lifecycle/episodes/{episode_id}`
- `GET /api/setup-lifecycle/alerts`
- `POST /api/setup-lifecycle/alerts/{alert_id}/acknowledge`
- `POST /api/setup-lifecycle/alerts/{alert_id}/dismiss`
- `POST /api/setup-lifecycle/run/{run_id}/evaluate`
- `POST /api/setup-lifecycle/evaluate`
- `POST /api/setup-lifecycle/evaluate-run`
- `POST /api/setup-lifecycle/replay`
- `GET /api/setup-lifecycle/evaluations/{evaluation_id}`
- `GET /api/setup-lifecycle/filter-options`
- `GET /api/setup-lifecycle/operations`
- `GET /api/setup-lifecycle/diagnostics`

API exports:

- `GET /api/setup-lifecycle/changes/export.csv`
- `GET /api/setup-lifecycle/changes/export.json`
- `GET /api/setup-lifecycle/alerts/export.csv`
- `GET /api/setup-lifecycle/alerts/export.json`
- `GET /api/setup-lifecycle/episodes/{episode_id}/export.csv`
- `GET /api/setup-lifecycle/episodes/{episode_id}/export.json`
- `GET /api/setup-lifecycle/operations/export.json`

Stable API error codes are defined in `config/setup_lifecycle.yaml` and include invalid filter/cursor/sort cases plus missing ticker, episode, alert, evaluation, and run lifecycle records.

## Jobs And Maintenance

Implemented background job types:

- `SETUP_LIFECYCLE_EVALUATE_RUN`
- `SETUP_LIFECYCLE_REPLAY`
- `SETUP_LIFECYCLE_REPAIR_TICKER`
- `SETUP_LIFECYCLE_DAILY_MAINTENANCE`
- `SETUP_ALERT_REBUILD`

Maintenance supports skipped daily sessions, observation-gap expiration, ticker repair, stale-run diagnostics, alert rebuild, and replay comparison. Observation gaps expire episodes; they do not manufacture failed breakouts.

## V1 Limitations

- Daily close is the only authoritative trigger.
- Intraday high crosses are diagnostic evidence only.
- There is no intraday lifecycle engine.
- Alerts are stored in-app only; there is no external delivery channel.
- Reconstructed history is separate from live forward evidence.
- Replay output is non-authoritative by default.
- SLSE is research-only and has no order placement path.

## Verification

Focused Phase 12 checks:

```powershell
ruff check app tests
pytest tests/setup_lifecycle -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest tests/test_background_job_service.py tests/test_background_worker.py -q
```

Full regression and migration checks:

```powershell
pytest -q
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

Run the downgrade cycle only against a disposable or backed-up local database.
