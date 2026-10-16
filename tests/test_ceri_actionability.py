import json
from pathlib import Path
from types import SimpleNamespace

from app.services.ceri.actionability import (
    CeriConsumer,
    evaluate_ceri_actionability,
)


def _snapshot(**overrides):
    values = {
        "data_confidence": "Normal",
        "opportunity_score": 7.0,
        "opportunity_coverage_pct": 80.0,
        "opportunity_unrated_reason": None,
        "confidence_ledger_json": {"caps": []},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_low_confidence_is_visible_for_research_but_not_actionable() -> None:
    snapshot = _snapshot(data_confidence="Low")

    research = evaluate_ceri_actionability(
        snapshot,
        consumer=CeriConsumer.RESEARCH_DISPLAY,
        evidence_eligible=True,
    )
    alert = evaluate_ceri_actionability(
        snapshot,
        consumer=CeriConsumer.ALERT,
        evidence_eligible=True,
        require_opportunity=True,
    )

    assert research.allowed is True
    assert research.reason_codes == ("research_visibility_only",)
    assert alert.allowed is False
    assert "confidence_not_actionable" in alert.reason_codes


def test_critical_provenance_cap_blocks_actionability() -> None:
    decision = evaluate_ceri_actionability(
        _snapshot(
            confidence_ledger_json={
                "caps": ["CRITICAL_PROVENANCE_CAP_LOW:estimate_source_unknown"]
            }
        ),
        consumer=CeriConsumer.RECOMMENDATION,
        evidence_eligible=True,
        require_opportunity=True,
    )

    assert decision.allowed is False
    assert "critical_provenance_cap" in decision.reason_codes


def test_risk_alert_requires_accepted_evidence() -> None:
    rejected = evaluate_ceri_actionability(
        _snapshot(),
        consumer=CeriConsumer.ALERT,
        evidence_eligible=True,
        require_risk_evidence=True,
        risk_evidence_accepted=False,
    )
    accepted = evaluate_ceri_actionability(
        _snapshot(),
        consumer=CeriConsumer.ALERT,
        evidence_eligible=True,
        require_risk_evidence=True,
        risk_evidence_accepted=True,
    )

    assert rejected.allowed is False
    assert accepted.allowed is True


def test_ineligible_evidence_is_never_visible_or_actionable() -> None:
    for consumer in CeriConsumer:
        assert not evaluate_ceri_actionability(
            _snapshot(), consumer=consumer, evidence_eligible=False
        ).allowed


def test_frozen_run_33_low_confidence_population_is_research_only() -> None:
    fixture = (
        Path(__file__).parent / "fixtures" / "readiness_phase2" / "run_33.json"
    )
    rows = json.loads(fixture.read_text(encoding="utf-8"))["ceri"]
    snapshots = [SimpleNamespace(**row, confidence_ledger_json={}) for row in rows]

    research = [
        evaluate_ceri_actionability(
            row,
            consumer=CeriConsumer.RESEARCH_DISPLAY,
            evidence_eligible=True,
        )
        for row in snapshots
    ]
    alerts = [
        evaluate_ceri_actionability(
            row,
            consumer=CeriConsumer.ALERT,
            evidence_eligible=True,
            require_opportunity=True,
        )
        for row in snapshots
    ]

    assert len(rows) == 118
    assert all(item.allowed for item in research)
    assert not any(item.allowed for item in alerts)
