# Technical Scoring v5 downstream consumer audit

## Scope and invariant

Audit point: commit `eb6798ee990e268b2ef808bb0747465220b9b0e7`, plus the shadow-calibration changes in this worktree.

When v5 is active, `TechnicalScore.dual_score`, classification, action, confidence, and engine version mirror v5. The adjacent persisted `trend_score`, `momentum_score`, `setup_score`, `risk_score`, `market_score`, `relative_strength_score`, and `combined_relative_strength_score` remain local/base or v4-style fields. No consumer may infer that those adjacent columns mathematically compose the active `dual_score`.

## Machine-readable dependency map

| Consumer | Fields | Classification | Default-activation impact | Evidence / required action |
|---|---|---|---|---|
| `app/services/combined_decision.py` | `dual_score`, classification, risk | `V5_SAFE` | none | Treats technical score as an opaque weighted input; it does not recompute it from legacy components. |
| `app/services/confidence_service.py` | `dual_score` presence | `V5_SAFE` | none | Uses score availability only. |
| `app/services/market_participation_service.py` | `dual_score` | `V5_SAFE` | none | Aggregates the active compatibility score without component reconstruction. |
| `app/services/sector_leadership_service.py`, `sector_universe_service.py`, `routers/sector_rotation_routes.py` | `dual_score`, classification | `V5_SAFE` | none | Ranking/aggregation consumes the active score as an opaque value. |
| `app/services/export_service.py` | all legacy and v5 fields | `V5_SAFE` | none | Exports distinct v4-compatible and v5 columns; historical nullable fallback is explicit. |
| `app/templates/run_detail.html`, `score_card_view_service.py`, `technical_display_fields.py` | score, components, v5 details, version | `V5_SAFE` | none | V5 decomposition is displayed separately and historical rows retain fallback behavior. |
| `app/services/relative_leadership.py` | v4 score/setup percentiles | `V4_ONLY_INTENTIONAL` | none | This is the v4 Leadership implementation used before independent v5 Leadership finalization. It must not be relabeled as v5. |
| `app/services/pine_replica_engine.py`, `technical_score_v4.py` | all base/v4 fields | `V4_ONLY_INTENTIONAL` | none | These modules generate the frozen base/v4 result that v5 consumes independently. |
| `app/services/ranking_profile_engine.py` | `dual_score`, legacy risk and gates | `NEEDS_UPGRADE` | blocks default | Profile scoring mixes the active compatibility score with v4-era penalties/gates. Add engine-version-aware profiles or prove the hybrid semantics empirically. |
| `app/services/ranking_profile_components.py`, `ranking_profile_penalties.py`, `ranking_profile_gates.py` | trend/momentum/setup/risk/RS, classification | `BLOCKS_DEFAULT_ACTIVATION` | blocks default | These consumers interpret neighboring legacy components and classification together. Active v5 would create a hybrid v5-total/v4-component feature vector. |
| `app/services/setup_lifecycle/snapshot_builder.py` | `dual_score`, trend, momentum, setup, risk, RS, classification/action | `BLOCKS_DEFAULT_ACTIVATION` | blocks default | Lifecycle snapshots and deltas would mix a v5 total with v4 component semantics. Version the snapshot schema and define v5 component mappings before default activation. |
| `app/services/setup_lifecycle/*_adapter.py`, `episode_service.py`, `query_service.py`, alert/change services | legacy components and lifecycle technical score | `NEEDS_UPGRADE` | blocks default transitively | Adapters depend on the snapshot contract. Upgrade after snapshot schema versioning; alert thresholds need v5 shadow comparison. |
| `app/services/winner_probability/capture_service.py`, `feature_extractor.py` | technical score, classification/action and evidence bands | `BLOCKS_DEFAULT_ACTIVATION` | blocks default | Existing Winner Evidence/cohort features were learned under v4 score semantics. V5 needs a versioned feature contract and later-period evidence before training/default use. |
| `app/services/winner_probability/reproduction_service.py` | captured technical features | `V4_ONLY_INTENTIONAL` | blocks v5 training, not shadow | Historical reproductions must retain the captured v4 contract; do not reinterpret them as v5. |
| `app/services/ib_market_intelligence/journal.py` | `dual_score` | `NEEDS_UPGRADE` | limited activation only | Captures the score but not a full v5 decomposition; include engine/config/signature if v5 becomes active. |
| `app/routers/gui_routes.py` | combined technical weight | `V5_SAFE` | none | UI config treats `dual_score` as the technical aggregate. |
| SLSE exports and alert certification scripts/tests | score and legacy component deltas | `BLOCKS_DEFAULT_ACTIVATION` | blocks default | Golden corpora and thresholds encode v4-era semantics. Add dual-version fixtures and threshold evidence before activation. |
| Database persistence and migrations | compatibility score, version, nullable v5 columns | `V5_SAFE` | none after migration | Shadow persistence is additive; active mirroring is explicit and v5 stays disabled by default. |

## Activation conclusion

The persistence, UI, export, combined-score, market-participation, and sector-aggregation paths are v5-safe. Ranking profiles, Setup Lifecycle/alerts, Winner Evidence, and their v4-trained golden contracts block default activation. Limited feature-flag activation would require isolating those consumers or explicitly keeping them on v4 while the user-facing technical total uses v5.

No production-default setting was changed by this audit.
