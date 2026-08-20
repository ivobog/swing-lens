from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.models.ceri_tables import CeriCompany, CeriIngestionRun, CeriSecSyncState
from app.models.tables import BackgroundJob
from app.services.background_job_service import JobStatus, enqueue_job
from app.services.background_worker import CancelRequested, JobDeferred
from app.services.ceri import batched_job_handlers
from app.services.ceri.batched_job_handlers import (
    _require_terminal_stage,
    execute_feature_batch_job,
    execute_provider_ingest_batch_job,
    execute_run_finalize_job,
)
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
    _ensure_ceri_companies,
    build_ceri_batched_workflow_plan,
    sec_readiness_coverage,
)
from app.services.ceri.enums import CeriDataset
from app.services.ceri.feature_rebuild_service import CeriFeatureRebuildResult
from app.services.ceri.orchestration import CeriIngestionResult, CeriIngestionService
from app.services.pipeline_prerequisites import CeriUpstreamStageBlockedError
from app.settings import SecDocumentIncrementalMode, Settings


def test_402_ticker_plan_is_deterministic_bounded_and_under_sanity_target() -> None:
    tickers = [f"T{index:03d}" for index in range(402)]
    settings = Settings(
        _env_file=None,
        ceri_provider_batch_size=25,
        ceri_normalization_batch_size=50,
        ceri_feature_batch_size=50,
    )

    plan = build_ceri_batched_workflow_plan(
        run_id=95,
        tickers=reversed(tickers),
        config_hash="config-a",
        settings=settings,
    )
    repeated = build_ceri_batched_workflow_plan(
        run_id=95,
        tickers=tickers,
        config_hash="config-a",
        settings=settings,
    )

    assert plan == repeated
    assert plan.provider_batches == 68
    assert plan.normalization_batches == 36
    assert plan.feature_batches == 9
    assert plan.initial_job_count == 114
    assert plan.expected_total_job_count == 117
    assert plan.expected_total_job_count < 150
    assert max(
        len(spec.payload["tickers"])
        for spec in plan.jobs
        if spec.job_type == CERI_PROVIDER_INGEST_BATCH
    ) == 25
    assert max(
        len(spec.payload["tickers"])
        for spec in plan.jobs
        if spec.job_type == CERI_NORMALIZE_BATCH
    ) == 50
    assert max(
        len(spec.payload["tickers"])
        for spec in plan.jobs
        if spec.job_type == CERI_FEATURE_BATCH
    ) == 50
    assert sum(spec.job_type == CERI_RUN_FINALIZE for spec in plan.jobs) == 1


def test_provider_ticker_scope_excludes_sec_not_applicable_symbols() -> None:
    plan = build_ceri_batched_workflow_plan(
        run_id=95,
        tickers=["AAA", "FOREIGN"],
        config_hash="config-a",
        settings=Settings(
            _env_file=None,
            ceri_provider_batch_size=25,
            ceri_normalization_batch_size=50,
            ceri_feature_batch_size=50,
        ),
        provider_datasets={"sec": (CeriDataset.GUIDANCE,)},
        provider_tickers={"sec": ("AAA",)},
    )

    sec_jobs = [
        job
        for job in plan.jobs
        if job.job_type in {CERI_PROVIDER_INGEST_BATCH, CERI_NORMALIZE_BATCH}
    ]
    assert sec_jobs
    assert all(job.payload["tickers"] == ["AAA"] for job in sec_jobs)
    feature_job = next(job for job in plan.jobs if job.job_type == CERI_FEATURE_BATCH)
    assert feature_job.payload["tickers"] == ["AAA", "FOREIGN"]


def test_company_ensure_preserves_existing_non_us_not_applicable_mapping() -> None:
    existing = CeriCompany(
        ticker="FOREIGN",
        exchange="NYSE",
        sec_applicability="NOT_APPLICABLE",
        sec_applicability_reason="reviewed non-SEC security",
    )

    class Db:
        def __init__(self):
            self.added = []

        def scalars(self, _statement):
            return [existing]

        def add(self, value):
            self.added.append(value)

        def flush(self):
            pass

    db = Db()
    _ensure_ceri_companies(db, ["FOREIGN"])

    assert db.added == []


def test_workflow_stage_identity_coalesces_completed_jobs() -> None:
    existing = BackgroundJob(
        id=7,
        job_type=CERI_RUN_FINALIZE,
        workflow_key="workflow-a",
        request_key="workflow-a:finalize",
        status=JobStatus.COMPLETED,
        payload_json={},
    )
    db = FakeDb(jobs=[existing])

    returned = enqueue_job(
        db,
        CERI_RUN_FINALIZE,
        {},
        workflow_key="workflow-a",
        request_key="workflow-a:finalize",
    )

    assert returned is existing
    assert returned._coalesced is True
    assert len(db.jobs) == 1


def test_provider_batch_resumes_after_checkpoint_and_bounds_reprocessing(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(provider_ingest=True),
    )
    calls = []

    class Service:
        def ingest(self, _db, request, **_kwargs):
            calls.append(request.ticker)
            return SimpleNamespace(
                as_dict=lambda: {
                    "ingestion_run_id": len(calls),
                    "provider": request.provider,
                    "dataset": request.dataset.value,
                    "status": "COMPLETED",
                    "failed": 0,
                }
            )

    job = _provider_batch_job([f"T{index}" for index in range(12)])
    job.operational_metadata_json = {
        "ceri_batch": {
            "completed_tickers": [f"T{index}" for index in range(5)],
            "results": {f"T{index}": {"status": "COMPLETED"} for index in range(5)},
        }
    }
    heartbeats = []
    job._heartbeat = lambda: heartbeats.append("heartbeat")

    result = execute_provider_ingest_batch_job(
        FakeDb(),
        job,
        ingestion_service=Service(),
    )

    assert calls == sorted(f"T{index}" for index in range(5, 12))
    assert result["processed_tickers"] == 12
    assert result["failed"] == 0
    assert len(heartbeats) == 1


def test_provider_batch_continues_after_partial_ticker_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(provider_ingest=True),
    )

    class Service:
        def ingest(self, _db, request, **_kwargs):
            if request.ticker == "T1":
                raise TimeoutError("provider timeout")
            return SimpleNamespace(
                as_dict=lambda: {
                    "status": "COMPLETED",
                    "failed": 0,
                }
            )

    job = _provider_batch_job(["T0", "T1", "T2"])

    result = execute_provider_ingest_batch_job(
        FakeDb(),
        job,
        ingestion_service=Service(),
    )

    assert result["processed_tickers"] == 3
    assert result["failed"] == 1
    assert result["results"]["T1"]["status"] == "PARTIAL"
    assert job.status == JobStatus.PARTIAL


def test_active_batched_sec_provider_routes_through_incremental_service(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(provider_ingest=True),
    )

    class SourceRecords:
        def create_ingestion_run(self, *_args, **kwargs):
            return CeriIngestionRun(
                id=7,
                provider="sec",
                dataset="guidance",
                request_key=kwargs["request_key"],
                status="RUNNING",
                requested_count=0,
                fetched_count=0,
                inserted_count=0,
                deduplicated_count=0,
                corrected_count=0,
                quarantined_count=0,
                failed_count=0,
                warning_count=0,
                retry_count=0,
            )

    class Registry:
        def get(self, _provider):
            return SimpleNamespace(provider_terms_version="test")

        def license_policy(self, *_args):
            return SimpleNamespace(
                terms_version="test",
                raw_payload_storage_allowed=False,
            )

    service = CeriIngestionService(
        registry=Registry(),
        source_records=SourceRecords(),
        settings=Settings(
            _env_file=None,
            sec_document_incremental_mode=SecDocumentIncrementalMode.ACTIVE,
        ),
    )
    calls = []

    def incremental(*_args, **_kwargs):
        calls.append("incremental")
        return CeriIngestionResult(
            ingestion_run_id=7,
            provider="sec",
            dataset="guidance",
            status="COMPLETED",
            requested=0,
            fetched=0,
            inserted=0,
            deduplicated=0,
            corrected=0,
            quarantined=0,
            failed=0,
            warnings=0,
        )

    monkeypatch.setattr(service, "_ingest_sec_incremental", incremental)
    job = _provider_batch_job(["AIZ"])
    job.payload_json.update(provider="sec", dataset="guidance")

    result = execute_provider_ingest_batch_job(FakeDb(), job, ingestion_service=service)

    assert calls == ["incremental"]
    assert result["status"] == "COMPLETED"


def test_sec_readiness_coverage_requires_current_signature_per_ticker() -> None:
    db = CoverageDb(
        [
            [
                CeriCompany(id=1, ticker="AIZ", cik="0001041668"),
                CeriCompany(id=2, ticker="AMZN", cik="0001018724"),
                CeriCompany(id=3, ticker="MISS", cik=None),
            ],
            [
                CeriSecSyncState(
                    cik="0001041668",
                    dataset="guidance",
                    processor_signature="sec-guidance:eed017654682a0c9",
                )
            ],
        ]
    )

    coverage = sec_readiness_coverage(db, tickers=["AIZ", "AMZN", "MISS"])

    assert coverage.ready_tickers == 1
    assert coverage.missing_tickers == ("AMZN", "MISS")
    assert coverage.missing_ciks == ("0001018724",)

def test_feature_batch_prepares_once_and_preserves_resume_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(batched_job_handlers, "_require_terminal_stage", lambda *_a, **_k: None)
    monkeypatch.setattr(batched_job_handlers, "_heartbeat_and_cancel", lambda *_a, **_k: False)
    processing_runs = {}

    def create_or_get(_self, _db, *, request_key, **_kwargs):
        run = SimpleNamespace(
            id=len(processing_runs) + 1,
            status="RUNNING",
            feature_count=0,
            checkpoint_json=None,
        )
        processing_runs[request_key] = run
        return run, True

    def finish(_self, _db, run, *, status, counts, **_kwargs):
        run.status = status
        run.feature_count = counts["features"]
        return run

    monkeypatch.setattr(
        batched_job_handlers.CeriProcessingRunService, "create_or_get", create_or_get
    )
    monkeypatch.setattr(batched_job_handlers.CeriProcessingRunService, "finish", finish)

    class Service:
        def __init__(self):
            self.prepared = []
            self.rebuilt = []

        def prepare_batch(self, _db, request):
            self.prepared.append(request.tickers)
            return SimpleNamespace(
                load_context_ms=3,
                select_count=9,
                rows_loaded={"ceri_estimate_snapshots": 6},
            )

        def rebuild(self, _db, request, **_kwargs):
            self.rebuilt.append(request.ticker)
            return CeriFeatureRebuildResult(
                features=2,
                processed_companies=1,
                companies_rebuilt=1,
                features_inserted=2,
                family_runtime_ms={"revisions": 1},
            )

    service = Service()
    job = BackgroundJob(
        id=77,
        job_type=CERI_FEATURE_BATCH,
        workflow_key="ceri:pipeline:95:config-a",
        request_key="ceri:pipeline:95:config-a:feature:1",
        related_run_id=95,
        status=JobStatus.RUNNING,
        payload_json={
            "workflow_key": "ceri:pipeline:95:config-a",
            "run_id": 95,
            "tickers": ["T0", "T1", "T2"],
            "expected_normalization_batches": 1,
        },
        operational_metadata_json={
            "ceri_batch": {
                "completed_tickers": ["T0"],
                "results": {"T0": {"status": "COMPLETED", "features": 2}},
            }
        },
    )

    result = execute_feature_batch_job(FakeDb(), job, feature_service=service)

    assert service.prepared == [("T1", "T2")]
    assert service.rebuilt == ["T1", "T2"]
    assert result["processed_tickers"] == 3
    assert result["telemetry"]["sql_select_count"] == 9
    assert job.operational_metadata_json["ceri_batch"]["completed_tickers"] == [
        "T0",
        "T1",
        "T2",
    ]


def test_feature_batch_cancellation_never_checkpoints_unfinished_ticker(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setattr(batched_job_handlers, "_require_terminal_stage", lambda *_a, **_k: None)
    checks = iter((False, False, True))
    monkeypatch.setattr(
        batched_job_handlers,
        "_heartbeat_and_cancel",
        lambda *_a, **_k: next(checks),
    )
    processing = SimpleNamespace(
        id=1,
        status="RUNNING",
        feature_count=0,
        checkpoint_json=None,
    )
    monkeypatch.setattr(
        batched_job_handlers.CeriProcessingRunService,
        "create_or_get",
        lambda *_a, **_k: (processing, True),
    )

    def finish(_self, _db, run, *, status, counts, **_kwargs):
        run.status = status
        run.feature_count = counts["features"]
        return run

    monkeypatch.setattr(batched_job_handlers.CeriProcessingRunService, "finish", finish)

    class Service:
        def prepare_batch(self, *_args):
            return SimpleNamespace(load_context_ms=0, select_count=1, rows_loaded={})

        def rebuild(self, *_args, **_kwargs):
            return CeriFeatureRebuildResult(
                features=1,
                processed_companies=1,
                companies_rebuilt=1,
            )

    job = BackgroundJob(
        id=78,
        job_type=CERI_FEATURE_BATCH,
        workflow_key="workflow-cancel",
        request_key="workflow-cancel:feature:1",
        related_run_id=95,
        status=JobStatus.RUNNING,
        payload_json={
            "workflow_key": "workflow-cancel",
            "run_id": 95,
            "tickers": ["T1", "T2"],
            "expected_normalization_batches": 1,
        },
        operational_metadata_json={},
    )

    with pytest.raises(CancelRequested):
        execute_feature_batch_job(FakeDb(), job, feature_service=Service())

    assert job.operational_metadata_json["ceri_batch"]["completed_tickers"] == ["T1"]
    assert "T2" not in job.operational_metadata_json["ceri_batch"]["results"]


def test_barrier_blocks_after_unsuccessful_terminal_upstream_batch() -> None:
    upstream = BackgroundJob(
        id=9,
        job_type=CERI_FEATURE_BATCH,
        workflow_key="workflow-blocked",
        request_key="workflow-blocked:feature:1",
        status=JobStatus.BLOCKED,
        payload_json={},
    )

    with pytest.raises(CeriUpstreamStageBlockedError) as raised:
        _require_terminal_stage(
            FakeDb(stage_jobs=[upstream]),
            "workflow-blocked",
            CERI_FEATURE_BATCH,
            expected=1,
        )

    assert raised.value.reason_code == "CERI_UPSTREAM_STAGE_UNSUCCESSFUL"
    assert raised.value.diagnostics["jobs"] == [{"job_id": 9, "status": "BLOCKED"}]


def test_finalizer_enqueues_exactly_one_capture_for_repeated_execution(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(enabled=True),
    )
    workflow_key = "ceri:pipeline:95:config-a"
    features = [
        BackgroundJob(
            id=index,
            job_type=CERI_FEATURE_BATCH,
            workflow_key=workflow_key,
            request_key=f"{workflow_key}:feature:{index}",
            status=JobStatus.COMPLETED,
            payload_json={},
        )
        for index in (1, 2)
    ]
    db = FakeDb(stage_jobs=features)
    finalizer = BackgroundJob(
        id=10,
        job_type=CERI_RUN_FINALIZE,
        workflow_key=workflow_key,
        request_key=f"{workflow_key}:finalize",
        status=JobStatus.RUNNING,
        priority=140,
        max_retries=3,
        related_run_id=95,
        payload_json={
            "workflow_key": workflow_key,
            "run_id": 95,
            "expected_feature_batches": 2,
        },
    )

    first = execute_run_finalize_job(db, finalizer)
    second = execute_run_finalize_job(db, finalizer)

    captures = [job for job in db.jobs if job.job_type == "CERI_CAPTURE_RUN"]
    assert len(captures) == 1
    assert first["capture_coalesced"] is False
    assert second["capture_coalesced"] is True
    assert captures[0].workflow_key == workflow_key


def test_finalizer_defers_until_feature_batches_are_terminal(monkeypatch) -> None:
    monkeypatch.setattr(
        batched_job_handlers,
        "ceri_flags",
        lambda: SimpleNamespace(enabled=True),
    )
    workflow_key = "ceri:pipeline:95:config-a"
    feature = BackgroundJob(
        id=1,
        job_type=CERI_FEATURE_BATCH,
        workflow_key=workflow_key,
        request_key=f"{workflow_key}:feature:1",
        status=JobStatus.QUEUED,
        payload_json={},
    )
    finalizer = BackgroundJob(
        id=2,
        job_type=CERI_RUN_FINALIZE,
        workflow_key=workflow_key,
        request_key=f"{workflow_key}:finalize",
        status=JobStatus.RUNNING,
        related_run_id=95,
        payload_json={
            "workflow_key": workflow_key,
            "run_id": 95,
            "expected_feature_batches": 1,
        },
    )

    with pytest.raises(JobDeferred, match="waiting for terminal"):
        execute_run_finalize_job(FakeDb(stage_jobs=[feature]), finalizer)


def test_settings_reject_simultaneous_legacy_and_v2_scheduling() -> None:
    with pytest.raises(ValueError, match="cannot both be enabled"):
        Settings(
            _env_file=None,
            ceri_legacy_pipeline_scheduling_enabled=True,
            ceri_batched_workflow_enabled=True,
        )


def test_background_job_schema_has_minimal_workflow_identity() -> None:
    assert "workflow_key" in BackgroundJob.__table__.c
    index_names = {index.name for index in BackgroundJob.__table__.indexes}
    assert "idx_background_jobs_workflow_type_status" in index_names
    assert "uq_background_jobs_workflow_stage" in index_names


def _provider_batch_job(tickers: list[str]) -> BackgroundJob:
    workflow_key = "ceri:pipeline:95:config-a"
    return BackgroundJob(
        id=1,
        job_type=CERI_PROVIDER_INGEST_BATCH,
        workflow_key=workflow_key,
        request_key=f"{workflow_key}:provider:eodhd:estimates:1",
        related_run_id=95,
        status=JobStatus.RUNNING,
        priority=80,
        payload_json={
            "workflow_key": workflow_key,
            "provider": "eodhd",
            "dataset": "estimates",
            "tickers": tickers,
            "checkpoint_interval": 5,
        },
        operational_metadata_json={},
    )


class FakeScalarRows:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class FakeDb:
    def __init__(self, *, jobs=None, stage_jobs=None) -> None:
        self.jobs = list(jobs or [])
        self.stage_jobs = list(stage_jobs or [])
        self.scalar_calls = 0
        self.flushes = 0

    def add(self, row) -> None:
        self.jobs.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def begin_nested(self):
        return nullcontext()

    def scalar(self, _statement):
        return False

    def scalars(self, _statement):
        self.scalar_calls += 1
        parameters = _statement.compile().params
        if self.stage_jobs and CERI_FEATURE_BATCH in parameters.values():
            return FakeScalarRows(self.stage_jobs)
        return FakeScalarRows([])


class CoverageDb:
    def __init__(self, rows) -> None:
        self.rows = list(rows)

    def scalars(self, _statement):
        return FakeScalarRows(self.rows.pop(0))
