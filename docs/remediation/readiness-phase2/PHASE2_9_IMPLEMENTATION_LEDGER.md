# Phase 2.9 implementation ledger

Baseline revision: `6656c55dd59c294c2ea471571863046e02bf7c35`

Working branch: `codex/ceri-authority-remediation`

This ledger is updated as gates complete. A stage is not marked PASS merely because
its focused tests pass; the final release decision also requires the consolidated
repository and isolated durable-pipeline gates.

| Stage | Root cause / objective | Files modified | Migrations | Tests executed and result | Safety verification | Remaining blockers | Release eligibility |
|---|---|---|---|---|---|---|---|
| A — baseline | Phase 2 depends on mixed uncommitted authority and GUI work, so a clean worktree would not reproduce the executable source | `PHASE2_9_BASELINE_MANIFEST.md`, this ledger | Verified single head 0092 | Source/status/configuration census complete | No reset, stash, database write, provider call, or history mutation | None | PASS for implementation |
| B — PostgreSQL/E2E infrastructure | Hardcoded `postgres:postgres` fallback ignored the working local configuration and caused setup authentication failures | `app/database_safety.py`; PostgreSQL fixtures; safety tests | No schema change | Pending focused PostgreSQL and E2E runs | Explicit URL for CI; loopback-only local fallback; forced admin DB; UUID disposable targets; connected identity guard; redacted diagnostics | Verification pending | Not yet eligible |
| C — 17 repository failures | Pending independent reproduction and root-cause repair | Pending | Pending | Pending | Authority enforcement may not be weakened | All 17 categories pending | Not eligible |
| D — Fundamental admission | Certify bounded low-priority-only admission for score-native Combined/Ranking edges behind a non-production version switch | Pending | Pending | Pending | No fabricated values, penalty removal, or historical mutation | Component sensitivity and activation tests pending | Shadow only |
| E — Phase 2 integration | Revalidate Warning, Winner, Pipeline, CERI, API/UI boundaries together | Pending | Pending | Pending | Append-only evidence and fail-closed unknown/integrity behavior retained | Consolidated tests pending | Not eligible |
| F — full regression | Produce new authoritative full-suite, architecture, authority, PostgreSQL, E2E, lint, and skip evidence | Pending | Pending | Pending | Mandatory gates must execute | Pending | Not eligible |
| G — isolated durable pipeline | Execute deterministic/recorded-provider cases only after Gate F is green | Pending | Pending | NOT RUN | Normal DB, live providers, IB Gateway, and historical runs prohibited | Gate F | Not eligible |
| H — release assessment | Decide non-production release candidacy and prepare an unexecuted live-provider canary specification | Pending | Pending | Pending | No merge, push, deployment, or live canary | Gates B–G | NO |

## Change ownership notes

- Phase 2.9 owns only changes explicitly described in this ledger.
- Corrections in files already modified before Phase 2.9 will be recorded by defect
  category and must preserve the pre-existing intent.
- No existing uncommitted change is assumed disposable.
