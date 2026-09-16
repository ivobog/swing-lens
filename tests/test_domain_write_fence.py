from __future__ import annotations

from datetime import UTC, date, datetime
from threading import Thread
from types import SimpleNamespace

import pytest
from sqlalchemy import Integer, String, create_engine, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.models.tables import (
    BackgroundJob,
    EffectiveConfigurationRecord,
    ExecutionConfigurationAnchor,
    ExecutionConfigurationBinding,
)
from app.services.background_job_service import JobLeaseLost, JobStatus
from app.services.background_worker import execute_job, run_worker_once
from app.services.ib_market_intelligence import orchestration
from app.services.ib_market_intelligence.enums import IntelligenceModule


class _TestBase(DeclarativeBase):
    pass


@compiles(JSONB, "sqlite")
def _configuration_jsonb_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _DomainMutation(_TestBase):
    __tablename__ = "domain_mutations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="domain")


@pytest.fixture
def fenced_sessions(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'domain-fence.db'}")
    _TestBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE background_jobs (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                execution_token TEXT,
                requested_cancel INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            text(
                "INSERT INTO background_jobs "
                "(id, status, execution_token, requested_cancel) "
                "VALUES (1, 'RUNNING', 'token-a', 0)"
            )
        )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _job(*, token: str = "token-a", tickers: list[str] | None = None) -> BackgroundJob:
    return BackgroundJob(
        id=1,
        job_type="TEST_DOMAIN_JOB",
        status=JobStatus.RUNNING,
        execution_token=token,
        requested_cancel=False,
        payload_json={
            "module": IntelligenceModule.LIQUIDITY.value,
            "tickers": tickers or [],
        },
    )


def _stored_mutations(factory) -> list[tuple[str, str]]:
    with factory() as db:
        return list(db.execute(select(_DomainMutation.name, _DomainMutation.kind)).all())


def _replace_token(factory, token: str) -> None:
    with factory.begin() as db:
        db.execute(
            text("UPDATE background_jobs SET execution_token = :token WHERE id = 1"),
            {"token": token},
        )


def _replace_token_as_other_worker(factory, token: str) -> None:
    errors: list[BaseException] = []

    def replace() -> None:
        try:
            _replace_token(factory, token)
        except BaseException as exc:  # pragma: no cover - asserted in caller
            errors.append(exc)

    thread = Thread(target=replace)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []


def test_valid_execution_owner_commits_domain_mutation(fenced_sessions) -> None:
    with fenced_sessions() as db:

        def handler(session: Session, _job: BackgroundJob):
            session.add(_DomainMutation(name="owned", kind="domain"))
            session.commit()
            return {"ok": True}

        result = execute_job(
            db,
            _job(),
            {"TEST_DOMAIN_JOB": handler},
            execution_token="token-a",
        )

    assert result == {"ok": True}
    assert _stored_mutations(fenced_sessions) == [("owned", "domain")]


def test_ownership_lost_after_prepare_rejects_commit_and_stops(fenced_sessions) -> None:
    calls: list[str] = []
    with fenced_sessions() as db:

        def handler(session: Session, _job: BackgroundJob):
            session.add(_DomainMutation(name="stale", kind="domain"))
            calls.append("prepared")
            _replace_token_as_other_worker(fenced_sessions, "token-b")
            session.commit()
            calls.append("after-commit")

        with pytest.raises(JobLeaseLost):
            execute_job(
                db,
                _job(),
                {"TEST_DOMAIN_JOB": handler},
                execution_token="token-a",
            )
        db.rollback()

    assert calls == ["prepared"]
    assert _stored_mutations(fenced_sessions) == []


def test_child_session_commit_is_fenced_by_parent_job_ownership(fenced_sessions) -> None:
    _replace_token(fenced_sessions, "token-b")
    with fenced_sessions() as db:

        def handler(_session: Session, _job: BackgroundJob):
            with fenced_sessions() as child_db:
                child_db.add(_DomainMutation(name="child-session", kind="domain"))
                child_db.commit()

        with pytest.raises(JobLeaseLost):
            execute_job(
                db,
                _job(),
                {"TEST_DOMAIN_JOB": handler},
                execution_token="token-a",
            )
        db.rollback()

    assert _stored_mutations(fenced_sessions) == []


def test_ibmi_reclaim_between_tickers_fences_old_owner_and_new_owner_continues(
    fenced_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    reclaim_before_second_commit = True

    monkeypatch.setattr(
        orchestration,
        "load_ib_market_intelligence_config",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        orchestration,
        "standalone_market_context",
        lambda **_kwargs: SimpleNamespace(
            latest_completed_session=date(2026, 9, 11),
            cutoff_at=datetime(2026, 9, 11, 22, 0, tzinfo=UTC),
        ),
    )
    monkeypatch.setattr(
        orchestration,
        "_start_run",
        lambda *_args, **_kwargs: SimpleNamespace(id=101, checkpoint_json={}),
    )
    monkeypatch.setattr(orchestration, "_finish_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestration, "_job_guard", lambda *_args, **_kwargs: None)

    def rebuild(db: Session, ticker: str, *_args, **_kwargs):
        nonlocal reclaim_before_second_commit
        calls.append(ticker)
        db.add(_DomainMutation(name=ticker, kind="ibmi-feature"))
        if ticker == "BBB" and reclaim_before_second_commit:
            reclaim_before_second_commit = False
            _replace_token_as_other_worker(fenced_sessions, "token-b")
        return object(), True

    monkeypatch.setattr(orchestration, "_rebuild_ticker_feature", rebuild)

    old_job = _job(tickers=["AAA", "BBB", "CCC"])
    old_job.job_type = "IB_INTELLIGENCE_REBUILD_FEATURES"
    # The financial calculators/config loader below are mocked. Supply a real
    # durable binding for this isolated Phase-0 lease test instead of executing
    # a newly certified business job with missing configuration authority.
    from app.services.configuration_delivery import (
        ANCHOR_KEY,
        bind_job_configuration,
        persist_configuration_anchor,
    )
    from app.services.decision_effective_configuration import freeze_decision_configuration

    with fenced_sessions() as db:
        for model in (
            EffectiveConfigurationRecord,
            ExecutionConfigurationAnchor,
            ExecutionConfigurationBinding,
        ):
            model.__table__.create(db.get_bind())
        anchor = persist_configuration_anchor(
            db,
            [
                freeze_decision_configuration(
                    "test.domain_fence", {"policy_version": "mocked-financial-calculator-v1"}, ()
                )
            ],
        )
        old_job.payload_json = {**old_job.payload_json, ANCHOR_KEY: anchor}
        bind_job_configuration(db, old_job)
        db.commit()
    with fenced_sessions() as db:
        with pytest.raises(JobLeaseLost):
            execute_job(
                db,
                old_job,
                {old_job.job_type: orchestration.execute_feature_rebuild},
                execution_token="token-a",
            )
        db.rollback()

    assert calls == ["AAA", "BBB"]
    assert _stored_mutations(fenced_sessions) == [("AAA", "ibmi-feature")]

    new_job = _job(token="token-b", tickers=["BBB", "CCC"])
    new_job.job_type = old_job.job_type
    new_job.payload_json = {**new_job.payload_json, ANCHOR_KEY: anchor}
    with fenced_sessions() as db:
        execute_job(
            db,
            new_job,
            {new_job.job_type: orchestration.execute_feature_rebuild},
            execution_token="token-b",
        )

    assert calls == ["AAA", "BBB", "BBB", "CCC"]
    assert _stored_mutations(fenced_sessions) == [
        ("AAA", "ibmi-feature"),
        ("BBB", "ibmi-feature"),
        ("CCC", "ibmi-feature"),
    ]


def test_stale_owner_cannot_commit_continuation(fenced_sessions) -> None:
    _replace_token(fenced_sessions, "token-b")
    with fenced_sessions() as db:

        def handler(session: Session, _job: BackgroundJob):
            session.add(_DomainMutation(name="child-job", kind="continuation"))
            session.commit()

        with pytest.raises(JobLeaseLost):
            execute_job(
                db,
                _job(),
                {"TEST_DOMAIN_JOB": handler},
                execution_token="token-a",
            )
        db.rollback()

    assert _stored_mutations(fenced_sessions) == []


def test_worker_does_not_finalize_success_after_fenced_handler_commit(
    fenced_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job()
    completed: list[int] = []

    monkeypatch.setattr("app.services.background_worker.register_worker", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_worker_control_loop", lambda *_a, **_k: None
    )
    monkeypatch.setattr("app.services.background_worker.heartbeat_worker", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "app.services.background_worker.recover_abandoned_jobs_for_worker", lambda *_a, **_k: 0
    )
    monkeypatch.setattr("app.services.background_worker.recover_stale_jobs", lambda *_a, **_k: 0)
    monkeypatch.setattr("app.services.background_worker.claim_next_job", lambda *_a, **_k: job)
    monkeypatch.setattr("app.services.background_worker.heartbeat_job", lambda *_a, **_k: job)
    monkeypatch.setattr(
        "app.services.background_worker.mark_job_completed",
        lambda _db, completed_job, *_a, **_k: completed.append(completed_job.id),
    )

    def handler(db: Session, _job: BackgroundJob):
        db.add(_DomainMutation(name="worker-stale", kind="domain"))
        _replace_token_as_other_worker(fenced_sessions, "token-b")
        db.commit()

    assert run_worker_once(
        worker_id="worker-a",
        worker_instance_id="instance-a",
        stale_after_seconds=60,
        session_factory=fenced_sessions,
        handlers={"TEST_DOMAIN_JOB": handler},
    )
    assert completed == []
    assert _stored_mutations(fenced_sessions) == []


def test_idempotency_does_not_authorize_stale_commit(fenced_sessions) -> None:
    with fenced_sessions.begin() as db:
        db.add(_DomainMutation(name="same-content", kind="domain"))
    _replace_token(fenced_sessions, "token-b")

    with fenced_sessions() as db:

        def handler(session: Session, _job: BackgroundJob):
            session.execute(
                text(
                    "INSERT OR IGNORE INTO domain_mutations (name, kind) "
                    "VALUES ('same-content', 'domain')"
                )
            )
            session.commit()

        with pytest.raises(JobLeaseLost):
            execute_job(
                db,
                _job(),
                {"TEST_DOMAIN_JOB": handler},
                execution_token="token-a",
            )
        db.rollback()

    assert _stored_mutations(fenced_sessions) == [("same-content", "domain")]


def test_not_cancelled_does_not_override_stale_execution_token(fenced_sessions) -> None:
    _replace_token(fenced_sessions, "token-b")
    with fenced_sessions() as db:

        def handler(session: Session, _job: BackgroundJob):
            cancelled = bool(
                session.scalar(select(BackgroundJob.requested_cancel).where(BackgroundJob.id == 1))
            )
            assert cancelled is False
            session.add(_DomainMutation(name="not-cancelled", kind="domain"))
            session.commit()

        with pytest.raises(JobLeaseLost):
            execute_job(
                db,
                _job(),
                {"TEST_DOMAIN_JOB": handler},
                execution_token="token-a",
            )
        db.rollback()

    assert _stored_mutations(fenced_sessions) == []
