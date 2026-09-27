from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriChangeEvent, CeriScoreSnapshot, CeriSourceRecord
from app.services.ceri.capture_service import _scalars as capture_scalars
from app.services.ceri.feature_rebuild_service import _scalars as feature_scalars
from app.services.ceri.job_handlers import _eligible_changes, _load_rows
from app.services.ceri.price_response_service import _scalars as price_response_scalars
from app.services.ceri.query_service import _load as query_load


@pytest.mark.parametrize(
    ("loader", "statement"),
    (
        (feature_scalars, select(CeriSourceRecord)),
        (capture_scalars, select(CeriScoreSnapshot)),
        (price_response_scalars, select(CeriSourceRecord)),
    ),
)
def test_large_ceri_statement_helpers_reject_predicate_free_production_reads(
    loader, statement
):
    with Session() as db, pytest.raises(ValueError, match="UNSCOPED_READ_FORBIDDEN"):
        loader(db, statement)


def test_dynamic_model_helpers_reject_production_sessions():
    with Session() as db:
        with pytest.raises(TypeError, match="fixture-only"):
            query_load(db, CeriScoreSnapshot)
        with pytest.raises(TypeError, match="fixture-only"):
            _load_rows(db, CeriChangeEvent)


def test_alert_change_loader_requires_authoritative_production_scope():
    with Session() as db, pytest.raises(ValueError, match="CERI_ALERT_REBUILD_SCOPE_REQUIRED"):
        _eligible_changes(db, {})


def test_large_ceri_statement_helpers_accept_explicit_scope():
    class FixtureSession:
        def scalars(self, statement):
            assert statement._where_criteria
            return []

    statement = select(CeriSourceRecord).where(CeriSourceRecord.id == 7)
    assert feature_scalars(FixtureSession(), statement) == []
    assert capture_scalars(FixtureSession(), statement) == []
    assert price_response_scalars(FixtureSession(), statement) == []
