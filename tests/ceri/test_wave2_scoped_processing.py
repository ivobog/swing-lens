from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriGuidanceEvent,
    CeriProcessingRun,
    CeriScoreSnapshot,
)
from app.models.tables import BackgroundJob
from app.services.ceri import backfill_service as backfill_module
from app.services.ceri.backfill_service import CeriBackfillRequest, CeriBackfillService
from app.services.ceri.capture_service import _catalyst_features_for_company
from app.services.ceri.change_detection_service import ChangeDetectionResult
from app.services.ceri.change_rebuild_service import (
    CeriChangeRebuildRequest,
    CeriChangeRebuildService,
)
from app.services.ceri.job_handlers import execute_alert_rebuild_job, execute_backfill_job

UTC_INFO = UTC


def test_capture_catalysts_are_company_and_as_of_scoped() -> None:
    company_one = CeriCatalystEvent(
        id=10,
        company_id=1,
        category="PRODUCT",
        subject_key="product-a",
        canonical_text="Product A",
    )
    company_two = CeriCatalystEvent(
        id=20,
        company_id=2,
        category="PRODUCT",
        subject_key="product-b",
        canonical_text="Product B",
    )
    rows = [
        CeriCatalystEventRevision(
            id=101,
            catalyst_event_id=10,
            revision_number=1,
            is_current=True,
            status="ANNOUNCED",
            direction="POSITIVE",
            effective_session=date(2026, 8, 1),
        ),
        CeriCatalystEventRevision(
            id=102,
            catalyst_event_id=10,
            revision_number=2,
            is_current=True,
            status="ANNOUNCED",
            direction="POSITIVE",
            effective_session=date(2026, 9, 1),
        ),
        CeriCatalystEventRevision(
            id=201,
            catalyst_event_id=20,
            revision_number=1,
            is_current=True,
            status="ANNOUNCED",
            direction="POSITIVE",
            effective_session=date(2026, 8, 1),
        ),
    ]
    db = RowDb({CeriCatalystEvent: [company_one, company_two], CeriCatalystEventRevision: rows})

    result = _catalyst_features_for_company(
        db,
        1,
        date(2026, 8, 7),
        CatalystFeatureStub(),
    )

    assert [item.revision_id for item in result] == [101]


def test_standalone_change_rebuild_honors_company_scope() -> None:
    companies = [
        CeriCompany(id=1, ticker="MSFT", exchange="US"),
        CeriCompany(id=2, ticker="AAPL", exchange="US"),
    ]
    snapshots = [
        _snapshot(1, 1),
        _snapshot(2, 2),
    ]
    events = [
        CeriCatalystEvent(id=10, company_id=1, category="PRODUCT", subject_key="a"),
        CeriCatalystEvent(id=20, company_id=2, category="PRODUCT", subject_key="b"),
    ]
    revisions = [
        _revision(101, 10),
        _revision(201, 20),
    ]
    guidance = [
        CeriGuidanceEvent(
            id=301,
            source_record_id=1,
            company_id=1,
            action="RAISED",
            effective_session=date(2026, 8, 2),
        ),
        CeriGuidanceEvent(
            id=302,
            source_record_id=2,
            company_id=2,
            action="LOWERED",
            effective_session=date(2026, 8, 2),
        ),
    ]
    detector = RecordingDetector()
    db = RowDb(
        {
            CeriCompany: companies,
            CeriScoreSnapshot: snapshots,
            CeriCatalystEvent: events,
            CeriCatalystEventRevision: revisions,
            CeriGuidanceEvent: guidance,
        }
    )

    result = CeriChangeRebuildService(detector=detector).rebuild(
        db,
        CeriChangeRebuildRequest(company_ids=(1,)),
    )

    assert result.changes == 3
    assert detector.revision_companies == [1]
    assert detector.guidance_companies == [1]
    assert detector.score_companies == [1]


def test_alert_rebuild_request_key_is_stable_across_job_ids() -> None:
    db = ProcessingDb(scalar_queue=[None])
    first_job = BackgroundJob(id=10, job_type="CERI_ALERT_REBUILD", payload_json={})

    first = execute_alert_rebuild_job(db, first_job)
    processing = next(row for row in db.added if isinstance(row, CeriProcessingRun))
    db.scalar_queue = [processing]
    second_job = BackgroundJob(id=11, job_type="CERI_ALERT_REBUILD", payload_json={})

    second = execute_alert_rebuild_job(db, second_job)

    assert first["processing_run_id"] == second["processing_run_id"]
    assert second["coalesced"] is True


def test_non_stale_warning_does_not_emit_data_stale_change() -> None:
    from app.services.ceri.change_detection_service import CeriChangeDetectionService

    prior = _snapshot(1, 1)
    current = _snapshot(2, 1)
    prior.opportunity_score = 1.0
    current.opportunity_score = 5.0
    current.warnings_json = ["missing_analyst_count"]
    db = ProcessingDb(scalar_queue=[None])

    result = CeriChangeDetectionService().detect_score_changes(
        db,
        current=current,
        prior=prior,
    )

    assert result.changes == 1
    assert {row.change_type for row in db.added} == {"OPPORTUNITY_UPGRADED"}


def test_backfill_retries_failed_ticker_from_checkpoint(monkeypatch) -> None:
    calls: list[str] = []

    class FakeIngestion:
        def __init__(self, **_kwargs) -> None:
            pass

        def ingest(self, _db, request):
            calls.append(request.ticker)
            if request.ticker == "A" and calls.count("A") == 1:
                raise RuntimeError("temporary provider failure")
            return SimpleNamespace(ingestion_run_id=None)

    class FakeNormalization:
        def normalize(self, *_args, **_kwargs):
            return None

    class FakeFeatures:
        def rebuild(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(backfill_module, "CeriIngestionService", FakeIngestion)
    monkeypatch.setattr(backfill_module, "CeriNormalizationService", FakeNormalization)
    monkeypatch.setattr(backfill_module, "CeriFeatureRebuildService", FakeFeatures)

    request = CeriBackfillRequest(
        provider="manual",
        dataset="estimates",
        tickers=("A", "B"),
    )
    db = BackfillDb([None])
    service = CeriBackfillService()

    first = service.run(db, request)
    processing = next(row for row in db.added if isinstance(row, CeriProcessingRun))
    db.scalar_queue = [processing]
    second = service.run(db, request)

    assert first.status == "PARTIAL"
    assert first.failed == 1
    assert first.checkpoints["failed_tickers"][0]["ticker"] == "A"
    assert second.status == "COMPLETED"
    assert second.failed == 0
    assert second.checkpoints["completed_tickers"] == ["A", "B"]
    assert calls == ["A", "B", "A"]


def test_partial_backfill_marks_background_job_partial(monkeypatch) -> None:
    class PartialBackfill:
        def run(self, *_args, **_kwargs):
            return SimpleNamespace(
                status="PARTIAL",
                as_dict=lambda: {"status": "PARTIAL", "checkpoints": {}, "failed": 1},
            )

    from app.services.ceri import job_handlers

    monkeypatch.setattr(job_handlers, "ceri_flags", lambda: SimpleNamespace(backfill=True))
    job = BackgroundJob(id=12, job_type="CERI_BACKFILL", payload_json={})

    result = execute_backfill_job(object(), job, backfill_service=PartialBackfill())

    assert result["status"] == "PARTIAL"
    assert job.status == "PARTIAL"


class CatalystFeatureStub:
    def calculate(self, *, event, revision, as_of_session):
        return SimpleNamespace(revision_id=revision.id, event_id=event.id, as_of=as_of_session)


class RecordingDetector:
    def __init__(self) -> None:
        self.score_companies = []
        self.revision_companies = []
        self.guidance_companies = []

    def detect_score_changes(self, _db, *, current, prior, scope):
        self.score_companies.append(current.company_id)
        return ChangeDetectionResult(1, 0)

    def detect_catalyst_revision(self, _db, *, revision, prior_revision, company_id):
        self.revision_companies.append(company_id)
        return ChangeDetectionResult(1, 0)

    def detect_guidance_change(self, _db, *, guidance, company_id, prior_action):
        self.guidance_companies.append(company_id)
        return ChangeDetectionResult(1, 0)


class RowResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class RowDb:
    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return RowResult(self.rows_by_model.get(model, []))

    def get(self, model, identifier):
        return next(
            (row for row in self.rows_by_model.get(model, []) if row.id == identifier),
            None,
        )


class ProcessingDb:
    def __init__(self, scalar_queue):
        self.scalar_queue = list(scalar_queue)
        self.added = []
        self.next_id = 1

    def scalar(self, _statement):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1


class BackfillDb(ProcessingDb):
    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        if model is CeriCompany:
            return RowResult(
                [
                    CeriCompany(id=1, ticker="A", exchange="US"),
                    CeriCompany(id=2, ticker="B", exchange="US"),
                ]
            )
        return RowResult([])


def _snapshot(snapshot_id: int, company_id: int) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        id=snapshot_id,
        company_id=company_id,
        ticker="MSFT" if company_id == 1 else "AAPL",
        as_of_session=date(2026, 8, snapshot_id),
        cutoff_at=datetime(2026, 8, snapshot_id, 21, tzinfo=UTC_INFO),
        opportunity_score=5.0,
        event_risk_score=2.0,
        data_confidence="Normal",
        coverage_pct=100.0,
        posture="Mixed",
        config_version="test",
        config_hash="test",
        calculation_version="test",
        evidence_hash=f"evidence-{snapshot_id}",
    )


def _revision(revision_id: int, event_id: int) -> CeriCatalystEventRevision:
    return CeriCatalystEventRevision(
        id=revision_id,
        catalyst_event_id=event_id,
        revision_number=1,
        is_current=True,
        status="ANNOUNCED",
        direction="POSITIVE",
        effective_session=date(2026, 8, 2),
    )
