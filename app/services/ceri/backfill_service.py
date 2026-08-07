from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.processing_run_service import CeriProcessingRunService


@dataclass(frozen=True)
class CeriBackfillRequest:
    provider: str
    dataset: str
    ticker: str | None = None
    start: date | None = None
    end: date | None = None
    mode: str = "AS_KNOWN"
    actor: str | None = None
    tickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CeriBackfillResult:
    processing_run_id: int | None
    status: str
    checkpoints: dict[str, Any]
    skipped: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "processing_run_id": self.processing_run_id,
            "status": self.status,
            "checkpoints": self.checkpoints,
            "skipped": self.skipped,
        }


class CeriBackfillService:
    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        processing_runs: CeriProcessingRunService | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.processing_runs = processing_runs or CeriProcessingRunService()

    def request_key(self, request: CeriBackfillRequest) -> str:
        return ":".join(
            [
                "ceri",
                "backfill",
                request.provider,
                request.dataset,
                request.ticker or "*",
                ",".join(sorted(request.tickers)) or "*",
                request.start.isoformat() if request.start else "*",
                request.end.isoformat() if request.end else "*",
                request.mode,
                self.config.engine.config_version,
            ]
        )

    def run(self, db: Session, request: CeriBackfillRequest) -> CeriBackfillResult:
        run, created = self.processing_runs.create_or_get(
            db,
            job_type="CERI_BACKFILL",
            request_key=self.request_key(request),
            scope={
                "provider": request.provider,
                "dataset": request.dataset,
                "ticker": request.ticker,
                "start": request.start.isoformat() if request.start else None,
                "end": request.end.isoformat() if request.end else None,
                "mode": request.mode,
            },
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            actor=request.actor,
        )
        if not created and run.status == "COMPLETED":
            return CeriBackfillResult(
                processing_run_id=run.id,
                status=run.status,
                checkpoints=run.checkpoint_json or {},
                skipped=1,
            )
        tickers = tuple(
            dict.fromkeys(
                ticker.upper()
                for ticker in (request.tickers or ((request.ticker,) if request.ticker else ()))
            )
        )
        if not tickers and request.provider == "eodhd":
            tickers = tuple(
                company.ticker.upper()
                for company in sorted(
                    _load(db, CeriCompany),
                    key=lambda company: (company.ticker.upper(), company.id or 0),
                )
                if company.ticker
            )
        checkpoint = dict(run.checkpoint_json or {})
        checkpoint.update(
            {
                "provider_page": checkpoint.get("provider_page", 0),
                "ticker": request.ticker,
                "mode": request.mode,
                "last_ticker_index": checkpoint.get("last_ticker_index", -1),
                "completed_tickers": checkpoint.get("completed_tickers", []),
                "failed_tickers": checkpoint.get("failed_tickers", []),
            }
        )
        if callable(getattr(db, "scalars", None)) and tickers:
            ingestion = CeriIngestionService(config=self.config)
            normalizer = CeriNormalizationService()
            feature_rebuild = CeriFeatureRebuildService()
            start_index = int(checkpoint["last_ticker_index"]) + 1
            batch = tickers[start_index : start_index + self.config.backfill.company_batch_size]
            for index, ticker in enumerate(batch, start=start_index):
                try:
                    result = ingestion.ingest(
                        db,
                        CeriIngestionRequest(
                            provider=request.provider,
                            dataset=CeriDataset(request.dataset),
                            ticker=ticker,
                            request_key=f"{self.request_key(request)}:{ticker}",
                            start=request.start,
                            end=request.end,
                            scope={"ticker": ticker, "backfill": True},
                        ),
                    )
                    if result.ingestion_run_id:
                        normalization_run, _ = self.processing_runs.create_or_get(
                            db,
                            job_type="CERI_NORMALIZE",
                            request_key=f"{self.request_key(request)}:normalize:{ticker}",
                            scope={"ticker": ticker, "backfill": True},
                            config_version=self.config.engine.config_version,
                            config_hash=self.config.config_hash,
                            actor=request.actor,
                        )
                        normalizer.normalize(
                            db,
                            processing_run=normalization_run,
                            ingestion_run_id=result.ingestion_run_id,
                        )
                    feature_rebuild.rebuild(
                        db,
                        CeriFeatureRebuildRequest(ticker=ticker, mode=request.mode),
                        processing_run=run,
                    )
                    checkpoint["completed_tickers"] = [*checkpoint["completed_tickers"], ticker]
                    checkpoint["last_ticker_index"] = index
                    run.checkpoint_json = checkpoint
                except Exception as exc:
                    checkpoint["failed_tickers"] = [
                        *checkpoint["failed_tickers"],
                        {"ticker": ticker, "error": str(exc)[:300]},
                    ]
                    checkpoint["last_ticker_index"] = index
                    run.checkpoint_json = checkpoint
        checkpoint["resumable"] = bool(
            callable(getattr(db, "scalars", None))
            and tickers
            and len(checkpoint.get("completed_tickers", [])) < len(tickers)
        )
        final_status = (
            "PARTIAL" if checkpoint["resumable"] or checkpoint["failed_tickers"] else "COMPLETED"
        )
        self.processing_runs.finish(
            db,
            run,
            status=final_status,
            counts={"read": 0, "features": 0, "warnings": 0, "failed": 0},
            checkpoint=checkpoint,
        )
        return CeriBackfillResult(
            processing_run_id=run.id,
            status=run.status,
            checkpoints=checkpoint,
        )


def _load(db: Session, model: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
