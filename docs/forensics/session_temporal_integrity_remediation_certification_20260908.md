# SwingLens session and temporal integrity remediation certification

Date: 2026-09-08 (Europe/Zurich)

Branch: `codex/session-temporal-integrity-remediation`

Baseline and forensic certification commit: `006d74e0713c5896d6aa6bc518e188467d8a884d`

Implementation tip before this report: `3df713653f7d234198108312f4038a36caf377c1`

## A. Executive verdict

```text
REMEDIATION CERTIFIED: YES
SAFE FOR SEPARATE CONTROLLED CANARY: YES
```

This certification applies to new-run behavior on the remediated code. It does not certify the retained historical artifacts as temporally valid and does not authorize their repair. No canary was run in this task.

The remediation replaces subsystem-local calendar-date guesses with one immutable, versioned `MarketCalculationCutoff`. A pipeline creates and persists one cutoff and all market-sensitive stages inherit it. Standalone paths create an explicit standalone context. Repository predicates are the first temporal barrier; worker/calculation assertions are the second.

## B. Implementation summary

- Added `MarketClockService`, named timestamp policies, immutable `MarketCalculationCutoff`, exchange-session distance, bar-readiness logic, and exchange-calendar weekly confirmation.
- Added a persisted, one-per-pipeline `market_calculation_contexts` record and propagated its ID, cutoff, completed session, and versions through pipeline stages and durable job payloads.
- Bounded technical ticker, trades, SPY, QQQ, and sector inputs by one `input_as_of_session` and knowledge cutoff. Added source-session assertions, exchange-session lags, coherent price-basis selection, and temporal diagnostics.
- Namespaced technical artifacts with schema `2-temporal` and made `input_as_of_session` part of cache identity.
- Bounded Market Regime and Sector ETF repositories and calculations; freshness now uses exchange sessions.
- Replaced Setup Lifecycle upload/processing date proxies with the frozen cutoff and rejected future market/sector context.
- Separated CERI knowledge time, feature session, effective session, and versioned causal reaction session. Daily events at or after the open now react from the next exchange session; date-only events fail closed.
- Canonicalized CERI cooldown endpoints, weekly HTF confirmation, Relative Strength timestamp inputs, and IBMI requested/provider ranges.
- Added temporal-lineage columns and operational counters without fabricating legacy values.

## C. Finding-by-finding closure

| Finding | Forensic status | Remediation implemented | Test evidence | Remaining limitation |
| --- | --- | --- | --- | --- |
| STI-F001 | CONFIRMED | Normal technical loads pass `max_session` and `as_of`; work items carry the frozen session; workers reject newer rows. | Technical work/score focused lane; future-row and assertion regressions. | Historical 1,122 scores remain unchanged. |
| STI-F002 | CONFIRMED | Ticker, SPY, QQQ, and sector frames share one target session; actual sessions and exchange-session lags are exposed. | Technical work overlap, dependency-signature, and bounded-loader tests. | Exact peer lineage of old scores remains unknowable. |
| STI-F003 | PARTIALLY_CONFIRMED | Cache identity includes `input_as_of_session`; schema is `2-temporal`; legacy keys cannot match. | `test_sti_f003_artifact_key_expresses_eligible_session` and artifact-cache suite. | Old artifacts remain stored but are incompatible. |
| STI-F004 | CONFIRMED | Market Regime SPY/QQQ queries and calculation inputs are bounded and asserted. | Market Regime focused suite with deliberate future-row characterization. | Retained history was already clean and was not rewritten. |
| STI-F005 | CONFIRMED | Staleness thresholds use `trading_session_distance`. | Calendar and Market Regime policy suites. | Three old false-stale flags remain. |
| STI-F006 | PARTIALLY_CONFIRMED | Outer snapshot and inner ETF loaders share the cutoff; source sessions are validated and persisted. | `test_sti_f006_enabled_sector_etf_loader_enforces_cutoff` and Sector Rotation suites. | Historical ETF mode was disabled; no historical repair needed or performed. |
| STI-F007 | CONFIRMED | Lifecycle eligibility comes from the calculation cutoff, not upload/processing calendar dates; future context is rejected. | Setup Lifecycle source-loader/snapshot/canonicalization suites. | The 390 suspect snapshots remain historical evidence. |
| STI-F008 | CONFIRMED | CERI rebuild requests/jobs carry explicit cutoff/session/context; point-in-time reads retain `known_at <= cutoff_at`. | `test_sti_f008_feature_cutoff_is_market_session_aware` and CERI rebuild suites. | Historical default-path attribution remains incomplete. |
| STI-F009 | CONFIRMED | CERI capture derives market session through the shared clock and persists temporal lineage. | CERI capture/orchestration suites and F018 regression. | 8,319 historical labels and 137 proven newer-bar rows remain unchanged. |
| STI-F010 | CONFIRMED | Daily reaction policy is `daily-open-causal-v1`; same-day pre-event opens cannot be attributed to regular-session events. | Reaction boundary matrix and Wave 4 evidence tests. | 2,705 contaminated historical features remain. |
| STI-F011 | PARTIALLY_CONFIRMED | Effective/event session remains distinct from causal reaction start; exact-open/close daily reactions advance. | `test_sti_f010_f011_reaction_open_characterization` and versioned reaction matrix. | Event categorization policy remains deliberately separate from reaction policy. |
| STI-F012 | CONFIRMED | Cooldown endpoints use named canonical-session policy and exchange-session distance. | F012 UTC/New York, weekend, holiday, and DST regressions. | The 97 persisted historical decisions remain unchanged. |
| STI-F013 | CONFIRMED | HTF buckets require calendar-confirmed week completion and the final required daily bar. | F013 normal/holiday/early-close/partial-week tests plus Pine/HTF lane. | Extraordinary exchange closures remain limited by the shared rule-based calendar. |
| STI-F014 | PARTIALLY_CONFIRMED | Date sessions remain dates; aware timestamps normalize through New York; ambiguous naive non-midnight timestamps fail closed. | F014, Relative Strength, DST, and overlap tests. | Naive timestamp sources must declare a contract before use. |
| STI-F015 | CONFIRMED | IBMI ends at the frozen completed session, separates semantic sessions from provider scope, normalizes chunk boundaries, and post-filters exactly. | F015, IBMI adapter/orchestration, and semantic-range tests. | Raw provider row count is not available after the adapter boundary; requested calendar span and filtered rows remain auditable. |
| STI-F016 | CONFIRMED | New technical, market, sector, lifecycle, CERI, and IBMI records persist or durably expose cutoff/session/version/source lineage. | PostgreSQL migration insert and focused service suites. | Unknown legacy lineage remains null; source series IDs are durable in technical cache/debug identity rather than dedicated score columns. |
| STI-F017 | CONFIRMED | Pipeline start persists one context; executor loads it once and forwards it through all market-sensitive dependencies. | `test_sti_f017_pipeline_dependencies_have_frozen_market_cutoff` and pipeline service/executor suites. | None for the covered normal pipeline path. |
| STI-F018 | CONFIRMED | IBMI feature rebuild/capture uses the market cutoff rather than `date.today()`. | `test_sti_f018_ib_intelligence_feature_rebuild_uses_market_cutoff` and IBMI suite. | Eight historical feature labels remain unchanged. |

Closure is architectural for all 18 new-run defect classes. The four findings that were only partially confirmed for retained production are closed for their reachable remediated code paths; their historical uncertainty is not reclassified or fabricated.

## D. SRS/SDD implementation delta

The two named standalone files, `SwingLens_Session_Temporal_Integrity_SRS.md` and `SwingLens_Session_Temporal_Integrity_SDD.md`, were not present in the checkout. Their requirements were available in the task's authoritative specification and the checked-in forensic report; the classification below is against those available requirements.

| Design element | Classification | Delta |
| --- | --- | --- |
| Immutable run-wide cutoff and named clock policies | implemented as written | Thin service wraps the existing US calendar; no second calendar was introduced. |
| One persisted pipeline context | implemented as written | Separate additive table chosen over widening the pipeline row. |
| Bounded repository reads plus calculation assertion | implemented as written | Flexible test-double calls retain compatibility, but the worker assertion remains mandatory. |
| Technical coherent source state and provenance | implemented with justified modification | Nullable columns plus structured debug/cache identity avoid inventing legacy source-version values. |
| Cache cutover | implemented as written | Schema/key namespace change causes safe misses. |
| Market/Sector/Lifecycle hardening | implemented as written | Existing safe outer Sector resolver was retained; the vulnerable inner loader was bounded. |
| CERI causal reaction | implemented as written | Versioned daily-open policy; unresolved event times fail closed. |
| HTF and Relative Strength | implemented with justified modification | Existing grouping remains; eligibility and timestamp normalization are made calendar-aware. |
| IBMI semantic/provider range split | implemented as written | Provider duration remains calendar-based but semantic boundaries are sessions and results are filtered. |
| Historical repair | deferred with reason | Explicitly unauthorized and impossible to do honestly where lineage is missing. |
| Legacy-vs-bounded shadow comparison | unnecessary as an authority gate | Proven invalid behavior is blocked directly; existing technical shadow diagnostics remain non-authoritative. |

## E. Migration and provenance summary

Migration `20260908_0068_market_calculation_context.py` advances the single code head from `0067_worker_quiesce` to `0068_market_calc_context`.

It creates `market_calculation_contexts` with pipeline/upload linkage, aware cutoff, exchange timezone, latest completed session, calendar/readiness versions, reason, creation time, uniqueness for pipeline linkage, and lookup indexes. Nullable context/cutoff/session/calendar lineage is added to technical scores, Market Regime snapshots, Sector Rotation snapshots, and Setup Signal snapshots. CERI receives event/feature/reaction/prior/window/policy and cutoff/calendar fields. IBMI features receive cutoff/calendar fields.

The migration contains no update/backfill statement. Existing rows remain null where temporal truth was not already known. The disposable PostgreSQL test upgrades from `0067`, proves one head, constraints/indexes/nullability, legacy compatibility, and new-context insertion.

## F. Cache migration summary

The artifact schema version changed to `2-temporal`. The key now includes the bounded `input_as_of_session` in addition to ticker, timeframe, adjusted/trades series versions, feature/config hash, and technical engine version. An artifact made for `S+1` therefore cannot resolve under `S`; pre-remediation keys are a different namespace and miss naturally. No legacy artifact was relabeled or certified.

## G. Test evidence

The broad focused command was:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_us_market_calendar.py tests\test_technical_indicators.py tests\test_technical_work.py tests\test_technical_artifact_cache.py tests\test_pine_replica_engine.py tests\test_technical_score_v4.py tests\test_technical_scoring_v5.py tests\test_technical_v5_calibration.py tests\test_technical_v5_forensics.py tests\test_technical_v51_overlay_research.py tests\test_relative_leadership.py tests\test_market_regime.py tests\test_market_regime_command_center.py tests\test_market_regime_policy.py tests\test_market_regime_repository.py tests\test_market_participation_service.py tests\test_sector_rotation_config.py tests\test_sector_rotation_policy.py tests\test_sector_rotation_repository.py tests\test_sector_rotation_service.py tests\setup_lifecycle tests\ceri\test_effective_session_service.py tests\ceri\test_feature_rebuild_service.py tests\ceri\test_wave4_evidence_integrity.py tests\ceri\test_ceri_alert_service.py tests\ceri\test_ceri_orchestration.py tests\ceri\test_batched_workflow_v2.py tests\test_pipeline_executor.py tests\ib_market_intelligence tests\winner_probability\test_temporal_integrity.py tests\winner_probability\test_technical_point_in_time.py tests\forensics\test_session_temporal_integrity_forensics.py tests\test_session_temporal_integrity_remediation.py -q --tb=short
```

Result: `650 passed, 7 skipped, 1 warning in 88.99s`.

Full non-slow unit lane:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not slow and not integration and not e2e and not external and not destructive" --ignore=tests\e2e -q --tb=short
```

Result: `2132 passed, 155 deselected, 22 warnings in 442.46s`.

Disposable PostgreSQL 16 migration test:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_session_temporal_integrity_postgresql.py -q --tb=short
```

Result: `1 passed, 3 warnings in 6.37s`. The disposable admin URL was injected through the environment and was not printed.

Existing technical-cache and Winner temporal PostgreSQL integrations were then run on uniquely named disposable databases in the local PostgreSQL 18 cluster:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_technical_artifact_cache_postgresql.py tests\integration\test_winner_temporal_integrity_postgresql.py -q --tb=short
```

Result: `12 passed, 1 warning in 197.62s`. The first attempt to run those older integration fixtures against PostgreSQL 16 produced 12 setup failures because their Alembic helper intentionally requires the configured production-major environment; no test body ran. The guarded PostgreSQL 18 disposable rerun passed and supersedes that infrastructure mismatch.

Final IBMI/remediation targeted lane:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_session_temporal_integrity_remediation.py tests\ib_market_intelligence -m "not external" -q --tb=short
```

Result: `73 passed, 7 deselected, 1 warning in 1.00s`.

Final pipeline/remediation smoke:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pipeline_service.py tests\test_session_temporal_integrity_remediation.py -q --tb=short
```

Result: `42 passed, 1 warning in 0.66s`.

Final static checks:

```powershell
$files = @(git diff 006d74e..HEAD --name-only --diff-filter=ACM | Where-Object { $_.EndsWith('.py') })
.\.venv\Scripts\ruff.exe check $files
.\.venv\Scripts\ruff.exe format --check $files
.\.venv\Scripts\python.exe -m compileall -q app tests
```

Changed-file lint passed for all 42 changed Python files, the formatter reported `42 files already formatted`, and `compileall` passed. Repository CI has no configured static type-check step. No remediation-caused failure remains.

## H. Performance evidence

The canonical daily-bar predicate uses the existing `idx_price_bars_ticker_date` index. A read-only production `EXPLAIN ANALYZE` representative bounded query used a Bitmap Index Scan on that index, returned 797 rows, and completed in 3.841 ms (4.556 ms planning). The market cutoff is resolved once per run and exchange-session arithmetic is in-memory, so the change adds no per-ticker calendar query. Source frames remain batched and observability uses bounded labels/counters rather than row-level database writes.

## I. Production-safety evidence

Before implementation, production was database `swinglens` at `127.0.0.1:5432`, PostgreSQL 18.3, Alembic `0067_worker_quiesce`; application and worker processes were absent; queued/running jobs were zero; latest completed session was 2026-09-04. Job status counts were BLOCKED 1, CANCELLED 1056, COMPLETED 24333, FAILED 1195, PARTIAL 15865, STALE 12. Pipeline counts were CANCELLED 3, FAILED 1, FETCHING_MARKET_DATA 4, PARTIAL 131, PENDING 1.

The final read-only snapshot at approximately `2026-09-08T02:18:47+02:00` local (`2026-09-08T00:18:47Z`, `2026-09-07T20:18:47` New York) showed the same database identity, Alembic head, job counts, pipeline counts, and latest price session. All recorded business-table counts below were unchanged. No application/worker process was present. The disposable PostgreSQL 16 container, network, and volume were removed after testing; no Docker container remained.

No production migration, job enqueue, provider request, rebuild, historical repair, Winner maturation, or pipeline canary was performed. Production queries explicitly began read-only transactions.

## J. Remaining historical contamination

The certified forensic baseline remains unchanged:

- 1,122 technical scores across eight runs used one completed session too new; 1,207 peer/ticker mismatches remain potentially affected.
- 363 ahead technical artifacts remain unvalidated; none was active-certified.
- Three Market Regime snapshots retain false-stale flags.
- 390 lifecycle snapshots remain ahead/non-session, with the previously reported downstream potential impact.
- 23,152 CERI derived rows and 4,725 build states retain later labels.
- 8,319 CERI score snapshots retain later/non-session labels; 137 prove newer-bar use.
- 2,705 of 5,782 CERI price responses remain pre-event contaminated.
- 97 persisted CERI alert cooldown decisions remain different under canonical sessions.
- 6,808 technical scores retain stale weekly HTF selection.
- One IB historical metric bar remains pre-ready and all eight retained IBMI features retain premature labels.

Production row counts remained: 147 uploads; 140 pipelines; 1,235 pipeline steps; 2,262,368 price bars; 34,244 revisions; 2,972 series versions; 8,894 technical artifacts; 23,866 technical scores; 88 Market Regime snapshots; 87 Sector Rotation snapshots; 26,523 Setup Signal snapshots; 31,945 lifecycle events; 148,511 changes; 4,021 signal alerts; 57,071 CERI processing runs; 23,835 derived features; 5,782 price responses; 10,718 CERI score snapshots; 9,852 CERI changes; 1,057 CERI alerts; 11 IBMI runs; 20 request items; 80 metric bars; and 8 IBMI features.

```text
Historical remediation remains separately unauthorized.
```

## K. Final recommendation

```text
May a separate task now run the small controlled end-to-end pipeline canary?
YES
```

The recommendation is supported by a frozen persisted pipeline cutoff, bounded and asserted inputs, versioned causal reaction semantics, cache isolation, PostgreSQL migration validation, green Winner temporal regressions, the 2,132-test non-slow lane, unchanged production state, and zero remediation-related test failures. The canary must be a separately authorized, controlled task and should verify the new lineage/metrics before any broader rollout.
