"""add IBKR market intelligence and Flex journal evidence

Revision ID: 0031_add_ib_market_intelligence
Revises: 0030_fix_ceri_estimate_snapshot_identity
"""

# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "0031_add_ib_market_intelligence"
down_revision: str | None = "0030_fix_ceri_estimate_snapshot_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE ib_intelligence_runs (
      id BIGSERIAL PRIMARY KEY, background_job_id BIGINT REFERENCES background_jobs(id) ON DELETE SET NULL,
      job_type TEXT NOT NULL, module TEXT NOT NULL, status TEXT NOT NULL,
      deterministic_request_key TEXT NOT NULL, scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      config_version TEXT NOT NULL, config_hash TEXT NOT NULL,
      counts_json JSONB NOT NULL DEFAULT '{}'::jsonb, checkpoint_json JSONB NOT NULL DEFAULT '{}'::jsonb,
      warning_flags_json JSONB NOT NULL DEFAULT '[]'::jsonb, error_message TEXT,
      started_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ
    );
    CREATE INDEX ix_ib_intelligence_runs_module_started ON ib_intelligence_runs(module, started_at);
    CREATE INDEX ix_ib_intelligence_runs_request_key ON ib_intelligence_runs(deterministic_request_key);

    CREATE TABLE ib_intelligence_request_items (
      id BIGSERIAL PRIMARY KEY,
      intelligence_run_id BIGINT NOT NULL REFERENCES ib_intelligence_runs(id) ON DELETE CASCADE,
      deterministic_request_key TEXT NOT NULL, ticker TEXT, ib_conid BIGINT,
      request_family TEXT NOT NULL, request_type TEXT NOT NULL, priority INTEGER NOT NULL,
      status TEXT NOT NULL, availability_status TEXT NOT NULL, request_json JSONB NOT NULL,
      result_counts_json JSONB NOT NULL DEFAULT '{}'::jsonb, retry_count INTEGER NOT NULL DEFAULT 0,
      error_message TEXT, started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ,
      CONSTRAINT uq_ib_intelligence_request_item_key
        UNIQUE(intelligence_run_id, deterministic_request_key)
    );
    CREATE INDEX ix_ib_intelligence_request_item_status
      ON ib_intelligence_request_items(status, priority, started_at);

    CREATE TABLE ib_historical_metric_bars (
      id BIGSERIAL PRIMARY KEY, intelligence_run_id BIGINT REFERENCES ib_intelligence_runs(id) ON DELETE SET NULL,
      ticker TEXT NOT NULL, ib_conid BIGINT, session_date DATE NOT NULL, effective_session DATE NOT NULL,
      timeframe TEXT NOT NULL, metric_type TEXT NOT NULL,
      open_value NUMERIC(24,10), high_value NUMERIC(24,10), low_value NUMERIC(24,10), close_value NUMERIC(24,10),
      source TEXT NOT NULL DEFAULT 'IBKR', source_semantic_type TEXT NOT NULL, requested_range TEXT,
      availability_status TEXT NOT NULL, capability_reason TEXT, data_hash TEXT NOT NULL,
      revision_count INTEGER NOT NULL DEFAULT 0, first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), revised_at TIMESTAMPTZ,
      warning_flags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
      CONSTRAINT uq_ib_historical_metric_bar_identity UNIQUE(ticker, session_date, timeframe, metric_type)
    );
    CREATE INDEX ix_ib_historical_metric_ticker_type_date ON ib_historical_metric_bars(ticker, metric_type, session_date);

    CREATE TABLE ib_historical_metric_revisions (
      id BIGSERIAL PRIMARY KEY, metric_bar_id BIGINT NOT NULL REFERENCES ib_historical_metric_bars(id) ON DELETE CASCADE,
      revision_number INTEGER NOT NULL, previous_data_hash TEXT NOT NULL, new_data_hash TEXT NOT NULL,
      previous_values_json JSONB NOT NULL, new_values_json JSONB NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
      CONSTRAINT uq_ib_metric_revision UNIQUE(metric_bar_id, revision_number)
    );

    CREATE TABLE ib_market_intelligence_snapshots (
      id BIGSERIAL PRIMARY KEY, intelligence_run_id BIGINT REFERENCES ib_intelligence_runs(id) ON DELETE SET NULL,
      ticker TEXT NOT NULL, ib_conid BIGINT, effective_session DATE NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
      snapshot_type TEXT NOT NULL, values_json JSONB NOT NULL, availability_status TEXT NOT NULL,
      capability_reason TEXT, evidence_hash TEXT NOT NULL, source_request_json JSONB NOT NULL,
      warning_flags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
      CONSTRAINT uq_ib_market_snapshot_evidence_hash UNIQUE(evidence_hash)
    );
    CREATE INDEX ix_ib_market_snapshot_latest ON ib_market_intelligence_snapshots(ticker, snapshot_type, observed_at);

    CREATE TABLE ib_intelligence_features (
      id BIGSERIAL PRIMARY KEY, intelligence_run_id BIGINT REFERENCES ib_intelligence_runs(id) ON DELETE SET NULL,
      ticker TEXT NOT NULL, ib_conid BIGINT, as_of_session DATE NOT NULL, calculated_at TIMESTAMPTZ NOT NULL,
      module TEXT NOT NULL, classification TEXT NOT NULL, score NUMERIC(12,6), confidence TEXT NOT NULL,
      freshness_status TEXT NOT NULL, coverage_status TEXT NOT NULL, components_json JSONB NOT NULL,
      reasons_json JSONB NOT NULL, warnings_json JSONB NOT NULL, source_evidence_hashes_json JSONB NOT NULL,
      source_version TEXT NOT NULL, calculation_version TEXT NOT NULL, config_hash TEXT NOT NULL,
      input_signature TEXT NOT NULL,
      CONSTRAINT uq_ib_intelligence_feature_version UNIQUE(ticker, as_of_session, module, calculation_version, config_hash)
    );
    CREATE INDEX ix_ib_intelligence_feature_latest ON ib_intelligence_features(ticker, module, as_of_session);

    CREATE TABLE ib_scanner_parameter_cache (
      id BIGSERIAL PRIMARY KEY, xml_payload TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
      fetched_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE ib_scanner_runs (
      id BIGSERIAL PRIMARY KEY, intelligence_run_id BIGINT REFERENCES ib_intelligence_runs(id) ON DELETE SET NULL,
      scanner_name TEXT NOT NULL, scanner_version TEXT NOT NULL, instrument TEXT NOT NULL,
      location TEXT NOT NULL, scan_code TEXT NOT NULL, max_results INTEGER NOT NULL,
      filters_json JSONB NOT NULL, config_hash TEXT NOT NULL, status TEXT NOT NULL,
      started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ, error_message TEXT
    );
    CREATE INDEX ix_ib_scanner_runs_started ON ib_scanner_runs(started_at);
    CREATE TABLE ib_scanner_candidates (
      id BIGSERIAL PRIMARY KEY, scanner_run_id BIGINT NOT NULL REFERENCES ib_scanner_runs(id) ON DELETE CASCADE,
      rank INTEGER NOT NULL, ticker TEXT NOT NULL, ib_conid BIGINT,
      contract_metadata_json JSONB NOT NULL, scanner_metadata_json JSONB NOT NULL,
      universe_source TEXT NOT NULL DEFAULT 'IBKR_SCANNER', enrichment_status TEXT NOT NULL DEFAULT 'PENDING',
      promoted_run_id BIGINT REFERENCES upload_runs(id) ON DELETE SET NULL,
      CONSTRAINT uq_ib_scanner_candidate_conid UNIQUE(scanner_run_id, ib_conid)
    );
    CREATE INDEX ix_ib_scanner_candidates_ticker ON ib_scanner_candidates(ticker);

    CREATE TABLE ib_histogram_snapshots (
      id BIGSERIAL PRIMARY KEY, intelligence_run_id BIGINT REFERENCES ib_intelligence_runs(id) ON DELETE SET NULL,
      ticker TEXT NOT NULL, ib_conid BIGINT, requested_period TEXT NOT NULL, use_rth BOOLEAN NOT NULL,
      observed_at TIMESTAMPTZ NOT NULL, reference_price NUMERIC(24,10), availability_status TEXT NOT NULL,
      evidence_hash TEXT NOT NULL UNIQUE, source_semantics TEXT NOT NULL DEFAULT 'IBKR_HISTOGRAM_PRICE_LEVEL_ACTIVITY',
      warnings_json JSONB NOT NULL
    );
    CREATE INDEX ix_ib_histogram_latest ON ib_histogram_snapshots(ticker, observed_at);
    CREATE TABLE ib_histogram_bins (
      id BIGSERIAL PRIMARY KEY, histogram_snapshot_id BIGINT NOT NULL REFERENCES ib_histogram_snapshots(id) ON DELETE CASCADE,
      price NUMERIC(24,10) NOT NULL, activity_count NUMERIC(24,10) NOT NULL,
      activity_rank INTEGER NOT NULL, density_percentile NUMERIC(12,6) NOT NULL,
      CONSTRAINT uq_ib_histogram_bin_price UNIQUE(histogram_snapshot_id, price)
    );

    CREATE TABLE ib_flex_import_runs (
      id BIGSERIAL PRIMARY KEY, intelligence_run_id BIGINT REFERENCES ib_intelligence_runs(id) ON DELETE SET NULL,
      query_type TEXT NOT NULL, query_id_fingerprint TEXT NOT NULL, reference_code_hash TEXT,
      content_hash TEXT, output_format TEXT, status TEXT NOT NULL, dry_run BOOLEAN NOT NULL DEFAULT false,
      row_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0,
      duplicate_count INTEGER NOT NULL DEFAULT 0, corrected_count INTEGER NOT NULL DEFAULT 0,
      error_message TEXT, started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ
    );
    CREATE INDEX ix_ib_flex_import_content_hash ON ib_flex_import_runs(content_hash);
    CREATE INDEX ix_ib_flex_import_started ON ib_flex_import_runs(started_at);

    CREATE TABLE ib_execution_fills (
      id BIGSERIAL PRIMARY KEY, flex_import_run_id BIGINT NOT NULL REFERENCES ib_flex_import_runs(id) ON DELETE RESTRICT,
      external_execution_id TEXT, account_hash TEXT, account_masked_label TEXT, symbol TEXT NOT NULL,
      conid BIGINT, asset_class TEXT, side TEXT NOT NULL, execution_time TIMESTAMPTZ NOT NULL,
      quantity NUMERIC(24,10) NOT NULL, price NUMERIC(24,10) NOT NULL, currency TEXT, exchange TEXT,
      commission NUMERIC(24,10) NOT NULL DEFAULT 0, fees NUMERIC(24,10) NOT NULL DEFAULT 0,
      broker_realized_pnl NUMERIC(24,10), order_reference TEXT, raw_record_hash TEXT NOT NULL,
      supersedes_fill_id BIGINT REFERENCES ib_execution_fills(id) ON DELETE SET NULL,
      is_superseded BOOLEAN NOT NULL DEFAULT false, is_excluded BOOLEAN NOT NULL DEFAULT false,
      exclusion_reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX ix_ib_execution_external_id ON ib_execution_fills(external_execution_id);
    CREATE INDEX ix_ib_execution_symbol_time ON ib_execution_fills(symbol, execution_time);
    CREATE INDEX ix_ib_execution_raw_hash ON ib_execution_fills(raw_record_hash);

    CREATE TABLE ib_trade_episodes (
      id BIGSERIAL PRIMARY KEY, episode_key TEXT NOT NULL UNIQUE, ticker TEXT NOT NULL, direction TEXT NOT NULL,
      opened_at TIMESTAMPTZ NOT NULL, closed_at TIMESTAMPTZ, entry_quantity NUMERIC(24,10) NOT NULL,
      exit_quantity NUMERIC(24,10) NOT NULL, average_entry_price NUMERIC(24,10) NOT NULL,
      average_exit_price NUMERIC(24,10), deployed_entry_capital NUMERIC(24,10) NOT NULL,
      gross_pnl NUMERIC(24,10), broker_realized_pnl NUMERIC(24,10), commissions NUMERIC(24,10) NOT NULL,
      fees NUMERIC(24,10) NOT NULL, net_pnl NUMERIC(24,10), return_pct NUMERIC(18,8),
      holding_seconds BIGINT, status TEXT NOT NULL, matching_policy TEXT NOT NULL DEFAULT 'FIFO_POSITION_V1',
      fill_ids_json JSONB NOT NULL, is_excluded BOOLEAN NOT NULL DEFAULT false
    );
    CREATE INDEX ix_ib_trade_episode_ticker_opened ON ib_trade_episodes(ticker, opened_at);

    CREATE TABLE ib_trade_research_links (
      id BIGSERIAL PRIMARY KEY, trade_episode_id BIGINT NOT NULL UNIQUE REFERENCES ib_trade_episodes(id) ON DELETE CASCADE,
      upload_run_id BIGINT REFERENCES upload_runs(id) ON DELETE SET NULL,
      combined_result_id BIGINT REFERENCES combined_results(id) ON DELETE SET NULL,
      technical_score_id BIGINT REFERENCES technical_scores(id) ON DELETE SET NULL,
      fundamental_score_id BIGINT REFERENCES fundamental_scores(id) ON DELETE SET NULL,
      matching_status TEXT NOT NULL, matching_policy TEXT NOT NULL, decision_timestamp TIMESTAMPTZ,
      context_json JSONB NOT NULL, leakage_check TEXT NOT NULL, ambiguity_json JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS ib_trade_research_links;
    DROP TABLE IF EXISTS ib_trade_episodes;
    DROP TABLE IF EXISTS ib_execution_fills;
    DROP TABLE IF EXISTS ib_flex_import_runs;
    DROP TABLE IF EXISTS ib_histogram_bins;
    DROP TABLE IF EXISTS ib_histogram_snapshots;
    DROP TABLE IF EXISTS ib_scanner_candidates;
    DROP TABLE IF EXISTS ib_scanner_runs;
    DROP TABLE IF EXISTS ib_scanner_parameter_cache;
    DROP TABLE IF EXISTS ib_intelligence_features;
    DROP TABLE IF EXISTS ib_market_intelligence_snapshots;
    DROP TABLE IF EXISTS ib_historical_metric_revisions;
    DROP TABLE IF EXISTS ib_historical_metric_bars;
    DROP TABLE IF EXISTS ib_intelligence_request_items;
    DROP TABLE IF EXISTS ib_intelligence_runs;
    """)
