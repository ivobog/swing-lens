from __future__ import annotations

import csv
import hashlib
import io
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import IBExecutionFill, IBFlexImportRun
from app.services.ib_market_intelligence.dtos import FlexExecutionDTO
from app.services.ib_market_intelligence.evidence_hash import evidence_hash
from app.services.ib_market_intelligence.request_budget import IBRequestBudget
from app.services.ib_market_intelligence.resilience import (
    RetryEvent,
    RetryPolicy,
    cancellable_sleep,
    retry_call,
)
from app.services.operational_metrics import operational_metrics
from app.services.redaction import redact_sensitive
from app.settings import Settings, get_settings


class IBFlexError(RuntimeError):
    pass


class IBFlexReportPending(IBFlexError):
    pass


Transport = Callable[[str, float], str]


class IBFlexClient:
    """Reporting-only Flex HTTPS client, intentionally independent of TWS/IB Gateway."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        timeout_seconds: float = 30,
        budget: IBRequestBudget | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retry_policy: RetryPolicy | None = None,
        guard: Callable[[], None] | None = None,
        on_retry: Callable[[RetryEvent], None] | None = None,
    ) -> None:
        if not token:
            raise IBFlexError("IB Flex token is not configured")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.budget = budget
        self.transport = transport or self._urllib_transport
        self.sleep = sleep
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=1)
        self.guard = guard
        self.on_retry = on_retry

    def send_request(self, query_id: str) -> str:
        if not query_id:
            raise IBFlexError("IB Flex query ID is not configured")
        if self.budget:
            self.budget.acquire_flex_send(guard=self.guard)
        url = self._url(
            "FlexStatementService.SendRequest", {"t": self._token, "q": query_id, "v": "3"}
        )
        started = time.monotonic()
        payload = self._request(url)
        operational_metrics.increment(
            "swinglens_ibmi_flex_send_duration_seconds",
            value=time.monotonic() - started,
        )
        root = _xml_root(payload, "Flex SendRequest")
        status = (_xml_text(root, "Status") or "").lower()
        if status != "success":
            raise IBFlexError(_xml_text(root, "ErrorMessage") or "Flex SendRequest failed")
        reference = _xml_text(root, "ReferenceCode")
        if not reference:
            raise IBFlexError("Flex SendRequest response omitted ReferenceCode")
        return reference

    def get_statement(self, reference_code: str) -> str:
        url = self._url(
            "FlexStatementService.GetStatement",
            {"t": self._token, "q": reference_code, "v": "3"},
        )
        started = time.monotonic()
        payload = self._request(url)
        operational_metrics.increment(
            "swinglens_ibmi_flex_get_duration_seconds",
            value=time.monotonic() - started,
        )
        if payload.lstrip().startswith("<FlexStatementResponse"):
            root = _xml_root(payload, "Flex GetStatement")
            code = _xml_text(root, "ErrorCode")
            message = _xml_text(root, "ErrorMessage") or "Flex report is not ready"
            if (
                code in {"1018", "1019", "1020"}
                or "not ready" in message.lower()
                or "generation" in message.lower()
            ):
                raise IBFlexReportPending(message)
            raise IBFlexError(message)
        return payload

    def download(
        self,
        query_id: str,
        *,
        attempts: int,
        poll_seconds: float,
        reference_code: str | None = None,
        start_attempt: int = 0,
        on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
        guard: Callable[[], None] | None = None,
    ) -> tuple[str, str]:
        effective_guard = guard or self.guard
        if effective_guard:
            effective_guard()
        reference = reference_code or self.send_request(query_id)
        for attempt in range(start_attempt, attempts):
            if effective_guard:
                effective_guard()
            if on_checkpoint:
                on_checkpoint(
                    {
                        "phase": "get_statement",
                        "reference_code_hash": _fingerprint(reference),
                        "_reference_code": reference,
                        "attempt": attempt + 1,
                    }
                )
            try:
                content = self.get_statement(reference)
                if on_checkpoint:
                    on_checkpoint(
                        {
                            "phase": "downloaded",
                            "reference_code_hash": _fingerprint(reference),
                            "_reference_code": reference,
                            "attempt": attempt + 1,
                            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        }
                    )
                return reference, content
            except IBFlexReportPending:
                if on_checkpoint:
                    on_checkpoint(
                        {
                            "phase": "get_statement",
                            "reference_code_hash": _fingerprint(reference),
                            "_reference_code": reference,
                            "attempt": attempt + 1,
                            "next_attempt": attempt + 2,
                        }
                    )
                if attempt + 1 >= attempts:
                    raise
                cancellable_sleep(
                    poll_seconds,
                    sleep=self.sleep,
                    guard=effective_guard,
                )
        raise IBFlexReportPending("Flex report did not become ready")

    def _request(self, url: str) -> str:
        try:
            return retry_call(
                lambda: self.transport(url, self.timeout_seconds),
                operation_name="IBKR Flex HTTPS request",
                policy=self.retry_policy,
                sleep=self.sleep,
                guard=self.guard,
                on_retry=self.on_retry,
            )
        except Exception as exc:
            safe = str(redact_sensitive(str(exc))).replace(self._token, "[REDACTED]")
            raise IBFlexError(safe) from None

    def _url(self, service: str, params: dict[str, str]) -> str:
        return f"{self.base_url}/{service}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _urllib_transport(url: str, timeout: float) -> str:
        request = urllib.request.Request(
            url, headers={"User-Agent": "SwingLens/0.1 Flex reporting"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured IB endpoint
            return response.read().decode("utf-8-sig")


def parse_flex_report(content: str, *, report_timezone: str = "UTC") -> list[FlexExecutionDTO]:
    stripped = content.lstrip()
    rows = (
        _xml_execution_rows(content) if stripped.startswith("<") else _csv_execution_rows(content)
    )
    executions: list[FlexExecutionDTO] = []
    errors: list[str] = []
    for index, row in enumerate(rows, start=1):
        try:
            executions.append(_normalize_execution(row, report_timezone=report_timezone))
        except (ValueError, InvalidOperation) as exc:
            errors.append(f"row {index}: {exc}")
    if errors:
        raise IBFlexError("Flex execution schema/values invalid: " + "; ".join(errors[:10]))
    return executions


def import_flex_report(
    db: Session,
    *,
    content: str,
    query_type: str,
    query_id: str,
    reference_code: str | None,
    dry_run: bool = False,
    intelligence_run_id: int | None = None,
    now: datetime | None = None,
    report_timezone: str = "UTC",
) -> dict[str, Any]:
    import_started = time.monotonic()
    now = now or datetime.now(UTC)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing_run = db.scalar(
        select(IBFlexImportRun)
        .where(IBFlexImportRun.content_hash == digest)
        .where(IBFlexImportRun.dry_run.is_(False))
        .where(IBFlexImportRun.status == "COMPLETED")
    )
    if existing_run is not None and not dry_run:
        operational_metrics.increment(
            "swinglens_ibmi_flex_duplicate_reports_total", query_type=query_type
        )
        return {"status": "DUPLICATE_REPORT", "import_run_id": existing_run.id, "inserted": 0}
    executions = parse_flex_report(content, report_timezone=report_timezone)
    missing_order_reference_count = sum(
        execution.order_reference is None for execution in executions
    )
    if dry_run:
        operational_metrics.increment(
            "swinglens_ibmi_flex_import_duration_seconds",
            value=time.monotonic() - import_started,
            query_type=query_type,
        )
        operational_metrics.increment(
            "swinglens_ibmi_flex_import_rows_total",
            value=len(executions),
            query_type=query_type,
        )
        return {
            "status": "DRY_RUN",
            "rows": len(executions),
            "symbols": sorted({execution.symbol for execution in executions}),
            "content_hash": digest,
            "missing_order_reference_count": missing_order_reference_count,
        }
    run = IBFlexImportRun(
        intelligence_run_id=intelligence_run_id,
        query_type=query_type,
        query_id_fingerprint=_fingerprint(query_id),
        reference_code_hash=_fingerprint(reference_code) if reference_code else None,
        content_hash=digest,
        output_format="XML" if content.lstrip().startswith("<") else "TEXT",
        status="RUNNING",
        dry_run=False,
        row_count=len(executions),
        started_at=now,
    )
    db.add(run)
    db.flush()
    inserted = duplicates = corrected = 0
    for execution in executions:
        same_hash = db.scalar(
            select(IBExecutionFill).where(
                IBExecutionFill.raw_record_hash == execution.raw_record_hash
            )
        )
        if same_hash is not None:
            duplicates += 1
            continue
        superseded = None
        if execution.external_execution_id:
            superseded = db.scalar(
                select(IBExecutionFill)
                .where(IBExecutionFill.external_execution_id == execution.external_execution_id)
                .where(IBExecutionFill.is_superseded.is_(False))
                .order_by(IBExecutionFill.id.desc())
            )
        if superseded:
            superseded.is_superseded = True
            db.flush()
        fill = IBExecutionFill(
            flex_import_run_id=run.id,
            external_execution_id=execution.external_execution_id,
            account_hash=execution.account_hash,
            account_masked_label=execution.account_masked_label,
            symbol=execution.symbol,
            conid=execution.conid,
            asset_class=execution.asset_class,
            side=execution.side,
            execution_time=execution.trade_time,
            quantity=execution.quantity,
            price=execution.price,
            currency=execution.currency,
            exchange=execution.exchange,
            commission=execution.commission,
            fees=execution.fees,
            broker_realized_pnl=execution.broker_realized_pnl,
            order_reference=execution.order_reference,
            raw_record_hash=execution.raw_record_hash,
            supersedes_fill_id=superseded.id if superseded else None,
        )
        db.add(fill)
        if superseded:
            corrected += 1
        inserted += 1
    run.inserted_count = inserted
    run.duplicate_count = duplicates
    run.corrected_count = corrected
    run.status = "COMPLETED"
    run.completed_at = now
    db.flush()
    operational_metrics.increment(
        "swinglens_ibmi_flex_import_duration_seconds",
        value=time.monotonic() - import_started,
        query_type=query_type,
    )
    operational_metrics.increment(
        "swinglens_ibmi_flex_import_rows_total",
        value=len(executions),
        query_type=query_type,
    )
    return {
        "status": "COMPLETED",
        "import_run_id": run.id,
        "rows": len(executions),
        "inserted": inserted,
        "duplicates": duplicates,
        "corrected": corrected,
        "missing_order_reference_count": missing_order_reference_count,
    }


def flex_client_from_settings(
    settings: Settings | None = None,
    *,
    budget: IBRequestBudget | None = None,
    transport: Transport | None = None,
    guard: Callable[[], None] | None = None,
    on_retry: Callable[[RetryEvent], None] | None = None,
) -> IBFlexClient:
    settings = settings or get_settings()
    return IBFlexClient(
        token=settings.ib_flex_token or "",
        base_url=settings.ib_flex_base_url,
        timeout_seconds=settings.ib_flex_http_timeout_seconds,
        budget=budget,
        transport=transport,
        guard=guard,
        on_retry=on_retry,
        retry_policy=RetryPolicy(
            max_attempts=settings.ib_intelligence_request_max_attempts,
            initial_backoff_seconds=settings.ib_intelligence_retry_initial_seconds,
            max_backoff_seconds=settings.ib_intelligence_retry_max_seconds,
        ),
    )


def _normalize_execution(row: dict[str, str], *, report_timezone: str) -> FlexExecutionDTO:
    normalized = {str(key).strip().lower(): (value or "").strip() for key, value in row.items()}
    symbol = _first(normalized, "symbol", "underlyingsymbol")
    side = _normalize_side(_first(normalized, "buy/sell", "buysell", "side", "transactiontype"))
    quantity = abs(_decimal(_first(normalized, "quantity", "tradequantity", "qty"), required=True))
    price = _decimal(_first(normalized, "tradeprice", "price", "trade_price"), required=True)
    trade_time = _parse_datetime(normalized, report_timezone=report_timezone)
    if not symbol:
        raise ValueError("missing required symbol")
    if quantity <= 0 or price <= 0:
        raise ValueError("quantity and price must be positive")
    account = _first(normalized, "accountid", "account", "accountnumber")
    raw_hash = evidence_hash(normalized)
    return FlexExecutionDTO(
        external_execution_id=_optional(_first(normalized, "tradeid", "executionid", "execid")),
        trade_time=trade_time,
        symbol=symbol.upper(),
        conid=_optional_int(_first(normalized, "conid", "contractid")),
        side=side,
        quantity=quantity,
        price=price,
        exchange=_optional(_first(normalized, "exchange", "listingexchange")),
        commission=_decimal(_first(normalized, "ibcommission", "commission"), default=Decimal("0")),
        fees=_decimal(
            _first(normalized, "fees", "regulatoryfee", "otherfees"), default=Decimal("0")
        ),
        currency=_optional(_first(normalized, "currency", "currencyprimary")),
        asset_class=_optional(_first(normalized, "assetclass", "assetcategory")),
        order_reference=_optional(
            _first(normalized, "orderreference", "orderref", "iborderid", "orderid")
        ),
        account_hash=_fingerprint(account) if account else None,
        account_masked_label=f"***{account[-4:]}" if account else None,
        broker_realized_pnl=_optional_decimal(
            _first(normalized, "fifoPnlRealized".lower(), "realizedpnl", "realizedpl")
        ),
        raw_record_hash=raw_hash,
    )


def _csv_execution_rows(content: str) -> list[dict[str, str]]:
    sample = content[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [dict(row) for row in csv.DictReader(io.StringIO(content), dialect=dialect)]


def _xml_execution_rows(content: str) -> list[dict[str, str]]:
    root = _xml_root(content, "Flex report")
    tags = {"Trade", "TradeConfirmation", "Execution", "Transaction"}
    rows = [dict(element.attrib) for element in root.iter() if element.tag.split("}")[-1] in tags]
    if not rows:
        raise IBFlexError("Flex XML report contains no supported execution rows")
    return rows


def _parse_datetime(row: dict[str, str], *, report_timezone: str) -> datetime:
    combined = _first(row, "datetime", "date/time", "date_time")
    if not combined:
        trade_date = _first(row, "tradedate", "date", "reportdate")
        trade_time = _first(row, "tradetime", "time") or "00:00:00"
        combined = f"{trade_date} {trade_time}".strip()
    parsed: datetime | None = None
    for fmt in (
        "%Y%m%d;%H%M%S",
        "%Y%m%d %H%M%S",
        "%Y%m%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(combined, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(combined.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(UTC)
        except ValueError as exc:
            raise ValueError(f"unsupported execution timestamp {combined!r}") from exc
    timezone_name = _first(row, "timezone", "timezonename", "datetimezone") or report_timezone
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unsupported Flex report timezone {timezone_name!r}") from exc
    return parsed.replace(tzinfo=timezone).astimezone(UTC)


def _normalize_side(value: str) -> str:
    side = value.upper().replace(" ", "")
    if side in {"BUY", "BOT", "B", "BUYTOCOVER"}:
        return "BUY"
    if side in {"SELL", "SLD", "S", "SELLSHORT"}:
        return "SELL"
    raise ValueError(f"unsupported side {value!r}")


def _decimal(value: str, *, required: bool = False, default: Decimal | None = None) -> Decimal:
    cleaned = value.replace(",", "").replace("$", "").strip()
    if not cleaned:
        if required:
            raise ValueError("missing required numeric value")
        return default if default is not None else Decimal("0")
    return Decimal(cleaned)


def _optional_decimal(value: str) -> Decimal | None:
    return _decimal(value) if value else None


def _first(row: dict[str, str], *names: str) -> str:
    return next((row.get(name.lower(), "") for name in names if row.get(name.lower(), "")), "")


def _optional(value: str) -> str | None:
    return value or None


def _optional_int(value: str) -> int | None:
    return int(value) if value else None


def _fingerprint(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _xml_root(content: str, label: str) -> ET.Element:
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise IBFlexError(f"{label} returned invalid XML") from exc


def _xml_text(root: ET.Element, name: str) -> str | None:
    element = root.find(f".//{name}")
    return element.text.strip() if element is not None and element.text else None
