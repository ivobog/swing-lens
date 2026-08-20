# Route and Export Inventory

This inventory is generated from `app.main:app`.

Update it after adding, removing, or renaming routes:

```powershell
python scripts/docs/check_route_inventory.py --write
```

CI checks that the generated blocks below match runtime route introspection.

## Export Policy Notes

- CSV and JSON exports are streamed through budgeted attachment responses where practical.
- Large exports should refuse with a structured `413` response instead of exhausting local memory.
- XLSX export is deferred. Do not add `.xlsx` promises to user docs until there is a separate
  implementation and test plan for workbook generation, schema versioning, and formula safety.

## Exports

<!-- EXPORT_INVENTORY_START -->
| Methods | Path | Endpoint |
| --- | --- | --- |
| GET | `/api/ib-intelligence/scanner/candidates.csv` | `scanner_candidates_csv` |
| GET | `/api/setup-lifecycle/alerts/export.csv` | `export_setup_lifecycle_alerts_csv` |
| GET | `/api/setup-lifecycle/alerts/export.json` | `export_setup_lifecycle_alerts_json` |
| GET | `/api/setup-lifecycle/changes/export.csv` | `export_setup_lifecycle_changes_csv` |
| GET | `/api/setup-lifecycle/changes/export.json` | `export_setup_lifecycle_changes_json` |
| GET | `/api/setup-lifecycle/episodes/{episode_id}/export.csv` | `export_setup_lifecycle_episode_csv` |
| GET | `/api/setup-lifecycle/episodes/{episode_id}/export.json` | `export_setup_lifecycle_episode_json` |
| GET | `/api/setup-lifecycle/operations/export.json` | `export_setup_lifecycle_operations_json` |
| GET | `/api/winner-probability/estimates/{estimate_id}/reproduction/export.json` | `export_winner_probability_reproduction_json` |
| GET | `/api/winner-probability/outcomes/explorer/export.csv` | `export_winner_probability_outcome_explorer_csv` |
| GET | `/api/winner-probability/run/{run_id}/export.csv` | `export_winner_probability_run_csv` |
| GET | `/api/winner-probability/run/{run_id}/export.json` | `export_winner_probability_run_json` |
| GET | `/ceri/export.csv` | `export_ceri_csv` |
| GET | `/ceri/export.json` | `export_ceri_json` |
| GET | `/market-regime/export.csv` | `export_latest_market_regime_csv` |
| GET | `/market-regime/export.json` | `export_latest_market_regime_json` |
| GET | `/openapi.json` | `openapi` |
| GET | `/runs/{run_id}/exports/{export_type}.csv` | `export_run_results` |
| GET | `/runs/{run_id}/ib/fetches/{fetch_run_id}/failed.csv` | `export_failed_fetch_items` |
| GET | `/runs/{run_id}/market-regime/export.csv` | `export_run_market_regime_csv` |
| GET | `/runs/{run_id}/market-regime/export.json` | `export_run_market_regime_json` |
| GET | `/runs/{run_id}/rankings/export.csv` | `export_all_ranking_results` |
| GET | `/runs/{run_id}/rankings/{profile_name}/export.csv` | `export_ranking_profile_results` |
| GET | `/runs/{run_id}/sector-rotation/brief.md` | `export_sector_rotation_dashboard_markdown` |
| GET | `/runs/{run_id}/sector-rotation/export.csv` | `export_sector_rotation_dashboard_csv` |
| GET | `/runs/{run_id}/sector-rotation/export.json` | `export_sector_rotation_dashboard_json` |
| GET | `/setup-lifecycle/export.csv` | `export_setup_lifecycle_csv` |
| GET | `/setup-lifecycle/export.json` | `export_setup_lifecycle_json` |
<!-- EXPORT_INVENTORY_END -->

## All Routes

<!-- ROUTE_INVENTORY_START -->
| Methods | Path | Endpoint |
| --- | --- | --- |
| GET | `/` | `upload_page` |
| GET | `/api/ceri/alerts` | `ceri_alerts` |
| POST | `/api/ceri/alerts/{alert_id}/acknowledge` | `acknowledge_ceri_alert` |
| POST | `/api/ceri/alerts/{alert_id}/dismiss` | `dismiss_ceri_alert` |
| POST | `/api/ceri/backfills` | `create_ceri_backfill` |
| GET | `/api/ceri/changes` | `ceri_changes` |
| GET | `/api/ceri/events` | `ceri_events` |
| GET | `/api/ceri/events/{event_id}` | `ceri_event` |
| POST | `/api/ceri/events/{event_id}/review` | `review_ceri_event` |
| GET | `/api/ceri/events/{event_id}/revisions` | `ceri_event_revisions` |
| POST | `/api/ceri/ingestion-runs` | `create_ceri_ingestion_run` |
| GET | `/api/ceri/jobs/{job_id}` | `ceri_job_status` |
| POST | `/api/ceri/jobs/{job_id}/cancel` | `cancel_ceri_job` |
| GET | `/api/ceri/latest` | `ceri_latest` |
| GET | `/api/ceri/operations/conflicts` | `ceri_operations_conflicts` |
| GET | `/api/ceri/operations/quarantine` | `ceri_operations_quarantine` |
| GET | `/api/ceri/operations/stale` | `ceri_operations_stale` |
| GET | `/api/ceri/operations/status` | `ceri_operations_status` |
| GET | `/api/ceri/providers/health` | `ceri_provider_health` |
| POST | `/api/ceri/providers/validate` | `ceri_provider_validate` |
| POST | `/api/ceri/purge/execute` | `execute_ceri_purge` |
| POST | `/api/ceri/purge/preview` | `preview_ceri_purge` |
| POST | `/api/ceri/recalculate` | `recalculate_ceri` |
| POST | `/api/ceri/reprocess` | `reprocess_ceri` |
| GET | `/api/ceri/revisions` | `ceri_revisions` |
| GET | `/api/ceri/revisions/{revision_id}` | `ceri_revision` |
| GET | `/api/ceri/run/{run_id}` | `ceri_run` |
| GET | `/api/ceri/ticker/{ticker}` | `ceri_ticker` |
| GET | `/api/ceri/ticker/{ticker}/history` | `ceri_ticker_history` |
| POST | `/api/ib-gateway/launch` | `launch_ib_gateway` |
| GET | `/api/ib-gateway/status` | `ib_gateway_status` |
| POST | `/api/ib-intelligence/flex/import` | `queue_flex` |
| POST | `/api/ib-intelligence/histogram/fetch` | `queue_histogram` |
| GET | `/api/ib-intelligence/histogram/{ticker}` | `histogram_api` |
| POST | `/api/ib-intelligence/live-snapshot` | `queue_live` |
| GET | `/api/ib-intelligence/operations` | `operations_api` |
| POST | `/api/ib-intelligence/rebuild-features` | `queue_rebuild` |
| POST | `/api/ib-intelligence/refresh` | `queue_historical` |
| GET | `/api/ib-intelligence/run/{run_id}` | `intelligence_run` |
| GET | `/api/ib-intelligence/scanner/candidates.csv` | `scanner_candidates_csv` |
| POST | `/api/ib-intelligence/scanner/run` | `queue_scanner` |
| GET | `/api/ib-intelligence/scanner/runs` | `scanner_runs_api` |
| GET | `/api/ib-intelligence/ticker/{ticker}` | `ticker_intelligence` |
| GET | `/api/ib-intelligence/trade-journal` | `trade_journal_api` |
| POST | `/api/ib-intelligence/trade-journal/fills/{fill_id}/exclude` | `exclude_fill` |
| POST | `/api/market-data/prewarm` | `queue_market_data_prewarm` |
| GET | `/api/market-data/prewarm/{job_id}` | `market_data_prewarm_status` |
| POST | `/api/market-data/prewarm/{job_id}/cancel` | `cancel_market_data_prewarm` |
| GET | `/api/market-regime/history` | `market_regime_history_api` |
| GET | `/api/market-regime/latest` | `latest_market_regime_api` |
| GET | `/api/market-regime/run/{run_id}` | `run_market_regime_api` |
| POST | `/api/market-regime/run/{run_id}/recalculate` | `recalculate_run_market_regime_api` |
| GET | `/api/runs/{run_id}/sector-rotation` | `api_sector_rotation` |
| POST | `/api/runs/{run_id}/sector-rotation/recalculate` | `recalculate_run_sector_rotation_api` |
| GET | `/api/runs/{run_id}/sector-rotation/{sector_slug}` | `api_sector_rotation_drilldown` |
| GET | `/api/runs/{run_id}/tickers/{ticker}/chart-data` | `ticker_chart_data` |
| GET | `/api/sector-rotation/snapshots` | `api_sector_rotation_snapshots` |
| GET | `/api/sector-rotation/snapshots/{snapshot_id}` | `api_sector_rotation_snapshot` |
| GET | `/api/setup-lifecycle/alerts` | `setup_lifecycle_alerts` |
| GET | `/api/setup-lifecycle/alerts/export.csv` | `export_setup_lifecycle_alerts_csv` |
| GET | `/api/setup-lifecycle/alerts/export.json` | `export_setup_lifecycle_alerts_json` |
| POST | `/api/setup-lifecycle/alerts/{alert_id}/acknowledge` | `acknowledge_setup_lifecycle_alert` |
| POST | `/api/setup-lifecycle/alerts/{alert_id}/dismiss` | `dismiss_setup_lifecycle_alert` |
| GET | `/api/setup-lifecycle/changes` | `setup_lifecycle_changes` |
| GET | `/api/setup-lifecycle/changes/export.csv` | `export_setup_lifecycle_changes_csv` |
| GET | `/api/setup-lifecycle/changes/export.json` | `export_setup_lifecycle_changes_json` |
| GET | `/api/setup-lifecycle/diagnostics` | `setup_lifecycle_diagnostics` |
| GET | `/api/setup-lifecycle/episodes/{episode_id}` | `setup_lifecycle_episode` |
| GET | `/api/setup-lifecycle/episodes/{episode_id}/export.csv` | `export_setup_lifecycle_episode_csv` |
| GET | `/api/setup-lifecycle/episodes/{episode_id}/export.json` | `export_setup_lifecycle_episode_json` |
| POST | `/api/setup-lifecycle/evaluate` | `evaluate_setup_lifecycle_run` |
| POST | `/api/setup-lifecycle/evaluate-run` | `evaluate_setup_lifecycle_run` |
| POST | `/api/setup-lifecycle/evaluations` | `queue_setup_lifecycle_evaluation` |
| GET | `/api/setup-lifecycle/evaluations/{evaluation_id}` | `setup_lifecycle_evaluation` |
| GET | `/api/setup-lifecycle/filter-options` | `setup_lifecycle_filter_options` |
| GET | `/api/setup-lifecycle/operations` | `setup_lifecycle_operations` |
| GET | `/api/setup-lifecycle/operations/export.json` | `export_setup_lifecycle_operations_json` |
| POST | `/api/setup-lifecycle/replay` | `replay_setup_lifecycle` |
| POST | `/api/setup-lifecycle/run/{run_id}/evaluate` | `evaluate_setup_lifecycle_run` |
| GET | `/api/setup-lifecycle/tickers/{ticker}` | `setup_lifecycle_ticker_timeline` |
| GET | `/api/setup-lifecycle/tickers/{ticker}/timeline` | `setup_lifecycle_ticker_timeline` |
| POST | `/api/winner-probability/cohorts/refresh` | `queue_winner_cohort_refresh` |
| GET | `/api/winner-probability/estimates/{estimate_id}/reproduction` | `winner_probability_estimate_reproduction` |
| GET | `/api/winner-probability/estimates/{estimate_id}/reproduction/export.json` | `export_winner_probability_reproduction_json` |
| GET | `/api/winner-probability/models` | `winner_probability_models` |
| GET | `/api/winner-probability/models/{id}/calibration` | `winner_probability_model_calibration` |
| GET | `/api/winner-probability/models/{id}/drift` | `winner_probability_model_drift` |
| POST | `/api/winner-probability/models/{id}/retire` | `retire_winner_probability_model` |
| GET | `/api/winner-probability/operations/status` | `winner_probability_operations_status` |
| GET | `/api/winner-probability/outcomes/explorer` | `winner_probability_outcome_explorer` |
| GET | `/api/winner-probability/outcomes/explorer/export.csv` | `export_winner_probability_outcome_explorer_csv` |
| POST | `/api/winner-probability/outcomes/process` | `queue_winner_outcome_maturation` |
| GET | `/api/winner-probability/predictions/{prediction_id}` | `winner_probability_prediction` |
| GET | `/api/winner-probability/predictions/{prediction_id}/neighbors` | `winner_probability_neighbors` |
| GET | `/api/winner-probability/run/{run_id}` | `winner_probability_run` |
| GET | `/api/winner-probability/run/{run_id}/export.csv` | `export_winner_probability_run_csv` |
| GET | `/api/winner-probability/run/{run_id}/export.json` | `export_winner_probability_run_json` |
| POST | `/api/winner-probability/runs/{run_id}/capture` | `queue_winner_prediction_capture` |
| GET | `/api/winner-probability/tickers/{ticker}/history` | `winner_probability_ticker_history` |
| GET | `/ceri` | `ceri_dashboard_page` |
| GET | `/ceri/changes` | `ceri_changes_page` |
| GET | `/ceri/export.csv` | `export_ceri_csv` |
| GET | `/ceri/export.json` | `export_ceri_json` |
| GET | `/ceri/operations` | `ceri_operations_page` |
| GET | `/ceri/ticker/{ticker}` | `ceri_ticker_page` |
| GET | `/docs` | `swagger_ui_html` |
| GET | `/docs/oauth2-redirect` | `swagger_ui_redirect` |
| GET | `/health` | `health` |
| GET | `/help` | `help_page` |
| GET | `/history` | `history_page` |
| GET | `/ib` | `ib_gateway_page` |
| GET | `/ib-intelligence` | `intelligence_page` |
| GET | `/ib-intelligence/operations` | `operations_page` |
| GET | `/ib-intelligence/scanner` | `scanner_page` |
| GET | `/ib-intelligence/trade-journal` | `trade_journal_page` |
| POST | `/ib/fetch` | `fetch_bars` |
| POST | `/ib/resolve/{ticker}` | `resolve_ticker` |
| GET | `/ib/status` | `ib_status` |
| POST | `/ib/test` | `test_ib_connection` |
| GET | `/market-regime` | `market_regime_page` |
| GET | `/market-regime/export.csv` | `export_latest_market_regime_csv` |
| GET | `/market-regime/export.json` | `export_latest_market_regime_json` |
| GET | `/metrics` | `metrics` |
| GET | `/openapi.json` | `openapi` |
| POST | `/ops/cleanup/execute` | `cleanup_execute` |
| GET | `/ops/cleanup/preview` | `cleanup_preview` |
| GET | `/ready` | `ready` |
| GET | `/redoc` | `redoc_html` |
| GET | `/runs` | `runs_page` |
| GET | `/runs/{run_id}` | `run_detail_page` |
| GET | `/runs/{run_id}/ceri` | `ceri_run_page` |
| POST | `/runs/{run_id}/combined-results` | `refresh_combined_results_action` |
| GET | `/runs/{run_id}/coverage` | `run_coverage_page` |
| GET | `/runs/{run_id}/exports/{export_type}.csv` | `export_run_results` |
| POST | `/runs/{run_id}/fundamentals/recalculate` | `recalculate_fundamentals_action` |
| POST | `/runs/{run_id}/ib/fetch` | `fetch_run_ib_bars_action` |
| POST | `/runs/{run_id}/ib/fetch/{fetch_run_id}/cancel` | `cancel_run_ib_fetch_action` |
| GET | `/runs/{run_id}/ib/fetch/{fetch_run_id}/progress` | `run_ib_fetch_progress` |
| POST | `/runs/{run_id}/ib/fetch/{fetch_run_id}/resume` | `resume_run_ib_fetch_action` |
| POST | `/runs/{run_id}/ib/fetch/{fetch_run_id}/retry-failed` | `retry_failed_run_ib_fetch_action` |
| GET | `/runs/{run_id}/ib/fetches/{fetch_run_id}` | `run_ib_fetch_progress_page` |
| GET | `/runs/{run_id}/ib/fetches/{fetch_run_id}/failed.csv` | `export_failed_fetch_items` |
| GET | `/runs/{run_id}/ib/fetches/{fetch_run_id}/status` | `run_ib_fetch_status` |
| GET | `/runs/{run_id}/ib/plan` | `preview_run_ib_fetch_plan` |
| POST | `/runs/{run_id}/ib/test` | `test_run_ib_connection_action` |
| GET | `/runs/{run_id}/mapping` | `run_mapping_page` |
| GET | `/runs/{run_id}/market-regime` | `run_market_regime_page` |
| GET | `/runs/{run_id}/market-regime/export.csv` | `export_run_market_regime_csv` |
| GET | `/runs/{run_id}/market-regime/export.json` | `export_run_market_regime_json` |
| POST | `/runs/{run_id}/pipeline` | `run_full_pipeline_action` |
| GET | `/runs/{run_id}/pipeline/{pipeline_id}` | `run_pipeline_progress_page` |
| POST | `/runs/{run_id}/pipeline/{pipeline_id}/cancel` | `cancel_run_pipeline_action` |
| POST | `/runs/{run_id}/pipeline/{pipeline_id}/resume` | `resume_run_pipeline_action` |
| GET | `/runs/{run_id}/pipeline/{pipeline_id}/status` | `run_pipeline_status` |
| GET | `/runs/{run_id}/rankings/export.csv` | `export_all_ranking_results` |
| GET | `/runs/{run_id}/rankings/profiles` | `list_ranking_profiles` |
| POST | `/runs/{run_id}/rankings/refresh` | `refresh_all_ranking_profiles_action` |
| GET | `/runs/{run_id}/rankings/{profile_name}` | `view_ranking_profile_results` |
| GET | `/runs/{run_id}/rankings/{profile_name}/export.csv` | `export_ranking_profile_results` |
| POST | `/runs/{run_id}/rankings/{profile_name}/refresh` | `refresh_ranking_profile_action` |
| GET | `/runs/{run_id}/sector-rotation` | `sector_rotation_dashboard` |
| GET | `/runs/{run_id}/sector-rotation/brief.md` | `export_sector_rotation_dashboard_markdown` |
| GET | `/runs/{run_id}/sector-rotation/export.csv` | `export_sector_rotation_dashboard_csv` |
| GET | `/runs/{run_id}/sector-rotation/export.json` | `export_sector_rotation_dashboard_json` |
| GET | `/runs/{run_id}/sector-rotation/{sector_slug}` | `sector_rotation_drilldown` |
| GET | `/runs/{run_id}/setup-lifecycle` | `run_setup_lifecycle` |
| POST | `/runs/{run_id}/technicals/refresh` | `refresh_technicals_action` |
| GET | `/runs/{run_id}/tickers/{ticker}/chart` | `ticker_chart_panel` |
| GET | `/runs/{run_id}/winner-probability` | `winner_probability_run_page` |
| GET | `/scoring` | `scoring_page` |
| GET | `/settings` | `settings_page` |
| GET | `/setup-lifecycle` | `setup_lifecycle_page` |
| GET | `/setup-lifecycle/alerts` | `setup_lifecycle_alerts_page` |
| GET | `/setup-lifecycle/episodes/{episode_id}` | `setup_lifecycle_episode_page` |
| GET | `/setup-lifecycle/export.csv` | `export_setup_lifecycle_csv` |
| GET | `/setup-lifecycle/export.json` | `export_setup_lifecycle_json` |
| GET | `/setup-lifecycle/operations` | `setup_lifecycle_operations_page` |
| GET | `/setup-lifecycle/ticker/{ticker}` | `setup_lifecycle_ticker_page` |
| POST | `/uploads` | `upload_csv` |
| GET | `/winner-probability/models` | `winner_probability_models_page` |
| GET | `/winner-probability/operations` | `winner_probability_operations_page` |
| GET | `/winner-probability/outcomes` | `winner_probability_outcomes_page` |
| GET | `/winner-probability/predictions/{prediction_id}` | `winner_probability_prediction_page` |
<!-- ROUTE_INVENTORY_END -->
