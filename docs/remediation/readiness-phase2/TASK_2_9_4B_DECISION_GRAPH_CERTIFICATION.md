# Task 2.9.4b — Decision-Graph Certification

## A. Verdict

**PASS.** The exact decision-graph restart test passes through its final frozen-history and replay assertions. Terminal Winner authority remains fail-closed, no duplicate Winner evidence is published, the worker-generation correction is certified, and Phase 2.9 source pins report `{"stale": []}`.

## B. Duration

- Start: `2026-10-14T11:22:56.9552499+02:00`
- Verification finish: `2026-10-14T11:29:19.9635724+02:00`
- Elapsed: `00:06:23.0083225`
- Budget: 20 minutes

## C. Root cause and fixture correction

The positive `queue-downstream` fixture duplicated `WINNER_PREDICTION_CAPTURE` after the real pipeline had already completed and published its Winner snapshot. Because the candidate retained `pipeline_run_id`, the production claim predicate correctly excluded it once the pipeline status was `COMPLETED`; the old fixture incorrectly expected that deliberately unauthorized row to complete.

The positive path now enqueues only the authorized lifecycle-maintenance and alert-rebuild jobs and strictly requires both to move from `QUEUED` to `COMPLETED`. A separate `post-terminal-winner-fence` probe creates the Winner candidate inside a transaction, establishes that it is `QUEUED` but absent from `claimable_pipeline_job_ids`, confirms the Winner count remains one, then rolls back. A new session proves the candidate was not persisted and the snapshot count is still one. This tests terminal authority without weakening it or leaving permanently unclaimable work in the queue.

Retained negative proof:

- Pipeline: `COMPLETED`; retained audit authority state: `ACTIVE`; effective execution authorization: `false`.
- Candidate id 8: claimable `false`; persisted after rollback `false`.
- Winner snapshots before/during/after: `1 / 1 / 1`.

## D. Worker correction verification

`run_worker_once()` now retains `worker_registration.instance_id` immediately after `register_worker()`. That same canonical identity flows to the worker-control heartbeat, `claim_next_job()`, detached progress, detached job-control heartbeat, infrastructure health, and all subsequent lease-fenced operations. Explicit caller identities are preserved because registration returns that valid identity; omitted identities adopt the generated durable generation.

The focused unit test verifies a generated identity reaches both the control heartbeat and claim call. The integration test's lease ledger records the abandoned claim, recovery event, and second claim by `t13e-native-<pid>`; attempt 2 completed. Existing registry ownership checks continue to fence mismatched generations.

## E. Frozen-history certification

| Invariant | Result |
|---|---|
| Root recovery | PASS — abandoned root moved from `RUNNING` to `COMPLETED`; attempt count 2 |
| New execution token | PASS — recovery and second claim were recorded as distinct lease events; the completed row cleared its live token |
| Original configuration anchor | PASS — anchor id `bfc13cc7a2944c90a50f8e2623aecd359d924da909b2abc00cb75085f08268b1`, fingerprint `9967113278629e3e9709698bb6b761aebf27ad2f201a9e163a828bcf37e22851` |
| C1 versus invalid C2 isolation | PASS — C1 executed and was read in fresh processes while every current YAML was invalid; later C2 identities differed by namespace |
| Calculation/source identity | PASS — retained C1 evidence and records were equal across fresh history retrieval and remained the exact prefix after C2 execution |
| PIT cutoff | PASS — the admitted C1 cutoff/session remained frozen (`2026-10-14T09:26:01.014039Z`, session `2026-10-13`) |
| Final history/replay assertions | PASS — replay completed as `DRY_RUN_REPLAY`, produced snapshots, used no `ORIGINAL_CONTEXT`, and did not mutate retained evidence families |
| Duplicate Winner evidence | PASS — exactly one Winner snapshot existed at the terminal-fence checkpoint; C2 later added only its legitimate separate-run result |
| Post-terminal job authorization | PASS — candidate unclaimable, unpublished, rolled back, and absent in a new session |
| Provider isolation | PASS — synthetic IB health and the offline dependency boundary were installed before worker execution; no live IB/SEC/EODHD connection occurred |

## F. Source pins

Affected reviewed addresses:

- `app/services/background_worker.py:run_worker_once` — existing T14D `REVIEWED_NATIVE_TRANSPORT`; no operation-family or authority assignment changed.
- `scripts/qa/t13e_deployed_probe.py:main` — reviewed certification fixture surface; no production writer family changed.

Registry changes: none. The immutable historical T14D registry/pin artifact was not modified. Only current source hashes in the Phase 2.9 overlay were regenerated with:

```text
.\.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins --write
```

The generator changed exactly the two paths reported stale before regeneration. Worker hash changed from `25c335ad196d8f4979e4333da5a7a144ef2577982c40d5b490ba236d85d671f1` to `34a8642fdd10bb9685003189d38f27e6d0a238ae0c965cfe5f69858fa82261ca`; the probe's current hash is `0a2f0d50f06becaa3f0ed68ebf8475d24f29f397f8e4f792bd606295a643e2b8`. Final overlay SHA-256: `C850F87B0FB7BFE1428D33E69D8DF58512ACB81934191940A0C4CE1360C6D644`.

Final validator result:

```json
{"stale": []}
```

## G. Tests

| Command | Runtime | Result | Artifact |
|---|---:|---|---|
| `.\.venv\Scripts\python.exe -m pytest 'tests/test_background_worker.py::test_worker_once_adopts_instance_identity_created_by_registration' -q --tb=short` | 0.30 s | PASS (1 passed) | console |
| `.\.venv\Scripts\python.exe -m pytest 'tests/integration/test_phase4_configuration_certification_postgresql.py::test_public_upload_enqueue_process_restart_native_worker_and_frozen_history[decision-graph]' -q --tb=short --junitxml=docs/remediation/readiness-phase2/TASK_2_9_4B_CERTIFICATION.xml` | 165.566 s suite / 165.266 s case | PASS (1 passed) | `TASK_2_9_4B_CERTIFICATION.xml` |

The unit test's first scaffolding attempt failed in 0.86 s because the fake DB did not stub stale-job recovery; the missing test-only stub was added before the passing run. No integration rerun occurred.

The JUnit artifact contains no PostgreSQL URL, password assignment, or secret sentinel. The test used only its generated disposable PostgreSQL database and bounded 180/360-second subprocess limits. No test-owned probe process remained afterward.

## H. Files changed

- `app/services/background_worker.py` — prior Task 2.9.4 canonical instance-identity correction, preserved and certified.
- `scripts/qa/t13e_deployed_probe.py` — separate rollback-only terminal Winner fence proof; include job type in retained diagnostics.
- `tests/integration/test_phase4_configuration_certification_postgresql.py` — strict authorized-job assertions, exact one-snapshot assertions, and explicit terminal-fence lifecycle proof. The unrelated pre-existing handler-count edit was preserved.
- `tests/test_background_worker.py` — registration stub now models the return contract; focused generated-instance propagation test.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json` — officially regenerated hashes for the two reviewed changed paths.
- `docs/remediation/readiness-phase2/TASK_2_9_4B_CERTIFICATION.xml` — credential-free focused JUnit.
- `docs/remediation/readiness-phase2/TASK_2_9_4B_DECISION_GRAPH_CERTIFICATION.md` — this report.

## I. Remaining blockers

No Task 2.9.4b blocker remains. This does not certify other Phase 4 tests or the broader Phase 2.9 release. The separately reported resume/CERI barrier, Fundamental retry, heartbeat, Winner publication, and deterministic-recovery failures remain unresolved.

## J. Recommended next single task

Address only `test_public_resume_retains_c1_and_duplicate_completed_work_stays_behind_ceri_barrier`, using its retained traceback and the same provider-isolation preflight. Do not start the wider worker/recovery suite.
