from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriCompany,
    CeriCompanyAlias,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import BackgroundJob
from app.services.ceri.catalyst_taxonomy import CeriCatalystTaxonomy
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.dtos import EstimateRequest
from app.services.ceri.enums import CeriConfidenceLabel, CeriMetric, CeriPeriodType
from app.services.ceri.feature_flags import CeriFeatureFlags, parse_explicit_bool
from app.services.ceri.guidance_normalizer import CeriGuidanceNormalizer
from app.services.ceri.job_handlers import (
    CERI_ALERT_REBUILD,
    CERI_CAPTURE_RUN,
    CERI_CHANGE_DETECTION,
    CERI_NORMALIZE,
    CERI_PROVIDER_INGEST,
    CERI_PURGE_LICENSED_DATA,
    CERI_REBUILD_FEATURES,
    execute_alert_rebuild_job,
    execute_backfill_job,
    execute_capture_run_job,
    execute_change_detection_job,
    execute_normalize_job,
    execute_provider_ingest_job,
    execute_purge_licensed_data_job,
    execute_rebuild_features_job,
)
from app.services.ceri.normalization_service import _persist_sec_identity
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.purge_service import (
    CeriPurgeError,
    CeriPurgeExecuteRequest,
    CeriPurgePreviewRequest,
    CeriPurgeService,
    confirmation_token_for_preview,
)
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient, SecFairAccessError
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.settings import Settings


def test_master_flag_gates_every_child_and_boolean_parser_is_explicit() -> None:
    flags = CeriFeatureFlags.from_settings(
        Settings(
            _env_file=None,
            ceri_enabled=False,
            ceri_provider_ingest_enabled=True,
            ceri_run_capture_enabled=True,
            ceri_ui_enabled=True,
            ceri_alerts_enabled=True,
            ceri_admin_enabled=True,
            ceri_backfill_enabled=True,
        )
    )
    assert flags == CeriFeatureFlags(False, False, False, False, False, False, False)
    assert parse_explicit_bool("false") is False
    assert parse_explicit_bool("true") is True
    with pytest.raises(ValueError):
        parse_explicit_bool("definitely")
    string_flags = SimpleNamespace(
        ceri_enabled="false",
        ceri_provider_ingest_enabled="true",
        ceri_run_capture_enabled="true",
        ceri_ui_enabled="true",
        ceri_alerts_enabled="true",
        ceri_admin_enabled="true",
        ceri_backfill_enabled="true",
    )
    assert not CeriFeatureFlags.from_settings(string_flags).enabled


def test_master_disabled_closes_ui_jobs_and_backfill_even_when_children_are_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            ceri_enabled=False,
            ceri_ui_enabled=True,
            ceri_admin_enabled=True,
            ceri_alerts_enabled=True,
            ceri_backfill_enabled=True,
        )
    )
    assert TestClient(app).get("/ceri").status_code == 404
    disabled = CeriFeatureFlags(False, False, False, False, False, False, False)
    assert disabled.alerts is False
    assert disabled.backfill is False
    monkeypatch.setattr("app.services.ceri.job_handlers.ceri_flags", lambda: disabled)
    alert = execute_alert_rebuild_job(
        TraceDb(), BackgroundJob(id=1, job_type=CERI_ALERT_REBUILD, payload_json={})
    )
    backfill = execute_backfill_job(
        TraceDb(), BackgroundJob(id=2, job_type="CERI_BACKFILL", payload_json={})
    )
    assert alert["status"] == "SKIPPED"
    assert backfill["status"] == "SKIPPED"
    purge = execute_purge_licensed_data_job(
        TraceDb(),
        BackgroundJob(id=4, job_type=CERI_PURGE_LICENSED_DATA, payload_json={}),
    )
    assert purge["status"] == "SKIPPED"
    provider = execute_provider_ingest_job(
        TraceDb(),
        BackgroundJob(
            id=3,
            job_type=CERI_PROVIDER_INGEST,
            payload_json={"provider": "manual", "dataset": "estimates", "ticker": "MSFT"},
        ),
        ingestion_service=FakeIngestionService(),
    )
    assert provider["status"] == "SKIPPED"


def test_pipeline_child_overrides_remain_gated_by_master(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = CeriFeatureFlags(False, False, False, False, False, False, False)
    monkeypatch.setattr("app.services.pipeline_service.ceri_flags", lambda: disabled)
    from app.services.pipeline_service import pipeline_step_names

    assert "CERI_CAPTURE_SNAPSHOT" not in pipeline_step_names(ceri_run_capture_enabled=True)
    assert "CERI_PROVIDER_INGEST" not in pipeline_step_names(ceri_provider_ingest_enabled=True)


def test_zero_elapsed_days_is_a_valid_freshness_input() -> None:
    feature = _feature(warnings=[])
    feature.as_of_session = date(2026, 8, 3)
    feature.actual_elapsed_days = 0
    result = CeriConfidenceService().calculate(
        as_of_session=date(2026, 8, 3),
        revision_features=[feature],
    )
    assert result.score > 4.0


def test_catalyst_materiality_preserves_zero_and_missing() -> None:
    def source(materiality):
        return CeriSourceRecord(
            provider="eodhd",
            dataset="catalysts",
            provider_record_id=f"news-{materiality}",
            raw_json={
                "category": "PRODUCT",
                "subject": "Product update",
                "materiality": materiality,
                "confidence": "NORMAL",
                "source_date": "2026-08-03",
            },
            content_hash="h",
            idempotency_key=f"i-{materiality}",
        )

    taxonomy = CeriCatalystTaxonomy()
    assert taxonomy.normalize(source(0), company_id=1).materiality == 0.0
    assert taxonomy.normalize(source(None), company_id=1).materiality is None


def test_eodhd_trend_baselines_use_provider_observation_time_not_future_fiscal_end() -> None:
    observed = datetime(2026, 8, 3, 15, 0, tzinfo=UTC)
    payload = [
        {
            "code": "AAPL.US",
            "period": "0q",
            "date": "2026-12-31",
            "observedAt": observed.isoformat(),
            "earningsEstimateAvg": 2.0,
            "epsTrend7daysAgo": 1.9,
            "epsTrend30daysAgo": 1.8,
            "epsTrend60daysAgo": 1.7,
            "epsTrend90daysAgo": 1.6,
        }
    ]
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="fixture"), transport=lambda _url, _timeout: payload
    )
    records = list(
        EodhdCeriProvider(client=client).fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "AAPL",
                (CeriMetric.EPS_DILUTED,),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )
    baselines = {
        row.payload["trend_baseline_days"]: row
        for row in records
        if "trend_baseline_days" in row.payload
    }
    assert baselines[7].observed_at == observed - timedelta(days=7)
    assert baselines[30].observed_at == observed - timedelta(days=30)
    assert baselines[60].observed_at == observed - timedelta(days=60)
    assert baselines[90].observed_at == observed - timedelta(days=90)
    assert all(row.payload["fiscal_period_end"] == "2026-12-31" for row in baselines.values())
    assert all(row.retrieved_at is not None for row in records)


def test_zero_revision_is_scored_but_none_revision_is_unavailable() -> None:
    base = dict(
        company_id=1,
        metric="EPS_DILUTED",
        period_key="period",
        as_of_session=date(2026, 8, 3),
        window_days=7,
        config_version="1",
        config_hash="1",
        calculation_version="1",
    )
    zero = CeriRevisionFeature(**base, pct_change=Decimal("0"), net_breadth=Decimal("0"))
    missing = CeriRevisionFeature(**base, pct_change=None, net_breadth=None)
    zero_result = CeriOpportunityScoreService().calculate(revision_features=[zero])
    missing_result = CeriOpportunityScoreService().calculate(revision_features=[missing])
    zero_component = next(row for row in zero_result.components if row.name == "revision_magnitude")
    missing_component = next(
        row for row in missing_result.components if row.name == "revision_magnitude"
    )
    assert zero_component.value == 0.0
    assert zero_component.contribution == 0.0
    assert missing_component.value is None
    assert "revision_magnitude_unavailable" in missing_result.warnings


def test_capture_penalties_are_isolated_per_company(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.ceri.capture_service as capture_module

    company_a = CeriCompany(id=1, ticker="AAA", exchange="US")
    company_b = CeriCompany(id=2, ticker="BBB", exchange="US")
    rows = [
        SimpleNamespace(ticker="AAA", upcoming_earnings_date=None, raw_json={}),
        SimpleNamespace(ticker="BBB", upcoming_earnings_date=None, raw_json={}),
    ]
    features = {
        1: [_feature(warnings=["source_conflict", "estimate_data_stale"])],
        2: [_feature(warnings=[])],
    }
    monkeypatch.setattr(capture_module, "_raw_rows_for_run", lambda _db, _run: rows)
    monkeypatch.setattr(
        capture_module,
        "_company_for_ticker",
        lambda _db, ticker: company_a if ticker == "AAA" else company_b,
    )
    monkeypatch.setattr(capture_module, "_existing_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        capture_module,
        "_revision_features",
        lambda _db, company_id, _date: features[company_id],
    )
    monkeypatch.setattr(capture_module, "_catalyst_features_for_company", lambda *_args: [])
    monkeypatch.setattr(capture_module, "_guidance_for_company", lambda *_args: [])
    monkeypatch.setattr(capture_module, "_source_ids", lambda _features: [])
    monkeypatch.setattr(capture_module, "_prior_snapshot", lambda *_args: None)
    monkeypatch.setattr(capture_module, "_latest_changes", lambda *_args: [])
    monkeypatch.setattr(capture_module, "_quarantined_count", lambda _db: 0)

    class Spy:
        def __init__(self):
            self.calls = []

        def calculate(self, **kwargs):
            self.calls.append(kwargs)
            if "revision_features" in kwargs:
                return SimpleNamespace(
                    label=SimpleNamespace(value="Normal"),
                    coverage_pct=100.0,
                    reasons=(),
                    warnings=(),
                )
            if "stale" in kwargs:
                return SimpleNamespace(
                    score=0.0,
                    earnings_proximity=SimpleNamespace(level="clear", risk_score=0.0),
                    reasons=(),
                    warnings=(),
                )
            return SimpleNamespace(score=0.0, components=(), reasons=(), warnings=())

        def summarize(self, *_args, **_kwargs):
            return SimpleNamespace(price_response_quality=None)

    class Snapshot:
        config = SimpleNamespace(
            config_hash="config", engine=SimpleNamespace(calculation_version="v1")
        )

        def build_snapshot(self, **kwargs):
            return SimpleNamespace(
                company_id=kwargs["company_id"],
                id=None,
                run_id=kwargs["run_id"],
                as_of_session=kwargs["as_of_session"],
                opportunity_score=kwargs["opportunity"].score,
                event_risk_score=kwargs["event_risk"].score,
                component_json={},
            )

        def persist_snapshot(self, _db, _snapshot):
            return None

    class Changes:
        def detect_score_changes(self, *_args, **_kwargs):
            return SimpleNamespace(changes=0)

    service = capture_module.CeriRunCaptureService()
    service.snapshot_service = Snapshot()
    service.confidence = Spy()
    service.opportunity = Spy()
    service.risk = Spy()
    service.surprise = Spy()
    service.change_detection = Changes()
    service.alert_service = SimpleNamespace()
    service.capture_run(TraceDb(), 7)

    confidence_calls = service.confidence.calls
    risk_calls = [call for call in service.risk.calls if "stale" in call]
    assert [call["conflict_penalty"] for call in confidence_calls] == [1.0, 0.0]
    assert [call["conflict_penalty"] for call in risk_calls] == [1.0, 0.0]
    assert [call["stale"] for call in risk_calls] == [True, False]


def test_guidance_confidence_aliases_point_values_and_unknown_extraction() -> None:
    source = CeriSourceRecord(
        id=1,
        provider="sec",
        dataset="guidance",
        provider_record_id="acc-1:point",
        raw_json={
            "action": "RAISED",
            "metric": "REVENUE",
            "period_type": "ANNUAL",
            "point_value": "100",
            "unit": "million",
            "currency": "USD",
            "confidence": "hIgH",
            "evidence_locator": "acc-1/exhibit#paragraph-1",
            "filing_accession": "0000000000-26-000001",
            "announced_at": "2026-08-03T16:30:00+00:00",
        },
        content_hash="h",
        idempotency_key="i",
    )
    normalized = CeriGuidanceNormalizer().normalize(source, company_id=42)
    assert normalized.point_value == Decimal("100")
    assert normalized.unit == "million"
    assert normalized.currency == "USD"
    assert normalized.confidence == CeriConfidenceLabel.HIGH.value
    assert normalized.filing_accession == "0000000000-26-000001"

    extracted = GuidanceExtractionService().extract(
        "The company discussed its outlook at approximately $100 million.", locator="doc"
    )[0]
    assert extracted.action == "UNKNOWN"
    assert extracted.confidence == "LOW"


def test_sec_identity_mapping_is_persisted_durably() -> None:
    company = CeriCompany(id=42, ticker="MSFT", exchange="US")
    source = CeriSourceRecord(
        id=9,
        provider="sec",
        dataset="guidance",
        provider_record_id="acc-1",
        raw_json={"cik": "789", "ticker": "MSFT"},
        content_hash="h",
        idempotency_key="i",
    )
    db = IdentityDb(company)

    _persist_sec_identity(db, source, company.id)

    assert company.cik == "0000000789"
    alias = next(row for row in db.added if isinstance(row, CeriCompanyAlias))
    assert alias.provider == "sec"
    assert alias.alias_type == "cik"
    assert alias.alias_value == "0000000789"


def test_sec_fair_access_403_stops_without_retry() -> None:
    sleeps: list[float] = []

    class Response:
        status = 403
        headers = {}

    client = SecEdgarClient(
        SecClientConfig(max_attempts=3),
        transport=lambda *_args: Response(),
        sleep=sleeps.append,
    )
    with pytest.raises(SecFairAccessError):
        client.get_json("/submissions/CIK0000000001.json")
    assert client.requests == 1
    assert sleeps == []


def test_eodhd_purge_rechecks_manifest_and_preserves_independent_lineage() -> None:
    eodhd = CeriSourceRecord(
        id=1,
        provider="eodhd",
        license_scope="personal",
        dataset="estimates",
        provider_record_id="eodhd-1",
        raw_json={"restricted": "payload"},
        content_hash="eodhd-hash",
        idempotency_key="eodhd-idem",
        export_policy="restricted",
        purge_eligible=True,
    )
    manual = CeriSourceRecord(
        id=2,
        provider="manual",
        license_scope="manual",
        dataset="estimates",
        provider_record_id="manual-1",
        raw_json={"independent": True},
        content_hash="manual-hash",
        idempotency_key="manual-idem",
        export_policy="exportable",
    )
    sec = CeriSourceRecord(
        id=3,
        provider="sec",
        license_scope="public_first_party",
        dataset="guidance",
        provider_record_id="sec-1",
        raw_json={"filing": True},
        content_hash="sec-hash",
        idempotency_key="sec-idem",
        export_policy="exportable",
    )
    eodhd_score = CeriScoreSnapshot(
        id=10,
        company_id=1,
        ticker="MSFT",
        data_confidence="Normal",
        coverage_pct=100.0,
        posture="Positive",
        component_json={"source_ids": [1]},
        warnings_json=[],
    )
    manual_score = CeriScoreSnapshot(
        id=11,
        company_id=1,
        ticker="MSFT",
        data_confidence="Normal",
        coverage_pct=100.0,
        posture="Positive",
        component_json={"source_ids": [2]},
        warnings_json=[],
    )
    unrelated_score = CeriScoreSnapshot(
        id=12,
        company_id=2,
        ticker="AAPL",
        data_confidence="Normal",
        coverage_pct=100.0,
        posture="Positive",
        component_json={"source_ids": [3]},
        warnings_json=[],
    )
    change = CeriChangeEvent(
        id=20,
        company_id=1,
        to_snapshot_id=10,
        change_type="OPPORTUNITY_UPGRADED",
        severity="INFO",
        dedup_key="change-eodhd",
    )
    alert = CeriAlertEvent(
        id=21,
        source_change_event_id=20,
        event_key="alert-eodhd",
        ticker="MSFT",
        severity="INFO",
        status="UNREAD",
    )
    db = PurgeDb(
        {
            CeriSourceRecord: [eodhd, manual, sec],
            CeriScoreSnapshot: [eodhd_score, manual_score, unrelated_score],
            CeriChangeEvent: [change],
            CeriAlertEvent: [alert],
        }
    )
    service = CeriPurgeService()
    with pytest.raises(CeriPurgeError, match="only valid for provider eodhd"):
        service.preview(
            db,
            CeriPurgePreviewRequest(
                provider="manual",
                license_scope="personal",
                actor="test",
                reason="scope mismatch",
            ),
        )
    preview = service.preview(
        db,
        CeriPurgePreviewRequest(
            provider="eodhd",
            license_scope="personal",
            actor="test",
            reason="termination",
        ),
    )
    eodhd.content_hash = "changed-after-preview"
    with pytest.raises(CeriPurgeError, match="no longer matches"):
        service.execute(
            db,
            CeriPurgeExecuteRequest(
                provider="eodhd",
                license_scope="personal",
                actor="test",
                reason="termination",
                confirmation_token=confirmation_token_for_preview(preview.preview_manifest_hash),
                preview_manifest_hash=preview.preview_manifest_hash,
            ),
        )

    fresh = service.preview(
        db,
        CeriPurgePreviewRequest(
            provider="eodhd",
            license_scope="personal",
            actor="test",
            reason="termination",
        ),
    )
    executed = service.execute(
        db,
        CeriPurgeExecuteRequest(
            provider="eodhd",
            license_scope="personal",
            actor="test",
            reason="termination",
            confirmation_token=confirmation_token_for_preview(fresh.preview_manifest_hash),
            preview_manifest_hash=fresh.preview_manifest_hash,
        ),
    )
    assert executed.affected_counts_json["source_records"] == 1
    assert eodhd.raw_json is None
    assert manual.raw_json == {"independent": True}
    assert sec.raw_json == {"filing": True}
    assert "provider_license_purge_invalidated" in eodhd_score.warnings_json
    assert not manual_score.warnings_json
    assert not unrelated_score.warnings_json
    assert alert.status == "INVALIDATED"


def test_pipeline_job_chain_enqueues_each_stage_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    enabled = CeriFeatureFlags(True, True, True, True, True, True, True)
    monkeypatch.setattr("app.services.ceri.job_handlers.ceri_flags", lambda: enabled)
    db = TraceDb()

    ingest_job = BackgroundJob(
        id=1,
        job_type=CERI_PROVIDER_INGEST,
        related_run_id=77,
        payload_json={"provider": "manual", "dataset": "estimates", "ticker": "MSFT", "run_id": 77},
    )
    ingest_result = execute_provider_ingest_job(
        db, ingest_job, ingestion_service=FakeIngestionService()
    )
    assert ingest_result["normalize_job_id"]

    normalize_job = next(row for row in db.added if row.job_type == CERI_NORMALIZE)
    normalize_result = execute_normalize_job(
        db, normalize_job, normalization_service=FakeNormalizationService()
    )
    assert normalize_result["feature_job_id"]

    feature_job = next(row for row in db.added if row.job_type == CERI_REBUILD_FEATURES)
    feature_result = execute_rebuild_features_job(
        db, feature_job, feature_service=FakeFeatureService()
    )
    assert feature_result["capture_job_id"]

    capture_job = next(row for row in db.added if row.job_type == CERI_CAPTURE_RUN)
    capture_result = execute_capture_run_job(db, capture_job, capture_service=FakeCaptureService())
    assert capture_result["change_job_id"]

    change_job = next(row for row in db.added if row.job_type == CERI_CHANGE_DETECTION)
    change_result = execute_change_detection_job(db, change_job, change_service=FakeChangeService())
    assert change_result["alert_job_id"]
    assert [row.job_type for row in db.added if isinstance(row, BackgroundJob)] == [
        CERI_NORMALIZE,
        CERI_REBUILD_FEATURES,
        CERI_CAPTURE_RUN,
        CERI_CHANGE_DETECTION,
        CERI_ALERT_REBUILD,
    ]


class FakeIngestionService:
    def ingest(self, db, request, *, should_cancel=None):
        return SimpleNamespace(
            ingestion_run_id=11,
            provider=request.provider,
            dataset=request.dataset.value,
            status="COMPLETED",
            requested=1,
            fetched=1,
            inserted=1,
            deduplicated=0,
            corrected=0,
            quarantined=0,
            failed=0,
            warnings=0,
            as_dict=lambda: {
                "ingestion_run_id": 11,
                "provider": request.provider,
                "dataset": request.dataset.value,
                "status": "COMPLETED",
                "requested": 1,
                "fetched": 1,
                "inserted": 1,
                "deduplicated": 0,
                "corrected": 0,
                "quarantined": 0,
                "failed": 0,
                "warnings": 0,
            },
        )

    def request_key(self, request):
        return f"ceri:{request.provider}:{request.dataset.value}:{request.ticker}"


class FakeNormalizationService:
    def normalize(self, db, *, processing_run, ingestion_run_id):
        processing_run.normalized_count = 1
        processing_run.status = "COMPLETED"
        return SimpleNamespace(
            processing_run_id=processing_run.id,
            status="COMPLETED",
            read=1,
            normalized=1,
            quarantined=0,
            failed=0,
            warnings=0,
            as_dict=lambda: {
                "processing_run_id": processing_run.id,
                "status": "COMPLETED",
                "read": 1,
                "normalized": 1,
                "quarantined": 0,
                "failed": 0,
                "warnings": 0,
            },
        )


class FakeFeatureService:
    def rebuild(self, db, request, *, processing_run):
        return SimpleNamespace(
            features=1,
            features_deduplicated=0,
            earnings_updated=0,
            processed_companies=1,
            warnings=0,
            failed=0,
            errors=(),
            as_dict=lambda: {"features": 1, "processed_companies": 1, "failed": 0},
        )


class FakeCaptureService:
    def capture_run(self, db, run_id):
        return SimpleNamespace(
            as_dict=lambda: {
                "score_snapshots": 1,
                "change_events": 1,
                "alerts": 0,
                "failed": 0,
                "stale": 0,
                "conflicted": 0,
            }
        )


class FakeChangeService:
    def rebuild(self, db, request):
        return SimpleNamespace(
            changes=1,
            duplicates=0,
            warnings=0,
            failed=0,
            errors=(),
            as_dict=lambda: {"changes": 1, "change_count": 1, "failed": 0},
        )


class TraceDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.next_id = 1

    def scalar(self, _statement):
        return None

    def add(self, row) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self.next_id
                self.next_id += 1


class IdentityDb(TraceDb):
    def __init__(self, company) -> None:
        super().__init__()
        self.company = company

    def get(self, model, row_id):
        return self.company if model is CeriCompany and row_id == self.company.id else None


class PurgeDb(TraceDb):
    def __init__(self, collections):
        super().__init__()
        self.collections = {model: list(rows) for model, rows in collections.items()}

    def add(self, row) -> None:
        super().add(row)
        self.collections.setdefault(type(row), []).append(row)

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return SimpleNamespace(all=lambda: list(self.collections.get(model, [])))

    def scalar(self, statement):
        model = statement.column_descriptions[0]["entity"]
        rows = self.collections.get(model, [])
        if model is CeriPurgeAudit:
            target = next(iter(statement.compile().params.values()), None)
            return next((row for row in rows if row.preview_manifest_hash == target), None)
        return rows[0] if rows else None


def _feature(*, warnings: list[str]) -> CeriRevisionFeature:
    return CeriRevisionFeature(
        company_id=1,
        metric="EPS_DILUTED",
        period_key="period",
        as_of_session=date(2026, 8, 3),
        window_days=7,
        warnings_json=warnings or None,
        config_version="1",
        config_hash="1",
        calculation_version="1",
    )
