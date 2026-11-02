# Market Sector Intelligence D3 implementation

**D3 IMPLEMENTATION: PASS — focused D3 acceptance gates.**

22 October 2026. Independent read-only ETF Technical V4/V5 diagnostics. This report concerns local implementation and disposable fixtures; it does not certify production readiness or activation.

## Baseline and scope

- Branch: `feature/market-sector-intelligence-d3-technical-v4-v5`.
- Worktree: `C:/Users/Ivica/.codex/worktrees/market-sector-intelligence-d3/SwingLens`.
- Exact D2 parent (also the D3 implementation-handoff HEAD before this checkpoint): `41e8eb3e33cca701952fb716b597da71526fd278`.
- D1: clean at `9ad285e6cfc8c920f38c090960b7ac1907d25466`.
- D2: clean at the mandatory parent SHA. The supplied D2 report matches the actual committed report exactly.
- Original checkout: `codex/ceri-authority-remediation`, HEAD `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`.
- `main`: `aeb0ef4b32220ef178a4a0a810086a3b043a7abb`.
- At implementation handoff, no files were staged or committed. The authorized final checkpoint below records fresh verification and the nine-file commit. Intended change set: two modified tracked files and seven new files, listed below. Test XML and synthetic evidence remain ignored under `output/playwright/market-sector-intelligence-d3/`.

| File | Change and justification |
|---|---|
| `app/services/market_sector_intelligence_technical_adapter.py` | New pure ETF policy, readiness checks, shared Pine base, native V4/V5 mapping and fixed-cohort leadership. |
| `app/services/market_sector_intelligence_technical_service.py` | New read-only twelve/thirteen-source PIT orchestration, OHLCV/source validation, cohort gate, finite JSON and material identities. |
| `app/routers/market_sector_intelligence_routes.py` | Two new safe GET routes and a private D3 response helper. Existing coverage and rotation handlers are unchanged. |
| `tests/test_route_security.py` | Expected total routes 202 to 204; unsafe inventory remains 52 with unchanged classifications. |
| `tests/market_sector_technical_helpers.py` | Synthetic contiguous US-session frames, explicit PIT manifests, native engine fixtures. |
| `tests/test_market_sector_intelligence_technical_adapter.py` | Exact pure engine matching, sector-NA safety, readiness, fixed leadership and current beta-window tests. |
| `tests/test_market_sector_intelligence_technical_service.py` | Shared source/base proof, failure isolation, full-cohort suppression, deterministic identity and volume contracts. |
| `tests/test_market_sector_intelligence_technical_pit.py` | Actual SQLite cached loader and HTTP: read-only SQL, no autoflush/jobs/providers/artifacts/context writes, upload independence and PIT replay. |
| `docs/market_sector_intelligence/D3_implementation_report.md` | Implementation evidence, limitations and D4 handoff. |

All other tracked files are unchanged against D2. No new model, table, migration, capture CLI, writer registration, scheduler or UI is introduced. Stock Pine/V4/V5, technical orchestration/artifact writers, Fundamental/Combined/Ranking, Setup/Lifecycle/Alerts, Winner/CERI, existing run-sector analytics, market context, acquisition, workers and pipeline source remain unchanged.

## Actual engine integration

The authoritative fixed eleven ETFs and canonical/display labels come from the unchanged D1 universe. SPY is the sole relative-performance benchmark. The active `market_regime_v4.enabled=true` and `use_qqq=true` require a thirteenth source, QQQ, explicitly labeled `market_regime_context_only`. QQQ is never passed as an ETF sector peer. When that context requirement is disabled in an effective scoped policy, only twelve sources are loaded. D1 coverage remains a twelve-symbol contract.

Each evaluation deep-copies effective Pine, V4 and V5 configuration. Only the Pine sector-filter flag, V5 leadership scope and V5 sector-confidence requirement are scoped for ETF use. Global `sectorSymbol: QQQ`, public defaults, stock V5 activation/shadow flags and all weights remain unchanged. The adapter requires the Pine market/benchmark labels to agree with SPY. Its separate policy version is `etf-technical-diagnostics-v1`; effective model versions, scoped configuration digests, fixed members and policy digest are returned independently of D2 identity.

Pure calls, without stock orchestration:

1. `calculate_technical_features` for each required source, using the already selected price/volume frames and exact expected session.
2. `calculate_htf_trend_features` with the shared completed session; current incomplete week excluded.
3. `calculate_relative_strength_features(etf, spy, None, params=scoped_pine)` and the existing beta/residual helper when its current windows are valid.
4. One `score_from_feature_result` Pine base for each eligible ETF.
5. `technical_score_v4_from_base_score` and `technical_score_v5_from_base_score` receive the same base object. No `build_technical_score`, stock run context or technical artifact operation is called.
6. `rank_leadership_v5` receives exactly the eleven ETF members with `scope=fixed_11_etf_universe`, preserving configured weights and native percentile/tie behavior. No CSV, `RawCompanyRow`, D2 rank or rotation return enters this computation.

V4 preserves the native broad-market RS fallback when no stock-run leadership object is supplied; the response policy discloses this. V5 uses a typed `SectorBenchmarkResolution(sector=None, benchmark_symbol=None, status=MISSING_SECTOR)` with the explicit reason that sector benchmarking is not applicable to a sector ETF. The response separately declares `NOT_APPLICABLE_ETF`. The pure engine sees `use_sector_evidence=false`; its diagnostic sector-missing warning remains visible but is excluded from material confidence warnings under the scoped `required_for_confidence=false`. Tests prove that a hypothetical low sector score adds no sector-weak risk or confidence penalty in this mode.

Native V4 final score/classification/action/confidence, components, warnings and readiness are preserved. Native V5 raw composite and confidence-adjusted score remain separate, alongside strength/setup/entry/trigger/leadership, missingness, classification/action, source and input signatures. Actions retain stock-model text under `ETF_RESEARCH_DIAGNOSTIC`, `stock_engine_applied_to_etf`, `research_only`. They do not authorize trades.

The comparison is descriptive `V5 raw composite - V4 final score`, plus exact-label classification equality, only when both models are available. No calibration claim, model fusion, extra ETF ranking or D2 blending is introduced.

## Readiness and PIT evidence

D2's 64 bars are insufficient. The active Pine daily core requires at least 252 sessions (`highLow52Len`), ROC126 requires 127 closes, and SMA200 slopes require their actual lookback. Enabled 252-observation adaptive percentiles and contraction features must be finite after indicator warm-up; 252 raw bars alone do not guarantee this. Confirmed weekly EMA10, SMA30, SMA40, four-week slopes and ROC13 must be finite. The default 320-session fixtures meet these gates; a longer required weekly window correctly withholds the result.

All selected price and separate volume inputs must be fresh at the shared target, use unique valid US sessions, contain continuous daily OHLCV, positive finite prices/volume and valid high/low ranges. D3 validates before the native normalizer can drop malformed bars or fill missing volume with zero. Separate TRADES volume must cover exactly the selected price sessions; both actual bases are disclosed. ETF/SPY price bases must match. These are cached price observations, not certified total return.

The native beta helper can select its last non-null historical beta and drop missing residual rows. D3 adds a readiness check for actual current benchmark-variance windows, including every 63-session window required by the latest 63 residual returns. When unavailable, beta/residual evidence is withheld, rather than reused from older windows. V4 remains available when otherwise valid; V5's required components then fail the full-cohort gate. The stock helper is unchanged.

Missing SPY or required QQQ withholds all benchmark/context-dependent diagnostics. One deficient ETF retains its visible row, with null V4 values and stable sanitized reasons. If any member lacks required V5 leadership input, all eleven final V5 composites and comparison deltas are null with `V5_COHORT_NOT_COMPARABLE`; individually valid V4 results remain. A later isolated V5 scoring exception is labeled separately as an engine failure, not a complete V5 result. Nonfinite engine output is rejected, including nested components.

One clock cutoff supplies `as_of` and `max_session` to each preferred cached loader call. No pipeline context/acquisition authority is granted. Manifests must match symbol, actual basis, timeframe and exact cutoff, with one valid immutable fingerprint per row. Optional receipt columns are checked. Actual loader tests exercise late receipts, excluded future sessions, replay of a post-cutoff revision and later visibility of the invalid revised value.

Material digests cover all scoring-relevant selected OHLCV and PIT fingerprints, including separate volume and QQQ when used, with stable session order. They exclude request-clock drift, database IDs and uploads. The evaluation identity also includes target session, calendar/readiness versions, scoped model/policy identity and sorted source identities. No technical history is stored. Frozen D2 snapshots and their identities remain untouched.

## Read-only API

- `GET /api/market-sector-intelligence/technicals/preview`: eleven rows, even when disabled or missing data.
- `GET /api/market-sector-intelligence/technicals/preview/{etf_symbol}`: validates membership before price access, evaluates the entire fixed cohort, then returns one row with common metadata. Lowercase sector ETFs normalize to uppercase; MSFT and QQQ are rejected with 404 `UNSUPPORTED_SECTOR_ETF`.

Invalid independent/model configuration returns structured 503. Historical D2 endpoints are unchanged. ORM autoflush is suppressed for source reads; no mutation authority, writer, cache artifact, context creation or capture is added. Responses contain bounded model/evidence summaries without raw bar arrays, SQL or private exceptions.

## Implementation verification (before final checkpoint)

**374 distinct tests passed; zero failures, errors or skips.** JUnit identities were independently counted from the three final XML files, with zero duplicates.

| Final gate | Result |
|---|---|
| Inherited D1/D2 pure/SQLite regressions | **329 passed**, **150.72 seconds**; includes all 264 D1 tests and 65 D2 tests. |
| New D3 suite | **44 passed**, **106.52 seconds**: 12 adapter, 29 orchestration/source, 3 actual-loader HTTP/PIT tests. |
| Disposable PostgreSQL | **1 passed**, **18.28 seconds**; full D2 migration chain and concurrency/immutability checks. Verified disposable database created/dropped; no application DB selected. |
| Ruff | **All checks passed**, all **8** changed/new Python files, including all helpers/tests. |
| Git whitespace | Working diff and all seven new text files checked; no whitespace errors. Index remains empty. |
| Alembic | Single unchanged head `0093_market_sector_rotation`; zero D3 migrations. |
| Architecture registry | **Global FAIL**, exit **1** on clean D2 and D3: same **18 findings**, **5 writer candidates + 13 transaction sites**, zero added/removed. No suppression, registration rewrite or unrelated fix. |
| Protected-source/configuration | Only router and route-count assertion differ among pre-existing tracked files. All protected scoring/pipeline/model/migration/configuration files and both required hashes remain unchanged. |
| Isolation | Original checkout fingerprints match exactly; D1 and D2 remain clean at their original branch/SHA; main unchanged. D3 HEAD is the exact D2 base with no new commit. |

Warnings are the existing Starlette/httpx deprecation, plus two existing Alembic path-separator warnings in PostgreSQL. None was treated as a skip or silently remediated.

### SQL, resource and synthetic output evidence

The actual SQLite fixture contains **13 symbols × 320 contiguous sessions = 4,160 synthetic observations**. One full preview performs **26 SELECTs**, no DML, no run/CSV query, no jobs, provider connection, context or artifact write. Detail also performs 26 SELECTs and evaluates the full fixed cohort. Unsupported symbols and disabled preview issue zero SQL. Existing revision replay can add source-history SELECTs.

The final measured HTTP preview took **6.355658 seconds** with **82,569 response bytes**. Process RSS sampled at 50 ms was **215,740,416 bytes** before the request and **223,920,128 bytes** at the observed peak, an increase of **8,179,712 bytes**. These process measurements include pytest/imports/fixture state and do not represent isolated allocation or live capacity.

A preliminary fixture run exposed pandas repeatedly deep-copying large PIT manifests through indicator/Series operations: its ordinary preview measured 64.062884 seconds, and intrusive allocation tracing took 337.463701 seconds. D3 now retains the validated manifests separately and gives native indicators identical OHLCV working copies with only small basis metadata. No source values, source manifests, material identity, stock loader or scoring code is changed. A source-preservation/native-base/V4 equality test proves the metadata-copy boundary; final SQL counts, response size and synthetic scores match the earlier run. Timing observations are local and are not a production latency guarantee.

**Synthetic cached-fixture values, not market observations**, first ETF XLB:

```json
{
  "application_scope": "ETF_RESEARCH_DIAGNOSTIC",
  "etf_symbol": "XLB",
  "v4": {"final_v4_score": 7.7176, "classification": "No trade"},
  "v5": {
    "technical_composite_score": 5.6946,
    "confidence_adjusted_score": 5.6946,
    "classification": "No trade",
    "leadership_scope": "fixed_11_etf_universe"
  },
  "comparison": {"raw_score_delta": -2.0230}
}
```

The example delta is rounded for this report only; API arithmetic uses the native floating-point scores. Another native test uses normal confidence and verifies that raw and confidence-adjusted V5 scores differ exactly as the existing engine returns them.

Scoped identities:

- Adapter policy: `40ff85d9019da2095dc7f90b0c01b933bc7286f33474766ed11d058c514c3b3a`.
- Pine: `59ef76a520fae22eb0102afc3181151c81860fe567c45fd256fa331b0adcfae0`.
- V4: `2be86010063a1d2de93294ddd193c43fecb7c5149192bd4faffe0e5e149f0d73`.
- V5: `511a1222537906555aab14387d5760784377645f7da45fc40ae497d069d2c3d0`.

### Reproduction and evidence files

Run from the D3 worktree with the shared existing virtual environment; the inherited regression list is the exact D2 checkpoint list:

```powershell
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_config.py tests/test_market_sector_intelligence_coverage.py tests/test_market_sector_intelligence_routes.py tests/test_sector_rotation_config.py tests/test_sector_taxonomy.py tests/test_us_market_calendar.py tests/test_session_temporal_integrity_remediation.py tests/test_sector_etf_rotation_service.py tests/test_sector_rotation_service.py tests/test_sector_rotation_policy.py tests/test_sector_rotation_routes.py tests/test_gui_phase1.py tests/test_settings.py tests/test_route_security.py tests/test_technical_score_v4.py tests/test_technical_v5_calibration.py tests/setup_lifecycle/test_snapshot_builder.py tests/winner_probability/test_feature_schema.py tests/test_market_sector_intelligence_rotation_math.py tests/test_market_sector_intelligence_rotation_service.py tests/test_market_sector_intelligence_rotation_storage.py tests/test_market_sector_intelligence_rotation_pit.py --junitxml=output/playwright/market-sector-intelligence-d3/regression-final.xml
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_technical_adapter.py tests/test_market_sector_intelligence_technical_service.py tests/test_market_sector_intelligence_technical_pit.py --junitxml=output/playwright/market-sector-intelligence-d3/d3-final.xml
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B scripts/check_architecture_registry.py --check
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B -m alembic heads
git diff --check
```

PostgreSQL runs in a child process using `resolve_test_postgres_admin_url`, with credentials read without printing from the existing local configuration. `DATABASE_URL` points to the administrative `postgres` database and `SWINGLENS_DATABASE_SAFETY_CONTEXT=DISPOSABLE_TEST`. The existing fixture creates/identity-checks a separate `swinglens_pytest_*` database, runs only there, then drops it. The test command is:

```powershell
python -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/integration/test_market_sector_intelligence_rotation_postgresql.py --junitxml=output/playwright/market-sector-intelligence-d3/postgresql-final.xml
```

Ignored final evidence: `regression-final.xml`, `d3-final.xml`, `postgresql-final.xml`, `verification-summary.json` (all 374 identities and observed properties), and `policy.json` under `output/playwright/market-sector-intelligence-d3/`. Preliminary runs are excluded from the final count.


| Acceptance | Evidence |
|---|---|
| D3-AT-01 | Exact clean D2 parent; new attached D3 worktree/branch; original/D1/D2 checks and fingerprints. |
| D3-AT-02 | Eleven canonical ETF slots, independent of arbitrary uploads; disabled mode has zero price reads. |
| D3-AT-03 | Thirteen identical PIT bounds, shared ETF/SPY inputs, actual receipt/revision/future-session tests. |
| D3-AT-04 | Native V4 score/classification/action/warnings/confidence match the shared Pine fixture exactly. |
| D3-AT-05 | Native V5 raw/adjusted scores, components and labels match; same base object reaches both models. |
| D3-AT-06 | Sector frame is always None for every ETF; QQQ appears only in market context. |
| D3-AT-07 | Exactly eleven fixed members, stable average-percentile ties and scope; no stock-run context. |
| D3-AT-08 | A single source/leadership deficiency suppresses all final V5 scores; ten or eleven valid V4 results remain as applicable. |
| D3-AT-09 | Missing/stale ETF/SPY/QQQ, malformed OHLCV, mixed basis, missing volume, ROC/adaptive/confirmed weekly and current beta evidence fail honestly. |
| D3-AT-10 | Explicit sector-NA resolution; changing hypothetical sector evidence changes neither risk nor confidence; globals unchanged. |
| D3-AT-11 | Native labels/scores preserved; descriptive delta and exact-label agreement only when both available. |
| D3-AT-12 | Actual cached-loader GETs are SELECT-only; pending ORM upload remains unflushed; provider/queue/context/artifact/stock-writer spies remain unused. |
| D3-AT-13 | Material identity is stable across clock drift, frame order and IDs; changes for revised source identity or separate historical volume. |
| D3-AT-14 | Protected-source diff and unchanged D2/legacy configuration hashes; inherited rotation persistence/JSON tests. |
| D3-AT-15 | New focused tests plus inherited 330-test aggregate, Ruff and Git checks; final counts below. |
| D3-AT-16 | Global registry FAIL on both clean D2 and D3 with the same 18 diagnostics, zero added/removed. |
| D3-AT-17 | Actual SQLite SQL, JSON size and measured fixture memory/time; no live capacity claim. |

## Compatibility and isolation

- Legacy sector hash: `26e0f6ea6099ea73ed29df8f7eb7260bbcfde75e0d560e87e485eb1624e8afcb`; legacy ETF blending remains false.
- D2 independent configuration digest: `4488f53e320f43daba6a257eea75e688e13b444b3bf911ecdff5eab9e946dfaa`; independent feature remains true.
- `config/sector_rotation.yaml`, `config/market_sector_intelligence.yaml`, Pine defaults and both stock technical YAMLs are byte-identical to D2 after normal Git CRLF conversion.
- Alembic retains the single head `0093_market_sector_rotation`; no D3 migration exists.
- Existing architecture registrations and generated registry document are untouched. The global check is not green: five pre-existing writer candidates and thirteen transaction boundaries remain unresolved.
- Original checkout pre/post status, staged/unstaged diffs and contents of all 1,422 changed/untracked files (468,379,433 bytes) are compared without changing that checkout. D1 and D2 remain clean at their original branch/SHA; `main` is unchanged.

## Limits and D4 handoff

Production PostgreSQL/schema activation, real ETF/SPY/QQQ cache completeness and basis compatibility, operational D2 capture permissions and live runtime/UI remain unverified. Fixtures do not establish live performance or production readiness. The adapter conservatively requires continuous positive-volume evidence and complete enabled indicators; actual cache deficiencies may make diagnostics unavailable. Model actions/classifications originated in stock engines and are not calibrated ETF forecasts.

D3 provides only current-as-of technical previews. It does not archive or backfill V4/V5 history, modify D2 snapshots or supply historical technical charts. D4 can consume D2 stored rotation and this separate current preview after the D3 checkpoint. No D4 work was performed.

The implementation handoff stopped before staging or committing. The separately authorized checkpoint is recorded below. Neither phase performs a push, PR, merge, deployment, provider request, production schema activation, NORMAL restart or production mutation.

## Independent D3 final checkpoint — 22 October 2026

**D3 CHECKPOINT: PASS — all D3-specific gates. Architecture registry: GLOBAL FAIL with unchanged baseline findings.**

This checkpoint independently reread every modified and new file, the complete source changes, the native V4/V5, leadership, weekly-confirmation and cached PIT loader call paths, and the tests. The earlier implementation results above were not accepted as proof. No implementation source or tests were changed during this checkpoint.

### Fresh acceptance results

| Gate | Independently observed result |
|---|---|
| Inherited regression suite | **329 passed in 88.89 s**: 264 D1 plus 65 D2, zero failures/errors/skips. |
| New D3 suite | **44 passed in 155.75 s**: 12 adapter, 29 service, 3 cached HTTP/PIT. Zero failures/errors/skips. |
| Disposable PostgreSQL | **1 passed in 11.00 s**: unchanged D2 migration/concurrent capture/immutability test. The fixture creates, identity-checks and drops only a `swinglens_pytest_*` database; its administrative connection selects `postgres`, never the application database. |
| Distinct aggregate | **374 passed = 264 D1 + 66 D2 + 44 D3**, zero failures/errors/skips and zero duplicate classname/test-name identities across the three new JUnit files. |
| Ruff | All **8** changed/new Python files pass; no automatic fixes. |
| Whitespace | Working-tree and complete nine-file staged diff pass `git diff --check` and `git diff --cached --check`. |
| Alembic | Single head **`0093_market_sector_rotation`**, unchanged; no D3 migration or schema change. |
| API security | 204 routes total; existing unsafe inventory stays 52, all classified: 48 NORMAL_ONLY, 2 CERTIFICATION_SESSION_SCOPED and 2 READ_ONLY. Two new endpoints are GET-only. |
| Architecture | Clean D2 and D3 both exit **1**, **GLOBAL FAIL**. Exact sorted findings match: **18 = 5 writers + 13 transaction sites**; zero added, removed or suppressed. The D3 baseline-relative gate passes. |

Only the existing Starlette/httpx warning and two existing Alembic configuration warnings appeared. These were not remediated or hidden.

Fresh evidence is ignored under `output/playwright/market-sector-intelligence-d3/`: `checkpoint-regression.xml`, `checkpoint-d3.xml`, `checkpoint-postgresql.xml` and `checkpoint-verification-summary.json`. The same documented commands were rerun with these new output filenames. JUnit suite durations are respectively 88.872, 155.737 and 11.003 seconds; table durations are pytest console measurements.

The fresh cached HTTP fixture again returns eleven ready V4/V5 rows using **26 SELECTs and no DML**, preserving a pending unflushed UploadRun. Provider, queue, stock scoring, artifact and pipeline-context spies remain unused; arbitrary uploads leave the full payload unchanged. Unsupported symbols and disabled preview make zero SQL calls. The observed request took 5.8045615 seconds, returned 82,569 bytes and sampled process RSS from 213,364,736 to 221,057,024 bytes. This is fixture evidence, not a production capacity claim.

### Independent correctness and isolation conclusions

- One frozen UTC cutoff and completed US session bound all thirteen preferred loader calls (twelve when QQQ context is disabled). Actual cached HTTP reads exercise revision replay, late receipts, future-session exclusion and a revised invalid close. Separate source-contract tests exercise incompatible bases, missing sessions and other invalid OHLCV.
- SPY is the benchmark; QQQ is market-regime context only. Every sector frame passed to relative-strength calculation is None. ETF-scoped configuration removes sector applicability without changing stock defaults, model weights or native results.
- V4 and V5 receive the identical Pine base object. Native raw/adjusted scores, classifications, actions, warnings and confidence are preserved. Sector-NA evidence adds no artificial sector-weak risk or material confidence downgrade.
- Native confirmed-week selection excludes the incomplete week. Daily/adaptive/HTF readiness and actual current beta/residual windows are checked before native fallback values can stand in for missing evidence. Nested nonfinite outputs fail closed.
- Leadership ranks exactly the fixed eleven ETFs. A single missing required cohort component prevents ranking and suppresses all final V5 scores and deltas, while valid V4 remains available. Material identities cover selected OHLCV, PIT fingerprints, separate volume and scoped policy; clock drift, database IDs, ordering and uploads do not alter unchanged evidence.
- All six pre-existing market-intelligence route/helper function ASTs match D2 exactly. The only changes to pre-existing tracked files are 33 router additions and the expected route-count assertion. Every other tracked file matches the exact D2 parent, including stock V4/V5/Pine, Fundamental/Combined/Ranking, Setup/Lifecycle/Alerts, Winner/CERI, run-specific rotation, D2 formulas/storage/capture, acquisition, workers, orchestration, models, migrations and architecture/configuration registries.
- The five Pine/V4/V5/sector/independent YAML files are byte-identical to clean D2. Both required legacy and independent configuration identities remain as recorded above.
- D1 and D2 remain clean at their recorded original branch/SHA. Original-checkout HEAD, status, index diff, working diff and all 1,422 changed/untracked file contents were fingerprinted before/after; `main` and other branch refs remain unchanged.

### Exact baseline architecture findings

The following complete diagnostic list is identical on clean D2 and D3. These remain unresolved global findings; none belongs to the new D3 adapter or service.

```text
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/market_calculation_context_service.py', 'create_pipeline_ceri_certification_context', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/decision_evidence.py', 'lock_setup_publication_sources', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/decision_evidence.py', 'prefetch_setup_validation_sources', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/repository.py', '_setup_source_evidence_ids', 'add')
- ARCH_WRITER_CANDIDATE_UNCLASSIFIED: unregistered writer candidate: ('app/services/setup_lifecycle/repository.py', 'visit', 'add')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/ceri/batched_job_handlers.py', 'execute_provider_ingest_batch_job', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'autonomous_session')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'begin_nested')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'flush')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/contextual_consumer_eligibility.py', 'certify_fundamental_admission_orm_contract', 'rollback')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/market_calculation_context_service.py', 'create_pipeline_ceri_certification_context', 'flush')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/setup_lifecycle/snapshot_builder.py', '_close_clean_parent_transaction_before_setup_build', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/setup_lifecycle/snapshot_builder.py', 'bounded_verification_checkpoint', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/setup_lifecycle/snapshot_builder.py', 'capture_snapshots_for_run', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_process_pool', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_process_pool', 'rollback')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_pure_sequential', 'commit')
- ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED: unregistered transaction site: ('app/services/technical_score_service.py', '_score_tickers_pure_sequential', 'rollback')
```

### Commit and D4 baseline

The checkpoint stages exclusively the nine files in the manifest above and creates one commit with message:

`feat(market-sector-intelligence): complete D3 technical V4 V5 diagnostics`

Its exact parent is `41e8eb3e33cca701952fb716b597da71526fd278`. Complete staged content is reviewed, checked against the reviewed working files and checked for whitespace before committing. Generated evidence, logs, databases, credentials and unrelated artifacts are excluded. The resulting SHA and clean D3 status are verified after commit and recorded in the final checkpoint response, avoiding a self-referential commit identifier in this report.

D3 is a suitable **development baseline for D4** from that verified commit. Production activation/schema state, real cache coverage and compatible bases, operational capture authority, live runtime/UI and production performance remain unverified. Stock-model outputs are research diagnostics and are not ETF-calibrated forecasts or trading recommendations. This checkpoint does not claim production readiness.

No D4 work, push, PR, merge, deploy, production migration, provider request or NORMAL restart was performed.
