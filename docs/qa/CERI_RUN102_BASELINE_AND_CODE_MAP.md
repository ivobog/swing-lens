# CERI Run 102 Baseline and Code Map

Captured before Run 102 remediation code changes on 2026-08-14 (Europe/Zurich).

## Repository baseline

- Branch: `codex/ceri-run101-remediation`
- HEAD: `305814a7119feb5b7b8ba2910aff50c7eef23751`
- HEAD subject: `Remediate CERI Run 101 pipeline`
- Dirty state at baseline: dirty only because of pre-existing untracked artifacts:
  - `docs/verification/ceri-nwe-run96.7z`
  - `docs/verification/ceri-nwe-run96/`
  - `docs/verification/ceri-queue-cleanup/`
- These artifacts are user-owned and are not part of the Run 102 change scope.

The Run 102 TDD and `ceri_export (3).json` were read from
`C:/Users/Ivica/Downloads`. The requested `Pasted markdown(5).md` was not present
in the workspace or Downloads directory; `ceri_export (3).json` and the live
Run 102 API/database were used as the page/evidence source.

## Baseline test results

- Focused CERI suite: `280 passed, 1 warning in 19.93s`.
- Explicit SEC fail-closed/current-state regression subset: `14 passed, 1 warning in 0.57s`.
- The passing SEC subset covers literal-true acceptance, null/false rejection,
  Run 101 hard negatives, current-state selection, and guidance scoring gates.

## Run 102 observed behavior

The supplied JSON export contains 177 snapshots:

- 0 have a non-null Opportunity score.
- 177 are `Unrated`.
- 177 have `Insufficient` confidence.
- 8 have nonzero Event Risk (NVDA is 1.5) while Opportunity remains Unrated.
- All 177 report revision magnitude, breadth, acceleration, Surprise Trend,
  catalysts, and Price Response unavailable.
- 29 report `guidance_rows_rejected`, demonstrating the SEC fail-closed change.
- 44 report a sparse analyst sample; 36 report an unavailable analyst sample.

NVDA live Run 102 diagnostics show source freshness without usable evidence:

- Estimates: source age 2 days, `AVAILABLE`.
- Earnings: source age 0 days, `AVAILABLE`.
- Catalysts: source age 0 days, `AVAILABLE`.
- Opportunity coverage: 0%, score null.
- Revision first-level reason: `baseline_unavailable`.
- Current-quarter revision counts are present (`up=4`, `down=0`), but breadth is null.

## Code and evidence path map

### Provider acquisition and source records

- EODHD adapter: `app/services/ceri/providers/eodhd_provider.py`
  - `fetch_estimate_snapshots` maps trend current/baselines and count fields.
  - `fetch_earnings_actuals` currently calls the earnings calendar twice with
    reported and upcoming date windows.
  - `fetch_catalysts` derives structured issuer relevance, lifecycle, and taxonomy hints.
- EODHD HTTP boundary: `app/services/ceri/providers/eodhd_client.py`.
- Licensed persistence projection: `app/services/ceri/provider_registry.py`,
  `provider_storage_projection`.
- Immutable source write/dedup boundary: `app/services/ceri/source_record_service.py`.

### Estimates and revisions

- Estimate normalizer: `app/services/ceri/estimate_normalizer.py`.
  - It currently sends EPS values through the absolute currency conversion service.
  - With missing currency, the service returns null canonical value and scale.
- Currency conversion: `app/services/ceri/currency_conversion_service.py`.
- PIT/current/baseline eligibility: `app/services/ceri/point_in_time_query.py`.
  - Current selection requires canonical scale.
  - Same-provider retrospective eligibility checks provider, company, metric,
    period type/slot/end, scale, observation reference, response known time, and
    compatible currency if both currencies exist.
- Revision magnitude, breadth, confidence, and acceleration:
  `app/services/ceri/revision_feature_service.py`.
  - Breadth is currently calculated only inside the current+baseline branch,
    even though revision counts are dimensionless current evidence.
- Feature orchestration and partial-family behavior:
  `app/services/ceri/feature_rebuild_service.py`.

### Earnings and Surprise Trend

- Earnings provider mapping: `app/services/ceri/providers/eodhd_provider.py`.
- Earnings normalizer: `app/services/ceri/earnings_normalizer.py`.
- Surprise selection/calculation: `app/services/ceri/surprise_feature_service.py`.
- Feature capability and derived persistence:
  `app/services/ceri/feature_rebuild_service.py`.

### Catalysts and Price Response

- Catalyst provider mapping: `app/services/ceri/providers/eodhd_provider.py`.
- Catalyst normalizer/eligibility: `app/services/ceri/catalyst_taxonomy.py` and
  `app/services/ceri/catalyst_feature_service.py`.
- Catalyst normalization/revision persistence:
  `app/services/ceri/normalization_service.py`.
- Price Response event selection: `_latest_price_event` in
  `app/services/ceri/feature_rebuild_service.py`.
- Price window calculation and persistence:
  `app/services/ceri/price_response_service.py`.

### Component ledger, snapshot, lifecycle, alerts, API/UI

- Opportunity components and 60% gate:
  `app/services/ceri/opportunity_score_service.py`.
- Evidence/component ledgers and immutable snapshot:
  `app/services/ceri/evidence_state_service.py` and
  `app/services/ceri/snapshot_service.py`.
- Lifecycle/change and alerts:
  `app/services/ceri/change_detection_service.py`,
  `app/services/ceri/alert_service.py`.
- API diagnostic DTOs/freshness: `app/services/ceri/query_service.py`.
- Routes: `app/routers/ceri_routes.py`.
- Ticker UI: `app/templates/ceri_ticker.html` and `app/static/ceri.js`.

## Exact pre-change defect hypotheses to drive RED tests

1. **EPS persistence boundary:** Run 102 source records contain numeric
   `consensus`, `eps_trend_current`, and retrospective values, but missing
   currency makes the absolute conversion service erase normalized consensus
   and canonical scale. This prevents current selection and same-provider
   relative comparison before the relative eligibility logic can run.
2. **Legacy/new baseline disconnect:** older normalized rows use
   `PROVIDER_RELATIVE_WINDOW`, while the current selector recognizes only
   `PROVIDER_RETROSPECTIVE_WINDOW`; Run 102 then falls back to an older current
   row and cannot select its paired baselines.
3. **Breadth coupling:** valid up/down counts are retained on current rows, but
   breadth is calculated only when a magnitude baseline is selected.
4. **Earnings acquisition/provider limitation:** the live calendar response
   persisted six historical NVDA rows, but all actual/estimate/surprise fields
   are null. It therefore does not supply reported-result evidence.
5. **Earnings persistence boundary:** the licensed projection omits
   `event_kind`, `acquisition_policy`, and report-time consensus semantics, so
   even valid provider fields would lose acquisition/lineage semantics before
   normalization.
6. **Catalyst persistence boundary:** provider-derived issuer relevance and
   reason (and expected event date) are omitted by the licensed projection.
   Normalized revisions therefore become issuer-unverified.
7. **Price Response parent selection:** the builder considers unaccepted
   guidance, rejected catalysts, and upcoming/null-actual earnings as candidate
   parents. When no usable parent exists, it persists no diagnostic row and
   exposes only generic unavailability.
8. **UI semantics:** freshness status `AVAILABLE` means source present/fresh,
   not normalized, eligible, or selected evidence. The DTO lacks stage counts
   and a dominant blocker.

These hypotheses must be proven by failing tests before production code changes.
