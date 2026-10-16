# Task 2.9.13 Final Report

## Outcome

The two Run 35 contract defects are fixed. Future evidence created with legitimate
`qqq_distribution` or `spy_distribution` warnings remains usable by Sector Rotation,
and Decision Handoff now validates both an included Regime parent and a deliberately
excluded Regime source without weakening immutable lineage checks.

Historical Run 35 was not modified, resumed, or recovered. No live provider was used
and no normal application database was mutated.

## Actual changes

- Advanced the warning catalog to `warning-taxonomy-v2`.
- Classified `qqq_distribution` and `spy_distribution` as visible, nonblocking
  strategy restrictions.
- Explicitly classified the remaining Market Regime producer vocabulary. Missing,
  stale, mixed-session, and low-confidence warnings remain blocking/fail-closed.
- Replaced the handoff validator's unconditional Sector parent-FK comparison with a
  validation of the complete frozen Regime -> Sector relationship:
  - exact Market and Sector calculation identities;
  - exact immutable evidence pointers and graph edge;
  - matching projection and immutable frozen permission;
  - matching considered Market snapshot and producer evidence ID;
  - a decision that re-evaluates identically under `REGIME_TO_SECTOR`;
  - a matching non-NULL parent for inclusion, or a NULL parent for explicit exclusion.
- Retained the existing `sector_market_parent` rejection reason for downstream error
  compatibility.
- Added disposable-PostgreSQL production-service regression coverage and refreshed
  the current Phase 2.9 source pin for the changed handoff service. Historical T14
  source-pin artifacts were not rewritten.

## Verified production behavior

The new native integration test executes:

`Market Regime -> readiness -> REGIME_TO_SECTOR -> Sector Rotation -> Setup source loading -> Decision Handoff`

With deterministic market bars producing `qqq_distribution`:

- the warning and `market_risk_off` remain present;
- Regime readiness is `READY`;
- `REGIME_TO_SECTOR` is `ELIGIBLE` and `included=true`;
- Sector Rotation persists the matching Market parent and immutable Regime evidence;
- Decision Handoff succeeds through its production validator.

With deliberately unavailable QQQ evidence:

- Regime readiness remains fail-closed as `STALE`;
- `REGIME_TO_SECTOR` is `INELIGIBLE` and `included=false`;
- Sector Rotation persists a NULL parent while retaining the exact considered Market
  snapshot, producer evidence ID, immutable source pin, and graph edge;
- Decision Handoff accepts that explicit exclusion.

## Preserved negative behavior

The integration coverage proves Decision Handoff still rejects:

- an included Regime permission paired with a NULL Sector parent;
- missing exclusion permission in both projection and immutable evidence;
- a forged producer evidence ID even when projection and immutable permission are
  forged consistently.

Unknown warnings remain blocking. Missing, stale, incomplete, or otherwise invalid
Market Regime evidence remains ineligible and cannot be converted into a valid lineage
merely by setting the Sector parent to NULL.

## Regression results

- Focused taxonomy/readiness/Sector/contextual/Winner unit regressions: **354 passed**.
- Native Run 35 cross-stage PostgreSQL regressions: **2 passed**.
- Existing transition-handoff compatibility regressions: **3 passed**.
- Downstream Sector/contextual/Winner regressions, including native PostgreSQL Winner
  permission coverage: **142 passed**.
- Market Regime, immutable Regime/Sector evidence, and temporal regressions:
  **13 passed**.
- Python compilation: passed.
- Ruff formatting and lint: passed.
- `git diff --check`: passed.
- Official Phase 2.9 T14 source-pin validator: `stale: []`.
- T14 semantic completeness validator: passed.

Only existing Starlette/httpx, Alembic configuration, and SQLAlchemy cycle warnings
were reported; none is related to this change.

## Safety verdict

Future runs are safe from the specific Run 35 false `DECISION_LINEAGE_MISMATCH` when
they generate fresh evidence under `warning-taxonomy-v2`. Existing frozen evidence,
including Run 35, retains its original readiness and fingerprints by design.

Remaining blocker: **none**.
