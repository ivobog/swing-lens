# Task 08 — Calculation Lineage Registry Synthesis Report

## 1. Implementation Baseline Verification

| Item | Result |
|---|---|
| Audited implementation baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Repository HEAD at synthesis | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Branch | `codex/winner-evidence-remediation` |
| Alembic head recorded by audits | `0074_ceri_evidence_quarantine` |
| Implementation drift | None; `BASELINE_DRIFT` was not raised |
| Audit date | 2026-09-14, Europe/Zurich |

Tracked application/runtime/calculation code did not change between the baseline and Task 08. Pre-existing audit documents and unrelated untracked operations probe/test files were not treated as implementation drift. Task 08 did not inspect later behavior into the baseline.

## 2. Input Reports Used

All seven reports were read in full:

1. `01_pipeline_execution.md`
2. `02_core_calculations.md`
3. `03_ceri.md`
4. `04_ranking.md`
5. `05_setup_lifecycle.md`
6. `06_winner.md`
7. corrected final `07_cross_cutting_integrity.md`

The synthesis normalizes their evidence into the durable master registry; it does not concatenate them. Original finding IDs remain cross-references.

## 3. Contradictions Resolved

| Earlier/conceptual claim | Resolution and authority |
|---|---|
| stage adjacency implies a data edge | Rejected. Task 01 stage order and Tasks 04–06 code-level dependency evidence are separate graphs. |
| same-run Sector Rotation feeds Ranking | Rejected. Ranking runs first and does not read Sector; Sector consumes Ranking. |
| CERI feeds Ranking | Rejected by Task 04 code trace. |
| CERI feeds Setup/Lifecycle | Rejected conclusively by Task 05 and corrected Task 07. |
| CERI feeds Winner | Rejected conclusively by Task 06 and corrected Task 07. |
| Setup/Lifecycle feeds Winner | Rejected conclusively by Task 06 and corrected Task 07. |
| Technical insufficiency reaches Setup through Combined/Ranking | Corrected. TechnicalScore feeds Setup/Lifecycle directly. Combined and Ranking scores/decisions are Setup metadata only; Combined `earnings_risk` separately affects actionability. |
| `PIPE-001` is a Setup/Winner contamination path | Rejected. It remains a CERI child-DAG completion/accounting defect only. |
| one generic CERI score exists | Rejected. CERI exposes independent opportunity, event-risk, confidence, and posture values. |
| IBMI is only a dashboard subsystem | Rejected. IBMI liquidity feeds Ranking; volatility/short-pressure feed CERI. It is not a Winner input. |

No remaining contradiction was resolved by silent assumption. Unresolved intent/behavior conflicts are recorded as `CONTRADICTORY`, including CERI provider priority, Gray-versus-bullish regime policy, and nominal Winner/IBMI master switches.

## 4. Corrections Applied During Synthesis

- Replaced architecture diagrams based on stage order with a separate verified data-dependency graph.
- Applied the final Task 07 precision corrections to every Setup readiness, compatibility, mutable-projection, and root-cause entry.
- Classified Ranking and Combined score/decision fields as Setup metadata/provenance, not lifecycle gates.
- Preserved direct TechnicalScore and Combined `earnings_risk` as Setup behavioral inputs.
- Removed all implemented CERI→Setup, CERI→Winner, and Setup→Winner edges.
- Preserved Winner as an independent consumer of Raw, Fundamental, Technical, Combined, Ranking, regime, and sector.
- Distinguished frozen value identity from valid source selection, and `RECORDED` hashes from `VALIDATED`/`ENFORCED` proof.
- Added IBMI as a first-class calculation, job, writer, readiness, fallback, configuration, and fencing subsystem.

## 5. Important Architecture Assumptions Disproved

Execution order is not the dependency graph. Shared `run_id+ticker` is not calculation identity. A positive numeric value is not evidence of readiness. A hash proves only the canonical payload supplied to it unless a consumer validates a complete declared schema. A mutable/current row is not historical evidence. Atomic publication is not monotonic publication. “Replay” and “backfill” do not imply original-context reconstruction.

The concrete negative dependencies are now explicit: CERI feeds neither Ranking, Setup/Lifecycle, nor Winner; Setup/Lifecycle does not feed Winner; same-run Sector Rotation does not feed Ranking; IBMI does not feed Winner directly.

## 6. New Subsystem Discovered by Task 07

IB Market Intelligence was elevated to a first-class calculation subsystem. It owns intelligence runs, historical metric current/revision state, live snapshots, availability/shortability, liquidity, volatility, short pressure, options activity, calculated feature rows, and background refresh/rebuild work.

Its key systemic defect is temporal: a feature row can declare an old `as_of_session`/cutoff while source selectors read future/unbounded metric bars, latest live/availability/shortable rows, and current PriceBars (`XINT-002`). Its hashes record the contaminated payload but do not prove eligibility. IBMI liquidity affects Ranking, and IBMI volatility/short-pressure affects CERI. The feature rebuild also provides the clearest verified stale-worker domain-commit path (`XINT-009`).

## 7. Findings by Severity and Subsystem

| Report/subsystem | P1 | P2 | Total |
|---|---:|---:|---:|
| Task 01 Pipeline (`PIPE-*`) | 2 | 7 | 9 |
| Task 02 Core (`CORE-*`) | 2 | 7 | 9 |
| Task 03 CERI (`CERI-*`) | 5 | 7 | 12 |
| Task 04 Combined/Ranking (`RANK-*`) | 7 | 1 | 8 |
| Task 05 Setup/Lifecycle (`SETUP-*`) | 6 | 4 | 10 |
| Task 06 Winner (`WIN-*`) | 3 | 7 | 10 |
| Task 07 Cross-cutting/IBMI (`XINT-*`) | 9 | 3 | 12 |
| **Total** | **34** | **36** | **70** |

The total includes 58 vertical findings and 12 systemic findings. `XINT-*` entries intentionally normalize/root-map several vertical findings, so 70 is a finding-record count, not a claim of 70 independent root defects. No P0 finding was assigned by Tasks 01–07.

## 8. Global Invariant Status

The 15 Task 07 systemic invariants were assigned stable master IDs. Fourteen are `VIOLATED` or `NOT_ENFORCED`; decision-evidence immutability versus later truth is `PARTIALLY_ENFORCED` because Winner is a strong local reference while other subsystems violate the repository-wide rule. None is fully enforced repository-wide.

The dominant failed contracts are complete calculation identity, explicit historical business time, upper-bounded historical queries, non-bypassable readiness, explicit fallback provenance, immutable configuration identity, uniform entry-point validation, separation of current projections from evidence, honest replay semantics, frozen continuation scope, monotonic publication, domain-write lease fencing, and distinct refresh-cycle identity.

## 9. Master Registry Coverage

The master registry contains all 40 required sections and stable `CALC-*`, `JOB-*`, `WRITER-*`, `FP-*`, `CLOCK-*`, `FALLBACK-*`, `SAFE-*`, and `INV-*` identifiers. It covers:

- both execution-order and verified dependency graphs;
- calculation and temporal identity;
- price, fundamental, technical, regime, Combined, Ranking, Sector, CERI, Setup/Lifecycle, alerts, IBMI, and Winner calculations;
- prediction, outcome, maturation, cohort/rescore, generation, publication, pipeline, and jobs;
- mutability, writers, compatibility, readiness, fallbacks, wall clock, fingerprints, configuration, historical semantics, evidence versus truth, invariants, root causes, safe patterns, certification, and a phased remediation blueprint.

Every Task 07 systemic finding appears directly in a register and in the root/invariant mapping. Important hashes/manifests state what they prove and do not prove. Major artifacts have mutability classifications; major job and writer families are registered.

## 10. Known Unknowns

- Live database contents and actual historical row populations.
- Deployed environment settings and effective feature-flag combinations.
- Provider runtime behavior, availability, timestamp accuracy, and correction cadence.
- Production queue concurrency, lease-loss frequency, and query plans.
- Pre-baseline artifacts produced by older code/config/migration states.
- Business intent for low-confidence IBMI/Winner use, sparse market regime, refresh cadence, and retrospective operation naming.
- Any dynamically constructed/reflection-driven writer not discoverable statically; none was found for principal tables.

These remain `UNKNOWN` or `PARTIALLY_VERIFIED`; they were not upgraded to assumptions.

## 11. Evidence Quality

Evidence quality is strongest where schema, code paths, tests, and persisted contracts agree: frozen MarketCalculationContext, canonical evidence serialization, PriceBar revision/PIT logic, technical feature cache identity, canonical Setup transition preflight, Winner frozen vectors, versioned outcomes, and generation manifests.

It is moderate where only selected entry points are strong or current projections coexist with history: regime/sector, CERI modern snapshots, Setup/Lifecycle histories, background lease fencing, and Winner maturation/publication. It is weakest for deployed/runtime facts, historical DB populations, provider behavior, fundamental effective-time provenance, Ranking profile reconstruction, alternate replay/backfill paths, and IBMI constituent eligibility.

Task 07's reported verification included repository-wide static searches, schema/migration inspection, `108 passed, 7 deselected` targeted tests, and a pure in-memory IBMI future-source probe. Task 08 performed synthesis and static document verification only; it did not rerun production behavior.

## 12. Recommended Remediation Sequencing

The dependency-aware order is:

1. Stop future/current contamination, stale-worker commits, and non-monotonic publication.
2. Make calculation identity a mandatory typed envelope.
3. Separate immutable evidence from current projections and centralize temporal upper bounds.
4. Make readiness/ineligibility non-bypassable at every consumer edge.
5. Persist one resolved effective configuration and formal fingerprint proof contracts.
6. Route pipeline/API/admin/repair/replay/backfill through one command boundary.
7. Freeze background target scope, separate refresh cycles from retries, and fence publication.
8. Implement explicitly named original-context versus current-rules reconstruction modes.

The master registry's Phase 0–7 blueprint records findings, dependencies, likely schema/API/backfill impact, and certification requirements. It intentionally contains no implementation estimate or remediation change.

## 13. Files Changed by Task 08

Task 08 created exactly:

- `docs/architecture/SWINGLENS_CALCULATION_LINEAGE_REGISTRY.md`
- `docs/audit/calculation-lineage/08_synthesis_report.md`

Tasks 01–07 were not modified. Pre-existing untracked files outside these outputs were left untouched.

## 14. Non-Mutation Confirmation

Task 08 made documentation-only changes. It did not modify application code, tests, configuration, database state, migrations, providers, jobs, queues, or any earlier audit deliverable. It did not run a production pipeline, call an external provider, enqueue work, or execute live SQL.

The authoritative implementation baseline remains `3a9d47063be996908b7d5d1cc5769e0bbd033546`.
