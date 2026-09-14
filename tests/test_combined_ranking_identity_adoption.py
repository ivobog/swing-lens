from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import (
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services import (
    combined_decision,
    fundamental_score_service,
    ranking_profile_service,
    technical_score_service,
)
from app.services.calculation_identity import IdentityDimension
from app.services.combined_ranking_identity import (
    CALCULATION_IDENTITY_FINGERPRINT_KEY,
    CalculationIdentityAdoptionError,
    build_fundamental_score_identity,
    build_technical_score_identity,
    calculation_identity_from_debug,
    embed_calculation_identity,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.ranking_profile_config import get_ranking_profile

SESSION = date(2026, 7, 7)
PIPELINE_ID = 11
RUN_ID = 7


class FakeDb:
    def __init__(self, *, existing_rankings: list[RankingResult] | None = None) -> None:
        self.added = list(existing_rankings or [])
        self.executed = []
        self.flushes = 0
        self.deleted = []

    def get(self, model, row_id):
        if model is UploadRun and row_id == RUN_ID:
            return UploadRun(id=RUN_ID, filename="test.csv", status="COMPLETED")
        return None

    def scalars(self, _statement):
        return self.added

    def execute(self, statement) -> None:
        self.executed.append(statement)

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def delete(self, row) -> None:
        self.deleted.append(row)
        if row in self.added:
            self.added.remove(row)

    def flush(self) -> None:
        self.flushes += 1


class SourceDb(FakeDb):
    def __init__(self, raw_row: RawCompanyRow) -> None:
        super().__init__()
        self.raw_row = raw_row

    def scalars(self, _statement):
        return [self.raw_row]


def test_combined_compatible_sources_persist_identity_and_exact_source_refs(monkeypatch) -> None:
    row, fundamental, technical, cutoff = _identity_aware_sources()
    db = FakeDb()
    _patch_combined(monkeypatch, row, fundamental, technical)

    result = combined_decision.refresh_combined_results(
        db,
        RUN_ID,
        market_cutoff=cutoff,
        pipeline_run_id=PIPELINE_ID,
    )[0]

    identity = calculation_identity_from_debug(result.debug_json)
    assert identity is not None and identity.validate().valid
    references = identity.source_lineage.value.references
    assert {(item.artifact_type, item.artifact_id) for item in references} == {
        ("FundamentalScore", str(fundamental.id)),
        ("TechnicalScore", str(technical.id)),
    }
    assert result.debug_json[CALCULATION_IDENTITY_FINGERPRINT_KEY] == str(identity.fingerprint())


def test_fundamental_and_technical_producers_persist_context_bound_identities() -> None:
    row, _, technical, cutoff = _identity_aware_sources()
    source_db = SourceDb(row)

    fundamentals = fundamental_score_service.recalculate_run_fundamentals(
        source_db,
        RUN_ID,
        market_cutoff=cutoff,
        pipeline_run_id=PIPELINE_ID,
    )
    finalized = technical_score_service.finalize_technical_scores(
        source_db,
        RUN_ID,
        [technical],
        market_cutoff=cutoff,
        pipeline_run_id=PIPELINE_ID,
        persist=False,
    )

    for score in (fundamentals[0], finalized[0]):
        identity = calculation_identity_from_debug(score.debug_json)
        assert identity is not None and identity.validate().valid
        assert identity.ownership.pipeline_id.value == PIPELINE_ID
        assert identity.calculation_context.market_calculation_context_id.value == cutoff.context_id


def test_combined_rejects_raw_row_changed_after_fundamental_identity(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    row.raw_json = {"Symbol": "ACME", "Revenue": "changed-after-fundamental-score"}
    _patch_combined(monkeypatch, row, fundamental, technical)

    with pytest.raises(CalculationIdentityAdoptionError, match="source_lineage.RawCompanyRow"):
        combined_decision.refresh_combined_results(FakeDb(), RUN_ID)


@pytest.mark.parametrize(
    ("dimension", "expected_path"),
    [
        ("context", "calculation_context.market_calculation_context_id"),
        ("session", "temporal.as_of_session"),
        ("cutoff", "temporal.calculation_cutoff"),
    ],
)
def test_combined_rejects_same_run_ticker_with_contextual_mismatch(
    monkeypatch, dimension: str, expected_path: str
) -> None:
    row, fundamental, technical, cutoff = _identity_aware_sources()
    identity = calculation_identity_from_debug(fundamental.debug_json)
    assert identity is not None
    if dimension == "context":
        identity = replace(
            identity,
            calculation_context=replace(
                identity.calculation_context,
                market_calculation_context_id=IdentityDimension.known(999),
            ),
        )
    elif dimension == "session":
        identity = replace(
            identity,
            temporal=replace(
                identity.temporal, as_of_session=IdentityDimension.known(date(2026, 7, 6))
            ),
        )
    else:
        identity = replace(
            identity,
            temporal=replace(
                identity.temporal,
                calculation_cutoff=IdentityDimension.known(datetime(2026, 7, 7, 19, tzinfo=UTC)),
            ),
        )
    fundamental.debug_json = embed_calculation_identity({}, identity, policy="TEST_MUTATION")
    _patch_combined(monkeypatch, row, fundamental, technical)

    with pytest.raises(CalculationIdentityAdoptionError, match=expected_path):
        combined_decision.refresh_combined_results(FakeDb(), RUN_ID)


def test_combined_unknown_and_legacy_identity_fail_closed(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    identity = calculation_identity_from_debug(fundamental.debug_json)
    assert identity is not None
    identity = replace(
        identity,
        configuration=replace(
            identity.configuration,
            effective_configuration=IdentityDimension.unknown(),
        ),
    )
    fundamental.debug_json = embed_calculation_identity({}, identity, policy="TEST_UNKNOWN")
    _patch_combined(monkeypatch, row, fundamental, technical)
    with pytest.raises(CalculationIdentityAdoptionError, match="INSUFFICIENT_IDENTITY"):
        combined_decision.refresh_combined_results(FakeDb(), RUN_ID)

    fundamental.debug_json = {}
    with pytest.raises(CalculationIdentityAdoptionError, match="LEGACY_UNKNOWN"):
        combined_decision.refresh_combined_results(FakeDb(), RUN_ID)


def test_combined_identity_fingerprint_is_stable_and_materially_sensitive(monkeypatch) -> None:
    row, fundamental, technical, cutoff = _identity_aware_sources()
    _patch_combined(monkeypatch, row, fundamental, technical)
    first = combined_decision.refresh_combined_results(FakeDb(), RUN_ID)[0]
    second = combined_decision.refresh_combined_results(FakeDb(), RUN_ID)[0]
    assert (
        first.debug_json[CALCULATION_IDENTITY_FINGERPRINT_KEY]
        == second.debug_json[CALCULATION_IDENTITY_FINGERPRINT_KEY]
    )

    changed_cutoff = replace(cutoff, cutoff_at=datetime(2026, 7, 7, 21, tzinfo=UTC), context_id=18)
    changed_row, changed_fundamental, changed_technical, _ = _identity_aware_sources(
        cutoff=changed_cutoff
    )
    _patch_combined(monkeypatch, changed_row, changed_fundamental, changed_technical)
    changed = combined_decision.refresh_combined_results(FakeDb(), RUN_ID)[0]
    assert (
        first.debug_json[CALCULATION_IDENTITY_FINGERPRINT_KEY]
        != changed.debug_json[CALCULATION_IDENTITY_FINGERPRINT_KEY]
    )


def test_identity_incompatible_combined_replacement_is_rejected(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    _patch_combined(monkeypatch, row, fundamental, technical)
    existing = combined_decision.refresh_combined_results(FakeDb(), RUN_ID)[0]

    changed_row, changed_fundamental, changed_technical, _ = _identity_aware_sources(
        cutoff=replace(
            _cutoff(),
            cutoff_at=datetime(2026, 7, 7, 21, tzinfo=UTC),
            context_id=18,
        )
    )
    _patch_combined(monkeypatch, changed_row, changed_fundamental, changed_technical)
    monkeypatch.setattr(combined_decision, "_combined_for_run", lambda *_: [existing])

    with pytest.raises(ValueError, match="CALCULATION_IDENTITY_PERSISTENCE_CONFLICT"):
        combined_decision.refresh_combined_results(FakeDb(), RUN_ID)


def test_ranking_compatible_inputs_and_profile_config_produce_distinct_identity(
    monkeypatch,
) -> None:
    row, fundamental, technical, cutoff = _identity_aware_sources()
    profiles = [get_ranking_profile("momentum_swing"), get_ranking_profile("quality_momentum")]
    _patch_ranking(monkeypatch, row, fundamental, technical, profiles=profiles)

    results = ranking_profile_service.refresh_all_ranking_profiles(
        FakeDb(),
        RUN_ID,
        market_cutoff=cutoff,
        pipeline_run_id=PIPELINE_ID,
    )

    assert len(results) == 2
    identities = [calculation_identity_from_debug(result.debug_json) for result in results]
    assert all(identity is not None and identity.validate().valid for identity in identities)
    assert identities[0].fingerprint() != identities[1].fingerprint()


def test_ranking_rejects_same_run_ticker_with_different_context(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    identity = calculation_identity_from_debug(fundamental.debug_json)
    assert identity is not None
    identity = replace(
        identity,
        calculation_context=replace(
            identity.calculation_context,
            market_calculation_context_id=IdentityDimension.known(999),
        ),
    )
    fundamental.debug_json = embed_calculation_identity({}, identity, policy="TEST_MUTATION")
    _patch_ranking(monkeypatch, row, fundamental, technical)

    with pytest.raises(CalculationIdentityAdoptionError, match="market_calculation_context_id"):
        ranking_profile_service.refresh_all_ranking_profiles(FakeDb(), RUN_ID)


def test_optional_ibmi_absent_is_not_a_ranking_failure(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    _patch_ranking(monkeypatch, row, fundamental, technical, liquidity={})

    result = ranking_profile_service.refresh_ranking_profile(FakeDb(), RUN_ID, "momentum_swing")[0]
    identity = calculation_identity_from_debug(result.debug_json)
    assert identity is not None
    assert "IBIntelligenceFeature" not in {
        item.artifact_type for item in identity.source_lineage.value.references
    }
    assert result.debug_json["inputs"]["ibkr_liquidity_classification"] is None


def test_optional_compatible_ibmi_is_used_and_included_in_identity(monkeypatch) -> None:
    row, fundamental, technical, cutoff = _identity_aware_sources()
    feature = _liquidity_feature(cutoff=cutoff)
    _patch_ranking(monkeypatch, row, fundamental, technical, liquidity={"ACME": feature})

    result = ranking_profile_service.refresh_ranking_profile(FakeDb(), RUN_ID, "momentum_swing")[0]
    identity = calculation_identity_from_debug(result.debug_json)
    assert identity is not None
    assert "IBIntelligenceFeature" in {
        item.artifact_type for item in identity.source_lineage.value.references
    }
    assert result.debug_json["inputs"]["ibkr_liquidity_classification"] == "POOR"


def test_temporally_safe_but_config_wrong_ibmi_is_omitted(monkeypatch) -> None:
    row, fundamental, technical, cutoff = _identity_aware_sources()
    feature = _liquidity_feature(cutoff=cutoff)
    feature.config_hash = "f" * 64
    _patch_ranking(monkeypatch, row, fundamental, technical, liquidity={"ACME": feature})

    result = ranking_profile_service.refresh_ranking_profile(FakeDb(), RUN_ID, "momentum_swing")[0]
    identity = calculation_identity_from_debug(result.debug_json)
    assert identity is not None
    assert feature.calculation_cutoff_at <= cutoff.cutoff_at
    assert "IBIntelligenceFeature" not in {
        item.artifact_type for item in identity.source_lineage.value.references
    }
    assert result.debug_json["inputs"]["ibkr_liquidity_classification"] is None


def test_identity_incompatible_ranking_upsert_is_rejected(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    profile = get_ranking_profile("quality_momentum")
    _patch_ranking(monkeypatch, row, fundamental, technical, profiles=[profile])
    db = FakeDb()
    first = ranking_profile_service.refresh_all_ranking_profiles(db, RUN_ID)
    assert len(first) == 1

    changed = replace(profile, description="materially changed profile identity")
    monkeypatch.setattr(ranking_profile_service, "load_ranking_profiles", lambda: [changed])
    with pytest.raises(ValueError, match="CALCULATION_IDENTITY_PERSISTENCE_CONFLICT"):
        ranking_profile_service.refresh_all_ranking_profiles(db, RUN_ID)


def test_legacy_ranking_row_is_explicitly_replaced_not_upgraded(monkeypatch) -> None:
    row, fundamental, technical, _ = _identity_aware_sources()
    profile = get_ranking_profile("quality_momentum")
    legacy = RankingResult(
        run_id=RUN_ID,
        ticker="ACME",
        ranking_profile=profile.name,
        ranking_label=profile.label,
        profile_rank=1,
        profile_score=Decimal("1"),
        decision_label="Avoid",
        debug_json={},
    )
    db = FakeDb(existing_rankings=[legacy])
    _patch_ranking(monkeypatch, row, fundamental, technical, profiles=[profile])

    result = ranking_profile_service.refresh_all_ranking_profiles(db, RUN_ID)[0]

    assert db.deleted == [legacy]
    assert result is not legacy
    assert calculation_identity_from_debug(result.debug_json) is not None


def _identity_aware_sources(
    *, cutoff: MarketCalculationCutoff | None = None
) -> tuple[RawCompanyRow, FundamentalScore, TechnicalScore, MarketCalculationCutoff]:
    cutoff = cutoff or _cutoff()
    row = RawCompanyRow(
        id=101,
        run_id=RUN_ID,
        row_number=1,
        ticker="ACME",
        company_name="Acme",
        sector="Technology",
        raw_json={"Symbol": "ACME"},
    )
    fundamental = FundamentalScore(
        id=201,
        run_id=RUN_ID,
        ticker="ACME",
        fundamental_score=Decimal("8.2"),
        fundamental_label="Clean compounder",
        scoring_model_version="fundamentals_v2.1",
        debug_json={"config_hash": "a" * 64, "model_version": "fundamentals_v2.1"},
    )
    technical = TechnicalScore(
        id=301,
        run_id=RUN_ID,
        ticker="ACME",
        calculation_context_id=cutoff.context_id,
        calculation_cutoff_at=cutoff.cutoff_at,
        input_as_of_session=cutoff.latest_completed_session,
        calendar_version=cutoff.calendar_version,
        trend_score=Decimal("8.0"),
        momentum_score=Decimal("8.5"),
        setup_score=Decimal("8.1"),
        risk_score=Decimal("2.0"),
        market_score=Decimal("8.0"),
        combined_relative_strength_score=Decimal("8.4"),
        dual_score=Decimal("8.2"),
        classification="Prime clean pullback",
        pullback_health="Healthy",
        technical_confidence="normal",
        technical_engine_version="4.0.0",
        vcp_score=Decimal("8.0"),
        box_tightness_score=Decimal("8.0"),
        breakout_quality_score=Decimal("8.0"),
        climax_risk_score=Decimal("1.0"),
        debug_json={
            "derived": {"liquidity_warning": False},
            "temporal_lineage": {"source_latest_sessions": {"ticker": "2026-07-07"}},
        },
    )
    fundamental_identity = build_fundamental_score_identity(
        fundamental,
        raw_row=row,
        market_cutoff=cutoff,
        pipeline_run_id=PIPELINE_ID,
    )
    technical_identity = build_technical_score_identity(
        technical,
        market_cutoff=cutoff,
        pipeline_run_id=PIPELINE_ID,
        effective_config={"technical": "complete-test-config"},
    )
    fundamental.debug_json = embed_calculation_identity(
        fundamental.debug_json, fundamental_identity, policy="TEST_PRODUCER"
    )
    technical.debug_json = embed_calculation_identity(
        technical.debug_json, technical_identity, policy="TEST_PRODUCER"
    )
    return row, fundamental, technical, cutoff


def _cutoff() -> MarketCalculationCutoff:
    return MarketCalculationCutoff(
        cutoff_at=datetime(2026, 7, 7, 20, tzinfo=UTC),
        exchange_timezone="America/New_York",
        latest_completed_session=SESSION,
        daily_bar_ready_at=datetime(2026, 7, 7, 20, tzinfo=UTC),
        calendar_version="XNYS-2026a",
        bar_readiness_version="daily-close-v1",
        cutoff_reason="TEST",
        context_id=17,
    )


def _liquidity_feature(*, cutoff: MarketCalculationCutoff) -> IBIntelligenceFeature:
    config = load_ib_market_intelligence_config()
    return IBIntelligenceFeature(
        id=401,
        intelligence_run_id=1,
        ticker="ACME",
        as_of_session=cutoff.latest_completed_session,
        calculated_at=cutoff.cutoff_at,
        calculation_cutoff_at=cutoff.cutoff_at,
        calendar_version=cutoff.calendar_version,
        module="LIQUIDITY",
        classification="POOR",
        score=Decimal("3"),
        confidence="HIGH",
        freshness_status="CURRENT",
        coverage_status="AVAILABLE",
        components_json={"dollar_volume": 1000},
        reasons_json=[],
        warnings_json=[],
        source_evidence_hashes_json=["b" * 64],
        source_version=config.source_version,
        calculation_version=config.calculation_version,
        config_hash=config.config_hash,
        input_signature="c" * 64,
    )


def _patch_combined(monkeypatch, row, fundamental, technical) -> None:
    monkeypatch.setattr(combined_decision, "_rows_for_run", lambda *_: [row])
    monkeypatch.setattr(combined_decision, "_fundamentals_for_run", lambda *_: [fundamental])
    monkeypatch.setattr(combined_decision, "_technicals_for_run", lambda *_: [technical])
    monkeypatch.setattr(combined_decision, "_combined_for_run", lambda *_: [])
    monkeypatch.setattr(combined_decision, "_load_scoring_config", _scoring_config)


def _patch_ranking(
    monkeypatch,
    row,
    fundamental,
    technical,
    *,
    profiles=None,
    liquidity=None,
) -> None:
    profiles = profiles or [get_ranking_profile("momentum_swing")]
    monkeypatch.setattr(ranking_profile_service, "load_ranking_profiles", lambda: profiles)
    monkeypatch.setattr(ranking_profile_service, "_raw_rows_for_run", lambda *_: [row])
    monkeypatch.setattr(ranking_profile_service, "_fundamentals_for_run", lambda *_: [fundamental])
    monkeypatch.setattr(ranking_profile_service, "_technicals_for_run", lambda *_: [technical])
    monkeypatch.setattr(ranking_profile_service, "_load_scoring_config", _scoring_config)
    monkeypatch.setattr(
        ranking_profile_service,
        "_load_liquidity_features",
        lambda *_: dict(liquidity or {}),
    )


def _scoring_config() -> dict:
    return {
        "combined_score": {"fundamental_score": 0.55, "dual_score": 0.45},
        "penalties": {
            "danger_classification": 3.0,
            "overheated_momentum": 1.5,
            "value_trap_risk": 2.0,
            "growth_trap_risk": 1.5,
            "quality_risk": 1.5,
            "missing_data": 1.0,
            "liquidity_warning": 1.0,
        },
        "labels": {
            "strong_candidate_min_score": 8.0,
            "candidate_min_score": 6.8,
            "watch_min_score": 5.5,
        },
        "earnings_risk_gate": {"enabled": False},
    }
