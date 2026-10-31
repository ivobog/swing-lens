"""Core calculator fixtures keep the real cutoff and evidence refusal boundaries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from core_readiness_helpers import certified_fundamental, seal_core
from operational_readiness_helpers import certified_fundamental as admitted_fundamental
from test_combined_ranking_identity_adoption import _cutoff, _identity_aware_sources

from app.services.combined_ranking_identity import (
    COMBINED_INPUT_COMPATIBILITY,
    CalculationIdentityAdoptionError,
    require_fundamental_raw_source,
    require_source_inputs,
)
from app.services.contextual_calculation_identity import artifact_identity
from app.services.contextual_consumer_eligibility import (
    FUNDAMENTAL_TO_COMBINED,
    FundamentalAdmissionEvidenceContractError,
    contextual_decision_input,
)
from app.services.core_calculation_evidence import EvidenceUnavailableError


@pytest.mark.parametrize("hours", [0, 1])
def test_fixture_derives_exact_existing_identity_cutoff(hours):
    cutoff = replace(_cutoff(), cutoff_at=_cutoff().cutoff_at + timedelta(hours=hours))
    _, row, _, _ = _identity_aware_sources(cutoff=cutoff, seal_fundamental=False)
    identity = artifact_identity(row)
    seal_core(row)
    evidence = row.calculation_evidence
    assert evidence.calculated_at == cutoff.cutoff_at
    assert evidence.calculation_identity_fingerprint == str(identity.fingerprint())
    assert (evidence.run_id, evidence.ticker) == (row.run_id, row.ticker)
    _, permission = contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
    assert (
        datetime.fromisoformat(permission["fundamental_admission_input"]["decision_cutoff"])
        == cutoff.cutoff_at
    )


@pytest.mark.parametrize("timestamp", [None, datetime(2026, 7, 7)])
def test_missing_or_naive_cutoff_still_fails_real_consumer(timestamp):
    row = admitted_fundamental(ticker="MSFT", fundamental_score=8)
    row.calculation_evidence.calculated_at = timestamp
    with pytest.raises(EvidenceUnavailableError, match="cutoff is not certified"):
        contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)


def test_unknown_fixture_has_no_clock_fallback_and_explicit_event_is_accepted():
    row = certified_fundamental(ticker="MSFT", fundamental_score=8)
    assert row.calculation_evidence.calculated_at is None
    with pytest.raises(EvidenceUnavailableError, match="cutoff is not certified"):
        contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
    timestamp = datetime(2026, 7, 7, tzinfo=UTC)
    seal_core(row, calculated_at=timestamp)
    assert row.calculation_evidence.calculated_at == timestamp


def test_explicit_time_cannot_replace_a_known_frozen_cutoff():
    _, row, _, cutoff = _identity_aware_sources(seal_fundamental=False)
    with pytest.raises(ValueError, match="must match its frozen identity cutoff"):
        seal_core(row, calculated_at=cutoff.cutoff_at + timedelta(hours=1))


def test_future_source_cannot_satisfy_earlier_consumer_identity():
    _, fundamental, technical, cutoff = _identity_aware_sources()
    require_source_inputs(
        artifact_identity(fundamental),
        artifact_identity(technical),
        policy=COMBINED_INPUT_COMPATIBILITY,
        consumer="COMBINED",
        left_producer="FUNDAMENTAL",
        right_producer="TECHNICAL",
    )
    future = replace(cutoff, cutoff_at=cutoff.cutoff_at + timedelta(hours=1))
    _, unavailable, _, _ = _identity_aware_sources(cutoff=future)
    with pytest.raises(CalculationIdentityAdoptionError, match="temporal.calculation_cutoff"):
        require_source_inputs(
            artifact_identity(unavailable),
            artifact_identity(technical),
            policy=COMBINED_INPUT_COMPATIBILITY,
            consumer="COMBINED",
            left_producer="FUNDAMENTAL",
            right_producer="TECHNICAL",
        )


def test_changed_raw_source_is_not_rescued_by_valid_time():
    raw, row, _, _ = _identity_aware_sources()
    identity = artifact_identity(row)
    require_fundamental_raw_source(identity, raw, consumer="COMBINED")
    raw.raw_json = {**raw.raw_json, "Revenue": "changed-after-calculation"}
    with pytest.raises(CalculationIdentityAdoptionError, match="source_lineage.RawCompanyRow"):
        require_fundamental_raw_source(identity, raw, consumer="COMBINED")


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("run_id", 999, "owner mismatch"),
        ("ticker", "FOREIGN", "ticker mismatch"),
        ("calculation_identity_fingerprint", "f" * 64, "identity mismatch"),
    ],
)
def test_valid_time_does_not_rescue_foreign_or_wrong_identity_evidence(field, value, reason):
    _, row, _, _ = _identity_aware_sources()
    setattr(row.calculation_evidence, field, value)
    with pytest.raises(FundamentalAdmissionEvidenceContractError, match=reason):
        contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)


def test_complete_explicit_synthetic_calculation_reaches_active_domain_output():
    row = admitted_fundamental(ticker="MSFT", fundamental_score=8)
    timestamp = row.calculation_evidence.calculated_at
    seal_core(row, calculated_at=timestamp)
    projected, permission = contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
    assert permission["included"] is True
    assert projected.fundamental_score == 8
    assert permission["orthogonal_decision"]["eligibility"] == "INCLUDED"


def test_source_backed_fixture_uses_real_csv_parser_and_preserves_threshold_score():
    from app.services.column_mapper import map_csv_rows
    from app.services.fundamental_ranker_v2 import score_row_v2

    row = certified_fundamental(
        ticker="BOUNDARY",
        fundamental_score=7.9999,
        fundamental_label="Clean compounder",
        calculated_at=datetime(2026, 7, 7, tzinfo=UTC),
        source_backed=True,
    )
    source = row.debug_json["synthetic_consumer_source"]
    calculated = score_row_v2(map_csv_rows([source["raw_csv_values"]])[0])
    for key in (
        "coverage",
        "component_coverage",
        "parse_diagnostics",
        "config_hash",
        "model_version",
    ):
        assert row.debug_json[key] == calculated.debug[key]
    assert row.debug_json["coverage"]["available_scoring_fields"] == 79
    assert row.fundamental_score == 7.9999
    assert source["producer_score"] != source["consumer_score"]
    projected, permission = contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
    assert float(projected.fundamental_score) == 7.9999
    assert permission["orthogonal_decision"]["eligibility"] == "INCLUDED"
    assert permission["orthogonal_decision"]["source_evidence_id"] == row.evidence_id


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("missing_critical", "critical_field_missing"),
        ("parse_failure", "fundamental_parse_failure"),
        ("unsupported_model", "scoring_model_version_unsupported"),
        ("unsupported_label", "fundamental_label_invalid"),
    ],
)
def test_source_backed_fixture_does_not_rescue_deliberate_invalid_inputs(mutation, reason):
    from copy import deepcopy

    from core_readiness_helpers import populate_fundamental_source

    from app.models.tables import FundamentalScore, RawCompanyRow

    row = FundamentalScore(run_id=7, ticker="NEGATIVE", fundamental_score=8)
    raw = RawCompanyRow(run_id=7, ticker="NEGATIVE", row_number=1, raw_json={})
    populate_fundamental_source(row, raw_row=raw)
    if mutation in {"missing_critical", "parse_failure"}:
        raw.raw_json = deepcopy(raw.raw_json)
        key = (
            "Market capitalization"
            if mutation == "missing_critical"
            else "Revenue growth %, TTM YoY"
        )
        raw.raw_json[key] = "" if mutation == "missing_critical" else "unparseable-value"
        populate_fundamental_source(row, raw_row=raw, raw_values=raw.raw_json)
    elif mutation == "unsupported_model":
        row.scoring_model_version = "unsupported-fixture-model"
    else:
        row.fundamental_label = "Quality growth"
    seal_core(row, calculated_at=datetime(2026, 7, 7, tzinfo=UTC))
    projected, permission = contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
    assert projected is None
    assert permission["orthogonal_decision"]["eligibility"] != "INCLUDED"
    assert reason in permission["orthogonal_decision"]["reason_codes"]


def test_canonical_keys_are_not_treated_as_csv_headers_or_backfilled():
    from core_readiness_helpers import populate_fundamental_source

    from app.models.tables import FundamentalScore

    row = FundamentalScore(run_id=7, ticker="CANONICAL")
    populate_fundamental_source(row, raw_values={"Symbol": "CANONICAL", "market_cap": "1000000000"})
    assert row.debug_json["coverage"]["available_scoring_fields"] == 0
    assert "market_cap" in row.debug_json["coverage"]["missing_core_fields"]
    seal_core(row, calculated_at=datetime(2026, 7, 7, tzinfo=UTC))
    projected, permission = contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
    assert projected is None
    assert "critical_field_missing" in permission["orthogonal_decision"]["reason_codes"]


def test_valid_fundamental_changes_reach_combined_and_ranking_weighted_scores():
    from test_combined_decision import _config, _fundamental, _row, _technical
    from test_ranking_profile_engine import _config as ranking_config
    from test_ranking_profile_engine import _profile

    from app.services.combined_decision import combine_row_decision
    from app.services.ranking_profile_engine import rank_single_row

    technical = _technical("PAIR", "Prime clean pullback", "8", "2")
    low, high = (_fundamental("PAIR", "Clean compounder", value) for value in ("6", "9"))
    combined = [
        combine_row_decision(_row("PAIR"), source, technical, config=_config())
        for source in (low, high)
    ]
    ranking = [
        rank_single_row(
            profile=_profile("momentum_swing"),
            row=_row("PAIR"),
            fundamental=source,
            technical=technical,
            config=ranking_config(),
        )
        for source in (low, high)
    ]
    for results, debug_key in ((combined, "debug_evidence"), (ranking, "debug")):
        for source, result in zip((low, high), results, strict=True):
            permission = getattr(result, debug_key)["contextual_consumer_eligibility"][
                "fundamental"
            ]
            assert permission["included"] is True
            assert permission["orthogonal_decision"]["source_evidence_id"] == source.evidence_id
            assert result.has_fundamental is True
            assert result.fundamental_score == float(source.fundamental_score)
    assert combined[1].final_score - combined[0].final_score == pytest.approx(3 * 0.55)
    assert ranking[1].profile_score - ranking[0].profile_score == pytest.approx(
        3 * _profile("momentum_swing").fundamental_weight, abs=0.0002
    )
