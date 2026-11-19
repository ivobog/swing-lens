"""Immutable source pins survive an authoritative v2.2 forecast omission."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.core_calculation_evidence import CoreEvidenceKind
from app.services.pipeline_executor import _validate_projection_source_pins


def specimen():
    permission = {
        "included": False,
        "fundamental_admission_mode": "ACTIVE",
        "source_feature_id": 100,
        "decision": {"status": "ELIGIBLE", "producer_evidence_id": 41},
        "orthogonal_decision": {
            "policy_version": "fundamental-admission-v5",
            "authoritative": True,
            "eligibility": "UNDECIDED",
            "source_evidence_id": 41,
            "reason_codes": ["no_usable_forward_financial_forecast"],
        },
        "fundamental_admission_input": {
            "scoring_model_version": "fundamentals_v2.2",
            "source_evidence_id": 41,
            "forward_financial_evidence_available": False,
        },
    }
    pins = {"fundamental": 41, "technical": 42}
    retained = {"raw_row_id": 9, "fundamental_evidence_id": 41, "technical_evidence_id": 42}
    projection = {
        "debug_json": {
            "source_ids": {
                "raw_row_id": 9,
                "fundamental_score_id": None,
                "technical_score_id": 200,
            },
            "contextual_consumer_eligibility": {"fundamental": deepcopy(permission)},
        }
    }
    evidence = SimpleNamespace(
        source_evidence_ids_json=pins,
        payload_json={
            "debug_json": {
                "source_ids": retained,
                "contextual_consumer_eligibility": {"fundamental": deepcopy(permission)},
            }
        },
    )

    def get_source(_model, address):
        assert address in {100, 200}
        return SimpleNamespace(evidence_id=41 if address == 100 else 42)

    return SimpleNamespace(get=get_source), projection, evidence


def validate(db, projection, evidence):
    _validate_projection_source_pins(
        db, kind=CoreEvidenceKind.COMBINED, projection=projection, evidence=evidence
    )


def test_sealed_v22_omission_preserves_source_identity_with_legacy_eligible_readiness():
    db, projection, evidence = specimen()
    validate(db, projection, evidence)


@pytest.mark.parametrize("fact", [None, 0, "false"])
def test_sealed_v22_invalid_evidence_remains_a_valid_recorded_omission(fact):
    db, projection, evidence = specimen()
    for parent in [projection, evidence.payload_json]:
        permission = parent["debug_json"]["contextual_consumer_eligibility"]["fundamental"]
        permission["fundamental_admission_input"]["forward_financial_evidence_available"] = fact
        permission["orthogonal_decision"]["reason_codes"] = [
            "forward_financial_evidence_missing_or_invalid"
        ]
    validate(db, projection, evidence)


@pytest.mark.parametrize(
    "fault",
    [
        "legacy-model",
        "unknown-model",
        "old-policy",
        "unknown-policy",
        "shadow-mode",
        "non-authoritative",
        "included",
        "required-rejection",
        "unknown-outcome",
        "unrelated-reason",
        "usable-forecast",
        "wrong-source",
        "wrong-input-source",
        "boolean-source",
        "missing-retained-permission",
        "legacy-foreign-source",
        "changed-serving-admission",
        "changed-serving-input",
        "changed-serving-mode",
        "changed-serving-inclusion",
        "changed-serving-context-source",
        "changed-raw-source",
    ],
)
def test_v22_resume_omission_refuses_foreign_missing_changed_or_legacy_authority(fault):
    db, projection, evidence = specimen()
    serving = projection["debug_json"]["contextual_consumer_eligibility"]["fundamental"]
    retained = evidence.payload_json["debug_json"]["contextual_consumer_eligibility"]["fundamental"]
    for permission in [serving, retained]:
        admission = permission["orthogonal_decision"]
        inputs = permission["fundamental_admission_input"]
        if fault == "legacy-model":
            inputs["scoring_model_version"] = "fundamentals_v2.1"
        elif fault == "unknown-model":
            inputs["scoring_model_version"] = "fundamentals_v9"
        elif fault == "old-policy":
            admission["policy_version"] = "fundamental-admission-v4"
        elif fault == "unknown-policy":
            admission["policy_version"] = "fundamental-admission-v99"
        elif fault == "shadow-mode":
            permission["fundamental_admission_mode"] = "SHADOW"
        elif fault == "non-authoritative":
            admission["authoritative"] = False
        elif fault == "included":
            admission["eligibility"] = "INCLUDED"
        elif fault == "required-rejection":
            admission["eligibility"] = "REJECTED_REQUIRED"
        elif fault == "unknown-outcome":
            admission["eligibility"] = "UNKNOWN"
        elif fault == "unrelated-reason":
            admission["reason_codes"] = ["coverage_below_minimum"]
        elif fault == "usable-forecast":
            inputs["forward_financial_evidence_available"] = True
        elif fault == "wrong-source":
            admission["source_evidence_id"] = 999
        elif fault == "wrong-input-source":
            inputs["source_evidence_id"] = 999
        elif fault == "boolean-source":
            admission["source_evidence_id"] = True
        elif fault == "legacy-foreign-source":
            permission["decision"]["producer_evidence_id"] = 999
    if fault == "missing-retained-permission":
        evidence.payload_json["debug_json"].pop("contextual_consumer_eligibility")
    elif fault == "changed-serving-admission":
        serving["orthogonal_decision"]["decision_cutoff"] = "foreign"
    elif fault == "changed-serving-input":
        serving["fundamental_admission_input"]["forward_financial_evidence_available"] = None
    elif fault == "changed-serving-mode":
        serving["fundamental_admission_mode"] = "LEGACY"
    elif fault == "changed-serving-inclusion":
        serving["included"] = True
    elif fault == "changed-serving-context-source":
        serving["source_feature_id"] = 200
    elif fault == "changed-raw-source":
        projection["debug_json"]["source_ids"]["raw_row_id"] = 999
    with pytest.raises(ValueError, match="source .*changed"):
        validate(db, projection, evidence)


@pytest.mark.parametrize("current_positive", [False, True])
def test_browser_positive_forecast_metadata_is_explicit_and_legacy_default_stays_bare(
    tmp_path, current_positive
):
    import csv
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from single_run_certification.fixtures import write_canonical_csv

    from app.services.column_mapper import map_csv_rows
    from app.services.fundamental_admission_policy import (
        FundamentalAdmissionInput,
        evaluate_fundamental_admission,
    )
    from app.services.fundamental_ranker_v2 import score_rows_v2

    canonical = json.loads(
        Path("tests/e2e/single_run_certification/golden/v7/financial-source.json").read_text()
    )["canonical"]
    metadata = (
        {
            key: canonical[key]
            for key in ("net_income_ttm_currency", "net_income_estimate_ntm_currency")
        }
        if current_positive
        else None
    )
    path = tmp_path / "positive.csv"
    write_canonical_csv(path, forward_currency_metadata=metadata)
    with path.open(newline="") as handle:
        rows = map_csv_rows(list(csv.DictReader(handle)))
    scores = score_rows_v2(rows)
    alfa = next(score for score in scores if score.ticker == "ALFA")
    source = next(row for row in rows if row.ticker == "ALFA")
    assert alfa.debug["forward_financial_evidence_available"] is current_positive
    assert source.canonical.get("net_income_ttm_currency") == ("USD" if current_positive else None)
    assert all(
        not row.canonical.get("net_income_ttm_currency") for row in rows if row.ticker != "ALFA"
    )
    decision = evaluate_fundamental_admission(
        FundamentalAdmissionInput(
            consumer_edge="FUNDAMENTAL_TO_COMBINED",
            source_identity_fingerprint="a" * 64,
            source_evidence_id=1,
            decision_cutoff=datetime(2026, 11, 19, tzinfo=UTC),
            fundamental_score=alfa.fundamental_score,
            fundamental_label=alfa.fundamental_label,
            scoring_model_version=alfa.debug["model_version"],
            coverage=alfa.debug["coverage"],
            component_coverage=alfa.debug["component_coverage"],
            parse_diagnostics=alfa.debug["parse_diagnostics"],
            warning_codes=tuple(alfa.warning_flags),
            forward_financial_evidence_available=alfa.debug["forward_financial_evidence_available"],
        ),
        authoritative=True,
    )
    assert decision.eligibility.value == ("INCLUDED" if current_positive else "UNDECIDED")
