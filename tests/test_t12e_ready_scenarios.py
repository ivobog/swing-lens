"""These native READY inputs run unchanged against the pre-enforcement checkout."""

from ceri_ready_helpers import rated_ceri_snapshot
from test_t12c_ready_scenarios import ready_ceri_sources

from app.services.ceri import capture_service as capture
from app.services.ib_market_intelligence.calculations import options_event_premium_score
from app.services.producer_readiness import ReadinessStatus, normalize_producer_readiness


def test_ready_prior_sector_preserves_rank_and_score_changes():
    from test_sector_rotation_service import (
        test_build_snapshot_uses_previous_rows_for_rank_and_score_changes,
    )

    test_build_snapshot_uses_previous_rows_for_rank_and_score_changes()


def test_ready_sector_consumes_native_technical_combined_and_ranking(monkeypatch):
    from readiness_capture_helpers import ready_identity_context
    from test_sector_rotation_service import FakeMarketRepository, FakeSectorRepository

    from app.services.sector_rotation_config import load_sector_rotation_config
    from app.services.sector_rotation_service import SectorRotationService
    from app.services.sector_universe_service import SectorUniverseService

    context, cutoff = ready_identity_context()
    ticker = context.tickers[0]
    for method, rows in (
        ("_raw_rows_for_run", [ticker.raw_row]),
        ("_fundamentals_for_run", [ticker.fundamental_score]),
        ("_technicals_for_run", [ticker.technical_score]),
        ("_combined_results_for_run", [ticker.combined_result]),
        ("_ranking_results_for_run", list(ticker.ranking_results)),
    ):
        monkeypatch.setattr(
            f"app.services.sector_universe_service.{method}", lambda _db, _run, rows=rows: rows
        )
    metrics = SectorUniverseService().build(object(), 7, load_sector_rotation_config())
    assert metrics[0].average_technical_score > 0 and metrics[0].average_final_score > 0
    snapshot = SectorRotationService(
        repository=FakeSectorRepository(),
        market_repository=FakeMarketRepository(run_snapshot=context.market_regime_snapshot),
    ).build_sector_rotation_snapshot(
        object(),
        run_id=7,
        market_cutoff=cutoff,
        persist=False,
        config=load_sector_rotation_config(),
    )
    assert snapshot.market_regime_snapshot_id == context.market_regime_snapshot.id
    assert snapshot.rows[0].final_score is not None


def test_fully_rated_ready_ceri_four_outputs(monkeypatch):
    cutoff, config, volatility, short_pressure = ready_ceri_sources(monkeypatch)
    kwargs = dict(
        as_of_session=cutoff.latest_completed_session, market_cutoff=cutoff, ibmi_config=config
    )
    from test_contextual_calculation_identity_adoption import _RowsDb

    vol = capture._point_in_time_volatility_feature(
        _RowsDb([volatility]), "MSFT", cutoff.cutoff_at, **kwargs
    )
    short = capture._point_in_time_short_pressure_feature(
        _RowsDb([short_pressure]), "MSFT", cutoff.cutoff_at, **kwargs
    )
    snapshot, opportunity, confidence, risk = rated_ceri_snapshot(
        cutoff, options_event_premium_score(vol), short.classification
    )
    assert opportunity.rated and opportunity.coverage_pct == 100
    assert confidence.coverage_pct == 100 and not confidence.warnings
    assert snapshot.opportunity_score > 0 and risk.score == 1.5
    from app.services.core_calculation_evidence import calculation_evidence_payload

    assert (
        normalize_producer_readiness(
            "CERI", calculation_evidence_payload(snapshot), identity_fingerprint="native"
        ).status
        is ReadinessStatus.READY
    )
