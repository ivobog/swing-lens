# Run 40 lifecycle remediation

This report records the isolated certification candidate before integration with
GitHub main. See [MAIN_INTEGRATION.md](MAIN_INTEGRATION.md) for the merged code,
its additional validation and the current release status.

17 October 2026. Implementation, native PostgreSQL certification and complete
browser pipeline certification are complete. Production activation is pending.

## A. Corrections and contracts

The original failure is proven by PostgreSQL's blocking graph: supervisor backend
43312 retained job 1384's row lock for 16.916s while the job was healthy; both
independent worker heartbeats exceeded the unchanged 3s lock timeout. The precise
cause of the supervisor's client-side pause remains unknown and is not needed by
the correction. Original evidence is unchanged in
[the forensic report](../../forensics/run40-lifecycle-failure/REPORT.md).

| Correction | Implementation and downstream contract |
|---|---|
| Healthy watchdog observation | `background_job_service.fence_stalled_jobs` reads candidates without row locks or healthy-job metadata writes. Only actual overdue/cancellation candidates enter a short conditional `FOR UPDATE SKIP LOCKED`, rechecking status, token, progress sequence/time, stage and deadline and loading fresh lease/heartbeat fields. Decision logging runs after the outer commit. True stalls and expired ownership remain fenced. |
| Claimable infrastructure retries | Typed `ControlPlaneLockTimeout` propagates through `pipeline_executor` to worker bookkeeping. `background_job_service` classifies it explicitly; `pipeline_service.mark_pipeline_job_failure` reconciles the job, root/dependency continuation, parent and current step in one control transaction. Retry uses QUEUED/INTERRUPTED with ACTIVE authority and a supported resume boundary; exhaustion records `CONTROL_PLANE_RETRIES_EXHAUSTED`. Terminal/cancelled/revoked authority suppresses retry. The terminal-parent claim guard remains intact. |
| Atomic lifecycle publication | `evaluation_service.evaluate_run` wraps complete publication in its existing semantic transaction. On failure it rolls back business work and records only a fenced failure/cancellation audit in a separate bounded session. `pipeline_executor._pipeline_step` rolls back before ordinary failure bookkeeping, retaining the step ID before ORM expiration. A successful commit publishes selections, canonical revisions, changes, episodes, transitions, alerts and completed evaluation together. Snapshot capture retains its earlier completed-stage boundary. |
| Serving incomplete artifacts | `repository` gates evaluation-owned selections/history, changes, events, episodes and alerts on successful publication. Exact validated reuse IDs can be published without changing their original creator. Episode visibility follows its latest evaluation evidence. `query_service`, `maintenance_service`, `export_service` and `replay_service` apply the same serving contract. Raw audit access and explicit legacy/standalone rows with no evaluation owner retain their existing behavior. |
| Immutable, idempotent replay | `repository.add_signal_change_event` excludes only evaluation-attempt provenance from semantic equivalence and revalidates the original proof and exact sources/configuration. It preserves original creator/proof bytes and rejects altered contents. `change_detector` forwards reused event IDs into lifecycle/alert processing. Completed replay validates the publication's historical selection and retained artifacts and returns its original completed result; a newer current selection does not invalidate that historical replay. The executor retains exact capture-handoff IDs for retry. |
| Measured validation reuse | `change_authority` uses one comparison-scoped, transaction-pinned validation bundle, exact source/configuration locks and an independent native comparison. It rejects transaction changes, ORM/untracked JSON edits, SQL mutations and altered yielded pins. Execution ownership is not cached: per-write authority and the final locking commit fence remain. |
| Bounded useful progress | `canonicalization.canonicalize_run` retains all canonical-key locks and runs existing native operations in batches of five groups within the same transaction. Evaluation/reconciliation/episode/primary-refresh loops report every five tickers; detector reports per ticker/every ten events. `technical_score_service` and `ranking_profile_service` report completed source/evidence batches through the executor's existing job callback, with cancellation. No financial calculation or publication commit was added. |
| Winner source validation | Positive certification exposed an existing `core_mutation_authority.validate_source_values` defect: a detached sector projection's explicit Session was discarded and `.scalars` was called on None. The validator now retains that Session, preserves the attached-source fallback, rejects missing session authority and validates complete stored sector child fields. A head-only projection mask no longer suppresses child checks. Native SQL tampering still rejects. |

There is no schema migration, timeout increase, financial formula change, new
production recovery framework or Winner feature change. Winner continues to use
its independently certified financial inputs and frozen handoff; setup lifecycle
features remain prohibited as Winner inputs.

The pre-edit review and follow-ups are in
[IMPACT.md](IMPACT.md).
The final production diff review covers 15 files and their callers, transaction
owners, commit/rollback behavior, authority and serving consumers:
[review record](evidence/final-diff-review.json).

## B. Executed verification

Exact commands, environments and JUnit paths are in
[VERIFICATION_COMMANDS.md](VERIFICATION_COMMANDS.md).
All persistence/concurrency cases use migrated disposable PostgreSQL databases.

| Requirement | Executed result |
|---|---|
| Healthy watchdog and genuine stall/expired fencing | PASS. Real worker/autonomous heartbeat survives a healthy supervisor transaction delayed 3.4s; genuine stalls and competing progress/ownership remain fenced correctly. Worker/progress/authority suite: 14 passed; final watchdog/evaluation boundaries: 76 passed. |
| Transient recovery, executable retry, exhaustion and cancellation | PASS. Actual root and dependency-continuation claims execute after injected post-detector control-plane failure; rollback, attempts, parent/job authority, deterministic exhaustion, cancellation and no orphan/duplicate continuation are checked with PostgreSQL. |
| Transaction and publication safety | PASS in focused native cases. Interrupted canonicalization and post-detector work remain invisible to a second connection and roll back. Real failed SQL-session regression preserves the original division-by-zero exception and commits only failure bookkeeping. Final canonical/interruption/legacy/SQL group: 3 passed. The full 88-history case below also passed. |
| Replay, original proofs, alerts and historical selection | PASS. Native retained-partial reconstruction reuses unchanged original events/proofs, publishes missing native alerts, deduplicates replay and rejects JSON/SQL/pin changes and transaction loss. Completed replay survives a newer selection. Final historical replay: 1 passed. |
| Existing downstream authority/reconciliation/Winner | PASS. 33 PostgreSQL cases passed. Detached-sector valid/missing-session/tampered-child cases followed by actual Winner insertion: 2 passed; shared source/sector unit suites: 48 passed. |
| Technical/ranking progress and cancellation | PASS. 85 affected unit tests, 2 PostgreSQL publication-interruption cases and 1 pipeline cancellation regression passed. The PostgreSQL case checks interrupted artifact publication; the earlier committed input boundary is deliberately retained. |
| Broad unit regression | 454 passed and one existing heartbeat timing assertion failed under concurrent load. A focused 23-test rerun, including that assertion and serving consumers, passed. Both results remain in evidence. |
| Final strict 88-symbol complete pipeline | PASS on isolated `80026eb`: terminal COMPLETED, all 13 stages COMPLETED, exactly 88 fundamental/technical/combined/CERI/setup/Winner outputs, 528 provider source records and exactly 2 FULL_PIPELINE jobs. 4,507 runtime observations, zero invariant failures. |
| Final comprehensive browser regression | PASS on the same application sources: 659/659 comparisons, 19 GUI surfaces and 51 database relationships; no failures. Final browser batch: 2 passed in 3,645.23s including fixtures/UI checks. |
| Native 88 with 321 historical snapshots | PASS: 1 cold-process PostgreSQL case, 3,335.90s including restore/capture/faults/replay/Winner. Actual capture, canonical/post-detector interruptions, atomic publication, unchanged completed replay and all 88 Winner insertions pass. Successful evaluation produced 1,069 changes, 88 transitions and 44 alerts, with zero failures; all 88 selected IDs equal captured IDs. |

Final native evidence:
[JUnit](evidence/native-88-final-runtime.xml),
[measurement](evidence/final-runtime-88.json),
[completed publication counts/IDs](evidence/native-88-completed-publication.json).
The measurement's published table counts include historical rows: 409 selections,
1,483 changes, 176 episodes, 802 lifecycle events and 44 alerts. Current evaluation
counts are the separate 88 / 1,069 / 88 / 44 values above. Native Winner inserted
88 with zero duplicates, exclusions or failures. Its fixture has no learned cohort
estimates; the recorded warnings/insufficient-estimate counts remain visible.

Final browser evidence:
[JUnit](evidence/full-e2e-upstream-progress-final.xml),
[88 metrics](evidence/e2e-upstream-progress-final/20261017T173318Z-1e80b117/db-results/provider-88-metrics.json),
[comprehensive report](evidence/e2e-upstream-progress-final/20261017T182519Z-9579c7c1/report.json).

The final native case restores an authenticated fixture into a newly created,
safety-checked disposable database. It then performs actual setup capture and all
application work in a cold process on final source. This avoids attributing an
old, long-lived fixture builder's loaded code to the final candidate. The builder
was stopped with 321 historical snapshots, zero current snapshots and zero current
evaluations, before the timed evaluation began. No production process was stopped.
[Fixture integrity and handoff](evidence/native-fixture-handoff.json).
The first restore invocation missed its explicit database argument and failed
before application work; the corrected invocation restored successfully. That
failure is retained as `native-88-final-runtime-restore-argument.xml`.

Earlier failed browser/native attempts and their proven corrections remain in
[CERTIFICATION_ATTEMPTS.md](CERTIFICATION_ATTEMPTS.md).
No PARTIAL pipeline is counted as passing. These tests use deterministic provider
fixtures; no live-provider canary is claimed.

## C. Performance

| Measurement scope | Before | After / final observation |
|---|---:|---:|
| Two-symbol native optimization fixture, 20 changes: whole-evaluation SQL operations | 683 | 485 (29.0% fewer) |
| Same fixture, unprofiled evaluation elapsed | 20.404s | Observations 11.744s, 20.829s, 14.869s and 19.837s |
| Profiled `_validate_evidence` calls | 86 | 46 |
| Profiled `validate_setup_projection` calls | 60 | 20 |
| Profiled canonical serialization calls | 19,805 | 12,379 |
| `_projection_pin` cumulative profile time | 15.473s | 1.980s |
| `validate_signal_change` cumulative profile time | 37.699s | 14.951s |
| Final strict 88 complete pipeline | No comparable baseline | 2,763.538s (46m03.5s) |
| Final strict 88 technical scoring | Existing test limit 300s | 261.197s |
| Final strict 88 CERI workflow / maximum checkpoint gap | Existing checkpoint limit 60s | 716.075s / 28.670s; zero CERI retries/recoveries/stalls |
| Final native 88-history successful evaluation / SQL | No comparable baseline | 630.539s (10m30.5s) / 21,738 whole-evaluation SQL operations; 276 useful checkpoints |

The two-symbol SQL counter covers the complete evaluation, not the detector alone.
Profiled overall wall time was 65.840s before and 65.340s after under different
load; no statistical wall-time speedup is claimed. The query/call reductions are
the stronger evidence. Native 88 measurement was recorded after all assertions.

Run 40's 33,454 detector SQL operations / 1,258.5s remain historical evidence,
not a like-for-like baseline for the synthetic fixture. No 88-symbol speedup ratio
is inferred between those different datasets/scopes. Progress/cancellation is
bounded by completed work batches, with no intermediate lifecycle commits.

## D. Preservation, source identity and remaining release gates

All eleven pre-edit reviewed source pins matched the forensic evidence, including
pre-existing dirty worker/supervisor changes. Existing dirty files were preserved.
The original forensic file hashes and five production domain fingerprints match
the baseline; Run 40 was not retried, repaired or republished. Pipeline 40 and
evaluation 57 remain FAILED; job 1384 remains QUEUED, retry 1, without a token.
[Preservation check](evidence/preservation-check.json).

At the time of this isolated certification, the source checkout remained at
`96204fb6fa15aed858fa06ba52d4b70c86d1d8b5`. No main commit, push or
production restart had been performed. Explicitly authorized local detached
certification snapshots exist only in the attached isolated worktree. Browser
certification used `80026eb`; the cold native run used `d839b59`. Final local
snapshot `4396ff7ad143360a044f7967445fd8f326f371d2` adds only the completed-replay
episode/alert progress and cancellation checks and their tests. Its queries,
validators, result construction, domain writes, commits and authority are
unchanged; the final focused PostgreSQL/unit group passed 10 tests.
[Exact final delta](evidence/final-replay-delta-review.json),
[executed boundary tests](evidence/final-replay-boundaries.xml).
Candidate hashes and the
version-specific test record are retained in
[candidate equivalence](evidence/candidate-equivalence.json).
All 439 application Python files match in source content; 303 have only newline
byte differences between checkouts. Edited application files match byte-for-byte.

Release gates recorded for this isolated snapshot were:

- The architecture registry check still fails with 19 pre-existing missing
  registrations. Baseline comparison proves zero unregistered sites added by this
  remediation; the release must resolve or explicitly disposition that existing
  check failure.
- Production processes predate this candidate, and a clean release has not been
  activated. The shared dirty checkout also differs across 413 paths from its
  cached `origin/main` reference. That is a local-reference comparison, not a new
  assertion about GitHub's current head. Reconcile the intended release baseline
  before an authorized deployment.
- Completed replay is CPU-heavy: the observed full retained-proof walk and
  pre-Winner checks took roughly 32 minutes for this 1,069-change history fixture,
  overlapping a focused test run. This is not an isolated benchmark. It completed
  correctly; snapshot/change and final episode/alert validation now all expose
  useful progress/cancellation. Further read-side validation reuse needs the
  same exact retained-view, transaction and source-pinning guarantees.

For Run 40, retain its failed pipeline/evaluation and original proof/event history.
Separately authorize an audited disposition of the orphaned queued job 1384;
prefer a fresh run after a clean, verified deployment. Do not force statuses to
COMPLETED or delete the 937 retained events. Any requested historical recovery
should first be validated in an isolated clone against its exact original frozen
inputs/configuration and provenance.

## E. Final verdict

WATCHDOG LOCK CONTENTION: PASS

INFRASTRUCTURE RETRY CONSISTENCY: PASS

LIFECYCLE PUBLICATION SAFETY: PASS

EVENT REPLAY IDEMPOTENCY: PASS

DOWNSTREAM COMPATIBILITY: PASS

88-SYMBOL EVALUATION: PASS

FULL PIPELINE END-TO-END: PASS

SAFE TO START A NEW PRODUCTION RUN: NO

The candidate was verified in isolation. See the main integration report for
subsequent source and test results before considering production activation.
