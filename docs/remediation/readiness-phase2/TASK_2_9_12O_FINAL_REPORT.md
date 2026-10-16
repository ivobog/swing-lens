# SwingLens Task 2.9.12o Final Report

## Acceptance status

| Gate | Status | Evidence |
|---|---|---|
| Systemic remediation | **FAIL** | Original anchor, CERI, handoff, and several transaction defects are corrected, but a newly localized Setup transaction-lifetime defect remains |
| Chromium-100 | **FAIL** | Latest native run: 1 failed in 1126.89 seconds; `POSTGRES_TRANSACTION_AGE_EXCEEDED` at 120.326891 seconds |
| Full Phase 2.9 release | **NOT RUN / BLOCKED** | The task authorizes the release suite only after Chromium-100 PASS |

No release certification is declared and no release JUnit was fabricated.

## 1. What exactly caused the original decision-anchor mismatch?

The Setup continuation loaded `RawCompanyRow` records in a bounded child SQLAlchemy Session, closed
that Session, and then reconstructed Setup decision evidence. `SetupLifecycleSnapshotBuilder`
accessed the lazy `RawCompanyRow.run` relationship to project `source_run_status`. Because the
relationship was not eager-loaded, all ten detached reads raised `DetachedInstanceError`.

The old consumer retained the per-ticker errors and continued with zero built decision manifests.
It compared that empty reconstruction to a valid ten-member stored v2 anchor and emitted the
misleading generic `DECISION_MANIFEST_MISMATCH`.

This was an asynchronous/session-bound consumer defect (cause class G), not an authorized or
unauthorized source mutation and not a defective anchor schema.

## 2. Which fields differed?

Exactly 20 canonical paths were missing:

- `$.immutable_source_lineage.ACMR`
- `$.immutable_source_lineage.AVT`
- `$.immutable_source_lineage.BHE`
- `$.immutable_source_lineage.BLLN`
- `$.immutable_source_lineage.DVN`
- `$.immutable_source_lineage.JNJ`
- `$.immutable_source_lineage.KLIC`
- `$.immutable_source_lineage.LSCC`
- `$.immutable_source_lineage.PDFS`
- `$.immutable_source_lineage.RDVT`
- `$.ticker_population[0]` through `$.ticker_population[9]`

Stored fingerprint:
`df40df7a64096ffe403a701bc0caba3975057aa3d391d9a10f835e2d65feec65`.
Reconstructed fingerprint:
`080dcd29dadcb1663e4de40d5c6220e1a6c0e350965b2fa52bfc230c24b2f69b`.
There were no extra paths or changed retained values. Exact safe digests and row origins are in
`TASK_2_9_12O_ANCHOR_FIELD_DIFF.json`.

## 3. Why did they differ?

The stored anchor contained the admitted ten-member population. The continuation's detached lazy
relationship failures prevented every per-ticker decision manifest from being rebuilt, so both the
reconstructed population and its per-ticker lineage map were empty.

## 4. Which producer or consumer violated the intended contract?

The producer, `build_run_start_anchor_manifest`, produced and persisted the correct v2 anchor. The
violating consumer was the Setup continuation path composed of `SetupLifecycleSourceLoader` and
`SetupLifecycleSnapshotBuilder`: the loader returned a graph that was not safe to consume after its
bounded Session closed, while the builder expected `RawCompanyRow.run` to remain available.

## 5. Why did earlier focused tests miss it?

They used bound ORM objects, mocks, or a Session that stayed open across construction. They did not
exercise the production sequence of a real child Session closing before the asynchronous builder
accessed `RawCompanyRow.run.status`. They also did not require per-ticker reconstruction errors to
surface before anchor equality.

## 6. Which other consumers were affected?

The versioned anchor is verified before pipeline enqueue, at decision-handoff freeze, and before
Setup mutation. Failures feed background-job classification, pipeline roll-up, retained failure
evidence, API/GUI status, and JUnit. Setup and Winner consume the same frozen plan/handoff lineage.
All were reviewed; the affected test selection includes real PostgreSQL Setup, Winner, CERI,
restart/continuation, execution-authority, and v1/v2 paths.

## 7. What changed?

- Eager-loaded `RawCompanyRow.run` in the Setup source loader.
- Added typed reconstruction failure with redacted/digest-only per-ticker diagnostics.
- Added exact canonical path/origin diagnostics to anchor mismatches.
- Preserved independent exact v1/v2 verification without schema changes or stored-row rewrites.
- Added durable plan/handoff verification certificates and final locked confirmation.
- Bounded expensive verification reads with five-member transaction checkpoints.
- Reconciled CERI certified/public snapshot counters on fresh and coalesced continuations.
- Distinguished committed/frozen handoff evidence from downstream consumer acceptance.
- Removed expired-ORM identity refreshes that could start a parent transaction after commit.
- Added execution-owned failure evidence export for pipeline-classified anchor failures.

No financial formula, source selector, PIT cutoff, C1 configuration, anchor field, canonical hash,
execution fence, cancellation rule, or 120-second threshold was weakened.

## 8. How were downstream dependencies verified?

| Evidence | Tests | Failures / errors / skips | Duration |
|---|---:|---:|---:|
| Admission certification | 1 | 0 / 0 / 0 | 265.36 s |
| Transaction certification | 2 | 0 / 0 / 0 | 118.75 s |
| Bounded verification unit | 4 | 0 / 0 / 0 | 0.98 s |
| Bounded verification PostgreSQL | 1 | 0 / 0 / 0 | 7.75 s |
| Setup ownership unit | 3 | 0 / 0 / 0 | 1.15 s |
| Setup ownership PostgreSQL | 2 | 0 / 0 / 0 | 28.21 s |
| Verification batch/checkpoint | 5 | 0 / 0 / 0 | 9.27 s |
| Cross-stage contract selection | 163 | 0 / 0 / 0 | 461.17 s |
| Chromium-10 sentinel | 1 | 0 / 0 / 0 | 279.17 s |
| Final Chromium-100 | 1 | **1 / 0 / 0** | 1126.86 s JUnit / 1126.89 s pytest |

The cross-stage selection covers the exact anchor diagnostics and classification, v1/v2
compatibility, protected mutation negatives, CERI completion barrier, handoff, Setup, Winner,
restart/continuation, execution authority, and the pipeline branches at steps 17/19/23/26/29.
Ruff, Python compilation, diff checking (line-ending warnings only), and the official T14 source-pin
check (`{"stale":[]}`) passed before browser certification.

The two reproduction JUnits intentionally contain one failure each because they preserve the
original anchor and detached-reconstruction incidents; they are forensic artifacts, not green
certification claims.

## 9. Was CERI capture reporting consistent?

Yes, after remediation. The prior zero was a stale pre-provider default retained by the
continuation, while the capture job had persisted the certified population. The completion barrier
now computes one exact count—distinct tickers for the exact run/context with non-null immutable
evidence—and writes it to both `ceri_certified_capture_count` and `ceri_score_snapshots` on new and
coalesced continuations. The real PostgreSQL barrier regression proves equality and exactly-once
continuation behavior.

## 10. Were Setup and Winner verified?

They passed the focused and 163-test cross-stage integration selection, including real PostgreSQL
authority and negative tests. Chromium-10 also passed the realistic browser path.

They are not fully end-to-end certified at 100 members. The final full run reached Setup but the
transaction monitor terminated it before Setup publication and before Winner. Therefore focused
Setup/Winner verification is green, while full-scale downstream certification remains incomplete.

## 11. Did the real Chromium-100 pass?

No. The final separately identified run was
`test-results/single-run-certification/20261015T143136Z-f710dce5`.

It entered `CAPTURING_SETUP_SIGNALS` at `2026-10-15T14:47:58Z`. At
`2026-10-15T14:49:58.964456Z`, the invariant observer found PID 33844 idle in transaction for
`120.326891` seconds after `RELEASE SAVEPOINT sa_savepoint_91`. Pytest reported 1 failure in
1126.89 seconds.

The most specific remaining code localization is the parent-Session `SOURCE_CONTEXT` progress
write immediately before the 100-member CPU build. The production build call does not pass its
available checkpoint callback, so the parent transaction is not closed during that work. No blind
retry was made.

## 12. Did the complete Phase 2.9 suite pass?

It was not run. The explicit gate requires Chromium-100 PASS first. Release status is therefore
**NOT RUN / BLOCKED**, not PASS and not an inferred failure of unexecuted tests.

## 13. Were all transaction and source-integrity invariants preserved?

Source-integrity invariants were preserved: there is no evidence of raw mutation, membership
mutation, PIT/configuration drift, cross-run selection, hash rewrite, or weakened verification.

The 120-second transaction invariant remained enabled and correctly stopped the full run. Because
the application violated it, transaction correctness is **not certified**. The invariant itself was
preserved; the implementation did not satisfy it.

## 14. What remains uncertified?

- Parent transaction ownership across Setup's progress callback and 100-member CPU build.
- Full 100-member Setup publication.
- Full 100-member Winner publication and terminal cardinality.
- Final pipeline publication.
- The official complete Phase 2.9 release regression.

The anchor-failure exporter is operational and the original reproduction retained readable
sanitized evidence. The latest transaction-monitor exception occurred outside the pipeline's
classified-return path, so it did not create an anchor-specific export; its native JUnit,
`runtime-invariants.jsonl`, worker/web logs, environment metadata, and execution ID remain in the
execution-owned directory.

## Artifact index

- `TASK_2_9_12O_SYSTEMIC_ROOT_CAUSE.md`
- `TASK_2_9_12O_CONTRACT_DEPENDENCY_GRAPH.md`
- `TASK_2_9_12O_ANCHOR_FIELD_DIFF.json`
- `TASK_2_9_12O_CHANGE_IMPACT_MATRIX.md`
- `TASK_2_9_12O_CERI_RECONCILIATION.md`
- `TASK_2_9_12O_CROSS_STAGE_CERTIFICATION.xml`
- `TASK_2_9_12O_TARGETED_PIPELINE_CERTIFICATION.xml`
- `TASK_2_9_12O_CHROMIUM_CERTIFICATION.xml` (earlier preserved failed attempt)
- `TASK_2_9_12O_CHROMIUM_100_CERTIFICATION.xml` (latest failed attempt)
- `TASK_2_9_12O_FINAL_REPORT.md`

`TASK_2_9_12O_RELEASE_CERTIFICATION.xml` does not exist because the release suite was not executed.

