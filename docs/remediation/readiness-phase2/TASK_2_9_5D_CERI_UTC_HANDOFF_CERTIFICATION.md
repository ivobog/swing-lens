# Task 2.9.5d — CERI UTC Timestamp Normalization and Final Handoff Certification

## A. Verdict

**FAIL.** The UTC timestamp correction passed and the supported CERI dependency handoff completed correctly, but the one permitted focused certification failed at the duplicate-resume authority check. Original root job `1` remained execution-authority eligible both before and after the rejected duplicate request (`[1]`, expected `[]`). No second integration run or additional repair was attempted.

`TASK 2.9.5 CERI RESUME BARRIER: OPEN`

## B. Duration

- Start: `2026-10-14T12:24:44.0195401+02:00`
- Evidence collection finish: `2026-10-14T12:28:53.3771031+02:00`
- Elapsed: `00:04:09.3575630`
- Budget: 15 minutes; pytest outer deadline: 600 seconds
- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`

## C. Root cause

Task 2.9.5c failed before dependency admission because the fixture compared the serialized cutoff `2026-10-14T10:14:16.696836Z` to the equivalent in-memory rendering `2026-10-14T10:14:16.696836+00:00` as raw strings.

The fixture-only correction now:

- preserves an existing `datetime` value;
- normalizes a terminal `Z` to `+00:00` only for parsing;
- rejects missing, invalid, or timezone-naive values;
- converts both operands to UTC and compares the exact instant;
- applies no tolerance, truncation, or persisted-representation change.

The corrected assertion passed. `prepare_ceri_workflow_dependency()` then executed twice through the supported production service, its idempotency assertions passed, and exactly one dependency persisted.

The new failure is not another timestamp or fixture-serialization defect. It is a production execution-authority blocker: the original queued root remains admitted by `job_execution_authority_predicate()` while the pipeline has `ACTIVE` execution authority and status `WAITING_FOR_CERI_COMPLETION`. The diagnostic returned root ID `1` before and after duplicate resume. The real queue claim also requires `run_after <= now`; the fixture has deferred this root by one day, so it is not immediately runnable, but that temporal delay is not a durable authority fence and the root becomes runnable when due unless authority changes first.

## D. Dependency handoff

| Invariant | Observed |
|---|---|
| Frozen cutoff equality | PASS — exact timezone-aware UTC comparison succeeded for `2026-10-14T10:25:40.678238Z` |
| Active CERI dependency count | `1` |
| Dependency type and status | `CERI_WORKFLOW`, `RUNNING`, active/incomplete |
| Dependency pipeline/root ownership | Pipeline `1`; root is executing continuation job `6`; worker instance `b82aafa42bc747eba6f423894f8b3262` |
| Idempotent admission | PASS — two preparation calls retained dependency ID `1`; no duplicate row |
| Continuation result | Job `6` `COMPLETED`, attempt `1`; result `WAITING_FOR_CERI_COMPLETION` |
| Pipeline state after reconciliation | `WAITING_FOR_CERI_COMPLETION` at `CERI_PROVIDER_INGEST`; no pipeline error |
| Duplicate resume HTTP status | `409`; `Only BLOCKED, FAILED, or PARTIAL pipelines can be resumed.` |
| Additional persisted jobs | None; IDs stayed `1–6` |
| Setup/Winner publication | Lifecycle `0`, transitions `0`, alerts `0`, Winner `0` |

Additional retained dependency proof:

- dependency key: `pipeline:1:ceri-workflow:3322d092bae9b1a0c75b0b0a5f59d3f6b00d7dab649719df748b9187310d5d8e`;
- continuation identity: `3322d092bae9b1a0c75b0b0a5f59d3f6b00d7dab649719df748b9187310d5d8e`;
- continuation step: `FREEZING_DECISION_HANDOFF_MANIFEST`;
- child and continuation job IDs: both null;
- original root job `1`: `QUEUED`, attempt `1`;
- legitimate continuation job `6`: distinct, `COMPLETED`, attempt `1`;
- `PIPELINE_ROOT_TERMINAL_WITH_ACTIVE_PIPELINE` did not recur.

Duplicate-resume diagnostics were stable before and after the HTTP 409:

```json
{
  "job_ids_before": [1, 2, 3, 4, 5, 6],
  "job_ids_after": [1, 2, 3, 4, 5, 6],
  "claimable_job_ids_before": [1],
  "claimable_job_ids_after": [1],
  "pipeline_status_before": "WAITING_FOR_CERI_COMPLETION",
  "pipeline_status_after": "WAITING_FOR_CERI_COMPLETION"
}
```

## E. Financial evidence integrity

- Original evidence at interruption: 12 rows, IDs `1–12`.
- Final evidence after continuation and duplicate resume: 12 rows, IDs `1–12`.
- The serialized evidence arrays were exactly equal from interruption through execution and duplicate resume.
- Frozen C1 anchor ID: `6685a9ddd6b9cd3c0a45c56f5363f560096c63b42fa012b8d09f675feb35be78`.
- Frozen C1 fingerprint: `94c475062178ceeecd40b225c03f58ed4b324fc32268af905c23ec66a84a01f5`.
- Market calculation context ID: `1`.
- PIT cutoff: `2026-10-14T10:25:40.678238Z`.
- Input session: `2026-10-13`.
- Scope ID: `0c2fa61a13d37eca1f8b62b39e5bc6d0686532e4ab514b7e997188fefd32f346`.
- Refresh cycle ID: `9b4619004f43cb3a37d0b25ada665e3ed8c4ea504d4b54c6c889b70b055c68a9`.
- Acquisition plan ID: `6624672a0f524e1f22603b9606f5f10db8774e4a1015fce3f7b0e2a8bccc3896`.
- Root and continuation retained all of the same C1 anchor, context, cutoff, session, scope, refresh-cycle, and acquisition-plan identities.
- No duplicate authoritative evidence and no premature Setup Lifecycle or Winner output appeared.

## F. Files changed

- `scripts/qa/t13e_deployed_probe.py` — replaced only the raw frozen-cutoff string assertion with strict timezone-aware instant parsing/comparison; retained all Task 2.9.5c dependency assertions.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json` — refreshed the reviewed probe hash through the official current-overlay generator; no historical registry changed.
- `docs/remediation/readiness-phase2/TASK_2_9_5D_CERTIFICATION.xml` — retained the failed one-run JUnit result.
- `.qa_work/task_2_9_5d_pytest.stdout.log` and `.qa_work/task_2_9_5d_pytest.stderr.log` — retained bounded process output.
- `.qa_work/t13e-enqueue-last.json`, `.qa_work/t13e-interrupt-last.json`, `.qa_work/t13e-resume-last.json`, `.qa_work/t13e-execute-last.json`, and `.qa_work/t13e-duplicate-resume-last.json` — retained phase state and integrity evidence from the one certification.
- `docs/remediation/readiness-phase2/TASK_2_9_5D_CERI_UTC_HANDOFF_CERTIFICATION.md` — this report.

No production service or integration-test source was edited. Existing dirty-worktree content from earlier Phase 2.9 tasks was preserved.

## G. Focused certification

```text
.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_phase4_configuration_certification_postgresql.py::test_public_resume_retains_c1_and_duplicate_completed_work_stays_behind_ceri_barrier' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_5D_CERTIFICATION.xml
```

- Executions: exactly `1`
- Result: `1 failed`, 2 warnings
- JUnit suite runtime: `72.975 s` (console: `73.04 s`)
- Exact failure: `tests/integration/test_phase4_configuration_certification_postgresql.py:419`
- Assertion: `proof["claimable_job_ids_before"] == []`
- Observed: `[1]`
- JUnit: `docs/remediation/readiness-phase2/TASK_2_9_5D_CERTIFICATION.xml`
- No second integration test was run.

## H. Source pins

The only expected reviewed-source change was the probe. Its current overlay was regenerated with the official generator and the final validator result was:

```json
{"stale": []}
```

## I. Safety

- The certification created and used disposable PostgreSQL database `swinglens_pytest_2e2f4c61b9ee` on loopback only.
- Synthetic IB readiness and the offline CERI scheduler kept IBKR, SEC, and EODHD provider boundaries offline.
- The normal database and historical Runs 22–33 were not accessed or mutated.
- Test-owned subprocess deadlines remained finite; the outer pytest deadline was 600 seconds.
- No matching pytest/probe Python process remained after the run.
- Credential scanning found no unredacted PostgreSQL authority, password, API-key, secret, or token in the retained JUnit, logs, execute snapshot, or duplicate-resume snapshot. Logged database authority remained `<restricted:userinfo>`.
- No reset, stash, merge, push, deployment, broad suite, E2E suite, or browser suite was performed.

## J. Remaining blockers

This task's timestamp-normalization defect is closed. The CERI handoff, waiting-state reconciliation, duplicate HTTP response, evidence immutability, and downstream suppression all passed.

The remaining Task 2.9.5 blocker is production-related and distinct from unrelated Phase 2.9 failures: an obsolete queued root retains pipeline execution authority while its legitimate continuation has completed into an active CERI dependency barrier. A future `run_after` prevents an immediate worker claim but does not revoke the old root. Resolving that authority/fencing policy is outside Task 2.9.5d's fixture-only scope and was not attempted.

No subsequent Phase 2.9 task was started.
