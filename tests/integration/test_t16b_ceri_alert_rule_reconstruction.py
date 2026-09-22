from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriAlertEvent, CeriAlertRule, CeriChangeEvent
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.alert_authority import alert_body
from app.services.ceri.original_context_reconstruction import (
    resolve_ceri_alert_rule_original_context,
)
from app.services.decision_effective_configuration import resolve_ceri_decision_configuration
from app.services.original_context_reconstruction import ReconstructionStatus

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@compiles(JSONB, "sqlite")
def _compile_jsonb(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint(_type, _compiler, **_kwargs) -> str:
    return "INTEGER"


@pytest.fixture
def ceri_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        CeriAlertRule.__table__,
        CeriChangeEvent.__table__,
        CeriAlertEvent.__table__,
    ):
        table.create(engine)
    with Session(engine) as db:
        yield db
        db.rollback()
    engine.dispose()


def _native_alert(db: Session, *, proof: bool = True) -> CeriAlertEvent:
    configuration = resolve_ceri_decision_configuration()
    change = CeriChangeEvent(
        company_id=1,
        change_type="ESTIMATE_UP",
        severity="HIGH",
        importance="HIGH",
        signal_class="POSITIVE",
        comparison_state="COMPARABLE",
        delta_json={
            "value": "1",
            "effective_configuration_at_creation": configuration.snapshot.as_dict(),
            "native_change_proof": {"source_kind": "CERTIFIED_SCORE_COMPARISON"},
        },
        dedup_key="change-1",
    )
    db.add(change)
    db.flush()
    base = {
        "effective_configuration_at_creation": configuration.snapshot.as_dict(),
        "change_type": change.change_type,
        "dedup_key": change.dedup_key,
        "delta": change.delta_json,
        "alert_rule": "ESTIMATE_UP",
        "alert_rule_version": "R1",
        "dedup_identity": {"identity_type": "CERI_ALERT", "key": "change-1"},
        "dedup_identity_type": "CERI_ALERT",
        "cooldown_scope": "rule_ticker",
        "cooldown_sessions": 3,
    }
    event = CeriAlertEvent(
        alert_rule_id=7,
        source_change_event_id=change.id,
        source_catalyst_revision_id=None,
        event_key="historical-event-key",
        ticker="ACME",
        severity="HIGH",
        importance="HIGH",
        signal_class="POSITIVE",
        validity_classification="VALID_CURRENT",
        status="UNREAD",
        evidence_json=base,
    )
    if proof:
        event.evidence_json = {
            **base,
            "native_alert_proof": Canonical.canonicalize(
                {
                    "artifact_role": "SUPPORTING_NOTIFICATION",
                    "classification": "SUPPORTED_DISTINCT_SAFE",
                    "change_key": change.dedup_key,
                    "body_fingerprint": Canonical.fingerprint(alert_body(event)),
                    "operation_time": {
                        "cutoff_at": datetime(2026, 9, 15, 20, tzinfo=UTC),
                        "session": "2026-09-15",
                    },
                    "rule": {
                        "row_id": 7,
                        "rule_id": "ESTIMATE_UP",
                        "enabled": True,
                        "severity": "HIGH",
                        "cooldown_sessions": 3,
                        "config_version": "R1",
                    },
                }
            ),
        }
    db.add(event)
    db.flush()
    return event


def test_ceri_old_r1_c1_k1_resolves_without_reading_current_r2(monkeypatch, ceri_db):
    event = _native_alert(ceri_db)
    ceri_db.add(
        CeriAlertRule(
            id=7,
            rule_id="ESTIMATE_UP",
            enabled=True,
            severity="CRITICAL",
            thresholds_json={"current": "R2"},
            scope_json={},
            cooldown_sessions=99,
            config_version="R2",
            source_event_types_json=[],
        )
    )
    ceri_db.flush()
    monkeypatch.setattr(
        "app.services.ceri.alert_authority.validate_change_source",
        lambda _db, _change: None,
    )
    resolved = resolve_ceri_alert_rule_original_context(ceri_db, event.id)
    assert resolved.authorization.authorized
    assert resolved.manifest.status is ReconstructionStatus.EXACT
    assert resolved.reconstructed_output["historical_rule"]["config_version"] == "R1"
    assert resolved.reconstructed_output["historical_rule"]["cooldown_sessions"] == 3
    assert resolved.reconstructed_output["historical_configuration"] != {"current": "R2"}


def test_ceri_legacy_alert_without_native_rule_proof_is_unavailable(monkeypatch, ceri_db):
    event = _native_alert(ceri_db, proof=False)
    monkeypatch.setattr(
        "app.services.ceri.alert_authority.validate_change_source",
        lambda _db, _change: None,
    )
    resolved = resolve_ceri_alert_rule_original_context(ceri_db, event.id)
    assert not resolved.authorization.authorized
    assert resolved.manifest.status is ReconstructionStatus.PERMANENTLY_UNAVAILABLE
    assert resolved.reconstructed_output is None
