# SwingLens CERI — Calculation and Evidence Lineage Audit

Task: 03 — CERI Calculation and Evidence Lineage
Verification state: repository/static analysis plus isolated tests; no provider calls, production jobs, migrations, or database writes
Evidence vocabulary: `VERIFIED`, `PARTIALLY_VERIFIED`, `INFERRED`, `UNKNOWN`, `CONTRADICTORY`, `LEGACY`, `DEAD_CODE`

## Repository identity and implementation baseline

| Item | Value | Status |
|---|---|---|
| Repository path | `C:\Users\Ivica\Documents\SwingLens` | VERIFIED |
| Current branch | `codex/winner-evidence-remediation` | VERIFIED |
| Current repository HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Implementation baseline SHA | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Application implementation drifted from baseline? | **No.** `HEAD` equals the baseline; the commit range and tracked implementation diff are empty. | VERIFIED |
| Exact implementation-changing commits since baseline | None | VERIFIED |
| Exact implementation-changing files since baseline | None | VERIFIED |
| `BASELINE_DRIFT` | **Not flagged.** No calculation or orchestration conclusion in this report depends on superseded implementation. | VERIFIED |
| Worktree at audit start | Untracked `docs/audit/`, `scripts/ops/ib_historical_probe.py`, and `tests/ops/test_ib_historical_probe.py`; no tracked modifications. The probe files pre-existed this task and are not treated as authoritative committed implementation. | VERIFIED |
| Change made by Task 03 | This file only: `docs/audit/calculation-lineage/03_ceri.md` | VERIFIED |
| Relevant repository migration head | `0074_ceri_evidence_quarantine` | VERIFIED |
| Live database migration revision | Not queried because a safe live-database context was not established. | UNKNOWN |
| Python runtime | CPython 3.12.2 from `.venv` | VERIFIED |
| Verification time | 2026-09-14T01:05:23+02:00 (Europe/Zurich) | VERIFIED |

Documentation-only commits under `docs/audit/calculation-lineage/` do not constitute implementation drift under the supplied baseline rule. **VERIFIED**

## Scope and method

This audit covers CERI provider ingestion, evidence storage, normalization, estimates, earnings, guidance, catalysts, SEC processing, feature construction, point-in-time selection, price response, confidence, freshness, opportunity and event-risk scores, posture, snapshots, changes, alerts, replay, purge, feature flags, preflight, bootstrap, signatures, and artifact lineage. **VERIFIED**

The audit is static and test-backed. It did not contact a provider, run the production pipeline, enqueue a job, change application data, or inspect live database contents. Database constraints and migration state are therefore established from models and migration files, not from a deployed database. **PARTIALLY_VERIFIED**

## Executive conclusions

1. CERI has a strong evidence-receipt boundary: normalized evidence is normally eligible only when its source record was retrieved or ingested by the cutoff, and pipeline-owned artifacts require a frozen calculation context, cutoff, and calendar version. **VERIFIED**
2. The configured provider-priority/conflict contract is not the selection contract used by the production point-in-time reader. Conflict and estimate-deduplication services exist but have no application caller; same-time provider observations can be selected by row identity. **VERIFIED — CONTRACT VIOLATION; CERI-001**
3. CERI price response does **not** use Task 02's authoritative `load_preferred_ohlcv_frames` reader. Production capture/rebuild paths do reconstruct `PriceBar` through `PriceBarRevision` at the cutoff, but the CERI reader mixes price bases and has public no-cutoff/injected-bar bypasses. **VERIFIED — CONTRACT VIOLATION; CERI-002 and CERI-003**
4. No future session can enter the normal pipeline price path past its frozen `feature_as_of_session`; however, missing benchmark history can be misreported as a window that has not elapsed, and date-only events cannot produce a response even when an effective session exists. **VERIFIED**
5. The opportunity score is a coverage-gated weighted average of available components, not a fixed seven-input average. Missing components are reweighted away once 60% of configured weight is present. Event-risk is a distinct score; there is no third numeric “final CERI” score. Posture is a hard-coded classification over opportunity, risk, and confidence. **VERIFIED**
6. Snapshot evidence hashes and append-like versioning are substantial lineage controls. Provider-license purge is an intentional exception that mutates historical evidence and snapshots without recomputing their evidence hashes. **VERIFIED — CERI-010**
7. SEC preflight and execution resolve the same deployed semantic signature, and bootstrap state is signature-specific. The signature proves a declared four-version tuple, not source bytes, dependency versions, model identity, configuration, or Git state. **VERIFIED**
8. The runtime feature flags default CERI off. `config/ceri.yaml` also says `engine.enabled: false`, but the effective gate is the runtime flag object, not that YAML field alone. **VERIFIED**

## Intended contract versus current implementation

| Subsystem | Intended contract | Current implementation | Assessment |
|---|---|---|---|
| Evidence possession | Only evidence known to SwingLens by the calculation cutoff may contribute. | `retrieved_at`, falling back to `ingested_at`, is the source-record knowledge time; source-backed reads reject later receipts. | VERIFIED |
| Provider resolution | Apply configured `manual > primary > eodhd > sec`, preserve observations, and surface conflicts. | Priority/conflict code exists but is not wired into production selection; PIT selection orders by effective time/session and IDs. | VERIFIED — CONTRACT VIOLATION |
| Historical revisions | Reconstruct what was known at the decision cutoff; later corrections must not rewrite prior results. | AS_KNOWN and LATEST_CORRECTED views are cutoff-bounded; derived features and snapshots are versioned by cutoff/config/calculation context. Explicit replay can add parallel outputs. | PARTIALLY_VERIFIED |
| Market data | Use Task 02's bounded preferred OHLCV view: adjusted price only when fully covered and TRADES volume, reconstructed from revisions. | CERI uses a bespoke `PriceBar` reader. It reconstructs revisions in normal production paths but does not implement preferred-basis selection. | VERIFIED — CONTRACT VIOLATION |
| Pipeline artifacts | Bind outputs to one run, session, observation cutoff, calendar, configuration, and calculation version. | Pipeline ownership validation enforces context/cutoff/calendar; snapshots and features carry most of these fields. Standalone artifacts are permitted with weaker lineage. | PARTIALLY_VERIFIED |
| Freshness | Penalize stale evidence consistently in confidence, risk, and alerts. | Confidence derives estimate-feed age; event risk looks for a stale warning on revision features before confidence creates that warning. | VERIFIED — CONTRACT VIOLATION |
| SEC equivalence | Preflight and worker must use the same certified builder semantics. | Both use the same declared processor signature and ACTIVE release. The signature is semantic/version-declared rather than code-content-derived. | PARTIALLY_VERIFIED |
| Historical immutability | Retain immutable evidence and reproduce scores. | Normal calculations append/version; licensed purge deliberately tombstones and invalidates historical rows. | PARTIALLY_VERIFIED |

## Actual architecture and execution topology

```text
provider adapter / SEC document pipeline
  -> CeriIngestionRun
  -> CeriSourceRecord (raw/restricted payload + receipt times + supersession)
  -> CeriProcessingRun
  -> normalized estimate / earnings / guidance / catalyst evidence
  -> point-in-time eligibility and feature rebuild
       -> revision features
       -> catalyst features
       -> price-response features
  -> capture at shared MarketCalculationCutoff
       -> surprise, guidance, confidence
       -> opportunity score + event-risk score + posture
       -> CeriScoreSnapshot
  -> change detection / rebuild
  -> alert rebuild
```

The legacy pipeline either schedules provider ingestion or captures immediately; the batched workflow creates provider, normalization, feature, and finalization stages under `ceri:pipeline:{run_id}:{config_hash}`. Capture runs after the core calculation stages and before setup-lifecycle evaluation when enabled. **VERIFIED**

Runtime defaults are `ceri_enabled=false`, provider ingest false, run capture false, UI false, alerts false, admin false, and backfill false. Legacy pipeline scheduling defaults true while batched workflow defaults false, but neither activates CERI without the master/child gates. **VERIFIED**

## Evidence lineage

### Source-record boundary

`CeriSourceRecord` retains provider, dataset, provider record identity, published/observed/source timestamps, retrieved and ingested times, normalized and content hashes, request/idempotency identity, correction/supersession links, quarantine state, licensing, and purge state. Raw payload retention is provider-policy dependent: EODHD and SEC paths can omit raw material and retain restricted normalization instead. **VERIFIED**

The canonical idempotency key is provider + dataset + provider record ID + canonical economic content hash. An exact repeat returns the existing source row. Changed content under the same provider record ID creates a new row and links it as a correction/supersession rather than overwriting the original. **VERIFIED**

For point-in-time eligibility, “known at” means timezone-aware `retrieved_at`, falling back to `ingested_at`. `published_at`, `observed_at`, and `source_timestamp` describe provider/event semantics but do not prove SwingLens possession. **VERIFIED**

### CERI Evidence Timestamp Matrix

| Evidence | published_at | observed_at | retrieved_at | ingested_at | event_session | Which timestamp controls selection | Which controls freshness |
|---|---|---|---|---|---|---|---|
| Estimate snapshot | Source record; optional | Source record; optional | Source record; primary receipt time | Source record fallback receipt time | Normalized `effective_session` | Effective time/session must be at or before the as-of boundary; source `retrieved_at`/`ingested_at` must be at or before cutoff | Estimate-feed freshness uses the latest successful completed ingestion run, not the selected estimate's age | VERIFIED |
| Earnings actual | Source record; optional | Source record; optional | Source record | Source record fallback | `report_session` from report/effective timestamp | Report time/session plus source receipt cutoff | Dataset limit exists (30 days), but surprise scoring has no separate evidence-age penalty | VERIFIED |
| Guidance | Source record; optional | Source record; optional | Source record | Source record fallback | `effective_session`; `announced_at` retained | Effective time/session and source receipt cutoff; accepted, unsuperseded rows selected per metric/period | Latest accepted guidance must be no older than 14 calendar days | VERIFIED |
| Catalyst | Source record; optional | Source record; optional | Source record | Source record fallback | Revision `effective_session`; announced/effective timestamps retained | Relevant, non-rejected, current/eligible revision with effective boundary and receipt cutoff | Selected catalyst is stale after 2 calendar days under dataset rules | VERIFIED |
| SEC filing/document | Filing publication/acceptance metadata | SEC response/document metadata where available | Fetch/cache observation recorded by the ingestion path | Source record insertion time | Extracted event's effective session | Filing/document must be processed by the active signature and source receipt bounded | Downstream guidance/catalyst dataset rule | PARTIALLY_VERIFIED |
| Price bar | Not applicable | `first_seen_at`/revision `observed_at` | Not a CERI source record | Not applicable | `bar_date` | `bar_date <= feature_as_of_session`; current row is projected backward through revisions with observation times after cutoff | No independent bar-age score; availability/window status controls result | VERIFIED |
| Uploaded upcoming earnings date | Not retained per-value | Not retained per-value | Upload/run receipt only, not attached to the field | Raw company-row insertion | Computed relative to snapshot session | Current run's `RawCompanyRow.upcoming_earnings_date` | No evidence-specific freshness | VERIFIED — CONTRACT GAP |

### Effective-session rules

Normalized date-only evidence maps to the same or next US trading session and receives a missing-timestamp warning. Timestamped evidence on a non-session rolls forward; after-close evidence rolls to the next session; premarket and intraday evidence retain that trading session. **VERIFIED**

Price reaction intentionally uses a stricter next-market-open policy: pre-open events react the same session; events exactly at the open, intraday, or after close react next session; weekends/holidays roll to the next session. It requires `event_effective_at` and ignores a date-only `event_effective_session` as a sufficient anchor. **VERIFIED**

### Revisions, current state, and historical reconstruction

- `AS_KNOWN` includes normalized evidence effective by the session and received by the cutoff. **VERIFIED**
- `LATEST_CORRECTED` additionally resolves correction chains only when the correction itself was received by the cutoff. **VERIFIED**
- Current estimate slot selection chooses the latest fiscal period end and then the latest eligible row. It does not apply configured provider priority. **VERIFIED**
- Catalyst updates retain event revisions, mark an older revision non-current, and link identical evidence through source links. The standalone fuzzy/tolerance catalyst deduplicator is not called by application code. **VERIFIED**
- Later source corrections do not enter an old cutoff view, but backfill/replay may append new feature/snapshot versions for a historical session under a later cutoff/config/version. **VERIFIED**
- Purge can tombstone evidence and invalidate derived artifacts, so the historical database state is not absolutely immutable. **VERIFIED**

## Processor lineage

| Processor | Signature / algorithm | Inputs | Outputs | Signature inputs | Bootstrap / reuse | Historical compatibility | Status |
|---|---|---|---|---|---|---|---|
| SEC guidance processor | `sec-guidance:<16 hex>`; signature algorithm `sec-processor-signature-v1` | SEC filing identity/body | Source records and guidance/catalyst evidence | Declared parser v1, extractor v3, locator v1, filing-selection v1 constants | ACTIVE release required; bootstrap certified per CIK/dataset/signature; document body content cache and completed extraction reuse | Old signature state remains distinct; signature change requires bootstrap | VERIFIED |
| Provider adapters | Provider-specific implementations; no common content-derived processor signature | Provider API/manual payload | Provider records consumed by orchestration | Adapter/provider identifiers and normalizer versions, not code bytes | Request/run idempotency; provider-specific transport caches | Source records preserve provider/version metadata, but exact code dependency identity is not complete | PARTIALLY_VERIFIED |
| Estimate normalizer | Declared normalizer version | Estimate source record | `CeriEstimateSnapshot` | Version string stored with output/source lineage | One normalized row per source record | Reprocessable under explicit job/version; historical code bytes not embedded | PARTIALLY_VERIFIED |
| Earnings normalizer | Declared normalizer version | Earnings source record | `CeriEarningsActual` | Version string | One normalized row per source record | Same limitation | PARTIALLY_VERIFIED |
| Guidance normalizer/comparison | Declared version plus comparison rules | Guidance/SEC evidence and prior comparable row | `CeriGuidanceEvent` | Version/config references, not source bytes | Existing normalized source is reused | Old rows retained/superseded | PARTIALLY_VERIFIED |
| Catalyst normalizer/revision writer | Declared version + taxonomy/config | Catalyst/SEC evidence | event, revision, source link | Version/config references | Exact logical event key and revision reuse | Revision history retained; separate deduplicator unused | PARTIALLY_VERIFIED |
| Revision feature builder | CERI calculation/config versions | PIT estimate snapshots | `CeriRevisionFeature` | Config hash, calculation version, event/cutoff/context identity | Event-key upsert/reuse | Distinct cutoff/context keys permit parallel history | VERIFIED |
| Surprise builder | CERI calculation/config versions | Earnings + pre-report estimates | Capture ledger/component | Snapshot evidence hash captures selected IDs | Recomputed during capture | Legacy `known_at` fallback weakens old-row compatibility | PARTIALLY_VERIFIED |
| Price-response builder | reaction policy + CERI config/calculation versions | Event + stock/SPY bars | `CeriPriceResponseFeature` | Event/company/config/calculation/reaction/cutoff/context key | Upsert by event key | PIT if cutoff supplied; basis ambiguity remains | PARTIALLY_VERIFIED |
| Capture/snapshot builder | CERI config hash and `ceri-1.3.0` | features/evidence/shared cutoff/context | `CeriScoreSnapshot` | Canonical evidence hash over scores, ledgers, evidence IDs, versions, alignment, temporal lineage | Existing run/company/config/calculation snapshot is reused | Append/parallel version except purge | VERIFIED |
| Controlled replay | Fixed replay signature plus target versions | Historical scope/evidence | Parallel rebuilt artifacts and replay audit | Request/target config/calculation identity | Does not replace original snapshot | Designed for comparison, not in-place upgrade | VERIFIED |

The SEC signature proves equality of the declared semantic version tuple. It does **not** prove equality of Python source, Git SHA, package versions, configuration, prompts/models, cached body bytes, or database schema. **VERIFIED**

`current_deployment_identity()` separately records Git SHA/dirty state, image digest, config hash, calculation version, and provider signatures, but hard-codes schema revision `0050_sec_processor_promotion` while repository migrations extend through `0074_ceri_evidence_quarantine`. **VERIFIED — CERI-009**

## Material feature calculations

### Estimate revisions

Required metric/period slots are two metrics (`EPS_DILUTED`, `REVENUE`) × four periods (`CURRENT_QUARTER`, `NEXT_QUARTER`, `CURRENT_FISCAL_YEAR`, `NEXT_FISCAL_YEAR`) × three lookback windows (7/30/90 days), or 24 expected values. Optional metrics exist but are not core coverage. **VERIFIED**

For each slot, current is the latest eligible estimate. Baseline is the latest eligible observation on or before the target lookback date; if absent, the first observation within the following three calendar days is allowed. There is no maximum age for a baseline found before the target, although actual elapsed days are retained. **VERIFIED**

`pct_change = (current - baseline) / abs(baseline) × 100`. Percent change is null when `abs(baseline) <= 0.01` or the values cross sign; absolute change remains available. Breadth is `(up - down)/(up + down)`, with zero when no analysts move. Dispersion is `(high - low)/abs(consensus)`. Acceleration is the recent percent-change rate minus a longer-window percent-change rate in percentage points per day. **VERIFIED**

EPS may use a provider-retrospective baseline only when observation window, provider, scale, currency, and semantic fields are compatible. Revenue disallows retrospective reconstruction and requires accumulated history. **VERIFIED**

Feature confidence starts at 8, subtracts 2 when analyst counts are missing, 2.5 below three analysts, 1 when the effective timestamp is missing, and 0.5 per warning capped at 2; label thresholds come from CERI confidence bands. **VERIFIED**

### Earnings surprise

Provider report-time consensus is preferred. Otherwise the service selects the latest estimate for the same fiscal period with effective time and known time before the earnings report. Surprise is `actual - consensus`; surprise percent divides by `abs(consensus) × 100` unless consensus is near zero. The summary uses the last four reported events, average surprise percent, positive/negative counts, and consistency. **VERIFIED**

Current normalized estimates use receipt-based `known_at`. Legacy rows missing that value fall back to provider observation/source timestamps before retrieval/effective time, which can make a post-report-received estimate appear pre-report. **VERIFIED — CERI-006**

The surprise opportunity component is `clamp(5 + average_surprise_pct × 0.2, 0, 10)`. Missing usable surprises make the component unavailable. **VERIFIED**

### Guidance

Guidance comparison uses current versus prior compatible company/metric/period evidence. Accepted actions are `Raised`, `Initiated`, `Maintained`, `Narrowed`, `Widened`, `Lowered`, and `Withdrawn`; unknown actions, low-quality/manual-review evidence, missing metric/period, and incompatible EPS units are rejected. SEC management claims are marked for manual review. **VERIFIED**

The latest unsuperseded accepted row per metric/period is scored: Raised 8, Initiated 6, Maintained/Narrowed 5, Widened 4, Lowered 2, Withdrawn 1; component score is the mean. Evidence older than 14 calendar days is unavailable/stale. **VERIFIED**

### Catalysts

A catalyst is selected only when issuer-relevant and not review-rejected. Opportunity contribution is `max(0, materiality × direction_multiplier - conflict_penalty)`, where multipliers are strong-positive 1.0, positive 0.7, neutral 0.2, negative -0.7, strong-negative -1.0, unknown 0; conflicts subtract 0.75 each, capped at 3. Selected opportunity contributions sum and clamp at 10. **VERIFIED**

Regulatory, legal, financing, and corporate-action categories can be binary. Only unresolved/scheduled/delayed or explicitly eligible future/current states contribute. Base risks are 4.0, 3.0, 2.5, and 3.0 respectively; known events more than 30 days away are halved; unknown dates receive the configured penalty; conflicts add secondary pressure. **VERIFIED**

### Price response

The complete temporal and market-data analysis is in the dedicated cross-task section below. **VERIFIED**

## CERI scoring and classification

### Opportunity score

| Component | Weight | Exact score construction | Missing behavior | Status |
|---|---:|---|---|---|
| Revision magnitude | 0.25 | Mean `max(0, revision_pct)` across usable revision features, clamped to 10 | Unavailable | VERIFIED |
| Revision breadth | 0.15 | `(mean_breadth + 1) × 5`, clamped 0–10 | Unavailable | VERIFIED |
| Revision acceleration | 0.10 | `5 + mean_acceleration × 10`, clamped 0–10 | Unavailable | VERIFIED |
| Surprise trend | 0.15 | `5 + average_surprise_pct × 0.2`, clamped 0–10 | Unavailable | VERIFIED |
| Guidance | 0.15 | Mean mapped action score for fresh accepted guidance | Unavailable | VERIFIED |
| Catalysts | 0.15 | Sum of selected opportunity contributions, clamped to 10 | Unavailable | VERIFIED |
| Price response | 0.05 | Price-response quality score described below | Unavailable/pending | VERIFIED |

`available_weight = sum(weights for non-null components)` and coverage is `available_weight × 100`. Below 60% coverage, opportunity is unrated. Otherwise `opportunity = clamp(sum(component × weight)/available_weight - conflict_penalty, 0, 10)`. Missing components are therefore reweighted away, not zero-filled. **VERIFIED**

The configured period weights 0.35/0.30/0.20/0.15 exist for current quarter, next quarter, current fiscal year, and next fiscal year. The main opportunity service averages available revision rows rather than applying those period weights directly to its three revision components. **VERIFIED**

### Confidence and freshness

Confidence inputs and weights are source quality 0.25, freshness 0.20, estimate coverage 0.20, analyst sample 0.15, timestamp quality 0.10, and conflict-free score 0.10. Each contributes `value × weight`; unavailable inputs contribute zero and weights are not renormalized. **VERIFIED**

Coverage is usable revision values divided by the 24 core slots. Analyst score is 10 for at least six analysts, 7 for at least three, otherwise 3. Timestamp quality is normally 9, degrades to 6 for missing timestamps and 4 for baseline/current timestamp warnings. Conflict-free begins at 10 and subtracts conflict penalties. **VERIFIED**

Freshness is based on the most recent successful `COMPLETED` estimates ingestion run for the ticker at/before cutoff, not the age of the exact estimates selected. Freshness score is 10 through one day, 7 through seven days, then `max(0, 7 - (age_days - 7))`. The configured estimate stale threshold is seven calendar days. **VERIFIED**

Labels are High at 8, Normal at 6, Low at 3.5, otherwise Insufficient. Zero usable core revision coverage forces Insufficient; any warning caps High to Normal. YAML's `critical_provenance_cap: Low` is not parsed or applied by the confidence configuration/service. **VERIFIED — CERI-005**

### Event-risk score

Earnings proximity from the uploaded upcoming earnings date scores 5 at two or fewer US trading days, 3 at five or fewer, 1.5 at ten or fewer, else 0. Unknown date gives 0 plus a warning. **VERIFIED**

The dominant primary risk is the maximum of earnings proximity and deduplicated binary catalyst risks. Secondary conflict, options-event-premium, staleness, and related pressures are summed but capped at 2; final risk is `min(10, dominant + secondary_penalty)`. Short pressure is context/reasoning, not a direct score term. **VERIFIED**

Capture computes `company_stale` from revision-feature warnings before confidence is calculated. Only the confidence service creates `estimate_data_stale`; no production revision-feature writer emits it. The event-risk staleness penalty is therefore unreachable in the normal ordering unless legacy/manually constructed feature warnings already contain it. **VERIFIED — CERI-007**

### Final CERI output and posture

There is no separate numeric final score beyond `opportunity_score` and `event_risk_score`. **VERIFIED**

Posture is hard-coded: confidence Insufficient or missing opportunity -> Unrated; risk >= 6 -> Binary Risk; otherwise opportunity >= 7 -> Positive, >= 5 -> Improving, >= 3 -> Mixed, else Deteriorating. YAML enumerates labels but does not configure these thresholds. **VERIFIED**

### Change thresholds

Comparable snapshots require equal calculation version, config hash, and evidence contract. The latest earlier snapshot is selected by session, cutoff, then ID. Opportunity changes resolve in order: cross 7.5 -> upgrade/downgrade; otherwise posture change; otherwise absolute score delta >= 1. Revision magnitude delta is 2 percentage points, event-risk escalation is 2, and acceleration delta is 0.01. **VERIFIED**

## Cross-task temporal dependency check: CERI price-derived values

### Required determination

| Question | Determination | Status |
|---|---|---|
| Exact market-data reader | `CeriPriceResponseService._bars`, a bespoke query over `PriceBar`; it is **not** Task 02's `load_preferred_ohlcv_frames`. Batch rebuild may preload with its own query and inject rows. | VERIFIED — CONTRACT VIOLATION |
| Calculation cutoff | Normal pipeline capture passes the shared `MarketCalculationCutoff.cutoff_at`; feature rebuild also prefilters `first_seen_at <= cutoff_at`. Public service calls may omit it. | VERIFIED |
| Latest permitted session | Normal paths pass `feature_as_of_session = latest_completed_session`; queries/preloads enforce `bar_date <= session`. | VERIFIED |
| Revision reconstruction | With a cutoff and real SQLAlchemy `Session`, current `PriceBar` rows are projected backward through `PriceBarRevision` via `project_price_bar_rows_as_of`. Feature preloading performs the same projection. | VERIFIED |
| Can mutable/current `PriceBar` bypass PIT? | **Yes.** Omitting `cutoff_at` reads current mutable rows directly. Supplying `stock_bars` or `benchmark_bars` bypasses the internal query/reconstruction and eligibility checks. Production capture/rebuild supplies proper bounds, but the callable contract does not require them. | VERIFIED — CONTRACT VIOLATION |
| Event-to-session alignment | Requires timestamped event. Pre-open -> same session; open/intraday/after-close -> next session; non-session -> next session. Date-only `event_effective_session` alone returns unresolved. | VERIFIED |
| Common stock/benchmark session | Required prior/target dates are exact and shared for return subtraction. There is no explicit common-series calendar/intersection or completeness check. | PARTIALLY_VERIFIED |
| Missing historical sessions | Missing stock prior/reaction -> `PRICE_DATA_MISSING`, except a reaction beyond max stock date -> `WINDOW_NOT_ELAPSED`. Missing H1 benchmark values yields null relative return and can cause `WINDOW_NOT_ELAPSED` even when a past benchmark session is actually absent. Missing H3 warns but does not suppress H1 score. | VERIFIED |

### Basis and row-selection violation

The CERI query filters ticker, daily timeframe, IB/IBKR source, close present, cutoff/session eligibility, but does not select `ADJUSTED_LAST` versus `TRADES`. It then builds one `date -> row` dictionary from a query ordered only by date. Duplicate basis rows for a date overwrite one another according to unspecified row order. Volume lookback likewise iterates both bases. **VERIFIED — CERI-002**

An isolated calculation using identical duplicated daily rows in opposite input orders produced different H1 returns: 20% when adjusted rows won and 2% when TRADES rows won. Volume ratios also changed from approximately 0.182 to 1.818. This demonstrates order-dependent calculations rather than a merely theoretical ambiguity. **VERIFIED**

Task 02's contract uses adjusted price only when that basis covers every TRADES session and always uses TRADES volume. CERI implements neither rule, so every price-derived value—gap, stock return, benchmark return, relative return, volume ratio, close location, bar IDs, and quality score—can inherit basis ambiguity. **VERIFIED — CONTRACT VIOLATION**

### Event selection, windows, and formula

The price anchor is the most recent eligible reported earnings event, accepted guidance event, or selected current catalyst, ordered by effective session/time/ID. Source receipt and event session are cutoff-filtered before production selection. A newer date-only event can win and then produce `EVENT_TIMESTAMP_UNRESOLVED`. **VERIFIED**

The prior reference is the immediately preceding US trading session. H1 target is the reaction session; H3 target is reaction plus two US sessions. Gap is `(reaction_open - prior_close)/abs(prior_close)`. Stock and SPY returns use target close versus the same prior-session close; relative return is stock minus benchmark. Volume ratio is reaction volume divided by the mean of up to 20 prior non-null row volumes. Close location is `(close-low)/(high-low)`. **VERIFIED**

Quality starts at 5. H1 relative return adds 2 at >=3%, 1 at >=1%, subtracts 2 at <=-3%, and subtracts 1 below zero. Volume ratio >=1.25 adds 1. Close location >=0.6 adds 0.5 and <=0.4 subtracts 0.5. The score is clamped 0–10. H3 is recorded but does not enter the quality score. **VERIFIED**

The CERI price feature key includes company/event, reaction session/policy, configuration, calculation version, and cutoff (plus context in pipeline use). Different cutoffs can coexist; the same event key is updated in place and has no independent row-revision ledger. **VERIFIED**

## Snapshots and artifact lineage

`CeriRunCaptureService` creates snapshots for upload-run tickers from revision features that exactly match the as-of session, are no later than the cutoff, and match pipeline calculation context/config/calculation version. It then computes source-backed earnings/estimates, surprise, guidance, catalysts, price response, confidence, opportunity, risk, posture, and ledgers. **VERIFIED**

Each `CeriScoreSnapshot` retains run, source-run text, company/ticker, as-of session, cutoff, calculation-context ID, calendar version, opportunity/risk/confidence/coverage/posture, component and alignment ledgers, selected feature/evidence IDs, configuration version/hash, calculation version, evidence contract, comparison state, and evidence hash. **VERIFIED**

The canonical SHA-256 evidence hash binds company/ticker/session/cutoff, scores, confidence, posture, component and alignment ledgers, source IDs, configuration/calculation versions, and temporal evidence lineage. It is a reproducibility fingerprint, not a signature of executable bytes or external provider truth. **VERIFIED**

Snapshot uniqueness is run + company + config hash + calculation version. Existing matching snapshots are skipped even with capture `force`; controlled replay writes parallel target-version artifacts rather than replacing originals. **VERIFIED**

Pipeline-owned artifacts must have `ownership_mode=PIPELINE`, calculation context ID, aware calculation cutoff, and calendar version. Standalone and legacy-unknown ownership bypass that strict validator by design. **VERIFIED**

Provider-license purge can redact/tombstone source and normalized evidence and invalidate feature/snapshot/change/alert rows. Snapshot confidence/posture/warnings may be changed while the stored evidence hash is not recomputed. Immutability is therefore conditional and a pre-purge hash is not a post-purge content checksum. **VERIFIED — CERI-010**

## Changes and alerts

Change event deduplication is deterministic across company, type, session, from/to values, catalyst/guidance identity, config hash, and calculation version. Scope is not part of the business key. Existing changes are preserved; recomputation can append missing changes but does not normally delete them. **VERIFIED**

Run-scoped change rebuild correctly limits score snapshots to the run's companies. It then reads current catalyst revisions and all guidance for those companies without applying the forwarded `as_of_session`/`cutoff_at`; the request model accepts only from/to sessions and `changed_since`. A historical run-triggered rebuild can therefore create catalyst/guidance changes from evidence that was not eligible for that run. **VERIFIED — CERI-004**

| Alert type | Trigger | Threshold/cooldown | Prior-state/session semantics | Dedup/persistence/replay | Status |
|---|---|---|---|---|---|
| `OPPORTUNITY_UPGRADED` | Matching change event, normally crossing 7.5 | 5 sessions | Depends on comparable prior snapshot | Unique business/event key; append; replay may add if not already present | VERIFIED |
| `NEW_BINARY_EVENT` | New binary catalyst change | 1 session | Canonical catalyst revision identity; cooldown bypass for canonical event | Append/dedup by event identity | VERIFIED |
| `RISK_ESCALATED` | Risk increase >=2 | 1 session | Prior comparable snapshot and accepted evidence | Append/dedup | VERIFIED |
| `DATA_STALE` | Fresh-to-stale change | 5 sessions | Snapshot warnings; cooldown uses alert/change creation times mapped to trading sessions | Append/dedup | VERIFIED |

Other detected change types do not have configured alert rules. Cooldown is based on persisted alert/change creation timestamps mapped to trading sessions, not necessarily the evidence effective session. Acknowledgement/dismissal mutates alert status; purge invalidates alerts. Historical recomputation can create or suppress through dedup/cooldown, but no ordinary path deletes an alert. **VERIFIED**

Persisted `CeriAlertRule` rows are reused by rule ID without synchronizing later YAML severity, cooldown, or config version. A configuration change can therefore leave runtime alert behavior on stale persisted values. **VERIFIED — CERI-008**

## SEC preflight, bootstrap, and equivalence

Pipeline preflight resolves the deployed SEC processor signature, requires one explicitly promoted ACTIVE matching release, and diagnoses every required ticker/CIK for that signature. Incomplete readiness raises `CeriBootstrapRequiredError` before enqueue/execution. **VERIFIED**

Ticker readiness distinguishes no CIK, bootstrap required, bootstrapping, ready, reusable terminal extraction, and failed state. `REQUIRE_READY` rejects a non-ready active sync; `ALLOW_DEGRADED` can permit a degraded ingest. **VERIFIED**

SEC sync state is keyed by CIK/dataset/processor signature. Document extraction state is keyed by document/dataset/signature. A signature change does not reuse certification from the old signature and requires bootstrap; already completed extraction for the active signature can be reused. Public filing bodies are retained in a content-addressed cache independently of derived extraction. **VERIFIED**

Preflight and normal execution both call the same processor-signature builder and compare to the ACTIVE lifecycle record. This guarantees equality of declared semantic versions at those two gates. It does not guarantee byte-for-byte builder equivalence because the signature omits code bytes and dependency identity. **PARTIALLY_VERIFIED**

## Contamination boundary

### Allowed influences

| Boundary | Allowed evidence | Status |
|---|---|---|
| Evidence | Non-quarantined, non-purged source-backed normalized rows received by cutoff; run-bound uploaded earnings date; eligible PriceBar projection | VERIFIED |
| Revisions | Corrections/revisions whose own receipt/observation time is at or before cutoff | VERIFIED |
| Sessions | Evidence effective session and market bar date no later than snapshot/latest-completed session; event reaction windows only when elapsed | VERIFIED |
| Providers | Configured capable providers; all observations preserved, subject to eligibility | VERIFIED |
| Run IDs | Pipeline snapshot must belong to the requested upload run; feature inputs must match context and temporal bounds | VERIFIED |
| Pipeline IDs | Indirectly through upload run/background workflow/calculation context; no universal pipeline ID column on every normalized row | PARTIALLY_VERIFIED |
| Processors | Deployed CERI code/config; SEC additionally requires matching ACTIVE/certified signature | VERIFIED |
| Writers | Registered ingestion, normalization, feature, capture, change, alert, replay, review, and purge services | VERIFIED |

### Forbidden influences

Evidence received after cutoff, evidence effective after the snapshot session, quarantined/purged evidence, revisions observed after cutoff, future/incomplete price sessions, cross-context pipeline features, unmatched SEC signatures, and unrelated-run snapshots are forbidden. **VERIFIED**

Current implementation does not fully exclude mutable current PriceBar reads without cutoff, mixed OHLCV bases, row-ID-driven provider selection, future catalyst/guidance state in run-scoped change rebuild, or unproven per-value timing for uploaded upcoming earnings dates. These are the explicit contamination openings. **VERIFIED**

## CERI Component Register

| Component | Purpose | Inputs | Outputs | Session | Cutoff | Processor | Fingerprint | Writer | Historical exposure | Risk | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Provider ingestion | Acquire evidence | Provider/ticker/dataset | ingestion run + source records | Request scope | Retrieval time | Provider adapter/orchestration | request key + content/idempotency hash | `CeriIngestionService` | Static direct keys can suppress refresh | High | VERIFIED |
| Source evidence | Preserve observation/revision | Provider payload + metadata | `CeriSourceRecord` | Event metadata | retrieved/ingested | source-record service | SHA-256 canonical content/idempotency | source-record service | Corrections append/supersede; purge mutates | Medium | VERIFIED |
| Normalization | Typed economic evidence | Source records | estimates/earnings/guidance/catalysts | effective session | receipt cutoff on read | dataset normalizers | source ID + normalizer/version fields | normalization service | Rebuild/version compatibility partial | Medium | VERIFIED |
| PIT query | Reconstruct decision view | Normalized evidence + source receipts | eligible current/corrected rows | as-of session | knowledge cutoff | point-in-time query | selected row/source IDs | read-only | Provider priority not applied | High | VERIFIED |
| Estimate revision | Measure changes | PIT estimates | revision features | exact as-of | cutoff/context | revision feature service | event/config/calc/context key | feature rebuild | Parallel cutoff versions; upsert same key | Medium | VERIFIED |
| Surprise | Compare actual vs known estimate | earnings + estimates | surprise ledger/component | report/as-of | pre-report known time and capture cutoff | surprise feature service | selected evidence IDs in snapshot hash | capture/snapshot | Legacy known-time fallback | Medium | VERIFIED |
| Guidance | Directional management update | accepted guidance | guidance component | effective <= as-of | receipt cutoff | comparison + scoring | evidence IDs/config | normalization/capture | Change rebuild can read unbounded rows | High | VERIFIED |
| Catalyst | Opportunity/binary events | event revisions | catalyst features/components | effective <= as-of | receipt cutoff | catalyst feature service | event/revision/dedup key | normalizer/feature rebuild | Current revision read in change rebuild | High | VERIFIED |
| Price response | Market reaction | timestamped event + stock/SPY OHLCV | price feature/quality | reaction/H1/H3 <= as-of | bar observation cutoff | price response service | event/config/calc/policy/cutoff/context | feature rebuild/capture | Current/basis bypasses | High | VERIFIED |
| Confidence/freshness | Evidence reliability | revision features + ingestion runs | score/label/warnings | snapshot session | run at/before cutoff | confidence/freshness services | snapshot ledger/hash | capture | Provenance cap omitted | High | VERIFIED |
| Opportunity | Aggregate upside evidence | seven components | 0–10 or unrated | snapshot session | inherited | opportunity score service | component ledger/hash | capture | Missing inputs reweighted | Medium | VERIFIED |
| Event risk | Aggregate near-term risk | earnings date/catalysts/penalties | 0–10 risk | snapshot session | inherited | event risk service | risk ledger/hash | capture | stale signal ordering and upload provenance | High | VERIFIED |
| Posture | User-facing class | opportunity/risk/confidence | label | snapshot session | inherited | capture helper | snapshot hash | capture | Thresholds hard-coded | Medium | VERIFIED |
| Snapshot | Freeze result | all components/lineage | `CeriScoreSnapshot` | as-of | shared cutoff | snapshot/capture service | SHA-256 evidence hash | capture/replay | Purge exception | Medium | VERIFIED |
| Changes | Detect state transition | comparable snapshots/current events | `CeriChangeEvent` | current/prior | partial | change detection/rebuild | deterministic business key | capture/change rebuild | Unbounded event reads | High | VERIFIED |
| Alerts | Persist actionable changes | change events + rules | `CeriAlertEvent` | change/creation session | indirect | alert service | event/business key | alert rebuild | Rule staleness, replay/cooldown | Medium | VERIFIED |
| SEC readiness | Gate semantic processor | releases/sync/extraction state | readiness/preflight | pipeline universe | pre-enqueue current state | SEC lifecycle/preflight | processor signature | deployment/bootstrap/repair | Signature proves declared tuple only | Medium | VERIFIED |

## CERI Parameter Register

| Parameter | Feature/calculation | Default/current value | Source | Purpose | Unit | Effect | Code reference | Status |
|---|---|---:|---|---|---|---|---|---|
| Calculation version | All CERI | `ceri-1.3.0` | `config/ceri.yaml` | Compatibility/version partition | identifier | Separates artifacts/comparisons | `config/ceri.yaml:3` | VERIFIED |
| Config version | All CERI | `2026-08-26-freshness-semantics-r1` | YAML | Human config identity | identifier | Persisted with artifacts | `config/ceri.yaml:4` | VERIFIED |
| Daily cutoff | Temporal | 16:15 America/New_York | YAML | Decision observation boundary | local time | Forms cutoff when not pipeline-supplied | `config/ceri.yaml:5-7` | VERIFIED |
| Provider priority | Evidence | manual, primary, eodhd, sec | YAML | Resolve competing evidence | order | Intended selection; not production-wired | `config/ceri.yaml:10-16` | VERIFIED |
| Estimate stale | Freshness | 7 | YAML | Estimate feed warning | days | Lowers freshness/warns | `config/ceri.yaml:46-49` | VERIFIED |
| Catalyst stale | Catalyst | 2 | YAML | Catalyst freshness | days | Makes stale evidence unavailable/warn | `config/ceri.yaml:50-53` | VERIFIED |
| Earnings stale | Earnings | 30 | YAML | Dataset freshness | days | Metadata/eligibility context | `config/ceri.yaml:54-57` | VERIFIED |
| Guidance stale | Guidance | 14 | YAML | Guidance freshness | days | Excludes stale component | `config/ceri.yaml:58-61` | VERIFIED |
| Revision windows | Revisions | 7, 30, 90 | YAML | Baseline horizons | calendar days | Produces three revisions per slot | `config/ceri.yaml:79-83` | VERIFIED |
| Near-zero | Revisions/surprise | 0.01 | YAML | Avoid unstable denominator | value units | Null percent change | `config/ceri.yaml:83` | VERIFIED |
| Minimum analysts | Revisions/confidence | 3 | YAML | Sample quality | analysts | Confidence penalty below | `config/ceri.yaml:84` | VERIFIED |
| Minimum component coverage | Opportunity/confidence | 60 | YAML | Rating gate | percent | Opportunity unavailable below | `config/ceri.yaml:85` | VERIFIED |
| Baseline tolerance | Revisions | 3 | YAML | Post-target fallback | calendar days | Allows first baseline after target | `config/ceri.yaml:86` | VERIFIED |
| Period weights | Revision aggregate | 0.35/0.30/0.20/0.15 | YAML | Favor nearer periods | fraction | Used by period aggregate, not main opportunity mean | `config/ceri.yaml:88-92` | VERIFIED |
| Opportunity weights | Opportunity | .25/.15/.10/.15/.15/.15/.05 | YAML | Combine components | fraction | Available-weight normalized sum | `config/ceri.yaml:109-116` | VERIFIED |
| Earnings risk windows | Event risk | 2/5 trading days | YAML/code | Block/high proximity | sessions | Scores 5/3; code also uses 10-day 1.5 band | `config/ceri.yaml:119-120`; `event_risk_service.py:180-192` | VERIFIED |
| Secondary penalty cap | Event risk | 2.0 | YAML | Bound secondary pressure | score points | Caps additive penalties | `config/ceri.yaml:126` | VERIFIED |
| Staleness penalty | Event risk | 1.0 | YAML | Penalize stale estimates | score points | Currently unreachable in normal ordering | `config/ceri.yaml:127` | VERIFIED |
| Confidence thresholds | Confidence | High 8, Normal 6, Low 3.5 | YAML | Label score | score | Maps label | `config/ceri.yaml:130-132` | VERIFIED |
| Critical provenance cap | Confidence | Low | YAML | Fail-safe provenance cap | label | Parsed/applied nowhere | `config/ceri.yaml:134` | VERIFIED |
| Change thresholds | Changes | score 1; upgrade 7.5; revision 2; risk 2; accel .01 | YAML | Material transition gates | mixed | Creates change types | `config/ceri.yaml:143-148` | VERIFIED |
| Alert cooldowns | Alerts | 5/1/1/5 | YAML | Suppress repeated alerts | sessions | Rule-specific cooldown | `config/ceri.yaml:176-197` | VERIFIED |
| Price benchmark | Price response | SPY | YAML | Market-relative return | ticker | Benchmark subtraction | `config/ceri.yaml:236` | VERIFIED |
| Price windows | Price response | H1, H3 | YAML | Reaction horizons | sessions | H1 scores; H3 context | `config/ceri.yaml:237-239` | VERIFIED |
| Volume lookback | Price response | 20 | YAML | Reaction volume context | preceding rows | Volume confirmation | `config/ceri.yaml:240` | VERIFIED |
| Relative thresholds | Price response | 1%, 3% | YAML | Positive/strong response | decimal return | +1/+2 and symmetric code negatives | `config/ceri.yaml:241-242` | VERIFIED |
| Volume threshold | Price response | 1.25 | YAML | Confirm reaction | ratio | +1 quality | `config/ceri.yaml:243` | VERIFIED |

## CERI Writer Register

| Writer | Table/artifact | Trigger | Current/historical | Run-bound | Session-bound | Mutation type | Guard | Risk |
|---|---|---|---|---|---|---|---|---|
| Ingestion orchestration | ingestion runs/source records | provider/admin/workflow job | Both | Processing/workflow-bound | Request metadata | Insert; run status update | request key/content idempotency | High — static key refresh suppression |
| Source-record service | `ceri_source_records` | acquired record | Both | ingestion-bound | event metadata | Insert/supersession; purge later | content hash + provider identity | Medium |
| Normalization service | estimate/earnings/guidance/catalyst tables | normalize job | Both | processing-run-bound | effective session | Insert/revision/current flag mutation | source uniqueness/validation | Medium |
| Feature rebuild | revision/catalyst/price features | feature job/backfill | Both | optional upload run/context | yes | Upsert by event key | PIT source filter/context lineage | Medium |
| Capture service | score snapshots and sometimes price features | pipeline/direct capture | Both | yes | exact as-of + cutoff | Insert/skip existing | ownership/context/config/evidence hash | Medium |
| Controlled replay | parallel features/snapshots/replay audit | authorized replay | Historical | replay scope | requested | Insert parallel version | target version and audit record | Medium |
| Change detection/rebuild | change events | capture or rebuild job | Both | score path yes; event path partial | partial | Insert/dedup | comparison/version guard | High — event cutoff gap |
| Alert rebuild | alert events/rules | change job | Both | indirect | indirect | Insert; ack/dismiss update | rule/event dedup + cooldown | Medium |
| SEC lifecycle/bootstrap | processor releases/sync/extraction | deploy/promote/repair/ingest | Both | pipeline/CIK scoped | not market-session-bound | Insert/status/current release update | ACTIVE signature/readiness | Medium |
| Manual review | quarantine/review state | reviewer action | Current and evidence history | actor/process-bound | evidence session retained | Status mutation | audit/review fields | Medium |
| Purge service | source, normalized, feature, snapshot, change, alert rows | licensed-data confirmed purge | Historical | purge-audit-bound | all affected | Tombstone/redact/invalidate/update | preview + confirmation + audit | High |

## Significant findings

### CERI-001 — Configured provider resolution is not used

- **ID:** CERI-001
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Evidence selection / estimates
- **Finding:** Production PIT selection does not call `CeriProviderConflictService` or `CeriEstimateDeduplicator`; configured provider priority can be replaced by effective-time/row-ID order.
- **Evidence:** Repository-wide references to both services are limited to their definitions and unit tests; `point_in_time_query.py` selects eligible rows without provider-priority resolution.
- **Why it matters:** Same-time provider evidence can change the selected consensus and every downstream revision, surprise, confidence, and opportunity result.
- **Potential contamination/correctness effect:** Row insertion order or backfill order can alter historical results without a business-semantic difference.
- **Existing guard:** All observations are preserved with source IDs and receipt cutoffs.
- **Missing guard:** One wired, deterministic provider/conflict selection policy with persisted resolution rationale.
- **Recommended future remediation:** Route PIT slot resolution through the configured conflict resolver/deduplicator and persist candidates, winner, and rule version.

### CERI-002 — Price response violates the preferred OHLCV basis contract

- **ID:** CERI-002
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Price response
- **Finding:** CERI uses a bespoke all-basis `PriceBar` reader rather than Task 02's preferred adjusted-price/TRADES-volume reader.
- **Evidence:** `price_response_service.py:447-475`; dictionary construction at `:144-145`; isolated duplicate-basis order test produced different returns and volume ratios.
- **Why it matters:** The economic meaning of price and volume is unstable and query-order dependent.
- **Potential contamination/correctness effect:** Gap, H1/H3 returns, relative return, volume confirmation, bar IDs, and quality score can all vary.
- **Existing guard:** Session/cutoff filtering and revision reconstruction in normal production paths.
- **Missing guard:** Deterministic preferred-basis frame construction and one row per ticker/session.
- **Recommended future remediation:** Consume `load_preferred_ohlcv_frames` or enforce its exact basis/coverage contract before calculation.

### CERI-003 — Mutable/current price state can bypass PIT reconstruction

- **ID:** CERI-003
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Price response API boundary
- **Finding:** `cutoff_at` is optional, and injected stock/benchmark lists bypass the internal bounded reader.
- **Evidence:** `price_response_service.py:55-117,447-475`; tests exercise a no-cutoff service call.
- **Why it matters:** A caller can calculate a historical-looking response from today's mutable bar projection.
- **Potential contamination/correctness effect:** Later bar revisions can alter an old event score; supplied lists can include future/ineligible sessions.
- **Existing guard:** Pipeline capture and production feature rebuild pass shared cutoff/session and reconstruct revisions.
- **Missing guard:** Required bounded input type/cutoff plus validation of injected frames.
- **Recommended future remediation:** Make decision context mandatory and accept only a validated PIT frame carrying session/basis/cutoff lineage.

### CERI-004 — Run-scoped change rebuild reads temporally unbounded event evidence

- **ID:** CERI-004
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Change detection and alerts
- **Finding:** A capture child job forwards as-of/cutoff fields, but change rebuild ignores them for catalyst and guidance reads.
- **Evidence:** `job_handlers.py:906-924,979-989`; `change_rebuild_service.py:194-264`.
- **Why it matters:** A historical run can acquire changes and alerts caused by later evidence.
- **Potential contamination/correctness effect:** False historical catalyst/guidance changes, dedup suppression of later legitimate jobs, and alerts with incorrect temporal ownership.
- **Existing guard:** Snapshot comparisons are run/company/version scoped; optional from/to/changed-since filters exist.
- **Missing guard:** Required capture cutoff/session/source-receipt filters on event evidence.
- **Recommended future remediation:** Add cutoff/as-of fields to the request and reuse the same evidence-eligibility query as capture.

### CERI-005 — Critical provenance confidence cap is inert

- **ID:** CERI-005
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Confidence
- **Finding:** YAML declares `critical_provenance_cap: Low`, but config parsing and confidence evaluation do not implement it.
- **Evidence:** `config/ceri.yaml:134`; `confidence_service.py:95-118`; confidence config model inspection.
- **Why it matters:** A result with critical provenance defects may retain Normal confidence.
- **Potential contamination/correctness effect:** Consumers can over-trust incompletely proven evidence.
- **Existing guard:** Zero coverage forces Insufficient and any warning caps High to Normal.
- **Missing guard:** Defined critical-warning set and Low cap.
- **Recommended future remediation:** Parse, validate, apply, and ledger the configured cap.

### CERI-006 — Legacy surprise knowledge time can use provider time instead of possession time

- **ID:** CERI-006
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT GAP
- **Subsystem:** Earnings surprise
- **Finding:** When normalized `known_at` is absent, surprise selection falls back to provider observation/source time before retrieval time.
- **Evidence:** `surprise_feature_service.py` `_known_at` fallback ordering and pre-report estimate selection.
- **Why it matters:** Provider-effective before report is not equivalent to received before report.
- **Potential contamination/correctness effect:** A later-retrieved consensus can enter a historical surprise baseline for legacy rows.
- **Existing guard:** Current normalizers populate receipt-based `known_at`; capture also bounds source receipt by its overall cutoff.
- **Missing guard:** Pre-report source receipt requirement for every estimate, including legacy data.
- **Recommended future remediation:** Resolve knowledge time only from source-record receipt and quarantine rows lacking it for surprise use.

### CERI-007 — Event-risk estimate-staleness penalty is disconnected

- **ID:** CERI-007
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Event risk / freshness
- **Finding:** Event risk looks for `estimate_data_stale` on revision features before confidence computes that warning; no normal feature writer emits it.
- **Evidence:** `capture_service.py:195-200`; `confidence_service.py:95-109`; repository-wide warning search.
- **Why it matters:** The configured staleness risk penalty is not applied in ordinary capture.
- **Potential contamination/correctness effect:** Event risk can be one point too low and stale transitions may be inconsistent across ledgers.
- **Existing guard:** Confidence still flags stale estimate-feed age.
- **Missing guard:** Shared freshness result calculated before both confidence and risk.
- **Recommended future remediation:** Compute freshness once and pass the same typed result to both services.

### CERI-008 — Persisted alert rules can outlive configuration changes

- **ID:** CERI-008
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT GAP
- **Subsystem:** Alerts
- **Finding:** Existing database rules are selected by rule ID without synchronizing config version, severity, or cooldown from YAML.
- **Evidence:** `alert_service.py` rule materialization/lookup path and `config/ceri.yaml:171-197`.
- **Why it matters:** Operators can believe a deployed config changed alert policy while the database still applies older values.
- **Potential contamination/correctness effect:** Alerts can be suppressed, emitted, or assigned severity under stale policy.
- **Existing guard:** Rules and alert event identity are persisted and queryable.
- **Missing guard:** Rule version match/upsert or immutable versioned rule selection.
- **Recommended future remediation:** Version rules by config hash and bind each alert to the exact resolved rule version.

### CERI-009 — Deployment identity records a stale schema revision

- **ID:** CERI-009
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Processing-run lineage
- **Finding:** CERI deployment identity hard-codes schema revision `0050_sec_processor_promotion`; repository migration head is 0074.
- **Evidence:** `deployment_identity.py:37-44`; `alembic/versions/20260913_0074_ceri_evidence_quarantine.py`.
- **Why it matters:** Processing-run lineage reports an incorrect database contract identity.
- **Potential contamination/correctness effect:** Forensic reproduction can select the wrong schema assumptions.
- **Existing guard:** Background-worker identity independently queries live `alembic_version`.
- **Missing guard:** CERI processing runs do not use that live/compiled migration identity.
- **Recommended future remediation:** Resolve schema revision from the same authoritative deployment/worker source and fail closed when unavailable.

### CERI-010 — Purge mutates hashed historical snapshots without rehashing

- **ID:** CERI-010
- **Severity:** P2
- **Status:** VERIFIED — CONTRACT GAP
- **Subsystem:** Retention / snapshot lineage
- **Finding:** Confirmed provider-license purge changes historical snapshot state while leaving the original evidence hash.
- **Evidence:** `purge_service.py` invalidation/tombstoning paths; snapshot hash inputs in `snapshot_service.py`; retention policy in YAML.
- **Why it matters:** The hash ceases to describe current row content after an authorized purge.
- **Potential contamination/correctness effect:** Integrity checks may confuse deliberate redaction with corruption or treat a stale hash as reproducible evidence.
- **Existing guard:** Purge requires preview, confirmation, and audit and marks artifacts invalidated.
- **Missing guard:** Explicit pre-purge hash/archive and post-purge tombstone hash/version semantics.
- **Recommended future remediation:** Preserve the original hash as sealed lineage and add a separate post-purge state hash plus purge reference.

### CERI-011 — Uploaded upcoming earnings date lacks per-value PIT provenance

- **ID:** CERI-011
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT GAP
- **Subsystem:** Event risk
- **Finding:** Earnings proximity consumes `RawCompanyRow.upcoming_earnings_date` without provider, publication, observation, retrieval, or field-effective timestamp.
- **Evidence:** Capture/event-risk path from raw upload row; raw-row schema and Task 02 upload lineage findings.
- **Why it matters:** The risk score cannot prove when the date became known or whether a rescheduled date was historically valid.
- **Potential contamination/correctness effect:** Historical event risk and posture can include an unanchored current/future schedule value.
- **Existing guard:** The raw row is bound to the upload run and snapshot session.
- **Missing guard:** Source-record and schedule-revision lineage for the earnings date.
- **Recommended future remediation:** Normalize earnings schedules as source-backed, revisioned evidence with receipt and effective session.

### CERI-012 — Default direct ingestion request identity can suppress refresh

- **ID:** CERI-012
- **Severity:** P1
- **Status:** VERIFIED — CONTRACT VIOLATION
- **Subsystem:** Provider ingestion
- **Finding:** The default key `ceri:{provider}:{dataset}:{ticker}` is stable, and a completed/partial existing run is reused; repeated equivalent admin payloads also derive stable enqueue identity.
- **Evidence:** `orchestration.py:207` request-key construction and existing-run short circuit; admin enqueue path; batched workflow keys at `batched_workflow.py:104-199`.
- **Why it matters:** A direct recurring ingest can stop acquiring later provider observations.
- **Potential contamination/correctness effect:** CERI remains stale while appearing idempotently complete.
- **Existing guard:** Batched pipeline workflow adds run/config/batch identity and avoids cross-workflow collision; callers can supply a unique key.
- **Missing guard:** Observation-window or scheduled-at identity in direct refresh requests.
- **Recommended future remediation:** Separate retry idempotency from refresh identity and include an explicit acquisition window/cycle.

## Audit Coverage

### Fully audited

- Provider source-record construction, receipt-time eligibility, correction and purge semantics. **VERIFIED**
- Estimate revision, surprise, guidance, catalyst, opportunity, risk, confidence, posture, price-response, snapshot, change, and alert calculation code. **VERIFIED**
- CERI runtime gates, legacy/batched pipeline integration, processing-run identity, controlled replay, and artifact ownership validation. **VERIFIED**
- SEC signature, lifecycle, readiness, bootstrap, cache/reuse, preflight, and worker fencing code. **VERIFIED**
- Task 02 PIT dependency comparison and all requested price-derived temporal fields. **VERIFIED**

### Partially audited

- Provider adapters were inspected for schema/timestamp behavior, but no live provider responses, contracts, rate limits, or production credentials were exercised. **PARTIALLY_VERIFIED**
- Database constraints and migrations were inspected statically; no deployed schema/data distribution was queried. **PARTIALLY_VERIFIED**
- UI/export surfaces were traced only where they consume or mutate CERI lineage; presentation correctness was not browser-tested in this task. **PARTIALLY_VERIFIED**

### Not audited

- Production provider truth, live SEC availability, live database contents, operational backlog, and deployed feature-flag values. **UNKNOWN**
- Statistical calibration or investment efficacy of the formulas; this audit establishes implementation and lineage, not predictive validity. **UNKNOWN**
- Task 04 and later prompt-pack scopes. **VERIFIED — intentionally excluded**

## Known Unknowns

- The live database migration revision, ACTIVE SEC signature, readiness state, stored rule versions, source-row distribution, and whether any purge has occurred were not queried. **UNKNOWN**
- The deployed environment may override default feature flags and configuration paths. **UNKNOWN**
- Provider guarantees for published/observed timestamps and correction identifiers cannot be established from repository code alone. **UNKNOWN**
- Whether legacy rows with null `known_at` exist in production is unknown; the vulnerable compatibility path exists. **UNKNOWN**
- Whether duplicate ADJUSTED_LAST/TRADES rows coexist for all CERI tickers in production is unknown; the standard ingestion model permits and normally creates both bases. **PARTIALLY_VERIFIED**

## Contradictions

- YAML states provider-priority resolution and preserve-all conflict semantics, while production PIT selection does not call the implemented conflict resolver. **CONTRADICTORY**
- YAML declares `critical_provenance_cap: Low`, while the confidence parser/service omits it. **CONTRADICTORY**
- Retention says retain immutable evidence, while authorized purge mutates/tombstones historical evidence and snapshots. This is a policy exception but the hash semantics do not distinguish it. **CONTRADICTORY**
- CERI processing identity claims schema revision 0050 while the repository migration head is 0074. **CONTRADICTORY**
- Task 02 defines one authoritative preferred PIT OHLCV reader, while CERI price response uses a different reader and basis policy. **CONTRADICTORY**

## Potential Contract Violations

- CERI-001: provider-priority/conflict selection not wired. **VERIFIED**
- CERI-002: price basis does not follow the authoritative preferred OHLCV contract. **VERIFIED**
- CERI-003: optional cutoff and injected-bar paths bypass PIT enforcement. **VERIFIED**
- CERI-004: run-scoped change rebuild can consume post-run event evidence. **VERIFIED**
- CERI-005: critical provenance cap is configured but inert. **VERIFIED**
- CERI-007: risk staleness penalty is disconnected from computed freshness. **VERIFIED**
- CERI-009: processing identity embeds a stale schema revision. **VERIFIED**
- CERI-012: stable direct-ingestion identity can prevent later acquisitions. **VERIFIED**

## High-Risk Cross-Subsystem Dependencies

| Source subsystem | CERI consumer | Dependency | Failure propagation | Status |
|---|---|---|---|---|
| Task 02 `PriceBar`/revisions | Price response/opportunity | PIT reconstruction, completed session, basis choice | Revisions/basis affect H1 score and posture | VERIFIED |
| Upload/raw-row mapping | Event risk/posture | Upcoming earnings date | Unproven schedule time changes risk/posture | VERIFIED |
| Provider ingestion/request identity | All evidence features | Continued refresh and receipt timestamps | Reused request can freeze entire CERI | VERIFIED |
| Source records | PIT query/surprise/guidance/catalyst | Receipt-time eligibility and correction chain | Wrong knowledge time creates lookahead | VERIFIED |
| Market calculation context | Feature rebuild/capture | Shared cutoff/session/calendar | Context mismatch should exclude features | VERIFIED |
| Confidence freshness | Event risk/change alerts | `estimate_data_stale` warning | Ordering disconnect suppresses risk penalty | VERIFIED |
| Change rebuild | Alerts | Current catalyst/guidance state | Later evidence can contaminate historical alert | VERIFIED |
| SEC lifecycle | Provider ingest/preflight | ACTIVE semantic signature/bootstrap | Mismatch blocks or degrades evidence | VERIFIED |
| Purge | Snapshots/changes/alerts | Evidence invalidation | Historical hashes/content diverge | VERIFIED |

## Files Inspected

- `config/ceri.yaml`; `app/settings.py`; CERI models in `app/models/ceri_tables.py`; relevant migration files through `alembic/versions/20260913_0074_ceri_evidence_quarantine.py`. **VERIFIED**
- CERI orchestration, batched workflow/handlers, job handlers, processing runs, feature flags, config, validation, source records, evidence eligibility/state, PIT query/eligibility, normalization, all four evidence normalizers, provider registry/protocol/adapters, conflict and deduplication services. **VERIFIED**
- Revision, surprise, guidance comparison, catalyst feature, freshness, confidence, opportunity, event risk, price response, feature rebuild, capture, snapshot, controlled replay, change detection/rebuild/semantics, alert, backfill, purge, query/export, and artifact-lineage services. **VERIFIED**
- SEC processor signature/capability/lifecycle, preflight, readiness diagnostics/service/repair, incremental ingestion, state service, client, content cache, parser/extractor/locator/selection paths. **VERIFIED**
- Cross-subsystem market clock/cutoff, `price_bar_repository.py`, pipeline service/executor, raw upload models, and background-worker deployment identity. **VERIFIED**

## Tests Inspected

- CERI unit suites for schema, config/flags, normalization, provider conflict, estimate/catalyst deduplication, PIT enforcement, effective session, revisions, surprise, guidance, catalysts, confidence, opportunity/risk/scoring, price response, feature rebuild, snapshots, changes, alerts, purge, orchestration, controlled replay, SEC lifecycle/readiness/preflight, capability matrix, and evidence integrity. **VERIFIED**
- Focused execution: 106 tests passed across PIT, feature/scoring, change/alert, ingestion/replay, and SEC suites; 24 additional tests passed across wave-4 integrity, effective-session, and remediation suites. One Starlette deprecation warning appeared in each pytest invocation; no test failed. **VERIFIED**
- Live-provider, live-database, full end-to-end browser, and production pipeline tests were not run. **VERIFIED**

## SQL Inspected

- SQLAlchemy model constraints/indexes and query construction for CERI source, normalized evidence, processing, features, snapshots, changes, alerts, SEC state/releases, and `PriceBar`/revision reads. **VERIFIED**
- Alembic migration chain through 0074, including CERI PIT/context, artifact lineage, and evidence quarantine changes. **VERIFIED**
- No ad hoc SQL was executed against a live database and no migration was applied. **VERIFIED**

## Verification Metadata

| Item | Value | Status |
|---|---|---|
| HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | VERIFIED |
| Baseline drift | No | VERIFIED |
| Branch | `codex/winner-evidence-remediation` | VERIFIED |
| Runtime | CPython 3.12.2 | VERIFIED |
| Tests | 130 passed in two focused invocations | VERIFIED |
| Live external systems | Not accessed | VERIFIED |
| Deliverable | `docs/audit/calculation-lineage/03_ceri.md` only | VERIFIED |
