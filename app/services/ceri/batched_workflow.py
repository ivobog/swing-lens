from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany
from app.models.tables import RawCompanyRow
from app.services.background_job_service import enqueue_job
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.settings import Settings, get_settings

CERI_PROVIDER_INGEST_BATCH = "CERI_PROVIDER_INGEST_BATCH"
CERI_NORMALIZE_BATCH = "CERI_NORMALIZE_BATCH"
CERI_FEATURE_BATCH = "CERI_FEATURE_BATCH"
CERI_RUN_FINALIZE = "CERI_RUN_FINALIZE"

DEFAULT_PROVIDER_DATASETS: Mapping[str, tuple[CeriDataset, ...]] = {
    "eodhd": (CeriDataset.ESTIMATES, CeriDataset.EARNINGS, CeriDataset.CATALYSTS),
    "sec": (CeriDataset.GUIDANCE,),
}
DATASET_PRIORITIES: Mapping[CeriDataset, int] = {
    CeriDataset.ESTIMATES: 80,
    CeriDataset.EARNINGS: 90,
    CeriDataset.CATALYSTS: 100,
    CeriDataset.GUIDANCE: 120,
}


@dataclass(frozen=True)
class CeriBatchJobSpec:
    job_type: str
    request_key: str
    priority: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class CeriBatchedWorkflowPlan:
    run_id: int
    workflow_key: str
    jobs: tuple[CeriBatchJobSpec, ...]
    provider_batches: int
    normalization_batches: int
    feature_batches: int

    @property
    def initial_job_count(self) -> int:
        return len(self.jobs)

    @property
    def expected_total_job_count(self) -> int:
        return self.initial_job_count + 3


def build_ceri_batched_workflow_plan(
    *,
    run_id: int,
    tickers: Iterable[str],
    config_hash: str,
    settings: Settings | None = None,
    provider_datasets: Mapping[str, tuple[CeriDataset, ...]] = DEFAULT_PROVIDER_DATASETS,
) -> CeriBatchedWorkflowPlan:
    runtime_settings = settings or get_settings()
    symbols = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
    )
    workflow_key = f"ceri:pipeline:{run_id}:{config_hash}"
    jobs: list[CeriBatchJobSpec] = []
    provider_count = 0
    normalization_count = 0

    for provider, datasets in sorted(provider_datasets.items()):
        for dataset in sorted(datasets, key=lambda item: (DATASET_PRIORITIES[item], item.value)):
            priority = DATASET_PRIORITIES[dataset]
            for batch_index, batch in enumerate(
                _chunks(symbols, runtime_settings.ceri_provider_batch_size),
                start=1,
            ):
                request_key = (
                    f"{workflow_key}:provider:{provider}:{dataset.value}:{batch_index:04d}"
                )
                jobs.append(
                    CeriBatchJobSpec(
                        job_type=CERI_PROVIDER_INGEST_BATCH,
                        request_key=request_key,
                        priority=priority,
                        payload={
                            "workflow_key": workflow_key,
                            "request_key": request_key,
                            "run_id": run_id,
                            "provider": provider,
                            "dataset": dataset.value,
                            "tickers": list(batch),
                            "batch_index": batch_index,
                            "checkpoint_interval": runtime_settings.ceri_batch_checkpoint_interval,
                        },
                    )
                )
                provider_count += 1
            for batch_index, batch in enumerate(
                _chunks(symbols, runtime_settings.ceri_normalization_batch_size),
                start=1,
            ):
                request_key = (
                    f"{workflow_key}:normalize:{provider}:{dataset.value}:{batch_index:04d}"
                )
                jobs.append(
                    CeriBatchJobSpec(
                        job_type=CERI_NORMALIZE_BATCH,
                        request_key=request_key,
                        priority=priority + 1,
                        payload={
                            "workflow_key": workflow_key,
                            "request_key": request_key,
                            "run_id": run_id,
                            "provider": provider,
                            "dataset": dataset.value,
                            "tickers": list(batch),
                            "batch_index": batch_index,
                            "checkpoint_interval": runtime_settings.ceri_batch_checkpoint_interval,
                        },
                    )
                )
                normalization_count += 1

    feature_specs: list[CeriBatchJobSpec] = []
    for batch_index, batch in enumerate(
        _chunks(symbols, runtime_settings.ceri_feature_batch_size),
        start=1,
    ):
        request_key = f"{workflow_key}:feature:{batch_index:04d}"
        feature_specs.append(
            CeriBatchJobSpec(
                job_type=CERI_FEATURE_BATCH,
                request_key=request_key,
                priority=130,
                payload={
                    "workflow_key": workflow_key,
                    "request_key": request_key,
                    "run_id": run_id,
                    "tickers": list(batch),
                    "batch_index": batch_index,
                    "expected_normalization_batches": normalization_count,
                    "checkpoint_interval": runtime_settings.ceri_batch_checkpoint_interval,
                },
            )
        )
    jobs.extend(feature_specs)
    finalizer_key = f"{workflow_key}:finalize"
    jobs.append(
        CeriBatchJobSpec(
            job_type=CERI_RUN_FINALIZE,
            request_key=finalizer_key,
            priority=140,
            payload={
                "workflow_key": workflow_key,
                "request_key": finalizer_key,
                "run_id": run_id,
                "expected_feature_batches": len(feature_specs),
            },
        )
    )
    return CeriBatchedWorkflowPlan(
        run_id=run_id,
        workflow_key=workflow_key,
        jobs=tuple(jobs),
        provider_batches=provider_count,
        normalization_batches=normalization_count,
        feature_batches=len(feature_specs),
    )


def schedule_ceri_batched_workflow(db: Session, run_id: int) -> CeriBatchedWorkflowPlan:
    tickers = sorted(
        {
            row.ticker.upper()
            for row in db.scalars(select(RawCompanyRow).where(RawCompanyRow.run_id == run_id))
            if row.ticker
        }
    )
    _ensure_ceri_companies(db, tickers)
    registry = CeriProviderRegistry()
    provider_datasets: dict[str, tuple[CeriDataset, ...]] = {}
    for provider, datasets in DEFAULT_PROVIDER_DATASETS.items():
        try:
            capabilities = registry.capabilities(provider)
        except Exception:
            continue
        provider_datasets[provider] = tuple(
            dataset for dataset in datasets if dataset in capabilities.datasets
        )
    plan = build_ceri_batched_workflow_plan(
        run_id=run_id,
        tickers=tickers,
        config_hash=load_ceri_config().config_hash,
        provider_datasets=provider_datasets,
    )
    for spec in plan.jobs:
        enqueue_job(
            db,
            spec.job_type,
            spec.payload,
            related_run_id=run_id,
            priority=spec.priority,
            request_key=spec.request_key,
            workflow_key=plan.workflow_key,
        )
    db.flush()
    return plan


def _ensure_ceri_companies(db: Session, tickers: Iterable[str]) -> None:
    existing = {
        (company.ticker.upper(), (company.exchange or "").upper())
        for company in db.scalars(select(CeriCompany))
    }
    for ticker in tickers:
        symbol = ticker.upper()
        if (symbol, "US") in existing:
            continue
        db.add(
            CeriCompany(
                ticker=symbol,
                exchange="US",
                current_provider_ids_json={"eodhd": f"{symbol}.US"},
            )
        )
        existing.add((symbol, "US"))
    db.flush()


def _chunks(values: tuple[str, ...], size: int) -> tuple[tuple[str, ...], ...]:
    return tuple(values[index : index + size] for index in range(0, len(values), size))
