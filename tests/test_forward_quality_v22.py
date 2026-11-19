import json
import math
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.column_mapper import MappedCsvRow
from app.services.fundamental_admission_policy import (
    FUNDAMENTAL_COMPONENTS,
    MINIMUM_COMPONENT_COVERAGE,
    MINIMUM_OVERALL_COVERAGE,
    FundamentalAdmissionInput,
    evaluate_fundamental_admission,
)
from app.services.fundamental_components_v2 import (
    COMPONENT_FORMULA_FIELDS,
    FORWARD_FORMULA_FIELDS_V21,
    forward_quality_score,
    forward_quality_subscores,
)
from app.services.fundamental_ranker_v2 import (
    FundamentalsConfigError,
    load_fundamentals_v2_config,
    score_row_v2,
)

V21_PATH = Path("tests/fixtures/fundamentals_v21.yaml")
BASELINE = json.loads(Path("tests/fixtures/forward_quality_v21.json").read_text())
AMOUNTS = {
    "net_income_ttm",
    "net_income_estimate_ntm",
    "total_revenue_ttm",
    "revenue_estimate_annual",
    "cash_short_term_investments_annual",
    "cash_short_term_investments_estimate_annual",
    "capex_ttm",
    "capex_estimate_ntm",
}


def _values(**values):
    return {**{f"{k}_currency": "USD" for k in values if k in AMOUNTS}, **values}


def _row(values, ticker="TEST"):
    return MappedCsvRow(1, ticker, None, None, values, {})


@pytest.mark.parametrize(
    ("historical", "estimated", "expected"),
    [
        (100, 130, 10),
        (100, 120, 8.3333),
        (100, 110, 6.6667),
        (100, 100, 5),
        (100, 90, 3),
        (100, 75, 0),
        (100, -1, 0),
        (100, 0, 0),
        (0, 100, 7),
        (-100, 100, 7),
        (-100, -50, 0),
        (0, 0, 0),
        (-100, 0, 0),
    ],
)
def test_income_growth_and_profit_transitions(historical, estimated, expected):
    values = _values(net_income_ttm=historical, net_income_estimate_ntm=estimated)
    assert forward_quality_subscores(values)["net_income_growth"] == expected
    assert forward_quality_score(values) == expected


@pytest.mark.parametrize(
    ("field", "history", "estimate", "expected"),
    [
        ("revenue", 100, 80, 0),
        ("revenue", 100, 90, 2.5),
        ("revenue", 100, 100, 5),
        ("revenue", 100, 105, 6.25),
        ("revenue", 100, 110, 7.5),
        ("revenue", 100, 115, 8.75),
        ("revenue", 100, 120, 10),
        ("revenue", 0, 100, None),
        ("revenue", -100, 100, None),
        ("revenue", 100, -1, None),
        ("revenue", 100, 0, 0),
        ("cash", 100, 70, 0),
        ("cash", 100, 85, 2.5),
        ("cash", 100, 100, 5),
        ("cash", 100, 115, 7.5),
        ("cash", 100, 130, 10),
        ("cash", 0, 100, None),
        ("cash", -1, 100, None),
        ("cash", 100, -1, None),
    ],
)
def test_annual_revenue_and_cash_comparisons(field, history, estimate, expected):
    historical, estimated = (
        ("total_revenue_ttm", "revenue_estimate_annual")
        if field == "revenue"
        else ("cash_short_term_investments_annual", "cash_short_term_investments_estimate_annual")
    )
    scores = forward_quality_subscores(_values(**{historical: history, estimated: estimate}))
    assert scores[f"{field}_comparison"] == expected


@pytest.mark.parametrize(
    ("capex", "earnings", "expected"),
    [
        (120, 130, 10),
        (120, 120, 10),
        (120, 110, 8),
        (130, 100, 4),
        (140, 90, 0),
        (80, 70, 8),
        (80, 100, 10),
        (0, 100, 10),
    ],
)
@pytest.mark.parametrize("sign", [1, -1])
def test_capex_pressure_uses_spending_magnitudes(capex, earnings, expected, sign):
    values = _values(
        capex_ttm=100 * sign,
        capex_estimate_ntm=capex * sign,
        net_income_ttm=100,
        net_income_estimate_ntm=earnings,
    )
    assert forward_quality_subscores(values)["capex_sustainability"] == expected


@pytest.mark.parametrize(
    ("historical_income", "estimated_income", "historical_capex", "expected"),
    [
        (-100, 10, 100, None),
        (0, 10, 100, None),
        (100, -1, 100, 0),
        (None, 0, 100, 0),
        (100, 100, 0, None),
        (100, 100, None, None),
        (100, None, 100, None),
        (100, -1, 0, None),
    ],
)
def test_capex_special_cases(historical_income, estimated_income, historical_capex, expected):
    values = _values(
        net_income_ttm=historical_income,
        net_income_estimate_ntm=estimated_income,
        capex_ttm=historical_capex,
        capex_estimate_ntm=120,
    )
    assert forward_quality_subscores(values)["capex_sustainability"] == expected


@pytest.mark.parametrize("historical_margin", [-10, 0, 10, 20, 30])
def test_profitability_unavailable_even_when_amounts_and_margins_exist(historical_margin):
    values = _values(
        net_income_ttm=100,
        net_income_estimate_ntm=130,
        total_revenue_ttm=1000,
        revenue_estimate_annual=1100,
        net_margin_ttm=historical_margin,
    )
    assert forward_quality_subscores(values)["profitability"] is None
    assert forward_quality_score(values) == 8.8636


@pytest.mark.parametrize(
    ("rating", "expected"),
    [
        ("Strong Buy", 10),
        ("  STRONG   sell ", 0),
        ("Buy", 7.5),
        ("Outperform", 7.5),
        ("Hold", 5),
        ("Neutral", 5),
        ("Sell", 2.5),
        ("Underperform", 2.5),
        ("No rating", None),
        ("buy now", None),
        ("not a strong buy", None),
        (1, None),
        (5, None),
        (None, None),
        ("", None),
    ],
)
def test_analyst_rating_exact_text_categories(rating, expected):
    assert forward_quality_subscores({"analyst_rating": rating})["analyst_rating"] == expected
    assert forward_quality_score({"analyst_rating": rating}) == (
        5 if expected is None else expected
    )


def test_weights_partial_availability_and_neutral_fallback():
    values = _values(
        net_income_ttm=100,
        net_income_estimate_ntm=130,
        total_revenue_ttm=100,
        revenue_estimate_annual=105,
        cash_short_term_investments_annual=100,
        cash_short_term_investments_estimate_annual=85,
        capex_ttm=-100,
        capex_estimate_ntm=120,
        analyst_rating="Buy",
    )
    assert forward_quality_score(values) == 7.7344
    assert forward_quality_score({}) == 5
    assert forward_quality_score({"eps_estimate_annual": 100, "ebit_estimate_ntm": 100}) == 5


@pytest.mark.parametrize(
    "historical,estimated,component",
    [
        ("net_income_ttm", "net_income_estimate_ntm", "net_income_growth"),
        ("total_revenue_ttm", "revenue_estimate_annual", "revenue_comparison"),
        (
            "cash_short_term_investments_annual",
            "cash_short_term_investments_estimate_annual",
            "cash_comparison",
        ),
        ("capex_ttm", "capex_estimate_ntm", "capex_sustainability"),
    ],
)
@pytest.mark.parametrize("currency", [None, "", "EUR", "N/A", "ZZZ"])
def test_currency_metadata_must_be_present_recognized_and_matching(
    historical, estimated, component, currency
):
    values = (
        _values(
            net_income_ttm=100, net_income_estimate_ntm=130, **{historical: 100, estimated: 120}
        )
        if historical != "net_income_ttm"
        else (_values(net_income_ttm=100, net_income_estimate_ntm=120))
    )
    values[f"{estimated}_currency"] = currency
    assert forward_quality_subscores(values)[component] is None


@pytest.mark.parametrize(
    "value",
    [None, "", "invalid", True, float("nan"), float("inf"), -float("inf"), "9" * 400, 10**400],
)
def test_invalid_and_nonfinite_inputs_are_locally_unavailable(value):
    values = _values(net_income_ttm=100, net_income_estimate_ntm=value)
    assert forward_quality_subscores(values)["net_income_growth"] is None
    assert forward_quality_score(values) == 5


@pytest.mark.parametrize(
    "history,estimate", [(5e-324, 1e308), (1e308, 5e-324), (1e308, 1e308), (1e-200, 1e308)]
)
def test_extreme_finite_growth_and_pressure_are_bounded_and_deterministic(history, estimate):
    values = _values(
        net_income_ttm=history,
        net_income_estimate_ntm=estimate,
        capex_ttm=-history,
        capex_estimate_ntm=estimate,
        total_revenue_ttm=history,
        revenue_estimate_annual=estimate,
    )
    first = forward_quality_score(values)
    assert math.isfinite(first) and 0 <= first <= 10
    assert forward_quality_subscores(values)["capex_sustainability"] == 10
    assert [forward_quality_score(values) for _ in range(5)] == [first] * 5


@pytest.mark.parametrize("case", BASELINE["cases"], ids=lambda case: case["name"])
def test_historical_v21_results_match_original_main_exactly(case):
    result = score_row_v2(
        _row(case["values"], case["name"].upper()), load_fundamentals_v2_config(V21_PATH)
    )
    assert asdict(result) == case["expected"]


def test_version_specific_formula_declarations_and_unchanged_configuration():
    old = load_fundamentals_v2_config(V21_PATH)
    new = load_fundamentals_v2_config()
    for key in ("weights", "thresholds", "field_priorities", "missing_data"):
        assert old[key] == new[key]
    assert new["weights"]["forward_quality_score"] == 0.08
    assert old.config_hash == "b919aaea0ed470f293fce0f9abe82173a934b77736722349eb0a8563863b69e1"
    assert old.config_hash != new.config_hash
    for config, count in ((old, 79), (new, 76)):
        fields = {field for item in config["components"].values() for field in item["fields"]}
        assert len(fields) == count
    assert set(old["components"]["forward_quality_score"]["fields"]) == FORWARD_FORMULA_FIELDS_V21
    assert (
        set(new["components"]["forward_quality_score"]["fields"])
        == (COMPONENT_FORMULA_FIELDS["forward_quality_score"])
    )
    for key in new["components"]:
        if key != "forward_quality_score":
            assert new["components"][key] == old["components"][key]
    assert set(new["coverage_only_fields"]) - set(old["coverage_only_fields"]) == {
        "eps_estimate_annual",
        "eps_estimate_quarterly",
        "ebit_estimate_ntm",
    }


@pytest.mark.parametrize("case", BASELINE["cases"], ids=lambda case: case["name"])
def test_only_forward_score_changes_and_penalty_is_identical(case):
    values = _values(**case["values"])
    old = score_row_v2(_row(values), load_fundamentals_v2_config(V21_PATH))
    new = score_row_v2(_row(values))
    for component in FUNDAMENTAL_COMPONENTS - {"forward_quality_score"}:
        assert getattr(new, component) == getattr(old, component)
    assert new.missing_data_penalty == old.missing_data_penalty
    weights = load_fundamentals_v2_config()["weights"]
    raw_old = sum(getattr(old, key) * weight for key, weight in weights.items())
    raw_new = sum(getattr(new, key) * weight for key, weight in weights.items())
    delta = 0.08 * (new.forward_quality_score - old.forward_quality_score)
    assert raw_new - raw_old == pytest.approx(delta)
    assert abs(delta) <= 0.8
    assert abs(new.fundamental_score - old.fundamental_score) <= 0.8001


@pytest.mark.parametrize("model", ["fundamentals_v2.1", "fundamentals_v2.2", "unknown"])
def test_wrong_formula_cannot_be_hidden_under_a_different_model_identity(model):
    config = deepcopy(load_fundamentals_v2_config().data)
    config["model_version"] = model
    if model == "fundamentals_v2.2":
        config["components"]["forward_quality_score"]["fields"].remove("net_margin_ttm")
    with pytest.raises(FundamentalsConfigError):
        score_row_v2(_row({}), config)
    with pytest.raises(ValueError, match="Unsupported"):
        forward_quality_score({}, model_version="unknown")


@pytest.mark.parametrize("path", [V21_PATH, Path("config/fundamentals_v2_2.yaml")])
def test_frozen_delivery_uses_its_own_model_and_hash(path, monkeypatch):
    from app.services import configuration_delivery as delivery
    from app.services.core_effective_configuration import resolve_fundamental_configuration
    from app.services.decision_effective_configuration import configuration_from_payload

    frozen = resolve_fundamental_configuration(path)
    frozen = configuration_from_payload(frozen.snapshot.as_dict())
    config = load_fundamentals_v2_config(path)
    values = _values(net_income_ttm=100, net_income_estimate_ntm=90)
    expected = score_row_v2(_row(values), config)
    monkeypatch.setattr(delivery, "current_delivery", lambda: object())
    monkeypatch.setattr(delivery, "delivered_configuration", lambda *_: frozen)
    assert score_row_v2(_row(values)) == expected
    assert expected.debug["model_version"] == config["model_version"]
    assert expected.debug["config_hash"] == config.config_hash


@pytest.mark.parametrize("path", [V21_PATH, Path("config/fundamentals_v2_2.yaml")])
def test_readiness_registration_respects_retained_fundamental_model(path, monkeypatch):
    from app.services import configuration_delivery as delivery
    from app.services.contextual_consumer_eligibility import FUNDAMENTAL_TO_COMBINED
    from app.services.core_effective_configuration import resolve_fundamental_configuration
    from app.services.decision_effective_configuration import resolve_readiness_policy_configuration

    frozen = resolve_fundamental_configuration(path)
    monkeypatch.setattr(delivery, "current_delivery", lambda: object())
    monkeypatch.setattr(delivery, "delivered_configuration", lambda *_: frozen)
    policy = resolve_readiness_policy_configuration(FUNDAMENTAL_TO_COMBINED)
    registered = policy.values["fundamental_admission"]
    if path == V21_PATH:
        assert registered == {
            "mode": "ACTIVE",
            "policy_version": "fundamental-admission-v3",
            "shadow_policy_version": "fundamental-admission-shadow-v3",
        }
    else:
        assert registered == {
            "mode": "ACTIVE",
            "policy_version": "fundamental-admission-v5",
            "shadow_policy_version": "fundamental-admission-shadow-v5",
            "requires_forward_financial_evidence": True,
            "supported_model_field_counts": [
                {"model_version": "fundamentals_v2.1", "field_count": 79},
                {"model_version": "fundamentals_v2.2", "field_count": 76},
            ],
        }


def _admission(model="fundamentals_v2.2", total=76, available=None):
    available = total if available is None else available
    ratio = round(available / total, 4)
    return FundamentalAdmissionInput(
        consumer_edge="FUNDAMENTAL_TO_COMBINED",
        source_identity_fingerprint="a" * 64,
        source_evidence_id=42,
        decision_cutoff=datetime(2026, 11, 19, tzinfo=UTC),
        fundamental_score=7.25,
        fundamental_label="High-quality quant",
        scoring_model_version=model,
        coverage={
            "available_scoring_fields": available,
            "total_scoring_fields": total,
            "coverage_ratio": ratio,
            "data_coverage_score": round(ratio * 10, 4),
            "missing_fields_by_priority": {k: [] for k in ("critical", "high", "medium", "low")},
        },
        component_coverage=dict.fromkeys(FUNDAMENTAL_COMPONENTS, 1.0),
        parse_diagnostics={"failed_field_count": 0, "failed_fields": []},
        forward_financial_evidence_available=True,
    )


@pytest.mark.parametrize(
    "model,total,valid",
    [
        ("fundamentals_v2.1", 79, True),
        ("fundamentals_v2.1", 76, False),
        ("fundamentals_v2.2", 76, True),
        ("fundamentals_v2.2", 79, False),
        ("unknown", 76, False),
        ("unknown", 79, False),
    ],
)
def test_model_and_count_admission_matrix(model, total, valid):
    decision = evaluate_fundamental_admission(_admission(model, total), authoritative=True)
    assert (decision.eligibility.value == "INCLUDED") is valid
    if valid:
        assert decision.reason_codes == ("complete_score_evidence",)
        assert decision.policy_version == (
            "fundamental-admission-v3" if model.endswith("2.1") else "fundamental-admission-v5"
        )
    else:
        assert "field_coverage_contract_invalid" in decision.reason_codes


@pytest.mark.parametrize("model,total", [("fundamentals_v2.1", 79), ("fundamentals_v2.2", 76)])
def test_overall_coverage_boundary_and_impossible_exact_threshold(model, total):
    assert MINIMUM_OVERALL_COVERAGE == 0.65
    assert MINIMUM_COMPONENT_COVERAGE == 0.50
    lower = math.floor(total * 0.65)
    below = evaluate_fundamental_admission(_admission(model, total, lower))
    above = evaluate_fundamental_admission(_admission(model, total, lower + 1))
    assert "overall_coverage_below_minimum" in below.reason_codes
    assert above.eligibility.value == "INCLUDED"
    # Neither certified denominator has an integer numerator for exactly 65%.
    value = _admission(model, total, lower)
    exact = replace(
        value, coverage={**value.coverage, "coverage_ratio": 0.65, "data_coverage_score": 6.5}
    )
    assert "field_coverage_contract_invalid" in evaluate_fundamental_admission(exact).reason_codes


@pytest.mark.parametrize("coverage,valid", [(0.4999, False), (0.5, True), (0.5001, True)])
def test_component_coverage_boundary(coverage, valid):
    value = _admission()
    value = replace(
        value, component_coverage={**value.component_coverage, "forward_quality_score": coverage}
    )
    assert (evaluate_fundamental_admission(value).eligibility.value == "INCLUDED") is valid


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"source_identity_fingerprint": "bad"}, "source_identity_invalid"),
        ({"source_evidence_id": None}, "source_identity_invalid"),
        ({"fundamental_score": "NaN"}, "fundamental_score_missing_or_non_finite"),
        ({"fundamental_label": "new label"}, "fundamental_label_invalid"),
        ({"consumer_edge": "FUNDAMENTAL_TO_SETUP"}, "consumer_field_dependencies_not_certified"),
        ({"warning_codes": ("new-warning",)}, "blocking_or_unclassified_warning"),
        (
            {"parse_diagnostics": {"failed_field_count": 1, "failed_fields": []}},
            "fundamental_parse_failure",
        ),
        ({"coverage": {}}, "field_coverage_contract_invalid"),
        ({"component_coverage": {}}, "component_coverage_contract_invalid"),
    ],
)
def test_v22_preserves_all_other_fail_closed_admission_requirements(overrides, reason):
    decision = evaluate_fundamental_admission(replace(_admission(), **overrides))
    assert decision.eligibility.value == "UNDECIDED"
    assert reason in decision.reason_codes


@pytest.mark.parametrize("count", [76.5, float("inf"), float("nan"), True, "invalid"])
def test_v22_rejects_malformed_counts_without_truncation(count):
    value = _admission()
    value = replace(value, coverage={**value.coverage, "total_scoring_fields": count})
    decision = evaluate_fundamental_admission(value)
    assert "field_coverage_contract_invalid" in decision.reason_codes
    assert decision.eligibility.value == "UNDECIDED"


@pytest.mark.parametrize("model", [None, [], {}, 22])
def test_nonstring_model_is_unsupported(model):
    decision = evaluate_fundamental_admission(replace(_admission(), scoring_model_version=model))
    assert "scoring_model_version_unsupported" in decision.reason_codes
    assert decision.eligibility.value == "UNDECIDED"


def test_maximum_raw_score_movement_is_exactly_eight_tenths():
    values = _values(**BASELINE["cases"][0]["values"])
    values.update(
        net_income_estimate_ntm=400_000_000,
        revenue_estimate_annual=2_500_000_000,
        cash_short_term_investments_estimate_annual=500_000_000,
        capex_estimate_ntm=None,
        analyst_rating="No rating",
    )
    old = score_row_v2(_row(values), load_fundamentals_v2_config(V21_PATH))
    new = score_row_v2(_row(values))
    assert old.forward_quality_score == 10
    assert new.forward_quality_score == 0
    assert new.fundamental_score - old.fundamental_score == pytest.approx(-0.8)
    assert new.missing_data_penalty == old.missing_data_penalty


def test_retired_formula_fields_keep_their_existing_warning_dependencies():
    values = _values(**BASELINE["cases"][0]["values"])
    values.update(eps_estimate_annual=-1, ebit_estimate_ntm=-1)
    result = score_row_v2(_row(values))
    assert result.forward_quality_score > 4
    assert "forward_quality_weak" in result.warning_flags
    assert result.fundamental_label == "Quality risk"


@pytest.mark.parametrize("fact", [False, None, 0, 1, "true", "false", [], {}])
def test_v22_requires_a_literal_true_frozen_financial_evidence_fact(fact):
    value = replace(_admission(), forward_financial_evidence_available=fact)
    decision = evaluate_fundamental_admission(value)
    assert decision.eligibility.value == "UNDECIDED"
    reason = (
        "no_usable_forward_financial_forecast"
        if fact is False
        else "forward_financial_evidence_missing_or_invalid"
    )
    assert reason in decision.reason_codes


def test_v21_ignores_new_fact_and_preserves_its_frozen_input_shape():
    value = _admission("fundamentals_v2.1", 79)
    old = evaluate_fundamental_admission(value)
    for fact in (None, False, True, "invalid"):
        variant = replace(value, forward_financial_evidence_available=fact)
        assert evaluate_fundamental_admission(variant) == old
        assert "forward_financial_evidence_available" not in variant.canonical_payload()


def test_frozen_fact_distinguishes_valid_and_incompatible_forecasts_without_changing_math():
    values = _values(**BASELINE["cases"][0]["values"])
    values.update(
        net_income_estimate_ntm=values["net_income_ttm"],
        revenue_estimate_annual=None,
        cash_short_term_investments_estimate_annual=None,
        capex_estimate_ntm=None,
        analyst_rating="Hold",
    )
    valid = score_row_v2(_row(values))
    invalid = score_row_v2(_row({**values, "net_income_estimate_ntm_currency": "EUR"}))
    assert valid.forward_quality_score == invalid.forward_quality_score == 5
    assert valid.fundamental_score == invalid.fundamental_score
    assert valid.debug["forward_financial_evidence_available"] is True
    assert invalid.debug["forward_financial_evidence_available"] is False
    assert valid.debug["coverage"] == invalid.debug["coverage"]


def test_zero_financial_subscore_is_usable_evidence_and_rating_alone_is_not():
    loss = score_row_v2(_row(_values(net_income_ttm=100, net_income_estimate_ntm=-10)))
    rating_only = score_row_v2(_row({"analyst_rating": "Strong Buy"}))
    assert loss.forward_quality_score == 0
    assert loss.debug["forward_financial_evidence_available"] is True
    assert rating_only.forward_quality_score == 10
    assert rating_only.debug["forward_financial_evidence_available"] is False


def test_fact_is_read_from_frozen_evidence_not_mutable_serving_debug():
    from datetime import timedelta
    from types import SimpleNamespace

    from app.services.contextual_consumer_eligibility import _fundamental_frozen_input
    from app.services.fundamental_admission_policy import fundamental_input_from_frozen_row

    value = _admission()
    payload = {
        "scoring_model_version": "fundamentals_v2.2",
        "fundamental_score": 7.25,
        "fundamental_label": "High-quality quant",
        "debug_json": {
            "coverage": dict(value.coverage),
            "component_coverage": dict(value.component_coverage),
            "parse_diagnostics": dict(value.parse_diagnostics),
            "forward_financial_evidence_available": False,
        },
    }
    evidence = SimpleNamespace(
        id=42,
        calculated_at=value.decision_cutoff - timedelta(minutes=1),
        calculation_identity_fingerprint="a" * 64,
        payload_json=payload,
    )
    frozen = _fundamental_frozen_input(evidence)
    admitted_input = fundamental_input_from_frozen_row(
        frozen, consumer_edge="FUNDAMENTAL_TO_COMBINED"
    )
    assert admitted_input.forward_financial_evidence_available is False
    assert evaluate_fundamental_admission(admitted_input).eligibility.value == "UNDECIDED"
