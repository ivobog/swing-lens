from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
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
    CeriChangeRebuildResult,
    CeriChangeRebuildService,
)
from app.services.ceri.feature_flags import CeriFeatureFlags
from app.services.ceri.job_handlers import (
    CERI_CHANGE_DETECTION,
    _eligible_changes,
    execute_alert_rebuild_job,
    execute_backfill_job,
    execute_change_detection_job,
)

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


def test_run_scoped_change_rebuild_uses_prior_snapshot_outside_scope() -> None:
    prior = _snapshot(1, 1)
    prior.run_id = 10
    current = _snapshot(2, 1)
    current.run_id = 11
    detector = RecordingDetector()
    db = RowDb({CeriScoreSnapshot: [prior, current]})

    CeriChangeRebuildService(detector=detector).rebuild(
        db,
        CeriChangeRebuildRequest(run_id=11),
    )

    assert detector.score_comparisons == [(current.id, prior.id)]


def test_alert_rebuild_request_key_is_stable_across_job_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ceri.job_handlers.ceri_flags",
        lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
    )
    db = ProcessingDb(scalar_queue=[None])
    first_job = BackgroundJob(id=10, job_type="CERI_ALERT_REBUILD", payload_json={})

    first = execute_alert_rebuild_job(db, first_job)
    processing = next(row for row in db.added if isinstance(row, CeriProcessingRun))
    db.scalar_queue = [processing]
    second_job = BackgroundJob(id=11, job_type="CERI_ALERT_REBUILD", payload_json={})

    second = execute_alert_rebuild_job(db, second_job)

    assert first["processing_run_id"] == second["processing_run_id"]
    assert second["coalesced"] is True


def test_snapshot_transition_alert_is_scoped_to_its_run() -> None:
    current = _snapshot(2, 1)
    current.run_id = 7
    transition = _change(10, company_id=1, from_snapshot_id=1, to_snapshot_id=2)
    unrelated = _change(11, company_id=1, from_snapshot_id=3, to_snapshot_id=4)
    db = RowDb(
        {
            CeriScoreSnapshot: [current],
            CeriChangeEvent: [transition, unrelated],
        }
    )

    selected = _eligible_changes(db, {"run_id": 7})

    assert [change.id for change in selected] == [transition.id]


def test_event_only_binary_alert_uses_exact_upstream_change_identity() -> None:
    current = _snapshot(2, 1)
    current.run_id = 7
    event_only = _change(
        20,
        company_id=1,
        change_type="NEW_BINARY_EVENT",
        catalyst_revision_id=101,
    )
    db = RowDb(
        {
            CeriScoreSnapshot: [current],
            CeriChangeEvent: [event_only],
        }
    )

    selected = _eligible_changes(db, {"run_id": 7, "change_ids": [event_only.id]})

    assert [change.id for change in selected] == [event_only.id]


def test_exact_event_alert_scope_does_not_sweep_unrelated_company_history() -> None:
    current = _snapshot(2, 1)
    current.run_id = 7
    event_only = _change(
        20,
        company_id=1,
        change_type="NEW_BINARY_EVENT",
        catalyst_revision_id=101,
    )
    unrelated = _change(
        21,
        company_id=1,
        change_type="NEW_BINARY_EVENT",
        catalyst_revision_id=99,
    )
    db = RowDb(
        {
            CeriScoreSnapshot: [current],
            CeriChangeEvent: [unrelated, event_only],
        }
    )

    selected = _eligible_changes(db, {"run_id": 7, "change_ids": [event_only.id]})

    assert [change.id for change in selected] == [event_only.id]


def test_change_detection_retry_reuses_exact_alert_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.ceri.job_handlers.ceri_flags",
        lambda: CeriFeatureFlags(True, True, True, True, True, True, True),
    )
    enqueued = []

    def capture_enqueue(_db, job_type, payload, **kwargs):
        enqueued.append((job_type, payload, kwargs))
        return SimpleNamespace(id=100 + len(enqueued))

    monkeypatch.setattr("app.services.ceri.job_handlers.enqueue_job", capture_enqueue)
    db = ProcessingDb(scalar_queue=[None])
    payload = {"request_key": "ceri:change-rebuild:run:7", "run_id": 7}
    first_job = BackgroundJob(
        id=30,
        job_type=CERI_CHANGE_DETECTION,
        payload_json=payload,
        related_run_id=7,
    )
    service = FixedChangeService(
        CeriChangeRebuildResult(changes=1, change_ids=(20,))
    )

    first = execute_change_detection_job(db, first_job, change_service=service)
    processing = next(row for row in db.added if isinstance(row, CeriProcessingRun))
    db.scalar_queue = [processing]
    retry_job = BackgroundJob(
        id=31,
        job_type=CERI_CHANGE_DETECTION,
        payload_json=payload,
        related_run_id=7,
    )
    second = execute_change_detection_job(db, retry_job, change_service=service)

    assert first["change_ids"] == [20]
    assert second["coalesced"] is True
    assert second["change_ids"] == [20]
    assert [item[1]["change_ids"] for item in enqueued] == [[20], [20]]
    assert enqueued[0][2]["request_key"] == enqueued[1][2]["request_key"]


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
    assert {row.change_type for row in db.added} == {"OPPORTUNITY_CHANGED"}


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
        self.score_comparisons = []

    def detect_score_changes(self, _db, *, current, prior, scope):
        self.score_companies.append(current.company_id)
        self.score_comparisons.append(
            (current.id, prior.id if prior is not None else None)
        )
        return ChangeDetectionResult(1, 0)

    def detect_catalyst_revision(self, _db, *, revision, prior_revision, company_id):
        self.revision_companies.append(company_id)
        return ChangeDetectionResult(1, 0)

    def detect_guidance_change(self, _db, *, guidance, company_id, prior_action):
        self.guidance_companies.append(company_id)
        return ChangeDetectionResult(1, 0)


class FixedChangeService:
    def __init__(self, result: CeriChangeRebuildResult) -> None:
        self.result = result

    def rebuild(self, _db, _request) -> CeriChangeRebuildResult:
        return self.result


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


def _change(
    change_id: int,
    *,
    company_id: int,
    change_type: str = "OPPORTUNITY_UPGRADED",
    from_snapshot_id: int | None = None,
    to_snapshot_id: int | None = None,
    catalyst_revision_id: int | None = None,
) -> CeriChangeEvent:
    return CeriChangeEvent(
        id=change_id,
        company_id=company_id,
        from_snapshot_id=from_snapshot_id,
        to_snapshot_id=to_snapshot_id,
        catalyst_revision_id=catalyst_revision_id,
        change_type=change_type,
        severity="NOTABLE",
        importance="NOTABLE",
        signal_class="RISK" if change_type == "NEW_BINARY_EVENT" else "POSITIVE",
        comparison_state="COMPARABLE",
        delta_json={},
        dedup_key=f"change-{change_id}",
        created_at=datetime(2026, 8, 14, 20, change_id % 60, tzinfo=UTC),
    )
