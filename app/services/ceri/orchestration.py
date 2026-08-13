from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriIngestionRun, CeriProviderRequestTelemetry
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import (
    CatalystRequest,
    EarningsRequest,
    EstimateRequest,
    GuidanceRequest,
    RawProviderRecord,
)
from app.services.ceri.enums import CatalystCategory, CeriDataset
from app.services.ceri.provider_protocol import CeriProvider
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.settings import SecDocumentIncrementalMode, Settings, get_settings


class CeriIngestionCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class CeriIngestionRequest:
    provider: str
    dataset: CeriDataset
    ticker: str
    request_key: str | None = None
    scope: dict[str, Any] | None = None
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class CeriIngestionResult:
    ingestion_run_id: int | None
    provider: str
    dataset: str
    status: str
    requested: int
    fetched: int
    inserted: int
    deduplicated: int
    corrected: int
    quarantined: int
    failed: int
    warnings: int
    documents_discovered: int = 0
    documents_downloaded: int = 0
    documents_skipped: int = 0
    documents_would_skip: int = 0
    readiness_state: str | None = None
    run_evidence_status: str | None = None
    readiness_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CeriIngestionService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        registry: CeriProviderRegistry | None = None,
        source_records: CeriSourceRecordService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.registry = registry or CeriProviderRegistry(config=self.config)
        self.source_records = source_records or CeriSourceRecordService()
        self.settings = settings or get_settings()

    def ingest(
        self,
        db: Session,
        request: CeriIngestionRequest,
        *,
        should_cancel=None,
    ) -> CeriIngestionResult:
        provider = self.registry.get(request.provider)
        dataset_policy = self.registry.license_policy(request.provider, request.dataset.value)
        request_key = request.request_key or self.request_key(request)
        ingestion_run = self.source_records.create_ingestion_run(
            db,
            provider=request.provider,
            provider_terms_version=getattr(provider, "terms_version", None)
            or getattr(provider, "provider_terms_version", None)
            or dataset_policy.terms_version,
            dataset=request.dataset.value,
            scope=request.scope or {"ticker": request.ticker},
            request_key=request_key,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version,
        )
        if ingestion_run.status in {"COMPLETED", "PARTIAL"}:
            return self._result_from_run(ingestion_run)

        if (
            request.provider == "sec"
            and request.dataset is CeriDataset.GUIDANCE
            and self.settings.sec_document_incremental_mode is not SecDocumentIncrementalMode.OFF
        ):
            return self._ingest_sec_incremental(
                db,
                request=request,
                provider=provider,
                dataset_policy=dataset_policy,
                ingestion_run=ingestion_run,
                should_cancel=should_cancel,
            )

        requested = fetched = inserted = deduplicated = corrected = quarantined = failed = 0
        response_bytes = stored_bytes = 0
        warning_count = 0
        errors: list[dict[str, Any]] = []
        checkpoint: dict[str, Any] = {}
        provider_stats_before = _provider_stats(provider)
        started = time.perf_counter()

        try:
            for index, record in enumerate(self._fetch_records(provider, request), start=1):
                requested += 1
                payload_bytes = len(
                    json.dumps(record.payload, sort_keys=True, default=str).encode("utf-8")
                )
                response_bytes += payload_bytes
                if callable(should_cancel) and should_cancel():
                    raise CeriIngestionCancelled("CERI ingestion cancelled.")
                try:
                    fetched += 1
                    write = self.source_records.store_source_record(
                        db,
                        ingestion_run_id=ingestion_run.id,
                        record=record,
                        raw_payload_allowed=dataset_policy.raw_payload_storage_allowed,
                    )
                    inserted += int(write.inserted)
                    if write.inserted:
                        stored_bytes += payload_bytes
                    deduplicated += int(write.deduplicated)
                    corrected += int(write.corrected)
                    quarantined += int(write.quarantined)
                except SQLAlchemyError:
                    raise
                except Exception as exc:
                    failed += 1
                    errors.append(_safe_error(index, record, exc))
                checkpoint = {"last_record_index": index}
        except CeriIngestionCancelled:
            status = "CANCELLED"
        except SQLAlchemyError:
            raise
        except Exception as exc:
            failed += 1
            errors.append({"error": _safe_message(exc)})
            status = "PARTIAL"
        else:
            status = "PARTIAL" if failed or quarantined else "COMPLETED"

        finished = self.source_records.finish_ingestion_run(
            db,
            ingestion_run,
            status=status,
            requested_count=requested,
            fetched_count=fetched,
            inserted_count=inserted,
            deduplicated_count=deduplicated,
            corrected_count=corrected,
            quarantined_count=quarantined,
            failed_count=failed,
            warning_count=warning_count,
            quota_state={"provider": request.provider, "status": "manual"},
            checkpoint=checkpoint,
            errors={"records": errors} if errors else None,
            warnings=None,
        )
        _record_provider_telemetry(
            db,
            provider=provider,
            dataset=request.dataset.value,
            request_key=request_key,
            scope=request.scope or {"ticker": request.ticker},
            before=provider_stats_before,
            started=started,
            failed=failed,
            response_bytes=response_bytes,
            stored_bytes=stored_bytes,
        )
        return self._result_from_run(finished)

    def request_key(self, request: CeriIngestionRequest) -> str:
        return f"ceri:{request.provider}:{request.dataset.value}:{request.ticker.upper()}"

    def _ingest_sec_incremental(
        self,
        db: Session,
        *,
        request: CeriIngestionRequest,
        provider,
        dataset_policy,
        ingestion_run: CeriIngestionRun,
        should_cancel,
    ) -> CeriIngestionResult:
        from app.services.ceri.sec.incremental_ingestion import (
            SecGuidanceIncrementalIngestionService,
            SecIncrementalCancelled,
        )

        service = SecGuidanceIncrementalIngestionService(
            settings=self.settings,
            source_records=self.source_records,
        )
        try:
            outcome = service.ingest(
                db,
                provider=provider,
                ingestion_run=ingestion_run,
                ticker=request.ticker,
                start=request.start,
                end=request.end,
                raw_payload_allowed=dataset_policy.raw_payload_storage_allowed,
                historical_backfill=bool((request.scope or {}).get("backfill")),
                should_cancel=should_cancel,
                worker_id=str(
                    (request.scope or {}).get("worker_id") or self.settings.job_worker_id
                ),
            )
            status = "PARTIAL" if outcome.failed or outcome.quarantined else "COMPLETED"
        except SecIncrementalCancelled:
            raise CeriIngestionCancelled("CERI ingestion cancelled.") from None
        except SQLAlchemyError:
            raise
        except Exception as exc:
            from app.services.ceri.sec.incremental_ingestion import SecIncrementalOutcome

            outcome = SecIncrementalOutcome(failed=1, errors=[{"error": _safe_message(exc)}])
            status = "PARTIAL"
        telemetry = outcome.telemetry()
        finished = self.source_records.finish_ingestion_run(
            db,
            ingestion_run,
            status=status,
            requested_count=outcome.requested,
            fetched_count=outcome.fetched,
            inserted_count=outcome.inserted,
            deduplicated_count=outcome.deduplicated,
            corrected_count=outcome.corrected,
            quarantined_count=outcome.quarantined,
            failed_count=outcome.failed,
            warning_count=0,
            quota_state={
                "provider": request.provider,
                "incremental_mode": self.settings.sec_document_incremental_mode.value,
                "processor_signature": service.processor_signature,
                "readiness_state": outcome.readiness_state,
                "run_evidence_status": outcome.run_evidence_status,
                "readiness_reason": outcome.readiness_reason,
                **telemetry,
            },
            checkpoint=telemetry,
            errors={"documents": outcome.errors} if outcome.errors else None,
        )
        result = self._result_from_run(finished)
        values = result.as_dict()
        values.update(
            documents_discovered=outcome.documents_discovered,
            documents_downloaded=outcome.documents_downloaded,
            documents_skipped=outcome.documents_skipped,
            documents_would_skip=outcome.documents_would_skip,
            readiness_state=outcome.readiness_state,
            run_evidence_status=outcome.run_evidence_status,
            readiness_reason=outcome.readiness_reason,
        )
        return CeriIngestionResult(**values)

    def _fetch_records(
        self,
        provider: CeriProvider,
        request: CeriIngestionRequest,
    ):
        if request.dataset is CeriDataset.ESTIMATES:
            return provider.fetch_estimate_snapshots(
                EstimateRequest(
                    company_id=None,
                    ticker=request.ticker,
                    metrics=self.config.metrics.required,
                    period_types=self.config.metrics.period_types,
                    start=request.start,
                    end=request.end,
                )
            )
        if request.dataset is CeriDataset.EARNINGS:
            return provider.fetch_earnings_actuals(
                EarningsRequest(
                    company_id=None, ticker=request.ticker, start=request.start, end=request.end
                )
            )
        if request.dataset is CeriDataset.GUIDANCE:
            return provider.fetch_guidance(
                GuidanceRequest(
                    company_id=None, ticker=request.ticker, start=request.start, end=request.end
                )
            )
        if request.dataset is CeriDataset.CATALYSTS:
            return provider.fetch_catalysts(
                CatalystRequest(
                    company_id=None,
                    ticker=request.ticker,
                    categories=tuple(CatalystCategory),
                    start=request.start,
                    end=request.end,
                )
            )
        raise ValueError(f"Unsupported CERI dataset: {request.dataset}")

    def _result_from_run(self, run: CeriIngestionRun) -> CeriIngestionResult:
        telemetry = run.quota_state_json or {}
        return CeriIngestionResult(
            ingestion_run_id=run.id,
            provider=run.provider,
            dataset=run.dataset,
            status=run.status,
            requested=run.requested_count,
            fetched=run.fetched_count,
            inserted=run.inserted_count,
            deduplicated=run.deduplicated_count,
            corrected=run.corrected_count,
            quarantined=run.quarantined_count,
            failed=run.failed_count,
            warnings=run.warning_count,
            documents_discovered=int(telemetry.get("documents_discovered") or 0),
            documents_downloaded=int(telemetry.get("documents_downloaded") or 0),
            documents_skipped=int(telemetry.get("documents_skipped") or 0),
            documents_would_skip=int(telemetry.get("documents_would_skip") or 0),
            readiness_state=telemetry.get("readiness_state"),
            run_evidence_status=telemetry.get("run_evidence_status"),
            readiness_reason=telemetry.get("readiness_reason"),
        )


def _safe_error(
    index: int,
    record: RawProviderRecord,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "record_index": index,
        "provider": record.provider,
        "dataset": record.dataset.value,
        "provider_record_id": record.provider_record_id,
        "error": _safe_message(exc),
    }


def _safe_message(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:500]


def _provider_stats(provider: CeriProvider):
    client = getattr(provider, "client", None)
    stats = getattr(client, "stats", None)
    return stats() if callable(stats) else None


def _record_provider_telemetry(
    db: Session,
    *,
    provider: CeriProvider,
    dataset: str,
    request_key: str,
    scope: dict[str, Any],
    before: Any,
    started: float,
    failed: int,
    response_bytes: int,
    stored_bytes: int,
) -> None:
    after = _provider_stats(provider)
    if after is None:
        return
    before_requests = int(getattr(before, "requests", 0) or 0)
    calls = max(0, int(getattr(after, "requests", 0) or 0) - before_requests)
    if calls == 0:
        return
    scope_hash = hashlib.sha256(repr(sorted(scope.items())).encode("utf-8")).hexdigest()
    db.add(
        CeriProviderRequestTelemetry(
            provider=getattr(provider, "name", "unknown"),
            dataset=dataset,
            endpoint=f"dataset:{dataset}",
            request_key=hashlib.sha256(request_key.encode("utf-8")).hexdigest(),
            scope_hash=scope_hash,
            status_code=503 if failed else 200,
            call_cost=max(
                1,
                int(getattr(after, "calls_used_today", 0) or 0)
                - int(getattr(before, "calls_used_today", 0) or 0),
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            retry_count=max(
                0,
                int(getattr(after, "retries", 0) or 0) - int(getattr(before, "retries", 0) or 0),
            ),
            response_bytes=response_bytes,
            stored_bytes=stored_bytes,
            error_code="PROVIDER_REQUEST_FAILED" if failed else None,
            observed_at=datetime.now(UTC),
        )
    )
