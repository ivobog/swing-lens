from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from _phase3_helpers import FakeWinnerRepository, build_run_context
from readiness_capture_helpers import ready_identity_context, reseal_context
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import (
    MarketRegimeSnapshot,
    RankingResult,
    TransitionDecisionHandoffManifest,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.combined_ranking_identity import embed_calculation_identity
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    build_regime_identity,
    consumer_context_identity,
    expected_sector_identity,
)
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)
from app.services.winner_probability.calculation_identity import (
    WinnerCalculationIdentityError,
    semantic_artifact_identity,
    validate_winner_handoff,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.repository import RunCaptureContext


def test_canonical_capture_validates_sources_then_freezes_identity() -> None:
    context, cutoff = ready_identity_context()
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(),
        run_id=7,
        market_cutoff=cutoff,
        captured_at=datetime(2026, 9, 14, 9, tzinfo=UTC),
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.decision_at == cutoff.cutoff_at
    assert prediction.feature_json["combined_score"] == "8.5"
    assert prediction.source_ids_json == {
        "upload_run_id": 7,
        "raw_row_id": 11,
        "fundamental_score_id": 21,
        "technical_score_id": 31,
        "combined_result_id": 41,
        "ranking_result_id": 51,
        "fundamental_evidence_id": 121,
        "technical_evidence_id": 131,
        "combined_evidence_id": 141,
        "ranking_evidence_id": 151,
        "market_regime_snapshot_id": 61,
        "sector_rotation_snapshot_id": 71,
        "regime_evidence_id": 161,
        "sector_evidence_id": 171,
        "sector_rotation_row_id": 81,
    }
    assert len(prediction.feature_vector_hash) == 64
    assert prediction.lineage_json["calculation_identity_fingerprint"]
    assert prediction.lineage_json["decision_handoff"]["manifest_id"] == 91


@pytest.mark.parametrize("source", ["technical", "combined", "ranking"])
def test_same_run_ticker_wrong_source_identity_never_drives_winner(source: str) -> None:
    context, cutoff = ready_identity_context(wrong_source=source)
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    if source in {"technical", "combined"}:
        assert result.failed == 1
        assert repository.predictions == []
    else:
        # Preserve the incompatible input; the registry requires ranking_profile.
        assert result.excluded == 1
        assert repository.predictions == []


def test_fundamental_identity_mismatch_is_optional_omission() -> None:
    context, cutoff = ready_identity_context(wrong_source="fundamental")
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.source_ids_json["fundamental_score_id"] is None
    assert "missing_fundamental_score" in prediction.warning_flags_json


def test_raw_row_mismatch_fails_before_vector_freeze() -> None:
    context, cutoff = ready_identity_context()
    context.tickers[0].raw_row.raw_json = {"Symbol": "MUTATED"}
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    assert result.failed == 1
    assert repository.predictions == []


def test_compatible_cross_run_regime_is_accepted_after_newer_incompatible_candidate() -> None:
    context, cutoff = ready_identity_context(newer_incompatible_regime=True)
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.source_ids_json["market_regime_snapshot_id"] == 61
    assert context.market_regime_candidates[0].id == 62
    assert context.market_regime_candidates[1].run_id == 99


def test_incompatible_sector_is_omitted_from_frozen_vector() -> None:
    context, cutoff = ready_identity_context(wrong_source="sector")
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.source_ids_json["sector_rotation_snapshot_id"] is None
    assert prediction.feature_json["sector_state"] is None


def test_ranking_filters_identity_before_priority_selection() -> None:
    context, cutoff = ready_identity_context(ranking_priority_mismatch=True)
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.source_ids_json["ranking_result_id"] == 51
    assert prediction.ranking_profile == "momentum_swing"


@pytest.mark.parametrize("dimension", ["context", "session", "cutoff", "calendar"])
def test_handoff_mismatch_fails_closed(dimension: str) -> None:
    context, cutoff = ready_identity_context()
    handoff = context.decision_handoff_manifest
    payload = dict(handoff.manifest_json)
    market = dict(payload["market_context"])
    if dimension == "context":
        market["id"] = 999
    elif dimension == "session":
        market["latest_completed_session"] = "2026-09-10"
    elif dimension == "cutoff":
        market["cutoff_at"] = "2026-09-11T19:00:00+00:00"
    else:
        market["calendar_version"] = "wrong-calendar"
    payload["market_context"] = market
    handoff.manifest_json = CanonicalEvidenceSerializer.canonicalize(payload)
    handoff.manifest_fingerprint = CanonicalEvidenceSerializer.fingerprint(
        handoff.manifest_json
    )

    with pytest.raises(WinnerCalculationIdentityError):
        validate_winner_handoff(handoff, run_id=7, market_cutoff=cutoff)


def test_missing_handoff_and_historical_capture_without_identity_fail_closed() -> None:
    context, cutoff = ready_identity_context()
    context = replace(context, decision_handoff_manifest=None)

    with pytest.raises(WinnerCalculationIdentityError, match="DecisionHandoff"):
        _service(FakeWinnerRepository(context)).capture_run(
            _session(), run_id=7, market_cutoff=cutoff
        )
    with pytest.raises(WinnerCalculationIdentityError, match="historical"):
        _service(FakeWinnerRepository(context)).capture_run(
            object(), run_id=7, reconstruction_method="HISTORICAL_AS_OF_REPLAY"
        )


def test_historical_business_identity_is_wall_clock_invariant() -> None:
    first_context, cutoff = ready_identity_context()
    second_context, _ = ready_identity_context()
    first = FakeWinnerRepository(first_context)
    second = FakeWinnerRepository(second_context)

    _service(first).capture_run(
        _session(),
        run_id=7,
        market_cutoff=cutoff,
        reconstruction_method="HISTORICAL_AS_OF_REPLAY",
        captured_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    _service(second).capture_run(
        _session(),
        run_id=7,
        market_cutoff=cutoff,
        reconstruction_method="HISTORICAL_AS_OF_REPLAY",
        captured_at=datetime(2040, 1, 1, tzinfo=UTC),
    )

    left = first.predictions[0]
    right = second.predictions[0]
    assert left.decision_at == right.decision_at == cutoff.cutoff_at
    assert left.prediction_as_of_date == right.prediction_as_of_date
    assert left.feature_json == right.feature_json
    assert left.feature_vector_hash == right.feature_vector_hash


def test_identity_compatible_technical_insufficiency_remains_excluded() -> None:
    context, cutoff = ready_identity_context()
    context.tickers[0].technical_score.insufficient_data = True
    reseal_context(context, cutoff, "technical")
    repository = FakeWinnerRepository(context)

    result = _service(repository).capture_run(
        _session(), run_id=7, market_cutoff=cutoff
    )

    assert result.excluded == 1
    assert result.exclusion_reasons == {"insufficient_completed_bars": 1}
    assert repository.predictions == []


def test_original_unloaded_identity_only_fixture_is_retained_and_rejected() -> None:
    context, cutoff = _identity_context()
    repository = FakeWinnerRepository(context)
    result = _service(repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
    assert result.failed == 1
    assert repository.predictions == []


def test_legacy_prediction_is_not_silently_upgraded_or_rewritten() -> None:
    context, cutoff = ready_identity_context()
    repository = FakeWinnerRepository(context)
    service = _service(repository)
    service.capture_run(_session(), run_id=7, market_cutoff=cutoff)
    prediction = repository.predictions[0]
    original_features = dict(prediction.feature_json)
    original_hash = prediction.feature_vector_hash
    prediction.lineage_json.pop("calculation_identity", None)
    prediction.lineage_json.pop("calculation_identity_fingerprint", None)

    result = service.capture_run(_session(), run_id=7, market_cutoff=cutoff)

    assert result.failed == 1
    assert prediction.feature_json == original_features
    assert prediction.feature_vector_hash == original_hash
    assert "calculation_identity" not in prediction.lineage_json


def test_no_negative_winner_edges_were_introduced() -> None:
    sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "app/services/winner_probability/capture_service.py",
            "app/services/winner_probability/calculation_identity.py",
            "app/services/winner_probability/repository.py",
        )
    ).casefold()

    assert "app.services.ceri" not in sources
    assert "setupsignalsnapshot" not in sources
    assert "app.services.setup_lifecycle" not in sources
    assert "ibintelligencefeature" not in sources


def _identity_context(
    *,
    wrong_source: str | None = None,
    newer_incompatible_regime: bool = False,
    ranking_priority_mismatch: bool = False,
):
    cutoff = MarketClockService().cutoff_for(
        datetime(2026, 7, 31, 21, 30, tzinfo=UTC), reason="T10D_TEST"
    ).with_context_id(71)
    context = build_run_context(as_of_date=cutoff.latest_completed_session)
    ticker_context = context.tickers[0]
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=7,
        pipeline_id=61,
        ticker="MSFT",
    )
    wrong = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=7,
        pipeline_id=999,
        ticker="MSFT",
    )
    for name, artifact, evidence_id in (
        ("fundamental", ticker_context.fundamental_score, 121),
        ("technical", ticker_context.technical_score, 131),
        ("combined", ticker_context.combined_result, 141),
        ("ranking", ticker_context.ranking_results[0], 151),
    ):
        artifact.debug_json = _debug(_producer(wrong if wrong_source == name else base, name))
        artifact.evidence_id = evidence_id

    regime_config = load_market_regime_command_center_config()
    market = context.market_regime_snapshot
    market.evidence_id = 161
    market.run_id = 99
    market.calculation_cutoff_at = cutoff.cutoff_at
    market.input_as_of_session = cutoff.latest_completed_session
    market.calendar_version = cutoff.calendar_version
    market.debug_json = _debug(
        build_regime_identity(
            market_cutoff=cutoff,
            config=regime_config,
            run_id=99,
            pipeline_id=88,
            source_payload={"benchmark": "compatible"},
        )
    )
    markets = [market]
    if newer_incompatible_regime:
        bad_cutoff = replace(cutoff, calendar_version="wrong-calendar")
        bad = MarketRegimeSnapshot(
            id=62,
            run_id=7,
            as_of_date=cutoff.latest_completed_session,
            calculation_version=market.calculation_version,
            regime="Downtrend",
            risk_state="Red",
            score=2,
            confidence="normal",
            action_summary="risk-off",
            created_at=datetime(2026, 7, 31, 16, tzinfo=UTC),
            calculation_cutoff_at=cutoff.cutoff_at,
            input_as_of_session=cutoff.latest_completed_session,
            calendar_version="wrong-calendar",
            debug_json=_debug(
                build_regime_identity(
                    market_cutoff=bad_cutoff,
                    config=regime_config,
                    run_id=7,
                    pipeline_id=61,
                    source_payload={"benchmark": "incompatible"},
                )
            ),
        )
        markets.insert(0, bad)

    sector = context.sector_rotation_snapshot
    sector.evidence_id = 171
    sector.calculation_cutoff_at = cutoff.cutoff_at
    sector.input_as_of_session = cutoff.latest_completed_session
    sector.calendar_version = cutoff.calendar_version
    sector_config = load_sector_rotation_config()
    sector_hash = sector_rotation_config_hash(sector_config)
    expected = expected_sector_identity(
        context=replace(base, subject=replace(base.subject, ticker=base.subject.company_id)),
        config_hash=sector_hash if wrong_source != "sector" else "f" * 64,
        calculation_version="sector-rotation-1.0.0",
        mode=(
            "combined"
            if bool(sector_config.get("etf_score", {}).get("enabled", False))
            else "universe_only"
        ),
    )
    sector.debug_json = _debug(_sector_identity(expected, base))

    rankings = list(ticker_context.ranking_results)
    if ranking_priority_mismatch:
        bad_ranking = RankingResult(
            id=52,
            run_id=7,
            ticker="MSFT",
            ranking_profile="bad_priority",
            ranking_label="Bad Priority",
            profile_rank=0,
            profile_score=9,
            decision_label="Bad",
            is_complete=True,
            created_at=datetime(2026, 7, 31, 15, tzinfo=UTC),
            debug_json=_debug(_producer(wrong, "ranking")),
        )
        rankings.insert(0, bad_ranking)
    ticker_context = replace(ticker_context, ranking_results=tuple(rankings))
    context = replace(
        context,
        market_regime_snapshot=markets[0],
        sector_rotation_snapshot=sector,
        market_regime_candidates=tuple(markets),
        sector_rotation_candidates=(sector,),
        sector_rows_by_snapshot={sector.id: {"Technology": ticker_context.sector_row}},
        tickers=(ticker_context,),
    )
    _refresh_handoff(context, cutoff)
    return context, cutoff


def _refresh_handoff(context: RunCaptureContext, cutoff) -> None:
    ticker = context.tickers[0]
    compatible_market = next(
        row for row in context.market_regime_candidates if row.id == 61
    )
    compatible_ranking = next(row for row in ticker.ranking_results if row.id == 51)
    artifacts = {
        "raw_row": semantic_artifact_identity(ticker.raw_row),
        "fundamental_score": semantic_artifact_identity(ticker.fundamental_score),
        "technical_score": semantic_artifact_identity(ticker.technical_score),
        "combined_result": semantic_artifact_identity(ticker.combined_result),
        "ranking_results": [semantic_artifact_identity(compatible_ranking)],
        "market_regime_snapshot": semantic_artifact_identity(compatible_market),
        "sector_rotation_snapshot": semantic_artifact_identity(
            context.sector_rotation_snapshot
        ),
        "sector_rotation_row": semantic_artifact_identity(ticker.sector_row),
        "eligible_price_bars": [],
        "setup_signal_input_hash": "setup-input",
    }
    payload = CanonicalEvidenceSerializer.canonicalize(
        {
            "contract": "transition-decision-handoff-v1",
            "binding": {
                "preflight_plan_id": 81,
                "run_start_anchor_fingerprint": "anchor-fingerprint",
            },
            "run": {"upload_run_id": 7, "pipeline_run_id": 61},
            "market_context": {
                "id": cutoff.context_id,
                "cutoff_at": cutoff.cutoff_at,
                "exchange_timezone": cutoff.exchange_timezone,
                "latest_completed_session": cutoff.latest_completed_session,
                "daily_bar_ready_at": cutoff.daily_bar_ready_at,
                "calendar_version": cutoff.calendar_version,
                "bar_readiness_version": cutoff.bar_readiness_version,
                "cutoff_reason": cutoff.cutoff_reason,
            },
            "decision_manifests": {},
            "artifact_lineage": {"MSFT": artifacts},
            "ceri_score_snapshots": [],
        }
    )
    handoff = TransitionDecisionHandoffManifest(
        id=91,
        preflight_plan_id=81,
        market_calculation_context_id=71,
        upload_run_id=7,
        pipeline_run_id=61,
        run_start_anchor_fingerprint="anchor-fingerprint",
        manifest_json=payload,
        manifest_fingerprint=CanonicalEvidenceSerializer.fingerprint(payload),
    )
    object.__setattr__(context, "decision_handoff_manifest", handoff)


def _producer(base, namespace: str):
    return build_contextual_result_identity(
        base=base,
        namespace=f"winner-test-{namespace}",
        config_hash="a" * 64,
        calculation_version=f"{namespace}-1",
        engine_version=f"{namespace}-1",
        source_artifacts=(),
        source_payload={"source": namespace},
    )


def _sector_identity(expected, context):
    identity = build_contextual_result_identity(
        base=replace(context, subject=replace(context.subject, ticker=context.subject.company_id)),
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
    return embed_calculation_identity({}, identity, policy="T10D_TEST")


def _service(repository):
    return WinnerPredictionCaptureService(
        repository=repository,
        decision_time_estimate_service=_FakeDecisionTimeEstimateService(),
    )


class _FakeDecisionTimeEstimateService:
    def create_decision_time_estimate(self, *_args, **_kwargs):
        return SimpleNamespace(status="insufficient")


def _session() -> Session:
    return Session(create_engine("sqlite://"))
