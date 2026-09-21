from __future__ import annotations

from datetime import date

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import CeriCompany
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.scope_refresh_adoption import admit_frozen_operation
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_t15d_ceri_population_rebuild_uses_retained_postgresql_scope(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    assert ScriptDirectory.from_config(config).get_heads() == ["0083_winner_scope_truth"]
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)

    try:
        with Session(engine) as db:
            db.add_all(
                [
                    CeriCompany(ticker="A", exchange="NASDAQ"),
                    CeriCompany(ticker="B", exchange="NASDAQ"),
                ]
            )
            db.flush()
            authority = admit_frozen_operation(
                db,
                operation_kind="ceri-feature-rebuild",
                subject_kind="ticker",
                members=(ScopeMember("TICKER", "A"), ScopeMember("TICKER", "B")),
                cycle_key="t15d:R1",
                business_cutoff=date(2026, 9, 18),
                provider_source_class="CERI",
                request_type="FEATURE_REBUILD",
                requirements=(AcquisitionRequirement("CERI_SOURCE_RECORDS"),),
                policy_identity="t15d-frozen-scope-v1",
                scope_definition={"selection": "explicit", "tickers": ["A", "B"]},
            )

            # This company becomes eligible after admission and must not leak into R1.
            db.add(CeriCompany(ticker="C", exchange="NASDAQ"))
            db.flush()

            service = CeriFeatureRebuildService()
            retained = service._companies(
                db,
                CeriFeatureRebuildRequest(semantic_authority=authority),
            )
            narrowed = service._companies(
                db,
                CeriFeatureRebuildRequest(
                    tickers=("B", "C"),
                    semantic_authority=authority,
                ),
            )

            assert [company.ticker for company in retained] == ["A", "B"]
            assert [company.ticker for company in narrowed] == ["B"]
            db.rollback()
    finally:
        engine.dispose()
