"""Round-trip the Phase 2 eligibility migration in a disposable PostgreSQL DB."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import UploadRun
from app.services.consumer_eligibility_ledger import (
    EligibilityShadowEvaluation,
    WinnerAcquisitionDecision,
    WinnerAcquisitionOutcome,
    persist_consumer_decision,
    persist_shadow_evaluation,
    persist_winner_acquisition_decision,
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
from app.settings import get_settings

PRIOR_REVISION = "0091_readiness_constraints"
PHASE2_REVISION = "0092_phase2_eligibility_ledgers"
TABLES = {
    "consumer_eligibility_decisions",
    "winner_acquisition_decisions",
    "eligibility_shadow_evaluations",
}


def main() -> int:
    active_url = make_url(get_settings().database_url)
    admin_url = active_url.set(database="postgres")
    database_name = f"swinglens_qa_phase2_{uuid.uuid4().hex[:12]}"
    candidate_url = active_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    candidate_engine = None
    result: dict[str, object] = {"database": database_name, "dropped": False}
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        config = Config("alembic.ini")
        configure_guarded_alembic(config, candidate_url)
        command.upgrade(config, PRIOR_REVISION)
        candidate_engine = create_engine(candidate_url)
        result["absent_at_0091"] = TABLES.isdisjoint(
            inspect(candidate_engine).get_table_names()
        )

        command.upgrade(config, PHASE2_REVISION)
        result["present_at_0092"] = TABLES.issubset(
            inspect(candidate_engine).get_table_names()
        )
        result.update(_certify_ledgers(candidate_engine))

        command.downgrade(config, PRIOR_REVISION)
        result["absent_after_downgrade"] = TABLES.isdisjoint(
            inspect(candidate_engine).get_table_names()
        )

        command.upgrade(config, PHASE2_REVISION)
        result["present_after_reupgrade"] = TABLES.issubset(
            inspect(candidate_engine).get_table_names()
        )
        checks = [value for key, value in result.items() if key not in {"database", "dropped"}]
        if not checks or not all(checks):
            raise RuntimeError("Phase 2 migration round-trip did not satisfy every check")
        return 0
    finally:
        if candidate_engine is not None:
            candidate_engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
        result["dropped"] = True
        admin_engine.dispose()
        print(json.dumps(result, sort_keys=True))


def _certify_ledgers(engine) -> dict[str, bool]:
    decision_cutoff = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
    decision = OrthogonalConsumerDecision(
        producer="COMBINED",
        consumer="WINNER",
        consumer_edge="COMBINED_TO_WINNER",
        policy_version="certification-v1",
        source_identity_fingerprint="a" * 64,
        source_evidence_id=None,
        decision_cutoff=decision_cutoff,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        integrity_status=IntegrityStatus.CERTIFIED,
        completeness_status=CompletenessStatus.COMPLETE,
        analytical_confidence=AnalyticalConfidence.LOW,
        warnings=classify_warnings(["high_leverage"]),
        eligibility=ConsumerEligibilityOutcome.INCLUDED,
        reason_codes=("classified_risk_warning",),
        authoritative=False,
    )
    with Session(engine) as db:
        db.add(UploadRun(id=1, filename="phase2-certification.csv", status="COMPLETED"))
        db.flush()
        first = persist_consumer_decision(
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
                decision_cutoff=decision_cutoff,
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
                decision_cutoff=decision_cutoff,
                legacy_outcome=ConsumerEligibilityOutcome.UNDECIDED,
                proposed_decision=decision,
                frozen_input={"warning_flags": ["high_leverage"]},
            ),
        )
        db.commit()
        persistence_ok = (
            first.id == duplicate.id and acquisition.id is not None and shadow.id is not None
        )

    append_only = False
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE consumer_eligibility_decisions "
                    "SET eligibility = 'UNDECIDED' WHERE id = :row_id"
                ),
                {"row_id": first.id},
            )
    except DBAPIError as exc:
        append_only = "PHASE2_DECISION_EVIDENCE_IS_APPEND_ONLY" in str(exc)
    return {"ledger_persistence": persistence_ok, "database_append_only": append_only}


if __name__ == "__main__":
    raise SystemExit(main())
