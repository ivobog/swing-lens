"""Bounded, read-only discovery and previews over existing local identities.

Uploads have no verified exchange identity. Conflicting names remain separate,
and an exchange-qualified company is never assigned an unqualified score when
more than one security could own it. No punctuation alias is inferred.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import quote, urlencode

from sqlalchemy import and_, case, cast, exists, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased
from sqlalchemy.types import String

from app.models.ceri_tables import CeriCompany, CeriCompanyAlias
from app.models.tables import (
    CombinedResult,
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    IBContract,
    PipelineRun,
    RawCompanyRow,
)
from app.services.pipeline_outcome import pipeline_requires_ceri_containment
from app.services.producer_readiness import ReadinessStatus, readiness_from_evidence


def _symbol(value):
    return func.upper(func.trim(value))


def _name(value):
    return func.lower(func.trim(func.coalesce(value, "")))


def _catalog(*, query: str | None = None, ticker: str | None = None):
    """Deduplicate in SQL before limiting; never load the ticker universe."""
    c = CeriCompany
    r = RawCompanyRow
    other = aliased(CeriCompany)
    name_rows = select(
        _symbol(r.ticker).label("ticker"),
        func.min(r.company_name).label("company_name"),
        func.count(func.distinct(_name(r.company_name))).label("name_count"),
    )
    aliases = _active_aliases()
    if ticker:
        canonical_aliases = (
            select(_symbol(c.ticker))
            .join(
                aliases,
                aliases.c.company_id == c.id,
            )
            .where(_symbol(aliases.c.alias_value) == ticker)
        )
        name_rows = name_rows.where(
            or_(
                _symbol(r.ticker) == ticker,
                _symbol(r.ticker).in_(canonical_aliases),
            )
        )
    # Referenced twice; one shared aggregation avoids rescanning upload history.
    upload_names = name_rows.group_by(_symbol(r.ticker)).cte("upload_names")
    # A name plus exact symbol can join sources only with one company identity.
    unique_company = ~exists(
        select(1).where(
            _symbol(other.ticker) == _symbol(c.ticker),
            other.id != c.id,
        )
    ).correlate(c)
    raw_has_company = exists(
        select(1).where(
            _symbol(c.ticker) == _symbol(r.ticker),
            or_(
                _name(c.company_name) == _name(r.company_name),
                and_(
                    c.company_name.is_(None),
                    exists(
                        select(1).where(
                            upload_names.c.ticker == _symbol(r.ticker),
                            upload_names.c.name_count == 1,
                        )
                    ),
                ),
            ),
            unique_company,
        )
    ).correlate(r)
    companies = select(
        (literal("ceri:") + cast(c.id, String)).label("identity"),
        _symbol(c.ticker).label("ticker"),
        func.coalesce(
            c.company_name,
            case(
                (upload_names.c.name_count == 1, upload_names.c.company_name),
                else_=None,
            ),
        ).label("company_name"),
        c.exchange.label("exchange"),
        c.id.label("company_id"),
    ).outerjoin(upload_names, upload_names.c.ticker == _symbol(c.ticker))
    raw = (
        select(
            (literal("raw:") + cast(func.min(r.id), String)).label("identity"),
            _symbol(r.ticker).label("ticker"),
            func.min(r.company_name).label("company_name"),
            literal(None, String).label("exchange"),
            literal(None, c.id.type).label("company_id"),
        )
        .where(~raw_has_company)
        .group_by(_symbol(r.ticker), _name(r.company_name))
    )
    # Contracts cover locally acquired securities that never entered an upload.
    contract = select(
        (literal("contract:") + cast(IBContract.id, String)).label("identity"),
        _symbol(IBContract.ticker).label("ticker"),
        literal(None, String).label("company_name"),
        func.coalesce(IBContract.primary_exchange, IBContract.exchange).label("exchange"),
        literal(None, c.id.type).label("company_id"),
    ).where(
        ~exists(select(1).where(_symbol(r.ticker) == _symbol(IBContract.ticker))),
        ~exists(select(1).where(_symbol(c.ticker) == _symbol(IBContract.ticker))),
    )
    if query:

        def matches(symbol, name):
            return or_(
                _symbol(symbol).contains(query.upper(), autoescape=True),
                _name(name).contains(query.lower(), autoescape=True),
            )

        raw = raw.where(matches(r.ticker, r.company_name))
        companies = companies.where(
            or_(
                matches(c.ticker, func.coalesce(c.company_name, upload_names.c.company_name)),
                exists(
                    select(1).where(
                        aliases.c.company_id == c.id,
                        _symbol(aliases.c.alias_value).contains(query.upper(), autoescape=True),
                    )
                ),
            )
        )
        contract = contract.where(
            _symbol(IBContract.ticker).contains(query.upper(), autoescape=True)
        )
    elif ticker:
        raw = raw.where(_symbol(r.ticker) == ticker)
        companies = companies.where(
            or_(
                _symbol(c.ticker) == ticker,
                exists(
                    select(1).where(
                        aliases.c.company_id == c.id,
                        _symbol(aliases.c.alias_value) == ticker,
                    )
                ),
            )
        )
        contract = contract.where(_symbol(IBContract.ticker) == ticker)
    return union_all(companies, raw, contract).subquery("local_ticker_identities")


def _active_aliases():
    # Same validity interval semantics as CeriIdentityResolver._alias_valid.
    # Providers qualify aliases in storage; conflicting provider mappings remain
    # separate companies in results rather than becoming a global replacement.
    today = datetime.now(UTC).date()
    return (
        select(CeriCompanyAlias.company_id, CeriCompanyAlias.alias_value)
        .where(
            CeriCompanyAlias.alias_type == "ticker",
            or_(CeriCompanyAlias.valid_from.is_(None), CeriCompanyAlias.valid_from <= today),
            or_(CeriCompanyAlias.valid_to.is_(None), CeriCompanyAlias.valid_to >= today),
        )
        .subquery("active_ticker_aliases")
    )


def _identity_dto(row):
    item = dict(row)
    item.pop("company_id", None)
    item.pop("match_group", None)
    item["qualifier"] = item["exchange"] or (
        "Upload identity" if item["identity"].startswith("raw:") else "Local identity"
    )
    return item


def search_tickers(db: Session, query: str, limit: int = 15):
    query = query.strip()
    if not query:
        return []
    catalog = _catalog(query=query)
    aliases = _active_aliases()
    ticker = catalog.c.ticker
    # contains/startswith escape SQL wildcard characters supplied by the user.
    alias_exact = exists(
        select(1).where(
            aliases.c.company_id == catalog.c.company_id,
            _symbol(aliases.c.alias_value) == query.upper(),
        )
    )
    alias_prefix = exists(
        select(1).where(
            aliases.c.company_id == catalog.c.company_id,
            _symbol(aliases.c.alias_value).startswith(query.upper(), autoescape=True),
        )
    )
    alias_contains = exists(
        select(1).where(
            aliases.c.company_id == catalog.c.company_id,
            _symbol(aliases.c.alias_value).contains(query.upper(), autoescape=True),
        )
    )
    exact = or_(ticker == query.upper(), alias_exact)
    prefix = or_(ticker.startswith(query.upper(), autoescape=True), alias_prefix)
    contains = or_(ticker.contains(query.upper(), autoescape=True), alias_contains)
    name_match = _name(catalog.c.company_name).contains(query.lower(), autoescape=True)
    rank = case((exact, 0), (prefix, 1), (contains, 2), else_=3).label("match_group")
    statement = (
        select(catalog, rank)
        .where(or_(contains, name_match))
        .order_by(
            rank,
            ticker,
            func.coalesce(catalog.c.exchange, ""),
            _name(catalog.c.company_name),
            catalog.c.identity,
        )
        .limit(min(max(limit, 1), 50))
    )
    return [_identity_dto(row) for row in db.execute(statement).mappings()]


def resolve_ticker_identity(db: Session, ticker: str, identity: str | None = None):
    ticker = ticker.strip().upper()
    catalog = _catalog(ticker=ticker)
    aliases = _active_aliases()
    symbol_match = or_(
        catalog.c.ticker == ticker,
        exists(
            select(1).where(
                aliases.c.company_id == catalog.c.company_id,
                _symbol(aliases.c.alias_value) == ticker,
            )
        ),
    )
    statement = select(catalog).where(symbol_match)
    if identity:
        statement = statement.where(catalog.c.identity == identity)
    identities = list(db.execute(statement.order_by(catalog.c.identity).limit(2)).mappings())
    if not identities:
        return {"state": "unknown_ticker", "decision": None, "workspace_url": None}
    if len(identities) > 1:
        return {"state": "identity_ambiguous", "decision": None, "workspace_url": None}
    item = _identity_dto(identities[0])
    result = {
        "state": "no_eligible_decision",
        "identity": item,
        "decision": None,
        "workspace_url": None,
        "intelligence": {
            "ceri": "not_checked",
            "lifecycle": "not_checked",
            "winner": "not_checked",
        },
    }
    # Run scores are symbol-scoped and cannot disambiguate multiple exchanges.
    company_count = db.scalar(
        select(func.count())
        .select_from(CeriCompany)
        .where(
            _symbol(CeriCompany.ticker) == item["ticker"],
        )
    )
    if company_count > 1:
        result["state"] = "decision_identity_unverified"
        return result
    result["state"] = "resolved"
    return result


def ticker_workspace_url(item, run_id: int | None = None):
    params = {"identity": item["identity"]}
    if run_id is not None:
        params["run_id"] = run_id
    return f"/tickers/{quote(item['ticker'], safe='')}?{urlencode(params)}"


def ticker_summary(db: Session, ticker: str, identity: str | None = None):
    result = resolve_ticker_identity(db, ticker, identity)
    if result["state"] != "resolved":
        return result
    result["state"] = "no_eligible_decision"
    result["workspace_url"] = ticker_workspace_url(result["identity"])
    row = next(eligible_ticker_decisions(db, result["identity"]), None)
    if row is not None:
        decision = {key: row[key] for key in DECISION_FIELDS if key != "producer_readiness"}
        decision.update(
            run_id=row["run_id"], evidence_id=row["id"], cutoff=row["calculated_at"].isoformat()
        )
        result.update(
            state="available",
            decision=decision,
            workspace_url=ticker_workspace_url(result["identity"], row["run_id"]),
        )
    return result


DECISION_FIELDS = (
    "final_rank",
    "final_score",
    "fundamental_score",
    "dual_score",
    "combined_decision",
    "position_size_hint",
    "warning_flags_json",
    "earnings_warning_flags_json",
    "sector",
    "producer_readiness",
)


def eligible_ticker_decisions(db: Session, item, *, run_id: int | None = None):
    """Shared preview/workspace authority; stream compact run metadata in batches."""
    e = CoreCalculationEvidence
    p = CoreCalculationCurrentProjection
    combined = CombinedResult
    raw = RawCompanyRow
    # Select only compact frozen fields, not the full evidence/configuration bundle.
    pipeline_id = e.calculation_identity_json["ownership"]["pipeline_id"]["value"].as_integer()
    raw_matches = and_(raw.run_id == e.run_id, raw.ticker == e.ticker)
    # A chart must have exactly one raw row, and its name must match the identity.
    raw_count = select(func.count()).where(raw_matches).correlate(e).scalar_subquery()
    statement = (
        select(
            e.id,
            e.run_id,
            e.ticker,
            e.calculated_at,
            e.calculation_identity_fingerprint,
            *[e.payload_json[key].label(key) for key in DECISION_FIELDS],
            PipelineRun.status.label("pipeline_status"),
            PipelineRun.result_json["pipeline_outcome_summary"].label("pipeline_outcome_summary"),
        )
        .join(
            p,
            and_(
                p.evidence_id == e.id,
                p.artifact_kind == "COMBINED",
                p.run_id == e.run_id,
                p.ticker == e.ticker,
                p.ranking_profile_key == "",
            ),
        )
        .join(
            combined,
            and_(
                combined.run_id == e.run_id,
                combined.ticker == e.ticker,
                combined.evidence_id == e.id,
            ),
        )
        .join(
            PipelineRun, and_(PipelineRun.id == pipeline_id, PipelineRun.upload_run_id == e.run_id)
        )
        .where(
            e.artifact_kind == "COMBINED",
            e.ticker == item["ticker"],
            e.payload_json["is_complete"].as_boolean().is_(True),
            e.payload_json["producer_readiness"]["status"].as_string() == "READY",
            or_(
                combined.debug_json["mutation_semantics"]["artifact_role"].as_string().is_(None),
                combined.debug_json["mutation_semantics"]["artifact_role"].as_string()
                != "LEGACY_SERVING_ONLY",
            ),
            PipelineRun.status.in_(("COMPLETED", "PARTIAL")),
            or_(
                PipelineRun.execution_authority_state.is_(None),
                PipelineRun.execution_authority_state.not_in(("QUARANTINED", "RETIRED")),
            ),
            raw_count == 1,
            exists(
                select(1).where(
                    raw_matches,
                    _name(raw.company_name) == (item["company_name"] or "").strip().lower(),
                )
            ),
        )
        .order_by(e.calculated_at.desc(), e.run_id.desc(), e.id.desc())
    )
    if run_id is not None:
        statement = statement.where(e.run_id == run_id)
    # Page through eligible metadata in fixed batches, never materialize history.
    # This also handles old PARTIAL owners without an integrity certification.
    offset = 0
    while True:
        rows = list(db.execute(statement.limit(10).offset(offset)).mappings())
        for row in rows:
            if pipeline_requires_ceri_containment(
                row["pipeline_status"],
                {
                    "pipeline_outcome_summary": row["pipeline_outcome_summary"],
                },
            ):
                continue
            evidence = SimpleNamespace(
                id=row["id"],
                artifact_kind="COMBINED",
                calculation_identity_fingerprint=row["calculation_identity_fingerprint"],
                payload_json={"producer_readiness": row["producer_readiness"]},
            )
            try:
                readiness = readiness_from_evidence(evidence)
            except (ValueError, TypeError, KeyError):
                continue
            if readiness.status is not ReadinessStatus.READY or readiness.blocking_reasons:
                continue
            yield row
        if len(rows) < 10:
            return
        offset += 10
