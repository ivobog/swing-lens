# SwingLens Release QA Checklist

Status reflects the 2026-08-06 execution on `codex/qa-populated-restore`.

| Area | Gate | Status | Evidence / remaining action |
| --- | --- | --- | --- |
| Build | Locked dependencies install | PASS | `uv sync --frozen --extra dev` completed |
| Static | Ruff | PASS | `All checks passed!` |
| Routes | Runtime route/export inventory | PASS | checker exit 0 |
| Secrets | Tracked credential-shape scan | PASS | zero findings |
| Database | Clean migration to head | PASS | `0026_technical_artifact_cache` |
| Database | Downgrade one revision and re-upgrade | PASS | `0026 -> 0025 -> 0026` on disposable DB |
| Recovery | Clean backup/restore validator | PASS | fixed runbook; validator `passed: true` |
| Recovery | Populated multi-module restore | PASS | 20 evidence/audit tables; exact row and SHA-256 parity; `/ready` healthy |
| CI evidence | JUnit and coverage artifacts | PASS | Run `31108152746`; `qa-test-reports` artifact published (94,711 bytes) |
| Regression | Non-browser suite | PASS | 1,098 passed; 6 e2e deselected |
| Regression | Complete final suite | PASS | 1,113 passed, 1 skipped, 4 warnings in 118.52 s |
| Coverage | Coverage report generated | PASS | 83.0% branch-aware CI total; XML generated |
| Golden | Scoring/ranking fixtures | PASS | focused gate: 3 passed in 0.34 s |
| Browser | Chromium and Firefox smoke | PASS | 6 passed |
| Browser | Microsoft Edge visual/interaction smoke | MANUAL | Execute M-01 |
| Accessibility | Automated basics | PASS | skip link, focus, labels, templates, responsive viewport |
| Accessibility | Screen-reader/contrast review | MANUAL | Execute M-02 |
| IB | Deterministic fake regression | PASS | read-only fake and no-order spy green |
| IB | Live paper connection | PARTIAL | Uploaded benchmarks, client-session loss/retry, cancel/resume, cache, redaction, and no-order checks passed; supervised physical Gateway disconnect remains |
| Jobs | Retry/cancel/coalesce/lease/recovery | PASS | background job, worker, fetch, and pipeline suites green |
| Regime/Sector | Advisory invariance | PASS | focused module suites included in regression |
| SLSE | Flags, state, replay, alerts, purge | PASS | full SLSE suite included in regression |
| OWPE | Point-in-time, model, outcome, reproduction | PASS | full OWPE suite included in regression |
| CERI | Manual provider, redaction, purge, outage isolation | PASS | deterministic CERI suite included in regression |
| CERI | Licensed provider certification | BLOCKED | Adapter/credential not supplied; execute M-04 if in release scope |
| Exports | Schema, UTF-8, ordering, formula safety, 413 | PASS | export suites and browser workflow green |
| Performance | Repeatable component budgets | PASS | 21 passed |
| Performance | Full scale/soak baseline | MANUAL | Execute M-05 and residual baseline work |
| Safety | No broker-order path | PASS | static scan, read-only connection tests, IB method spy |
| Defects | Open S0/S1 | PASS | none open; DEF-001, DEF-003, and DEF-004 fixed and regressed |
| Evidence | Matrix/report/manual procedures | PASS | files in `docs/qa/` |

## Release Sign-off

- QA verdict: **CONDITIONAL PASS**.
- Automated release blockers: none observed after DEF-001, DEF-003, and DEF-004 were fixed.
- Required before an unconditional release: complete M-01, M-02, the remaining M-03 drills, and M-05; complete
  M-04 only if a licensed CERI provider is included in the candidate.
- Any failed safety, leakage, golden, migration, restore, evidence-immutability, or advisory
  non-mutation check changes the decision to **FAIL / NO-GO**.
