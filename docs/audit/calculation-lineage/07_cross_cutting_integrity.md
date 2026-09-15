# Task 07 — Cross-Cutting Integrity, Provenance, and Hidden Calculation Paths

## Audit Identity

| Field | Value | Status |
|---|---|---|
| Audit task | Task 07 only — repository-wide horizontal integrity audit | **VERIFIED** |
| Repository | `C:\Users\Ivica\Documents\SwingLens` | **VERIFIED** |
| Current branch | `codex/winner-evidence-remediation` | **VERIFIED** |
| Current repository HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | **VERIFIED** |
| Implementation baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | **VERIFIED** |
| Implementation drift | None. HEAD equals the Task 01 implementation baseline; `BASELINE_DRIFT` is not raised. | **VERIFIED** |
| Worktree at start | Task 01–06 audit reports and two unrelated IB probe files were pre-existing untracked work; no tracked changes. | **VERIFIED** |
| Alembic head | `0074_ceri_evidence_quarantine` | **VERIFIED** |
| Runtime | CPython 3.12.2 in repository `.venv` | **VERIFIED** |
| Verified date | 2026-09-14, Europe/Zurich | **VERIFIED** |
| Audit mode | Static repository/schema/SQL/test inspection plus isolated, non-provider tests and one pure calculation probe. No live database, job enqueue, provider call, migration, or application mutation. | **VERIFIED** |
| Deliverable | `docs/audit/calculation-lineage/07_cross_cutting_integrity.md` only | **VERIFIED** |

## Executive Assessment

The repeated defects from Tasks 01–06 are systemic design patterns, not six isolated subsystem accidents. SwingLens has a strong calculation-identity model—frozen `MarketCalculationContext`, canonical evidence serialization, revisioned market snapshots, CERI context ownership, transition preflight/handoff manifests, and Winner generation manifests—but that model is enforced mainly on recent canonical/certification paths. Older tables, direct services, admin jobs, replay/repair paths, and global feature overlays still use run/ticker ownership, latest/current selection, or wall clock as substitutes for calculation identity. **VERIFIED**

The principal root mechanism is identity collapse:

```text
execution owner: run/ticker/job
               is treated as
calculation identity: pipeline + context + cutoff + session + config + version + revision
```

That collapse is visible in Combined/Ranking, Setup alternate paths, Winner capture, sector fallbacks, and the newly audited IB market-intelligence feature builder. It causes later/current artifacts to acquire plausible same-run or same-ticker provenance without proving decision-time compatibility. **VERIFIED**

The second root mechanism is projection ambiguity. `PriceBar`, IB historical metric bars, RankingResult, active Setup episodes/canonical selectors, alert rules, and publication pointers are mutable current projections. Revision/history controls exist for some of them, but historical consumers do not uniformly reconstruct the correct version. Winner's frozen `feature_json` and versioned outcome truth are the strongest positive counterexample. **VERIFIED**

Task 07 discovered one important calculation subsystem omitted from the vertical audits: **IB market intelligence**. It produces liquidity, short-pressure, volatility, options-activity, scanner, histogram, and trade-journal artifacts. Its feature rows have good config/version/hash metadata, but the feature builder does not bound historical bars, live snapshots, shortable observations, or PriceBar-derived dollar volume to the declared `as_of_session`/cutoff. A pure probe accepted a 2026-01-10 liquidity observation for `as_of=2026-01-05`, scored it, and reported freshness `AVAILABLE`. Ranking and CERI then consume selected IB features while checking coverage, not confidence/freshness or constituent-source time. **VERIFIED — XINT-002**

## Intended Contract Versus Current Implementation

| Area | Intended contract | Current implementation | Assessment |
|---|---|---|---|
| Calculation identity | Every decision artifact is bound to exact execution, time, config, code/model, and source revisions. | Strong fields exist for newer snapshots, but many consumers require only run/ticker or select latest/current. | **VIOLATED** |
| Historical time | Replay/backfill uses original business-time anchors. | Ranking earnings, Winner capture/backfill, Setup maintenance/replay, and standalone builders can derive time from current execution state. | **VIOLATED** |
| Point-in-time evidence | No source newer than cutoff/session enters a historical calculation. | Authoritative OHLCV and newer CERI paths enforce PIT; CERI bypasses, change rebuild, Setup history, and IB intelligence have openings. | **PARTIALLY_ENFORCED** |
| Readiness | Insufficient/low/error evidence cannot become actionable without explicit policy. | Technical numeric remnants independently enter Combined, Ranking, and Setup/Lifecycle; Setup/Lifecycle reads TechnicalScore directly. Winner blocks explicit technical insufficiency but not all low-confidence/missing requirements. | **VIOLATED** |
| Fingerprints | A hash is authoritative only when its scope is complete and a consumer validates it. | Some hashes are enforced; others are recorded only, omit configuration/context, or become stale after mutation. | **PARTIALLY_ENFORCED** |
| Historical mutation | Current projections never rewrite historical meaning. | Revisioned snapshots and Winner vectors are strong; Ranking, Setup current state/rules, CERI purge, and IB current metrics retain mutable semantics. | **VIOLATED** |
| Entry-point equivalence | Pipeline, API, job, repair, replay, and backfill enforce the same minimum envelope. | Canonical pipeline/certification is materially stronger than alternate entry points. | **VIOLATED** |
| Background fencing | A stale worker cannot commit decision-relevant domain writes. | Job-row completion is token-fenced; internal-commit handlers vary. IB feature rebuild can commit without an in-handler lease check. | **PARTIALLY_ENFORCED** |
| Publication | Active pointers change atomically, completely, idempotently, and monotonically. | Winner switch is atomic/complete but automatic cohort publication is neither monotonic nor duplicate-idempotent. | **VIOLATED** |

## Global Calculation and Mutation DAG

```text
CSV upload
  ├─ RawCompanyRow
  └─ pre-pipeline FundamentalScore write
          │
          ▼
FULL_PIPELINE ── MarketCalculationContext (frozen cutoff/session/calendar)
  ├─ validation / SEC transition preflight
  ├─ FundamentalScore  ─────────────┐
  ├─ PriceBar fetch/cache/revisions ├─ TechnicalScore
  ├─ MarketRegimeSnapshot           │       │
  ├─ CombinedResult ◄───────────────┘       │
  ├─ RankingResult ◄── global latest IB liquidity overlay
  ├─ SectorRotationSnapshot ◄── ranking + regime + prior sector history
  ├─ CERI workflow/capture ◄── provider/SEC evidence + IB vol/short + PriceBar
  ├─ Decision Handoff Manifest
  ├─ SetupSignalSnapshot → mutable canonical selection
  │       └─ active LifecycleEpisode → versioned events → alerts
  └─ WinnerPredictionSnapshot (frozen feature_json)
          └─ current PriceBar outcome truth → revisions
                 └─ cohort watermark → generation/manifests/statistics
                        └─ generation-bound rescore → serving pointer

Sideways/background writers:
  IB_FETCH / MARKET_DATA_PREWARM ───────────────► PriceBar current projection
  IB intelligence jobs ─► metric current rows/snapshots/features ─► Ranking/CERI
  CERI direct ingest/backfill/rebuild/replay/purge ─► CERI state
  Setup direct evaluate/repair/replay/maintenance/alert rebuild ─► Setup state
  Winner maturation/revision/cohort/rescore/backfill/publication ─► Winner state
  startup/supervisor recovery ─► reclaims and replays durable jobs
  scripts/admin routes ─► selected historical/current artifacts
```

The canonical parent stage sequence (ordering only) is technical, market regime, combined, ranking, sector rotation, CERI, Setup/Lifecycle, then Winner. The arrows in the DAG reflect actual reads, not mere stage adjacency. Sector rotation consumes ranking; same-run ranking does not consume sector rotation. CERI is not an input to Setup/Lifecycle or Winner, and Setup/Lifecycle is not an input to Winner. **VERIFIED**

## Canonical Time and Session Dictionary

| Term/field | Canonical meaning | Actual conflicts or caveats | Status |
|---|---|---|---|
| `cutoff_at` / calculation cutoff | UTC instant after which evidence is ineligible. | Optional or reconstructed in several standalone paths; sometimes source maximum rather than parent cutoff. | **PARTIALLY_VERIFIED** |
| `MarketCalculationContext.cutoff_at` | Immutable cutoff owned by one pipeline; authoritative with context ID. | `market_context_for_upload_run()` chooses newest context for the run when pipeline identity is absent. | **VERIFIED / UNSAFE FALLBACK** |
| `latest_completed_session` | Latest US trading session complete under close/bar-readiness policy at cutoff. | Some code uses `cutoff.date()` or wall-clock `date.today()` instead of the shared market clock. | **PARTIALLY_VERIFIED** |
| `input_as_of_session` | Latest permitted market input session for a calculation. | Market/sector persist it; many core rows do not. IB feature rows persist it but source queries do not enforce it. | **PARTIALLY_VERIFIED** |
| `as_of_date` | Snapshot business label. | Market regime uses max of independently loaded benchmark sessions; may not be one common source session. | **CONTRADICTORY** |
| `as_of_session` | Trading-session identity for CERI/IB/Winner evidence. | CERI generally maps events explicitly; IB features can contain future constituent sessions. | **PARTIALLY_VERIFIED** |
| `data_as_of_date` | Setup snapshot evaluation/session date. | Can be reconstructed from current canonical snapshots; not sufficient to prove cutoff/context. | **PARTIALLY_VERIFIED** |
| `decision_at` | Instant at which Winner decision evidence is frozen. | Defaults to capture wall clock, including historical backfill. | **VIOLATED** |
| `prediction_as_of_date` | Latest completed US session derived from Winner `decision_at`. | Deterministic after `decision_at`, but the chosen decision instant may be wrong. | **PARTIALLY_VERIFIED** |
| `event_date` | Economic/calendar date supplied by a source. | Must be mapped to an effective session; uploaded earnings lacks per-value knowledge-time provenance. | **PARTIALLY_VERIFIED** |
| `effective_session` | Trading session on which evidence/event is allowed to affect decisions. | CERI has explicit policy; IB historical rows set it equal to session date and feature builder does not bound it. | **PARTIALLY_VERIFIED** |
| `bar_date` / `session_date` | Trading date of an OHLCV/metric observation. | It is not knowledge time; current rows may reflect later revisions. | **VERIFIED** |
| `first_seen_at` | First local possession/observation time for a mutable evidence row. | Essential to PIT reconstruction; not all external values have it. | **VERIFIED** |
| `observed_at` | Provider/local observation time. | CERI legacy surprise may prefer provider observation over local retrieval when `known_at` is absent. | **CONTRADICTORY** |
| `retrieved_at` / `ingested_at` | System possession and persistence times. | Strong CERI lineage fields, but not present on uploaded fundamentals/earnings values. | **PARTIALLY_VERIFIED** |
| `created_at` | Database insertion time. | Frequently used as availability/order proxy; not automatically business time. | **CONDITIONALLY_SAFE** |
| `updated_at` | Operational mutation time. | Used as evidence cutoff in some generation logic with explicit rules; otherwise cannot establish original meaning. | **CONDITIONALLY_SAFE** |
| `published_at` | Time an estimate/generation became serving or source publication time, depending table. | Same name family has both operational-publication and source-publication semantics. | **AMBIGUOUS** |
| `source_timestamp` | Provider-supplied temporal field. | Meaning varies by dataset and is not equivalent to possession time. | **AMBIGUOUS** |

## Systemic Risk Pattern Register

| Pattern | Root mechanism | Confirmed occurrences | Isolated/systemic | Downstream effect | Status |
|---|---|---|---|---|---|
| Ownership identity substituted for calculation identity | Join by run/ticker/company only | Combined, Ranking, Setup loader/history, Winner loader, sector consumers | Systemic | Cross-context/version mixing | **VERIFIED** |
| Latest/current fallback | Missing owned artifact replaced by newest/global state | Market context, sector regime, ranking liquidity, Setup source/prior state, Winner manifest/input selection, IB features | Systemic | Silent contamination with plausible provenance | **VERIFIED** |
| Wall-clock business time | `now`/today converted into session/decision/risk | Ranking earnings, Winner capture/backfill, standalone contexts, Setup maintenance orchestration, IB feature rebuild | Systemic | Historical meaning changes with execution time | **VERIFIED** |
| Mutable projection treated as evidence | Current row or pointer read without reconstruction | CERI optional PriceBar path, Ranking, Setup canonical/active state/rules, IB metric/live inputs, legacy Winner serving | Systemic | Retrospective drift | **VERIFIED** |
| Readiness metadata not enforced | Numeric values survive insufficient/low/error | Technical independently feeds Combined, Ranking, and Setup/Lifecycle; Setup/Lifecycle reads TechnicalScore directly. Combined score/decision and Ranking score/decision/profile are Setup metadata only, while Combined `earnings_risk` participates in Setup actionability. Winner independently consumes Technical/Combined/Ranking and rejects explicit technical insufficiency, but Ranking policy gaps remain; IB → Ranking/CERI | Systemic | Invalid evidence regains actionability at unchecked edges | **VERIFIED** |
| Configuration identity omitted or partial | Name/version/debug only | Ranking, market regime, fundamentals input identity, Setup upstream profile, alert rules | Systemic | Historical rules unrecoverable | **VERIFIED** |
| Recorded fingerprint mistaken for proof | Hash copied but not compared or scope incomplete | Winner handoff lineage, prewarm metadata, CERI processor signature/config gaps, stale purge hashes | Systemic | False confidence in lineage | **VERIFIED** |
| Canonical path stronger than alternates | Required context only in pipeline/certification | Legacy upload, standalone technical/ranking, Setup direct/repair/replay, CERI direct/rebuild, Winner backfill/admin | Systemic | Entry-point-dependent correctness | **VERIFIED** |
| Historical upper bound omitted | Prior/current queries lack `<= calculation time` | Setup alert cooldown, CERI run change rebuild, IB feature sources | Repeated | Future evidence influences older result | **VERIFIED** |
| Dynamic background target set | Continuation re-queries eligibility | Winner maturation; CERI all-company backfill scope can be rediscovered on later invocation | Limited but important | Root scope broadens | **PARTIALLY_VERIFIED** |
| Atomic but non-monotonic pointer | Transactional switch lacks version ordering | Winner automatic cohort publication | Isolated current pointer implementation | Older evidence becomes active | **VERIFIED** |
| Job-row fence not domain-write fence | Handler commits before/without ownership check | IBMI rebuild; partially mitigated IB fetch/internal SEC paths | Cross-cutting risk, one clear violation | Stale worker domain commit | **PARTIALLY_VERIFIED** |
| Retry identity conflated with refresh identity | Stable key reused beyond one attempt | CERI direct ingestion/admin; potentially all-company backfill | Repeated in CERI | Legitimate acquisition suppressed | **VERIFIED** |

## Ownership Identity Versus Calculation Identity

This register covers material calculation consumers, not trivial display/export lookups. `run_id` and ticker prove ownership only; they are safe only where the source is immutable within the run or the consumer separately validates the richer envelope. **VERIFIED**

| Consumer/edge | Selection identity | Rich identity available upstream | Classification | Reason/status |
|---|---|---|---|---|
| Raw row → FundamentalScore | run + ticker/raw row | Upload row ID/content, model/config hash in debug | `CONDITIONALLY_SAFE` | Raw upload is stable, but source value/effective-time/currency manifest is absent. **VERIFIED** |
| Fundamental/Technical → CombinedResult | run + ticker | Technical context/session/config; fundamental model hash | `UNSAFE` | Readiness and compatibility are not enforced; result is delete/recreated. RANK-001/004. **VERIFIED** |
| Raw/Fundamental/Technical → RankingResult | run + ticker | Technical context/config/readiness; IB feature session/config | `UNSAFE` | Profile config and temporal compatibility are not persisted/validated. RANK-002/004/005. **VERIFIED** |
| Ranking global liquidity overlay | ticker + latest before run `processed_at` | IB feature ID/session/cutoff/config/input signature | `UNSAFE` | No run/context match or persisted feature ID; coverage only. RANK-003. **VERIFIED** |
| Ranking/Technical → sector universe | same run + ticker | Snapshot/config/context on sector output | `CONDITIONALLY_SAFE` | Same-run ownership is enforced; ranking itself lacks temporal/config identity. **VERIFIED** |
| Market regime → sector rotation | run/date if available, else global | Context/cutoff/session/revision/evidence hash | `UNSAFE` on fallback | Global fallback is silent. CORE-006. **VERIFIED** |
| Setup source loader | run + ticker, then selected profile/latest snapshots | Handoff/pipeline/context/session/config/source revisions | `CONDITIONALLY_SAFE` in guarded pipeline; `UNSAFE` direct | Parent validates handoff; alternate paths reconstruct/latest-fallback. SETUP-005/008. **VERIFIED** |
| Setup prior canonical history | ticker/timeframe/family/date | run/config/context/source hash/canonical selection | `UNSAFE` | Cross-run/config mutable canonical rows can supply history. SETUP-004/009. **VERIFIED** |
| Winner capture core graph | run + ticker; first ranking; latest same-run snapshots | parent pipeline/context/cutoff/handoff/config/revisions | `UNSAFE` as compatibility proof | Values freeze by value, but capture chooses current decision time and does not validate manifest. WIN-001/002/003/005. **VERIFIED** |
| Winner rescore | prediction ID + exact published generation/contract | frozen vector/manifests/watermark | `SAFE` | Does not reread mutable upstream state. **VERIFIED** |
| CERI pipeline features | company/run + context/cutoff/config/calculation version | full CERI ownership and PIT fields | `SAFE` for modern pipeline artifacts | Database/service context constraints and PIT selectors apply, except named defects. **PARTIALLY_VERIFIED** |
| CERI change rebuild | run companies + unbounded current event rows | event effective time/cutoff | `UNSAFE` | Forwarded cutoff does not bound catalyst/guidance reads. CERI-004. **VERIFIED** |
| IB feature persistence | ticker + as-of + module + calc/config + output/input signature | intelligence run and cutoff | `CONDITIONALLY_SAFE` for content reuse | Dedup intentionally crosses runs and omits cutoff/run from unique identity; exact same source hashes/output can reuse an earlier owner. **VERIFIED** |
| IB feature source selection | ticker/module only for constituent histories/latest snapshots | session/effective session/observed time/hash | `UNSAFE` | No `<= as_of/cutoff` bounds. XINT-002. **VERIFIED** |
| Trade episode → research | ticker + latest completed pre-entry run/results | explicit source IDs and decision cutoff | `CONDITIONALLY_SAFE` | Has upper time bounds and ambiguity status, but upstream config/context completeness remains limited. **VERIFIED** |
| Display/export APIs | run/ticker/current pointer | varies | `CONDITIONALLY_SAFE` | Safe for clearly current display; unsafe if presented as historical reconstruction. **VERIFIED** |

## Implicit Context Fallback Register

| Caller | Expected artifact | Fallback artifact / selection | Run compatible | Session/cutoff compatible | Version/config compatible | Provenance visible | Affects calculation | Assessment |
|---|---|---|---|---|---|---|---|---|
| `market_context_for_upload_run` | Pipeline-owned context | Highest context ID for upload run | Yes | Not necessarily original pipeline | Calendar fields exist, pipeline not checked | Context ID visible | Yes | **UNSAFE** |
| Legacy queued pipeline | Frozen enqueue context | Creates context on first execution | Yes | Execution-time, not enqueue-time | Current calendar/readiness code | Reason visible | Yes | **CONDITIONALLY_SAFE / LEGACY** |
| IB fetch/prewarm execution | Frozen serialized plan | Rebuilds plan at execution; prewarm ignores stored effective session/config equality | Varies | Current planner session | Current settings | Recorded metadata not enforced | Yes | **UNSAFE — PIPE-005** |
| Sector rotation | Same-run market snapshot | Latest global snapshot at/before date | No | Date-bounded only | Not fully proven | No fallback flag | Yes | **UNSAFE — CORE-006** |
| Standalone sector prior | Standalone-owned prior | Latest earlier matching-mode/config snapshot, including run-owned | No | Earlier date | Mode/config match | Source ID visible | Yes | **UNSAFE — CORE-007** |
| Ranking | Run-owned liquidity | Latest global ticker LIQUIDITY before `processed_at` | No | Coarse timestamp/date bound | No config requirement | Feature identity omitted | Yes | **UNSAFE — RANK-003** |
| Ranking earnings risk | Decision date | Current Europe/Zurich date | N/A | No historical anchor | Current thresholds | Days/risk stored, anchor absent | Yes | **UNSAFE — RANK-007** |
| Setup direct evaluate | Explicit pipeline context/cutoff | Newest upload-run context, else run processed/uploaded timestamp | Run yes | Pipeline/cutoff not proved | Current Setup config | Partial | Yes | **UNSAFE — SETUP-005** |
| Setup source loader | Same-run market/sector | Eligible global snapshot when owned one missing | No | Cutoff-filtered but not owned | Partial config checks | Warning/IDs partial | Yes | **UNSAFE — SETUP-005** |
| Setup history | Exact prior transition chain | Current canonical snapshots / active episode | Cross-run possible | Earlier-row logic incomplete; active row can be newer | Config not uniformly matched | Partial | Yes | **UNSAFE — SETUP-004/009** |
| Setup alert rebuild | Original rule revision | Current mutable alert rule | N/A | Event date used; cooldown lacks upper bound | Current rule config | Rule ID only | Yes | **UNSAFE — SETUP-007** |
| CERI provider selection | Configured conflict resolver | Effective-time/row-ID selection | Company/run scoped varies | PIT cutoff generally | Priority config not applied | Selected source IDs retained | Yes | **UNSAFE — CERI-001** |
| CERI surprise | Local knowledge/possession time | Provider observation/source time before retrieval | N/A | Can predate possession | N/A | Source timestamps retained | Yes | **UNSAFE — CERI-006** |
| CERI price response public call | PIT reconstructed OHLCV | Current PriceBar or injected lists when cutoff omitted | Global | No | Reader/version not enforced | Weak | Yes | **UNSAFE — CERI-002/003** |
| CERI alert evaluation | Rule matching config version | Existing DB rule by ID | N/A | Current rule | May be stale vs YAML | Rule ID visible, drift not | Yes | **UNSAFE — CERI-008** |
| Winner capture | Parent decision context | `datetime.now(UTC)` and latest same-run input/manifest | Same run | Current capture time | Current Winner config | Time/config stored; parent mismatch hidden | Yes | **UNSAFE — WIN-001/002** |
| Winner serving without V2 | Active generation-bound estimate | Latest non-generation estimate by cutoff/creation/ID | Prediction scoped | Cutoff ordered | Current compatibility incomplete | Estimate row visible | Yes | **LEGACY** |
| IB feature rebuild | Sources eligible at feature cutoff | All metric rows, latest live snapshot/availability, latest 20 PriceBars | Global | No constituent upper bound | Feature config bound; sources not | Source hashes only | Yes | **UNSAFE — XINT-002** |
| IB feature freshness | Freshness at declared cutoff | `datetime.now(UTC)` for live-snapshot age | N/A | Execution time, not supplied historical cutoff | Config bound | Status stored | Yes | **UNSAFE for historical rebuild** |
| IB dashboard/histogram | Session-specific feature/snapshot | Latest global by ticker/module/time | No | Current-display semantics | Mixed configs can be shown | IDs/metadata visible | Display mostly | **CONDITIONALLY_SAFE** |

## Wall-Clock Dependency Register

Operational timestamps (`created_at`, lease expiry, retry scheduling, log time) are legitimate and are not findings. The rows below are business/calculation dependencies. **VERIFIED**

| Caller | Clock expression / derived value | Purpose | Historical effect | Assessment |
|---|---|---|---|---|
| Ranking profile service | Europe/Zurich `date.today()` | Earnings-distance risk | Re-ranking the same run on another day changes score/decision | **UNSAFE — RANK-007** |
| Winner capture/backfill | `datetime.now(UTC)` | `decision_at`, then decision session/cutoff | Backfilled historical predictions acquire current decision time | **UNSAFE — WIN-001** |
| Setup direct/maintenance paths | current time or latest completed session when explicit context is absent | Evaluation/cutoff recovery | Repair/replay can use today's context | **UNSAFE — SETUP-005/006/010** |
| Legacy pipeline first execution | current time when context is created | Frozen calculation context | Queue delay changes the business-time envelope; visible but not enqueue-frozen | **CONDITIONALLY_SAFE / LEGACY — PIPE-003** |
| IB feature `_snapshot_availability` | `datetime.now(UTC)` | Live-snapshot age/freshness | Historical rebuild freshness changes with execution day | **UNSAFE — XINT-002/003** |
| IB admin request key | `date.today()` | Same-day job coalescing key | Operational scheduling only; completed jobs do not permanently suppress later requests | **CONDITIONALLY_SAFE** |
| Winner/Setup/IB job records | current UTC | lease, retry, creation/update time | Operational only when not reused as decision time | **SAFE** |
| Price/outcome ingestion | observation/creation current UTC | possession/audit time | Legitimate later-truth timestamp if kept separate from effective session | **SAFE** |

Repository searches also found standard timestamp defaults and scheduler clocks. Those are **NOT_APPLICABLE** unless a listed fallback promotes them into decision time. **VERIFIED**

## Historical Query Upper-Bound Register

| Query/consumer | Lower/order rule | Required upper bound | Present? | Risk/status |
|---|---|---|---|---|
| Authoritative PriceBar PIT reader | session range + revisions by recorded cutoff | revision/effective evidence `<= cutoff` | Yes | **SAFE / VERIFIED** |
| Sector prior snapshot | latest earlier date, mode/config compatible | `< current as-of` | Yes | Earlier date enforced; run mixing remains CORE-007. **PARTIALLY_VERIFIED** |
| Setup alert cooldown | latest prior alert in cooldown window | `alert business time <= transition/evaluation time` | No | A later alert can suppress replayed older alert. **UNSAFE — SETUP-007** |
| Setup active episode/repair | active/latest episode by ticker/family | state/evaluation `<= target session/cutoff` | No complete fence | Older repair can observe/overwrite newer state. **UNSAFE — SETUP-002/004** |
| CERI change rebuild | event/change rows for run/ticker | event knowledge/effective time `<= forwarded cutoff` | No | Future catalysts/guidance can enter older rebuild. **UNSAFE — CERI-004** |
| CERI surprise possession fallback | provider/source time | local possession `<= cutoff` | Not reliably | Provider time can stand in for knowledge time. **UNSAFE — CERI-006** |
| IB historical metric bars | all rows ordered by session | bar session/observed time `<= feature as-of/cutoff` | No | Future bars enter historical feature. **UNSAFE — XINT-002** |
| IB live/shortable/availability sources | latest or latest 30 | observed/effective time `<= feature cutoff` | No | Newer current evidence enters older feature. **UNSAFE — XINT-002** |
| IB dollar-volume PriceBars | latest 20 current rows | PIT session/revision `<= feature cutoff` | No | Mutable/current price state enters historical feature. **UNSAFE — XINT-002** |
| Winner input lookup | same run and time filters where present | input timestamp/session `<= decision cutoff` | Partial | Handoff/context compatibility is recorded, not validated. **UNSAFE — WIN-002/005** |
| Winner maturation | due horizon and current PriceBar truth | outcome session eligibility | Yes for horizon selection | Current corrected truth is intentional, but exact bar revision IDs are absent. **PARTIALLY_VERIFIED — WIN-006** |
| Trade-journal research | completed source run before entry | source completion/session `<= entry` | Yes | Ambiguity is surfaced; upstream provenance is still partial. **CONDITIONALLY_SAFE** |

## Mutable Projection Register

| Projection | Classification | Immutable/version history | Historical readers | Repair/replay behavior | Reconstruction assessment |
|---|---|---|---|---|---|
| `PriceBar` current row | `MUTABLE_CURRENT` | `PriceBarRevision` exists | CERI optional path, IB dollar volume, Winner outcomes | Provider refresh updates current and revisions | Decision history reconstructable only through PIT reader; bypasses are unsafe. **PARTIALLY_VERIFIED** |
| IB historical metric bar | `MUTABLE_CURRENT` | Revision rows exist | IB feature rebuild reads current | Refresh updates row and appends revision | Feature stores source hashes but source-time query is unbounded; exact old row can be difficult to recover from run reassignment. **UNSAFE** |
| IB live snapshots | `VERSIONED` content snapshots; latest is a projection | Content-addressed rows | IB feature rebuild/dashboard | New snapshots append | History exists, but historical builder selects latest without cutoff. **UNSAFE consumer** |
| `RankingResult` | `MUTABLE_CURRENT` upsert | No immutable generations | Setup captures score/decision/profile as metadata; Winner independently consumes Ranking-derived cohort features; current APIs also read it | Refresh updates/deletes profile rows | Historical Ranking meaning is not recoverable from the row alone; in Setup this is a metadata/provenance inconsistency, not a lifecycle actionability edge. **UNSAFE — RANK-006** |
| Combined/Technical/Fundamental run results | `DELETE_RECREATE` or mutable run result | Debug/input metadata varies | TechnicalScore directly feeds Setup/Lifecycle; Combined score/decision are Setup metadata while Combined `earnings_risk` affects actionability; Ranking and Winner have independent reads | Recalculation replaces run-owned meaning | Exact historical component row/config not uniformly recoverable. **UNSAFE** |
| Setup canonical signal | `MUTABLE_CURRENT` selection/denormalization over snapshots | Snapshots exist but can be recanonicalized | lifecycle/current APIs | Repair/replay can change canonical state | Original selection is not immutable across runs/configs. **UNSAFE — SETUP-004/009** |
| Active LifecycleEpisode | `MUTABLE_CURRENT` | Versioned evaluations/events partially preserve transitions | Setup/Lifecycle services maintain it; Winner capture does not consume it | Older repair can mutate active row | Current state can lose chronology; exact prior chain not guaranteed. **UNSAFE — SETUP-002** |
| Setup/CERI rule rows | `MUTABLE_CURRENT` | Rule ID, limited/no full revision history | alert evaluation/rebuild | Current rule reused | Historical rule semantics not fully reconstructable. **UNSAFE — SETUP-007/CERI-008** |
| Market/sector current pointer | `DERIVED_POINTER` over versioned snapshots | Versioned snapshots | Setup/display; sector itself | Recompute appends/selects | Safer when source ID/context validated; global fallback is unsafe. **PARTIALLY_VERIFIED** |
| CERI snapshots/features | `VERSIONED`/append-like | Source/evidence hashes and context fields | CERI-internal change/alert processing and display APIs; not Setup/Lifecycle or Winner | Purge can remove/change inputs | Purge can stale surviving snapshot hashes. **PARTIALLY_VERIFIED — CERI-010** |
| Winner prediction feature vector | `IMMUTABLE` decision values | Stored JSON plus evidence manifests | rescore/cohort | Rescore creates generation-bound estimates | Strong positive reference; does not reread upstream state. **VERIFIED** |
| Winner outcome | `VERSIONED` later truth | Outcome revisions | cohorts/metrics | Rematuration may append revision/update current outcome | Decision vector remains frozen; evidence lacks exact bar revision IDs. **PARTIALLY_VERIFIED** |
| Winner active generation | `DERIVED_POINTER` | Immutable/versioned generations and manifests | serving | publish switches pointer | Atomic/complete, but not monotonic or duplicate-idempotent. **UNSAFE — WIN-007/009** |

## Readiness Propagation Matrix

| Producer → consumer | Producer readiness/confidence | Numeric remains populated? | Consumer checks readiness | Checks confidence | Numeric/actionability effect | Assessment |
|---|---|---:|---:|---:|---|---|
| Technical → Combined | `insufficient_data`, `technical_confidence` | Yes | No | No | Numeric components become Combined score/label | **VIOLATED — CORE-009/RANK-001** |
| Technical → Ranking | same | Yes | No | No | Normalization, numeric rank, decision population include row | **VIOLATED — RANK-001/002** |
| Technical → Setup/Lifecycle directly | `insufficient_data`, `technical_confidence` | Yes | No adequate insufficiency gate | No adequate confidence gate | Setup/Lifecycle directly consumes retained TechnicalScore numerics; they can drive lifecycle state and alerts | **VIOLATED — SETUP-003** |
| Technical → Winner | frozen technical-derived features | Yes | Explicit insufficiency gate exists | Partial | Known insufficiency is blocked, but low confidence/required-feature policy is incomplete | **PARTIALLY_ENFORCED — WIN-004** |
| Combined → Setup | `final_score`, decision label, `earnings_risk`, config/input identity | Yes | No complete upstream compatibility check | N/A | `final_score` and decision label are denormalized metadata only; `earnings_risk` participates in Setup actionability without a complete upstream compatibility contract | **VIOLATED provenance/actionability compatibility — SETUP-008** |
| Ranking → Setup | `profile_score`, decision label/profile, `is_complete`, warnings | Yes | Provenance/readiness checks are incomplete | No | Score/decision/profile are denormalized metadata/provenance only and do not directly drive lifecycle state or actionability; risk is provenance/display inconsistency | **VIOLATED provenance contract — SETUP-008** |
| Ranking → Winner | warnings/completion/profile identity | Yes | No | No | Rank/score-derived Winner features can inherit invalid upstream state | **VIOLATED — WIN-003** |
| Market regime → Setup | confidence/warnings/staleness | Policy numerics remain bullish under severe staleness | Partial | Not decisive | Gates/sizing can remain actionable despite Gray display | **VIOLATED — CORE-002 propagation** |
| Market regime → Winner | snapshot policy values embedded | Yes | No full confidence/policy coherence validation | No | Probability feature vector inherits sparse/stale defects | **VIOLATED — WIN-005** |
| Sector rotation → Setup; Sector rotation → Winner independently | confidence/warnings/source identity | Yes | Partial/No | Partial/No | Each consumer can inherit global/prior mixing; Setup does not pass it to Winner | **VIOLATED — CORE-006/007 propagation** |
| IB liquidity → Ranking | coverage, confidence, freshness | Yes | Coverage only | No | Tradeability penalty uses low/stale feature | **VIOLATED — XINT-004/RANK-003** |
| IB volatility/short pressure → CERI | coverage, confidence, freshness | Yes | Coverage only | No | CERI features can use temporally contaminated/low-confidence IB input | **VIOLATED — XINT-002/004** |
| Setup signal → Lifecycle | signal confidence/readiness inherited incompletely | Yes | Partial | Partial | Actionable transition may originate in insufficient technical | **VIOLATED — SETUP-003** |
| Winner prediction → cohort | completeness/eligibility/exclusions | Probability stored | Cohort contract filters | Contract-specific | Published model metrics depend on recorded exclusion policy | **PARTIALLY_ENFORCED** |

## Entry-Point Contract Matrix

Legend: `R` required, `D` derived/defaulted, `—` absent/not applicable. The envelope columns are run, pipeline/context, session, cutoff, calendar, config, calculation version, generation.

| Subsystem / entry point | Run | Pipe/context | Session | Cutoff | Calendar | Config | Version | Generation | Assessment |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Durable full pipeline | R | R | R | R | R | R/partial per artifact | R | — | Strongest path; CERI async boundary remains. **PARTIALLY_ENFORCED** |
| Legacy synchronous upload pipeline | R | D | D | D | D | current | current | — | Weaker/non-durable. **LEGACY — PIPE-003** |
| Standalone technical/ranking services | R | D/— | D | D | partial | current | current | — | Run alone accepted. **UNSAFE** |
| Standalone regime/sector | R/— | D/— | D/R | D | partial | R/partial | R | — | Global/prior fallbacks differ from pipeline. **UNSAFE** |
| CERI canonical pipeline/capture | R | R | R | R | R | R | R | — | Strong, except provider completion and named selector defects. **PARTIALLY_ENFORCED** |
| CERI direct ingest/admin/backfill | R/D | D/— | D | D | partial | current | current | — | Stable idempotency/current defaults can suppress refresh or weaken PIT. **UNSAFE** |
| CERI change/alert rebuild/purge | R/target | partial | D | forwarded/partial | — | current/DB | current | — | Not original reconstruction; can mutate historical meaning. **UNSAFE** |
| Setup canonical pipeline | R | R | R | R | R | Setup version + handoff | R | — | Strong when transition preflight is validated. **PARTIALLY_ENFORCED** |
| Setup stage invocation on the CERI-resume orchestration branch | R | missing | D | missing | — | current | current | — | The pipeline passes an incomplete Setup invocation envelope on this stage-order branch; Setup does not consume CERI data. **UNSAFE — PIPE-002/SETUP-001** |
| Setup direct/repair/replay/maintenance | R/D | D/— | R/D | D/— | partial | current | current | — | Current/latest state can replace original context. **UNSAFE** |
| Winner pipeline capture | R | recorded, not validated | D | D | partial | R | R | — | Frozen output, weak acquisition identity. **UNSAFE** |
| Winner admin capture/backfill | R/target | —/D | D | D | partial | current | current | — | Wall-clock historical decision identity. **UNSAFE** |
| Winner maturation/revision | prediction IDs/dynamic due set | via frozen prediction | outcome session | current truth cutoff | market sessions | policy | R | outcome revision | Correct decision/truth separation; target/evidence gaps. **PARTIALLY_ENFORCED** |
| Winner cohort/rescore/publication | target IDs/watermark | frozen manifests | bounded | bounded | contract | R | R | R | Strong generations; publication monotonicity/idempotency gaps. **PARTIALLY_ENFORCED** |
| IB feature rebuild (job/API) | intelligence run | — | R | R | R | R | R | — | Declared envelope is persisted but constituent reads ignore time; lease guard absent. **UNSAFE** |
| IB fetch/prewarm | upload/run | replanned | D | D | current | current | current | — | Stored plan is not enforced. **UNSAFE — PIPE-005** |
| Maintenance/debug/scripts | varies | usually — | parameter/current | parameter/current | varies | current | current | — | Must be treated individually; mutating examples are in the writer register. **PARTIALLY_VERIFIED** |

## Fingerprint and Manifest Registry

`RECORDED` means stored; `VALIDATED` means a consumer compares it; `ENFORCED` means mismatch blocks use/write; `AUTHORITATIVE` means the complete intended identity is covered. These are independent properties. **VERIFIED**

| Fingerprint/manifest | Creator; algorithm/canonicalization | Exact inputs | Storage / consumers | Binding | What it proves | What it does not prove | Status |
|---|---|---|---|---|---|---|---|
| Canonical evidence hash | `CanonicalEvidenceSerializer`; SHA-256 over deterministic JSON, UTC microseconds, normalized decimal, sorted maps/reason sets | Supplied evidence object | CERI, market/sector, Winner, IB feature evidence | Only fields supplied by creator | Byte-semantic equality of the canonical payload | Completeness, truth, cutoff eligibility, or config compatibility outside payload | `RECORDED`; selectively `VALIDATED`; **not globally AUTHORITATIVE** |
| Market context fingerprint | market context service; canonical SHA-256 | persisted cutoff/session/calendar/bar-readiness semantic fields | `MarketCalculationContext`; pipeline validators | Pipeline/context/session/calendar | Exact equality of the frozen context payload | Compatibility of every downstream artifact unless it stores/validates context ID | `RECORDED+VALIDATED+ENFORCED+AUTHORITATIVE` for context itself |
| PriceBar change hash | price persistence; MD5 over normalized bar fields | current OHLCV/adjustment/source values | `PriceBar`, `PriceBarRevision`; revision writer/PIT reader | ticker/session/revision | Whether bar content changed and which revision reconstructs cutoff state | Calculation config or consumer cutoff when reader is bypassed | `RECORDED+VALIDATED+ENFORCED` for revision creation; not decision-authoritative alone |
| Price-series version | price/PIT service; canonical series digest | eligible ordered bars/revisions | Technical context/cache | session/cutoff/source series | Exact technical price series identity | Scoring policy or non-price inputs | `RECORDED+VALIDATED` on canonical technical path |
| Technical artifact cache key/signature | technical cache; canonical hashes | ticker/timeframe, adjusted/trades series version, feature config, as-of, engine/schema | cache rows; technical engine | session/series/config/schema, deliberately no run | Feature-equivalent reuse | Scoring config, full run context, or readiness of downstream score | `RECORDED+VALIDATED+ENFORCED`; authoritative only for cached feature artifact |
| Fundamental debug/config hash | fundamental service | model/config values in debug payload | FundamentalScore/debug consumers | run/model partial | Which reported model settings were used | Input row/effective-time/currency identity or immutable config object | `RECORDED`, weak validation |
| Ranking profile identity | ranking config loader | profile name and individual weights at runtime | profile name/selected fields in result | run/profile name only | Named profile used | Exact full YAML, hash, version, normalization population | **MISSING authoritative fingerprint — RANK-005** |
| CERI config/evidence/processor signatures | CERI engine/services; canonical hashes/version strings | varies: feature inputs, rules/provider evidence, processor/config subset | snapshots/features/alerts/manifests | strong run/context/session on modern rows | Equality of included evidence/config/processor subset | Provider priority actually applied; omitted evidence; validity after destructive purge | `RECORDED`; often `VALIDATED`; **PARTIAL** |
| Setup source/config/anchor fingerprints | Setup engine/transition preflight; canonical SHA-256 | source snapshot IDs/values, config, canonical anchor/transition envelope | setup snapshots/preflight/handoff | strong on canonical path | Exact selected source/transition input on that path | Alternate-path equivalence or immutability of referenced mutable rules/current rows | `RECORDED+VALIDATED+ENFORCED` in preflight; partial elsewhere |
| Decision Handoff Manifest | parent pipeline | stage artifact IDs/counts, context/cutoff/session/config metadata | manifest row; Setup and Winner consume it independently | same run/pipeline/context | Declared completed handoff inventory | CERI descendant terminality; Winner input compatibility unless validated | Setup: `ENFORCED`; Winner: `RECORDED` only — WIN-002 |
| Winner feature/evidence hash | Winner capture; canonical SHA-256 | frozen feature JSON and evidence manifest payload | prediction snapshot | prediction/run/config/schema | Immutable equality of decision vector/evidence payload | That selected upstream evidence was temporally valid | `RECORDED+VALIDATED+ENFORCED` after capture; selection remains partial |
| Winner generation key/content hash | cohort/generation service; deterministic key/canonical hash | contract/model/config, watermark/target manifest, estimates/statistics | generation/manifests/publication | generation/model/watermark | Reproducible generation content/identity | Publication monotonicity or duplicate request idempotency | `RECORDED+VALIDATED+ENFORCED`; pointer remains unsafe |
| IB historical metric hash/revision | IB repository; canonical content hash | metric bar values/source/time | current row + revisions | ticker/session/metric, run ownership mutable | Content change history | Historical consumer cutoff; stable old run ownership | `RECORDED+ENFORCED` on changes; not query-authoritative |
| IB feature input/evidence hash | IB repository/calculator; SHA-256 canonical payload | module components, evidence hashes, output classification/score/confidence/freshness/warnings | feature row/dedup | ticker/as-of/module/config/calc; no run/cutoff in reuse identity | Same included constituent/output payload | That constituents were eligible at declared as-of/cutoff | `RECORDED+VALIDATED` for dedup; **not authoritative — XINT-002** |
| Background execution token | job worker | job ID + current attempt token/lease state | job row/worker finalizer/progress | job attempt | Worker still owns job row at guarded point | Safety of earlier internal domain commits | `ENFORCED` at guarded commits only — PIPE-009/XINT-009 |

## Important Configuration Registry

| Configuration | Source and declared value/default | Effective resolution/gate | Runtime override | Persisted/hash with result | Historical reconstruction | Assessment |
|---|---|---|---|---|---|---|
| Durable pipeline | settings/env; enabled by default | Chooses durable vs legacy upload path | Env | Job/pipeline metadata, not one full global hash | Path inferable | **PARTIALLY_VERIFIED** |
| Technical v4/v5 | YAML + settings; model versions `4.0.0`/`5.0` | engine/version selection and cache mode | Settings/API path | Feature config/engine/schema hashes on modern path | Good for technical artifact; scoring config excluded from cache by design | **PARTIALLY_ENFORCED** |
| Technical cache/series maintenance | env/settings; default off | Cache read/write/shadow and maintenance jobs | Env | Cache signature/status | Reuse policy inferable from deployment settings, not result alone | **PARTIAL** |
| Fundamental model | code/config, `fundamentals_v2.1` | Current service model | Code/settings | Debug/config subset | Effective-time/input identity absent | **NOT FULLY RECONSTRUCTABLE** |
| Market regime | YAML/model `mrcc-1.0` | service config | File/deploy | version/date, not full config hash | Exact policy not guaranteed | **NOT ENFORCED — CORE-008** |
| Sector rotation | YAML/model `1.0` | service config | File/deploy | full hash/version | Mostly reconstructable | **ENFORCED on snapshot production; fallback still unsafe** |
| Ranking profiles | YAML profiles | profile name selects weights/gates | File deployment | Profile name/partial fields, no full hash/version | No | **NOT ENFORCED — RANK-005** |
| CERI | YAML `ceri-1.3.0`, config ID/date + settings feature flags + DB rules | settings control jobs/providers; YAML algorithms; DB rules may outlive YAML | Env/YAML/DB | substantial but incomplete hashes/version | Partial; effective priority/rule drift remains | **PARTIALLY_ENFORCED** |
| Setup/Lifecycle | YAML `slse-1.3.0` + settings + mutable DB alert rules | canonical pipeline flags and direct endpoints | Env/YAML/DB/API | setup config/source hashes on snapshots; rule revision incomplete | Canonical snapshot stronger than alert replay | **PARTIAL** |
| Winner | settings flags + YAML/engine, calc `1.1`, schema `1.0` | `WINNER_PROBABILITY_ENABLED`/`engine.enabled` do not serve as coherent master gate; subfeature flags independently expose mutation paths | Env/admin/API | vector/config/model/generation contracts are strong | Generation work reconstructable; capture enablement context not one authority | **CONTRADICTORY — WIN-010** |
| IB market intelligence | settings master/module flags + YAML `ibmi-1.0` sections | settings global + per-module + section; YAML `engine.enabled` is not the master gate | Env/YAML/API | feature config/calc/source versions and hash | Feature policy yes; constituent temporal eligibility no | **CONTRADICTORY master-switch semantics** |
| Calendar/session policy | market clock/settings/calendar version | authoritative context on canonical path | deployment/calendar data | Context fingerprint/version | Yes when context ID retained | **ENFORCED on canonical context; missing elsewhere** |

The settings named like master switches are especially hazardous because code paths can be reachable under subordinate/admin flags even when the nominal engine/global flag is false. This is a configuration-authority defect, not merely a documentation issue. **VERIFIED**

## Retry Identity Versus Refresh Identity

| Key/workflow | Identity actually represented | Intended scope | Distinct refresh collapsed? | Assessment |
|---|---|---|---|---|
| Generic background execution token | One lease/attempt | Retry ownership | No; token changes on reclaim | **SAFE for job row** |
| Pipeline stage/job idempotency keys | Same pipeline/stage/business target | Retry of same stage | Usually no | **CONDITIONALLY_SAFE** |
| CERI direct ingestion stable key | Company/provider/source/business identity | Implemented as idempotent business request | Yes, a later legitimate provider refresh can look identical | **UNSAFE — CERI-012** |
| CERI provider child request keys | Provider/company/capture scope | Same scheduled acquisition | Can collapse a new cycle when generation/refresh identity is absent | **PARTIALLY_VERIFIED** |
| IB admin request key | Payload + effective current date/config | Same-day request | Active duplicate coalesced; completed later request can proceed | **CONDITIONALLY_SAFE** |
| Winner maturation root | Request/root job identity | One maturation pass | Competing roots can be created by repeated triggers | Does not suppress refresh; concurrency/target overlap risk remains. **PARTIALLY_VERIFIED** |
| Winner generation key | Immutable calculation generation content/contract | Same generation | Intentional dedup; new watermark/model/config changes key | **SAFE** |
| Winner publication request | Requested generation transition | Idempotent pointer operation | Duplicate same request can fail/non-idempotently repeat | **UNSAFE — WIN-009** |
| Technical content cache | Same feature calculation, deliberately cross-run | Calculation-equivalent reuse | No if full feature identity matches; scoring changes intentionally outside cache | **SAFE within documented feature boundary** |

## Dynamic Target-Set Background Workflows

| Workflow | Root target set / manifest | Continuation behavior | Can new rows enter? | Progress/termination | Assessment |
|---|---|---|---|---|---|
| Winner maturation | Due rows queried; no complete frozen target-ID manifest | Continuations re-query due population | Yes | Zero-progress/termination protections are incomplete for changing due set | **UNSAFE — WIN-008** |
| Winner rescore | Explicit prediction IDs/generation contract | Carries frozen target set/watermark | No | Bounded | **SAFE** |
| Winner cohort build | Watermark/target manifest/generation | Generation-bound continuation | Not after manifest freeze | Bounded build state | **SAFE; publication separately unsafe** |
| CERI batched company work | Child payload contains chunk/company IDs | Inherits chunk | Normally no | Bounded retries | **CONDITIONALLY_SAFE** |
| CERI all-company backfill | Current company universe selected for invocation; no durable full root manifest | Later invocation/redelivery may rediscover population | Possible | Per-job progress exists | **PARTIALLY_VERIFIED** |
| IB historical/live/feature jobs | Tickers/date ranges/checkpoint in payload | Checkpoint advances through submitted scope | No material evidence of scope expansion | Bounded by payload | **CONDITIONALLY_SAFE** |
| Setup repair/replay | Query-defined target/session range | No formal immutable target manifest | Database changes can alter selection | Bounded loop, semantics not original reconstruction | **PARTIALLY_VERIFIED** |

## Publication and Pointer Monotonicity

| Pointer/update | Atomic | Complete-before-switch | Lease/attempt fenced | Idempotent | Monotonic | Assessment |
|---|---:|---:|---:|---:|---:|---|
| Winner active generation | Yes | Yes, validates generation/build artifacts | Background handler guarded | No for duplicate request | No generation/evidence ordering fence | **VIOLATED — WIN-007/009** |
| Setup canonical snapshot selection | Transactional row updates vary | Snapshot exists | Usually outer job fenced | Re-evaluation can recanonicalize | No immutable monotonic contract | **VIOLATED — SETUP-004/009** |
| Active lifecycle episode/current state | Transactional | Current episode exists | Outer handler only | Repair can repeat | Older repair can overwrite newer | **VIOLATED — SETUP-002** |
| Market/sector latest selectors | Read-time derived pointer | Snapshot complete | N/A | N/A | Ordered by date/ID, but cross-run/global allowed | **PARTIALLY_ENFORCED** |
| PriceBar current revision | Transactional current+revision write | Revision ledger created on change | Depends on writer | Content idempotency | Revision ordering exists | **PARTIALLY_ENFORCED; PIT reader required** |

## Historical Reconstruction Semantics

| Operation | Actual semantic contract | Uses original session/cutoff/config? | Name accuracy | Assessment |
|---|---|---|---|---|
| PriceBar PIT reconstruction | `ORIGINAL_CONTEXT_RECONSTRUCTION` | Yes, with explicit cutoff | Accurate | **ENFORCED** |
| Pipeline retry/resume | `OPERATIONAL_RETRY` against stored pipeline context | Usually; the branch that schedules Setup when resuming from the CERI stage omits required Setup fields, despite no CERI → Setup data dependency | Accurate but incomplete | **PARTIAL** |
| CERI replay/rebuild | Mix of `CURRENT_RULES_RETROSPECTIVE` and `CURRENT_STATE_REPAIR` | Not uniformly; current DB rules/unbounded events possible | “Replay” overstates original reconstruction | **UNSAFE** |
| CERI provider backfill | `DATA_ACQUISITION` | Target dates vary; idempotency can suppress new cycle | Mostly accurate | **PARTIAL** |
| Setup replay/repair | `CURRENT_STATE_REPAIR` / current-rules retrospective | No complete original cutoff/config/prior chain | “Replay” overstates semantics | **UNSAFE — SETUP-006** |
| Winner historical capture/backfill | `NEW_VERSION_RECALCULATION` at current decision time, not original capture | No | “Historical/backfill” can imply stronger semantics than provided | **UNSAFE — WIN-001** |
| Winner rescore | `NEW_VERSION_RECALCULATION` over frozen original vector | Yes for evidence; model/generation intentionally changes | Accurate | **SAFE** |
| Winner rematuration/outcome revision | `CURRENT_RULES_RETROSPECTIVE` over later corrected outcome truth | Decision vector stays frozen | Accurate if described as truth revision | **PARTIAL — WIN-006** |
| IB feature rebuild for old as-of | Intended retrospective calculation; actually `CURRENT_STATE_REPAIR` with latest sources | No | “as-of” overstates temporal semantics | **UNSAFE — XINT-002** |
| Technical cache rebuild | `NEW_VERSION_RECALCULATION`/cache maintenance | Explicit series/as-of/config key | Accurate | **SAFE within cache boundary** |

## Decision Evidence Versus Later Truth

| Subsystem | Decision-time evidence | Legitimate later truth | Can later truth mutate original evidence? | Assessment |
|---|---|---|---|---|
| Winner | Frozen `feature_json` + evidence manifests | Versioned horizon outcomes/current corrected PriceBars | Feature vector does not change; outcome evidence revision is separate | **STRONG POSITIVE REFERENCE; exact price revision IDs missing — WIN-006** |
| Price market data | PIT-reconstructed bar series | Current corrected `PriceBar` + revisions | Only if a consumer bypasses PIT | **PARTIALLY_ENFORCED** |
| CERI price response | CERI decision feature/snapshot | Later bar/provider corrections | Optional/current reader paths and purge can alter/reinterpret evidence | **VIOLATED — CERI-002/003/010** |
| Setup/Lifecycle | Setup snapshot and prior transition | Later repair/replay/canonical changes | Mutable active/canonical state can change historical interpretation | **VIOLATED — SETUP-002/004/006/009** |
| Ranking | Current run/profile result | Later upstream recalculation/config | Mutable upsert means no frozen original row generation | **VIOLATED — RANK-005/006** |
| IB feature | Stored values/hashes labeled as-of | Later provider metric/live/PriceBar truth | Existing feature is append-like, but rebuild may use latest sources without original temporal boundary | **VIOLATED — XINT-002** |

## Current Row Versus Immutable History

| Important artifact/table family | Classification | Historical calculation reading non-immutable state | Risk/status |
|---|---|---|---|
| Upload/raw company rows | `IMMUTABLE`/run-owned input | Fundamental/Combined/Ranking | Stable owner, but business effective-time/currency identity weak. **PARTIAL** |
| FundamentalScore | `DELETE_RECREATE` | Combined, Ranking, Setup, and Winner through independent reads | Recalculation can change old run meaning. **UNSAFE** |
| TechnicalScore / CombinedResult | `DELETE_RECREATE` or mutable run result | Setup directly consumes TechnicalScore; Combined score/decision are Setup metadata, while Combined `earnings_risk` affects Setup actionability; Ranking and Winner read independently | No immutable generation identity. **UNSAFE** |
| RankingResult | `MUTABLE_CURRENT` | Setup captures score/decision/profile as metadata only; Winner independently consumes Ranking-derived cohort features | Setup risk is provenance/display inconsistency, while Winner has an independent behavioral feature edge. **UNSAFE** |
| MarketRegimeSnapshot / SectorRotationSnapshot | `VERSIONED`; latest is `DERIVED_POINTER` | Setup and Winner independently, plus global fallbacks | Snapshot history is strong; selection compatibility is weak. **PARTIAL** |
| PriceBar / PriceBarRevision | `MUTABLE_CURRENT` + `VERSIONED` history | CERI/IB/Outcome | Safe only through PIT reconstruction for decision features. **PARTIAL** |
| CERI feature/snapshot/change/alert | Mostly `VERSIONED`/`APPEND_ONLY`; rule rows mutable; purge destructive | CERI-internal rebuild/alert paths and display APIs; not Setup/Lifecycle or Winner | Strong identity undermined by optional readers/rules/purge. **PARTIAL** |
| Setup snapshot | `APPEND_ONLY` with mutable canonical projection | lifecycle/current selection | Original canonical choice not frozen. **UNSAFE** |
| Lifecycle episode/evaluation/event | Episode `MUTABLE_CURRENT`; evaluations/events `VERSIONED` | repair/replay/current state | Future/older overwrite semantics unsafe. **UNSAFE** |
| Winner prediction/vector | Decision values `IMMUTABLE`; operational fields mutable | rescore/cohort | Original vector recoverable. **SAFE** |
| Winner outcome/revision | `VERSIONED` truth with current projection | cohort/statistics | Legitimate revision, incomplete exact bar lineage. **PARTIAL** |
| Winner generation/manifest | `VERSIONED`/immutable build | serving through active pointer | Content strong; pointer non-monotonic. **PARTIAL** |
| IB metric bar/revisions | `MUTABLE_CURRENT` + `VERSIONED` history | IB feature rebuild | Unbounded current-row reads. **UNSAFE** |
| IB feature/snapshot | `VERSIONED`/content deduplicated | Ranking/CERI/dashboard | Stored values stable; constituent eligibility can be false. **PARTIAL** |
| Alert/config rule tables | `MUTABLE_CURRENT` | historical alert rebuild | Exact old policy not recoverable. **UNSAFE** |

## Hidden Path Register

| Subsystem | Non-canonical path | Mutation/read effect | Contract difference | Status |
|---|---|---|---|---|
| Upload/fundamental | Pre-pipeline score calculation during upload | Writes FundamentalScore before frozen pipeline context | No pipeline/session/cutoff identity | **VERIFIED** |
| Pipeline | Legacy synchronous processing | Runs same conceptual stages without durable stage/manifest guarantees | Context created at execution | **LEGACY — PIPE-003** |
| Market data | `IB_FETCH`, prewarm, direct refresh | Writes current PriceBars/revisions independently | Replans session/config; can race certification | **VERIFIED — PIPE-005/008** |
| Technical | Standalone service/cache maintenance | Recomputes/current run or cache artifacts | Context/config enforcement varies | **VERIFIED** |
| Regime/sector/ranking | Direct service/admin refresh | Upserts/snapshots using latest/global context | Bypasses parent handoff | **VERIFIED** |
| CERI | Direct ingest, admin capture, provider backfill, change/alert rebuild, replay, purge, SEC readiness | Appends/updates/deletes decision evidence/rules/alerts | PIT, context, refresh identity vary | **VERIFIED** |
| Setup/Lifecycle | Direct evaluate, resume, repair, replay, maintenance, alert rebuild | Creates snapshots/transitions or mutates active/canonical state | Original cutoff/pipeline/prior-state contract weaker | **VERIFIED** |
| Winner | Admin capture/backfill, maturation roots, revision, cohort refresh, rescore, publication | Writes predictions/outcomes/generations/pointers | Strong generation contract begins after weak capture boundary | **VERIFIED** |
| IB intelligence | Historical/live refresh, feature rebuild, scanner/flex/trade-journal jobs, dashboards | Writes current/revisioned/versioned IB artifacts | Own context model; feature reads ignore source upper bounds | **VERIFIED — XINT-002/009** |
| Ops script | `scripts/ops/winner_candidate_estimates.py` | Can write estimates only with explicit `write --approve-write`; actor/request hashes recorded | Outside normal API/job entrypoint | **CONDITIONALLY_SAFE** |
| Ops script | `scripts/ops/resolve_sec_ciks.py` | Writes CIK resolutions unless `--dry-run` | No certification envelope; acquisition metadata operation | **CONDITIONALLY_SAFE**, operator-controlled |
| Profiling/forensics | SEC performance profiler and disposable run forensics | May enqueue/cancel jobs or mutate disposable DB | Operational/test purpose, not production calculation contract | **PARTIALLY_VERIFIED** |

## Snapshot Compatibility Register

| Consumer | Snapshot/artifact | Run | Pipeline/context | Session/cutoff | Config/version/revision | Classification |
|---|---|---|---|---|---|---|
| Combined | Fundamental + Technical | Same run | Not validated | Not validated | Not validated | **UNSAFE** |
| Ranking | scores + IB liquidity | Scores same run; IB global | Not validated | IB coarse bound only | profile/IB compatibility not validated | **UNSAFE** |
| Sector rotation | Ranking + market regime + prior sector | same-run ranking; fallback/prior may cross run | Partial | date-based | sector config match; upstream profile/context incomplete | **UNSAFE** |
| CERI canonical capture | event/provider/price/IB features | Mostly run-owned; IB global | CERI context strong | PIT for canonical price; IB source internals unsafe | CERI version strong; IB compatibility partial | **PARTIAL** |
| Setup canonical | Direct TechnicalScore; Combined `earnings_risk` for actionability; Combined score/decision and Ranking score/decision/profile as metadata only; regime/sector; no CERI input | same run preferred | Handoff validation strong | cutoff/session stored | upstream configs not uniformly recoverable | **PARTIAL** |
| Setup alternate | Same behavioral/metadata split as canonical Setup; no CERI input | fallback/cross-run possible | missing/derived | latest/current | current config | **UNSAFE** |
| Winner capture | Ranking/technical/fundamental/regime/sector | same run | recorded not validated | wall-clock decision | frozen by value after selection | **UNSAFE acquisition; immutable result** |
| Winner rescore | frozen prediction + generation | prediction/generation exact | manifest bound | frozen | model/config/schema bound | **SAFE** |
| IB feature | metric/live/shortable/availability/PriceBar | intelligence run ownership mixed/global | no market context ID | declared as-of not enforced at sources | source hashes stored | **UNSAFE — XINT-002** |

## Cache Registry

| Cache | Key / invalidation | Scope | Provenance interaction | Assessment |
|---|---|---|---|---|
| Technical artifact cache | Content key over series version, as-of, feature config, engine/schema; READY + shadow-match reuse | Cross-run by design | Does not include scoring policy because it caches features, not final score | **SAFE within explicit boundary** |
| Technical series-version maintenance | Ordered eligible PriceBar series version | Ticker/timeframe/session | Disabled by default; no TTL dependence | **PARTIALLY_VERIFIED** |
| SEC document/content cache | CIK/accession/document/content hash; expected-body hash checked when supplied | Cross-run provider content | PostgreSQL remains derived identity authority | **CONDITIONALLY_SAFE** |
| In-process config/settings caches | Process lifetime/current deployment | Global | Historical artifacts need their own persisted config hash; several do not | **UNSAFE when treated as historical authority** |
| DB latest/current selectors | Not a true cache, but used as one | Global/ticker | Silent fallback frequently lacks source ID/config/cutoff | **UNSAFE systemic pattern** |

No evidence was found that a transient application cache, by itself, is treated as the authoritative persisted decision result. The dominant problem is database “latest/current” selection, not memory-cache staleness. **VERIFIED**

## Dangerous Writer Register

| Writer | Artifact/table | Entry point/job | Scope binding | Can mutate frozen/published meaning? | Guard | Assessment |
|---|---|---|---|---|---|---|
| Upload processing/fundamental service | FundamentalScore | upload/pre-pipeline/recalculate | run+ticker | Yes, delete/recreate old run score | transaction, no generation | **UNSAFE historical meaning** |
| Technical/Combined/Ranking services | their result rows | pipeline and standalone | run+ticker; context partial | Yes, replace/upsert | transaction/outer job | **UNSAFE** |
| Price persistence | PriceBar + revisions | fetch/prewarm/provider jobs | ticker/session/source | Current row yes; revisions retained | content hash/transaction, job guard varies | **PARTIAL** |
| Market/sector services | snapshots/current selections | pipeline/direct | run/date/config partial | Append snapshots; selectors can shift | transaction | **PARTIAL** |
| CERI ingestion/processors | provider evidence/features/snapshots | pipeline/direct/backfill | run/context on modern paths | Adds newer evidence; direct refresh identity can collapse | provider/job/idempotency guards | **PARTIAL** |
| CERI purge/admin | event evidence and dependent rows | admin/maintenance | target filters | Yes; surviving snapshot hash can become stale | explicit admin action/transaction | **UNSAFE — CERI-010** |
| CERI/Setup rule writers | mutable rule rows | seed/admin/migration | rule ID | Yes, historical alert semantics | transaction only | **UNSAFE** |
| Setup evaluation/repair/replay | snapshots/canonical/episodes/evaluations/events | pipeline/direct/jobs | run/session weak on alternates | Yes, older repair can rewrite newer current state | outer job; source hash partial | **UNSAFE** |
| Winner capture | prediction vector | pipeline/admin/backfill | run+ticker, current decision | Vector immutable after insert, but wrong historical identity can be frozen | unique/content constraints | **PARTIAL** |
| Winner maturation/revision | outcomes/revisions | auto/admin/jobs | prediction/horizon | Later truth changes intentionally | revisions/lease; target set dynamic | **PARTIAL** |
| Winner generation/publication | generation/manifests/active pointer | cohort/rescore/admin/auto | generation/contract | Pointer can activate older completed generation | transactional validation/lease | **UNSAFE pointer** |
| IB historical/live writers | metric current+revision, live snapshots | IB jobs/API | ticker/session/module/intelligence run | Current metric ownership/value changes | content hash/checkpoint heartbeat | **PARTIAL** |
| IB feature rebuild | IB feature rows | background rebuild/direct service | as-of/config/version; source bounds absent | Commits temporally invalid features; stale worker can commit | transaction commits inside handler, no execution-token check | **UNSAFE — XINT-002/009** |
| Candidate estimate ops script | Winner estimates | explicit CLI write mode | request/actor/hash | Writes model estimates outside service | two explicit write flags | **CONDITIONALLY_SAFE** |
| SEC CIK resolver script | company/source identity | CLI | company/lookup | Mutates acquisition identity | `--dry-run` optional | **CONDITIONALLY_SAFE** |

Raw SQL/ORM scans did not reveal a second shadow writer that bypasses all named service/script paths for the principal calculation tables. Migration data fixes are historical deployment writers and must still be considered when reconstructing pre-migration state. **PARTIALLY_VERIFIED**

## Domain Write Fencing Matrix

| Handler/writer | Transaction scope / internal commits | Lease/token check before domain commit | Duplicate-write guard | Can stale worker commit? | Assessment |
|---|---|---|---|---|---|
| Generic durable worker | Handler transaction followed by token-guarded terminal update | Yes at finalization | Job idempotency | If handler never commits internally, stale finalization rolls back | **ENFORCED for single-transaction handlers** |
| Pipeline stages | Stage transactions/savepoints; explicit stage lease checks | Generally yes | stage status/idempotency | Named async/resume gaps are orchestration, not silent stale commit proof | **PARTIALLY_ENFORCED** |
| Fundamental/technical/combined/ranking/setup ordinary handlers | Primarily outer transaction | Final token guard | natural keys/upserts | No domain commit survives lost final token in inspected paths | **CONDITIONALLY_SAFE** |
| IB fetch | Multiple internal commits/progress callbacks | Token-aware progress is called around work, but every internal boundary is not one atomic lease+domain commit | Price content idempotency/revisions | Narrow stale window remains | **PARTIALLY_ENFORCED — PIPE-009** |
| Market prewarm | Internal provider/database work | Plan/session enforcement weak; worker progress guards vary | Price idempotency | Possible decision mutation independent of original plan | **PARTIAL — PIPE-005/009** |
| CERI SEC incremental/provider children | Internal commits; document-level leases on SEC paths | Specialized lease/heartbeat | provider/document natural keys | Reduced, not globally proven across all provider handlers | **PARTIALLY_ENFORCED** |
| CERI/Setup single-transaction rebuild handlers | Outer transaction | Final job token | content/natural keys | Generally rolled back on lost final token | **CONDITIONALLY_SAFE** |
| Winner maturation/cohort/rescore | Internal staged work with heartbeat/lease checks | Yes on inspected background paths | revisions/generation keys/manifests | Stale publication ordering still possible as a semantic race | **PARTIALLY_ENFORCED** |
| IB historical/live/scanner/flex refresh | Checkpoint commits | Heartbeat/checkpoint before commits in inspected paths | content hashes/natural keys | Reduced | **PARTIALLY_ENFORCED** |
| IB feature rebuild | Commits inside ticker loop | Cancellation only; no execution-token/lease ownership check before commit | content dedup | **Yes** | **VIOLATED — XINT-009** |

Job-row ownership is therefore not a repository-wide domain-write fence. A handler is safe only when it keeps domain changes in the worker-owned transaction or explicitly validates ownership immediately before each internal commit. **VERIFIED**

## Database Constraint and Writer Conflict Assessment

Database uniqueness and foreign keys strongly enforce local row shape and many natural keys, but they generally do not enforce cross-table temporal/configuration compatibility. The schema cannot ensure that a RankingResult's TechnicalScore shares a cutoff/config, that a Setup transition did not see a future episode, or that an IB feature's constituent observation preceded its declared as-of. Those checks are absent or service-level. **VERIFIED**

Important conflict patterns are: delete/recreate versus downstream references (core score rows), upsert versus historical consumers (RankingResult), current+revision versus non-PIT readers (PriceBar/IB metrics), and atomic pointer switches without ordering (Winner). No database trigger was found that repairs these semantic conflicts. **VERIFIED**

## Omitted Calculation Subsystems

IB market intelligence is a material omitted subsystem because it computes scored/classified features consumed by Ranking and CERI and has independent background refresh/rebuild routes. It is included throughout this report. **VERIFIED**

Trade-journal/flex/scanner artifacts were reviewed as IB-adjacent calculation/display paths. They can affect research surfaces, but no evidence showed that trade-journal research directly feeds the canonical scoring/ranking/Setup/Winner decision DAG. **VERIFIED**

No other unaudited subsystem with comparable direct decision influence was identified. Exports, dashboards, diagnostics, monitoring, and notification formatting are consumers or operational support rather than independent score producers, unless they invoke a hidden writer listed above. **PARTIALLY_VERIFIED**

## Formal Task 07 Findings

### XINT-001 — Calculation identity collapses to execution ownership

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Cross-cutting calculation provenance
- **Finding:** Multiple consumers treat run/ticker/company identity as sufficient even though upstream meaning also depends on pipeline/context/session/cutoff/calendar/config/version/revision/generation.
- **Evidence:** Combined/Ranking joins, sector fallbacks, Setup alternate loaders, Winner capture, and IB feature selection in the registers above; inherited RANK-004, SETUP-005/008, WIN-002/003/005.
- **Why it matters:** Ownership answers “whose row?” but not “which calculation truth?”.
- **Potential effect:** Cross-run/current/config-incompatible evidence can be labeled as one coherent decision.
- **Existing guard:** Strong MarketCalculationContext, CERI context fields, Setup handoff preflight, and Winner generation contracts on selected paths.
- **Missing guard:** One required compatibility envelope enforced at every producer/consumer edge.
- **Recommended future remediation:** Define typed artifact identity and database/service validators; persist exact upstream artifact IDs and reject incomplete envelopes.

### XINT-002 — IB historical features can contain future/current source evidence

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** IB market intelligence → Ranking/CERI
- **Finding:** IB feature rebuild labels output with `as_of_session` and cutoff but source queries load unbounded metric bars, the latest live/availability/shortable rows, and current PriceBars.
- **Evidence:** `app/services/ib_market_intelligence/orchestration.py` and repository selectors; isolated probe scored a 2026-01-10 liquidity bar for 2026-01-05 and returned score `1.0`, classification `VERY_POOR`, freshness `AVAILABLE`.
- **Why it matters:** Stored as-of/config/hash metadata describes the output label, not source eligibility.
- **Potential effect:** Ranking liquidity penalties and CERI volatility/short-pressure features can incorporate post-cutoff evidence.
- **Existing guard:** Feature config/calculation/source versions and constituent evidence hashes are stored; Ranking/CERI bound the selected feature row's own timestamp and coverage.
- **Missing guard:** Source-level `<= as_of/cutoff` queries, PIT PriceBar reconstruction, common session policy, and consumer confidence/freshness validation.
- **Recommended future remediation:** Make all IB inputs cutoff-aware, persist exact source IDs/revisions and common-session diagnostics, and reject future constituents before hashing/persistence.

### XINT-003 — Wall clock supplies historical business time

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Ranking, Winner, Setup, IB intelligence, legacy orchestration
- **Finding:** Current date/time or latest completed session is used when historical entry points lack an explicit decision anchor.
- **Evidence:** RANK-007, SETUP-005/006/010, WIN-001, IB `_snapshot_availability`, and legacy context creation.
- **Why it matters:** Repeating an otherwise identical historical calculation later changes eligibility, freshness, earnings distance, or decision identity.
- **Potential effect:** Non-reproducible rankings, predictions, setup state, and IB feature confidence.
- **Existing guard:** Canonical pipeline context freezes cutoff/session; operational clocks are separated in newer models.
- **Missing guard:** Mandatory business-time parameters and rejection of fallback for historical/replay/backfill modes.
- **Recommended future remediation:** Introduce an explicit temporal mode and require frozen decision/session/cutoff for every retrospective mutation.

### XINT-004 — Readiness/confidence is metadata, not an end-to-end gate

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Scoring and decision consumers
- **Finding:** Numeric values remain populated under insufficient/low/error/stale states, while consumers frequently check neither readiness nor confidence.
- **Evidence:** CORE-009 independently reaches Combined (RANK-001), Ranking (RANK-001/002), and direct Setup/Lifecycle TechnicalScore consumption (SETUP-003); Combined `earnings_risk` has a separate Setup actionability edge (SETUP-008), while Combined score/decision and Ranking score/decision/profile are Setup metadata only. RANK-003/WIN-003/004 and IB coverage-only reads in Ranking/CERI remain independent readiness paths.
- **Why it matters:** An invalid numeric payload can regain actionability after one consumer edge.
- **Potential effect:** Rankings, Setup/Lifecycle transitions, and CERI features can use evidence explicitly marked unreliable. Winner independently rejects explicit technical insufficiency, but Ranking readiness/configuration weaknesses can still affect Winner cohort features.
- **Existing guard:** Winner explicitly rejects `TechnicalScore.insufficient_data=true` and applies some cohort eligibility policies; warnings are stored in several artifacts.
- **Missing guard:** A mandatory readiness contract with explicit consumer policy and normalization exclusion.
- **Recommended future remediation:** Use typed unavailable values or eligibility gates; require policy decisions for each readiness/confidence state and persist the decision.

### XINT-005 — Mutable projections are used as historical evidence

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Price, Ranking, Setup/Lifecycle, CERI rules, IB metrics, publication
- **Finding:** Historical calculations read current/upserted/canonical/active rows without consistently reconstructing the version valid at calculation time.
- **Evidence:** CERI-002/003/008/010, RANK-006, SETUP-002/004/007/009, WIN-007, and XINT-002.
- **Why it matters:** A row reference can retain the same identity while its historical meaning changes.
- **Potential effect:** Replay drift, future-state observation, lost ranking/profile meaning, and serving rollback.
- **Existing guard:** PriceBar revisions/PIT reader, snapshot version tables, Setup events, Winner frozen vectors/generations.
- **Missing guard:** Uniform immutable IDs or revision-as-of reconstruction for all historical reads.
- **Recommended future remediation:** Separate append-only evidence from mutable serving projections and forbid historical code from querying projection tables directly.

### XINT-006 — Mutation safety depends on entry point

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Pipeline, CERI, Setup, Winner, market/IB jobs
- **Finding:** Canonical pipeline and certification paths require stronger provenance than direct API, admin, repair, replay, maintenance, backfill, and standalone service calls.
- **Evidence:** Entry-Point Contract Matrix; PIPE-002/003/005/007/008, CERI-003/004/008/010/012, SETUP-001/005/006/010, WIN-001/002.
- **Why it matters:** The same domain artifact has different correctness depending on how it was invoked.
- **Potential effect:** Alternate paths can create artifacts indistinguishable from canonical results.
- **Existing guard:** Feature flags, transition preflight, worker jobs, and explicit admin controls.
- **Missing guard:** One domain command boundary enforcing the minimum provenance envelope for every caller.
- **Recommended future remediation:** Route all mutation entry points through shared validated command objects; mark retrospective/current-state products explicitly.

### XINT-007 — Recorded fingerprints and manifests have incomplete proof boundaries

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Cross-cutting lineage/manifest infrastructure
- **Finding:** Several persisted hashes/manifests are not validated by consumers, omit necessary inputs, or can become stale after mutation.
- **Evidence:** Ranking lacks profile hash; Winner records but does not validate handoff; prewarm records but does not enforce plan metadata; IB hashes unbounded inputs; CERI purge leaves stale derived hashes.
- **Why it matters:** Presence of a hash can be mistaken for evidence of completeness, compatibility, or temporal validity.
- **Potential effect:** Audits and consumers overstate provenance guarantees.
- **Existing guard:** Canonical serializer and strongly enforced market-context, Setup preflight, technical cache, and Winner generation hashes.
- **Missing guard:** Declared schema/scope and required consumer comparison for each fingerprint.
- **Recommended future remediation:** Publish a machine-readable fingerprint registry with proof claims, schema versions, creators, and mandatory validators.

### XINT-008 — Historical selectors lack calculation-time upper bounds

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Setup/Lifecycle, CERI, IB intelligence
- **Finding:** Several prior/latest/history queries order or lower-bound rows but do not require evidence time `<= calculation_time`.
- **Evidence:** Setup alert cooldown/active episode, CERI change rebuild/surprise fallback, IB metric/live/shortable/availability/PriceBar selectors.
- **Why it matters:** “Previous/latest” is relative to database state rather than the historical decision.
- **Potential effect:** Future state changes older transitions, alerts, CERI changes, and IB features.
- **Existing guard:** Authoritative OHLCV PIT reader and several sector/Winner/trade-research upper bounds.
- **Missing guard:** Required cutoff predicate and deterministic same-session ordering for every historical selector.
- **Recommended future remediation:** Centralize temporal query helpers and test with older-after-newer and same-session-replay cases.

### XINT-009 — IB feature rebuild can commit after job ownership loss

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Background job/domain write fencing
- **Finding:** `execute_feature_rebuild` commits inside its ticker loop while checking cancellation but not the background execution token/lease immediately before commit.
- **Evidence:** IB feature rebuild orchestration versus generic worker final token guard; other IB refresh handlers use heartbeat/checkpoint fencing.
- **Why it matters:** The final job-row guard cannot roll back an already committed domain write.
- **Potential effect:** A reclaimed/stale worker can persist duplicate or temporally conflicting IB features consumed by Ranking/CERI.
- **Existing guard:** Content-signature dedup and cancellation check.
- **Missing guard:** Lease/token validation within the same transaction immediately before every internal commit.
- **Recommended future remediation:** Pass a mandatory lease guard into the orchestrator and make domain commit conditional on current execution ownership.

### XINT-010 — Configuration authority is fragmented and misleading

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Winner, IB intelligence, Ranking, market regime, CERI/Setup rules
- **Finding:** Settings, YAML, DB rows, API parameters, and code constants combine without one persisted effective configuration; some “enabled” settings do not act as master gates.
- **Evidence:** WIN-010; IB settings global/per-module gates versus unused YAML `engine.enabled`; RANK-005; CORE-008; CERI-008/Setup rule history.
- **Why it matters:** Operators and historical readers cannot infer behavior from a single named flag or version.
- **Potential effect:** Unexpected background mutation and irreproducible historical decisions.
- **Existing guard:** Many result-specific config/version fields and full hashes in sector/CERI/Setup/Winner/IB artifacts.
- **Missing guard:** One resolved effective config object/hash and authoritative gate precedence.
- **Recommended future remediation:** Resolve configuration once per command, persist the complete canonical hash, and generate gates from that resolved object.

### XINT-011 — Winner publication is atomic but not monotonic/idempotent

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Winner generation publication
- **Finding:** A complete older generation may become active after a newer generation, and duplicate publication requests lack a full idempotent success contract.
- **Evidence:** WIN-007 and WIN-009; Publication and Pointer Monotonicity table.
- **Why it matters:** Atomicity prevents partial state, not semantic rollback.
- **Potential effect:** Serving silently moves to older evidence/model output.
- **Existing guard:** Complete-build validation, manifests, transaction, generation-bound estimates.
- **Missing guard:** Compare-and-swap on current pointer with monotonic generation/evidence ordering and duplicate request key.
- **Recommended future remediation:** Fence publication by active-pointer version and generation ordering; make repeated same-generation publication a no-op success.

### XINT-012 — Retry idempotency can suppress a later refresh cycle

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** CERI provider acquisition/orchestration
- **Finding:** Stable provider/direct-ingestion keys represent a business identity wider than one retry attempt and can collapse a later acquisition cycle.
- **Evidence:** CERI-012 and provider-child key review; Retry Identity Versus Refresh Identity table.
- **Why it matters:** “Same request” and “new observation attempt” are different identities.
- **Potential effect:** New provider truth is never acquired while downstream state appears successfully idempotent.
- **Existing guard:** Deterministic retry behavior and provider/document natural keys.
- **Missing guard:** Explicit refresh-cycle/generation identity separated from attempt idempotency.
- **Recommended future remediation:** Add acquisition-cycle IDs and scope retry keys to an attempt/business cycle without duplicating writes within that cycle.

## Global Invariant Assessment

| Invariant | Classification | Evidence and rationale |
|---|---|---|
| INV-01 Execution ownership identity is not sufficient calculation identity. | `VIOLATED` | Run/ticker-only Combined/Ranking, Setup alternates, Winner capture, IB selectors; XINT-001, RANK-004, SETUP-005/008, WIN-002/003/005. |
| INV-02 Historical calculations never derive business time from current wall clock. | `VIOLATED` | Ranking earnings, Winner backfill/capture, Setup maintenance, IB historical freshness; XINT-003. |
| INV-03 Historical/prior-state queries cannot observe future state. | `VIOLATED` | Setup cooldown/active state, CERI changes, IB feature sources; XINT-002/008. |
| INV-04 Known-insufficient evidence cannot regain actionability through populated numeric values. | `VIOLATED` | CORE-009 independently contaminates Combined and Ranking and directly enters Setup/Lifecycle through TechnicalScore (SETUP-003); Combined `earnings_risk` separately affects Setup actionability. Ranking/Combined score and decision labels remain Setup metadata only. IB readiness and Winner policy gaps are independent; XINT-004. |
| INV-05 Silent latest/global fallback cannot masquerade as execution-owned provenance. | `VIOLATED` | CORE-006/007, RANK-003, SETUP-005, CERI-003, WIN-001/002, PIPE-007; fallback register. |
| INV-06 Every persisted decision artifact retains/references exact configuration. | `VIOLATED` | Ranking profile, market regime, fundamentals, mutable alert rules; RANK-005, CORE-008, CERI-008, SETUP-007. |
| INV-07 Every fingerprint/manifest has explicitly documented proof boundaries. | `NOT_ENFORCED` | Scope is implicit in creator code; several consumers do not validate; XINT-007. |
| INV-08 All mutating entry points enforce the same minimum provenance contract. | `VIOLATED` | Canonical versus direct/admin/replay/backfill matrices; XINT-006. |
| INV-09 Current mutable projections are never treated as immutable historical evidence without reconstruction. | `VIOLATED` | PriceBar bypass, Ranking, Setup active/canonical, IB current metrics, rule rows; XINT-005. |
| INV-10 Replay/rebuild semantics are explicit and cannot substitute current rules/state. | `VIOLATED` | CERI and Setup replay/rebuild, Winner historical backfill, IB as-of rebuild; historical semantics table. |
| INV-11 Decision evidence remains immutable when later outcome/provider truth is revised. | `PARTIALLY_ENFORCED` | Winner is strong; CERI purge/current PriceBar bypass, Setup repair, Ranking upsert, IB rebuild violate broader rule. |
| INV-12 Continuations use frozen targets or explicitly documented dynamic scope. | `VIOLATED` | Winner maturation dynamic due population and incomplete termination contract; WIN-008. |
| INV-13 Publication pointers are atomic, complete, idempotent, and monotonic. | `VIOLATED` | Winner pointer is atomic/complete but not monotonic or duplicate-idempotent; XINT-011. |
| INV-14 Stale workers cannot commit decision-relevant writes after ownership loss. | `VIOLATED` | IB feature rebuild internal commits have no token/lease validation; XINT-009; generic worker only protects outer transaction. |
| INV-15 Retry idempotency cannot suppress a legitimate later refresh. | `VIOLATED` | CERI direct/provider refresh-cycle identity; XINT-012/CERI-012. |

None of the fifteen systemic invariants is fully repository-wide enforced. INV-11 is the closest because Winner cleanly separates frozen decision evidence from versioned outcome truth, but other subsystems violate the global form. **VERIFIED**

## Cross-Finding Dependency Graph

```text
ROOT A — calculation identity collapse (XINT-001)
  ├─ run-only score joins → RANK-004
  │    ├─ ranking input/profile ambiguity → RANK-005/006
  │    ├─ Setup unchecked compatibility → SETUP-005/008
  │    └─ Winner recorded-not-validated acquisition → WIN-002/003/005
  ├─ newest/global fallback → PIPE-007 + CORE-006/007 + RANK-003
  │    ├─ Setup cross-run source contamination → SETUP-005
  │    └─ Winner independently inherits regime/sector contamination → WIN-005
  └─ IB feature row label trusted over constituents → XINT-002
       ├─ Ranking liquidity contamination → RANK-003
       └─ CERI volatility/short-pressure contamination → CERI decision features

ROOT B — missing/insufficient values retain numerics (XINT-004)
  └─ CORE-009
       ├─ Technical → Combined independently → RANK-001
       ├─ Technical → Ranking independently → RANK-001/002
       ├─ Technical → Setup/Lifecycle directly → lifecycle state/alerts → SETUP-003
       ├─ Combined `earnings_risk` → Setup actionability → SETUP-008
       ├─ Combined score/decision + Ranking score/decision/profile → Setup metadata only
       ├─ Technical → Winner independently; `insufficient_data=true` is explicitly rejected
       ├─ Combined/Ranking → Winner independently; ranking readiness/config gaps affect
       │    cohort features → WIN-003/004
       └─ explicit confidence can become non-operative metadata at unchecked edges

ROOT C — wall clock/current selector as business time (XINT-003/008)
  ├─ Ranking earnings proximity → RANK-007
  ├─ Setup direct/repair/replay/maintenance → SETUP-005/006/010
  ├─ Winner capture/backfill decision identity → WIN-001
  └─ IB historical freshness and future-source inclusion → XINT-002

ROOT D — mutable current/history ambiguity (XINT-005)
  ├─ current PriceBar bypass → CERI-002/003
  ├─ mutable RankingResult → RANK-006
  │    ├─ Setup metadata/provenance inconsistency only → SETUP-008
  │    └─ Winner cohort-feature inconsistency independently → WIN-003
  ├─ Setup canonical/active/rules → SETUP-002/004/006/007/009
  ├─ CERI mutable rules/purge → CERI-008/010
  └─ Winner serving pointer → WIN-007/009 → XINT-011

ROOT E — entry-point and ownership fencing fragmentation (XINT-006/009)
  ├─ async CERI parent/child boundary → PIPE-001 → pipeline completion/accounting defect;
  │    provider descendants can outlive the parent stage, but neither Setup nor Winner consumes CERI
  ├─ resume arguments missing → PIPE-002/SETUP-001
  ├─ execution replanning/alternate writers → PIPE-005/008/009
  ├─ CERI refresh identity collapse → CERI-012/XINT-012
  └─ IB feature loop commits without token fence → XINT-009

ROOT F — fingerprint/config proof overstatement (XINT-007/010)
  ├─ incomplete regime/profile hashes → CORE-008 + RANK-005
  ├─ stale/current rule authority → CERI-008 + SETUP-007
  ├─ Winner handoff recorded only → WIN-002
  └─ nominal master switches do not define mutation authority → WIN-010 + IB gate conflict
```

Corrections to the hypothesis chains in the addendum: same-run sector rotation does not feed Ranking; CERI does not feed Ranking, Setup/Lifecycle, or Winner; and Setup/Lifecycle does not feed Winner capture. PIPE-001 remains a pipeline completion/accounting defect because CERI provider descendants can outlive the parent stage, but it is not a Setup or Winner contamination path. Winner independently inherits Technical, Combined, Ranking, regime, and sector inputs through its actual frozen input graph; it explicitly rejects `TechnicalScore.insufficient_data=true`, while Ranking readiness/configuration weaknesses may still affect Winner cohort features. **VERIFIED**

## Top 10 Architectural Remediation Themes

These are future design themes only; Task 07 implements none of them.

1. **Make calculation identity a first-class type.** Require pipeline/context/session/cutoff/calendar/config/version/revision/generation as applicable, not ad hoc run/ticker joins.
2. **Separate immutable evidence from mutable projections.** Historical services should consume append-only/versioned artifacts; current/canonical/active tables should be serving projections only.
3. **Centralize temporal query semantics.** Every prior/latest/history selector should require an explicit upper bound and deterministic same-session ordering.
4. **Make readiness non-bypassable.** Represent insufficient/error inputs as ineligible typed values and persist each consumer's missing/confidence policy.
5. **Unify mutation entry points.** Pipeline, API, admin, repair, replay, backfill, maintenance, and scripts should call the same validated domain command.
6. **Create authoritative config snapshots.** Resolve environment/YAML/DB/API/code settings once, hash the full effective object, and store/reference it with every decision.
7. **Declare fingerprint proof contracts.** Version schemas, list included/excluded inputs, and require comparison where compatibility is claimed.
8. **Fence domain commits, not only jobs.** Validate lease/execution token inside the same transaction immediately before each internal commit.
9. **Freeze background scope and enforce monotonic publication.** Persist target manifests/watermarks and use compare-and-swap plus generation ordering for active pointers.
10. **Name retrospective semantics honestly.** Distinguish original-context reconstruction, current-rules recalculation, current-state repair, data acquisition, and operational retry in APIs and artifacts.

## Final Assessment

1. **Is there one authoritative calculation identity across the repository?** No. Strong identity exists in MarketCalculationContext and newer subsystem artifacts, but consumers do not uniformly require it. **VERIFIED**
2. **Can all historical business time be reconstructed without wall clock?** No. Ranking, Winner, Setup alternate paths, and IB feature freshness have current-time dependencies. **VERIFIED**
3. **Can historical queries observe future state?** Yes, in Setup, CERI, and IB intelligence. **VERIFIED**
4. **Can known-insufficient evidence become actionable?** Yes. Technical insufficient numeric evidence independently propagates into Combined, Ranking, and Setup/Lifecycle; Setup/Lifecycle consumes TechnicalScore directly, allowing retained numerics to affect lifecycle state and alerts. Combined `earnings_risk` separately participates in Setup actionability, while Combined score/decision and Ranking score/decision/profile are metadata only in Setup. Winner is an independent branch: it explicitly rejects `TechnicalScore.insufficient_data=true`, although Ranking readiness/configuration weaknesses can still affect its cohort features. **VERIFIED**
5. **Are persisted hashes/manifests enforcement mechanisms?** Some are; many are recorded-only or incomplete. Presence of a hash is not sufficient proof. **VERIFIED**
6. **Do all entry points enforce an equivalent provenance contract?** No. Canonical pipeline/certification paths are materially stronger. **VERIFIED**
7. **Are historical decisions fully reconstructable from immutable artifacts?** Winner's frozen vector is close; Ranking, Setup/Lifecycle, fundamentals, and IB historical features are not. **VERIFIED**
8. **Can a stale worker commit decision-relevant state?** Yes; IB feature rebuild is the clearest verified path. **VERIFIED**
9. **Are background target and publication transitions safe?** Not completely: Winner maturation is dynamic, and Winner publication lacks monotonic/idempotent fencing. **VERIFIED**
10. **Are the Task 01–06 defects isolated?** No. Their common causes recur under different names, including in IB market intelligence. **VERIFIED**

The authoritative Task 07 conclusion is therefore: SwingLens contains several strong provenance primitives, but they form islands of enforcement rather than a system-wide contract. Certification cannot rely on shared run/ticker identity, recorded hashes, or current/latest database state as proof of temporal compatibility. **VERIFIED**

## Audit Coverage

- Repository-wide searches covered `app/`, `tests/`, `scripts/`, `alembic/`, configuration files, and Tasks 01–06 reports. **VERIFIED**
- Material models, repositories, services, routers, background handlers, orchestration, migration heads, and selected operational scripts were inspected for identity joins, latest/current fallbacks, wall clock, writers, commits, leases, hashes, manifests, flags, replay/backfill, and publication. **VERIFIED**
- IB market intelligence was audited as the principal omitted calculation subsystem and traced into Ranking/CERI consumers. **VERIFIED**
- No production provider, live application endpoint, queue, database, migration, or background mutation was executed. **VERIFIED**

## Known Unknowns

- Live database contents, deployed environment-variable values, provider behavior, task-queue concurrency, and production query plans were not inspected. **UNKNOWN**
- Static review cannot prove that every dynamic SQL/reflection/plugin-driven writer has executed in production; no additional principal-table writer was found. **PARTIALLY_VERIFIED**
- Pre-baseline historical behavior and artifacts written by older deployments require migration/deployment records outside this repository state. **UNKNOWN**
- The intended business policy for accepting low-confidence IB features and the intended refresh-cycle cadence for every provider are not expressed as one authoritative contract. **UNKNOWN**

## Contradictions

- Winner's nominal global/engine enabled settings do not consistently govern subordinate capture/admin/automatic mutation flags. **VERIFIED — WIN-010**
- IB YAML `engine.enabled` appears authoritative by name but effective enablement comes from settings global/module flags plus section settings. **VERIFIED — XINT-010**
- IB feature rows declare an as-of/cutoff and hash their evidence, while source selection permits later/current constituents. **VERIFIED — XINT-002**
- Severe market staleness can display Gray while retaining bullish sizing/permissions. **VERIFIED — CORE-002**
- CERI config declares provider priority, but effective conflict resolution does not apply it. **VERIFIED — CERI-001**
- Conceptual dependency assumptions that CERI or same-run sector rotation feed Ranking, or that Setup/CERI feed Winner, are contradicted by code. **VERIFIED**

## Potential Contract Violations

- **P1:** XINT-001 calculation identity collapse.
- **P1:** XINT-002 future/current evidence in IB historical features.
- **P1:** XINT-003 wall-clock historical business time.
- **P1:** XINT-004 readiness/confidence propagation.
- **P1:** XINT-005 mutable projections as historical evidence.
- **P1:** XINT-006 entry-point-dependent mutation correctness.
- **P2:** XINT-007 incomplete fingerprint/manifest proof boundaries.
- **P1:** XINT-008 missing historical query upper bounds.
- **P1:** XINT-009 stale-worker IB feature commits.
- **P2:** XINT-010 fragmented configuration authority.
- **P1:** XINT-011 non-monotonic/non-idempotent Winner publication.
- **P2:** XINT-012 retry identity suppressing refresh identity.

## High-Risk Cross-Subsystem Dependencies

- Technical insufficient numeric evidence independently propagates into Combined, Ranking, and Setup/Lifecycle; Setup/Lifecycle consumes TechnicalScore directly, and those retained numerics can drive lifecycle state and alerts. **VERIFIED**
- Combined `earnings_risk` participates in Setup actionability. Combined score/decision and Ranking score/decision/profile are denormalized Setup metadata/provenance only and do not drive lifecycle state. **VERIFIED**
- Winner independently consumes Technical/Combined/Ranking/regime/sector inputs. It rejects `TechnicalScore.insufficient_data=true`, while Ranking readiness/configuration weaknesses may still affect Winner cohort features. **VERIFIED**
- Global, temporally unbounded IB feature inputs → Ranking liquidity and CERI volatility/short-pressure. **VERIFIED**
- Sparse/stale market regime and cross-run sector history independently affect Setup and Winner feature vectors. **VERIFIED**
- Mutable RankingResult/profile ambiguity creates Setup metadata/provenance inconsistency and independently affects Winner cohort features; Ranking score/decision does not drive Setup actionability. **VERIFIED**
- Asynchronous CERI provider descendants can outlive the parent CERI stage and pipeline completion boundary. This is a pipeline completion/accounting defect; neither Setup/Lifecycle nor Winner consumes CERI. **VERIFIED**
- Current PriceBar/IB metric projections → historical CERI/IB calculations when PIT reconstruction is bypassed. **VERIFIED**
- Setup repair/replay/current rules → lifecycle transition/alert history. **VERIFIED**
- Completed Winner generation → non-monotonic active pointer switch. **VERIFIED**

## Files Inspected

Representative decision-relevant files inspected directly or through repository searches include:

- `app/services/market_calculation_context.py`, `app/services/market_data/*`, and PriceBar model/repository/migrations.
- `app/services/technical*`, `app/services/fundamental*`, `app/services/combined*`, `app/services/ranking_profile_service.py`, and `app/services/ranking_profile_engine.py`.
- Market-regime and sector-rotation services, models, repositories, routes, and configuration.
- `app/services/ceri/*`, CERI models/repositories/routes/background handlers/configuration/migrations.
- Setup/Lifecycle services, repositories, models, routes, transition preflight/handoff code, repair/replay/maintenance/alert paths, and configuration.
- Winner prediction, evidence, maturation, cohort, rescore, generation, serving/publication, routes, handlers, models, and configuration.
- `app/services/ib_market_intelligence/repository.py`, `orchestration.py`, calculators, routes, background handlers, models, and configuration.
- Background job worker/service/registry, pipeline orchestrators, upload processing, startup/supervisor recovery, caches, and manifest utilities.
- `scripts/ops/winner_candidate_estimates.py`, `scripts/ops/resolve_sec_ciks.py`, SEC profiling/forensics tools, and Alembic migrations through `0074_ceri_evidence_quarantine`.
- `docs/audit/calculation-lineage/01_pipeline_orchestration.md` through `06_winner_probability.md` as inherited finding hypotheses and cross-check sources.

## Tests Inspected

- Canonical evidence serialization tests.
- Background job execution-token, lease, recovery, idempotency, and handler tests.
- Market-context, OHLCV PIT/revision, technical cache, market-regime, sector, ranking, CERI, Setup/Lifecycle, and Winner tests referenced by Tasks 01–06.
- IB market-intelligence repository, calculator, orchestration, route, job, feature, and consumer tests.
- Task 07 targeted execution: `108 passed, 7 deselected, 1 warning` for canonical serializer, background-job service, technical artifact cache, and IB market-intelligence tests excluding external smoke tests. **VERIFIED**
- Pure IB calculation probe: a future 2026-01-10 liquidity metric was accepted for 2026-01-05 and emitted score `1.0`, classification `VERY_POOR`, freshness `AVAILABLE`. **VERIFIED**

## SQL Inspected

- ORM queries and mutations for latest/prior/current selectors, delete/recreate/upsert behavior, PriceBar/IB revisions, Setup active/canonical state, Winner generations/pointers, and background leases.
- Alembic DDL/data migrations through head `0074_ceri_evidence_quarantine`, with attention to uniqueness, foreign keys, revisions, current pointers, config/version fields, and data-fix writers.
- No live SQL was executed. Database-enforced classifications are based on schema/model/migration inspection, not production data. **VERIFIED**

## Verification Metadata

| Item | Result |
|---|---|
| Repository HEAD / implementation baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` / same |
| Baseline drift | None; `BASELINE_DRIFT` not raised |
| Alembic head | `0074_ceri_evidence_quarantine` |
| Python | 3.12.2 (`.venv`) |
| Targeted tests | 108 passed, 7 deselected, 1 unrelated Starlette/httpx deprecation warning |
| Additional probe | Pure in-memory IB liquidity calculation; no persistence/provider access |
| Production mutations | None |
| Files created/modified by Task 07 | Only `docs/audit/calculation-lineage/07_cross_cutting_integrity.md` |
| Verification timestamp basis | 2026-09-14, Europe/Zurich |
