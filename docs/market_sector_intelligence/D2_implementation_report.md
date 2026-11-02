# Market Sector Intelligence D2 implementation

**D2 IMPLEMENTATION: PASS — focused D2 acceptance gates.**

Date: 22 October 2026. This is implementation evidence, not production activation or a repository-wide architecture certification. The whole-repository architecture scan still fails with **18 pre-existing findings**, independently reproduced on D1; D2 introduces no remaining registry finding. These unrelated findings were not suppressed or fixed by changing protected pipeline code.

## 1. Baseline and isolation

- Branch: `feature/market-sector-intelligence-d2-rotation-engine`.
- Worktree: `C:/Users/Ivica/.codex/worktrees/market-sector-intelligence-d2/SwingLens`.
- Exact D1 base and implementation-stage HEAD: `9ad285e6cfc8c920f38c090960b7ac1907d25466`.
- D1 was clean at that commit before the new worktree/branch was created. It remains clean on `feature/market-sector-intelligence-d1-foundation` at the same SHA.
- Original checkout remains on `codex/ceri-authority-remediation`, HEAD `96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. Pre/post status, staged/unstaged diffs, and content fingerprints for all 1,422 modified/untracked files (468,379,433 bytes) match exactly. Ignored runtime data was not recursively scanned.
- `main` remains `aeb0ef4b32220ef178a4a0a810086a3b043a7abb`.
- At the implementation boundary, no staging or commit had been performed. D2 contained seven modified tracked files and fourteen new files, including this report. The separately authorized final checkpoint is recorded in section 9. Generated evidence is ignored under `output/playwright/market-sector-intelligence-d2/`.

## 2. Files and scope

| File | Responsibility / reason for change |
|---|---|
| `app/services/market_sector_intelligence_rotation_math.py` | New pure versioned ratios, exact calendar anchors, quadrants and ordinal ranking. |
| `app/services/market_sector_intelligence_rotation_service.py` | New twelve-series, one-cutoff preview; input validation, evidence extraction and deterministic identities. |
| `app/services/market_sector_intelligence_rotation_repository.py` | New complete-only atomic capture, material validation, idempotency/conflict handling and stored-only historical reads. |
| `app/services/market_sector_intelligence_capture_authority.py` | New D2-specific typed CLI authority and transaction-scoped insert guard; reuses existing runtime capability/role rules. |
| `app/models/market_sector_intelligence_tables.py` | New D2-only header/row models with constraints and ORM insert/update/delete protection. |
| `alembic/versions/20261022_0093_market_sector_rotation.py` | One additive migration, two new tables, indexes, uniqueness/FK/range/finite constraints and database immutability triggers. |
| `scripts/market_sector_intelligence_capture.py` | New explicit `--dry-run` / `--capture`; no force/backdate, migration, provider or worker option. |
| `app/services/market_sector_intelligence_config.py` | Minimal D1 extension: version-locked rotation rules and immutable default rule view; old D1 configuration remains loadable. |
| `config/market_sector_intelligence.yaml` | Adds only the independent `rotation_engine` section. |
| `app/routers/market_sector_intelligence_routes.py` | Adds four safe GET routes; original coverage handler behavior retained. |
| `app/models/__init__.py` | Shared-file change limited to exporting/registering the two new models in the established metadata layout. No existing model changed. |
| `config/architecture_registry.json` | Narrow required registration: two D2 tables, writer `WRITE-083`, transaction `TX-27`, capture's add/begin/flush/rollback sites and direct test references. Existing entries preserved. |
| `docs/audit/whole-application-closure/GENERATED_ARCHITECTURE_REGISTRY.md` | Updates generated inventory counts for the new D2 registrations only. |
| `tests/test_route_security.py` | Expected route count 198 → 202; unsafe route count/classifications remain unchanged at 52. |
| `tests/market_sector_rotation_helpers.py` | Explicit synthetic frames, PIT manifests and clock fixtures. |
| `tests/test_market_sector_intelligence_rotation_math.py` | 18 pure mathematical, sign, ranking, finite-value and calendar tests. |
| `tests/test_market_sector_intelligence_rotation_service.py` | 23 input, cutoff, basis, completeness, configuration and provenance tests. |
| `tests/test_market_sector_intelligence_rotation_storage.py` | 20 migration, authority, atomicity, retry/conflict/concurrency, history and CLI tests. |
| `tests/test_market_sector_intelligence_rotation_pit.py` | Four actual-loader SQL/PIT/API/dry-run/immutability tests against disposable SQLite. |
| `tests/integration/test_market_sector_intelligence_rotation_postgresql.py` | Full disposable PostgreSQL upgrade, additive schema check, concurrent capture and immutability proof. |
| `docs/market_sector_intelligence/D2_implementation_report.md` | This report. |

Unchanged against D1: legacy sector YAML/hash/taxonomy, D1 universe and coverage implementation, existing run-sector route/services, price-bar selection/revisions, market clock, stock Technical V4/V5, Fundamental/Combined/Ranking, Setup/Lifecycle/Alerts, Winner/CERI, pipeline/workers/providers, original model definitions and old migrations. Existing runtime/domain mutation-authority modules were not changed or weakened.

## 3. Methodology and data integrity

Algorithm: **`market-rotation-v1`**. Required dates are actual US trading-session offsets **0, 5, 21, 42, 63**, using the existing calendar. Every symbol is loaded once with the same `max_session` and UTC `as_of` through the existing preferred cache loader; no calculation context or run ID is supplied.

With `Q[k] = ETF[k]/SPY[k]`, compute `100*(Q[0]/Q[k]-1)` at 5/21/63, prior21 `100*(Q[21]/Q[42]-1)`, momentum = current21 minus prior21, and separate 63-session ETF/SPY absolute returns. Percentage values and percentage-point acceleration are not mixed or blended into a score. All terms must be finite; no intermediate rounding is performed. Quadrant signs use unrounded 63-session RS and momentum, counting zero as nonnegative. Ranking is descending RS63, RS21, RS5, then ascending ETF ticker.

An absent, stale, duplicated, malformed, historically invisible or incompatible-basis input remains visible with null metrics and rank. Invalid SPY invalidates the entire cohort. Selected basis must be known and match SPY; neither basis is claimed to be certified total return. Missing a required anchor is not repaired by selecting a different cached row. COMPLETE/PARTIAL/UNAVAILABLE/DISABLED and eligible/total counts are explicit.

Selected anchor dates/closes and existing immutable PIT fingerprints are retained. Digest material excludes invocation cutoff time, database-generated IDs, uploads and irrelevant cache coverage. The loader's manifest boundary is checked; post-cutoff acquisitions are rejected rather than granted pipeline ownership. Required-anchor provenance changes affect identity even when the numerical score would be unchanged.

### Configuration identities

- Legacy `etf_score.enabled` remains **false**; independent enablement remains **true**.
- Unchanged legacy hash: `26e0f6ea6099ea73ed29df8f7eb7260bbcfde75e0d560e87e485eb1624e8afcb`.
- Prior D1 independent digest: `c821b44f8fbbff852f33d68e462d5a80d92ccefff7086abb78c98147c048b725`.
- D2 independent digest: `4488f53e320f43daba6a257eea75e688e13b444b3bf911ecdff5eab9e946dfaa`.

The independent digest change is intentional and limited to the new versioned rules. Arbitrary offsets, rank rules, versions or invalid rule types are rejected.

## 4. Capture, schema and API

Migration head: **`0093_market_sector_rotation`**, parent observed locally as **`0092_phase2_eligibility_ledgers`**. New tables: `market_sector_rotation_snapshots` and `market_sector_rotation_rows`. No old table or record is reused.

Only explicit CLI `--capture` writes. It checks the configured CLI process role and existing `NORMAL_ONLY` runtime capability before capture work; web/worker/certification/wrong-operation/untyped authorities are rejected. It does not force a process role, mint a pipeline context or add a job. Preview's read session closes before a fresh capture transaction. The repository revalidates current session, configuration, exact anchors, metrics, ranks and digests before writing.

COMPLETE writes exactly one header and eleven rows in one transaction. Partial/disabled capture returns NOT_READY without writing. Natural-key uniqueness is `(market_session, algorithm_version, configuration_digest)`. An exact repeat returns ALREADY_EXISTS without DML even when wall time advances. Changed required material/provenance returns EVIDENCE_CONFLICT. PostgreSQL uniqueness plus rollback/re-read handles racing captures; SQLite serializes its explicit writer transaction. Injected row failure rolls back header and rows. ORM guards require the sealed D2 capture transaction; database triggers reject update/delete, including direct SQL.

Safe GET endpoints, under `/api/market-sector-intelligence/rotation/`:

- `preview`: fresh read-only current cached evaluation, eleven sector slots.
- `latest`: newest stored snapshot; typed NO_ROTATION_SNAPSHOT 404 when empty.
- `history?limit=30`: stored summaries, limit 1–100, descending session/ID order, empty list when absent.
- `snapshots/{snapshot_id}`: frozen stored evidence and eleven rows; typed 404 when absent.

Historical reads do not recompute financial metrics or query prices. Uncaptured dates are not interpolated. CLI help was executed and exposes only `--dry-run` / `--capture`.

## 5. Implementation verification (before final checkpoint)

**330 distinct tests passed; 0 failed, 0 errors, 0 skipped.** This comprises the inherited **264 D1 regressions**, **65 new pure/SQLite D2 tests**, and **one disposable PostgreSQL integration test**. The combined regression run passed 328 in 38.02 seconds. A final storage run passed 20 in 8.05 seconds, including one newly added disabled-capture test (19 overlap with the combined run). The PostgreSQL test passed in 13.04 seconds and its final recheck passed in 11.18 seconds. JUnit identities were deduplicated to verify the distinct total.

Ruff passed on all 17 changed/new Python files. `git diff --check` passed. Warnings are existing Starlette/httpx and Alembic path-separator deprecations; they were not addressed by unrelated source changes.

| Acceptance | Concrete evidence |
|---|---|
| AT-01 | Exact D1 base, clean D1 worktree, new D2 branch/worktree; pre/post isolation fingerprints match. |
| AT-02–04 | Eleven rows without uploads, twelve shared bounded reads, hand-computed ratios/units, all four signs, zero boundaries and deterministic tie-breaks. |
| AT-05–06 | Missing/stale SPY and ETF, a missing 42 anchor despite 64 rows, bad/duplicate/future sessions, invalid close and mixed basis fail closed. Actual loader excludes late receipts/future bars and replays a historical corrected close. Existing holiday/DST/early-close tests also pass. |
| AT-07–08 | Adding unrelated uploads leaves full preview/fingerprints identical; preview/dry-run/history emit SELECT only; disabled preview and disabled capture emit zero SQL. Provider connections and enqueue are instrumented as forbidden. Pending ORM changes are not autoflushed. |
| AT-09–11 | 10/11 capture writes zero rows. Complete capture writes 1+11 with one commit. Fault rollback leaves zero rows; time-advanced retry writes no DML; changed material conflicts; two concurrent callers share one snapshot in SQLite and PostgreSQL. |
| AT-12 | Saved details are unchanged after actual cached closes mutate; historical API calculator is instrumented as forbidden. |
| AT-13 | SQLite migration upgrade/downgrade preserves a sentinel old table. The full PostgreSQL chain upgraded a verified disposable database to D1's observed schema head, then to D2; exactly two tables were added. PostgreSQL concurrent capture and trigger rejection passed. The disposable database was dropped by the existing safe fixture. |
| AT-14–15 | All 264 inherited regressions pass; legacy hash is unchanged; protected-source diff is empty. |
| AT-16 | Honest empty-cache/disabled results and missing latest/history behavior are exercised. |

### Measured fixture resource evidence

- Twelve series / 768 synthetic daily observations in the full cached-price fixture; five anchors per symbol retained for calculation.
- Full revision-free preview: **24 SELECTs**, zero DML; existing revision replay can add source-history reads.
- Historical latest/detail: **two SELECTs**; bounded history reads one header query.
- Created capture: **one header INSERT + eleven row INSERTs, one commit**. Repeat and conflict: zero DML.
- Instrumented preview: **1.118 seconds** with tracemalloc enabled, **1,709,572 bytes** peak traced Python allocation; HTTP JSON **17,621 bytes**. These are disposable fixture measurements, not production latency/RSS claims.
- Synthetic exported persisted JSON: approximately **18.7 KB** before indentation, plus the structured metric rows in storage.

### Architecture registry result

`python -B scripts/check_architecture_registry.py --check` was run on both the clean D1 worktree and D2. D1 reports 18 pre-existing writer/transaction census findings in market context, contextual readiness, Setup, Technical and CERI code. D2 initially added six findings for its new tables/writer/sites. After narrowly registering WRITE-083 and TX-27, D2 reports the **same 18 baseline findings and no D2 finding**. The command still exits 1; this report does not label that whole-repository check green. Broad regeneration of production censuses or changes to those protected writers were deliberately avoided.

## 6. Reproduction commands and local evidence

Commands were run from the D2 worktree using the existing shared virtual environment:

```powershell
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_config.py tests/test_market_sector_intelligence_coverage.py tests/test_market_sector_intelligence_routes.py tests/test_sector_rotation_config.py tests/test_sector_taxonomy.py tests/test_us_market_calendar.py tests/test_session_temporal_integrity_remediation.py tests/test_sector_etf_rotation_service.py tests/test_sector_rotation_service.py tests/test_sector_rotation_policy.py tests/test_sector_rotation_routes.py tests/test_gui_phase1.py tests/test_settings.py tests/test_route_security.py tests/test_technical_score_v4.py tests/test_technical_v5_calibration.py tests/setup_lifecycle/test_snapshot_builder.py tests/winner_probability/test_feature_schema.py tests/test_market_sector_intelligence_rotation_math.py tests/test_market_sector_intelligence_rotation_service.py tests/test_market_sector_intelligence_rotation_storage.py tests/test_market_sector_intelligence_rotation_pit.py --junitxml=output/playwright/market-sector-intelligence-d2/regression.xml
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/test_market_sector_intelligence_rotation_storage.py --junitxml=output/playwright/market-sector-intelligence-d2/storage-final.xml
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B scripts/market_sector_intelligence_capture.py --help
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B scripts/check_architecture_registry.py --check
& 'C:\Users\Ivica\Documents\SwingLens\.venv\Scripts\python.exe' -B -m alembic heads
git diff --check
```

PostgreSQL was invoked as the following test in a child process with `SWINGLENS_TEST_POSTGRES_ADMIN_URL` resolved from the existing local configuration without printing credentials, `DATABASE_URL` directed to the `postgres` administrative database, and `SWINGLENS_DATABASE_SAFETY_CONTEXT=DISPOSABLE_TEST`. The existing fixture generated and verified a separate `swinglens_pytest_*` database. No product CLI capture or migration was run against the administrative/application database.

```powershell
python -B -m pytest -q -p no:cacheprovider -o junit_family=legacy tests/integration/test_market_sector_intelligence_rotation_postgresql.py --junitxml=output/playwright/market-sector-intelligence-d2/postgresql-final.xml
```

Ruff's explicit target list was the tracked changed Python paths plus `git ls-files --others --exclude-standard -- '*.py'`; it included the model, migration, CLI, helpers and every new test. Final result: `All checks passed!`.

Generated evidence, ignored by Git: `regression.xml`, `storage-final.xml`, `postgresql.xml`, `postgresql-final.xml`, `pit.xml`, `verification-summary.json`, `synthetic-preview.json` and `synthetic-snapshot.json` under `output/playwright/market-sector-intelligence-d2/`.

## 7. Synthetic response evidence

**Synthetic test values only, not market observations.** The exported preview and persisted JSON contain all eleven rows and full selected anchor evidence. Example from their first sector (XLB), shown with rounded display values only here:

```json
{
  "source_type": "PREVIEW",
  "algorithm_version": "market-rotation-v1",
  "market_session": "2026-10-21",
  "cohort_status": "COMPLETE",
  "eligible_count": 11,
  "total_sector_count": 11,
  "etf_symbol": "XLB",
  "rs_5_pct": 2.4505928854,
  "rs_21_pct": 4.1322314050,
  "rs_63_pct": 9.0909090909,
  "rs_prior_21_pct": 1.7687074830,
  "relative_momentum_pp": 2.3635239220,
  "etf_return_63_pct": 20.0,
  "spy_return_63_pct": 10.0,
  "rotation_state": "Leading",
  "rank": 1
}
```

The persisted example adds `snapshot_id=1`, `source_type=PERSISTED`, UTC capture/cutoff timestamps and snapshot digest `5d3fd9eba76788afbb330e1167f63445c4b2a49d60ddd198d9fbd2d4830685a4`. It was created only in a disposable in-memory SQLite fixture with an injected test clock. The public CLI provides no backdating parameter. Actual APIs/DB payloads retain unrounded floating-point values.

## 8. Limits and handoff

Disposable PostgreSQL migration/capture compatibility is now verified; **production PostgreSQL/schema activation and real ETF cache/basis coverage remain unverified**. No real application data was inspected for leadership, fetched, backfilled or modified. No runtime browser/UI smoke was performed for D2; inherited Settings/API tests pass and the full dashboard belongs to D4. The 18 pre-existing repository-wide architecture findings remain a separate unresolved baseline issue.

The D2 worktree is ready for review and a separate checkpoint decision. D3 can build its isolated ETF Technical V4/V5 diagnostics on the versioned inputs, immutable complete snapshots and stored APIs after D2 review. **ETF V4/V5 diagnostics and D3/D4 features are not implemented.**

**At the implementation boundary: original checkout and D1 checkpoint unchanged; no commit, push, PR, merge, deployment, provider request, NORMAL restart or production database mutation.**

## 9. Independent final checkpoint — 22 October 2026

**D2 CHECKPOINT: PASS.** The final checkpoint independently inspected the actual source, all new tests and the complete D2 diff against the exact D1 base. No D2 source correction or new feature was needed. D3 was not started. The user explicitly authorized this separate checkpoint commit; implementation-stage no-commit statements above describe the earlier boundary.

### Fresh gate results

| Gate | Independently observed checkpoint result |
|---|---|
| Git isolation | Expected branch/worktree; pre-commit HEAD exactly `9ad285e6cfc8c920f38c090960b7ac1907d25466`; initially empty index; seven modified and fourteen new intended D2 files. |
| Regression suite | **329 passed**, zero failures/errors/skips, in **107.13 seconds**: all **264 D1 regressions** plus **65 D2 pure/SQLite tests**, in one invocation of the combined command in section 6, using `checkpoint-regression.xml`. |
| Disposable PostgreSQL | **1 passed**, zero failures/errors/skips, in **21.55 seconds**, using `checkpoint-postgresql.xml`. Full chain through 0092 then 0093, exactly two added tables, concurrent same-key capture produces one header/eleven rows and one shared ID, database mutation triggers reject updates. Existing fixture drops its verified disposable database. |
| Distinct total | JUnit test identities independently counted: **330 distinct**, **0 duplicates**, **0 failed**, **0 errors**, **0 skipped**. |
| Ruff | **All checks passed**, all **17** changed/new Python files explicitly targeted, without fixes. |
| Whitespace | `git diff --check` passed; the staged diff is also checked before commit. |
| Alembic | Exactly one head, `0093_market_sector_rotation`, with parent `0092_phase2_eligibility_ledgers`; no additional migration branch or conflicting head. |
| Architecture registry | **Global FAIL**, exit **1** on both clean D1 and D2: **18 identical findings**, zero added and zero removed; entire diagnostic outputs match. This is only a baseline-relative D2 gate pass, never a clean global pass. |
| Registry registration diff | Only two tables, WRITE-083, one capture writer site, TX-27, three transaction sites and TX-27 test references were added. Every existing registry entry is unchanged. Generated document changes only the four inventory counts. |
| Protected pipeline | Complete tracked changed-file list is restricted to the seven shared D2 integration/config/registry/test paths in section 2. No protected pipeline, scoring, acquisition, existing run-sector, old model or old migration file changes. |
| Checkout preservation | Checkpoint pre/post verification fingerprints match the original checkout's branch, HEAD, main ref, status, staged/unstaged diffs and contents of all 1,422 changed/untracked files (468,379,433 bytes). D1 stays clean at the exact base SHA. |
| Commit scope | One checkpoint commit with exact subject `feat(market-sector-intelligence): complete D2 rotation engine`, containing only the 21 files enumerated in section 2; no generated evidence or local runtime artifacts. The resulting SHA is reported after the commit, outside this self-contained report. |

The fresh regression invocation reported one existing Starlette/httpx deprecation; PostgreSQL reported that same warning plus two existing Alembic path-separator warnings. No test was skipped.

### Independent source review

The fixed universe comes from unchanged D1 configuration/universe code, never uploads. All twelve preferred cache reads share one completed-session cutoff and omit pipeline context authority. Exact 0/5/21/42/63-session anchors, valid finite positive closes, selected price-basis agreement and PIT manifest boundaries are required. Ratio-of-ratios percent returns, percentage-point momentum, unrounded quadrant signs and deterministic ordinal tie-breaking match the specification.

All four new HTTP routes are GET reads. Preview and stored reads suppress autoflush; history reads stored data and checks saved integrity without price access or metric recomputation. The only intended writer is the explicit CLI. It resolves configured NORMAL_ONLY/CLI authority before database work, closes preview's read session and uses a fresh capture session. Capture revalidates the current session, configuration, full eleven-sector cohort, exact anchors, metrics, ranks and digests. Incomplete input writes nothing.

The additive migration and models agree on keys, constraints, types and indexes. ORM insert guards bind the exact capture session/transaction and sealed evidence; ORM and database update/delete guards make saved records immutable. One transaction writes the header and eleven rows. SQLite serializes this writer; PostgreSQL uses natural-key uniqueness with rollback before race re-read. Identical evidence returns ALREADY_EXISTS, changed evidence returns EVIDENCE_CONFLICT and neither overwrites saved history. Failure-injection and concurrent-capture tests pass.

The legacy sector configuration hash remains `26e0f6ea6099ea73ed29df8f7eb7260bbcfde75e0d560e87e485eb1624e8afcb`, with legacy ETF blending disabled. D1's independent digest remains reproducible when the optional D2 section is omitted. D1 coverage, Settings and inherited regressions pass.

### Exact architecture baseline comparison

Each finding below appears identically on both clean D1 and D2. None was suppressed, concealed, fixed or reclassified by this checkpoint.

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

### Final boundary and D3 handoff

D2 is suitable as a **development baseline for D3** after this checkpoint commit. This is **not production readiness**: production PostgreSQL/schema activation, real eleven-ETF/SPY cache completeness and basis compatibility, operational capture permissions and runtime/UI smoke remain unverified. No push, PR, merge, deploy, production migration, provider request or NORMAL restart was performed. Stop after D2.
