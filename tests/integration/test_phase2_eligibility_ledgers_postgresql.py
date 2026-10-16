from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from core_readiness_helpers import certified_fundamental
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    ConsumerEligibilityDecisionRecord,
    EligibilityShadowEvaluationRecord,
    UploadRun,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.consumer_eligibility_ledger import (
    EligibilityShadowEvaluation,
    WinnerAcquisitionDecision,
    WinnerAcquisitionOutcome,
    persist_consumer_decision,
    persist_shadow_evaluation,
    persist_winner_acquisition_decision,
)
from app.services.contextual_consumer_eligibility import (
    FUNDAMENTAL_TO_COMBINED,
    contextual_decision_input,
    record_fundamental_admission,
)
from app.services.eligibility_policy import (
    AnalyticalConfidence,
    CompletenessStatus,
    ConsumerEligibilityOutcome,
    ExecutionOutcome,
    IntegrityStatus,
    OrthogonalConsumerDecision,
)
from app.services.warning_taxonomy import classify_warnings
from app.settings import FundamentalAdmissionMode

pytestmark = [pytest.mark.integration, pytest.mark.destructive]

REVISION = "0092_phase2_eligibility_ledgers"
PRIOR_REVISION = "0091_readiness_constraints"
NOW = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
TABLES = {
    "consumer_eligibility_decisions",
    "winner_acquisition_decisions",
    "eligibility_shadow_evaluations",
}


def _config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _decision() -> OrthogonalConsumerDecision:
    return OrthogonalConsumerDecision(
        producer="COMBINED",
        consumer="WINNER",
        consumer_edge="COMBINED_TO_WINNER",
        policy_version="test-v1",
        source_identity_fingerprint="a" * 64,
        source_evidence_id=None,
        decision_cutoff=NOW,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        integrity_status=IntegrityStatus.CERTIFIED,
        completeness_status=CompletenessStatus.COMPLETE,
        analytical_confidence=AnalyticalConfidence.LOW,
        warnings=classify_warnings(["high_leverage"]),
        eligibility=ConsumerEligibilityOutcome.INCLUDED,
        reason_codes=("classified_risk_warning",),
        authoritative=False,
    )


def test_migration_round_trip_persistence_idempotence_and_database_immutability(
    disposable_postgres_database: str,
) -> None:
    config = _config(disposable_postgres_database)
    command.upgrade(config, PRIOR_REVISION)
    engine = create_engine(disposable_postgres_database)
    assert TABLES.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(config, REVISION)
    assert TABLES.issubset(inspect(engine).get_table_names())

    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="phase2.csv", status="COMPLETED"))
        db.flush()
        decision = _decision()
        consumer = persist_consumer_decision(
            db,
            decision=decision,
            source_artifact_kind="COMBINED",
            pipeline_run_id=None,
            upload_run_id=1,
        )
        duplicate = persist_consumer_decision(
            db,
            decision=decision,
            source_artifact_kind="COMBINED",
            pipeline_run_id=None,
            upload_run_id=1,
        )
        acquisition = persist_winner_acquisition_decision(
            db,
            WinnerAcquisitionDecision(
                upload_run_id=1,
                ticker="ABC",
                decision_cutoff=NOW,
                outcome=WinnerAcquisitionOutcome.EXCLUDED,
                eligibility_reason="source_ineligible",
                source_identities={"combined": "a" * 64},
                source_decisions=(decision.canonical_payload(),),
                warning_classifications=decision.warnings,
            ),
        )
        shadow = persist_shadow_evaluation(
            db,
            EligibilityShadowEvaluation(
                pipeline_run_id=None,
                upload_run_id=1,
                ticker="ABC",
                consumer_edge=decision.consumer_edge,
                policy_version=decision.policy_version,
                source_identity_fingerprint=decision.source_identity_fingerprint,
                decision_cutoff=NOW,
                legacy_outcome=ConsumerEligibilityOutcome.UNDECIDED,
                proposed_decision=decision,
                frozen_input={"warning_flags": ["high_leverage"]},
            ),
        )
        db.commit()
        assert consumer.id == duplicate.id
        assert acquisition.id is not None
        assert shadow.differs is True

    with engine.begin() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM consumer_eligibility_decisions")
        ).scalar_one()
        assert count == 1

    with engine.connect() as connection, pytest.raises(
        DBAPIError, match="PHASE2_DECISION_EVIDENCE_IS_APPEND_ONLY"
    ):
        connection.execute(
            text(
                "UPDATE consumer_eligibility_decisions "
                "SET eligibility = 'UNDECIDED' WHERE id = 1"
            )
        )

    command.downgrade(config, PRIOR_REVISION)
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    command.upgrade(config, REVISION)
    assert TABLES.issubset(inspect(engine).get_table_names())
    engine.dispose()


def test_fundamental_shadow_and_active_decisions_are_durable_at_consumer_boundary(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    config = _config(disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    ratio = round(78 / 79, 4)
    components = {
        name: 1.0
        for name in (
            "growth_quality_score",
            "profitability_quality_score",
            "fcf_quality_score",
            "earnings_quality_score",
            "capital_efficiency_score",
            "balance_sheet_quality_score",
            "valuation_quality_score",
            "forward_quality_score",
            "shareholder_quality_score",
            "liquidity_risk_score",
        )
    }
    fundamental = certified_fundamental(
        ticker="ACME",
        fundamental_score=7.25,
        fundamental_label="High-quality quant",
        scoring_model_version="fundamentals_v2.1",
        missing_data_penalty=0.05,
        v2_warning_flags_json={"flags": ["balance_sheet_stress"]},
        debug_json={
            "coverage": {
                "available_scoring_fields": 78,
                "total_scoring_fields": 79,
                "coverage_ratio": ratio,
                "data_coverage_score": round(ratio * 10, 4),
                "missing_fields_by_priority": {
                    "critical": [],
                    "high": [],
                    "medium": [],
                    "low": ["capex_estimate_ntm"],
                },
            },
            "component_coverage": components,
            "parse_diagnostics": {"failed_field_count": 0, "failed_fields": []},
        },
    )
    evidence = fundamental.calculation_evidence
    evidence.calculation_identity_json = {}
    evidence.payload_fingerprint = Canonical.fingerprint(evidence.payload_json)
    evidence.source_evidence_ids_json = {}
    evidence.evidence_key = "phase2-9-fundamental-policy"
    evidence.calculated_at = NOW

    with Session(engine, expire_on_commit=False) as db:
        db.add(UploadRun(id=1, filename="phase2-9.csv", status="COMPLETED"))
        db.flush()
        db.add(evidence)
        db.flush()
        db.add(fundamental)
        db.flush()

        for mode in (FundamentalAdmissionMode.SHADOW, FundamentalAdmissionMode.ACTIVE):
            monkeypatch.setattr(
                "app.settings.get_settings",
                lambda selected=mode: SimpleNamespace(
                    fundamental_admission_mode=selected
                ),
            )
            projected, permission = contextual_decision_input(
                fundamental,
                FUNDAMENTAL_TO_COMBINED,
            )
            record_fundamental_admission(
                db,
                row=fundamental,
                policy=FUNDAMENTAL_TO_COMBINED,
                permission=permission,
                pipeline_run_id=None,
            )
            assert (projected is not None) is (mode is FundamentalAdmissionMode.ACTIVE)

        db.commit()
        active = db.scalars(select(ConsumerEligibilityDecisionRecord)).one()
        shadow = db.scalars(select(EligibilityShadowEvaluationRecord)).one()
        assert active.authoritative is True
        assert active.eligibility == "INCLUDED"
        assert active.analytical_confidence == "LOW"
        assert shadow.legacy_outcome == "UNDECIDED"
        assert shadow.proposed_outcome == "INCLUDED"
        assert shadow.differs is True

    engine.dispose()
