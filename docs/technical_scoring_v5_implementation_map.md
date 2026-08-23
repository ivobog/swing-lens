# Technical Scoring v5 implementation map

## Current production path

`technical_score_service.score_run_technicals` loads the market inputs, executes the
database-free v3.2 ticker work (sequentially or in a process pool), ranks the v4
leadership universe, applies `technical_score_v4_from_base_score`, and persists one
`TechnicalScore` row per ticker.  The same finalizer is used by the overlap coordinator.

## V5 path

1. Keep ticker-local feature calculation and confirmed weekly HTF calculation unchanged.
2. Add `roc10` and target/stop diagnostics to the local feature/debug boundary.
3. Resolve canonical sectors from `RawCompanyRow`, load each required sector ETF once per
   run, and pass the matching frame to the existing database-free worker boundary.
4. Rank v5 Leadership after every ticker-local result is available.  Its inputs are only
   ROC21/63/126, broad/sector benchmark RS, and beta-adjusted residual momentum.
5. Finalize independent TS, setup-selected SQ, danger-capped EQ, and regime-policy TCS.
6. Always preserve the v4 result in `v4_debug_json`.  In shadow mode persist v5 fields and
   comparison diagnostics while leaving `dual_score`, classification, and action on v4.
   In active mode those compatibility fields mirror v5 TCS/classification/action.
7. Persist v5 columns through the next Alembic revision.  Historical rows remain nullable
   and therefore render/export through the existing v4 fallback path.
8. Include the v5 config hash, benchmark resolution, universe scope, inputs, formulas,
   caps, warnings, and shadow comparison in `v5_debug_json`.

## Compatibility and cache boundaries

The local artifact cache remains ticker-local. Its input signature now uses a dedicated
feature-generation hash (Pine plus the v4 feature-generation contract); final v4/v5
scoring identity is retained as provenance but does not invalidate local OHLCV/indicator
artifacts. Pure v5 weight, cap, confidence, and composite changes therefore reuse the
same local features, while a Pine/v4 feature-generation change invalidates them.
Cross-sectional Leadership is never cached as a local artifact. Sector ETF identity/data
participates in the run-level market signature used by stale-result fencing.
