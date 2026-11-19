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
