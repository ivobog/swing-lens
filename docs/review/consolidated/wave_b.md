# SwingLens Wave B Consolidated Software Review Report

**Review date:** 2026-08-02  
**Review target commit:** `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`  
**Repository:** `ivobog/swing-lens`  
**Overall status:** **Not exit-ready**  
**Quantitative trust status:** **Blocked by one Critical temporal-validity defect and several unresolved data-lineage and policy conflicts**

## 1. Purpose

This report consolidates the following Wave B phase reviews:

1. `phase5_csv_ingestion_normalization_export_safety.md`
2. `phase6_ib_market_data_integrity.md`
3. `phase7_fundamental_scoring_correctness.md`
4. `phase8_technical_indicators_pine_parity.md`
5. `phase9_combined_decisions_ranking_profiles_gates.md`

The report preserves every original finding ID, removes duplicated diagnosis, and reorganizes the work into cross-phase release gates covering the complete evidence path:

```text
CSV input
  -> canonical security identity
  -> IB contract and OHLCV evidence
  -> fundamental and technical scores
  -> combined/ranking decisions
  -> user guidance and exports
```

## 2. Executive Summary

SwingLens has strong local unit and focused integration coverage. The reviewed phase suites all passed, and many individual components are deterministic, modular, and well instrumented. The positive engineering foundation includes:

- read-only Interactive Brokers connections;
- deterministic scoring functions and bounded score ranges;
- useful missing-data and warning plumbing;
- technical confidence degradation when context is absent;
- natural-key price-bar upserts;
- market-calendar handling for common sessions and holidays;
- strong ranking-profile debug persistence;
- Jinja and export structures that are generally predictable;
- focused tests across ingestion, IB fetching, fundamentals, technical indicators, and decisions.

Wave B nevertheless remains open because the full quantitative chain is not yet trustworthy end to end.

The most serious issue is **PH8-001**, where centered pivot detection writes a future-confirmed pivot onto the original pivot bar. Any downstream score or lifecycle rule consuming those fields can receive information before it was knowable in real time.

The remaining High findings cluster around four system-level risks:

1. **Identity ambiguity:** duplicate CSV tickers, exchange-distinct instruments, and multiple IB contract matches do not use one canonical policy.
2. **Market-data ambiguity:** requirements say adjusted prices should be preferred, while implementation and tests currently prefer TRADES prices.
3. **Decision-policy drift:** fundamental labels, risk penalties, ranking profiles, position-size hints, and user-facing terminology do not share one authoritative contract.
4. **Reproducibility gaps:** no frozen TradingView parity fixture exists, combined results lack reconstructable calculation evidence, and fundamental formulas/configuration are not governed tightly enough.

### Original finding distribution

| Severity | Count |
|---|---:|
| Critical | 1 |
| High | 16 |
| Medium | 14 |
| **Total** | **31** |

## 3. Overall Readiness Decision

### Decision: No-go for quantitative release certification

SwingLens should not yet claim that:

- technical results are free of future-bar leakage;
- Python technical outputs reproduce the intended Pine implementation;
- every ticker maps to the correct listed security;
- corporate actions are handled under one approved price-series policy;
- historical results can always be reconstructed from persisted source and configuration lineage;
- duplicate CSV rows produce deterministic results across every subsystem;
- all risk labels and position-size hints enforce a consistent conservative policy;
- exported CSV files are safe to open in spreadsheet software.

### Acceptable interim use

Controlled local research remains possible only with explicit restrictions:

- do not rely on pivot-derived features for live or historical decision evaluation until PH8-001 is fixed;
- manually verify every ambiguous or unusual ticker against IB contract identity;
- reject duplicate ticker rows before upload;
- treat split-affected and dividend-adjusted histories as unverified until the price-source policy is resolved;
- review technical results when benchmark dates, timezones, or market sessions differ;
- treat all decision and size labels as advisory research annotations;
- sanitize exported CSV files before opening them in spreadsheet software;
- preserve original inputs and fetch metadata for manual audit.

## 4. Evidence and Test Baseline

| Phase | Focused test evidence | Phase conclusion |
|---|---|---|
| Phase 5 | `81 passed` | Not exit-ready |
| Phase 6 | `53 passed, 1 warning`; additional context suite `21 passed` | Not exit-ready |
| Phase 7 | `39 passed`; additional golden/view suite `54 passed` | Not exit-ready |
| Phase 8 | `73 passed`; market/sector technical suite `19 passed` | Not exit-ready |
| Phase 9 | `142 passed` | Partially exit-ready |

The focused suites overlap and must not be added together as a unique test count.

### Reproduced probes that matter most

- Formula-leading CSV values were exported unchanged.
- Duplicate CSV headers silently overwrote the earlier value.
- Duplicate ticker rows were handled inconsistently.
- Multiple IB contract candidates resolved silently to the first result.
- TRADES prices were selected when adjusted and TRADES data both existed.
- The configured stale-day value did not change freshness behavior.
- Duplicate OHLCV dates remained in the technical input.
- Relative-strength alignment crashed on timezone mismatch and no-overlap cases.
- Pivot values were backdated to their original bars.
- `Quality risk` behaved like an ordinary quality label in combined scoring.
- A `Growth trap risk` case still produced `Strong candidate` and `Full starter`.
- A configured ranking missing-data penalty was parsed but inert.

## 5. Positive Controls and Strengths

### 5.1 CSV and normalization

- UTF-8 BOM, UTF-8, and CP1252 fallback are supported.
- TradingView-style happy-path files are mapped and tested.
- Column alias precedence is deterministic for configured aliases.
- Numeric parsing handles common placeholders, NaN, Infinity, suffixes, and diagnostics.
- Earnings dates support several common formats.
- Sector normalization preserves raw and canonical status.
- Filename traversal is reduced to a basename.
- Core export column ordering is tested in several subsystems.

### 5.2 Interactive Brokers integration

- Reviewed IB connection paths use `readonly=True`.
- No application code path to common order methods was found in the phase scan.
- Request pacing and retry mechanics exist.
- Fetch runs expose progress, attempts, failures, and cancellation state.
- Repeated natural-key bars are upserted instead of duplicated.
- Common calendar behavior is tested.
- Missing benchmark context lowers confidence.

### 5.3 Fundamental engine

- Fundamentals v2 calculates ten explicit component families.
- Component and final scores are clamped to `[0, 10]`.
- Missing-data coverage and penalties are persisted.
- Warning, explanation, and debug payloads are relatively rich.
- Sparse-data probes behaved conservatively in reviewed cases.
- Focused scoring and golden pipeline tests pass.

### 5.4 Technical engine

- Most rolling-window calculations are trailing and causal.
- SMA, EMA, RMA, RSI, ATR, DMI/ADX, OBV, ROC, slopes, breakouts, stage, contraction, and climax features are modular.
- Prior breakout references commonly use `shift(1)`.
- Adaptive percentile and box/breakout paths reviewed were causal.
- Technical confidence and insufficient-data concepts already exist.
- Numerical output is locally deterministic under the lockfile.

### 5.5 Decision and ranking layer

- Combined and ranking calculations are compact and deterministic.
- Earnings blocks and danger classifications have tested override paths.
- Complete rows generally sort ahead of incomplete rows.
- Ranking profiles persist penalties, gates, component scores, and debug evidence.
- Threshold behavior is understandable and reproducible in direct probes.
- The main run page already describes size guidance as advisory.

These controls are valuable, but they do not offset the cross-phase blockers below.

# 6. Consolidated Release Gates

## 6.1 Gate A: Eliminate Future-Bar Leakage

**Severity:** Critical  
**Source finding:** `PH8-001`

### Problem

Pivot highs and lows use centered windows. The implementation identifies a pivot only after future bars confirm it, but stores the value on the original pivot bar. Higher-high and higher-low state then consumes that backdated value as though it were known at that time.

### Impact

Backtests, rankings, setup classifications, lifecycle events, and score histories can benefit from future information. This invalidates temporal correctness even when the arithmetic itself is correct.

### Required actions

1. Separate chart annotation from score-time state:
   - `pivot_*_at_pivot_bar` may identify the historical visual location;
   - `pivot_*_confirmed_value` and `pivot_*_confirmed_at` must become available only after the configured right-side confirmation bars.
2. Feed only current-time confirmed pivot state into scoring, classification, lifecycle, and probability features.
3. Trace all consumers of:
   - `pivot_high`;
   - `pivot_low`;
   - `higher_high`;
   - `higher_low`;
   - derived structure and stop/target fields.
4. Recompute affected golden results after the semantic correction.
5. Add temporal fixtures proving that adding future bars cannot alter prior score-producing rows.

### Acceptance gate

- No score-producing feature changes before its information-availability timestamp.
- A fixture with `right=3` produces no pivot-dependent score or state change until the third confirming bar.
- A repository-wide look-ahead register has no unresolved Red entries.
- Historical backtest/replay output is regenerated under the corrected semantics.

### Owner profile

Quant/backend engineer.

---

## 6.2 Gate B: Establish Canonical Security Identity

**Severity:** High  
**Source findings:** `PH5-003`, `PH6-001`, `PH7-003`

### Problem

Identity is currently reduced mainly to uppercase ticker text during upload. Different services use first-row-wins, last-value-wins, unique constraints, or silent first-contract selection. Multiple IB contract candidates are not surfaced as ambiguity.

### Impact

One research result can combine:

- company or sector data from one CSV row;
- a fundamental score from another row;
- an IB contract from the wrong exchange or share class;
- technical data for a different listed instrument.

The output can remain numerically consistent while referring to the wrong security.

### Required actions

1. Define one canonical security identity model, including the approved use of:
   - ticker;
   - exchange and primary exchange;
   - currency;
   - security type;
   - local symbol;
   - trading class;
   - IB `conId`.
2. Decide the upload duplicate policy:
   - reject;
   - quarantine;
   - deterministic best-coverage selection;
   - or exchange-qualified identities.
3. Apply the same canonical identity set before:
   - fundamental scoring;
   - IB resolution;
   - technical lookup;
   - combined/ranking calculation;
   - sector rotation;
   - setup lifecycle;
   - winner probability;
   - exports.
4. Mark multiple qualified IB contracts as `AMBIGUOUS` unless an approved deterministic policy selects one.
5. Preserve all contract candidates or sufficient candidate metadata for review.
6. Add stale-contract refresh behavior.

### Acceptance gate

- Duplicate and exchange-distinct rows cannot be silently collapsed.
- All subsystems consume the same canonical identity record.
- Multiple IB matches are visible and block technical readiness until resolved.
- Cross-system tests prove there is no first-row/last-row/first-contract drift.

### Owner profile

Backend/domain-modeling engineer.

---

## 6.3 Gate C: Approve One Market-Data Price Policy

**Severity:** High  
**Source findings:** `PH6-002`, `PH8-005`

### Problem

Requirements and design documents prefer `ADJUSTED_LAST` prices with TRADES volume. Current repository logic and a technical test prefer TRADES prices when both series exist.

### Impact

Splits and dividends can distort:

- moving averages;
- returns;
- breakouts;
- volatility and risk;
- stop and target levels;
- relative strength;
- market regime;
- sector ETF rotation;
- setup lifecycle;
- outcome labels;
- charts.

Pine parity cannot be meaningfully assessed without agreeing on the source and adjustment convention.

### Required actions

1. Approve one canonical policy:
   - adjusted OHLC with TRADES volume;
   - raw TRADES;
   - TradingView-adjusted daily bars;
   - or another explicitly defined hybrid.
2. Update requirements, code, tests, and user help together.
3. Persist in every technical artifact:
   - price source;
   - volume source;
   - adjustment type;
   - selected series policy version.
4. Add split and dividend fixtures where adjusted and raw histories intentionally diverge.
5. Include the policy in Pine parity fixture metadata.

### Acceptance gate

- One documented source policy is used by all downstream consumers.
- Split fixtures prove the selected behavior.
- Technical and decision debug output identifies the selected series.
- No test locks behavior that contradicts the approved product contract.

### Owner profile

Quant/data engineer.

---

## 6.4 Gate D: Validate OHLCV Quality and Session Alignment

**Severity:** High  
**Source findings:** `PH6-004`, `PH6-005`, `PH8-003`, `PH8-004`, `PH8-006`

### Problem

IB bars and technical frames can accept or silently normalize:

- duplicate dates;
- missing OHLC;
- invalid high/low relationships;
- gaps;
- zero or negative values;
- timezone-aware versus naive mismatches;
- non-overlapping benchmark ranges;
- partial HTF periods;
- stale or incomplete sessions.

The displayed stale-day setting is currently ignored by decision helpers.

### Impact

Malformed or misaligned data can:

- count toward readiness;
- alter rolling windows;
- crash relative-strength calculations;
- produce incomplete or misleading HTF context;
- lower or raise scores without a visible data-quality explanation.

### Required actions

1. Build a shared market-bar validator at ingestion and technical-preparation boundaries.
2. Normalize timestamps to one canonical market-session date.
3. Define deterministic policies for:
   - duplicate dates;
   - missing OHLC;
   - `high < low`;
   - close outside valid range, if enforced;
   - zero or negative prices;
   - zero volume;
   - split-like discontinuities;
   - unexpected session gaps.
4. Compare expected sessions against a market calendar.
5. Return controlled insufficient-data results for:
   - no benchmark overlap;
   - timezone mismatch;
   - insufficient RS history;
   - stale benchmark;
   - partial HTF history.
6. Expose HTF metadata:
   - source week;
   - confirmation state;
   - excluded partial week;
   - insufficient confirmed history.
7. Either implement `stale_after_days` as a true policy or remove/rename it.
8. Count valid sessions, not merely stored rows, in readiness.

### Acceptance gate

- No malformed or duplicate bar silently contributes to a score.
- Relative-strength alignment never crashes for supported mismatch cases.
- Partial HTF periods are explicitly visible.
- Freshness settings have observable tested behavior.
- Coverage summaries distinguish inserted, valid, rejected, quarantined, and missing sessions.

### Owner profile

Backend/market-data engineer.

---

## 6.5 Gate E: Create a Pine Parity Certification Harness

**Severity:** High  
**Source findings:** `PH8-002`, `PH8-007`

### Problem

No frozen multi-ticker TradingView/Pine export, fixture runner, mismatch report, or classification parity report exists. The supported NumPy/pandas range is broader than the versions under which deterministic output was reviewed.

### Impact

The following can drift while the current Python tests remain green:

- EMA and RMA seed behavior;
- RSI, ATR, DMI, and ADX warmup;
- weekly resampling;
- stop/target/RR;
- classification priority;
- nullable and timezone behavior;
- output under different pandas/NumPy versions.

### Required actions

1. Create a frozen parity fixture with:
   - ticker and exchange;
   - date;
   - Pine script version and hash;
   - TradingView/data vendor;
   - adjustment mode;
   - session timezone;
   - source date range;
   - expected indicators, scores, classifications, stops, targets, and RR.
2. Build a mismatch harness reporting:
   - ticker;
   - date;
   - field;
   - Pine value;
   - Python value;
   - absolute and relative difference;
   - tolerance;
   - source exception;
   - pass/fail.
3. Enforce exact matching for booleans, classifications, dates, and missing flags.
4. Enforce documented numerical tolerances for arithmetic and smoothed indicators.
5. Include multiple market behaviors:
   - liquid uptrend;
   - high-beta momentum;
   - failed breakout/distribution;
   - split history;
   - low/zero volume;
   - gaps and holidays;
   - duplicate dates;
   - short histories;
   - timezone alignment.
6. Either constrain NumPy/pandas to the validated lock range or run the fixture on the oldest and newest supported versions.

### Acceptance gate

- Critical approved Pine fields meet the documented parity target.
- Classification mismatches are zero unless explicitly waived with source metadata.
- CI emits a durable mismatch report.
- Package versions used in certification are recorded.

### Owner profile

Quant/test engineer.

---

## 6.6 Gate F: Unify Fundamental Risk Labels and Decision Effects

**Severity:** High  
**Source findings:** `PH7-001`, `PH7-002`, `PH9-002`, `PH9-004`

### Problem

The active fundamentals v2 model emits `Quality risk`, while product documents define `Growth trap risk`. The v2 path cannot emit the documented label. Downstream combined and ranking logic penalizes `Growth trap risk` and `Value trap risk`, but does not penalize `Quality risk`. A high-scoring `Growth trap risk` row can still produce `Strong candidate` and `Full starter`.

### Impact

Risk semantics can disappear between model output and user guidance. A row can carry a severe warning while receiving the same final treatment as an ordinary quality candidate.

### Required actions

1. Approve one fundamental label taxonomy.
2. Decide whether:
   - `Quality risk` replaces `Growth trap risk`;
   - both exist with different meanings;
   - or one is removed.
3. Define for every risk label:
   - score penalty;
   - decision cap;
   - ranking-profile behavior;
   - allowed size-hint ceiling;
   - warning severity;
   - sorting effect;
   - UI wording.
4. Apply the contract consistently across:
   - fundamentals;
   - combined decisions;
   - ranking profiles;
   - warning services;
   - exports;
   - help/glossary.
5. Add contradiction tests for excellent scores plus:
   - quality risk;
   - growth trap;
   - value trap;
   - danger technical;
   - liquidity warning;
   - high earnings risk;
   - insufficient technical confidence.

### Acceptance gate

- Every emitted label is documented and reachable.
- No severe risk label receives an unreviewed strongest-size hint.
- Product, persistence, UI, exports, and tests use the same taxonomy.
- Exact threshold and contradiction behavior is locked in tests.

### Owner profile

Quant/product-policy engineer.

---

## 6.7 Gate G: Govern Fundamental Formulas and Model Versions

**Severity:** Medium/High systemic risk  
**Source findings:** `PH7-004`, `PH7-005`, `PH7-006`

### Problem

Configured quarterly growth fields are not read by active formulas, while some coverage-priority fields affect penalties without appearing as component inputs. The YAML is loaded as a raw dictionary without complete schema, threshold, unknown-field, or version-change validation. The golden fixture covers too few representative cases.

### Impact

Fields can appear to affect scoring while being inert. Other fields can affect missing-data penalties without transparent component lineage. A material formula or threshold change can retain the same model version and pass existing tests.

### Required actions

1. Create a typed fundamentals configuration schema.
2. Reject:
   - unknown and missing weights;
   - invalid weight sums;
   - invalid threshold order;
   - duplicate fields;
   - unknown fields;
   - missing label thresholds;
   - incompatible formula/model-version combinations.
3. Mark every configured field as:
   - score-producing;
   - coverage-only;
   - warning-only;
   - metadata-only.
4. Generate the formula catalogue from code/config or centralize formulas in validated configuration.
5. Add an independent audit script that does not call the production scoring function.
6. Create a multi-row golden fixture covering:
   - clean compounder;
   - high-quality quant;
   - mixed;
   - low priority;
   - value trap;
   - approved quality/growth risk;
   - sparse data;
   - bad numeric parsing;
   - duplicate identity behavior.
7. Add monotonic property tests.
8. Require a model-version bump for changes to:
   - formulas;
   - weights;
   - thresholds;
   - labels;
   - missing-data penalties;
   - parser semantics;
   - duplicate policy.

### Acceptance gate

- Every configured scoring field has a tested directional or explicitly non-scoring role.
- Independent calculation reproduces representative scores.
- Model-changing edits cannot merge without version, fixture, documentation, and release-note updates.
- Every label and warning boundary has exact tests.

### Owner profile

Quant/backend engineer.

---

## 6.8 Gate H: Make Combined and Ranking Decisions Reconstructable

**Severity:** High  
**Source findings:** `PH9-001`, `PH9-003`, `PH9-006`

### Problem

Ranking configuration contains a `missing_data_policy.penalty` field that is parsed but not used. Combined rows persist summary outcomes but not the complete calculation path, config hash, penalty breakdown, or threshold snapshot. Boundary coverage is useful but incomplete.

### Impact

Configuration appears to control behavior when it does not. Historical combined results cannot be independently reconstructed after mutable config changes. Small threshold or rounding changes can alter user-facing labels without a focused regression failure.

### Required actions

1. Remove or correctly wire `missing_data_policy.penalty`.
2. Persist combined calculation evidence:
   - engine version;
   - config hash/version;
   - source scores;
   - weights;
   - weighted score before penalties;
   - penalty breakdown;
   - earnings calculation;
   - label thresholds;
   - decision path;
   - position-hint path;
   - sort key.
3. Add a recomputation test from persisted evidence.
4. Add parameterized below/at/above tests for:
   - combined thresholds;
   - every ranking profile threshold;
   - earnings thresholds;
   - risk-score size threshold;
   - clamping at `0` and `10`;
   - rounding near labels.
5. Test every profile’s weights, component weights, gates, penalties, and missing-data policy.

### Acceptance gate

- No parsed configuration field is inert.
- Historical decisions can be reconstructed from durable persisted evidence.
- Threshold and rounding behavior is explicitly locked.
- Combined and profile audit outputs identify why a row received its score, label, sort bucket, and size hint.

### Owner profile

Backend/quant engineer.

---

## 6.9 Gate I: Harden CSV Ingestion and Export Safety

**Severity:** High  
**Source findings:** `PH5-001`, `PH5-002`, `PH5-004`, `PH5-005`, `PH5-006`

### Problem

CSV processing and exporting have several independent weaknesses:

- formula-leading values are exported unchanged;
- duplicate headers overwrite earlier values;
- over-wide rows produce malformed raw dictionaries;
- delimiter anomalies produce vague later errors;
- rows without tickers can be silently skipped when another row is valid;
- long filenames and non-seekable upload objects are not handled safely;
- failed artifact cleanup is not centrally governed;
- exports have no shared schema version or round-trip contract.

### Impact

Malicious input can become spreadsheet-executable content. Forensic raw preservation can lose the original ticker value. Users can receive partial ingestion without clear diagnostics. Filesystem state can diverge from database state. Downstream scripts can silently break after schema changes.

### Required actions

1. Add one strict CSV loader contract:
   - approved encodings;
   - approved delimiters;
   - unique normalized headers;
   - maximum columns and rows;
   - exact row-width policy;
   - blank-row policy;
   - malformed quoting behavior;
   - skipped-row diagnostics.
2. Decide whether duplicate headers fail or receive deterministic suffixes.
3. Preserve sufficient raw evidence to reconstruct all accepted columns under the approved policy.
4. Add controlled errors for:
   - non-seekable streams;
   - long and reserved filenames;
   - permission failures;
   - oversized uploads;
   - parse failures.
5. Define failed-upload artifact retention or cleanup.
6. Create a shared CSV export helper that neutralizes cells beginning with:
   - `=`;
   - `+`;
   - `-`;
   - `@`;
   - optional leading whitespace followed by those characters.
7. Preserve unmodified semantics in JSON exports.
8. Add export schema ID/version and a manifest.
9. Add header snapshot and round-trip tests.

### Acceptance gate

- No CSV export family bypasses the common spreadsheet sanitizer.
- Duplicate headers and row-width anomalies produce deterministic outcomes.
- Accepted raw rows remain reconstructable under the documented policy.
- Upload failure leaves deterministic file/database state.
- Every export identifies its schema version.
- Accidental column changes require an intentional schema update.

### Owner profile

Backend ingestion/data-export engineer.

---

## 6.10 Gate J: Preserve Market-Data Revision Lineage and Classify IB Errors

**Severity:** High/Medium  
**Source findings:** `PH6-003`, `PH6-006`

### Problem

Price bars are overwritten in place when their hash changes. The database retains revision count and current hash, but not the prior values. IB exceptions are retried generically without a policy based on pacing, entitlement, invalid-contract, or reconnect-required classes.

### Impact

Historical technical and outcome results cannot always be reconstructed after vendor revisions. Non-retryable errors may be repeated unnecessarily, while pacing violations may not receive the correct cooldown or operator guidance.

### Required actions

1. Add an append-only price-bar revision table containing:
   - natural key;
   - revision number;
   - prior and new OHLCV values;
   - prior and new hashes;
   - fetch run/item;
   - observed timestamps;
   - source and adjustment metadata.
2. Make `ib_revision_audit_enabled` enforce actual behavior or remove the flag.
3. Link derived artifacts to:
   - source bar set/hash;
   - price-series policy;
   - relevant fetch evidence.
4. Classify IB errors into:
   - retryable pacing/timeouts;
   - reconnect-required transport failures;
   - non-retryable entitlement failures;
   - invalid or ambiguous contract failures.
5. Make conservative mode materially affect pacing and cooldown.
6. Add interrupted/repeated fetch tests and representative IB error-code fixtures.

### Acceptance gate

- Prior market-bar values remain reconstructable after revision.
- Identical refetches do not create revisions.
- Derived artifacts identify source lineage.
- Retry policy changes by error class and is visible to operators.

### Owner profile

Market-data/backend engineer.

# 7. User-Guidance Safety and Terminology

**Severity:** Medium  
**Source findings:** `PH9-004`, `PH9-005`

### Problem

Combined decisions use `Watchlist`; ranking profiles use `Watch`, `Low confidence`, and `Speculative watch`. Position-size hints such as `Full starter`, `Half starter`, and `Small probe` appear in some UI and exports without nearby research-only context.

### Required actions

1. Create one shared decision glossary.
2. For every label define:
   - producing engine;
   - meaning;
   - score or gate conditions;
   - sort bucket;
   - warning severity;
   - allowed research-size hints.
3. Rename display fields to `Research size hint`.
4. Add export metadata:
   - `guidance_type=research_hint`;
   - `execution_instruction=false`.
5. Show severe warnings and size caps together.
6. Add a “why this label” explanation from persisted calculation evidence.
7. Add a test that every emitted label is documented.

### Acceptance gate

No surface presents a position-size hint as an executable instruction, and every emitted label has one documented semantic definition.

# 8. Medium-Priority Supporting Risks

## 8.1 Upload artifact lifecycle

Source: `PH5-004`

- Cap sanitized filename length.
- Handle Windows reserved names.
- Wrap file IO errors in domain errors.
- Decide failed-upload retention versus cleanup.
- Reconcile orphaned artifacts.

## 8.2 CSV dialect support

Source: `PH5-005`

- Decide whether semicolon and tab delimiters are supported.
- Report skipped and malformed row counts.
- Distinguish delimiter failure from missing ticker mapping.

## 8.3 Export schema evolution

Source: `PH5-006`

- Version every export family.
- Publish column manifests.
- Test stable order and round-trip expectations.

## 8.4 Stale-data policy

Source: `PH6-004`

- Either implement a real grace-day setting or remove the misleading control.
- Cover early closes, DST boundaries, weekends, holidays, and current-day bars.

## 8.5 IB pacing policy

Source: `PH6-006`

- Map known IB errors to retry classes.
- Diagnose client-ID conflicts and permission failures.
- Make conservative mode observable.

## 8.6 Technical HTF metadata

Source: `PH8-006`

- Distinguish confirmed, excluded partial, and unavailable weekly evidence.
- Cover Friday-after-close and holiday-shortened weeks.

## 8.7 Dependency numerical determinism

Source: `PH8-007`

- Narrow supported package ranges or run parity fixtures across the range.

## 8.8 Decision taxonomy and boundaries

Sources: `PH9-004`, `PH9-006`

- Harmonize `Watch` and `Watchlist`.
- Lock every exact threshold and contradiction case.

# 9. Recommended Remediation Program

## Stage 0: Immediate Containment

Until blockers are closed:

1. Disable pivot-derived score influence or mark it experimental.
2. Reject duplicate upload tickers manually.
3. Require manual confirmation for multiple or unusual IB contract matches.
4. Avoid certification of split-affected results.
5. Treat benchmark/date alignment failures as incomplete, not retry-until-success.
6. Do not open exported CSV files from untrusted data without neutralization.
7. Display all position-size fields as research-only guidance.
8. Preserve input CSV, fetch evidence, and configuration files for every important run.

## Stage 1: Quantitative Stop-the-Line Fixes

Complete first:

1. Fix pivot confirmation timing.
2. Create temporal no-look-ahead tests.
3. Approve and implement the adjusted/TRADES policy.
4. Define canonical security identity and duplicate behavior.
5. Mark IB contract ambiguity.
6. Add OHLCV quality and session validation.
7. Fix relative-strength alignment failure behavior.

## Stage 2: Model and Decision Governance

1. Approve the fundamental label taxonomy.
2. Define risk-label penalties and size caps.
3. Wire or remove inert ranking configuration.
4. Add typed fundamentals configuration.
5. Create independent fundamentals calculation fixtures.
6. Persist combined config and calculation lineage.
7. Add threshold and contradiction suites.

## Stage 3: Parity and Reproduction

1. Build the Pine parity fixture and harness.
2. Add split, gap, short-history, and timezone datasets.
3. Add append-only price-bar revisions.
4. Link derived artifacts to source-bar lineage.
5. Validate or constrain NumPy/pandas versions.

## Stage 4: Ingestion, Export, and Operator Safety

1. Add strict CSV header/width/dialect validation.
2. Centralize formula-safe CSV export.
3. Add upload artifact lifecycle management.
4. Version export schemas.
5. Add IB-aware error classification and runbook diagnostics.
6. Harmonize research guidance wording.

# 10. Master Verification Plan

## 10.1 Temporal validity

- Pivot confirmation availability tests.
- Prefix-invariance tests: adding future rows cannot change prior score-producing outputs.
- Look-ahead static and dynamic register.
- Replay tests under corrected pivot semantics.

## 10.2 Canonical identity

- Duplicate identical ticker rows.
- Duplicate conflicting rows.
- Case variants.
- Share classes.
- Exchange-qualified symbols.
- ADR/native pairs.
- ETF/common-stock ambiguity.
- Non-USD results.
- Stale cached contract refresh.
- Multiple IB candidates.

## 10.3 Market-data quality

- Duplicate dates.
- Missing rows.
- Invalid OHLC.
- Zero/negative prices.
- Zero volume.
- Split discontinuity.
- Current-day incomplete bar.
- Weekend and holiday.
- Half-day and early close.
- DST transition.
- Stock/benchmark timezone mismatch.
- No-overlap benchmark.
- Stale stock with current benchmark.
- Stale benchmark with current stock.

## 10.4 Fundamental model

- Every label at, below, and above thresholds.
- Every warning threshold.
- Every configured field’s directional or non-scoring role.
- Sparse and invalid parses.
- Unit/currency edge cases.
- Monotonic property tests.
- Independent calculation audit.
- Required model-version bump.

## 10.5 Pine parity

- Frozen multi-ticker TradingView fixture.
- Exact classification match.
- Numerical tolerance report.
- Stop/target/RR parity.
- Warmup and seed rows.
- HTF confirmation cases.
- Package-version matrix or strict range.

## 10.6 Combined and ranking decisions

- Every score threshold.
- Every gate.
- Every penalty.
- Missing fundamental, technical, benchmark, and market context.
- Growth/quality/value trap contradictions.
- Danger technical with strong fundamentals.
- Liquidity warning with strong score.
- Earnings windows.
- Risk score `3.5` and `3.5001`.
- Clamping and rounding.
- Sort ties.
- Reconstruction from persisted debug evidence.

## 10.7 CSV and exports

- BOM, UTF-8, CP1252.
- Semicolon and tab according to policy.
- Duplicate headers.
- Under-wide and over-wide rows.
- Embedded newlines.
- Blank and header-only files.
- Malformed quoting.
- Oversized input.
- Long and reserved filenames.
- Non-seekable streams.
- Save and DB failure cleanup.
- Formula-leading cells in every export family.
- Schema version and round-trip tests.

## 10.8 IB lineage and retry

- Repeated identical fetch.
- Revised fetch preserving prior values.
- Interrupted fetch and resume.
- Pacing errors.
- Entitlement errors.
- Invalid contract errors.
- Disconnect/reconnect.
- Client-ID collision.
- Conservative-mode behavior.

# 11. Required Decision Records

| Decision ID | Required decision |
|---|---|
| DR-B-001 | How are pivot values represented for charts versus score-time availability? |
| DR-B-002 | What fields form the canonical security identity? |
| DR-B-003 | What is the duplicate upload ticker policy? |
| DR-B-004 | How are multiple IB contract matches resolved? |
| DR-B-005 | What price and volume series are canonical for technical calculations? |
| DR-B-006 | What market-session timezone and calendar define daily alignment? |
| DR-B-007 | What gaps, duplicates, and malformed bars are rejected, quarantined, or repaired? |
| DR-B-008 | What is the authoritative fundamental label taxonomy? |
| DR-B-009 | Which risk labels cap decisions and research-size hints? |
| DR-B-010 | Which fundamentals fields are score-producing versus coverage-only? |
| DR-B-011 | What changes require a fundamentals model-version bump? |
| DR-B-012 | What is the authoritative ranking missing-data penalty field? |
| DR-B-013 | What calculation evidence must be persisted for combined results? |
| DR-B-014 | Which CSV dialects are supported? |
| DR-B-015 | Do duplicate headers fail or receive deterministic suffixes? |
| DR-B-016 | Which spreadsheet formula-neutralization convention is used? |
| DR-B-017 | What is the failed-upload artifact retention policy? |
| DR-B-018 | What export schema compatibility promise is supported? |
| DR-B-019 | What market-bar revision history must be retained? |
| DR-B-020 | What numerical package versions are certified? |
| DR-B-021 | What wording is acceptable for research-size guidance? |

# 12. Wave B Exit Criteria

Wave B can close only when all the following are true.

## Critical exit gate

- No unresolved future-bar usage remains in score-producing features.

## Identity and source-data gates

- Duplicate ticker behavior is documented and enforced before scoring.
- Multiple IB contract matches cannot silently resolve.
- One canonical adjusted/TRADES policy is implemented and persisted.
- Malformed, duplicate, stale, and misaligned bars cannot silently produce ready status.
- Relative-strength mismatch cases degrade to explicit insufficiency.
- Partial HTF periods are visible.

## Model and parity gates

- A frozen Pine/TradingView parity fixture and mismatch report exist.
- Approved critical fields meet numerical and exact-match tolerances.
- Every fundamental label is documented and reachable.
- Every risk label has explicit downstream penalty/gate/size behavior.
- Fundamental configuration is typed and version-governed.
- Representative fundamental results can be reproduced independently.
- Supported NumPy/pandas behavior is certified or constrained.

## Decision and audit gates

- No parsed decision configuration field is inert.
- Combined results persist enough evidence for independent reconstruction.
- Every threshold, gate, penalty, and size boundary has exact tests.
- Label taxonomy is harmonized across combined results, ranking profiles, UI, and exports.
- Position-size guidance is clearly research-only on every surface.

## Ingestion and export gates

- Formula-leading CSV cells are neutralized by one shared helper.
- Duplicate headers and malformed widths have deterministic behavior.
- Upload file failures leave deterministic artifact state.
- Every export identifies a schema version.
- Core exports have header and round-trip compatibility tests.

## Market-data lineage gates

- Prior bar values remain reconstructable after revision.
- Derived artifacts identify source-bar and series-policy lineage.
- IB retry behavior is classified by error type.

# 13. Original Finding Traceability

| Original ID | Severity | Consolidated section |
|---|---|---|
| PH5-001 | High | 6.9 CSV and Export Safety |
| PH5-002 | High | 6.9 CSV and Export Safety |
| PH5-003 | High | 6.2 Canonical Security Identity |
| PH5-004 | Medium | 6.9 CSV and Export Safety |
| PH5-005 | Medium | 6.9 CSV and Export Safety |
| PH5-006 | Medium | 6.9 CSV and Export Safety |
| PH6-001 | High | 6.2 Canonical Security Identity |
| PH6-002 | High | 6.3 Market-Data Price Policy |
| PH6-003 | High | 6.10 Revision Lineage |
| PH6-004 | Medium | 6.4 OHLCV Quality and Session Alignment |
| PH6-005 | Medium | 6.4 OHLCV Quality and Session Alignment |
| PH6-006 | Medium | 6.10 IB Error Classification |
| PH7-001 | High | 6.6 Fundamental Label Contract |
| PH7-002 | High | 6.6 Fundamental Risk Effects |
| PH7-003 | High | 6.2 Canonical Security Identity |
| PH7-004 | Medium | 6.7 Fundamental Formula Governance |
| PH7-005 | Medium | 6.7 Fundamental Config Validation |
| PH7-006 | Medium | 6.7 Golden and Property Coverage |
| PH8-001 | Critical | 6.1 Future-Bar Leakage |
| PH8-002 | High | 6.5 Pine Parity Harness |
| PH8-003 | High | 6.4 Relative-Strength Alignment |
| PH8-004 | High | 6.4 Technical Bar Quality |
| PH8-005 | High | 6.3 Market-Data Price Policy |
| PH8-006 | Medium | 6.4 HTF Partial Periods |
| PH8-007 | Medium | 6.5 Numerical Dependency Determinism |
| PH9-001 | High | 6.8 Ranking Configuration Consistency |
| PH9-002 | High | 6.6 Fundamental/Decision Contradictions |
| PH9-003 | High | 6.8 Combined Decision Reconstruction |
| PH9-004 | Medium | 7 User-Guidance Terminology |
| PH9-005 | Medium | 7 Research-Only Guidance |
| PH9-006 | Medium | 6.8 Boundary Coverage |

# 14. Phase-Level Status Summary

| Phase | Status | Principal conclusion |
|---|---|---|
| Phase 5 | Not exit-ready | Happy path is solid, but raw preservation, identity, formula-safe export, and hostile-file handling need hardening |
| Phase 6 | Not exit-ready | Read-only posture is strong, but contract identity, adjusted-price policy, revision history, and bar validation are incomplete |
| Phase 7 | Not exit-ready | Scoring plumbing is useful, but labels, duplicate handling, formula/config governance, and golden coverage are insufficient |
| Phase 8 | Not exit-ready | One Critical look-ahead defect exists; Pine parity, bar quality, RS alignment, and HTF metadata are incomplete |
| Phase 9 | Partially exit-ready | Deterministic decision code exists, but missing-data policy, trap contradictions, reconstructability, and guidance taxonomy need work |

# 15. Final Assessment

Wave B shows that SwingLens is not suffering from a lack of tests or from broadly chaotic code. The trouble lives in the seams where one type of evidence becomes another:

- text ticker becomes security identity;
- IB response becomes trusted market history;
- historical bar becomes real-time-available feature;
- model label becomes risk penalty;
- score becomes position-size guidance;
- database summary becomes historical audit evidence;
- exported text becomes a spreadsheet cell.

The highest-value remediation sequence is:

1. eliminate future-data leakage;
2. establish canonical identity and price-source policy;
3. validate and align market bars;
4. certify Pine parity;
5. unify fundamental and decision risk semantics;
6. persist full calculation and market-data lineage;
7. harden CSV input and exports.

Completing those gates will transform the current strong collection of local engines into a quantitatively auditable research system.
