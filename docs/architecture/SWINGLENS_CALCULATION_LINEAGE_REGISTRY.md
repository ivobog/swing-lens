# SwingLens Calculation Lineage Registry

## 1. Document Authority

This is the authoritative calculation, provenance, temporal, mutation, execution, and reconstruction registry for the audited SwingLens implementation baseline. It describes the baseline below; it is not automatically a description of a later repository state.

| Authority item | Value |
|---|---|
| Implementation baseline and audited HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Branch at audit time | `codex/winner-evidence-remediation` |
| Alembic head | `0074_ceri_evidence_quarantine` |
| Audit date | 2026-09-14, Europe/Zurich |
| Inputs | Tasks 01–07 under `docs/audit/calculation-lineage/` |
| Baseline drift | None; `BASELINE_DRIFT` not raised |

Evidence status means: `VERIFIED` (direct code/schema/test evidence), `PARTIALLY_VERIFIED` (material path verified but boundary remains), `INFERRED` (reasoned from indirect evidence), `UNKNOWN`, `CONTRADICTORY`, `LEGACY`, or `DEAD_CODE`. Enforcement means: `DATABASE_ENFORCED`, `CODE_ENFORCED`, `VALIDATED_AT_RUNTIME`, `RECORDED_ONLY`, `ASSUMED`, `NOT_ENFORCED`, `VIOLATED`, `NOT_APPLICABLE`, or `UNKNOWN`. Hash status uses the independent properties `RECORDED`, `VALIDATED`, `ENFORCED`, and `AUTHORITATIVE`.

Scope includes the parent pipeline, calculations, persisted evidence, temporal selectors, configuration, background jobs, writers, repair/replay/backfill paths, and IB Market Intelligence (IBMI). It excludes live database contents, deployed environment values, provider runtime behavior, and production execution. Update this document only after a new implementation audit: record a new baseline, preserve stable IDs, mark changed contracts, and never silently merge behavior from different baselines.

## 2. Executive Architecture Summary

### A. Canonical parent stage ordering

```text
Upload → Fundamentals → Market Data → Technical → Market Regime
       → Combined → Ranking → Sector Rotation → CERI
       → Decision Handoff → Setup/Lifecycle → Winner
```

This is execution order, not proof of data dependency. The CERI stage may return after scheduling provider descendants, so those descendants can outlive the parent stage/pipeline boundary (`PIPE-001`); that is a completion/accounting defect, not a CERI contamination edge into Setup or Winner.

### B. Verified calculation dependency graph

```text
Upload/raw → Fundamental ─┬→ Combined
                          ├→ Ranking → Sector Rotation
PriceBar PIT → Technical ─┼→ Combined
                          ├→ Ranking
                          └→ Setup/Lifecycle (direct behavioral input)
PriceBar PIT ─────────────────→ Setup/Lifecycle
Combined earnings_risk ───────→ Setup/Lifecycle actionability
Combined/Ranking score/decision→ Setup metadata only
Regime + Sector Rotation ─────→ Setup/Lifecycle

IBMI liquidity ───────────────→ Ranking
IBMI volatility/short pressure→ CERI
Event/provider/SEC evidence
  + bounded CERI price response→ CERI

Raw + Fundamental + Technical + Combined + Ranking + Regime + Sector Rotation
  ────────────────────────────→ Winner (independent capture branch)
```

Verified negative edges are architectural facts: same-run Ranking does not consume Sector Rotation; CERI feeds neither Ranking, Setup/Lifecycle, nor Winner; Setup/Lifecycle does not feed Winner; and IBMI feature rows are not direct Winner inputs. Sector Rotation consumes same-run Ranking. Winner independently reads Raw, Fundamental, Technical, Combined, Ranking, Market Regime, and Sector Rotation.

## 3. Calculation Identity Model

Execution ownership says which upload, job, run, or worker owns work. Calculation identity says which exact decision truth was computed. `run_id + ticker` is insufficient because two rows with that pair can differ by cutoff, session, calendar, source revision, configuration, model, or generation.

The target identity envelope is:

```text
run_id + pipeline_id + market_calculation_context_id + ticker/company
+ decision/as_of_session + calculation_cutoff + calendar_version
+ configuration identity/hash + calculation/model version
+ source artifact IDs/revisions + generation (when applicable)
```

| Subsystem | Strongly represented dimensions | Missing/weak dimensions | Baseline classification |
|---|---|---|---|
| Pipeline/MarketCalculationContext | run, pipeline, cutoff, completed session, calendar, context fingerprint | downstream adoption varies | `ENFORCED` for context itself |
| Price PIT | ticker, session, cutoff, revision knowledge time, basis | callers can bypass | `CODE_ENFORCED` on canonical reader |
| Fundamental | run, ticker, model/debug metadata | effective time, provider/period/currency, immutable input/config identity | `NOT_ENFORCED` |
| Technical cache | ticker, series versions, as-of, feature config, engine/schema | scoring config deliberately outside feature key; final score mutable | `ENFORCED` within cache boundary |
| Regime/Sector | versioned snapshots; sector full config hash | regime full config; compatible upstream envelope | `PARTIALLY_ENFORCED` |
| Combined/Ranking | same run/ticker; Combined input IDs/config snapshot | pipeline/context/session/cutoff; Ranking full profile identity/generation | `VIOLATED` |
| CERI | modern rows carry run/context/session/cutoff/config/version/evidence | direct/current paths, priority/rules/purge semantics | `PARTIALLY_ENFORCED` |
| Setup canonical | run/pipeline/context/session/cutoff, source/config hashes, handoff preflight | alternate paths and mutable upstream/rules | `PARTIALLY_ENFORCED` |
| Winner prediction | run/ticker, frozen vector/hash, model/config/schema; handoff recorded | decision time is wall-clock derived; handoff/upstream compatibility not validated | `VIOLATED` at acquisition, immutable afterward |
| Winner generation | generation, manifests, watermark, model/config/content hash | active-pointer monotonicity/idempotency | `ENFORCED` for content, not publication |
| IBMI | intelligence-run/output as-of/config/version/hash | constituent source upper bounds and market context | `VIOLATED` |

## 4. Canonical Time and Session Model

| Term | Intended/current meaning | Safe use | Unsafe/conflicting use |
|---|---|---|---|
| `cutoff_at` / market calculation cutoff | latest instant evidence may have been possessed for a calculation | explicit, frozen, timezone-aware upper bound | defaulting historical work to now |
| latest completed session | most recent exchange session fully complete at cutoff | derive through frozen calendar/context | recompute from today's wall clock |
| `input_as_of_session`, `as_of_session`, `as_of_date`, `data_as_of_date` | business-session/date label on source or result; names vary | pair with cutoff, calendar, source IDs | treat label alone as proof constituents were eligible |
| `decision_at` | Winner decision instant | explicit historical anchor | current wall clock during capture/backfill (`WIN-001`) |
| `prediction_as_of_date` | Winner decision-session label | derive from explicit decision context | derive from current date/session |
| `event_date` | provider/business event date | retain provider semantics and provenance | use as possession time |
| `effective_session` | market session to which event/evidence applies | calendar-derived and cutoff bounded | conflate with publication/ingestion date |
| `bar_date` / `session_date` | market or metric session represented by a row | use with source/basis/revision | assume row was observable on that date |
| `first_seen_at` | earliest local knowledge time for PriceBar state | PIT revision eligibility | replace with `updated_at` |
| `observed_at` | provider/collector observation time | source timing when authenticated | assume it equals local possession |
| `retrieved_at` | acquisition/possession time | CERI knowledge time when present | silently fall back without marking provenance |
| `ingested_at` | local ingestion time; CERI knowledge fallback | conservative possession fallback, explicitly marked | claim it is provider publication time |
| `created_at`, `updated_at` | database mutation timestamps | operational ordering/audit | business-time eligibility or historical session |
| `published_at` | provider or generation publication, depending on artifact | qualify object and timezone | use as universal evidence-known time |
| `source_timestamp` | source-specific event/publication/observation time | use only with named source semantics | compare heterogeneous meanings as one clock |
| revision timestamp | time a corrected version entered lineage | reconstruct knowledge state at cutoff | assume revised value existed at original session |

Business time (decision/session/cutoff) controls calculation eligibility. Provider event and publication time describe the outside world; local possession time (`retrieved_at`, `ingested_at`, `first_seen_at`) describes what SwingLens could know. Database mutation and job execution times are operational. Wall clock is legitimate for leases, timestamps, and scheduling, but not as an implicit historical business clock.

## 5. Input Classification Standard

Every `CALC-*` entry uses: `RAW` (uploaded/untransformed values), `DERIVED` (calculated artifact), `CONFIGURATION`, `TEMPORAL`, `EXECUTION_CONTEXT`, `HISTORICAL_STATE`, and `EXTERNAL_DATA`. One input may have two relevant roles, such as a prior lifecycle evaluation (`DERIVED`, `HISTORICAL_STATE`). The class does not imply trust: eligibility and compatibility must still be enforced.

### Calculation inventory

| Calculation ID | Who calculates / what causes it | Principal inputs and selected artifacts | Stored output; readers | Mutability / baseline reproducibility |
|---|---|---|---|---|
| `CALC-MD-001` | market-data PIT service / Technical, Setup, historical caller | PriceBar + revisions, basis, session, cutoff | eligible series/version; Technical, Setup | reconstructed/versioned; reproducible only through bounded reader |
| `CALC-FUND-001` | fundamental service / upload, pipeline, recalculate | uploaded raw row, model/config | FundamentalScore; Combined, Ranking, Setup assembly, Winner | `DELETE_RECREATE`; temporal reconstruction incomplete |
| `CALC-TECH-001` | technical engine / pipeline, standalone/cache jobs | PIT series, feature/scoring config, context | TechnicalScore/cache artifact; Combined, Ranking, Setup, Winner | result replace-like; cache content-addressed; readiness weak downstream |
| `CALC-REGIME-001` | regime service / pipeline/direct | SPY/QQQ features, session/config | MarketRegimeSnapshot; Sector, Setup, Winner | `VERSIONED`; config/source-session gaps |
| `CALC-COMB-001` | Combined service / pipeline/standalone | FundamentalScore, TechnicalScore, config | CombinedResult; Setup metadata/earnings risk, Winner | `DELETE_RECREATE`; no full context identity |
| `CALC-RANK-001` | ranking engine / pipeline/admin | Fundamental, Technical, profile YAML, optional IBMI liquidity | RankingResult; Sector, Setup metadata, Winner | `MUTABLE_CURRENT`; profile/history unrecoverable |
| `CALC-SECTOR-001` | sector service / pipeline/direct | same-run Ranking, regime, prior sector, config | SectorRotationSnapshot; Setup, Winner | `VERSIONED`; global/prior fallbacks |
| `CALC-CERI-001/002` | CERI engines / parent/child DAG, direct/admin | event/SEC/provider evidence, IBMI, CERI price reader, rules | CERI snapshot/features; CERI change/alert/display only | mostly append/versioned; direct/rebuild/purge exceptions |
| `CALC-SETUP-001` | Setup engine / pipeline/direct/repair | direct TechnicalScore, PIT price, fundamentals/liquidity, Combined earnings risk, regime, sector; score metadata | Setup snapshots/canonical; Lifecycle/display | append snapshots + `DERIVED_POINTER`; canonical path strongest |
| `CALC-LIFE-001` | lifecycle engine / Setup evaluation/repair/replay | Setup snapshot, prior episode/evaluation, rules, session | episode/evaluations/events; alerts/UI | mutable episode + versioned events; chronology not reconstructable fully |
| `CALC-ALERT-001` | alert engine / transition/rebuild | lifecycle event, Setup snapshot, mutable rule, cooldown history | alert; notification/UI | append/upsert behavior with mutable rules; replay weak |
| `CALC-IBMI-001` | IBMI calculators / refresh/rebuild jobs/APIs | metric bars, live/current observations, PriceBars, config | IBMI features; Ranking, CERI | versioned/dedup output over temporally unsafe selectors |
| `CALC-WINNER-001` | Winner capture / pipeline/admin/backfill | Raw, Fundamental, Technical, Combined, Ranking, regime, sector, handoff metadata | frozen prediction/vector; cohort/rescore | immutable values after capture; source acquisition incomplete |
| `CALC-WINNER-002/003` | maturation engine / maturation/revision jobs | frozen prediction, current corrected outcome bars, horizon policy | versioned outcomes; cohorts/metrics | decision frozen, truth versioned; target scope dynamic |
| `CALC-WINNER-004` | cohort/rescore engine / refresh/rescore job | frozen vectors/outcomes, target manifest, model/config | generation/estimates/manifests; publication | versioned/content-addressed; reproducible |
| `CALC-WINNER-005` | publication service / admin/automatic publish | complete generation and current pointer | active serving pointer | `DERIVED_POINTER`; atomic but non-monotonic |

## 6. Artifact Mutability Model

| Class | Meaning | SwingLens examples |
|---|---|---|
| `IMMUTABLE` | value cannot be changed after creation | Winner frozen `feature_json` semantics |
| `APPEND_ONLY` | new facts append; old facts remain | many CERI/Setup snapshots and lifecycle events, subject to named exceptions |
| `VERSIONED` | current fact may change but prior revisions remain addressable | PriceBarRevision, Winner outcomes/generations |
| `MUTABLE_CURRENT` | row is a serving/current projection and may change in place | RankingResult, active LifecycleEpisode, rule rows |
| `DELETE_RECREATE` | recalculation removes/replaces prior run-owned rows | FundamentalScore, TechnicalScore/CombinedResult paths |
| `DERIVED_POINTER` | mutable selector points to versioned content | Winner active generation, canonical/latest snapshot selection |

Immutable evidence preserves historical meaning. Mutable projections answer “what is current?” and are unsafe historical evidence unless the exact prior version is reconstructed. A foreign key to a mutable row does not freeze its former values.

## 7. Price and Market Data Lineage

**`CALC-MD-001 — point-in-time OHLCV reconstruction` (`VERIFIED`).** Inputs: PriceBar current rows (`EXTERNAL_DATA`), PriceBarRevision (`HISTORICAL_STATE`), ticker/timeframe/basis, session and cutoff (`TEMPORAL`). The reader restricts sessions to the latest completed session, admits rows known by `first_seen_at/created_at <= cutoff`, and reconstructs overwritten state from revisions. Unprovable rows are excluded. Output is an eligible series and price-series version consumed by Technical and canonical Setup price reads.

PriceBar is a `MUTABLE_CURRENT` projection unique by ticker/date/timeframe/basis. PriceBarRevision is `VERSIONED`; persistence uses an MD5 content-change hash over normalized bar fields to decide when to record a revision. IB normally supplies daily `ADJUSTED_LAST` price and `TRADES` volume, RTH only. Preferred adjusted basis is used only with full date coverage; otherwise TRADES is used. The authoritative technical reader maintains basis discipline; CERI's bespoke reader can mix basis rows (`CERI-002`).

Current corrected market truth is the latest PriceBar/revision state. Decision-time observable truth is the state provably possessed by the frozen cutoff. Winner outcome maturation intentionally uses later current-corrected truth, while decision features should use PIT truth.

`IB_FETCH`, market-data prewarm, and provider refresh cause PriceBar writes independently. Prewarm/fetch may re-plan current sessions/config at execution instead of enforcing stored plan metadata (`PIPE-005`); these writers can race certification work (`PIPE-008`). Direct/current CERI and IBMI readers can bypass PIT (`CERI-003`, `XINT-002`). Interior session completeness is not enforced (`CORE-004`). Technical content cache reuse is keyed by eligible series versions and has no TTL authority; current/latest database reads are not a safe substitute for it.

Historical reconstruction is `ORIGINAL_CONTEXT_RECONSTRUCTION` only when ticker, basis, session, cutoff, and revision ledger are used. Current PriceBar reads are current truth, not historical reconstruction.

## 8. Fundamental Calculation Registry

**`CALC-FUND-001 — Fundamental Score v2.1` (`VERIFIED`).** Inputs are uploaded raw company fundamentals (`RAW`, run-owned), current model/config (`CONFIGURATION`), and ticker/run (`EXECUTION_CONTEXT`). Output is FundamentalScore (`DELETE_RECREATE`) consumed independently by Combined, Ranking, Setup metadata/feature assembly, and Winner.

The score is a weighted sum: growth 13%, profitability 13%, free cash flow 12%, earnings 12%, capital efficiency 12%, balance sheet 12%, valuation 10%, forward 8%, shareholder return 5%, and liquidity/risk 3%. Missing-data penalties are 0.35/0.20/0.10/0.05 by configured tier, capped at 2.5; coverage below 0.65 emits a warning.

The model label is `fundamentals_v2.1`; a config/debug hash records a subset of model settings. It does not prove provider identity, fiscal period/effective time, currency conversion, or immutable input-row identity. Fundamentals are run-bound but temporally unanchored and may be recalculated with current rules. Pre-pipeline upload processing and recalculation are duplicate/alternate writers. Historical reconstruction is therefore `NOT_ENFORCED` even when the run is known (`CORE-005`, `XINT-001/005`).

## 9. Technical Calculation Registry

**`CALC-TECH-001 — daily/weekly technical features and score` (`VERIFIED`).** Inputs: PIT ADJUSTED/TRADES series (`EXTERNAL_DATA`), frozen session/cutoff (`TEMPORAL`), v4/v5 YAML/settings (`CONFIGURATION`), and run/context (`EXECUTION_CONTEXT`). The canonical requirement is 252 daily bars.

Important parameters include EMA 10/20; SMA 50/150/200; slope windows 10/20; ADX 14 with smoothing 14 and threshold 18; RSI 14; ATR 14; volume means including 20; confirmed pivots using 3 left/3 right with confirmation shift; breakout lookback 40; pullback 20; relative-strength windows 21/63/126 with 70/30 weighting; and W-FRI higher-timeframe bars with EMA 10, SMA 30/40, slope 4, ROC 13, and gate 5.5. No monthly calculation is active.

The v4 score uses active regime weight vectors; v5 is shadow by default. The content-addressed feature cache key includes ticker/timeframe, adjusted and trades price-series versions, feature config, as-of session, engine, and schema. Scoring configuration is intentionally not part of the feature cache key. Cache/series maintenance is disabled by default.

TechnicalScore rows use replace/delete-reinsert semantics. With short history, missing components can become numeric zero/default while other positive components survive: `insufficient_data=true` and low/error `technical_confidence` can coexist with a positive score (`CORE-009`). Combined and Ranking fail to make that readiness non-bypassable; Setup/Lifecycle directly consumes TechnicalScore numerics; Winner alone hard-rejects `insufficient_data=true` but has weaker low-confidence/required-feature policy. This readiness defect affects normalization, labels, lifecycle states, and alerts independently—not through Ranking as a Setup behavioral gate.

## 10. Market Regime Registry

**`CALC-REGIME-001 — Market Regime and Capital Context` (`VERIFIED`).** Sources are SPY (primary) and QQQ (risk proxy); optional IWM/TLT/VIXY inputs are not active classifier inputs. Trend/momentum/volatility features produce component scores combined 65%/35%. Breadth and sector leadership are context, not the main classifier.

Outputs include regime label, numeric score, risk state, `gate_ok`, position-size multiplier, allowed profiles/setups, minimum-score adjustment, confidence, warnings, source sessions, and a versioned snapshot/evidence hash. Consumers must name the exact field: Setup reads regime label/gate behavior; Winner freezes selected regime features; sector rotation reads the snapshot. Combined/Ranking do not use the persisted MarketRegimeSnapshot.

Sparse benchmark data can incorrectly produce bullish state (`CORE-001`). Severe staleness can display Gray while retaining bullish sizing/permissions (`CORE-002`), so displayed risk state is not equivalent to effective policy. SPY/QQQ source sessions can differ (`CORE-003`). The model/config label is `mrcc-1.0`, but no full authoritative config hash is persisted (`CORE-008`). Snapshots are `VERSIONED`; global/latest selectors weaken run/context compatibility.

## 11. Combined Result Registry

**`CALC-COMB-001 — Combined Result` (`VERIFIED`).** It independently reads same-run FundamentalScore and TechnicalScore; Ranking is a sibling calculation and does not read CombinedResult. The normal formula uses 55% fundamental and 45% technical available weight, then applies missing/other penalties. Decision thresholds are Strong candidate ≥ 8.0, Candidate ≥ 6.8, Watch ≥ 5.5. Earnings proximity/risk uses the current Zurich date on affected entry points (`RANK-007`).

CombinedResult persists its scoring configuration snapshot/hash, source input IDs, calculation version, final score, decision label, `earnings_risk`, completeness, and warnings. It is `DELETE_RECREATE`, lacks a required pipeline/context/session/cutoff envelope, and does not reject a positive TechnicalScore merely because `insufficient_data=true` (`RANK-001`).

Setup denormalizes Combined final score/decision as metadata only; those fields do not drive lifecycle actionability. Combined `earnings_risk` is a distinct behavioral input to Setup actionability. Winner independently reads Combined and requires existence/completeness. A refreshed CombinedResult may therefore change acquisition meaning without an immutable generation (`RANK-004/006`, `SETUP-008`).

## 12. Ranking Registry

**`CALC-RANK-001 — Ranking Profiles` (`VERIFIED`).** Every profile independently reads same-run FundamentalScore and TechnicalScore, normalizes within the selected population, applies profile gates/penalties, and writes/upserts RankingResult (`MUTABLE_CURRENT`).

| Profile | Base blend | Emphasis |
|---|---|---|
| Momentum Swing | Fundamental 45%, Technical 55% | momentum/technical |
| Quality | Fundamental 55%, Technical 45% | quality/fundamental |
| Early Rocket | Fundamental 30%, Technical 70% | highest technical |
| Clean Compounder | Fundamental 65%, Technical 35% | highest fundamental |
| Defensive | Fundamental 60%, Technical 40% | defensive/quality |

Missing technical components can be zero/default. Readiness is assessed after numeric scoring: low/error/insufficient rows may receive `Low confidence` while retaining score/rank and remaining in normalization (`RANK-001/002`). Three profiles apply an IBMI liquidity overlay selected as a global latest-ticker feature with only coarse upload-time bounding; feature ID/config is not persisted (`RANK-003`). Earnings logic uses wall clock (`RANK-007`).

Profile YAML is effective configuration, but RankingResult retains only the name and partial settings, not an immutable full profile hash/version, population, or missing-data decisions (`RANK-005`). Upserts can change historical meaning (`RANK-006`); compatibility generally stops at run+ticker (`RANK-004`). Ranking consumes neither CERI nor same-run Sector Rotation. Sector Rotation consumes Ranking. Setup captures ranking score/decision/profile as metadata only; Winner independently uses Ranking-derived cohort features.

## 13. Sector Rotation Registry

**`CALC-SECTOR-001 — Sector Rotation Snapshot` (`VERIFIED`).** Same-run Ranking is a direct input, with market regime and prior sector history (`HISTORICAL_STATE`). Default `universe_only` mode weights technical 25%, profile 20%, top-25 participation 20%, setup density 20%, and risk 15%. ETF mode is disabled by default; when enabled, the blend is 55% universe/45% ETF.

Sector snapshots are `VERSIONED`, carry model `1.0`, full config hash, evidence/source metadata, and revisions. Run-owned regime is preferred, but global/latest regime fallback exists (`CORE-006`); standalone prior-sector selection can cross runs (`CORE-007`). Consumers are Setup and Winner independently. Same-run Sector does not feed Ranking.

## 14. CERI Registry

**`CALC-CERI-001 — CERI Feature Snapshot` (`VERIFIED`).** CERI has four separate outputs, never one generic score:

| Output | Calculation |
|---|---|
| `opportunity_score` | available-weight normalization over revision magnitude 25%, breadth 15%, acceleration 10%, surprise 15%, guidance 15%, catalysts 15%, price response 5%; ≥60% available weight required |
| `event_risk_score` | dominant maximum of earnings/binary risk plus capped secondary contribution; stale penalty is disconnected (`CERI-007`) |
| `confidence` | source 25%, freshness 20%, coverage 20%, analyst 15%, timestamp 10%, conflict-free 10%; not renormalized; thresholds 8/6/3.5 |
| `posture` | hard-coded threshold mapping from opportunity, risk, confidence |

Inputs include normalized estimates, earnings, guidance, catalysts, SEC material, IBMI volatility/short pressure, and price response. Source records carry provider and published/observed/retrieved/ingested/source times plus correction/supersession/quarantine/purge lineage. Knowledge time prefers `retrieved_at`, then `ingested_at` (`CERI-006`). AS_KNOWN and LATEST_CORRECTED are distinct views.

Configured priority (`manual > primary > eodhd > sec`) is not applied by production conflict selection, which orders effective time/row ID (`CERI-001`). Earnings schedule provenance is incomplete (`CERI-011`). SEC processor signatures are a four-version semantic tuple, not code/Git/dependency/schema/full-config identity; deployment identity can report schema `0050` while head is `0074` (`CERI-009`).

**`CALC-CERI-002 — price response` (`VERIFIED`).** The normal bespoke PriceBar path is bounded by CERI session/cutoff and reconstructs revisions, but can mix adjusted/trades basis; public/direct callers may omit cutoff or use current rows (`CERI-002/003`). Benchmark/source series require common-session alignment; missing required sessions reduce/omit the feature.

Config is `ceri-1.3.0` plus YAML, settings, and mutable DB rules. Modern rows are version/append-like with run/context/session/cutoff/config/calculation/evidence hashes. Change rebuild can read unbounded current catalysts/guidance (`CERI-004`); purge is destructive and may leave hashes stale (`CERI-010`); stable request keys can suppress a later refresh (`CERI-012`). Provider acquisition → normalization → feature → finalizer/capture → change/alert is an asynchronous child DAG, whose descendants may outlive parent completion (`PIPE-001`). CERI feeds neither Ranking, Setup/Lifecycle, nor Winner.

## 15. Setup Signal Registry

**`CALC-SETUP-001 — Setup Signal Snapshot` (`VERIFIED`).** Behavioral inputs are direct TechnicalScore, PIT price evidence, fundamental/liquidity fields, Combined `earnings_risk`, regime label/gate, and sector rank/confidence. Positive TechnicalScore numerics can remain actionable despite insufficient readiness/confidence (`SETUP-003`). Canonical price loading is cutoff/session bounded and revision reconstructing, normally TRADES-prioritized, with weaker raw/current fallback paths.

TechnicalScore is a direct behavioral input. Ranking score/decision does not drive lifecycle actionability. Combined score/decision does not drive lifecycle actionability. Combined `earnings_risk` can participate in actionability.

Combined final score/decision and Ranking `profile_score`/decision/profile are denormalized metadata/provenance only; they do not drive lifecycle actionability. Combined `earnings_risk` can. CERI is not an input.

Snapshots are append-like with source/config fingerprints and mutable canonical selection (`DERIVED_POINTER`). Canonical execution validates Decision Handoff, pipeline/context/session/cutoff, and transition anchor. Direct/repair/replay/maintenance paths can derive latest/current context (`SETUP-005/006/010`). The CERI-resume stage-order branch invokes Setup without required cutoff/pipeline ID and fails (`PIPE-002/SETUP-001`); this is not a CERI data edge, and supplying current/latest values would be unsafe.

## 16. Lifecycle Registry

**`CALC-LIFE-001 — Lifecycle Evaluation and Transition` (`VERIFIED`).** States are `DISCOVERED`, `DEVELOPING`, `TIGHTENING_1`, `TIGHTENING_2`, `READY`, `TRIGGERED`, `CONFIRMED`, and terminal `EXTENDED`, `FAILED`, `EXPIRED`. Inputs include selected Setup snapshot (`DERIVED`), prior episode/evaluation (`HISTORICAL_STATE`), session/cutoff, and rules/config.

LifecycleEpisode is `MUTABLE_CURRENT`; evaluations/events preserve partial versioned history. Prior-state selection lacks a universal `effective_time <= calculation_time` upper bound. Older repair can overwrite newer current state (`SETUP-002`), and canonical history can change across runs/configs (`SETUP-004/009`). Counters mean N qualifying observations, not N consecutive trading sessions. “Replay” is current-rules/current-canonical repair, not original-context reconstruction (`SETUP-006`).

## 17. Setup/Lifecycle Alert Registry

**`CALC-ALERT-001 — Setup/Lifecycle Alert` (`VERIFIED`).** Alerts derive from a lifecycle transition/evaluation and selected Setup signal. Event/effective session is business time; creation time is operational. Rule rows are `MUTABLE_CURRENT` with incomplete revision lineage. Dedup/cooldown queries do not uniformly enforce `alert_time <= calculation_time`, so a later alert can suppress an older replay (`SETUP-007`, `XINT-008`). Rebuild/replay uses current rules/current state and is not original reconstruction. Required lineage is exact transition, Setup snapshot, rule revision, source artifacts, session/cutoff, cooldown anchor, and dedup key; baseline coverage is partial.

## 18. IB Market Intelligence Registry

**`CALC-IBMI-001 — IBMI Feature Calculation` (`VERIFIED`).** IBMI intelligence runs cause historical metric-bar/current+revision writes, live snapshots, shortability/availability observations, and liquidity, volatility, short-pressure, options-activity, scanner/histogram/trade-journal calculations where material. Feature rows record as-of/cutoff labels, config/calculation/source versions, component evidence hashes, confidence, freshness, coverage, score/classification, and warnings.

Historical metric bars are `MUTABLE_CURRENT` plus `VERSIONED` revisions; live/availability/shortable rows are latest observations; feature rows are versioned/content-deduplicated. A historical feature's declared as-of/cutoff is not enforced against every constituent: selectors can load unbounded metric bars, latest live/availability/shortable rows, and current PriceBars (`XINT-002`). A probe accepted a 2026-01-10 liquidity bar in a 2026-01-05 feature. Hashes prove payload equality, not temporal eligibility.

IB liquidity feeds Ranking. IB volatility/short pressure feed CERI. IBMI is not a Winner input. Consumers mostly check coverage, not complete confidence/freshness/constituent compatibility (`XINT-004`). Settings global/module flags, YAML `ibmi-1.0`, API/job payloads, and runtime state form effective config; YAML `engine.enabled` is not the master gate. Feature rebuild commits inside a ticker loop without token/lease verification immediately before commit, permitting stale-worker domain writes (`XINT-009`).

## 19. Winner Prediction Capture Registry

**`CALC-WINNER-001 — Winner Prediction Capture` (`VERIFIED`).** Direct inputs are Raw/Upload, FundamentalScore, TechnicalScore, CombinedResult, RankingResult, MarketRegimeSnapshot, SectorRotationSnapshot, and Decision Handoff metadata. CERI, Setup/Lifecycle, and IBMI feature rows are not inputs.

Capture stores selected values in immutable `feature_json`, with feature/evidence hashes, model/calculation version `1.1`, schema `1.0`, config, run/ticker, eligibility/exclusions, and source metadata. Rescore reads this frozen vector. This strong value-freezing boundary does not cure weak selection: capture receives run identity, derives `decision_at`/session from wall clock, selects latest same-run sources, and records but does not validate Handoff compatibility (`WIN-001/002`).

Technical `insufficient_data=true` is a hard exclusion. Low/error confidence and required-feature completeness are not equivalent gates (`WIN-004`). Combined must exist/be complete. Ranking decision label becomes `setup_family` and profile is a cohort dimension; exact ranking config/readiness is not reconstructable (`WIN-003`). Regime/sector compatibility is incomplete (`WIN-005`).

## 20. Winner Outcome Registry

**`CALC-WINNER-002 — Winner Outcome Truth` (`VERIFIED`).** From frozen decision/session, the next eligible session establishes planned entry, then exact horizons 1, 3, 5, 10, and 20 sessions are evaluated. Complete ADJUSTED basis is preferred, else TRADES; current corrected PriceBar state supplies later truth. The policy includes a +2.5 target before a -2 stop over the five-session rule where applicable.

Outcomes are `VERSIONED`: provider correction/rematuration can append a revision and change truth while prediction features remain frozen. Evidence hashes/label versions are stored, but exact PriceBar row/revision IDs are missing (`WIN-006`). Market-session targeting handles holidays; missing exact bars defer or omit calculation. Later truth may improve; decision evidence may not change.

## 21. Winner Maturation Registry

**`CALC-WINNER-003 — Outcome Maturation Workflow` (`VERIFIED`).** A root job queries due predictions and creates/continues bounded batches. State includes parent/root job, continuation depth (guarded to 1000), processed/unvisited counts, retry-not-before (15 minutes for deferral), and zero-progress detection. The implementation now terminates or defers zero-progress passes and is materially safer than the prior runaway-child incident.

The root does not freeze a complete target-ID manifest/due-session watermark; continuations re-query the due population, so newly eligible rows can enter and repeated clicks/API calls can create competing roots (`WIN-008`). Termination safety is improved, but target scope is not fully frozen. Maturation may update only versioned outcome truth, never `feature_json`.

## 22. Winner Cohort and Generation Registry

**`CALC-WINNER-004 — Cohort/Generation Build and Rescore` (`VERIFIED`).** Cohort dimensions include Ranking-derived `setup_family`/profile and other frozen feature dimensions. Eligibility/training exclusions, definition/model/config/schema versions, target IDs, watermark, outcome revision policy, generation ID/key, manifest, statistics, and content hash define a build.

Bayesian cohort estimates use prior probability 0.5 and prior strength 20. Hierarchy levels L0–L5 require minimum counts 100/40/25/15/15/15. Generation membership is frozen by target manifest/watermark; build completeness and content hashes are validated before publication. Rescore selects explicit prediction IDs, reads immutable original vectors, allows a declared new model/config, writes new generation-bound estimates, and leaves old estimates recoverable. It is `NEW_VERSION_RECALCULATION`, not current-upstream substitution.

## 23. Winner Publication and Serving Registry

**`CALC-WINNER-005 — Generation Publication` (`VERIFIED`).** Generations/manifests are `VERSIONED`; the active generation is a `DERIVED_POINTER`. Publication transactionally validates complete build artifacts before switching the pointer. The switch is atomic and complete, but lacks monotonic evidence/generation fencing: an older completed generation can publish after a newer one (`WIN-007`). Duplicate publication is not a full idempotent no-op success (`WIN-009`). Therefore **atomic != monotonic**.

Legacy serving may operate without the strongest generation contract and must be identified explicitly. A published generation's content is immutable; the mutable active pointer can reinterpret what is served. Required future fence is compare-and-swap over the current pointer plus semantic generation/evidence ordering.

## 24. Pipeline Execution Registry

**`CALC-PIPE-001 — Durable Parent Pipeline` (`VERIFIED`).** UploadRun owns uploaded input; PipelineRun owns an execution; one frozen MarketCalculationContext establishes cutoff/session/calendar; PipelineStep records stage state; BackgroundJob owns asynchronous execution; Decision Handoff Manifest records stage artifact inventory. The durable path is the strongest provenance entry point. The legacy synchronous upload path is `LEGACY` and weaker (`PIPE-003`).

“Who calculates” is the domain service that applies formulas and writes artifacts. “Who causes calculation” is a pipeline stage, API, admin command, maintenance task, or background handler. The parent pipeline causes Fundamentals through Winner in section 2's order, but each service's real inputs are the dependency graph, not adjacent stages.

Workers atomically claim jobs with worker instance, lease, and execution token. Progress/finalization checks ownership; stale recovery can reclaim expired work, but has no universal retry/recovery ceiling (`PIPE-004`). Graceful shutdown stops claims and returns/recovers work. Job `parent_job_id`/`root_job_id` express orchestration trees, not input compatibility.

The CERI provider stage can schedule a child DAG and return before descendants are terminal (`PIPE-001`). Resume validates counts/presence more often than hashes (`PIPE-006`), can select latest MarketCalculationContext by upload run (`PIPE-007`), and the CERI-resume branch invokes Setup with an incomplete envelope (`PIPE-002`). Independent writers can mutate evidence during/after certification (`PIPE-008`). Job-token fencing protects the job row and outer transaction, not every internal domain commit (`PIPE-009`).

## 25. Background Job Registry

| Job registry ID / type | Creator, target and binding | Retry/continuation/idempotency | Mutation and fence assessment |
|---|---|---|---|
| `JOB-PIPE-001` `FULL_PIPELINE` | upload/API; root PipelineRun, frozen context/session/cutoff | durable stages; resume/recovery; stage keys | broad writer cause; strongest outer lease, `PARTIALLY_ENFORCED` |
| `JOB-MD-001` `IB_FETCH` | pipeline/API; upload/ticker/date target, execution re-plans context | checkpoint/retry; content idempotency | PriceBar/revisions; internal commits have narrow stale window (`PIPE-005/009`) |
| `JOB-MD-002` `MARKET_DATA_PREWARM` | scheduler/pipeline; ticker/session plan | re-plans latest completed session/config | PriceBar/revisions; stored plan not enforced, `UNSAFE` |
| `JOB-CERI-001` `SEC_READINESS_REPAIR` | CERI/admin; SEC document/company target | incremental retry, document leases | readiness/source mutation; specialized fencing, `PARTIAL` |
| `JOB-CERI-002` provider acquisition | parent CERI stage; company/provider children, root/parent linked | stable keys; new refresh can collapse | provider evidence; descendants outlive stage (`PIPE-001`, `CERI-012`) |
| `JOB-CERI-003` normalization/features | provider/capture DAG; run/context/company | bounded child payload/retry | normalized evidence/features; modern context strong, source selection partial |
| `JOB-CERI-004` finalizer/capture | DAG finalizer; run/context/session/cutoff | parent completion accounting incomplete | snapshots; transaction/final token generally guards |
| `JOB-CERI-005` changes/alerts | pipeline/admin/rebuild; run/target | rebuild can reread current rules/evidence | change/alert rows; not original reconstruction |
| `JOB-SETUP-001` Setup evaluation | pipeline/direct; run/ticker/context | normal retry | snapshots/lifecycle; canonical strong, direct paths weak |
| `JOB-SETUP-002` Setup replay | admin/job; query-defined historical scope | no immutable target/original-context manifest | current-rules retrospective writes, `UNSAFE` |
| `JOB-SETUP-003` Setup repair | admin/job; run/session range | can select current canonical/prior state | may overwrite newer episode (`SETUP-002`) |
| `JOB-SETUP-004` daily maintenance | scheduler; current target population | current wall clock/latest context | lifecycle/alerts; no original certification envelope |
| `JOB-SETUP-005` alert rebuild | admin/job; rules/transition target | dedup/cooldown current rules | alerts; missing upper bound/rule revision |
| `JOB-WIN-001` `WINNER_PREDICTION_CAPTURE` | pipeline/admin; run/tickers | natural/content uniqueness | immutable vector after weak acquisition; outer job fenced |
| `JOB-WIN-002` `WINNER_OUTCOME_MATURATION` | auto/admin; dynamic due predictions | children, zero-progress guard, depth/retry | versioned outcome; target set not frozen |
| `JOB-WIN-003` `WINNER_OUTCOME_REVISION_CHECK` | auto/admin; matured predictions | revision/content identity | legitimate later truth revisions; exact bar revision IDs absent |
| `JOB-WIN-004` `WINNER_COHORT_REFRESH` | auto/admin; watermark/manifest | generation-key idempotency, bounded build | generation artifacts; strong until publication pointer |
| `JOB-WIN-005` `WINNER_LATEST_RESCORE` | admin/auto; explicit predictions/generation | frozen target/vector, new generation | new estimates, old retained; strong |
| `JOB-WIN-006` `WINNER_HISTORICAL_BACKFILL` | admin; historical run/targets | current decision-time defaults | freezes wall-clock-derived historical identity (`WIN-001`) |
| `JOB-IBMI-001` historical/live refresh | scheduler/API; tickers/date/checkpoint | bounded payload, heartbeat/checkpoint | metric/current+revision/live writes; `PARTIALLY_ENFORCED` |
| `JOB-IBMI-002` feature rebuild | API/job; ticker/as-of/config | content dedup, ticker-loop continuation | internal commits lack token fence and sources ignore cutoff (`XINT-002/009`) |
| `JOB-IBMI-003` scanner/flex/trade-journal | IB routes/scheduler; payload-bound targets | checkpoints/idempotency vary | IB-adjacent artifacts; no direct canonical Winner dependency |

No material declared-but-unused handler changes the dependency graph. Optional IWM/TLT/VIXY regime inputs and CERI provider-priority policy are inactive/ineffective behavior rather than evidence of an extra consumer edge.

## 26. Dangerous Writer Registry

| Writer ID | Artifact; entry point | Identity binding / target | Historical meaning and fence |
|---|---|---|---|
| `WRITER-FUND-001` | FundamentalScore; upload/pre-pipeline/recalculate | run+ticker; `DELETE_RECREATE` | rewrites old run meaning; outer transaction only |
| `WRITER-TECH-001` | TechnicalScore/cache; pipeline/standalone | run+ticker; cache has richer content identity | final score replace semantics; safe cache boundary, weak result generation |
| `WRITER-COMB-001` | CombinedResult; pipeline/standalone | run+ticker; source IDs/config snapshot | delete/recreate; context/session not bound |
| `WRITER-RANK-001` | RankingResult; pipeline/admin/service | run+ticker+profile name | mutable upsert; no profile-generation identity |
| `WRITER-MD-001` | PriceBar/Revision; fetch/prewarm/provider | ticker/session/source/basis | current row changes, revisions retained; internal fence varies |
| `WRITER-REGIME-001` | regime snapshots; pipeline/direct | run/date/version partial | append/versioned, but latest selector shifts |
| `WRITER-SECTOR-001` | sector snapshots; pipeline/direct | run/date/config | versioned; global/prior selection can contaminate |
| `WRITER-CERI-001` | source/features/snapshots; DAG/direct/backfill | modern run/context/session; alternate partial | append-like; refresh-key and selector weaknesses |
| `WRITER-CERI-002` | CERI rules/events; admin/purge/rebuild | target/rule ID | mutable/destructive; may stale hashes (`CERI-008/010`) |
| `WRITER-SETUP-001` | Setup snapshots/canonical; pipeline/direct | canonical rich; alternate run/session partial | append snapshot plus mutable canonical pointer |
| `WRITER-LIFE-001` | episodes/evaluations/events; evaluate/repair/replay | run/session/prior chain partial | old repair can rewrite current episode |
| `WRITER-ALERT-001` | Setup/CERI alerts/rules; jobs/admin | event/rule/dedup identity | mutable rules/current retrospective rebuild |
| `WRITER-WIN-001` | Winner prediction vector; pipeline/admin/backfill | run+ticker/model/schema; no strong decision context | immutable values after insert, possibly wrong acquisition identity |
| `WRITER-WIN-002` | outcomes/revisions; maturation/check | prediction+horizon | legitimate versioned truth; bar revision lineage incomplete |
| `WRITER-WIN-003` | generations/manifests/estimates; cohort/rescore | generation/contract/watermark | content strong and versioned |
| `WRITER-WIN-004` | active generation pointer; publish | generation pointer | atomic/complete, not monotonic/idempotent |
| `WRITER-IBMI-001` | metric current/revisions/live; IB jobs/API | ticker/session/module/intelligence run partial | checkpoints; current projection can shift |
| `WRITER-IBMI-002` | IB feature rows; rebuild/service | as-of/config/version, not bounded constituents | stale worker can commit; temporal label can be false |
| `WRITER-OPS-001` | Winner estimates; approved ops script | actor/request/hash | external service path; two explicit write flags, `CONDITIONALLY_SAFE` |
| `WRITER-OPS-002` | SEC CIK identity; resolver script | company/lookup | operator-controlled; `--dry-run` optional |
| `WRITER-MIG-001` | calculation tables; migrations/data fixes | deployment revision | historical deployment writer; relevant to pre-baseline reconstruction |

No second shadow writer bypassing all named service/script paths was found (`PARTIALLY_VERIFIED`). Database keys enforce local shape, not cross-table cutoff/config/version compatibility.

## 27. Snapshot and Artifact Compatibility Matrix

Codes: `E`=`ENFORCED`, `V`=`VALIDATED`, `R`=`RECORDED_ONLY`, `A`=`ASSUMED`, `N`=`NOT_ENFORCED`, `—`=`NOT_APPLICABLE`. Columns are run, pipeline/context, session, cutoff, calendar, config, calculation/model version, source revision, generation.

| Producer → consumer | Run | P/C | Sess | Cut | Cal | Cfg | Ver | Rev | Gen | Result |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Price revisions → PIT reader | — | R | E | E | V | — | V | E | — | safe canonical boundary |
| Fundamental + Technical → Combined | E | N | N | N | N | R/V | R | N | N | `VIOLATED` |
| Fundamental + Technical → Ranking | E | N | N | N | N | N | R | N | N | `VIOLATED` |
| IBMI liquidity → Ranking | N/global | N | A | partial | N | N | R | N | N | `VIOLATED` |
| Ranking + regime/prior → Sector | E/partial | N | V | partial | R | sector E; input partial | R | R | — | `PARTIAL` |
| Events/price/IBMI → CERI | E/partial | E/partial | V | V/partial | R | R/V | R/V | partial | — | canonical `PARTIAL`; direct unsafe |
| Technical → Setup | E | V canonical | V | V | V | R | R | partial | — | readiness still violated |
| Combined `earnings_risk` → Setup | E | partial | A | A | N | N | R | N | — | actionability compatibility violated |
| Combined/Ranking metadata → Setup | E | partial | A | A | N | N | N | N | — | provenance/display only, incomplete |
| Regime/Sector → Setup | E preferred | V/partial | V | V/partial | R | partial | R | R | — | canonical partial; fallback unsafe |
| Setup snapshot → Lifecycle | E | V canonical | V | V | R | V/partial | R | source IDs partial | — | prior-chain upper bound violated |
| Raw/Fund/Tech/Combined/Ranking/Regime/Sector → Winner | E | R not V | wall-clock | wall-clock | partial | partial | R | N/partial | — | acquisition `VIOLATED` |
| Winner prediction → outcome | E | by frozen prediction | E | decision frozen / truth current | V | E | E | N exact bars | outcome rev E | `PARTIAL` |
| Prediction/vector → rescore generation | E | E | E | E | R | E | E | E vector hash | E | `ENFORCED` |
| Generation → active pointer | E | E | E | E | E | E | E | E | E | completeness E; ordering N |
| IBMI constituents → feature | mixed | N | label R, source N | label R, source N | N | R | R | hashes R | — | `VIOLATED` |

Shared run is never sufficient proof of the other columns.

## 28. Readiness Propagation Matrix

| Edge | Producer readiness/confidence; numeric state | Consumer policy | Behavioral versus provenance impact |
|---|---|---|---|
| Technical → Combined | insufficiency/confidence exist; positive numerics survive | no adequate readiness/confidence gate | score/decision behavior contaminated (`CORE-009/RANK-001`) |
| Technical → Ranking | same; numerics survive | post-score low-confidence label; row remains in normalization | rank/population behavior (`RANK-001/002`) |
| Technical → Setup/Lifecycle directly | same; numerics survive | no adequate technical readiness/confidence gate | direct lifecycle state/alert actionability (`SETUP-003`) |
| Technical → Winner | frozen numerics | `insufficient_data=true` hard rejected; confidence partial | explicit insufficiency blocked; low-confidence policy gap (`WIN-004`) |
| Combined → Setup | completeness/config metadata; values populated | no complete compatibility validation | score/decision metadata only; `earnings_risk` affects actionability (`SETUP-008`) |
| Ranking → Setup | completeness/warnings/profile; score populated | provenance/readiness incomplete | score/decision/profile metadata only; display/provenance inconsistency, not lifecycle gate |
| Ranking → Winner | warnings/profile identity; derived fields populated | no full readiness/config validation | independent cohort-feature impact (`WIN-003`) |
| Regime → Setup | confidence/warnings; bullish policy can survive Gray | partial checks | gate/policy actionability may be stale (`CORE-002`) |
| Regime → Winner | policy values populated | no full coherence validation | frozen probability features (`WIN-005`) |
| Sector → Setup | confidence/warnings populated | partial | actionability/context can inherit global/prior mixing |
| Sector → Winner | same | incomplete compatibility | independent frozen feature impact (`WIN-005`) |
| IBMI liquidity → Ranking | coverage/confidence/freshness | coverage only | tradeability penalty may use low/future evidence (`XINT-002/004`) |
| IBMI volatility/short → CERI | same | coverage only | CERI feature impact may use low/future evidence |
| Setup signal → Lifecycle | inherited readiness incomplete | partial | actionable transition/alert can originate in insufficient technical |
| Winner prediction → cohort | eligibility/exclusions and outcome maturity | generation contract filters | recorded cohort eligibility/policy, versioned generation |

## 29. Implicit Context Fallback Registry

| Fallback ID / caller | Expected owned artifact → selected fallback and ordering | Compatibility/provenance | Calculation effect |
|---|---|---|---|
| `FALLBACK-PIPE-001` resume | exact context → latest by upload run | context ID not proven; fallback visible only indirectly | stages may use wrong cutoff (`PIPE-007`) |
| `FALLBACK-MD-001` fetch/prewarm | stored plan → recalculated latest completed session/current config | run payload exists; session/config changed and not enforced | PriceBar acquisition (`PIPE-005`) |
| `FALLBACK-REGIME-001` sector | same-run regime → global latest snapshot ordered date/ID | run/context incompatible; source ID partially visible | sector score (`CORE-006`) |
| `FALLBACK-SECTOR-001` prior | same-run compatible prior → latest prior sector across runs | session lower/ordering only; config/run not proven | trend/history (`CORE-007`) |
| `FALLBACK-RANK-001` ranking | run-owned liquidity → latest ticker IBMI feature before coarse upload time | global, constituent cutoff/config not proven or persisted | tradeability/normalization (`RANK-003`) |
| `FALLBACK-CERI-001` price | bounded PIT → current/optional-cutoff PriceBar | run/cutoff/revision not proven | price response (`CERI-003`) |
| `FALLBACK-CERI-002` knowledge | `retrieved_at` → `ingested_at` | recorded but semantic downgrade not authoritative | evidence eligibility (`CERI-006`) |
| `FALLBACK-CERI-003` changes | bounded run events → newest/current catalyst/guidance | cutoff/run may mismatch, weakly exposed | change state (`CERI-004`) |
| `FALLBACK-SETUP-001` direct/repair | run/context-owned sources → latest/current/global rows | provenance may carry selected IDs but compatibility not guaranteed | setup/actionability (`SETUP-005`) |
| `FALLBACK-LIFE-001` prior state | latest prior/current episode → row ordered without calculation-time upper bound | future state can masquerade as prior | lifecycle transition (`SETUP-002/004`) |
| `FALLBACK-ALERT-001` cooldown | prior eligible alert → latest matching alert without historical upper bound | later alert not distinguishable as invalid for replay | alert suppression (`SETUP-007`) |
| `FALLBACK-WIN-001` capture/backfill | explicit decision context → current time/latest same-run artifacts | wall-clock and selected IDs frozen; compatibility not validated | prediction vector (`WIN-001/002`) |
| `FALLBACK-WIN-002` serving | monotonic newer generation → requested completed generation | content valid but ordering absent | serving rollback (`WIN-007`) |
| `FALLBACK-IBMI-001` feature | bounded constituent rows → unbounded/latest metric/live/availability/current PriceBar | feature label/hash hides temporal incompatibility | Ranking/CERI inputs (`XINT-002`) |

These fallbacks are `UNSAFE` unless the row states otherwise. “Latest” is a selection rule, not lineage.

## 30. Wall-Clock Dependency Registry

| Clock ID / path | Clock role | Assessment |
|---|---|---|
| `CLOCK-OPS-001` job lease, heartbeat, created/updated timestamps | operational scheduling/ownership | legitimate when not reused as business time |
| `CLOCK-RANK-001` Combined/Ranking earnings proximity | current date determines historical proximity | `VIOLATED` (`RANK-007`) |
| `CLOCK-WIN-001` prediction capture/historical backfill | current time derives `decision_at` and session | `VIOLATED` (`WIN-001`) |
| `CLOCK-SETUP-001` direct/repair/replay/maintenance | current/latest context fills missing historical envelope | `VIOLATED` (`SETUP-005/006/010`) |
| `CLOCK-IBMI-001` historical availability/freshness | current observation/latest source affects old as-of | `VIOLATED` (`XINT-002/003`) |
| `CLOCK-LEGACY-001` legacy pipeline context creation | current execution creates business anchor | `LEGACY`, not reproducible (`PIPE-003`) |
| `CLOCK-MD-001` prewarm/fetch replanning | latest completed session recalculated at execution | `NOT_ENFORCED` against stored plan (`PIPE-005`) |

Operational `now()` is valid for leases, retries, audit timestamps, and publication time. It is unsafe when it chooses a historical session, eligibility, freshness, earnings proximity, or decision identity.

## 31. Fingerprint and Manifest Registry

| Fingerprint ID | Creator/canonicalization; inputs; storage/consumer | What it proves / does not prove | R/V/E/A |
|---|---|---|---|
| `FP-CANON-001` Canonical evidence hash | CanonicalEvidenceSerializer; SHA-256 deterministic JSON with UTC microseconds, normalized decimal, sorted maps/reasons; stored across CERI/regime/sector/Winner/IBMI | proves equality of supplied canonical payload; not completeness, truth, cutoff, or omitted config | Y / selective / selective / N globally |
| `FP-CTX-001` Market context fingerprint | context service; canonical SHA-256 of cutoff/session/calendar/bar-readiness fields; context validators | proves exact frozen context payload; not downstream compatibility unless ID validated | Y/Y/Y/Y for context |
| `FP-MD-001` PriceBar change hash | persistence; MD5 normalized OHLCV/adjustment/source; current+revision rows | proves content changed and revision lineage; not consumer cutoff/config | Y/Y/Y/N |
| `FP-MD-002` price-series version | PIT/technical service; ordered eligible bars/revisions | proves exact eligible price series; not scoring/non-price policy | Y/Y/selective/Y for series |
| `FP-TECH-001` technical cache key | cache; ticker/timeframe, adjusted/trades series versions, feature config, as-of, engine/schema | proves feature-equivalent reuse; intentionally excludes score config/readiness | Y/Y/Y/Y for cached features |
| `FP-FUND-001` fundamental debug/config hash | fundamental service; model/config subset in debug | indicates reported settings; not source period/effective time/currency/immutable input | Y/weak/N/N |
| `FP-RANK-001` ranking profile identity | config loader; profile name/partial weights in result | proves named profile only; not exact YAML/hash/version/population | partial/N/N/N (`RANK-005`) |
| `FP-CERI-001` CERI config/evidence hashes | CERI services; canonical included feature/source/config subsets | proves equality of included evidence; not effective provider priority, omissions, or validity after purge | Y/often/often/N globally |
| `FP-CERI-002` SEC processor signature | SEC processor; four semantic version fields | proves declared processor-version tuple; not code/Git/deps/schema/full config | Y/selective/selective/N |
| `FP-SETUP-001` source/config/anchor fingerprints | Setup/preflight; selected source IDs/values, config, transition anchor | proves exact canonical selection/anchor validated on that path; not alternate-path equivalence or mutable rule history | Y/Y/Y/partial |
| `FP-HANDOFF-001` Decision Handoff Manifest | parent pipeline; artifact IDs/counts and context metadata | proves declared inventory; not CERI descendant terminality or Winner input compatibility unless validated | Y; Setup Y/Y; Winner N/N; N global |
| `FP-WIN-001` Winner feature/evidence hash | capture; canonical frozen feature/evidence JSON | proves immutable vector equality after capture; not temporal validity of source selection | Y/Y/Y/Y for vector |
| `FP-WIN-002` generation key/content hash | cohort service; model/config/contract/watermark/targets/statistics | proves reproducible complete generation content; not publication monotonicity/idempotency | Y/Y/Y/Y for generation |
| `FP-IBMI-001` metric hash/revision | IB repository; canonical metric content | proves metric content change/history; not historical reader cutoff or stable old-run ownership | Y/selective/Y/N |
| `FP-IBMI-002` feature input/evidence hash | IB calculator; included constituents/output/config | proves same included payload for dedup; not that constituents were eligible at declared as-of/cutoff | Y/Y/dedup only/N (`XINT-002`) |
| `FP-JOB-001` execution token | worker; job ID/current attempt/lease | proves worker owned job at guarded point; not safety of earlier internal commits | Y/Y/Y at checks/N globally |

R/V/E/A abbreviates `RECORDED`/`VALIDATED`/`ENFORCED`/`AUTHORITATIVE`. A stored hash is never described as enforcement without a blocking comparison.

Invalidation and mutability complete each proof contract: `FP-CTX-001`, `FP-MD-002`, `FP-TECH-001`, `FP-SETUP-001`, `FP-WIN-001`, and `FP-WIN-002` are immutable for their persisted payload/version and invalidate by creating a new context, series, cache artifact, snapshot, prediction, or generation. `FP-MD-001` and `FP-IBMI-001` create new revisions when content changes; the current projection remains mutable. `FP-FUND-001` and `FP-RANK-001` can be replaced/upserted with their result and do not preserve an authoritative old config. `FP-CERI-001/002` normally version new evidence, but mutable rules and destructive purge can invalidate meaning without recomputing every surviving hash. `FP-HANDOFF-001` is immutable as recorded inventory but does not invalidate Winner use because Winner does not compare it. `FP-IBMI-002` is immutable on its feature row and invalidates through a new feature row, but temporal invalidity is outside its payload proof. `FP-JOB-001` is mutable attempt state; reclaim replaces the token, immediately invalidating the prior worker's ownership at guarded comparisons.

## 32. Configuration Authority Registry

| Subsystem | Sources / declared model | Effective resolution and gate | Persisted reconstruction |
|---|---|---|---|
| Pipeline | environment/settings durable-pipeline flag | selects durable versus legacy path | path metadata partial; no universal effective-config hash |
| Calendar/context | settings/calendar data/version | context service freezes session/cutoff/calendar | authoritative when context retained/validated |
| Fundamental | code/config `fundamentals_v2.1` | current service model | debug subset only; history incomplete |
| Technical | YAML/settings v4 `4.0.0`, v5 `5.0` shadow; cache flags | engine/schema/feature config; scoring separate | strong feature key, result policy partial |
| Regime | YAML `mrcc-1.0` | current service config | no full config hash (`CORE-008`) |
| Combined | service/config snapshot | current scoring inputs | full Combined snapshot/hash stored; context still weak |
| Ranking | YAML profiles | profile name selects weights/gates | no full immutable hash/version (`RANK-005`) |
| Sector | YAML/model `1.0` | current service config | full hash/version on snapshot |
| CERI | YAML `ceri-1.3.0`, settings, DB rules, API/job payload | flags/providers + algorithms + mutable rules | substantial but incomplete; effective priority/rule history weak |
| Setup/Lifecycle | YAML `slse-1.3.0`, settings, mutable DB alert rules, API | canonical flags/preflight; alternates weaker | snapshot config/source strong; rules/history partial |
| Winner | settings flags, YAML/engine, calculation `1.1`, schema `1.0` | subordinate capture/maturation/cohort flags can enable mutation despite nominal global/engine switch | vector/generation config strong; global gate authority contradictory (`WIN-010`) |
| IBMI | settings global/module flags, YAML `ibmi-1.0`, API/job payload | settings/section flags; YAML `engine.enabled` is not master | output config/hash stored; constituent temporal policy not reconstructable |

Material source types are environment variables, settings objects, YAML, DB rules, API parameters, job payloads, hard-coded thresholds, and runtime/current state. No single source wins globally. Every command must be interpreted through its subsystem's effective resolution order; “enabled” in a name is not evidence of authority (`XINT-010`).

## 33. Historical Reconstruction Semantics Registry

| Operation | Exact semantic class at baseline | Contract |
|---|---|---|
| PriceBar PIT read | `ORIGINAL_CONTEXT_RECONSTRUCTION` | explicit cutoff/session/revisions; strongest historical reference |
| pipeline retry/resume | `OPERATIONAL_RETRY` | reuses stored pipeline context, except incomplete Setup resume branch |
| stale-job recovery | `OPERATIONAL_RETRY` | new execution token/lease; no universal recovery ceiling |
| CERI provider refresh/backfill | `DATA_ACQUISITION` | obtains provider truth; stable key can collapse a new cycle |
| CERI replay/rebuild | `CURRENT_RULES_RETROSPECTIVE` / `CURRENT_STATE_REPAIR` | current DB rules and possibly unbounded events; not original replay |
| Setup replay/repair | `CURRENT_STATE_REPAIR` / `CURRENT_RULES_RETROSPECTIVE` | current canonical/config/prior state; can rewrite newer state |
| Setup daily maintenance | `CURRENT_STATE_REPAIR` | current wall clock/current context, not historical reconstruction |
| Winner historical capture/backfill | `NEW_VERSION_RECALCULATION` using current decision clock | name overstates historical identity (`WIN-001`) |
| Winner rescore | `NEW_VERSION_RECALCULATION` over frozen original vector | safe declared model/generation experiment |
| Winner rematuration | `CURRENT_RULES_RETROSPECTIVE` over later corrected truth | decision vector remains frozen; outcome revision changes |
| IBMI historical feature rebuild | intended retrospective, actually `CURRENT_STATE_REPAIR` | latest/unbounded constituents violate declared as-of |
| technical cache rebuild | `NEW_VERSION_RECALCULATION` | explicit series/as-of/config key, safe within feature-cache boundary |
| market-data refresh | `DATA_ACQUISITION` | current corrected PriceBars/revisions; historical use needs PIT reader |
| bootstrap/recalculate/recovery generic names | `UNKNOWN` until specific command mapped | names alone grant no reconstruction guarantee |

“Replay,” “repair,” “rebuild,” “backfill,” “refresh,” “recalculate,” “bootstrap,” and “recovery” are operational names, not proof of original-context semantics.

## 34. Decision Evidence Versus Later Truth

| Subsystem | Frozen/decision evidence | Legitimate later truth | Baseline separation |
|---|---|---|---|
| Winner | immutable `feature_json` + evidence hash | versioned outcome revisions/current-corrected prices | strongest positive pattern; exact outcome bar revisions missing |
| Price data | PIT-reconstructed series | current PriceBar + revisions | safe only when decision consumers use PIT |
| CERI | versioned feature/snapshot/source evidence | later provider/bar corrections | current reader, unbounded changes, mutable rules, purge can reinterpret history |
| Setup/Lifecycle | snapshot/evaluation/event history | later repair/canonical/rule changes | mutable episode/canonical selection can change prior meaning |
| Ranking | run/profile row | recalculated upstream/profile config | mutable upsert has no immutable generation |
| Fundamentals/Combined/Technical result rows | current run result | later recalculation | delete/recreate can replace historical meaning |
| IBMI | stored feature values/hash | later provider/current metric state | feature itself stable, but rebuild can use temporally invalid current sources |

Later corrections may update outcome/provider truth, but must not mutate or silently reinterpret decision-time evidence. Winner meets the value-freezing part; repository-wide `INV-EVIDENCE-001` does not.

## 35. Global Invariant Registry

No invariant below is fully enforced repository-wide at the audited baseline. Test coverage cites the audited static/targeted evidence; a future certification requires explicit negative temporal and concurrency cases.

| Invariant ID | Statement / classification | Affected systems and findings | Existing / missing enforcement; certification requirement |
|---|---|---|---|
| `INV-IDENTITY-001` | Ownership identity is not sufficient calculation identity. `VIOLATED` | Combined, Ranking, Setup, Winner, IBMI; `XINT-001`, `RANK-004`, `SETUP-005/008`, `WIN-002/003/005` | frozen context locally / typed mandatory envelope and mismatched-input tests missing |
| `INV-TIME-001` | Historical business time never comes from wall clock. `VIOLATED` | Ranking, Setup, Winner, IBMI, legacy; `XINT-003` | canonical context / historical entry points must reject absent anchors and pass replay-date tests |
| `INV-TIME-002` | Historical/prior queries cannot observe future state. `VIOLATED` | Setup alerts/episodes, CERI changes, IBMI; `XINT-002/008` | Price PIT helper / universal upper-bound query helpers and older-after-newer tests missing |
| `INV-READINESS-001` | Insufficient evidence cannot regain actionability through numeric values. `VIOLATED` | Independent edges Technical→Combined, Technical→Ranking, and Technical→Setup directly; IBMI edges; `CORE-009`, `RANK-001/002`, `SETUP-003`, `XINT-004` | Winner technical hard gate / typed ineligibility and consumer-policy tests missing |
| `INV-FALLBACK-001` | Latest/global fallback cannot masquerade as execution-owned provenance. `VIOLATED` | pipeline, regime/sector, Ranking, Setup, CERI, Winner, IBMI; `XINT-001/002/005` | selected IDs sometimes stored / canonical rejection or explicit fallback identity tests missing |
| `INV-CONFIG-001` | Persisted decisions retain/reference exact effective config. `VIOLATED` | regime, Ranking, fundamentals, alert rules; `CORE-008`, `RANK-005`, `CERI-008`, `SETUP-007`, `XINT-010` | sector/Combined/Winner local hashes / global resolved-config snapshot tests missing |
| `INV-FP-001` | Fingerprints declare and enforce explicit proof boundaries. `NOT_ENFORCED` | all manifests/hashes; `XINT-007` | canonical serializer/selected validators / schema registry and omission/mismatch tests missing |
| `INV-ENTRY-001` | All mutating entry points enforce one minimum provenance contract. `VIOLATED` | pipeline, CERI, Setup, Winner, market/IB jobs; `XINT-006` | strong canonical paths / shared command validation across API/admin/replay/backfill tests missing |
| `INV-EVIDENCE-001` | Mutable current projections are not historical evidence without reconstruction. `VIOLATED` | Price, Ranking, Setup, CERI rules, IB metrics, publication; `XINT-005` | Price PIT/Winner vector / historical-read bans and revision-as-of tests missing |
| `INV-REPLAY-001` | Replay/rebuild semantics are explicit and cannot substitute current state. `VIOLATED` | CERI, Setup, Winner backfill, IBMI; `XINT-006` | Winner rescore is explicit / typed mode and original-context fixtures missing |
| `INV-TRUTH-001` | Decision evidence remains immutable when later truth changes. `PARTIALLY_ENFORCED` | Winner strong; CERI, Setup, Ranking, IBMI weak; `XINT-005`, `WIN-006` | Winner frozen vector/versioned outcomes / immutable evidence across all calculations missing |
| `INV-SCOPE-001` | Continuations use frozen targets or declared dynamic scope. `VIOLATED` | Winner maturation, some CERI/Setup batches; `WIN-008` | Winner generation manifests / root target manifest and progress proofs missing |
| `INV-PUBLICATION-001` | Publication is atomic, complete, idempotent, monotonic. `VIOLATED` | Winner serving; `WIN-007/009`, `XINT-011` | transaction/completeness / CAS ordering and duplicate-request tests missing |
| `INV-FENCE-001` | Stale workers cannot commit after ownership loss. `VIOLATED` | IBMI rebuild, some internal-commit paths; `PIPE-009`, `XINT-009` | outer execution token / in-transaction lease guard at every commit and lease-loss tests missing |
| `INV-REFRESH-001` | Retry idempotency cannot suppress a later refresh. `VIOLATED` | CERI acquisition; `CERI-012`, `XINT-012` | retry dedup / refresh-cycle identity and repeated-cycle tests missing |

## 36. Systemic Root-Cause Graph

```text
ROOT A calculation identity collapse (XINT-001)
  ├─ run-only score joins → RANK-004/005/006
  ├─ latest/global selection → PIPE-007, CORE-006/007, RANK-003
  │   ├─ Setup source compatibility → SETUP-005/008
  │   └─ Winner independently inherits regime/sector/ranking issues → WIN-002/003/005
  └─ declared IBMI as-of trusted over constituents → XINT-002
      ├─ liquidity → Ranking contamination
      └─ volatility/short pressure → CERI contamination

ROOT B insufficient values retain numerics (XINT-004, CORE-009)
  ├─ Technical → Combined independently → RANK-001
  ├─ Technical → Ranking independently → RANK-001/002
  ├─ Technical → Setup/Lifecycle directly → state/alerts → SETUP-003
  ├─ Combined earnings_risk → Setup actionability → SETUP-008
  ├─ Combined/Ranking score/decision/profile → Setup metadata only
  └─ Technical → Winner independently; explicit insufficiency is rejected,
      while Ranking readiness/config gaps affect cohort features → WIN-003/004

ROOT C wall clock/current selector as business time (XINT-003/008)
  ├─ Ranking earnings → RANK-007
  ├─ Setup repair/replay/maintenance → SETUP-005/006/010
  ├─ Winner capture/backfill → WIN-001
  └─ IBMI historical freshness/future sources → XINT-002

ROOT D mutable projection ambiguity (XINT-005)
  ├─ current PriceBar bypass → CERI-002/003
  ├─ mutable RankingResult → RANK-006 → Setup metadata inconsistency + Winner independent feature risk
  ├─ Setup canonical/episode/rules → SETUP-002/004/006/007/009
  ├─ CERI rules/purge → CERI-008/010
  └─ Winner active pointer → WIN-007/009 → XINT-011

ROOT E entry-point/fencing fragmentation (XINT-006/009)
  ├─ async CERI descendants → PIPE-001 completion/accounting only
  ├─ incomplete Setup resume invocation → PIPE-002/SETUP-001
  ├─ replanning/independent writers → PIPE-005/008/009
  ├─ CERI retry key collapses refresh → CERI-012/XINT-012
  └─ IBMI ticker-loop commits lack token fence → XINT-009

ROOT F fingerprint/config overstatement (XINT-007/010)
  ├─ incomplete regime/profile identity → CORE-008/RANK-005
  ├─ mutable rules → CERI-008/SETUP-007
  ├─ Winner handoff recorded, not validated → WIN-002
  └─ misleading master switches → WIN-010/IBMI config conflict
```

There is no CERI→Setup, CERI→Winner, or Setup/Lifecycle→Winner edge in this graph. Same-run Sector Rotation is downstream of Ranking.

## 37. Known Safe Patterns

| Pattern ID | Strong reference design | Why strong / boundary |
|---|---|---|
| `SAFE-CTX-001` | frozen MarketCalculationContext | canonical cutoff/session/calendar payload is fingerprinted and validated; consumers must still retain/validate its ID |
| `SAFE-FP-001` | canonical evidence serialization | deterministic SHA-256 payload equality; does not prove omitted inputs or truth |
| `SAFE-MD-001` | PriceBar revisions + PIT reconstruction | separates current truth from observable-at-cutoff truth; unsafe callers can still bypass it |
| `SAFE-TECH-001` | technical content-addressed feature cache | complete feature identity supports deliberate cross-run reuse; final scoring/readiness is outside boundary |
| `SAFE-SETUP-001` | Setup transition preflight and Decision Handoff validation | rejects incompatible canonical transition envelope; alternate entry points remain weaker |
| `SAFE-WIN-001` | frozen Winner `feature_json` | rescore cannot silently reread mutable upstream state; original selection can still be invalid |
| `SAFE-WIN-002` | versioned Winner outcome truth | later truth changes without altering decision vector; exact PriceBar revisions remain missing |
| `SAFE-WIN-003` | Winner generation manifests/content addressing | freezes target/watermark/model/config/content and validates completeness; publication pointer ordering is separate |
| `SAFE-JOB-001` | execution-token fencing with one owned transaction | lease loss rolls back decision writes; internal commits require their own immediate guard |
| `SAFE-SECTOR-001` | sector full config hash/versioned snapshots | makes own calculation policy reconstructable; upstream compatibility must still be proven |

Remediation should extend these bounded designs, not replace them wholesale.

## 38. Known Unknowns and Evidence Gaps

- Live database contents, deployed environment variables, production query plans, task-queue concurrency, and actual provider behavior were not inspected (`UNKNOWN`).
- Static review cannot prove every reflection/plugin/dynamic-SQL writer has executed; no additional principal-table writer was found (`PARTIALLY_VERIFIED`).
- Pre-baseline artifacts may embody older code/config/migrations and need deployment records not in this baseline (`UNKNOWN`).
- Intended business policy for low-confidence IBMI features, low-confidence Winner inputs, sparse regime state, and refresh cadence is not one authoritative contract (`UNKNOWN`).
- Exact production frequency/impact of current/global fallbacks and stale-worker windows is unknown without runtime data.
- CERI provider priority intent conflicts with effective selection; the registry records behavior, not an inferred intended fix (`CONTRADICTORY`).
- Severe-staleness Gray display conflicts with bullish effective policy (`CONTRADICTORY`).
- Winner and IBMI “master” enable settings conflict with actual subordinate gates (`CONTRADICTORY`).
- Exact original config and temporal identity for mutable/deleted historical Fundamental, Ranking, Setup, and alert artifacts cannot be recovered from baseline schema alone.

No unresolved contradiction was converted silently into an assumption. Corrected Tasks 05–07 direct dependency evidence supersedes earlier conceptual/adjacency descriptions.

## 39. Certification Matrix

This is an architecture-level baseline assessment, not production deployment certification.

| Subsystem | Same-run reproducibility | Historical PIT | Config reconstruction | Artifact immutability | Background safety | Cross-run isolation | Replay/reconstruction | Fingerprint authority |
|---|---|---|---|---|---|---|---|---|
| Pipeline/context | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `CERTIFIED` for context only |
| Price market data | `PARTIAL` | `CERTIFIED` via PIT | `PARTIAL` | `PARTIAL` versioned | `PARTIAL` | `PARTIAL` | `CERTIFIED` with cutoff | `PARTIAL` |
| Fundamental | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` | `NOT_CERTIFIED` |
| Technical | `PARTIAL` | `CERTIFIED` canonical input | `PARTIAL` | `NOT_CERTIFIED` result / cache strong | `PARTIAL` | `PARTIAL` | `PARTIAL` | `CERTIFIED` for feature cache |
| Market Regime | `NOT_CERTIFIED` | `PARTIAL` | `NOT_CERTIFIED` | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` | `PARTIAL` | `PARTIAL` |
| Combined | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `PARTIAL` | `NOT_CERTIFIED` | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` | `PARTIAL` |
| Ranking | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `PARTIAL` | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `NOT_CERTIFIED` |
| Sector Rotation | `PARTIAL` | `PARTIAL` | `CERTIFIED` own config | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` | `PARTIAL` | `PARTIAL` |
| CERI | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` | `PARTIAL` |
| Setup/Lifecycle | `PARTIAL` canonical | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` current state | `PARTIAL` | `NOT_CERTIFIED` alternates | `NOT_CERTIFIED` | `PARTIAL` canonical |
| IBMI | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` rebuild | `NOT_CERTIFIED` | `NOT_CERTIFIED` | `NOT_CERTIFIED` temporally |
| Winner capture | `PARTIAL` | `NOT_CERTIFIED` acquisition | `PARTIAL` | `CERTIFIED` vector | `PARTIAL` | `PARTIAL` | `NOT_CERTIFIED` backfill | `PARTIAL` |
| Winner outcome | `PARTIAL` | `NOT_APPLICABLE` as decision PIT; current truth intended | `PARTIAL` | `CERTIFIED` versioning | `PARTIAL` | `PARTIAL` | `PARTIAL` | `PARTIAL` |
| Winner generation/rescore | `CERTIFIED` | `CERTIFIED` frozen vector | `CERTIFIED` | `CERTIFIED` content | `PARTIAL` | `CERTIFIED` manifest scope | `CERTIFIED` rescore | `CERTIFIED` content |
| Winner publication | `PARTIAL` | `NOT_APPLICABLE` | `CERTIFIED` generation | `PARTIAL` pointer | `NOT_CERTIFIED` ordering | `PARTIAL` | `PARTIAL` rollback | `NOT_CERTIFIED` pointer |

No major subsystem is globally `CERTIFIED` across all eight dimensions.

## 40. Remediation Program Blueprint

This is a dependency-aware architecture blueprint only. It implements no remediation and gives no time estimate.

### Phase 0 — Stop unsafe historical contamination

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| future/current source eligibility | `XINT-002/008`, `CERI-003/004`, `SETUP-002/007`; blocks post-cutoff evidence/future state | add query bounds and reject absent historical anchors; repository/API changes, possible lineage columns/backfill | older-after-newer, same-session, future-constituent negative tests |
| stale worker domain commits | `PIPE-009`, `XINT-009`; blocks lost-owner mutation | in-transaction lease guard; handler/API changes, little data migration | deterministic lease-loss-before-commit test |
| publication rollback | `WIN-007/009`, `XINT-011`; blocks older serving activation | pointer CAS/order/idempotency fields; schema/API migration likely | stale/duplicate/older-after-newer publish tests |

### Phase 1 — Calculation Identity

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| execution identity collapse | `XINT-001`, `RANK-004`, `SETUP-005/008`, `WIN-002/003/005` | define typed envelope first; schema/FK and command API changes, backfill or explicit unknown markers | every producer/consumer rejects mismatched run/context/session/cutoff/config/version/revision/generation |

### Phase 2 — Immutable Evidence and Temporal Queries

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| mutable projections as evidence | `XINT-005`, `RANK-006`, `SETUP-002/004/009`, `CERI-010` | depends on Phase 1 IDs; append/version tables, serving pointers, migrations/backfills | historical reads use immutable ID or revision-as-of; projection access rejected |
| incomplete market/outcome lineage | `CORE-004`, `CERI-002`, `WIN-006` | basis/session completeness and exact revision IDs; schema/API changes | gap, mixed-basis, corrected-bar reconstruction tests |

### Phase 3 — Readiness Contract

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| readiness as metadata | `CORE-009`, `RANK-001/002`, `SETUP-003`, `WIN-004`, `XINT-004` | typed unavailable/eligibility decision; score/normalization/API/schema changes, historical classification backfill | each edge defines reject/suppress/default policy; insufficient rows cannot regain actionability |

### Phase 4 — Configuration and Fingerprints

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| fragmented config authority | `CORE-008`, `RANK-005`, `CERI-008`, `SETUP-007`, `WIN-010`, `XINT-010` | resolve one canonical effective config per command; snapshot/hash schema and API fields, backfill unknowns | same hash reproduces policy; any effective setting change changes identity |
| proof overstatement | `PIPE-006`, `CERI-009/010`, `WIN-002`, `XINT-007` | machine-readable fingerprint schemas/validators | omission, mutation, and mismatch tests prove exact boundary and block claimed incompatibility |

### Phase 5 — Entry-Point Unification

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| canonical versus admin/replay disparity | `PIPE-002/003/005/007`, `CERI-003/012`, `SETUP-001/005/006/010`, `WIN-001`, `XINT-006` | depends on identity/config contracts; shared domain command and API/job payload changes; legacy migration plan | identical minimum envelope and policy tests for pipeline, API, admin, repair, replay, backfill, maintenance |

### Phase 6 — Background Scope and Publication

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| dynamic targets/recovery | `PIPE-004`, `WIN-008`, `INV-SCOPE-001` | root target manifests/watermarks and explicit dynamic-scope semantics; job schema/payload changes | every continuation advances frozen scope or terminates; competing-root tests |
| retry versus refresh identity | `CERI-012`, `XINT-012` | acquisition-cycle/generation ID separate from attempt key; API/job/schema change | retry dedups within cycle; later refresh always acquires anew |
| pointer semantics | `WIN-007/009` | Phase 0 fence finalized with rollback/supersession policy | atomic, complete, idempotent, monotonic publication under races/restarts |

### Phase 7 — Historical Reconstruction

| Root mechanism | Findings / risk reduced | Dependencies and likely impact | Certification requirement |
|---|---|---|---|
| ambiguous replay/backfill | `CERI-004/008`, `SETUP-002/006/007`, `WIN-001`, `XINT-006` | requires Phases 1–5; explicit temporal mode and original-context manifests; API/schema changes and selective backfills | operation declares ORIGINAL_CONTEXT, CURRENT_RULES, REPAIR, NEW_VERSION, ACQUISITION, or RETRY and cannot masquerade as another |
| certification evidence | all systemic invariants | generate immutable audit manifest over code/deployment/config/schema/context/source revisions | repeatable golden historical cases, concurrency cases, and registry drift check against a declared implementation baseline |
