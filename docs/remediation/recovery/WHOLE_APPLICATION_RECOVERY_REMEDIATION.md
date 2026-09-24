# SwingLens Whole-Application Recovery Remediation

Date: 2026-09-24  
Branch: `codex/whole-application-recovery-remediation`  
Starting HEAD: `595102d6a70a14dd35eff75601141119515c07`  
Scope: REC-P0-01 through REC-P0-04 and REC-P1-01 through REC-P1-05  

No live upload, IB/provider sweep, Winner mutation, small live slice, or final live canary was run.

## 1. Executive verdict

| Verdict | Result |
| --- | --- |
| FRESH-FETCH VISIBILITY FIXED | YES |
| TECHNICAL EVIDENCE AMPLIFICATION FIXED | YES |
| TECHNICAL TRANSACTION SCOPE FIXED | YES |
| JOB LEASE CONTINUITY FIXED | YES |
| CANCELLATION FIXED | YES |
| WATCHDOG LIVENESS VERIFIED | YES |
| DETERMINISTIC 10 PASSED | YES |
| DETERMINISTIC 25 PASSED | YES |
| DETERMINISTIC 100 PASSED | YES |
| ALL DOWNSTREAM STAGES REACHED | YES |
| FORMULAS UNCHANGED | YES |
| EVIDENCE SEMANTICS PRESERVED | YES |
| READY FOR SMALL LIVE VERTICAL SLICE | YES |
| READY FOR FINAL 100 LIVE CANARY | NO |

The deterministic production-shaped PostgreSQL path accounts for all 100 requested symbols, completes Technical below the five-minute target, reaches Winner, and repeats the 100-symbol Technical publication idempotently. The final live-canary verdict remains NO because the required preceding live vertical slice was explicitly out of scope and was not run.

## 2. Root-cause-to-fix mapping

| Finding | Implementation | Principal files/functions | Proof | Remaining limitation |
| --- | --- | --- | --- | --- |
| REC-P0-01 | Canonicalize the cohort source basis once, store one immutable manifest, validate each unique source manifest once per publication, and reference its digest from every score. | `technical_score_service.py`: `finalize_technical_scores`, `_canonical_technical_source_manifest`; `core_mutation_authority.py`: `validate_technical_source_manifest`; `tables.py`: `TechnicalSourceManifest` | `test_technical_evidence_is_linear_and_bounded`; deterministic 10/25/100 query and payload assertions | None at P0/P1 severity. |
| REC-P0-02 | Publish the initial claim before work; renew the job lease through independent short sessions at every Technical checkpoint; retain execution-token validation. | `background_worker.py`: `run_worker_once`, `_execute_full_pipeline_job`; `pipeline_executor.py`: Technical checkpoint adapter; `background_job_service.py`: heartbeat/progress paths | `test_technical_heartbeat_survives_long_work`; `test_full_pipeline_control_callbacks_use_detached_job_on_independent_sessions`; 10/25/100 checkpoint/lease assertions | Checkpoints are work-boundary driven, not a busy timer. A single work unit must remain below the 60-second budget. |
| REC-P0-03 | Remove the control row from the long business transaction; poll cancellation before, during and after bounded Technical work; reject publication after cancellation. | `background_worker.py`: independent `should_cancel`; `technical_score_service.py`: `_technical_checkpoint`; `pipeline_executor.py`: cancellation propagation | `test_technical_cancel_is_nonblocking_and_rolls_back` uses PostgreSQL, a staged Technical write, a separate cancel transaction with a 2-second timeout, checkpoint observation, and rollback | None. |
| REC-P0-04 | Record immutable first-fetch run/item lineage on first insert and admit post-cutoff rows only through the exact pipeline plan/scope/refresh/fetch item. | `bar_cache_service.py`: first-insert lineage; `price_bar_repository.py`: `_pipeline_acquisition_visibility`, `load_price_bars_frame`; migration 0084 | `test_pipeline_owned_first_fetch_is_visible`; `test_unrelated_post_cutoff_fetch_remains_invisible`; deterministic 100 includes BHE/BLLN/KLIC/LSCC/PDFS and reports 100/100 | Pre-0084 rows without first-fetch lineage remain governed by the original PIT cutoff; no unsafe inference/backfill is attempted. |
| REC-P1-01 | Report phase, completed/total, current/last item, durable checkpoint version, timestamp, and renewed lease after approximately ten tickers. | `technical_score_service.py`: `_technical_checkpoint`; `pipeline_executor.py`: Technical progress adapter; `background_job_service.py`: `record_job_progress` | Recovery checkpoint metrics plus worker/progress suites | None. |
| REC-P1-02 | Commit the read/calculation phase before publication; keep cache work independent; perform one bounded canonical validation and all score/evidence/projection writes in one final atomic transaction. | `technical_score_service.py`: `score_run_technicals`, `finalize_technical_scores` | Phase telemetry, score INSERT timing, cancellation rollback, stale-owner test | The final transaction still validates the unique canonical source set, by design; repeated per-score validation is gone. |
| REC-P1-03 | Add a non-locking observation before `FOR UPDATE SKIP LOCKED`, then distinguish `NO_CANDIDATE`, healthy/stale decisions, and `LOCKED_CANDIDATE_SKIPPED`. | `background_job_service.py`: `fence_stalled_jobs` | `test_watchdog_reports_locked_candidate` | Diagnostic observation does not authorize mutation; the watchdog still mutates only rows it locks. |
| REC-P1-04 | Replace every score’s full cohort JSON with a compact immutable `{contract,digest,source_count,state_count}` reference. | `technical_score_service.py`; `combined_ranking_identity.py`; `core_mutation_authority.py` | Max/total score-payload gates and exact retry | One canonical cohort manifest remains intentionally complete for historical verification. |
| REC-P1-05 | Add one production-shaped PostgreSQL recovery suite covering temporal visibility, bounded evidence, lease, cancel, fencing, watchdog, and 10/25/100 through Winner. | `tests/recovery/test_whole_application_recovery.py` | Recovery suite results in section 10 | Provider calls are frozen/stubbed by requirement; the critical Technical/evidence/control-plane/database path is real. |

## 3. Fresh-fetch temporal model

The calculation cutoff is business/PIT authority: market sessions and revisions later than that boundary remain unavailable. Storage time is separate: a valid historical bar may first be downloaded after admission.

Migration 0084 adds immutable `PriceBar.first_fetch_run_id` and `first_fetch_item_id` fields because the pre-existing cache row did not retain which fetch first created it. `bar_cache_service` sets them only on first insert; conflict updates never rewrite them. A post-admission row is visible only when all of the following resolve from retained database authority:

1. the calculation context identifies the exact pipeline and upload run;
2. the pipeline's frozen work scope identifies its parent fetch scope;
3. the fetch run matches that upload, acquisition plan, parent scope and refresh cycle;
4. a successful fetch item matches ticker, feed and timeframe;
5. the price row's immutable first-fetch run/item matches that item;
6. the bar's market session is no later than the frozen business session; and
7. the observed row/revision is no later than that authorized fetch completion.

Rows already visible at the cutoff keep the original `created_at/first_seen_at <= cutoff` rule. The positive test admits all five incident symbols after storage time while the negative test proves a post-cutoff row with no matching fetch lineage remains invisible. The production-shaped 100-symbol gate reports `100 requested / 100 visible`.

## 4. New Technical evidence model

`_canonical_technical_source_manifest` canonicalizes all price/benchmark/sector manifests, deduplicates them by SHA-256 fingerprint, sorts by digest, and binds the result to run, pipeline, calculation context, cutoff/session, and frozen configuration identity. One immutable `technical_source_manifests` row stores that complete source set and its digest. Each `technical_scores` row holds the manifest foreign key and a compact digest/reference in debug lineage.

Publication verifies the row digest against canonical JSON, verifies the Technical calculation identity/context/configuration binding, and validates each unique price-source manifest once. A transaction-local digest cache prevents repeated validation of the same source. Exact price-bar IDs, immutable state fingerprints and acquisition authority remain in the shared manifest, so historical reconstruction is unchanged in meaning: the score belongs to calculation identity X and immutable source-manifest Y, whose exact source states are Z. Legacy scores remain readable because the new foreign key is nullable.

This changes representation, not formulas or evidence strength. At 100 symbols the retained source basis is one canonical manifest rather than a full universe manifest copied into every score; validation and materialization scale with unique sources/states.

## 5. Transaction model

Before:

```text
81-minute outer transaction
  read + calculate + repeat huge canonicalization/validation
  + insert + evidence/projection writes + ownership fence + commit
  BackgroundJob control row could remain locked throughout
```

After:

```text
short control transaction: publish claim, verify token, heartbeat
read/prepare transaction: PIT loads and canonical input preparation
bounded calculation: checkpoint/cancel/lease after about 10 tickers
commit read/calculation and independent cache work
short publish transaction:
  re-check cancellation and current execution ownership
  create/reuse one canonical immutable manifest
  validate each unique source once
  replace scores and persist evidence/projections atomically
  commit
stage-boundary transaction: publish completion, heartbeat, commit immediately
```

During long Technical work, the BackgroundJob row is touched only through a detached control session or the currently fenced child transaction; it is never retained by the read/calculation transaction. Stage boundaries and bounded non-pipeline handlers use their business session and commit immediately, preventing a second session from waiting on the worker's own row lock. The final write remains all-or-nothing.

## 6. Lease, progress and cancellation model

Technical checkpoints run before preparation, during bounded input/source work, after approximately ten completed calculations, during bounded manifest validation, immediately before publication, and after publication. In-stage callbacks use a short independent SQLAlchemy session unless a synchronous fenced child transaction already owns the job row; in that case the callback renews through that exact session. Each path validates the execution token and advances durable progress without creating a self-lock. The detached control object prevents the business session's ORM state from being attached to an independent control session.

Progress records phase/stage, processed and total counts, current and last-completed items, checkpoint version/sequence, and `last_progress_at`. The 100-symbol deterministic measurement enforces a maximum observed callback gap below 60 seconds.

Cancellation uses the same independent read path, so it does not queue behind a long Technical transaction. `_technical_checkpoint` raises before any final publish; rollback leaves no partial score/evidence rows. Immediately before mutation, the existing domain-write fence revalidates the current execution token. A superseded worker therefore cannot publish.

The watchdog first performs a read-only MVCC observation, then attempts its existing `SKIP LOCKED` mutation lock. It now logs a locked stale candidate as `LOCKED_CANDIDATE_SKIPPED` rather than misreporting an empty queue.

## 7. Before/after performance

Final after-values are from the deterministic 100-symbol PostgreSQL recovery gate; sizes are canonical serialized bytes.

| Metric | Before | After | Improvement |
| --- | ---: | ---: | ---: |
| Requested Technical tickers | 100 | 100 | fully accounted |
| Calculable/visible tickers | 95 | 100 | +5 / 100% visible |
| Pure kernel | 72.883 s | 43.146 s | 40.8% faster |
| Process-pool calculation | 53.749 s | 31.648 s | 41.1% faster |
| Evidence/finalization preparation | ~246 s | 58.996 s | 76.0% faster |
| Score INSERT SQL | ~342 s | 0.167 s | >99.9% faster |
| Repeated evidence residual | ~4,207 s | 0 s duplicated work | eliminated |
| Total Technical | 4,894 s | 96.252 s | 98.0% faster / 50.8x |
| Per-score full cohort manifests | 481 | 0 | eliminated; one shared manifest |
| Total state visits | 36.24 M | 64,960 unique states | 99.8% fewer / 557.9x |
| Duplicate Technical JSON | ~3.60 GB | 0 B duplicated cohort JSON | eliminated |
| Repeated price-bar validation queries | 4,718 | 203 unique-source queries | 95.7% fewer / 23.2x |
| Price-bar rows returned in repeated validation | 3.75 M | 64,960 unique states | 98.3% fewer / 57.7x |
| Max job heartbeat/checkpoint gap | >15 min | 49.020 s | below 60-second budget |
| Job lease expired during Technical | YES | NO | fixed |
| Normal cancel blocked | YES | NO | fixed |
| Backend termination required | YES | NO | fixed |

The after representation also records the one complete canonical manifest separately from compact per-score debug payloads; it is not counted as duplicate JSON.

## 8. Query and payload scaling

| Metric | 10 | 25 | 100 |
| --- | ---: | ---: | ---: |
| Technical wall time | 12.317 s | 21.703 s | 96.252 s |
| Process-pool calculation | 4.153 s | 8.623 s | 31.648 s |
| Source-validation queries | 23 | 53 | 203 |
| All observed SQL / score-evidence queries | 519 | 967 | 3,183 |
| Unique price states materialized/validated | 7,360 | 16,960 | 64,960 |
| Canonical manifest bytes | 694,003 | 1,610,116 | 6,163,151 |
| Total per-score payload bytes | 98,906 | 247,326 | 989,736 |
| Maximum per-score payload bytes | 9,893 | 9,894 | 9,898 |
| Process RSS growth | 12.78 MiB | 18.63 MiB | 92.45 MiB |
| Checkpoint count | 32 | 39 | 68 |
| Maximum checkpoint gap | 5.539 s | 5.654 s | 49.020 s |

The gate asserts validation queries below `8N + 50`, maximum score payload below 50,000 bytes, one canonical manifest below 20 MB, and no checkpoint gap of 60 seconds or more. The 10/25/100 results show approximately linear growth in unique source states, manifest size, payload and validation queries; the quadratic per-score universe expansion is absent.

## 9. Deterministic pipeline gates

All three gates use frozen provider inputs, real orchestration, real production Technical/evidence code, real migrations, and a disposable PostgreSQL database. Non-Technical downstream adapters are deterministic to prohibit provider calls and Winner mutation while proving orchestration handoff through the final stage.

| Stage | 10 | 25 | 100 | Status |
| --- | ---: | ---: | ---: | --- |
| Fundamental | PASS | PASS | PASS | reached |
| Market handoff | PASS | PASS | PASS | reached; five first-fetch incident symbols included at 100 |
| Technical | PASS | PASS | PASS | real production path |
| Combined | PASS | PASS | PASS | reached |
| Ranking | PASS | PASS | PASS | reached |
| CERI | PASS | PASS | PASS | reached |
| Setup | PASS | PASS | PASS | reached |
| Lifecycle | PASS | PASS | PASS | reached |
| Alerts | PASS | PASS | PASS | reached |
| Winner | PASS | PASS | PASS | reached without live mutation |

The 100-symbol retry preserves exactly 100 Technical rows and the same anchor output across process-pool and pure sequential execution.

## 10. Tests

| Gate/suite | Result |
| --- | --- |
| Focused Technical work/score/confidence/eligibility, combined identity, calculation identity, domain fencing, pipeline executor, background worker/job service | 180 passed, 0 failed |
| Recovery suite excluding scale gate | 7 passed, 0 failed, 1 deselected |
| Impacted PostgreSQL configuration, worker progress, Technical artifact and consumer eligibility | 16 passed, 0 failed |
| Observability and pipeline metrics | 41 passed, 0 failed |
| Deterministic PostgreSQL 10/25/100 scale gate | 1 passed, 0 failed |
| One broad repository run (`tests`, including e2e/integration/recovery) | 4,098 passed, 10 skipped, 10 failed in 6,023.06 s; every failure was remediation-induced test-isolation/API/schema-inventory drift, then fixed |
| Exact rerun of all broad-run failures | 10 passed, 0 failed (4 compatibility, 5 inventory, 1 deterministic 10/25/100) |
| Reconciled distinct broad inventory | 4,108 passed, 0 currently failing, 10 intentionally skipped |

Warnings are pre-existing Starlette, Alembic path-separator, and cyclic-FK ordering warnings unless stated otherwise. No test invoked live providers.

## 11. Git state

- Branch: `codex/whole-application-recovery-remediation`
- Starting HEAD: `595102d6a70a14dd35eff75601141119515c07`
- Final HEAD: the report/test commit containing this file; resolve with `git rev-parse HEAD` (a commit cannot embed its own hash)
- Commits:
  - `d04f4945a50526d72857dede3cb145746e87593e` — fresh-fetch lineage and visibility contract
  - `289141130c0fdf0a368fe44275dfa07cc95d2b0c` — canonical Technical lineage/evidence
  - `d04a290` — independent Technical control checkpoints, cancellation and watchdog diagnostics
  - `e149f04c18934023c11ff73c4821a1535dfb4dc8` — recovery tests and initial certification report
  - final control isolation, compatibility and report closure commit — see final HEAD
- Worktree after remediation: only the pre-existing untracked `docs/remediation/recovery/WHOLE_APPLICATION_RECOVERY_AUDIT.md` remains; it was not modified or staged
- Migration status: repository/disposable test head `0084_technical_recovery`; retained local application database intentionally remains at `0083_winner_scope_truth` because this task did not authorize deployment
- PostgreSQL used for certification: 18.3
- Push: none

The verified starting baseline was local `main` at `595102d` (48 commits ahead of and 0 behind its configured upstream), migration 0083, no active SwingLens Python process, no active background job, retained run 163/pipeline 154 with pipeline 154 failed at Technical. The reported baseline hash in the task text contained a typographical extra character; the repository object is `595102d6a70a14dd35eff75601141119515c07`.

## 12. Remaining findings

No remaining P0 or P1 finding was reproduced in the deterministic remediation gates. The branch is ready for review and for a separately authorized small live vertical slice. It is not certified for the final 100-symbol live canary until that slice is run and reviewed.
