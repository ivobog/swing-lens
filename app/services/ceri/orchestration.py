from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriIngestionRun
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


class CeriIngestionCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class CeriIngestionRequest:
    provider: str
    dataset: CeriDataset
    ticker: str
    request_key: str | None = None
    scope: dict[str, Any] | None = None


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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CeriIngestionService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        registry: CeriProviderRegistry | None = None,
        source_records: CeriSourceRecordService | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.registry = registry or CeriProviderRegistry(config=self.config)
        self.source_records = source_records or CeriSourceRecordService()

    def ingest(
        self,
        db: Session,
        request: CeriIngestionRequest,
        *,
        should_cancel=None,
    ) -> CeriIngestionResult:
        provider = self.registry.get(request.provider)
        dataset_policy = self.registry.dataset_policy(request.dataset.value)
        request_key = request.request_key or self.request_key(request)
        ingestion_run = self.source_records.create_ingestion_run(
            db,
            provider=request.provider,
            provider_terms_version=self.config.retention.provider_terms_version,
            dataset=request.dataset.value,
            scope=request.scope or {"ticker": request.ticker},
            request_key=request_key,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
        )
        if ingestion_run.status in {"COMPLETED", "PARTIAL"}:
            return self._result_from_run(ingestion_run)

        requested = fetched = inserted = deduplicated = corrected = quarantined = failed = 0
        warning_count = 0
        errors: list[dict[str, Any]] = []
        checkpoint: dict[str, Any] = {}

        try:
            for index, record in enumerate(self._fetch_records(provider, request), start=1):
                requested += 1
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
                    deduplicated += int(write.deduplicated)
                    quarantined += int(write.quarantined)
                except Exception as exc:
                    failed += 1
                    errors.append(_safe_error(index, record, exc))
                checkpoint = {"last_record_index": index}
        except CeriIngestionCancelled:
            status = "CANCELLED"
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
        return self._result_from_run(finished)

    def request_key(self, request: CeriIngestionRequest) -> str:
        return f"ceri:{request.provider}:{request.dataset.value}:{request.ticker.upper()}"

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
                )
            )
        if request.dataset is CeriDataset.EARNINGS:
            return provider.fetch_earnings_actuals(
                EarningsRequest(company_id=None, ticker=request.ticker)
            )
        if request.dataset is CeriDataset.GUIDANCE:
            return provider.fetch_guidance(GuidanceRequest(company_id=None, ticker=request.ticker))
        if request.dataset is CeriDataset.CATALYSTS:
            return provider.fetch_catalysts(
                CatalystRequest(
                    company_id=None,
                    ticker=request.ticker,
                    categories=tuple(CatalystCategory),
                )
            )
        raise ValueError(f"Unsupported CERI dataset: {request.dataset}")

    def _result_from_run(self, run: CeriIngestionRun) -> CeriIngestionResult:
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
