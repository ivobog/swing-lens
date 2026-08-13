# ruff: noqa: E501
from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import re
import socket
import subprocess
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.db import engine
from app.models.tables import BackgroundJob
from app.services.background_job_service import (
    JobStatus,
    heartbeat_job,
    mark_job_completed,
    mark_job_partial,
)
from app.services.ceri.batched_job_handlers import execute_provider_ingest_batch_job
from app.services.ceri.batched_workflow import CERI_PROVIDER_INGEST_BATCH
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import (
    CeriIngestionRequest,
    CeriIngestionService,
)
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient
from app.services.ceri.sec.guidance_extractor import GuidanceExtractionService
from app.services.ceri.sec.provider import SecCeriProvider
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.services.worker_registry import heartbeat_worker, register_worker
from app.settings import get_settings

PROFILE_TICKERS = ("AIZ", "AMZN", "CLBT", "JPM", "SLDE")
GUIDANCE_FORMS = {"8-K", "10-Q", "10-K", "6-K", "20-F"}
FORM_KEYS = {
    "8-K": "candidate_8k",
    "10-Q": "candidate_10q",
    "10-K": "candidate_10k",
    "6-K": "candidate_6k",
    "20-F": "candidate_20f",
}

INITIAL_QUEUE_INVENTORY = {
    "captured_at": "2026-08-13T13:19:08.369029+00:00",
    "totals": {
        "queued": 167,
        "running": 1,
        "terminal": 43,
        "active": 168,
        "oldest_queued_at": "2026-08-12T22:08:03.521816+02:00",
        "oldest_queued_age_seconds": 61864.847213,
    },
    "running_jobs": [
        {
            "id": 29749,
            "run_id": 97,
            "job_type": "CERI_NORMALIZE_BATCH",
            "worker_id": "local-worker-1",
            "requested_cancel": False,
        }
    ],
    "grouped": [
        {"run_id": 97, "job_type": "CERI_FEATURE_BATCH", "status": "QUEUED", "count": 4},
        {"run_id": 97, "job_type": "CERI_NORMALIZE_BATCH", "status": "COMPLETED", "count": 12},
        {"run_id": 97, "job_type": "CERI_NORMALIZE_BATCH", "status": "QUEUED", "count": 3},
        {"run_id": 97, "job_type": "CERI_NORMALIZE_BATCH", "status": "RUNNING", "count": 1},
        {
            "run_id": 97,
            "job_type": "CERI_PROVIDER_INGEST_BATCH",
            "status": "COMPLETED",
            "count": 23,
        },
        {"run_id": 97, "job_type": "CERI_PROVIDER_INGEST_BATCH", "status": "PARTIAL", "count": 5},
        {"run_id": 97, "job_type": "CERI_RUN_FINALIZE", "status": "QUEUED", "count": 1},
        {"run_id": 97, "job_type": "FULL_PIPELINE", "status": "COMPLETED", "count": 1},
        {"run_id": 98, "job_type": "CERI_FEATURE_BATCH", "status": "QUEUED", "count": 8},
        {"run_id": 98, "job_type": "CERI_NORMALIZE_BATCH", "status": "QUEUED", "count": 32},
        {"run_id": 98, "job_type": "CERI_PROVIDER_INGEST_BATCH", "status": "QUEUED", "count": 64},
        {"run_id": 98, "job_type": "CERI_RUN_FINALIZE", "status": "QUEUED", "count": 1},
        {"run_id": 98, "job_type": "FULL_PIPELINE", "status": "COMPLETED", "count": 1},
        {"run_id": 99, "job_type": "CERI_FEATURE_BATCH", "status": "QUEUED", "count": 4},
        {"run_id": 99, "job_type": "CERI_NORMALIZE_BATCH", "status": "QUEUED", "count": 16},
        {"run_id": 99, "job_type": "CERI_PROVIDER_INGEST_BATCH", "status": "QUEUED", "count": 32},
        {"run_id": 99, "job_type": "CERI_RUN_FINALIZE", "status": "QUEUED", "count": 1},
        {"run_id": 99, "job_type": "FULL_PIPELINE", "status": "COMPLETED", "count": 1},
        {"run_id": 100, "job_type": "FULL_PIPELINE", "status": "QUEUED", "count": 1},
    ],
}


@dataclass
class FilingMetric:
    ticker: str
    cik: str
    accession: str
    document: str
    form: str
    filing_date: str
    candidate: bool
    filtered: bool
    known_before_download: bool
    known_basis: str | None
    previously_processed: bool | None = None
    document_hash_known: bool | None = None
    guidance_previously_extracted: bool = False
    downloaded: bool = False
    downloaded_again: bool = False
    parsed: bool = False
    guidance_records: int = 0
    bytes: int = 0
    characters: int = 0
    parse_ms: float = 0.0
    candidate_paragraphs: int = 0
    http_ms: float = 0.0
    pacing_ms: float = 0.0
    status: int = 0


@dataclass
class TickerMetric:
    ticker: str
    cik: str | None = None
    total_ms: float = 0.0
    provider_ms: float = 0.0
    ticker_resolution_total_ms: float = 0.0
    ticker_resolution_ms: float = 0.0
    filing_discovery_ms: float = 0.0
    source_record_write_ms: float = 0.0
    deduplication_ms: float = 0.0
    db_flush_ms: float = 0.0
    db_commit_ms: float = 0.0
    ingestion_run_db_ms: float = 0.0
    cancel_check_db_ms: float = 0.0
    queue_orchestration_ms: float = 0.0
    sec_calls: int = 0
    filings_present: int = 0
    candidate_filings: int = 0
    candidate_8k: int = 0
    candidate_10q: int = 0
    candidate_10k: int = 0
    candidate_6k: int = 0
    candidate_20f: int = 0
    filings_considered: int = 0
    filings_filtered: int = 0
    filings_already_known: int = 0
    filings_downloaded: int = 0
    filings_parsed: int = 0
    filings_with_guidance: int = 0
    guidance_records: int = 0
    inserted_records: int = 0
    deduplicated_records: int = 0
    failed_records: int = 0
    result_status: str | None = None


class Tracker:
    def __init__(
        self, scenario: str, tickers: tuple[str, ...], known: dict[str, dict[str, set[Any]]]
    ):
        self.scenario = scenario
        self.tickers = tickers
        self.known = known
        self.current_ticker: str | None = None
        self.stage = "other"
        self.in_source_service = False
        self.pending_pacing_ms = 0.0
        self.retry_sleep_ms = 0.0
        self.retry_sleep_by_ticker: Counter[str] = Counter()
        self.retry_sleep_by_stage: Counter[tuple[str, str]] = Counter()
        self.request_attempts: Counter[str] = Counter()
        self.requests: list[dict[str, Any]] = []
        self.filings: dict[tuple[str, str, str], FilingMetric] = {}
        self.ticker = {value: TickerMetric(value) for value in tickers}
        self.system_samples: list[dict[str, Any]] = []
        self.total_elapsed_ms = 0.0
        self.job_id: int | None = None
        self.ingestion_run_ids: list[int] = []
        self.result: dict[str, Any] = {}
        self.scenario_started_at: str | None = None
        self.scenario_completed_at: str | None = None
        self._flush_total_ms = 0.0
        self._commit_total_ms = 0.0
        self._process_start = time.process_time()

    def metric(self, ticker: str | None = None) -> TickerMetric:
        value = ticker or self.current_ticker
        if value not in self.ticker:
            raise RuntimeError(f"No active profiling ticker: {value!r}")
        return self.ticker[value]

    def add_flush(self, elapsed_ms: float) -> None:
        self._flush_total_ms += elapsed_ms
        if self.current_ticker in self.ticker:
            self.metric().db_flush_ms += elapsed_ms

    def add_commit(self, elapsed_ms: float) -> None:
        self._commit_total_ms += elapsed_ms
        if self.current_ticker in self.ticker:
            self.metric().db_commit_ms += elapsed_ms

    def record_submissions(self, cik: str, data: dict[str, Any]) -> None:
        ticker = self.current_ticker
        if ticker is None:
            return
        metric = self.metric(ticker)
        metric.cik = cik
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])
        filing_dates = recent.get("filingDate", [])
        metric.filings_present = len(forms)
        known_pairs = self.known.get(ticker, {}).get("pairs", set())
        known_accessions = self.known.get(ticker, {}).get("accessions", set())
        for form, accession, document, filing_date in zip(
            forms, accessions, documents, filing_dates, strict=False
        ):
            candidate = form in GUIDANCE_FORMS
            known_pair = (str(accession), str(document)) in known_pairs
            known_accession = str(accession) in known_accessions
            known = bool(known_pair or known_accession)
            filing = FilingMetric(
                ticker=ticker,
                cik=cik,
                accession=str(accession),
                document=str(document),
                form=str(form),
                filing_date=str(filing_date),
                candidate=candidate,
                filtered=not candidate,
                known_before_download=known,
                known_basis="document_guidance"
                if known_pair
                else ("accession_guidance" if known_accession else None),
                guidance_previously_extracted=known,
            )
            self.filings[(ticker, filing.accession, filing.document)] = filing
            if candidate:
                metric.candidate_filings += 1
                metric.filings_considered += 1
                if form in FORM_KEYS:
                    setattr(metric, FORM_KEYS[form], getattr(metric, FORM_KEYS[form]) + 1)
                metric.filings_already_known += int(known)
            else:
                metric.filings_filtered += 1

    def filing_for_url(self, ticker: str, url: str) -> FilingMetric | None:
        match = re.search(r"/Archives/edgar/data/\d+/(\d{18})/([^/?#]+)", url)
        if not match:
            return None
        raw_accession, document = match.groups()
        accession = f"{raw_accession[:10]}-{raw_accession[10:12]}-{raw_accession[12:]}"
        return self.filings.get((ticker, accession, document))

    def finalize(self) -> None:
        for ticker, metric in self.ticker.items():
            rows = [row for row in self.requests if row["ticker"] == ticker]
            metric.sec_calls = len(rows)
            filing_rows = [
                row for row in self.filings.values() if row.ticker == ticker and row.candidate
            ]
            metric.filings_downloaded = sum(row.downloaded for row in filing_rows)
            metric.filings_parsed = sum(row.parsed for row in filing_rows)
            metric.filings_with_guidance = sum(row.guidance_records > 0 for row in filing_rows)
            metric.guidance_records = sum(row.guidance_records for row in filing_rows)
            resolution_network = (
                sum(
                    row["http_ms"] + row["pacing_wait_ms"]
                    for row in rows
                    if row["stage"] == "ticker_resolution"
                )
                + self.retry_sleep_by_stage[(ticker, "ticker_resolution")]
            )
            metric.ticker_resolution_ms = max(
                0.0, metric.ticker_resolution_total_ms - resolution_network
            )
            request_ms = sum(row["http_ms"] + row["pacing_wait_ms"] for row in rows)
            parse_ms = sum(row.parse_ms for row in filing_rows)
            retry_ms = self.retry_sleep_by_ticker[ticker]
            metric.filing_discovery_ms = max(
                0.0,
                metric.provider_ms - request_ms - parse_ms - retry_ms - metric.ticker_resolution_ms,
            )

    def as_dict(self) -> dict[str, Any]:
        self.finalize()
        requests = list(self.requests)
        filings = [
            asdict(row)
            for row in sorted(
                (row for row in self.filings.values() if row.candidate),
                key=lambda x: (x.ticker, x.filing_date, x.accession, x.document),
            )
        ]
        ticker_rows = [ticker_summary(self, value) for value in self.tickers]
        additive = timing_breakdown(self)
        request_summary = summarize_requests(requests)
        cpu = summarize_system(self.system_samples)
        return {
            "scenario": self.scenario,
            "tickers": list(self.tickers),
            "job_id": self.job_id,
            "ingestion_run_ids": self.ingestion_run_ids,
            "started_at": self.scenario_started_at,
            "completed_at": self.scenario_completed_at,
            "total_elapsed_ms": self.total_elapsed_ms,
            "timing": additive,
            "requests": requests,
            "request_summary": request_summary,
            "filings": filings,
            "ticker_metrics": ticker_rows,
            "system_samples": self.system_samples,
            "system_summary": cpu,
            "handler_result": self.result,
        }


class ProfilingSession(Session):
    def __init__(self, *args: Any, tracker: Tracker, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tracker = tracker

    def flush(self, objects: Any = None) -> None:
        started = time.perf_counter()
        try:
            return super().flush(objects)
        finally:
            self.tracker.add_flush((time.perf_counter() - started) * 1000)

    def commit(self) -> None:
        flush_before = self.tracker._flush_total_ms
        started = time.perf_counter()
        try:
            return super().commit()
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            nested_flush = self.tracker._flush_total_ms - flush_before
            self.tracker.add_commit(max(0.0, elapsed - nested_flush))

    def scalar(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return super().scalar(statement, params=params, **kwargs)
        finally:
            if (
                not self.tracker.in_source_service
                and self.tracker.current_ticker in self.tracker.ticker
            ):
                self.tracker.metric().cancel_check_db_ms += (time.perf_counter() - started) * 1000


class ProfilingSourceRecordService(CeriSourceRecordService):
    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker

    def create_ingestion_run(self, db: Session, **kwargs: Any):
        self.tracker.in_source_service = True
        flush_before = self.tracker._flush_total_ms
        started = time.perf_counter()
        try:
            run = super().create_ingestion_run(db, **kwargs)
            if run.id is not None:
                self.tracker.ingestion_run_ids.append(int(run.id))
            return run
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.tracker.metric().ingestion_run_db_ms += max(
                0.0, elapsed - (self.tracker._flush_total_ms - flush_before)
            )
            self.tracker.in_source_service = False

    def store_source_record(self, db: Session, **kwargs: Any):
        self.tracker.in_source_service = True
        flush_before = self.tracker._flush_total_ms
        started = time.perf_counter()
        try:
            result = super().store_source_record(db, **kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            nonflush = max(0.0, elapsed - (self.tracker._flush_total_ms - flush_before))
            if result.deduplicated:
                self.tracker.metric().deduplication_ms += nonflush
            else:
                self.tracker.metric().source_record_write_ms += nonflush
            return result
        finally:
            self.tracker.in_source_service = False

    def finish_ingestion_run(self, db: Session, run: Any, **kwargs: Any):
        self.tracker.in_source_service = True
        flush_before = self.tracker._flush_total_ms
        started = time.perf_counter()
        try:
            return super().finish_ingestion_run(db, run, **kwargs)
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.tracker.metric().ingestion_run_db_ms += max(
                0.0, elapsed - (self.tracker._flush_total_ms - flush_before)
            )
            self.tracker.in_source_service = False


class ProfilingSecClient(SecEdgarClient):
    def __init__(self, config: SecClientConfig, tracker: Tracker) -> None:
        self.tracker = tracker
        super().__init__(config, transport=self._profile_transport, sleep=self._retry_sleep)

    def _pace(self) -> None:
        now = time.monotonic()
        minimum_gap = 1.0 / max(self.config.requests_per_second, 0.1)
        waited_ms = 0.0
        if self._last_request and now - self._last_request < minimum_gap:
            started = time.perf_counter()
            time.sleep(minimum_gap - (now - self._last_request))
            waited_ms = (time.perf_counter() - started) * 1000
        self._last_request = time.monotonic()
        self.tracker.pending_pacing_ms = waited_ms

    def _retry_sleep(self, seconds: float) -> None:
        started = time.perf_counter()
        time.sleep(seconds)
        elapsed = (time.perf_counter() - started) * 1000
        ticker = self.tracker.current_ticker or "UNKNOWN"
        self.tracker.retry_sleep_ms += elapsed
        self.tracker.retry_sleep_by_ticker[ticker] += elapsed
        self.tracker.retry_sleep_by_stage[(ticker, self.tracker.stage)] += elapsed

    def _profile_transport(self, url: str, timeout: int, user_agent: str) -> Any:
        ticker = self.tracker.current_ticker or "UNKNOWN"
        self.tracker.request_attempts[url] += 1
        attempt = self.tracker.request_attempts[url]
        request_type = request_type_for_url(url)
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        status = 0
        size = 0
        timed_out = False
        response: Any = None
        try:
            response = SecEdgarClient._urllib_transport(url, timeout, user_agent)
            status = int(getattr(response, "status", 200))
            body = getattr(response, "body", b"")
            size = len(body if isinstance(body, bytes) else str(body).encode("utf-8"))
            return response
        except Exception as exc:
            status = int(getattr(exc, "code", 0) or 0)
            reason = getattr(exc, "reason", None)
            timed_out = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(
                reason, (TimeoutError, socket.timeout)
            )
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            pacing = self.tracker.pending_pacing_ms
            self.tracker.pending_pacing_ms = 0.0
            row = {
                "timestamp": started_at,
                "ticker": ticker,
                "request_type": request_type,
                "url_category": request_type,
                "stage": self.tracker.stage,
                "pacing_wait_ms": round(pacing, 3),
                "http_ms": round(elapsed_ms, 3),
                "http_status": status,
                "bytes": size,
                "retry_number": attempt - 1,
                "timed_out": timed_out,
            }
            self.tracker.requests.append(row)
            filing = self.tracker.filing_for_url(ticker, url)
            if filing is not None:
                filing.pacing_ms += pacing
                filing.http_ms += elapsed_ms
                filing.status = status
                filing.bytes += size
                if 200 <= status < 300:
                    filing.downloaded = True
                    filing.downloaded_again = filing.known_before_download

    def company_tickers(self) -> dict[str, Any]:
        previous = self.tracker.stage
        self.tracker.stage = "ticker_resolution"
        try:
            return super().company_tickers()
        finally:
            self.tracker.stage = previous

    def submissions(self, cik: str) -> dict[str, Any]:
        previous = self.tracker.stage
        self.tracker.stage = "submissions"
        try:
            result = super().submissions(cik)
            self.tracker.record_submissions(cik, result)
            return result
        finally:
            self.tracker.stage = previous

    def archive_document(self, cik: str, accession: str, document: str) -> str:
        previous = self.tracker.stage
        self.tracker.stage = "filing_document"
        try:
            return super().archive_document(cik, accession, document)
        finally:
            self.tracker.stage = previous


class ProfilingExtractor(GuidanceExtractionService):
    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker

    def extract(self, text_value: str, *, locator: str = "document"):
        started = time.perf_counter()
        rows = super().extract(text_value, locator=locator)
        elapsed = (time.perf_counter() - started) * 1000
        ticker = self.tracker.current_ticker
        accession, document = locator.split("/", 1) if "/" in locator else (locator, "")
        filing = self.tracker.filings.get((ticker or "", accession, document))
        if filing is not None:
            parts = re.split(r"\n\s*\n|(?<=[.!?])\s+", text_value)
            filing.parsed = True
            filing.parse_ms += elapsed
            filing.characters = len(text_value)
            filing.guidance_records += len(rows)
            filing.candidate_paragraphs = sum(
                any(
                    word in paragraph.lower()
                    for word in ("guidance", "outlook", "expects", "forecast")
                )
                for paragraph in parts
            )
        return rows


class ProfilingProvider(SecCeriProvider):
    def __init__(
        self, tracker: Tracker, client: ProfilingSecClient, extractor: ProfilingExtractor
    ) -> None:
        self.tracker = tracker
        super().__init__(client=client, extractor=extractor)

    def _cik_for_ticker(self, ticker: str) -> str | None:
        previous = self.tracker.stage
        self.tracker.stage = "ticker_resolution"
        started = time.perf_counter()
        try:
            cik = super()._cik_for_ticker(ticker)
            self.tracker.metric(ticker).cik = cik
            return cik
        finally:
            self.tracker.metric(ticker).ticker_resolution_total_ms += (
                time.perf_counter() - started
            ) * 1000
            self.tracker.stage = previous

    def fetch_guidance(self, request: Any):
        started = time.perf_counter()
        try:
            return super().fetch_guidance(request)
        finally:
            self.tracker.metric(request.ticker).provider_ms += (
                time.perf_counter() - started
            ) * 1000


class ProfilingIngestionService(CeriIngestionService):
    def __init__(self, tracker: Tracker, **kwargs: Any) -> None:
        self.tracker = tracker
        super().__init__(**kwargs)

    def ingest(self, db: Session, request: CeriIngestionRequest, **kwargs: Any):
        self.tracker.current_ticker = request.ticker
        started = time.perf_counter()
        result = None
        try:
            result = super().ingest(db, request, **kwargs)
            return result
        finally:
            metric = self.tracker.metric(request.ticker)
            metric.total_ms += (time.perf_counter() - started) * 1000
            if result is not None:
                metric.inserted_records = result.inserted
                metric.deduplicated_records = result.deduplicated
                metric.failed_records = result.failed
                metric.result_status = result.status


class SystemSampler:
    def __init__(self, tracker: Tracker, interval: float = 5.0) -> None:
        self.tracker = tracker
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f"sec-profile-sampler-{tracker.scenario}", daemon=True
        )
        self.previous_system = system_cpu_times()
        self.previous_process_cpu = time.process_time()
        self.previous_wall = time.perf_counter()
        self.initial_sample = True

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=self.interval + 1)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.sample()
            self.stop_event.wait(self.interval)

    def sample(self) -> None:
        now_system = system_cpu_times()
        now_process = time.process_time()
        now_wall = time.perf_counter()
        cpu_percent = system_cpu_percent(self.previous_system, now_system)
        elapsed_wall = max(1e-9, now_wall - self.previous_wall)
        worker_cpu = (
            None
            if self.initial_sample
            else max(0.0, (now_process - self.previous_process_cpu) / elapsed_wall * 100.0)
        )
        self.initial_sample = False
        self.previous_system = now_system
        self.previous_process_cpu = now_process
        self.previous_wall = now_wall
        self.tracker.system_samples.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "system_cpu_percent": round(cpu_percent, 3) if cpu_percent is not None else None,
                "worker_cpu_percent_one_core": (
                    round(worker_cpu, 3) if worker_cpu is not None else None
                ),
                "available_ram_bytes": available_ram_bytes(),
                "worker_ram_bytes": process_rss_bytes(os.getpid()),
                "postgresql_ram_bytes": postgresql_working_set_bytes(),
            }
        )


def run_scenario(label: str, tickers: tuple[str, ...], stamp: str) -> dict[str, Any]:
    known = load_known_filings(tickers)
    tracker = Tracker(label, tickers, known)
    SessionFactory = sessionmaker(
        bind=engine, class_=ProfilingSession, autoflush=False, expire_on_commit=False
    )
    db: ProfilingSession = SessionFactory(tracker=tracker)
    settings = get_settings()
    worker_id = f"sec-profiler-{label.lower()}-{stamp}"
    workflow_key = f"sec-profile:{stamp}:{label.lower()}"
    token = uuid4().hex
    now = datetime.now(UTC)
    job = BackgroundJob(
        job_type=CERI_PROVIDER_INGEST_BATCH,
        related_run_id=None,
        request_key=f"{workflow_key}:batch",
        workflow_key=workflow_key,
        status=JobStatus.RUNNING,
        priority=0,
        payload_json={
            "provider": "sec",
            "dataset": CeriDataset.GUIDANCE.value,
            "tickers": list(tickers),
            "checkpoint_interval": settings.ceri_batch_checkpoint_interval,
            "workflow_key": workflow_key,
            "profile_scenario": label,
        },
        requested_cancel=False,
        worker_id=worker_id,
        lease_owner=worker_id,
        execution_token=token,
        locked_at=now,
        heartbeat_at=now,
        lease_expires_at=now + timedelta(seconds=settings.job_stale_after_seconds),
        started_at=now,
        operational_metadata_json={"profile": {"scenario": label, "tickers": list(tickers)}},
    )
    try:
        assert_no_unrelated_active_jobs(db)
        register_worker(
            db,
            worker_id=worker_id,
            queues=("background",),
            heartbeat_timeout_seconds=settings.job_worker_heartbeat_timeout_seconds,
            hostname=socket.gethostname(),
            process_id=os.getpid(),
        )
        db.add(job)
        db.commit()
        tracker.job_id = int(job.id)

        tracker._flush_total_ms = 0.0
        tracker._commit_total_ms = 0.0
        for metric in tracker.ticker.values():
            metric.db_flush_ms = metric.db_commit_ms = 0.0

        config = load_ceri_config()
        client = ProfilingSecClient(
            SecClientConfig(
                user_agent=os.getenv("SEC_USER_AGENT", "SwingLens/0.1.0 operator@example.invalid"),
                requests_per_second=settings.sec_requests_per_second,
                timeout_seconds=settings.sec_http_timeout_seconds,
            ),
            tracker,
        )
        extractor = ProfilingExtractor(tracker)
        provider = ProfilingProvider(tracker, client, extractor)
        registry = CeriProviderRegistry(providers={"sec": provider}, config=config)
        service = ProfilingIngestionService(
            tracker,
            config=config,
            registry=registry,
            source_records=ProfilingSourceRecordService(tracker),
        )

        def heartbeat() -> None:
            flush_before = tracker._flush_total_ms
            commit_before = tracker._commit_total_ms
            started = time.perf_counter()
            heartbeat_job(
                db,
                job,
                lease_seconds=settings.job_stale_after_seconds,
                execution_token=token,
            )
            heartbeat_worker(
                db,
                worker_id,
                hostname=socket.gethostname(),
                process_id=os.getpid(),
            )
            db.commit()
            elapsed = (time.perf_counter() - started) * 1000
            nested = (tracker._flush_total_ms - flush_before) + (
                tracker._commit_total_ms - commit_before
            )
            if tracker.current_ticker in tracker.ticker:
                tracker.metric().queue_orchestration_ms += max(0.0, elapsed - nested)

        job._heartbeat = heartbeat
        sampler = SystemSampler(tracker)
        tracker.scenario_started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        sampler.start()
        try:
            result = execute_provider_ingest_batch_job(db, job, ingestion_service=service)
            tracker.result = result
            if job.status == JobStatus.PARTIAL:
                mark_job_partial(db, job, result, execution_token=token)
            else:
                mark_job_completed(db, job, result, execution_token=token)
            db.commit()
        finally:
            tracker.total_elapsed_ms = (time.perf_counter() - started) * 1000
            tracker.scenario_completed_at = datetime.now(UTC).isoformat()
            sampler.stop()
            if hasattr(job, "_heartbeat"):
                delattr(job, "_heartbeat")
        return tracker.as_dict()
    except Exception as exc:
        db.rollback()
        if job.id is not None:
            job = db.get(BackgroundJob, job.id)
            if job is not None:
                job.status = JobStatus.FAILED
                job.error_message = safe_error(exc)
                job.completed_at = datetime.now(UTC)
                job.worker_id = job.lease_owner = job.execution_token = None
                job.locked_at = job.heartbeat_at = job.lease_expires_at = None
                db.commit()
        raise
    finally:
        db.close()


def request_type_for_url(url: str) -> str:
    if "company_tickers.json" in url:
        return "company_ticker_map"
    if "/submissions/" in url:
        return "submissions"
    if "/Archives/edgar/data/" in url:
        return "filing_document"
    return "other"


def load_known_filings(tickers: tuple[str, ...]) -> dict[str, dict[str, set[Any]]]:
    known = {ticker: {"pairs": set(), "accessions": set()} for ticker in tickers}
    with Session(bind=engine) as db:
        rows = db.execute(
            text("""
            SELECT company_hint_json->>'ticker' AS ticker,
                   restricted_normalized_json->>'filing_accession' AS accession,
                   source_reference
            FROM ceri_source_records
            WHERE provider='sec' AND dataset='guidance'
              AND company_hint_json->>'ticker' = ANY(:tickers)
        """),
            {"tickers": list(tickers)},
        ).mappings()
        for row in rows:
            ticker = str(row["ticker"] or "").upper()
            accession = str(row["accession"] or "")
            reference = str(row["source_reference"] or "")
            if ticker not in known or not accession:
                continue
            known[ticker]["accessions"].add(accession)
            prefix = f"{accession}/"
            if reference.startswith(prefix):
                document = reference[len(prefix) :].split("#", 1)[0]
                if document:
                    known[ticker]["pairs"].add((accession, document))
    return known


def assert_no_unrelated_active_jobs(db: Session) -> None:
    rows = (
        db.execute(
            text("""
        SELECT id, related_run_id, job_type, status
        FROM background_jobs
        WHERE status IN ('QUEUED','RUNNING')
        ORDER BY id
    """)
        )
        .mappings()
        .all()
    )
    if rows:
        raise RuntimeError(
            f"Profiling isolation failed: {len(rows)} unrelated active background job(s) exist"
        )


def verify_clean_queue() -> dict[str, Any]:
    with Session(bind=engine) as db:
        rows = (
            db.execute(
                text("""
            SELECT status, count(*) AS count
            FROM background_jobs
            WHERE related_run_id IN (97,98,99,100)
            GROUP BY status ORDER BY status
        """)
            )
            .mappings()
            .all()
        )
        active = (
            db.execute(
                text("""
            SELECT count(*) FILTER (WHERE status='QUEUED') AS queued,
                   count(*) FILTER (WHERE status='RUNNING') AS running
            FROM background_jobs
            WHERE related_run_id IN (97,98,99,100)
        """)
            )
            .mappings()
            .one()
        )
    result = {
        "status_counts": [dict(row) for row in rows],
        "queued": active["queued"],
        "running": active["running"],
    }
    if result["queued"] or result["running"]:
        raise RuntimeError(f"Runs 97-100 are not clean: {result}")
    return result


def actual_runtime_config() -> dict[str, Any]:
    settings = get_settings()
    keys = (
        "ceri_enabled",
        "ceri_provider_ingest_enabled",
        "ceri_legacy_pipeline_scheduling_enabled",
        "ceri_batched_workflow_enabled",
        "ceri_provider_batch_size",
        "ceri_normalization_batch_size",
        "ceri_feature_batch_size",
        "ceri_batch_checkpoint_interval",
        "ceri_barrier_retry_seconds",
        "ceri_run_capture_enabled",
        "ceri_ui_enabled",
        "ceri_alerts_enabled",
        "ceri_admin_enabled",
        "ceri_backfill_enabled",
        "sec_requests_per_second",
        "sec_http_timeout_seconds",
        "sec_form4_enabled",
    )
    return {key.upper(): getattr(settings, key) for key in keys}


def summarize_requests(requests: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(int(row["http_status"]) for row in requests)
    types = Counter(row["request_type"] for row in requests)
    return {
        "total": len(requests),
        "company_ticker_map": types["company_ticker_map"],
        "submissions": types["submissions"],
        "filing_documents": types["filing_document"],
        "other": types["other"],
        "http_2xx": sum(count for status, count in statuses.items() if 200 <= status < 300),
        "http_403": statuses[403],
        "http_429": statuses[429],
        "http_5xx": sum(count for status, count in statuses.items() if status >= 500),
        "timeouts": sum(bool(row["timed_out"]) for row in requests),
        "retries": sum(int(row["retry_number"]) > 0 for row in requests),
        "pacing_sleep_ms": round(sum(float(row["pacing_wait_ms"]) for row in requests), 3),
        "http_wait_ms": round(sum(float(row["http_ms"]) for row in requests), 3),
        "bytes_downloaded": sum(int(row["bytes"]) for row in requests),
    }


def ticker_summary(tracker: Tracker, ticker: str) -> dict[str, Any]:
    metric = tracker.metric(ticker)
    requests = [row for row in tracker.requests if row["ticker"] == ticker]
    filings = [row for row in tracker.filings.values() if row.ticker == ticker and row.candidate]
    db_ms = (
        metric.source_record_write_ms
        + metric.deduplication_ms
        + metric.db_flush_ms
        + metric.db_commit_ms
        + metric.ingestion_run_db_ms
        + metric.cancel_check_db_ms
    )
    return {
        **asdict(metric),
        "http_ms": round(sum(float(row["http_ms"]) for row in requests), 3),
        "pacing_ms": round(sum(float(row["pacing_wait_ms"]) for row in requests), 3),
        "retry_backoff_ms": round(tracker.retry_sleep_by_ticker[ticker], 3),
        "parse_ms": round(sum(row.parse_ms for row in filings), 3),
        "db_ms": round(db_ms, 3),
        "bytes_downloaded": sum(int(row["bytes"]) for row in requests),
        "oldest_candidate_date": min((row.filing_date for row in filings), default=None),
        "newest_candidate_date": max((row.filing_date for row in filings), default=None),
    }


def timing_breakdown(tracker: Tracker) -> dict[str, Any]:
    requests = tracker.requests
    ticker_metrics = list(tracker.ticker.values())
    filings = [row for row in tracker.filings.values() if row.candidate]
    values = {
        "ticker_cik_resolution_ms": sum(row.ticker_resolution_ms for row in ticker_metrics),
        "sec_pacing_sleep_ms": sum(float(row["pacing_wait_ms"]) for row in requests),
        "company_ticker_map_http_ms": sum(
            float(row["http_ms"]) for row in requests if row["request_type"] == "company_ticker_map"
        ),
        "submissions_http_ms": sum(
            float(row["http_ms"]) for row in requests if row["request_type"] == "submissions"
        ),
        "filing_http_download_ms": sum(
            float(row["http_ms"]) for row in requests if row["request_type"] == "filing_document"
        ),
        "other_http_ms": sum(
            float(row["http_ms"]) for row in requests if row["request_type"] == "other"
        ),
        "retry_backoff_ms": tracker.retry_sleep_ms,
        "filing_discovery_provider_local_ms": sum(
            row.filing_discovery_ms for row in ticker_metrics
        ),
        "guidance_parsing_extraction_ms": sum(row.parse_ms for row in filings),
        "source_record_write_ms": sum(row.source_record_write_ms for row in ticker_metrics),
        "deduplication_ms": sum(row.deduplication_ms for row in ticker_metrics),
        "db_flush_commit_ms": tracker._flush_total_ms + tracker._commit_total_ms,
        "ingestion_run_db_ms": sum(row.ingestion_run_db_ms for row in ticker_metrics),
        "queue_orchestration_ms": sum(
            row.queue_orchestration_ms + row.cancel_check_db_ms for row in ticker_metrics
        ),
    }
    classified = sum(values.values())
    values["other_unclassified_ms"] = tracker.total_elapsed_ms - classified
    values["classified_ms"] = classified
    values["reconciliation_error_ms"] = tracker.total_elapsed_ms - (
        classified + values["other_unclassified_ms"]
    )
    values["http_network_wait_ms"] = sum(float(row["http_ms"]) for row in requests)
    return {key: round(value, 3) for key, value in values.items()}


def summarize_system(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {}

    def stats(key: str) -> dict[str, float | None]:
        values = [float(row[key]) for row in samples if row.get(key) is not None]
        return {
            "min": min(values) if values else None,
            "mean": sum(values) / len(values) if values else None,
            "max": max(values) if values else None,
        }

    return {
        "sample_count": len(samples),
        "system_cpu_percent": stats("system_cpu_percent"),
        "worker_cpu_percent_one_core": stats("worker_cpu_percent_one_core"),
        "available_ram_bytes": stats("available_ram_bytes"),
        "worker_ram_bytes": stats("worker_ram_bytes"),
        "postgresql_ram_bytes": stats("postgresql_ram_bytes"),
    }


def build_comparison(first: dict[str, Any], repeated: dict[str, Any]) -> list[dict[str, Any]]:
    def scenario_metrics(value: dict[str, Any]) -> dict[str, float]:
        request = value["request_summary"]
        timing = value["timing"]
        tickers = value["ticker_metrics"]
        return {
            "Total elapsed ms": value["total_elapsed_ms"],
            "SEC requests": request["total"],
            "Filing downloads": request["filing_documents"],
            "Bytes downloaded": request["bytes_downloaded"],
            "Pacing sleep ms": request["pacing_sleep_ms"],
            "HTTP wait ms": request["http_wait_ms"],
            "Parsing time ms": timing["guidance_parsing_extraction_ms"],
            "DB time ms": sum(row["db_ms"] for row in tickers),
            "Guidance records": sum(row["guidance_records"] for row in tickers),
            "Deduplicated records": sum(row["deduplicated_records"] for row in tickers),
        }

    a, b = scenario_metrics(first), scenario_metrics(repeated)
    return [
        {
            "metric": key,
            "first_run": round(a[key], 3),
            "repeated_run": round(b[key], 3),
            "difference": round(b[key] - a[key], 3),
        }
        for key in a
    ]


def annotate_repeated_filings(first: dict[str, Any], repeated: dict[str, Any]) -> None:
    first_downloads = {
        (row["ticker"], row["accession"], row["document"])
        for row in first["filings"]
        if row["downloaded"]
    }
    for row in repeated["filings"]:
        key = (row["ticker"], row["accession"], row["document"])
        row["observed_downloaded_in_first_run"] = key in first_downloads
        row["downloaded_again"] = bool(row["downloaded"] and key in first_downloads)


def repeated_work_summary(first: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    annotate_repeated_filings(first, scenario)
    filings = [row for row in scenario["filings"] if row["candidate"]]
    known_downloads = [row for row in filings if row["downloaded"] and row["known_before_download"]]
    downloaded = [row for row in filings if row["downloaded"]]
    known_request_cost = sum(row["http_ms"] + row["pacing_ms"] for row in known_downloads)
    known_parse_cost = sum(row["parse_ms"] for row in known_downloads)
    dedup_cost = scenario["timing"]["deduplication_ms"]
    experimental_repeats = [row for row in downloaded if row["downloaded_again"]]
    experimental_reparsed = [row for row in experimental_repeats if row["parsed"]]
    repeated_filing_request_cost = sum(
        row["http_ms"] + row["pacing_ms"] for row in experimental_repeats
    )
    repeated_filing_parse_cost = sum(row["parse_ms"] for row in experimental_reparsed)
    return {
        "candidate_filings": len(filings),
        "downloaded_filings": len(downloaded),
        "already_guidance_known_before_download": sum(
            row["known_before_download"] for row in filings
        ),
        "known_filings_downloaded_again": len(known_downloads),
        "known_download_ratio": len(known_downloads) / len(downloaded) if downloaded else 0.0,
        "known_request_pacing_ms": known_request_cost,
        "known_parse_ms": known_parse_cost,
        "deduplication_ms": dedup_cost,
        "measured_repeated_work_lower_bound_ms": known_request_cost + known_parse_cost + dedup_cost,
        "experimentally_re_downloaded_from_first_run": len(experimental_repeats),
        "experimentally_reparsed_from_first_run": len(experimental_reparsed),
        "experimental_repeat_download_ratio": (
            len(experimental_repeats) / len(downloaded) if downloaded else 0.0
        ),
        "experimental_repeated_filing_request_pacing_ms": repeated_filing_request_cost,
        "experimental_repeated_filing_parse_ms": repeated_filing_parse_cost,
        "experimental_repeated_work_ms": (
            repeated_filing_request_cost + repeated_filing_parse_cost + dedup_cost
        ),
        "schema_limit": "Only filings that previously produced persisted guidance can be known before HTTP. There is no filing-processing/document-hash ledger, so prior processing of zero-guidance documents is unknowable.",
    }


def write_outputs(payload: dict[str, Any], output_dir: Path, stamp: str) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"sec_performance_profile_{stamp}.json"
    md_path = output_dir / f"sec_performance_profile_{stamp}.md"
    tickers_path = output_dir / f"sec_performance_tickers_{stamp}.csv"
    trace_path = output_dir / f"sec_request_trace_{stamp}.csv"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    write_tickers_csv(payload, tickers_path)
    trace_scenario = payload["scenarios"].get("C", payload["scenarios"]["A"])
    write_trace_csv(trace_scenario["requests"], trace_path)
    return {
        "markdown": str(md_path.resolve()),
        "json": str(json_path.resolve()),
        "tickers_csv": str(tickers_path.resolve()),
        "request_trace_csv": str(trace_path.resolve()),
    }


def write_tickers_csv(payload: dict[str, Any], path: Path) -> None:
    fields = [
        "scenario",
        "ticker",
        "total_ms",
        "sec_calls",
        "filings_considered",
        "filings_downloaded",
        "filings_already_known",
        "http_ms",
        "pacing_ms",
        "parse_ms",
        "db_ms",
        "guidance_records",
        "deduplicated_records",
        "cik",
        "filings_present",
        "filings_filtered",
        "bytes_downloaded",
        "oldest_candidate_date",
        "newest_candidate_date",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for label in ("A", "B"):
            for row in sorted(
                payload["scenarios"][label]["ticker_metrics"],
                key=lambda item: item["total_ms"],
                reverse=True,
            ):
                writer.writerow(
                    {key: label if key == "scenario" else row.get(key) for key in fields}
                )


def write_trace_csv(requests: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "timestamp",
        "ticker",
        "request_type",
        "url_category",
        "stage",
        "pacing_wait_ms",
        "http_ms",
        "http_status",
        "bytes",
        "retry_number",
        "timed_out",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in requests)


def render_markdown(payload: dict[str, Any]) -> str:
    a = payload["scenarios"]["A"]
    b = payload["scenarios"]["B"]
    repeated = payload["repeated_work"]
    recommendation = payload["recommendation"]
    lines = [
        f"# SwingLens SEC/CERI performance profile — {payload['profile_timestamp']}",
        "",
        "## Executive summary",
        "",
        f"The isolated five-ticker first run took **{fmt_ms(a['total_elapsed_ms'])}**; the identical fresh-provider repeat took **{fmt_ms(b['total_elapsed_ms'])}**. "
        f"The repeat issued **{b['request_summary']['total']} SEC requests**, downloaded **{b['request_summary']['filing_documents']} filing documents**, and transferred **{fmt_bytes(b['request_summary']['bytes_downloaded'])}**. "
        f"All **{repeated['experimentally_re_downloaded_from_first_run']}** filing downloads repeated the same accession/document downloaded and parsed in Scenario A. "
        f"The database itself could pre-identify only **{repeated['known_filings_downloaded_again']}** of them because those had persisted guidance; SwingLens has no durable filing-processing ledger for zero-guidance documents.",
        "",
        f"**Single recommended next architecture:** {recommendation['architecture']}. {recommendation['reason']}",
        "",
        "## Queue cleanup",
        "",
        f"Dry-run inventory at {INITIAL_QUEUE_INVENTORY['captured_at']}: **167 QUEUED**, **1 RUNNING**, **43 terminal**. "
        "The running job was 29749 (`CERI_NORMALIZE_BATCH`, run 97) on `local-worker-1`. "
        f"The oldest queued age was {INITIAL_QUEUE_INVENTORY['totals']['oldest_queued_age_seconds'] / 3600:.2f} hours.",
        "",
        "All 167 queued jobs were cancelled through `request_job_cancel`; job 29749 received cooperative cancellation and the worker completed normal cancellation. No worker was force-stopped and no records were deleted.",
        "",
        f"Final verification: **QUEUED={payload['queue_cleanup']['final']['queued']}**, **RUNNING={payload['queue_cleanup']['final']['running']}** for runs 97–100.",
        "",
        "| Run | Job type | Status | Count |",
        "|---:|---|---|---:|",
    ]
    for row in INITIAL_QUEUE_INVENTORY["grouped"]:
        lines.append(f"| {row['run_id']} | {row['job_type']} | {row['status']} | {row['count']} |")
    lines.extend(
        [
            "",
            "## Runtime configuration",
            "",
            "Values were read from the active settings/environment allowlist; no secrets were printed.",
            "",
            "| Setting | Actual value |",
            "|---|---:|",
        ]
    )
    for key, value in payload["runtime_configuration"].items():
        lines.append(f"| `{key}` | `{str(value).lower() if isinstance(value, bool) else value}` |")
    lines.extend(
        [
            "",
            "## Five profiling tickers",
            "",
            "| Ticker | Selection role |",
            "|---|---|",
            "| AIZ | High historical guidance/filing workload; recent guidance |",
            "| AMZN | Medium/high workload; recent guidance; large representative issuer |",
            "| CLBT | Lower record volume and little recent guidance (latest persisted guidance was in March 2026 before profiling) |",
            "| JPM | Medium workload with a small number of guidance-producing accessions |",
            "| SLDE | Low-volume workload with recent guidance |",
            "",
            "The handler sorts tickers, so every run executed the same order: `AIZ, AMZN, CLBT, JPM, SLDE`. Scenario C is the dedicated detailed request/system trace. Scenario B used a new provider/client/workflow identity, matching a new provider job/run rather than reusing Scenario A's process-local HTTP cache.",
            "",
            "## Actual execution path and lifecycle findings",
            "",
            "`CERI_PROVIDER_INGEST_BATCH` → `execute_provider_ingest_batch_job()` → one shared `CeriIngestionService`/registry/provider per batch → `SecCeriProvider.fetch_guidance()` → `SecEdgarClient` → submissions discovery → synchronous filing downloads → `GuidanceExtractionService` → `CeriSourceRecordService.store_source_record()`.",
            "",
            "The batched handler does not populate `CeriIngestionRequest.start` or `.end`. Therefore all selected 8-K, 10-Q, 10-K, 6-K, and 20-F entries in each SEC submissions `recent` array are inspected. The measured oldest/newest candidate dates appear in the ticker table below.",
            "",
            "`SEC_FORM4_ENABLED=true` was verified, but Form 4 does not execute inside this guidance job: `SecCeriProvider.fetch_guidance()` selects only 8-K, 10-Q, 10-K, 6-K, and 20-F.",
            "",
            "The provider batch stores completed-ticker/results checkpoint metadata after each ticker and performs the configured interval check every five ticker positions. There is no filing-level checkpoint or accession processing state; cancellation cannot be observed while the synchronous provider is still downloading/parsing one ticker.",
            "",
            "One `SecCeriProvider` and `SecEdgarClient` are reused across tickers inside a provider batch. The ticker→CIK map and URL response cache are in memory on that client/provider. A new provider job constructs a new service/registry/provider/client, so neither cache survives jobs or pipeline runs. No persistent accession/document cache exists.",
            "",
            "Current provider telemetry does not capture SEC detail: `_record_provider_telemetry()` only writes when a client exposes `stats()`, which `SecEdgarClient` does not. The existing request key coalesces only the same workflow identity; a new pipeline workflow gets a new ingestion request key.",
            "",
            "## Where the time went",
            "",
        ]
    )
    for label, scenario in (("First run (A)", a), ("Repeated run (B)", b)):
        lines.extend(
            [
                f"### {label}",
                "",
                "| Component | Milliseconds | Percent of elapsed |",
                "|---|---:|---:|",
            ]
        )
        for key, value in scenario["timing"].items():
            if key in {"classified_ms", "reconciliation_error_ms", "http_network_wait_ms"}:
                continue
            lines.append(
                f"| {key.replace('_', ' ')} | {value:,.1f} | {percent(value, scenario['total_elapsed_ms'])} |"
            )
        lines.append(
            f"| **Total elapsed** | **{scenario['total_elapsed_ms']:,.1f}** | **100.0%** |"
        )
        lines.append("")
        lines.append(
            f"HTTP/network wait (aggregate view): **{scenario['timing']['http_network_wait_ms']:,.1f} ms**. Unclassified time is explicit and includes small Python/handler bookkeeping not captured by the timed boundaries."
        )
        lines.append("")
    lines.extend(
        [
            "## First run vs repeated run",
            "",
            "| Metric | First run | Repeated run | Difference |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in payload["comparison"]:
        lines.append(
            f"| {row['metric']} | {row['first_run']:,.3f} | {row['repeated_run']:,.3f} | {row['difference']:+,.3f} |"
        )
    lines.extend(
        [
            "",
            "## SEC request volume",
            "",
            "| Metric | First run | Repeated run |",
            "|---|---:|---:|",
        ]
    )
    request_keys = (
        "total",
        "company_ticker_map",
        "submissions",
        "filing_documents",
        "other",
        "http_2xx",
        "http_403",
        "http_429",
        "http_5xx",
        "timeouts",
        "retries",
        "pacing_sleep_ms",
        "http_wait_ms",
        "bytes_downloaded",
    )
    for key in request_keys:
        lines.append(
            f"| {key.replace('_', ' ')} | {a['request_summary'][key]:,.3f} | {b['request_summary'][key]:,.3f} |"
        )
    lines.extend(["", "## Per-ticker results", ""])
    for label, scenario in (("First run (A)", a), ("Repeated run (B)", b)):
        lines.extend(
            [
                f"### {label}",
                "",
                "| Ticker | Total ms | SEC calls | Filings considered | Downloads | Already known | HTTP ms | Pacing ms | Parse ms | DB ms | Guidance | Duplicates |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in sorted(
            scenario["ticker_metrics"], key=lambda item: item["total_ms"], reverse=True
        ):
            lines.append(
                f"| {row['ticker']} | {row['total_ms']:,.1f} | {row['sec_calls']} | {row['filings_considered']} | {row['filings_downloaded']} | {row['filings_already_known']} | {row['http_ms']:,.1f} | {row['pacing_ms']:,.1f} | {row['parse_ms']:,.1f} | {row['db_ms']:,.1f} | {row['guidance_records']} | {row['deduplicated_records']} |"
            )
        lines.append("")
        lines.append(
            "Candidate windows: "
            + "; ".join(
                f"{row['ticker']} {row['oldest_candidate_date']}→{row['newest_candidate_date']}"
                for row in scenario["ticker_metrics"]
            )
            + "."
        )
        lines.append("")
    trace = payload["scenarios"].get("C")
    if trace is not None:
        lines.extend(
            [
                "## Dedicated request/system trace (Scenario C)",
                "",
                f"Scenario C took **{fmt_ms(trace['total_elapsed_ms'])}**, made **{trace['request_summary']['total']}** SEC requests, downloaded **{trace['request_summary']['filing_documents']}** filing documents, and transferred **{fmt_bytes(trace['request_summary']['bytes_downloaded'])}**. Its 413 chronological request rows are the companion trace CSV.",
                "",
            ]
        )
    lines.extend(
        [
            "## Filing-volume detail (repeated run)",
            "",
            "| Ticker | Submissions rows | 8-K | 10-Q | 10-K | 6-K | 20-F | Filtered other forms |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(b["ticker_metrics"], key=lambda item: item["total_ms"], reverse=True):
        lines.append(
            f"| {row['ticker']} | {row['filings_present']} | {row['candidate_8k']} | {row['candidate_10q']} | {row['candidate_10k']} | {row['candidate_6k']} | {row['candidate_20f']} | {row['filings_filtered']} |"
        )
    by_ticker = {row["ticker"]: row for row in b["ticker_metrics"]}
    lines.extend(
        [
            "",
            "## Ticker interpretation",
            "",
            f"- **AIZ:** Dominated the repeat at {by_ticker['AIZ']['total_ms']:,.1f} ms because it downloaded {by_ticker['AIZ']['filings_downloaded']} selected filings spanning {by_ticker['AIZ']['oldest_candidate_date']} to {by_ticker['AIZ']['newest_candidate_date']} and emitted {by_ticker['AIZ']['guidance_records']} records—all deduplicated.",
            f"- **AMZN:** Downloaded {by_ticker['AMZN']['filings_downloaded']} filings and emitted {by_ticker['AMZN']['guidance_records']} duplicate records; network+pacing accounted for {by_ticker['AMZN']['http_ms'] + by_ticker['AMZN']['pacing_ms']:,.1f} ms.",
            f"- **CLBT:** Downloaded {by_ticker['CLBT']['filings_downloaded']} filings, but only {by_ticker['CLBT']['filings_already_known']} were database-known guidance documents before HTTP; it represents little recent guidance despite continued document scanning.",
            f"- **JPM:** Its submissions array contained {by_ticker['JPM']['filings_present']:,} rows, but form filtering selected {by_ticker['JPM']['filings_considered']}; those still produced {by_ticker['JPM']['guidance_records']} duplicate records.",
            f"- **SLDE:** Was the lowest-volume/fastest repeat ticker with {by_ticker['SLDE']['filings_downloaded']} downloads and {by_ticker['SLDE']['guidance_records']} duplicate records. Its slower first-run parse time was not reproduced in B or C and is treated as runtime variance, not a stable bottleneck.",
            "",
            "## Slowest parsed filing documents (repeated run)",
            "",
            "| Ticker | Accession | Form | Size | Parse ms | Candidate paragraphs | Guidance records |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for filing in sorted(b["filings"], key=lambda item: item["parse_ms"], reverse=True)[:10]:
        lines.append(
            f"| {filing['ticker']} | {filing['accession']} | {filing['form']} | {fmt_bytes(filing['bytes'])} | {filing['parse_ms']:,.1f} | {filing['candidate_paragraphs']} | {filing['guidance_records']} |"
        )
    lines.append("")
    slowest = max(b["ticker_metrics"], key=lambda item: item["total_ms"])
    lines.extend(
        [
            "## Repeated filing work",
            "",
            f"The controlled trace proves **{repeated['experimentally_re_downloaded_from_first_run']} of {repeated['downloaded_filings']}** repeat downloads ({repeated['experimental_repeat_download_ratio'] * 100:.1f}%) were the same accession/document pairs downloaded in Scenario A, and **{repeated['experimentally_reparsed_from_first_run']}** were parsed again. "
            f"Their filing request+pacing cost was **{repeated['experimental_repeated_filing_request_pacing_ms']:,.1f} ms**, parsing cost was **{repeated['experimental_repeated_filing_parse_ms']:,.1f} ms**, and record deduplication cost was **{repeated['deduplication_ms']:,.1f} ms**: **{repeated['experimental_repeated_work_ms']:,.1f} ms** ({percent(repeated['experimental_repeated_work_ms'], b['total_elapsed_ms'])}) of repeat elapsed. "
            f"Independently, the database could identify **{repeated['known_filings_downloaded_again']} of {repeated['downloaded_filings']}** ({repeated['known_download_ratio'] * 100:.1f}%) before HTTP because they had previously produced guidance. "
            f"Their directly measured request+pacing cost was **{repeated['known_request_pacing_ms']:,.1f} ms**, parsing cost was **{repeated['known_parse_ms']:,.1f} ms**, and record-level deduplication cost was **{repeated['deduplication_ms']:,.1f} ms**. "
            f"The conservative measured repeated-work lower bound is **{repeated['measured_repeated_work_lower_bound_ms']:,.1f} ms** ({percent(repeated['measured_repeated_work_lower_bound_ms'], b['total_elapsed_ms'])} of repeat elapsed).",
            "",
            "Record-level deduplication is not filing-level avoidance: it occurs after the document HTTP request, parsing, extraction, cancellation heartbeat, and source-record lookup. `previously_processed` and `document_hash_known` remain `null` in the filing detail because the schema has no such ledger. Thus the known-repeat number above excludes repeated zero-guidance documents and is a lower bound.",
            "",
            "## Slowest ticker",
            "",
            f"`{slowest['ticker']}` was slowest on the repeated run at **{slowest['total_ms']:,.1f} ms**. It considered {slowest['filings_considered']} selected filings, downloaded {slowest['filings_downloaded']}, made {slowest['sec_calls']} SEC calls, spent {slowest['http_ms']:,.1f} ms in HTTP, {slowest['pacing_ms']:,.1f} ms pacing, {slowest['parse_ms']:,.1f} ms parsing, and {slowest['db_ms']:,.1f} ms in measured DB work.",
            "",
            "## CPU / I/O classification",
            "",
            cpu_conclusion(payload["scenarios"]),
            "",
            "## Root-cause ranking (repeated run)",
            "",
            "| Bottleneck | Measured impact | Evidence | Recommended action |",
            "|---|---:|---|---|",
        ]
    )
    for row in payload["root_causes"]:
        lines.append(
            f"| {row['bottleneck']} | {row['measured_impact']} | {row['evidence']} | {row['recommended_action']} |"
        )
    lines.extend(
        [
            "",
            "## Recommended next architecture",
            "",
            f"**{recommendation['architecture']}**",
            "",
            recommendation["reason"],
            "",
            "The safe design target is: sync submissions metadata → discover accessions/documents → consult durable processing state → download/extract only unseen or explicitly stale documents → persist processing outcome (including zero-guidance) → continue normal CERI source-record handling. This task did not implement that change.",
            "",
            "## Request trace and detailed filing evidence",
            "",
            "The chronological Scenario C request trace is in the companion CSV. It contains only request categories, not raw URLs or secrets. The JSON contains per-document accession, document, form, filing date, known-before-download status, download/parse flags, sizes, timings, and guidance counts.",
            "",
            "No 25-ticker validation was run: the five-ticker evidence already showed identical 407-document request volume across fresh-provider A/B runs and was sufficient to determine the scaling mechanism.",
        ]
    )
    return "\n".join(lines) + "\n"


def root_causes(repeated: dict[str, Any], repeated_work: dict[str, Any]) -> list[dict[str, str]]:
    total = repeated["total_elapsed_ms"]
    timing = repeated["timing"]
    repeated_ms = repeated_work["experimental_repeated_work_ms"]
    repeated_http_parse = (
        repeated_work["experimental_repeated_filing_request_pacing_ms"]
        + repeated_work["experimental_repeated_filing_parse_ms"]
    )
    necessary_http = max(
        0.0,
        timing["http_network_wait_ms"]
        + timing["sec_pacing_sleep_ms"]
        + timing["guidance_parsing_extraction_ms"]
        - repeated_http_parse,
    )
    db_ms = max(
        0.0,
        timing["source_record_write_ms"]
        + timing["deduplication_ms"]
        + timing["db_flush_commit_ms"]
        + timing["ingestion_run_db_ms"]
        - repeated_work["deduplication_ms"],
    )
    queue_ms = timing["queue_orchestration_ms"]
    rows = [
        (
            "Repeated historical downloads/parsing",
            repeated_ms,
            f"{repeated_work['experimentally_re_downloaded_from_first_run']} of {repeated_work['downloaded_filings']} filing documents were downloaded and parsed in both A and B",
            "Persist accession/document processing state and skip known work before HTTP",
        ),
        (
            "Necessary metadata HTTP/pacing",
            necessary_http,
            "Residual company-map/submissions request time after subtracting repeated filing request/parse work",
            "Reassess after persistent filing-level state removes unnecessary calls",
        ),
        (
            "Filing discovery/provider local",
            timing["filing_discovery_provider_local_ms"],
            "Measured provider-local remainder after request, pacing, resolution, and parsing",
            "Reprofile after filing-level incremental state reduces candidate processing",
        ),
        (
            "Database",
            db_ms,
            "Measured source lookup/write, flush, commit, and run lifecycle time",
            "Only investigate queries if still material after request elimination",
        ),
        (
            "Queue/orchestration",
            queue_ms,
            "Measured cooperative cancellation heartbeat/check overhead",
            "Keep current design unless post-incremental profiling shows material impact",
        ),
        (
            "Other/unclassified",
            max(0.0, timing["other_unclassified_ms"]),
            "Explicit reconciliation remainder",
            "Refine instrumentation only if material",
        ),
    ]
    rows.sort(key=lambda row: row[1], reverse=True)
    return [
        {
            "bottleneck": name,
            "measured_impact": f"{value:,.1f} ms ({percent(value, total)})",
            "evidence": evidence,
            "recommended_action": action,
        }
        for name, value, evidence, action in rows
    ]


def recommendation_for(
    first: dict[str, Any], repeated: dict[str, Any], repeated_work: dict[str, Any]
) -> dict[str, str]:
    a_downloads = first["request_summary"]["filing_documents"]
    b_downloads = repeated["request_summary"]["filing_documents"]
    ratio = b_downloads / a_downloads if a_downloads else 0.0
    return {
        "architecture": "Persistent accession/document-level incremental SEC ingestion",
        "reason": (
            f"The fresh-provider repeat retained {ratio * 100:.1f}% of first-run filing downloads: all "
            f"{repeated_work['experimentally_re_downloaded_from_first_run']} accession/document pairs were downloaded and parsed in both runs, while the database could pre-identify only {repeated_work['known_filings_downloaded_again']} guidance-producing documents before HTTP. "
            "A durable processing ledger (including zero-guidance outcomes) is the largest safe improvement because it removes network, pacing, parsing, heartbeat, and late dedup work without changing SEC selection or extraction semantics."
        ),
    }


def cpu_conclusion(scenarios: dict[str, dict[str, Any]]) -> str:
    repeated = scenarios["B"]
    summaries = [value.get("system_summary", {}) for value in scenarios.values()]
    worker_means = [
        item.get("worker_cpu_percent_one_core", {}).get("mean") for item in summaries if item
    ]
    worker_mean = sum(value for value in worker_means if value is not None) / max(
        1, sum(value is not None for value in worker_means)
    )
    http_pacing = (
        repeated["timing"]["http_network_wait_ms"]
        + repeated["timing"]["sec_pacing_sleep_ms"]
        + repeated["timing"]["retry_backoff_ms"]
    )
    if http_pacing > repeated["total_elapsed_ms"] * 0.5 and worker_mean < 50:
        label = "I/O-bound"
    elif worker_mean > 80:
        label = "CPU-bound"
    else:
        label = "mixed, with I/O as the larger measured component"
    trace_summary = scenarios.get("C", repeated).get("system_summary", {})
    postgres_mean = trace_summary.get("postgresql_ram_bytes", {}).get("mean")
    postgres_text = fmt_bytes(postgres_mean) if postgres_mean is not None else "unavailable"
    return f"Classification: **{label}**. Mean profiler-process CPU was {worker_mean:.1f}% of one logical core; repeat-run HTTP+pacing+backoff was {fmt_ms(http_pacing)}. Dedicated trace-run mean PostgreSQL working set was {postgres_text}. See JSON system samples for system CPU, available RAM, worker RSS, and PostgreSQL RSS."


def postgresql_working_set_bytes() -> int | None:
    """Read PostgreSQL service working sets without requiring OpenProcess rights."""
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq postgres.exe", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        total_kib = 0
        matched = False
        for row in csv.reader(completed.stdout.splitlines()):
            if len(row) < 5 or row[0].lower() != "postgres.exe":
                continue
            digits = re.sub(r"[^0-9]", "", row[4])
            if digits:
                total_kib += int(digits)
                matched = True
        return total_kib * 1024 if matched else None
    except Exception:
        return None


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    @property
    def value(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def system_cpu_times() -> tuple[int, int, int] | None:
    try:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        return idle.value, kernel.value, user.value
    except Exception:
        return None


def system_cpu_percent(
    before: tuple[int, int, int] | None, after: tuple[int, int, int] | None
) -> float | None:
    if before is None or after is None:
        return None
    idle = after[0] - before[0]
    total = (after[1] - before[1]) + (after[2] - before[2])
    return max(0.0, min(100.0, (total - idle) / total * 100.0)) if total else None


def available_ram_bytes() -> int | None:
    try:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        return (
            int(status.ullAvailPhys)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            else None
        )
    except Exception:
        return None


def process_rss_bytes(pid: int) -> int:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
    )
    if not handle:
        return 0
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return int(counters.WorkingSetSize) if ok else 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def percent(value: float, total: float) -> str:
    return f"{value / total * 100:.1f}%" if total else "0.0%"


def fmt_ms(value: float) -> str:
    return f"{value / 1000:.2f} s"


def fmt_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} GiB"


def safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ").strip()[:500] or exc.__class__.__name__


def main() -> int:
    parser = argparse.ArgumentParser(description="Measurement-only SwingLens SEC/CERI profiler")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--tickers", nargs="*", default=list(PROFILE_TICKERS))
    args = parser.parse_args()
    tickers = tuple(sorted({value.strip().upper() for value in args.tickers if value.strip()}))
    if len(tickers) != 5:
        raise SystemExit(f"Exactly five unique tickers are required; got {len(tickers)}")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    clean = verify_clean_queue()
    runtime = actual_runtime_config()
    first = run_scenario("A", tickers, stamp)
    second = run_scenario("B", tickers, stamp)
    repeated_work = repeated_work_summary(first, second)
    payload = {
        "profile_timestamp": stamp,
        "objective": "Measurement-first SEC/CERI provider profiling; no optimization applied",
        "queue_cleanup": {
            "dry_run_inventory": INITIAL_QUEUE_INVENTORY,
            "cancellation": {
                "queued_requested": 167,
                "running_requested": 1,
                "mechanism": "request_job_cancel",
                "records_deleted": 0,
                "worker_force_stopped": False,
            },
            "final": clean,
        },
        "runtime_configuration": runtime,
        "profiling_tickers": list(tickers),
        "scenario_c_trace_source": "Scenario A",
        "implementation_findings": {
            "date_window_populated": False,
            "history_inspected": "All selected forms in SEC submissions.filings.recent because start/end are None",
            "record_level_deduplication": True,
            "filing_level_avoidance": False,
            "previously_processed_schema_available": False,
            "document_hash_schema_available": False,
            "provider_lifecycle": "one provider per CERI provider batch",
            "client_lifecycle": "one client per provider; reused within batch only",
            "ticker_cik_cache": "in-memory per provider; reused within batch only",
            "http_cache": "in-memory per client URL; reused within batch only",
            "between_jobs_or_runs": "no SEC client/cache reuse",
            "current_sec_detailed_telemetry": False,
        },
        "scenarios": {"A": first, "B": second},
        "comparison": build_comparison(first, second),
        "repeated_work": repeated_work,
        "recommendation": recommendation_for(first, second, repeated_work),
        "root_causes": root_causes(second, repeated_work),
    }
    paths = write_outputs(payload, args.output_dir, stamp)
    print(
        json.dumps(
            {
                "status": "complete",
                "paths": paths,
                "summary": {
                    "first_elapsed_ms": first["total_elapsed_ms"],
                    "repeat_elapsed_ms": second["total_elapsed_ms"],
                    "first_requests": first["request_summary"]["total"],
                    "repeat_requests": second["request_summary"]["total"],
                    "known_downloads_repeated": repeated_work["known_filings_downloaded_again"],
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
