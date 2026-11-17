# Evidence navigation

`IMPLEMENTATION_REPORT.md` records the final decision and its limits.
`FILE_INVENTORY.md` explains each changed file; `file-inventory.json` records
exact source hashes and the patch hash. The delivery archive contains source,
the complete uncommitted patch, and this evidence directory.

| Requirement | Primary evidence |
|---|---|
| Canonical identity, ownership, containment and current population | `IDENTITY_AND_OWNERSHIP.md` |
| Migration preflight, postflight and rollback | `MIGRATION_AND_RECOVERY.md`, `preflight.sql`, `postflight.sql`, `migration-preflight.json`, `migration-postflight.json` |
| Authoritative forensic input | `authoritative-input/`; unchanged supplied report, SQL, evidence and patches |
| Genuine historical fixture and constraints | `disposable-environment.json`, `authorized-fixture-copy.json`, `authorized-closure-tables.json`, `foreign-keys-restore.log` |
| Exact approved frozen scope children | `frozen-scope-prerequisites.json`, `authorized-scope-members-copy.json` |
| All old score/evidence/disposition fingerprints | `historical-before.json`, `historical-after.json`; 5,496 sorted rows |
| Referenced main-context technical prerequisites | `handoff-technical-prerequisites.json`, `authorized-handoff-prerequisites-copy.json`, `handoff-prerequisites-copy.log` |
| Actual context-116 capture, tail, handoff and retries | `context-116-certification.json`, `new-context-116-scores.json`, `native-certification-resumed.log`, `native-certification-handoff-resumed.log`, `native-certification-final-checks.log` |
| Actual parent and dependency continuation | `pipeline-66-continuation.json`, `pipeline-66-continuation.log`, `pipeline-66-progress.jsonl` (read-only observations) |
| Original job, context, manifest-41 membership and new ownership lineage | `migration-postflight.json` |
| Production stayed unchanged | `production-read-only-final-check.json`; original 611 fingerprints/dispositions and terminal owner/job/context/manifest hashes |
| Final affected regression suite | `regression-summary.json`, `affected-regressions-final.xml`, `affected-regressions-current-source.log` |
| Focused checks and baseline fixture comparisons | `focused-test-summary.json`, `focused-test-results/`, `baseline-*.log` |
| Patch applicability | `patch-apply-check.txt` |
| Combined final gate review | `final-certification-review.json`; terminal parent, dependency, postflight, historical comparison and unchanged regression sources |
| Conditional normal-runtime recovery | `MIGRATION_AND_RECOVERY.md`; recommendation only, never executed here |

The `attempts/` directory deliberately retains earlier unsuccessful checkpoints.
The first capture attempt exhausted its lease under host memory pressure and
committed no scores. The second exposed the dedicated-context decision-writer
defect; all 447 tentative scores rolled back before that defect was corrected
and tested. The third capture committed 447 scores, then its tail encountered
the incomplete QA scope fixture and stale sequence counters. Its failed
terminalization transaction rolled back, so no exclusion disposition was
applied to the new scores. The user authorized the exact missing 447 genuine
scope members. They were restored without changing any existing financial row;
the abandoned child claim was deferred through the real control API and the
continuation resumed under a fresh claim.

The handoff then correctly rejected absent main-context technical prerequisites.
All 447 genuine technical rows and one missing source manifest were restored
under the existing technical/FK-ancestor authorization and the same 19-table
allowlist. Exact immutable Combined source pins identified all inputs, including
18 blocked-input rows with cleared compatibility IDs. Source and target full-row
hashes match. Handoff certification resumed without repeating capture or tail.

After the verified handoff and all three zero-insert retries committed, the
harness accessed expired ORM objects while preparing its final negative check.
That harness failure is preserved under `attempts/attempt-3-final-check-harness/`.
Fresh scalar IDs correct the bookkeeping. The finalizer rechecks the durable
job/manifest receipts, executes the actual incompatible-context query, and
compares every historical fingerprint in a read-only QA transaction. It does
not rerun or replace the successful financial services.

The scope helper's retained audit reports `copied: 0` for its successful retry:
the first invocation had already committed all 447 members before rejecting an
unrelated expired historical RUNNING job row. The initial zero-member state is
retained in `frozen-scope-prerequisites.json`, and the final full-row fingerprint
matches production exactly. Historical job 182 was retained unchanged.

Earlier root diagnostic logs, failure XML and environment-blocker JSON are
retained for audit. Their intermediate decisions do not replace the final
decision in the implementation report and completed certification artifacts.
No production recovery, deployment or pipeline 67 is authorized by these files.
