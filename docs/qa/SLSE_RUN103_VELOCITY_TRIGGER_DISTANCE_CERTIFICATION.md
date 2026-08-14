# SLSE Run 103 Velocity and Trigger Distance Certification

Date: 2026-08-14

Branch: `codex/ceri-run101-remediation`

Baseline HEAD: `39df1fe2a22dfa93adc197261e8241f83327b703`

Database/Alembic head: `0045_ceri_changes_alerts_semantics`

Remediated semantic identity: engine `slse-1.3.0`, config `2026-08-14-velocity-trigger-distance`, snapshot schema `slse-snapshot-1.0.0`

## Certification decision

The applicable Velocity and Trigger Distance gates in the Run 103 remediation specification pass. The implementation repairs the authoritative server-side path from source evidence through snapshot persistence, event/query projection, API output, export, and rendered Lifecycle Changes UI. No Run 103 snapshot or event was mutated and no database migration was required.

The dedicated repository-wide 100k-history performance test was also run. Its new capture path passed the 1,000-ticker target in 37.64 seconds. The test as a whole remains red on the pre-existing compound-filter query gate (976.04 ms p95 versus 500 ms; the checked-in prior report was already red at 1,183.52 ms). This unrelated query-indexing debt is not caused by the Velocity/Trigger Distance data path and is recorded under remaining work rather than hidden.

## Sources reviewed

- `SwingLens_Run103_SLSE_Velocity_TriggerDistance_Remediation.md`, completely.
- `SwingLens_Setup_Lifecycle_and_Signal_Change_Engine_SRS.docx`, including FR-003, FR-005, FR-025, FR-027, point-in-time, trigger-authority, and missing-data clauses.
- `SwingLens_Setup_Lifecycle_and_Signal_Change_Engine_SDD.docx`, including canonical snapshots, signal registry, family adapters, lifecycle thresholds, query/API, replay, and UI clauses.

The requested `(3)`-suffixed SRS/SDD filenames were not present in Downloads. The available unsuffixed documents have the matching document control and cited clauses and were therefore used as the authoritative copies.

## Baseline and Run 103 evidence

Before production edits, `pytest -q tests/setup_lifecycle` produced `226 passed, 1 warning in 38.52s`.

Read-only database evidence was recaptured after remediation to prove historical immutability:

| Check | Result |
|---|---:|
| Run 103 snapshots | 434 |
| Run 103 canonical snapshots | 324 |
| Run 103 snapshot trigger distance non-null | 0 |
| Run 103 business-distance sentinels | 0 |
| Run 103 evaluation runs | `176:COMPLETED, 177:COMPLETED` |
| Evaluation 177 signal changes | 3,406 |
| Evaluation 177 lifecycle events | 613 |
| Immutable legacy lifecycle evidence containing `999.0` | 25 |
| Run 103 snapshot engine versions | `slse-1.2.0=434` |
| Run 103 TechnicalScore rows with `v4_debug_json.box.box_high` | 433 / 434 |

The 25 legacy sentinel values remain only in immutable historical lifecycle evidence. They are not snapshot business distances, are not projected as `trigger_distance_pct`, and the remediated adapter cannot create them.

## Forensic root cause

### Velocity

`SetupLifecycleChangeDetector.detect_and_persist` loaded a bounded list of canonical snapshots and `velocity_by_window` interpreted a configured window as `history[window - 1]`. It therefore measured the first/third/fifth/tenth stored observation, not the exact completed US trading session required by FR-025. Velocity was then stored only in the particular `SignalChangeEvent.evidence_json` generated for `technical_score` or `setup_score`. Lifecycle rows and unrelated signal-change rows had no local velocity, so their generic dashboard field rendered null.

Run 103 control `XYZ`, current snapshot 27243 on 2026-08-13, proves the defect:

| Series | Stored label | Actual prior stored date | Stored delta |
|---|---:|---|---:|
| technical | 1 | 2026-08-05 | +0.9122 |
| technical | 3 | 2026-08-03 | +0.0384 |
| technical | 5 | 2026-07-30 | +1.6362 |
| technical | 10 | 2026-07-06 | +0.0184 |
| setup | 1 | 2026-08-05 | +3.2 |
| setup | 3 | 2026-08-03 | +0.8 |
| setup | 5 | 2026-07-30 | +0.7 |
| setup | 10 | 2026-07-06 | +0.3 |

The correct exact target sessions for 2026-08-13 are 1S `2026-08-12`, 3S `2026-08-10`, 5S `2026-08-06`, and 10S `2026-07-30`.

### Trigger Distance

The snapshot builder looked only for `pivot_price`/`trigger_price` in `RawCompanyRow.raw_json`. Run 103 raw rows do not contain those fields even though 433 of 434 TechnicalScore rows contain canonical box-high evidence. Pullback prior-session price evidence was not loaded. Consequently all Run 103 snapshots persisted null pivot, trigger, and distance values, and all 3,585 combined UI rows projected null.

Separately, `BreakoutAdapter` converted missing distance to `999.0`. That was the source of the 25 Run 103 lifecycle evidence sentinels. The old helper calculated `(close - pivot) / pivot`, whose sign contradicted the configured `lower_is_better_until_trigger` direction and `[5,3,2,1,0]` readiness crossings. Because no coherent canonical convention existed, the documented fallback is now used:

```text
((reference_price - close) / reference_price) * 100
```

Thus positive is below the reference, zero is at the reference, and negative is post-trigger.

## Remediation

- The snapshot capture service batch-loads prior canonical history once per run.
- Snapshot construction computes and persists separate `technical_score.velocity` and `setup_score.velocity` maps for 1/3/5/10 exact US trading-session targets.
- Missing exact target-session snapshots or values remain null with explicit missing reasons; no interpolation or observation substitution occurs.
- Query filtering, sorting, DTOs, API rows, CSV export, and the generic dashboard Velocity field read snapshot-level technical-score velocity.
- Setup-score velocity remains separately named and exported.
- Family trigger references are normalized with source ID, source path, effective session, reference type, price, family, and missing reason:
  - BREAKOUT: explicit pivot, then canonical TechnicalScore box high.
  - VCP: explicit VCP pivot, then canonical TechnicalScore box high.
  - PULLBACK: configured trigger, then exact prior completed US-session high.
  - CONTINUATION: configured trigger, then canonical continuation box high.
  - GENERIC: null unless an explicit legitimate trigger/pivot exists.
- The projected loader fetches one authoritative TRADES/ADJUSTED_LAST record for each of the latest two sessions, enabling point-in-time pullback reference selection without look-ahead.
- `999`, `999.0`, non-finite, zero, empty, or missing reference geometry produces null.
- Family adapters preserve numeric/null trigger evidence and explicit missing reasons; breakout no longer manufactures `999.0`.
- The UI labels the field `Technical Velocity (3S)` and performs formatting only. It does no lifecycle or metric calculation.

No scoring, ranking, lifecycle states, opportunity/actionability, confidence, alerts, CERI, or broker/order semantics were changed.

## Exact-session velocity positive control

For a current 2026-08-13 snapshot, the certification fixture persisted:

| Window | Exact target | Technical old -> current | Technical velocity | Setup old -> current | Setup velocity |
|---:|---|---|---:|---|---:|
| 1S | 2026-08-12 | 7.4 -> 8.0 | +0.6 | 7.6 -> 7.8 | +0.2 |
| 3S | 2026-08-10 | 7.0 -> 8.0 | +1.0 | 7.1 -> 7.8 | +0.7 |
| 5S | 2026-08-06 | 6.5 -> 8.0 | +1.5 | 6.9 -> 7.8 | +0.9 |
| 10S | 2026-07-30 | 6.0 -> 8.0 | +2.0 | 6.8 -> 7.8 | +1.0 |

A negative control with only a 2026-08-05 stored snapshot produces a 1S target of 2026-08-12, null delta, null prior snapshot ID, and `EXACT_TARGET_SESSION_SNAPSHOT_UNAVAILABLE`.

The real query/API integration fixture binds a lifecycle event and a setup-score signal-change event to the same current snapshot. Both return dashboard `score_velocity_3d=0.9`; both separately return `setup_score_velocity_3d=0.4`, even though the signal event deliberately carries misleading local velocity evidence. Snapshot-level filtering at `velocity_min=0.8` returns both rows.

### Complete five-control data/API proof

A disposable PostgreSQL database was migrated to Alembic head and populated through the production source loader, snapshot capture, canonicalization, change detector, family/lifecycle engine, and query service. Snapshot IDs below belong to that isolated certification run; the database was dropped afterward. All five controls use current date 2026-08-13, technical score 8.0, setup score 7.8, exact technical velocities `1S +0.6 / 3S +1.0 / 5S +1.5 / 10S +2.0`, and separately persisted setup velocities `1S +0.2 / 3S +0.7 / 5S +0.9 / 10S +1.0`. Exact target dates are respectively 2026-08-12, 2026-08-10, 2026-08-06, and 2026-07-30.

| Ticker | Current snapshot | Family | State / phase | Reference type, price, lineage | Close | Distance | API proof |
|---|---:|---|---|---|---:|---:|---|
| CBRK | 5 | BREAKOUT | READY / PIVOT_READY | BREAKOUT_PIVOT, 100, `raw_company_rows.raw_json.pivot_price` | 99 | +1.0% | 4 rows; all 3S velocity +1.0 and distance +1.0% |
| CVCP | 10 | VCP | TIGHTENING / CONTRACTION_3 | VCP_PIVOT, 100, `raw_company_rows.raw_json.pivot_price` | 101 | -1.0% | 4 rows; all 3S velocity +1.0 and distance -1.0% |
| CPBK | 15 | PULLBACK | READY / REVERSAL_READY | PULLBACK_CONFIGURED_TRIGGER, 100, `raw_company_rows.raw_json.trigger_price` | 98 | +2.0% | 2 rows; all 3S velocity +1.0 and distance +2.0% |
| CCON | 20 | CONTINUATION | DEVELOPING / PAUSE | CONTINUATION_TRIGGER, 100, `raw_company_rows.raw_json.trigger_price` | 101 | -1.0% | 4 rows; all 3S velocity +1.0 and distance -1.0% |
| CNULL | 25 | GENERIC | READY / READY | no legitimate reference (`TRIGGER_NOT_APPLICABLE`) | 98 | null | 1 row; 3S velocity +1.0 and distance null |

The CNULL source explicitly omits trigger/pivot fields. Its null is therefore a genuine not-applicable control, not a loader failure. The separate pullback unit control also proves the fallback lineage `price_bars.previous_completed_session.high`, source session 2026-08-12, close 98, reference 100, and distance +2.0%; a non-exact stale bar remains null.

## Trigger/reference controls

| Control | Reference lineage | Close / reference | Business distance | Result |
|---|---|---:|---:|---|
| BREAKOUT | `technical_scores.v4_debug_json.box.box_high` | 101 / 100 | -1.000000% | numeric |
| VCP | `technical_scores.v4_debug_json.box.box_high` | 101 / 100 | -1.000000% | numeric |
| PULLBACK | exact 2026-08-12 `price_bars.previous_completed_session.high` | 98 / 100 | +2.000000% | numeric |
| CONTINUATION | `technical_scores.v4_debug_json.box.box_high` | 101 / 100 | -1.000000% | numeric |
| GENERIC without legitimate reference | none, `TRIGGER_NOT_APPLICABLE` | 98 / null | null | legitimate null |

The PostgreSQL production-stack golden corpus additionally proves API numeric distance for GBRK/BREAKOUT, GPBK/PULLBACK, GVCP/VCP, and GCON/CONTINUATION across below-trigger, at-trigger, and post-trigger sessions. Every API row for the same current snapshot returns the same snapshot distance. Trigger-distance filters and sorting operate on that promoted snapshot column.

READY hysteresis/state-machine tests remain green. Formula boundary tests prove +2% below reference, 0% at reference, and -1% above reference. No missing value becomes zero.

## API, export, and UI evidence

- `market_change_payload` exposes technical velocity for all source-event types, separately named setup velocity, numeric/null `trigger_distance_pct`, and trigger reference lineage.
- Real PostgreSQL service tests cover combined lifecycle/signal rows, filters, sorts, and CSV export.
- FastAPI/Jinja tests render `Technical Velocity (3S)`, `+1.10`, and `+2.00%` from authoritative payloads.
- The Playwright populated-page test renders two source-event rows tied to one current snapshot and asserts two `+0.90` velocity values plus two `-0.40%` trigger distances. Screenshot: `output/playwright/slse-market-changes-populated.png`.

## Point-in-time, replay, and immutability

- Exact session dates come from the existing US-market calendar utilities.
- History selection is canonical, ticker/timeframe scoped, strictly before the current data-as-of date, and exact-date matched.
- Pullback fallback requires the exact previous US session and selects the authoritative source deterministically; a stale earlier bar is not substituted.
- Trigger source lineage is embedded in immutable snapshot lineage/debug evidence and participates in the source-data hash.
- Engine/config versioning creates corrected future/replay evidence without updating Run 103.
- Existing replay, same-day revision, canonical revision, historical-view, retry/idempotency, and alert-idempotency regression tests pass.

## Test record

TDD red controls first failed for the missing `_trigger_distance_pct` helper, missing two-session source query, event-local velocity projection, and adapter lineage registration. The corresponding green runs were:

```text
pytest -q tests/setup_lifecycle/test_signal_velocity.py tests/setup_lifecycle/test_snapshot_builder.py tests/setup_lifecycle/test_breakout_lifecycle.py
35 passed

pytest -q tests/setup_lifecycle/test_query_service.py tests/setup_lifecycle/test_routes.py
41 passed

pytest -q tests/setup_lifecycle/test_source_loader.py
8 passed

pytest -q tests/setup_lifecycle
245 passed

pytest -q tests/integration/test_slse_golden_corpus.py
3 passed

pytest -q tests/test_config_files.py tests/integration/test_slse_market_alert_vertical.py
11 passed

pytest -q tests/e2e/test_slse_populated_browser.py
1 passed
```

The normal combined integration run produced `4 passed, 1 skipped`; the skip is the opt-in scale test. Its explicit opt-in run produced:

```text
1,000-ticker evaluation: 37.639610 seconds (PASS, target <= 60)
compound-filter query: 976.044 ms p95 (FAIL, target <= 500)
prior checked-in compound-filter report: 1,183.523 ms p95 (already FAIL)
```

Final relevant green count is 260 test results: 245 SLSE unit/regression, 3 golden-corpus, 11 config/PostgreSQL vertical, and 1 browser E2E. The separate opt-in performance test is reported independently because its unrelated pre-existing compound-filter gate remains red.

## Modified files

Production/configuration:

- `app/services/setup_lifecycle/adapter_input_audit.py`
- `app/services/setup_lifecycle/breakout_adapter.py`
- `app/services/setup_lifecycle/change_detector.py`
- `app/services/setup_lifecycle/continuation_adapter.py`
- `app/services/setup_lifecycle/export_service.py`
- `app/services/setup_lifecycle/pullback_adapter.py`
- `app/services/setup_lifecycle/query_service.py`
- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/setup_lifecycle/source_loader.py`
- `app/services/setup_lifecycle/vcp_adapter.py`
- `app/templates/setup_lifecycle.html`
- `config/setup_lifecycle.yaml`

Tests/certification:

- `tests/setup_lifecycle/test_breakout_lifecycle.py`
- `tests/setup_lifecycle/test_family_adapters.py`
- `tests/setup_lifecycle/test_query_service.py`
- `tests/setup_lifecycle/test_routes.py`
- `tests/setup_lifecycle/test_setup_lifecycle_config.py`
- `tests/setup_lifecycle/test_signal_velocity.py`
- `tests/setup_lifecycle/test_snapshot_builder.py`
- `tests/setup_lifecycle/test_source_loader.py`
- `tests/integration/test_slse_golden_corpus.py`
- `tests/integration/test_slse_market_alert_vertical.py`
- `tests/e2e/test_slse_populated_browser.py`
- `tests/test_config_files.py`
- `docs/qa/SLSE_RUN103_VELOCITY_TRIGGER_DISTANCE_CERTIFICATION.md`

## Remaining low-priority item

The general compound-filter query at 100k history remains above its repository-wide 500 ms p95 target. It predates this remediation and improved in the current run, but needs a separate query-plan/indexing remediation. It does not invalidate the Velocity/Trigger Distance semantic, point-in-time, API, UI, replay, or immutability gates certified here.

## Gate matrix

| Gate family | Status |
|---|---|
| Exact 1/3/5/10 completed-session velocity | PASS |
| Missing exact history remains null | PASS |
| Dashboard technical velocity / separate setup velocity | PASS |
| Same snapshot consistency and server-side filter/sort | PASS |
| Point-in-time family trigger lineage | PASS |
| No business `999`/missing-as-zero | PASS |
| Numeric BREAKOUT/VCP/PULLBACK/CONTINUATION controls | PASS |
| Legitimate null / no look-ahead | PASS |
| API/export/UI rendering | PASS |
| Scores/rankings/state machine/alerts unchanged | PASS |
| Replay/versioning/historical immutability | PASS |

Overall Velocity certification: **PASS**

Overall Trigger Distance certification: **PASS**
