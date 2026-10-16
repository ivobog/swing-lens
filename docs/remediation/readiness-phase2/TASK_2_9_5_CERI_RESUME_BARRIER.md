# Task 2.9.5 — CERI Resume Barrier and Frozen C1 Continuation

## A. Verdict

**PARTIAL.** The retained failure was reproduced and its stale fixture assumption was established: the application creates one legitimate resume continuation, completes that continuation with `WAITING_FOR_CERI_COMPLETION`, and correctly refuses to reclaim the original root while the pipeline is waiting. The narrow fixture/probe correction is implemented, but the single permitted post-fix run stopped on a newly added initial-state assertion (`PENDING`, not `QUEUED`) before exercising the corrected duplicate-resume proof. That observed assertion is corrected in the worktree, but it was not rerun.

No production source was changed. This report does not certify broader Phase 2.9 release readiness.

## B. Duration

- Start: `2026-10-14T11:35:20.6958425+02:00`
- Finish: `2026-10-14T11:46:56.0664157+02:00`
- Elapsed: `00:11:35.3705732`
- Budget: 20 minutes

## C. Root cause

The original fixture conflated two intentionally distinct durable jobs. Root job `1` failed during provider scheduling, remained `QUEUED` for retry with attempt count `1`, and left pipeline `1` resumable. The public resume created legitimate `FULL_PIPELINE` continuation job `6` at `CERI_PROVIDER_INGEST`, preserving the original C1 anchor, calculation context, cutoff, scope, refresh cycle, and acquisition plan. Job `6` ran once, completed its worker obligation, and returned `WAITING_FOR_CERI_COMPLETION`; the pipeline remained behind the CERI dependency barrier.

The stale `retry-ready` sequence then made root job `1` temporally ready and required another worker claim. Production execution authority correctly excluded it because its pipeline was `WAITING_FOR_CERI_COMPLETION`, so `run_worker_once()` returned no work and the probe failed at `assert worked`. The fixture incorrectly expected root job `1` to become completed with attempt count at least two. This was not lost C1 state, a wrong resume step, duplicate financial publication, or a production barrier defect.

The correction replaces that invalid worker claim with a repeated public resume request. The probe records the 409 response, exact before/after job IDs, pipeline state, and the authority-filtered claimable job IDs. Strict evidence, identity, retry-count, and negative Setup/Winner assertions remain.

## D. State transitions

| Checkpoint | Root status | CERI barrier | Config anchor | Downstream work |
|---|---|---|---|---|
| Initial enqueue | Job `1`: `QUEUED`; pipeline `1`: `PENDING` | Not entered | anchor `6685a9ddd6b9cd3c0a45c56f5363f560096c63b42fa012b8d09f675feb35be78`; fingerprint `94c475062178ceeecd40b225c03f58ed4b324fc32268af905c23ec66a84a01f5` | Setup `0`; lifecycle `0`; Winner `0` |
| After interruption | Job `1`: `QUEUED`, attempt `1`, error `T13E transient acquisition scheduling boundary`; pipeline failed at `CERI_PROVIDER_INGEST` | Provider scheduling incomplete | Same anchor/fingerprint | Setup/lifecycle/alerts/Winner `0` |
| Resume accepted | Root remains `QUEUED`; continuation job `6`: `QUEUED`; pipeline re-queued at `CERI_PROVIDER_INGEST` | Continuation admitted from the failed provider step | Job `6` carries the exact original anchor | Setup/lifecycle/alerts/Winner `0` |
| First resumed execution | Root `QUEUED` attempt `1`; job `6` `COMPLETED` attempt `1` with result `WAITING_FOR_CERI_COMPLETION` | Pipeline `WAITING_FOR_CERI_COMPLETION`; provider step is `WAITING_DEPENDENCY` | Same anchor, context, cutoff, scope, refresh cycle, and plan | Setup/lifecycle/alerts/Winner `0` |
| Retry | Baseline fixture left root `QUEUED` and job `6` `COMPLETED`; worker returned no claimable work | Barrier remained `WAITING_FOR_CERI_COMPLETION` | Same anchor | No new job or evidence in the retained state; corrected 409/no-claim proof was not reached by the post-fix run |

## E. Evidence integrity

- Evidence immediately after interruption: 12 rows, IDs `1–12`; kinds `CERI`, `IBMI`, `FUNDAMENTAL`, `TECHNICAL`, `REGIME`, `COMBINED`, five `RANKING`, and `SECTOR`.
- Evidence after the legitimate resumed continuation: the same 12 IDs and payloads; duplicate authoritative records: `0`.
- C1 anchor ID and fingerprint on root and continuation are exactly equal.
- Market calculation context remains `1`; PIT cutoff remains `2026-10-14T09:36:39.109924Z`; input session remains `2026-10-13`.
- Scope, refresh-cycle, and acquisition-plan identities are exact between root and continuation.
- Setup Lifecycle evaluations `0`, lifecycle transitions `0`, alerts `0`, Winner snapshots `0` while the barrier is unsatisfied.
- The final duplicate-resume assertions are implemented but not certified because the permitted post-fix run stopped at the earlier initial-state assertion.

## F. Files changed

- `scripts/qa/t13e_deployed_probe.py` — expose initial/pipeline state, add a read-only duplicate-resume/claimability proof, and retain reviewed diagnostics.
- `tests/integration/test_phase4_configuration_certification_postgresql.py` — distinguish the legitimate continuation from the root, assert exact identities/evidence/attempts, and replace the invalid second worker claim with the public duplicate-resume barrier check.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json` — refreshed only the current review overlay with the official generator; historical pin artifacts were not modified.
- `TASK_2_9_5_BASELINE.xml` and `TASK_2_9_5_CERTIFICATION.xml` — retained baseline and post-fix JUnit evidence.
- This report.

## G. Tests

| Command | Runtime | Outcome | Artifact |
|---|---:|---|---|
| Exact target baseline pytest node | 82.118 s | FAIL — second `execute` found no claimable work, reproducing the retained regression | `TASK_2_9_5_BASELINE.xml` |
| `python -m py_compile` for the probe and target test | 2.5 s | PASS | Console |
| Exact target post-fix pytest node | 15.915 s | FAIL — initial pipeline state was actually `PENDING`, not the newly asserted `QUEUED` | `TASK_2_9_5_CERTIFICATION.xml` |
| `python -m scripts.qa.t14a_phase2_9_source_pins` before regeneration | 2.6 s | Expected stale: only `scripts/qa/t13e_deployed_probe.py` | Console |
| Official source-pin generator with `--write`, then validator | 3.2 s combined | PASS — `{"stale": []}` | Current overlay |

The initial-state assertion was corrected to `PENDING` after the post-fix result. No second integration process was started.

## H. Safety

- Branch `codex/ceri-authority-remediation`, HEAD `6656c55dd59c294c2ea471571863046e02bf7c35`; the pre-existing dirty worktree was preserved without reset, stash, or cleanup.
- Both runs used generated disposable PostgreSQL databases and the fixture's bounded subprocess timeouts. Normal SwingLens data and Runs 22–33 were not accessed or changed.
- Synthetic IB readiness and patched offline execution dependencies were installed before worker execution; no live IB, SEC, or EODHD provider was called.
- Test-owned worker/process identities were used. No matching task Python process remained after the run.
- Both JUnit files contain no unredacted PostgreSQL authority, password/key pattern, or secret sentinel. The baseline's two database URLs contain only `<restricted:userinfo>`.
- Source-pin validation is current: `{"stale": []}`. No production writer family or authority classification changed.

## I. Remaining blockers

Current task: the final corrected fixture has no green integration certification because the one allowed post-fix run was consumed by the `PENDING`/`QUEUED` diagnostic assertion. Therefore PASS cannot be claimed.

Unrelated and untouched: the Fundamental retry, heartbeat/fence, Winner publication/concurrency, deterministic recovery, browser/E2E, and broader Phase 2.9 failures.

## J. Next recommended single task

Run only this exact target node once in a new bounded certification task. If it passes, update this verdict using the existing evidence; do not begin another Phase 2.9 regression or broader suite.
