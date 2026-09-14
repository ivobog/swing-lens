from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import CombinedResult, RankingResult, TechnicalScore
from app.services.calculation_identity import (
    CalculationIdentity,
    CalculationIdentityCompatibilityValidator,
    IdentityDimension,
)
from app.services.ceri import capture_service as ceri_capture
from app.services.combined_ranking_identity import (
    COMBINED_INPUT_COMPATIBILITY,
    RANKING_IBMI_COMPATIBILITY,
    RANKING_INPUT_COMPATIBILITY,
    embed_calculation_identity,
)
from app.services.contextual_calculation_identity import (
    CERI_CONTEXT_COMPATIBILITY,
    CERI_IBMI_COMPATIBILITY,
    IBMI_CONTEXT_COMPATIBILITY,
    REGIME_CONTEXT_COMPATIBILITY,
    SECTOR_PRIOR_COMPATIBILITY,
    SECTOR_RANKING_COMPATIBILITY,
    SECTOR_REGIME_COMPATIBILITY,
    SETUP_COMBINED_COMPATIBILITY,
    SETUP_RANKING_METADATA_COMPATIBILITY,
    SETUP_REGIME_COMPATIBILITY,
    SETUP_SECTOR_COMPATIBILITY,
    SETUP_TECHNICAL_COMPATIBILITY,
    build_contextual_result_identity,
    build_regime_identity,
    consumer_context_identity,
    contextual_compatibility,
    expected_regime_identity,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.sector_universe_service import _compatible_run_artifacts
from app.services.setup_lifecycle.source_loader import (
    _compatible_ticker_artifacts,
    _select_compatible_context_candidate,
)
from app.services.winner_probability.calculation_identity import (
    WINNER_COMBINED_COMPATIBILITY,
    WINNER_FUNDAMENTAL_COMPATIBILITY,
    WINNER_HANDOFF_COMPATIBILITY,
    WINNER_RANKING_COMPATIBILITY,
    WINNER_RAW_COMPATIBILITY,
    WINNER_REGIME_COMPATIBILITY,
    WINNER_SECTOR_COMPATIBILITY,
    WINNER_TECHNICAL_COMPATIBILITY,
)

EDGE_POLICIES = (
    COMBINED_INPUT_COMPATIBILITY,
    RANKING_INPUT_COMPATIBILITY,
    RANKING_IBMI_COMPATIBILITY,
    REGIME_CONTEXT_COMPATIBILITY,
    SECTOR_RANKING_COMPATIBILITY,
    SECTOR_REGIME_COMPATIBILITY,
    SECTOR_PRIOR_COMPATIBILITY,
    SETUP_TECHNICAL_COMPATIBILITY,
    SETUP_COMBINED_COMPATIBILITY,
    SETUP_RANKING_METADATA_COMPATIBILITY,
    SETUP_REGIME_COMPATIBILITY,
    SETUP_SECTOR_COMPATIBILITY,
    CERI_IBMI_COMPATIBILITY,
    IBMI_CONTEXT_COMPATIBILITY,
    CERI_CONTEXT_COMPATIBILITY,
    WINNER_HANDOFF_COMPATIBILITY,
    WINNER_RAW_COMPATIBILITY,
    WINNER_FUNDAMENTAL_COMPATIBILITY,
    WINNER_TECHNICAL_COMPATIBILITY,
    WINNER_COMBINED_COMPATIBILITY,
    WINNER_RANKING_COMPATIBILITY,
    WINNER_REGIME_COMPATIBILITY,
    WINNER_SECTOR_COMPATIBILITY,
)


RUNTIME_EDGES = (
    (
        "FundamentalScore",
        "CombinedResult",
        COMBINED_INPUT_COMPATIBILITY,
        "app/services/combined_decision.py",
    ),
    (
        "TechnicalScore",
        "CombinedResult",
        COMBINED_INPUT_COMPATIBILITY,
        "app/services/combined_decision.py",
    ),
    (
        "FundamentalScore",
        "RankingResult",
        RANKING_INPUT_COMPATIBILITY,
        "app/services/ranking_profile_service.py",
    ),
    (
        "TechnicalScore",
        "RankingResult",
        RANKING_INPUT_COMPATIBILITY,
        "app/services/ranking_profile_service.py",
    ),
    (
        "IBIntelligenceFeature",
        "RankingResult",
        RANKING_IBMI_COMPATIBILITY,
        "app/services/ranking_profile_service.py",
    ),
    (
        "RankingResult",
        "SectorRotation",
        SECTOR_RANKING_COMPATIBILITY,
        "app/services/sector_rotation_service.py",
    ),
    (
        "MarketRegimeSnapshot",
        "SectorRotation",
        SECTOR_REGIME_COMPATIBILITY,
        "app/services/sector_rotation_service.py",
    ),
    (
        "PriorSectorRotation",
        "SectorRotation",
        SECTOR_PRIOR_COMPATIBILITY,
        "app/services/sector_rotation_service.py",
    ),
    (
        "TechnicalScore",
        "SetupLifecycle",
        SETUP_TECHNICAL_COMPATIBILITY,
        "app/services/setup_lifecycle/source_loader.py",
    ),
    (
        "CombinedResult",
        "SetupLifecycle",
        SETUP_COMBINED_COMPATIBILITY,
        "app/services/setup_lifecycle/source_loader.py",
    ),
    (
        "RankingResult",
        "SetupLifecycle",
        SETUP_RANKING_METADATA_COMPATIBILITY,
        "app/services/setup_lifecycle/source_loader.py",
    ),
    (
        "MarketRegimeSnapshot",
        "SetupLifecycle",
        SETUP_REGIME_COMPATIBILITY,
        "app/services/setup_lifecycle/source_loader.py",
    ),
    (
        "SectorRotationSnapshot",
        "SetupLifecycle",
        SETUP_SECTOR_COMPATIBILITY,
        "app/services/setup_lifecycle/source_loader.py",
    ),
    (
        "IBIntelligenceFeature",
        "CERI",
        CERI_IBMI_COMPATIBILITY,
        "app/services/ceri/capture_service.py",
    ),
    (
        "RawCompanyRow",
        "Winner",
        WINNER_RAW_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "FundamentalScore",
        "Winner",
        WINNER_FUNDAMENTAL_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "TechnicalScore",
        "Winner",
        WINNER_TECHNICAL_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "CombinedResult",
        "Winner",
        WINNER_COMBINED_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "RankingResult",
        "Winner",
        WINNER_RANKING_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "MarketRegimeSnapshot",
        "Winner",
        WINNER_REGIME_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "SectorRotationSnapshot",
        "Winner",
        WINNER_SECTOR_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
    (
        "DecisionHandoff",
        "Winner",
        WINNER_HANDOFF_COMPATIBILITY,
        "app/services/winner_probability/calculation_identity.py",
    ),
)


def test_phase1_policy_registry_is_unique_and_complete() -> None:
    names = {policy.name for policy in EDGE_POLICIES}

    assert len(names) == len(EDGE_POLICIES) == 23
    assert names == {
        "COMBINED_INPUT_COMPATIBILITY",
        "RANKING_INPUT_COMPATIBILITY",
        "RANKING_IBMI_COMPATIBILITY",
        "REGIME_CONTEXT_COMPATIBILITY",
        "SECTOR_RANKING_COMPATIBILITY",
        "SECTOR_REGIME_COMPATIBILITY",
        "SECTOR_PRIOR_COMPATIBILITY",
        "SETUP_TECHNICAL_COMPATIBILITY",
        "SETUP_COMBINED_COMPATIBILITY",
        "SETUP_RANKING_METADATA_COMPATIBILITY",
        "SETUP_REGIME_COMPATIBILITY",
        "SETUP_SECTOR_COMPATIBILITY",
        "CERI_IBMI_COMPATIBILITY",
        "IBMI_CONTEXT_COMPATIBILITY",
        "CERI_CONTEXT_COMPATIBILITY",
        "WINNER_HANDOFF_COMPATIBILITY",
        "WINNER_RAW_COMPATIBILITY",
        "WINNER_FUNDAMENTAL_COMPATIBILITY",
        "WINNER_TECHNICAL_COMPATIBILITY",
        "WINNER_COMBINED_COMPATIBILITY",
        "WINNER_RANKING_COMPATIBILITY",
        "WINNER_REGIME_COMPATIBILITY",
        "WINNER_SECTOR_COMPATIBILITY",
    }
    assert all(policy.rules for policy in EDGE_POLICIES)
    assert all(
        len({rule.dimension for rule in policy.rules}) == len(policy.rules)
        for policy in EDGE_POLICIES
    )


@pytest.mark.parametrize(("producer", "consumer", "policy", "source_path"), RUNTIME_EDGES)
def test_every_major_edge_has_a_reachable_runtime_policy(
    producer: str, consumer: str, policy, source_path: str
) -> None:
    source = Path(source_path).read_text(encoding="utf-8")

    assert producer and consumer
    assert policy.name in source


@pytest.mark.parametrize(
    "policy",
    (
        COMBINED_INPUT_COMPATIBILITY,
        SECTOR_RANKING_COMPATIBILITY,
        SETUP_TECHNICAL_COMPATIBILITY,
        CERI_CONTEXT_COMPATIBILITY,
        WINNER_TECHNICAL_COMPATIBILITY,
    ),
)
def test_legacy_unknown_never_proves_representative_edge_compatibility(policy) -> None:
    expected = _producer(_decision_identity(), "expected")
    legacy = CalculationIdentity.legacy_unknown(run_id=7, ticker="MSFT")

    assert not CalculationIdentityCompatibilityValidator.compare(
        expected, legacy, policy=policy
    ).accepted


def test_unknown_required_dimension_never_proves_compatibility() -> None:
    expected = _producer(_decision_identity(), "expected")
    actual = replace(
        expected,
        calculation_context=replace(
            expected.calculation_context,
            context_fingerprint=IdentityDimension.unknown(),
        ),
    )

    result = contextual_compatibility(expected, actual, policy=SETUP_TECHNICAL_COMPATIBILITY)

    assert not result.accepted
    assert result.status.name == "INSUFFICIENT_IDENTITY"


def test_sector_filters_wrong_context_ranking_before_consumption() -> None:
    expected = _decision_identity()
    compatible = _ranking(1, _producer(expected, "ranking"))
    wrong_context = consumer_context_identity(
        market_cutoff=_cutoff(context_id=99), run_id=7, pipeline_id=3, ticker="MSFT"
    )
    incompatible = _ranking(2, _producer(wrong_context, "ranking"))

    accepted = _compatible_run_artifacts([incompatible, compatible], 7, 3, _cutoff(context_id=42))

    assert accepted == [compatible]


@pytest.mark.parametrize(
    ("policy", "factory"),
    (
        (SETUP_TECHNICAL_COMPATIBILITY, lambda identity: _technical(identity)),
        (SETUP_COMBINED_COMPATIBILITY, lambda identity: _combined(identity)),
        (SETUP_RANKING_METADATA_COMPATIBILITY, lambda identity: _ranking(1, identity)),
    ),
)
def test_setup_rejects_same_run_artifact_from_wrong_pipeline(policy, factory) -> None:
    wrong = consumer_context_identity(
        market_cutoff=_cutoff(context_id=42), run_id=7, pipeline_id=88, ticker="MSFT"
    )
    row = factory(_producer(wrong, "setup-source"))

    accepted = _compatible_ticker_artifacts(
        (row,),
        run_id=7,
        pipeline_id=3,
        market_cutoff=_cutoff(context_id=42),
        policy=policy,
    )

    assert accepted == []


def test_setup_prefers_compatible_cross_run_regime_over_incompatible_newest() -> None:
    cutoff = _cutoff()
    config = load_market_regime_command_center_config()
    expected = expected_regime_identity(market_cutoff=cutoff, config=config)
    compatible = _context_row(
        1,
        build_regime_identity(
            market_cutoff=cutoff,
            config=config,
            run_id=99,
            pipeline_id=88,
            source_payload={"source": "compatible"},
        ),
        run_id=99,
        created_at=datetime(2026, 9, 11, 20, tzinfo=UTC),
    )
    incompatible = _context_row(
        2,
        build_regime_identity(
            market_cutoff=replace(cutoff, calendar_version="wrong-calendar"),
            config=config,
            run_id=None,
            pipeline_id=None,
            source_payload={"source": "newer-incompatible"},
        ),
        run_id=None,
        created_at=datetime(2026, 9, 11, 21, tzinfo=UTC),
    )

    selected = _select_compatible_context_candidate(
        (incompatible, compatible),
        cutoff.latest_completed_session,
        7,
        expected=expected,
        policy=SETUP_REGIME_COMPATIBILITY,
        allow_cross_run=True,
    )

    assert selected is compatible
    assert selected.run_id == 99


def test_ceri_prefers_compatible_global_ibmi_over_newer_wrong_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = _cutoff()
    config = load_ib_market_intelligence_config()
    compatible = _ibmi_feature(config=config, cutoff=cutoff.cutoff_at)
    incompatible = _ibmi_feature(
        config=config,
        cutoff=cutoff.cutoff_at,
        config_hash="f" * 64,
        row_id=702,
        calculated_at=datetime(2026, 9, 11, 21, tzinfo=UTC),
    )
    monkeypatch.setattr(
        ceri_capture,
        "get_settings",
        lambda: SimpleNamespace(
            ib_market_intelligence_enabled=True,
            ib_volatility_intelligence_enabled=True,
        ),
    )

    selected = ceri_capture._point_in_time_volatility_feature(
        _RowsDb([incompatible, compatible]),
        "MSFT",
        cutoff.cutoff_at,
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=config,
    )

    assert selected is not None
    assert selected.id == compatible.id
    assert selected.source_identity.ownership.run_id.state.name == "NOT_APPLICABLE"


def test_negative_dependency_graph_is_preserved() -> None:
    ranking = Path("app/services/ranking_profile_service.py").read_text(encoding="utf-8")
    setup = Path("app/services/setup_lifecycle/source_loader.py").read_text(encoding="utf-8")
    winner = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "app/services/winner_probability/capture_service.py",
            "app/services/winner_probability/calculation_identity.py",
            "app/services/winner_probability/repository.py",
        )
    ).casefold()

    assert "app.services.ceri" not in ranking
    assert "app.services.ceri" not in setup
    assert "ceri" not in winner
    assert "setupsignalsnapshot" not in winner
    assert "app.services.setup_lifecycle" not in winner
    assert "ibintelligencefeature" not in winner
    assert "sectorrotationsnapshot" not in ranking.casefold()


def _cutoff(*, context_id: int | None = None):
    cutoff = MarketClockService().cutoff_for(
        datetime(2026, 9, 11, 20, 30, tzinfo=UTC), reason="T10E_CERTIFICATION"
    )
    return cutoff.with_context_id(context_id) if context_id is not None else cutoff


def _decision_identity():
    return consumer_context_identity(
        market_cutoff=_cutoff(context_id=42), run_id=7, pipeline_id=3, ticker="MSFT"
    )


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


def _debug(identity):
    return embed_calculation_identity({}, identity, policy="T10E_CERTIFICATION")


def _technical(identity):
    return TechnicalScore(id=301, run_id=7, ticker="MSFT", debug_json=_debug(identity))


def _combined(identity):
    return CombinedResult(id=401, run_id=7, ticker="MSFT", debug_json=_debug(identity))


def _ranking(row_id: int, identity):
    return RankingResult(
        id=row_id,
        run_id=7,
        ticker="MSFT",
        ranking_profile="quality",
        profile_rank=row_id,
        debug_json=_debug(identity),
    )


def _context_row(row_id: int, identity, *, run_id, created_at: datetime):
    return SimpleNamespace(
        id=row_id,
        run_id=run_id,
        as_of_date=date(2026, 9, 11),
        created_at=created_at,
        debug_json=_debug(identity),
    )


def _ibmi_feature(
    *,
    config,
    cutoff,
    config_hash: str | None = None,
    row_id: int = 701,
    calculated_at: datetime = datetime(2026, 9, 11, 18, tzinfo=UTC),
):
    return IBIntelligenceFeature(
        id=row_id,
        ticker="MSFT",
        as_of_session=date(2026, 9, 11),
        calculated_at=calculated_at,
        calculation_cutoff_at=cutoff,
        calendar_version="swinglens-us-equities-v1",
        module="VOLATILITY",
        classification="AVAILABLE",
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


class _RowsDb:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _statement):
        return self.rows
