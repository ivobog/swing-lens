from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from readiness_helpers import certified_technical

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import (
    CombinedResult,
    PriceBar,
    RankingResult,
    RawCompanyRow,
    UploadRun,
)
from app.services.ceri import capture_service as ceri_capture
from app.services.ceri.config import load_ceri_config
from app.services.combined_ranking_identity import embed_calculation_identity
from app.services.contextual_calculation_identity import (
    REGIME_CONTEXT_COMPATIBILITY,
    SECTOR_PRIOR_COMPATIBILITY,
    SECTOR_RANKING_COMPATIBILITY,
    SECTOR_REGIME_COMPATIBILITY,
    SETUP_COMBINED_COMPATIBILITY,
    SETUP_RANKING_METADATA_COMPATIBILITY,
    SETUP_REGIME_COMPATIBILITY,
    SETUP_SECTOR_COMPATIBILITY,
    SETUP_TECHNICAL_COMPATIBILITY,
    artifact_identity,
    build_contextual_result_identity,
    build_ibmi_feature_identity,
    build_regime_identity,
    consumer_context_identity,
    contextual_compatibility,
    expected_regime_identity,
    expected_sector_identity,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.sector_rotation_service import _select_compatible_previous_snapshot
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import (
    _compatible_ticker_artifacts,
    _select_compatible_context_candidate,
    build_run_source_context,
)


def test_global_regime_requires_exact_contextual_identity_not_run_ownership() -> None:
    cutoff = _cutoff()
    config = load_market_regime_command_center_config()
    expected = expected_regime_identity(market_cutoff=cutoff, config=config)
    actual = build_regime_identity(
        market_cutoff=cutoff,
        config=config,
        run_id=None,
        pipeline_id=None,
        source_payload={"SPY": "source-a", "QQQ": "source-b"},
    )

    assert contextual_compatibility(expected, actual, policy=REGIME_CONTEXT_COMPATIBILITY).accepted
    assert contextual_compatibility(expected, actual, policy=SECTOR_REGIME_COMPATIBILITY).accepted
    assert actual.ownership.run_id.state.name == "NOT_APPLICABLE"


@pytest.mark.parametrize("mutation", ["session", "cutoff", "calendar", "config", "version"])
def test_global_regime_rejects_one_dimension_context_mismatch(mutation: str) -> None:
    cutoff = _cutoff()
    config = load_market_regime_command_center_config()
    expected = expected_regime_identity(market_cutoff=cutoff, config=config)
    changed_cutoff = cutoff
    changed_config = config
    if mutation == "session":
        changed_cutoff = replace(cutoff, latest_completed_session=date(2026, 9, 10))
    elif mutation == "cutoff":
        changed_cutoff = replace(cutoff, cutoff_at=datetime(2026, 9, 11, 19, tzinfo=UTC))
    elif mutation == "calendar":
        changed_cutoff = replace(cutoff, calendar_version="other-calendar-v2")
    elif mutation == "config":
        changed_config = replace(config, freshness={"max_stale_trading_days": 99})
    elif mutation == "version":
        changed_config = replace(config, calculation_version="market-regime-next")
    actual = build_regime_identity(
        market_cutoff=changed_cutoff,
        config=changed_config,
        run_id=None,
        pipeline_id=None,
        source_payload={"SPY": "source-a"},
    )

    result = contextual_compatibility(expected, actual, policy=REGIME_CONTEXT_COMPATIBILITY)

    assert not result.accepted


@pytest.mark.parametrize(
    "policy",
    [
        SECTOR_RANKING_COMPATIBILITY,
        SETUP_TECHNICAL_COMPATIBILITY,
        SETUP_COMBINED_COMPATIBILITY,
        SETUP_RANKING_METADATA_COMPATIBILITY,
    ],
)
def test_same_run_ticker_different_calculation_context_is_rejected(policy) -> None:
    cutoff = _cutoff(context_id=42)
    expected = consumer_context_identity(
        market_cutoff=cutoff, run_id=7, pipeline_id=3, ticker="MSFT"
    )
    actual_base = consumer_context_identity(
        market_cutoff=cutoff, run_id=7, pipeline_id=99, ticker="MSFT"
    )
    actual = _producer(actual_base, "source")

    result = contextual_compatibility(expected, actual, policy=policy)

    assert not result.accepted
    assert any("ownership.pipeline_id" in item for item in result.diagnostics)


@pytest.mark.parametrize(
    "policy",
    [
        SECTOR_RANKING_COMPATIBILITY,
        SETUP_TECHNICAL_COMPATIBILITY,
        SETUP_COMBINED_COMPATIBILITY,
        SETUP_RANKING_METADATA_COMPATIBILITY,
    ],
)
def test_exact_run_context_artifact_is_accepted(policy) -> None:
    cutoff = _cutoff(context_id=42)
    expected = consumer_context_identity(
        market_cutoff=cutoff, run_id=7, pipeline_id=3, ticker="MSFT"
    )
    actual = _producer(expected, "source")

    result = contextual_compatibility(expected, actual, policy=policy)

    assert result.accepted


def test_legacy_unknown_never_proves_setup_or_regime_compatibility() -> None:
    cutoff = _cutoff(context_id=42)
    expected = consumer_context_identity(
        market_cutoff=cutoff, run_id=7, pipeline_id=3, ticker="MSFT"
    )
    legacy = artifact_identity(SimpleNamespace(run_id=7, ticker="MSFT", debug_json={}))

    result = contextual_compatibility(expected, legacy, policy=SETUP_TECHNICAL_COMPATIBILITY)

    assert not result.accepted
    assert result.status.name == "INSUFFICIENT_IDENTITY"


def test_setup_omits_incompatible_combined_risk_and_ranking_metadata() -> None:
    cutoff = _cutoff(context_id=42)
    expected = consumer_context_identity(
        market_cutoff=cutoff, run_id=7, pipeline_id=3, ticker="MSFT"
    )
    technical = _technical(_producer(expected, "technical"))
    wrong = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=88, ticker="MSFT")
    combined = _combined(_producer(wrong, "combined"))
    ranking = _ranking(_producer(wrong, "ranking"))
    compatible_combined = _compatible_ticker_artifacts(
        (combined,),
        run_id=7,
        pipeline_id=3,
        market_cutoff=cutoff,
        policy=SETUP_COMBINED_COMPATIBILITY,
    )
    compatible_rankings = _compatible_ticker_artifacts(
        (ranking,),
        run_id=7,
        pipeline_id=3,
        market_cutoff=cutoff,
        policy=SETUP_RANKING_METADATA_COMPATIBILITY,
    )
    upload = _upload()
    raw = _raw()
    raw.run = upload
    context = build_run_source_context(
        upload_run=upload,
        raw_rows=(raw,),
        technical_scores=(technical,),
        combined_results=tuple(compatible_combined),
        ranking_results=tuple(compatible_rankings),
        price_bars=(_bar(),),
        market_cutoff=cutoff,
    ).tickers[0]

    built = SetupLifecycleSnapshotBuilder().build(context)

    assert built.dto.signals["earnings_risk"]["value"] is None
    assert built.dto.promoted_fields["profile_score"] is None
    assert built.dto.promoted_fields["setup_score"] == Decimal("7.8")
    assert built.dto.source_ids["combined_result_id"] is None
    assert built.dto.source_ids["ranking_result_id"] is None
    assert (
        artifact_identity(SimpleNamespace(debug_json=built.dto.debug)).source_lineage.state.name
        == "KNOWN"
    )


def test_setup_incompatible_technical_cannot_drive_setup() -> None:
    cutoff = _cutoff(context_id=42)
    wrong = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=88, ticker="MSFT")
    technical = _technical(_producer(wrong, "technical"))

    accepted = _compatible_ticker_artifacts(
        (technical,),
        run_id=7,
        pipeline_id=3,
        market_cutoff=cutoff,
        policy=SETUP_TECHNICAL_COMPATIBILITY,
    )

    assert accepted == []


def test_setup_selects_older_compatible_regime_over_newer_incompatible() -> None:
    cutoff = _cutoff()
    config = load_market_regime_command_center_config()
    expected = expected_regime_identity(market_cutoff=cutoff, config=config)
    compatible = _context_row(
        1,
        date(2026, 9, 10),
        build_regime_identity(
            market_cutoff=cutoff,
            config=config,
            run_id=99,
            pipeline_id=88,
            source_payload={"source": "compatible"},
        ),
        run_id=99,
    )
    incompatible = _context_row(
        2,
        date(2026, 9, 11),
        build_regime_identity(
            market_cutoff=replace(cutoff, calendar_version="wrong-calendar"),
            config=config,
            run_id=None,
            pipeline_id=None,
            source_payload={"source": "incompatible"},
        ),
    )

    selected = _select_compatible_context_candidate(
        (incompatible, compatible),
        date(2026, 9, 11),
        7,
        expected=expected,
        policy=SETUP_REGIME_COMPATIBILITY,
        allow_cross_run=True,
    )

    assert selected is compatible
    assert selected.run_id == 99


def test_setup_sector_requires_run_context_and_current_sector_contract() -> None:
    cutoff = _cutoff(context_id=42)
    context = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=3)
    expected = expected_sector_identity(
        context=context,
        config_hash="a" * 64,
        calculation_version="sector-rotation-1.0.0",
        mode="universe_only",
    )
    compatible = _sector_identity(expected, context)
    wrong_context = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=44)
    incompatible = _sector_identity(
        expected_sector_identity(
            context=wrong_context,
            config_hash="a" * 64,
            calculation_version="sector-rotation-1.0.0",
            mode="universe_only",
        ),
        wrong_context,
    )

    assert contextual_compatibility(
        expected, compatible, policy=SETUP_SECTOR_COMPATIBILITY
    ).accepted
    assert not contextual_compatibility(
        expected, incompatible, policy=SETUP_SECTOR_COMPATIBILITY
    ).accepted


def test_sector_prior_selector_skips_newer_incompatible_snapshot() -> None:
    cutoff = _cutoff(context_id=42)
    context = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=3)
    expected = expected_sector_identity(
        context=context,
        config_hash="a" * 64,
        calculation_version="sector-rotation-1.0.0",
        mode="universe_only",
    )
    prior_context = consumer_context_identity(
        market_cutoff=replace(
            cutoff,
            latest_completed_session=date(2026, 9, 9),
            cutoff_at=datetime(2026, 9, 9, 20, 30, tzinfo=UTC),
        ),
        run_id=None,
        pipeline_id=None,
        globally_reusable=True,
    )
    compatible_identity = _sector_identity(expected, prior_context)
    incompatible_expected = expected_sector_identity(
        context=context,
        config_hash="b" * 64,
        calculation_version="sector-rotation-1.0.0",
        mode="universe_only",
    )
    incompatible_identity = _sector_identity(incompatible_expected, prior_context)
    newer = _context_row(2, date(2026, 9, 10), incompatible_identity, run_id=99)
    older = _context_row(
        1,
        date(2026, 9, 9),
        compatible_identity,
        run_id=None,
        evidence_id=101,
    )

    selected = _select_compatible_previous_snapshot(
        (newer, older), expected=expected, current_session=date(2026, 9, 11)
    )

    assert selected is older
    assert contextual_compatibility(
        expected, artifact_identity(older), policy=SECTOR_PRIOR_COMPATIBILITY
    ).accepted


def test_sector_prior_selector_returns_unavailable_without_compatible_predecessor() -> None:
    cutoff = _cutoff(context_id=42)
    context = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=3)
    expected = expected_sector_identity(
        context=context,
        config_hash="a" * 64,
        calculation_version="sector-rotation-1.0.0",
        mode="universe_only",
    )
    prior_context = consumer_context_identity(
        market_cutoff=replace(
            cutoff,
            latest_completed_session=date(2026, 9, 10),
            cutoff_at=datetime(2026, 9, 10, 20, 30, tzinfo=UTC),
        ),
        run_id=None,
        pipeline_id=None,
        globally_reusable=True,
    )
    incompatible = _context_row(
        2,
        date(2026, 9, 10),
        _sector_identity(
            expected_sector_identity(
                context=context,
                config_hash="b" * 64,
                calculation_version="sector-rotation-1.0.0",
                mode="universe_only",
            ),
            prior_context,
        ),
    )

    selected = _select_compatible_previous_snapshot(
        (incompatible,), expected=expected, current_session=date(2026, 9, 11)
    )

    assert selected is None


@pytest.mark.parametrize("module", ["VOLATILITY", "SHORT_PRESSURE"])
def test_ceri_uses_compatible_global_ibmi_and_skips_newer_incompatible(
    monkeypatch, module: str
) -> None:
    cutoff = _cutoff()
    config = load_ib_market_intelligence_config()
    compatible = _ibmi_feature(module, config=config, cutoff=cutoff.cutoff_at)
    incompatible = _ibmi_feature(
        module,
        config=config,
        cutoff=cutoff.cutoff_at,
        config_hash="f" * 64,
        calculated_at=datetime(2026, 9, 11, 19, tzinfo=UTC),
    )
    monkeypatch.setattr(
        ceri_capture,
        "get_settings",
        lambda: SimpleNamespace(
            ib_market_intelligence_enabled=True,
            ib_volatility_intelligence_enabled=True,
            ib_short_pressure_enabled=True,
        ),
    )
    db = _RowsDb([incompatible, compatible])
    selector = (
        ceri_capture._point_in_time_volatility_feature
        if module == "VOLATILITY"
        else ceri_capture._point_in_time_short_pressure_feature
    )

    decisions = {}
    selected = selector(
        db,
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
        decisions=decisions,
    )

    edge = "ibmi_volatility" if module == "VOLATILITY" else "ibmi_short_pressure"
    assert selected is None
    assert decisions[edge]["source_feature_id"] == compatible.id
    assert decisions[edge]["producer_readiness"]["status"] == "LEGACY_UNKNOWN"
    # Preserve the original invalid FRESH/unsealed input and independently prove
    # that the native AVAILABLE, fully frozen equivalent passes identity selection.
    from contextual_readiness_helpers import ibmi_feature, seal_contextual

    seal_contextual(compatible)
    assert (
        selector(
            _RowsDb([incompatible, compatible]),
            "MSFT",
            cutoff.cutoff_at,
            as_of_session=cutoff.latest_completed_session,
            market_cutoff=cutoff,
            ibmi_config=config,
            decisions=decisions,
        )
        is None
    )
    assert decisions[edge]["producer_readiness"]["status"] == "UNKNOWN"
    ready = ibmi_feature(
        module,
        config_hash=config.config_hash,
        calculation_cutoff_at=cutoff.cutoff_at,
        calendar_version=cutoff.calendar_version,
        as_of_session=cutoff.latest_completed_session,
    )
    selected = selector(
        _RowsDb([incompatible, ready]),
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
    )
    assert selected is not None and selected.id == ready.id
    assert selected.source_identity.ownership.run_id.state.name == "NOT_APPLICABLE"


def test_ceri_omits_ibmi_with_unknown_cutoff(monkeypatch) -> None:
    cutoff = _cutoff()
    config = load_ib_market_intelligence_config()
    row = _ibmi_feature("VOLATILITY", config=config, cutoff=None)
    monkeypatch.setattr(
        ceri_capture,
        "get_settings",
        lambda: SimpleNamespace(
            ib_market_intelligence_enabled=True,
            ib_volatility_intelligence_enabled=True,
        ),
    )

    selected = ceri_capture._point_in_time_volatility_feature(
        _RowsDb([row]),
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
    )

    assert selected is None


def test_ceri_legacy_result_is_not_silently_reused_or_upgraded() -> None:
    legacy_lineage = {"legacy": True}
    row = SimpleNamespace(
        company_id=55,
        ticker="MSFT",
        evidence_lineage_json=legacy_lineage,
    )

    with pytest.raises(ValueError, match="LEGACY_UNKNOWN"):
        ceri_capture._existing_snapshot_company_ids(
            _RowsDb([row]),
            7,
            {55},
            load_ceri_config(),
            market_cutoff=_cutoff(context_id=42),
            pipeline_id=3,
        )

    assert legacy_lineage == {"legacy": True}


def test_no_negative_architecture_edges_were_introduced() -> None:
    setup_source = Path("app/services/setup_lifecycle/source_loader.py").read_text(encoding="utf-8")
    winner_source = Path("app/services/winner_probability/capture_service.py").read_text(
        encoding="utf-8"
    )
    sector_source = Path("app/services/sector_rotation_service.py").read_text(encoding="utf-8")

    assert "app.services.ceri" not in setup_source
    assert "setup_lifecycle" not in winner_source
    assert "ceri" not in winner_source.casefold()
    assert "refresh_ranking" not in sector_source


def _cutoff(*, context_id: int | None = None):
    cutoff = MarketClockService().cutoff_for(
        datetime(2026, 9, 11, 20, 30, tzinfo=UTC), reason="T10C_TEST"
    )
    return cutoff.with_context_id(context_id) if context_id is not None else cutoff


def _producer(base, namespace: str):
    return build_contextual_result_identity(
        base=base,
        namespace=namespace,
        config_hash="a" * 64,
        calculation_version=f"{namespace}-1",
        engine_version=f"{namespace}-1",
        source_artifacts=(),
        source_payload={"source": namespace},
    )


def _sector_identity(expected, context):
    identity = build_contextual_result_identity(
        base=context,
        namespace="sector-rotation",
        config_hash=expected.configuration.effective_configuration.value.fingerprint.digest,
        calculation_version="sector-rotation-1.0.0",
        engine_version="sector-rotation-1.0.0",
        source_artifacts=(),
        source_payload={"mode": "universe_only"},
    )
    return replace(
        identity,
        configuration=expected.configuration,
        algorithm=replace(identity.algorithm, components=expected.algorithm.components),
    )


def _debug(identity):
    return embed_calculation_identity({}, identity, policy="T10C_TEST")


def _technical(identity):
    return certified_technical(
        id=301,
        run_id=7,
        ticker="MSFT",
        dual_score=Decimal("8.2"),
        setup_score=Decimal("7.8"),
        classification="Breakout",
        stage="PIVOT_READY",
        data_quality_score=Decimal("9"),
        debug_json=_debug(identity),
    )


def _combined(identity):
    return CombinedResult(
        id=401,
        run_id=7,
        ticker="MSFT",
        final_score=Decimal("9.2"),
        combined_decision="Buyable",
        earnings_risk_level="IMMINENT",
        is_complete=True,
        debug_json=_debug(identity),
    )


def _ranking(identity):
    return RankingResult(
        id=501,
        run_id=7,
        ticker="MSFT",
        ranking_profile="quality",
        ranking_label="Quality",
        profile_rank=1,
        profile_score=Decimal("9.5"),
        decision_label="Buyable",
        debug_json=_debug(identity),
    )


def _upload():
    return UploadRun(
        id=7,
        filename="run.csv",
        status="COMPLETED",
        uploaded_at=datetime(2026, 9, 11, 19, tzinfo=UTC),
        processed_at=datetime(2026, 9, 11, 20, tzinfo=UTC),
    )


def _raw():
    return RawCompanyRow(
        id=101,
        run_id=7,
        row_number=1,
        ticker="MSFT",
        company_name="Microsoft",
        sector="Technology",
        sector_canonical="Technology",
        raw_json={"trigger_price": "100", "earnings_risk_level": "IMMINENT"},
    )


def _bar():
    return PriceBar(
        id=601,
        ticker="MSFT",
        bar_date=date(2026, 9, 11),
        timeframe="1 day",
        open=Decimal("99"),
        high=Decimal("102"),
        low=Decimal("98"),
        close=Decimal("101"),
        volume=Decimal("1000000"),
        source="IBKR",
        what_to_show="TRADES",
        data_hash="bar-hash",
    )


def _context_row(row_id, as_of_date, identity, *, run_id=None, evidence_id=None):
    return SimpleNamespace(
        id=row_id,
        evidence_id=evidence_id,
        run_id=run_id,
        as_of_date=as_of_date,
        created_at=datetime(2026, 9, as_of_date.day, 21, tzinfo=UTC),
        debug_json=_debug(identity),
    )


def _ibmi_feature(
    module,
    *,
    config,
    cutoff,
    config_hash=None,
    calculated_at=datetime(2026, 9, 11, 18, tzinfo=UTC),
):
    row = IBIntelligenceFeature(
        id=701 if config_hash is None else 702,
        ticker="MSFT",
        as_of_session=date(2026, 9, 11),
        calculated_at=calculated_at,
        calculation_cutoff_at=cutoff,
        calendar_version="swinglens-us-equities-v1",
        module=module,
        classification="HIGH" if module == "SHORT_PRESSURE" else "AVAILABLE",
        score=Decimal("5"),
        confidence="HIGH",
        freshness_status="FRESH",
        coverage_status="AVAILABLE",
        components_json={"event_premium_score": 1.0},
        reasons_json=[],
        warnings_json=[],
        source_evidence_hashes_json=["b" * 64],
        source_version=config.source_version,
        calculation_version=config.calculation_version,
        config_hash=config_hash or config.config_hash,
        input_signature="input",
    )
    from test_contextual_effective_configuration import frozen_evidence

    from app.services.contextual_effective_configuration import resolve_ibmi_configuration

    if cutoff is None:
        return row
    frozen = resolve_ibmi_configuration(config, module.lower())
    if config_hash is not None:
        from app.services.contextual_effective_configuration import _freeze

        values = frozen.values
        key = "high_fee_rate_pct" if module == "SHORT_PRESSURE" else "lookback_sessions"
        values["config"][module.lower()][key] = 999
        frozen = _freeze(
            frozen.snapshot.family.namespace,
            values,
            {entry.key: entry.source for entry in frozen.snapshot.entries},
        )
    identity = frozen.bind(build_ibmi_feature_identity(row))
    # The legacy readiness fixture stays uncertified; only its own configuration
    # and temporal compatibility are explicit. This still proves the blocked edge.
    from app.models.tables import CoreCalculationEvidence

    evidence = frozen_evidence(frozen, identity)
    row.calculation_evidence = CoreCalculationEvidence(
        id=900 + row.id,
        artifact_kind="IBMI",
        ticker=row.ticker,
        ranking_profile=module,
        payload_json=evidence.payload_json,
        payload_fingerprint=evidence.payload_fingerprint,
        calculation_identity_json=evidence.calculation_identity_json,
        calculation_identity_fingerprint=evidence.calculation_identity_fingerprint,
    )
    row.evidence_id = 900 + row.id
    return row


class _RowsDb:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _statement):
        return self.rows
