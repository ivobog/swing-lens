"""Immutable acceptance evidence for exact versions of valid historical bars."""

from datetime import UTC, datetime
from math import isfinite
from types import SimpleNamespace

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models.tables import (
    IBBarAdmissionReceipt,
    IBFetchItem,
    IBFetchRun,
    MarketCalculationContext,
    PipelineRun,
    PriceBar,
    RefreshCycleRecord,
    WorkScopeRecord,
)
from app.services.bar_cache_service import bar_data_hash, price_bar_data_hash
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.price_bar_evidence import (
    PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS,
    price_bar_immutable_evidence_hash,
)
from app.services.us_market_calendar import is_us_trading_day, next_us_trading_day

POLICY = "ib-daily-gap-aware-v4"
CONTRACT = "ib-bar-admission-receipt-v1"
_HEADER = (
    "fetch_item_id", "fetch_run_id", "upload_run_id", "pipeline_run_id",
    "calculation_context_id", "acquisition_plan_id", "scope_id", "refresh_cycle_id",
    "ticker", "what_to_show", "timeframe", "policy_version", "accepted_at",
)


def immutable_bar_fingerprint(row):
    values = {name: getattr(row, name) for name in PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS}
    values = {
        name: value.replace(tzinfo=UTC)
        if isinstance(value, datetime) and value.tzinfo is None else value
        for name, value in values.items()
    }
    return price_bar_immutable_evidence_hash(SimpleNamespace(**values))


def verify_bar_admission_receipt(receipt):
    body = receipt.payload_json
    if (
        not isinstance(body, dict)
        or body.get("contract") != CONTRACT
        or body.get("policy_version") != POLICY
        or Canonical.fingerprint(body) != receipt.receipt_hash
        or any(body.get(name) != Canonical.canonicalize(getattr(receipt, name)) for name in _HEADER)
    ):
        raise ValueError("IB_BAR_ADMISSION_RECEIPT_INTEGRITY_MISMATCH")
    members = body.get("accepted_bars")
    if (
        not isinstance(members, list) or not members
        or len({member["price_bar_id"] for member in members}) != len(members)
        or len({member["bar_date"] for member in members}) != len(members)
    ):
        raise ValueError("IB_BAR_ADMISSION_RECEIPT_MEMBERSHIP_INVALID")
    for member in members:
        if any(member[name] != body[name] for name in ("ticker", "what_to_show", "timeframe")):
            raise ValueError("IB_BAR_ADMISSION_RECEIPT_SCOPE_MISMATCH")
    return body


def _owner(db, fetch_run):
    scope = db.get(WorkScopeRecord, fetch_run.scope_id)
    refresh = db.get(RefreshCycleRecord, fetch_run.refresh_cycle_id)
    if (
        scope is None or refresh is None
        or scope.scope_kind != "ib-price-acquisition"
        or scope.acquisition_plan_id != fetch_run.acquisition_plan_id
        or refresh.scope_id != scope.scope_id
    ):
        raise ValueError("IB_BAR_ADMISSION_SEMANTIC_SCOPE_REQUIRED")
    owner = db.execute(
        select(PipelineRun, MarketCalculationContext)
        .join(MarketCalculationContext, MarketCalculationContext.pipeline_run_id == PipelineRun.id)
        .where(PipelineRun.scope_id == scope.parent_scope_id)
    ).first() if scope.parent_scope_id is not None else None
    if owner is not None and (
        owner[0].upload_run_id != fetch_run.run_id
        or owner[1].upload_run_id != fetch_run.run_id
    ):
        raise ValueError("IB_BAR_ADMISSION_OWNER_MISMATCH")
    return owner


def seal_bar_admission(
    db, *, fetch_item, scope, admitted_bars, missing_sessions, contract_identity,
):
    """Seal cache versions in the caller's transaction; never commit financial work."""
    if not isinstance(db, Session):
        return None
    if scope.response_admission_policy != POLICY:
        raise ValueError("IB_BAR_ADMISSION_POLICY_REQUIRED")
    bars = list(admitted_bars)
    if not bars:
        return None
    if (
        not contract_identity or not contract_identity.get("conId")
        or contract_identity.get("symbol", "").upper() != fetch_item.ticker.upper()
        or scope.what_to_show != fetch_item.what_to_show or scope.bar_size != fetch_item.bar_size
    ):
        raise ValueError("IB_BAR_ADMISSION_CONTRACT_MISMATCH")
    frozen_contract = (fetch_item.decision_metadata_json or {}).get("contract_identity")
    if frozen_contract is not None and Canonical.canonicalize(frozen_contract) != (
        Canonical.canonicalize(contract_identity)
    ):
        raise ValueError("IB_BAR_ADMISSION_CONTRACT_MISMATCH")
    seen = set()
    for bar in bars:
        if (
            bar.ticker != fetch_item.ticker.upper() or bar.what_to_show != fetch_item.what_to_show
            or bar.timeframe != scope.bar_size or bar.source != "IB"
            or bar.bar_date in seen or not is_us_trading_day(bar.bar_date)
            or not scope.reviewed_start_date <= bar.bar_date <= scope.reviewed_end_date
            or any(value is None or not isfinite(value) or value < 0
                   for value in (bar.open, bar.high, bar.low, bar.close))
            or bar.high < max(bar.open, bar.close, bar.low)
            or bar.low > min(bar.open, bar.close, bar.high)
            or (bar.volume is not None and (not isfinite(bar.volume) or bar.volume < 0))
        ):
            raise ValueError("IB_BAR_ADMISSION_BAR_INVALID")
        seen.add(bar.bar_date)
    required = scope.required_start_date or scope.required_end_date or scope.reviewed_end_date
    required_end = scope.required_end_date or scope.reviewed_end_date
    expected_missing = []
    while required <= required_end:
        if required not in seen:
            expected_missing.append(required)
        required = next_us_trading_day(required)
    if sorted(set(missing_sessions)) != expected_missing:
        raise ValueError("IB_BAR_ADMISSION_GAP_MANIFEST_MISMATCH")
    db.flush()
    fetch_run = db.get(IBFetchRun, fetch_item.fetch_run_id)
    if fetch_run is None or not all((
        fetch_run.scope_id, fetch_run.refresh_cycle_id, fetch_run.acquisition_plan_id,
    )):
        raise ValueError("IB_BAR_ADMISSION_SEMANTIC_SCOPE_REQUIRED")
    owner = _owner(db, fetch_run)
    if owner is not None and scope.reviewed_end_date > owner[1].latest_completed_session:
        raise ValueError("IB_BAR_ADMISSION_CONTEXT_SESSION_MISMATCH")
    keys = [(bar.ticker, bar.bar_date, bar.timeframe, bar.what_to_show) for bar in bars]
    rows = db.scalars(select(PriceBar).where(tuple_(
        PriceBar.ticker, PriceBar.bar_date, PriceBar.timeframe, PriceBar.what_to_show,
    ).in_(keys)).with_for_update()).all()
    by_key = {(row.ticker, row.bar_date, row.timeframe, row.what_to_show): row for row in rows}
    members = []
    for bar, key in zip(bars, keys, strict=True):
        row = by_key.get(key)
        expected_hash = bar_data_hash(bar)
        if (row is None or row.data_hash != expected_hash
                or price_bar_data_hash(row) != expected_hash):
            raise ValueError("IB_BAR_ADMISSION_CACHE_VERSION_MISMATCH")
        members.append({
            "price_bar_id": row.id, "ticker": row.ticker, "bar_date": row.bar_date,
            "what_to_show": row.what_to_show, "timeframe": row.timeframe,
            "data_hash": expected_hash, "revision_count": row.revision_count,
            "immutable_fingerprint": immutable_bar_fingerprint(row),
        })
    existing = db.scalar(select(IBBarAdmissionReceipt).where(
        IBBarAdmissionReceipt.fetch_item_id == fetch_item.id,
    ))
    values = {
        "fetch_item_id": fetch_item.id, "fetch_run_id": fetch_run.id,
        "upload_run_id": fetch_run.run_id,
        "pipeline_run_id": owner[0].id if owner else None,
        "calculation_context_id": owner[1].id if owner else None,
        "acquisition_plan_id": fetch_run.acquisition_plan_id, "scope_id": fetch_run.scope_id,
        "refresh_cycle_id": fetch_run.refresh_cycle_id, "ticker": fetch_item.ticker.upper(),
        "what_to_show": fetch_item.what_to_show, "timeframe": fetch_item.bar_size,
        "policy_version": POLICY,
        "accepted_at": existing.accepted_at if existing else datetime.now(UTC),
    }
    body = Canonical.canonicalize({
        "contract": CONTRACT, **values, "request_scope": scope.to_dict(),
        "contract_identity": contract_identity,
        "outcome": "PARTIAL" if expected_missing else "COMPLETE",
        "missing_sessions": expected_missing,
        "accepted_bars": sorted(members, key=lambda member: member["bar_date"]),
    })
    fingerprint = Canonical.fingerprint(body)
    if existing is not None:
        verify_bar_admission_receipt(existing)
        if existing.receipt_hash != fingerprint:
            raise ValueError("IB_BAR_ADMISSION_REPLAY_MISMATCH")
        return existing
    receipt = IBBarAdmissionReceipt(receipt_hash=fingerprint, payload_json=body, **values)
    db.add(receipt)
    db.flush()
    db.info.pop("pipeline_price_acquisition_visibility", None)
    return receipt


def load_bar_admissions(
    db, *, pipeline_run_id, tickers=None, what_to_show=None, timeframes=None, receipt_hashes=None,
):
    """Load only verified receipts still bound to this exact owner/child chain."""
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        return []
    statement = select(IBBarAdmissionReceipt)
    for column, values in (
        (IBBarAdmissionReceipt.ticker, tickers),
        (IBBarAdmissionReceipt.what_to_show, what_to_show),
        (IBBarAdmissionReceipt.timeframe, timeframes),
        (IBBarAdmissionReceipt.receipt_hash, receipt_hashes),
    ):
        if values is not None:
            statement = statement.where(column.in_(values))
    receipts = db.scalars(
        statement
        .join(IBFetchItem, IBFetchItem.id == IBBarAdmissionReceipt.fetch_item_id)
        .join(IBFetchRun, IBFetchRun.id == IBBarAdmissionReceipt.fetch_run_id)
        .join(WorkScopeRecord, WorkScopeRecord.scope_id == IBBarAdmissionReceipt.scope_id)
        .join(RefreshCycleRecord, RefreshCycleRecord.refresh_cycle_id
              == IBBarAdmissionReceipt.refresh_cycle_id)
        .join(MarketCalculationContext, MarketCalculationContext.id
              == IBBarAdmissionReceipt.calculation_context_id)
        .where(
            IBBarAdmissionReceipt.pipeline_run_id == pipeline_run_id,
            IBBarAdmissionReceipt.upload_run_id == pipeline.upload_run_id,
            MarketCalculationContext.pipeline_run_id == pipeline_run_id,
            MarketCalculationContext.upload_run_id == pipeline.upload_run_id,
            WorkScopeRecord.parent_scope_id == pipeline.scope_id,
            WorkScopeRecord.scope_kind == "ib-price-acquisition",
            WorkScopeRecord.acquisition_plan_id == IBBarAdmissionReceipt.acquisition_plan_id,
            RefreshCycleRecord.scope_id == WorkScopeRecord.scope_id,
            IBFetchRun.run_id == pipeline.upload_run_id,
            IBFetchRun.scope_id == IBBarAdmissionReceipt.scope_id,
            IBFetchRun.refresh_cycle_id == IBBarAdmissionReceipt.refresh_cycle_id,
            IBFetchRun.acquisition_plan_id == IBBarAdmissionReceipt.acquisition_plan_id,
            IBFetchItem.fetch_run_id == IBFetchRun.id,
            IBFetchItem.ticker == IBBarAdmissionReceipt.ticker,
            IBFetchItem.what_to_show == IBBarAdmissionReceipt.what_to_show,
            IBFetchItem.bar_size == IBBarAdmissionReceipt.timeframe,
            IBFetchItem.status.in_(("SUCCESS", "PARTIAL")),
            IBFetchItem.completed_at.is_not(None),
            IBFetchItem.completed_at >= IBBarAdmissionReceipt.accepted_at,
        ).order_by(IBBarAdmissionReceipt.accepted_at, IBBarAdmissionReceipt.receipt_hash)
    ).all()
    for receipt in receipts:
        verify_bar_admission_receipt(receipt)
    return receipts


def load_gap_admissions(db, *, pipeline_run_id):
    return [row for row in load_bar_admissions(db, pipeline_run_id=pipeline_run_id)
            if row.missing_sessions]
