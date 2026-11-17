# CERI capture identity, ownership and handoff remediation

**Decision: PASS.** The genuine disposable context-116 capture, downstream
handoff, retries, actual parent/dependency completion, PostgreSQL migration
checks and all 900 affected regression tests passed. All 5,496 historical
score/evidence/disposition fingerprints remain unchanged. Earlier failed
checkpoints remain in the audit trail. Production remains unchanged.

## Scope and source

The implementation is isolated at
`C:/Users/Ivica/Documents/SwingLens/.worktrees/ceri-context-remediation`, based on
release `6d6952feec93adb9dd62635e41d299e3749b4819`. Its ancestor
`7e113f72f4c980a252b23658b3035969855cbb88` remains intact. The authoritative input
is the supplied `SwingLens_Upload61_62_Regression_Forensics.zip`, SHA256
`90f3d99ba925f671598979c5c611762b90cfcb57cf4ff30d465721da130682c5`.
Its original report, evidence, SQL, replay and repair patches are retained in
`authoritative-input/`. The completed forensic findings were used directly.

No production migration, deployment, pipeline recovery, pipeline 67, commit,
push or merge was performed. The primary dirty checkout was not edited.
Production connections for prerequisite copying were read-only.

## Coherent implementation

The storage key and retry query now identify native scores by upload, company
and frozen context. The writer checks the remaining frozen identity dimensions:
configuration, algorithm version, ticker/company, cutoff, completed session,
calendar and context fingerprint. Incompatible same-context candidates fail
closed. Other contexts remain independent generations and historical comparison
continues through its separate selector.

Migration 0096 atomically replaces the broad four-column constraint with a
native context index and a legacy NULL-context index. PostgreSQL NULL semantics
are explicit; replay uniqueness stays independent. The migration never edits
historical financial rows. See `IDENTITY_AND_OWNERSHIP.md` for the canonical
contract and `MIGRATION_AND_RECOVERY.md` for preflight, lock, rollback and
conditional normal-runtime procedures.

The shared ownership resolver requires one actual context/pipeline binding.
Dedicated CERI contexts use the existing reverse binding and keep their unique
pipeline FK NULL. Capture verifies the job's owner, workflow, frozen anchor and
cutoff, then embeds the verified owner in immutable evidence. Eligibility and
containment use that binding, so a failed sibling cannot claim another context.
Unresolved legacy ownership stays immutable and is explicitly excluded through
an unattributed snapshot incident when appropriate.

The central persisted-context assertion distinguishes MAIN and CERI roles.
The CERI core writer and native score-change writer use the CERI role, which
verifies the dedicated reverse binding and every frozen temporal field. Other
decision writers retain their main-context checks. A real sealed-comparator
integration test covers the corrected score-change write and rejects main-role
and ambiguous-owner substitutions.

One shared current-population validator serves the change/alert tail, completion
barriers and handoff producer/verifier. It requires a nonempty exact ticker set,
one native score per expected company, exact receipt IDs, frozen identity,
immutable seals and point-in-time source references. Historical comparators
remain separate. Historical manifest verification uses its original frozen
membership, including manifest 41's 164 references.

## Disposable environment and genuine prerequisites

The retained historical database is `swinglens_qa_ceri_context_1794788997961`.
It initially retained the 611 authoritative score/evidence fingerprints but
lacked schema constraints, required parent rows and manifest 41. The user
explicitly authorized the bounded missing-row copy and then its referenced
technical-score foreign-key ancestor closure. Only genuine missing primary-key
rows were inserted. No existing target rows were updated or deleted by copying.
`authorized-fixture-copy.json` records successful batch hashes;
`authorized-closure-tables.json` records the allowed dependency graph.

The full genuine primary-key/index/trigger/foreign-key schema was restored and
validated before migration/capture. Earlier failed restores and rolled-back
copy attempts are retained as preparation diagnostics. Batch commits are
audited; the fixture was not accepted until the full constraint restore passed.

Preflight found PostgreSQL 18.3, 5,496 retained native scores in 47 contexts,
no duplicate native keys, and no pre-existing NULL-context or controlled replay
rows. Those two populations are covered separately by real PostgreSQL tests.
`historical-before.json` hashes every retained score row, evidence envelope and
disposition set. The original job 2546 and manifest 41 are hashed separately.

The certification harness loads job 2546's exact retained anchor and CERI
configuration, verifies genuine semantic and ACTIVE execution authority, and
operator-resumes only the disposable parent copy. It creates a new workflow,
dependency and actual worker claim. It does not manufacture revoked authority
or substitute local defaults. The original failed job remains unchanged.

| Frozen delivery | Retained value |
|---|---|
| Configuration anchor | `8e37564fc30dfa21e72a9bb31a68944894b399619e23ce8a74ceb461e5fad43e` |
| Anchor fingerprint | `326a130767119b99288c8897fd5bfcfc806ab778994bdca52fd9cad143395807` |
| Effective CERI configuration | `77c7ae22de5372b399c60fd407f3caee849a29cac00cb430b9ce0d8d8f892348` |
| Native CERI config hash | `28adaf159aa4a4af9a62827496436c81be7da239b58bade6b7272897e49e0d9a` |
| Calculation version | `ceri-1.3.0` |
| Context-116 cutoff | `2026-11-15T22:57:34.652245+00:00` |
| Completed session / calendar | `2026-11-13` / `swinglens-us-equities-v2` |

The third genuine capture committed 447 new context-116 snapshots using that
exact delivery. Its tail exposed a further QA-only omission: the retained scope
parent lacked its 447 child members. The user separately authorized only those
exact genuine rows. Source and target full-row fingerprints now match
(`4a7be91ac8c27e6c5a12e1ed9b57c9c1`). All 120 QA serial counters were checked
without lowering any. The abandoned change claim was deferred through the normal
control API and resumed under a fresh claim. The committed financial envelopes
were preserved. Earlier unsuccessful attempts and fixture-repair diagnostics
remain available; `EVIDENCE_INDEX.md` distinguishes them from final evidence.

The first real handoff correctly failed closed because the partial fixture also
lacked all 447 context-115 technical rows required by its existing Combined
results. Production read-only inspection confirmed the genuine rows. The
already authorized technical-score/FK-ancestor closure was completed from the
Combined envelopes' exact immutable technical evidence pins, within the same
19-table allowlist: 447 technical rows and one missing source manifest. All 447
full-row hashes match production. This includes 18 blocked-input rows whose
compatibility score IDs were cleared but whose immutable source graph retains
the actual input. No lineage or eligibility requirement was weakened.

## Native disposable certification

The actual service run retained job 2546's frozen configuration and produced:

| Gate | Actual result |
|---|---|
| Native capture job 2549 | 447 new snapshots, IDs 7383–7829, context 116, explicit pipeline-66 ownership |
| Change and alert continuation | Jobs 2550 and 2551 completed through actual handlers |
| Immutable reproduction | All 447 new score hashes reproduced |
| Handoff producer and independent verifier | Manifest 42 committed; exactly the 447 new IDs, no context-113 IDs |
| Manifest fingerprint | `2114eb20e962387a2a3ab9d9c48a696167aff1029a7bdf031979e6653a037185` |
| Same-context retry job 2553 | Zero inserts, 447 skips |
| Concurrent retries 2554 and 2555 | Both completed with zero inserts and 447 skips |
| Incompatible same-context configuration | Rejected with `CERI_SAME_CONTEXT_CONFIGURATION_MISMATCH` |
| Historical preservation | All 5,496 score/evidence/disposition fingerprints unchanged |
| Original job, context and manifest | Job 2546, context 116 and manifest 41 full-row fingerprints unchanged |

The harness's final bookkeeping initially accessed expired ORM objects after
all financial services and retries had committed. The corrected finalizer uses
fresh scalar IDs, rechecks the durable job/manifest receipts, and executes the
remaining negative query and complete historical comparison in a read-only QA
transaction. The original harness exception is retained. No financial service
result was fabricated or replaced. Application/migration/test sources still
match the 900-test run; this QA-only correction is separately recorded in the
regression provenance and was executed successfully.

The production read-only check confirms that its original 611 authoritative
score fingerprints and disposition sets, pipeline 66, job 2546, context 116 and
manifest 41 remain unchanged. Production still has zero context-116 scores and
remains at migration 0095.

## Verification and regression review

Focused checks cover native context independence, strict same-context identity,
actual PostgreSQL NULL/replay semantics and concurrent unique insertion,
transactional downgrade refusal, exact nonempty barrier membership, tail
receipts, failed and safe-PARTIAL sibling isolation, ambiguous legacy containment,
issuer succession, immutable evidence, contextual configuration and terminal
pipeline fences.

The initial affected run found 753 passing tests and 13 failures. Seven failures
unrelated to the new ownership fixtures were also reproduced on pristine
`6d6952f`. Fixtures now supply real owner/context and calendar inputs instead of
upload-history inference or an obsolete calendar. The issuer-conflict fixture
retains the fail-closed behavior of `7e113f7`; a separate distinct-issuer test
preserves per-CIK lifecycle coverage. The stale-lease integration exposed an
existing recovery transition omission: recovery may repeat QUEUED while
reclaiming a queued parent. Five terminal-state guards retain the existing
termination fences. No financial evidence or eligibility assertion was removed
to make tests pass.

The final current-source affected run passed **900 tests, with zero failures,
zero errors and zero skips**, in 3,400.66 seconds. It includes the dedicated
CERI context correction, shared-authority suites and T14C decision-writer
integration. Its 277 warnings are dependency, Alembic table-order and
pytest/JUnit compatibility warnings; they did not mask failures. The complete
JUnit is `affected-regressions-final.xml`; machine-readable counts, source
hashes and invocation provenance are in `regression-summary.json`. The earlier
799-test run is preserved under `attempts/regression-before-authority-fix/`.
Lint passes for every changed source and
test file. The full uncommitted patch applies cleanly to pristine `6d6952f`.
The exact file/change inventory is `FILE_INVENTORY.md` and `file-inventory.json`.
The affected suite covers CERI,
pipeline services/executor/recovery/dependencies, configuration delivery,
systemic immutability, change scoping, handoff, isolation and PIT integrations.
This is the full affected suite, not an assertion that every unrelated test in
the repository was run.

## Actual parent completion and final postflight

The actual admitted continuation job 2552 ended `COMPLETED`. Its disposable parent 66 ended `PARTIAL` with all 13 stages closed through normal handlers. The parent integrity outcome is `NO_INTEGRITY_HAZARD`. CERI dependency 90 is `COMPLETED`, bound to that continuation. The final certified population remains the exact 447 context-116 score IDs.

The parent's `SUCCEEDED_WITH_GAPS` outcome retains all 447 Combined rows, with 29 marked incomplete. Its coverage diagnostics list two IB failures, seven technical errors and one unavailable market-data ticker (`CLBK`). The outcome reports no integrity hazards. These coverage limits are retained in the certification evidence; ingestion and market-data changes are outside this remediation.

Final postflight rechecked all 5,496 historical fingerprints after the parent finished, preserved manifest 41's original 164 references, confirmed every new score's known owner/context lineage and the dedicated context's NULL pipeline FK, and tested actual transactional downgrade refusal. The 447 overlapping old-key groups correctly reject downgrade with SQLSTATE `23505`; both new indexes and migration 0096 remain intact. See `pipeline-66-continuation.json`, `migration-postflight.json` and `final-certification-review.json`.

## Recovery recommendation

Do not recover production as part of this implementation. After a PASS decision,
review and separately authorize publication, migration and normal-runtime
recovery using `MIGRATION_AND_RECOVERY.md`. Keep historical pipeline 66/job 2546
terminal and all old context-113 exclusions intact. An independently authorized
future execution needs its own frozen context and configuration receipts.
After overlapping generations exist, the old uniqueness contract cannot be
restored without losing history; downgrade must refuse transactionally.
