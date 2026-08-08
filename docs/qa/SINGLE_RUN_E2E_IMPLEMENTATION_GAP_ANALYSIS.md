# Single-Run E2E Certification: Implementation Gap Analysis

Date: 2026-08-08
Repository revision inspected: `8e56c8ddb62f2eda9058eeb70607b20739f3144f`
Current Alembic head: `0029_ceri_wave4_evidence_features`

## Scope and source-of-truth decision

This analysis maps `SwingLens_Single_Run_Comprehensive_E2E_Test_Plan.md` to the
current repository. Current SQLAlchemy metadata, routes, templates, services,
migrations, settings, and tests are authoritative where the specification uses
older or illustrative names.

The current application maps 66 PostgreSQL tables. The normal durable pipeline is:

1. `VALIDATING_RUN`
2. `SCORING_FUNDAMENTALS`
3. `FETCHING_MARKET_DATA`
4. `SCORING_TECHNICALS`
5. `MARKET_REGIME_SNAPSHOT`
6. `COMBINING_RESULTS`
7. `SECTOR_ROTATION_SNAPSHOT`
8. optional `CERI_PROVIDER_INGEST` or `CERI_CAPTURE_SNAPSHOT`
9. optional `CAPTURING_SETUP_SIGNALS`
10. optional `EVALUATING_SETUP_LIFECYCLES`
11. `CAPTURING_WINNER_PREDICTIONS` (capture is gated independently)

## Existing reusable QA assets

| Requirement | Current support | Existing coverage/assets | Gap / planned extension |
|---|---|---|---|
| Disposable PostgreSQL | `tests/conftest.py` creates safely prefixed databases; browser fixture migrates them | `disposable_postgres_database_factory`, `tests/e2e/conftest.py` | Generalize into a certification environment that also exposes the database URL and preserves artifacts |
| Real migrations | Browser fixture runs `alembic upgrade head` | E2E smoke | Record head/current revision and migration output in the evidence package |
| Real browser | The Playwright library is installed and browser smoke tests cover navigation/upload/accessibility; the optional pytest Playwright plugin/page fixture is absent | `tests/e2e/test_browser_smoke.py` | Add one GUI-created run, pipeline polling, data extraction, state-changing alert action, exports, screenshots, trace-on-failure, and a local browser/page fixture |
| Deterministic IB | `ScriptedIBGateway` is read-only and rejects order methods | `fake_ib_gateway_factory`, IB service tests | Inject the scripted gateway into the real fetch executor used by the worker; record requests and assert the order-call list is empty |
| Fixed clock / OHLCV | Repeatable OHLCV and frozen clock fixtures exist | `ohlcv_factory`, `fixed_clock` | Build an information-dense shared-cache universe and record its hash |
| CERI manual provider | `ManualCeriProvider` and ingestion/normalization/rebuild/capture services exist | `tests/ceri/test_manual_provider.py`, CERI service tests | Seed deterministic manual records through the real provider path, normalize/rebuild, then let pipeline capture run-linked score snapshots |
| Setup Lifecycle | Full capture/evaluation/episode/change/alert stack exists | `tests/setup_lifecycle/*`, golden acceptance fixtures | Seed a prior canonical state, enable pipeline capture/evaluation/alerts, and reconcile current pages and rows |
| Winner Probability | Capture, cohorts, estimates, manifests, outcome maturation, and exports exist | `tests/winner_probability/*` | Seed required definitions/evidence, capture through the pipeline, certify prediction/estimate lineage, and mature an eligible prediction when deterministic bars permit |
| Evidence manifests | Operational and Winner Probability manifest services exist | `scripts/ops/evidence_manifest.py`, Winner manifest tests | Add a run evidence graph manifest based on current metadata plus semantic/shared-cache relationships |
| Screenshots/reports | Browser smoke writes under `output/playwright`; no certification report generator exists | E2E logs and Playwright defaults | Add numbered GUI/database screenshots, SQL/result artifacts, JSON report, and `REPORT.md` |

## Current run evidence graph

### Direct upload-run ownership

`upload_runs` directly owns or is referenced by:

- `raw_company_rows`
- `fundamental_scores`
- `technical_scores`
- `combined_results`
- `ranking_results`
- `engine_parameters`
- `ib_fetch_runs`
- `pipeline_runs`
- `market_regime_snapshots`
- `sector_rotation_snapshots`
- `setup_signal_snapshots`
- `setup_lifecycle_evaluation_runs` (`source_run_id`)
- `winner_prediction_snapshots`
- `winner_processing_runs`
- `ceri_score_snapshots`
- `background_jobs` through semantic `related_run_id` (not a database FK)

### Indirect ownership reachable through foreign keys

- `pipeline_runs -> pipeline_steps`
- `ib_fetch_runs -> ib_fetch_items -> price_bar_revisions`
- `sector_rotation_snapshots -> sector_rotation_rows`
- `setup_lifecycle_evaluation_runs/setup_signal_snapshots -> setup_lifecycle_episodes`
- lifecycle evaluations/episodes/snapshots -> `setup_lifecycle_events`,
  `signal_change_events`, `signal_alert_events`, and administrative audit events
- `winner_prediction_snapshots -> winner_probability_estimates ->
  winner_estimate_evidence_members`
- predictions -> `winner_forward_outcomes`, `winner_target_stop_outcomes`, and
  `winner_similarity_links`
- estimates -> `winner_evidence_manifests`, cohorts/models where referenced
- `ceri_score_snapshots -> ceri_change_events -> ceri_alert_events`
- CERI score component/source lineage -> companies, normalized features,
  catalysts/revisions, estimates/revisions, guidance, earnings, source records,
  ingestion runs, and processing runs

### Shared cache and reference state

The following are not upload-owned and need before/after plus consumption-lineage
checks rather than a literal `run_id` predicate:

- `ib_contracts`
- `price_bars`
- `price_bar_revisions`
- `price_series_versions`
- `technical_feature_artifacts`
- CERI company/source/normalized feature tables
- Winner outcome definitions, cohorts, models, evidence manifests, and historical
  evidence members
- lifecycle and CERI alert rules

## Current run-related GUI and export surfaces

The current code exposes these run-scoped user surfaces:

- Runs list, run detail, column mapping, coverage, history
- durable pipeline progress/status and IB fetch progress/status
- fundamental, technical, combined, and ranking content on run detail plus ranking profiles
- ticker chart/drill-down
- Market Regime run view and CSV/JSON exports
- Sector Rotation dashboard, sector drill-down, CSV/JSON/Markdown exports
- Setup Lifecycle run view, Market Changes, ticker timeline, episode detail,
  alerts, operations, and CSV/JSON exports
- Winner Evidence run view, prediction detail, ticker history, outcomes, models,
  operations, reproduction, and run CSV/JSON exports
- CERI run view, dashboard, ticker detail, changes, operations, alerts API,
  and CSV/JSON exports
- core run CSV exports (`fundamentals`, `technicals`, `combined`)

Advanced pages are feature-gated through `Settings`. The certification profile must
enable master, UI, run-capture/pipeline, alert, and admin flags intentionally while
leaving replay/purge and all order placement unavailable.

## Coverage gaps against the specification

| Area | Existing current-code support | Missing certification functionality | Planned implementation |
|---|---|---|---|
| One canonical GUI-created run | Upload smoke exists | Smoke stops before full pipeline | Upload canonical CSV in Playwright, start durable pipeline in UI, poll to terminal state |
| Dense deterministic universe | Per-service fixtures exist | No cross-subsystem fixture version/hash | Add a small canonical universe with complete, weak, incomplete, Unknown-sector, lifecycle, CERI, and Winner scenarios |
| Decoy isolation | Route unit tests scope individual queries | No end-to-end decoy canary | Seed one clearly distinct completed decoy run before the browser creates the canonical run |
| Schema discovery | SQLAlchemy metadata is complete | No execution-time relationship manifest | Traverse metadata FKs from `upload_runs`, then apply documented semantic edges for background jobs, cache, CERI, and Winner shared records |
| DB certification | Many service/unit assertions | No unified run table/cardinality/FK/uniqueness checks | Data-driven table expectations, orphan checks, duplicate checks, run isolation, and hashes |
| GUI-to-DB parity | Route rendering tests check selected fields | No exhaustive field-level cross-layer oracle | Page-specific extractors and presentation normalizers; compare all practical visible rows/fields |
| Lifecycle | Full product services/pages | Not correlated in a GUI-created full run | Reconcile snapshot, evaluation, episode, event/change, actionability, confidence, alerts, and three GUI views |
| Alert mutation | API/service tests exist | No real GUI state mutation tied to DB | Acknowledge one lifecycle or CERI alert through the rendered UI when a control is present and verify persisted status |
| Winner maturation | Service tests exist | No certification-run maturation | Capture original immutable prediction; attempt real maturation with deterministic later bars and report applicability/result |
| CERI restricted data | Redaction tests exist | No evidence-package leak scan | Seed manual evidence, reconcile GUI score/evidence, and scan reports/screenshots metadata/exports/log text for sentinel secrets |
| Database screenshots | None | Specification asks reproducible SQL evidence | Render sanitized query text and result rows into local HTML, then screenshot through Playwright |
| Exports | Each subsystem has tests | No same-run cross-format reconciliation | Download through browser/context, parse, compare to DB, and assert the decoy canary is absent |
| Idempotency | Individual services have idempotency tests | No same-run pipeline-operation proof | Reinvoke documented capture/evaluation operations and compare primary-key/count manifests |
| Report package | None | Entire package absent | Emit environment, manifest, SQL/results, comparisons, exports, logs, screenshots, JSON report, and final Markdown matrix |

## Important current-code constraints

- A fully cached symbol produces a fetch-plan `SKIP` and the pipeline intentionally
  does not create an `ib_fetch_run` when the entire plan estimates zero requests.
  The certification fixture therefore keeps one deterministic, safe market-data
  request so real fetch-run/item/cache lineage is exercised.
- `background_jobs.related_run_id` is semantic lineage and is not declared as a
  foreign key; graph discovery must include it explicitly.
- CERI normalized evidence is mostly company/as-of scoped. The run-owned anchor is
  `ceri_score_snapshots.run_id`; upstream evidence is followed through component and
  source ID lineage instead of being falsely classified as directly run-owned.
- `price_bars` and technical artifacts are shared caches. Their certification is a
  before/after delta and consumption proof, not a `run_id` row-count assertion.
- Winner models/cohorts/evidence may legitimately be prerequisite shared history.
  Only records referenced by the run prediction/estimate graph are included.
- The current templates do not expose every database column. GUI-to-DB parity
  applies to every practical visible persisted field; non-visible columns receive
  database/lineage certification instead of artificial UI assertions.

## Implementation organization

The certification extension will be split into reusable components for:

- environment/database lifecycle and migrated server startup
- canonical fixture and deterministic provider seeding
- Playwright run launcher and pipeline waiter
- schema/run graph discovery
- SQL/result evidence and HTML rendering
- GUI extraction and value normalization
- DB/GUI comparison definitions
- screenshots and export capture
- integrity/isolation/idempotency checks
- JSON and Markdown report generation

The final command will be opt-in and destructive only to a safely prefixed disposable
PostgreSQL database. A failed semantic comparison remains a certification `FAIL`; a
missing external prerequisite such as PostgreSQL or browser binaries is `BLOCKED`.
