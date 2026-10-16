from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from alembic.config import Config
from native_mutation_support import seed_native_core
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    CoreCalculationEvidence,
    MarketRegimeSnapshot,
    PriceBar,
    SectorRotationSnapshot,
)
from app.services.contextual_consumer_eligibility import CONTEXTUAL_ELIGIBILITY_KEY
from app.services.contextual_effective_configuration import (
    resolve_regime_configuration,
    resolve_sector_configuration,
)
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.producer_readiness import ReadinessStatus, readiness_from_evidence
from app.services.sector_rotation_service import SectorRotationService
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    _validate_handoff_temporal_lineage,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.fixture
def run35_engine(disposable_postgres_database: str):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def test_run35_distribution_chain_persists_included_lineage_and_handoff_passes(
    run35_engine,
) -> None:
    with Session(run35_engine, expire_on_commit=False) as db:
        cutoff, _, _ = seed_native_core(
            db,
            include_decision_context=True,
            price_frame=_distribution_frame(),
        )

        market = db.scalar(
            select(MarketRegimeSnapshot).where(
                MarketRegimeSnapshot.run_id == 7,
                MarketRegimeSnapshot.is_current_revision.is_(True),
            )
        )
        sector = db.scalar(
            select(SectorRotationSnapshot).where(
                SectorRotationSnapshot.run_id == 7,
                SectorRotationSnapshot.is_current_revision.is_(True),
            )
        )
        assert market is not None and sector is not None
        assert "qqq_distribution" in market.warnings_json
        assert "market_risk_off" in market.warnings_json

        market_evidence = db.get(CoreCalculationEvidence, market.evidence_id)
        sector_evidence = db.get(CoreCalculationEvidence, sector.evidence_id)
        readiness = readiness_from_evidence(market_evidence)
        permission = sector.debug_json[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]

        assert readiness.status is ReadinessStatus.READY
        assert permission["included"] is True
        assert permission["decision"]["status"] == "ELIGIBLE"
        assert permission["source_feature_id"] == market.id
        assert permission["decision"]["producer_evidence_id"] == market.evidence_id
        assert sector.market_regime_snapshot_id == market.id
        assert sector_evidence.source_evidence_ids_json["regime"] == market.evidence_id

        built_rows = _handoff_rows(db, run_id=7, cutoff=cutoff)
        _validate_handoff_temporal_lineage(
            db,
            upload_run_id=7,
            market_cutoff=cutoff,
            built_rows=built_rows,
        )

        sector.market_regime_snapshot_id = None
        with (
            db.no_autoflush,
            pytest.raises(
                TransitionPreflightError,
                match="sector_market_parent",
            ),
        ):
            _validate_handoff_temporal_lineage(
                db,
                upload_run_id=7,
                market_cutoff=cutoff,
                built_rows=built_rows,
            )
        db.rollback()


def test_handoff_accepts_authorized_regime_exclusion_and_rejects_forged_proof(
    run35_engine,
) -> None:
    regime_configuration = resolve_regime_configuration()
    sector_configuration = resolve_sector_configuration()
    with Session(run35_engine, expire_on_commit=False) as db:
        cutoff, _, _ = seed_native_core(
            db,
            extra_configurations=(regime_configuration, sector_configuration),
        )
        db.execute(delete(PriceBar).where(PriceBar.ticker == "QQQ"))
        db.flush()

        MarketRegimeCommandCenterService().build_snapshot(
            db,
            run_id=7,
            market_cutoff=cutoff,
            effective_configuration=regime_configuration,
        )
        SectorRotationService().build_sector_rotation_snapshot(
            db,
            7,
            market_cutoff=cutoff,
            effective_configuration=sector_configuration,
        )
        db.flush()

        market = db.scalar(
            select(MarketRegimeSnapshot).where(
                MarketRegimeSnapshot.run_id == 7,
                MarketRegimeSnapshot.is_current_revision.is_(True),
            )
        )
        sector = db.scalar(
            select(SectorRotationSnapshot).where(
                SectorRotationSnapshot.run_id == 7,
                SectorRotationSnapshot.is_current_revision.is_(True),
            )
        )
        assert market is not None and sector is not None
        market_evidence = db.get(CoreCalculationEvidence, market.evidence_id)
        sector_evidence = db.get(CoreCalculationEvidence, sector.evidence_id)
        permission = sector.debug_json[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]

        assert readiness_from_evidence(market_evidence).status is ReadinessStatus.STALE
        assert permission["included"] is False
        assert permission["decision"]["status"] == "INELIGIBLE"
        assert permission["source_feature_id"] == market.id
        assert permission["decision"]["producer_evidence_id"] == market.evidence_id
        assert sector.market_regime_snapshot_id is None
        assert sector_evidence.source_evidence_ids_json["regime"] == market.evidence_id

        built_rows = _handoff_rows(db, run_id=7, cutoff=cutoff)
        _validate_handoff_temporal_lineage(
            db,
            upload_run_id=7,
            market_cutoff=cutoff,
            built_rows=built_rows,
        )

        original_debug = deepcopy(sector.debug_json)
        original_payload = deepcopy(sector_evidence.payload_json)
        for corrupt in ("missing", "forged"):
            changed = deepcopy(original_debug)
            changed_payload = deepcopy(original_payload)
            projected_permission = changed[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]
            evidence_permission = changed_payload["debug"][CONTEXTUAL_ELIGIBILITY_KEY]["regime"]
            if corrupt == "missing":
                del changed[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]
                del changed_payload["debug"][CONTEXTUAL_ELIGIBILITY_KEY]["regime"]
            else:
                projected_permission["decision"]["producer_evidence_id"] += 1
                evidence_permission["decision"]["producer_evidence_id"] += 1
            sector.debug_json = changed
            sector_evidence.payload_json = changed_payload
            with (
                db.no_autoflush,
                pytest.raises(
                    TransitionPreflightError,
                    match="sector_market_parent",
                ),
            ):
                _validate_handoff_temporal_lineage(
                    db,
                    upload_run_id=7,
                    market_cutoff=cutoff,
                    built_rows=built_rows,
                )
        db.rollback()


def _handoff_rows(db: Session, *, run_id: int, cutoff):
    run_context = SetupLifecycleSourceLoader().load_run_context(
        db,
        run_id,
        market_cutoff=cutoff,
    )
    return tuple((context, SimpleNamespace()) for context in run_context.tickers)


def _distribution_frame() -> pd.DataFrame:
    row_count = 320
    close = np.linspace(100.0, 180.0, row_count)
    close[-8:] = [178.0, 176.0, 179.0, 175.0, 180.0, 174.0, 181.0, 173.0]
    volume = np.linspace(100_000.0, 120_000.0, row_count)
    volume[-8:] = [
        121_000.0,
        122_000.0,
        123_000.0,
        124_000.0,
        125_000.0,
        126_000.0,
        127_000.0,
        128_000.0,
    ]
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=row_count, freq="D"),
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": volume,
        }
    )
