# Task 05 — Setup Signals and Lifecycle Audit

## Audit Identity

| Field | Value | Status |
|---|---|---|
| Audit task | Task 05 only — Setup Signals and Lifecycle | **VERIFIED** |
| Repository | `C:\Users\Ivica\Documents\SwingLens` | **VERIFIED** |
| Current repository HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | **VERIFIED** |
| Implementation baseline SHA | `3a9d47063be996908b7d5d1cc5769e0bbd033546` | **VERIFIED** |
| Branch | `codex/winner-evidence-remediation` | **VERIFIED** |
| Implementation drift from baseline | No. The baseline-to-HEAD commit range and tracked diff are empty. Documentation under `docs/audit/calculation-lineage/` is excluded from implementation drift. | **VERIFIED** |
| Baseline-drift disposition | `BASELINE_DRIFT` is not raised. | **VERIFIED** |
| Audit mode | Static implementation/schema/SQL/test inspection plus focused automated tests; no production jobs, providers, or live database were invoked. | **VERIFIED** |
| Test result | `288 passed, 1 warning` for `tests/setup_lifecycle` and `tests/test_pipeline_executor.py` | **VERIFIED** |

## Scope and Decision Summary

The implemented Setup/Lifecycle subsystem captures an immutable-identity setup snapshot, selects a mutable canonical snapshot for each ticker/timeframe/session, calculates a lifecycle state, mutates a single active episode per ticker/timeframe/setup family, appends transition/change records, and emits rule-driven alerts. The normal parent-pipeline entry point supplies a frozen market cutoff and pipeline identity and performs a comparatively strong pre-write handoff check. Direct evaluation, repair, daily maintenance, replay, and alert rebuild do not all preserve that envelope. **VERIFIED**

The implemented subsystem does **not** read CERI snapshots, features, changes, alerts, `opportunity_score`, `event_risk_score`, CERI `confidence`, or `posture`. The proposition that Setup/Lifecycle consumes current-run or pre-existing CERI is contradicted by the code. Consequently PIPE-001 cannot contaminate Setup/Lifecycle by causing it to read an older CERI artifact; instead, Setup/Lifecycle is CERI-blind. **CONTRADICTORY**

The most consequential current behaviors are:

- insufficient/error technical rows can retain numeric values that produce actionable lifecycle states because upstream technical readiness is not checked; **VERIFIED**
- an older repair can select and mutate a newer active episode, including moving its current/last-observed date backward; **VERIFIED**
- historical persistence counts observations rather than consecutive market sessions and reads mutable canonical history across runs/configurations; **VERIFIED**
- alert cooldown selection is not upper-bounded by the alert being evaluated, so a future alert can suppress an older repair/rebuild; **VERIFIED**
- the CERI-resume-plus-Setup path omits mandatory invocation arguments and fails before Setup evaluation; **VERIFIED**
- replay is a current-code/current-configuration comparison over current canonical snapshots, not a reconstruction of the original state chain. **VERIFIED**

## Intended Contract Versus Current Implementation

| Area | Intended contract | Current implementation | Status |
|---|---|---|---|
| Pipeline invocation | Setup receives the exact frozen `MarketCalculationCutoff` and owning `pipeline_run_id`. | The normal full pipeline does; the CERI-resume call omits both required arguments and raises `TypeError`. Direct API/worker evaluation passes neither and reconstructs context. | **VERIFIED** |
| CERI dependency | Cross-task material asks whether CERI influences Setup/Lifecycle. | No CERI artifact or field is loaded or consumed. | **CONTRADICTORY** |
| Technical readiness | An insufficient/error technical result should not silently become actionable. | `TechnicalScore.insufficient_data` and technical confidence are not consumer gates; retained numeric values are scored normally. | **VERIFIED** |
| Temporal envelope | Every transition is reconstructable from exact prior state and inputs under one session/cutoff/config/version envelope. | Current snapshots retain substantial source identity, but lifecycle events do not identify the exact prior snapshot/event, history is canonical/global across runs and configs, and active episodes are mutable. | **PARTIALLY_VERIFIED** |
| Historical replay | Replay uses original cutoff, configuration, prior state chain, and source versions. | Replay reads today's canonical pointers and current engine/config, evaluates rows independently, and does not persist reconstructed episodes/events/alerts. | **CONTRADICTORY** |
| Alert lineage | Alerts identify the transition and its business date and preserve the rule semantics that caused them. | Transition/change IDs and effective dates are retained, but the referenced rule row is mutable and cooldown search can look forward in time. | **PARTIALLY_VERIFIED** |

## Actual Dependency Direction

```text
UploadRun / RawCompanyRow
        │
        ├── FundamentalScore ───────────────┐
        ├── TechnicalScore ─────────────────┤
        ├── CombinedResult ─────────────────┤
        ├── RankingResult (display/lineage) ┤
        ├── MarketRegimeSnapshot ───────────┤
        ├── SectorRotationSnapshot ─────────┤
        └── bounded PIT PriceBar reader ────┘
                                             ▼
                                  SetupSignalSnapshot
                                             │
                              mutable canonical selection
                                             │
                  prior canonical snapshots + active episode
                                             ▼
                         LifecycleEvaluation / Episode / Event
                                             │
                                ChangeEvent / AlertEvent

CERI artifacts ──────────────────────────────X  (no implemented read path)
```

This is the code-level direction. Ranking and Combined values are captured into setup snapshot provenance/display fields, but lifecycle behavior principally consumes technical-derived signals, PIT price behavior, fundamental liquidity, Combined earnings risk, market gate/label, and sector rank/confidence. **VERIFIED**

## Setup Input and Provenance Contract

### Source selection

`SetupLifecycleSourceLoader.load_run_context()` loads only a completed `UploadRun`, same-run raw rows, same-run `FundamentalScore`, `TechnicalScore`, `CombinedResult`, and `RankingResult`, plus eligible market/sector snapshots and bounded price bars. It does not query CERI. **VERIFIED**

When an explicit cutoff is supplied, price rows are bounded by `bar_date <= latest_completed_session` and `first_seen_at <= calculation_cutoff`, then reconstructed with `project_price_bar_rows_as_of()` from `PriceBarRevision` lineage. The source priority is `TRADES` before `ADJUSTED_LAST`, and only the latest eligible sessions are read. **VERIFIED**

If the caller supplies no cutoff, the loader first tries the upload run's recorded market calculation context and otherwise builds a standalone cutoff from `processed_at` or `uploaded_at`. This is deterministic relative to the upload row but is not proof that it is the original decision context. **VERIFIED**

If no PIT price bar is available, the builder can fall back to `RawCompanyRow` close/high values. That fallback is warned and tends to reduce setup data quality, but the lifecycle state is still evaluated before actionability and trigger/confirm alert rules do not universally reject blocked/low-confidence snapshots. **VERIFIED**

### Input compatibility envelope

| Dimension | Normal parent pipeline | Direct evaluation / repair / replay | Classification |
|---|---|---|---|
| `run_id` | Same-run raw/fundamental/technical/combined/ranking checked by loader and preflight. | Evaluation loader uses same run; repair/replay operate from canonical snapshots independent of a requested run. | `CODE_ENFORCED` / `NOT_ENFORCED` |
| `pipeline_id` | Exact pipeline ID passed and asserted against the market context. | Usually absent; CERI resume accidentally omits it; repair/replay have no equivalent. | `VALIDATED_AT_RUNTIME` / `NOT_ENFORCED` |
| `market_calculation_context_id` | Preflight validates the supplied cutoff/context and market/sector handoff. | Loader may reconstruct context; repair/replay read captured/canonical rows without asserting original ownership. | `VALIDATED_AT_RUNTIME` / `ASSUMED` |
| `as_of_session` | Exact cutoff session governs bars; market/sector must not exceed it. | Captured snapshot date is used, but prior/canonical history can come from other runs/configs. | `VALIDATED_AT_RUNTIME` / `PARTIALLY_VERIFIED` |
| `calculation_cutoff` | Price and handoff rows are bounded and verified. | Reconstructed for direct capture; absent from episode selection, repair, maintenance and replay decisions. | `VALIDATED_AT_RUNTIME` / `NOT_ENFORCED` |
| `calendar_version` | Present in cutoff and decision manifest and checked during guarded transition handoff. | Not checked by episode/history/replay selection. | `VALIDATED_AT_RUNTIME` / `NOT_ENFORCED` |
| Configuration hash/version | Setup engine/config hash is part of snapshot identity. Upstream Combined/Ranking compatibility is not fully checked. | Canonical history and active episode selection do not require a matching config; replay request config is recorded but not applied. | `PARTIALLY_VERIFIED` / `NOT_ENFORCED` |
| Calculation/model version | Setup snapshot stores its engine version; technical version/fingerprint is in the guarded manifest. | Prior history/episode selection and alerts do not require compatible versions. | `PARTIALLY_VERIFIED` / `NOT_ENFORCED` |
| Snapshot revision | Exact source row IDs and bar hash are captured; market/sector revision/evidence/config are not copied into setup lineage. | Mutable canonical pointers select one candidate after the fact. | `PARTIALLY_VERIFIED` |
| Generation | No end-to-end generation dimension is enforced across setup inputs, episodes, events, and alerts. | Same. | `NOT_ENFORCED` |

Shared `run_id` is therefore insufficient proof of temporal compatibility. The guarded parent-pipeline path adds meaningful checks, but those checks are entry-point dependent rather than a database invariant. **VERIFIED**

## Ranking Input Provenance

For a ticker, ranking rows are sorted by `(profile_rank, ranking_profile)`, and `ranking_results[0]` is selected. The setup snapshot records that row's ID, profile name, and numeric profile score. The builder does not check `RankingResult.is_complete`, warnings, `has_warning`, `Low confidence`, profile configuration/version, calculation context, session, or cutoff. **VERIFIED**

Ranking decision label and numeric score do not drive lifecycle state or actionability. They are denormalized metadata. This materially limits direct propagation of ranking defects, but it does not make the recorded ranking provenance sound. **VERIFIED**

| Task 04 finding | Setup/Lifecycle propagation | Status |
|---|---|---|
| RANK-001 — Combined may promote insufficient technical data | Combined score/decision do not drive lifecycle, but the same insufficient technical row is read directly and can drive lifecycle. Propagation is direct from CORE-009, not through the Combined label. | **PARTIALLY_VERIFIED** |
| RANK-002 — profiles retain synthetic scores while incomplete | The synthetic profile score is captured but is not a lifecycle input. No direct behavioral propagation; misleading provenance/display remains possible. | **PARTIALLY_VERIFIED** |
| RANK-003 — global latest-ticker liquidity fallback | Ranking's IB liquidity value is not consumed. Setup instead reads `FundamentalScore.liquidity_risk`. | **CONTRADICTORY** |
| RANK-004 — run equality substitutes for temporal compatibility | Repeated at the loader boundary for several same-run artifacts outside the guarded pipeline. | **VERIFIED** |
| RANK-005 — exact profile configuration is unrecoverable | The selected ranking row/profile is recorded without full profile config identity; arbitrary first-profile selection becomes historical metadata. | **VERIFIED** |
| RANK-006 — recalculation mutates/replaces same-run meaning | Ranking-only numeric changes may not change setup `source_data_hash` because promoted ranking values are excluded; a previously upserted setup snapshot may therefore retain stale denormalized ranking values. | **VERIFIED** |

Ranking session/context is assumed rather than proven because `RankingResult` itself is not bound to them. The normal preflight proves its run/raw-row relationship, not a nonexistent ranking session/cutoff/config identity. **VERIFIED**

## CombinedResult Dependency

Setup captures or falls back through Combined fields for `company_name`, `sector`, fundamental/dual values, classification, `final_score`, `decision`, and `earnings_risk`. Of these, `earnings_risk` participates in the actionability policy; the final score and decision are denormalized and do not drive lifecycle transitions. **VERIFIED**

The loader does not check Combined completeness, warnings, configuration hash, or calculation version. In the normal parent pipeline, handoff validation compares Combined debug source IDs to the exact raw/fundamental/technical rows, which is a useful guard but does not establish complete configuration compatibility. **VERIFIED**

`Candidate` or `Strong candidate` can originate from an insufficient technical result, but those labels are not the lifecycle decision input. The retained technical numeric fields can independently cause the same candidate to become READY/TRIGGERED/CONFIRMED. **VERIFIED**

A refreshed or delete/recreated `CombinedResult` can coexist with a prior setup/lifecycle chain. Setup stores a nullable source FK and selected derived values, but historical evaluation does not bind to an immutable Combined version; deletion can null the FK, and same-row mutation can change source meaning. **VERIFIED**

## CERI Dependency

| CERI artifact/value | Exact artifact selected | Run/context/session/cutoff/config/version/evidence validation | Current/latest substitution | Status |
|---|---|---|---|---|
| `opportunity_score` | None | Not applicable | None | **NOT_APPLICABLE** |
| `event_risk_score` | None | Not applicable | None | **NOT_APPLICABLE** |
| CERI `confidence` | None | Not applicable | None | **NOT_APPLICABLE** |
| `posture` | None | Not applicable | None | **NOT_APPLICABLE** |
| CERI feature/snapshot change | None | Not applicable | None | **NOT_APPLICABLE** |
| CERI alert | None | Not applicable | None | **NOT_APPLICABLE** |

PIPE-001 schedules the asynchronous provider-ingestion DAG and allows the parent pipeline to proceed to Setup before that DAG completes. Because Setup has no CERI reader, it cannot select pre-existing CERI state during that window. The defect remains important to the parent pipeline and later CERI consumers, but the hypothesized Setup/Lifecycle contamination path is absent. **VERIFIED**

The transition preflight validates any same-run CERI rows that happen to exist, but it does not require CERI presence/completion and the Setup decision manifest contains no CERI input. That does not make CERI an implicit Setup dependency. **VERIFIED**

## CERI Resume Invocation Defect

The normal pipeline calls `_invoke_setup_evaluation()` with `market_cutoff` and `pipeline_run_id`. The helper requires both arguments, checks that the configured evaluator accepts them, and forwards them to `evaluate_run()`. **VERIFIED**

The resume-from-CERI path reconstructs/validates the frozen cutoff for CERI capture, then invokes `_invoke_setup_evaluation()` without `market_cutoff` and `pipeline_run_id`. When Setup is enabled this is a deterministic Python argument error before Setup evaluation starts. Existing tests cover resume from CERI with Setup disabled and normal Setup forwarding, but not the combined resume-plus-Setup path. **VERIFIED**

The cutoff is required because it fixes the latest permitted market session, eligible `PriceBar` visibility, `PriceBarRevision` reconstruction time, market/sector candidate eligibility, setup `data_as_of`, freshness, source hash, and canonical candidate. The pipeline ID is required to prove that cutoff belongs to the resumed pipeline's calculation context and to activate the exact handoff assertions. **VERIFIED**

Direct/manual evaluation demonstrates the unsafe alternate behavior: when those values are absent, the loader reconstructs a cutoff from an upload-run context or its processed/uploaded timestamp. A future fix that supplies wall-clock “now,” latest session, or a newly reconstructed context would not preserve the resumed run's decision-time evidence. The required future contract is to recover the original `MarketCalculationContext` owned by the resumed `PipelineRun`, pass its exact immutable cutoff plus the original pipeline ID, assert their ownership, and validate the exact handoff manifest before any Setup/Lifecycle write. **VERIFIED**

## Technical Readiness and Numeric Propagation

The setup builder requires non-null `technical_score`, `setup_score`, `classification`, and a close price. It does not reject `TechnicalScore.insufficient_data = true`, nor does it use technical confidence as an eligibility gate. Technical confidence is merely copied into the snapshot. **VERIFIED**

Family adapters treat missing numeric values as zero and score retained numeric fields. A technically insufficient row with positive component values can therefore have complete required-feature coverage, high lifecycle confidence, and READY/TRIGGERED/CONFIRMED evidence. The actionability policy blocks snapshot-level `INSUFFICIENT` quality, but it does not inspect upstream technical insufficiency. **VERIFIED**

Lifecycle state is computed before actionability. Low-confidence or blocked snapshots can therefore transition the episode. `NEW_READY` suppresses only `BLOCKED`, while `NEW_TRIGGER` and `CONFIRMATION` matching does not apply the same actionability rejection. Numeric propagation from known-insufficient upstream data can consequently reach both state and alerts. **VERIFIED**

## Market Regime and Sector Rotation

| Source | Fields actually consumed | Ownership/session behavior | Effect | Status |
|---|---|---|---|---|
| `MarketRegimeSnapshot` | `regime`, `gate_ok` | Prefer eligible same-run row, otherwise eligible global `run_id IS NULL`; bounded by session/cutoff in capture loader. Exact context ownership is enforced only in guarded parent-pipeline preflight. | Gate/label affect actionability and alert restrictions; regime also contributes confidence context. | **VERIFIED** |
| `SectorRotationSnapshot` | `current_rank`, `confidence` | Prefer eligible same-run row, otherwise eligible global row; same entry-point-dependent guard. | Affects setup-family confidence, sector-change evidence, and sector acceleration alerts. | **VERIFIED** |

Setup/Lifecycle does not consume the regime numeric score, risk state, risk-off flag, position-size multiplier, allowed profiles/setups, minimum score adjustment, regime confidence, warnings, or source-session metadata as policy inputs. An explicit `gate_ok` is more authoritative than the displayed label. **VERIFIED**

Propagation assessment:

- CORE-001 can propagate: a sparse-data bullish regime/gate can permit actionable Setup/Lifecycle outcomes. **VERIFIED**
- CORE-002 can propagate: a severely stale Gray label that retains bullish `gate_ok` is not necessarily blocked and can remain actionable. **VERIFIED**
- CORE-006 can propagate through a SectorRotationSnapshot already calculated with global-regime fallback; the direct Setup loader also permits a global market/sector fallback outside guarded pipeline use. **VERIFIED**
- CORE-007 can propagate because cross-run prior-sector history may already be embedded in the same-run sector rank/confidence consumed by Setup. **VERIFIED**

## Setup Register

| Setup | Inputs | Conditions | Parameters | Session | Output | Expiry | Consumers | Risk | Status |
|---|---|---|---|---|---|---|---|---|---|
| Breakout | Classification/flags, setup score, trigger/pivot, close/high, ATR, contraction history, relative strength, volume | Discover from breakout classification/flags; READY at family score and near pivot; TRIGGERED on price cross; CONFIRMED on persisted cross; FAILED on failure evidence | Track `5.5`; ready `7.5`; pivot distance `<=2%`; contraction `2`; confirmation `2`; extension `2.5 ATR`; gap `3`; max age `40`; rearm `10` | Snapshot `data_as_of`; history rows `< current` | Setup snapshot, lifecycle state/episode/event, optional alerts | Age/gap and terminal rules | Episode service, change detector, alert service, replay | Observation count can span missing sessions; readiness ignores technical insufficiency | **VERIFIED** |
| Pullback | Prior uptrend, setup score, support/trigger, price, relative strength, volume/history | READY when support held; TRIGGERED on close cross; CONFIRMED with repeated cross plus RS/volume; FAILED on support break | Track `5.5`; ready `7.5`; support policy `1 ATR`; declining-volume history `2`; confirmation `2`; gap `3`; max age `30`; rearm `10` | Same snapshot/history model | Same | Same | Same | Some configured policy concepts are represented indirectly by adapter evidence rather than exact parameter enforcement | **PARTIALLY_VERIFIED** |
| VCP | VCP classification, contraction, dry-volume percentile, trigger/pivot, close | READY after contraction count and dry volume; TRIGGERED on cross; CONFIRMED after repeated cross | Track `5.5`; ready `7.5`; contraction `2`; dry-volume percentile `<=35`; gap `3`; max age `45`; rearm `10` | Same | Same | Same | Same | History is canonical/global, not configuration-bound | **VERIFIED** |
| Continuation | Continuation classification, tight-range history, trigger, close, ATR | READY after tight-range observations; TRIGGERED on cross; FAILED/EXTENDED by evidence | Track `5.5`; ready `7.5`; range percentile `<=40` for `2` observations; extension `2.5 ATR`; gap `2`; max age `20`; rearm `8` | Same | Same | Same | Same | “Consecutive” observations need not be consecutive sessions | **VERIFIED** |
| Generic | Technical or setup score, classification, trigger, close | Track/ready score thresholds; trigger cross; common failure/expiry rules | Track `5`; ready `7`; gap `2`; max age `20`; rearm `8` | Same | Same | Same | Same | Missing numerics become zero; retained insufficient numerics remain usable | **VERIFIED** |
| Cross-cutting actionability | Snapshot quality, liquidity risk, earnings risk, market gate/label, lifecycle confidence | BLOCKED, LOW_CONFIDENCE, WATCH_ONLY, or ACTIONABLE after lifecycle state calculation | Confidence threshold `70` plus configured market/risk rules | Current setup snapshot | `actionability_state`, blockers/restrictions | Re-evaluated per snapshot | Episode and alert policy | Does not inspect technical insufficiency; Gray+true gate can pass | **VERIFIED** |

Trigger references are setup-family specific. Breakout/VCP/continuation use explicit raw triggers or technical box values. Pullback uses an explicit raw trigger or the exact previous trading-session bar high. Missing required historical sessions do not use a nearest-session fallback for that trigger; the trigger becomes unavailable and a warning/quality effect is recorded. **VERIFIED**

## Lifecycle State Model

The effective precedence is terminal failure/expiry, extension, confirmation, trigger, ready, tightening phases, developing/trackable, and discovered. Terminal states are locked. A proposed weakening from READY/TRIGGERED can be held by hysteresis; CONFIRMED requires trigger persistence of at least two observations or is reduced to TRIGGERED. **VERIFIED**

```text
DISCOVERED
   └─> DEVELOPING ─> TIGHTENING_1 ─> TIGHTENING_2 ─> READY
                                                        └─> TRIGGERED
                                                              └─> CONFIRMED

Any nonterminal ─> EXTENDED / FAILED / EXPIRED
Terminal states remain terminal.
```

### Lifecycle Transition Matrix

| From | To | Trigger | Inputs | Threshold | Minimum duration | Session rule | Alert | Implementation Status |
|---|---|---|---|---|---|---|---|---|
| None/early nonterminal | DISCOVERED | No stronger evidence | Family classification/base evidence | Family adapter base match | None | Current setup snapshot date | Usually none | **VERIFIED** |
| DISCOVERED/DEVELOPING | DEVELOPING | Trackable family score | Technical/setup features | Family track threshold (`5` or `5.5`) | None | Current plus canonical history | Optional state-change policy | **VERIFIED** |
| DEVELOPING | TIGHTENING_1/2 | Improving contraction/range/volume evidence | Family history and velocities | Family-specific evidence count | Observation count | Rows ordered before current date | Change events | **VERIFIED** |
| Nonterminal | READY | Ready score and family-specific near-trigger/support/contraction condition | Technical-derived fields, price, trigger, history | Generally `7`/`7.5` plus family conditions | Family-dependent | Current date; history `< current` | `NEW_READY`, subject to partial actionability filter | **VERIFIED** |
| READY | TRIGGERED | Price crosses trigger | Current/previous PIT price and trigger | Cross condition | None | Current snapshot; previous price evidence | `NEW_TRIGGER` | **VERIFIED** |
| TRIGGERED | CONFIRMED | Persistent cross and family confirmation evidence | History, price, volume/RS as applicable | At least `2` observed true rows | `2` observations | Not guaranteed to be consecutive trading sessions | `CONFIRMATION` | **VERIFIED** |
| Any nonterminal | EXTENDED | Price too far beyond reference | Close, trigger/reference, ATR | Typically `2.5 ATR` | None | Current snapshot | Rule-dependent | **VERIFIED** |
| Any nonterminal | FAILED | Hard failure/family invalidation | Support/pivot failure, hard-failure flags | Family rule | Immediate | Current snapshot | Failure/change alert if configured | **VERIFIED** |
| Any nonterminal | EXPIRED | Age or observation gap exceeds policy | Episode first/last dates and calendar sessions | Family max age/gap | Family-specific | Daily maintenance may use caller date without frozen cutoff | Expiry/change alert if configured | **VERIFIED** |
| READY/TRIGGERED | Same stronger state | Hysteresis against weak regression | Prior state and confidence margin | Margin `>= -5`; confirmation persistence rule | Until evidence breaks rule | Prior active episode, not exact prior snapshot chain | None unless material change | **VERIFIED** |
| FAILED/EXPIRED/CONFIRMED | Same terminal | Terminal lock | Current active episode | Terminal | Permanent for episode | Rearm requires a later eligible episode | None | **VERIFIED** |

## State-Chain Temporal Integrity

| Required identity | Persisted or selected behavior | Status |
|---|---|---|
| Current evaluation session | `SetupSignalSnapshot.data_as_of` / lifecycle effective date | **VERIFIED** |
| Previous state selected | Mutable active episode by ticker/timeframe/family | **VERIFIED** |
| Previous state session | Episode `last_observed_on`, but not enforced `< current` before mutation | **PARTIALLY_VERIFIED** |
| Setup signal session | Exact setup snapshot date | **VERIFIED** |
| Ranking session | Not represented on `RankingResult`; assumed through run/order | **UNKNOWN** |
| CERI session | Not applicable; CERI not consumed | **NOT_APPLICABLE** |
| Market regime session | Captured source row/as-of and FK; exact context enforced only in guarded path | **PARTIALLY_VERIFIED** |
| Sector rotation session | Captured source row/as-of and FK; same limitation | **PARTIALLY_VERIFIED** |
| Market-data cutoff | Stored on setup snapshot and used by PIT capture; not enforced in active episode/history selection | **PARTIALLY_VERIFIED** |

Lifecycle history validation checks ordering, unique dates, ticker/timeframe consistency, and `history_date < current_date`. It does not require the same run, pipeline, calculation context, cutoff, calendar version, setup config, engine version, or source generation. **VERIFIED**

## Prior-State and “Latest” Selection Audit

| Selection | Ordering | Upper bound | Run/session/config bound | Same-session behavior | Future-state risk | Status |
|---|---|---|---|---|---|---|
| Price bars | Date/source priority plus PIT reconstruction | Session and cutoff | Context cutoff; not run-owned | Source priority chooses row | Guarded | **VERIFIED** |
| Market/sector candidate | Greatest eligible as-of/created/id; same-run preferred, then global | As-of and calculation cutoff | Run optional; exact context only preflight | Latest eligible wins | Guarded by upper bound | **PARTIALLY_VERIFIED** |
| Ranking profile | Lowest numeric rank, then profile name | None applicable | Same run only | First profile wins | Session unknown | **VERIFIED** |
| Canonical setup snapshot history | Mutable current pointer, dates `< current`, ordered | Current setup date | No run/config/context/version filter | Only current-selected candidate survives | No future date, but later canonical reselection changes historical input | **VERIFIED** |
| Active lifecycle episode | Active row by ticker/timeframe/family | **None** | No run/session/config/version bound | Same active row mutated | Newer/future relative state can be selected | **VERIFIED** |
| Latest closed episode for rearm | Latest closed row | **None relative to evaluated snapshot** | No run/config/version bound | Latest wins | Future closed episode can affect older evaluation | **VERIFIED** |
| Recent alerts for cooldown | Effective date `>= since` | **No `<= current effective_date`** | Rule/ticker/family filtering | Existing matching event suppresses | Future alert can suppress older work | **VERIFIED** |

## Replay, Repair, Maintenance, and Recalculation

| Path | Target and anchor | Upstream reread | Prior-state behavior | Persistence/alerts | Temporal result | Status |
|---|---|---|---|---|---|---|
| Normal evaluate | Completed upload run under supplied/reconstructed cutoff | Same-run scores plus PIT bars and eligible regime/sector | Canonical history + mutable active episode | Writes snapshots, canonical choice, episode/event/change/alerts | Strong only when invoked through parent-pipeline cutoff/preflight | **PARTIALLY_VERIFIED** |
| Historical repair | Current canonical snapshots for scope/date | Does not recapture source calculations | Applies each to the live active episode without history | Mutates episodes and creates alerts | Can rewrite newer current state with older evidence | **VERIFIED** |
| Daily maintenance | All active episodes at caller `as_of_date` and `market_session_completed` flag | None | Ages current mutable episode | May expire/mutate and alert | No frozen calculation context or cutoff proof | **VERIFIED** |
| Replay | Current canonical snapshots in requested range | Reads current canonical choice; uses current engine/config | Evaluates each snapshot independently without historical chain | “Persist” stores evaluation metadata only; no episodes/events/alerts | Comparison, not historical reconstruction | **VERIFIED** |
| Alert rebuild | Current lifecycle events and signal changes in range | Reads current mutable rules | No transition recalculation | Recreates missing alerts | Uses current rule semantics and flawed cooldown temporal bound | **VERIFIED** |
| Re-evaluate same run | Recaptures current same-run upstream rows | Yes | Current canonical/episode state | Can create/reuse snapshot and mutate chain | Meaning may change after upstream recalculation | **VERIFIED** |

### Required temporal scenarios

| Scenario | Observed code behavior | Status |
|---|---|---|
| Older session evaluated after newer session | Active episode selection has no upper bound; `_update_episode()` writes the older snapshot as current and can move `last_observed_on` backward. | **VERIFIED** |
| Same session evaluated twice | Setup snapshot upsert deduplicates identical identity/hash, while different config/source hash can create candidates; mutable canonical selection chooses one. Episode/event dedup is evaluation/source based, not a universal same-session prohibition. | **VERIFIED** |
| Same run evaluated after ranking refresh | Ranking-only promoted-value mutation may not change `source_data_hash`, leaving stale captured profile score; ranking is not a lifecycle decision input. | **VERIFIED** |
| Historical replay after CERI revision | No effect because CERI is not read. | **VERIFIED** |
| Historical replay after technical recalculation | Replay reads stored current canonical setup snapshots rather than exact historical technical rows; a fresh evaluate-run can capture recalculated technical evidence and change canonical history. Neither is an original-context reconstruction. | **VERIFIED** |

## Alert Lineage

Lifecycle alerts identify the exact lifecycle event/evaluation/episode; signal alerts identify the exact change event. `effective_date` is the business/transition date, while alert creation time is separate. Creation time is therefore not incorrectly used as business time. **VERIFIED**

The dedup key includes rule, source, ticker, episode, effective date, and evaluation identity. Cooldown is anchored in effective dates/trading sessions, but `recent_alert_events()` lacks an upper bound at the event being evaluated. A future alert yields zero intervening sessions and can suppress an older repair/rebuild. Conversely, an alert emitted by an older repair can suppress a later valid alert within the cooldown. **VERIFIED**

Alert rules are mutable upserts by rule ID. Historical AlertEvent rows refer to the same mutable rule row, so the exact rule parameters/version used at emission cannot necessarily be recovered later. Alert rebuild applies current rules to old transitions and does not restore the original rule configuration. **VERIFIED**

`NEW_READY` excludes blocked actionability but permits low confidence; trigger and confirmation rules do not apply an equivalent general actionability exclusion. Low-confidence or technically insufficient-derived transitions can therefore generate alerts when the rule's lifecycle-confidence threshold and other restrictions pass. **VERIFIED**

## Mutation and Generation Semantics

| Artifact | Semantics | Historical meaning retained/lost | Status |
|---|---|---|---|
| SetupSignalSnapshot | Append/upsert by run+ticker+timeframe+session+engine+config+source hash; selected derived fields later updated with lifecycle/canonical values | Strong input snapshot fields; ranking/Combined promoted values are not all in source hash and source FKs can become null after source replacement | **PARTIALLY_VERIFIED** |
| Canonical selection | Mutable pointer per ticker/timeframe/session with append-only selection audit | Current winner recoverable; downstream history changes when pointer changes | **VERIFIED** |
| LifecycleEpisode | Mutable current row | Prior current state/date/actionability lost except where a transition event happened to record it | **VERIFIED** |
| LifecycleEvent | Append-like with current/superseded semantics | From/to values retained; exact prior snapshot/event and complete input envelope not retained | **PARTIALLY_VERIFIED** |
| SetupSignalChange | Append event tied to evaluation/snapshot | Exact source snapshot retained; canonical/config interactions remain external | **PARTIALLY_VERIFIED** |
| AlertRule | Mutable upsert | Historical rule configuration can be lost | **VERIFIED** |
| AlertEvent | Append/deduplicated event | Exact triggering transition/change and effective date retained; mutable rule semantics not frozen | **PARTIALLY_VERIFIED** |
| ReplayEvaluation | Evaluation metadata/results counts | Does not create a parallel immutable lifecycle generation | **VERIFIED** |

## Lifecycle Writer Register

| Writer | Artifact | Trigger | Historical/current | Run binding | Session binding | Can overwrite newer state | Guard | Risk |
|---|---|---|---|---|---|---|---|---|
| Setup snapshot capture | `SetupSignalSnapshot` | Run evaluation | Historical candidate plus denormalized current fields | Yes | Yes | Not identity row, but can update denormalized fields | Unique identity/hash; optional handoff verifier | Source hash omits some promoted data |
| Canonicalizer | Canonical pointer/selection audit | Candidate capture/reselection | Mutable current pointer + audit | No single-run guarantee | Ticker/timeframe/session | Replaces current candidate | Deterministic precedence | Later recalculation changes historical consumer input |
| Episode service | `LifecycleEpisode`, `LifecycleEvent` | Canonical snapshot application | Mutable current episode + events | Evaluation reference, not episode ownership | Snapshot date written to episode | **Yes** | Terminal lock/hysteresis only | Older repair rolls episode backward |
| Change detector | `SetupSignalChange` | Snapshot comparison/evaluation | Append/current semantics | Evaluation-bound | Effective session | Can supersede same-key current event | Event key/current flag | No complete generation envelope |
| Alert service | `AlertEvent` | Lifecycle/change event | Append/deduplicated | Evaluation referenced | Effective date | Does not overwrite event; may suppress another | Dedup/cooldown/rule filters | Future-aware cooldown, mutable rules |
| Historical repair | Episodes/events/alerts | Admin/job repair | Mutates live current chain | Not original-run bound | Optional target date | **Yes** | Scope confirmation; terminal rules | No temporal upper-bound on active episode |
| Daily maintenance | Episodes/events/alerts | Caller date/session-complete flag | Mutates live current chain | None | Caller supplied | Advances current state | Trading-session age calculation | No frozen context/cutoff evidence |
| Replay | Evaluation-run metadata | Replay request | Comparative only | No original-run enforcement | Date range | No episode overwrite | Feature flags/confirmation | Reported transitions are not persisted transitions |
| Alert rebuild | Missing AlertEvents | Current events in range | Adds alerts | Event evaluation reference | Event effective date | Can affect cooldown outcomes | Dedup and current rule filters | Reinterprets history with current mutable rules |

## Formal Findings

### SETUP-001 — CERI resume cannot invoke Setup with its required provenance contract

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Pipeline resume / Setup invocation
- **Finding:** The resume-from-CERI branch calls `_invoke_setup_evaluation()` without its mandatory `market_cutoff` and `pipeline_run_id` arguments. With Setup enabled, execution fails before the evaluation. Supplying latest/current values would be provenance-unsafe.
- **Evidence:** `app/services/pipeline_executor.py:883-919`, `app/services/pipeline_executor.py:2061-2086`, `app/services/setup_lifecycle/evaluation_service.py:113-132`; normal forwarding is at `app/services/pipeline_executor.py:695-731`.
- **Why it matters:** Resume must continue the original frozen decision context, not create a new one or fail after CERI capture.
- **Potential contamination/correctness effect:** Current code produces a deterministic failure. An incomplete future fix could admit later bars/revisions/regime/sector state and silently change the resumed decision.
- **Existing guard:** Full pipeline passes and asserts both values; resumed CERI capture itself requires a frozen cutoff.
- **Missing guard:** No resume-plus-Setup test and no single context object enforced by the helper call site.
- **Recommended future remediation:** Pass the exact original PipelineRun-owned cutoff and pipeline ID, assert ownership, validate the frozen handoff manifest, and add a resume+CERI+Setup regression test. Do not synthesize either from wall-clock/latest state.

### SETUP-002 — Older repair can overwrite a newer active lifecycle episode

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Episode selection and historical repair
- **Finding:** Active episodes are selected only by ticker/timeframe/family. `_update_episode()` writes the supplied snapshot as current even when its date is not newer, so an older repair can roll current state and dates backward.
- **Evidence:** `app/services/setup_lifecycle/repository.py:775-816`, `app/services/setup_lifecycle/episode_service.py:60-123`, `app/services/setup_lifecycle/episode_service.py:277-315`, `app/services/setup_lifecycle/maintenance_service.py:105-137`.
- **Why it matters:** Current lifecycle state ceases to mean the latest market-time state.
- **Potential contamination/correctness effect:** Newer state/actionability/metadata can be replaced, later hysteresis and alerts can start from the wrong state, and rearm logic can consult a future closed episode.
- **Existing guard:** Completed-observation increments are zero for non-newer dates; terminal-state rules exist.
- **Missing guard:** No `active.last_observed_on < evaluated_session` precondition, historical branch/generation, or session-bounded episode query.
- **Recommended future remediation:** Separate historical replay generations from live episodes or require monotonic effective dates for live mutation; bind prior/closed selection to `< evaluated_session`.

### SETUP-003 — Insufficient technical evidence can become actionable state and alerts

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Setup scoring, lifecycle evaluation, alert policy
- **Finding:** Setup does not gate on `TechnicalScore.insufficient_data` or technical confidence. Positive numeric remnants are scored, lifecycle state is evaluated before actionability, and trigger/confirmation alert paths do not universally reject blocked/low-confidence outcomes.
- **Evidence:** `app/services/setup_lifecycle/snapshot_builder.py:29-36`, `app/services/setup_lifecycle/snapshot_builder.py:326-376`, `app/services/setup_lifecycle/family_adapters.py:114-116`, `app/services/setup_lifecycle/actionability_policy.py:39-75`, `app/services/setup_lifecycle/alert_service.py:283-297`, `app/services/setup_lifecycle/alert_service.py:366-385`.
- **Why it matters:** CORE-009 can cross the final action boundary despite the upstream row explicitly declaring insufficiency.
- **Potential contamination/correctness effect:** READY/TRIGGERED/CONFIRMED episodes and user-facing alerts may be generated from known-insufficient technical history.
- **Existing guard:** Missing required values and setup-level insufficient quality can block actionability; rule confidence thresholds exist.
- **Missing guard:** Explicit upstream readiness/confidence policy at capture, transition, and alert layers.
- **Recommended future remediation:** Persist and enforce a technical-readiness decision, suppress actionable transitions/alerts when insufficient, and test positive-numeric insufficient rows end to end.

### SETUP-004 — Lifecycle history is not a coherent, reconstructable temporal chain

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Historical features and transition lineage
- **Finding:** Prior setup history comes from mutable canonical pointers across runs/configurations; repeated-condition helpers count rows rather than consecutive market sessions; events do not identify the exact prior snapshot/event that supplied the previous state.
- **Evidence:** `app/services/setup_lifecycle/repository.py:585-647`, `app/services/setup_lifecycle/lifecycle_engine.py:120-194`, `app/services/setup_lifecycle/snapshot_builder.py:669-718`, lifecycle event schema in `app/models/tables.py:3947-4050`.
- **Why it matters:** A transition cannot be reproduced solely from persisted lineage, and later canonical reselection can alter the history used by a future calculation.
- **Potential contamination/correctness effect:** Missing-session gaps can masquerade as persistence; incompatible run/config evidence can satisfy confirmation; audit reconstruction cannot prove the exact prior state.
- **Existing guard:** Histories are ordered, unique by date, same ticker/timeframe, and strictly earlier than the current date.
- **Missing guard:** Trading-session adjacency, run/context/config/version compatibility, immutable history generation, and explicit prior snapshot/event IDs.
- **Recommended future remediation:** Freeze a lifecycle input manifest per evaluation with exact prior IDs and session adjacency; version canonical generations and require compatibility.

### SETUP-005 — Temporal compatibility and global fallback guards are entry-point dependent

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Source loading / market and sector context
- **Finding:** The normal parent pipeline verifies a frozen handoff, but direct evaluation reconstructs context and permits eligible global market/sector rows; repair/replay operate on canonical snapshots without reasserting the original context.
- **Evidence:** `app/services/setup_lifecycle/source_loader.py:88-184`, `app/services/setup_lifecycle/source_loader.py:624-687`, `app/services/setup_lifecycle/snapshot_builder.py:785-799`, `app/services/pipeline_executor.py:802-865`, `app/services/setup_lifecycle/transition_preflight_plan_service.py:313-412`.
- **Why it matters:** Safety depends on how evaluation is invoked rather than on the persisted schema/consumer contract.
- **Potential contamination/correctness effect:** CORE-001, CORE-002, CORE-006, and CORE-007 can influence actionability, confidence, changes, or alerts; global context can masquerade as a run-relevant fallback on unguarded paths.
- **Existing guard:** PIT upper bounds, source IDs/as-of/cutoff fields, and strong parent-pipeline preflight.
- **Missing guard:** Mandatory context ownership validation for every mutating entry point and complete source revision/config lineage.
- **Recommended future remediation:** Require one immutable calculation-context envelope for all mutating paths and explicitly mark/persist approved fallback provenance.

### SETUP-006 — Replay is not historical reconstruction

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Replay
- **Finding:** Replay reads current canonical pointers, applies current engine/config independently to each snapshot without its prior chain, and “persist” stores evaluation metadata rather than parallel episodes/events/alerts. Requested config is recorded but not applied.
- **Evidence:** `app/services/setup_lifecycle/replay_service.py:46-98`, `app/services/setup_lifecycle/replay_service.py:132-173`.
- **Why it matters:** Replay results cannot answer what the system would have decided under the original conditions.
- **Potential contamination/correctness effect:** Reported transition counts can be mistaken for persisted transitions; later canonical/config changes alter replay output.
- **Existing guard:** Replay is feature-gated and persisted requests require explicit confirmation.
- **Missing guard:** Original cutoff/config/prior-state reconstruction and immutable replay generation.
- **Recommended future remediation:** Define replay as either a dry comparison or a versioned reconstruction, persist exact inputs/outputs accordingly, and label counts precisely.

### SETUP-007 — Alert cooldown and rule lineage are temporally unsafe

- **Severity:** P1
- **Status:** **VERIFIED**
- **Subsystem:** Alert creation and rebuild
- **Finding:** Cooldown queries have a lower date bound but no upper bound at the evaluated event. Alert rules are mutable, and rebuild applies current rule semantics to historical transitions.
- **Evidence:** `app/services/setup_lifecycle/repository.py:993-1016`, `app/services/setup_lifecycle/alert_service.py:283-297`, `app/services/setup_lifecycle/alert_service.py:540-549`, alert schemas in `app/models/tables.py:4122-4208`.
- **Why it matters:** Alert eligibility can depend on future state, and historical alert meaning is not immutable.
- **Potential contamination/correctness effect:** A future alert can suppress an older repair/rebuild; an old replay/repair alert can suppress a later valid event; exact historical rule thresholds may be unrecoverable.
- **Existing guard:** Stable event keys, transition/change FKs, effective business date, and rule/cooldown filters.
- **Missing guard:** `effective_date <= candidate_date`, immutable rule revision FK, and original-rule rebuild semantics.
- **Recommended future remediation:** Bound cooldown temporally in both directions, version alert rules immutably, and bind each alert to the exact rule revision.

### SETUP-008 — Ranking and Combined provenance is captured without consumer readiness/config validation

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Setup snapshot composition
- **Finding:** Setup selects the first ranked profile and captures Ranking/Combined values without completeness, warning, profile config, session, or cutoff checks. Some promoted fields are excluded from setup source hashing.
- **Evidence:** `app/services/setup_lifecycle/source_loader.py:203-224`, `app/services/setup_lifecycle/source_loader.py:609-615`, `app/services/setup_lifecycle/snapshot_builder.py:286-376`, `app/services/setup_lifecycle/snapshot_builder.py:194-203`, `app/services/setup_lifecycle/snapshot_builder.py:542-592`.
- **Why it matters:** Persisted display/provenance can be stale or suggest stronger compatibility than code proves.
- **Potential contamination/correctness effect:** RANK-004/005/006 propagate into auditability; Combined earnings-risk refresh can change actionability while older lifecycle state remains.
- **Existing guard:** Same-run loading and, in the parent pipeline, exact Combined input-ID checks.
- **Missing guard:** Readiness/warning policy, immutable profile/config identity, and hashing of every captured decision-relevant/promoted value.
- **Recommended future remediation:** Persist exact input revisions/configs, define readiness policy, and include all captured semantic values in immutable snapshot identity.

### SETUP-009 — Mutable canonical/episode state can change historical meaning

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Snapshot canonicalization and episode materialization
- **Finding:** Canonical selection is mutable, lifecycle episodes are mutable current rows, same-date events can be superseded, and setup snapshots receive lifecycle/canonical denormalized updates.
- **Evidence:** `app/services/setup_lifecycle/canonicalization.py:195-218`, `app/services/setup_lifecycle/repository.py:205-286`, `app/services/setup_lifecycle/repository.py:835-854`, `app/services/setup_lifecycle/episode_service.py:483-494`.
- **Why it matters:** “Current” is recoverable, but the complete sequence of meanings assigned to historical rows is not always recoverable from a single immutable chain.
- **Potential contamination/correctness effect:** Upstream recalculation can change future historical inputs and supersede prior same-session interpretations.
- **Existing guard:** Candidate identities and canonical-selection audit records; lifecycle events preserve from/to values.
- **Missing guard:** Immutable generation identity across candidate, canonical selection, episode, event, and alert.
- **Recommended future remediation:** Introduce explicit generations and immutable transition materializations; keep current pointers as derived projections.

### SETUP-010 — Daily maintenance is not bound to a frozen decision context

- **Severity:** P2
- **Status:** **VERIFIED**
- **Subsystem:** Lifecycle maintenance
- **Finding:** Daily maintenance accepts a caller date and market-session-completed boolean, then ages/mutates all active episodes without a MarketCalculationContext, cutoff, calendar version assertion, or exact source snapshot for that date.
- **Evidence:** `app/services/setup_lifecycle/maintenance_service.py:64-103` and setup/lifecycle job handlers.
- **Why it matters:** Expiry and observation-gap state are business-time decisions and should be attributable to a market calendar/context.
- **Potential contamination/correctness effect:** Episodes can expire or advance under a date/calendar assumption that cannot later be reconstructed.
- **Existing guard:** Trading-session age calculation and explicit caller date.
- **Missing guard:** Frozen context/calendar identity and a decision manifest for maintenance.
- **Recommended future remediation:** Require a persisted maintenance context with calendar version, cutoff/session completion evidence, and exact affected episode generation.

## Cross-Task Invariants

| Invariant | Result | Finding |
|---|---|---|
| Setup cannot consume CERI evidence newer than the evaluation cutoff. | Vacuously satisfied because Setup consumes no CERI evidence. | **VERIFIED** |
| Setup cannot assume CERI provider ingestion completed merely because parent CERI stage completed. | No implemented assumption/read; PIPE-001 does not flow into Setup. | **VERIFIED** |
| An insufficient technical/ranking result cannot silently become actionable. | Violated for technical evidence; ranking values do not drive behavior. | SETUP-003 |
| Lifecycle previous-state selection must never see a future state. | Violated by unbounded active/closed episode selection and cooldown lookup. | SETUP-002, SETUP-007 |
| Older evaluation cannot overwrite/supersede newer state without explicit replay semantics. | Violated by repair/apply behavior. | SETUP-002, SETUP-009 |
| Historical replay uses original decision/session context. | Violated. | SETUP-006 |
| Every transition is reconstructable from the exact prior state and inputs. | Not enforced. | SETUP-004, SETUP-009 |
| Alerts remain attributable to exact transition and temporal context. | Transition/change attribution exists, but rule revision and cooldown direction are unsafe. | SETUP-007 |
| Setup inputs are version/temporally compatible, not merely same-run. | Only partially enforced on the guarded pipeline path. | SETUP-005, SETUP-008 |
| Recalculating upstream evidence cannot silently change historical lifecycle meaning. | Not enforced because canonical choice and current episode semantics are mutable. | SETUP-008, SETUP-009 |

## Audit Coverage

- Setup source loading, PIT price reconstruction, session/cutoff selection, market/sector fallback, and raw-price fallback were traced. **VERIFIED**
- All setup family adapters, common score/transition thresholds, trigger references, history/velocity readers, actionability rules, lifecycle engine states, episode mutation, change detection, and alert rules were traced. **VERIFIED**
- Parent pipeline, CERI resume, direct API/worker evaluation, historical repair, daily maintenance, replay, and alert rebuild paths were traced. **VERIFIED**
- Ranking, Combined, technical readiness, CERI, market regime, sector rotation, and alert lineage cross-task dependencies were explicitly evaluated. **VERIFIED**
- No remediation, production invocation, provider call, or live database mutation was performed. **VERIFIED**

## Known Unknowns

- Live-database contents, orphaned/null source FKs, duplicate historical candidates, existing future-dated episodes/alerts, and production rule configuration were not inspected. **UNKNOWN**
- Operational deployment flags may differ from repository defaults; repository defaults disable Setup/Lifecycle pipeline, alerts, and replay features. **UNKNOWN**
- The intended product requirement for CERI influence on Setup/Lifecycle is not encoded in the audited implementation. Whether CERI-blind behavior is intentional requires an external specification decision. **UNKNOWN**
- Runtime proof of the older-repair rollback and future-alert cooldown cases was not added because this task is audit-only; the behavior is deterministically established by the inspected query/update paths. **INFERRED**

## Contradictions

- Any architecture showing CERI feeding current Setup/Lifecycle is contradicted by the absence of CERI queries, fields, or decision inputs. **CONTRADICTORY**
- A claim that replay reconstructs original lifecycle history is contradicted by current-canonical/current-config stateless evaluation. **CONTRADICTORY**
- A claim that `run_id` proves a common temporal envelope is contradicted by missing session/cutoff/config identity on Ranking and by cross-run/config canonical history and episode selection. **CONTRADICTORY**
- A claim that Low confidence or insufficient technical rows are ineligible is contradicted by setup scoring/actionability/alert code. **CONTRADICTORY**

## Potential Contract Violations

- `SETUP-001`: resumed pipeline provenance invocation is incomplete and currently fails. **VERIFIED**
- `SETUP-002`: live episode mutation is not monotonic in business time. **VERIFIED**
- `SETUP-003`: technical readiness is not enforced at the action boundary. **VERIFIED**
- `SETUP-004`: exact prior-state and compatible historical-input reconstruction is unavailable. **VERIFIED**
- `SETUP-005`: context/fallback controls are not uniform across mutating entry points. **VERIFIED**
- `SETUP-006`: replay semantics do not meet historical reconstruction expectations. **VERIFIED**
- `SETUP-007`: cooldown can observe future alerts and rule lineage is mutable. **VERIFIED**
- `SETUP-008`: upstream readiness/config provenance is incomplete. **VERIFIED**
- `SETUP-009`: mutable current projections can change historical meaning. **VERIFIED**
- `SETUP-010`: daily maintenance decisions lack a frozen context identity. **VERIFIED**

## High-Risk Cross-Subsystem Dependencies

- Technical calculation → setup family scoring → lifecycle state → alerts: CORE-009 propagates without an explicit readiness gate. **VERIFIED**
- Market regime → actionability: CORE-001 and CORE-002 can permit actionability because only label/gate are consumed and `gate_ok` dominates. **VERIFIED**
- Sector rotation → setup confidence/change/alerts: CORE-006 and CORE-007 can arrive embedded in sector rank/confidence. **VERIFIED**
- Combined earnings risk → actionability: mutable/recalculated Combined evidence can coexist with older episode state. **VERIFIED**
- Canonical setup selection → historical persistence → confirmation: later canonical reselection changes the history seen by future evaluations. **VERIFIED**
- Repair/maintenance → mutable active episode → alert cooldown: historical writes can change current state and suppress later alerts. **VERIFIED**
- CERI → Setup/Lifecycle: no dependency exists in the audited implementation; PIPE-001 does not create CERI reuse here. **VERIFIED**

## Files Inspected

- `app/services/pipeline_executor.py`
- `app/services/setup_lifecycle/source_loader.py`
- `app/services/setup_lifecycle/snapshot_builder.py`
- `app/services/setup_lifecycle/family_adapters.py`
- `app/services/setup_lifecycle/lifecycle_engine.py`
- `app/services/setup_lifecycle/actionability_policy.py`
- `app/services/setup_lifecycle/episode_service.py`
- `app/services/setup_lifecycle/repository.py`
- `app/services/setup_lifecycle/canonicalization.py`
- `app/services/setup_lifecycle/evaluation_service.py`
- `app/services/setup_lifecycle/change_detection.py`
- `app/services/setup_lifecycle/alert_service.py`
- `app/services/setup_lifecycle/maintenance_service.py`
- `app/services/setup_lifecycle/replay_service.py`
- `app/services/setup_lifecycle/transition_preflight_plan_service.py`
- `app/services/setup_lifecycle/decision_manifest.py`
- `app/services/setup_lifecycle/job_handlers.py`
- `app/api/routes.py`
- `app/models/tables.py`
- Setup/Lifecycle configuration and settings files

## Tests Inspected

- `tests/setup_lifecycle/` (full suite)
- `tests/test_pipeline_executor.py`
- Focus areas included capture lineage, family adapters, state transitions, canonicalization, episodes, changes, alerts, replay/maintenance, and parent-pipeline handoff behavior.
- Executed: `.venv\Scripts\python.exe -m pytest tests/setup_lifecycle tests/test_pipeline_executor.py -q`
- Result: `288 passed, 1 warning in 22.18s`; the warning is a Starlette deprecation warning unrelated to the audited calculations. **VERIFIED**

## SQL Inspected

- Price-bar candidate SQL in `SetupLifecycleSourceLoader`, including `bar_date`, `first_seen_at`, source-priority, and PIT revision reconstruction inputs. **VERIFIED**
- Market/sector candidate ordering and run/global fallback queries. **VERIFIED**
- Canonical snapshot history/current-selection queries and candidate ordering. **VERIFIED**
- Active/latest-closed episode selection queries. **VERIFIED**
- Lifecycle event current/supersession queries. **VERIFIED**
- Alert dedup and cooldown queries, including the missing upper effective-date bound. **VERIFIED**
- No live SQL was executed. **VERIFIED**

## Verification Metadata

| Item | Value |
|---|---|
| Verified at | 2026-09-14 (Europe/Zurich) |
| Git HEAD | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Implementation baseline | `3a9d47063be996908b7d5d1cc5769e0bbd033546` |
| Baseline drift | No |
| Python | CPython 3.12.2 in repository `.venv` |
| Automated verification | 288 tests passed |
| Live database/provider access | None |
| Deliverable | `docs/audit/calculation-lineage/05_setup_lifecycle.md` only |
