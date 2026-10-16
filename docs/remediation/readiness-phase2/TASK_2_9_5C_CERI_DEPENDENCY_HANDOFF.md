# Task 2.9.5c — CERI Offline Dependency Handoff Remediation

## A. Verdict

**FAIL.** The exact certification test ran once and failed. The supported dependency-admission service was wired into the offline fixture, but a fixture-local cutoff assertion compared equivalent UTC timestamps textually (`Z` versus `+00:00`) before admission. The worker rolled back the transaction, no dependency persisted, and the continuation was requeued. No correction/retest cycle was started.

## B. Duration

- Start: `2026-10-14T12:12:07.4027113+02:00`
- Evidence collection finish: `2026-10-14T12:16:09.1849915+02:00`
- Elapsed: `00:04:01.7822802`
- Budget: 20 minutes; pytest outer deadline: 600 seconds

## C. Root cause and production contract

Production creates the handoff through `prepare_ceri_workflow_dependency()`. It requires an admitted pipeline and the currently executing `FULL_PIPELINE` job, and idempotently creates one `PipelineDependency` with:

- dependency type `CERI_WORKFLOW`;
- state `RUNNING` (active/incomplete);
- the admitted pipeline ID;
- `root_job_id` equal to the executing continuation job;
- a workflow key and continuation identity derived from pipeline, workflow, and continuation step;
- no required child or continuation job while the handoff remains incomplete.

The active-state set is `PENDING_ENQUEUE`, `QUEUED`, and `RUNNING`; a partial unique index permits one active dependency per pipeline/type. Root reconciliation preserves `WAITING_FOR_CERI_COMPLETION` only when exactly one active `CERI_WORKFLOW` row is associated with that pipeline and completed root/continuation job.

The fixture now calls this supported service twice and asserts idempotent identity, cardinality one, correct pipeline/root ownership, `RUNNING`, matching workflow key, and null child/continuation IDs. However, the earlier frozen-cutoff assertion compared payload text `2026-10-14T10:14:16.696836Z` with the in-memory datetime rendering `2026-10-14T10:14:16.696836+00:00`. The instants are equal, but the textual assertion raised `AssertionError` before the service call. The job was classified `UNCLASSIFIED_RETRYABLE`, the transaction rolled back, and zero dependencies remained.

## D. Dependency handoff

| Invariant | Expected | Observed |
|---|---|---|
| Active CERI dependency count | 1 | `0` after rollback |
| Dependency ownership | Pipeline `1`, executing continuation job `6` | Not persisted; fixture failed before admission |
| Configuration anchor | Frozen C1 | Continuation retained C1 anchor, but no dependency record was created |
| Dependency status | Active and incomplete (`RUNNING`) | No persisted status |
| Continuation result | `WAITING_FOR_CERI_COMPLETION` | No result; job `6` returned to `QUEUED`, attempt `1` |
| Pipeline status | `WAITING_FOR_CERI_COMPLETION` | `FAILED` at `CERI_PROVIDER_INGEST` after the assertion |
| Duplicate resume | HTTP 409 | Not reached |
| Additional persisted jobs | 0 | `0`; job IDs remained `1–6` |
| Setup/Winner publication | 0 | Lifecycle `0`, transitions `0`, alerts `0`, Winner `0` |

The original root remained `QUEUED` at attempt `1`. The continuation remained distinct as job `6`, `QUEUED` at attempt `1` after the retryable fixture assertion.

## E. Financial evidence integrity

- Evidence before and after the attempted continuation was identical: 12 rows, IDs `1–12`, with unchanged payloads.
- Frozen C1 anchor ID: `6685a9ddd6b9cd3c0a45c56f5363f560096c63b42fa012b8d09f675feb35be78`.
- Frozen C1 fingerprint: `94c475062178ceeecd40b225c03f58ed4b324fc32268af905c23ec66a84a01f5`.
- Calculation context: `1`.
- PIT cutoff: `2026-10-14T10:14:16.696836Z`; input session: `2026-10-13`.
- No duplicate financial evidence or premature Setup/Winner output was observed.

## F. Files changed

- `scripts/qa/t13e_deployed_probe.py` — use the production dependency-admission service in the offline scheduler; add fixture-local identity/cardinality/idempotency assertions and dependency diagnostics. No production service changed.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json` — refreshed only the reviewed probe hash with the official current-overlay generator.
- `docs/remediation/readiness-phase2/TASK_2_9_5C_CERTIFICATION.xml` — retained failed certification evidence.
- `.qa_work/task_2_9_5c_pytest.stdout.log` and `.stderr.log` — retained bounded process output.
- This report.

The existing modified test file and all other dirty-worktree content were preserved and not edited by this task.

## G. Certification

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_phase4_configuration_certification_postgresql.py::test_public_resume_retains_c1_and_duplicate_completed_work_stays_behind_ceri_barrier' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_5C_CERTIFICATION.xml
```

- Result: `1 failed`, 2 warnings
- JUnit runtime: `63.534 s` (console: `63.58 s`)
- Failure: test line 399 expected continuation `COMPLETED`; observed `QUEUED`
- Retained classification: `AssertionError`, `UNCLASSIFIED_RETRYABLE`

## H. Safety verification

- Disposable PostgreSQL isolation and test-owned worker/subprocess identities remained active.
- Synthetic IB readiness and the offline CERI scheduler prevented live IBKR, SEC, or EODHD acquisition.
- No normal database or historical run was accessed or mutated.
- No matching task Python process remained after completion.
- Retained stdout contains two database URLs with `<restricted:userinfo>`; no unredacted authority, password/API-key pattern, or secret sentinel was found. JUnit contains no database URL.
- Source-pin sequence: expected stale path `scripts/qa/t13e_deployed_probe.py`, official current-overlay regeneration, final `{"stale": []}`. Historical pin artifacts were not changed.

## I. Remaining blockers

`TASK 2.9.5 CERI RESUME BARRIER: OPEN`

The durable handoff remains uncertified because the fixture compares equivalent cutoff representations as raw strings before invoking dependency admission.

## J. Next recommended single task

In one bounded fixture-only task, normalize the serialized `Z` cutoff to a timezone-aware datetime and compare the instant to the frozen cutoff, preserving all dependency-service and idempotency assertions. Then rerun only this exact node once. Do not begin another Phase 2.9 regression.
