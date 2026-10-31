"""Atomic, complete-only independent captures and immutable stored reads."""

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.market_sector_intelligence_tables import (
    MarketSectorRotationRow,
    MarketSectorRotationSnapshot,
)
from app.services.market_clock_service import (
    BAR_READINESS_VERSION,
    EXCHANGE_TIMEZONE,
    MarketClockService,
)
from app.services.market_sector_intelligence_capture_authority import (
    capture_transaction,
    require_capture_authority,
)
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_rotation_math import (
    METRIC_NAMES,
    evaluate_pair,
    quadrant,
    rank_sectors,
    required_sessions,
)
from app.services.market_sector_intelligence_rotation_service import (
    fingerprint,
    material_identity_version,
    snapshot_digest,
)


class MarketSectorRotationRepository:
    def __init__(self, *, now=None):
        self.now = now or (lambda: datetime.now(UTC))

    def capture(self, db, preview: dict, *, authority) -> dict:
        require_capture_authority(authority)
        if preview["cohort_status"] != "COMPLETE" or not preview["feature_enabled"]:
            return {"status": "NOT_READY", "snapshot_id": None, "preview": preview}
        if db.in_transaction() or db.new or db.dirty or db.deleted:
            raise ValueError("CAPTURE_REQUIRES_FRESH_SESSION")
        config = load_market_sector_intelligence_config()
        now = self.now()
        _validate_capture(preview, config, now)
        key = (
            date.fromisoformat(preview["market_session"]),
            preview["algorithm_version"],
            preview["configuration_digest"],
        )
        try:
            with db.begin():
                # SQLite serializes only this explicit writer transaction.
                if db.get_bind().dialect.name == "sqlite":
                    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                existing = _find(db, key)
                if existing is not None:
                    return _repeat(existing, preview)
                with capture_transaction(
                    db,
                    authority,
                    preview["snapshot_digest"],
                    {row["etf_symbol"]: row["input_digest"] for row in preview["sectors"]},
                ) as seal:
                    header = MarketSectorRotationSnapshot(
                        market_session=key[0],
                        captured_at_utc=now,
                        cutoff_at_utc=datetime.fromisoformat(
                            preview["cutoff_at_utc"].replace("Z", "+00:00")
                        ),
                        algorithm_version=key[1],
                        configuration_digest=key[2],
                        benchmark_symbol=preview["benchmark_symbol"],
                        timeframe=preview["timeframe"],
                        cohort_status="COMPLETE",
                        eligible_count=11,
                        source_material_digest=preview["source_material_digest"],
                        snapshot_digest=preview["snapshot_digest"],
                        evidence_summary_json={
                            "metadata": {
                                k: deepcopy(v) for k, v in preview.items() if k != "sectors"
                            },
                            "sector_inputs": {
                                row["etf_symbol"]: {
                                    k: deepcopy(row[k])
                                    for k in (
                                        "anchor_evidence",
                                        "bar_count",
                                        "first_session",
                                        "last_session",
                                        "bars_used",
                                    )
                                }
                                for row in preview["sectors"]
                            },
                        },
                    )
                    db.add(header)
                    db.flush()
                    seal["snapshot_id"] = header.id
                    for row in preview["sectors"]:
                        db.add(
                            MarketSectorRotationRow(
                                snapshot_id=header.id,
                                sector_canonical=row["run_canonical_sector"],
                                sector_display=row["standard_market_sector"],
                                etf_symbol=row["etf_symbol"],
                                quality_status="ELIGIBLE",
                                price_basis=row["price_basis"],
                                benchmark_price_basis=row["benchmark_price_basis"],
                                anchor_sessions_json=row["required_sessions"],
                                rotation_state=row["rotation_state"],
                                rank=row["rank"],
                                source_digest=row["input_digest"],
                                **{name: row[name] for name in METRIC_NAMES},
                            )
                        )
                    db.flush()
                    identifier = header.id
            return {
                "status": "CREATED",
                "snapshot_id": identifier,
                "snapshot_digest": preview["snapshot_digest"],
            }
        except IntegrityError:
            # A racing PostgreSQL insert loses the natural-key uniqueness check.
            # Re-read only after rollback; do not overwrite, retry or hide other constraints.
            db.rollback()
            with db.no_autoflush:
                existing = _find(db, key)
            if existing is None:
                raise
            return _repeat(existing, preview)

    def detail(self, db, snapshot_id: int) -> dict | None:
        with db.no_autoflush:
            header = db.get(MarketSectorRotationSnapshot, snapshot_id)
            return _detail(db, header) if header is not None else None

    def latest(self, db) -> dict | None:
        with db.no_autoflush:
            header = db.scalars(
                select(MarketSectorRotationSnapshot)
                .order_by(
                    MarketSectorRotationSnapshot.market_session.desc(),
                    MarketSectorRotationSnapshot.id.desc(),
                )
                .limit(1)
            ).first()
            return _detail(db, header) if header is not None else None

    def history(self, db, *, limit=30) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("HISTORY_LIMIT_OUT_OF_RANGE")
        with db.no_autoflush:
            headers = db.scalars(
                select(MarketSectorRotationSnapshot)
                .order_by(
                    MarketSectorRotationSnapshot.market_session.desc(),
                    MarketSectorRotationSnapshot.id.desc(),
                )
                .limit(limit)
            ).all()
            return [
                {
                    "snapshot_id": row.id,
                    "market_session": row.market_session.isoformat(),
                    "algorithm_version": row.algorithm_version,
                    "configuration_digest": row.configuration_digest,
                    "snapshot_digest": row.snapshot_digest,
                    "source_material_digest": row.source_material_digest,
                    "cohort_status": row.cohort_status,
                    "eligible_count": row.eligible_count,
                    "captured_at_utc": _utc(row.captured_at_utc),
                    "source_type": "PERSISTED",
                }
                for row in headers
            ]


def _find(db, key):
    return db.scalars(
        select(MarketSectorRotationSnapshot).where(
            MarketSectorRotationSnapshot.market_session == key[0],
            MarketSectorRotationSnapshot.algorithm_version == key[1],
            MarketSectorRotationSnapshot.configuration_digest == key[2],
        )
    ).first()


def _repeat(existing, preview):
    identical = (
        existing.source_material_digest == preview["source_material_digest"]
        and existing.snapshot_digest == preview["snapshot_digest"]
    )
    return {
        "status": "ALREADY_EXISTS" if identical else "EVIDENCE_CONFLICT",
        "snapshot_id": existing.id,
        "snapshot_digest": existing.snapshot_digest,
    }


def _utc(value):
    # SQLite drops timezone annotations on retrieval; these columns store UTC only.
    return (
        (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _detail(db, header):
    rows = db.scalars(
        select(MarketSectorRotationRow)
        .where(MarketSectorRotationRow.snapshot_id == header.id)
        .order_by(MarketSectorRotationRow.sector_canonical, MarketSectorRotationRow.etf_symbol)
    ).all()
    if len(rows) != 11 or {row.rank for row in rows} != set(range(1, 12)):
        raise ValueError("STORED_ROTATION_INTEGRITY_ERROR")
    result = deepcopy(header.evidence_summary_json["metadata"])
    result.update(
        source_type="PERSISTED",
        snapshot_id=header.id,
        captured_at_utc=_utc(header.captured_at_utc),
        cutoff_at_utc=_utc(header.cutoff_at_utc),
    )
    result["sectors"] = [
        {
            **deepcopy(header.evidence_summary_json["sector_inputs"][row.etf_symbol]),
            "run_canonical_sector": row.sector_canonical,
            "standard_market_sector": row.sector_display,
            "etf_symbol": row.etf_symbol,
            "quality_status": row.quality_status,
            "reasons": [],
            "price_basis": row.price_basis,
            "benchmark_price_basis": row.benchmark_price_basis,
            "required_sessions": deepcopy(row.anchor_sessions_json),
            "rotation_state": row.rotation_state,
            "rank": row.rank,
            "input_digest": row.source_digest,
            **{name: getattr(row, name) for name in METRIC_NAMES},
        }
        for row in rows
    ]
    if snapshot_digest(result) != header.snapshot_digest:
        raise ValueError("STORED_ROTATION_INTEGRITY_ERROR")
    return result


def _validate_capture(preview, config, now):
    if not config.enabled:
        raise ValueError("FEATURE_DISABLED")
    calendar_version = preview.get("calendar_version")
    clock = MarketClockService(calendar_version=calendar_version)
    if (
        preview.get("exchange_timezone") != EXCHANGE_TIMEZONE
        or preview.get("bar_readiness_version") != BAR_READINESS_VERSION
        or preview.get("material_identity_version", material_identity_version(calendar_version))
        != material_identity_version(calendar_version)
    ):
        raise ValueError("CAPTURE_CALENDAR_IDENTITY_MISMATCH")
    cutoff = datetime.fromisoformat(preview["cutoff_at_utc"].replace("Z", "+00:00"))
    market_session = date.fromisoformat(preview["market_session"])
    if (
        now.tzinfo is None
        or cutoff.tzinfo is None
        or cutoff > now
        or clock
        .cutoff_for(now, reason="CAPTURE_VALIDATION")
        .latest_completed_session
        != market_session
        or clock
        .cutoff_for(cutoff, reason="CAPTURE_EVIDENCE")
        .latest_completed_session
        != market_session
    ):
        raise ValueError("CAPTURE_REQUIRES_CURRENT_COMPLETED_SESSION")
    if (
        preview["algorithm_version"] != config.rotation_engine.algorithm_version
        or preview["configuration_digest"] != config.configuration_digest
        or preview["benchmark_symbol"] != config.benchmark_symbol
        or preview["timeframe"] != config.timeframe
        or preview["eligible_count"] != 11
        or preview["total_sector_count"] != 11
    ):
        raise ValueError("CAPTURE_CONFIGURATION_MISMATCH")
    dates = {
        str(key): day.isoformat()
        for key, day in required_sessions(market_session, calendar_version=calendar_version).items()
    }
    expected = [asdict(sector) for sector in config.sectors]
    if (
        len(preview["sectors"]) != 11
        or [{key: row[key] for key in expected[0]} for row in preview["sectors"]] != expected
    ):
        raise ValueError("CAPTURE_UNIVERSE_MISMATCH")
    benchmark = preview["benchmark_quality"]
    inputs = {config.benchmark_symbol: _validate_input(benchmark, dates, config.timeframe)}
    for row in preview["sectors"]:
        if (
            row["quality_status"] != "ELIGIBLE"
            or row["reasons"]
            or row["required_sessions"] != dates
        ):
            raise ValueError("CAPTURE_NOT_COMPLETE")
        material = {**row, "ticker": row["etf_symbol"]}
        inputs[row["etf_symbol"]] = _validate_input(material, dates, config.timeframe)
        if (
            row["price_basis"] != benchmark["price_basis"]
            or row["benchmark_price_basis"] != benchmark["price_basis"]
        ):
            raise ValueError("INCOMPATIBLE_PRICE_BASIS")
        metrics = evaluate_pair(_anchor_closes(row), _anchor_closes(benchmark))
        if any(row[name] != getattr(metrics, name) for name in METRIC_NAMES) or row[
            "rotation_state"
        ] != quadrant(metrics.rs_63_pct, metrics.relative_momentum_pp):
            raise ValueError("CAPTURE_METRIC_MISMATCH")
    ranked = deepcopy(preview["sectors"])
    rank_sectors(ranked)
    if [row["rank"] for row in ranked] != [row["rank"] for row in preview["sectors"]]:
        raise ValueError("CAPTURE_RANK_MISMATCH")
    source = fingerprint(
        {
            "market_session": preview["market_session"],
            "timeframe": config.timeframe,
            "inputs": {symbol: inputs[symbol] for symbol in sorted(inputs)},
        }
    )
    if (
        source != preview["source_material_digest"]
        or snapshot_digest(preview) != preview["snapshot_digest"]
    ):
        raise ValueError("CAPTURE_DIGEST_MISMATCH")


def _anchor_closes(data):
    return {int(offset): anchor["close"] for offset, anchor in data["anchor_evidence"].items()}


def _validate_input(data, dates, timeframe):
    if (
        data["quality_status"] != "ELIGIBLE"
        or data["reasons"]
        or data["price_basis"]
        not in {
            "TRADES",
            "ADJUSTED_LAST",
        }
    ):
        raise ValueError("CAPTURE_INPUT_NOT_ELIGIBLE")
    anchors = data["anchor_evidence"]
    if set(anchors) != set(dates) or any(
        anchors[offset]["session"] != day for offset, day in dates.items()
    ):
        raise ValueError("CAPTURE_ANCHOR_MISMATCH")
    for anchor in anchors.values():
        digest = anchor["source_fingerprint"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("CAPTURE_SOURCE_IDENTITY_REQUIRED")
    digest = fingerprint(
        {
            "ticker": data["ticker"],
            "timeframe": timeframe,
            "price_basis": data["price_basis"],
            "anchors": anchors,
        }
    )
    if digest != data["input_digest"]:
        raise ValueError("CAPTURE_INPUT_DIGEST_MISMATCH")
    return digest
