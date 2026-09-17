from dataclasses import replace
from types import SimpleNamespace

import psycopg
import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_domain_mutation import mutation_context

from alembic import command
from app.models.tables import BackgroundJob, CoreCalculationEvidence, UploadRun
from app.services.background_job_service import JobLeaseLost
from app.services.calculation_identity import IdentityDimension
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    get_evidence_by_id,
    persist_core_evidence,
)
from app.services.core_effective_configuration import resolve_fundamental_configuration
from app.services.domain_mutation import (
    MutationDomain,
    fence_mutation_transaction,
    validate_mutation_context,
)
from app.services.domain_write_fence import DomainWriteOwnership
from app.services.effective_configuration import configuration_from_evidence
from app.services.producer_readiness import readiness_from_evidence

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.fixture
def mutation_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        db.add(UploadRun(id=101, filename="t14a-disposable.csv", status="COMPLETED"))
        db.add(
            BackgroundJob(
                id=1,
                job_type="T14A_TEST",
                status="RUNNING",
                payload_json={},
                execution_token="attempt-one",
            )
        )
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def test_fence_holds_the_same_transaction_row_lock(mutation_engine):
    context = replace(
        mutation_context(), durable=True, execution=DomainWriteOwnership(1, "attempt-one")
    )
    with Session(mutation_engine) as writer, Session(mutation_engine) as contender:
        fence_mutation_transaction(writer, context)
        assert writer.in_transaction()
        with pytest.raises(OperationalError) as error:
            contender.execute(
                select(BackgroundJob.id).where(BackgroundJob.id == 1).with_for_update(nowait=True)
            )
        assert isinstance(error.value.orig, psycopg.errors.LockNotAvailable)
        contender.rollback()
        writer.rollback()
        assert (
            contender.scalar(
                select(BackgroundJob.id).where(BackgroundJob.id == 1).with_for_update(nowait=True)
            )
            == 1
        )


@pytest.mark.parametrize("token", ["stale-attempt", "other-job-attempt"])
def test_stale_execution_cannot_authorize_domain_write(mutation_engine, token):
    context = replace(mutation_context(), durable=True, execution=DomainWriteOwnership(1, token))
    with Session(mutation_engine) as db:
        with pytest.raises(JobLeaseLost):
            fence_mutation_transaction(db, context)
        db.rollback()
        assert db.scalar(select(CoreCalculationEvidence.id)) is None


def test_configuration_readiness_and_immutable_identity_compose(mutation_engine):
    config = resolve_fundamental_configuration().snapshot
    context = mutation_context(MutationDomain.FUNDAMENTAL)
    identity = replace(
        context.calculation_identity,
        configuration=replace(
            context.calculation_identity.configuration,
            effective_configuration=IdentityDimension.known(config.identity),
        ),
    )
    context = replace(
        context,
        calculation_identity=identity,
        configuration=config.identity,
        durable=True,
        execution=DomainWriteOwnership(1, "attempt-one"),
    )
    validate_mutation_context(context).require_valid()
    with Session(mutation_engine) as db:
        fence_mutation_transaction(db, context)
        current = SimpleNamespace(run_id=101, ticker="AAPL", evidence_id=None)
        first = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            current_row=current,
            calculation_identity=identity,
            effective_configuration=config,
            payload={"fundamental_score": 7.0, "is_complete": True},
        )
        second = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.FUNDAMENTAL,
            current_row=current,
            calculation_identity=identity,
            effective_configuration=config,
            payload={"fundamental_score": 8.0, "is_complete": True},
        )
        first_id, second_id = first.id, second.id
        assert first_id != second_id
        assert first.payload_json["fundamental_score"] == 7.0
        db.commit()
    with Session(mutation_engine) as db:
        retained = get_evidence_by_id(db, evidence_id=first_id, kind=CoreEvidenceKind.FUNDAMENTAL)
        assert configuration_from_evidence(retained).value == context.configuration
        assert readiness_from_evidence(retained).calculation_identity_fingerprint == str(
            identity.fingerprint()
        )
        assert retained.payload_json["fundamental_score"] == 7.0
        assert (
            get_evidence_by_id(db, evidence_id=second_id).payload_json["fundamental_score"] == 8.0
        )
