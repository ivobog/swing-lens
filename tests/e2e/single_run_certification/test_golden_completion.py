"""Fast audit of literal A05B vectors; no application calculator imports."""

import copy
import json
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from single_run_certification.golden_completion import COMPLETION, KEYS, compare_vector
from single_run_certification.golden_oracle import (
    OracleIncomplete,
    OracleMismatch,
    decimal_equal,
    expected_manifest,
    require_complete,
)


def test_sealed_completion_and_all_domain_populations():
    for handoff in (False, True):
        manifest = expected_manifest(handoff)
        require_complete(manifest)
        domains = manifest["completion"]["domains"]
        assert len(domains) == 26
        for section, rows in domains.items():
            compare_vector(rows, copy.deepcopy(rows), keys=KEYS[section])
        assert len(domains["winner_forwards"]) == 100
        assert len(domains["winner_targets"]) == 20
        assert domains["alerts"] == domains["setup_alerts"] == []
        assert all(row["point_probability"] is None for row in domains["winner_estimates"])


def test_independent_dot_products_and_component_sums():
    worksheet = json.loads((COMPLETION / "core-derivations.json").read_text())
    domains = expected_manifest(False)["completion"]["domains"]
    for row in domains["technical"]:
        item = worksheet["technical"][row["ticker"]]
        for column, terms in [
            ("local_trend_score", "local_trend_terms"),
            ("momentum_score", "momentum_terms"),
            ("setup_score", "setup_terms"),
        ]:
            decimal_equal(
                row[column], sum(Decimal(v) for v in item[terms].values()), tolerance="0.00000001"
            )
        total = sum(
            Decimal(item["v4_parts"][k]) * Decimal(w) for k, w in item["regime_weights"].items()
        )
        decimal_equal(row["dual_score"], total.quantize(Decimal(".0001"), rounding=ROUND_HALF_EVEN))
    for row in domains["combined"]:
        penalty = Decimal("1.5") if row["technical_classification"] == "Overheated momentum" else 0
        total = (
            Decimal(row["fundamental_score"]) * Decimal(".55")
            + Decimal(row["dual_score"]) * Decimal(".45")
            - penalty
        )
        decimal_equal(
            row["final_score"], total.quantize(Decimal(".0001"), rounding=ROUND_HALF_EVEN)
        )
    for profile in {row["profile"] for row in domains["ranking"]}:
        rows = [r for r in domains["ranking"] if r["profile"] == profile]
        ordered = sorted(
            rows,
            key=lambda r: (
                3 if r["decision_label"] == "Avoid" else int(r["has_warning"]),
                -Decimal(r["profile_score"]),
                -Decimal(r["technical_profile_score"]),
                r["ticker"],
            ),
        )
        assert [r["profile_rank"] for r in ordered] == list(range(1, 11))


@pytest.mark.parametrize(
    "section,field,wrong",
    [
        ("technical", "dual_score", "7.99"),
        ("technical", "classification", "Fresh breakout"),
        ("combined", "combined_decision", "Avoid"),
        ("ranking", "profile_rank", 2),
        ("market_regime", "regime", "Risk off"),
        ("sector", "rotation_state", "Leading"),
        ("revisions", "pct_change", "4"),
        ("captures", "ticker", "FOREIGN"),
        ("setup", "is_canonical", False),
        ("winner_acquisition", "acquisition_outcome", "EXCLUDED"),
        ("winner", "exclusion_reason", "NO_TECHNICAL"),
        ("winner_forwards", "due_session", "2026-10-28"),
        ("derived", "ownership_mode", "STANDALONE"),
    ],
)
def test_new_domain_mutation_is_rejected(section, field, wrong):
    rows = expected_manifest(False)["completion"]["domains"][section]
    changed = copy.deepcopy(rows)
    assert changed[0][field] != wrong
    changed[0][field] = wrong
    with pytest.raises(OracleMismatch):
        compare_vector(rows, changed, keys=KEYS[section])


def test_missing_winner_child_and_wrong_tie_are_rejected():
    domains = expected_manifest(False)["completion"]["domains"]
    with pytest.raises(OracleMismatch):
        compare_vector(
            domains["winner_targets"], domains["winner_targets"][:-1], keys=KEYS["winner_targets"]
        )
    rows = [
        r
        for r in domains["ranking"]
        if r["profile"] == "early_rocket" and r["ticker"] in ("KLIC", "ACMR")
    ]
    assert rows[0]["profile_score"] == rows[1]["profile_score"]
    changed = copy.deepcopy(rows)
    changed[0]["profile_rank"], changed[1]["profile_rank"] = (
        changed[1]["profile_rank"],
        changed[0]["profile_rank"],
    )
    with pytest.raises(OracleMismatch):
        compare_vector(rows, changed, keys=KEYS["ranking"])


@pytest.mark.parametrize(
    "mutation", ["contract", "section", "field", "population", "variants", "input"]
)
def test_completion_cannot_be_claimed_by_deleting_requirements(mutation):
    manifest = expected_manifest(False)
    c = manifest["completion"]
    if mutation == "contract":
        del c["contracts"]["technical"]
    if mutation == "section":
        c["contracts"]["technical"]["sections"] = ["technical"]
    if mutation == "field":
        del c["domains"]["technical"][0]["dual_score"]
    if mutation == "population":
        c["domains"]["winner_estimates"] = []
    if mutation == "variants":
        del c["variants"]["true"]
    if mutation == "input":
        c["base_input_identity"] = "foreign"
    with pytest.raises(OracleIncomplete):
        require_complete(manifest)


def test_opaque_source_configuration_and_canonical_issuer_associations():
    from single_run_certification.golden_completion import references_match

    owned = {"BHE": 101, "BLLN": 102}
    assert references_match(owned, dict(owned))
    assert not references_match(owned, {"BHE": 102, "BLLN": 101})
    source = {"technical_score_id": 11, "configuration": "sealed-policy"}
    assert not references_match(source, {**source, "technical_score_id": 12})
    assert not references_match(source, {**source, "configuration": "foreign-policy"})


def test_ceri_revision_arithmetic_and_legitimate_unrated_state():
    from single_run_certification.golden_oracle import artifact

    domains = expected_manifest(False)["completion"]["domains"]
    sources = {
        r["provider_record_id"]: r for r in artifact("provider-replay.json")["eodhd"]["estimates"]
    }
    quantum = Decimal(".000001")
    for row in domains["revisions"]:
        current = sources[row["current_record_key"]]
        baseline = sources[row["baseline_record_key"]]
        change = Decimal(current["consensus"]) - Decimal(baseline["consensus"])
        pct = change / Decimal(baseline["consensus"]) * 100
        assert Decimal(row["absolute_change"]) == change.quantize(quantum)
        assert Decimal(row["pct_change"]) == pct.quantize(quantum)
        assert Decimal(row["net_breadth"]) == (Decimal(7) / 9).quantize(quantum)
    confidence = (
        Decimal(8) * Decimal(".25")
        + Decimal("1.25") * Decimal(".20")
        + Decimal(10) * Decimal(".15")
        + Decimal(9) * Decimal(".10")
        + Decimal(10) * Decimal(".10")
    )
    for row in domains["captures"]:
        assert Decimal(row["confidence_score"]) == confidence == Decimal("5.65")
        assert Decimal(row["opportunity_coverage_pct"]) == 50
        assert row["opportunity_score"] is None
        assert row["posture"] == "Unrated"


def test_context_setup_confidence_and_winner_calendar_arithmetic():
    from single_run_certification.golden_oracle import artifact

    domains = expected_manifest(False)["completion"]["domains"]
    worksheet = json.loads((COMPLETION / "context-derivations.json").read_text())
    total = sum(
        Decimal(worksheet["sector_components"][k]) * Decimal(w)
        for k, w in worksheet["sector_weights"].items()
    )
    decimal_equal(domains["sector"][0]["sector_final_score"], total.quantize(Decimal(".0001")))
    assert Decimal(domains["market_regime"][0]["score"]) == sum(
        map(Decimal, ["2.5", "2", "1.5", "1.5", "1", "1"])
    )
    assert {r["confidence_score"] for r in domains["setup"]} == {72}
    sessions = artifact("calendar.json")["sessions"]
    for row in domains["winner_forwards"]:
        assert (
            row["due_session"]
            == sessions[sessions.index(row["entry_session"]) + row["horizon_sessions"] - 1]
        )


def test_optional_sector_omission_is_explicit_and_preserves_required_sources():
    domains = expected_manifest(False)["completion"]["domains"]
    for row in domains["setup"]:
        assert row["confidence_score"] == 72
        assert row["context_completeness"] == ".5"
        assert row["source_eligibility"]["sector"] == {
            "included": False,
            "status": "POLICY_UNDECIDED",
            "reasons": ["DEGRADED_POLICY_UNDECIDED"],
        }
    for row in domains["winner"]:
        assert row["eligibility_status"] == "ELIGIBLE"
        assert row["features"]["sector_rank"] is None
        assert row["source_eligibility"]["sector"]["included"] is False
        assert all(
            row["source_eligibility"][role]["included"]
            for role in ("technical", "ranking", "combined")
        )
        assert row["warning_flags"] == ["missing_sector_rotation_context"]


def test_empty_map_and_unreviewed_null_score_cannot_claim_truth():
    manifest = expected_manifest(False)
    rows = manifest["completion"]["domains"]["market_regime"]
    changed = copy.deepcopy(rows)
    changed[0]["participation"]["ranking_profile_average_scores"] = {"early_rocket": 7}
    with pytest.raises(OracleMismatch):
        compare_vector(rows, changed, keys=KEYS["market_regime"])
    manifest["completion"]["domains"]["technical"][0]["dual_score"] = None
    with pytest.raises(OracleIncomplete):
        require_complete(manifest)


def test_ceri_unknown_pipeline_dimension_does_not_admit_a_foreign_owner():
    rows = expected_manifest(False)["completion"]["domains"]["captures"]
    assert {r["pipeline_ownership_state"] for r in rows} == {"UNKNOWN"}
    changed = copy.deepcopy(rows)
    changed[0]["pipeline_ownership_state"] = "KNOWN"
    with pytest.raises(OracleMismatch):
        compare_vector(rows, changed, keys=KEYS["captures"])
