# Task 06 — Winner Prediction and Outcome System Audit

## Audit Identity

| Field | Value | Status |
|---|---|---|
| Audit task | Task 06 only — Winner Prediction and Outcome System | **VERIFIED** |
| Repository | `C:\Users\Ivica\Documents\SwingLens` | **VERIFIED** |
| Current repository HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | **VERIFIED** |
| Implementation baseline SHA | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | **VERIFIED** |
| Branch | `codex/winner-evidence-remediation` | **VERIFIED** |
| Implementation drift | No. Baseline and current HEAD are identical; `BASELINE_DRIFT` is not raised. | **VERIFIED** |
| Audit mode | Static code, schema, SQL, configuration, migration, and test inspection plus focused automated tests. No production jobs, providers, or live database were invoked. | **VERIFIED** |
| Automated verification | `301 passed, 1 warning` for `tests/winner_probability` and `tests/test_pipeline_executor.py` | **VERIFIED** |
| Deliverable | `docs/audit/calculation-lineage/06_winner.md` only | **VERIFIED** |

## Executive Assessment

Winner prediction capture freezes its complete numerical/categorical feature vector by value in `WinnerPredictionSnapshot.feature_json` and protects it with `feature_vector_hash`. Existing probability rescoring uses that frozen vector rather than rereading Ranking, Technical, Fundamental, CERI, Setup, Lifecycle, regime, or sector tables. The original vector is therefore reconstructable without current upstream state. **VERIFIED**

The provenance envelope that explains why those frozen values were valid is incomplete. Winner capture receives only `run_id`; it does not receive or validate the parent pipeline ID, frozen `MarketCalculationCutoff`, or `MarketCalculationContext`. It derives `decision_at` and the decision session from wall-clock time, and merely copies the latest run-level Decision Handoff Manifest ID/fingerprint into JSON. Delayed capture and the historical backfill path can therefore attach current decision time to old run evidence. **VERIFIED**

Winner does not consume CERI, SetupSignalSnapshot, Setup Lifecycle episodes/evaluations, or IB intelligence features. PIPE-001 and SETUP-002/003/004/009 are closed as direct Winner contamination paths. The underlying technical insufficiency issue is also explicitly blocked: `TechnicalScore.insufficient_data = true` makes a prediction ineligible before pending outcomes or estimates are created. **VERIFIED**

Outcome maturation intentionally reads current corrected `PriceBar` rows as later outcome truth and appends outcome revisions when the bar-lineage hash changes. It does not alter the frozen decision feature vector, but it does mutate `WinnerPredictionSnapshot.entry_data_status`. Native outcome rows preserve calculated values, revision numbers, and a lineage hash/cutoff, but not the exact constituent PriceBar/PriceBarRevision IDs, so the old label remains recoverable while its complete raw price derivation is not. **PARTIALLY_VERIFIED**

Cohort generation has strong content addressing, evidence watermarks, immutable manifests, completeness checks, temporal eligibility checks, transaction/lease fencing, and generation-bound rescore output. Publication nevertheless lacks a monotonic watermark/generation fence: an older READY generation can publish after a newer one and supersede it. The automatic generation publisher's duplicate-publication check is also unreachable because it rejects already-PUBLISHED generations first. **VERIFIED**

The prior runaway maturation-child incident is substantially remediated. Active maturation roots are single-flight, zero progress stops or defers, missing prerequisites receive a retry time, and continuation depth is capped. Continuations do not freeze target IDs and omit the root's `due_session`, so the finite workflow can broaden to newer eligible rows. **VERIFIED**

## Actual Winner Prediction Input Graph

```text
UploadRun + RawCompanyRow
          │
          ├── FundamentalScore ──────────────────────┐
          ├── TechnicalScore ──────────────┐          │
          │      └── insufficiency GATE ───┤          │
          ├── CombinedResult ───────────────┤          │
          ├── RankingResult[lowest rank] ───┤          │
          ├── MarketRegimeSnapshot ─────────┤          │
          ├── SectorRotationSnapshot/Row ───┤          │
          └── Decision Handoff Manifest ─ lineage only│
                                             ▼         │
                                  frozen feature_json ◄┘
                                             │
                               WinnerPredictionSnapshot
                                             │
                     ┌───────────────────────┴────────────────────┐
                     ▼                                            ▼
            pending outcomes                         decision-time estimate
                     │                                from historical cohort
          current PriceBar truth                                  │
                     ▼                                            ▼
        versioned outcome revisions                    evidence manifest
                     │
                     ▼
      cohort watermark → generation → manifests/statistics → rescore

CERI / Setup Lifecycle / IB intelligence ──────────────X (no capture read)
```

Pipeline order is not used as evidence of dependency. The graph above is derived from the Winner repository loader, feature extractor, capture service, outcome services, and estimator. **VERIFIED**

### Input dependency classification

| Candidate input | Classification | Exact use | Status |
|---|---|---|---|
| `CombinedResult` | `DIRECT_INPUT`, `GATE` | Final score, score band, setup-family fallback, reward/risk, earnings risk, classification; existence and `is_complete` gate eligibility. | **VERIFIED** |
| `RankingResult` | `DIRECT_INPUT` | First row by `(profile_rank, ranking_profile)` supplies `decision_label` as the first setup-family choice, `ranking_profile`, and an earnings-risk fallback. Numeric score/rank are not features. | **VERIFIED** |
| `TechnicalScore` | `DIRECT_INPUT`, `GATE`, `INDIRECTLY_EMBEDDED` | Technical score, score band, trigger/action bias, reward/risk, target/stop, classification, quality; missing/`insufficient_data` excludes. It embeds prior PIT price calculations. | **VERIFIED** |
| `FundamentalScore` | `DIRECT_INPUT` | Fundamental score and coverage; fallback into some Combined-derived values. Missing is warned, not excluded. | **VERIFIED** |
| `MarketRegimeSnapshot` | `DIRECT_INPUT` | `regime`, derived regime family, and `risk_state`. No gate/permissions/sizing fields are consumed. | **VERIFIED** |
| `SectorRotationSnapshot` and row | `DIRECT_INPUT` | Row `rotation_state`, `current_rank`, and derived leadership bucket; snapshot provides source availability/as-of identity. | **VERIFIED** |
| `SetupSignalSnapshot` | `NOT_USED` | No query or source ID. | **VERIFIED** |
| Setup `LifecycleEpisode` / `LifecycleEvaluation` | `NOT_USED` | No query or field. Winner's own `WinnerPredictionEpisode` is a different dedup/training-independence concept. | **VERIFIED** |
| CERI `opportunity_score` | `NOT_USED` | No CERI query/field. | **VERIFIED** |
| CERI `event_risk_score` | `NOT_USED` | No CERI query/field. | **VERIFIED** |
| CERI `confidence` | `NOT_USED` | No CERI query/field. | **VERIFIED** |
| CERI `posture` | `NOT_USED` | No CERI query/field. | **VERIFIED** |
| CERI alerts/changes | `NOT_USED` | No CERI query/field. | **VERIFIED** |
| IB intelligence features | `NOT_USED` | No liquidity/short-pressure/options/scanner/intelligence reader in prediction capture. IB contract metadata is used only by later market-data recovery obligations. | **VERIFIED** |
| `PriceBar` / PIT price evidence | `INDIRECTLY_EMBEDDED` for prediction; `DIRECT_INPUT` for outcomes | Capture has no direct bar reader or bar IDs; technical/combined values may embed PIT bars. Maturation reads current bars as later outcome truth. | **VERIFIED** |
| `RawCompanyRow` / `UploadRun` | `DIRECT_INPUT` | Ticker, sector, filename, notes, earnings fallback and run ownership. | **VERIFIED** |
| Decision Handoff Manifest | `LINEAGE_ONLY` | Latest manifest for upload run; ID/fingerprints copied into prediction lineage JSON, not validated as capture input. | **VERIFIED** |

## CERI Completion-Boundary Risk

Winner capture reads no CERI table or output. It does not require same-run CERI, provider descendants to be terminal, or a CERI capture artifact to exist. It cannot select older/current/global CERI state because there is no selector. **VERIFIED**

The parent pipeline can reach Winner and finalization while provider-ingestion descendants scheduled by the CERI stage remain asynchronous, as established by Task 01. That remains a parent-pipeline completion-accounting problem, but Winner's feature vector and probability cannot be stale *because of CERI*. PIPE-001 is therefore closed as a Winner contamination path. **VERIFIED**

The Decision Handoff Manifest does not need a CERI member for current Winner behavior. Winner also does not validate that manifest generally; any CERI members present are irrelevant to Winner capture. **VERIFIED**

## Technical Readiness Propagation

| Winner feature/policy | Source | Readiness/confidence check | Missing/default behavior | Eligibility effect | Status |
|---|---|---|---|---|---|
| `technical_score` | `TechnicalScore.dual_score` | Row must exist; `insufficient_data` must be false. Technical confidence is not an eligibility gate. | `None` remains `None`; feature cutoff audit marks missing. | No explicit exclusion for null score if row itself exists. | **PARTIALLY_VERIFIED** |
| `dual_score_band` | Technical score | Same upstream check | Null remains no band and becomes `__MISSING__` in cohort keys. | Can still reach broader cohort levels. | **VERIFIED** |
| `trigger_state` | `TechnicalScore.action_bias` | Same upstream check | Null is retained; Winner episode uses `unknown`. | Not an exclusion. | **VERIFIED** |
| `technical_data_quality` | Technical confidence | Captured only; no low/error/unknown gate. | Null/missing retained even though schema metadata calls it required for eligible predictions. | Not an exclusion. | **VERIFIED** |
| target/stop/reward-risk | Technical, then Combined fallback where defined | No separate readiness/confidence check | Nullable | Does not independently gate prediction. | **VERIFIED** |
| Combined completeness | `CombinedResult.is_complete` | Explicit check | Missing/incomplete excludes | Hard gate | **VERIFIED** |

CORE-009 does not affect a native eligible Winner probability or native training label when `insufficient_data` is correctly set: the prediction is persisted as excluded, and `_ensure_eligible_children()` is not called, so no outcomes or decision-time estimate are created. Training policy independently rejects non-eligible predictions. **VERIFIED**

The broader low-confidence contract is only partial. A technical row with `insufficient_data = false` but low/error confidence, null core numbers, or warnings can remain eligible. Feature-schema `missingness_policy` metadata is descriptive; capture does not enforce every “required” feature. Such a prediction can fall back to missing/broader cohorts and receive a probability. **VERIFIED**

## Ranking Provenance and Propagation

Winner loads all same-run RankingResult rows, sorts by `(profile_rank, ranking_profile)`, and selects index zero. It uses:

- `decision_label` as the highest-priority source of the feature named `setup_family`;
- `ranking_profile` as a direct cohort dimension;
- `earnings_risk_level` only as a fallback after Combined;
- the exact ranking row ID as an explicit FK and in `source_ids_json`. **VERIFIED**

Winner does not consume ranking numeric score, decision score, or ordinal rank as a numeric model input. It does not check Ranking `is_complete`, warnings, `has_warning`, or Low confidence. It cannot validate profile configuration/version, calculation context, session, or cutoff because Ranking does not persist that envelope. **VERIFIED**

Task 04 propagation:

| Finding | Winner effect | Status |
|---|---|---|
| RANK-001 insufficient technical propagation | Blocked when `TechnicalScore.insufficient_data` is true, even if Combined/Ranking retained positive values. | **VERIFIED** |
| RANK-002 incomplete/synthetic profile score | Numeric score is not used, but incomplete profile name/decision label can still define cohort membership because readiness is unchecked. | **PARTIALLY_VERIFIED** |
| RANK-003 global latest liquidity | Not read directly. Any resulting change to ranking profile/rank/decision can be indirectly frozen into Winner's selected profile/label. | **INDIRECTLY_EMBEDDED** |
| RANK-004 run-only compatibility | Repeated: same run is accepted without ranking session/cutoff/context compatibility. | **VERIFIED** |
| RANK-005 profile config unrecoverable | Propagates to Winner because the profile name/row ID is stored but full profile config is not. | **VERIFIED** |
| RANK-006 mutable ranking refresh | Existing prediction feature values do not change, but the referenced Ranking row can; a later recapture conflicts or, if wall-clock date changes, creates a distinct prediction session. | **PARTIALLY_VERIFIED** |

Later Ranking mutation does not silently change an existing Winner vector because `feature_json` is by value. It can make the exact source row no longer reproduce those values, so historical source semantics are weaker than feature-vector reconstruction. **VERIFIED**

## Setup and Lifecycle Provenance

`TickerCaptureContext` defines an optional `setup_lifecycle_features` field, and the feature extractor would copy keys prefixed `setup_lifecycle_`. The production repository constructor never populates that field and does not query SetupSignalSnapshot or Setup Lifecycle tables. The dormant extension point is not an implemented dependency. **DEAD_CODE**

Winner's `WinnerPredictionEpisode` groups predictions by ticker, frozen `setup_family`, frozen `trigger_state`, and a fixed cooldown date range. It is not the mutable active SetupLifecycleEpisode audited in Task 05. **VERIFIED**

Propagation results:

- SETUP-002 older repair overwriting active Setup lifecycle state: not propagated. **VERIFIED**
- SETUP-003 insufficient technical becoming actionable Setup/Lifecycle: Setup state is not read, and Winner separately gates `technical.insufficient_data`; not propagated through Setup. **VERIFIED**
- SETUP-004 incoherent Setup historical chain: not propagated. **VERIFIED**
- SETUP-009 mutable Setup canonical/episode state: not propagated. **VERIFIED**

## Market Regime and Sector Rotation

Winner selects the latest same-run MarketRegimeSnapshot and latest same-run SectorRotationSnapshot by `as_of_date DESC, id DESC`, then the sector row matching the raw row's canonical/raw sector. There is no global fallback in Winner's repository. **VERIFIED**

The feature extractor uses market `regime` and `risk_state`, sector row `rotation_state` and `current_rank`, and derived regime/sector buckets. Missing market/sector context only warns; it does not exclude. `gate_ok`, sizing/permission fields, regime warnings/confidence, and sector evidence details are not used. **VERIFIED**

Winner rejects/omits a market or sector snapshot whose row availability/as-of is after `decision_at`. A snapshot dated after the latest completed signal session but before `decision_at` is warned and still used. Winner does not validate exact market calculation context, calendar version, calculation cutoff, evidence hash, revision, or source config hash. **VERIFIED**

Propagation assessment:

- CORE-001 sparse bullish regime can directly change `market_regime`, `market_regime_family`, and cohort selection/probability. **VERIFIED**
- CORE-002 stale Gray/bullish-policy mismatch propagates only through the stored label/risk state; Winner does not consume the retained bullish sizing/permissions. **PARTIALLY_VERIFIED**
- CORE-006 can be indirectly embedded in a same-run SectorRotationSnapshot produced using global regime fallback, although Winner itself performs no global fallback. **INDIRECTLY_EMBEDDED**
- CORE-007 cross-run prior-sector history can be embedded in sector state/rank and therefore affect cohort membership/probability. **VERIFIED**

## Decision-Time Evidence Freezing

### Storage-mode matrix

| Prediction input/output | Storage mode | Exact persisted evidence | Mutable/current dependency after capture | Status |
|---|---|---|---|---|
| Complete feature vector | Stored by value | Canonical `feature_json` + SHA-256 `feature_vector_hash` | None for rescore/cohort-key derivation | **VERIFIED** |
| Technical/Fundamental values | Stored by value; IDs in JSON | Scores, bands, quality/coverage and source IDs | Source rows may later mutate, but stored values do not | **VERIFIED** |
| Combined values | Stored by value + mutable/nullable FK | Score/band/family fallback/reward-risk/earnings values + row ID | FK source may mutate or be set null | **PARTIALLY_VERIFIED** |
| Ranking values | Stored by value + mutable FK | Selected profile/label-derived family/risk fallback + row ID | Referenced row/config may change | **PARTIALLY_VERIFIED** |
| Market regime | Stored by value + snapshot FK | Label/family/risk state + snapshot ID | No reread for rescore; source version details not frozen | **PARTIALLY_VERIFIED** |
| Sector rotation | Stored by value + snapshot FK; row ID in JSON | State/rank/bucket + IDs | No reread for rescore; full evidence not frozen | **PARTIALLY_VERIFIED** |
| Setup/Lifecycle | Not stored | None | None | **NOT_APPLICABLE** |
| CERI | Not stored | None | None | **NOT_APPLICABLE** |
| IB intelligence | Not stored | None | None | **NOT_APPLICABLE** |
| Decision-time price bars | Not stored directly | Indirect technical/combined values only | Exact constituent bars must be recovered through upstream lineage, not Winner | **PARTIALLY_VERIFIED** |
| Decision timing | Stored by value | `prediction_as_of_date`, `decision_at`, `source_data_cutoff_at`, planned entry | Values are frozen, but anchor selection may be wrong | **PARTIALLY_VERIFIED** |
| Winner prediction episode | Stored by FK and values in feature JSON | Episode ID/group dates | Episode is append-like, not Setup lifecycle state | **VERIFIED** |
| Decision-time probability | Stored by value + evidence manifest/member references | Probability/interval/statistics/config/schema/cutoff/manifest hash and exact outcome revision members | Reproduction uses exact persisted evidence rows, not current upstream features | **VERIFIED** |
| Outcome truth | Versioned; first maturity mutates pending row, later changes append revisions | Prices, returns, label, lineage hash/cutoff, supersession | Recomputed from current PriceBar state | **PARTIALLY_VERIFIED** |

The complete original prediction feature vector can be reconstructed from `feature_json` without reading mutable/current upstream state, and the hash can verify it. The exact upstream calculation context cannot be reconstructed completely because pipeline/context/calendar identity and several source config/revision identities are absent or only weakly referenced. **PARTIALLY_VERIFIED**

## Prediction Identity and Manifest

| Identity dimension | Classification | Implementation | Status |
|---|---|---|---|
| `prediction_id` | `PERSISTED` | `WinnerPredictionSnapshot.id` | **VERIFIED** |
| `run_id` | `PERSISTED` | UploadRun FK | **VERIFIED** |
| `pipeline_id` | `MISSING` | Not passed to capture or stored; handoff manifest may imply a pipeline externally but is not validated. | **VERIFIED** |
| Decision session | `PERSISTED`, `DERIVED` | `prediction_as_of_date = latest_completed_us_trading_day(decision_at)` | **VERIFIED** |
| Decision cutoff | `PERSISTED`, `DERIVED` | `decision_at` and max source availability `source_data_cutoff_at`; not the parent frozen market cutoff | **PARTIALLY_VERIFIED** |
| Market context ID | `MISSING` | No field/source ID | **VERIFIED** |
| Ranking/profile identity | `PERSISTED` / config `MISSING` | Ranking FK/JSON ID and profile name; no full profile hash/version | **PARTIALLY_VERIFIED** |
| Technical/Fundamental identity | `PERSISTED` in JSON | IDs plus values, without FKs | **PARTIALLY_VERIFIED** |
| Setup/Lifecycle identity | `NOT_APPLICABLE` | Not consumed | **VERIFIED** |
| CERI identity | `NOT_APPLICABLE` | Not consumed | **VERIFIED** |
| Model identity | `NOT_APPLICABLE` for cohort baseline prediction; optional on estimate | Current probability is cohort baseline; optional `model_version_id` exists for estimates | **VERIFIED** |
| Calculation version | `PERSISTED` | Prediction and estimate | **VERIFIED** |
| Config hash | `PERSISTED` | Winner config hash on prediction/estimate | **VERIFIED** |
| Feature schema | `PERSISTED`, `VALIDATED` | Version on prediction/estimate and compatibility gates in evidence selection | **VERIFIED** |
| Feature manifest/hash | `PERSISTED` | Feature-vector hash; no dedicated immutable prediction-manifest row | **PARTIALLY_VERIFIED** |
| Decision Handoff Manifest | `PERSISTED` in lineage, not `VALIDATED` | Latest run manifest ID/fingerprint/start-anchor fingerprint | **PARTIALLY_VERIFIED** |
| Prediction revision | `PERSISTED` | Natural identity includes revision; normal capture conflicts rather than creating a new changed revision | **VERIFIED** |
| Cohort generation | `NOT_APPLICABLE` to prediction; `PERSISTED` on rescore estimate | Generation identity belongs to statistics/estimates | **VERIFIED** |

## Prediction Formula, Model, and Eligibility

The serving baseline is not a weighted per-company formula or ML model. Winner maps the frozen prediction into hierarchical cohort keys, selects the most specific cohort satisfying sample/interval requirements, and uses a smoothed historical win probability. **VERIFIED**

For evidence weights `w_i` and labels `y_i`:

```text
effective_n = Σ w_i
wins        = Σ(w_i where y_i = true)
posterior   = (wins + prior_strength × prior_probability)
              / (effective_n + prior_strength)
```

Defaults are prior strength `20` and prior probability `0.5`. The interval is a normal approximation using `1.96 × sqrt(p(1-p)/n)`. Evidence grades apply configured effective-sample and maximum-width thresholds. **VERIFIED**

Hierarchy:

| Level | Frozen cohort dimensions | Minimum effective N | Status |
|---|---|---:|---|
| L0 | setup family, dual-score band, market risk state, sector state, ranking profile | 100 | **VERIFIED** |
| L1 | setup family, combined-score band, market risk state, sector leadership bucket | 40 | **VERIFIED** |
| L2 | setup family, combined-score band, market regime family | 25 | **VERIFIED** |
| L3 | setup family, combined-score band | 15 | **VERIFIED** |
| L4 | setup family | 15 | **VERIFIED** |
| L5 | global | 15 | **VERIFIED** |

The first hierarchy level meeting minimum N, configured maximum interval width `0.35`, and non-Insufficient grade is selected. If none qualifies, Winner persists an Insufficient estimate with no probability. One representative per Winner episode is admitted, and self/episode inclusion is rejected for generation-based rescoring. **VERIFIED**

Regularized logistic regression exists only as shadow-training support; no implemented Winner job handler serves it as the canonical prediction model. `WINNER_MODEL_TRAINING` and `WINNER_SIMILARITY_CACHE` constants exist but are absent from the implemented job-handler registry. **DEAD_CODE**

## Wall-Clock Leakage

Normal parent-pipeline Winner capture calls the capture function with only `run_id` and cancellation/progress hooks. `_capture_ticker()` independently calls `datetime.now(UTC)` for feature extraction, decision timing, and persistence unless an explicit `decision_at` is supplied. Parent pipeline capture supplies none. **VERIFIED**

Consequences:

- a delayed or resumed Winner stage derives a later decision session and planned entry than the upstream run's frozen calculation context; **VERIFIED**
- mutable same-run upstream rows updated before that later wall clock can be accepted because validation is only `source_available_at <= decision_at`; **VERIFIED**
- capture does not validate the latest run-level handoff manifest or bind it to a pipeline/context; **VERIFIED**
- historical backfill passes only `captured_at=run.processed_at/uploaded_at`, but `_capture_ticker()` ignores `captured_at` when choosing `decision_at`, so the reconstructed prediction is dated by current wall clock. **VERIFIED**

Wall-clock use in maturation (`now` → latest completed session), scheduler session keys, generation request/metrics timestamps, and publication timestamps is appropriate operational time when the underlying decision/evidence watermark is explicit. Cohort generation excludes `requested_at` from generation identity and keys the generation to the material evidence watermark and contract. **VERIFIED**

## Outcome Maturation Temporal Contract

### Horizons and price basis

| Entry model | Horizons | Decision/entry rule | Due/last session | Price basis | Status |
|---|---|---|---|---|---|
| `NEXT_OPEN` | 1, 3, 5, 10, 20 | Planned entry is the first US market open strictly after `decision_at`; entry price is that session's open. | Nth regular session with entry counted as session 1; H1 is entry day. | One complete `ADJUSTED_LAST` series if available, otherwise one complete `TRADES` series. | **VERIFIED** |
| `SIGNAL_CLOSE_DIAGNOSTIC` | 1, 3, 5, 10, 20 | Entry session is latest completed session at prediction source cutoff; entry price is close. | Same counting convention | Same basis selection | **VERIFIED** |

The required series includes every regular US trading session from entry through due, inclusive. Weekends and exchange holidays are skipped by the US market calendar. If an entry, due, or intermediate historical session is missing, the outcome remains pending with a 15-minute `retry_not_before_at`; invalid/mixed OHLC or adjustment basis is excluded. **VERIFIED**

For each complete series:

- close return is `(due close - entry price) / entry price × 100`;
- MFE uses the maximum high and MAE the minimum low over the full horizon;
- SPY and the configured sector ETF proxy are aligned to the same entry/due sessions; missing benchmark/sector series produces a warning and nullable relative result, not a failed ticker outcome;
- sector-to-ETF mapping is read from current sector-rotation configuration at maturation time. **VERIFIED**

Primary success label defaults to `+2.5%` target before `-2.0%` stop within five NEXT_OPEN sessions. High/low traversal determines first event; a same-bar target/stop conflict uses `CONSERVATIVE_STOP_FIRST`, making the primary winner false while retaining optimistic/conservative alternatives. Neither event is a non-winner. **VERIFIED**

### Decision evidence versus outcome truth

Decision-time features do not directly use maturation bars and remain frozen. Maturation intentionally reads the current corrected PriceBar projection without a historical observation cutoff. This is later outcome truth, not decision evidence. **VERIFIED**

When PriceBar hashes/revision counts change, recomputation creates a new current forward/target-stop revision and marks the prior row noncurrent/superseded. Later provider corrections can therefore relabel a prediction. Old calculated labels/returns remain in old outcome rows, and cohort evidence watermarks/generations identify specific outcome revision rows. **VERIFIED**

Native forward/target-stop outcomes store aggregate lineage hash, revision count via their rows, and source revision cutoff, but not the constituent PriceBar IDs or PriceBarRevision IDs. Exact old raw bar reconstruction is therefore not guaranteed. Compatibility replay artifacts have richer per-bar lineage, but that does not repair native outcome lineage. **VERIFIED**

Maturation mutates `prediction.entry_data_status` to MISSING, INVALID, or AVAILABLE. That field is operational and excluded from `feature_json`/feature hash, so outcome processing does not change the decision-time vector. **VERIFIED**

## Maturation Continuation Safety

| Required element | Implementation | Status |
|---|---|---|
| Root job | `enqueue_outcome_maturation_workflow()` creates/coalesces a global H5 NEXT_OPEN root and sets `root_job_id`. | **VERIFIED** |
| Parent/child | Continuation stores `parent_job_id`, inherited `root_job_id`, trigger source, and incremented depth. | **VERIFIED** |
| Continuation depth | Hard cap `1000`; default slice is at most 500 × 10 rows. | **VERIFIED** |
| Target IDs | Not frozen. Each slice queries the then-current due queue. | **MISSING** |
| Visited IDs | In-memory only within one drain invocation; not persisted across child jobs. | **PARTIALLY_VERIFIED** |
| Processed count | Counts every attempted forward outcome. | **VERIFIED** |
| Watermark | Price obligations have a series watermark; forward outcome has `last_attempted_bar_watermark` but maturation does not set/use it. No root target watermark exists. | **PARTIALLY_VERIFIED** |
| `retry_not_before` | Missing prerequisites set 15-minute retry delay; deferred roots raise `JobDeferred`. | **VERIFIED** |
| Zero-progress detection | `processed == 0` stops if blocked/pending, or defers to earliest retry. | **VERIFIED** |
| Termination | No work, no eligible work, zero progress, continuation cap, or fully drained queue terminates; deferred work waits. | **VERIFIED** |
| Repeated API clicks | Active roots coalesce through workflow/type single-flight; sequential requests after completion can start a new root, but competing active roots are database-fenced. | **VERIFIED** |

Every continuation either attempts a bounded set and changes terminal/retry state, defers, stops, or reaches a finite depth cap. The prior unbounded zero-progress chain is therefore guarded. **VERIFIED**

The continuation payload contains only limit/max-batches/continuation and omits the root's `due_session`. A session-bounded scheduled root can therefore hand off to a child that uses the child's current latest-completed session and sees newly due rows. This is bounded but not a frozen target-set workflow. **VERIFIED**

## Cohort Generation Semantics

A cohort contract contains outcome definition ID, feature schema, Winner calculation version, Winner config hash, eligibility policy version, compatibility bridge version, and `cohort-v2.2`. Material evidence watermark contains maximum forward-revision ID, target-stop-revision ID, eligibility-decision ID, training-replay ID, and temporal-validity-decision ID. Generation key is SHA-256 over contract + watermark; request time is deliberately excluded. **VERIFIED**

Generation membership criteria require, among other gates:

- prediction/source cutoff before training cutoff;
- full horizon and maturation before cutoff;
- outcome revision visible at cutoff;
- exact outcome definition/target/stop compatibility;
- eligible, native, point-in-time/temporal-valid prediction;
- compatible feature schema/calculation/config;
- production-training eligibility and quality gates;
- rolling-window membership;
- no outcome source revision later than cutoff;
- independent Winner episode and one representative per episode. **VERIFIED**

Evidence is copied into frozen DTOs during load, content-addressed in root/per-cohort manifests, and statistics are bound to generation ID. Membership does not change after publication through implemented services; later material evidence produces a different watermark and generation. **VERIFIED**

Build transitions are BUILDING → READY → PUBLISHED, with cancellation/failure and supersession states. Partial generations cannot publish; the count of materialized groups must equal the plan, L5 must exist first, and all manifest predictions are rechecked for temporal eligibility before publication. **VERIFIED**

An older generation is not fenced against a newer desired or published watermark. `publish()` locks the refresh-state row and atomically switches the pointer, but does not require `generation.watermark_hash == state.desired_watermark_hash` and does not compare generation recency. An old READY generation can therefore supersede a newer PUBLISHED generation. **VERIFIED**

## Rescore Semantics

### Generation-based latest rescore

The implemented job accepts only an explicit RUN or explicit prediction-ID scope; unbounded all-history scope is rejected. On its first slice it freezes sorted target prediction IDs (maximum 10,000), cursor, and cohort generation ID into the job payload. Subsequent slices process IDs greater than the cursor. **VERIFIED**

The job requires the generation to remain PUBLISHED and validates outcome definition, feature schema, calculation version, and config hash. Cohort keys are derived from each prediction's frozen `feature_json`; no current Ranking, Technical, Fundamental, CERI, Setup, Lifecycle, regime, sector, or price feature is reread. **VERIFIED**

Each new estimate is generation-bound and unique by prediction/outcome/kind/generation/source version. Existing estimates remain recoverable. A retrospective prediction/episode found in the generation manifest is rejected as self-inclusion. If the generation becomes non-PUBLISHED, the slice stops rather than writing against a stale generation. **VERIFIED**

### Legacy/direct latest rescore

`ProbabilityEstimator.create_latest_rescore()` remains callable with an arbitrary `as_of` cutoff and creates an immediately published, non-generation-bound estimate from cutoff-filtered evidence. It still uses the frozen prediction feature vector and does not reread current upstream feature sources. No implemented Winner background job uses this method; the job uses the generation-bound path. **LEGACY**

### Historical reconstruction/publication

Reviewed estimate replacement uses candidate estimates, exact generation/manifest hashes, explicit actor/request key, locked original/candidate sets, serving-set hashes, and one transaction to supersede originals, publish candidates, and move the generation pointer. Duplicate reviewed requests with the same request key return the persisted result. **VERIFIED**

## Generation Publication Fencing

| Scenario | Current behavior | Status |
|---|---|---|
| Older generation finishes after newer desired watermark | Older generation can publish; return value requests another continuation because desired watermark is ahead. A stale generation is temporarily active. | **VERIFIED** |
| Older READY generation attempts publish after newer PUBLISHED generation | No monotonic comparison; it can supersede the newer generation. | **VERIFIED** |
| Stale worker attempts publish | Lease guard is invoked before and after the locked pointer mutation; lost lease rolls back the transaction. PostgreSQL integration coverage exists. | **VERIFIED** |
| Duplicate automatic generation publication | Already-PUBLISHED generation fails the initial READY check; the later same-pointer idempotency branch is unreachable. | **VERIFIED** |
| Duplicate reviewed estimate publication | Request-key row returns the prior result if all hashes/generation key match; mismatch fails. | **VERIFIED** |
| Partial generation build | Publication rejects unequal planned/completed group counts; process restart resumes content-addressed generation/checkpoint. | **VERIFIED** |
| Process restart during publication | Transaction and lease fencing protect pointer/status atomicity; the generation/checkpoint is resumable before publication. | **VERIFIED** |
| Rollback | Previous generations and estimates remain stored; serving is controlled by lifecycle status and published-generation relationship. No one-call automatic rollback API was found. | **PARTIALLY_VERIFIED** |
| Published content mutation | Materializer no-ops on PUBLISHED generation and manifests/statistics are not updated by normal code. Immutability is code-governed, not a database-wide write prohibition. | **PARTIALLY_VERIFIED** |

## Automatic Scheduling and Feature-Flag Interactions

All repository defaults are false, including YAML `engine.enabled`. The flags do not form one master gate. **VERIFIED**

| Combination | Resulting behavior | Mutation possible | Status |
|---|---|---|---|
| `WINNER_PROBABILITY_CAPTURE_IN_PIPELINE=true` | Parent pipeline captures predictions/outcomes/decision estimates even if `WINNER_PROBABILITY_ENABLED=false` and YAML `engine.enabled=false`. | Predictions, episodes, pending outcomes, estimates/manifests | **VERIFIED** |
| `WINNER_PROBABILITY_ENABLED=true` only | No authoritative runtime gate was found; it is used only in rollout-readiness reporting. Routes remain mounted. | No automatic mutation by this flag alone | **VERIFIED** |
| YAML `engine.enabled=true` only | Parsed and hashed but not checked as an execution gate. | No automatic mutation by this flag alone | **VERIFIED** |
| `WINNER_PROBABILITY_AUTO_MATURATION_ENABLED=true` | Worker scheduler attempts one primary H5 drain per completed US session; active roots coalesce. | Outcome revisions, prediction entry-data status, obligations, optional downstream planner state | **VERIFIED** |
| Auto maturation + auto cohort refresh | Material target/stop changes advance watermark and queue cohort refresh. | Outcomes then cohort generations/manifests/statistics/publication | **VERIFIED** |
| `WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED=true` with V2 false | Settings validation rejects startup. | None | **VERIFIED** |
| `WINNER_COHORT_REFRESH_V2_ENABLED=true` | Latest-rescore serving is constrained to the contract's active PUBLISHED generation. It does not itself schedule refresh. | Serving-selection change only | **VERIFIED** |
| V2 false | API serves non-generation latest-rescore estimates only, ordered by cutoff/creation/id. | Legacy serving semantics | **VERIFIED** |
| Admin enabled | Local-admin mutation routes can enqueue capture, maturation, cohort refresh, rescore/backfill paths regardless of the global Winner/YAML engine flags. | All explicitly requested Winner mutations | **VERIFIED** |
| Certification runtime + either automatic flag | Settings validation rejects the configuration; unrelated auto maturation/cohort work cannot run in certification. | None | **VERIFIED** |

Additional flags/parameters include Winner admin, cohort slice limits, latest-rescore slice cap, worker enablement, and durable pipeline/worker settings. Automatic work is explicitly forbidden in certification runtime, satisfying the certification isolation invariant for configured processes. **VERIFIED**

## Immutable Versus Mutable Winner Artifacts

| Artifact | Classification | Notes | Status |
|---|---|---|---|
| Prediction feature vector | `IMMUTABLE` by implemented capture contract | Stored by value/hash; changed recapture conflicts. | **VERIFIED** |
| Prediction row | `MUTABLE_CURRENT` + revision-capable schema | `entry_data_status` mutates during outcomes; lineage is enriched before first insert. | **VERIFIED** |
| Decision evidence/source IDs | `IMMUTABLE` JSON/value snapshot, incomplete lineage | Values/hash persist; exact pipeline/context/config lineage incomplete. | **PARTIALLY_VERIFIED** |
| Temporal validity decision | `APPEND_ONLY`, `VERSIONED` | Latest validation sequence is authoritative. | **VERIFIED** |
| Winner prediction episode | `APPEND_ONLY` | Fixed dependency group/date range; used for independence. | **VERIFIED** |
| Pending forward outcome | `MUTABLE_CURRENT` until first maturity | Status/retry fields updated. | **VERIFIED** |
| Matured forward outcome | `VERSIONED` | Later lineage change appends new revision and supersedes prior. | **VERIFIED** |
| Target/stop outcome | `VERSIONED` | Same revision pattern. | **VERIFIED** |
| Market-data obligation | `MUTABLE_CURRENT` | Watermark/status/missing ranges update. | **VERIFIED** |
| Training eligibility/replay | `APPEND_ONLY`, `VERSIONED` | Supersession IDs and hashes. | **VERIFIED** |
| Cohort membership manifest | `IMMUTABLE`, content-addressed | Hash plus member rows/revision IDs. | **VERIFIED** |
| Cohort aggregate | `VERSIONED`, generation-bound | One statistic per generation/definition. | **VERIFIED** |
| Probability estimate/rescore | `APPEND_ONLY`, `VERSIONED` with mutable lifecycle status | Probability values retained; publication/supersession status changes. | **VERIFIED** |
| Cohort generation | `VERSIONED` with mutable lifecycle status | Contract+watermark content identity; status/pointer mutable. | **VERIFIED** |
| Root/per-cohort manifest | `IMMUTABLE`, content-addressed | Hash identifies exact member set. | **VERIFIED** |
| Publication pointer | `DERIVED_POINTER`, `MUTABLE_CURRENT` | Refresh-state FK switched transactionally; monotonicity gap remains. | **VERIFIED** |
| Publication request | `APPEND_ONLY` | Unique request key and reviewed hashes/result. | **VERIFIED** |
| Processing run | `APPEND_ONLY` attempt ledger with supersession | Captures job/config/checkpoint/counts. | **VERIFIED** |

## Cross-Run and “Latest/Current” Selector Audit

| Path | Selector | Cross-run/current behavior | Provenance/guard | Status |
|---|---|---|---|---|
| Prediction capture | Same-run score rows; latest same-run regime/sector/handoff; first ranked profile | No global source fallback, but latest rows may belong to a later same-run recalculation and handoff is not pipeline-bound | Values frozen; compatibility incomplete | **VERIFIED** |
| Maturation | Global current due queue and current PriceBars | Outcomes from all runs are intentionally eligible; latest-corrected price truth can relabel | Exact prediction/outcome IDs and versioned revisions | **VERIFIED** |
| Revision check | Current matured outcomes, optional explicit IDs | Global by design | Versioned outcome rows | **VERIFIED** |
| Cohort refresh | Current material max-ID watermark across eligible runs | Cross-run historical training is intended and contract/watermark-bound | Frozen generation/manifests | **VERIFIED** |
| Latest rescore | Explicit run/IDs; selected published generation | No silent all-history target scope; training evidence is cross-run by cohort design | Target IDs/cursor/generation persisted | **VERIFIED** |
| API serving with V2 | Current published pointer for exact contract | No older generation should serve, subject to publication monotonicity defect | Generation status/pointer predicate | **PARTIALLY_VERIFIED** |
| API serving without V2 | Latest non-generation estimate by cutoff/created/id | Legacy current/latest semantics | Query ordering only | **LEGACY** |
| Historical backfill | Explicit runs, but decision anchor defaults to current time | Old run evidence receives current decision/session semantics | Reconstruction flags prevent native training, but temporal identity is wrong | **VERIFIED** |

## Winner Artifact Lineage

| Artifact | Created by | Inputs | Decision-bound | Session-bound | Generation-bound | Immutable | Can be recalculated | Consumers | Status |
|---|---|---|---|---|---|---|---|---|---|
| Prediction snapshot | Capture service | Same-run raw/core/rank/regime/sector | By stored `decision_at`, not parent cutoff | Yes, wall-clock derived | No | Feature vector yes; row partly mutable | Changed same identity conflicts | Outcomes, estimator, APIs, cohorts | **PARTIALLY_VERIFIED** |
| Temporal validity decision | Capture/validation | Prediction timing/lineage | Yes | Entry session | No | Append-only | New validation sequence | Eligibility/evidence/publication | **VERIFIED** |
| Winner episode | Episode service | Ticker/family/trigger/date | Yes | Fixed range | No | Append-like | Reused by key | Independence/dedup | **VERIFIED** |
| Forward outcome | Pending/maturation/revision | Prediction + current bars | References frozen prediction | Entry/due | No | Versioned | Yes | Target-stop, evidence, UI | **VERIFIED** |
| Target/stop label | Maturation/revision | Forward outcome + ticker OHLC + definition | References prediction | Full horizon | No | Versioned | Yes | Training/cohorts | **VERIFIED** |
| Evidence manifest | Manifest service | Exact prediction/outcome/label revision members | Cutoff-filtered | Via members | Shared or generation-bound | Content-addressed | Recreated to same hash | Statistics/reproduction | **VERIFIED** |
| Cohort statistic | Materializer/estimator | Manifest members + priors | Training cutoff | Completed outcomes | V2 yes | Code-immutable | New generation | Rescore | **VERIFIED** |
| Decision-time estimate | Capture estimator | Frozen prediction + pre-cutoff historical evidence | Yes | Via cutoff | Legacy baseline no | Append-like | Separate replacement | API/publication | **VERIFIED** |
| Latest rescore | Rescore job | Frozen prediction + published generation statistic | Original vector | Original prediction session | Yes | Append-like | New generation | API/publication | **VERIFIED** |
| Cohort generation | Generation/materializer | Contract + watermark + manifests/statistics | Training cutoff | Evidence outcome sessions | Self | Content immutable after READY by code | New key for new evidence | Serving pointer/rescore | **PARTIALLY_VERIFIED** |
| Publication pointer | Generation/estimate publisher | Validated READY generation | No prediction decision mutation | Not applicable | Points to one generation | No | Atomic switch | API serving | **PARTIALLY_VERIFIED** |

## Winner Job Register

| Job | Trigger | Parent | Children | Target set | Generation | Continuation | Mutation surface | Can affect historical prediction | Guard | Risk |
|---|---|---|---|---|---|---|---|---|---|---|
| Prediction capture | Pipeline/admin/job | Pipeline or root job | Outcomes/estimate materialized inline | Run tickers | No | Ticker loop/checkpoint | Predictions, episodes, outcomes, estimates | Can create new historical reconstruction | Duplicate hash conflict, lease/cancel | Wall-clock/context defect |
| Outcome maturation | Scheduler/admin | Root/previous child | Child maturation jobs | Dynamic global H5 due queue | No | Child with depth cap | Outcomes, prediction entry-data status, planner state | Outcome truth only | Single flight, retries, zero-progress stop | Child scope broadens |
| Outcome revision check | Admin/job | Root | Optional cohort refresh | Current matured outcomes or explicit IDs | No | Limit only | Outcome revisions/watermark | Relabels outcome truth | Append revisions | Native raw-bar lineage incomplete |
| Cohort refresh V2 | Maturation/revision/admin | Root job | Same job deferred/resumed | Contract watermark evidence universe | Yes | Checkpoint/slice defer | Generations, manifests, definitions, statistics, pointer | Changes serving training generation | Completeness, lease, temporal audit | Non-monotonic publish |
| Latest rescore | Admin/job | Root | Same job deferred/resumed | Frozen RUN/explicit IDs | Required | Cursor/slice | New estimates | Changes latest score, not vector | Contract/generation/status/self-inclusion | Serving affected by pointer defect |
| Historical backfill | Admin/job | Root | Inline capture children | Explicit trusted runs | No | Run limit | Reconstructed predictions/outcomes/estimates | Yes | Trust/quality/training exclusion | Current decision clock |
| Reviewed estimate publication | Explicit script/service | Manual reviewed request | None | Frozen manifest mappings | Yes | None | Estimate/generation statuses and pointer | Replaces serving historical estimates | Hashes, locks, active-job fence, atomic transaction | Strongly guarded |
| Model training | No implemented handler | None | None | N/A | N/A | N/A | None through worker | No | Disabled by absence | **DEAD_CODE** |
| Similarity cache | No implemented handler | None | None | N/A | N/A | N/A | None through worker | No | Disabled by absence | **DEAD_CODE** |
| Cleanup | No destructive Winner cleanup job found | N/A | N/A | N/A | N/A | N/A | None | No | Permanent-retention config | **NOT_APPLICABLE** |
| Recovery | Generic job recovery + generation resume | Existing job/generation | Same attempt/continuation | Persisted job/generation state | Preserved where applicable | Lease/checkpoint | Processing ledger and resumed writes | Potentially, but fenced | Execution token/lease | Publication ordering gap remains |

## Winner Parameter Register

| Parameter | Purpose | Source | Value/default | Generation/version bound | Effect | Status |
|---|---|---|---|---|---|---|
| Feature schema | Prediction vector contract | YAML | `owpe-features-1.0.0` | Prediction/generation/estimate | Compatibility and identity | **VERIFIED** |
| Calculation version | Winner calculation contract | YAML | `owpe-calc-1.1.0` | Prediction/outcome definition/generation | Compatibility | **VERIFIED** |
| Entry model | Production execution | YAML | `NEXT_OPEN` | Outcome definition | Entry open/session | **VERIFIED** |
| Diagnostic entry | Nonproduction comparison | YAML | `SIGNAL_CLOSE_DIAGNOSTIC` | Outcome definition | Signal-session close | **VERIFIED** |
| Horizons | Forward evaluation windows | YAML | 1, 3, 5, 10, 20 sessions | Outcome rows | Due dates/returns | **VERIFIED** |
| Primary label | Winner success | YAML/outcome definition | +2.5% before -2.0%, H5 NEXT_OPEN | Version/config bound | `primary_winner` | **VERIFIED** |
| Same-bar policy | Intrabar ambiguity | YAML/outcome definition | `CONSERVATIVE_STOP_FIRST` | Definition bound | Same-bar label false | **VERIFIED** |
| Episode cooldown | Independence/dedup | YAML | 5 sessions | Prediction config hash | Training membership | **VERIFIED** |
| Prior strength/probability | Bayesian smoothing | YAML | 20 / 0.5 | Generation/config | Probability | **VERIFIED** |
| Rolling window | Historical evidence age | YAML | 5 years | Generation/config | Membership | **VERIFIED** |
| Max cohort interval width | Cohort eligibility | YAML | 0.35 | Generation/config | Hierarchy fallback | **VERIFIED** |
| L0–L5 minimum N | Hierarchy eligibility | YAML | 100/40/25/15/15/15 | Generation/config | Selected cohort | **VERIFIED** |
| Evidence grades | Confidence labels | YAML | High 100/.20; Medium 40/.30; Low 15/.40 | Config bound | Display/eligibility | **VERIFIED** |
| Maturation batch/max batches | Bounded work | Scheduler/job payload | 500 / 10 | Job only | Up to 5,000 visits/slice | **VERIFIED** |
| Continuation cap | Runaway safeguard | Code | 1000 | Code version only | Hard termination | **VERIFIED** |
| Missing-data retry | Backoff | Code | 15 minutes | Code version only | Defers pending outcomes | **VERIFIED** |
| Cohort slice limits | Bounded materialization | Settings | 100 groups / 45 seconds | Job/settings | Checkpoint frequency | **VERIFIED** |
| Rescore slice cap | Bounded estimates | Settings | 250, max 500 | Job/settings | Cursor progress | **VERIFIED** |
| Rescore target cap | Safety | Code | 10,000 IDs | Job payload | Rejects oversized manifest | **VERIFIED** |
| Cohort algorithm | Generation identity | Code | `cohort-v2.2` | Contract/generation | Generation key | **VERIFIED** |

## Winner Temporal Matrix

| Artifact/calculation | Decision session | Evidence cutoff | Outcome session | Latest permitted input | Revision semantics |
|---|---|---|---|---|---|
| Prediction vector | Wall-clock-derived latest completed session | `decision_at`; stored source max timestamp | N/A | Source availability `<= decision_at` | Frozen hash; capture conflict on same identity/hash mismatch |
| NEXT_OPEN forward outcome | Prediction session, entry first open after decision | Outcome calculation wall clock only for due eligibility | Entry through H1/H3/H5/H10/H20 due session | Current PriceBars bounded by entry/due dates | First row filled; later lineage change appends revision |
| Signal-close diagnostic | Latest completed at prediction source cutoff | Same | Signal session through horizon | Same | Same |
| Target/stop label | Same prediction/entry | Outcome source-revision cutoff | First target/stop event or due | Full current corrected horizon series | Append revision on lineage change |
| Decision-time estimate | Prediction source cutoff | Strictly matured before source cutoff | Historical outcomes due before cutoff | Outcome/revision visible at cutoff | Estimate and manifest append-like |
| Cohort generation | N/A | Watermark observation + 1µs and exact revision-ID watermark | Matured sessions before cutoff | Contract-compatible visible revisions | New generation per watermark |
| Latest rescore | Original prediction session | Published generation cutoff | Historical cohort outcome sessions | Frozen prediction + published generation | New generation-bound estimate |
| Historical backfill | Incorrectly current unless explicit decision passed | Old run source timestamps | Future of current decision | Current same-run rows | Reconstructed/training-blocked, but temporal anchor defective |

## Contamination Boundary

Forbidden decision-feature influences are future bars, later Ranking/Technical/Fundamental/Combined changes, later CERI/Setup/Lifecycle state, later regime/sector state, provider revisions after decision cutoff, or a model/config not explicitly identified. Winner's stored vector prevents later readers from silently substituting most current upstream state. **VERIFIED**

The open contamination boundary is capture time itself: because the parent cutoff/context is not supplied, values already updated by the later wall-clock decision time can enter a newly captured vector for an old or delayed run. **VERIFIED**

Outcome corrections are allowed to change outcome truth and labels through explicit revisions. They must not update `feature_json`; current code respects that boundary. New outcome truth should flow to a new cohort generation and rescore, not mutate a historical probability estimate. Current generation/estimate design largely follows this rule. **VERIFIED**

## Formal Findings

### WIN-001 — Winner capture is wall-clock-bound instead of parent-context-bound

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Prediction capture / historical backfill
- **Finding:** Parent and job capture pass only `run_id`; `_capture_ticker()` uses `datetime.now(UTC)` for feature and decision timing. Historical backfill passes an old `captured_at` but not `decision_at`, and that old timestamp is ignored when selecting the feature cutoff/session.
- **Evidence:** `app/services/pipeline_executor.py:734-760`, `app/services/pipeline_executor.py:1984-2022`, `app/services/winner_probability/capture_service.py:274-350`, `app/services/winner_probability/feature_extractor.py:89-140`, `app/services/winner_probability/backfill.py:181-219`.
- **Why it matters:** Decision session and permitted source state must come from the original calculation context, not execution delay.
- **Potential contamination/correctness effect:** Delayed/resumed capture can admit later same-run mutations and produce a different session/planned entry; historical backfill can assign today's decision semantics to old evidence.
- **Existing guard:** Source timestamps after chosen `decision_at` are rejected; vector/timing are persisted and hashed; reconstructed history is normally training-blocked.
- **Missing guard:** Mandatory parent cutoff/context/pipeline ID and use of original historical decision time.
- **Recommended future remediation:** Require an immutable decision context object on every capture path, derive session/entry from it, and make backfill pass/recover the original cutoff explicitly.

### WIN-002 — Decision Handoff Manifest is recorded but not validated

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Capture provenance
- **Finding:** Winner loads the latest handoff manifest by upload run and copies its ID/fingerprints into JSON, but does not assert its pipeline/context/cutoff or compare its exact input IDs/hashes with the rows used by Winner.
- **Evidence:** `app/services/winner_probability/repository.py:52-124`, `app/services/winner_probability/feature_extractor.py:225-270`, `app/services/winner_probability/capture_service.py:425-493`.
- **Why it matters:** Same run does not prove one temporal/version envelope.
- **Potential contamination/correctness effect:** Recalculated Ranking/Combined/regime/sector rows can be frozen under lineage that appears to reference an earlier handoff; pipeline/context/session compatibility cannot be proved later.
- **Existing guard:** Same-run queries, source availability audit, exact source IDs, by-value feature vector, and manifest fingerprint copy.
- **Missing guard:** Required manifest FK/identity and fail-closed comparison against the exact Winner input graph.
- **Recommended future remediation:** Bind capture to the parent pipeline/context and validate a Winner-specific decision manifest containing exact source identities/configs/revisions before insert.

### WIN-003 — Ranking readiness and profile configuration are not enforced

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Feature extraction / cohort membership
- **Finding:** Winner selects the first ranked profile and uses its decision label/profile in probability-driving cohort keys without checking completeness, warnings, low confidence, session/cutoff, or exact profile config.
- **Evidence:** `app/services/winner_probability/repository.py:64-75`, `app/services/winner_probability/repository.py:269-275`, `app/services/winner_probability/feature_extractor.py:98-170`, `app/services/winner_probability/feature_extractor.py:275-345`.
- **Why it matters:** RANK-002/004/005 and indirect RANK-003 effects can cross into Winner even though numeric ranking score is not used.
- **Potential contamination/correctness effect:** An incomplete or globally influenced ranking profile/decision can select a different historical cohort and probability.
- **Existing guard:** Exact row ID and resulting values are frozen in the prediction.
- **Missing guard:** Ranking readiness policy and immutable full profile/config/session/context identity.
- **Recommended future remediation:** Define eligible ranking profiles, enforce readiness/warnings, and persist/validate complete profile configuration and temporal identity.

### WIN-004 — Required feature and confidence policies are metadata-only

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Feature schema / eligibility
- **Finding:** Capture correctly excludes missing/insufficient technical rows and incomplete Combined rows, but does not enforce all feature-schema `required_for_eligible_prediction` policies or low/error technical confidence.
- **Evidence:** `app/services/winner_probability/feature_schema.py:63-245`, `app/services/winner_probability/feature_extractor.py:148-170`, `app/services/winner_probability/feature_extractor.py:362-413`.
- **Why it matters:** A feature declared required can be missing while a prediction remains eligible and receives a broader/missing-bucket probability.
- **Potential contamination/correctness effect:** Low-confidence or semantically incomplete upstream evidence can affect probability despite CORE-009's explicit insufficiency case being blocked.
- **Existing guard:** Explicit technical insufficiency and Combined completeness gates; missing values are audited/warned and preserved, not coerced to numeric zero.
- **Missing guard:** Executable schema missingness policy and confidence threshold/action policy.
- **Recommended future remediation:** Make feature-schema readiness policies executable and persist the resulting eligibility decision/reason per feature.

### WIN-005 — Market and sector defects can enter probability without context compatibility

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Market/sector feature provenance
- **Finding:** Same-run latest market/sector values directly determine cohort features, but Winner does not validate their market context, cutoff, revision, evidence hash, or configuration.
- **Evidence:** `app/services/winner_probability/repository.py:76-100`, `app/services/winner_probability/feature_extractor.py:105-146`, `app/services/winner_probability/feature_extractor.py:306-335`.
- **Why it matters:** CORE-001, CORE-002 labels, CORE-006 embedded fallback, and CORE-007 cross-run sector history can influence probability.
- **Potential contamination/correctness effect:** Sparse/stale/cross-run-derived state may select a materially different cohort while appearing same-run compatible.
- **Existing guard:** Same-run snapshot selection, future-source omission, warnings, exact snapshot/row IDs, by-value freeze.
- **Missing guard:** Exact calculation-context/config/evidence/revision compatibility and upstream readiness policy.
- **Recommended future remediation:** Validate and persist the full regime/sector provenance contract or explicitly exclude nonconforming context from probability-driving features.

### WIN-006 — Native outcome revisions do not preserve exact constituent bar lineage

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Outcome maturation and revision evidence
- **Finding:** Native outcomes store calculated values, lineage hash, and revision cutoff but not each PriceBar/PriceBarRevision identity. Revisions preserve old labels but not a self-contained reconstruction path to old raw OHLC evidence.
- **Evidence:** `app/services/winner_probability/outcome_service.py:257-365`, `app/services/winner_probability/outcome_service.py:615-744`, `app/services/winner_probability/outcome_service.py:872-894`, `app/services/winner_probability/outcome_revision_service.py:11-77`, `app/models/tables.py:2289-2539`.
- **Why it matters:** Outcome truth must be auditable separately from decision evidence, especially after provider corrections.
- **Potential contamination/correctness effect:** A historical label and hash remain visible, but exact price-level reproduction may require unavailable inference over mutable PriceBar history.
- **Existing guard:** Append revisions, old calculated values, source hash/cutoff, revision IDs in manifests.
- **Missing guard:** Per-bar immutable lineage members for native outcomes.
- **Recommended future remediation:** Persist exact bar/revision IDs, hashes, basis, and observation cutoff for every native outcome revision.

### WIN-007 — Cohort publication is not monotonic by evidence watermark

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Cohort generation publication
- **Finding:** Automatic publication locks and atomically switches the pointer but does not require the generation watermark to equal the state's desired watermark or be newer than the current publication. An older READY generation can supersede a newer PUBLISHED generation.
- **Evidence:** `app/services/winner_probability/cohort_generation_service.py:276-414`; current tests intentionally allow publication while the desired watermark has advanced in `tests/integration/test_winner_jobs_reliability_postgresql.py:1095-1197`.
- **Why it matters:** Stale cohort evidence can become authoritative and remove newer generation-bound estimates from serving.
- **Potential contamination/correctness effect:** Active probability service can regress to older labels/membership, including after a newer generation already published.
- **Existing guard:** Contract/content-addressed generation key, completeness/temporal audit, row lock, lease fencing, atomic status/pointer transaction.
- **Missing guard:** Monotonic desired/current watermark comparison and explicit supersession ordering.
- **Recommended future remediation:** Publish only the current desired generation and reject any generation not strictly succeeding the active pointer; add older-after-newer concurrency tests.

### WIN-008 — Maturation continuation is finite but not target-set stable

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Maturation orchestration
- **Finding:** Root/child lineage, retry backoff, zero-progress stop, and depth cap prevent runaway chains, but no frozen target IDs/watermark is persisted and continuation payload drops `due_session`.
- **Evidence:** `app/services/winner_probability/job_handlers.py:77-134`, `app/services/winner_probability/job_handlers.py:394-637`, `app/services/winner_probability/outcome_orchestration_service.py:82-183`.
- **Why it matters:** A session-scoped root should have a stable, auditable target universe.
- **Potential contamination/correctness effect:** Children can process rows that became due after the root's scheduled session; root counts do not describe one frozen cohort of targets.
- **Existing guard:** Global single flight, in-slice visited IDs, bounded batch/depth, retry deferral, zero-progress termination.
- **Missing guard:** Frozen target manifest or persistent root watermark and inherited due-session boundary.
- **Recommended future remediation:** Persist the root target IDs/watermark/due session and require every child to consume only the remaining frozen subset.

### WIN-009 — Automatic generation publication is not idempotent for duplicate calls

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Cohort publication
- **Finding:** `publish()` first requires READY, then later contains a same-pointer check for an already published generation. Because a duplicate has status PUBLISHED, the idempotency branch is unreachable and the duplicate raises.
- **Evidence:** `app/services/winner_probability/cohort_generation_service.py:373-414`.
- **Why it matters:** Retried publication after an uncertain commit should resolve idempotently.
- **Potential contamination/correctness effect:** Recovery can report failure even though publication succeeded, complicating job state and operator decisions.
- **Existing guard:** Transactional pointer/status switch and content-addressed generation identity; reviewed estimate publication has request-key idempotency.
- **Missing guard:** Reachable idempotent check before READY enforcement or a publication request ledger for automatic publication.
- **Recommended future remediation:** Resolve same-generation/same-watermark publication as success before validating a new transition.

### WIN-010 — Winner enablement flags are not a coherent execution gate

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Configuration and scheduling
- **Finding:** `WINNER_PROBABILITY_ENABLED` and YAML `engine.enabled` are not authoritative runtime gates. Pipeline capture, admin mutations, and V2 serving are controlled independently.
- **Evidence:** `app/settings.py:273-282`, `app/services/pipeline_executor.py:1951-1954`, `app/services/winner_probability/config.py`, `app/routers/winner_probability_routes.py:541-625`, `app/services/winner_probability/backfill.py:84-156`.
- **Why it matters:** Operators cannot infer mutation/serving behavior from the apparent master enable flags.
- **Potential contamination/correctness effect:** Winner state can be mutated while the global/YAML engine flag is false if a narrower flag/admin path is enabled.
- **Existing guard:** All defaults are false; local-admin checks; certification runtime forbids automatic maturation/cohort refresh; auto cohort requires V2.
- **Missing guard:** One documented/enforced enablement hierarchy covering serving, pipeline, admin, jobs, and YAML engine state.
- **Recommended future remediation:** Define and enforce a single flag interaction contract, with explicit overrides only where intentional.

## Cross-Task Invariants

| Invariant | Result | Finding/guard |
|---|---|---|
| A Winner prediction is reconstructable from immutable decision-time evidence. | Feature vector yes; complete source context no. | WIN-002 |
| No decision-time feature consumes evidence after the decision cutoff. | Checked against chosen cutoff, but chosen cutoff can be current rather than original pipeline time. | WIN-001 |
| Known-insufficient upstream evidence cannot silently become an eligible prediction. | Explicit technical insufficiency is blocked; broader low confidence is not. | Guard verified; WIN-004 |
| Maturation updates outcome truth but never decision-time evidence. | Frozen `feature_json` unchanged; only operational entry-data status mutates. | **VERIFIED** |
| Historical rescore uses the original frozen feature vector unless explicitly experimental. | Generation job does; no current upstream feature reread. | **VERIFIED** |
| A stale maturation/cohort/rescore job cannot overwrite a newer generation. | Rescore stops on nonpublished generation and stale lease is fenced; cohort publication can regress. | WIN-007 |
| Every continuation makes measurable bounded progress or terminates. | Satisfied through attempted-row state change, defer/stop, and depth cap. | **VERIFIED** |
| A published generation is immutable. | Content is code-immutable; lifecycle status can become SUPERSEDED. No DB-wide immutable-write barrier. | **PARTIALLY_VERIFIED** |
| Active generation pointer moves atomically only to a completely validated generation. | Atomic/completeness/temporal checks exist; monotonic/current-desired check is missing. | WIN-007 |
| Automatic work never mutates a certification run unless explicitly included. | Certification settings reject automatic maturation and cohort refresh. | **VERIFIED** |
| PIPE-001 cannot contaminate Winner through stale CERI. | Winner does not consume CERI. | **VERIFIED** |
| SETUP-002/003/004/009 cannot alter Winner input retrospectively. | Winner does not consume Setup/Lifecycle. | **VERIFIED** |

## Audit Coverage

- Prediction capture, eligibility, feature extraction/schema, source selection, source availability checks, episode assignment, child creation, and decision-time estimation were traced. **VERIFIED**
- Actual dependencies on core scores, Ranking, regime, sector, Setup/Lifecycle, CERI, IB intelligence, and price evidence were independently classified. **VERIFIED**
- Outcome due-session mapping, current price selection, adjustment basis, benchmark alignment, labels, retries, revisions, obligations, and target/stop logic were traced. **VERIFIED**
- Root/parent/child maturation jobs, single-flight behavior, depth, progress, zero-progress, retries, target scope, and termination were traced. **VERIFIED**
- Cohort watermarks, generation identity, evidence filters, manifests, statistics, checkpoints, publication, rescoring, estimate lifecycle, and reviewed replacement publication were traced. **VERIFIED**
- Feature flags, scheduler/worker interactions, certification exclusions, admin paths, and legacy/dead-code paths were traced. **VERIFIED**
- No implementation remediation or production mutation was performed. **VERIFIED**

## Known Unknowns

- Live database state, active/published generation ordering, existing stale jobs, production flag values, and production Winner artifact integrity were not queried. **UNKNOWN**
- PostgreSQL-only concurrency properties were assessed from implementation and existing integration tests; the integration suite was not executed against a live PostgreSQL instance in this audit. **PARTIALLY_VERIFIED**
- Upstream TechnicalScore's exact embedded PIT bar lineage is documented by Task 02 but is not copied into Winner; recoverability depends on retention of those upstream artifacts. **UNKNOWN**
- Whether Ranking `decision_label` was intentionally meant to represent Winner `setup_family` is not established by an external product specification. **UNKNOWN**
- The intended role of `WINNER_PROBABILITY_ENABLED` and YAML `engine.enabled` beyond rollout reporting/config hashing is not expressed in executable code. **UNKNOWN**

## Contradictions

- Any architecture showing CERI as a Winner input is contradicted by the implementation. **CONTRADICTORY**
- Any architecture showing Setup/Lifecycle state as a Winner input is contradicted by the production repository loader. **CONTRADICTORY**
- A claim that parent pipeline order proves Winner uses CERI or Setup is contradicted by the actual queries. **CONTRADICTORY**
- A claim that historical backfill uses the original decision cutoff is contradicted by `_capture_ticker()` choosing current time when `decision_at` is absent. **CONTRADICTORY**
- A claim that active cohort publication is monotonic is contradicted by the absence of desired/current watermark ordering checks. **CONTRADICTORY**
- A claim that the apparent master Winner/YAML enable flags govern execution is contradicted by independent pipeline/admin/scheduler gates. **CONTRADICTORY**

## Potential Contract Violations

- `WIN-001`: prediction decision identity can be derived from execution wall clock instead of the original pipeline/historical context. **VERIFIED**
- `WIN-002`: handoff lineage is recorded without exact compatibility validation. **VERIFIED**
- `WIN-003`: incomplete/unversioned ranking semantics can influence cohort probability. **VERIFIED**
- `WIN-004`: required-feature and confidence policies are not fully enforced. **VERIFIED**
- `WIN-005`: known regime/sector weaknesses can propagate without context validation. **VERIFIED**
- `WIN-006`: native outcome evidence is not fully reconstructable to exact bar revisions. **VERIFIED**
- `WIN-007`: an older cohort generation can supersede a newer generation. **VERIFIED**
- `WIN-008`: maturation continuation target scope is not frozen. **VERIFIED**
- `WIN-009`: automatic publication retry is not idempotent. **VERIFIED**
- `WIN-010`: Winner enablement state is not governed by one coherent contract. **VERIFIED**

## High-Risk Cross-Subsystem Dependencies

- Pipeline timing → Winner decision/session identity: no frozen cutoff/pipeline/context is forwarded. **VERIFIED**
- Ranking → Winner setup-family/profile cohort dimensions: incomplete/profile-config weaknesses can change probability. **VERIFIED**
- Market regime/sector rotation → Winner cohort keys: CORE-001/002/006/007 can be embedded. **VERIFIED**
- PriceBar current projection → versioned outcome truth → cohort watermark/generation: provider corrections can relabel and republish probabilities. **VERIFIED**
- Cohort generation publisher → API serving: non-monotonic publication can remove newer generation-bound estimates from serving. **VERIFIED**
- Background worker → maturation → cohort auto-refresh: combined flags permit automatic global outcome and generation mutation outside a pipeline run, but certification mode forbids it. **VERIFIED**
- CERI/Setup Lifecycle → Winner: no implemented dependency; their asynchronous/mutable defects do not directly contaminate Winner. **VERIFIED**

## Files Inspected

- `app/services/pipeline_executor.py`
- `app/services/winner_probability/capture_service.py`
- `app/services/winner_probability/feature_extractor.py`
- `app/services/winner_probability/feature_schema.py`
- `app/services/winner_probability/repository.py`
- `app/services/winner_probability/episode_service.py`
- `app/services/winner_probability/pending_outcome_service.py`
- `app/services/winner_probability/outcome_service.py`
- `app/services/winner_probability/outcome_revision_service.py`
- `app/services/winner_probability/outcome_orchestration_service.py`
- `app/services/winner_probability/target_stop_service.py`
- `app/services/winner_probability/market_data_obligation_service.py`
- `app/services/winner_probability/decision_time_estimate_service.py`
- `app/services/winner_probability/probability_estimator.py`
- `app/services/winner_probability/evidence_service.py`
- `app/services/winner_probability/evidence_manifest_service.py`
- `app/services/winner_probability/training_eligibility.py`
- `app/services/winner_probability/temporal_eligibility.py`
- `app/services/winner_probability/temporal_integrity.py`
- `app/services/winner_probability/cohort_definition.py`
- `app/services/winner_probability/cohort_statistics.py`
- `app/services/winner_probability/cohort_generation_service.py`
- `app/services/winner_probability/cohort_materialization_service.py`
- `app/services/winner_probability/cohort_refresh_planner.py`
- `app/services/winner_probability/estimate_lifecycle.py`
- `app/services/winner_probability/estimate_publication_service.py`
- `app/services/winner_probability/reproduction_service.py`
- `app/services/winner_probability/model_registry.py`
- `app/services/winner_probability/model_training.py`
- `app/services/winner_probability/backfill.py`
- `app/services/winner_probability/job_handlers.py`
- `app/services/winner_probability/scheduler.py`
- `app/services/winner_probability/api_service.py`
- `app/services/background_job_service.py`
- `app/services/background_worker.py`
- `app/routers/winner_probability_routes.py`
- `app/settings.py`
- `app/models/tables.py`
- `config/winner_probability.yaml`
- Winner migrations `0016`, `0025`, `0049`, and `0057`–`0061`

## Tests Inspected

- Entire `tests/winner_probability/` suite, including capture, decision-time contract, temporal integrity, outcome/maturation, obligations, revisions, continuation reliability, cohorts, generations, estimates, publication, reproduction, training eligibility, scheduler, routes, flags/config, and quantitative validation.
- `tests/test_pipeline_executor.py`
- Winner PostgreSQL integration tests were inspected, especially temporal integrity, maturation canary, capture recovery, and job/generation reliability scenarios.
- Executed: `.venv\Scripts\python.exe -m pytest tests/winner_probability tests/test_pipeline_executor.py -q`
- Result: `301 passed, 1 warning in 10.63s`. The warning is an unrelated Starlette/httpx deprecation warning. **VERIFIED**

## SQL Inspected

- Same-run core/ranking/regime/sector and latest handoff-manifest capture queries. **VERIFIED**
- Active prediction/episode and outcome-definition/current-revision selectors. **VERIFIED**
- Due/retry-eligible/unvisited maturation queue queries and global single-flight background-job constraints. **VERIFIED**
- Current PriceBar series queries for ticker, SPY, and sector proxies. **VERIFIED**
- Historical evidence cutoff, revision visibility, temporal eligibility, quality, version/config, rolling-window, episode-independence, and watermark selectors. **VERIFIED**
- Cohort refresh-state locking, generation creation/content key, completeness audit, and publication pointer updates. **VERIFIED**
- Generation-bound rescore target, statistic, manifest-member, self-inclusion, and serving queries. **VERIFIED**
- Reviewed publication locks, active-job guard, serving-set hashes, candidate purity, and pointer switch. **VERIFIED**
- No live SQL was executed. **VERIFIED**

## Verification Metadata

| Item | Value |
|---|---|
| Verified at | 2026-09-14 (Europe/Zurich) |
| Git HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Implementation baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Baseline drift | No |
| Branch | `codex/winner-evidence-remediation` |
| Python | CPython 3.12.2 in repository `.venv` |
| Automated verification | 301 passed, 1 warning |
| Live database/provider access | None |
| Application code changes | None |
| Deliverable | `docs/audit/calculation-lineage/06_winner.md` only |
