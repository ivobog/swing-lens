# SwingLens Phase 2.9 Release Certification Report

Date: 2026-10-14  
Branch: `codex/ceri-authority-remediation`  
Baseline revision: `6656c55dd59c294c2ea471571863046e02bf7c35`  
Schema head: `0092_phase2_eligibility_ledgers`

This report records the implementation and certification evidence available when the
operator directed the active test process to finish and requested that no additional
process be started. It does not convert incomplete or failing gates into a PASS.

## A. Executive verdict

| Gate | Verdict |
|---|---|
| PHASE 2.9 IMPLEMENTATION | **PARTIAL** |
| REPOSITORY BASELINE | **PASS** |
| POSTGRESQL TEST ENVIRONMENT | **PASS** |
| E2E TESTS | **FAIL** |
| T14 AUTHORITY | **FAIL** |
| ARCHITECTURE REGISTRY | **PASS** |
| FUNDAMENTAL ADMISSION | **PASS** |
| WARNING TAXONOMY | **PASS** |
| WINNER EVIDENCE | **FAIL** |
| PIPELINE STATUS | **FAIL** |
| CERI CONTAINMENT | **FAIL** |
| CERI ACTIONABILITY | **PASS** |
| FULL REGRESSION | **FAIL** |
| END-TO-END PIPELINE | **PASS** |
| HISTORICAL IMMUTABILITY | **PASS** |
| SAFETY INVARIANTS | **FAIL** |
| READY FOR NON-PRODUCTION RELEASE CANDIDATE | **NO** |
| READY FOR LIVE-PROVIDER CANARY | **NO** |
| READY FOR NORMAL PRODUCTION USE | **NO** |

The isolated comprehensive pipeline certification passed, but repository-wide
certification did not. The current revision must not be promoted. Nineteen known test
cases remain red: fifteen in the final retained non-browser replay and four browser/E2E
cases from the complete suite. In addition, the two final CERI context source changes
were made after the last source-pin certification and therefore require pin regeneration
and T14A re-execution before T14 can be considered green.

No merge, push, deployment, live-provider pipeline, production canary, historical
replay, or normal database mutation was performed.

## B. Initial repository and test baseline

The work began on `codex/ceri-authority-remediation` at commit
`6656c55dd59c294c2ea471571863046e02bf7c35`. The worktree already contained a large,
mixed set of uncommitted T14 authority-remediation and application changes. Those files
were treated as user-owned work: they were not reset, discarded, overwritten, or
silently stashed. Phase 2.9 changes were integrated in place and tracked in the Phase 2
implementation ledger.

The executable environment was the repository virtual environment at
`.venv/Scripts/python.exe`. The migration chain was verified through
`0092_phase2_eligibility_ledgers`. Stale tests that still expected `0086` were corrected
to verify the complete `0085 -> 0086 -> ... -> 0092` chain rather than merely changing a
single literal.

The initial complete current-suite execution, after the first remediation set, produced:

- 4,465 passed
- 14 skipped
- 50 failed
- 5 errors
- 2,112 warnings
- 10,474.75 seconds (`2:54:34`)

That run exercised real disposable PostgreSQL and browser test bodies; it was not a
collection-only or setup-only result. Its complete artifacts are:

- `PHASE2_9_FULL_PYTEST.log`
- `PHASE2_9_FULL_PYTEST.xml`

The final retained replay was deliberately limited to pytest's prior-failure cache and
excluded `tests/e2e`; it was not represented as a replacement full regression. It
produced:

- 12 passed
- 15 failed
- 529 deselected
- 0 setup/collection errors
- 843 warnings
- 939.71 seconds (`0:15:39`)

Its artifacts are:

- `PHASE2_9_FAILED_SET_RECHECK.log`
- `PHASE2_9_FAILED_SET_RECHECK.xml`

## C. Remediation of the 17 reported failures

| Reported group | Root cause and implementation | Evidence / disposition |
|---|---|---|
| PostgreSQL purge under SQLite | A PostgreSQL-only JSONB operation had been exercised through an emulation backend. Production semantics were retained and the operation was routed through the disposable PostgreSQL certification environment. | The original purge failure did not recur in the complete run. **Resolved.** |
| Lifecycle action inventory | `certify-runtime` was a legitimate registered action missing from the authoritative inventory. Registration, lifecycle authority, route/export documentation, and tests were reconciled. | Inventory checks passed. **Resolved.** |
| 144 architecture-registry findings | Registry generation, registered operations, mutation boundaries, ownership metadata, and source identities had drifted together. The registry scripts and generated registries were reconciled by operation family rather than suppressing findings. | Architecture/T14 focused set: 105 passed; registry clean. **Resolved at registry gate.** |
| Two transition-preflight fixtures | Test doubles no longer faithfully implemented the supported session lookup contract (`db.get`). Fixtures were updated without removing transition ownership or fail-closed checks. | Focused tests passed and neither failure recurred. **Resolved.** |
| Eight T14A inventory/pin/ownership failures | Progress callback state had been attached to a mapped ORM object and appeared to be an unregistered writer. Runtime callback state now lives in `Session.info`, with a non-SQLAlchemy fallback only for unit doubles. Source review and ownership inventory were regenerated. | `PHASE2_9_T14A_RECHECK.xml`: 20 passed. Two later CERI source edits now make the pin artifact stale until regenerated. **Implementation fixed; final pin certification pending.** |
| T14B consensus writer boundary | The CERI consensus path was brought under the registered mutation-authority wrapper and exact source ownership contract. | Did not recur in the complete or retained failure sets. **Resolved.** |
| Three T14D operation families | Admin-route tests used invalid request objects; CERI handoff fixtures omitted the exact market cutoff; operation-family metadata had drifted. Real local Starlette request fixtures and explicit cutoff identity were added. | `PHASE2_9_FIXTURE_CONTRACT_RECHECK.xml`: 4 passed. Six T14D performance/authority cases remain red, so the overall T14D gate is **not resolved**. |

Additional integration defects revealed only after PostgreSQL setup was repaired were
also addressed:

- Decision-handoff fixtures now seed the mandatory Market Regime and Sector Rotation
  snapshots with their effective-configuration identities.
- The handler inventory now accounts for the legitimate
  `CERI_FEATURE_CERTIFICATION` root (35 handlers).
- Pipeline progress callbacks no longer create an apparent pipeline-row mutation writer.
- Feature-only CERI certification now retains a distinct unowned CERI calculation
  context, at the requested deterministic cutoff, alongside the pipeline-owned market
  context. It no longer overwrites the retained CERI identity when it records root-job
  metadata.
- UI labels now map Liquidity Risk and Missing Penalty to their authoritative fields.
- Lazy run-detail expansion was repaired so the comprehensive browser certification can
  inspect all ticker detail evidence.

The CERI context regression that previously raised
`CERI_CONTEXT_IDENTITY_MISMATCH` passed after the authority correction:
`PHASE2_9_CERI_CONTEXT_RECHECK.xml` contains 1 passing PostgreSQL test.

## D. PostgreSQL and E2E remediation

Integration tests resolve a dedicated disposable PostgreSQL database with an
unmistakable `swinglens_pytest_*` identity. Before migration or destructive setup, the
fixture verifies both the database identity and its loopback/test endpoint. Credentials
are supplied through the test environment, redacted from diagnostics, and are not
committed. The fixture migrates the disposable database to repository head and disposes
it after use. The normal SwingLens database and Runs 22–33 were not used.

This repaired the prior credential-rejection condition: PostgreSQL tests reached their
actual assertions, and the complete suite had no blanket database-authentication setup
failure. The final retained replay likewise had zero setup errors.

The exact comprehensive isolated browser/pipeline certification passed:

- 1 passed
- 38 warnings
- 473.42 seconds
- JUnit: `PHASE2_9_COMPREHENSIVE_E2E.xml`
- Evidence directory: `test-results/single-run-certification/20261013T185330Z-3d0e39b6`

It used deterministic/recorded providers and explicitly skipped live provider access.
Its persisted assertions covered the complete cohort, bounded low-priority Fundamental
omission, critical Fundamental exclusion, risk-warning retention, unsafe CERI
containment, failed/cancelled ownership, durable jobs, decisions, source identities,
eligibility ledgers, completion, and GUI/API population semantics.

The browser gate nevertheless remains red because the full suite also found:

1. `test_frozen_real_writer_cardinality[chromium-100]` — a transaction remained idle
   longer than the 120-second production invariant during the 100-ticker path.
2. `test_windows_worker_kill_self_heals_repeatedly[0]` — canonical web readiness did
   not recover.
3. `test_windows_worker_kill_self_heals_repeatedly[1]` — the repeated recovery case
   failed identically.
4. `test_windows_runtime_survives_controller_exit_and_reuses_generation` — the test
   expected a lifecycle state location that conflicted with existing local state rather
   than a fully isolated temporary state path.

These tests were not rerun after their investigation because the operator requested no
new process after the retained replay. The complete E2E gate is therefore **FAIL**.

## E. Fundamental policy certification

The bounded Fundamental admission policy is implemented as an explicit versioned
consumer policy. Normal runtime retains legacy behavior; the new policy is activated
only in certification/non-production mode. No production activation was performed.

Admission to the approved score-native Combined and Ranking edges requires all of the
following:

- a finite numeric Fundamental score;
- a recognized score label;
- valid available/total field accounting;
- zero parsing failures;
- no missing critical, high-priority, or medium-priority fields;
- valid evidence in every configured scoring component;
- no blocking, unknown, source-integrity, temporal, or provenance warning;
- exact run and calculation identity; and
- a consumer edge explicitly supported by the policy.

Only supported low-priority omissions may pass. The existing numeric missing-data
penalty remains in the score, the omission and confidence reduction remain visible, and
no missing value is fabricated. A low-priority field that removes all valid evidence
from a required component remains excluded. Medium, high, critical, parse, non-finite,
unknown-warning, temporal, provenance, and cross-run defects remain fail-closed.

The policy does not grant Winner mandatory-source permission, setup permission, trade
eligibility, or general DEGRADED admission. Those remain separate contracts. The test
matrix covers complete data, one and multiple low-priority omissions, a component-
critical low-priority dependency, medium/high/critical omissions, an absent component,
non-finite score, invalid values, parsing failures, unknown and investment-risk
warnings, temporal/provenance failure, and cross-run mismatch.

The preserved shadow evidence reports:

- Run 32: 171 proposed included, 87 undecided.
- Run 33: 83 proposed included, 35 undecided.

Those evaluations did not rewrite either historical run. No arbitrary quantitative
coverage threshold was introduced.

## F. Phase 2 cross-component certification

Warning classification remains orthogonal: investment-risk warnings are retained as
analytical restrictions rather than converted into evidence-integrity failures;
unknown and integrity warnings remain fail-closed. The comprehensive isolated pipeline
confirmed the intended risk-bearing research path without treating it as trade
authorization.

Pipeline outcome, coverage, and integrity remain separately represented. `PARTIAL`
continues to describe operational/coverage state and does not itself certify or condemn
all evidence. Cancellation, failure, blocked state, dependency continuation, and
terminal fences remain explicit.

CERI feature certification now proves exact context identity and stops at the feature
boundary. Evidence decisions remain append-only and the UI consumes authoritative
eligibility/actionability results rather than independently reinterpreting them.
High/Normal confidence still requires the existing additional gates; Low and
Insufficient remain research-visible but non-actionable.

Cross-component release certification remains incomplete because the retained test set
still has CERI authority-scaling failures, Phase 4 worker/configuration failures, Winner
publication failures, and deterministic recovery failure. Component success is not used
to override those failures.

## G. Full regression evidence

### Complete repository run

Command class: complete `pytest` suite using `.venv/Scripts/python.exe`, disposable
PostgreSQL, and browser/E2E prerequisites.

Result: **50 failed, 4,465 passed, 14 skipped, 5 errors, 2,112 warnings in 10,474.75s**.

The five errors were stale `0086` schema-head setup assertions in T15a tests. Those were
corrected and the affected schema set was rechecked. Other focused evidence includes:

- T14A: 20 passed (`PHASE2_9_T14A_RECHECK.xml`), before the last two CERI source edits.
- Schema-head affected set: 11 passed and one stale direct-parent assertion identified
  (`PHASE2_9_SCHEMA_HEAD_RECHECK.xml`).
- Corrected complete migration-chain assertion: 1 passed
  (`PHASE2_9_SCHEMA_CHAIN_RECHECK.xml`).
- Handler inventory: 1 passed (`PHASE2_9_HANDLER_INVENTORY_RECHECK.xml`).
- Admin/cutoff fixture contracts: 4 passed
  (`PHASE2_9_FIXTURE_CONTRACT_RECHECK.xml`).
- Decision-context fixture regressions: 2 passed
  (`PHASE2_9_DECISION_CONTEXT_FIXTURE_RECHECK.xml`).
- CERI context identity: 1 passed (`PHASE2_9_CERI_CONTEXT_RECHECK.xml`).

### Final retained failure replay

Command:

```text
.venv\Scripts\python.exe -m pytest --lf --ignore=tests/e2e -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/PHASE2_9_FAILED_SET_RECHECK.xml
```

Result: **15 failed, 12 passed, 529 deselected, 843 warnings in 939.71s**.

The remaining failures are:

1. `test_high_cardinality_unrelated_rows_never_enter_scoped_change_reads`
   (`max_parameters` 33, required <= 10).
2. `test_public_upload_enqueue_process_restart_native_worker_and_frozen_history[decision-graph]`.
3. `test_public_resume_retains_c1_and_duplicate_completed_work_stays_behind_ceri_barrier`.
4. `test_real_worker_retry_after_native_fundamental_commit_uses_c1`.
5. `test_worker_heartbeat_uses_child_transaction_retaining_fence`.
6. `test_model_retirement_api_delivers_exact_new_governance_and_maps_rejections`.
7. `test_capture_authority_scaling[capture-1]`.
8. `test_capture_authority_scaling[capture-50]`.
9. `test_capture_authority_scaling[pipeline-1]`.
10. `test_capture_authority_scaling[pipeline-50]`.
11. `test_capture_authority_scaling[material-1]`.
12. `test_capture_authority_scaling[material-50]`.
13. `test_clean_estimate_publication_is_atomic_idempotent_and_never_resurrects_old_rows`.
14. `test_two_concurrent_api_clients_receive_one_maturation_workflow`.
15. `test_deterministic_pipeline_10_25_100`.

The recovery output also attempted an IB API loopback connection and received
`ConnectionRefusedError`. No connection was established and IB Gateway was not
launched, but a deterministic recovery test reaching that adapter is itself an isolation
defect that must be removed before certification.

The fourteen full-suite skips have not all been reclassified after the final changes.
They therefore remain part of the release audit, even though no evidence currently
shows that they concealed the reported PostgreSQL/E2E blockers.

No final complete suite was run after the focused repairs. Full regression is **FAIL**.

## H. Non-production end-to-end evidence

The deterministic single-run certification completed and its persisted evidence checks
passed. The scenario asserted:

- no unexpected terminal failure or stuck durable job;
- no orphaned active child job;
- exact run, scope, cutoff, configuration, and calculation identities;
- durable Fundamental, Technical, Combined, Ranking, CERI, setup/lifecycle, Winner,
  and eligibility evidence;
- local exclusion of critical Fundamental gaps;
- penalized but permitted score-native use of supported low-priority omissions;
- research retention of investment-risk observations;
- containment of genuine CERI integrity failure;
- terminal fencing for failed/cancelled ownership; and
- authoritative API/GUI population labels.

JUnit and the evidence directory named in section D are the machine-verifiable record.
No separately exported row-count manifest was produced in the retained report bundle,
so this report does not invent per-table counts. That reporting omission does not erase
the passing persisted assertions, but exact count export should be added when the next
authorized process is run.

The isolated end-to-end scenario is **PASS**. It does not override the failed full-suite
and Windows/browser gates.

## I. Historical immutability

Runs 22–33 were not replayed, recovered, reclassified, backfilled, or mutated. Run 32/33
shadow results were read as fixtures/evidence only. No Winner prediction or outcome was
fabricated. CERI dispositions and eligibility ledgers remain append-only. Calculation
fingerprints, source identities, point-in-time cutoffs, and run ownership were preserved.

The Phase 2 immutable-evidence and migration-chain focused checks passed after fixture
correction. Historical immutability is **PASS** for the work performed.

## J. Architecture and mutation authority

Architecture-registry remediation retained enforcement. No finding category was
suppressed and no global writer exemption was introduced. The registry suite previously
reported 105 passing checks and no findings.

The progress callback correction removed non-persistent worker state from a mapped
PipelineRun object and uses `Session.info` for real sessions. The decision fixtures now
produce the mandatory regime/sector sources instead of bypassing handoff validation.
The CERI certification fix preserves the resolver's fail-closed requirement for an
unowned, pipeline-retained CERI authority; it does not weaken identity checks.

T14 is nevertheless **FAIL** at this evidence point because:

- six T14D CERI authority-scaling cases remain red;
- the worker heartbeat/fence case remains red; and
- source pins were last generated before edits to
  `app/services/market_calculation_context_service.py` and
  `app/services/ceri/feature_certification_workflow.py`.

The correct next action is to repair the operation-family query/authority behavior,
regenerate reviewed pins using the repository generator, and rerun T14A/T14D. The pins
must not be manually edited to evade review.

## K. Git, schema and rollback readiness

The branch remains uncommitted and mixed with pre-existing user/T14 work. It is not yet
a single reviewable release commit. No merge or push occurred.

The schema head is `0092_phase2_eligibility_ledgers`; Phase 2.9 introduced no successor
migration. The corrected tests verify the full migration path rather than assuming
`0086` is head.

Fundamental policy rollback is configuration-based: normal runtime continues to use the
legacy policy, while the bounded policy is certification-only. Rolling back policy
activation does not delete consumer decisions, shadow evaluations, Winner acquisition
decisions, CERI dispositions, or other audit ledgers. Database rollback by destructive
evidence deletion is neither required nor authorized.

Rollback readiness is technically bounded, but release packaging is **not ready** until
the dirty worktree is separated/reviewed and all mandatory gates pass.

## L. Remaining risks

| Severity | Risk | Affected area | Required next action |
|---|---|---|---|
| P1 | 15 current non-browser tests fail | CERI, Phase 4 configuration, Winner, recovery, T14 | Diagnose each retained traceback and repair root causes. |
| P1 | Four full-suite browser/E2E tests fail | Cardinality and Windows lifecycle/recovery | End long transactions before CPU-heavy work and isolate ports/state paths in temporary test resources. |
| P1 | T14 source pins are stale after final CERI changes | Mutation/source authority | Regenerate via the reviewed generator and rerun all T14A/T14D gates. |
| P1 | Deterministic recovery reached the IB adapter | Provider isolation | Make recovery fixtures select the deterministic adapter before any network-capable construction. |
| P1 | No green post-fix complete suite exists | Release certification | Run the entire suite once the known blockers are repaired. |
| P1 | CERI scoped read binds 33 parameters where <=10 is required | Population containment/performance | Replace per-member binding with the intended retained-scope join/bounded identity query without broadening membership. |
| P1 | CERI capture/material/pipeline scaling contracts remain red | T14D authority/performance | Reconcile legitimate source-authority reads and fix missing semantic membership for pipeline populations; do not inflate limits blindly. |
| P1 | Winner publication/concurrency tests remain red | Winner immutability/idempotency | Repair publication identity and single-flight API admission under concurrent clients. |
| P1 | Phase 4 worker/configuration cases remain red | Durable configuration and retry | Preserve C1 binding through restart/resume/retry and revalidate the CERI barrier. |
| P2 | Per-table E2E count export is absent from the report bundle | Audit usability | Export a machine-readable count/identity manifest on the next authorized certification run. |
| P2 | Fourteen skips require final classification | Coverage audit | Review every skip against platform limitation versus missing mandatory verification. |
| P2 | Worktree contains mixed uncommitted ownership groups | Release reviewability | Separate or commit coherent change groups only after tests pass; preserve user changes. |

There are unresolved P1 release blockers. Safety invariants and release eligibility are
therefore **FAIL**, even though many component gates and the isolated pipeline scenario
pass.

## M. Final release decision

**NO — the current implementation cannot safely proceed to a live-provider canary.**

The exact blockers are the fifteen retained non-browser failures, four outstanding
browser/E2E failures, stale T14 source-pin certification after the final CERI fix, an
unresolved deterministic-provider isolation defect in recovery, and the absence of a
green post-fix complete repository run.

The implementation has materially advanced: disposable PostgreSQL is reproducible,
the comprehensive deterministic pipeline passes, migration head is coherent, the
bounded Fundamental policy is certified for its narrow consumers, registry drift is
resolved, and historical evidence remains immutable. Those results justify continuing
remediation from this state; they do not justify promotion.

Normal production use and any live-provider canary remain unauthorized.
