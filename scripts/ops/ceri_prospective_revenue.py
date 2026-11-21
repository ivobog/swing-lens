"""Owner-approved foreground invocation for the one F8 prospective capture."""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Timer
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriIngestionRun, CeriSecFilingDocument
from app.services.alembic_heads import cached_repository_alembic_heads
from app.services.ceri.config import load_ceri_config
from app.services.ceri.normalization_service import CeriNormalizationCancelled
from app.services.ceri.providers.eodhd_client import EodhdClientConfig
from app.services.ceri.sec import prospective_capture as capture
from app.services.ceri.sec.client import SecClientConfig
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.reporting_currency import CANDIDATE_FORMS, PERIODIC, _issuer_conflict
from app.services.ceri.sec.statement_currency import extract_presentation_currency
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.services.scope_refresh_adoption import admit_frozen_operation
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.settings import RuntimeMode, get_settings

GRANT = "f8:e6c5ba72e1de8190e48e296bdf72ea3de1411688:sec-reporting-currency-v1"
REQUEST_KEY = f"ceri:prospective:{GRANT}"
OWNER = "ivobog"
NAMESPACE = "swinglens-ceri-prospective"
ROOT = Path(__file__).resolve().parents[2]
SHA256 = r"^[0-9a-f]{64}$"


class Unavailable(ValueError):
    """A safe, printable reason for rejecting a proposed operation."""


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation: Literal["reserve", "execute"]
    ingestion_run_id: int | None = Field(gt=0)
    source_grant: Literal[GRANT]
    request_key: Literal[REQUEST_KEY]
    operator: str = Field(min_length=1, max_length=160)
    principal: str
    approval_reference: str = Field(min_length=1, max_length=160)
    git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime_fingerprint: str = Field(pattern=SHA256)
    database_fingerprint: str = Field(pattern=SHA256)
    database_principal: str
    alembic_head: str
    starts_at: datetime
    cutoff_at: datetime
    symbols: tuple[str, ...]
    sec_attempts: int
    eodhd_attempts: int
    observations: int
    entitlement_reference: str = Field(min_length=1, max_length=160)
    entitlement_checked_at: datetime
    entitlement_valid_until: datetime
    included_calendar_units: int = Field(ge=1)

    def scope(self) -> capture.RevenueCaptureScope:
        fingerprint = _digest(
            self.model_dump(mode="json", exclude={"operation", "ingestion_run_id"})
        )
        return capture.RevenueCaptureScope(
            ingestion_run_id=self.ingestion_run_id or 1,
            request_key=self.request_key,
            operator=self.operator,
            approval_reference=f"{self.approval_reference}#{fingerprint}",
            starts_at=self.starts_at,
            cutoff_at=self.cutoff_at,
            symbols=self.symbols,
            sec_attempts=self.sec_attempts,
            eodhd_attempts=self.eodhd_attempts,
            observations=self.observations,
        )

    def validate_window(self, operation: str) -> None:
        now = datetime.now(UTC)
        self.scope().validate()
        if (self.ingestion_run_id is None) != (self.operation == "reserve"):
            raise Unavailable("invalid_approval_acquisition_id")
        if operation != "inspect" and (
            operation != self.operation or not self.starts_at <= now < self.cutoff_at
        ):
            raise Unavailable("approval_operation_or_window_mismatch")
        if (
            now >= self.cutoff_at
            or self.entitlement_checked_at.utcoffset() != UTC.utcoffset(None)
            or self.entitlement_valid_until.utcoffset() != UTC.utcoffset(None)
            or self.entitlement_checked_at > now
            or self.cutoff_at > self.entitlement_valid_until
            or self.included_calendar_units < self.eodhd_attempts
            or not self.entitlement_reference.strip()
        ):
            raise Unavailable("current_included_calendar_entitlement_evidence_required")


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _windows_system_dir() -> Path:
    path = ctypes.create_unicode_buffer(32768)
    if not 0 < ctypes.windll.kernel32.GetSystemDirectoryW(path, len(path)) < len(path):
        raise Unavailable("windows_system_identity_unavailable")
    return Path(path.value)


def _principal() -> str:
    if os.name != "nt":
        return f"uid:{os.geteuid()}"
    output = subprocess.check_output(
        [str(_windows_system_dir() / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
        timeout=5,
    ).decode("ascii", errors="replace")
    _, sid = next(csv.reader(output.splitlines()))
    if not sid.startswith("S-1-"):
        raise Unavailable("windows_token_sid_unavailable")
    return f"sid:{sid}"


def _trust() -> tuple[Path, Path]:
    if os.name == "nt":
        system = _windows_system_dir()
        signers = Path(system.anchor) / "Program Files/SwingLens/ceri-allowed-signers"
        executable = system / "OpenSSH/ssh-keygen.exe"
    else:
        signers = Path("/etc/swinglens/ceri-allowed-signers")
        executable = Path("/usr/bin/ssh-keygen")
        for path in (signers.parent.parent, signers.parent, signers):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise Unavailable("owner_trust_store_permissions")
    if not signers.is_file() or signers.resolve() != signers:
        raise Unavailable("owner_trust_store_unavailable")
    return executable, signers


def _unique_fields(items):
    result = dict(items)
    if len(result) != len(items):
        raise Unavailable("duplicate_approval_field")
    return result


def _approval(path: Path, signature: Path) -> Approval:
    with path.open("rb") as file:
        content = file.read(16385)
    if len(content) > 16384:
        raise Unavailable("approval_too_large")
    executable, signers = _trust()
    result = subprocess.run(
        [
            str(executable),
            "-Y",
            "verify",
            "-f",
            str(signers),
            "-I",
            OWNER,
            "-n",
            NAMESPACE,
            "-s",
            str(signature),
        ],
        input=content,
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise Unavailable("owner_signature_invalid")
    json.loads(content, object_pairs_hook=_unique_fields)
    return Approval.model_validate_json(content)


@dataclass(frozen=True)
class Runtime:
    git_sha: str
    principal: str
    fingerprint: str
    head: str
    cache_dir: Path
    config_version: str
    config_hash: str
    terms: str
    sec: SecClientConfig
    eodhd: EodhdClientConfig


def _runtime(settings) -> Runtime:
    git_environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    git_sha = (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, env=git_environment, timeout=5
        )
        .decode()
        .strip()
    )
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=ROOT,
        env=git_environment,
        timeout=5,
    )
    if (
        Path.cwd() != ROOT
        or dirty
        or settings.runtime_mode != RuntimeMode.NORMAL
        or os.environ.get("GIT_SHA", git_sha) != git_sha
    ):
        raise Unavailable("clean_normal_release_checkout_required")
    if not (settings.ceri_enabled and settings.ceri_provider_ingest_enabled):
        raise Unavailable("ceri_provider_ingestion_disabled")
    (head,) = cached_repository_alembic_heads(ROOT)
    config = load_ceri_config(settings.ceri_config_path, settings.ceri_taxonomy_path)
    cache_dir = settings.cache_dir.resolve()
    sec = SecClientConfig(
        user_agent=settings.sec_user_agent,
        requests_per_second=settings.sec_requests_per_second,
        timeout_seconds=settings.sec_http_timeout_seconds,
    )
    eodhd = EodhdClientConfig(
        api_key=settings.eodhd_api_key,
        base_url=settings.eodhd_base_url,
        timeout_seconds=settings.eodhd_http_timeout_seconds,
        requests_per_minute=settings.eodhd_requests_per_minute,
        daily_call_budget=settings.eodhd_daily_call_budget,
    )
    if (
        not eodhd.api_key
        or eodhd.api_key.strip().lower() in {"", "demo"}
        or eodhd.base_url.rstrip("/") != "https://eodhd.com"
        or not 0 < eodhd.timeout_seconds <= 30
        or not 0 < sec.timeout_seconds <= 30
        or "@" not in sec.user_agent
        or "example.invalid" in sec.user_agent.lower()
        or not settings.eodhd_terms_version.strip()
    ):
        raise Unavailable("required_provider_configuration_unavailable")
    fingerprint = _digest(
        {
            "git_sha": git_sha,
            "root": str(ROOT),
            "python": str(Path(sys.executable).resolve()),
            "head": head,
            "ceri_config": config.config_hash,
            "cache": str(cache_dir),
            "terms": settings.eodhd_terms_version,
            "sec": vars(sec),
            "eodhd": vars(eodhd),
            "image_digest": os.environ.get("IMAGE_DIGEST"),
        }
    )
    return Runtime(
        git_sha,
        _principal(),
        fingerprint,
        head,
        cache_dir,
        config.engine.config_version,
        config.config_hash,
        settings.eodhd_terms_version,
        sec,
        eodhd,
    )


def _database(db: Session) -> tuple[str, str, str]:
    identity = db.execute(
        text(
            "SELECT system_identifier::text, current_database(), oid, inet_server_port(), "
            "session_user, current_user FROM pg_control_system(), pg_database "
            "WHERE datname = current_database()"
        )
    ).one()
    cluster, name, oid, port, login, role = identity
    if login != role or port is None:
        raise Unavailable("database_session_principal_or_target_unverified")
    (head,) = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    return _digest([cluster, name, oid, port]), role, head


def _cache_checks(db: Session, symbols: tuple[str, ...], cache_dir: Path) -> dict[str, str]:
    cache = SecDocumentContentCache(cache_dir)
    results = {}
    for symbol in symbols:
        proof = capture._REVIEWED[symbol]
        company = db.scalars(
            select(CeriCompany).where(CeriCompany.ticker == symbol.removesuffix(".US"))
        ).one_or_none()
        doc = db.get(CeriSecFilingDocument, proof.registry_id)
        if not (
            company
            and company.cik == proof.cik
            and company.sec_applicability == "REQUIRED"
            and not _issuer_conflict(db, company, datetime.now(UTC).date())
            and doc
            and doc.cik == proof.cik
            and doc.accession_number == proof.accession
            and doc.last_content_hash == proof.sha256
            and doc.last_downloaded_at
            and proof.accepted_at <= doc.last_downloaded_at <= datetime.now(UTC)
            and doc.filing_date
            and doc.form in PERIODIC
            and 0 < (doc.last_content_bytes or 0) <= 16_000_000
        ):
            results[symbol] = "reviewed_registry_or_issuer_mismatch"
            continue
        body = cache.load(doc, expected_hash=proof.sha256)
        if (
            not body
            or extract_presentation_currency(
                body,
                cik=proof.cik,
                fiscal_end=proof.fiscal_end,
                annual=proof.family == "ANNUAL",
                reviewed_interim=doc.form == "6-K",
            )
            != proof.currency
        ):
            results[symbol] = "reviewed_cache_or_currency_unavailable"
            continue
        competing = db.scalar(
            select(CeriSecFilingDocument.id)
            .where(
                CeriSecFilingDocument.cik == proof.cik,
                CeriSecFilingDocument.accession_number != proof.accession,
                CeriSecFilingDocument.form.in_(CANDIDATE_FORMS),
                CeriSecFilingDocument.filing_date >= doc.filing_date,
                CeriSecFilingDocument.first_seen_at <= datetime.now(UTC),
            )
            .limit(1)
        )
        results[symbol] = "locally_competing_filing" if competing else "cached_evidence_verified"
    return results


def _inspect(engine: Engine, runtime: Runtime, approval: Approval | None, operation: str):
    if engine.dialect.name != "postgresql":
        raise Unavailable("postgresql_required")
    if approval:
        approval.validate_window(operation)
        if approval.eodhd_attempts > runtime.eodhd.daily_call_budget:
            raise Unavailable("configured_calendar_budget_too_low")
        if (runtime.git_sha, runtime.principal, runtime.fingerprint, runtime.head) != (
            approval.git_sha,
            approval.principal,
            approval.runtime_fingerprint,
            approval.alembic_head,
        ):
            raise Unavailable("operator_or_runtime_identity_mismatch")
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        fingerprint, role, head = _database(db)
        if (
            head != runtime.head
            or approval
            and (fingerprint, role)
            != (
                approval.database_fingerprint,
                approval.database_principal,
            )
        ):
            raise Unavailable("database_identity_or_schema_mismatch")
        symbols = approval.symbols if approval else tuple(sorted(capture._REVIEWED))
        evidence = _cache_checks(db, symbols, runtime.cache_dir)
    return {
        "git_sha": runtime.git_sha,
        "principal": runtime.principal,
        "runtime_fingerprint": runtime.fingerprint,
        "alembic_head": head,
        "database_fingerprint": fingerprint,
        "database_principal": role,
        "issuers": evidence,
        "ready": bool(
            approval and all(result == "cached_evidence_verified" for result in evidence.values())
        ),
        "owner_approval_verified": approval is not None,
    }


def _reserve(engine: Engine, runtime: Runtime, approval: Approval) -> dict:
    scope = approval.scope()
    with Session(engine) as db, db.begin():
        # Serialize reservation of the one-use grant even if two callers arrive together.
        lock = int.from_bytes(hashlib.sha256(REQUEST_KEY.encode()).digest()[:8], signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
        if _database(db) != (
            approval.database_fingerprint,
            approval.database_principal,
            runtime.head,
        ):
            raise Unavailable("database_identity_or_schema_mismatch")
        approval.validate_window("reserve")
        if db.scalar(
            select(CeriIngestionRun.id).where(CeriIngestionRun.request_key == REQUEST_KEY)
        ):
            raise Unavailable("source_grant_already_reserved")
        authority = admit_frozen_operation(
            db,
            operation_kind=capture.CAPTURE_SCOPE_KIND,
            subject_kind="SYMBOL",
            members=tuple(ScopeMember("SYMBOL", s) for s in scope.symbols),
            cycle_key=scope.request_key,
            business_cutoff=scope.cutoff_at,
            provider_source_class="eodhd",
            request_type="estimates",
            requirements=(AcquisitionRequirement("REVENUE"),),
            policy_identity=capture.POLICY,
            scope_definition=scope.envelope(),
            configuration_identity=runtime.config_hash,
        )
        run = CeriSourceRecordService().create_ingestion_run(
            db,
            provider="eodhd",
            provider_terms_version=runtime.terms,
            dataset="estimates",
            scope=scope.envelope(),
            request_key=scope.request_key,
            config_version=runtime.config_version,
            config_hash=runtime.config_hash,
            semantic_authority=authority,
        )
        approval.validate_window("reserve")
        return {
            "status": "RESERVED",
            "ingestion_run_id": run.id,
            "request_key": run.request_key,
            **authority.as_dict(),
        }


def _run(operation, engine, runtime, approval, *, cancelled=lambda: False, open_response=None):
    result = _inspect(engine, runtime, approval, operation)
    if operation == "inspect":
        return result
    if not result["ready"]:
        raise Unavailable("approved_cache_evidence_unavailable")
    if operation == "reserve":
        return _reserve(engine, runtime, approval)
    with engine.connect() as connection, Session(connection) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        if _database(db) != (
            approval.database_fingerprint,
            approval.database_principal,
            runtime.head,
        ):
            raise Unavailable("database_identity_or_schema_mismatch")
        db.rollback()
        return capture.capture_prospective_revenue(
            db,
            approval.scope(),
            cache_dir=runtime.cache_dir,
            sec_config=runtime.sec,
            eodhd_config=runtime.eodhd,
            should_cancel=cancelled,
            open_response=open_response,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "reserve", "execute"))
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--signature", type=Path)
    args = parser.parse_args()
    try:
        if bool(args.approval) != bool(args.signature) or not (
            args.approval or args.operation == "inspect"
        ):
            raise Unavailable("signed_owner_approval_required")
        approval = _approval(args.approval, args.signature) if args.approval else None
        if approval:
            approval.validate_window(args.operation)
        settings = get_settings()
        runtime = _runtime(settings)
        url = make_url(settings.database_url)
        if (
            url.drivername != "postgresql+psycopg"
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or url.query
        ):
            raise Unavailable("direct_loopback_postgresql_required")
        stop = Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        remaining = (approval.cutoff_at - datetime.now(UTC)).total_seconds() if approval else 10
        milliseconds = max(1, int(remaining * 1000))
        engine = create_engine(
            url,
            connect_args={
                "connect_timeout": 5,
                "application_name": "swinglens-ceri-prospective",
                "options": f"-c lock_timeout=1500 -c statement_timeout={milliseconds}",
            },
        )
        # The owner and run ID can reconcile an interrupted commit from the durable audit.
        deadline_seconds = (
            (approval.cutoff_at - datetime.now(UTC)).total_seconds() if approval else 10
        )
        deadline = Timer(max(0, deadline_seconds), lambda: os._exit(124))
        if args.operation != "inspect":
            deadline.start()
        try:
            receipt = _run(args.operation, engine, runtime, approval, cancelled=stop.is_set)
        finally:
            deadline.cancel()
            engine.dispose()
        print(json.dumps(receipt))
        if args.operation == "inspect":
            return 0 if receipt["ready"] else 2
        return 0 if receipt["status"] in {"RESERVED", "COMPLETED", "PARTIAL"} else 2
    except Unavailable as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
    except CeriNormalizationCancelled:
        print(json.dumps({"status": "CANCELLED", "reason": "cancelled_before_claim"}))
    except Exception as exc:
        # Validation and connection errors can include credentials or signed approval contents.
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
