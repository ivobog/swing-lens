# Run 41 integration onto main

Run 41 failed because an idempotent recapture returned a new attempt ID while
reusing snapshots whose immutable creator remained capture 58. Evaluation
incorrectly treated the attempt ID as each reused snapshot's creator.

This change records exact capture membership independently of creator ownership,
validates immutable evidence and frozen context, preserves strict legacy checks,
and rejects invalid handoffs with a deterministic non-retryable error. Internal
recapture forwards progress and cancellation; failed publication rolls back before
separate audit bookkeeping. Exception logs retain bounded frames and chains.
Both root and CERI continuation routing are covered with the handoff flag enabled
and disabled.

The integration starts from `e69361f35ba9425c01d015c9cac80e91fd95c54a` and preserves
its Run 40 remediation. Unrelated local readiness, scoring, worker and performance
changes were excluded. The primary working tree was left untouched.

| Fresh verification on this main baseline | Result |
| --- | --- |
| Evaluator, capture, pipeline, jobs and logging regressions | 163 passed |
| Recovery outcome contract | 10 passed |
| Native PostgreSQL capture and recovery-review regressions | 9 passed; no skips |
| Repository-wide Ruff | Passed |
| Architecture registry | Passed; zero exceptions |
| Both-flag E2E cardinality matrix collection | Six cases collected |

[Verification record](verification.json), [regression JUnit](regressions.xml),
[native PostgreSQL JUnit](native-postgresql.xml), and
[outcome-contract JUnit](outcome-contract.xml) record the fresh checks. The initial
regression invocation skipped nine database cases due to default credentials;
all nine were subsequently executed successfully using configured credentials
and disposable databases. No production mutations were performed for publication.

The original [forensic report](../../forensics/run41-lifecycle-failure/REPORT.md)
and [implementation/recovery report](../run41-lifecycle/REPORT.md) are preserved
with their sealed evidence. They document the earlier exact-data clone, full
pipeline certification and successful live recovery. Live job 1417 completed all
13 steps with 88 canonical snapshots and zero lifecycle failures; its native
outcome retained the six pre-existing data exclusions and 82 Winner predictions.

Those earlier whole-pipeline and live checks certified the recorded candidate
`75bc04f32960497a4d823d01aa6d0e6a96aa4105`, which also contained a separate readiness
baseline. They do not establish whole-source equivalence with this integration;
the full pipeline was not rerun for this publication.

The read-only `run41_outcome_reference.py` decoder verifies the retained
`pipeline-outcome-v1` summary without introducing that other baseline's production
policy. The historical clone CLI refuses execution before any action unless its
certified readiness helper and acquisition-ledger schema exist. It is retained
for reproducibility, not as an instruction to repeat the completed live recovery.

Scoped Git attributes preserve original evidence bytes and line endings. Every
sealed hash was verified against the staged Git blobs. Credential URL checks found
no unredacted non-default credentials. The 2.23 GB database backup and local `.env`
are excluded from the commit.
