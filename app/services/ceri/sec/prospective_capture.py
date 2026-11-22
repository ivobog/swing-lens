"""One explicitly owned SEC-currency revenue acquisition, ending at normalization."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import islice
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriIngestionRunSourceRecord,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.models.tables import BackgroundJob, WorkScopeRecord
from app.services.ceri.dtos import EstimateRequest, RawProviderRecord
from app.services.ceri.enums import CeriDataset, CeriMetric, CeriPeriodType
from app.services.ceri.normalization_service import (
    CeriNormalizationCancelled,
    CeriNormalizationService,
)
from app.services.ceri.pit_eligibility import source_record_historical_eligibility_at
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ceri.providers.eodhd_client import (
    EodhdClientConfig,
    EodhdHttpClient,
    EodhdProviderError,
)
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.sec.client import SecClientConfig, SecEdgarClient, SecEdgarError
from app.services.ceri.sec.reporting_currency import (
    DATASET,
    POLICY,
    SecRevenueCurrencyCapture,
    StatementReview,
    _matching_filing,
    _time,
    register_statement,
)
from app.services.ceri.sec.statement_currency import CURRENCIES
from app.services.ceri.source_record_service import (
    CeriSourceRecordService,
    source_record_content_hash,
)
from app.services.scope_refresh_adoption import (
    SemanticWorkAuthority,
    require_semantic_authority,
    retained_scope_members,
)
from app.services.work_scope_identity import ScopeMember


@dataclass(frozen=True)
class _ReviewedFiling:
    cik: str
    entity: str
    registry_id: int
    accession: str
    sha256: str
    fiscal_end: date
    family: str
    currency: str
    accepted_at: datetime
    fiscal_targets: tuple[tuple[CeriPeriodType, date], ...] = ()
    fiscal_reference: str = ""


# Exact evidence in the approved F7 validation receipt. No alternate accession is selected.
def _filing(
    cik,
    entity,
    registry_id,
    accession,
    sha256,
    fiscal_end,
    family,
    currency,
    accepted,
    *,
    fiscal_targets=(),
):
    return _ReviewedFiling(
        cik,
        entity,
        registry_id,
        accession,
        sha256,
        date.fromisoformat(fiscal_end),
        family,
        currency,
        datetime.fromisoformat(accepted),
        fiscal_targets,
    )


_REVIEWED = {
    "AAPL.US": _filing(
        "0000320193",
        "Apple Inc.",
        25949,
        "0000320193-26-000026",
        "9a3ee612c32965fc9ac705a4a7989b6508b72111017ee0699ed2c41c45ab26cd",
        "2026-09-26",
        "ANNUAL",
        "USD",
        "2026-11-03T13:05:15Z",
    ),
    "AMZN.US": _filing(
        "0001018724",
        "AMAZON COM INC",
        31463,
        "0001018724-26-000040",
        "d9979a65fb3b979fdba863923048092be6e2b41b6824b10039b6c2f4be66d90c",
        "2026-09-30",
        "QUARTERLY",
        "USD",
        "2026-10-29T22:20:46Z",
    ),
    "ABBV.US": _filing(
        "0001551152",
        "AbbVie Inc.",
        31328,
        "0001551152-26-000037",
        "c67b66806aa429843a2387f41dd9e8a66135609548e3afb4966f0d3f0825d8c7",
        "2026-09-30",
        "QUARTERLY",
        "USD",
        "2026-11-03T18:43:40Z",
        # The exact 10-Q declares December 31 and shows consolidated 2026
        # quarter closes on March 31, June 30, and September 30.
        fiscal_targets=(
            (CeriPeriodType.CURRENT_QUARTER, date(2026, 12, 31)),
            (CeriPeriodType.NEXT_QUARTER, date(2027, 3, 31)),
            (CeriPeriodType.CURRENT_FISCAL_YEAR, date(2026, 12, 31)),
            (CeriPeriodType.NEXT_FISCAL_YEAR, date(2027, 12, 31)),
        ),
    ),
    "LIN.US": _filing(
        "0001707925",
        "LINDE PLC",
        24865,
        "0001628280-26-068079",
        "1e05395db9bf85cbbd018ad2aa5a6480cb38ab6e2c1e9de195850b384cb5d48f",
        "2026-09-30",
        "QUARTERLY",
        "USD",
        "2026-10-29T14:48:04Z",
    ),
    "HG.US": _filing(
        "0001593275",
        "Hamilton Insurance Group, Ltd.",
        26304,
        "0001593275-26-000128",
        "3ec7fa0f60955b9f581465a130588d7b811ac1ff1a0cc81e8fd1ba9f1dc26658",
        "2026-09-30",
        "QUARTERLY",
        "USD",
        "2026-11-04T21:19:16Z",
    ),
    "SPOT.US": _filing(
        "0001639920",
        "Spotify Technology S.A.",
        33158,
        "0001628280-26-066889",
        "f5bb0fa3bb946f9876c2dbe6904d648035c86d399cff4a24471c6d6d17750da7",
        "2026-09-30",
        "QUARTERLY",
        "EUR",
        "2026-10-22T20:09:19Z",
    ),
}
_PERIODS = (
    CeriPeriodType.CURRENT_QUARTER,
    CeriPeriodType.NEXT_QUARTER,
    CeriPeriodType.CURRENT_FISCAL_YEAR,
    CeriPeriodType.NEXT_FISCAL_YEAR,
)
_SEC = "https://data.sec.gov"
_EODHD = "https://eodhd.com"
CAPTURE_SCOPE_KIND = "CERI_PROSPECTIVE_REVENUE_CURRENCY"
SIGNED_CYCLE_GRANT_PATTERN = (
    r"cycle-v1:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


@dataclass(frozen=True)
class RevenueCaptureScope:
    ingestion_run_id: int
    request_key: str
    operator: str
    approval_reference: str
    starts_at: datetime
    cutoff_at: datetime
    symbols: tuple[str, ...]
    sec_attempts: int
    eodhd_attempts: int
    observations: int
    reviewed_filings: tuple[tuple[str, _ReviewedFiling], ...] = ()

    def filings(self) -> dict[str, _ReviewedFiling]:
        return dict(self.reviewed_filings) or {symbol: _REVIEWED[symbol] for symbol in self.symbols}

    def envelope(self) -> dict:
        envelope = {
            "policy": POLICY,
            "operator": self.operator,
            "approval_reference": self.approval_reference,
            "starts_at": self.starts_at.isoformat(),
            "cutoff_at": self.cutoff_at.isoformat(),
            "symbols": sorted(self.symbols),
            "sec_attempts": self.sec_attempts,
            "eodhd_attempts": self.eodhd_attempts,
            "observations": self.observations,
        }
        if self.reviewed_filings:
            envelope["reviewed_filings"] = {
                symbol: {
                    **asdict(proof),
                    "accepted_at": proof.accepted_at.isoformat(),
                    "fiscal_end": proof.fiscal_end.isoformat(),
                    "fiscal_targets": {
                        role.value: end.isoformat() for role, end in proof.fiscal_targets
                    },
                }
                for symbol, proof in self.reviewed_filings
            }
        return envelope

    def validate(self) -> None:
        if not (
            self.ingestion_run_id > 0
            and self.request_key.strip()
            and self.operator.strip()
            and self.approval_reference.strip()
            and 0 < len(self.symbols) <= 6
            and len(set(self.symbols)) == len(self.symbols)
            and all(symbol in _REVIEWED for symbol in self.symbols)
            and self.starts_at.utcoffset() == self.cutoff_at.utcoffset() == timedelta(0)
            and timedelta(0) < self.cutoff_at - self.starts_at <= timedelta(minutes=30)
            and 0 < self.sec_attempts <= 12
            and 0 < self.eodhd_attempts <= 6
            and 0 < self.observations <= 24
        ):
            raise ValueError("Invalid prospective revenue capture approval")
        signed_cycle = re.fullmatch(
            rf"ceri:prospective:{SIGNED_CYCLE_GRANT_PATTERN}", self.request_key
        )
        if bool(signed_cycle) != bool(self.reviewed_filings):
            raise ValueError("Signed cycle requires its own reviewed filings")
        if signed_cycle:
            filings = dict(self.reviewed_filings)
            if (
                len(self.reviewed_filings) != len(self.symbols)
                or filings.keys() != set(self.symbols)
            ):
                raise ValueError("Signed filings must match the exact issuer scope")
            for symbol, proof in filings.items():
                if not (
                    proof.cik == _REVIEWED[symbol].cik
                    and 0 < len(proof.entity.strip()) <= 160
                    and proof.registry_id > 0
                    and re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", proof.accession)
                    and re.fullmatch(r"[a-f0-9]{64}", proof.sha256)
                    and proof.family in {"ANNUAL", "QUARTERLY"}
                    and proof.currency in CURRENCIES
                    and proof.accepted_at.utcoffset() == timedelta(0)
                    and proof.fiscal_end <= proof.accepted_at.date()
                    and proof.accepted_at < self.starts_at
                    and 0 < len(proof.fiscal_reference.strip()) <= 240
                    and _ordered_targets(proof, self.cutoff_at.date())
                ):
                    raise ValueError("Reviewed filing or fiscal-period authority is invalid")


def _ordered_targets(proof: _ReviewedFiling, cutoff: date) -> bool:
    targets = dict(proof.fiscal_targets)
    if len(proof.fiscal_targets) != len(_PERIODS) or targets.keys() != set(_PERIODS):
        return False
    quarter, next_quarter, year, next_year = (targets[role] for role in _PERIODS)
    return (
        proof.fiscal_end < quarter
        and cutoff <= quarter < next_quarter <= next_year
        and quarter <= year < next_year
    )


class _StopCapture(RuntimeError):
    pass


class _SkipIssuer(RuntimeError):
    pass


class _DuplicateObservation(RuntimeError):
    pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Requests:
    def __init__(self, scope, cancelled, open_response):
        self.scope = scope
        self.cancelled = cancelled
        self.open_response = open_response or build_opener(_NoRedirects()).open
        self.attempts = {"sec": 0, "eodhd": 0}

    def check(self, timeout: int = 0) -> None:
        if self.cancelled():
            raise CeriNormalizationCancelled("Prospective capture cancelled")
        if (
            not self.scope.starts_at
            <= datetime.now(UTC)
            < (self.scope.cutoff_at - timedelta(seconds=timeout))
        ):
            raise _StopCapture("capture_window_exhausted")

    def request(self, provider, url, timeout, agent):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if provider == "sec":
            allowed_paths = {
                f"/submissions/CIK{proof.cik}.json" for proof in self.scope.filings().values()
            }
            permitted = origin == _SEC and parts.path in allowed_paths and not parts.query
        else:
            query = parse_qs(parts.query)
            permitted = (
                origin == _EODHD
                and parts.path == "/api/calendar/trends"
                and set(query) == {"symbols", "api_token", "fmt"}
                and len(query["symbols"]) == 1
                and query["symbols"][0] in self.scope.symbols
            )
        if not permitted or parts.fragment:
            raise _StopCapture("provider_request_outside_scope")
        self.check(timeout)
        limit = self.scope.sec_attempts if provider == "sec" else self.scope.eodhd_attempts
        if self.attempts[provider] >= limit:
            raise _StopCapture(f"{provider}_attempt_budget_exhausted")
        self.attempts[provider] += 1
        req = Request(url, headers={"User-Agent": agent, "Accept": "application/json"})  # noqa: S310
        with self.open_response(req, timeout=timeout) as response:
            if response.status != 200:
                raise OSError("Provider did not return HTTP 200")
            body = response.read(4_000_001)
        self.check()
        if len(body) > 4_000_000:
            raise _StopCapture("provider_response_size_exceeded")
        return json.loads(body)


def capture_prospective_revenue(
    db: Session,
    scope: RevenueCaptureScope,
    *,
    cache_dir: Path,
    sec_config: SecClientConfig,
    eodhd_config: EodhdClientConfig,
    should_cancel: Callable[[], bool] = lambda: False,
    open_response: Callable | None = None,
) -> dict:
    """Consume one pre-authorized, owned run and stop after source/estimate writes.

    The caller passes a fresh PostgreSQL session. The durable claim survives a
    failed transaction so a retry cannot repeat provider attempts for the grant.
    """
    scope.validate()
    if db.get_bind().dialect.name != "postgresql" or db.in_transaction():
        raise ValueError("Prospective capture requires an idle PostgreSQL session")
    if (
        sec_config.base_url.rstrip("/") != _SEC
        or eodhd_config.base_url.rstrip("/") != _EODHD
        or not 0 < sec_config.timeout_seconds <= 30
        or not 0 < eodhd_config.timeout_seconds <= 30
        or not eodhd_config.api_key
    ):
        raise ValueError("Invalid prospective capture provider configuration")
    requests = _Requests(scope, should_cancel, open_response)
    requests.check()
    authority = _claim(db, scope)
    sec = SecEdgarClient(
        replace(sec_config, max_attempts=1),
        transport=lambda url, timeout, agent: requests.request("sec", url, timeout, agent),
    )
    eodhd = EodhdCeriProvider(
        client=EodhdHttpClient(
            replace(eodhd_config, max_attempts=1),
            transport=lambda url, timeout: requests.request(
                "eodhd",
                url,
                timeout,
                eodhd_config.user_agent,
            ),
        )
    )
    receipt = {
        "policy": POLICY,
        "ingestion_run_id": scope.ingestion_run_id,
        "request_key": scope.request_key,
        "scope": scope.envelope(),
        "ownership": authority.as_dict(),
        "requests": requests.attempts,
        "issuers": [],
    }
    try:
        with db.begin():
            run = db.get(CeriIngestionRun, scope.ingestion_run_id, with_for_update=True)
            _capture(db, run, authority, scope, cache_dir, sec, eodhd, requests, receipt)
            requests.check()
            completed = sum(r["status"] == "CAPTURED" for r in receipt["issuers"])
            status = "COMPLETED" if completed == len(scope.symbols) else "PARTIAL"
            _finish(db, run, receipt, status)
            requests.check()
    except (CeriNormalizationCancelled, _StopCapture) as exc:
        status = "CANCELLED" if isinstance(exc, CeriNormalizationCancelled) else "FAILED"
        _record_failure(db, scope, receipt, status, str(exc))
    except Exception:
        _record_failure(db, scope, receipt, "FAILED", "unexpected_capture_error")
        raise
    return receipt


def _claim(db: Session, scope: RevenueCaptureScope) -> SemanticWorkAuthority:
    with db.begin():
        run = db.get(CeriIngestionRun, scope.ingestion_run_id, with_for_update=True)
        if not (
            run
            and run.request_key == scope.request_key
            and run.provider == "eodhd"
            and run.dataset == "estimates"
            and run.status == "RUNNING"
            and run.scope_json == scope.envelope()
            and not run.checkpoint_json
            and run.started_at
            and scope.starts_at <= run.started_at <= datetime.now(UTC)
        ):
            raise ValueError("Acquisition is outside the approved scope or already claimed")
        authority = require_semantic_authority(run)
        work = db.get(WorkScopeRecord, authority.scope_id)
        expected = {ScopeMember("SYMBOL", symbol) for symbol in scope.symbols}
        if not (
            work
            and work.scope_kind == CAPTURE_SCOPE_KIND
            and work.membership_policy == "FROZEN"
            and work.acquisition_plan_id == authority.acquisition_plan_id
            and work.payload_json.get("scope_definition") == scope.envelope()
            and set(retained_scope_members(db, authority.scope_id)) == expected
            and db.scalar(
                select(BackgroundJob.id)
                .where(BackgroundJob.scope_id == authority.scope_id)
                .limit(1)
            )
            is None
            and db.scalar(
                select(CeriIngestionRunSourceRecord.id)
                .where(CeriIngestionRunSourceRecord.ingestion_run_id == run.id)
                .limit(1)
            )
            is None
        ):
            raise ValueError("Acquisition is outside the frozen membership or already used")
        run.checkpoint_json = {"policy": POLICY, "state": "CLAIMED"}
    return authority


def _record_failure(db, scope, receipt, status, reason):
    # The data transaction rolled back. Only its durable claim and the attempt audit remain.
    receipt["issuers"] = []
    receipt.pop("processing_run_id", None)
    receipt["reason"] = reason
    with db.begin():
        run = db.get(CeriIngestionRun, scope.ingestion_run_id, with_for_update=True)
        _finish(db, run, receipt, status)


def _finish(db, run, receipt, status):
    count = sum(len(row.get("observations", [])) for row in receipt["issuers"])
    duplicates = sum(len(row.get("already_seen_period_roles", [])) for row in receipt["issuers"])
    receipt.update(status=status, normalized=count)
    CeriSourceRecordService().finish_ingestion_run(
        db,
        run,
        status=status,
        requested_count=len(receipt["scope"]["symbols"]),
        fetched_count=count + duplicates,
        inserted_count=count,
        deduplicated_count=duplicates,
        corrected_count=0,
        quarantined_count=0,
        failed_count=int(status == "FAILED"),
        warning_count=sum(row["status"] == "UNAVAILABLE" for row in receipt["issuers"]),
        quota_state=receipt["requests"],
        checkpoint=receipt,
    )


def _capture(db, run, authority, scope, cache_dir, sec, eodhd, requests, receipt):
    estimates = {}
    sec_ids = []
    for symbol in sorted(scope.symbols):
        requests.check()
        try:
            with db.begin_nested():
                row, observations = _issuer(
                    db,
                    run,
                    scope,
                    symbol,
                    cache_dir,
                    sec,
                    eodhd,
                    remaining=scope.observations - len(estimates),
                )
        except (_SkipIssuer, SecEdgarError, EodhdProviderError) as exc:
            reason = str(exc) if isinstance(exc, _SkipIssuer) else "provider_unavailable"
            receipt["issuers"].append({"symbol": symbol, "status": "UNAVAILABLE", "reason": reason})
        else:
            sec_ids.append(row["authority_source_id"])
            estimates.update(observations)
            receipt["issuers"].append(row)
    if not estimates:
        return
    processing, created = CeriProcessingRunService().create_or_get(
        db,
        job_type="CERI_NORMALIZE_BATCH",
        request_key=f"{run.request_key}:normalize",
        scope=scope.envelope(),
        config_version=run.config_version,
        config_hash=run.config_hash,
        actor=scope.operator,
        cutoff_at=scope.cutoff_at,
        semantic_authority=authority,
    )
    if not created:
        raise _StopCapture("normalization_run_already_exists")
    capture = SecRevenueCurrencyCapture(
        ingestion_run_id=run.id, authority_source_ids=tuple(sec_ids)
    )
    result = CeriNormalizationService(sec_currency_capture=capture).normalize(
        db,
        processing_run=processing,
        ingestion_run_id=run.id,
        should_cancel=requests.cancelled,
    )
    if result.status != "COMPLETED" or result.normalized != len(estimates):
        raise _StopCapture("normalization_did_not_close")
    receipt["processing_run_id"] = processing.id
    _verify_normalized(db, estimates)


def _current_review(sec, proof, document, company):
    submissions = sec.submissions(proof.cik, fresh=True)
    review = StatementReview(
        document, proof.fiscal_end, proof.family, submissions, datetime.now(UTC)
    )
    filings = submissions.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    names = ("accessionNumber", "primaryDocument", "form", "acceptanceDateTime", "reportDate")
    if (
        not isinstance(submissions.get("tickers"), list)
        or not isinstance(recent, dict)
        or not all(isinstance(recent.get(k), list) for k in names)
    ):
        raise _SkipIssuer("invalid_submissions_metadata")
    if (
        submissions.get("name") != proof.entity
        or _matching_filing(review, company) != proof.accepted_at
    ):
        raise _SkipIssuer("reviewed_filing_stale_or_entity_changed")
    items = recent.get("items")
    for index, (form, accepted) in enumerate(
        zip(
            recent["form"],
            recent["acceptanceDateTime"],
            strict=True,
        )
    ):
        when = _time(accepted)
        if form in ("8-K", "8-K/A") and (when is None or when > proof.accepted_at):
            label = items[index] if isinstance(items, list) and len(items) > index else None
            material = {"2.01", "4.02", "5.01", "5.03", "8.01"}
            if (
                when is None
                or not isinstance(label, str)
                or not label
                or material.intersection(label.replace(" ", "").split(","))
            ):
                raise _SkipIssuer("issuer_event_requires_review")
    return review


def _fiscal_targets(
    proof: _ReviewedFiling, received_at: datetime | None, possessed: datetime, cutoff: datetime
) -> dict[str, str]:
    targets = dict(proof.fiscal_targets)
    received_at = _time(received_at)
    if not (
        targets.keys() == set(_PERIODS)
        and received_at
        and proof.accepted_at <= received_at <= possessed < cutoff
        and proof.fiscal_end < possessed.date()
        and cutoff.date() <= targets[CeriPeriodType.CURRENT_QUARTER]
    ):
        raise _SkipIssuer("fiscal_calendar_authority_unavailable")
    return {role.value: end.isoformat() for role, end in targets.items()}


def _select_revenue_records(
    candidates: Iterable[RawProviderRecord], remaining: int, targets: dict[str, str] | None
) -> list[RawProviderRecord]:
    limit = remaining
    if targets is not None:
        allow = set(targets.items())
        candidates = (
            record
            for record in candidates
            if (record.payload.get("period_type"), record.payload.get("fiscal_period_end")) in allow
        )
        # One extra match is sufficient to prove ambiguity. Unmatched history
        # never consumes the observation budget.
        limit = len(targets)
    records = list(islice(candidates, limit + 1))
    if len(records) > remaining and targets is None:
        raise _StopCapture("observation_budget_exhausted")
    if not records or len({record.payload.get("period_type") for record in records}) != len(
        records
    ):
        raise _SkipIssuer("empty_or_duplicate_provider_rows")
    if len(records) > remaining:
        raise _StopCapture("observation_budget_exhausted")
    return records


def _issuer(db, run, scope, symbol, cache_dir, sec, eodhd, *, remaining):
    proof = scope.filings()[symbol]
    company = db.scalars(
        select(CeriCompany).where(
            CeriCompany.ticker == symbol.removesuffix(".US"),
        )
    ).one_or_none()
    if company is None or company.cik != proof.cik or company.sec_applicability != "REQUIRED":
        raise _SkipIssuer("issuer_cik_mismatch")
    document = db.get(CeriSecFilingDocument, proof.registry_id)
    if (
        not document
        or document.cik != proof.cik
        or document.accession_number != proof.accession
        or document.last_content_hash != proof.sha256
    ):
        raise _SkipIssuer("reviewed_registry_document_mismatch")
    review = _current_review(sec, proof, document, company)
    source_id = register_statement(
        db,
        run_id=run.id,
        company_id=company.id,
        review=review,
        cache_dir=cache_dir,
    )
    source = db.get(CeriSourceRecord, source_id) if source_id else None
    authority_payload = source.restricted_normalized_json if source else {}
    submissions_hash = source_record_content_hash(review.submissions)
    if (
        not source
        or source.dataset != DATASET
        or source.provider != "sec"
        or not authority_payload
        or authority_payload.get("currency") != proof.currency
        or authority_payload.get("cik") != proof.cik
        or authority_payload.get("filing_accession") != proof.accession
        or authority_payload.get("filing_registry_id") != proof.registry_id
        or authority_payload.get("document_sha256") != proof.sha256
        or authority_payload.get("eodhd_ingestion_run_id") != run.id
        or authority_payload.get("submissions_sha256") != submissions_hash
        or _time(authority_payload.get("submissions_received_at")) != review.submissions_received_at
    ):
        raise _SkipIssuer("approved_authority_missing_or_mismatched")
    possessed = source_record_historical_eligibility_at(source)
    if possessed is None or not run.started_at <= possessed < scope.cutoff_at:
        raise _SkipIssuer("authority_possession_unproven")
    targets = None
    if scope.reviewed_filings or symbol == "ABBV.US":
        targets = _fiscal_targets(proof, document.last_downloaded_at, possessed, scope.cutoff_at)
    acquisition = EstimateRequest(company.id, symbol, (CeriMetric.REVENUE,), _PERIODS)
    records = _select_revenue_records(
        eodhd.fetch_estimate_snapshots(acquisition), remaining, targets
    )
    post_review = _current_review(sec, proof, document, company)
    capture = SecRevenueCurrencyCapture(ingestion_run_id=run.id, authority_source_ids=(source_id,))
    prepared = capture.prepare(db).get(company.id)
    writer = CeriSourceRecordService()
    observations = {}
    row = {
        "symbol": symbol,
        "status": "CAPTURED",
        "cik": proof.cik,
        "accession": proof.accession,
        "document_sha256": proof.sha256,
        "authority_source_id": source_id,
        "authority_content_hash": source.content_hash,
        "authority_possessed_at": possessed.isoformat(),
        "filing_accepted_at": proof.accepted_at.isoformat(),
        "document_received_at": document.last_downloaded_at.isoformat(),
        "submissions_received_at": review.submissions_received_at.isoformat(),
        "submissions_sha256": submissions_hash,
        "post_submissions_received_at": post_review.submissions_received_at.isoformat(),
        "post_submissions_sha256": source_record_content_hash(post_review.submissions),
        "observations": [],
    }
    if targets is not None:
        row["fiscal_targets"] = targets
    already_seen = []
    for record in sorted(records, key=lambda r: r.provider_record_id):
        amount = _qualify_observation(record, symbol, possessed, scope.cutoff_at)
        try:
            with db.begin_nested():
                stored = writer.store_source_record(
                    db,
                    ingestion_run_id=run.id,
                    record=record,
                    raw_payload_allowed=False,
                )
                if scope.reviewed_filings and stored.deduplicated and not stored.quarantined:
                    # The writer associated an older fact. Roll back that association so only
                    # newly possessed facts owned by this run reach the normalizer.
                    raise _DuplicateObservation
        except _DuplicateObservation:
            already_seen.append(record.payload["period_type"])
            continue
        estimate = stored.source_record
        binding = capture.resolve(estimate, prepared)
        if (
            not stored.inserted
            or stored.quarantined
            or not binding
            or binding.source_record_id != source_id
            or binding.currency != proof.currency
        ):
            raise _SkipIssuer("new_owned_observation_cannot_bind")
        link = {
            "source_record_id": estimate.id,
            "source_sha256": estimate.content_hash,
            "authority_source_id": source_id,
            "currency": proof.currency,
            "scale": 1,
            "observed_at": record.observed_at.isoformat(),
            "received_at": record.retrieved_at.isoformat(),
            "fiscal_period_end": record.payload["fiscal_period_end"],
            "period_type": record.payload["period_type"],
        }
        row["observations"].append(link)
        observations[estimate.id] = (company.id, amount, link)
    if not observations:
        raise _SkipIssuer("no_new_provider_observations")
    if already_seen:
        row["already_seen_period_roles"] = already_seen
    return row, observations


def _qualify_observation(record: RawProviderRecord, symbol, possessed, cutoff) -> Decimal:
    payload = record.payload
    if not (
        record.provider == "eodhd"
        and record.dataset == CeriDataset.ESTIMATES
        and payload.get("provider_company_id") == symbol
        and payload.get("metric") == "REVENUE"
        and _time(record.observed_at)
        and _time(record.retrieved_at)
        and possessed < record.observed_at <= record.retrieved_at < cutoff
    ):
        raise _SkipIssuer("estimate_identity_or_observation_time_invalid")
    try:
        value = Decimal(str(payload["consensus"]))
    except (KeyError, InvalidOperation):
        raise _SkipIssuer("estimate_consensus_unavailable") from None
    if not value.is_finite():
        raise _SkipIssuer("estimate_consensus_unavailable")
    return value


def _verify_normalized(db, estimates):
    normalized = {
        row.source_record_id: row
        for row in db.scalars(
            select(CeriEstimateSnapshot).where(
                CeriEstimateSnapshot.source_record_id.in_(tuple(estimates))
            )
        )
    }
    for record_id, (company, amount, link) in estimates.items():
        row = normalized.get(record_id)
        if not (
            row
            and row.company_id == company
            and row.metric == "REVENUE"
            and row.currency_verified
            and row.currency_basis == "filing_currency"
            and row.conversion_source_record_id == link["authority_source_id"]
            and row.fiscal_period_end.isoformat() == link["fiscal_period_end"]
            and row.period_type == link["period_type"]
            and row.source_currency == row.canonical_currency == link["currency"]
            and row.source_scale == row.canonical_scale == 1
            and row.consensus == amount
        ):
            raise _StopCapture("persisted_estimate_lineage_or_amount_mismatch")
        link["estimate_id"] = row.id
