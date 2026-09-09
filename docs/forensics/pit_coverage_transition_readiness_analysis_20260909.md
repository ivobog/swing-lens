# PIT Coverage and Transition Readiness Analysis — 2026-09-09

## A. Executive verdict

```text
PIT COVERAGE ANALYSIS CERTIFIED: YES
TRANSITION READINESS ANALYSIS CERTIFIED: YES
HIGH LIVE TRANSITION CANDIDATE AVAILABLE NOW: NO
SAFE TO RUN FINAL TARGETED TRANSITION CANARY: NO
CODE REMEDIATION REQUIRED BEFORE NEXT CANARY: YES
```

No genuine HIGH-confidence transition candidate exists at the certified prospective cutoff. The production candidate path scanned all 1,491 supported tickers: 552 had an exact current-selection key, 939 would initialize a new key, none was PIT-complete, none predicted a pointer advance, and all 1,491 were LOW.

The immediate blockers are correct key mismatch for 939 tickers, legacy technical provenance for 341 otherwise-winning same-key tickers, canonical non-improvement for 210, and genuine required-input absence for one. A second, systemic blocker is narrower than a production pipeline defect: `TransitionCandidateDiscoveryService` creates a new wall-clock prospective cutoff and then requires an already-persisted technical score to have that exact cutoff. `start_pipeline` subsequently creates a second fresh cutoff. A pre-enqueue stored score therefore cannot satisfy the first equality and also prove the context that enqueue will persist. This candidate-model overconstraint/rendezvous defect has no current primary-tier row because legacy evidence fails first, but it would block the next naturally stamped candidate. It must be fixed without relaxing the certified bar or source known-at rules.

Current data coverage itself is strong for receipt evidence: all 2,262,432 eligible daily price-bar rows have `first_seen_at` and usable current-revision provenance at the analysis cutoff; all 867,461 CERI source rows have `retrieved_at`; and all 10,992 distinct price bars referenced by CERI price-response features have current-cutoff provenance. The weak areas are legacy derived-artifact context: only 16/23,882 technical scores and 8/183 market/sector snapshots carry a complete frozen-context envelope, and all 5,792 CERI price-response rows predate 0070 and retain null 0070 fields. No post-0070 business row has accumulated yet.

Code-remediation classification: `CODE_REMEDIATION_NEEDED_CANDIDATE_OVERCONSTRAINT`.

## B. Baseline

| Control | Certified value |
|---|---|
| Branch | `codex/pit-coverage-transition-readiness` |
| Baseline HEAD | `946c7ee1bbf4bd8a35650704dd3e1ab4363417ee` |
| Baseline worktree | clean |
| `946c7ee` ancestry | ancestor of baseline HEAD |
| `5c76d7ec` ancestry | ancestor of baseline HEAD |
| `730570d` ancestry | ancestor of baseline HEAD |
| Alembic code head | `0070_ceri_price_response_pit_context` (single head) |
| Production Alembic head | `0070_ceri_price_response_pit_context` |
| Database | PostgreSQL 18.3, `127.0.0.1:5432`, database `swinglens`, role `postgres` |
| Inspection transaction | repeatable read, read only |
| App / worker / supervisor | stopped / stopped / stopped; zero matching processes |
| Port 8000 | zero listeners |
| Active / queued / recovering / stalled jobs | 0 |
| Latest Run / Pipeline / Job | 151 / 144 / 42931 |
| Latest market context / lifecycle snapshot / CERI snapshot | 4 / 36949 / 10929 |
| Runs / pipelines / jobs | 151 / 144 / 42,520 |
| Lifecycle snapshots / current pointers / selection events | 26,539 / 14,646 / 1 |
| Baseline local / UTC / New York | `2026-09-09T17:42:38.325291+02:00` / `2026-09-09T15:42:38.325291Z` / `2026-09-09T11:42:38.325291-04:00` |
| Prospective analysis cutoff | `2026-09-09T15:47:17.341061Z` |
| Latest completed US session | `2026-09-08` |
| Daily-bar ready boundary | `2026-09-08T16:15:00-04:00` |
| Calendar / bar-readiness version | `swinglens-us-equities-v1` / `daily-close-plus-15m-v1` |

The production head matched the required 0070 stop gate. Counts taken after the 1,491-ticker scan were identical: 151 runs, 144 pipelines, 42,520 jobs, 4 contexts, 26,539 lifecycle snapshots, 14,646 pointers, and 1 selection event.

### Protected history

The immutable lifecycle hashes recomputed with `immutable_evidence_hash` exactly match the certified report:

| Run | Snapshot hashes | Result |
|---:|---|---|
| 148 | 36934 `5c025126…`; 36935 `29d2c741…`; 36936 `5176e290…`; 36937 `3d116499…`; 36938 `fddd1f8c…` | unchanged |
| 149 | 36939 `59eb7793…`; 36940 `2c93ebb8…`; 36941 `4b6433d9…`; 36942 `0cf54cb4…`; 36943 `d7630cab…` | unchanged |
| 150 | 36944 `1f0059af…`; 36945 `55bf5f74…`; 36946 `84e630ed…`; 36947 `89deaf1e…`; 36948 `c6892585…` | unchanged |
| 151 | 36949 `18edc2ad7695aa3bd154295fa01f2a6550db62f72d457621f84b981ee43fcbfa` | unchanged |

CERI snapshot 10929 retains full-row hash `75971daad3f89f7f3500d616346b43d4217edd75482f8b4a65f7040dfb883d87` and stored evidence hash `1df165a0cbeba82db61e9545a4f820a6599a687e0b45c7df1cb8f37fdf9a8eb8`. Its cutoff remains `2026-09-09T11:30:53.203249+02:00`, its session `2026-09-08`, and its ticker DRS.

Sources 867459–867465 retain the certified retrieval/ingest interval `11:31:56.393435+02:00` through `11:31:59.263801+02:00`. Current canonical full-row fingerprints are, in ID order: `c2c7aa2d…`, `c80f1829…`, `edbd2b3f…`, `cb8ade0c…`, `714b6af8…`, `1b4b80c4…`, `ec57a365…`. Bars 2263479 and 2263504 remain DRS/2026-09-08, unrevised, with first-seen times `11:31:04.697382+02:00` and `11:31:07.168191+02:00`; their full-row fingerprints are `9511dcbe…` and `8ff9389d…`. No protected row was changed.

## C. Candidate-discovery dependency model

### Actual code path

1. `prospective_pipeline_market_context` constructs an in-memory MarketClock cutoff (`market_calculation_context_service.py:199-207`).
2. `SetupLifecycleSourceLoader.load_run_context` loads a completed upload, filters daily bars by session/`first_seen_at`/`revised_at`, and filters market/sector versions by `calculation_cutoff_at` (`source_loader.py:87-219`, `437-452`, `502-508`, `619-666`).
3. `SetupLifecycleSnapshotBuilder` derives the key date from the latest eligible bar and computes the four required sources: technical score, setup score, classification, and close (`snapshot_builder.py:28-35`, `121-180`, `446-484`).
4. Candidate lookup reads current-selection rows created by the prospective cutoff and obtains both the latest ticker pointer and the exact `(ticker,timeframe,data_as_of_date)` pointer (`transition_candidate_service.py:92-124`).
5. The transient candidate competes only with the exact pointer. Production canonical precedence is: completed daily bar, source-pipeline success, required-feature coverage, market/sector context completeness, calculated time, ID (`canonicalization.py:195-218`).
6. HIGH additionally requires exact technical `calculation_cutoff_at == prospective.cutoff_at`, exact `input_as_of_session`, 100% required coverage, no fatal warning, and a deterministic canonical win (`transition_candidate_service.py:131-180`, `233-256`).

The selection key is exactly `(ticker, timeframe, data_as_of_date)`. A later ticker date is a different independent key, not an advance of an older key.

### Input classification

| Input | Consumer | PIT provenance required? | Classification | Missing blocks HIGH? | Why |
|---|---|---:|---|---:|---|
| Prospective MarketCalculationContext | all temporal loaders and candidate result | yes | `REQUIRED_FOR_SELECTION` | yes | Defines cutoff, completed session, calendar and bar readiness. |
| Completed upload/raw ticker row | source loader/key identity | yes, through completed-run timestamps | `REQUIRED_FOR_LIFECYCLE_SNAPSHOT` | yes | Supplies ticker and base source identity. |
| Ticker daily price bars | key date, close, completed-bar precedence, trigger reference | yes | `REQUIRED_FOR_SELECTION` | yes | Directly determines key and the first/third canonical terms. |
| Persisted or reconstructed technical result | required feature values, warnings, setup family | yes | `REQUIRED_FOR_SELECTION` | yes | Supplies technical/setup/classification and affects fatal-warning/coverage terms. |
| Previous completed ticker bar | trigger/cross lifecycle evidence | yes | `REQUIRED_FOR_LIFECYCLE_SNAPSHOT` | no, unless its absence removes a required feature | Snapshot evidence may degrade without changing the pointer decision. |
| Market-regime snapshot | context fields and canonical context-complete term | yes if present | `REQUIRED_FOR_SELECTION` | conditional | Absence is allowed, but proven presence changes canonical precedence. |
| Sector-rotation snapshot and matching row | context fields and canonical context-complete term | yes if present | `REQUIRED_FOR_SELECTION` | conditional | Same as market context; it is not an unconditional completeness gate. |
| Fundamental score | promoted/evidence fields | no independent candidate receipt test | `REQUIRED_FOR_LIFECYCLE_SNAPSHOT` | no | Builder records a warning, but canonical success/coverage does not require it. |
| Combined result | promoted/evidence fields and fallback values | no independent candidate receipt test | `REQUIRED_FOR_LIFECYCLE_SNAPSHOT` | conditional | It matters only if used as fallback for a required value. |
| Ranking results | display/profile evidence | no | `OPTIONAL` | no | Not referenced by canonical precedence or HIGH completeness. |
| SPY/QQQ/IWM benchmark bars | upstream technical computation | yes | `REQUIRED_FOR_PIPELINE_BUT_DEGRADABLE` | no by itself | Missing benchmark data can degrade technical output; HIGH blocks only if required output becomes incomplete. |
| Sector ETF bars | upstream sector-relative technical computation | yes | `REQUIRED_FOR_PIPELINE_BUT_DEGRADABLE` | no by itself | Sector-relative features degrade; zero stored sector ETF bars is not itself a pointer veto. |
| CERI source records | downstream CERI pipeline | yes | `NOT_RELEVANT_TO_POINTER_DECISION` | no | Candidate/lifecycle canonicalization does not read CERI. |
| CERI price-response bars/features | downstream CERI pipeline | yes | `NOT_RELEVANT_TO_POINTER_DECISION` | no | They cannot reject a lifecycle pointer transition. |
| Provider fetch availability | upstream enrichment | yes for data used | `REQUIRED_FOR_PIPELINE_BUT_DEGRADABLE` | no by itself | A provider miss matters only if it prevents required selection output. |

No current LOW candidate is rejected solely because an OPTIONAL or `REQUIRED_FOR_PIPELINE_BUT_DEGRADABLE` input is absent. The failing temporal equality applies to a required technical result.

## D. TBLA

| Field | Value |
|---|---|
| Current pointer key / snapshot | `TBLA/1d/2026-08-03` / 18885 |
| Current pointer session | `2026-08-03` |
| Prospective cutoff / latest completed session | `2026-09-09T15:47:17.341061Z` / `2026-09-08` |
| Latest reconstructable / prospective PIT session | `2026-08-03` |
| Same exact key | YES |
| Diagnostic canonical win if PIT completeness were assumed | YES |
| Certified predicted advance | NO |
| Confidence / reason | LOW / `PIT_INPUTS_INCOMPLETE_OR_NOT_PROSPECTIVELY_RECONSTRUCTED` |

| Source | IDs | Missing/incomplete provenance | Existed by cutoff? | Receipt unprovable? | Required for selection? | Degradable? | Exact effect |
|---|---|---|---:|---:|---:|---:|---|
| Raw/fundamental/combined/ranking | raw 8475; fundamental 23726; combined 15878; rankings 13923, 14292, 14112, 14481, 13801 | none used by HIGH gate | yes | no | no, except conditional fallbacks | yes | Does not block. |
| Technical | 13123 | `calculation_cutoff_at=NULL`, `input_as_of_session=NULL`, context/calendar absent | yes | exact input world unprovable | yes | no | Primary legacy gap; cannot equal prospective context. |
| Latest/previous bars | TRADES 1786650/1786649; ADJUSTED_LAST 1786648/1786647 | none; `first_seen_at` present, `revised_at=NULL` | yes | no | yes | no | Eligible and not blocking. |
| Market context | snapshot 20, as-of 2026-07-31 | legacy `calculation_cutoff_at/context/calendar=NULL`; loader excludes it | yes | calculation receipt unprovable | conditional canonical input | absence allowed | Coverage gap, but both candidate and pointer remain context-incomplete; not the HIGH veto. |
| Sector context | snapshot 19, as-of 2026-08-04 | no eligible version at candidate date 2026-08-03 | no applicable state | no | conditional canonical input | absence allowed | Genuine PIT absence; not the HIGH veto. |

## E. NNI

| Field | Value |
|---|---|
| Current pointer key / snapshot | `NNI/1d/2026-08-03` / 18927 |
| Prospective cutoff / latest completed session | `2026-09-09T15:47:17.341061Z` / `2026-09-08` |
| Latest reconstructable / prospective PIT session | `2026-08-03` |
| Same exact key / diagnostic win / certified advance | YES / YES / NO |
| Confidence / reason | LOW / `PIT_INPUTS_INCOMPLETE_OR_NOT_PROSPECTIVELY_RECONSTRUCTED` |

NNI has the same blocker shape as TBLA. Raw 8517, fundamental 23768, combined 15940, rankings 14138/13961/14333/14563/13850, and bars 1792808/1792807 (TRADES) plus 1792798/1792797 (ADJUSTED_LAST) existed by the cutoff. The bars have proven `first_seen_at` and no revision. Technical 13165 exists but has null cutoff, session, context and calendar provenance. Market snapshot 20 is excluded for missing legacy calculation provenance; sector snapshot 19 is dated after the candidate key. Only the required technical proof blocks HIGH.

## F. TPL

| Field | Value |
|---|---|
| Current pointer key / snapshot | `TPL/1d/2026-08-03` / 18961 |
| Prospective cutoff / latest completed session | `2026-09-09T15:47:17.341061Z` / `2026-09-08` |
| Latest reconstructable / prospective PIT session | `2026-08-03` |
| Same exact key / diagnostic win / certified advance | YES / YES / NO |
| Confidence / reason | LOW / `PIT_INPUTS_INCOMPLETE_OR_NOT_PROSPECTIVELY_RECONSTRUCTED` |

TPL also has the same primary blocker. Raw 8551, fundamental 23802, combined 15897, rankings 14493/14322/13969/14190/13833, and bars 1794358/1794357 (TRADES) plus 1794351/1794350 (ADJUSTED_LAST) existed by cutoff. Technical 13199 lacks cutoff/session/context/calendar provenance. Market snapshot 20 is legacy-unprovable and sector snapshot 19 is not applicable to the 2026-08-03 key. The price evidence is PIT-complete; the required technical reconstruction is not.

## G. DRS

DRS reconfirms `same exact selection key = NO` and primary classification `NEW_KEY_INITIALIZATION_NOT_TRANSITION_COVERAGE`.

The latest current pointer across DRS keys remains snapshot 19017 at `DRS/1d/2026-08-03`. There is also current pointer 14646 to Run-151 snapshot 36949 at `DRS/1d/2026-07-30`. At the new prospective cutoff, bars 2263479 and 2263504 are now eligible because their first-seen times precede the analysis cutoff. The loader therefore derives `DRS/1d/2026-09-08`. No current pointer exists for that exact key, so any write would initialize a third key rather than advance an existing selection.

Technical score 33635 is complete for historical context 4 (`2026-09-09T11:30:53.203249+02:00`, session 2026-09-08), but it does not equal the new prospective cutoff. This is a secondary completeness mismatch; the selection-key mismatch independently and correctly prevents transition coverage.

## H. Blocker taxonomy / `STI-READINESS-F001+`

| ID | Evidence | Taxonomy | Primary class | Blocks next canary? |
|---|---|---|---|---:|
| `STI-READINESS-F001` | 23,866/23,882 technical rows, including TBLA/NNI/TPL, lack cutoff/session provenance | `PIT_VERSION_HISTORY_MISSING` | B — Legacy provenance gap | yes for affected candidate replay |
| `STI-READINESS-F002` | All 1,470 universe tickers with a technical row fail exact equality to the newly generated prospective cutoff; enqueue generates another cutoff | `OTHER_PROVEN` | C — Candidate overconstraint | yes |
| `STI-READINESS-F003` | Run-76 market snapshot 20 exists but lacks calculation cutoff/context/calendar | `LEGACY_RECEIPT_PROVENANCE_MISSING` | B — Legacy provenance gap | no by itself |
| `STI-READINESS-F004` | Run-76 sector snapshot 19 is as-of 2026-08-04, later than the 2026-08-03 candidate keys | `DATA_DID_NOT_EXIST_AT_CUTOFF` | A — Genuine PIT absence | no by itself |
| `STI-READINESS-F005` | DRS prospective key is 2026-09-08; current DRS keys are 2026-08-03 and 2026-07-30 | `SELECTION_KEY_MISMATCH` | D — Correct non-transition | yes for DRS |
| `STI-READINESS-F006` | 939/1,491 universe candidates have no exact current pointer | `SELECTION_KEY_MISMATCH` | D — Correct non-transition | yes for those tickers |
| `STI-READINESS-F007` | 210/552 same-key candidates still do not outrank their exact pointer after required-input completeness is established | `PROSPECTIVE_STATE_NOT_BETTER` | D — Correct non-transition | yes for those tickers |
| `STI-READINESS-F008` | 6,599/39,406 revised price rows lack a complete one-ledger-row-per-revision history | `PIT_VERSION_HISTORY_MISSING` | B — Legacy provenance gap | only for reconstruction before their current revision |
| `STI-READINESS-F009` | Configured sector ETF bar denominator is 0 | `DATA_DID_NOT_EXIST_AT_CUTOFF` | A — Genuine PIT absence | no; upstream technical input is degradable |
| `STI-READINESS-F010` | 5,792/5,792 CERI price-response rows have null 0070 context fields | `PIT_VERSION_HISTORY_MISSING` | B — Legacy provenance gap | no; not used by pointer selection |
| `STI-READINESS-F011` | OPTIONAL/degradable-only rejection count is zero | `OTHER_PROVEN` | D — audit pass | no |
| `STI-READINESS-F012` | FIBK is the sole same-key row with missing technical/setup/classification required inputs | `DATA_DID_NOT_EXIST_AT_CUTOFF` | A — Genuine PIT absence | yes for FIBK |

No `PRICE_BAR_FIRST_SEEN_MISSING`, `PRICE_BAR_REVISION_PROVENANCE_MISSING` at the current prospective cutoff, `SOURCE_RECEIPT_PROVENANCE_MISSING`, `CURRENT_STATE_ONLY_RECORD`, `CURRENT_POINTER_ALREADY_NEWER`, or `PROVIDER_DEPENDENT_UNRESOLVED` blocker was found for TBLA, NNI, or TPL. Unknown historical versions remain unknown; they were not reconstructed from current state.

## I. Genuine absence vs legacy provenance

The sector context for the three Run-76 candidates is genuine absence: the only run-scoped sector snapshot is dated 2026-08-04 and cannot be moved back to their 2026-08-03 key. The market context and technical score are legacy gaps: rows existed, but their calculation cutoff/session cannot be proven. This distinction does not change the immediate HIGH result because the sector/market absence is accepted by the HIGH completeness function, while the technical mismatch is not.

The current price-bar tables have receipt stamps, but their revision ledger is incomplete for legacy revisions. The certified policy therefore remains: a current row revised after a historical cutoff is excluded unless a separately certified version can reconstruct it. The presence of a current value or partial revision audit is not enough.

## J. 0070-era provenance accumulation

`Will PIT completeness naturally improve as new post-0070 sessions/data arrive? YES, for persisted data coverage; NO, by itself, for pre-enqueue HIGH candidate readiness.`

Code inspection confirms:

- CERI providers stamp `retrieved_at`; persistence supplies `ingested_at`; source known-at remains `retrieved_at`, else `ingested_at`.
- New bars receive `first_seen_at`; changed current rows receive `revised_at`, increment `revision_count`, and append `price_bar_revisions` rows (`bar_cache_service.py:141-190`, `280-320`).
- Technical, market-regime, sector-rotation, lifecycle and CERI price-response paths persist the supplied frozen cutoff/context/calendar.
- Price revision history is preserved for new changes, but legacy history is incomplete and the current CERI path intentionally fails closed rather than reconstructing an uncertified overwritten value.

All observed business data predates the 0070 production application. The 0069 backup at `2026-09-09T14:39:00Z` proves the cutover was later; the latest observed bar/source/price-response timestamps are around `09:31–09:32Z`. Thus the actual 0070-era denominator is zero, not a fabricated percentage.

| Domain | `LEGACY_PRE_0070` PIT-complete | `0070_ERA` PIT-complete | Interpretation |
|---|---:|---:|---|
| Daily price bars at analysis cutoff | 2,262,432 / 2,262,432 | 0 / 0 | Receipt/revision stamps are complete for current values; no new era row yet. |
| Technical scores | 16 / 23,882 | 0 / 0 | Almost all derived artifacts are legacy-unstamped. |
| Market-regime snapshots | 4 / 92 | 0 / 0 | Four frozen-context rows exist, all before 0070. |
| Sector-rotation snapshots | 4 / 91 | 0 / 0 | Same pattern. |
| CERI source records | 867,461 / 867,461 | 0 / 0 | Receipt provenance is complete, all pre-0070. |
| CERI price-response features | 0 / 5,792 | 0 / 0 | 0070 columns correctly remain null on legacy rows. |
| Distinct CERI-referenced price bars at analysis cutoff | 10,992 / 10,992 | 0 / 0 | Current-cutoff bar provenance complete; historical eligibility is cutoff-specific. |

Natural accumulation will eliminate missing context on new technical/market/sector/CERI-derived rows and keep new bar/source receipts. It cannot make an already-stored score equal a prospective cutoff created later, nor can it force a new-key initialization to become a same-key advance.

## K. Broader universe scan

Universe definition: every distinct normalized ticker appearing in a completed upload, evaluated from its latest completed occurrence. This yields 1,491 tickers, exactly matching the 1,491 distinct tickers represented by current selection pointers. Each run context was loaded once under the fixed prospective cutoff and each selected ticker passed through the production loader, builder, pointer lookup and assessment service in a repeatable-read, read-only transaction.

| Metric | Count |
|---|---:|
| Total tickers scanned | 1,491 |
| Same-key candidates (exact pointer exists) | 552 |
| New-key initializations | 939 |
| PIT-complete candidates | 0 |
| PIT-incomplete candidates | 1,491 |
| Predicted pointer advances | 0 |
| Diagnostic canonical wins if completeness were assumed | 341 |
| HIGH / MEDIUM / LOW | 0 / 0 / 1,491 |

### Readiness tiers

| Tier | Count |
|---|---:|
| `READY_HIGH` | 0 |
| `READY_MEDIUM` | 0 |
| `BLOCKED_GENUINE_PIT_ABSENCE` | 1 |
| `BLOCKED_LEGACY_PROVENANCE` | 341 |
| `BLOCKED_SELECTION_KEY` | 939 |
| `BLOCKED_POINTER_ALREADY_NEWER` | 0 |
| `BLOCKED_NOT_BETTER` | 210 |
| `BLOCKED_OVERCONSTRAINT` | 0 primary; systemic secondary blocker |
| `BLOCKED_PROVIDER_STATE` | 0 |
| `UNRESOLVED` | 0 |

For primary-tier assignment, exact key mismatch wins first. Among the 552 same-key rows, FIBK lacks required selection inputs, 210 complete-shape candidates cannot win canonical precedence, and the remaining 341 would win but have legacy technical scores with null cutoff/session. The systemic cutoff-rendezvous defect is secondary for those 341 and has zero primary rows in the current universe.

### PIT coverage metrics

| Metric | Numerator / denominator | Coverage | Denominator definition |
|---|---:|---:|---|
| Technical ticker price-bar PIT provenance | 2,259,240 / 2,259,240 | 100.000% | Daily TRADES/ADJUSTED_LAST rows through 2026-09-08 for the 1,491 universe tickers; eligible current value at analysis cutoff. |
| Broad benchmark PIT provenance | 3,192 / 3,192 | 100.000% | Daily rows through 2026-09-08 for configured market inputs SPY, QQQ and IWM. |
| Sector ETF PIT provenance | 0 / 0 | N/A | Daily rows through 2026-09-08 for XLB/XLC/XLE/XLF/XLI/XLK/XLP/XLRE/XLU/XLV/XLY; no rows exist. |
| Lifecycle source PIT provenance | 8 / 183 | 4.372% | Market-regime plus sector-rotation snapshot rows with cutoff, context ID and calendar version. |
| CERI source PIT provenance | 867,461 / 867,461 | 100.000% | All CERI source rows with certified known-at available via retrieved-at else ingested-at. |
| CERI price-bar PIT provenance | 10,992 / 10,992 | 100.000% | Distinct bar IDs referenced by all CERI price-response rows, eligible at the analysis cutoff as current values. |
| CERI price-response 0070 context provenance | 0 / 5,792 | 0.000% | All price-response rows with cutoff, context ID and calendar version. |

These are provenance metrics, not assertions that every row was knowable at every earlier cutoff.

## L. HIGH candidate shortlist

No HIGH candidates exist, so the shortlist is empty. False-positive control is vacuous: no ticker is claimed HIGH without an existing exact pointer, deterministic outrank, required/lifecycle PIT completeness, absence of post-cutoff evidence, and exclusion of new-key initialization.

## M. Overconstraint audit

The candidate does **not** over-require missing market context, sector context, ranking data, benchmark data, sector ETF data, or CERI data as unconditional HIGH gates. Market/sector presence is legitimately part of canonical precedence, while absence can still be a complete PIT state. The four required feature values and fatal-warning set match production canonical inputs and must not be weakened.

The confirmed overconstraint is the way technical proof is obtained:

- `transition_candidate_service.py:233-256` requires the stored technical row's cutoff to equal the new prospective cutoff byte-for-byte.
- `market_calculation_context_service.py:199-207` creates that cutoff from `datetime.now(UTC)`.
- `pipeline_service.py:173-185` later creates the pipeline and calls `create_pipeline_market_context` without accepting the assessed cutoff; that function takes another wall-clock instant.
- The technical result is selection-critical, but exact equality to a future independently generated enqueue instant is not the only valid proof. A deterministic read-only recomputation or an artifact with the same certified input signature/session/calendar can prove the prospective outputs without pretending that an old timestamp equals the new cutoff.

### Narrow remediation design

1. Capture one prospective `MarketCalculationCutoff` for preflight and make enqueue persist that exact object after revalidation, rather than deriving a second time.
2. Add a read-only technical preview/reconstruction path under that context. It must query bars with the certified first-seen/revision predicates and use the same technical artifact input signatures/config versions as production.
3. Accept HIGH only when the preview proves all four required lifecycle values, the exact selection key, and the production canonical win. If source versions change between preview and enqueue, fail closed and rediscover.
4. Continue treating absent optional/degradable inputs as degradation, unless their absence actually changes a required output or canonical context-complete term.

Required tests: shared preflight/enqueue cutoff identity; read-only discovery with zero flush/write; identical input-signature reuse and changed-signature rejection; session-boundary and bar-revision race fail-closed tests; optional/degradable-only absence does not veto a genuine win; DRS/new-key remains LOW; current-pointer better remains LOW; post-cutoff source/bar evidence remains excluded.

This defect blocks the next canary. It does not justify changing the pipeline's PIT filters.

### False-negative sample

IRWD (technical 383/bar 125198), HOOD (385/128206), TATT (403/153386), FBIO (406/157122), and JRSH (416/119182) are same-key LOW examples. Each has 100% required feature coverage and only stale/optional-context warnings, but each technical score has null cutoff/session provenance and cannot match the prospective context. They are therefore not rejected solely because OPTIONAL or degradable data is absent.

## N. READY_WHEN conditions

```text
TBLA READY_WHEN:
one frozen preflight context is shared with enqueue; a read-only technical reconstruction under that exact context proves complete required outputs for 2026-08-03; the exact pointer 18885 still exists; and the transient candidate deterministically outranks it.

NNI READY_WHEN:
one frozen preflight context is shared with enqueue; a read-only technical reconstruction under that exact context proves complete required outputs for 2026-08-03; the exact pointer 18927 still exists; and the transient candidate deterministically outranks it.

TPL READY_WHEN:
one frozen preflight context is shared with enqueue; a read-only technical reconstruction under that exact context proves complete required outputs for 2026-08-03; the exact pointer 18961 still exists; and the transient candidate deterministically outranks it.

DRS READY_WHEN:
a natural run first initializes a current pointer for the then-prospective DRS key (currently 2026-09-08), and before the key advances again a later shared-context, PIT-complete reconstruction for that same exact key deterministically displaces the initialized pointer.

OTHER SAME-KEY READY_WHEN:
after the shared-context/read-only technical preview remediation, required source signatures remain unchanged through enqueue and the prospective candidate outranks the exact pointer. A new completed session alone is insufficient if it creates a new key.
```

## O. Code-change recommendation

```text
CODE_REMEDIATION_NEEDED_CANDIDATE_OVERCONSTRAINT
```

Remediate only the preflight context handoff and selection-critical technical reconstruction proof. Do not backfill legacy rows, invent receipt times, accept current-state-only data, or remove any certified source/bar cutoff predicate.

## P. Final recommendation

Do not run the final targeted transition canary now. First implement and test the narrow candidate-discovery remediation in a separate authorized task. Then wait for discovery to produce a genuine same-key deterministic HIGH result under the exact context enqueue will persist. DRS or any other new-key initialization remains ineligible as transition coverage.

No recommendation relies on `bar_date` as known-at, `published_at` as receipt, `observed_at` as receipt, `created_at` as receipt, fabricated `first_seen_at`, or fabricated historical receipt timestamps. Unknown remains unknown.

```text
Production business writes: NO
Pipeline enqueued: NO
Canary run: NO
Merge performed: NO
```
