# Run 40 remediation: contract and impact review

Recorded before production source edits, 17 October 2026.

Certification follow-up: the first strict 88-symbol browser execution exposed a
canonicalization checkpoint gap. Review of `canonicalize_run`, its evaluation
adapter, `canonicalize_snapshots` and repository batch writers established that
all affected canonical keys are advisory-locked before native writes. The fix
retains those locks and the existing evaluation transaction while invoking the
same operations in five-group batches. Optional callbacks preserve direct callers'
default behavior. Reconciliation, evidence validation and episode loops retain
their ordering and calculations with a five-ticker reporting cadence; primary
refresh retains the same sorted key order in bounded calls. Exceptions from these
callbacks enter the same atomic rollback and fenced failure-audit path. The real
PostgreSQL regression injects a failure after a canonical batch and verifies
independent-reader isolation and complete rollback before testing the existing
post-detector interruption boundary.

The eleven reviewed source files match the forensic SHA-256 pins. HEAD remains
96204fb6fa15aed858fa06ba52d4b70c86d1d8b5; origin/main is d3b66c32767257d1187719206fa948656ffe3045.
Existing uncommitted worker memory recycling and supervisor graceful-recycle changes
are unrelated and must be preserved. No repository or parent AGENTS.md was found.
The original forensic directory is immutable evidence for this task.

## Callers, transaction owners and downstream impact

| Boundary | Callers / consumers | Existing contract and defect | Intended correction / protection |
|---|---|---|---|
| `fence_stalled_jobs` | Supervisor `_fence_no_progress`, worker recovery, recovery tests | Caller commits; currently locks every healthy candidate and rewrites metadata. Progress is atomically recorded with `last_progress_at` and sequence. | Read healthy observations without mutation; lock only overdue/cancel candidates and revalidate current ownership/progress. Keep true stall, lease recovery and cancellation behavior. |
| `control_plane_transaction` / domain commit fence | Detached callbacks, autonomous heartbeat, worker bookkeeping | 3s lock timeout; token check during work, mandatory locking check at outer domain commit; nested savepoints are not publication. | Preserve timeout and ownership fence. Explicitly classify infrastructure failure and propagate it without a terminal business-state transition. |
| `_pipeline_step` / executor exception handlers | Initial pipeline and CERI/dependency continuation paths | Step-start and step-end commits; generic exception currently commits staged domain writes, then terminalizes pipeline. | Roll back unfinished work before failure recording. Infrastructure exceptions reach worker orchestration without terminalizing the parent. Preserve completed earlier stages and attempt history. |
| `mark_job_failed_or_retry` / pipeline failure rollup | Worker, dependency continuation, queue claim predicate | Job retry and terminal-parent transition disagree; continuation can bypass root-job failure rollup. | Reconcile job, current step and parent in the same retry bookkeeping transaction, using existing QUEUED/INTERRUPTED states. Terminal/cancel/revoked authority must not reactivate; exhausted retries terminate explicitly. Keep terminal-parent claim guard. |
| `evaluate_run` | FULL_PIPELINE initial/resumed evaluator, standalone setup job, direct authorized service callers | Caller owns successful commit; nested semantic writers do not commit. Failure handler currently stages FAILED alongside domain work. | One atomic lifecycle publication transaction: canonical pointers/audits, changes, episodes, transitions, alerts and completed evaluation commit together. Roll back all staged artifacts on any callback/calculation failure. Record only failure audit after rollback, under existing ownership authority. |
| Signal change key/body/proof | Detector, repository retry check, alert proof validation, change queries, replay/maintenance | Key excludes evaluation attempt ID; proof/body includes original attempt provenance. Detector omits reused events from downstream inputs. | Keep immutable original proof/provenance. Compare financial event identity separately from attempt provenance, validating both retained and incoming proof. Return reused event IDs for downstream processing; reject changed content/source/configuration. |
| Current selection / lifecycle publication visibility | Canonical history loader, setup queries, change queries, source loader, episode selection, point-in-time feature export and replay comparison views | Current pointer alone exposes legacy failed evaluations. New atomic transactions prevent future partial visibility, but existing failed artifacts remain. | Use completed evaluation publication status for serving, retain historical audit access, and preserve explicit standalone/legacy no-evaluation behavior. Record exact validated reuse IDs in the existing evaluation audit, without schema changes. Episode visibility follows its latest evaluation evidence, not merely its opening attempt. |
| Winner continuation | Pipeline stage following evaluation, winner source manifest / prediction capture | Winner consumes independently certified upstream financial sources and the exact frozen handoff. Its consumer eligibility explicitly rejects setup lifecycle features; sequencing follows committed lifecycle evaluation. | Exercise native winner capture after successful evaluation and after retry; no continuation on rolled-back lifecycle work. Do not introduce setup features into Winner calculations. |
| Detector validation | Projection pin discovery, native change validation, repository insert, decision authority | Source proofs/native calculation repeated per event; no progress inside detector. | Measure first; reuse only exact transaction/source/configuration validation, still validate every event and token. Bounded progress/cancellation with no intermediate domain commits. |

## Regression coverage to execute

Existing relevant suites: background job/worker and supervisor reliability tests;
PostgreSQL worker progress, pipeline authority/dependency, lifecycle immutability,
setup reconciliation and alert evidence tests; native writer authority rejection
tests; setup evaluation/change/query/replay/maintenance and winner tests; existing
single-run browser certification and frozen real-writer cardinality harness.

New PostgreSQL coverage must run healthy watchdog delay concurrently with heartbeat,
true stall and competing progress, real worker claim/retry/exhaustion/cancellation,
post-detector interruption with independent-reader checks, unchanged/altered replay,
downstream alert/lifecycle processing, and representative 88-symbol execution.

Performance evidence must distinguish the historical Run 40 timings from repeatable
before/after fixture timings. Production Run 40 and its artifacts must remain
unchanged; eventual disposition will be a recommendation, not an automatic replay.

This document records the proposed boundaries. Implementation details, executed
commands, measurements, changed contracts and the final readiness verdict belong
in the accompanying final remediation report after certification.

Winner certification follow-up, reviewed before changing the shared validator:
the complete positive browser path reaches
`WinnerPredictionCaptureService._capture_ticker` -> `prediction_capture_authority`
-> `core_mutation_authority.validate_source_values`. Winner supplies an explicit
item Session and an intentionally detached sector projection. The validator's
SECTOR branch discards that Session and calls `object_session(source)`, yielding
None. The retained full browser traceback proves the resulting `.scalars` error.
Other callers are core source/decision validation, sector-universe admission and
transition preflight; their signatures and financial rules stay unchanged. The
correction must retain the explicit Session (with the attached-source fallback
for existing callers), reject missing session authority, and validate all stored
sector child fields. The reduced head-projection mask must not suppress checks
on fully loaded child rows. A real PostgreSQL regression will accept the valid
detached projection and reject a mutated child score, then run native Winner.

Upstream progress follow-up, reviewed before edits: the strict browser observer
recorded a transaction without fresh progress at 17:21:22 UTC in RANKING_PROFILES
(17:19:21–17:21:32); Technical had completed at 17:18:44. An earlier observation
also failed during Technical finalization. Technical already reports calculation
and manifest validation, but not its per-score evidence publication loop.
Ranking's pipeline adapter, all-profile producer, source validation and evidence
writer have no intra-stage progress callback. Callers are the full pipeline,
explicit ranking refresh routes, native certification and service tests. Optional
callbacks will report completed source/evidence batches; pipeline adapters retain
cancellation classification and detached job progress. Cohort calculation order,
source/proof validation, transaction ownership and commit fences remain unchanged.
Direct callers without callbacks retain their existing behavior. Native PostgreSQL
will verify callback interruption rolls back staged evidence, followed by the
existing full browser pipeline with its unchanged 120s/30s observer thresholds.

Final replay review, before edits: `_completed_publication` validates snapshots
and changes with callbacks, then validates episodes and alerts without another
cancellation/progress boundary. Its sole caller is the completed-result branch of
the existing atomic evaluator. Materialize those same query results and report
after each validated entity. Queries, proof validators, order, returned result,
transactions and authority remain unchanged. Focused PostgreSQL interruption at
each new read boundary must preserve the original completed publication and every
domain count; a subsequent replay must return that original result. The long
88-symbol run is allowed to finish on its already-loaded version; record this
final read-only callback delta and its own tests explicitly in the source matrix.
