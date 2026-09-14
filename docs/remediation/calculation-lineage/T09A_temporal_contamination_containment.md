# T09A — Temporal Contamination Containment

## 1. Baseline and branch

- Audited baseline: `3a9d47063be996908b7d5d1cc5769e0bbd033546`
- Starting HEAD: `3a9d47063be996908b7d5d1cc5769e0bbd033546`
- Branch: `codex/t09a-temporal-contamination-containment`
- Final HEAD: the remediation commit containing this report; its exact SHA is recorded in the T09A completion response because a commit cannot contain its own SHA.
- Migration head before and after remediation: `0074_ceri_evidence_quarantine`
- Verification runtime: Python 3.12.2 from the repository `.venv`

The pre-existing untracked audit/registry files, `scripts/ops/ib_historical_probe.py`, and `tests/ops/test_ib_historical_probe.py` were preserved and were not staged. The registry and audit reports were not edited.

## 2. Scope

Included findings:

- `XINT-002`: future/current source evidence entering historical IBMI calculations.
- `XINT-003`: only the historical wall-clock substitutions required for this containment (IBMI freshness and missing historical anchors).
- `XINT-008`: only missing upper bounds in the affected IBMI, CERI, and lifecycle selectors.
- `CERI-003`, `CERI-004`.
- `SETUP-002`, `SETUP-007`.

Explicitly excluded:

- Phase 1 calculation identity, including `RANK-004`.
- Phase 2 immutable artifact/evidence architecture.
- Phase 3 global readiness contracts, including `CORE-009`.
- Phase 4 effective configuration and fingerprint architecture, including `RANK-005`.
- Phase 5 entry-point unification.
- Phase 6 Winner maturation target freezing and CERI retry/refresh identity, including `CERI-012`.
- Phase 7 complete replay/reconstruction semantics.
- `RANK-003`, `WIN-001`, `WIN-007`, `WIN-009`, `XINT-009`, and unrelated cleanup.

## 3. Root causes

### IBMI temporal leakage

Historical feature rebuilding loaded every stored metric row for a ticker/type, selected the latest live snapshot without a session or possession upper bound, used current `PriceBar` rows directly for dollar volume, and measured live freshness against `datetime.now(UTC)`. An old `as_of_session` was therefore only an output label; it did not constrain all constituents or the freshness reference clock.

### CERI temporal leakage

The price-response service permitted unbounded database `PriceBar` reads and arbitrary injected arrays when no cutoff was supplied. The feature rebuild could synthesize a current wall-clock context when no historical anchor was present. The change rebuild accepted a forwarded historical scope but selected mutable current catalyst revisions and guidance without constraining effective session and source possession time.

### Setup/Lifecycle future-state leakage

The episode selector chose the currently active row by descending ID, even when its lifecycle business dates were later than an older repair target. The alert cooldown query had a lower bound but no upper bound, so a future alert could suppress an older replay. Operational `created_at` was not the alert business time, but the old selection did not make that distinction explicit.

## 4. Implementation

### IBMI

| Changed path | Old source selection | New source selection | Business time | Knowledge time | Failure behavior |
|---|---|---|---|---|---|
| Historical metric features | All rows for ticker/type | `effective_session <= as_of_session` and `first_seen_at <= cutoff`; post-cutoff revisions are projected from `IBHistoricalMetricRevision` | `effective_session` | `first_seen_at`; revision `observed_at` | Exclude rows whose initial or pre-cutoff revised value cannot be proven |
| Live shortable/options snapshots | Latest `observed_at` | `effective_session <= as_of_session` and `observed_at <= cutoff`, ordered by `observed_at DESC, id DESC` | `effective_session` | `observed_at` | Return no snapshot; calculators preserve existing unavailable/insufficient semantics |
| Shortable history | Latest available rows | Same session and observation upper bounds as the selected snapshot | `effective_session` | `observed_at` | Omit ineligible observations; never fall back to newest |
| Historical availability status | Latest request by start time | Completed request with `completed_at <= cutoff`, deterministic `completed_at DESC, id DESC` | Requested metric/range | `completed_at` | Existing row-derived availability or `UNKNOWN`/`UNAVAILABLE` semantics |
| Liquidity dollar volume | Twenty current mutable `PriceBar` rows | Canonical `load_preferred_ohlcv_frames(max_session=as_of_session, as_of=cutoff)` with revision projection | `PriceBar.bar_date` trading session | Canonical PIT `created_at`/`first_seen_at` and revision `observed_at` contract | Return `None` if an eligible aligned price/volume frame is unavailable |
| Live freshness | `datetime.now(UTC) - observed_at` | `calculation_cutoff_at - observed_at` | Feature target/cutoff | Snapshot `observed_at` | Deterministic stale/available status relative to the declared calculation |

Current provider refreshes and live captures remain current-mode operations. They freeze an explicit cutoff after acquisition and label the call `CURRENT`; direct historical rebuilding defaults to `HISTORICAL`, requires an aware cutoff, and validates the requested session with the market calendar. The unbounded `_latest_close` remains only in current histogram capture.

### CERI

| Changed path | Old source selection | New source selection | Business time | Knowledge time | Failure behavior |
|---|---|---|---|---|---|
| Price response | Optional cutoff; current DB rows or arbitrary injected bars | Explicit `CURRENT` mode, or `HISTORICAL` with both target session and aware cutoff; database reads use session/first-seen bounds plus canonical revision projection; injected historical bars must independently prove PIT eligibility | `PriceBar.bar_date` and event effective session | `PriceBar.first_seen_at`/`revised_at`; revision `observed_at` | Reject missing/partial historical anchors and unproven injected historical arrays |
| Feature rebuild boundary | Missing anchor silently created a current market context | Supplied cutoff derives the latest completed session; supplied valid session derives its deterministic close-plus-15-minute cutoff; no anchor is rejected | Exchange `as_of_session` | Explicit or deterministically derived `cutoff_at` | Fail closed when both are absent or inconsistent |
| Catalyst change rebuild | Mutable `is_current` revisions | All revisions are eligibility-filtered, grouped by event, then the highest eligible `(revision_number, id)` is selected with its prior eligible revision | `effective_session`; `announced_at` must also be no later than cutoff | Source `retrieved_at`, falling back conservatively to `ingested_at` under the existing PIT contract | Exclude missing-session, missing-source, or post-cutoff evidence |
| Guidance change rebuild | Guidance read was not constrained by forwarded cutoff | Requires `effective_session <= target`, `effective_at <= cutoff`, `accepted_at <= cutoff`, and eligible referenced source possession | `effective_session` and `effective_at` | Source `retrieved_at`, otherwise `ingested_at`; `accepted_at` is an additional decision-availability gate | Exclude evidence whose business or possession eligibility cannot be proven |

`changed_since` continues to use `created_at` only as an operational work-selection filter. It is not treated as the historical evidence boundary; the independent session/cutoff gates always apply.

### Setup/Lifecycle

| Changed path | Old source selection | New source selection | Business time | Knowledge time | Failure behavior |
|---|---|---|---|---|---|
| Active episode for repair/evaluation | Current active row by `id DESC` | Requires `opened_on`, `current_as_of_date`, and `last_observed_on` no later than the target; deterministic `current_as_of_date DESC, opened_on DESC, id DESC` | Lifecycle episode business dates | The snapshot/evaluation supplies the explicit target date | If only a newer active episode exists, reject rather than create or mutate older state |
| Latest closed episode | Latest closed row without target upper bound | `closed_on <= target`, then `closed_on DESC, id DESC` | `closed_on` | Target snapshot/evaluation date | No future closed episode is eligible |
| Repair API | Optional `as_of_date` | Historical repair requires `as_of_date` | Requested repair target | Explicit caller anchor | Reject missing anchor |
| Alert cooldown | `effective_date >= lower_bound` | `evaluation_date - cooldown <= effective_date <= evaluation_date`, ordered `effective_date DESC, id DESC` | `SignalAlertEvent.effective_date` | Not `created_at`; creation remains operational metadata | Future alert is invisible to the older replay |

## 5. Temporal contract table

| Subsystem/path | Operation mode | Required session | Required cutoff | Effective/business time field | Knowledge/possession field | Fallback permitted? | Failure behavior |
|---|---|---:|---:|---|---|---|---|
| IBMI metric feature acquisition | Historical | Yes | Yes | `effective_session` | `first_seen_at`, revision `observed_at` | No | Exclude/unavailable |
| IBMI live snapshot feature input | Historical | Yes | Yes | `effective_session` | `observed_at` | No | Omit component/unavailable |
| IBMI PriceBar dollar volume | Historical | Yes | Yes | `bar_date` via market calendar | Canonical PriceBar PIT/revision fields | No | `None`/insufficient |
| IBMI provider refresh/live capture | Current | Explicit current session from frozen context | Explicit current cutoff | Source effective session | Observation/persistence time | Current evidence only | Existing current-mode error semantics |
| CERI price response acquisition | Historical | Yes | Yes | `bar_date`, event effective session | PriceBar first-seen/revision history | No | Reject or omit unproven bars |
| CERI price response pure/prepared input | Current | No historical claim | No historical cutoff | Caller-provided current arrays | Caller current-mode contract | Yes, only when explicitly `CURRENT` | Reject ambiguous mode |
| CERI feature rebuild | Historical | Yes or deterministically derivable from cutoff | Yes or deterministically derivable from valid session | Exchange session | Frozen cutoff | No current clock | Reject absent/inconsistent anchor |
| CERI change rebuild catalyst/guidance | Historical | Yes | Yes | Artifact effective session/time | Source retrieved/ingested time; guidance acceptance time | No | Exclude or reject request |
| Setup episode selection/repair | Historical | Yes | Date-granularity target is the explicit boundary | Episode lifecycle dates | Target snapshot/repair date | No | Reject when only future active state exists |
| Alert cooldown | Historical/current evaluation | Yes | Evaluation date | Alert `effective_date` | Evaluation date | No future fallback | Ignore future alerts |

## 6. Tests

New IBMI regressions in `tests/ib_market_intelligence/test_ibmi_temporal_containment.py` prove:

- The exact Task-07 Jan-05 feature / Jan-10 liquidity observation positive control is rejected and cannot yield the contaminated `1.0 / VERY_POOR / AVAILABLE` result.
- Effective-session and possession-time gates are both required.
- A metric revised after cutoff reconstructs its pre-cutoff values.
- Future shortable and options snapshots, including the no-eligible-source case, do not fall back to latest.
- Future availability observations are absent from short-pressure history.
- Dollar volume forwards the original session and cutoff to the canonical PIT reader.
- Historical freshness and output remain invariant when the execution wall clock changes.
- Missing historical cutoff fails closed.

New CERI regressions in `tests/ceri/test_ceri_temporal_containment.py` prove:

- Missing or partial historical price-response anchors fail closed.
- Unbounded/injected current PriceBars cannot claim historical semantics.
- A post-cutoff PriceBar revision is reconstructed and changes the asserted response metric to the historically known value.
- Explicit current calculation remains functional.
- Feature and change rebuilds reject missing anchors.
- Post-cutoff catalyst and guidance rows are excluded, while the eligible prior catalyst revision is selected even though it is no longer `is_current`.
- Identical historical change rebuilds select identical evidence under different mocked wall clocks.

Setup/lifecycle regressions prove:

- An older repair after a newer active episode raises before mutation.
- Active/prior-state SQL contains all business-date upper bounds.
- Same-session selection uses deterministic business-date/ID ordering and remains eligible.
- A future alert cannot suppress an older replayed alert.
- A valid alert in the bounded cooldown interval suppresses as intended.
- Cooldown uses `effective_date`, including when `created_at` is later.
- Repair without an explicit historical `as_of_date` fails closed.

Existing current-mode, background-handler, pipeline/resume, market-data PIT, and service tests were updated only where the now-explicit contracts required an argument.

## 7. Verification results

### Static verification

- `.venv\Scripts\python.exe -m ruff check <all changed Python files>`: PASS, no findings.
- `git diff --check`: PASS. Git emitted only the repository's LF-to-CRLF checkout notices.

### Focused containment

Command:

```text
.venv\Scripts\python.exe -m pytest -q tests/ib_market_intelligence --ignore=tests/ib_market_intelligence/test_external_smoke.py tests/ceri tests/setup_lifecycle tests/test_run153_systemic_remediation.py tests/test_market_data_prewarm.py tests/setup_lifecycle/test_setup_lifecycle_job_handlers.py
```

Result: **799 passed, 0 failed, 0 skipped, 0 deselected, 1 warning** in 105.30 seconds.

A pre-certification execution of the same command found one invalid Sunday-cutoff fixture: 798 passed and 1 failed. The fixture was corrected to use the next completed trading-session cutoff; the clean 799-test result above is the final certification run.

### Background, pipeline, and disposable-PostgreSQL integration selection

Command:

```text
.venv\Scripts\python.exe -m pytest -q -rs tests/integration/test_ib_market_intelligence_persistence.py tests/integration/test_ib_market_intelligence_runtime_resilience.py tests/integration/test_ceri_pit_postgresql.py tests/integration/test_ceri_evidence_quarantine_postgresql.py tests/integration/test_ceri_batched_workflow_v2.py tests/integration/test_lifecycle_immutability_postgresql.py tests/integration/test_market_data_prewarm_postgresql.py tests/test_background_worker.py tests/test_background_queue.py tests/test_background_job_service.py tests/test_pipeline_service.py tests/test_pipeline_executor.py tests/test_golden_pipeline.py tests/ceri/test_sec_pipeline_preflight.py tests/setup_lifecycle/test_setup_lifecycle_job_handlers.py tests/ceri/test_batched_workflow_v2.py tests/ceri/test_ceri_orchestration.py
```

Result: **151 passed, 0 failed, 16 skipped, 0 deselected, 1 warning** in 4.47 seconds. All 16 skips state that the disposable PostgreSQL admin database at `127.0.0.1:5432` rejected the configured test credentials. These are environment skips, not implementation failures. No production database was contacted or mutated.

### Broader repository run

Unfiltered non-external command:

```text
.venv\Scripts\python.exe -m pytest -q -m "not external"
```

Result: **2,439 passed, 3 failed, 142 skipped, 7 deselected, 24 warnings, 21 setup errors** in 548.68 seconds. The 21 errors are browser/E2E or disposable-PostgreSQL environment setup failures. The three failures are in unchanged runtime/observability tests; two reproduce in isolation (`test_complete_runtime_state_with_gone_pid_is_stale_not_corrupt` and `test_metrics_off_worker_and_supervisor_start_no_prometheus_components`), while `test_metrics_off_web_lifespan_starts_no_prometheus_collectors` passed on immediate isolation rerun. None imports or exercises a changed T09A path.

Clean broad non-external, non-E2E, non-PostgreSQL lane with those three disclosed baseline nodes deselected:

```text
.venv\Scripts\python.exe -m pytest -q -m "not external" --ignore=tests/e2e --ignore=tests/integration --deselect=tests/ops/test_lifecycle_safety.py::test_complete_runtime_state_with_gone_pid_is_stale_not_corrupt --deselect=tests/test_observability_review2.py::test_metrics_off_web_lifespan_starts_no_prometheus_collectors --deselect=tests/test_observability_review2.py::test_metrics_off_worker_and_supervisor_start_no_prometheus_components
```

Result: **2,423 passed, 0 failed, 7 skipped, 10 deselected, 22 warnings** in 421.91 seconds.

Warnings are the existing Starlette/httpx deprecation and Python 3.12 SQLite datetime adapter deprecations. External/provider smoke tests remained excluded because they require live services.

## 8. Residual risks

- T09B domain-write fencing must address stale/concurrent worker commits; this task bounds evidence selection but does not create a global write fence.
- T09C retains Winner publication remediation.
- Phase 1 must introduce complete calculation identity rather than the small explicit mode/session/cutoff contracts used here.
- Phase 2 must provide immutable lifecycle and evidence history. For legacy mutable state that cannot be reconstructed, T09A deliberately fails closed.
- Phase 5 must unify all entry points; T09A hardens the affected production/direct paths without redesigning orchestration.
- Phase 7 must define full reconstruction semantics and legacy recovery. This task does not manufacture unavailable historical truth.
- PostgreSQL-specific integrations could not run because the disposable test database credentials were unavailable. The affected semantic tests and repository-query contract tests all passed without skips.

## 9. Database impact

- Migration required: **NO**
- Data rewrite required: **NO**

Existing effective-session, observation/possession, revision, lifecycle business-date, and alert effective-date fields were sufficient. No migration was created, no historical values were fabricated, and no production data was read or written.

## 10. Required self-review and certification verdict

The required search covered changed and adjacent IBMI, CERI, lifecycle, and canonical PriceBar PIT paths for `datetime.now`, `date.today`, `utcnow`, `latest`, `newest`, `current`, descending selection/`first()`, `PriceBar`, active episodes, cooldown, `as_of`, `cutoff`, effective session, and observation/retrieval/ingestion fields.

| Classification | Relevant occurrences |
|---|---|
| SAFE CURRENT USE | Provider acquisition timestamps; run/request completion timestamps; explicit-current IBMI feature/live calls; current histogram capture and its `_latest_close`; explicit-current CERI prepared-array tests; alert acknowledgement/dismissal timestamps |
| SAFE BOUNDED HISTORICAL USE | IBMI metric/snapshot/availability/PIT dollar-volume selectors and cutoff-relative freshness; CERI feature, PriceBar, catalyst, guidance, and snapshot bounds; lifecycle active/closed selectors; alert cooldown interval |
| OUT OF SCOPE | Read-only dashboard/query services that intentionally present latest/current state; operational `changed_since` work selection; worker/job timing; CERI retry/refresh identity; Winner paths |
| REMAINING DEFECT | None in the T09A scope |

Certification verdicts:

```text
IBMI HISTORICAL TEMPORAL CONTAINMENT: PASS
CERI HISTORICAL TEMPORAL CONTAINMENT: PASS
SETUP/LIFECYCLE HISTORICAL TEMPORAL CONTAINMENT: PASS

T09A OVERALL: PASS
```

The PASS is based on zero failures/skips in the 799-test affected suite and semantic regressions that assert selected evidence, values, source timestamps, and non-mutation. The unrelated broad-suite infrastructure and baseline runtime failures are disclosed above and do not leave an in-scope contamination path executable.
