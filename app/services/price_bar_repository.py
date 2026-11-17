from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache

import pandas as pd
from sqlalchemy import and_, func, or_, select, tuple_
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
    bar_admissions: tuple[dict, ...] = ()
    legacy_success: bool = True


def _legacy_acquisition_manifest(acquisition):
    return {key: value for key, value in asdict(acquisition).items()
            if key not in {"bar_admissions", "legacy_success"}}


def acquisition_authority_manifest(acquisition, rows, *, as_of):
    """Pin only the exact acceptance receipts actually consumed by this frame."""
    if acquisition is None:
        return None
    acquired = [row for row in rows if _as_utc(row.created_at) > _as_utc(as_of)
                or _as_utc(row.first_seen_at) > _as_utc(as_of)]
    if not acquired:
        return None
    members = []
    legacy = False
    for row in acquired:
        member = getattr(row, "_ib_bar_admission", None)
        if member is None:
            legacy = True
        else:
            members.append(member)
    if not members:
        return _legacy_acquisition_manifest(acquisition)
    return {
        "contract": "pipeline-bar-admissions-v1",
        "pipeline_run_id": acquisition.pipeline_run_id,
        "upload_run_id": acquisition.upload_run_id,
        "calculation_context_id": acquisition.calculation_context_id,
        "bar_admissions": sorted(members, key=lambda member: member["price_bar_id"]),
        "legacy": _legacy_acquisition_manifest(acquisition) if legacy else None,
    }


def project_acquisition_price_rows(db, rows, *, as_of, acquisition, authority=None):
    """Project exact receipt versions; a later repair never shadows other dates."""
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.ib_bar_admission_service import immutable_bar_fingerprint

    baseline = [row for row in rows if _as_utc(row.created_at) <= _as_utc(as_of)
                and _as_utc(row.first_seen_at) <= _as_utc(as_of)]
    acquired = [row for row in rows if row not in baseline]
    projected = project_price_bar_rows_as_of(db, baseline, as_of=as_of)
    if not acquired:
        return projected
    if acquisition is None:
        raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_REQUIRED")
    candidates = acquisition.bar_admissions
    legacy_allowed = acquisition.legacy_success
    if authority is not None:
        if authority.get("contract") == "pipeline-bar-admissions-v1":
            for key in ("pipeline_run_id", "upload_run_id", "calculation_context_id"):
                if authority.get(key) != getattr(acquisition, key):
                    raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_MISMATCH")
            selected = authority.get("bar_admissions")
            if not isinstance(selected, list) or len({m["price_bar_id"] for m in selected}) != (
                len(selected)
            ):
                raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_MISMATCH")
            available = {Canonical.dumps(member) for member in candidates}
            if any(Canonical.dumps(member) not in available for member in selected):
                raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_MISMATCH")
            candidates = tuple(selected)
            legacy_allowed = authority.get("legacy") is not None
            if legacy_allowed and (not acquisition.legacy_success or (
                Canonical.canonicalize(authority["legacy"]) !=
                Canonical.canonicalize(_legacy_acquisition_manifest(acquisition))
            )):
                raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_MISMATCH")
        elif not acquisition.legacy_success or Canonical.canonicalize(authority) != (
            Canonical.canonicalize(_legacy_acquisition_manifest(acquisition))
        ):
            raise ValueError("MUTATION_PRICE_ACQUISITION_AUTHORITY_MISMATCH")
        else:
            candidates = ()
    boundaries = []
    member_by_id = {}
    for row in acquired:
        matches = [member for member in candidates if member["price_bar_id"] == row.id]
        if matches:
            member = max(matches, key=lambda value: (value["accepted_at"], value["receipt_hash"]))
            boundary = datetime.fromisoformat(member["accepted_at"].replace("Z", "+00:00"))
            member_by_id[row.id] = member
        elif legacy_allowed and (
            row.first_fetch_run_id == acquisition.fetch_run_id
            and row.first_fetch_item_id == acquisition.fetch_item_id
            and _as_utc(row.created_at) <= _as_utc(acquisition.completed_at)
            and _as_utc(row.first_seen_at) <= _as_utc(acquisition.completed_at)
        ):
            boundary = acquisition.completed_at
        else:
            raise ValueError("MUTATION_PRICE_ACQUISITION_SCOPE_MISMATCH")
        boundaries.append((row, boundary))
    owned = _project_price_bar_rows_at_boundaries(db, boundaries)
    for row in owned:
        member = member_by_id.get(row.id)
        if member is not None:
            if (row.data_hash != member["data_hash"]
                    or row.revision_count != member["revision_count"]
                    or immutable_bar_fingerprint(row) != member["immutable_fingerprint"]):
                raise ValueError("IB_BAR_ADMISSION_VERSION_MISMATCH")
            row._ib_bar_admission = member
    return [*projected, *owned]


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
    from app.services.sector_read_budget import checkpoint

    checkpoint()
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
            legacy_owned = and_(
                PriceBar.first_fetch_run_id == acquisition.fetch_run_id,
                PriceBar.first_fetch_item_id == acquisition.fetch_item_id,
                PriceBar.created_at <= acquisition.completed_at,
                PriceBar.first_seen_at <= acquisition.completed_at,
            ) if acquisition.legacy_success else False
            pipeline_owned = or_(legacy_owned, PriceBar.id.in_(
                [member["price_bar_id"] for member in acquisition.bar_admissions]
            ))
            statement = statement.where(or_(pit_visible, pipeline_owned))
    batch = db.info.get("msi_price_rows") if isinstance(db, Session) else None
    if batch and calculation_context_id is None and (as_of, max_session) == batch[:2]:
        first = db.info.get("msi_min_session")
        rows = [row for row in batch[2].get((ticker.upper(), what_to_show, timeframe), [])
                if first is None or row.bar_date >= first]
    else:
        rows = list(db.scalars(statement).all())
    checkpoint()
    if as_of is not None:
        normalized_as_of = _as_utc(as_of)
        rows = project_acquisition_price_rows(
            db, rows, as_of=normalized_as_of, acquisition=acquisition,
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
                acquisition_authority_manifest(acquisition, rows, as_of=as_of)
            ),
            "states": [
                {"id": row.id, "fingerprint": price_bar_pit_manifest_hash(db, row)} for row in rows
            ],
        }
    )
    checkpoint()
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
    # Technical panels visit each window row once. Building and retaining a
    # global cache key costs more on that cold path than its canonical hash.
    # Full-history panels retain bounded cross-read reuse within their scopes.
    if isinstance(db, Session) and db.info.get("msi_direct_fingerprints"):
        return price_bar_immutable_evidence_hash(row)
    # Exact immutable values (including receipt/revision fields), never row ID
    # or TTL, identify a reusable fingerprint. Preserve original canonical bytes.
    values = tuple(getattr(row, name, None) for name in PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS)
    # Retain type distinctions (Decimal/int/float/bool have different canonical
    # encodings) without allocating a string and a pair for every field on every
    # hit. Datetimes retain an explicit offset: Python considers the two folds
    # of a ZoneInfo wall time equal even when their canonical UTC instants differ.
    identity = tuple(type(value) for value in values)
    temporal = tuple(value.isoformat() for value in values if isinstance(value, datetime))
    return _immutable_fingerprint((identity, temporal), values)


@lru_cache(maxsize=32768)
def _immutable_fingerprint(identity, values):
    from types import SimpleNamespace

    from app.services.price_bar_evidence import (
        PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS,
        price_bar_immutable_evidence_hash,
    )

    return price_bar_immutable_evidence_hash(
        SimpleNamespace(**dict(zip(PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS, values, strict=True)))
    )


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
    *,
    checkpoint_callback=None,
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
    for index, (row, boundary) in enumerate(rows, start=1):
        if index > 1 and (index - 1) % 256 == 0:
            _price_checkpoint(
                checkpoint_callback, "PRICE_HISTORY_RECONSTRUCTED", index - 1, len(rows),
                f"rows-through:{index - 1}",
            )
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
    _price_checkpoint(
        checkpoint_callback, "PRICE_HISTORY_RECONSTRUCTED", len(rows), len(rows),
        f"rows-through:{len(rows)}",
    )
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
    checkpoint_callback=None,
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
        if visibility is not None and visibility.legacy_success
    }
    receipt_ids = {member["price_bar_id"] for visibility in visibilities.values()
                   if visibility is not None for member in visibility.bar_admissions}
    pit_visible = and_(PriceBar.created_at <= as_of, PriceBar.first_seen_at <= as_of)
    visibility_predicate = pit_visible
    if owned_pairs:
        visibility_predicate = or_(
            pit_visible,
            tuple_(PriceBar.first_fetch_run_id, PriceBar.first_fetch_item_id).in_(owned_pairs),
        )
    if receipt_ids:
        visibility_predicate = or_(visibility_predicate, PriceBar.id.in_(receipt_ids))
    criteria = (
        PriceBar.ticker.in_(normalized_tickers),
        PriceBar.what_to_show.in_(normalized_sources),
        PriceBar.timeframe.in_(normalized_timeframes),
        PriceBar.bar_date <= max_session,
        PriceBar.close.is_not(None),
        visibility_predicate,
    )
    admission_index = _index_visibility_admissions(visibilities)
    _price_checkpoint(
        checkpoint_callback, "PRICE_AUTHORITY_LOADED", len(visibilities),
        len(visibilities), "price-authority",
    )
    limited = session_count is not None or one_source_per_session
    safe_count = max(1, min(int(session_count or 1), 10))
    projected: list[PriceBar] = []
    if limited:
        # Rank only candidate addresses in SQL. A candidate date may have no
        # provably visible historical row after authorization/reconstruction, so
        # keep advancing older pages until each ticker has enough VALID dates.
        # Applying LIMIT to current rows before those checks loses that fallback.
        ranked = (
            select(
                PriceBar.id.label("price_bar_id"),
                PriceBar.ticker.label("ticker"),
                func.dense_rank().over(
                    partition_by=PriceBar.ticker, order_by=PriceBar.bar_date.desc(),
                ).label("candidate_session_rank"),
            )
            .where(*criteria)
            .subquery()
        )
        unfinished = set(normalized_tickers)
        page_size = max(2, safe_count)
        offset = 0
        while unfinished:
            rows = list(db.scalars(
                select(PriceBar)
                .join(ranked, ranked.c.price_bar_id == PriceBar.id)
                .where(
                    ranked.c.ticker.in_(unfinished),
                    ranked.c.candidate_session_rank > offset,
                    ranked.c.candidate_session_rank <= offset + page_size,
                )
                .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
            ))
            if not rows:
                break
            projected.extend(_project_context_bar_batch(
                db, rows, as_of=as_of, visibilities=visibilities,
                admission_index=admission_index, checkpoint_callback=checkpoint_callback,
            ))
            valid_dates: dict[str, set[date]] = {}
            for row in projected:
                valid_dates.setdefault(row.ticker.upper(), set()).add(row.bar_date)
            candidates = {row.ticker.upper() for row in rows}
            unfinished = {
                ticker for ticker in unfinished & candidates
                if len(valid_dates.get(ticker, ())) < safe_count
            }
            offset += page_size
            _price_checkpoint(
                checkpoint_callback, "PRICE_SESSION_PAGE_VERIFIED",
                len(normalized_tickers) - len(unfinished), len(normalized_tickers),
                f"candidate-sessions-through:{offset}",
            )
    else:
        rows = list(db.scalars(
            select(PriceBar).where(*criteria)
            .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
        ))
        projected = _project_context_bar_batch(
            db, rows, as_of=as_of, visibilities=visibilities,
            admission_index=admission_index, checkpoint_callback=checkpoint_callback,
        )
    if session_count is None and not one_source_per_session:
        return projected

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


def _price_checkpoint(callback, phase, processed, total, current_item):
    if callback is not None:
        callback(
            phase=phase, processed=processed, total=total, current_item=current_item,
            last_completed_item=current_item,
        )


def _index_visibility_admissions(visibilities):
    """Index exact members for this read; never cache mutable validation outcomes."""
    indexed = {}
    for key, visibility in visibilities.items():
        if visibility is None:
            continue
        by_id = {}
        for member in visibility.bar_admissions:
            by_id.setdefault(member["price_bar_id"], []).append(member)
        indexed[key] = by_id
    return indexed


def _project_context_bar_batch(
    db, rows, *, as_of, visibilities, admission_index, checkpoint_callback=None,
):
    bounded: list[tuple[PriceBar, datetime]] = []
    members = {}
    for index, row in enumerate(rows, start=1):
        boundary = _price_bar_visibility_boundary(
            row, as_of=as_of, visibilities=visibilities, admission_index=admission_index,
        )
        if boundary is not None:
            bounded.append((row, boundary))
            if getattr(row, "_ib_bar_admission", None) is not None:
                members[row.id] = row._ib_bar_admission
        if index % 256 == 0 or index == len(rows):
            _price_checkpoint(
                checkpoint_callback, "PRICE_VISIBILITY_VERIFIED", index, len(rows),
                f"bar:{row.id}",
            )
    projected = _project_price_bar_rows_at_boundaries(
        db, bounded,
        **({"checkpoint_callback": checkpoint_callback} if checkpoint_callback is not None else {}),
    )
    from app.services.ib_bar_admission_service import immutable_bar_fingerprint

    for index, row in enumerate(projected, start=1):
        member = members.get(row.id)
        if member is not None:
            if (row.data_hash != member["data_hash"]
                    or row.revision_count != member["revision_count"]
                    or immutable_bar_fingerprint(row) != member["immutable_fingerprint"]):
                raise ValueError("IB_BAR_ADMISSION_VERSION_MISMATCH")
            row._ib_bar_admission = member
        if index % 256 == 0 or index == len(projected):
            _price_checkpoint(
                checkpoint_callback, "PRICE_REVISIONS_VERIFIED", index, len(projected),
                f"bar:{row.id}",
            )
    return projected


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
    admission_index = _index_visibility_admissions(visibilities)
    for row in materialized:
        boundary = _price_bar_visibility_boundary(
            row, as_of=as_of, visibilities=visibilities, admission_index=admission_index,
        )
        revised_at = row.revised_at
        member = getattr(row, "_ib_bar_admission", None)
        from app.services.ib_bar_admission_service import immutable_bar_fingerprint
        if (
            row.bar_date > max_session
            or boundary is None
            or (revised_at is not None and _as_utc(revised_at) > _as_utc(boundary))
            or (member is not None and immutable_bar_fingerprint(row)
                != member["immutable_fingerprint"])
        ):
            invalid.append(row)
    return tuple(invalid)


def _price_bar_visibility_boundary(
    row: PriceBar,
    *,
    as_of: datetime,
    visibilities: dict[tuple[str, str, str], PipelineAcquisitionVisibility | None],
    admission_index=None,
) -> datetime | None:
    created_at = row.created_at
    first_seen_at = row.first_seen_at
    if (
        created_at is not None
        and first_seen_at is not None
        and _as_utc(created_at) <= _as_utc(as_of)
        and _as_utc(first_seen_at) <= _as_utc(as_of)
    ):
        row._ib_bar_admission = None
        return as_of
    key = (row.ticker.upper(), row.what_to_show, row.timeframe)
    visibility = visibilities.get(key)
    if visibility is None:
        return None
    members = (
        admission_index.get(key, {}).get(row.id, ()) if admission_index is not None
        else [member for member in visibility.bar_admissions if member["price_bar_id"] == row.id]
    )
    if members:
        from app.services.ib_bar_admission_service import immutable_bar_fingerprint

        # Validation of an already projected row keeps its captured receipt.
        # Fresh cache reads choose the newest accepted version and reconstruct it.
        captured = getattr(row, "_ib_bar_admission", None)
        fingerprint = immutable_bar_fingerprint(row)
        exact = [member for member in members
                 if member["immutable_fingerprint"] == fingerprint]
        member = captured if captured in exact else max(
            exact or members, key=lambda value: (value["accepted_at"], value["receipt_hash"]),
        )
        row._ib_bar_admission = member
        return datetime.fromisoformat(member["accepted_at"].replace("Z", "+00:00"))
    row._ib_bar_admission = None
    if (
        not visibility.legacy_success
        or row.first_fetch_run_id != visibility.fetch_run_id
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
    receipt_hashes: tuple[str, ...] | None = None,
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
        receipt_hashes=receipt_hashes,
    ).get((ticker.upper(), what_to_show, timeframe))


def _pipeline_acquisition_visibilities(
    db: Session,
    *,
    calculation_context_id: int,
    tickers: tuple[str, ...],
    what_to_show: tuple[str, ...],
    timeframes: tuple[str, ...],
    receipt_hashes: tuple[str, ...] | None = None,
) -> dict[tuple[str, str, str], PipelineAcquisitionVisibility | None]:
    """Resolve exact acquisition authority for many ticker/feed keys in one query."""

    transaction = db.get_transaction()
    selected_receipts = None if receipt_hashes is None else tuple(sorted(set(receipt_hashes)))

    def cache_key(key):
        return (int(calculation_context_id), *key, selected_receipts)

    cache = db.info.setdefault("pipeline_price_acquisition_visibility", {})
    if cache.get("transaction") is not transaction:
        cache = {"transaction": transaction}
        db.info["pipeline_price_acquisition_visibility"] = cache
    requested = {
        (ticker.upper(), source, timeframe)
        for ticker in tickers
        for source in what_to_show
        for timeframe in timeframes
    }
    missing = {
        key
        for key in requested
        if cache_key(key) not in cache
    }
    if not missing:
        return {
            key: cache[cache_key(key)]
            for key in requested
        }
    owner = db.execute(
        select(MarketCalculationContext, PipelineRun)
        .join(PipelineRun, PipelineRun.id == MarketCalculationContext.pipeline_run_id)
        .where(MarketCalculationContext.id == calculation_context_id)
    ).first()
    if owner is None:
        for key in missing:
            cache[cache_key(key)] = None
        return {key: cache[cache_key(key)] for key in requested}
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
            cache[cache_key(key)] = None
        return {key: cache[cache_key(key)] for key in requested}
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
            or_(
                IBFetchItem.decision_metadata_json["response_admission_policy"].astext.is_(None),
                IBFetchItem.decision_metadata_json["response_admission_policy"].astext
                != "ib-daily-gap-aware-v4",
            ),
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
    from dataclasses import replace

    from app.services.ib_bar_admission_service import load_bar_admissions

    admissions = load_bar_admissions(
        db, pipeline_run_id=pipeline.id, tickers=missing_tickers,
        what_to_show=missing_sources, timeframes=accepted_bar_sizes,
        receipt_hashes=selected_receipts,
    )
    for key in missing:
        ticker, source, timeframe = key
        relevant = [receipt for receipt in admissions
                    if receipt.calculation_context_id == context.id
                    and receipt.ticker == ticker and receipt.what_to_show == source
                    and receipt.timeframe in {timeframe, "1 day", "1 d"}]
        if not relevant:
            continue
        members = tuple({
            **member, "receipt_hash": receipt.receipt_hash,
            "accepted_at": receipt.payload_json["accepted_at"],
        } for receipt in relevant for member in receipt.payload_json["accepted_bars"])
        visibility = resolved.get(key)
        if visibility is None:
            receipt = relevant[-1]
            visibility = PipelineAcquisitionVisibility(
                pipeline_run_id=pipeline.id, upload_run_id=context.upload_run_id,
                calculation_context_id=context.id, acquisition_plan_id=receipt.acquisition_plan_id,
                scope_id=receipt.scope_id, refresh_cycle_id=receipt.refresh_cycle_id,
                fetch_run_id=receipt.fetch_run_id, fetch_item_id=receipt.fetch_item_id,
                completed_at=receipt.accepted_at, legacy_success=False,
            )
        resolved[key] = replace(visibility, bar_admissions=members)
    for key in missing:
        cache[cache_key(key)] = resolved.get(key)
    cache["transaction"] = db.get_transaction()
    return {key: cache[cache_key(key)] for key in requested}


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
