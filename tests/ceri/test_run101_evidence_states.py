from __future__ import annotations

from app.services.ceri.evidence_state_service import CeriEvidenceLedgerService


def test_evidence_ledger_separates_persisted_considered_rejected_selected_and_scored() -> None:
    lineage = {
        "revision_pairs": [
            {"feature_id": 10, "available": True, "unavailable_reason": None},
            {"feature_id": 11, "available": False, "unavailable_reason": "BASELINE_MISSING"},
        ],
        "guidance_ids": [20, 21],
        "guidance_selected_ids": [20],
        "guidance_rejected": [{"id": 21, "reason": "GUIDANCE_REQUIRES_REVIEW"}],
        "catalyst_event_ids": [30, 31],
        "catalyst_selected_event_ids": [30],
        "catalyst_rejected": [{"event_id": 31, "reason": "ISSUER_MISMATCH"}],
        "earnings_ids": [40],
    }

    enriched = CeriEvidenceLedgerService().enrich(
        lineage,
        source_ids=[1, 2, 3],
        opportunity_selected_ids=[10, 20, 40],
        risk_selected_ids=[30],
    )

    by_key = {
        (row["evidence_type"], row["evidence_id"]): row
        for row in enriched["evidence_states"]
    }
    assert by_key[("GUIDANCE", 20)]["states"][-2:] == [
        "SELECTED_FOR_COMPONENT",
        "SCORED",
    ]
    assert by_key[("GUIDANCE", 21)]["states"][-1] == "REJECTED"
    assert by_key[("REVISION_FEATURE", 11)]["reason"] == "BASELINE_MISSING"
    assert by_key[("CATALYST_EVENT", 30)]["states"][-1] == "SCORED"
    assert enriched["evidence_counts"] == {
        "PERSISTED": 10,
        "CONSIDERED": 7,
        "REJECTED": 3,
        "ACCEPTED": 4,
        "SELECTED_FOR_COMPONENT": 4,
        "SCORED": 4,
    }


def test_provider_readiness_is_preserved_as_evidence_availability_not_zero_score() -> None:
    enriched = CeriEvidenceLedgerService().enrich(
        {"provider_readiness": {"sec": "BOOTSTRAP_REQUIRED"}},
        source_ids=[],
        opportunity_selected_ids=[],
        risk_selected_ids=[],
    )

    assert enriched["provider_readiness"]["sec"] == "BOOTSTRAP_REQUIRED"
    assert enriched["evidence_counts"]["SCORED"] == 0


def test_xpel_breadth_selection_is_lineage_even_when_magnitude_pair_is_unavailable() -> None:
    enriched = CeriEvidenceLedgerService().enrich(
        {
            "revision_pairs": [
                {
                    "feature_id": 17174,
                    "available": False,
                    "unavailable_reason": "baseline_unavailable",
                }
            ]
        },
        source_ids=[],
        opportunity_selected_ids=[17174],
        risk_selected_ids=[],
    )

    row = enriched["evidence_states"][0]
    assert row["states"] == [
        "PERSISTED",
        "CONSIDERED",
        "ACCEPTED",
        "SELECTED_FOR_COMPONENT",
        "SCORED",
    ]
