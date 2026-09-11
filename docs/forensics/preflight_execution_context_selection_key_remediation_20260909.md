# Preflight/Execution Context and Lifecycle Selection-Key Remediation

Date: 2026-09-09  
Baseline: `bb7dcf5a8fc801ddfba38fd547ce1af3fa82bf82`  
Implementation: `6805492817f78ccb2a6a46d53cb1d5bba3c24a4e`  
Branch: `codex/preflight-context-selection-key-remediation`

## A. Executive verdict

```text
PREFLIGHT/EXECUTION CONTEXT CONTRACT CERTIFIED: YES
LIFECYCLE SELECTION-KEY CONTRACT CERTIFIED: YES
ARCHITECTURE REMEDIATION CERTIFIED: YES
SAFE FOR FINAL TARGETED TRANSITION CANARY: YES
```

No live canary or pipeline was run. The certification means that a separately authorized canary now has a deterministic contract; it is not authorization to run one.

The context defect was real: candidate discovery created an in-memory prospective context, while `start_pipeline` independently persisted a second wall-clock-derived context. The remediation persists an immutable preflight plan and a reserved `MarketCalculationContext`, evaluates against it, revalidates critical evidence under the same cutoff, atomically attaches that exact context to the pipeline, and repeats/validates the envelope in the durable job.

The lifecycle key is not defective. The exact key `(ticker, timeframe, data_as_of_date)` intentionally identifies one session-canonical snapshot. Current state across sessions is the latest session-canonical selection for `(ticker, timeframe)`. The former canary requirement incorrectly expected the mutable row for an older session key to move across sessions. Classification: `SELECTION_KEY_CORRECT_CANARY_EXPECTATION_WRONG`.

The technical-score requirement was also overconstrained. Selection-critical technical state can be reconstructed read-only from PIT-safe price bars using the production bounded-frame calculation path. Classification: `SAFE_TO_RECOMPUTE_READ_ONLY_FROM_PIT_RAW_INPUTS`.

## B. Baseline

| Item | Observed state |
|---|---|
| Branch / baseline HEAD | `codex/preflight-context-selection-key-remediation` / `bb7dcf5a8fc801ddfba38fd547ce1af3fa82bf82` |
| Initial worktree | clean |
| Required ancestry | `bb7dcf5`, `5c76d7ec`, and `946c7ee` are all ancestors |
| Alembic code head before / after | `0070_ceri_price_response_pit_context` / `0071_transition_preflight_plan` (one head) |
| Production Alembic head | `0070_ceri_price_response_pit_context` |
| Production database | PostgreSQL 18.3, database `swinglens`, role `postgres`, `127.0.0.1:5432` |
| App / worker / supervisor | stopped; no SwingLens process registration and no listener on port 8000 |
| Active / queued jobs | 0 |
| Latest run / pipeline / job | 151 / 144 / 42931 |
| Latest context / lifecycle / CERI | 4 / 36949 / 10929 |
| Baseline wall clock | 2026-09-09 18:46:53 Europe/Zurich; 16:46:53 UTC; 12:46:53 America/New_York |
| Latest completed US session | 2026-09-08 |
| Bar readiness | daily session 2026-09-08 ready under the production calendar/readiness policy |

The actual prior artifacts were read: `docs/forensics/pit_coverage_transition_readiness_analysis_20260909.md` and `artifacts/forensics/pit_coverage_transition_readiness_summary.json`.

## C. Current C1/C2 mismatch

Before remediation the ownership flow was:

```text
TransitionCandidateDiscoveryService.discover_for_run
  -> prospective_pipeline_market_context(datetime.now(UTC))
     -> in-memory MarketCalculationCutoff C1; no context ID
  -> PIT loaders and candidate HIGH/MEDIUM/LOW decision under C1
  -> separate start_pipeline request
     -> create PipelineRun
     -> create_pipeline_market_context(datetime.now(UTC))
        -> persisted MarketCalculationContext C2 with a new context ID
     -> enqueue FULL_PIPELINE with pipeline ID
  -> worker reloads pipeline context C2
```

| Stage | Before remediation | Persisted | Recomputed / wall-clock sensitivity |
|---|---|---:|---|
| Candidate discovery | `MarketCalculationCutoff` C1 | no | cutoff, completed session, calendar and readiness derived from discovery-time `now` |
| Candidate evaluation / HIGH | C1 passed to PIT loaders | no | evidence eligibility and session key depend on C1 |
| Pipeline creation | `PipelineRun` | yes | separate request/transaction |
| Context freeze | `MarketCalculationContext` C2 | yes | independently derived from enqueue-time `now` |
| Durable job | pipeline ID only | yes | did not repeat the complete frozen envelope |
| Worker | context resolved from pipeline | yes | used C2, not the context that certified HIGH |

Therefore C1 and C2 were not identity-equivalent even when they happened to resolve the same session. A deterministic test fixes C1 at 2026-09-08 20:14 UTC and C2 at 20:16 UTC: the US daily-bar-ready boundary at 16:15 New York changes `latest_completed_session` from 2026-09-04 to 2026-09-08.

Boundary characterization:

- During market open and between close and readiness, the session can stay stable while cutoff-eligible receipt/revision evidence changes.
- At market close, the daily session still does not advance until the configured readiness delay elapses.
- At the daily-bar-ready boundary, the completed session and candidate selection key can change materially.
- A trading-session boundary changes the date component of the session-canonical key.
- Midnight, weekends, and holidays normally retain the prior completed trading session, but cutoff-eligible arrivals can still differ.
- DST moves the same New York readiness rule in UTC (20:15 UTC during EDT, 21:15 UTC during EST).
- Provider refreshes and price-bar revisions become eligible only when their certified receipt/revision timestamps are at or before the frozen cutoff. Post-cutoff arrivals stay excluded from that world.

## D. Chosen shared-context contract

### Design comparison

| Design | Audit/durability | Race and retry behavior | UX / DB cost | Verdict |
|---|---|---|---|---|
| A. Reserved context | strong context identity, but little candidate-state evidence | needs separate evidence/CAS token | simple, incomplete audit | useful primitive only |
| B. Immutable preflight plan | context, candidates, keys, pointers and fingerprints are durable | explicit revalidation, idempotent consume, stale failure | one additive table and clear two-step UX | chosen |
| C. Atomic preflight + enqueue | strongest gap elimination | naturally race-resistant | removes review window and couples expensive discovery to enqueue transaction | broader than required |
| D. Existing architecture | no durable C1 identity | C1/C2 gap remains | low DB cost | rejected |

Option B is implemented using Option A's reserved-context primitive. Preflight creates exactly one immutable `MarketCalculationContext` with `pipeline_run_id = NULL`, evaluates the candidate under that row, and persists `transition_preflight_plans`. Enqueue locks and verifies the plan, reconstructs selection evidence under the reserved cutoff, creates the pipeline, and attaches the same context ID. It never calls the wall clock to create C2.

The plan binds:

- `market_calculation_context_id`, whose immutable row contains `cutoff_at`, `latest_completed_session`, `calendar_version`, `bar_readiness_version`, exchange timezone, readiness instant, reason and upload run;
- upload run, tickers, exact selection keys, candidate classification and serialized candidate results;
- expected latest and exact-key pointer snapshot IDs and revisions;
- predicted prospective session/selection identity;
- per-candidate and aggregate required-evidence fingerprints;
- technical reconstruction fingerprints;
- idempotency key, creation time, expiry, status, and eventual pipeline ID.

The post-remediation execution graph is:

```text
create_transition_preflight_plan
  -> reserve_preflight_market_context -> persisted context C
  -> discover_for_run(..., market_cutoff=C)
     -> PIT raw loaders + non-persisting technical preview
  -> persist immutable plan/fingerprints
start_pipeline(plan_id)
  -> lock plan and re-run discovery under C
  -> compare key, pointer revision, evidence and technical fingerprints; require HIGH
  -> create PipelineRun
  -> attach_reserved_market_context(C, pipeline)
  -> mark plan CONSUMED and enqueue one FULL_PIPELINE job
     carrying context ID/cutoff/session/calendar/readiness/fingerprint
worker
  -> validate payload envelope against pipeline-owned C
  -> inject C into PipelineExecutionDependencies
```

## E. Stale preflight / CAS semantics

Enqueue fails closed rather than substituting a new context:

- missing, expired, cancelled, stale, wrong-run or already-owned reservation: `STALE_PREFLIGHT`;
- changed current pointer ID/revision, selection key, required PIT evidence, or technical reconstruction: `PRECONDITION_CHANGED`;
- no remaining HIGH candidate: `STALE_PREFLIGHT`;
- a pipeline/job envelope that differs from its authoritative context: `PipelineCalculationContextError`.

The pointer rows and plan are locked with `SELECT ... FOR UPDATE`; pointer revisions are part of the fingerprint. The execution world's bar/session boundary is the reserved context, so crossing real wall time after preflight either remains valid under C or is rejected due to changed critical evidence—it never produces C2.

Idempotence and recovery:

- duplicate preflight with the same idempotency key and run returns the same plan; reuse across another run is `IDEMPOTENCY_CONFLICT`;
- duplicate enqueue of a consumed plan returns the same pipeline with deterministic coalescing;
- unique constraints enforce one plan per idempotency key, context, and pipeline;
- reservations have a 30-minute default TTL, can be cancelled, and are batch-expired with `FOR UPDATE SKIP LOCKED`;
- abandoned plans create no technical scores, lifecycle snapshots, CERI snapshots, pointer changes, selection events, pipeline, or job, and hold no transaction-spanning lock;
- restart recovery reloads the durable plan/context; consumed plans remain attached to their single pipeline.

Concurrency tests cover pointer change rejection, boundary reuse, post-cutoff revision exclusion, duplicate enqueue, and abandoned-plan expiry/no business mutation.

## F. Exact lifecycle selection key

The ORM, migration 0069, repository lookup and event uniqueness all agree on:

```text
(ticker, timeframe, data_as_of_date)
```

`setup_signal_snapshot_current_selections` has `uq_setup_signal_snapshot_current_selection_key` on those three fields and a unique selected snapshot. The event ledger adds `selection_revision` to that key. Model version, source scope and upload run are evidence attributes, not pointer-key fields.

The repository now names both semantics explicitly:

- `historical_session_canonical_snapshot(ticker, timeframe, data_as_of_date)` returns one session's canonical choice;
- `current_cross_session_snapshot(ticker, timeframe[, as_of_date])` derives current state from the latest selected session, without a second mutable pointer.

## G. 939 blocker breakdown

All 939 prior `BLOCKED_SELECTION_KEY` results differed only because the prospective session date was newer:

| Differing component | Count |
|---|---:|
| `data_as_of_date` / session | 939 |
| ticker | 0 |
| timeframe | 0 |
| model/version | 0 |
| source scope | 0 |
| run scope | 0 |
| other | 0 |

These are legitimate new-session canonical initializations, not blocked replacements. The previous scan compared only an exact old session key and treated absence of that new key as inability to transition.

## H. Intended pointer semantics

The certified domain contract is **B: session-scoped canonical lifecycle snapshot**, with current cross-session state derived from the latest session-canonical row. The execution-plan documentation calls for one canonical snapshot per ticker/timeframe/date; migration 0069 and the repository implement exactly that; historical/as-of consumers require the date dimension for reproducibility.

Keeping older session selections preserves what was canonical for each completed session. Advancing current state means a later date becomes the latest derived selection, not rewriting the older date's pointer. Same-session reruns can still replace that session's pointer when canonical precedence wins.

Classification:

```text
SELECTION_KEY_CORRECT_CANARY_EXPECTATION_WRONG
```

## I. Consumer matrix

| Consumer | Required view | Resolution |
|---|---|---|
| Lifecycle UI current card/list | `CURRENT_CROSS_SESSION_STATE` | latest session-canonical selection for ticker/timeframe |
| Current-state API | `CURRENT_CROSS_SESSION_STATE` | explicit cross-session repository query |
| Historical run views | `HISTORICAL_AS_OF_RUN` | immutable snapshot rows from the requested run |
| Alerts / change detection | `CURRENT_CROSS_SESSION_STATE` | compare latest derived state; ledger reason distinguishes initialization/replacement |
| Episodes | `CURRENT_CROSS_SESSION_STATE` | chronological selected sessions drive state evolution |
| Ranking / actionability | `HISTORICAL_AS_OF_RUN` | run-bound scored evidence, not today's pointer |
| Winner integration | `HISTORICAL_AS_OF_RUN` | frozen manifest and referenced lifecycle evidence are revalidated as-of run |
| CERI integration | `HISTORICAL_AS_OF_RUN` | independently frozen context and evidence lineage; not pointer authority |
| Forensic tooling | `SESSION_CANONICAL_STATE` | exact ticker/timeframe/date pointer and append-only events |
| Session-specific lifecycle queries | `SESSION_CANONICAL_STATE` | explicit historical-session canonical lookup |

## J. Chosen selection architecture

### Model comparison

| Model | Reproducibility / correctness | Migration and compatibility | Concurrency / ledger | Verdict |
|---|---|---|---|---|
| 1. Session pointer; derive cross-session current | preserves exact session history and correct current lookup | no schema/backfill; compatible | existing per-key lock/revision/event model | chosen |
| 2. Separate session and cross-session pointers | explicit but duplicates state | new table, initialization policy and dual-write consistency | two CAS domains/events | unnecessary now |
| 3. Cross-session pointer only | loses direct session canonical addressability | destructive semantic migration | simpler pointer, weaker history | rejected |
| 4. Existing unnamed lookup behavior | schema is sound but semantics were ambiguous to callers/canary | no migration | easy to misuse | clarified by explicit APIs/tests |

Model 1 is the narrowest correct architecture. No selection-key schema change or legacy pointer migration is required. Tests cover same-session replacement, next-session initialization, different timeframes, new keys, cross-session current lookup, and historical session lookup.

## K. Event-ledger semantics

The append-only ledger now records semantic reasons without changing old events:

- `NEW_KEY_INITIALIZATION`: no exact key and no earlier session for that ticker/timeframe;
- `NEW_SESSION_CANONICAL_INITIALIZATION`: first canonical pointer for a later session key;
- `SAME_SESSION_REPLACEMENT`: a higher-precedence snapshot replaces the selected snapshot for the same exact key;
- `CURRENT_CROSS_SESSION_ADVANCE`: a derived condition, not a second persisted event today; it occurs when a `NEW_SESSION_CANONICAL_INITIALIZATION` causes `current_cross_session_snapshot` to move from the prior session to the new session.

Snapshot evidence remains immutable. No code reintroduces `UPDATE old snapshot SET is_canonical=false`; existing events remain unchanged.

## L. Technical-score legacy provenance

Classification:

```text
SAFE_TO_RECOMPUTE_READ_ONLY_FROM_PIT_RAW_INPUTS
```

Candidate discovery does not need a previously persisted score whose historical context happens to match the prospective cutoff. It needs the technical decision state that the pipeline would calculate from bars eligible under the shared context. `preview_run_technicals` now uses the same bounded frames, work items and finalization conversion as production technical scoring, with persistence and caches disabled. The preflight records a deterministic reconstruction fingerprint. It persists no technical score and performs no historical backfill.

The production plan path reconstructs the full requested run universe so relative ranks match execution. The readiness scan reused one minimum current-universe technical preview across per-run candidate evaluation for tractability; the canonical precedence decision itself depends on completeness and evidence identity, while actual enqueue always revalidates the exact plan path.

## M. Post-remediation readiness scan

The production scan ran read-only in a repeatable-read transaction at frozen cutoff `2026-09-09T16:46:53.397637Z`, completed session 2026-09-08. Table counts were captured before and after and were identical.

| Result | Count |
|---|---:|
| Universe | 1,491 |
| HIGH | 1,273 |
| MEDIUM | 0 |
| LOW | 218 |
| Predicted legitimate transitions | 1,273 |
| Same-session replacements | 341 |
| New-session canonical initializations / derived current advances | 932 |
| Selection-key blockers | 0 |
| Legacy-provenance blockers | 0 |
| Overconstraint blockers | 0 |

The seven PIT-incomplete and 211 non-displacing candidates remain LOW for substantive reasons. Architecture is no longer structurally incapable of producing a HIGH candidate.

## N. Adjacent contract findings

| ID | Finding | Severity / disposition |
|---|---|---|
| STI-CONTRACT-F001 | Full-pipeline `build_fetch_plan` still calls wall-clock `latest_completed_us_trading_day()` instead of accepting the authoritative context. Fetch orchestration can vary, although all selection-critical scoring remains cutoff-authoritative. | non-blocking follow-up |
| STI-CONTRACT-F002 | IB Gateway health preflight is intentionally operational and may change between UI request and worker execution; the worker rechecks and blocks/falls back by policy. It is not certification evidence. | expected operational behavior |
| STI-CONTRACT-F003 | CERI ACTIVE readiness is rechecked in the worker against current processor/config state. Failure must block/repair rather than silently change the evidence cutoff. | non-blocking follow-up |
| STI-CONTRACT-F004 | Winner maturation uses a reviewed immutable manifest plus per-row revalidation/CAS before mutation. | pass |
| STI-CONTRACT-F005 | Market-data prewarm reuse is a current-state optimization; downstream eligibility remains governed by the authoritative frozen cutoff. | non-blocking documentation/monitoring |

None changes the selected lifecycle evidence world or blocks the next targeted canary.

## O. Migration

Migration `0071_transition_preflight_plan` is additive after `0070_ceri_price_response_pit_context`. It adds only `transition_preflight_plans`, foreign keys, uniqueness, status checks and lookup/expiry indexes. Reserved contexts are represented by the already-nullable pipeline FK. There is one Alembic head.

No history is backfilled or inferred. No lifecycle key/pointer/event schema is changed. Upgrade, use, downgrade and neighboring temporal migrations were validated on disposable PostgreSQL. Production remains at 0070; migration 0071 was not applied there.

## P. Tests

| Suite | Result |
|---|---|
| Focused new/context/candidate/pipeline tests | 37 passed, 5 skipped in the broad focused run; final edited-file subset 32 passed |
| Pipeline/lifecycle targeted suite | 323 passed |
| Cross-domain temporal + CERI + Winner focused suite | 1,020 passed |
| Disposable PostgreSQL: plan, migration, immutability, CERI PIT, session temporal, Winner temporal/maturation | 29 passed |
| Full non-slow suite | 2,186 passed, 112 skipped, 58 deselected; one metrics scrape timing-budget test failed only after accumulated registry/cardinality load and passed alone in 0.31 s |
| Ruff on all changed files | passed |
| `compileall` for app/scripts/tests | passed |
| `git diff --check` / changed-file formatting | passed |
| Alembic heads | one: `0071_transition_preflight_plan` |

The isolated timing failure does not exercise the remediation and is not a functional regression. PostgreSQL validation was rerun after updating the two expected-head assertions, yielding a clean 29/29 result.

## Q. Production safety

| Guard | Result |
|---|---|
| Runs 148–151 unchanged | YES; all 16 certified immutable lifecycle hashes match, including snapshot 36949 `18edc2ad7695aa3bd154295fa01f2a6550db62f72d457621f84b981ee43fcbfa` |
| CERI snapshot 10929 unchanged | YES; canonical full-row hash `75971daad3f89f7f3500d616346b43d4217edd75482f8b4a65f7040dfb883d87`, stored evidence hash `1df165a0cbeba82db61e9545a4f820a6599a687e0b45c7df1cb8f37fdf9a8eb8` |
| Production counts unchanged | YES: runs 151, pipelines 144, jobs 42,520, contexts 4, technical scores 23,882, lifecycle snapshots 26,539, pointers 14,646, events 1 |
| Production business writes | NO |
| Pipeline run | NO |
| Canary run | NO |
| Historical repair/backfill | NO |
| Production migration | NO; still 0070 |
| Merge | NO |

## R. Final recommendation

The next live run requires separate authorization and must use a persisted transition preflight plan ID. It must show the same context ID/cutoff/session/calendar/readiness envelope in the plan, pipeline, durable job, worker and resulting evidence.

The corrected transition proof is either:

```text
same_session_replacement_events >= 1
```

or:

```text
new_session_canonical_initializations >= 1
AND current_cross_session_snapshot moves old session -> new session
```

It must also prove the plan's expected pointer revision and evidence fingerprints still match at enqueue, and that post-cutoff arrivals remain excluded. Requiring mutation of an older session's pointer during a cross-session run is invalid and must not return as a canary gate.
