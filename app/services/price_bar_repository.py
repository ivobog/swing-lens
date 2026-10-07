from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import and_, or_, select, tuple_
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
        normalized_as_of = _as_utc(as_of)
        baseline = [
            row
            for row in rows
            if _as_utc(row.created_at) <= normalized_as_of
            and _as_utc(row.first_seen_at) <= normalized_as_of
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

    return _project_price_bar_rows_at_boundaries(
        db,
        [(row, as_of) for row in rows],
    )


def _project_price_bar_rows_at_boundaries(
    db: Session,
    rows: list[tuple[PriceBar, datetime]],
) -> list[PriceBar]:
    """Project many rows at individual visibility boundaries with one revision query."""

    revised_ids = [
        int(row.id)
        for row, boundary in rows
        if row.id is not None
        and row.revised_at is not None
        and _as_utc(row.revised_at) > _as_utc(boundary)
    ]
    if not revised_ids:
        return [row for row, _boundary in rows]
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
    for row, boundary in rows:
        history = by_bar.get(int(row.id or 0), [])
        normalized_as_of = _as_utc(boundary)
        first_after = next(
            (item for item in history if _as_utc(item.observed_at) > normalized_as_of),
            None,
        )
        if first_after is None:
            # A mutable current row cannot stand in for its historical value.
            # Without the first post-boundary revision record, provenance is
            # incomplete and the only safe behavior is conservative exclusion.
            if row.revised_at is None or _as_utc(row.revised_at) <= normalized_as_of:
                projected.append(row)
            continue
        prior_revisions = [
            item for item in history if _as_utc(item.observed_at) <= normalized_as_of
        ]
        values = dict(first_after.previous_values_json or {})
        historical = PriceBar(
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
        # The source mutation guard replays this projection from retained SQL
        # before accepting it as a historical source argument.
        historical._pit_projection_as_of = boundary
        historical._pit_projection_revision_id = first_after.id
        projected.append(historical)
    return projected


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def load_price_bar_rows_for_context(
    db: Session,
    tickers: tuple[str, ...],
    *,
    what_to_show: tuple[str, ...],
    timeframes: tuple[str, ...],
    max_session: date,
    as_of: datetime,
    calculation_context_id: int,
    session_count: int | None = None,
    one_source_per_session: bool = False,
    source_priority: tuple[str, ...] = (),
) -> list[PriceBar]:
    """Load a batched PIT bar view including only this pipeline's authorized acquisition.

    Ordinary cache rows remain bounded by ``as_of``. Rows first acquired after that
    boundary are admitted only when their immutable fetch addresses belong to the
    calculation context's exact acquisition, and are projected at that fetch item's
    completion time. Revision reconstruction is batched across all requested tickers.
    """

    normalized_tickers = tuple(sorted({ticker.strip().upper() for ticker in tickers if ticker}))
    normalized_sources = tuple(dict.fromkeys(what_to_show))
    normalized_timeframes = tuple(dict.fromkeys(timeframes))
    if not normalized_tickers or not normalized_sources or not normalized_timeframes:
        return []

    visibilities = _pipeline_acquisition_visibilities(
        db,
        calculation_context_id=calculation_context_id,
        tickers=normalized_tickers,
        what_to_show=normalized_sources,
        timeframes=normalized_timeframes,
    )
    owned_pairs = {
        (visibility.fetch_run_id, visibility.fetch_item_id)
        for visibility in visibilities.values()
        if visibility is not None
    }
    pit_visible = and_(PriceBar.created_at <= as_of, PriceBar.first_seen_at <= as_of)
    visibility_predicate = pit_visible
    if owned_pairs:
        visibility_predicate = or_(
            pit_visible,
            tuple_(PriceBar.first_fetch_run_id, PriceBar.first_fetch_item_id).in_(owned_pairs),
        )
    rows = list(
        db.scalars(
            select(PriceBar)
            .where(
                PriceBar.ticker.in_(normalized_tickers),
                PriceBar.what_to_show.in_(normalized_sources),
                PriceBar.timeframe.in_(normalized_timeframes),
                PriceBar.bar_date <= max_session,
                PriceBar.close.is_not(None),
                visibility_predicate,
            )
            .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
        )
    )

    bounded: list[tuple[PriceBar, datetime]] = []
    for row in rows:
        boundary = _price_bar_visibility_boundary(row, as_of=as_of, visibilities=visibilities)
        if boundary is not None:
            bounded.append((row, boundary))
    projected = _project_price_bar_rows_at_boundaries(db, bounded)
    if session_count is None and not one_source_per_session:
        return projected

    safe_count = max(1, min(int(session_count or 1), 10))
    priority = {source: index for index, source in enumerate(source_priority)}
    grouped: dict[str, dict[date, list[PriceBar]]] = {}
    for row in projected:
        grouped.setdefault(row.ticker.upper(), {}).setdefault(row.bar_date, []).append(row)

    selected: list[PriceBar] = []
    for ticker in sorted(grouped):
        sessions = sorted(grouped[ticker], reverse=True)[:safe_count]
        for session in sessions:
            candidates = grouped[ticker][session]
            if one_source_per_session:
                selected.append(
                    min(
                        candidates,
                        key=lambda row: (
                            priority.get(row.what_to_show, len(priority)),
                            -(row.id or 0),
                        ),
                    )
                )
            else:
                selected.extend(candidates)
    selected.sort(key=lambda row: (row.ticker, row.bar_date, row.id or 0))
    return selected


def invalid_price_bar_rows_for_context(
    db: Session,
    rows: list[PriceBar] | tuple[PriceBar, ...],
    *,
    max_session: date,
    as_of: datetime,
    calculation_context_id: int,
) -> tuple[PriceBar, ...]:
    """Return rows that cannot be proven visible under the shared context policy."""

    materialized = tuple(rows)
    if not materialized:
        return ()
    visibilities = _pipeline_acquisition_visibilities(
        db,
        calculation_context_id=calculation_context_id,
        tickers=tuple({row.ticker.upper() for row in materialized}),
        what_to_show=tuple({row.what_to_show for row in materialized}),
        timeframes=tuple({row.timeframe for row in materialized}),
    )
    invalid = []
    for row in materialized:
        boundary = _price_bar_visibility_boundary(row, as_of=as_of, visibilities=visibilities)
        revised_at = row.revised_at
        if (
            row.bar_date > max_session
            or boundary is None
            or (revised_at is not None and _as_utc(revised_at) > _as_utc(boundary))
        ):
            invalid.append(row)
    return tuple(invalid)


def _price_bar_visibility_boundary(
    row: PriceBar,
    *,
    as_of: datetime,
    visibilities: dict[tuple[str, str, str], PipelineAcquisitionVisibility | None],
) -> datetime | None:
    created_at = row.created_at
    first_seen_at = row.first_seen_at
    if (
        created_at is not None
        and first_seen_at is not None
        and _as_utc(created_at) <= _as_utc(as_of)
        and _as_utc(first_seen_at) <= _as_utc(as_of)
    ):
        return as_of
    visibility = visibilities.get((row.ticker.upper(), row.what_to_show, row.timeframe))
    if visibility is None:
        return None
    if (
        row.first_fetch_run_id != visibility.fetch_run_id
        or row.first_fetch_item_id != visibility.fetch_item_id
        or created_at is None
        or first_seen_at is None
        or _as_utc(created_at) > _as_utc(visibility.completed_at)
        or _as_utc(first_seen_at) > _as_utc(visibility.completed_at)
    ):
        return None
    return visibility.completed_at


def load_preferred_ohlcv_frames(
    db: Session,
    ticker: str,
    timeframe: str = "1 day",
    *,
    max_session: date | None = None,
    as_of: datetime | None = None,
    calculation_context_id: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    context_kwargs = (
        {"calculation_context_id": calculation_context_id}
        if calculation_context_id is not None
        else {}
    )
    adjusted = load_price_bars_frame(
        db,
        ticker,
        "ADJUSTED_LAST",
        timeframe,
        max_session=max_session,
        as_of=as_of,
        **context_kwargs,
    )
    trades = load_price_bars_frame(
        db,
        ticker,
        "TRADES",
        timeframe,
        max_session=max_session,
        as_of=as_of,
        **context_kwargs,
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

    return _pipeline_acquisition_visibilities(
        db,
        calculation_context_id=calculation_context_id,
        tickers=(ticker.upper(),),
        what_to_show=(what_to_show,),
        timeframes=(timeframe,),
    ).get((ticker.upper(), what_to_show, timeframe))


def _pipeline_acquisition_visibilities(
    db: Session,
    *,
    calculation_context_id: int,
    tickers: tuple[str, ...],
    what_to_show: tuple[str, ...],
    timeframes: tuple[str, ...],
) -> dict[tuple[str, str, str], PipelineAcquisitionVisibility | None]:
    """Resolve exact acquisition authority for many ticker/feed keys in one query."""

    cache = db.info.setdefault("pipeline_price_acquisition_visibility", {})
    requested = {
        (ticker.upper(), source, timeframe)
        for ticker in tickers
        for source in what_to_show
        for timeframe in timeframes
    }
    missing = {
        key
        for key in requested
        if (int(calculation_context_id), *key) not in cache
    }
    if not missing:
        return {
            key: cache[(int(calculation_context_id), *key)]
            for key in requested
        }
    owner = db.execute(
        select(MarketCalculationContext, PipelineRun)
        .join(PipelineRun, PipelineRun.id == MarketCalculationContext.pipeline_run_id)
        .where(MarketCalculationContext.id == calculation_context_id)
    ).first()
    if owner is None:
        for key in missing:
            cache[(int(calculation_context_id), *key)] = None
        return {key: cache[(int(calculation_context_id), *key)] for key in requested}
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
        for key in missing:
            cache[(int(calculation_context_id), *key)] = None
        return {key: cache[(int(calculation_context_id), *key)] for key in requested}
    missing_tickers = {key[0] for key in missing}
    missing_sources = {key[1] for key in missing}
    accepted_bar_sizes = {
        candidate
        for key in missing
        for candidate in (key[2], "1 day", "1 d")
    }
    statement = (
        select(IBFetchRun, IBFetchItem)
        .join(IBFetchItem, IBFetchItem.fetch_run_id == IBFetchRun.id)
        .where(
            IBFetchRun.run_id == context.upload_run_id,
            IBFetchItem.ticker.in_(missing_tickers),
            IBFetchItem.what_to_show.in_(missing_sources),
            IBFetchItem.bar_size.in_(accepted_bar_sizes),
            IBFetchItem.status == "SUCCESS",
            IBFetchItem.completed_at.is_not(None),
        )
        .order_by(IBFetchItem.completed_at.desc(), IBFetchItem.id.desc())
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
    resolved: dict[tuple[str, str, str], PipelineAcquisitionVisibility] = {}
    for row in db.execute(statement):
        fetch_run, fetch_item = row
        completed_at = fetch_item.completed_at
        if completed_at is None:
            continue
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
        for key in missing:
            ticker, source, timeframe = key
            if (
                key not in resolved
                and fetch_item.ticker.upper() == ticker
                and fetch_item.what_to_show == source
                and fetch_item.bar_size in {timeframe, "1 day", "1 d"}
            ):
                resolved[key] = visibility
    for key in missing:
        cache[(int(calculation_context_id), *key)] = resolved.get(key)
    return {key: cache[(int(calculation_context_id), *key)] for key in requested}


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
