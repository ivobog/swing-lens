# T11E — Phase-2 Immutable Evidence Integration Certification

## 1. Executive verdict

**T11E VERDICT: PASS. PHASE-2 CERTIFIED: YES.** The complete Phase-2 evidence graph is
runtime-certified on disposable PostgreSQL. Historical readers resolve exact immutable evidence
or fail closed; mutable projections, later truth, repairs, and retrospective replay do not change
prior decision meaning. Certification found and fixed two genuine integration defects: populated
Phase-2 downgrade ordering and five ORM/migration evidence-index metadata mismatches.

## 2. Git baseline and remediation chain

| Item | Commit / value | Ancestry |
|---|---|---|
| Original audited baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | verified ancestor |
| T11A | `5d2df19c0ef4ff3e7f49f17bd7889378021bfb31` | verified ancestor |
| T11B-1 | `bef1abe285779c3aa278e3691a9438126f5ccedc` | verified ancestor |
| T11B-2 | `f5bfa546ac5d03e641eb67473b21e7a04629cb23` | verified ancestor |
| T11B-3 | `3aee1c260f63c53f5fe25d85be2948283b48dbc1` | verified ancestor |
| T11C | `7e19ad81c30727d5177f20d559b1a3b67304031a` | verified ancestor |
| T11D / T11E start | `5da05510feb0b43dea2d670e56f49dffb2a913cc` | verified starting HEAD |
| T11E branch | `codex/t11e-phase2-immutable-evidence-certification` | created from T11D |

The starting worktree was clean. Local and remote-tracking `main` both resolved to
`febe376be67f01589199cd3ba55af98fd54001ed`. No unrelated user changes were overwritten.

## 3. Migration chain

Alembic has one head, `0079_setup_lifecycle_alert_ev`. The exact Phase-2 chain is:

`0074_ceri_evidence_quarantine → 0075_core_immutable_evidence →
0076_regime_sector_evidence → 0077_ceri_decision_evidence →
0078_ibmi_constituent_evidence → 0079_setup_lifecycle_alert_ev`.

Revision metadata, parentage, head count, and executable ordering were verified.

## 4. PostgreSQL certification environment

Certification used repository CPython 3.12.2, Docker 29.6.2, and PostgreSQL 16.13 in container
`swinglens-t11e-pg-20260915`, exposed only on loopback with a random host port. Each test used a
uniquely named database accepted by the repository disposable-database guard. Credentials are
deliberately omitted. The container and all certification databases were disposable.

## 5. PostgreSQL migration results

- Empty database to `0074`, then Phase-2 upgrade to head: pass.
- Fresh empty database to head: pass.
- `alembic current`: `0079_setup_lifecycle_alert_ev (head)`.
- `alembic check`: `No new upgrade operations detected`.
- Head to `0074` downgrade with a populated Phase-2 graph: pass.
- Re-upgrade from `0074` to head: pass.
- ORM insert/commit/new-session reload and exact relationships: pass.

The original downgrade sequence attempted to restore narrower artifact-kind constraints before
removing evidence kinds introduced by later revisions. T11E changed revisions 0076–0079 to
delete only the newer, unrepresentable evidence kinds and their dependent current/source rows
before narrowing each constraint. This is correct downgrade behavior but necessarily discards
data that the older schema cannot represent; it must not be applied casually to an authoritative
database. T11E also aligned five evidence index declarations between migrations and ORM metadata.

Alembic reports an existing SQLAlchemy warning about sorting the mutually referencing lifecycle
evaluation/transition tables. It reports no drift and does not impede migration execution.

## 6. Phase-2 evidence graph certification

The integrated E1/C1 → E2/C2 test builds every shared core kind—Fundamental, Technical,
Combined, Ranking, Regime, Sector, CERI, IBMI, and Setup—then the lifecycle and alert chain.
It proves exact source IDs and frozen payloads survive a later independent world, a process/session
reload, and projection advancement. Dedicated T11A/B/C suites provide subsystem-depth proof in
addition to this cross-graph test.

### Phase-2 certification matrix

| Subsystem | Immutable evidence | Exact source pinning | Current projection separated | Historical read fail-closed | Legacy isolated | Later truth isolated | DB runtime certified | Overall |
|---|---|---|---|---|---|---|---|---|
| Fundamental | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Technical | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Combined | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Ranking | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Regime | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Sector | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| CERI | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| IBMI | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Setup | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Lifecycle | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Alerts | Yes | Yes | Yes | Yes | Yes | Yes | Yes | PASS |
| Winner | Frozen native evidence | Combined/Ranking | Yes | Yes | Yes | Yes | Yes | PASS |
| Pipeline/resume | Frozen handoff | Every manifest member | Yes | Yes | Yes | Yes | Yes | PASS |

## 7. Adversarial scenarios

The campaign exercises same values under different Calculation Identities, same run/ticker with
an incompatible identity, compatible cross-run Regime reuse, mutable current-pointer advancement,
provider corrections, price revisions, rule changes, lifecycle advancement, repair, replay,
evidence misses, legacy rows, process/session reload, pipeline resume, alert suppression, and
Winner freeze. Old consumers continue to resolve E1/C1; new work resolves E2/C2.

## 8. Current-projection advancement tests

Current projection rows move from E1 to E2 without updating the evidence envelope or its sources.
Historical `EVIDENCE` reads continue returning E1 after commit and new-session reload. Direct
inspection confirms equal business values under a different identity remain separately addressed.

## 9. Provider/price/rule correction tests

CERI and IBMI retain the exact provider facts/constituents used by the decision. Setup freezes
its PIT price manifest. Alert decisions retain exact R1 rule evidence. Provider corrections,
later price revisions, and R2 rule edits yield later evidence or projections and leave the earlier
decision output, sources, and hashes unchanged.

## 10. Lifecycle/alert history tests

Lifecycle evaluations include no-transition outcomes and pin prior evaluation/transition and Setup
evidence. Transitions form an immutable predecessor chain. Advancing the current episode does not
rewrite its prior chain. Generated, cooldown-suppressed, dedup-suppressed, and ineligible alert
decisions pin exact rule and eligible predecessor evidence; notification acknowledgement/dismissal
remains separate current state.

## 11. Historical-read fail-closed tests

`ReadMode.EVIDENCE` requires an evidence ID or complete Calculation Identity, validates kind and
scope, and does not consult current projections after a miss. Wrong-kind, missing, mismatched,
future, and incomplete evidence requests return explicit unavailable errors. No recalculation,
repair, rebuild, or latest-row fallback is invoked.

## 12. Legacy tests

Legacy current rows remain usable only through explicit current/legacy behavior. They cannot
masquerade as certified history and are not backfilled with invented evidence. Legacy-only
historical requests fail with an explicit legacy/evidence-unavailable status.

## 13. Winner freeze tests

Winner W1 retains its frozen vector and exact Combined/Ranking source evidence after upstream
current projections advance. Historical Winner serialization no longer enriches from mutable
Combined or Ranking rows and returns an explicit unavailable/mismatch status for invalid legacy
metadata.

## 14. Pipeline resume evidence tests

Resume validates the frozen Decision Handoff fingerprint, ownership, context, exact raw/core/
context members, and certified evidence pointers. A newer current pointer is ignored. Missing,
rewritten, or incompatible handoff evidence fails closed; the designed compatible cross-run Regime
member resolves by its exact manifest ID.

## 15. Writer immutability audit

Repository searches and call-site review found insert-or-exact-reuse writers for shared core,
CERI, IBMI, Setup, lifecycle, and alert evidence. No application update, delete, merge, bulk-update,
repair mutation, or cascade-delete path targets the evidence models. Supported ORM update/delete
is rejected by mutation guards. PostgreSQL restrictive FKs reject deletion of referenced evidence.
Mutable projection updates are separate and expected.

Privileged direct SQL by a database owner can bypass ORM mutation hooks; Phase 2 does not claim
database-row update denial against an administrator.

## 16. Reader re-audit

The T11D selector inventory was repeated across latest/newest/current/canonical, temporal anchors,
history, replay/repair/backfill/rebuild/recalculate/resume, and evidence/legacy status paths. Major
repository historical readers use exact evidence or an upper-bounded prior selector. Dashboard and
maintenance reads that intentionally remain current are explicitly classified. Confirmed material
historical-to-current fallback count is zero.

Performance inspection found exact-ID reads on primary keys, Calculation Identity/scope indexes,
batch evidence loads for historical pages, and bounded manifest membership queries for resume. No
new unbounded evidence scan or per-row historical N+1 was found.

## 17. Negative dependency certification

Static graph assertions and integration tests confirm the absence of CERI → Ranking/Setup/
Lifecycle/Winner, Setup/Lifecycle/Alert → Winner acquisition, same-run Sector → Ranking, and direct
IBMI → Winner decision edges. No Phase-2 change introduced a forbidden edge.

## 18. Phase-1 regression

The established Calculation Identity suite passes: 134 tests, with no failure or skip. Identity
embedding, compatibility, Combined/Ranking adoption, contextual adoption, and Winner acquisition
identity remain intact.

## 19. Phase-0 regression

The expanded temporal/domain/publication safety suite passes: 185 tests, including real PostgreSQL
temporal integrity. No Phase-0 safety regression was found.

## 20. Static gates

Ruff over `app`, `tests`, `scripts`, and every Phase-2 migration, Python `compileall`,
`git diff --check`, route-inventory governance, and the tracked-secret scan pass. Alembic reports
one head and no schema/model upgrade operations on a fresh PostgreSQL head. An additional
repository-wide Ruff probe that included all historical migrations reported 24 pre-existing
formatting findings in untouched revisions 0001–0066; these are outside the relevant configured
gate and were not rewritten during certification.

| Certification lane | Final result |
|---|---|
| T11E focused adversarial | 3 passed, 0 failed, 0 skipped, 9 warnings |
| Full T11A/B/C/D + T11E evidence suite | 60 passed, 0 failed, 0 skipped, 17 warnings |
| Affected PostgreSQL compatibility modules | 17 passed, 0 failed, 1 skipped, 36 warnings |
| Lifecycle consecutive stability rerun | 3 passed, 0 failed, 0 skipped, 6 warnings |
| Phase-1 identity | 134 passed, 0 failed, 0 skipped, 1 warning |
| Expanded Phase-0 safety | 185 passed, 0 failed, 0 skipped, 5 warnings |
| Broad safe repository lane | 2,574 passed, 0 failed, 0 skipped, 7 deselected, 22 warnings |

The single skip is the repository's explicit opt-in one-million-row observability load gate
(`SWINGLENS_RUN_OBSERVABILITY_LOAD_CERTIFICATION=1`), not a Phase-2 semantic or migration test.
The broad lane excludes `tests/integration` because PostgreSQL integration is separately executed,
`tests/e2e`, the live `external` marker, migration-remediation infrastructure, and the QA
infrastructure self-test. A stricter clean precursor that also omitted `slow` markers produced
2,541 passed and 40 deselected; it is not substituted for the final broader result above.

## 21. Failure/flakiness analysis

Two failures were `PHASE2_DEFECT`: populated downgrade ordering and ORM index metadata drift; both
were fixed and their exact regressions pass. Three stale-test assumptions were `TEST_DEFECT` and
were corrected: hard-coded old migration heads, a whole-row lifecycle hash that included a newly
nullable compatibility pointer, and an IBMI fixture that omitted now-required certified
constituents. Initial system-Python collection lacked repository dependencies and was classified
`TEST_INFRASTRUCTURE`; all certification ran under `.venv`. Broad exploratory all-integration runs
were stopped because they entered unrelated hours-long operational suites; they are not represented
as certification results. No unexplained Phase-2-relevant flaky failure remains. The lifecycle
PostgreSQL module passed twice consecutively after its fixture correction. The whole-history Ruff
probe is `PRE_EXISTING_BASELINE`: all findings are in untouched pre-Phase-2 migrations, while the
application/tests/scripts and complete Phase-2 migration chain pass Ruff.

## 22. Finding reconciliation

| Finding | Final status | Evidence-supported reason / remaining scope |
|---|---|---|
| `CORE-005` | PARTIAL | Fundamental evidence is immutable; fuller provider/effective-time/currency provenance remains. |
| `CORE-006` | CLOSED | Phase-2 historical Regime reads are exact; designed compatible global reuse remains only for new/current calculations. |
| `CORE-007` | CLOSED | Historical Sector evidence pins exact prior Sector/Regime/Ranking sources. |
| `RANK-006` | CLOSED | Certified history uses Ranking evidence; mutable compatibility rows are current/legacy only. |
| `CERI-002` | PARTIAL | Exact consumed PIT evidence is frozen; mixed price-basis policy remains. |
| `CERI-003` | CLOSED | Historical reads are exact; explicitly current/direct paths remain current operations. |
| `CERI-004` | PARTIAL | Stored decisions are sealed; current-rules rebuild is not original-context replay. |
| `CERI-008` | PARTIAL | CERI score configuration is frozen, but complete alert/global configuration authority remains. |
| `CERI-010` | PARTIAL | Certified references are deletion-protected; legacy/noncertified tombstone and purge policy remains. |
| `SETUP-002` | CLOSED | Older-context repair fails closed; valid repair appends evidence. |
| `SETUP-004` | PARTIAL | Phase-2 history is closed; unrelated consecutive-session/rule/config reconstruction remains. |
| `SETUP-005` | CLOSED | Historical reads are evidence-bound; direct current mode remains explicit. |
| `SETUP-006` | PARTIAL | Original-context reconstruction remains unsupported. |
| `SETUP-007` | PARTIAL | Historical temporal/rule predecessor evidence is closed; configuration authority remains. |
| `SETUP-009` | CLOSED | Historical evidence and mutable current selection are separated. |
| `SETUP-010` | PARTIAL | Maintenance remains current-context activity. |
| `XINT-003` | PARTIAL | Phase-2 historical reads are wall-clock independent; broader current/legacy behavior is not claimed. |
| `XINT-005` | CLOSED | Major repository decision paths use evidence within the supported application boundary; privileged direct SQL is not claimed. |
| `XINT-008` | CLOSED | Material Phase-2 historical and prior selectors are exact or upper bounded. |

## 23. Invariant certification

| Invariant | Final status | Certification boundary |
|---|---|---|
| `INV-EVIDENCE-001` | ENFORCED | Major repository decision paths resolve immutable evidence. |
| `INV-TRUTH-001` | ENFORCED | Later truth cannot reinterpret certified Phase-2 decision evidence. |
| `INV-FALLBACK-001` | ENFORCED | Historical evidence reads do not fall back to mutable current state; no global current/config claim. |
| `INV-TIME-002` | ENFORCED | Material historical selectors use exact IDs or upper-bounded predecessor selection. |

## 24. Residual risks

The following are explicit later-phase work and do not weaken the Phase-2 evidence guarantees:
full original-context replay/reconstruction; readiness propagation; configuration authority;
`PIPE-005` acquisition-plan semantics; refresh-cycle identity; background target-scope freezing;
entry-point unification beyond evidence reads; and privileged direct-SQL governance. Legacy rows
remain uncertified instead of being silently promoted.

## 25. Production safety statement

No production pipeline, authoritative SwingLens database migration, production evidence backfill,
row rewrite, Winner publication, broker order, destructive brokerage action, provider write, or
production CERI purge was performed. Only repository files and explicitly named disposable local
PostgreSQL databases/container were mutated.

## 26. Final Phase-2 verdict

All 30 Phase-2 pass conditions are satisfied. The implementation and real PostgreSQL runtime are
certified, no material historical-current fallback or supported evidence mutation path remains,
the required regression/static lanes pass, and both certification documents are complete.
SwingLens is ready to proceed to the next remediation phase without treating the explicitly partial
non-Phase-2 findings as closed.
