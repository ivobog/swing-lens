from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBExecutionFill,
    IBFlexImportRun,
    IBHistoricalMetricRevision,
    IBScannerCandidate,
    IBScannerRun,
    IBTradeEpisode,
)
from app.models.tables import CombinedResult, SetupSignalSnapshot, UploadRun
from app.services.ceri import capture_service as ceri_capture_service
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.dtos import FeatureResult, HistoricalMetricBarDTO
from app.services.ib_market_intelligence.enums import AvailabilityStatus, Confidence
from app.services.ib_market_intelligence.flex import import_flex_report
from app.services.ib_market_intelligence.journal import (
    match_episode_to_research,
    rebuild_trade_episodes,
)
from app.services.ib_market_intelligence.query_service import (
    latest_features,
    overview,
    scanner_runs,
    trade_journal,
)
from app.services.ib_market_intelligence.repository import (
    persist_feature,
    persist_historical_metric_bar,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_metric_revision_and_flex_import_are_idempotent(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    env = {**os.environ, "DATABASE_URL": disposable_postgres_database}
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(disposable_postgres_database)
    indexes = {item["name"]: item for item in inspect(engine).get_indexes("ib_execution_fills")}
    assert indexes["uq_ib_execution_raw_hash"]["unique"] is True
    assert indexes["uq_ib_execution_active_external"]["unique"] is True
    dto = HistoricalMetricBarDTO(
        ticker="XYZ",
        ib_conid=123,
        session_date=date(2026, 8, 7),
        timeframe="1 day",
        metric_type="FEE_RATE",
        open_value=1.0,
        high_value=1.5,
        low_value=0.8,
        close_value=1.2,
        requested_range="60 D",
        source_semantic_type="BORROW_FEE_RATE",
    )
    changed = HistoricalMetricBarDTO(**{**dto.__dict__, "close_value": 2.2})
    with Session(engine) as db:
        row, outcome = persist_historical_metric_bar(db, dto)
        assert outcome == "INSERTED"
        _, outcome = persist_historical_metric_bar(db, dto)
        assert outcome == "UNCHANGED"
        row, outcome = persist_historical_metric_bar(db, changed)
        assert outcome == "REVISED" and row.revision_count == 1
        db.commit()
        revisions = db.scalars(select(IBHistoricalMetricRevision)).all()
        assert len(revisions) == 1

        report = (
            "AccountId,TradeID,TradeDate,TradeTime,Symbol,Buy/Sell,Quantity,"
            "TradePrice,IBCommission,Fees,Currency\n"
            "U123,E1,20260807,093000,XYZ,BUY,10,25,1,0.1,USD\n"
        )
        first = import_flex_report(
            db,
            content=report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="QUERY",
            reference_code="REF",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
        assert first["inserted"] == 1
        db.commit()
        second = import_flex_report(
            db,
            content=report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="QUERY",
            reference_code="REF2",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
        assert second["status"] == "DUPLICATE_REPORT"
        assert len(db.scalars(select(IBExecutionFill)).all()) == 1

        rebuild_trade_episodes(db)
        db.commit()
        corrected_report = report.replace("BUY,10,25", "BUY,10,26")
        corrected = import_flex_report(
            db,
            content=corrected_report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="QUERY",
            reference_code="REF3",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
        assert corrected["corrected"] == 1
        rebuilt = rebuild_trade_episodes(db)
        db.commit()
        assert len(rebuilt) == 1
        episodes = db.execute(
            select(IBTradeEpisode.status).order_by(IBTradeEpisode.id)
        ).scalars().all()
        assert episodes == ["SUPERSEDED", "OPEN"]
        active_fills = db.scalars(
            select(IBExecutionFill).where(IBExecutionFill.is_superseded.is_(False))
        ).all()
        assert len(active_fills) == 1
        journal_payload = trade_journal(db)
        assert len(journal_payload["episodes"]) == 1
        assert sorted(fill["is_superseded"] for fill in journal_payload["fills"]) == [
            False,
            True,
        ]

        config = load_ib_market_intelligence_config()
        available = FeatureResult(
            module="VOLATILITY",
            classification="ELEVATED_IV_PREMIUM",
            score=5.0,
            confidence=Confidence.HIGH,
            freshness_status=AvailabilityStatus.AVAILABLE,
            coverage_status=AvailabilityStatus.AVAILABLE,
            components={"iv_hv_ratio": 2.0},
            evidence_hashes=("iv-raw", "hv-raw"),
        )
        unavailable = FeatureResult(
            **{
                **available.__dict__,
                "confidence": Confidence.LOW,
                "coverage_status": AvailabilityStatus.SUBSCRIPTION_REQUIRED,
                "warnings": ("OPTIONS_SUBSCRIPTION_REQUIRED",),
            }
        )
        first_feature, inserted = persist_feature(
            db,
            ticker="XYZ",
            ib_conid=123,
            as_of_session=date(2026, 8, 9),
            feature=available,
            config=config,
            calculated_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        )
        assert inserted is True
        second_feature, inserted = persist_feature(
            db,
            ticker="XYZ",
            ib_conid=123,
            as_of_session=date(2026, 8, 9),
            feature=unavailable,
            config=config,
            calculated_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC) + timedelta(seconds=1),
        )
        assert inserted is True and second_feature.id != first_feature.id
        db.commit()
        latest = latest_features(db, ticker="XYZ")
        assert latest[0]["coverage_status"] == AvailabilityStatus.SUBSCRIPTION_REQUIRED
        assert overview(db)["cards"]["volatility"]["coverage"] == 0
        monkeypatch.setattr(
            ceri_capture_service,
            "get_settings",
            lambda: SimpleNamespace(
                ib_market_intelligence_enabled=True,
                ib_volatility_intelligence_enabled=True,
                ib_short_pressure_enabled=True,
            ),
        )
        assert (
            ceri_capture_service._point_in_time_volatility_feature(
                db, "XYZ", datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
            ).id
            == first_feature.id
        )
        assert (
            ceri_capture_service._point_in_time_volatility_feature(
                db, "XYZ", datetime(2026, 8, 9, 12, 2, tzinfo=UTC)
            )
            is None
        )
        short_context = FeatureResult(
            module="SHORT_PRESSURE",
            classification="HIGH_BORROW_COST",
            score=7.0,
            confidence=Confidence.NORMAL,
            freshness_status=AvailabilityStatus.AVAILABLE,
            coverage_status=AvailabilityStatus.AVAILABLE,
            components={"fee_rate": 15.0},
            evidence_hashes=("fee-local",),
        )
        short_row, inserted = persist_feature(
            db,
            ticker="XYZ",
            ib_conid=123,
            as_of_session=date(2026, 8, 9),
            feature=short_context,
            config=config,
            calculated_at=datetime(2026, 8, 9, 12, 1, tzinfo=UTC),
        )
        assert inserted is True
        db.commit()
        ceri_short = ceri_capture_service._point_in_time_short_pressure_feature(
            db, "XYZ", datetime(2026, 8, 9, 12, 2, tzinfo=UTC)
        )
        assert ceri_short is not None
        assert ceri_short.id == short_row.id
        assert ceri_short.classification == "HIGH_BORROW_COST"

        resolved_scan = IBScannerRun(
            scanner_name="HOT_VOLUME_US",
            scanner_version="1",
            instrument="STK",
            location="STK.US.MAJOR",
            scan_code="HOT_BY_VOLUME",
            max_results=50,
            filters_json=[],
            config_hash=config.config_hash,
            status="COMPLETED",
            started_at=datetime(2026, 8, 9, 13, 0, tzinfo=UTC),
            completed_at=datetime(2026, 8, 9, 13, 1, tzinfo=UTC),
        )
        unresolved_scan = IBScannerRun(
            scanner_name="MOST_ACTIVE_US",
            scanner_version="1",
            instrument="STK",
            location="STK.US.MAJOR",
            scan_code="MOST_ACTIVE",
            max_results=50,
            filters_json=[],
            config_hash=config.config_hash,
            status="COMPLETED",
            started_at=datetime(2026, 8, 9, 13, 2, tzinfo=UTC),
            completed_at=datetime(2026, 8, 9, 13, 3, tzinfo=UTC),
        )
        db.add_all((resolved_scan, unresolved_scan))
        db.flush()
        db.add_all(
            (
                IBScannerCandidate(
                    scanner_run_id=resolved_scan.id,
                    rank=1,
                    ticker="AAPL",
                    ib_conid=265598,
                    contract_metadata_json={"sec_type": "STK", "currency": "USD"},
                    scanner_metadata_json={},
                    universe_source="IBKR_SCANNER",
                    enrichment_status="PENDING",
                ),
                IBScannerCandidate(
                    scanner_run_id=unresolved_scan.id,
                    rank=3,
                    ticker="AAPL",
                    ib_conid=None,
                    contract_metadata_json={"sec_type": "STK", "currency": "USD"},
                    scanner_metadata_json={},
                    universe_source="IBKR_SCANNER",
                    enrichment_status="PENDING",
                ),
            )
        )
        db.commit()
        candidate_pool = scanner_runs(db)["candidate_pool"]
        assert len(candidate_pool) == 1
        assert candidate_pool[0]["canonical_identity"] == "CONID:265598"
        assert candidate_pool[0]["ib_conid"] == 265598
        assert len(candidate_pool[0]["discovery_reasons"]) == 2
        assert overview(db)["cards"]["scanner_candidates"] == 1

        entry_time = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
        eligible_run = UploadRun(
            filename="eligible.csv",
            status="COMPLETED",
            processed_at=entry_time - timedelta(hours=1),
        )
        future_run = UploadRun(
            filename="future.csv",
            status="COMPLETED",
            processed_at=entry_time - timedelta(minutes=30),
        )
        db.add_all((eligible_run, future_run))
        db.flush()
        eligible_combined = CombinedResult(
            run_id=eligible_run.id,
            ticker="PIT",
            final_score=7.5,
            combined_decision="Candidate",
            created_at=entry_time - timedelta(hours=1),
        )
        future_combined = CombinedResult(
            run_id=future_run.id,
            ticker="PIT",
            final_score=9.9,
            combined_decision="Strong candidate",
            created_at=entry_time + timedelta(minutes=1),
        )
        db.add_all((eligible_combined, future_combined))
        db.flush()
        db.add(
            SetupSignalSnapshot(
                run_id=eligible_run.id,
                combined_result_id=eligible_combined.id,
                ticker="PIT",
                timeframe="1D",
                data_as_of_date=entry_time.date(),
                calculated_at=entry_time - timedelta(minutes=45),
                origin_type="LIVE",
                engine_version="test",
                config_version="test",
                config_hash="pit-config",
                source_data_hash="pit-source",
                schema_version="1",
                data_quality_label="COMPLETE",
                close_price=100,
                primary_setup_family="BREAKOUT",
            )
        )
        episode = IBTradeEpisode(
            episode_key="pit-slippage-e2e",
            ticker="PIT",
            direction="LONG",
            opened_at=entry_time,
            entry_quantity=10,
            exit_quantity=0,
            average_entry_price=102,
            deployed_entry_capital=1020,
            commissions=0,
            fees=0,
            status="OPEN",
            fill_ids_json=[],
            is_excluded=False,
        )
        db.add(episode)
        db.flush()
        link = match_episode_to_research(db, episode)
        assert link.matching_status == "MATCHED"
        assert link.upload_run_id == eligible_run.id
        assert link.leakage_check == "PASS"
        assert link.context_json["slippage_reference"] == "SETUP_DECISION_CLOSE"
        assert Decimal(link.context_json["execution_slippage_pct"]) == Decimal("2")

        today = datetime.now(UTC)
        db.add(
            IBFlexImportRun(
                query_type="ACTIVITY",
                query_id_fingerprint="daily",
                status="COMPLETED",
                dry_run=False,
                started_at=today - timedelta(minutes=1),
                completed_at=today,
            )
        )
        db.flush()
        from app.services.ib_market_intelligence import orchestration

        assert orchestration._activity_imported_today(db, "Europe/Zurich") is True
    engine.dispose()
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0031_add_ib_market_intelligence"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    reupgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert reupgrade.returncode == 0, reupgrade.stdout + reupgrade.stderr
