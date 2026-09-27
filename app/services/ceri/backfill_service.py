from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
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
from app.services.redaction import redact_text


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
    refresh_cycle_key: str | None = None
    semantic_authority: Any | None = None


@dataclass(frozen=True)
class CeriBackfillResult:
    processing_run_id: int | None
    status: str
    checkpoints: dict[str, Any]
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "processing_run_id": self.processing_run_id,
            "status": self.status,
            "checkpoints": self.checkpoints,
            "skipped": self.skipped,
            "failed": self.failed,
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
        if request.provider == "sec" and (request.start is None or request.end is None):
            raise ValueError(
                "SEC historical backfill requires explicit bounded start and end dates"
            )
        tickers = tuple(
            dict.fromkeys(
                ticker.upper()
                for ticker in (request.tickers or ((request.ticker,) if request.ticker else ()))
            )
        )
        if not tickers and request.provider == "eodhd":
            if isinstance(db, Session):
                tickers = tuple(
                    str(row.ticker).upper()
                    for row in db.execute(
                        select(CeriCompany.id, CeriCompany.ticker)
                        .where(CeriCompany.ticker.is_not(None))
                        .order_by(CeriCompany.ticker, CeriCompany.id)
                        .execution_options(yield_per=self.config.backfill.company_batch_size)
                    )
                )
            else:
                tickers = tuple(
                    company.ticker.upper()
                    for company in sorted(
                        _fixture_companies(db),
                        key=lambda company: (company.ticker.upper(), company.id or 0),
                    )
                    if company.ticker
                )
        authority = request.semantic_authority
        processing_request_key = self.request_key(request)
        if isinstance(db, Session) and authority is None:
            from app.services.canonical_evidence import CanonicalEvidenceSerializer
            from app.services.scope_refresh_adoption import admit_frozen_operation
            from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

            cutoff = request.end or datetime.now(UTC).date()
            cycle_key = request.refresh_cycle_key or (
                f"{processing_request_key}:session:{cutoff.isoformat()}"
            )
            authority = admit_frozen_operation(
                db,
                operation_kind="ceri-backfill",
                subject_kind="ticker",
                members=tuple(ScopeMember("TICKER", ticker) for ticker in tickers),
                cycle_key=cycle_key,
                business_cutoff=cutoff,
                provider_source_class=request.provider,
                request_type=request.dataset,
                requirements=(AcquisitionRequirement(request.dataset),),
                policy_identity=CanonicalEvidenceSerializer.fingerprint(
                    {
                        "provider": request.provider,
                        "dataset": request.dataset,
                        "mode": request.mode,
                        "config_hash": self.config.config_hash,
                    }
                ),
                scope_definition={
                    "window_start": request.start,
                    "window_end": request.end,
                    "mode": request.mode,
                },
                refresh_reason="CERI_BACKFILL_ADMISSION",
            )
        if authority is not None:
            refresh_suffix = f":refresh:{authority.refresh_cycle_id}"
            if not processing_request_key.endswith(refresh_suffix):
                processing_request_key = f"{processing_request_key}{refresh_suffix}"
        run, created = self.processing_runs.create_or_get(
            db,
            job_type="CERI_BACKFILL",
            request_key=processing_request_key,
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
            semantic_authority=authority,
        )
        if not created and run.status == "COMPLETED":
            return CeriBackfillResult(
                processing_run_id=run.id,
                status=run.status,
                checkpoints=run.checkpoint_json or {},
                skipped=1,
            )
        checkpoint = dict(run.checkpoint_json or {})
        completed_tickers = {
            str(value).upper() for value in checkpoint.get("completed_tickers", [])
        }
        failed_by_ticker = {
            str(item.get("ticker", "")).upper(): {
                "ticker": str(item.get("ticker", "")).upper(),
                "error": redact_text(str(item.get("error", "")))[:300],
                "attempts": int(item.get("attempts", 1) or 1),
            }
            for item in checkpoint.get("failed_tickers", [])
            if isinstance(item, dict) and str(item.get("ticker", "")).strip()
        }
        checkpoint.update(
            {
                "provider_page": checkpoint.get("provider_page", 0),
                "ticker": request.ticker,
                "mode": request.mode,
                "last_ticker_index": checkpoint.get("last_ticker_index", -1),
                "completed_tickers": sorted(completed_tickers),
                "failed_tickers": list(failed_by_ticker.values()),
            }
        )
        if callable(getattr(db, "scalars", None)) and tickers:
            ingestion = CeriIngestionService(config=self.config)
            normalizer = CeriNormalizationService()
            feature_rebuild = CeriFeatureRebuildService()
            index_by_ticker = {ticker: index for index, ticker in enumerate(tickers)}
            retry_tickers = [
                ticker
                for ticker in tickers
                if ticker in failed_by_ticker and ticker not in completed_tickers
            ]
            start_index = int(checkpoint["last_ticker_index"]) + 1
            new_tickers = [
                ticker
                for ticker in tickers[start_index:]
                if ticker not in completed_tickers and ticker not in failed_by_ticker
            ]
            batch = tuple(dict.fromkeys(retry_tickers + new_tickers))[
                : self.config.backfill.company_batch_size
            ]
            checkpoint["batch_tickers"] = list(batch)
            for ticker in batch:
                index = index_by_ticker[ticker]
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
                            semantic_authority=authority,
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
                            semantic_authority=authority,
                        )
                        normalizer.normalize(
                            db,
                            processing_run=normalization_run,
                            ingestion_run_id=result.ingestion_run_id,
                        )
                    feature_rebuild.rebuild(
                        db,
                        CeriFeatureRebuildRequest(
                            ticker=ticker,
                            mode=request.mode,
                            cutoff_at=datetime.combine(
                                request.end or datetime.now(UTC).date(),
                                datetime.max.time(),
                                tzinfo=UTC,
                            ),
                            semantic_authority=authority,
                        ),
                        processing_run=run,
                    )
                    completed_tickers.add(ticker)
                    failed_by_ticker.pop(ticker, None)
                    checkpoint["completed_tickers"] = sorted(completed_tickers)
                    checkpoint["failed_tickers"] = list(failed_by_ticker.values())
                    checkpoint["last_ticker_index"] = index
                    run.checkpoint_json = checkpoint
                except Exception as exc:
                    prior_failure = failed_by_ticker.get(ticker, {})
                    failed_by_ticker[ticker] = {
                        "ticker": ticker,
                        "error": redact_text(str(exc)).replace("\n", " ")[:300],
                        "attempts": int(prior_failure.get("attempts", 0) or 0) + 1,
                    }
                    checkpoint["failed_tickers"] = list(failed_by_ticker.values())
                    checkpoint["last_ticker_index"] = index
                    run.checkpoint_json = checkpoint
            checkpoint["completed_tickers"] = sorted(completed_tickers)
            checkpoint["failed_tickers"] = list(failed_by_ticker.values())
            checkpoint["next_ticker_index"] = next(
                (index for index, ticker in enumerate(tickers) if ticker not in completed_tickers),
                len(tickers),
            )
        checkpoint["resumable"] = bool(
            callable(getattr(db, "scalars", None))
            and tickers
            and len(completed_tickers) < len(tickers)
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
            failed=len(failed_by_ticker),
        )


def _fixture_companies(db: Session) -> list[CeriCompany]:
    if isinstance(db, Session):
        raise TypeError("fixture-only company loading cannot run against production")
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(CeriCompany))
    return list(result.all() if hasattr(result, "all") else result)
