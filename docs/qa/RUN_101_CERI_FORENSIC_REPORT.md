# Run 101 CERI Forensic Certification

**Investigation date:** 2026-08-13
**Subject:** `upload_runs.id = 101`
**Method:** direct PostgreSQL queries in explicit read-only transactions, source inspection, independent all-ticker reconstruction, source ablation, API/UI reconciliation, and manual evidence review
**Evidence preservation:** no production rows were inserted, updated, deleted, backfilled, migrated, or re-fetched. The audit harness writes only files under `docs/qa/`.

## 1. Executive Verdict

**CERI RUN 101 CERTIFICATION: REJECTED.**

Run 101 did produce exactly one snapshot for each of its 200 requested tickers in approximately two hours. The snapshot scoring layer also did one important thing correctly: it did not turn absent evidence into zero or neutral evidence. All 200 snapshots were below the configured 60% coverage threshold, had `opportunity_score = NULL`, were labeled `Unrated`, and had `data_confidence = Insufficient`.

That successful gating does **not** make the run technically valid. EODHD made 600 calls and generated 9,884 source records but contributed zero usable CERI components. SEC generated no new records during the run because 192 of 200 ticker syncs failed the ACTIVE-mode shadow-bootstrap prerequisite. Five stale, legacy SEC extractions were nevertheless selected as high-confidence guidance for four tickers; manual review shows all five are HTML/XBRL or boilerplate false positives. Finally, lifecycle generation classified every one of the 200 first, null-scored snapshots as `OPPORTUNITY_UPGRADED`, and 40 false upgrades became notable alerts.

Plain answers:

| Question | Verdict | Answer |
| --- | --- | --- |
| Is run 101 technically valid? | **NO** | Count completion is valid; the end-to-end evidence, lifecycle, and alert result is not. |
| Are all 200 snapshots trustworthy? | **NO** | Their `Unrated` arithmetic is reproducible, but their evidence lineage includes unusable and false evidence, and downstream changes are wrong. |
| Is EODHD acquisition correct? | **FAIL** | 598/600 calls succeeded and persistence reconciles, but timestamp provenance is locally synthesized, one symbol fails both endpoints, and the returned projection is not usable by CERI. |
| Is EODHD processing correct? | **FAIL** | Currency verification rejects all 9,156 estimates; earnings have no actual/consensus values; catalysts are all ineligible. |
| Is SEC acquisition correct? | **FAIL** | 192 ticker syncs are `PARTIAL`; no run-time SEC records, downloads, or extractions occurred. |
| Is SEC processing correct? | **FAIL** | Five stale legacy false positives were treated as available guidance because `accepted_for_scoring = NULL` is not rejected. |
| Is CERI calculation correct? | **PARTIAL** | Snapshot arithmetic, coverage gating, event risk, and hashes reproduce 200/200; evidence selection and lifecycle semantics do not. |
| Is point-in-time integrity preserved? | **BLOCKED** | No referenced source timestamp is after cutoff and no numeric look-ahead affected a score, but EODHD provider time is not historically provable and full source replay is impossible. |
| Is evidence coverage handled correctly? | **PASS at snapshot layer** | 196 snapshots have 0% and four have 15%; all are correctly `Unrated`. |
| Is EODHD worth keeping? | **NO MATERIAL VALUE for run 101** | Zero component, coverage, score, rating, ranking, lifecycle, or alert contribution. |
| Is SEC worth keeping? | **NO MATERIAL VALUE for run 101** | It changed coverage for four tickers but changed no score, rating, decision, lifecycle, or legitimate alert. |
| Is CERI production-ready? | **NO** | False evidence and false actionable lifecycle events are release-blocking. |

The performance improvement is real: provider work began at 17:17:20 and alert rebuild ended at 19:14:11. It is not a correctness certification.

## 2. Run 101 Reconciliation

### Run identity

| Fact | Evidence |
| --- | --- |
| Run ID | `upload_runs.id = 101` |
| File | `money money_2026-08-13.csv` |
| Uploaded | `2026-08-13 17:14:40.067267+02` |
| Upload processed | `2026-08-13 17:14:40.901056+02` |
| Input rows/tickers | 200 rows / 200 distinct normalized tickers |
| Upload status | `COMPLETED` |
| Provider work | `17:17:20.683+02` to `19:12:51.072+02` |
| Snapshot capture | `19:12:51.159+02` to `19:13:55.009+02` |
| Change generation | through `19:14:06.777+02` |
| Alert rebuild | through `19:14:11.221+02` |
| Snapshot cutoff | exactly `2026-08-13 19:12:51.330758+02` for all 200 |
| Calculation version | `ceri-1.1.0` |
| Configuration version | `2026-08-12-remediation-r3` |
| Configuration hash | `aff83bb918fee7febe22dc35c66489178bbacce9d41cb9e88fc5a31f9434d677` |
| SEC processor signature | `sec-guidance:910cfd73179f55a7` |

The database stores the CERI calculation/configuration identity and the SEC extraction signature, but not a deployed Git commit SHA. Exact source-code identity beyond those version strings is therefore not independently provable.

### Count proof

| Reconciliation item | Count |
| --- | ---: |
| Requested input rows | 200 |
| Distinct requested tickers | 200 |
| Snapshot rows | 200 |
| Distinct snapshot tickers | 200 |
| Distinct snapshot companies | 200 |
| Duplicate snapshot tickers | 0 |
| Missing requested tickers | 0 |
| Unexpected snapshot tickers | 0 |
| Null opportunity scores | 200 |
| `Unrated` postures | 200 |
| `Insufficient` confidence labels | 200 |

The proof is a set reconciliation, not a row-count assertion: normalized input tickers minus snapshot tickers is empty; snapshot tickers minus input tickers is empty; grouping snapshots by ticker finds no count other than one.

Run 101 created 57 run-related background jobs. The 32 provider-batch jobs ended as 22 `COMPLETED` and 10 `PARTIAL`; 16 normalization jobs, four feature jobs, the finalizer, capture, change, and alert jobs reached terminal states. No job was retried, abandoned, expired, or cancelled. The top-level full pipeline was `PARTIAL` because of an unrelated incomplete input row, while the later batched CERI workflow continued.

### Actual evidence graph

```text
upload_runs / upload_run_rows (101)
  -> ceri_companies + ceri_company_aliases
  -> background_jobs / ceri_ingestion_runs
     -> EODHD ceri_source_records
        -> ceri_estimate_snapshots
        -> ceri_earnings_actuals
        -> ceri_catalyst_events / ceri_catalyst_event_revisions
     -> SEC ceri_sec_filing_documents / ceri_sec_document_extractions
        -> ceri_source_records -> ceri_guidance_events
  -> ceri_processing_runs
     -> ceri_revision_features / ceri_price_response_features / ceri_derived_features
  -> ceri_score_snapshots
  -> ceri_change_events
  -> ceri_alert_events
  -> CeriQueryService / API DTOs / CERI templates
```

## 3. EODHD Findings

### Acquisition and persistence

CERI uses three EODHD products through `EodhdCeriProvider`: `/api/calendar/trends`, `/api/calendar/earnings`, and `/api/news`.

| Dataset | Requests | HTTP success | HTTP failure | Provider rows fetched | Inserted source rows | Deduplicated | Corrected/superseding |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Estimates/trends | 200 | 199 | 1 | 9,156 | 9,156 | 0 | 7,722 |
| Earnings | 200 | 199 | 1 | 869 | 160 | 709 | 0 |
| Catalysts/news | 200 | 200 | 0 | 907 | 568 | 339 | 236 |
| **Total** | **600** | **598** | **2** | **10,932** | **9,884** | **1,048** | **7,958** |

Both failures are `MOG.A`, mapped to provider symbol `MOG.A.US`, with EODHD HTTP 404 for estimates and earnings. Application telemetry stored the wrapper status as 503 while preserving the provider 404 message, so transport reporting is internally inconsistent.

The run stored both `raw_json` and `restricted_normalized_json` for the EODHD licensed projection. Logical row payload size for run-created source records is approximately 13.64 MB: 12,632,988 bytes estimates, 131,072 bytes earnings, and 870,768 bytes catalysts. This excludes indexes and PostgreSQL page/TOAST overhead.

### Normalization and use

| Stage | Result |
| --- | --- |
| Estimate source rows | 9,156 |
| Normalized estimate snapshots | 9,156 |
| Missing canonical currency | 9,156 / 9,156 |
| Currency verified | 0 / 9,156 |
| Revision feature slots | 4,800 = 200 tickers x 24 configured slots |
| Usable revision features | 0 / 4,800 |
| Run-created normalized earnings actual rows | 160 |
| Non-null actual values | 0 |
| Non-null provider consensus values | 0 |
| Computed surprises | 0 |
| Current catalyst revisions referenced by universe | 2,219 across 169 tickers |
| Issuer-relevant catalysts | 0 |
| Binary-eligible catalysts | 0 |
| Materiality greater than zero | 0 |

The code requires a canonical currency and canonical scale before two estimates are comparable. The EODHD trend response rows contained no `currency`/`currencyCode`, and the normalizer correctly left currency unknown. This preserved `missing != zero`, but it made the entire estimate product unusable. No estimate revision direction, magnitude, acceleration, or breadth can be independently recomputed because there is no valid comparable pair.

The earnings endpoint supplied calendar observations, not usable actual-versus-consensus evidence. Consequently, no surprise calculation, period pairing, or pre-announcement consensus test exists to certify. Future-scheduled earnings rows through 2026-09-30 appear in snapshot lineage, but all have null actuals and do not affect a component.

The catalyst rows were persisted but uniformly failed semantic eligibility. They affected neither opportunity nor Event Risk. Event Risk in run 101 comes from the upload row's `upcoming_earnings_date`, not EODHD news.

### PIT, period, currency, and revision defects

- No run-created estimate has `known_at` or `effective_at` later than the snapshot cutoff.
- EODHD did not supply a stable provider observation timestamp. The provider adapter substituted retrieval time. Re-fetching therefore changes content/provenance and helps explain 7,722 correction/supersession rows, 84.3% of the estimate batch.
- Trend baselines are a retrospective provider projection retrieved on the run date, not immutable historical consensus snapshots. `known_at = retrieved_at` prevents them being treated as historically known before retrieval, but the original provider-time history cannot be proven.
- Period slots were created, but all revision comparisons stopped at currency comparability. There was no cross-period numerical comparison to leak into a score, and there is also no successful period-alignment calculation to certify.
- Currency was not inherited or guessed. That behavior is correct; the acquisition contract is insufficient.
- Surprise reconstruction is blocked because actual, consensus, and computed surprise values are all null.

**EODHD acquisition verdict: FAIL.** Persistence counts reconcile, but historical provenance is ambiguous and two mapped requests fail.
**EODHD processing verdict: FAIL.** It yielded zero usable CERI evidence.

## 4. SEC Findings

### Incremental acquisition

Run 101 created 200 SEC guidance ingestion runs:

| Status | Tickers | Requested | Fetched | Inserted | Failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| `PARTIAL` | 192 | 0 | 0 | 0 | 192 |
| `COMPLETED` | 8 | 0 | 0 | 0 | 0 |

All 192 failures say ACTIVE sync requires a successful SHADOW bootstrap for the ticker's CIK and processor signature. Three completed tickers (`HIFS`, `MOG.A`, `NBN`) had no resolved CIK. Five (`AIZ`, `AMZN`, `CLBT`, `JPM`, `SLDE`) had durable terminal registry state and correctly reused it without download or extraction.

For those five reusable CIKs, the registry contains 407 documents: 112 `COMPLETED_WITH_RECORDS` and 295 `COMPLETED_NO_RECORDS`, representing 319,222,805 recorded content bytes. All were downloaded before the run's SEC phase. No SEC HTTP telemetry, document download, extraction attempt, or new source record occurred during run 101.

This proves that durable terminal reuse works where bootstrap exists. It also proves that ACTIVE rollout coverage did not: 192/197 CIK-bearing tickers were refused. The orchestration continued to snapshots despite this dominant provider failure.

### Historical evidence and interpretation

The 200-company universe has 20,291 historical `ceri_guidance_events` rows over 1,653 ticker/accession pairs. Exactly zero rows have `accepted_for_scoring = TRUE`. Legacy rows have `accepted_for_scoring = NULL`; the scorer rejects only explicit `FALSE`, so unknown acceptance is treated as eligible.

Snapshot selection chose five events for four tickers, all with 15% guidance coverage and all stale by years:

| Ticker | Event | Filing/effective session | Stored classification | Manual finding |
| --- | --- | --- | --- | --- |
| MET | 4281 | `0001099219-23-000268`, 2023-11-02 | maintained revenue, 12-31 billion, HIGH | XBRL/HTML document header; `12-31` is the fiscal-year-end date, not a revenue range. |
| MLI | 191 | `0000089439-23-000016`, 2023-02-28 | raised FY revenue, 20211226-20221231%, HIGH | Pension-plan historical text/XBRL context dates, not guidance. |
| MLI | 192 | same accession/session | raised quarter revenue, 20211226-20221231%, HIGH | XBRL context ID date range, not guidance. |
| NIC | 18207 | `0001174850-23-000029`, 2023-08-04 | lowered quarter revenue, 1995%, HIGH | Private Securities Litigation Reform Act of 1995 boilerplate, not guidance. |
| NVDA | 23105 | `0001045810-21-000163`, 2021-11-22 | raised quarter revenue, 20210201-20211031%, HIGH | XBRL context dates in the hidden header, not guidance. |

Manual precision for the complete selected set is therefore 0/5. The classifications are not merely uncertain; they are demonstrably unrelated numeric tokens near forward-looking keywords. None should be evidence-bearing, let alone `HIGH` confidence.

No freshness limit removes years-old guidance. Each row remains temporally knowable at the 2026 cutoff, so it is not look-ahead leakage, but it is stale and semantically invalid. Source accession and locator are present, satisfying row-level lineage; the stored `comparison_basis` is raw HTML rather than a clean, bounded passage, so passage provenance is technically present but operationally poor.

**SEC acquisition verdict: FAIL.** Incremental reuse passes for five bootstrapped tickers, while 192 CIK-bearing tickers fail and the run obtains nothing new.
**SEC extraction/interpretation verdict: FAIL.** All five selected events are false positives.

## 5. CERI Reconstruction

### Current source-of-truth model

`config/ceri.yaml` defines calculation version `ceri-1.1.0`, a 60% minimum opportunity coverage threshold, and these component weights:

| Component | Weight |
| --- | ---: |
| Revision magnitude | 25% |
| Revision breadth | 15% |
| Revision acceleration | 10% |
| Surprise trend | 15% |
| Guidance | 15% |
| Catalysts | 15% |
| Price response | 5% |

Only components with `available = true` and a numeric value contribute. Available weight is the evidence coverage. Below 60%, the score remains null and the posture is `Unrated`; the engine does not renormalize missing components into numeric neutral evidence.

### Independent all-200 result

The independent harness read the stored component ledgers and source evidence, implemented the configured availability, weighting, clamping, coverage gate, risk dominance/penalty cap, and canonical snapshot hashing independently, and compared each ticker.

| Check | Matches |
| --- | ---: |
| Opportunity coverage | 200 / 200 |
| Opportunity score/posture | 200 / 200 |
| Event Risk | 200 / 200 |
| Snapshot evidence hash | 200 / 200 |
| Combined reconstruction flag | 200 / 200 |

There are no stored-versus-recalculated score mismatches because all scores are null. There are no hidden numeric defaults in the opportunity ledgers. Hash reproduction demonstrates deterministic serialization of the persisted snapshot inputs; it does not prove that historical provider acquisition can be replayed.

Event Risk distribution is 188 at 0.0, four at 1.5, three at 3.0, and five at 5.0. The values independently reproduce from uploaded earnings-date proximity. They are not a suspicious universal default.

## 6. Coverage & Confidence

| Coverage | Tickers | Available components | Stored result |
| --- | ---: | --- | --- |
| 0% | 196 | none | null score, `Unrated`, `Insufficient` |
| 15% | 4 | SEC guidance only | null score, `Unrated`, `Insufficient` |
| Below 60% | 200 | n/a | all correctly gated |

For 196 tickers all seven opportunity components are missing. For MET, MLI, NIC, and NVDA, only guidance is marked available; manual review proves that even this 15% is not real evidence. Thus the semantically valid coverage is 0% for all 200.

Confidence core coverage is 0% for every ticker because there are zero usable revision features. Raw confidence ledger scores are 1.0 for one ticker, 2.4 for 147, and 3.0 for 52, based on secondary/fallback subscores, but the explicit no-core-evidence gate correctly forces all labels to `Insufficient`. No high-confidence rating escaped that gate.

Regression certification:

| Historical defect class | Run-101 result |
| --- | --- |
| Missing treated as zero/neutral | **Not reproduced in snapshot opportunity scoring** |
| Below-threshold still rated | **Not reproduced; 200/200 Unrated** |
| Coverage threshold ignored | **Not reproduced; configured 60% gate respected** |
| Confidence from fallbacks | **Label gate passes; raw ledger still contains fallback-derived scores** |
| EODHD timestamp ambiguity | **Reproduced** |
| Canonical currency failure | **Reproduced; all 9,156 estimates unusable** |
| Period alignment failure | **No invalid comparison used, but successful behavior is untestable** |
| Surprise reconstruction incomplete | **Reproduced; zero usable surprises** |
| SEC guidance overconfidence | **Reproduced** |
| SEC evidence lineage incomplete | **Partially reproduced; locator exists, clean passage does not** |
| Catalyst/event evidence incomplete | **Reproduced; all catalysts ineligible** |
| Nondeterministic reconstruction | **Snapshot hashes pass; acquisition replay remains blocked** |
| API/DTO/UI snapshot divergence | **Not reproduced for snapshot fields** |

## 7. Anomalies

### CRITICAL

1. **All 200 null-scored first snapshots became upgrades.** `CeriChangeDetectionService` emits `OPPORTUNITY_UPGRADED` whenever no prior snapshot exists. Run 101 therefore has 200 upgrades with `from_snapshot_id = NULL`, `from = NULL`, `to = NULL`. This is a CERI lifecycle defect, not a provider limitation.
2. **Forty false upgrades became notable alerts.** The alert engine did not require a rated score or minimum coverage before alerting. The UI exposes these alerts as actionable change information despite every snapshot being `Unrated` and `Insufficient`.

### HIGH

1. **SEC false guidance:** five of five selected records are HTML/XBRL/boilerplate false positives with `HIGH` confidence. This is extraction and evidence-selection failure.
2. **SEC acquisition collapse:** 192/200 ticker jobs are `PARTIAL`; zero SEC records were acquired during the run. This is orchestration/readiness failure, with durable reuse functioning only for five tickers.
3. **EODHD produces no usable evidence:** 600 calls, 9,884 persisted source rows, 4,800 feature slots, and zero usable CERI components. This is an acquisition-contract/normalization mismatch.
4. **Provider timestamp nondeterminism:** retrieval time substitutes for provider observation time. The resulting churn includes 7,722 corrected estimate records, so historical revision provenance cannot be distinguished reliably from re-fetch effects.

### MEDIUM

1. **Misleading evidence volume:** snapshot lineage references 2,219 EODHD catalyst rows across 169 tickers, 870 EODHD earnings rows across 199, and 20,291 SEC guidance rows across 30, although none of that bulk is valid scoring evidence. Evidence count must not be presented as coverage.
2. **Stale evidence has no TTL:** 2021-2023 SEC events were selected for a 2026 decision.
3. **Future scheduled earnings in lineage:** future report dates are referenced despite null actuals. They do not alter scores but make lineage harder to interpret.
4. **No deploy commit signature:** version/config hashes are stored, but exact code provenance is incomplete.
5. **`MOG.A` mapping failure:** the provider symbol returns 404 for two products, leaving one ticker with less acquisition coverage.

### LOW

1. **No monetary cost ledger:** application request-unit cost and runtime exist; actual EODHD billing/account cost does not. A monetary ROI cannot be calculated.
2. **Raw confidence score can look nonzero:** 1.0-3.0 raw values are correctly overridden to `Insufficient`, but consumers must use the label/core coverage gate.

No cross-ticker source contamination was found in source/company/ticker joins. No duplicate snapshots, mixed-currency comparisons, impossible revision percentages, or impossible computed surprises reached a snapshot because those components were unavailable.

## 8. Source Value Analysis

The ablation harness left official snapshots untouched and evaluated five conceptual modes per ticker.

| Mode | Coverage/result |
| --- | --- |
| A. Full EODHD + SEC | 196 at 0%, four at 15%; 200 Unrated |
| B. Without EODHD | identical to full for all 200 |
| C. Without SEC | four tickers fall 15% -> 0%; all 200 remain Unrated |
| D. EODHD only | 200 at 0%; 200 Unrated |
| E. SEC only | same coverage as full; 200 Unrated |

### EODHD value

| Metric | Result |
| --- | ---: |
| Request universe coverage | estimates 199/200; earnings 199/200; news 200/200 |
| Usable evidence coverage | 0/200 |
| Revision coverage | 0/200 |
| Surprise coverage | 0/200 |
| Eligible catalyst coverage | 0/200 |
| Unique component changes in ablation | 0/200 |
| Material score/rating decisions changed | 0/200 |
| Ranking/lifecycle/legitimate alert changes | 0/200 |

**Value classification: NO MATERIAL VALUE for run 101.**
**Recommendation: REDUCE SCOPE and REQUIRE MORE HISTORICAL VALIDATION.** Disable or narrow CERI use of these products until the contract supplies verified currency, stable observation provenance, usable actual/consensus values, and eligible catalysts. This is a run-specific value conclusion, not proof that EODHD can never be useful.

### SEC value

| Metric | Result |
| --- | ---: |
| Current-run ticker acquisition | 0/200 records; 192 partial failures |
| Durable registry reuse | 5 tickers, 407 terminal documents |
| Selected guidance coverage | 4/200 tickers |
| Manual precision of selected evidence | 0/5 records |
| Unique component/coverage changes in ablation | 4/200 |
| Material score/rating decisions changed | 0/200 |
| Ranking/lifecycle/legitimate alert changes | 0/200 |

**Value classification: NO MATERIAL VALUE for run 101.**
**Recommendation: REQUIRE MORE HISTORICAL VALIDATION and KEEP BUT OPTIMIZE the durable registry.** The cache architecture avoids redundant work where bootstrapped, but extraction must fail closed and prove precision before SEC evidence can re-enter CERI.

## 9. Cost/Performance Analysis

| Cost dimension | EODHD | SEC |
| --- | --- | --- |
| Requests | 600 | 0 during run |
| Application call units | 1,400 | n/a |
| Recorded request latency | 643,675 ms (10.73 min) | none |
| HTTP errors | 2 | no HTTP attempts |
| Retries | 0 | 0 |
| Inserted source rows | 9,884 | 0 |
| Normalized/feature work | 9,156 estimates, 160 actual rows, 568 source catalyst rows, 4,800 revision feature slots | cached terminal checks; no new extraction |
| Stored source logical bytes | about 13.64 MB, excluding DB overhead | 319,222,805 cached document bytes for reusable CIKs, acquired before run |
| Useful CERI components | 0 | five selected rows, all false; semantically 0 |
| Monetary provider cost | **UNKNOWN**; no account/billing evidence available | **UNKNOWN**; no system cost ledger available |

The dominant run time was feature construction: approximately 97 minutes of wall time between feature start and completion, after roughly 11 minutes of EODHD request work. It created 4,800 unavailable revision slots. That is poor cost/benefit even without assigning a dollar amount.

SEC's incremental design has the right cost shape for already-terminal documents, but run 101 mostly paid orchestration complexity for immediate bootstrap failures. The architecture should not label a full run complete while 192 SEC ticker acquisitions are partial.

## 10. Predictive Value

The database contains 2,177 CERI snapshots over nine run IDs but only six as-of sessions, from 2026-08-08 through 2026-08-13. That is insufficient for point-in-time-safe forward-outcome comparison at SwingLens's intended horizon, and run 101 has no rated cross-sectional signal to evaluate.

**Predictive/trading value cannot yet be certified.**

Information contribution and trading alpha are separate questions. This report establishes that neither provider materially changed run-101 decisions; it does not establish that either provider lacks predictive value under a corrected, historically validated pipeline.

## 11. DB/API/UI Reconciliation

`CeriQueryService.run` returned all 200 run-101 snapshots with zero mismatches against the database for ticker, opportunity score, Event Risk, confidence label, opportunity coverage, posture, cutoff, and evidence hash. The API correctly exposes `rated = false` and the 60% minimum coverage.

The UI route returned 200 rows and visibly rendered `Unrated`, `Insufficient`, coverage, and the 60% requirement. The snapshot dashboard therefore does not visually inflate null scores.

The parity failure is downstream semantics, not snapshot DTO mapping: change and alert views display the 200 `OPPORTUNITY_UPGRADED` events and 40 notable alerts without suppressing null-to-null first observations or requiring rated coverage. The UI faithfully displays incorrect lifecycle records and thereby makes them misleading.

## 12. Stratified Manual Trace

Because every opportunity score is null, “highest,” “lowest,” and “middle” score strata collapse into the same Unrated class. The cohort instead covers the meaningful evidence/risk strata:

| Ticker | Stratum | Trace conclusion |
| --- | --- | --- |
| MET | SEC-only 15% coverage | Filing 2023 accession -> XBRL `12-31` token -> false maintained revenue -> 15% -> Unrated |
| MLI | two SEC records | Historical/XBRL date ranges -> false raised guidance -> 15% -> Unrated |
| NIC | negative SEC classification | 1995 statute boilerplate -> false lowered guidance -> 15% -> Unrated |
| NVDA | SEC plus near earnings risk | XBRL context dates -> false raised guidance; upload earnings proximity -> Event Risk 1.5 -> Unrated |
| GLNG | highest Event Risk | no opportunity evidence; upload earnings proximity -> Event Risk 5.0 -> Unrated |
| TPR | highest Event Risk plus false alert | no opportunity evidence -> Risk 5.0 -> null-to-null upgrade -> notable alert |
| MOG.A | provider mapping failure | estimates/earnings 404, news only ineligible -> 0% -> Unrated |
| AIZ | SEC durable-cache reuse | 212 terminal registry docs reused, no selected guidance -> 0% -> Unrated |
| AMZN | SEC records but no selected component | 88 terminal docs and 436 historical rows, none selected -> 0% -> Unrated |
| AEIS | high lineage volume, no evidence | 10 EODHD and 600 SEC lineage references, zero available components -> 0% -> Unrated |

Every anomaly identified outside this cohort was quantified over the full 200 in the CSV artifacts.

## 13. Determinism

All 200 stored evidence hashes reproduce exactly from the canonical persisted snapshot payload, and all independently calculated coverage, score/posture, and Event Risk results match. Ordering and floating-point behavior did not create a mismatch.

Determinism is certified only for persisted snapshot inputs under calculation version `ceri-1.1.0` and the stored config hash. Acquisition replay is not certified because EODHD substitutes retrieval time for absent provider timestamps, the database does not store a deploy commit SHA, and a new capture against mutable historical tables could select newly inserted evidence unless the exact cutoff and source set are enforced.

## 14. Final Certification Matrix

| Area | Verdict | Evidence |
| --- | --- | --- |
| Run integrity | **FAIL** | Terminal count completed, but 192 SEC subruns partial and lifecycle output invalid. |
| 200-snapshot reconciliation | **PASS** | Exact 200 requested = 200 unique expected snapshots; no missing, extra, or duplicates. |
| EODHD acquisition | **FAIL** | 598/600 success, 2 failures, unstable provider-time provenance, zero usable projection. |
| EODHD normalization | **FAIL** | 9,156/9,156 estimates lack canonical currency; no usable revisions/surprises/catalysts. |
| EODHD PIT integrity | **BLOCKED** | No post-cutoff rows used, but provider observation history is not independently provable. |
| SEC acquisition | **FAIL** | 192 partial; zero run-time downloads/extractions/records. |
| SEC extraction | **FAIL** | 0/5 selected records are valid guidance. |
| SEC interpretation | **FAIL** | Stale XBRL/boilerplate tokens classified HIGH-confidence raised/lowered/maintained guidance. |
| CERI reconstruction | **PASS** | 200/200 coverage, score/posture, risk, and evidence hashes reproduce. |
| Missing-data handling | **PASS** | No unavailable opportunity component became zero or numeric neutral. |
| Coverage gating | **PASS** | 200/200 below 60%; 200/200 null score and Unrated. |
| Provenance | **FAIL** | Accessions/locators exist, but EODHD provider time is synthetic and SEC passage extraction is raw/misbounded. |
| Determinism | **PASS** | Persisted snapshot replay 200/200; acquisition replay limitation explicitly excluded. |
| DB/API/UI parity | **FAIL** | Snapshot parity passes; lifecycle/alerts shown outside computation are materially false. |
| EODHD value | **NONE** | Zero unique component or decision changes in run-101 ablation. |
| SEC value | **NONE** | Four coverage changes, zero decisions, and 0/5 manual precision. |

## 15. Prioritized Remediation Recommendations

No remediation was implemented during this forensic phase.

1. **P0: fail lifecycle closed.** A first Unrated/null snapshot is baseline establishment, not an upgrade. Alert rules must require a real from/to semantic transition and rated/minimum-coverage eligibility.
2. **P0: fail SEC evidence closed.** Only `accepted_for_scoring = TRUE` should be eligible. Strip/segment HTML before classification, reject XBRL/date/statute tokens, impose freshness rules, and validate on a labeled filing corpus.
3. **P0: enforce run-level provider readiness.** A run with 192 SEC `PARTIAL` ticker syncs must not be reported as a fully successful evidence run. Finish SHADOW bootstrap or explicitly mark SEC unavailable in run provenance.
4. **P1: repair the EODHD evidence contract.** Require verified currency, stable provider-known timestamps, actual/consensus fields, explicit fiscal-period identity, and deterministic idempotency before enabling features.
5. **P1: stop expensive unavailable feature builds early.** Detect the all-currency-missing batch once, record a structured provider limitation, and avoid 97 minutes of doomed feature work.
6. **P1: narrow evidence lineage.** Separate “considered source rows” from “selected scoring evidence” so counts cannot imply coverage or confidence.
7. **P1: persist full processor identity.** Store deploy commit/image digest plus all provider/extractor signatures on the capture/run manifest.
8. **P2: build historical PIT validation.** Accumulate immutable observation history and sufficient forward outcomes before claiming provider value or trading alpha.
9. **P2: add cost accounting.** Persist request units, bytes, CPU/extraction time, storage, and actual account cost where contractually available.

## Evidence Artifacts

- `docs/qa/run101_ceri_snapshot_audit.csv`: one row per ticker with reconstruction, coverage, source counts, lifecycle/alert counts, and anomaly flags.
- `docs/qa/run101_eodhd_reconciliation.csv`: one row per ticker and EODHD dataset.
- `docs/qa/run101_sec_reconciliation.csv`: one row per ticker with sync, registry, extraction, and guidance selection facts.
- `docs/qa/run101_source_ablation.csv`: five conceptual source modes per ticker.
- `docs/qa/run101_forensic_summary.json`: aggregate counts and exact run facts.
- `docs/qa/run101_forensic_queries.sql`: read-only SQL behind the principal claims.
- `scripts/qa/certify_ceri_run101.py`: repeatable read-only reconstruction and artifact generator.

These artifacts are evidence exports, not production fixes. They must be preserved with the database backup/snapshot used for this certification because mutable historical tables could otherwise change later query results.
