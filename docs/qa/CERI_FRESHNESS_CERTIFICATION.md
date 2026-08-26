# CERI Freshness Certification

## Root-cause disposition

| Classification | Evidence | Severity | Fix / implications |
|---|---|---|---|
| `GLOBAL_VS_TICKER_SCOPE_MISMATCH` | Ops selected global max; ticker warnings used snapshot source IDs | High | Ops now labels provider-global scope and shows ticker coverage |
| `TIMESTAMP_SEMANTICS_BUG` | scoring mixed four dataset retrieval ages under `estimate_data_stale` | Critical | confidence now consumes estimate feed age only |
| `DEDUP_LAST_SEEN_BUG` | successful sample requests were 48/48 deduped while immutable retrieval stayed old | High | feed health reads completed ingestion run; no evidence rewrite/schema change |
| `NEGATIVE_AGE_EVENT_DATE_BUG` | 179 future earnings `published_at`; HOG produced -70 | High | future event date remains event data; Ops reads check completion |
| `CONFIG_RESOLUTION_BUG` | default loader ignored settings path | Medium | runtime settings paths are authoritative |
| `SNAPSHOT_CAPTURE_BUG` | scoped change rebuild selected prior only inside scope and reset 685 snapshot comparison metadata rows | Medium | rebuild now uses full company history for comparison context |

No evidence supports `PROVIDER_REFRESH_BUG`, `FEATURE_REBUILD_BUG`, or `ALERT_DEDUP_BUG`. Provider checks succeeded; the semantic consumers were wrong. `SNAPSHOT_CAPTURE_BUG` applies narrowly to comparison metadata corrupted by the separate scoped change-rebuild path, not to freshness snapshot values.

## Classification impact ledger

- `CORRECT_BEHAVIOR_UI_MISLEADING` / Medium: a provider-global row is a legitimate Ops dimension, but “Freshness” did not disclose global scope or ticker degradation. Code/UI: `CeriQueryService.operations_status`, `ceri_operations.html`. Tables: ingestion/source records; all 942 tracked tickers. Impact: operators saw “Fresh” beside a degraded population. Fix: explicit provider-feed scope plus fresh/stale/missing coverage. UI/API tests cover global-fresh/ticker-stale. No migration; historical UI responses are not persisted.
- `GLOBAL_VS_TICKER_SCOPE_MISMATCH` / High: global KBH source row 832869 produced the estimate Ops status while individual snapshot source IDs drove ticker warnings. Code: legacy `_database_freshness_records` versus `_confidence_freshness_days`. Tables: `ceri_source_records`, `ceri_ingestion_runs`, `ceri_score_snapshots`. Warnings affected 2,717 snapshots/642 tickers in runs 89-130; durable stale changes affected 302 tickers in runs 115, 118, 120, 123, 124, 126, 128. Fix/tests: canonical feed service, coverage test, Ops/scoring parity test. No schema migration; legacy runs remain immutable.
- `TIMESTAMP_SEMANTICS_BUG` / Critical: scoring took the oldest of four datasets' latest immutable retrieval ages and compared it with the estimates 7-day limit. The same affected runs/tickers and confidence impact apply; 27 of run 130's 332 labels move Low -> Normal and mean confidence rises 6.61 -> 7.98. Code: capture/confidence services. Tables: source, normalized evidence, revision features, score snapshots. Fix: estimate check completion only for `estimate_data_stale`; separate observation/retrieval ages. Cross-dataset isolation and parity tests added. No migration; do not rewrite scores.
- `DEDUP_LAST_SEEN_BUG` / High: sample requests returned 48 unchanged rows and deduplicated all of them, leaving immutable source retrieval timestamps unchanged; no general last-seen column exists. Code: source-record dedup plus legacy capture consumer. Tables: ingestion runs/source records. Impact overlaps the 2,717 warning snapshots. Fix/test: successful ingestion completion is feed health; unchanged-value regression added. No schema migration; existing immutable source history is preserved.
- `NEGATIVE_AGE_EVENT_DATE_BUG` / High: 179 future EODHD earnings source rows across 160 tickers used event date as `published_at`; source 340160/HOG yielded -70. Code: EODHD upcoming-earnings mapping and legacy Ops fallback. Tables: source records/earnings actuals. Impact: misleading Ops health, not Event Risk scoring. Fix/tests: upcoming event date is not publication; feed completion supplies freshness; future-event and non-negative invariant tests added. No destructive migration; optional re-ingest creates corrected superseding rows.
- `CONFIG_RESOLUTION_BUG` / Medium: no-argument config loading ignored the Settings path, although the deployed path happened to equal the default and thresholds were unaffected. Code: config loader; no data table. Fix: lazy Settings resolution; config tests retain explicit version/threshold assertions. No migration or historical impact.
- `SNAPSHOT_CAPTURE_BUG` / Medium: scoped change rebuild used only in-scope snapshots as comparison candidates. It affected 939 change rows/685 destination snapshots/433 tickers in runs 109-130; change rows retained valid lineage, so alerts were not duplicated. Code: `CeriChangeRebuildService`; tables: score snapshots/change events. Fix/test: full company history supplies comparison context while output remains scoped. No production repair was run; any metadata repair requires a separately authorized audited backfill.

`CHANGE_DETECTION_BUG`, `ALERT_DEDUP_BUG`, `PROVIDER_REFRESH_BUG`, `NORMALIZATION_FRESHNESS_BUG`, and `FEATURE_REBUILD_BUG` are rejected for the freshness incident: transitions and uniqueness are correct, provider checks completed, and normalized rows/features exist. The confidence consumer—not normalization—selected the wrong semantic dimension.

## Verification

- Investigation baseline: 404 CERI tests passed.
- TDD red: new suite failed at missing canonical freshness implementation.
- Focused post-fix: 75 passed.
- Full post-fix CERI suite before the scoped-rebuild finding: 416 passed in 25.24s. Final suite result is recorded after the additional regression test.
- Final full post-fix CERI suite: **418 passed in 23.52s**; `ruff check app/services/ceri tests/ceri` passed.
- Read-only live Ops verification: estimates/catalysts/earnings/guidance provider feed ages are non-negative; EODHD earnings changed from -70 to 0 without changing database rows.
- Live AGNC API: estimate feed age 0; evidence retrieval age 8; earnings future `published_at` ignored with `RETRIEVAL_ONLY` quality.

## Product impact

- Latest run 130: 214/332 warnings (64.46%) were false under feed freshness.
- All 303 historical stale changes were false under the canonical feed semantic; 302 historical alerts inherit that invalidity.
- 27 run-130 confidence labels would move Low -> Normal under corrected feed freshness; average confidence rises by 1.37 points. Opportunity scores are not directly freshness-weighted.
- Event Risk was not changed by these warnings.
- No duplicate stale alerts exist; refresh symmetry works at the change layer.
- The 303 stale change rows have valid comparable from/to transitions, but their destination snapshot comparison metadata was later reset by scoped rebuild; the code path is fixed and historical data was not mutated.

## Final result

- CERI freshness semantics: **PASS** (v1.3 implementation; v1.2 history remains documented legacy semantics)
- CERI DATA_STALE alerts: **PASS** for new comparable v1.3 transitions; historical v1.2 stale alerts are semantically invalid and retained for audit
- CERI Ops freshness: **PASS**
- CERI earnings freshness: **PASS**

Historical runs must remain immutable. No schema migration is required. A normal new-version run, not a historical rewrite, is the production activation boundary.
