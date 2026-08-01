# Setup Lifecycle and Signal-Change Engine Release Notes

Release target: SLSE v1 (`slse-1.0.0`)

## Included

- Immutable setup signal snapshots with source lineage, quality labels, warning flags, config hash, and canonicalization metadata.
- Deterministic canonicalization for affected ticker/date snapshots.
- Lifecycle adapters for breakout, bull pullback, VCP, continuation, and generic fallback setups.
- Episode management with one active episode per ticker/timeframe/family, state age, observation-gap expiry, terminal states, and re-arm cooldown.
- Signal-change detection with stable source keys, change severity, threshold crossings, and current/superseded lifecycle events.
- In-app alert rules for readiness, triggers, confirmations, failures, extension, score acceleration, sector acceleration, gate blocking, and data degradation.
- Query and dashboard surfaces for market changes, ticker timeline, episode detail, alert center, operations, run detail, diagnostics, and exports.
- Background jobs for run evaluation, replay, ticker repair, daily maintenance, and alert rebuild.
- Non-authoritative replay comparison and persisted replay versions that require explicit promotion.
- Point-in-time lifecycle feature export for the Winner Probability Engine.

## Rollout Stages

1. Shadow capture: enable `SETUP_LIFECYCLE_ENABLED` and `SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED`.
2. Lifecycle preview: keep alerts/replay disabled and review episodes, signal changes, diagnostics, and exports.
3. Dashboard release: expose `/setup-lifecycle` and related read-only pages.
4. Alert release: enable `SETUP_LIFECYCLE_ALERTS_ENABLED`.
5. Replay release: enable `SETUP_LIFECYCLE_REPLAY_ENABLED` for comparison workflows.
6. OWPE bridge: enable downstream use of point-in-time SLSE features after live evidence quality is accepted.

Each stage has a direct flag rollback path.

## Acceptance Status

- Configuration defaults are locked by tests: disabled by default, daily-close authority, diagnostic high-cross, forward-only live origin, immutable retention, and non-authoritative replay.
- Golden fixtures cover clean breakout, failed breakout, clean bull pullback, deteriorating pullback, VCP, extended momentum, choppy score oscillation, missing-data blocking, market-gate blocking, and filtered-universe observation gaps.
- API and dashboard routes are covered by route tests.
- Export helpers are covered for CSV/JSON output and OWPE point-in-time features.
- Background job handlers are covered for evaluation, replay, repair, daily maintenance, and alert rebuild.
- Performance contract tests verify target settings, index coverage for canonical/dashboard queries, route pagination, and deterministic 1,000-ticker stable-key generation.

## Limitations

- Daily close is the only authoritative trigger in v1.
- Intraday high-cross evidence remains diagnostic and cannot trigger lifecycle authority.
- No intraday lifecycle loop is implemented.
- Alert delivery is in-app only.
- Reconstructed history is separate from live forward evidence and excluded from live alert statistics and OWPE export by default.
- Replay results are not authoritative unless explicitly promoted by an admin workflow.
- No broker order placement, modification, or cancellation is implemented.

## Verification Notes

Recommended checks before release:

```powershell
ruff check app tests
pytest tests/setup_lifecycle -q
pytest tests/test_pipeline_service.py tests/test_pipeline_executor.py -q
pytest tests/test_background_job_service.py tests/test_background_worker.py -q
pytest -q
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

The migration downgrade cycle should be run only against a disposable or backed-up local database.
