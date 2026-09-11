# CERI Point-in-Time and Transition-Candidate Remediation — 2026-09-09

## A. Executive verdict

```text
CERI PIT ROOT CAUSE CERTIFIED: YES
CERI PIT REMEDIATION CERTIFIED: YES
TRANSITION CANDIDATE REMEDIATION CERTIFIED: YES
SAFE FOR TARGETED TRANSITION CANARY RETRY: YES
```

The two failures exposed by the latest transition canary were independent. CERI snapshot 10929 carried the correct frozen context envelope but used source and price-bar evidence first known after that context's cutoff. Separately, the candidate ranker treated current/raw availability as predictive without reconstructing the exact prospective point-in-time world or the selection key that a real run would use.

The remediation introduces shared, fail-closed CERI source and price-bar eligibility, preserves the original cutoff through delayed jobs and materialization identities, and adds a read-only candidate service that uses the production MarketClock policy, PIT lifecycle loaders, exact current-selection keys, and canonical selection rules. Run 151 and snapshot 10929 remain historical evidence and were not changed. No live canary was run.

### Certified baseline

| Control | Value before changes |
|---|---|
| Branch | `codex/ceri-pit-transition-candidate-remediation` |
| HEAD | `830c2b6730345a2d4721a34d2b6789356ad9c474` |
| Worktree | clean |
| Required ancestors | `830c2b67`: yes; `b651b18d`: yes; `730570d`: yes |
| Alembic code head | `0069_lifecycle_current_selection` |
| Production Alembic head | `0069_lifecycle_current_selection` |
| Production PostgreSQL | PostgreSQL 18.3, `127.0.0.1:5432`, database `swinglens`, role `postgres` |
| App / worker / supervisor | stopped / stopped / stopped; stale durable registrations were not process-backed |
| Active/queued/recovering/stalled jobs | 0 / 0 / 0 / 0 |
| Run / pipeline / initial job | 151 / 144 / 42916, all `COMPLETED` |
| CERI snapshot | 10929 present and unchanged |
| Baseline time | `2026-09-09T12:36:36.662897+02:00` / `10:36:36.662897Z` / `06:36:36.662897-04:00` New York |

One historical `BLOCKED` job, 31115 for Run 120, was terminal and neither active nor queued. It was not changed. The actual malformed-link replacements from commit `830c2b67` are:

- `docs/forensics/lifecycle_current_selection_transition_canary_20260909.md`
- `artifacts/forensics/lifecycle_current_selection_transition_canary_summary.json`

## B. Run 151 reconstruction

| Evidence | Value |
|---|---|
| Ticker / company | DRS / company 1011 |
| Run / pipeline | 151 / 144 |
| Initial, repair, resumed jobs | 42916 / 42917 / 42918 |
| CERI descendant jobs | 42919–42931 inclusive |
| Job terminal state | all 42916–42931 `COMPLETED`; retry count 0; recovery count 0 |
| Frozen calculation context | 4 |
| Frozen cutoff | `2026-09-09T11:30:53.203249+02:00` (`09:30:53.203249Z`) |
| Latest completed session | `2026-09-08` |
| Daily-bar ready boundary | `2026-09-08T22:15:00+02:00` |
| Calendar / readiness policy | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |
| CERI snapshot | 10929, DRS, Run 151 |
| Feature as-of session | `2026-09-08` |
| Snapshot context/cutoff/calendar | 4 / exact frozen cutoff / `swinglens-us-equities-v1` |
| Snapshot evidence hash | `1df165a0cbeba82db61e9545a4f820a6599a687e0b45c7df1cb8f37fdf9a8eb8` |
| Snapshot created | `2026-09-09T11:32:13.080077+02:00` |

The frozen context was created once and propagated from `start_pipeline` to pipeline 144, its parent/resume jobs, every CERI child payload, handlers, and snapshot 10929. The defect was not context replacement. The child work ran later and reloaded current CERI sources and current price bars without applying the supplied cutoff.

Read-only replay of the historical path returned all seven source IDs and both DRS bar IDs below. Replay through the remediated eligibility helpers returned no eligible row from either violating set. The replay did not write or replace snapshot 10929.

## C. Seven violating CERI sources

For these source rows, `observed_at` and `source_timestamp` were `NULL`; no correction/supersession timestamp supplied earlier knowledge. All source sessions/effective dates used by the snapshot were on or before the frozen session, but all authoritative receipt times were after the frozen cutoff.

| Source ID | Type | Provider | Published/Event Time | Observed/Retrieved/Ingested Time | Effective known_at | Frozen cutoff | Eligible? |
|---:|---|---|---|---|---|---|---|
| 867459 | earnings actual | eodhd | `2025-05-01T00:00:00+02:00` | obs `NULL`; ret `2026-09-09T11:31:56.393435+02:00`; ing `11:31:56.446872+02:00` | retrieved `11:31:56.393435+02:00` | `11:30:53.203249+02:00` | NO |
| 867460 | earnings actual | eodhd | `2025-07-30T00:00:00+02:00` | obs `NULL`; ret `2026-09-09T11:31:56.499459+02:00`; ing `11:31:56.572519+02:00` | retrieved `11:31:56.499459+02:00` | `11:30:53.203249+02:00` | NO |
| 867461 | earnings actual | eodhd | `2025-10-29T00:00:00+01:00` | obs `NULL`; ret `2026-09-09T11:31:56.667520+02:00`; ing `11:31:56.731498+02:00` | retrieved `11:31:56.667520+02:00` | `11:30:53.203249+02:00` | NO |
| 867462 | earnings actual | eodhd | `2026-02-19T00:00:00+01:00` | obs `NULL`; ret `2026-09-09T11:31:56.762760+02:00`; ing `11:31:56.816957+02:00` | retrieved `11:31:56.762760+02:00` | `11:30:53.203249+02:00` | NO |
| 867463 | earnings actual | eodhd | `2026-05-05T00:00:00+02:00` | obs `NULL`; ret `2026-09-09T11:31:56.849008+02:00`; ing `11:31:56.963028+02:00` | retrieved `11:31:56.849008+02:00` | `11:30:53.203249+02:00` | NO |
| 867464 | earnings actual | eodhd | `2026-07-30T00:00:00+02:00` | obs `NULL`; ret `2026-09-09T11:31:56.994388+02:00`; ing `11:31:57.050037+02:00` | retrieved `11:31:56.994388+02:00` | `11:30:53.203249+02:00` | NO |
| 867465 | catalyst | eodhd | `2026-09-07T15:40:04+02:00` | obs `NULL`; ret `2026-09-09T11:31:59.195489+02:00`; ing `11:31:59.263801+02:00` | retrieved `11:31:59.195489+02:00` | `11:30:53.203249+02:00` | NO |

The six earnings records became domain rows 11022–11027. The catalyst became event 18594/revision 18933 with effective session `2026-09-08`. Those domain dates pass the session gate, but their source receipts fail the independent knowledge gate.

## D. Two violating price bars

Price-response feature 8683 cited catalyst revision 18933 and price bars 2263441, 2263442, 2263479, and 2263504. The two SPY bars were known on September 8 and were eligible. The two DRS bars were first observed only after the frozen cutoff:

| Bar ID | Session | Provider/source | First-known timestamp | Frozen cutoff | Session eligible? | Knowledge-time eligible? |
|---:|---|---|---|---|---|---|
| 2263479 | `2026-09-08` | IB / `ADJUSTED_LAST` (adjusted) | `2026-09-09T11:31:04.697382+02:00` | `2026-09-09T11:30:53.203249+02:00` | YES | NO |
| 2263504 | `2026-09-08` | IB / `TRADES` | `2026-09-09T11:31:07.168191+02:00` | `2026-09-09T11:30:53.203249+02:00` | YES | NO |

Both current rows had `revised_at=NULL` and `revision_count=0`. Thus `bar_date <= input_as_of_session` was true while `first_seen_at > cutoff_at` independently made both bars invalid for PIT reconstruction. The eligible SPY controls were bars 2263441 (`ADJUSTED_LAST`, first seen `2026-09-08T23:30:15.590335+02:00`) and 2263442 (`TRADES`, first seen `23:30:18.463743+02:00`).

## E. CERI root cause

The call graph was:

```text
start_pipeline
  -> create_pipeline_market_context
  -> durable parent/resume job payload
  -> pipeline_executor CERI child jobs
  -> execute_rebuild_features_job / execute_capture_run_job
  -> resolve_pipeline_market_context
  -> CeriFeatureRebuildService / CeriCaptureService
  -> source repositories and CeriPriceResponseService
  -> feature calculation
  -> CeriScoreSnapshot persistence
```

`calculation_context_id`, `cutoff_at`, latest session, and calendar were available at every orchestration boundary. Before remediation, the source path used effective/session constraints but did not constrain receipt time; dedup/current-revision selection could occur before cutoff filtering; capture's price-response query constrained `bar_date` but not `first_seen_at` or `revised_at`; and rebuild/cache identities did not consistently include the cutoff. Child jobs correctly retained the original context but reloaded then-current source state under those incomplete queries.

| Finding | Classification | Certification |
|---|---|---|
| `STI-CERI-PIT-F001` | `KNOWN_AT_NOT_FILTERED`, `SOURCE_QUERY_FILTERS_SESSION_ONLY`, `POST_QUERY_FILTER_MISSING`, `CHILD_JOB_RELOADS_CURRENT_SOURCE_STATE` | CONFIRMED / FIXED |
| `STI-CERI-PIT-F002` | `KNOWN_AT_FIELD_PRECEDENCE_WRONG`, `FALLBACK_TIMESTAMP_SEMANTICS_UNSAFE` in estimate normalization | CONFIRMED / FIXED |
| `STI-CERI-PIT-F003` | `REVISED_RECORD_SELECTED_AFTER_CUTOFF`, `DEDUP_SELECTS_LATEST_CURRENT_RECORD` | CONFIRMED / FIXED |
| `STI-CERI-PIT-F004` | `PRICE_BAR_QUERY_FILTERS_SESSION_ONLY` | CONFIRMED / FIXED |
| `STI-CERI-PIT-F005` | `CACHE_IGNORES_CUTOFF` for CERI rebuild/materialization identities | CONFIRMED / FIXED |

## F. Authoritative known_at semantics

CERI now uses one conservative source-receipt policy across earnings, estimates, guidance, catalysts, and their revisions:

```text
known_at = retrieved_at, when present
        else ingested_at, when present
        else UNKNOWN and ineligible
```

`published_at`, event/effective time, provider `source_timestamp`, and `observed_at` describe the event or provider assertion; they do not prove when SwingLens received the row and therefore cannot authorize it. `created_at` is not substituted for missing receipt provenance. No timestamp is invented for legacy data. Every PIT input must pass both `effective/session <= frozen latest_completed_session` and `known_at <= frozen cutoff_at`; either failure excludes it.

For versioned data, the order is now: filter sources/revisions to the frozen session and cutoff, then choose the latest eligible version. The regression case `v1` known before cutoff plus `v2` known after cutoff selects `v1`, never `v2` and never an unqualified current row.

## G. CERI remediation

The shared module `app/services/ceri/pit_eligibility.py` supplies authoritative receipt derivation, object eligibility, eligible-ID selection, and matching SQL predicates. It is used by capture, PIT source loading, feature rebuild, and price response.

The capture path filters source records and all dependent earnings, estimates, guidance, catalysts, and revisions before current-version selection. Delayed execution resolves the persisted parent context and continues to use that original cutoff. Price response now receives both the frozen session and cutoff, applies SQL predicates and defensive post-query checks, includes the cutoff in its event key, and persists `calculation_cutoff_at`, `calculation_context_id`, and `calendar_version` for new rows.

Feature rebuild uses PIT source prefetch, filters before dedup, and keys fingerprints, cache identities, and event identities by cutoff/context/source versions (`batch-prefetch-pit-v2`). An artifact produced with a later cutoff cannot satisfy an earlier request.

Price-bar policy is fail closed:

```text
bar_session <= latest_completed_session
AND first_seen_at <= cutoff_at
AND (revised_at IS NULL OR revised_at <= cutoff_at)
```

`first_seen_at` is the earliest stored observation of the current row. `price_bar_revisions` preserves previous/new values and observation time, but the current CERI path does not claim it can reconstruct an uncertified overwritten version; it excludes a current row revised after cutoff unless an independently certified versioned source is used. Missing required PIT provenance is excluded and surfaced as degraded/missing coverage rather than silently admitted.

Production read-only replay result:

| Behavior | Source IDs admitted | Violating DRS bar IDs admitted |
|---|---|---|
| Historical snapshot-10929 path | 867459–867465 | 2263479, 2263504 |
| Remediated PIT selection at context 4 | none | none |

## H. DRS candidate-discovery root cause

Before Run 151, pointer 5725 targeted snapshot 19017 under `DRS/1d/2026-08-03`. Discovery observed current/raw availability through August 3 and predicted that a new same-key snapshot would tie on quality terms and win on later calculation time/ID, assigning HIGH confidence.

That prediction did not model the context a real run would freeze. At cutoff `2026-09-09T09:30:53.203249Z`, post-cutoff bar writes/revisions made the stored July 31–August 3 DRS rows unusable for historical reconstruction. The actual technical view ended July 30. Run 151 therefore created snapshot 36949 under `DRS/1d/2026-07-30`; pointer 14646/event 1 initialized that different key, while pointer 5725 remained on snapshot 19017. A new-key initialization is not an old-to-new transition.

The root cause was a mismatch of questions: the old ranker asked whether current data looked likely to produce a different/latest snapshot. It did not ask whether an exact, prospectively frozen, PIT-reconstructable snapshot would compete with and displace an existing pointer in the same selection scope.

## I. Prospective cutoff-aware candidate model

`TransitionCandidateDiscoveryService` is read-only. It creates an in-memory `MarketCalculationCutoff` through the same MarketClock policy used at pipeline start—cutoff, latest completed session, daily-bar readiness, calendar version, and readiness version—without persisting a pipeline context. It then invokes the lifecycle source loader and snapshot builder under that boundary, ignores pointer rows created after the prospective cutoff, derives the prospective snapshot key, locates the exact competing pointer, and runs the same canonical selector used by production.

HIGH requires an existing exact key, complete and exact prospective PIT input provenance, and a deterministic canonical win. MEDIUM permits one explicitly identified nondeterministic factor. Missing provenance, ambiguous/different keying, unavailable future data, no deterministic win, and all new-key initializations are LOW.

Read-only replay against the pre-Run-151 evidence produced:

| Ticker | Prospective cutoff | Prospective latest session | Latest reconstructable | Current pointer key / snapshot | Current target | Prospective key | Compete? | Initialize? | Predicted advance? | Reason | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DRS | `2026-09-09T09:30:53.203249Z` | `2026-09-08` | `2026-07-30` | `DRS/1d/2026-08-03` / 19017 | `2026-08-03` | `DRS/1d/2026-07-30` | NO | YES | NO | `NEW_KEY_INITIALIZATION_NOT_TRANSITION_COVERAGE` | LOW |
| NNI | same | `2026-09-08` | `2026-08-03` | `NNI/1d/2026-08-03` / 18927 | `2026-08-03` | `NNI/1d/2026-08-03` | YES | NO | NO | exact prospective technical provenance unavailable at context 4 | LOW |
| TBLA | same | `2026-09-08` | `2026-08-03` | `TBLA/1d/2026-08-03` / 18885 | `2026-08-03` | `TBLA/1d/2026-08-03` | YES | NO | NO | exact prospective technical provenance unavailable at context 4 | LOW |
| TPL | same | `2026-09-08` | `2026-08-03` | `TPL/1d/2026-08-03` / 18961 | `2026-08-03` | `TPL/1d/2026-08-03` | YES | NO | NO | exact prospective technical provenance unavailable at context 4 | LOW |

NNI, TBLA, and TPL are intentionally conservative replay classifications, not claims about a future run. Their stored technical artifacts could not be certified as exact reconstructions of context 4, so the service refuses HIGH confidence.

## J. Selection-key semantics

The selection key is exactly `(ticker, timeframe, data_as_of_date)`, rendered as `ticker/timeframe/date`. Candidate discovery distinguishes:

- `EXISTING_POINTER_ADVANCE`: an exact pointer exists for the prospective key and the fully PIT-qualified candidate deterministically wins canonical selection.
- `NEW_KEY_INITIALIZATION`: no exact pointer exists for the prospective key; inserting the first pointer is valid lifecycle behavior but does not cover an old-to-new transition.

The DRS regression is the latter. A genuine-transition fixture with an older same-key pointer and complete prospective inputs ranks HIGH and predicts an advance. A different-key fixture always reports initialization and LOW.

## K. Adjacent PIT audit

| Finding | Area | Result |
|---|---|---|
| `STI-PIT-F001` | CERI source capture/rebuild session-only eligibility | CONFIRMED / FIXED |
| `STI-PIT-F002` | CERI estimate known-time precedence | CONFIRMED / FIXED |
| `STI-PIT-F003` | CERI catalyst/current-revision dedup order | CONFIRMED / FIXED |
| `STI-PIT-F004` | CERI price-response bar eligibility | CONFIRMED / FIXED |
| `STI-PIT-F005` | Cutoff-blind CERI materialization/cache identities | CONFIRMED / FIXED |
| `STI-PIT-F006` | Lifecycle market/sector lookup chose present `is_current_revision` with date-only filtering | CONFIRMED / FIXED: calculation cutoff is filtered before version choice |
| `STI-PIT-F007` | IBMI feature loaders | AUDITED / PASS: `calculated_at <= cutoff` plus as-of session; boundary fixtures corrected |
| `STI-PIT-F008` | Technical artifact reuse | AUDITED / PASS: cutoff, as-of session, and input versions are part of eligibility; regressions green |
| `STI-PIT-F009` | Winner inputs | AUDITED / PASS: temporal ledger/source revision cutoff retained; regressions green |
| `STI-PIT-F010` | Alerts/change generation | AUDITED / PASS: consumes frozen snapshot/features without a new current-source read |

No adjacent blocking PIT defect remains in the audited paths. Test-fixture corrections made while exercising those paths align fixture sessions, frozen clocks, pointer rows, and durable-worker registrations with already-enforced production contracts; they do not relax temporal rules.

## L. Migration impact

Correct output provenance requires additive migration `0070_ceri_price_response_pit_context`, immediately after 0069. It adds nullable `calculation_cutoff_at`, `calculation_context_id` (foreign key), and `calendar_version` to CERI price-response features plus supporting indexes. Nullable fields preserve legacy unknowns; there is no backfill and no fabricated timestamp. Current pipeline-owned PIT requests do not trust a legacy row with missing cutoff provenance.

Upgrade 0069→0070, constraints, indexes, foreign key behavior, and legacy-null behavior passed on disposable PostgreSQL. Alembic has one code head: `0070_ceri_price_response_pit_context`. Production remains at 0069; the migration was not applied there.

## M. Tests

All required non-slow tests were covered by terminal green, sharded invocations. Counts are reported per invocation because PostgreSQL groups and repair reruns can overlap and therefore are not summed.

| Validation | Result |
|---|---|
| Final delivery-focused PIT/candidate/forensic/pipeline rerun | 56 passed, 1 warning |
| CERI PIT, revision, async context, cache, candidate, clock boundaries | 96 passed |
| Entire CERI suite | 431 passed, 1 warning |
| Lifecycle/cross-domain/Winner focused suite | 356 passed, 1 warning |
| Non-integration non-slow selection | 2,154 passed, 14 skipped, 54 deselected, 22 warnings |
| PowerShell lifecycle lane | 21 passed |
| PostgreSQL migration test | 1 passed |
| PostgreSQL selected final segment | 16 passed, 19 warnings |
| PostgreSQL observability group | 14 passed, 1 skipped, 18 warnings |
| PostgreSQL Winner/remainder group | 37 passed; corrected progress-reliability file then 2 passed |
| Winner claim regressions | 4 passed in focused reruns; included in green groups above |
| Ruff | changed Python files pass |
| Compile | changed application/tests plus `compileall` pass |
| Alembic | one head; disposable 0069→0070 upgrade pass |

During full-suite convergence, stale test fixtures exposed the newer migration head, trading-session, quiesce-fence, pointer, and frozen-clock contracts and were corrected. One monolithic run also recorded a transient Operations performance sample of 3.149 seconds against a 3.0-second budget; the complete isolated test passed in 0.77 seconds. Winner's repeated same-session idle poll was optimized with a session-local cache after its stable benchmark failed; it then measured about 84.5 microseconds per poll with unchanged query results. The terminal sharded union of the non-slow selection is green.

Repository-wide Ruff still reports 24 pre-existing lint findings in historical migration files. They are outside this focused change; all changed Python files pass Ruff. `git diff --check`, JSON parsing, and final focused verification are part of delivery validation.

## N. Production safety

All production inspection and replay used explicit read-only transactions. Final inspection showed Runs 148–151 still `COMPLETED` with their original row counts and timestamps; pipeline 144 and jobs 42916–42931 were unchanged and terminal; snapshot 10929 retained its original fields and evidence hash; production Alembic remained 0069; and active/queued/recovering/stalled job counts remained zero. No app, background worker, or supervisor process was running. Stale worker registrations and historical job 31115 were not modified.

```text
Runs 148–151 unchanged: YES
CERI snapshot 10929 unchanged: YES
Production business writes: NO
Historical repair: NO
Live canary rerun: NO
Production migration applied: NO
```

No production pipeline was enqueued, no pointer transition was forced, no selection event was manually inserted, and no historical source, bar, CERI, technical, Winner, lifecycle, run, pipeline, or job row was rewritten.

## O. Final recommendation

The root cause and both remediations are certified. The next targeted transition canary may be retried only as a separately authorized task after migration 0070 is deployed through the normal production process. Candidate selection for that retry must use the new prospective service and accept HIGH only for a fully reconstructed, same-key deterministic pointer advance. It must not treat initialization as coverage and must not weaken PIT eligibility to manufacture a candidate.

This task stops at remediation certification. It did not run the retry, merge to `main`, repair history, or apply the migration to production.

```text
CERI PIT ROOT CAUSE CERTIFIED: YES
CERI PIT REMEDIATION CERTIFIED: YES
TRANSITION CANDIDATE REMEDIATION CERTIFIED: YES
SAFE FOR TARGETED TRANSITION CANARY RETRY: YES
```
