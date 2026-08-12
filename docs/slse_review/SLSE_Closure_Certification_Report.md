# SLSE Closure Certification Report

## 1. Release decision

**FAIL / NOT READY TO MERGE OR ACTIVATE.** The implementation and all functional certification gates pass, but the mandatory worst-compound-filter performance target does not. No SRS exception has been approved. Independent GitHub Actions, merge to `main`, and engine activation remain blocked by design.

## 2. Identity and checkpoint

| Item | Value |
|---|---|
| Branch | `codex/ibmi-phase1-correctness` |
| Starting commit | `42fa338d8b81efdd77014c3600998bef1e445075` |
| Closure state | Uncommitted reviewed working tree; not published or merged |
| Engine | `slse-1.2.0` |
| Config | `2026-08-12` |
| Snapshot schema | `slse-snapshot-1.0.0` |
| Migration head | `0034_slse_dashboard_indexes` |
| Engine YAML | `enabled: false` |
| Local runtime flags | engine, pipeline, alerts, replay, reconstruction, purge all `false` |

Pre-existing unrelated user changes in `app/services/ib_fetch_plan_service.py`, `config/ib_market_intelligence.yaml`, and `tests/test_ib_fetch_plan_service.py` were preserved and are outside this certification.

## 3. DEF-030 through DEF-034

| Defect | Disposition |
|---|---|
| DEF-030 production adapter lineage | **PASS** — executable input catalog, source/derivation registry, and prohibited-input audit cover all adapter inputs. |
| DEF-031 semantic history | **PASS** — every family uses bounded prior canonical history for temporal rules; same-current/different-history tests prove semantic effects. |
| DEF-032 confidence semantics | **PASS** — exact 30/25/20/15/10 formula; independent agreement and freshness/lineage subcomponents. |
| DEF-033 compound actionability | **PASS** — one tested precedence table covers terminal, hard-gate, evidence, confidence, market, lifecycle, earnings, and liquidity combinations. |
| DEF-034 documentation status | **PASS** — current authoritative override tables supersede forensic historical prose. |

Previously partial API/filter/sort/pagination/export/version-label/operator-scope defects are functionally closed. The remaining red item is NFR-003 scale latency, not a semantic or lineage mismatch.

## 4. Golden and PostgreSQL certification

The versioned `slse-golden-1.0.0` corpus contains exactly 25 named scenarios and seeds persisted upstream source structures before invoking the production evaluator. It covers clean and failed breakouts, pullbacks, VCP, continuation, extension, market/earnings/liquidity gates, oscillation, optional and required missing data, stale/fresh changes, sector and score acceleration, direct initial states, filtered/prolonged absence, same-day and canonical revisions, and retry/idempotency.

Final command:

```text
python -m pytest tests/setup_lifecycle tests/integration/test_slse_market_alert_vertical.py tests/integration/test_slse_golden_corpus.py tests/test_config_files.py -q
```

Result: **236 passed in 134.71 seconds** on real disposable PostgreSQL for persistence-sensitive cases. Ruff, migration head/current, metadata drift, and diff checks pass.

## 5. Natural multi-date certification

Result: **PASS** across eight chronological completed source runs, 1,796 source rows, and 1,796 lineage/DTO checks. Total evaluation time was 142.97 seconds. Natural history observed DISCOVERED, DEVELOPING, TIGHTENING, READY, FAILED, and EXPIRED; GATE_BLOCKED, NEW_FAILURE, SCORE_ACCELERATION, and SECTOR_ACCELERATION alerts were observed naturally. Golden scenarios cover naturally absent trigger/confirmation/extension states and rules.

Evidence: `evidence/slse_natural_certification_2026-08-12.json`.

## 6. Dev/QA history rebuild

Result: **PASS**. A recoverable six-table derived-data backup was created at `backups/slse_pre_rebuild_20260812.dump` before the audited purge. Only derived SLSE tables were cleared; all six upstream source-table counts remained unchanged. Ninety-five completed source runs were rebuilt chronologically.

Post-rebuild base counts were 15,172 snapshots, 2,856 episodes, 16,932 lifecycle events, 64,473 signal changes, 1,593 alerts, and 95 evaluation runs. All rebuilt snapshots have engine/config `slse-1.2.0 / 2026-08-12`, and all five historical defect signatures are zero.

Two later run-96 pipeline evaluations occurred while stale local `.env` runtime flags were still true. Those valid versioned evaluations were retained. The leakage was corrected by setting all SLSE runtime/pipeline/replay/reconstruction/purge flags false. Current counts and integrity are recorded in `evidence/slse_rebuilt_e2e_2026-08-12.json`.

## 7. Rebuilt-history Market Changes and Alert Center

Rebuilt GUI/API/DB/source checks are **PASS** for:

- combined lifecycle and signal-change streams, counts, filters, sorts and keyset pagination;
- No Material Change quick filter;
- ticker timeline chronology and version/canonical/origin labels;
- complete Alert Center filters, severity/source/state/actionability/blocker fields and pagination;
- alert review actions (`3400` acknowledged, `3446` dismissed);
- non-truncating Alert Center export parity: API total 1,598, JSON 1,598, CSV 1,598, same first ID and version identity.

Screenshots are under `output/playwright/slse-rebuilt-*.png`; structured evidence is `evidence/slse_rebuilt_e2e_2026-08-12.json`.

Market Changes functional status: **PASS**. Alert Center functional status: **PASS**.

## 8. Accessibility

Result: **PASS**. The populated SLSE/browser suite and cross-surface accessibility suite produced **19 passed in 79.37 seconds**. Checks cover landmarks/H1, named navigation and controls, table headers, color-independent state text, contrast, keyboard focus, responsive layout, and console errors. Manual keyboard proof confirmed Tab focuses “Skip to main content” and Enter moves focus to `main#main-content`.

Evidence: `evidence/slse_accessibility_2026-08-12.json`.

## 9. Performance

### Evaluator

| Tickers | Total | Capture | Post-capture | Result |
|---:|---:|---:|---:|---|
| 100 | 4.562201s | 1.270972s | 3.291229s | PASS |
| 500 | 24.374489s | 5.285582s | 19.088907s | PASS |
| 1,000 | 57.210395s | 11.356755s | 45.853640s | **PASS <=60s** |

### 110,000 snapshots / 100,000 combined changes

| Query | P50 | P95 | Target | Result |
|---|---:|---:|---:|---|
| Market Changes first page | 179.506ms | 307.075ms | 500ms | PASS |
| Deep keyset page | 204.747ms | 250.431ms | 500ms | PASS |
| No Material Change | 164.029ms | 170.688ms | 500ms | PASS |
| Ticker timeline | 22.317ms | 23.561ms | 500ms | PASS |
| Common filters | 105.022ms | 115.781ms | 500ms | PASS |
| Worst compound filter | 945.069ms | 1,183.523ms | 500ms | **FAIL** |
| Export preflight, 500 rows | 507.960ms | 707.566ms | recorded separately | Advisory |

The query path was improved from 346 database calls and multi-second responses to bounded set-based loads, combined-stream keyset pagination, revision-aware summary reuse, batch No Material Change context, and indexed dashboard paths. The compound-filter target remains red, so no exception is assumed.

Evidence: `evidence/slse_performance_certification_2026-08-12.json`.

## 10. CI, merge and activation

A dedicated `.github/workflows/slse-closure.yml` now defines PostgreSQL semantic/golden/migration, Chromium/Firefox browser/accessibility, artifact, and opt-in 1,000-ticker/100k-row performance jobs.

Local CI-equivalent status is green except for the explicit performance test: Ruff PASS, diff PASS, Alembic head/current/drift PASS, core PostgreSQL/golden 236 PASS, browser/accessibility 19 PASS. Independent GitHub Actions evidence is **NOT PRODUCED**, because publishing/PR/merge is blocked by the red mandatory gate.

Merge status: **NOT MERGED**. Activation status: **DISABLED / NOT APPROVED**. No post-activation smoke was attempted.

## 11. Remaining exception and required decision

One mandatory exception remains: worst-compound-filter P95 is above target. Closure can proceed only after either:

1. a further architecture change brings that case below 500ms and the dedicated performance workflow passes; or
2. the SRS owner explicitly approves and records a performance exception.

Only then should the branch be committed/published, independent CI run, merged to `main`, and activation deliberately approved and smoke-tested.
