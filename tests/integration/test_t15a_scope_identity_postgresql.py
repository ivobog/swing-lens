from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    AcquisitionPlanRecord,
    RefreshCycleRecord,
    WorkScopeMember,
    WorkScopeRecord,
)
from app.services.work_scope_identity import (
    AcquisitionPlanRevisionReason,
    AcquisitionPlanSnapshot,
    AcquisitionRequirement,
    RefreshCycleIdentity,
    ScopeIdentityStore,
    ScopeMember,
    ScopeMembershipPolicy,
    WorkScopeSnapshot,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _plan(*, revision_key: str = "initial") -> AcquisitionPlanSnapshot:
    return AcquisitionPlanSnapshot(
        plan_kind="market-data",
        plan_version="v1",
        revision_key=revision_key,
        subjects=(ScopeMember("ticker", "MSFT"), ScopeMember("ticker", "AAPL")),
        provider_source_class="IB-HISTORICAL",
        request_type="DAILY-BARS",
        business_cutoff=date(2026, 9, 18),
        window_start=date(2025, 9, 18),
        window_end=date(2026, 9, 18),
        configuration_identity="c" * 64,
        policy_identity="ib-fetch-v1",
        requirements=(AcquisitionRequirement("TRADES"),),
    )


def _scope(plan_id: str, size: int = 3) -> WorkScopeSnapshot:
    return WorkScopeSnapshot(
        scope_kind="market-data-prewarm",
        subject_kind="ticker",
        definition_version="v1",
        scope_definition={"universe": "explicit"},
        selection_policy_identity="explicit-symbols-v1",
        membership_policy=ScopeMembershipPolicy.FROZEN,
        members=tuple(ScopeMember("ticker", f"T{index:04d}") for index in range(size)),
        selected_at=NOW,
        business_cutoff=date(2026, 9, 18),
        configuration_identity="c" * 64,
        acquisition_plan_id=plan_id,
    )


@pytest.fixture
def scope_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database.replace("%", "%%"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0083_winner_scope_truth"]
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def test_persistence_is_idempotent_immutable_and_linked(scope_engine) -> None:
    first_plan = _plan()
    with Session(scope_engine) as db:
        first_plan_id = ScopeIdentityStore.persist_acquisition_plan(db, first_plan)
        scope = _scope(first_plan_id)
        scope_id = ScopeIdentityStore.persist_scope(db, scope)
        first_refresh = RefreshCycleIdentity(
            refresh_kind="MARKET_DATA_REFRESH",
            scope_id=scope_id,
            observation_cycle_key="session-2026-09-18:first",
            business_observation_cutoff=NOW,
            refresh_reason="SCHEDULED",
            effective_configuration_identity="c" * 64,
        )
        first_refresh_id = ScopeIdentityStore.persist_refresh_cycle(db, first_refresh)
        db.commit()

    with Session(scope_engine) as db:
        assert ScopeIdentityStore.persist_acquisition_plan(db, first_plan) == first_plan_id
        assert ScopeIdentityStore.persist_scope(db, scope) == scope_id
        assert ScopeIdentityStore.persist_refresh_cycle(db, first_refresh) == first_refresh_id
        second_plan = first_plan.revised(
            revision_key="fallback-1",
            reason=AcquisitionPlanRevisionReason.PROVIDER_LIMITATION,
            provider_source_class="SECONDARY-HISTORICAL",
        )
        second_plan_id = ScopeIdentityStore.persist_acquisition_plan(db, second_plan)
        second_refresh = first_refresh.next_refresh(
            observation_cycle_key="session-2026-09-18:second",
            business_observation_cutoff=NOW,
            refresh_reason="OPERATOR_REFRESH",
        )
        second_refresh_id = ScopeIdentityStore.persist_refresh_cycle(db, second_refresh)
        db.commit()

        assert second_plan_id != first_plan_id
        assert db.get(AcquisitionPlanRecord, second_plan_id).previous_plan_id == first_plan_id
        assert second_refresh_id != first_refresh_id
        assert db.get(RefreshCycleRecord, second_refresh_id).prior_refresh_id == first_refresh_id
        assert len(db.scalars(select(WorkScopeMember)).all()) == 3

    table_keys = (
        ("acquisition_plan_records", "plan_id", first_plan_id),
        ("work_scope_records", "scope_id", scope_id),
        ("work_scope_members", "scope_id", scope_id),
        ("refresh_cycle_records", "refresh_cycle_id", first_refresh_id),
    )
    for table, key, value in table_keys:
        with scope_engine.connect() as connection:
            transaction = connection.begin()
            with pytest.raises(Exception, match="immutable"):
                connection.execute(
                    text(f"UPDATE {table} SET {key} = {key} WHERE {key} = :value"),
                    {"value": value},
                )
            transaction.rollback()


@pytest.mark.parametrize("size", (1, 50, 200))
def test_scope_persistence_uses_constant_query_count(scope_engine, size: int) -> None:
    plan = _plan(revision_key=f"size-{size}")
    counts = {"statements": 0}

    def count_statement(*_args):
        counts["statements"] += 1

    event.listen(scope_engine, "before_cursor_execute", count_statement)
    try:
        with Session(scope_engine) as db:
            plan_id = ScopeIdentityStore.persist_acquisition_plan(db, plan)
            before = counts["statements"]
            ScopeIdentityStore.persist_scope(db, _scope(plan_id, size=size))
            db.commit()
            scope_statements = counts["statements"] - before
    finally:
        event.remove(scope_engine, "before_cursor_execute", count_statement)

    assert scope_statements <= 6


def test_database_foreign_keys_reject_unpersisted_scope_and_plan(scope_engine) -> None:
    missing_scope = "f" * 64
    with Session(scope_engine) as db:
        db.add(
            RefreshCycleRecord(
                refresh_cycle_id="e" * 64,
                refresh_kind="INVALID",
                scope_id=missing_scope,
                prior_refresh_id=None,
                observation_cycle_key="invalid",
                payload_json={},
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(
            WorkScopeRecord(
                scope_id="d" * 64,
                scope_kind="INVALID",
                subject_kind="INVALID",
                membership_policy="FROZEN",
                membership_fingerprint="c" * 64,
                acquisition_plan_id="b" * 64,
                payload_json={},
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
