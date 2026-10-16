# Task 2.9.12d Setup Transaction Remediation

## A. Executive verdict

**PARTIAL.** A concrete Setup read/calculation transaction ownership defect was
corrected and its focused regression passed. The one authorized Chromium-100
certification exceeded its 23-minute outer deadline before pytest produced a
terminal result. It is therefore not a PASS and Tasks 2.9.12a/2.9.12d remain open.

The retained runtime monitor did not reproduce either the Technical or Setup
transaction invariant during 1,519 observations through termination, but a clean
non-terminal interval cannot replace the complete browser case.

## B. Timing

- Task start: `2026-10-14T18:30:04.8756455+02:00`.
- Task deadline: `2026-10-14T19:15:04.8756455+02:00`.
- Focused regression timestamp: `2026-10-14T18:34:48.241479+02:00`.
- Focused regression runtime: `0.856` seconds (`0.298` case seconds).
- Chromium-100 start: `2026-10-14T18:35:56+02:00`.
- Chromium-100 outer termination: approximately `2026-10-14T18:58:56+02:00`.
- Chromium-100 outer runtime: `1380.141` seconds.
- Reporting/cleanup checkpoint: `2026-10-14T19:01:52.6203185+02:00`.
- Total elapsed to that checkpoint: approximately 31 minutes 48 seconds of the
  45-minute budget.

## C. Exact root cause

### Proven transaction owner

`SetupLifecycleSnapshotCaptureService.capture_snapshots_for_run` received the
pipeline's authoritative domain `Session` and used that same Session for both:

1. `SetupLifecycleSourceLoader.load_run_context`, whose first `db.get(UploadRun, …)`
   implicitly opened a PostgreSQL transaction; and
2. `SetupLifecycleRepository.canonical_snapshot_histories_before`, the last read
   required to materialize the Setup calculation inputs.

The service then passed Session-bound source rows and histories into
`build_run_context_snapshots`. That function performs the CPU-heavy 100-ticker build
before the first authoritative Setup write. No commit or rollback separated the
last required read from that calculation, so the caller-owned pipeline Session could
remain idle in transaction during calculation. This is a **shared pipeline Session /
misplaced read boundary (class B/D)**; the interval is read-only, not the Setup
publication transaction.

### Retained anchor statement

The retained `execution_configuration_anchors` SELECT is consistent with the same
pipeline authority Session's frozen-configuration validation, but PostgreSQL records
only the connection's last statement. The retained artifacts did not contain a
backend-PID-to-Python-function correlation. Consequently, that SQL statement alone
does not prove whether the observed PID was in the source-materialization interval or
a later handoff/publication validation interval. The code trace above proves the
long-lived read/calculation boundary; the timed-out final certification did not
provide a terminal measurement sufficient to claim that every later Setup boundary
is certified.

This limitation is part of the PARTIAL verdict rather than being concealed as a
closed forensic finding.

## D. Correction

Changed production file:

- `app/services/setup_lifecycle/snapshot_builder.py`

Changed test file:

- `tests/setup_lifecycle/test_snapshot_builder.py`

Before:

- Source rows, frozen identities, price/context rows, and bounded snapshot histories
  were loaded on the pipeline publication Session.
- That read transaction remained open while per-ticker Setup DTOs were calculated.
- The same Session later verified the decision handoff and published authoritative
  Setup evidence.

After:

- Setup source/context and history acquisition runs in a short
  `bounded_domain_session`, explicitly inheriting the current execution attempt.
- The read Session uses the existing `expire_on_commit=False` bounded-session
  contract, rejects any pending `new`, `dirty`, or `deleted` state, commits the
  read-only transaction, verifies that no transaction remains, and closes before
  calculation begins.
- Preloaded histories are passed into `build_run_context_snapshots`; the calculation
  does not reopen the read Session.
- The original pipeline Session remains the sole owner of transition-handoff
  verification and Setup evidence publication.
- The existing verification of the frozen market cutoff, exact source membership,
  decision-handoff manifest, calculation identities, execution token, worker
  generation, and configuration hashes is unchanged.
- The existing authoritative publication transaction and rollback behavior are
  unchanged. No financial formula, ticker scope, configuration selection, runtime
  threshold, or execution fence was weakened.

## E. Transaction evidence

| Invariant | Before | After |
|---|---|---|
| Setup read transaction owner | Pipeline publication Session | Short independently fenced child Session |
| Transaction retained across CPU work | Yes, proven by the call chain and previously observed 120.398-second failure | No in the focused proof; child and parent Sessions were transaction-free at the build boundary |
| Maximum observed Setup transaction age | `120.397988s` at the retained failure | Not measured to a terminal browser result; 1,519 monitor observations reported no violation before timeout |
| Technical idle transaction recurrence | Not observed after Task 2.9.12a | Not observed in the retained 2.9.12d monitor interval |
| Execution ownership preserved | Pipeline domain fence | `bounded_domain_session` explicitly inherits the same job ID/token; commit-time fence remains active |
| Atomic Setup publication | Pipeline Session | Unchanged pipeline Session and existing handoff/source revalidation |

The retained runtime monitor covered
`2026-10-14T18:44:16.849178+02:00` through
`2026-10-14T18:58:47.404118+02:00`, with 1,519 observations and zero reported
transaction failures. Its last observation still reported one active job.

## F. Focused regression

Exact command:

```text
.\.venv\Scripts\python.exe -m pytest \
  'tests/setup_lifecycle/test_snapshot_builder.py::test_setup_capture_closes_isolated_read_transaction_before_build' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12D_FOCUSED_REGRESSION.xml
```

Result: **PASS**, `1 passed, 1 warning in 0.86s`.

The test directly proves that source/context and history reads use a child Session,
the read transaction is active while those reads occur, both child and parent
Sessions are transaction-free at the simulated calculation boundary, an independent
observer can read the durable pre-calculation state, and no pending parent write is
published. Existing domain-fence tests continue to cover stale-token rejection; this
focused proof does not replace the browser's 120-second invariant.

JUnit: `TASK_2_9_12D_FOCUSED_REGRESSION.xml`.

## G. Chromium-100 certification

Exact command:

```text
.\.venv\Scripts\python.exe -m pytest \
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-100]' \
  --browser chromium -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12D_CERTIFICATION.xml
```

An outer process deadline of 1,380 seconds was applied.

- Result: **TIMEOUT / NOT CERTIFIED**.
- Outer runtime: `1380.141` seconds.
- Outer marker: `TASK_2_9_12D_OUTER_COMPLETED=False`.
- Pytest did not emit a terminal traceback or write its JUnit because the process tree
  was stopped at the mandatory deadline. `TASK_2_9_12D_CERTIFICATION.xml` therefore
  records the outer-harness timeout explicitly; it is not represented as a pytest
  PASS.
- Pipeline terminal state: not reached/retained. The last monitor observation had
  pipeline ID 1 and one active job.
- Population and final financial evidence counts: not reached and not certified.
- Transaction monitor: 1,519 retained observations, zero failures through the last
  observation.
- Provider mode: deterministic/frozen. No live provider was used.
- Disposable database: `swinglens_pytest_cert_f723ce91dd75`; removed after forced
  timeout cleanup.
- Test runtime: `20261014T163556Z-8b930959`; all six test-owned processes and the
  listener on port 55445 were removed. Final checks found zero matching processes,
  zero listeners, and no remaining database.

No second Chromium-100 run was started.

## H. Compatibility

The correction is confined to Setup capture input materialization. It does not alter
the certified Windows worker-recovery or controller-lifecycle fixtures from Tasks
2.9.12b/2.9.12c, nor CERI, Winner, Fundamental, Technical, or pipeline formulas.
Those focused certifications are not invalidated by this change.

Task 2.9.12a still requires a complete terminal Chromium-100 pass. A later,
separately authorized certification should allow enough bounded time for the
post-Setup tail; no broad recertification or full Phase 2.9 suite was started here.

## I. Source pins

Reviewed production functions:

- `build_run_context_snapshots`
- `_load_run_context_histories`
- `SetupLifecycleSnapshotCaptureService.capture_snapshots_for_run`

Registered operation family: existing `SETUP` capture/publication family. No new
writer or mutation boundary was introduced. The current overlay hash was regenerated
with the official generator; historical immutable artifacts were not changed.

Final validator output:

```json
{"stale": []}
```

## J. Safety

- Only the exact focused regression and the one authorized Chromium-100 node ran.
- No full E2E or repository suite ran.
- No normal application database was accessed or modified.
- No historical Runs 22–33 were modified.
- No live IBKR, SEC, EODHD, or CERI provider was used.
- Credentials, tokens, anchor payloads, and database user information are omitted.
- Existing working-tree changes were preserved; no reset, stash, discard, merge,
  commit, push, or deployment occurred.
- Forced-timeout cleanup targeted only the retained certification runtime and exact
  disposable database.

## K. Remaining blockers

1. The exact Chromium-100 test has no terminal PASS because it exceeded the mandated
   23-minute outer deadline.
2. Final pipeline status, 100-ticker population counts, authoritative Setup/Winner
   evidence counts, and orphan-job assertions were not reached.
3. The retained historical backend PID was not correlated to a Python function in
   existing logs. The corrected read/calculation boundary is proven locally, but the
   exact browser-level maximum Setup transaction age remains uncertified.
4. No further code correction is justified from the timeout alone. The smallest next
   action is a separately authorized single Chromium-100 certification with a longer
   finite outer allowance and the same invariant monitor—not an automatic code change
   or broad suite.

## L. Release status

`TASK 2.9.12A CARDINALITY: OPEN`

`TASK 2.9.12D SETUP TRANSACTION REMEDIATION: OPEN`

`FULL PHASE 2.9 RELEASE CERTIFICATION: NOT YET EXECUTED`

Production readiness is not declared.
