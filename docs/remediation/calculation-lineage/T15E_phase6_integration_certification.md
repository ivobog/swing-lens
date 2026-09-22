# T15E rerun — final Phase-6 integration certification

## 1. New run identity and verdict

**PASS. PHASE-6 OVERALL CERTIFIED: YES.** This is a new certification of corrected T15D commit `c562f4bd6e723c8256b3d4a54c076e7f24fa786c`. It supersedes but does not erase the prior FAIL run.

## 2. Preserved failed-run history

The previous T15E run failed because `CERI-005`, `CERI-006`, `CERI-009`, and `CERI-011` disappeared from downstream handoffs. Its source hash and all four forensic artifact hashes remain recorded in the machine certificate. The corrected reconciliation preserves the historical omission and proves the current handoff accounts for every assignment.

## 3. Frozen source

HEAD `c562f4bd6e723c8256b3d4a54c076e7f24fa786c` on `codex/t15e-phase6-integration-certification`; implementation SHA-256 `9b51fd0971ea59fbe95fa8b1b0ba8f2e56a193a0ad961c98f7faebe820b7adf8` across 668 files; test SHA-256 `c506352c2fcc4a75108e7a659dbfb48bd0218da099103d0e6b597c957c2313cd` across 450 files; migration head `0083_winner_scope_truth`.

## 4. Handoff conservation

`INV-HANDOFF-001` is enforced. T15A assigned 25 findings; 25 are accounted; lost findings = 0. The negative fixture removes `CERI-005`, observes FAIL, restores it, and observes PASS.

## 5. Corrected CERI findings

CERI-005, CERI-006, CERI-009, CERI-011 are independently CLOSED. The critical-provenance cap changes the confidence label without double-penalizing the numeric score. Historical eligibility requires local possession plus external existence. Database migration identity is separate from `ceri-decision-evidence-v1`. Upcoming earnings retain exact value, candidates, source/provider/revision/content hash, external and possession times, cutoff, and frozen selection reason; D1 evidence remains pinned after D2 arrives.

## 6. Additional corrected findings

`CORE-002` is CLOSED: severe staleness applies the complete Unknown policy. `CERI-007` is CLOSED: one feed-freshness result reaches event risk and confidence without duplicating the penalty.

## 7. Scope, refresh, revision, and truth

`INV-SCOPE-001` and `INV-REFRESH-001` are repository-wide ENFORCED; `XINT-012` is CLOSED. `INV-REVISION-001` remains an honest PARTIAL at exact unavailable legacy boundaries. Pipeline S/R/P retry, resume, reclaim, continuation, zero-progress, parent/child, and checkpoint isolation pass. Winner maturation, cohort/generation, publication, and append-only truth behavior pass; `WIN-006` remains partial because nonexistent historical revision primary keys cannot be fabricated.

## 8. Provider, temporal, and currency provenance

Provider provenance is PASS for supported current paths. Temporal provenance is PASS: post-cutoff and pre-possession facts are rejected, including upcoming earnings. Currency provenance is PARTIAL only for explicit legacy raw-upload boundaries; no synthetic FX claim is made.

## 9. Algorithm deltas

All expected Phase-6 deltas map to their findings. Unexpected numeric changes = 0.

## 10. Performance

Capture 1/50 = 29/29; pipeline = 70/70; material = 29/29; upcoming-earnings provenance = 1/1; feature rebuild = 13/13. No provenance N+1 exists.

## 11. Phase-5 and negative dependencies

Phase 5 remains 252/252 callers and 220/220 operation families with zero partial authority, bypass, or unknown paths. CERI→Ranking/Setup/Winner, Setup/Lifecycle→Winner, IBMI→Winner-direct, and Sector→same-run-Ranking remain absent.

## 12. PostgreSQL, E2E, repository, and static gates

PostgreSQL: 52 passed, 117 warnings in 574.35s against disposable PostgreSQL 18.6; migrations, drift, downgrade/re-upgrade, provenance, temporal, scope/refresh, and source-bundle coverage. Browser/E2E: 28 passed, 38 warnings in 633.75s; full tests/e2e suite including Pipeline, CERI, Winner, and Windows recovery paths. Full repository: 3927 passed, 2 established skips, 38 deselected, 1672 warnings in 4678.57s; exact non-E2E/non-external repository scope on a freshly migrated disposable PostgreSQL 18.6 database. Separately excluded populated-restore opt-in: 1 established skip because local pg_dump is unavailable. Golden pipeline/ranking suites: 3 passed, 1 warning in 0.91s. Static/inventory: ruff check, changed-file format check, compileall, secret scan, route inventory, handoff conservation, sole Alembic head 0083, and git diff check passed; repository-wide format check retains the pre-existing 215-file advisory baseline. Focused certification: 767 passed, 21 warnings in 54.25s; CERI plus T15A-T15D and Phase 1-4 certification coverage. Performance: 8 passed, 882 warnings in 583.98s; capture 29/29, pipeline 70/70, material 29/29, score-change bounded 1/50, earnings provenance 1/1, feature rebuild 13/13.

## 13. Canonical 70-finding snapshot

CLOSED 58; PARTIAL 12; OPEN 0; TOTAL 70. Remaining partials: CORE-005, CORE-009, CERI-008, CERI-010, RANK-007, SETUP-004, SETUP-006, SETUP-007, SETUP-010, WIN-006, XINT-007, XINT-010. Active supported-current defects hidden under PARTIAL = 0.

## 14. Phase-7 handoff

`T15E_phase7_exact_handoff.json` is `READY_FOR_PHASE_7`. Phase 7 receives exact original-context/rule/history reconstruction targets and conditional archive-recovery targets. External governance remains separate. It must never reconstruct historical authority from current configuration, current source rows, current provider revisions, or today's latest state without independent proof.

## 15. Production safety and final verdict

No production database, provider, broker, deployed runtime, user data, backfill, rewrite, or migration was changed. **T15E PASS — PHASE-6 OVERALL CERTIFIED: YES.**
