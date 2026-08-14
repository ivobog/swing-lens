# Run 104 Winner Evidence forensic certification

- Audit date: 2026-08-14
- Repository: SwingLens
- Audited run: 104
Final verdict: **REJECT**

## 1. Executive certification verdict

Run 104 Winner Evidence is not certifiable as decision-time evidence.

The first and highest-severity failure is temporal. The capture occurred at
`2026-08-14 04:34:21.268503 Europe/Zurich`, which is
`2026-08-13 22:34:21.268503 America/New_York`. The latest completed U.S.
regular trading session was therefore Thursday 2026-08-13 and the next
regular-session open was Friday 2026-08-14. The persisted prediction date
(`2026-08-14`) and entry date (`2026-08-17`) are wrong. The pre-fix sector
snapshot used a persistence timestamp's calendar date and the Winner feature
extractor preferred that date over the market-session date.

The evidence population independently fails. There are 8,673 historical
predictions before Run 104's cutoff and 3,509 exact five-session `NEXT_OPEN`
outcomes old enough to mature. The production cohort query goes from 3,509 to
zero at `WinnerForwardOutcome.status = MATURED`. Every current five-session
`NEXT_OPEN` row is still `PENDING`. A bounded, manually invoked maturation
queue processed shorter horizons but never reached this target, and the
repository has no automatic enqueue/scheduling path for maturation or cohort
refresh.

The current API also presented prior-only interval width, evidence state, and
cohort-attempt metadata as if they were an interval, model lifecycle state,
calibration state, and selected cohort. Pagination hid 86 run rows and summary
cards counted only the first page.

The code changes in this audit fix future session derivation, withheld-field
semantics, and pagination/count presentation. They do not rewrite Run 104 or
manufacture historical decision-time evidence. No production database row was
changed.

## 2. Environment and version snapshot

| Item | Audited value |
|---|---|
| Git branch | `codex/ceri-run101-remediation` |
| Git commit at audit start | `3e45cf4b898548e7b8869e1413103c20501fdf15` |
| Database | PostgreSQL 18.3, database `swinglens` |
| Schema revision | `0045_ceri_changes_alerts_semantics` |
| Application timezone | Europe/Zurich / Windows `W. Europe` |
| Worker | enabled |
| Winner capture in pipeline | enabled |
| Run | 104, `money money_2026-08-14.csv`, `COMPLETED`, 186 rows |
| Uploaded / processed | 2026-08-14 04:32:34.862069+02 / 04:32:35.805556+02 |
| Winner source cutoff | 2026-08-14 04:34:21.268503+02 |
| Prediction IDs | 8674 through 8859 |
| Alembic / test data mutation | none |

The SRS and SDD supplied with the audit were treated as the functional and
design authority. The supplied JSON, Markdown investigations, live database,
API service, templates, job ledger, and source code were treated as
implementation evidence.

## 3. UI-to-API reconciliation

The supplied JSON was read in full. Its first page contains exactly 100 items,
starts with AEIS prediction 8848, ends with ITT prediction 8779, and returns
`next_cursor = "8779"`. Across all 100 supplied estimates:

| Field | Uniform supplied value |
|---|---|
| point probability / bounds | null / null / null |
| sample n / effective n | 0 / 0 |
| grade / reason | `Insufficient` / `no_eligible_cohort` |
| interval width | 0.43827 |
| source | `INSUFFICIENT` |
| cohort / model foreign key | null / null |

The visible fields in the supplied UI capture reconcile to those 100 JSON
items. The reconciliation was incomplete only because both artifacts stopped
at the API page boundary.

Following the cursor with the live service produced:

```text
page 1: 100 rows, next_cursor=8779
page 2:  86 rows, next_cursor=null
unique prediction rows: 186
```

The two predictions without an estimate are MIAX 8721 and MOG.A 8798; both
were excluded for `insufficient_completed_bars`. Thus the exact count
semantics are:

| Count | Value |
|---|---:|
| page rows | 100 |
| all filtered rows | 186 |
| run total | 186 |
| estimate total | 184 |
| calibrated total | 0 |
| insufficient estimate total | 184 |
| rows missing an estimate | 2 |

**Finding F-06 — CONFIRMED_DEFECT, P1.** The pre-fix UI route did not accept a
cursor, the template had no pagination link, and `_run_evidence_summary`
computed every card from `payload.items`. It therefore reported 100 rows and
100 estimates for a run containing 186 predictions and 184 estimates.

The repair adds page-independent `counts` in
`app/services/winner_probability/api_service.py`, uses those totals in
`app/routers/winner_probability_routes.py`, and exposes `Next page` in
`app/templates/winner_probability_run.html`.

## 4. Session, timezone, and cutoff verdict

### Approved-calendar proof

`app/services/us_market_calendar.py:7-16` defines New York time and a 16:15
daily-bar readiness threshold. An aware timestamp is converted to
`America/New_York`; before readiness it selects the previous U.S. trading day.
`next_us_trading_day` is at line 42.

Deterministic reproduction:

```python
cutoff = datetime(2026, 8, 14, 4, 34, 21,
                  tzinfo=ZoneInfo("Europe/Zurich"))
completed = latest_completed_us_trading_day(cutoff)
assert completed == date(2026, 8, 13)
assert next_us_trading_day(completed) == date(2026, 8, 14)
```

The actual source data agrees:

| Source | ID | as-of / latest bar | created / visible by cutoff |
|---|---:|---|---|
| market regime snapshot | 49 | 2026-08-13 | 2026-08-14 04:34:17+02 |
| sector rotation snapshot | 48 | **2026-08-14** | 2026-08-14 04:34:21+02 |
| AEIS daily PriceBar | — | 2026-08-13 | yes |
| CR daily PriceBar | — | 2026-08-13 | yes |
| CSWC daily PriceBar | — | 2026-08-13 | yes |
| FORM daily PriceBar | — | 2026-08-13 | yes |
| ITT daily PriceBar | — | 2026-08-13 | yes |

There was no 2026-08-14 daily bar at the cutoff for any mandatory trace.

### How the wrong dates were derived

At the audited commit, `sector_rotation_service._resolve_as_of_date`
(`app/services/sector_rotation_service.py`, pre-fix lines 396-413) returned
`latest_technical.created_at.date()`, then `uploaded_at.date()`, then
`date.today()`. At 04:34 Zurich this produced 2026-08-14. The pre-fix
`feature_extractor._prediction_as_of_date` (lines 273-286) preferred sector
snapshot date over market snapshot date, then used upload/capture `.date()`
fallbacks. `_planned_entry_session` correctly advanced that already-wrong
Friday date over the weekend to Monday 2026-08-17.

**Finding F-01 — CONFIRMED_DEFECT, P0.** This is a session/timezone defect, not
a harmless date label. It shifts the signal session forward one day and the
executable entry forward one session, corrupting episode dates, outcome entry
and due dates, cohort features containing the date, deduplication identity,
and all downstream point-in-time reasoning.

The minimal future-facing repair is at
`feature_extractor.py:99-105,137-140` and
`sector_rotation_service.py:403-414`: both now use the approved U.S. calendar,
and capture warns when a context snapshot date lies after the completed signal
session. The exact Run 104 instant, a weekend, the observed Independence Day
holiday, and the U.S./Europe DST-gap boundary are regression fixtures in
`tests/test_us_market_calendar.py` and
`tests/winner_probability/test_capture_service.py`.

Answer: **Run 104 actually belongs to completed U.S. session 2026-08-13.
2026-08-17 is not the correct executable next-open entry; 2026-08-14 is.**

## 5. Historical evidence funnel

This SQL shape reproduces the production join and removes one predicate at a
time. `cutoff` is Run 104's minimum source cutoff and `completed_session` is the
proven 2026-08-13 session.

```sql
WITH params AS (
  SELECT min(source_data_cutoff_at) AS cutoff,
         date '2026-08-13' AS completed_session
  FROM winner_prediction_snapshots WHERE run_id = 104
), hist AS (
  SELECT p.*
  FROM winner_prediction_snapshots p, params x
  WHERE p.source_data_cutoff_at < x.cutoff
    AND p.superseded_at IS NULL
), joined AS (
  SELECT h.*, f.id AS f_id, f.status AS f_status, f.matured_at,
         f.due_session, t.id AS t_id, t.status AS t_status, t.evaluated_at
  FROM hist h
  LEFT JOIN winner_forward_outcomes f
    ON f.prediction_id = h.id
   AND f.entry_model = 'NEXT_OPEN'
   AND f.horizon_sessions = 5
   AND f.is_current_revision
  LEFT JOIN winner_target_stop_outcomes t
    ON t.forward_outcome_id = f.id
   AND t.outcome_definition_id = (
     SELECT id FROM winner_outcome_definitions
     WHERE definition_id = 'T2_5_S2_0_H5_NEXT_OPEN' AND is_active
   )
   AND t.is_current_revision
)
SELECT ... FILTER (WHERE each_predicate) FROM joined;
```

| Stage | Before | After |
|---|---:|---:|
| historical predictions before cutoff | — | 8,673 |
| exact 5-session `NEXT_OPEN` forward row exists | 8,673 | 8,641 |
| due session no later than 2026-08-13 | 8,641 | 3,509 |
| forward outcome is current `MATURED` | 3,509 | **0** |
| forward `matured_at < cutoff` | 0 | 0 |
| matching target/stop row is `MATURED` | 0 | 0 |
| target/stop `evaluated_at < cutoff` | 0 | 0 |
| point-in-time valid | 0 | 0 |
| production-training allowed | 0 | 0 |
| quality/config/schema/label compatible | 0 | 0 |
| independent episode representative | 0 | 0 |
| rolling-window eligible | 0 | 0 |
| L5 raw / effective n | 0 | 0 / 0 |
| persisted L5 statistic | 0 | 0 |
| display eligible | 0 | 0 |
| served probability | 0 | null (`no_eligible_cohort`) |

The 3,509 old-enough rows were separately checked before the broken stage:

```text
eligibility_status=ELIGIBLE                  3509
lineage point_in_time_validated=true         3509
native (reconstruction_method is null)       3509
non-dependent                                2042
distinct independent episodes               2042
production_training_allowed=true                0
```

All 3,509 share feature schema `owpe-features-1.0.0`, calculation version
`owpe-calc-1.0.0`, one configuration hash, native lineage, and eligible status.

## 6. First broken stage

**Finding F-02 — CONFIRMED_DEFECT, P0.** Historical evidence first goes from a
positive population to zero at the exact five-session `NEXT_OPEN` forward
outcome maturity predicate.

`EvidenceService.load_evidence` enforces this at
`app/services/winner_probability/evidence_service.py:57-73`. Database status
distribution proves that all 8,825 current five-session `NEXT_OPEN` rows are
`PENDING`; none is `MATURED`. In contrast, the ledger contains maturation of
shorter horizons and diagnostic labels:

```text
NEXT_OPEN H1: 1686 MATURED
NEXT_OPEN H3: 1066 MATURED
NEXT_OPEN H5:    0 MATURED, 8825 PENDING
SIGNAL_CLOSE H5: 275 MATURED
```

The last maturation job was background job 15900, `PARTIAL`, created
2026-08-10 22:52:11+02 and completed 22:55:07+02:

```json
{"processed":500,"matured":330,"pending":170,
 "target_stop_matured":275,"warnings":330,"failed":0}
```

There is no later job. Source search finds enqueue calls only in the local-admin
POST routes at `winner_probability_routes.py:561-619`; no recurring or
post-session scheduler exists. The bounded queue orders never-attempted rows by
due session and ID (`outcome_service.py:367-399`), so the manually submitted
500-row batches were still consuming shorter-horizon backlog and never reached
the primary H5 `NEXT_OPEN` label.

This is not a statistical cold start. It is an unprocessed label pipeline.

## 7. L0-L5 trace

The production cohort service was executed read-only for each mandatory
prediction at Run 104's training cutoff. Every level returned raw n=0 and
effective n=0 because the SQL maturity stage had already eliminated the global
population.

| Ticker / prediction | L0 key | L1 | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|---|
| AEIS 8848 | `L0:e0427199781191ad5a23a3f7d3fd731ad63ab3758e16fe3c8769a2c36a451eb4` | `L1:3169ef8c...` | `L2:0b525df1...` | `L3:2e3f82c7...` | `L4:045bbb79...` | `L5:aa19550e1b53b14d29315fdbe5204f23bb9c5dd03c89dbfd8313b3988b0c04ba` |
| CR 8837 | same as AEIS | same | same | same | same | same |
| CSWC 8752 | `L0:6b55cc3260486a3affea79d4d1b590906f8f08ba07abc5a28d54cd9e170b03ac` | `L1:1d14b7b8...` | `L2:e81619b7...` | `L3:e8165388...` | `L4:045bbb79...` | same global L5 |
| FORM 8692 | `L0:b4d7ae11a78292c61dff8ee8b8f5c82646c4fa277678dd605140b74970f91a23` | `L1:adbc808e...` | `L2:be38399e...` | `L3:6f721c3a...` | `L4:5f671a42...` | same global L5 |
| ITT 8779 | `L0:8feacae6058c0c76022d7e8472dba5df424ead30b3e60926862c2df4020a3069` | `L1:1d14b7b8...` | `L2:e81619b7...` | `L3:e8165388...` | `L4:045bbb79...` | same global L5 |

The exact L5 dimensions are `{"global":"all"}`. The database contains zero
`winner_cohort_definitions`, zero `winner_cohort_statistics`, and zero
`winner_estimate_evidence_members`.

**Finding F-05c — CONTRACT_AMBIGUITY, P1.** Stored `cohort_level=L5` was the
last attempted level, not a selected cohort. `cohort_definition_id=null` proves
there was no selected or persisted definition. The repair stores selected
cohort fields as null and records `attempted_cohort_level=L5` and the attempted
key separately (`probability_estimator.py:351-365`). Legacy rows are normalized
the same way at the API boundary.

## 8. Mandatory prediction traces

The query joins prediction, estimate, and source foreign keys by prediction ID:

```sql
SELECT p.ticker,p.id,e.id,p.raw_row_id,p.combined_result_id,
       p.ranking_result_id,p.market_regime_snapshot_id,
       p.sector_rotation_snapshot_id,p.episode_id,
       p.lineage_json->>'dependent_episode',
       p.lineage_json->>'production_training_allowed',
       e.cohort_definition_id,e.model_version_id,e.evidence_manifest_id
FROM winner_prediction_snapshots p
LEFT JOIN winner_probability_estimates e
  ON e.prediction_id=p.id AND e.estimate_kind='DECISION_TIME'
WHERE p.id IN (8848,8837,8752,8692,8779);
```

| Ticker | Prediction / estimate | raw / combined / technical / fundamental | market / sector snapshot / sector row | episode | dependent | setup | sector | ranking result |
|---|---|---|---|---:|---|---|---|---|
| AEIS | 8848 / 8814 | 17338 / 28685 / 23800 / 50183 | 49 / 48 / 469 | 3345 | true | Avoid / No trade | Risk-off, rank 2 | null |
| CR | 8837 / 8803 | 17327 / 28727 / 23789 / 50172 | 49 / 48 / 469 | 3589 | **false** | Avoid / Distribution risk | Risk-off, rank 2 | null |
| CSWC | 8752 / 8719 | 17242 / 28646 / 23704 / 50087 | 49 / 48 / 471 | 2900 | true | Avoid / Extended momentum | **Lagging**, rank 4 | null |
| FORM | 8692 / 8660 | 17182 / 28599 / 23644 / 50027 | 49 / 48 / 469 | 3169 | true | Candidate / Trend repair | Risk-off, rank 2 | null |
| ITT | 8779 / 8746 | 17269 / 28643 / 23731 / 50114 | 49 / 48 / 473 | 2916 | true | Avoid / Extended momentum | Risk-off, rank 6 | null |

All five estimates point to evidence manifest 1, have zero n, no cohort/model
foreign key, and stored the ghost width and L5 attempt. ITT is a data row, not
an end marker; it happens to be the last row of page one and the cursor token.

## 9. `production_training_allowed` reason analysis

**Finding F-04 — CONFIRMED_DEFECT, P1.** The field is false for all 186 Run 104
predictions and all 8,673 historical predictions before the cutoff, including
the only non-dependent Run 104 row, CR 8837.

Cause: `WinnerPredictionCaptureService.capture_run` declares
`production_training_allowed: bool = False` at `capture_service.py:80-90` and
persists it at lines 263-270. The native pipeline caller at
`pipeline_executor.py:1108-1111` does not override the default. Episode
dependence is assigned only afterward at `capture_service.py:134-139`, so the
flag cannot currently express the observed reason.

This field does not cause today's zero at the maturity stage because the
production evidence query does not test it. That creates a second defect:

**Finding F-03 — CONFIRMED_DEFECT, P0 (latent training-integrity risk).** The
production evidence query filters maturity, timestamps, cohort dimensions,
dependence, reconstruction, and one-per-episode
(`evidence_service.py:52-95`), but does not enforce
`production_training_allowed`, point-in-time validation, configured rolling
windows, data-quality flags, or explicit feature/config/schema/calculation
compatibility. Adding the flag filter alone now would incorrectly eliminate
all history, so that unsafe one-line change was not made.

Required future semantics are a reasoned policy result, for example:

```text
allowed=true only when native + PIT validated + quality compatible
               + label/schema/config/calculation compatible
               + independent representative
reason codes retained even when allowed=false
```

The value must be computed after episode assignment, then enforced by the
evidence query with deterministic tests. Existing rows require a revisioned,
audited classification procedure; a blind boolean update is prohibited.

## 10. Ranking-profile and sector-source analysis

**Finding F-07a — CONFIRMED_EXPECTED_BEHAVIOR, P2.** Run 104 has zero upstream
`ranking_results`; all 186 prediction rows therefore have null
`ranking_result_id` and `ranking_profile`, with `missing_ranking_result` in
warnings. Capture preserved the absence rather than inventing a profile. The
cohort key uses explicit `__MISSING__`, as required for optional features.

**Finding F-07b — CONFIRMED_DEFECT, P2.** The pre-fix run table did not expose
ranking/final rank and score, regime, sector, or warning context even though
the API had enough source IDs to reconstruct most of it. The repaired API
resolves the existing combined/ranking foreign keys and exposes warnings; the
template now renders rank/score, setup, regime/sector, and warnings. No ranking
score was changed.

**Finding F-08 — CONFIRMED_EXPECTED_BEHAVIOR, P3.** Full-run sector-state
distribution is 184 `Risk-off` and 2 `Lagging` (the supplied first page is
99/1). `feature_extractor.py` copies `SectorRotationRow.rotation_state`.
`Risk-off` and `Lagging` are both configured values in the sector-rotation
vocabulary. CSWC's `Lagging` comes from sector row 471 and is not a mapping
error.

## 11. Estimate-field contract analysis

### Ghost interval width

`cohort_statistics.py:39-47` calculates a posterior even for zero observations.
With prior probability 0.5 and prior strength 20, its normal-approximation
interval has width 0.438269..., persisted as 0.438270. The point and bounds
were then deliberately withheld while the width was copied.

**Finding F-05a — CONFIRMED_DEFECT, P1.** An interval width with no estimate or
bounds is not a valid served interval and also contaminated the API's maximum
interval filter. Future insufficient estimates now persist `interval_width`
as null (`probability_estimator.py:335-341`); legacy rows are normalized to
null when point probability is withheld.

### Source, model, calibration, and cohort fields

| Supplied field | Evidence | Classification |
|---|---|---|
| `source=INSUFFICIENT` | `EstimateSource` adds `INSUFFICIENT` and `SIMILARITY` beyond the SDD's proposed source set; it stores evidence state in an estimator-source field | **CONFIRMED_DEFECT, P1** |
| `model_status=INSUFFICIENT` | API fell back to `estimate.source`; this is not a model lifecycle state | **CONFIRMED_DEFECT, P1** |
| `model_version_label=cohort_baseline_v1`, model ID null | source version was presented as a model version even though no model or selected cohort exists | **CONFIRMED_DEFECT, P1** |
| `cohort_level=L5`, definition ID null | L5 was the last attempt, not selected | **CONTRACT_AMBIGUITY, P1** |
| `calibration_status=insufficient`, calculated-at null | point-null shortcut used evidence state as calibration state | **CONFIRMED_DEFECT, P1** |
| manifest ID 1 reused | manifest hash `6375508c...b778620`, member count 0, payload `{"members":[]}`, referenced by 8,825 estimates | **CONFIRMED_EXPECTED_BEHAVIOR, P3**, content-addressed empty-set deduplication |

The API repair returns null source/model fields for a withheld no-model
estimate, `calibration_status=not_applicable`, null selected cohort fields, and
separate attempted cohort metadata (`api_service.py:718-761,892-896`). The
underlying legacy enum and non-null storage contract still need a versioned
schema/API decision; no migration was performed in this audit.

**Finding F-09 — CONTRACT_AMBIGUITY, P2.** The SRS/SDD display threshold is 15,
while `config/winner_probability.yaml:122` says 5, and
`minimum_display_n` is parsed but not consumed by estimation or API display.
The effective L3-L5 selection threshold is 15 from the cohort hierarchy, so it
does not explain Run 104. The config was not silently changed because it is
part of the immutable config hash and needs an explicit versioned decision.

## 12. Pagination and count semantics

The supplied cursor was real and hid 86 additional rows. Cursor ordering is
recomputed from the current result set and identifies the last displayed
prediction ID. It is adequate for this immutable run but is not a database
snapshot cursor; concurrent revisions could make a mutable view unstable.

After the repair, a read-only service call against Run 104 returned:

```python
{
  "page1": 100, "cursor1": "8779",
  "page2": 86,  "cursor2": None,
  "counts": {
    "run_total": 186,
    "filtered_total": 186,
    "filtered_estimate_total": 184,
    "estimate_total": 184,
    "calibrated_total": 0,
    "insufficient_total": 184,
    "missing_estimate_total": 2
  }
}
```

The summary cards are now proven page-independent by
`test_run_summary_uses_full_counts_instead_of_page_length`.

## 13. UI contract matrix

| Required information | Pre-fix run table | Detail/API | Post-fix disposition |
|---|---|---|---|
| rank / score | missing | source FK only / combined score | final/profile rank and ranking score exposed; absence explicit |
| setup | ticker subtext | yes | dedicated column |
| market regime / risk | missing | yes | run column |
| sector state / rank | missing | yes | run column/context |
| probability / lower bound / interval | yes | yes | withheld fields now semantically null |
| evidence grade / effective n | yes | yes | retained |
| return / MFE / MAE / target-first | yes | yes | retained |
| warnings | missing | detail only | run row now lists warnings |
| definition | hero/detail | yes | retained |
| selected cohort/model | misleading aliases | yes | null when absent; attempt separate |
| sample / cutoff / no-evidence reason | yes | yes | retained |
| pagination | missing | cursor present | Next page link added |
| page/filter/run totals | page length only | absent | explicit API counts and cards |

## 14. Confirmed root causes and impact radius

1. **P0 session derivation:** persistence/host calendar dates were used instead
   of the completed U.S. signal session. Impact: all 186 Run 104 prediction
   snapshots, planned entries, episode associations, pending outcome schedules,
   estimates keyed to those predictions, API rows, exports, and UI.
2. **P0 maturation orchestration:** no automatic schedule and insufficient
   bounded-queue draining left every primary H5 `NEXT_OPEN` label pending.
   Impact: every cohort level and all 184 Run 104 decision-time estimates.
3. **P0 latent evidence-policy omissions:** several required production gates
   are not queried. Impact begins as soon as any H5 label matures.
4. **P1 training-allowance default:** native capture always records false.
   Impact: all 8,859 current prediction snapshots observed through Run 104,
   including the independent CR control.
5. **P1 estimate/UI contract leakage:** prior-only width and evidence states
   masqueraded as interval/model/calibration/cohort semantics. Impact: 8,825
   estimates sharing the empty manifest, including 184 in Run 104.
6. **P1 pagination/counts:** 86 Run 104 predictions were inaccessible from the
   run page and summary totals were page counts.

## 15. Minimal fixes implemented

1. Derive prediction session and entry-data due state with the approved U.S.
   calendar; derive sector snapshot as-of from the same utility.
2. Emit a deterministic warning if optional market/sector context is dated
   after the completed signal session.
3. Persist no interval for future insufficient estimates.
4. Distinguish selected cohort from last attempted cohort.
5. Normalize legacy withheld estimates at the API boundary without changing
   their durable rows.
6. Add full-run/filter/estimate/calibrated/insufficient/missing counts,
   cursor-aware UI navigation, and missing UI contract columns.

Not implemented because it requires a policy/schema decision or production
data mutation: job scheduling/draining, training-allowance classification,
enforcement of all evidence gates, enum/schema migration, or Run 104 repair.

## 16. Tests and results

Targeted commands:

```powershell
.\.venv\Scripts\python.exe -m ruff check <changed Python files and tests>
.\.venv\Scripts\python.exe -m pytest `
  tests/winner_probability `
  tests/test_us_market_calendar.py `
  tests/test_sector_rotation_service.py `
  tests/test_sector_rotation_routes.py -q
```

Result:

```text
ruff: all checks passed
pytest: 168 passed, 1 third-party Starlette deprecation warning
```

A repository-wide `pytest -x -q` verification reached 353 passing tests before
stopping at the pre-existing CERI end-to-end certification assertion that its
isolated fixture produced zero user-visible CERI alerts (it expected more than
zero). That failure is outside Winner Evidence and did not exercise a changed
assertion; evidence package:
`test-results/single-run-certification/20260814T190605Z-01a24677`.

The focused fixtures prove the exact Zurich timestamp, weekend/holiday/DST
behavior, corrected next-open entry, no ghost interval, attempted-vs-selected
cohort semantics, page-independent totals, and existing Winner/sector behavior.

## 17. Safe repair/backfill proposal (not executed)

Run 104's original decision-time records must remain immutable. A safe repair
requires explicit approval and a new, audited operation:

1. Freeze a transaction-consistent export of Run 104 predictions, estimates,
   pending outcomes, manifests, and job ledger; record row counts and hashes.
2. Add target-specific maturation job parameters for
   `T2_5_S2_0_H5_NEXT_OPEN`, `NEXT_OPEN`, horizon 5, due through 2026-08-13.
   Dry-run the 3,509-row scope and report missing bars before writing.
3. Drain in deterministic idempotent batches keyed by outcome ID and source-bar
   lineage hash. Never broaden this into 200-ticker recertification.
4. Because those labels were not processed by Run 104's cutoff, do not insert
   them into the immutable original `DECISION_TIME` estimate. A retrospective
   result must be `AS_OF_REPLAY`/reconstructed with reconstruction method,
   processing time, source revision cutoff, and production-training=false
   unless governance explicitly approves reconstructed history.
5. For the session defect, create revision-2 prediction snapshots for only the
   186 Run 104 rows, with signal session 2026-08-13 and entry 2026-08-14;
   supersede rather than update revision 1. Rematerialize children under new
   identities and retain links to the old rows.
6. Compute production-training eligibility after episode assignment and store
   explicit reason codes. Apply the same logic in dry-run to historical rows;
   insert revisions/classification records rather than mass-updating lineage.
7. Rebuild cohort definitions/statistics only from labels that pass every
   production gate. Persist before/after funnel counts and manifest hashes.
8. Make the job scheduler run after each completed U.S. session and continue
   until the due target-specific queue is drained; alert on partial/backlog age.

Every step must be rerunnable without duplicates and must preserve old
decision-time estimates, old revisions, no-look-ahead boundaries, and the
research-only/no-auto-trading boundary.

## 18. Final answers and verdict

| Certification question | Answer |
|---|---|
| What completed U.S. session does Run 104 belong to? | **2026-08-13** |
| Is 2026-08-17 the correct executable next-open entry? | **No. 2026-08-14 is correct.** |
| Where does historical evidence first go positive to zero? | At current five-session `NEXT_OPEN` forward outcome `status=MATURED`: **3,509 → 0**. |
| Why is production training false for every supplied row? | Native capture's default is false and both pipeline callers omit an override; it is unrelated to episode dependence. |
| Why is ranking context absent for every row? | Run 104 has zero upstream `ranking_results`; capture records explicit optional-feature missingness. |
| What is exact L5 state? | Global key `L5:aa19550e...c04ba`, raw/effective n 0/0, no definition, no statistic; L5 was last attempted, not selected. |
| What generates the ghost width? | Prior-only p=0.5, strength=20 interval calculation on empty evidence, copied while point/bounds were withheld. |
| Are source/model/calibration/cohort fields semantically correct? | No pre-fix; the API repair makes absent/attempted states explicit, while durable enum/schema cleanup remains governed work. |
| Does pagination hide rows? | Yes pre-fix: 86 additional rows; page totals were mistaken for run totals. |
| What fixes prove correctness? | U.S.-calendar capture derivation, context-date warning, withheld-field normalization, attempted cohort metadata, explicit counts/cursor UI, and 168 passing targeted tests. |

**Final verdict: REJECT.** Future-facing code defects identified in this audit
are repaired and tested, but Run 104's durable decision-time dates are wrong,
its primary outcomes were not mature at its recorded cutoff, and the production
evidence policy omits mandatory gates. Declaring Run 104 `PASS` would require
rewriting history or treating late-processed labels as decision-time knowledge,
both of which violate the governing specification.
