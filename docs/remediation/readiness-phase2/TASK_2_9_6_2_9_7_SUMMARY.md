# Tasks 2.9.6 and 2.9.7 — Combined Certification Summary

## A. Executive verdict

| Task | Root cause established | Fix implemented | Focused test | Source pins | Verdict |
|---|---|---|---|---|---|
| 2.9.6 Fundamental retry | Yes — job requeued while its pipeline remained terminal | Yes — atomic, unsuperseded same-root retry transition | 1 passed | `{"stale": []}` | CLOSED |
| 2.9.7 Worker heartbeat | Yes — stale implicit child-Session binding assumption | Yes — fixture uses the explicit domain fence and proves isolation | 1 passed | `{"stale": []}` | CLOSED |

`TASK 2.9.6 FUNDAMENTAL RETRY: CLOSED`

`TASK 2.9.7 WORKER HEARTBEAT FENCING: CLOSED`

## B. Timing

| Task | Start | Finish | Elapsed |
|---|---|---|---:|
| 2.9.6 | `2026-10-14T12:48:18.5139442+02:00` | `2026-10-14T12:55:12.4583814+02:00` | 6m 54s |
| 2.9.7 | `2026-10-14T12:56:15.5557174+02:00` | `2026-10-14T12:58:49.1822455+02:00` | 2m 34s |
| Total | `2026-10-14T12:48:18.5139442+02:00` | `2026-10-14T12:58:49.1822455+02:00` | 10m 31s |

Both tasks remained below their independent 20-minute limits and the combined work remained below 40 minutes.

## C. Task 2.9.6

The executor committed a terminal `FAILED` pipeline before propagating a transient acquisition exception. The worker then correctly returned the same root job to `QUEUED`, but queue authority excluded it because its pipeline was still terminal. The fix completes both halves of that retry state transition in the worker control transaction.

Changed production files:

- `app/services/background_job_service.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_state_machine.py`

The current root may reopen its pipeline from `FAILED` to `QUEUED` only under active authority and only if no operator-resume child supersedes it. The second claim obtains a new execution token and reaches attempt 2. The exact target proves the original C1 anchor is retained after current YAML files are made invalid, the same calculation-context payload/cutoff is reused, and the one Fundamental evidence projection is unchanged rather than duplicated.

Certification: **1 passed in 51.43s**.

JUnit: `docs/remediation/readiness-phase2/TASK_2_9_6_CERTIFICATION.xml`

Detailed report: `docs/remediation/readiness-phase2/TASK_2_9_6_FUNDAMENTAL_RETRY.md`

## D. Task 2.9.7

Production deliberately separates ownership validation from authority to commit. The fixture still assumed that calling `assert_current_execution_ownership()` implicitly made an arbitrary child Session authoritative. It did not, so the assertion correctly observed no fenced Session and the handler failed.

No production code changed. The target now explicitly binds the child through `fence_domain_commits()`. It demonstrates that:

- the child Session is the exact domain transaction owner;
- heartbeat writes stay within that transaction and do not commit staged business data;
- another Session cannot see the staged row before the child commit;
- a wrong execution token fails closed with `JobLeaseLost`;
- the fence is released when the transaction ends;
- the valid job completes and clears its execution token.

Certification: **1 passed in 8.39s**.

JUnit: `docs/remediation/readiness-phase2/TASK_2_9_7_CERTIFICATION.xml`

Detailed report: `docs/remediation/readiness-phase2/TASK_2_9_7_WORKER_HEARTBEAT_FENCING.md`

## E. Regression compatibility

| Contract | Result |
|---|---|
| Worker-generation identity from Task 2.9.4b | Production path unchanged; valid registered instance exercised by 2.9.7. Wrong-generation branch not separately rerun. |
| Superseded-root authority from Task 2.9.5e | Preserved by the existing claim predicate and the new locked operator-resume-child refusal. No separate 2.9.5e integration rerun. |
| Legitimate retry preservation | Directly PASS-certified by Task 2.9.6 real-worker attempt 2. |
| CERI dependency authority | Unchanged and not directly exercised by these targets. |
| Financial transaction isolation | Directly PASS-certified by the 2.9.7 observer and explicit child commit boundary. |

Task 2.9.7 changed only its test fixture, so it did not invalidate Task 2.9.6’s production-source certification. No cross-task recertification is required.

## F. Source pins

Task 2.9.6 initially made these reviewed files stale:

- `app/services/background_job_service.py`
- `app/services/pipeline_service.py`
- `app/services/pipeline_state_machine.py`

They were reviewed as the registered root-job retry/failure transports and `WF_PIPELINE_STATE_TRANSITION` family, then regenerated through the official `--write` command. Historical T14D artifacts were preserved. Task 2.9.7 changed no reviewed source. Final validation:

```json
{"stale": []}
```

## G. Safety

- Both targets used disposable PostgreSQL only.
- Task 2.9.6 installed offline/deterministic provider boundaries; current C2 changes occurred only in its temporary copied tree.
- Task 2.9.7 constructed no provider and disposed its derivative engine in `finally`.
- All subprocess and SQL/lock deadlines were finite.
- No normal SwingLens pipeline, live IB Gateway/TWS, SEC, EODHD, deployment, push, merge, reset, stash, or broad pytest suite was used.
- Normal application data and historical Runs 22–33 were not accessed or mutated.
- No credentials were written to the reports or JUnit artifacts.
- Existing unrelated working-tree changes were preserved.

## H. Remaining blockers

- Task 2.9.6: none.
- Task 2.9.7: none.
- Broader Phase 2.9 release: other retained failures remain outside these two closures.

## I. Recommended next task

The next retained unresolved regression in release-report order is `test_model_retirement_api_delivers_exact_new_governance_and_maps_rejections` (the likely Task 2.9.8 work unit). It was not started.
