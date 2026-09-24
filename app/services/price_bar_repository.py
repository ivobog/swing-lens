from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.tables import (
    IBFetchItem,
    IBFetchRun,
    MarketCalculationContext,
    PipelineRun,
    PriceBar,
    PriceBarRevision,
    RefreshCycleRecord,
    WorkScopeRecord,
)


@dataclass(frozen=True)
class PipelineAcquisitionVisibility:
    pipeline_run_id: int
    upload_run_id: int
    calculation_context_id: int
    acquisition_plan_id: str
    scope_id: str | None
    refresh_cycle_id: str | None
    fetch_run_id: int
    fetch_item_id: int
    completed_at: datetime


def load_price_bars_frame(
    db: Session,
    ticker: str,
    what_to_show: str,
    timeframe: str = "1 day",
    *,
    max_session: date | None = None,
    as_of: datetime | None = None,
    calculation_context_id: int | None = None,
) -> pd.DataFrame:
    acquisition = (
        _pipeline_acquisition_visibility(
            db,
            calculation_context_id=calculation_context_id,
            ticker=ticker,
            what_to_show=what_to_show,
            timeframe=timeframe,
        )
        if as_of is not None and calculation_context_id is not None
        else None
    )
    statement = (
        select(PriceBar)
        .where(
            PriceBar.ticker == ticker.upper(),
            PriceBar.what_to_show == what_to_show,
            PriceBar.timeframe == timeframe,
        )
        .order_by(PriceBar.bar_date)
    )
    if max_session is not None:
        statement = statement.where(PriceBar.bar_date <= max_session)
    if as_of is not None:
        pit_visible = and_(PriceBar.created_at <= as_of, PriceBar.first_seen_at <= as_of)
        if acquisition is None:
            statement = statement.where(pit_visible)
        else:
            pipeline_owned = and_(
                PriceBar.first_fetch_run_id == acquisition.fetch_run_id,
                PriceBar.first_fetch_item_id == acquisition.fetch_item_id,
                PriceBar.created_at <= acquisition.completed_at,
                PriceBar.first_seen_at <= acquisition.completed_at,
            )
            statement = statement.where(or_(pit_visible, pipeline_owned))
    rows = list(db.scalars(statement).all())
    if as_of is not None:
        baseline = [
            row
            for row in rows
            if row.created_at <= as_of and row.first_seen_at <= as_of
        ]
        acquired = [row for row in rows if row not in baseline]
        rows = project_price_bar_rows_as_of(db, baseline, as_of=as_of)
        if acquired and acquisition is not None:
            rows.extend(
                project_price_bar_rows_as_of(
                    db, acquired, as_of=acquisition.completed_at
                )
            )
            rows.sort(key=lambda row: row.bar_date)

    frame = pd.DataFrame(
        [
            {
                "date": row.bar_date,
                "open": float(row.open) if row.open is not None else None,
                "high": float(row.high) if row.high is not None else None,
                "low": float(row.low) if row.low is not None else None,
                "close": float(row.close) if row.close is not None else None,
                "volume": float(row.volume) if row.volume is not None else None,
            }
            for row in rows
        ],
        columns=["date", "open", "high", "low", "close", "volume"],
    )
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

    frame.attrs["pit_source_manifest"] = Canonical.canonicalize(
        {
            "ticker": ticker.upper(),
            "what_to_show": what_to_show,
            "timeframe": timeframe,
            "as_of": as_of,
            "max_session": max_session,
            "acquisition_authority": (
                asdict(acquisition)
                if acquisition is not None
                and any(row.created_at > as_of for row in rows)
                else None
            ),
            "states": [
                {"id": row.id, "fingerprint": price_bar_pit_manifest_hash(db, row)} for row in rows
            ],
        }
    )
    return frame


def price_bar_pit_manifest_hash(db, row):
    from datetime import UTC
    from types import SimpleNamespace

    from app.services.price_bar_evidence import (
        PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS,
        price_bar_immutable_evidence_hash,
    )

    # SQLite drops timezone information for timezone=True ORM columns. This
    # adapter preserves their declared UTC storage semantics in isolated tests.
    if isinstance(db, Session) and db.get_bind().dialect.name == "sqlite":
        values = {name: getattr(row, name, None) for name in PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS}
        values = {
            key: value.replace(tzinfo=UTC)
            if isinstance(value, datetime) and value.tzinfo is None
            else value
            for key, value in values.items()
        }
        row = SimpleNamespace(**values)
    return price_bar_immutable_evidence_hash(row)


def project_price_bar_rows_as_of(
    db: Session,
    rows: list[PriceBar] | tuple[PriceBar, ...],
    *,
    as_of: datetime,
) -> list[PriceBar]:
    """Project current cache rows back to the exact values known at ``as_of``.

    A post-cutoff revision must not make the pre-revision row disappear.  The
    first revision after the boundary contains the prior values and data hash;
    earlier revision records provide the then-current revision metadata.
    Returned revised rows are detached transient objects and cannot be flushed
    back accidentally.
    """

    materialized = list(rows)
    revised_ids = [
        int(row.id)
        for row in materialized
        if row.id is not None and row.revised_at is not None and row.revised_at > as_of
    ]
    if not revised_ids:
        return materialized
    revisions = list(
        db.scalars(
            select(PriceBarRevision)
            .where(PriceBarRevision.price_bar_id.in_(revised_ids))
            .order_by(
                PriceBarRevision.price_bar_id,
                PriceBarRevision.revision_number,
                PriceBarRevision.id,
            )
        )
    )
    by_bar: dict[int, list[PriceBarRevision]] = {}
    for revision in revisions:
        by_bar.setdefault(int(revision.price_bar_id), []).append(revision)

    projected: list[PriceBar] = []
    for row in materialized:
        history = by_bar.get(int(row.id or 0), [])
        first_after = next((item for item in history if item.observed_at > as_of), None)
        if first_after is None:
            # A mutable current row cannot stand in for its historical value.
            # Without the first post-boundary revision record, provenance is
            # incomplete and the only safe behavior is conservative exclusion.
            if row.revised_at is None or row.revised_at <= as_of:
                projected.append(row)
            continue
        prior_revisions = [item for item in history if item.observed_at <= as_of]
        values = dict(first_after.previous_values_json or {})
        projected.append(
            PriceBar(
                id=row.id,
                ticker=row.ticker,
                bar_date=row.bar_date,
                timeframe=row.timeframe,
                open=_decimal_or_none(values.get("open")),
                high=_decimal_or_none(values.get("high")),
                low=_decimal_or_none(values.get("low")),
                close=_decimal_or_none(values.get("close")),
                volume=_decimal_or_none(values.get("volume")),
                source=values.get("source") or row.source,
                what_to_show=values.get("what_to_show") or row.what_to_show,
                adjustment_type=values.get("adjustment_type"),
                first_fetch_run_id=row.first_fetch_run_id,
                first_fetch_item_id=row.first_fetch_item_id,
                created_at=row.created_at,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                revised_at=(prior_revisions[-1].observed_at if prior_revisions else None),
                revision_count=max(0, int(first_after.revision_number) - 1),
                data_hash=first_after.previous_data_hash,
            )
        )
    return projected


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def load_preferred_ohlcv_frames(
    db: Session,
    ticker: str,
    timeframe: str = "1 day",
    *,
    max_session: date | None = None,
    as_of: datetime | None = None,
    calculation_context_id: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    adjusted = load_price_bars_frame(
        db,
        ticker,
        "ADJUSTED_LAST",
        timeframe,
        max_session=max_session,
        as_of=as_of,
        calculation_context_id=calculation_context_id,
    )
    trades = load_price_bars_frame(
        db,
        ticker,
        "TRADES",
        timeframe,
        max_session=max_session,
        as_of=as_of,
        calculation_context_id=calculation_context_id,
    )
    adjusted_complete = not adjusted.empty and (
        trades.empty or set(adjusted["date"]) >= set(trades["date"])
    )
    price = adjusted if adjusted_complete else trades
    volume = trades if not trades.empty else None
    price.attrs["price_basis"] = "ADJUSTED_LAST" if adjusted_complete else "TRADES"
    if volume is not None:
        volume.attrs["volume_basis"] = "TRADES"
    return price, volume


def _pipeline_acquisition_visibility(
    db: Session,
    *,
    calculation_context_id: int,
    ticker: str,
    what_to_show: str,
    timeframe: str,
) -> PipelineAcquisitionVisibility | None:
    """Resolve exact insertion authority for one pipeline-owned first fetch.

    The immutable PriceBar insertion addresses are necessary: timing overlap alone
    cannot prove that a post-cutoff row came from this pipeline's acquisition.
    """

    cache = db.info.setdefault("pipeline_price_acquisition_visibility", {})
    key = (int(calculation_context_id), ticker.upper(), what_to_show, timeframe)
    if key in cache:
        return cache[key]
    owner = db.execute(
        select(MarketCalculationContext, PipelineRun)
        .join(PipelineRun, PipelineRun.id == MarketCalculationContext.pipeline_run_id)
        .where(MarketCalculationContext.id == calculation_context_id)
    ).first()
    if owner is None:
        cache[key] = None
        return None
    context, pipeline = owner
    retained = (pipeline.result_json or {}).get("ib_fetch_authority")
    child_scope = False
    if isinstance(retained, dict) and all(
        retained.get(name)
        for name in ("acquisition_plan_id", "scope_id", "refresh_cycle_id")
    ):
        acquisition_plan_id = retained["acquisition_plan_id"]
        scope_id = retained["scope_id"]
        refresh_cycle_id = retained["refresh_cycle_id"]
    else:
        # JSON persistence redacts keys containing "auth". The immutable child
        # scope relation is therefore the durable source of truth after a
        # pipeline step commit/reload.
        child_scope = pipeline.scope_id is not None
        acquisition_plan_id = None if child_scope else pipeline.acquisition_plan_id
        scope_id = None if child_scope else pipeline.scope_id
        refresh_cycle_id = None if child_scope else pipeline.refresh_cycle_id
    if acquisition_plan_id is None and not child_scope:
        cache[key] = None
        return None
    statement = (
        select(IBFetchRun, IBFetchItem)
        .join(IBFetchItem, IBFetchItem.fetch_run_id == IBFetchRun.id)
        .where(
            IBFetchRun.run_id == context.upload_run_id,
            IBFetchItem.ticker == ticker.upper(),
            IBFetchItem.what_to_show == what_to_show,
            IBFetchItem.bar_size.in_((timeframe, "1 day", "1 d")),
            IBFetchItem.status == "SUCCESS",
            IBFetchItem.completed_at.is_not(None),
        )
        .order_by(IBFetchItem.completed_at.desc(), IBFetchItem.id.desc())
        .limit(1)
    )
    if child_scope:
        statement = statement.join(
            WorkScopeRecord, WorkScopeRecord.scope_id == IBFetchRun.scope_id
        ).join(
            RefreshCycleRecord,
            RefreshCycleRecord.refresh_cycle_id == IBFetchRun.refresh_cycle_id,
        ).where(
            WorkScopeRecord.parent_scope_id == pipeline.scope_id,
            WorkScopeRecord.acquisition_plan_id == IBFetchRun.acquisition_plan_id,
            RefreshCycleRecord.scope_id == IBFetchRun.scope_id,
        )
    else:
        statement = statement.where(
            IBFetchRun.acquisition_plan_id == acquisition_plan_id,
            IBFetchRun.scope_id.is_not_distinct_from(scope_id),
            IBFetchRun.refresh_cycle_id.is_not_distinct_from(refresh_cycle_id),
        )
    row = db.execute(statement).first()
    visibility = None
    if row is not None:
        fetch_run, fetch_item = row
        completed_at = fetch_item.completed_at
        if completed_at is not None:
            if completed_at.tzinfo is None:
                completed_at = completed_at.replace(tzinfo=UTC)
            visibility = PipelineAcquisitionVisibility(
                pipeline_run_id=int(pipeline.id),
                upload_run_id=int(context.upload_run_id),
                calculation_context_id=int(context.id),
                acquisition_plan_id=str(fetch_run.acquisition_plan_id),
                scope_id=(str(fetch_run.scope_id) if fetch_run.scope_id is not None else None),
                refresh_cycle_id=(
                    str(fetch_run.refresh_cycle_id)
                    if fetch_run.refresh_cycle_id is not None
                    else None
                ),
                fetch_run_id=int(fetch_run.id),
                fetch_item_id=int(fetch_item.id),
                completed_at=completed_at,
            )
    cache[key] = visibility
    return visibility


def load_price_bar_rows(
    db: Session,
    ticker: str,
    *,
    start_date: date,
    end_date: date,
    what_to_show: str,
    timeframe: str = "1 day",
) -> list[PriceBar]:
    return list(
        db.scalars(
            select(PriceBar)
            .where(
                PriceBar.ticker == ticker.upper(),
                PriceBar.what_to_show == what_to_show,
                PriceBar.timeframe == timeframe,
                PriceBar.bar_date >= start_date,
                PriceBar.bar_date <= end_date,
            )
            .order_by(PriceBar.bar_date)
        )
    )


def load_preferred_price_bar_rows(
    db: Session,
    ticker: str,
    *,
    start_date: date,
    end_date: date,
    timeframe: str = "1 day",
) -> list[PriceBar]:
    adjusted = load_price_bar_rows(
        db,
        ticker,
        start_date=start_date,
        end_date=end_date,
        what_to_show="ADJUSTED_LAST",
        timeframe=timeframe,
    )
    if adjusted:
        return adjusted
    return load_price_bar_rows(
        db,
        ticker,
        start_date=start_date,
        end_date=end_date,
        what_to_show="TRADES",
        timeframe=timeframe,
    )
