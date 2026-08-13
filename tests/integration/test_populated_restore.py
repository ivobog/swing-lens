from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every mapped table
from app.db import Base
from app.services.readiness_service import ReadinessService
from app.services.worker_registry import register_worker
from app.settings import Settings
from scripts.ops.evidence_manifest import (
    DEFAULT_EVIDENCE_TABLES,
    capture_database_manifest,
    compare_database_to_manifest,
    read_manifest,
    write_comparison_report,
    write_manifest,
)
from scripts.ops.validate_restore import validate_database, write_report

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_populated_multi_module_backup_restore_preserves_evidence(
    disposable_postgres_database_factory: Callable[[], AbstractContextManager[str]],
    tmp_path: Path,
) -> None:
    pg_dump = _require_postgres_tool("pg_dump")
    pg_restore = _require_postgres_tool("pg_restore")
    dump_path = tmp_path / "populated_swinglens.dump"
    source_manifest_path = tmp_path / "source_evidence_manifest.json"
    comparison_path = tmp_path / "restored_evidence_comparison.json"
    validation_path = tmp_path / "restore_validation.json"

    with disposable_postgres_database_factory() as source_url:
        with disposable_postgres_database_factory() as restored_url:
            _upgrade_to_head(source_url)
            expected_ids = _seed_representative_evidence(source_url)
            source_engine = create_engine(source_url, pool_pre_ping=True)
            expected_manifest = capture_database_manifest(source_engine)
            write_manifest(expected_manifest, source_manifest_path)
            source_engine.dispose()

            subprocess.run(
                [
                    pg_dump,
                    "--format=custom",
                    "--no-owner",
                    f"--file={dump_path}",
                    _client_database_url(source_url),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    pg_restore,
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--exit-on-error",
                    f"--dbname={_client_database_url(restored_url)}",
                    str(dump_path),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            restored_engine = create_engine(restored_url, pool_pre_ping=True)
            validation = validate_database(restored_engine)
            write_report(validation, validation_path)
            comparison = compare_database_to_manifest(
                restored_engine,
                read_manifest(source_manifest_path),
            )
            write_comparison_report(comparison, comparison_path)
            readiness_settings = _readiness_settings(tmp_path, restored_url)
            with Session(restored_engine) as session:
                register_worker(
                    session,
                    worker_id=readiness_settings.job_worker_id,
                    queues=("background",),
                    heartbeat_timeout_seconds=(
                        readiness_settings.job_worker_heartbeat_timeout_seconds
                    ),
                )
                session.commit()
            readiness = ReadinessService(
                engine=restored_engine,
                settings=readiness_settings,
            ).report()
            restored_engine.dispose()

    assert dump_path.stat().st_size > 0
    assert validation.passed is True
    assert comparison.passed is True
    assert comparison.row_count_mismatches == {}
    assert comparison.content_hash_mismatches == {}
    assert readiness.status == "ok"
    assert readiness.database_ok is True
    assert readiness.checks["migrations"].ok is True
    assert set(expected_manifest.tables) == set(DEFAULT_EVIDENCE_TABLES)
    assert all(table.row_count >= 1 for table in expected_manifest.tables.values())
    assert expected_ids == {
        "upload_run_id": 1,
        "raw_row_id": 1,
        "fundamental_score_id": 1,
        "technical_score_id": 1,
        "combined_result_id": 1,
        "market_regime_snapshot_id": 1,
        "sector_rotation_snapshot_id": 1,
        "ceri_company_id": 1,
    }


def _upgrade_to_head(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _seed_representative_evidence(database_url: str) -> dict[str, int]:
    engine = create_engine(database_url)
    captured_at = datetime(2026, 8, 5, 20, 0, tzinfo=UTC)
    as_of = date(2026, 8, 5)
    tables = Base.metadata.tables

    with engine.begin() as connection:
        upload_run_id = _insert_id(
            connection,
            tables["upload_runs"],
            filename="qa_restore_Žürich.csv",
            uploaded_at=captured_at,
            processed_at=captured_at,
            row_count=1,
            status="COMPLETED",
            pine_engine_version="pine-qa-v1",
            python_engine_version="python-qa-v1",
            notes="M-06 populated restore fixture",
        )
        raw_row_id = _insert_id(
            connection,
            tables["raw_company_rows"],
            run_id=upload_run_id,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft Zürich QA",
            sector="Technology",
            sector_canonical="Technology",
            sector_taxonomy="swinglens-sector-v1",
            sector_mapping_status="canonical",
            raw_json={
                "Symbol": "MSFT",
                "Company": "Microsoft Zürich QA",
                "Revenue Growth %": "12.50%",
                "raw_warning": "preserve exactly",
            },
            created_at=captured_at,
        )
        connection.execute(
            insert(tables["price_bars"]).values(
                ticker="MSFT",
                bar_date=as_of,
                timeframe="1 day",
                open="101.25",
                high="104.00",
                low="100.50",
                close="103.75",
                volume="1234567",
                source="IB_FAKE",
                what_to_show="TRADES",
                adjustment_type="raw",
                first_seen_at=captured_at,
                last_seen_at=captured_at,
                revision_count=0,
                data_hash="price-sha256-msft-20260805",
            )
        )
        fundamental_score_id = _insert_id(
            connection,
            tables["fundamental_scores"],
            run_id=upload_run_id,
            ticker="MSFT",
            fundamental_score="8.200000",
            fundamental_label="Strong",
            data_coverage_score="0.920000",
            scoring_model_version="fundamental-qa-v1",
            v2_warning_flags_json={"warnings": []},
            debug_json={"source_row": raw_row_id},
            created_at=captured_at,
        )
        technical_score_id = _insert_id(
            connection,
            tables["technical_scores"],
            run_id=upload_run_id,
            ticker="MSFT",
            trend_score="8.100000",
            momentum_score="7.900000",
            setup_score="8.300000",
            risk_score="7.500000",
            dual_score="8.000000",
            classification="Clean bull pullback",
            technical_confidence="high",
            technical_engine_version="technical-qa-v1",
            insufficient_data=False,
            warning_flags_json=[],
            debug_json={"cutoff": captured_at.isoformat()},
            created_at=captured_at,
        )
        combined_result_id = _insert_id(
            connection,
            tables["combined_results"],
            run_id=upload_run_id,
            ticker="MSFT",
            company_name="Microsoft Zürich QA",
            sector="Technology",
            final_rank=1,
            final_score="8.100000",
            fundamental_score="8.200000",
            fundamental_label="Strong",
            technical_classification="Clean bull pullback",
            dual_score="8.000000",
            combined_decision="Strong candidate",
            position_size_hint="normal",
            warning_flags_json=[],
            is_complete=True,
            has_fundamental=True,
            has_technical=True,
            has_warning=False,
            calculation_version="combined-qa-v1",
            config_hash="combined-config-sha256",
            debug_json={"advisory_overlays_mutated": False},
            created_at=captured_at,
        )
        market_regime_snapshot_id = _insert_id(
            connection,
            tables["market_regime_snapshots"],
            run_id=upload_run_id,
            as_of_date=as_of,
            calculation_version="mrcc-qa-v1",
            config_version="market-config-v1",
            regime="Confirmed Uptrend",
            risk_state="Green",
            score=8.0,
            confidence="high",
            action_summary="Constructive research environment",
            input_symbols_json={"SPY": as_of.isoformat(), "QQQ": as_of.isoformat()},
            reasons_json=["breadth_constructive"],
            warnings_json=[],
            debug_json={"fixture": "M-06"},
            evidence_hash="market-evidence-sha256",
            created_at=captured_at,
        )
        sector_rotation_snapshot_id = _insert_id(
            connection,
            tables["sector_rotation_snapshots"],
            run_id=upload_run_id,
            market_regime_snapshot_id=market_regime_snapshot_id,
            as_of_date=as_of,
            calculation_version="sector-qa-v1",
            config_version="sector-config-v1",
            config_hash="sector-config-sha256",
            mode="universe_only",
            default_ranking_profile="momentum_swing",
            benchmark_ticker="SPY",
            sector_count=1,
            ticker_count=1,
            leading_sector="Technology",
            summary_json={"Technology": "Leading"},
            warning_flags_json=[],
            debug_json={"fixture": "M-06"},
            evidence_hash="sector-evidence-sha256",
            created_at=captured_at,
        )
        pipeline_run_id = _insert_id(
            connection,
            tables["pipeline_runs"],
            upload_run_id=upload_run_id,
            status="COMPLETED",
            current_step="COMBINED_RESULTS",
            requested_by="qa-m06",
            started_at=captured_at,
            completed_at=captured_at,
            result_json={"successful_tickers": ["MSFT"], "failed_tickers": []},
        )
        connection.execute(
            insert(tables["pipeline_steps"]).values(
                pipeline_run_id=pipeline_run_id,
                step_name="COMBINED_RESULTS",
                step_order=7,
                status="COMPLETED",
                started_at=captured_at,
                completed_at=captured_at,
                result_json={"rows": 1},
            )
        )
        connection.execute(
            insert(tables["background_jobs"]).values(
                job_type="FULL_PIPELINE",
                related_run_id=upload_run_id,
                request_key="qa-m06-full-pipeline",
                status="COMPLETED",
                payload_json={"pipeline_run_id": pipeline_run_id},
                result_json={"status": "COMPLETED"},
                completed_at=captured_at,
                operational_metadata_json={"fixture": "M-06"},
            )
        )
        connection.execute(
            insert(tables["setup_signal_snapshots"]).values(
                run_id=upload_run_id,
                source_run_id_text=str(upload_run_id),
                raw_row_id=raw_row_id,
                fundamental_score_id=fundamental_score_id,
                technical_score_id=technical_score_id,
                combined_result_id=combined_result_id,
                market_regime_snapshot_id=market_regime_snapshot_id,
                sector_rotation_snapshot_id=sector_rotation_snapshot_id,
                ticker="MSFT",
                company_name="Microsoft Zürich QA",
                sector="Technology",
                timeframe="1d",
                data_as_of_date=as_of,
                calculated_at=captured_at,
                captured_at=captured_at,
                origin_type="authoritative_daily_close",
                engine_version="slse-qa-v1",
                config_version="slse-config-v1",
                config_hash="slse-config-sha256",
                source_data_hash="slse-source-sha256",
                schema_version="slse-schema-v1",
                is_canonical=True,
                canonical_reason="completed_daily_close",
                canonicalized_at=captured_at,
                primary_setup_family="breakout",
                lifecycle_state_candidate="READY",
                actionability_candidate="ACTIONABLE",
                data_quality_label="HIGH",
                confidence_score=92,
                confidence_label="HIGH",
                signals_json={"close_above_trigger": True},
                feature_flags_json={"slse_enabled": True},
                warning_flags_json=[],
                missing_data_json={},
                source_lineage_json={"price_bar_hash": "price-sha256-msft-20260805"},
                diagnostic_high_cross_json={},
                canonical_decision_json={"authoritative": True},
                debug_json={"fixture": "M-06"},
            )
        )
        connection.execute(
            insert(tables["setup_lifecycle_administrative_audit_events"]).values(
                event_type="REPAIR_PREVIEWED",
                requester="qa-m06",
                reason="verify immutable administrative audit restoration",
                preview_token_hash="slse-preview-token-sha256",
                scope_json={"ticker": "MSFT", "run_id": upload_run_id},
                before_json={"canonical_snapshot_id": 1},
                after_json={"canonical_snapshot_id": 1},
                affected_counts_json={"snapshots": 0},
                created_at=captured_at,
            )
        )
        connection.execute(
            insert(tables["winner_prediction_snapshots"]).values(
                run_id=upload_run_id,
                raw_row_id=raw_row_id,
                combined_result_id=combined_result_id,
                market_regime_snapshot_id=market_regime_snapshot_id,
                sector_rotation_snapshot_id=sector_rotation_snapshot_id,
                ticker="MSFT",
                prediction_as_of_date=as_of,
                source_data_cutoff_at=captured_at,
                captured_at=captured_at,
                planned_entry_session=date(2026, 8, 6),
                entry_schedule_status="PLANNED",
                entry_data_status="AVAILABLE",
                eligibility_status="ELIGIBLE",
                setup_family="breakout",
                ranking_profile="momentum_swing",
                fundamental_score="8.2000",
                technical_score="8.0000",
                combined_score="8.1000",
                market_regime="Confirmed Uptrend",
                market_risk_state="Green",
                sector_state="Leading",
                feature_schema_version="owpe-schema-v1",
                feature_vector_hash="winner-feature-sha256",
                config_hash="winner-config-sha256",
                calculation_version="owpe-qa-v1",
                feature_json={"combined_score": "8.1000", "cutoff": captured_at.isoformat()},
                source_ids_json={"raw_row_id": raw_row_id},
                warning_flags_json=[],
                lineage_json={"source_data_hash": "slse-source-sha256"},
                retention_class="permanent",
            )
        )
        connection.execute(
            insert(tables["winner_evidence_manifests"]).values(
                manifest_hash="winner-manifest-sha256",
                hash_algorithm="sha256",
                content_encoding="json-canonical-v1",
                member_count=3,
                compressed_size_bytes=256,
                payload_json={"members": ["raw", "combined", "market"]},
                created_at=captured_at,
            )
        )
        ceri_company_id = _insert_id(
            connection,
            tables["ceri_companies"],
            ticker="MSFT",
            exchange="NASDAQ",
            company_name="Microsoft Zürich QA",
            current_provider_ids_json={"manual_fixture": "MSFT-QA"},
            created_at=captured_at,
        )
        connection.execute(
            insert(tables["ceri_source_records"]).values(
                provider="manual_fixture",
                provider_terms_version="qa-only-v1",
                dataset="earnings",
                provider_record_id="msft-2026q4",
                company_hint_json={"ticker": "MSFT"},
                published_at=captured_at,
                observed_at=captured_at,
                ingested_at=captured_at,
                source_reference="local-fixture-msft-2026q4",
                raw_json={"estimate": "3.25", "currency": "USD"},
                content_hash="ceri-source-sha256",
                idempotency_key="manual_fixture:earnings:msft-2026q4",
                export_policy="exportable",
            )
        )
        connection.execute(
            insert(tables["ceri_revision_features"]).values(
                company_id=ceri_company_id,
                metric="eps",
                period_key="2026Q4",
                as_of_session=as_of,
                window_days=30,
                actual_elapsed_days=30,
                absolute_change="0.150000",
                pct_change="0.048387",
                upward_count=4,
                downward_count=1,
                revision_confidence_score=0.9,
                revision_confidence_label="HIGH",
                warnings_json=[],
                source_observation_ids_json=["msft-2026q4"],
                provider_selection_reason="manual deterministic fixture",
                evidence_hash="ceri-revision-evidence-sha256",
                config_version="ceri-config-v1",
                config_hash="ceri-config-sha256",
                calculation_version="ceri-revision-qa-v1",
            )
        )
        connection.execute(
            insert(tables["ceri_score_snapshots"]).values(
                run_id=upload_run_id,
                source_run_id_text=str(upload_run_id),
                company_id=ceri_company_id,
                ticker="MSFT",
                as_of_session=as_of,
                cutoff_at=captured_at,
                opportunity_score=82.5,
                event_risk_score=18.0,
                data_confidence="HIGH",
                coverage_pct=100.0,
                posture="CONSTRUCTIVE",
                alignment_flags_json={"revisions_positive": True},
                component_json={"revision": 82.5},
                reasons_json=["positive_revision_breadth"],
                warnings_json=[],
                config_version="ceri-config-v1",
                config_hash="ceri-config-sha256",
                calculation_version="ceri-score-qa-v1",
                evidence_hash="ceri-score-evidence-sha256",
                created_at=captured_at,
            )
        )
        connection.execute(
            insert(tables["ceri_purge_audits"]).values(
                provider="manual_fixture",
                license_scope="qa-only",
                preview_manifest_hash="ceri-purge-preview-sha256",
                actor="qa-m06",
                reason="verify purge audit preservation without executing a purge",
                confirmation_token_hash="ceri-confirmation-token-sha256",
                affected_counts_json={"source_records": 0},
                invalidated_derivatives_json={"scores": 0},
                status="PREVIEWED",
                previewed_at=captured_at,
            )
        )

    engine.dispose()
    return {
        "upload_run_id": upload_run_id,
        "raw_row_id": raw_row_id,
        "fundamental_score_id": fundamental_score_id,
        "technical_score_id": technical_score_id,
        "combined_result_id": combined_result_id,
        "market_regime_snapshot_id": market_regime_snapshot_id,
        "sector_rotation_snapshot_id": sector_rotation_snapshot_id,
        "ceri_company_id": ceri_company_id,
    }


def _insert_id(connection, table, **values) -> int:
    statement = insert(table).values(**values).returning(table.c.id)
    return int(connection.execute(statement).scalar_one())


def _readiness_settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        upload_dir=tmp_path / "uploads",
        export_dir=tmp_path / "exports",
        cache_dir=tmp_path / "cache",
        job_worker_enabled=True,
        use_durable_pipeline=True,
    )


def _client_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _require_postgres_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is required for the populated restore integration test")
    return executable
