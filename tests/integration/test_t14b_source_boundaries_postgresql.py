"""Real transactions reject malformed source authority at every assigned adapter.

This is rejection/rollback coverage, not proof that null provider inputs can be
acquired. Native acquisition positives remain in each subsystem's source tests.
"""

import importlib
import inspect

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy.orm import Session
from test_ceri_batched_workflow_v2 import _new_job
from test_t14b_writer_contract_adoption import HANDOFF, native_owner

from app.models.tables import BackgroundJob, RawCompanyRow, UploadRun
from app.services.background_job_service import JobStatus

contextual_engine = contextual.contextual_engine

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_every_assigned_source_adapter_rejects_malformed_authority_and_rolls_back(
    contextual_engine,
):
    checked = []
    with Session(contextual_engine) as db:
        db.add(UploadRun(id=7, filename="source-boundary.csv", status="COMPLETED"))
        db.flush()
        raw = RawCompanyRow(run_id=7, row_number=1, ticker="ACME", raw_json={"Symbol": "ACME"})
        db.add(raw)
        # The source-declaration attack must reach that guard through a genuine
        # production configuration anchor, rather than fail on an earlier
        # missing durable-authority prerequisite.
        job = _new_job(
            db,
            job_type="CERI_NORMALIZE",
            payload_json={},
            status=JobStatus.RUNNING,
            request_key="t14b-source-declaration",
        )
        db.commit()
        job_id = job.id
        raw_id = raw.id
        for family in HANDOFF["writer_families"]:
            owner = native_owner(family["canonical_owner"])
            wrapped = owner
            adapter = False
            while hasattr(wrapped, "__wrapped__"):
                closure = inspect.getclosurevars(wrapped).nonlocals
                adapter |= "role" in closure and "domain" in closure
                wrapped = wrapped.__wrapped__
            if not adapter:
                continue
            kwargs = {}
            parameters = inspect.signature(owner).parameters
            for name, parameter in parameters.items():
                if name == "db":
                    kwargs[name] = db
                elif name == "job":
                    kwargs[name] = db.get(BackgroundJob, job_id)
                elif name == "self":
                    path, symbol = family["canonical_owner"].split(":")
                    module = importlib.import_module(path[:-3].replace("/", "."))
                    kwargs[name] = object.__new__(getattr(module, symbol.split(".")[0]))
                elif parameter.default is inspect.Parameter.empty:
                    kwargs[name] = None
            if "db" not in parameters:
                # Row-only normalization mechanisms discover the real Session
                # from their row argument before executing their native body.
                argument = next(name for name in kwargs if name != "self")
                kwargs[argument] = db.get(RawCompanyRow, raw_id)
            db.get(RawCompanyRow, raw_id).ticker = "STAGED_UNAUTHORIZED"
            with pytest.raises(
                ValueError, match="MUTATION_SOURCE_(DECLARATION_MISMATCH|RECORD_ARGUMENT_MISMATCH)"
            ):
                owner(**kwargs, mutation_context={"untyped": "authority"})
            db.commit()
            assert db.get(RawCompanyRow, raw_id).ticker == "ACME", family["writer_family_id"]
            checked.append(family["writer_family_id"])
        assert len(checked) == 18
