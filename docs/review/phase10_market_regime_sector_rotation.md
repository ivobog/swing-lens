# Phase 10 Review - Market Regime, Breadth, and Sector Rotation

Date: 2026-08-02
Reviewer: Codex
Scope: market regime command center, benchmark freshness, run-universe participation,
sector taxonomy, sector leadership, sector rotation scoring, ETF confirmation, permissions,
snapshots, exports, dashboard routes, and drill-down views.

## Objective

Validate cross-sectional and market-context overlays without introducing circularity,
survivorship bias, or hidden look-ahead.

## Executive Summary

Phase 10 is partially exit-ready.

The implementation is deterministic from database inputs and versioned configuration, and the
focused regime/rotation test slice is strong. Market regime scoring is explicit, sector scoring is
componentized, sector taxonomy normalization preserves missing/unmapped status, low sector coverage
produces explicit warnings, and policy permissions are derived from an auditable market-bucket by
rotation-state matrix.

The main blockers against the strict Phase 10 exit criteria are around audit boundaries rather than
formula correctness. Sector snapshots are replaced in place for the same run/date/mode/config key,
so they are not immutable historical records. Sector rotation can also fall back from a missing
run-specific market snapshot to the latest global market snapshot without enforcing an as-of cutoff,
which can contaminate a historical run with later market context. Finally, breadth and sector
rotation are explicitly based on the uploaded run universe, not a fixed market constituent universe;
that is acceptable if labeled as "candidate-universe breadth," but it is not full-market breadth and
has survivorship/selection-bias risk.

## Evidence Log

| Check | Result | Notes |
| --- | --- | --- |
| Phase 10 checklist from `C:/Users/Ivica/Downloads/software_review_plan.md` | Reviewed | Objective, review activities, actionable outputs, and exit criteria mapped. |
| Focused Phase 10 test suite | Passed | `uv run pytest tests/test_market_regime.py tests/test_market_regime_command_center.py tests/test_market_regime_policy.py tests/test_market_regime_repository.py tests/test_market_regime_export_service.py tests/test_market_regime_routes.py tests/test_market_participation_service.py tests/test_sector_rotation_config.py tests/test_sector_taxonomy.py tests/test_sector_universe_service.py tests/test_sector_rotation_service.py tests/test_sector_rotation_policy.py tests/test_sector_rotation_repository.py tests/test_sector_rotation_exports.py tests/test_sector_rotation_routes.py tests/test_sector_etf_rotation_service.py tests/test_sector_leadership_service.py -q` -> `121 passed, 1 warning in 10.50s`. |
| Regime formula review | Reviewed | SPY drives primary classification; QQQ risk proxy contributes 35% score and distribution checks. Missing SPY is unknown/low confidence. |
| Freshness review | Partially satisfied | Stale benchmark data adds warnings; severely stale data forces configured stale risk state. No equivalent freshness gate exists for sector ETF proxy dates when ETF mode is disabled. |
| Universe construction review | Partially satisfied | Sector metrics are run-scoped and deduplicated by ticker. Missing and unmapped sectors are tracked, but the universe is candidate/upload scoped, not a fixed full-market universe. |
| Snapshot consistency review | Gap found | Previous sector snapshots are selected only before current `as_of_date`, but current sector snapshots may use latest global market snapshot when no run-specific market snapshot exists. |
| Dashboard/export parity | Mostly satisfied | API, CSV, JSON, markdown, dashboard, and drill-down share repository/export payloads. Markdown explicitly marks universe-only snapshots. |

## Regime Formula Specification

Market regime inputs are loaded for configured symbols from price bars and then transformed with
the shared technical feature engine (`app/services/market_regime_command_center.py:68-87`).

Primary formulas:

1. Missing SPY produces `Unknown`, score `0.0`, `risk_off=True`, `gate_ok=False`,
   confidence `low`, and `missing_spy_market_data` (`app/services/market_regime.py:25-40`).
2. SPY score uses: above SMA200 `+2.5`, above SMA50 `+2.0`, SMA50 above SMA200 `+1.5`,
   positive SMA50 slope `+1.5`, positive ROC21 `+1.0`, positive ROC63 `+1.0`, and
   distribution count >= 4 `-2.0` (`app/services/market_regime.py:101-111`).
3. If QQQ/risk proxy is available, combined market score is `SPY * 0.65 + QQQ * 0.35`
   (`app/services/market_regime.py:47-49`).
4. Regime classification is ordered: crash risk, distribution, correction, bear rally,
   risk-on breakout, bull trend, bull pullback, then choppy fallback
   (`app/services/market_regime.py:80-99`).
5. Risk-off regimes are distribution, correction, and crash risk; `gate_ok` is false for risk-off
   and unknown (`app/services/market_regime.py:59-65`).
6. Missing QQQ can lower confidence without blocking calculation
   (`app/services/market_regime.py:42-53`, `app/services/market_regime.py:66-68`).
7. Freshness is measured as calendar-day age versus `max_stale_trading_days`; stale inputs add
   warnings, and severely stale data can force the configured stale risk state
   (`app/services/market_regime_command_center.py:217-235`,
   `app/services/market_regime_policy.py:79-117`).

Configured policy outputs include risk state, allowed/reduced/blocked profiles, allowed/blocked
setups, minimum score adjustment, warnings, and position-size multiplier. The market regime policy
DTO stores those values, but in the reviewed ranking path they are advisory/contextual rather than
direct rank mutations (`app/services/market_regime_policy.py:63-76`).

## Rotation Formula Specification

Sector rotation uses a run-scoped sector universe and optional ETF confirmation.

Universe construction:

1. Load unique raw rows, fundamentals, technicals, combined results, and ranking results for one
   `run_id` (`app/services/sector_universe_service.py:45-57`).
2. Build ticker records from raw rows first, then fill gaps from combined/ranking rows, then
   remaining fundamentals/technicals as `Unknown` sector
   (`app/services/sector_universe_service.py:478-529`).
3. Normalize sectors through canonical labels, TradingView map, aliases, missing, and unmapped
   statuses (`app/services/sector_taxonomy.py:22-79`).
4. Aggregate average fundamental, technical, final, and default-profile scores; top-candidate
   counts; setup density; danger counts; warning distributions; missing score counts; and profile
   distribution (`app/services/sector_universe_service.py:244-341`).

Universe leadership score:

1. Components are average technical score, average profile score with final-score fallback,
   top-candidate share, setup density, and risk control
   (`app/services/sector_universe_service.py:343-410`).
2. Current weights are technical `0.25`, profile `0.20`, top-candidate share `0.20`,
   setup density `0.20`, risk control `0.15`
   (`config/sector_rotation.yaml:81-87`).
3. Top-candidate component compares sector top-25 share to expected top-25 share
   (`app/services/sector_universe_service.py:413-419`).
4. Risk control penalizes danger classifications and danger warning flags
   (`app/services/sector_universe_service.py:422-424`).

Confidence:

1. Zero tickers is `insufficient`.
2. Fewer than `min_tickers_for_normal_confidence` tickers is `low`.
3. Technical availability below 50% is `low`.
4. At least `min_tickers_for_high_confidence` tickers and 80% technical availability is `high`.
5. Otherwise confidence is `normal`
   (`app/services/sector_universe_service.py:427-449`,
   `config/sector_rotation.yaml:3-8`).

ETF confirmation:

1. ETF mode is disabled by default (`config/sector_rotation.yaml:120-128`).
2. When disabled, the ETF service returns no rows (`app/services/sector_etf_rotation_service.py:24-27`).
3. When enabled, sector proxy bars and SPY benchmark bars are scored on trend, relative strength,
   momentum, breakout, and risk control (`app/services/sector_etf_rotation_service.py:29-80`).
4. Missing proxy data yields a null ETF score and warning
   (`app/services/sector_etf_rotation_service.py:50-75`).

Final sector decision:

1. If ETF mode is enabled and both universe and ETF scores exist, final score is
   `universe * 0.55 + etf * 0.45`; otherwise available universe score becomes
   `universe_only` (`app/services/sector_rotation_policy.py:121-140`,
   `config/sector_rotation.yaml:130-134`).
2. Rotation state priority is insufficient data, risk-off danger share, crowded risk, improving,
   fading, leading, lagging, neutral (`app/services/sector_rotation_policy.py:143-174`).
3. Market regime maps to `supportive`, `choppy`, `risk_off`, or `unknown`, then permissions are
   read from the config matrix (`app/services/sector_rotation_policy.py:69-77`,
   `app/services/sector_rotation_policy.py:185-211`, `config/sector_rotation.yaml:145-185`).
4. Decision reasons include state, permission, market bucket, score source, and score-change
   direction (`app/services/sector_rotation_policy.py:214-232`).
5. Decision warnings carry universe and ETF warnings plus sector/market risk warnings and missing
   ETF confirmation when ETF mode is enabled but unavailable
   (`app/services/sector_rotation_policy.py:235-253`).

Tie policy:

- Decisions are sorted by descending final score, confidence quality, lower danger share, then
  sector name (`app/services/sector_rotation_service.py:168-184`). This gives deterministic ties.

## Universe and Survivorship-Risk Report

The "universe" in this feature is not a market-wide constituent universe. Participation uses raw
rows, technical scores, and ranking results for a single uploaded run (`app/services/market_participation_service.py:27-65`).
Sector rotation likewise groups raw/fundamental/technical/combined/ranking rows for one run
(`app/services/sector_universe_service.py:45-57`).

This makes the output reproducible for a run, but it also means:

- Breadth metrics answer "how healthy is this uploaded/candidate universe?"
- They do not answer "how healthy is the total market, S&P 500, Nasdaq 100, or Russell 3000?"
- If the uploaded CSV is already screened for quality or momentum, top-candidate share and setup
  density can overstate true market breadth.
- If delisted or failed names are absent from the uploaded run, sector rotation can inherit
  survivorship bias.
- Unknown and unmapped sectors are preserved with warnings, which is good, but they still dilute or
  concentrate sector buckets based on the available candidate set.

Recommendation: label dashboard/export copy as candidate-universe breadth, and add an optional
fixed universe source if the product intends to support true market breadth.

## Snapshot Consistency Tests

Covered behavior:

- Market regime snapshots include `run_id`, as-of date, calculation version, config version,
  input symbols, index health, participation, sector leadership, warnings, and debug payloads
  (`app/services/market_regime_command_center.py:107-160`).
- Sector rotation snapshots include `run_id`, market regime snapshot id, as-of date, mode,
  calculation/config versions, config hash, default ranking profile, benchmark, summary, warnings,
  and row debug payloads (`app/services/sector_rotation_service.py:143-166`,
  `app/services/sector_rotation_service.py:253-287`).
- Previous sector snapshots are selected with `as_of_date < current as_of_date`, same mode, same
  config hash, and same run id when run id is provided
  (`app/services/sector_rotation_repository.py:133-157`).
- Dashboard/API/export paths read the latest run-specific sector snapshot and rows, then serialize
  through shared export helpers (`app/routers/sector_rotation_routes.py:164-192`,
  `app/services/sector_rotation_export_service.py:39-72`).

Gaps:

- `MarketRegimeRepository.upsert_snapshot` updates the matching market snapshot in place for the
  same run/as-of/calculation/config key (`app/services/market_regime_repository.py:41-60`).
- `SectorRotationRepository.save_snapshot` replaces existing rows in place for the same
  run/as-of/mode/config hash key (`app/services/sector_rotation_repository.py:84-115`,
  `app/services/sector_rotation_repository.py:205-224`).
- Sector rotation first tries `latest_for_run`, then falls back to `latest` global market snapshot
  (`app/services/sector_rotation_service.py:105`,
  `app/services/sector_rotation_service.py:275-281`). The global lookup sorts by latest as-of date
  and has no cutoff tied to the sector snapshot date (`app/services/market_regime_repository.py:62-71`).

Recommended snapshot tests:

- Building a sector snapshot for a historical run with no run-specific market snapshot must not use
  a global market snapshot dated after the sector `as_of_date`.
- Recalculating the same run/date/config should either create a new immutable revision or explicitly
  record revision lineage and superseded rows.
- Exported JSON for a persisted snapshot should be byte-stable from the same frozen database rows,
  ignoring generated ids/timestamps.

## Policy Conflict Matrix

| Market bucket | Leading | Improving | Neutral | Fading | Lagging | Crowded risk | Risk-off | Insufficient data |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Supportive | full_allowed | reduced_size | watch_only | watch_only | avoid_new_longs | reduced_size | avoid_new_longs | watch_only |
| Choppy | reduced_size | watch_only | watch_only | avoid_new_longs | avoid_new_longs | watch_only | avoid_new_longs | watch_only |
| Risk-off | watch_only | watch_only | avoid_new_longs | avoid_new_longs | avoid_new_longs | avoid_new_longs | avoid_new_longs | avoid_new_longs |
| Unknown | reduced_size | watch_only | watch_only | watch_only | avoid_new_longs | watch_only | avoid_new_longs | watch_only |

Interpretation:

- Rotation state does not override ranking rows directly; it produces sector permission and position
  size context.
- Market bucket can reduce or block otherwise leading sectors.
- Sector risk-off always blocks new longs in every market bucket.
- Insufficient sector data is watch-only in supportive/choppy/unknown markets and avoid-new-longs
  in risk-off markets.
- A leading sector in a risk-off market is still watch-only, so market regime can constrain sector
  permission even when the sector score is high.

## Data-Freshness and Minimum-Coverage Requirements

Current requirements:

- Market benchmark data is stale when age exceeds `freshness.max_stale_trading_days`; current config
  sets this to `3`, and severely stale data forces `Gray` risk state
  (`config/market_regime_command_center.yaml`, `app/services/market_regime_command_center.py:217-235`,
  `app/services/market_regime_policy.py:105-117`).
- Sector normal confidence requires at least 5 tickers; high confidence requires at least 10
  tickers (`config/sector_rotation.yaml:3-8`).
- Sector technical availability below 50% lowers confidence
  (`app/services/sector_universe_service.py:435-443`).
- Empty sector universe adds `empty_sector_rotation_universe`
  (`app/services/sector_rotation_service.py:139-141`).
- Missing/unmapped sectors, missing technical/fundamental scores, missing ranking profile results,
  and low/insufficient confidence are surfaced as warnings
  (`app/services/sector_universe_service.py:531-546`).

Recommended additional requirements:

- Sector ETF proxy and benchmark bars should have explicit max-stale thresholds when ETF mode is
  enabled.
- Universe-level coverage should emit a snapshot-level low-confidence state when too many tickers
  have unknown sectors, missing technical scores, or missing ranking profile rows.
- The market snapshot used by sector rotation should satisfy `market_snapshot.as_of_date <=
  sector_snapshot.as_of_date`, or be rejected with an explicit low-confidence/no-context state.

## Findings Register

### PH10-001 - Sector rotation can use a later global market snapshot

Severity: High

Evidence:

- Sector rotation asks for the latest run-specific market snapshot and falls back to global latest
  if absent (`app/services/sector_rotation_service.py:105`,
  `app/services/sector_rotation_service.py:275-281`).
- The global latest query sorts by descending `as_of_date`, `created_at`, and id with no cutoff
  (`app/services/market_regime_repository.py:62-71`).
- The selected market snapshot drives the market bucket and can change permissions and warnings
  (`app/services/sector_rotation_policy.py:69-77`,
  `app/services/sector_rotation_policy.py:185-211`).
- Tests currently assert global fallback behavior for missing run-specific context, including a
  risk-off fallback path (`tests/test_sector_rotation_service.py:147-166`).

Impact: Recalculating a historical sector snapshot can use a market regime snapshot that did not
exist at the historical decision cutoff. This violates the Phase 10 no-later-snapshot requirement
and can change sector permissions from full/reduced/watch/avoid.

Recommendation:

- Replace the global `latest()` fallback with an as-of-aware lookup:
  `latest_for_run(run_id)` first, then `latest_as_of_or_before(as_of_date)`, or no market context.
- Add a warning such as `missing_asof_market_regime_context` when no eligible market snapshot exists.
- Add a test where the only global market snapshot is dated after the sector snapshot; expected
  result should not use it.

### PH10-002 - Market and sector snapshots are not immutable revisions

Severity: High

Evidence:

- Market regime snapshots update an existing matching row in place
  (`app/services/market_regime_repository.py:41-60`).
- Sector rotation snapshots replace existing rows when the same run/as-of/mode/config hash snapshot
  already exists (`app/services/sector_rotation_repository.py:84-115`).
- Matching keys are not revisioned; sector matching uses as-of date, calculation version, mode,
  run id, and config hash (`app/services/sector_rotation_repository.py:205-224`).
- Tests explicitly cover update/replace behavior (`tests/test_market_regime_repository.py:29-46`,
  `tests/test_sector_rotation_repository.py:29-42`).

Impact: A saved snapshot can be overwritten by a later recalculation with changed upstream rows,
price bars, or code behavior while retaining the same logical snapshot identity. This does not meet
the Phase 10 snapshot immutability exit criterion.

Recommendation:

- Convert recalculation to append-only revisions or store a `revision`, `superseded_by_id`, input
  hash, and recalculation reason.
- If replacement is retained, rename the behavior as "latest materialized snapshot" and keep an
  append-only audit table for frozen decision records.

### PH10-003 - Breadth and sector leadership are candidate-universe metrics, not full-market metrics

Severity: Medium

Evidence:

- Market participation reads raw rows, technicals, and ranking results for the supplied run id
  (`app/services/market_participation_service.py:27-65`).
- Sector universe construction reads raw, fundamental, technical, combined, and ranking rows for
  the supplied run id (`app/services/sector_universe_service.py:45-57`).
- Sector confidence thresholds only measure ticker count and technical availability inside that
  same candidate universe (`app/services/sector_universe_service.py:427-449`).

Impact: If an uploaded run is screened, curated, or excludes delisted/failed names, breadth,
top-candidate share, and sector leadership can overstate market participation. This is not a code
bug if the feature is intended to describe the uploaded universe, but it is a survivorship and
selection-bias risk if presented as market breadth.

Recommendation:

- Rename UI/export fields to candidate-universe breadth/sector rotation where appropriate.
- Document that the model does not currently use fixed historical constituents.
- Add optional constituent-universe inputs for full-market breadth.

### PH10-004 - ETF confirmation is optional and disabled by default

Severity: Medium

Evidence:

- `etf_score.enabled` is false in the default config (`config/sector_rotation.yaml:120-128`).
- With ETF disabled, the ETF service returns no rows (`app/services/sector_etf_rotation_service.py:24-27`).
- Combined score config allows `missing_etf_policy: use_universe_only`
  (`config/sector_rotation.yaml:130-134`).
- The export brief notes universe-only mode instead of inventing ETF detail
  (`app/services/sector_rotation_export_service.py:93-114`).

Impact: Sector rotation states can be produced without ETF corroboration. This is transparent in the
mode/export, but it means Phase 10's ETF confirmation review criterion is not satisfied by default.

Recommendation:

- Decide whether ETF confirmation is required for production-grade rotation states.
- If required, enable ETF mode and set missing ETF policy to produce null/insufficient final score
  rather than universe-only fallback.
- If optional, keep the current mode but label it prominently as universe-only.

### PH10-005 - Snapshot-level sector coverage confidence is weaker than row-level confidence

Severity: Low

Evidence:

- Individual sectors emit `low_confidence_sector` or `insufficient_sector_data`
  (`app/services/sector_universe_service.py:244-341`,
  `app/services/sector_universe_service.py:427-470`).
- Snapshot warnings aggregate row and decision warnings
  (`app/services/sector_rotation_service.py:239-250`).
- There is no snapshot-level minimum coverage decision for high unknown-sector share, all-missing
  profiles, or broad technical unavailability beyond the aggregated warnings.

Impact: A dashboard can show a leading sector and normal summary even when the overall universe has
poor coverage spread across many rows. The row warnings are present, but operators may miss that the
whole snapshot should be low confidence.

Recommendation:

- Add snapshot-level coverage fields: total raw tickers, known-sector share, technical coverage,
  ranking-profile coverage, ETF coverage, and overall confidence.
- Gate leading/weakest summary labels when snapshot confidence is low.

## Positive Controls

- Config validation covers required sections, sector taxonomy, ETF proxies, score weights,
  rotation thresholds, and complete permission matrix
  (`app/services/sector_rotation_config.py:42-70`,
  `app/services/sector_rotation_config.py:170-242`).
- Sector taxonomy preserves missing and unmapped labels for auditability
  (`app/services/sector_taxonomy.py:22-79`).
- Low and insufficient sector coverage states are explicit and tested
  (`tests/test_sector_universe_service.py:483-559`,
  `tests/test_sector_rotation_policy.py:101-106`).
- Missing ETF proxy/data and missing benchmark behavior is tested
  (`tests/test_sector_etf_rotation_service.py:40-69`,
  `tests/test_sector_rotation_service.py:198-217`,
  `tests/test_sector_rotation_policy.py:153-181`).
- Dashboard, API, drill-down, CSV, JSON, and markdown paths are covered
  (`tests/test_sector_rotation_routes.py`, `tests/test_sector_rotation_exports.py`).

## Exit Criteria

| Criterion | Status | Notes |
| --- | --- | --- |
| Regime and sector outputs are reproducible from frozen inputs | Partial | Formula/config are deterministic and tests pass, but snapshots are upserted/replaced rather than immutable frozen revisions. |
| Stale or insufficient coverage produces explicit low-confidence state | Partial | Market stale data and row-level low/insufficient sector confidence are explicit. Snapshot-level aggregate coverage confidence and ETF freshness are incomplete. |
| Policy overrides are documented and tested | Pass | Market-regime policy and sector permission matrix are configured and tested. Sector permissions annotate/constrain sector context; ranking mutation is not observed in this phase. |
| No later snapshot or revised unavailable data used at decision cutoff | Fail | Sector rotation can use global latest market snapshot when run-specific snapshot is missing, with no as-of cutoff. |
| Sector taxonomy and unknown sectors are auditable | Pass | Canonical/mapped/missing/unmapped statuses are preserved in debug/export payloads and tested. |

## Recommended Next Actions

1. Add an as-of-safe market snapshot repository query and prevent sector rotation from using
   future global market snapshots.
2. Decide whether snapshots are intended to be immutable. If yes, replace upsert/row replacement
   with append-only revisions.
3. Label run-scoped participation and sector rotation as candidate-universe metrics in dashboard,
   exports, and docs.
4. Add snapshot-level coverage confidence and ETF freshness requirements.
5. Add one frozen-input regression fixture that covers market regime, participation, sector rows,
   permissions, JSON export, CSV export, markdown brief, and drill-down payload.
