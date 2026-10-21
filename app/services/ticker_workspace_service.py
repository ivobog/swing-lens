"""Compose one selected frozen decision for display, without recalculation."""

from datetime import date, datetime
from decimal import Decimal
from itertools import islice

from sqlalchemy import Date, DateTime, Numeric, inspect, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    CoreCalculationEvidence,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
)
from app.services.calculation_identity import (
    CalculationIdentity,
    CalculationIdentityCompatibilityValidator,
)
from app.services.combined_ranking_identity import COMBINED_INPUT_COMPATIBILITY
from app.services.core_calculation_evidence import CoreEvidenceKind, get_evidence_by_id
from app.services.score_card_view_service import build_score_cards
from app.services.ticker_search_service import eligible_ticker_decisions, resolve_ticker_identity

RUN_OPTION_LIMIT = 200


def ticker_workspace(
    db: Session,
    ticker: str,
    identity: str | None = None,
    run_id: int | None = None,
    *,
    include_runs: bool = True,
):
    context = resolve_ticker_identity(db, ticker, identity)
    context.update(
        ticker=ticker.strip().upper(),
        selected_run_id=run_id,
        runs=[],
        score_cards=[],
        fundamental=None,
        technical=None,
        combined=None,
        evidence=None,
        component_states={},
    )
    if context["state"] != "resolved":
        return context
    item = context["identity"]
    context["ticker"] = item["ticker"]
    # Only compact per-ticker metadata is read, with a bounded selector.
    rows = (
        list(islice(eligible_ticker_decisions(db, item), RUN_OPTION_LIMIT)) if include_runs else []
    )
    context["runs"] = [dict(run_id=r["run_id"], cutoff=r["calculated_at"]) for r in rows]
    selected = (
        next((r for r in rows if r["run_id"] == run_id), None) if run_id else next(iter(rows), None)
    )
    if run_id and selected is None:
        selected = next(eligible_ticker_decisions(db, item, run_id=run_id), None)
        if selected is not None:
            context["runs"].append(dict(run_id=run_id, cutoff=selected["calculated_at"]))
    if selected is None:
        context["state"] = "selected_run_unavailable" if run_id else "no_eligible_decision"
        return context
    evidence = get_evidence_by_id(db, evidence_id=selected["id"], kind=CoreEvidenceKind.COMBINED)
    combined = _frozen_score(CombinedResult, evidence)
    raw = db.scalar(
        select(RawCompanyRow).where(
            RawCompanyRow.run_id == evidence.run_id,
            RawCompanyRow.ticker == evidence.ticker,
        )
    )
    sources = evidence.source_evidence_ids_json or {}
    pins = [
        sources[role] for role in ("fundamental", "technical") if type(sources.get(role)) is int
    ]
    pinned = (
        {
            e.id: e
            for e in db.scalars(
                select(CoreCalculationEvidence).where(
                    CoreCalculationEvidence.id.in_(pins),
                )
            )
        }
        if pins
        else {}
    )
    values = {}
    states = {}
    for role, model, kind in (
        ("fundamental", FundamentalScore, "FUNDAMENTAL"),
        ("technical", TechnicalScore, "TECHNICAL"),
    ):
        source = pinned.get(sources.get(role))
        valid = _source_matches(evidence, source, kind)
        values[role] = _frozen_score(model, source) if valid else None
        states[role] = "available" if valid else "Pinned evidence unavailable for this decision."
    cards = build_score_cards(raw, values["fundamental"], values["technical"], combined)
    context.update(
        state="available",
        selected_run_id=evidence.run_id,
        evidence=evidence,
        combined=combined,
        **values,
        score_cards=cards,
        component_states=states,
        company_name=combined.company_name or item["company_name"],
        sector=combined.sector or (raw.sector if raw else None),
        primary_warnings=cards[-1]["badges"],
        raw_row=raw,
    )
    return context


def _source_matches(combined, source, kind):
    if source is None or (source.artifact_kind, source.run_id, source.ticker) != (
        kind,
        combined.run_id,
        combined.ticker,
    ):
        return False
    try:
        expected = CalculationIdentity.from_canonical_payload(combined.calculation_identity_json)
        actual = CalculationIdentity.from_canonical_payload(source.calculation_identity_json)
        return CalculationIdentityCompatibilityValidator.compare(
            expected,
            actual,
            policy=COMBINED_INPUT_COMPATIBILITY,
        ).accepted
    except (ValueError, TypeError, KeyError):
        return False


def _frozen_score(model, evidence):
    """Use the existing card builder with transient models, never mutable scores."""
    payload = evidence.payload_json
    values = {}
    for column in inspect(model).column_attrs:
        key = column.key
        value = payload.get(key)
        if key not in payload:
            continue
        column_type = column.columns[0].type
        if value is not None:
            if isinstance(column_type, Numeric):
                value = Decimal(str(value))
            elif isinstance(value, str) and isinstance(column_type, DateTime):
                value = datetime.fromisoformat(value)
            elif isinstance(value, str) and isinstance(column_type, Date):
                value = date.fromisoformat(value)
        values[key] = value
    values.update(evidence_id=evidence.id, run_id=evidence.run_id, ticker=evidence.ticker)
    return model(**values)
