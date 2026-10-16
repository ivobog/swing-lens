# Task 2.9.5e — Superseded Root Execution-Authority Fencing

## A. Verdict

**PASS.** Operator resume now records an exact durable predecessor-to-continuation relationship, and the canonical job execution-authority predicate excludes only the superseded `FULL_PIPELINE` predecessor. The original root stayed preserved as queued history but was not authority-eligible, even with an expired `run_after`. Legitimate continuation, dependency-owned CERI work, and non-superseded retry authority remained intact.

`TASK 2.9.5 CERI RESUME BARRIER: CLOSED`

## B. Duration

- Start: `2026-10-14T12:34:19.8502534+02:00`
- Finish: `2026-10-14T12:41:23.2960063+02:00`
- Elapsed: `00:07:03.4457529`
- Budget: 20 minutes; final certification outer deadline: 300 seconds
- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`

## C. Root cause

The interrupted original pipeline root remained `QUEUED` because its retryable failure returned it to the durable queue. While the pipeline was `FAILED`, the pipeline-level authority predicate correctly kept it non-executable. Public resume then created a distinct continuation and changed the pipeline back to an executable state, but did not record that the continuation replaced the prior control job.

`job_execution_authority_predicate()` authorized every pipeline-owned job whenever the owning pipeline had `ACTIVE` execution authority and a nonterminal status. After the continuation moved the pipeline to `WAITING_FOR_CERI_COMPLETION`, the old queued root therefore regained authority. Its one-day `run_after` prevented only an immediate queue claim; it did not provide durable fencing.

The pipeline's `result_json.background_job_id` already identified the current control job at resume admission. It is now used only while the pipeline row is locked to identify the predecessor and persist exact job lineage. Claim-time authority is decided from that durable foreign-key lineage, not from mutable latest-state inference.

## D. Production correction

Two existing production boundaries changed:

1. `app/services/pipeline_service.py::resume_pipeline`
   - locks the `PipelineRun` row with `FOR UPDATE`, serializing concurrent resume admission;
   - validates and locks the exact current `FULL_PIPELINE` predecessor;
   - enqueues the replacement with `parent_job_id` pointing to that predecessor, stable `root_job_id`, incremented continuation depth, and `trigger_source=PIPELINE_OPERATOR_RESUME`;
   - creates the continuation, records lineage, changes the pipeline state, and replaces the current control-job identity in one transaction.

2. `app/services/pipeline_execution_authority.py::job_execution_authority_predicate`
   - detects only a `FULL_PIPELINE` job with a same-pipeline `FULL_PIPELINE` child whose explicit trigger is `PIPELINE_OPERATOR_RESUME`;
   - excludes that predecessor from every queue, recovery, and observability query using the canonical predicate;
   - leaves the replacement continuation and all non-superseded or dependency-owned work on the existing authority path.

No new status, migration, mutable latest-pointer claim rule, scheduling workaround, or second fencing mechanism was introduced. A concurrent claimant before the resume commit still sees the pipeline as terminal/non-executable; after commit it sees the durable replacement relationship. Concurrent resumes serialize on the pipeline lock, so predecessor and successor cannot both acquire authority through a check-then-act window.

## E. Execution-authority comparison

| Invariant | Before | After |
|---|---|---|
| Original root authority eligibility | `[1]` | `[]` |
| Authority after `run_after` expiry | Eligible | Ineligible; supporting proof used an already-expired timestamp |
| Legitimate continuation | Authorized | Authorized; job `6` completed on attempt `1` |
| CERI dependency status | `RUNNING` | `RUNNING`; exactly one active dependency |
| CERI work authorization | Not isolated by 2.9.5d | PASS — dependency-owned CERI job remained eligible |
| Non-superseded retry | Not isolated by 2.9.5d | PASS — retry with no replacement remained eligible |
| Duplicate resume | HTTP `409` | HTTP `409`; no new job |
| Pipeline status | `WAITING_FOR_CERI_COMPLETION` | `WAITING_FOR_CERI_COMPLETION` |

Final retained state:

- original root job `1`: `QUEUED`, attempt `1`, preserved for history, not authority-eligible;
- continuation job `6`: distinct `FULL_PIPELINE`, `COMPLETED`, attempt `1`, result `WAITING_FOR_CERI_COMPLETION`;
- dependency `1`: `CERI_WORKFLOW`, `RUNNING`, pipeline `1`, owned by continuation job `6`;
- dependency continuation identity: `3322d092bae9b1a0c75b0b0a5f59d3f6b00d7dab649719df748b9187310d5d8e`;
- dependency child and continuation job IDs: null, as expected for the active offline handoff;
- duplicate-resume job IDs before and after: `1–6`;
- authority-eligible IDs before and after duplicate resume: both empty;
- no `PIPELINE_ROOT_TERMINAL_WITH_ACTIVE_PIPELINE` recurrence.

## F. Financial integrity

- Original evidence after interruption: 12 rows, IDs `1–12`.
- Final evidence: 12 rows, IDs `1–12`.
- Evidence arrays were exactly equal across interruption, continuation execution, and duplicate resume.
- Frozen C1 anchor ID: `6685a9ddd6b9cd3c0a45c56f5363f560096c63b42fa012b8d09f675feb35be78`.
- Frozen C1 fingerprint: `94c475062178ceeecd40b225c03f58ed4b324fc32268af905c23ec66a84a01f5`.
- Market calculation context ID: `1`.
- PIT cutoff: `2026-10-14T10:39:01.767625Z`.
- Input session: `2026-10-13`.
- Scope ID: `2b15b4d0c5ce7871fc62bacdd06acc11e3537a42a8d256ee8fb27d85c2b5f95a`.
- Refresh-cycle ID: `7409a89e13593925c9e728b4df09199c0cb23af478fa78d72cdec0b928f8130c`.
- Acquisition-plan ID: `787e9fcb634372489807b89e53331ef982ed377f2ac8b9983d1db83cb0619972`.
- Root and continuation retained identical C1, calculation-context, PIT, session, scope, refresh-cycle, and acquisition-plan identities.
- Duplicate authoritative calculations: `0`.
- Setup Lifecycle rows: `0`; transitions: `0`; alerts: `0`; Winner rows: `0`.

## G. Tests

Supporting unit command:

```text
.\.venv\Scripts\python.exe -m pytest 'tests/test_pipeline_service.py::test_resume_pipeline_queues_checkpoint_job_without_rewriting_completed_steps' 'tests/test_pipeline_execution_authority.py' -q --tb=short
```

- Result: `3 passed`, 1 warning in `0.70 s`.
- Proved resume lineage recording and preserved pipeline-level authority/revocation behavior.

Focused disposable-PostgreSQL authority regression:

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_readiness_execution_authority_postgresql.py::test_operator_resume_fences_only_superseded_root_authority' -q --tb=short
```

- Result: `1 passed`, 2 warnings in `8.75 s`.
- Proved an expired superseded root is excluded while legitimate CERI work and a non-superseded retry remain eligible.

Single final integration certification:

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_phase4_configuration_certification_postgresql.py::test_public_resume_retains_c1_and_duplicate_completed_work_stays_behind_ceri_barrier' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_5E_CERTIFICATION.xml
```

- Executions: exactly `1`.
- Result: `1 passed`, 2 warnings in `75.41 s`.
- JUnit suite runtime: `75.376 s`.
- JUnit: `docs/remediation/readiness-phase2/TASK_2_9_5E_CERTIFICATION.xml`.

## H. Source pins

Reviewed production changes were limited to:

- `job_execution_authority_predicate()` — operational claim/recovery authority filter; no writer or financial mutation boundary added;
- `resume_pipeline()` — existing `SERVICE_PUBLIC`, `CANONICAL_CALCULATION`, `PIPELINE` entrypoint in operation family `WF_PIPELINE_CONTROL`; its existing semantic and mutation-authority classification was preserved.

Initial validation reported only:

```json
{
  "stale": [
    "app/services/pipeline_execution_authority.py",
    "app/services/pipeline_service.py"
  ]
}
```

After review, the official current-overlay generator refreshed only current source hashes. Immutable historical review artifacts were not changed. Final validation:

```json
{"stale": []}
```

## I. Safety

- All PostgreSQL tests used disposable loopback databases created by the test fixture.
- Synthetic IB readiness and the offline CERI scheduler prevented live IBKR, SEC, or EODHD calls.
- The normal database and historical Runs 22–33 were not accessed or mutated.
- Unit, supporting, and final certification processes had finite deadlines; the final outer deadline was 300 seconds.
- No matching pytest, probe, or task worker process remained afterward.
- Credential scanning found no unredacted PostgreSQL authority, password, API key, secret, or execution token in JUnit, retained logs, or final snapshots. Database authority remained redacted as `<restricted:userinfo>`.
- No full pytest, browser/E2E suite, normal pipeline, gateway launch, reset, stash, merge, push, or deployment occurred.
- All pre-existing working-tree changes were preserved.

## J. Remaining blockers

There is no remaining Task 2.9.5 blocker. UTC equivalence, CERI dependency admission, waiting-state reconciliation, superseded-root fencing, duplicate-resume idempotency, immutable financial evidence, and downstream suppression all passed together.

Unrelated Phase 2.9 findings, if any, were not investigated or changed by this task.

## K. Next recommended single task

Proceed only under a separate authorization to the explicitly specified Task 2.9.6 certification/remediation. No Task 2.9.6 work was started here.

## Files changed

- `app/services/pipeline_execution_authority.py` — exclude only roots durably superseded by an operator-resume continuation.
- `app/services/pipeline_service.py` — lock resume admission and persist exact predecessor/continuation lineage.
- `tests/test_pipeline_service.py` — verify resume lineage fields.
- `tests/integration/test_readiness_execution_authority_postgresql.py` — verify superseded root, CERI work, and non-superseded retry cases.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json` — official current reviewed hashes.
- `docs/remediation/readiness-phase2/TASK_2_9_5E_CERTIFICATION.xml` — passing final JUnit.
- `.qa_work/task_2_9_5e_supporting.stdout.log` and `.stderr.log` — focused authority regression output.
- `.qa_work/task_2_9_5e_pytest.stdout.log` and `.stderr.log` — final certification output.
- `.qa_work/t13e-*-last.json` — retained final phase state and integrity snapshots.
- `docs/remediation/readiness-phase2/TASK_2_9_5E_ROOT_AUTHORITY_FENCING.md` — this report.
