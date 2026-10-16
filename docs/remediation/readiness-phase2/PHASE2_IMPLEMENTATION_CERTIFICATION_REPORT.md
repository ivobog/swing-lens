# SwingLens Phase 2 implementation and certification report

Baseline branch: `codex/ceri-authority-remediation`  
Baseline revision: `6656c55dd59c294c2ea471571863046e02bf7c35`  
Certification date: 2026-10-13  
Schema head: `0092_phase2_eligibility_ledgers`

## A. Executive verdict

- PHASE 2 IMPLEMENTATION: **PARTIAL**
- FUNDAMENTAL POLICY: **BLOCKED** — the contract and shadow evaluator pass, but no production threshold was invented or activated.
- WARNING TAXONOMY: **PASS**
- WINNER EVIDENCE: **PASS**
- PIPELINE STATUS MODEL: **PASS**
- CERI CONTAINMENT: **PASS**
- CERI ACTIONABILITY: **PASS**
- API/UI SEMANTICS: **PASS**
- HISTORICAL IMMUTABILITY: **PASS**
- SAFETY INVARIANTS: **FAIL** — Phase 2-specific invariants pass, but the repository-wide safety gate still contains 17 unrelated failures.
- INTEGRATION TESTS: **FAIL** — the broad suite is not green and 24 E2E cases cannot authenticate with their default PostgreSQL credentials.
- SHADOW CERTIFICATION: **PASS**
- PROSPECTIVE ACTIVATION: **NOT AUTHORIZED**
- READY FOR PRODUCTION CANARY: **NO**

The implementation corrects the approved architectural defects without changing Runs 22–33, but complete release certification is blocked by the existing repository failures, unavailable E2E database setup, unresolved Fundamental production thresholds, and the absence of an authorized non-production canary.

## B. Implementation summary

Implemented:

- Orthogonal, deterministic consumer decisions across execution, integrity, completeness, confidence, warning category, and consumer eligibility in `app/services/eligibility_policy.py`.
- A versioned, exact-code warning catalog with conservative unknown-code handling in `app/services/warning_taxonomy.py`.
- Append-only generic consumer decisions, Winner acquisition decisions, and shadow evaluations in `app/models/tables.py`, `app/services/consumer_eligibility_ledger.py`, and `app/services/eligibility_shadow.py`.
- Shadow-only Fundamental admission contracts in `app/services/fundamental_admission_policy.py`; scoring arithmetic was not changed.
- Prospective advisory-warning normalization in `app/services/producer_readiness.py`, preserving the Fundamental/Technical correction already present at the baseline and extending it to Combined/Ranking.
- A durable Winner acquisition decision for future inserted, duplicate, excluded, and failed attempts in `app/services/winner_probability/capture_service.py`.
- Versioned pipeline outcome summaries in `app/services/pipeline_outcome.py`, integrated into `pipeline_executor.py`, `pipeline_service.py`, and `pipeline_state_machine.py`.
- Hazard-based CERI containment in `app/services/ceri/evidence_eligibility.py` and `pipeline_containment_service.py`.
- Consumer-specific CERI actionability in `app/services/ceri/actionability.py`, enforced by `alert_service.py` and reported by `evidence_population.py`.
- Additive pipeline, eligibility, and acquisition Prometheus metrics in `app/observability/metrics.py` and commit-aware publication in `transaction_metrics.py`.
- Population-explicit CERI and Winner reporting in `run_routes.py`, `ceri_routes.py`, `run_detail.html`, `ceri_dashboard.html`, and `ceri_ticker.html`. Winner uses the new decision ledger for prospective runs and the frozen pipeline result summary for legacy denominators; it does not fabricate per-ticker historical decisions.

Schema and tools:

- `alembic/versions/20261013_0092_phase2_eligibility_ledgers.py` adds `consumer_eligibility_decisions`, `winner_acquisition_decisions`, and `eligibility_shadow_evaluations`, with keys, checks, indexes, idempotent fingerprints, and PostgreSQL UPDATE/DELETE rejection triggers.
- `scripts/export_phase2_readiness_baseline.py` exports read-only Run 32/33 fixtures, including source identities, cutoffs, coverage, components, sector, readiness, and section fingerprints.
- `scripts/certify_phase2_eligibility_migration.py` certifies the migration in a disposable PostgreSQL database.

Documentation:

- ADR-0013 records P1–P6, retained invariants, compatibility, versioning, and rollback.
- `TRACEABILITY_MATRIX.md`, `IMPLEMENTATION_LEDGER.md`, and `SHADOW_RESULTS.md` provide requirement-to-test traceability and durable certification evidence.

No live provider, production pipeline, replay, historical reclassification, deployment, merge, push, or commit was performed. The 48 pre-existing tracked modifications and untracked GUI/readiness work were preserved.

## C. Policy implementation matrix

| Policy | Original behavior | New behavior | Consumers | Version | Activation | Regression evidence |
|---|---|---|---|---|---|---|
| P1 Fundamental scores | A positive missing-data penalty could become universal non-use after the score had already been penalized. | Keeps arithmetic unchanged; evaluates score validity, priority omissions, component presence, parsing, warnings, and the consumer edge separately. | Combined/Ranking shadow-evaluated; Setup/Winner remain undecided. | `fundamental-admission-shadow-v1` | Shadow only | Omission matrix plus Run 32/33 deterministic shadows. |
| P2 Winner Evidence | Warning-bearing mandatory sources could be excluded as if warnings were integrity failures; exclusions lacked a dedicated durable per-attempt ledger. | Keeps Technical/Ranking/Combined mandatory, preserves exact identities/cutoffs, admits classified business-risk observations prospectively, freezes warnings as features, and records every future acquisition outcome. | Winner research/prediction acquisition; trade/training remain separate. | `winner-source-acquisition-v1`; existing edge policies including `combined-to-winner-v2` and `ranking-to-winner-v2` | Code present; not deployed | Capture, retry/idempotency, readiness, and route/view-model tests. |
| P3 Typed warnings | Producer-specific allowlists and generic warning degradation produced inconsistent Combined/Ranking readiness. | Central exact-code taxonomy; original codes retained; investment/strategy warnings preserve integrity readiness; unknown and integrity/temporal/quality/completeness codes fail closed. | Fundamental, Technical, Combined, Ranking, Winner. | `warning-taxonomy-v1` | Prospective code path | Taxonomy tests and 51-row Run 33 shadow differential. |
| P4 CERI containment | Any `PARTIAL` owner was globally unsuccessful for CERI. | Separates execution outcome, analytical gaps, and integrity hazard. A `PARTIAL` owner is safe only with an exact versioned `NO_INTEGRITY_HAZARD` summary; legacy/missing summaries fail closed. | Pipeline operations, CERI eligibility, reconciliation, UI. | `pipeline-outcome-v1` | Prospective code path | Complete/gaps/failure/cancel/block/legacy matrices and CERI completion-barrier tests. |
| P5 CERI actionability | Integrity eligibility and analytical actionability were conflated in reporting/alerts. | Research display, change detection, alerting, and recommendation are explicit consumers. Low may be research-visible/change-detectable; Insufficient is research-only; alerts/recommendations require High/Normal plus their existing opportunity/risk/provenance gates. | CERI UI, changes, opportunity/risk alerts. | `ceri-actionability-v1` | Prospective code path | Confidence matrix, alert tests, population reconciliation. |
| P6 Historical immutability | Historical records risked reinterpretation when current policy changed. | Frozen readiness is read as stored; shadows use exported inputs; explicit dispositions remain first-class; ledgers are append-only; no historical Winner row is inserted. | All historical readers and shadows. | Fixture schema `swinglens-readiness-phase2-baseline-v1` | Active invariant | Independent read-only re-export and byte comparison; database append-only trigger test. |

## D. Historical baseline comparison

| Measure | Run 32 legacy | Run 32 shadow | Run 33 legacy | Run 33 shadow |
|---|---:|---:|---:|---:|
| Pipeline status | `PARTIAL` | Stored status unchanged | `PARTIAL` | Stored status unchanged |
| Fundamental rows | 258 | 171 included / 87 undecided for score-only edges | 118 | 83 included / 35 undecided for score-only edges |
| Fundamental positive penalties | 118 | Arithmetic unchanged | 53 | Arithmetic unchanged |
| Combined incomplete | 222 | No historical mutation | 53 | No historical mutation |
| Complete warning-bearing Combined | 0 | 0 affected | 51 stored `DEGRADED` | 51 prospectively `READY` for integrity/readiness only |
| Winner predictions inserted | 36 | 36; no inserts | 14 | 14; no inserts |
| Winner excluded | 222: 219 source-ineligible, 3 insufficient bars | Stored summary unchanged | 104: 53 source-ineligible, 51 readiness-undecided | Stored summary unchanged; 51 warning decisions explained prospectively |
| CERI captured | 258 | 258 conditionally research-visible if independently eligible | 118 | 118 conditionally research-visible if independently eligible |
| CERI confidence | 256 Low / 2 Insufficient | 256 change-detectable; 0 alert/recommendation | 116 Low / 2 Insufficient | 116 change-detectable; 0 alert/recommendation |
| CERI historical dispositions | 258 excluded | Still excluded | 118 excluded | Still excluded |

Fixture fingerprints:

- Run 32: `1bf88b6d9235b65a2ddcce5468e592fc37d647f51951f8c29c6d95d1aff00a73`
- Run 33: `554f41a6ae6dc3e68286dbfbf9c034c0a69d5d4d7e63710d95321e109e86b0c1`

A second read-only export was byte-identical to both fixtures. The exporter enforces `default_transaction_read_only=on`. No historical row, readiness envelope, disposition, prediction, outcome, identity, or cutoff was changed.

## E. Fundamental coverage and thresholds

The implemented shadow contract requires:

- A finite stored score and a non-empty label.
- Zero parse failures.
- Valid available/total field accounting.
- No missing critical, high, or medium priority fields.
- Explicit component coverage with every represented component greater than zero.
- No integrity-blocking or unknown warning.
- Only score-native Combined/Ranking edges; Setup/Winner field dependencies are not certified.

Low-priority omissions can therefore produce `INCLUDED` with `PARTIAL` completeness and `LOW` confidence when every component has evidence. This is a categorical support rule, not a percentage threshold. No minimum coverage percentage or maximum omission count was invented.

Run 32 reasons (overlapping where multiple defects exist): 106 classified risk observations, 77 high omissions, 45 medium omissions, nine critical omissions, one absent component, 31 low-only supported gaps, and 55 complete observations. Run 33: 55 classified risk observations, 31 high omissions, 23 medium omissions, one critical omission, 18 low-only supported gaps, and 23 complete observations.

Production Fundamental admission is blocked because no outcome-backed evidence justifies quantitative thresholds for medium/high omissions, minimum component quality, Setup dependencies, or Winner dependencies. The current live policy therefore remains unchanged.

## F. Winner sample and warning analysis

For future runs, every Winner attempt writes one idempotent, inspectable acquisition decision with upload/pipeline identity, ticker/raw row, decision cutoff, exact source identities, source decisions, warning classifications, reason, policy version, fingerprint, and final outcome (`INSERTED`, `DUPLICATE`, `EXCLUDED`, or `FAILED`). Exclusion/error paths use a durable transaction without creating a prediction.

The 51 Run 33 complete warning-bearing observations split into:

- 29 with only `INVESTMENT_RISK`.
- 6 with only `STRATEGY_RESTRICTION`.
- 16 with both.
- 133 investment-risk and 25 strategy-restriction warning occurrences in total.

They span all descriptive score deciles, low to high: 5, 5, 6, 12, 7, 2, 5, 3, 4, 2. They span 13 sectors: Electronic technology 9; Technology services 6; Health technology, Retail trade, Energy minerals, and Industrial services 5 each; Distribution services and Commercial services 4 each; Health services 3; Transportation 2; Finance, Producer manufacturing, and Consumer non-durables 1 each.

None of the 51 has a historical Winner prediction, so none has a legitimate historical outcome attached to a prediction. Their admission could reduce the legacy sample's warning-free selection bias, but calibration/training impact cannot be measured without prospective outcomes. They remain outside training and no later information was used to manufacture outcomes.

## G. CERI integrity and actionability

The pipeline outcome contract distinguishes `SUCCEEDED`, `SUCCEEDED_WITH_GAPS`, hard terminal failure, coverage gaps, and integrity hazard. A new `PARTIAL` pipeline with only unrelated analytical gaps can leave independently certified CERI evidence eligible. A `PARTIAL` pipeline with an integrity hazard, an absent/unknown summary, cross-run ownership, bad identity/provenance, missing certification, unsafe lifecycle state, explicit quarantine, or an existing exclusion remains blocked.

Explicit historical dispositions are evaluated before owner status, so the 258 Run 32 and 118 Run 33 exclusions remain excluded. This change cannot silently make them current.

Consumer policy:

| Confidence/evidence | Research display | Change detection | Alert/recommendation |
|---|---|---|---|
| High/Normal and integrity-eligible | Yes | Yes | Only with existing opportunity/risk/provenance requirements |
| Low and integrity-eligible | Yes | Yes | No |
| Insufficient and integrity-eligible | Yes | No | No |
| Integrity-ineligible or explicitly excluded | Audit path only | No | No |

Thus the frozen Run 33 population remains 118 captured/118 historically excluded. Conditional shadow evaluation shows 118 research-visible, 116 change-detectable, and zero alert/recommendation candidates if integrity eligibility were independently established; it does not reactivate the records.

## H. Safety certification

Phase 2-specific protections passed:

- Exact source fingerprint and decision cutoff are mandatory and fingerprinted.
- Unknown warnings and unreviewed edges fail closed.
- Mandatory Winner Technical/Ranking/Combined contracts remain exact.
- Cross-run, temporal, provenance, certification, parent-terminal, and hard failure fences were not relaxed.
- Legacy `PARTIAL` without the v1 outcome summary remains contained.
- Existing CERI `EXCLUDED` dispositions override prospective owner reinterpretation.
- Low/Insufficient CERI cannot produce opportunity/risk alerts.
- Retry/idempotency produces stable ledger rows; PostgreSQL rejects ledger UPDATE/DELETE.
- Migration upgrade/downgrade/re-upgrade and cleanup passed in a disposable database.
- Run 32/33 byte-identical re-export proves no historical mutation.
- No provider, replay, live pipeline, training update, deployment, or production writer was invoked.

The overall safety verdict is nevertheless **FAIL** because the repository-wide regression contains unresolved architecture/authority certification failures outside these Phase 2 surfaces. Those failures prevent complete release certification even though the Phase 2 invariant suite is green.

## I. Regression test results

Primary repository command:

```text
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Result before the three directly attributable Phase 2 repairs: **4018 passed, 443 skipped, 20 failed, 24 errors** in 19m08s. The three attributable failures were stale schema-head assertions (two tests) and an obsolete Technical-warning expectation (one test). The approved policy justified the expectation correction; the repaired 59-test gate passed.

Final component gates:

- Policy/ledger/Fundamental/producer readiness: **68 passed, 1 skipped**.
- Pipeline outcome/executor/service/authority/CERI barrier: **66 passed, 4 skipped**.
- CERI actionability/population/alerts/context/UI: **41 passed**.
- Winner capture/readiness plus pipeline/run UI models: **270 passed**.
- Aggregate: **445 passed, 5 skipped**.
- Winner denominator/legacy fallback and affected route/UI regression: **69 passed** (overlaps the component aggregate except for the added denominator tests).
- Frozen fixture verification after the sector extension: **10 passed**.
- Ruff over every Phase 2 Python surface: **passed**.

The five component-gate skips require a default `postgres:postgres` test connection that is unavailable. The dedicated migration certificate used the configured local PostgreSQL connection and passed.

Remaining 17 failures:

- One SQLite purge emulation failure (`jsonb_array_elements_text` is PostgreSQL-only).
- One lifecycle action inventory mismatch caused by the pre-existing `certify-runtime` action.
- One architecture registry drift test with 144 pre-existing findings.
- Two transition-preflight fixture failures whose `SimpleNamespace` has no new `db.get` behavior.
- Eight T14A mutation-inventory/source-pin/ownership failures.
- One T14B unwrapped CERI consensus writer boundary.
- Three T14D incomplete/changed operation-family failures.

All 24 E2E setup errors fail before execution because `127.0.0.1:5432` rejects the suite's default `postgres` password. No second complete 19-minute repository run was claimed; the repaired cases and every affected Phase 2 suite were rerun directly. Integration status therefore remains **FAIL**.

## J. Migration and compatibility assessment

Alembic reports one head: `0092_phase2_eligibility_ledgers`. In a disposable database the certificate proved absence at 0091, presence at 0092, persistence/idempotency, database append-only rejection, absence after downgrade, presence after re-upgrade, and database cleanup.

Compatibility is additive:

- Existing readiness enums and historical envelopes remain readable.
- Existing `PipelineStatus` values remain unchanged; the orthogonal summary is added to `result_json`.
- Existing API fields remain; population and outcome fields are additive.
- Original warning codes remain stored.
- SQLite unit harnesses do not pretend to certify PostgreSQL-only ledger persistence.

External consumers of additive JSON fields were not independently surveyed. Rollback of behavior should disable prospective policy writers while preserving 0092 audit tables. Although schema downgrade is mechanically certified in a disposable database, dropping populated decision ledgers in production would destroy audit evidence and is not an authorized rollback strategy.

## K. GUI/API verification

- Pipeline status JSON exposes the versioned outcome/coverage/integrity summary while retaining the legacy status.
- CERI dashboard, ticker, and Run Detail show captured, eligible/current, actionable, research-only, excluded, quarantined, low-confidence, insufficient, and warning populations with reason codes.
- Winner Run Detail shows attempted, inserted, duplicate, excluded, failed, snapshots, estimates, insufficient, and categorized exclusion reasons.
- Prospective Winner counts come from the append-only decision ledger. Historical runs without ledger rows use only their frozen pipeline result summary and are labeled as such; no historical ticker decision is manufactured.
- Run 33 therefore reconciles as 118 attempts = 14 inserted + 104 excluded, with 51 readiness-undecided and 53 source-ineligible, while still containing only 14 prediction snapshots.
- UI tests confirm that zero current/actionable does not mean zero captured, and that the UI consumes backend decisions rather than promoting evidence itself.

Browser E2E verification remains blocked by the test database credential failure described in Section I.

## L. Remaining risks and blockers

1. Fundamental production admission has no justified quantitative thresholds for unresolved consumer dependencies; it remains shadow-only.
2. The repository-wide regression is not green: 17 non-Phase 2 failures and 24 E2E setup errors remain.
3. No authorized non-production end-to-end pipeline was executed after activation of 0092.
4. No live provider or controlled production canary was authorized or run.
5. The 51 potential Winner additions have no legitimate historical outcomes, so calibration and training effects remain unknown.
6. External consumers of the additive API/result fields have not been inventoried outside this repository.
7. The worktree still contains substantial pre-existing uncommitted work; Phase 2 changes must not be promoted as an isolated clean release without deliberate separation and review.

## M. Final operational recommendation

1. **Further shadow testing: YES.** Continue frozen and prospective non-authoritative evaluation, especially Fundamental component dependencies and the 51 warning-bearing Winner candidates.
2. **Non-production end-to-end execution: NOT YET.** First repair the PostgreSQL E2E configuration and resolve or formally quarantine the 17 repository failures; then run a disposable 0092 pipeline without live providers unless separately authorized.
3. **Controlled production canary: NO.** It is neither authorized nor certified by the current gate.
4. **Normal production use: NO.** Keep historical records untouched, retain Fundamental shadow-only behavior, preserve the 0092 ledgers, and do not deploy/merge/push until the full safety and integration gates pass.

Phase 2 is suitable for continued shadow work and code review, not release promotion.
