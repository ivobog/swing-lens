"""Read-only twelve-series orchestration with exact exchange-session anchors."""

import hashlib
import json
import logging
import math
from contextlib import nullcontext
from dataclasses import asdict
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.services.market_clock_service import MarketClockService, clock_for_cutoff
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_coverage import CoverageEvidenceError, _session
from app.services.market_sector_intelligence_rotation_math import (
    METRIC_NAMES,
    evaluate_pair,
    quadrant,
    rank_sectors,
    required_sessions,
)
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.us_market_calendar import (
    CALENDAR_VERSION,
    LEGACY_CALENDAR_VERSION,
    is_us_trading_day,
)

logger = logging.getLogger(__name__)
LEGACY_MATERIAL_IDENTITY = "market-sector-rotation-material-v1"
MATERIAL_IDENTITY = "market-sector-rotation-material-v2"


def material_identity_version(calendar_version):
    # Constructor rejects unknown versions; never infer them from wall time.
    MarketClockService(calendar_version=calendar_version)
    return (
        LEGACY_MATERIAL_IDENTITY
        if calendar_version == LEGACY_CALENDAR_VERSION
        else MATERIAL_IDENTITY
    )


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def snapshot_digest(result: dict) -> str:
    """Material identity excludes invocation time and incidental cache coverage."""
    keys = (
        "run_canonical_sector",
        "standard_market_sector",
        "etf_symbol",
        "price_basis",
        "benchmark_price_basis",
        "required_sessions",
        "input_digest",
        "rotation_state",
        "rank",
        *METRIC_NAMES,
    )
    calendar_version = result["calendar_version"]
    identity_version = material_identity_version(calendar_version)
    declared = result.get("material_identity_version")
    if declared != identity_version and not (
        calendar_version == LEGACY_CALENDAR_VERSION and declared is None
    ):
        raise ValueError("ROTATION_MATERIAL_IDENTITY_MISMATCH")
    material = {
        "algorithm_version": result["algorithm_version"],
        "configuration_digest": result["configuration_digest"],
        "market_session": result["market_session"],
        "benchmark_symbol": result["benchmark_symbol"],
        "timeframe": result["timeframe"],
        "source_material_digest": result["source_material_digest"],
        "sectors": [
            {key: row[key] for key in keys}
            for row in sorted(
                result["sectors"],
                key=lambda row: (row["run_canonical_sector"], row["etf_symbol"]),
            )
        ],
    }
    # Valid historical v1 captures already have this canonical universe order.
    # Keep their exact serialization; v2 gets an explicit domain/version binding.
    if calendar_version == LEGACY_CALENDAR_VERSION:
        return fingerprint(material)
    if calendar_version == CALENDAR_VERSION:
        return fingerprint(
            {
                "material_identity_version": identity_version,
                "calendar_version": calendar_version,
                "evidence": material,
            }
        )
    raise ValueError("unsupported US equity calendar version")


class MarketSectorRotationService:
    def __init__(self, *, clock=None, frame_loader=None):
        self.clock = clock or MarketClockService()
        self.frame_loader = frame_loader or load_preferred_ohlcv_frames
        self.native_loader = frame_loader is None

    def preview(self, db, *, config=None, cutoff=None) -> dict:
        config = config or load_market_sector_intelligence_config()
        cutoff = cutoff or self.clock.cutoff_for(
            datetime.now(UTC), reason="MARKET_SECTOR_ROTATION_D2"
        )
        clock_for_cutoff(cutoff)
        anchors = required_sessions(
            cutoff.latest_completed_session, calendar_version=cutoff.calendar_version
        )
        dates = {str(key): value.isoformat() for key, value in anchors.items()}
        inputs = {}
        symbols = [sector.etf_symbol for sector in config.sectors] + [config.benchmark_symbol]
        from app.services.market_sector_intelligence_read_window import batch_cached_read

        with (
            db.no_autoflush if isinstance(db, Session) else nullcontext(),
            batch_cached_read(
                db,
                symbols,
                timeframe=config.timeframe,
                cutoff=cutoff,
                enabled=self.native_loader and config.enabled,
            ),
        ):
            for symbol in symbols:
                inputs[symbol] = self._input(db, symbol, config, cutoff, anchors)
        benchmark = inputs[config.benchmark_symbol]
        rows = []
        for sector in config.sectors:
            data = inputs[sector.etf_symbol]
            row = {
                **asdict(sector),
                "quality_status": "INSUFFICIENT_DATA",
                "reasons": list(data["reasons"]),
                "price_basis": data["price_basis"],
                "benchmark_price_basis": benchmark["price_basis"],
                "required_sessions": dates,
                "bar_count": data["bar_count"],
                "first_session": data["first_session"],
                "last_session": data["last_session"],
                "bars_used": len(data["anchor_evidence"]),
                "anchor_evidence": data["anchor_evidence"],
                "input_digest": data["input_digest"],
                "rank": None,
                "rotation_state": "Insufficient data",
                **dict.fromkeys(METRIC_NAMES),
            }
            if not config.enabled:
                pass
            elif benchmark["quality_status"] != "ELIGIBLE":
                row["reasons"].append("BENCHMARK_UNAVAILABLE")
            elif data["quality_status"] == "ELIGIBLE":
                if data["price_basis"] != benchmark["price_basis"]:
                    row["reasons"].append("INCOMPATIBLE_PRICE_BASIS")
                else:
                    try:
                        metrics = evaluate_pair(_closes(data), _closes(benchmark))
                        row.update(
                            asdict(metrics),
                            quality_status="ELIGIBLE",
                            rotation_state=quadrant(
                                metrics.rs_63_pct, metrics.relative_momentum_pp
                            ),
                        )
                    except ValueError:
                        row["reasons"].append("NONFINITE_METRICS")
            rows.append(row)
        rank_sectors(rows)
        eligible = sum(row["quality_status"] == "ELIGIBLE" for row in rows)
        status = (
            "DISABLED"
            if not config.enabled
            else ("COMPLETE" if eligible == 11 else "PARTIAL" if eligible else "UNAVAILABLE")
        )
        result = {
            "source_type": "PREVIEW",
            "feature_enabled": config.enabled,
            "algorithm_version": config.rotation_engine.algorithm_version,
            "configuration_digest": config.configuration_digest,
            "market_session": cutoff.latest_completed_session.isoformat(),
            "cutoff_at_utc": cutoff.cutoff_at.isoformat().replace("+00:00", "Z"),
            "exchange_timezone": cutoff.exchange_timezone,
            "calendar_version": cutoff.calendar_version,
            "material_identity_version": material_identity_version(cutoff.calendar_version),
            "bar_readiness_version": cutoff.bar_readiness_version,
            "benchmark_symbol": config.benchmark_symbol,
            "timeframe": config.timeframe,
            "cohort_status": status,
            "eligible_count": eligible,
            "total_sector_count": 11,
            "reasons": ["BENCHMARK_UNAVAILABLE"]
            if config.enabled and benchmark["quality_status"] != "ELIGIBLE"
            else [],
            "captured_at_utc": None,
            "benchmark_quality": benchmark,
            "sectors": rows,
            "numerical_units": "returns: percent; relative_momentum_pp: percentage points",
            "methodology": (
                "Transparent ratio-of-ratios quadrant approximation; "
                "not JdK RRG or Technical V4/V5."
            ),
        }
        result["source_material_digest"] = fingerprint(
            {
                "market_session": result["market_session"],
                "timeframe": config.timeframe,
                "inputs": {symbol: inputs[symbol]["input_digest"] for symbol in sorted(inputs)},
            }
        )
        result["snapshot_digest"] = snapshot_digest(result)
        return result

    def _input(self, db, symbol, config, cutoff, anchors):
        result = dict(
            ticker=symbol,
            quality_status="INSUFFICIENT_DATA",
            reasons=[],
            price_basis=None,
            first_session=None,
            last_session=None,
            bar_count=0,
            anchor_evidence={},
            input_digest=None,
        )
        if not config.enabled:
            result["reasons"] = ["FEATURE_DISABLED"]
            return result
        try:
            frame, _volume = self.frame_loader(
                db,
                symbol,
                timeframe=config.timeframe,
                max_session=cutoff.latest_completed_session,
                as_of=cutoff.cutoff_at,
            )
            _extract_input(result, frame, cutoff, anchors, config.timeframe)
        except (CoverageEvidenceError, RotationInputError) as exc:
            result["reasons"] = [
                str(exc) if isinstance(exc, RotationInputError) else "INVALID_SESSION"
            ]
        except Exception as exc:
            logger.warning("Rotation input unavailable for %s (%s)", symbol, type(exc).__name__)
            result["reasons"] = ["PIT_UNAVAILABLE"]
        return result


class RotationInputError(ValueError):
    pass


def _closes(data):
    return {int(offset): anchor["close"] for offset, anchor in data["anchor_evidence"].items()}


def _extract_input(result, frame, cutoff, anchors, timeframe):
    if frame.empty:
        raise RotationInputError("MISSING_DATA")
    basis = frame.attrs.get("price_basis")
    if basis not in {"TRADES", "ADJUSTED_LAST"}:
        raise RotationInputError("UNKNOWN_PRICE_BASIS")
    result["price_basis"] = basis
    if not {"date", "close"}.issubset(frame.columns):
        raise RotationInputError("INVALID_COLUMNS")
    sessions = [_session(value) for value in frame["date"]]
    if any(day > cutoff.latest_completed_session for day in sessions):
        raise RotationInputError("FUTURE_SESSION")
    if any(
        not is_us_trading_day(day, calendar_version=cutoff.calendar_version) for day in sessions
    ):
        raise RotationInputError("INVALID_SESSION")
    if len(set(sessions)) != len(sessions):
        raise RotationInputError("DUPLICATE_SESSION")
    result.update(
        first_session=min(sessions).isoformat(),
        last_session=max(sessions).isoformat(),
        bar_count=len(sessions),
    )
    if cutoff.latest_completed_session not in sessions:
        raise RotationInputError("STALE")
    manifest = frame.attrs.get("pit_source_manifest")
    if not isinstance(manifest, dict) or len(manifest.get("states", [])) != len(frame):
        raise RotationInputError("PIT_UNAVAILABLE")
    if (
        manifest.get("ticker") != result["ticker"]
        or manifest.get("what_to_show") != basis
        or manifest.get("timeframe") != timeframe
        or pd.Timestamp(manifest.get("as_of")) != cutoff.cutoff_at
        or manifest.get("max_session") != cutoff.latest_completed_session.isoformat()
        or manifest.get("acquisition_authority") is not None
    ):
        raise RotationInputError("PIT_BOUNDARY_MISMATCH")
    for column in ("created_at", "first_seen_at"):
        if column in frame.columns:
            for value in frame[column]:
                stamp = pd.Timestamp(value)
                if pd.isna(stamp) or stamp.tzinfo is None or stamp > cutoff.cutoff_at:
                    raise RotationInputError("POST_CUTOFF_OBSERVATION")
    for offset, day in anchors.items():
        if day not in sessions:
            raise RotationInputError(
                "INSUFFICIENT_HISTORY" if day < min(sessions) else "MISSING_ANCHOR"
            )
        index = sessions.index(day)
        value = frame.iloc[index]["close"]
        if pd.api.types.is_bool(value):
            raise RotationInputError("INVALID_CLOSE")
        try:
            close = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RotationInputError("INVALID_CLOSE") from exc
        if not math.isfinite(close) or close <= 0:
            raise RotationInputError("INVALID_CLOSE")
        source = manifest["states"][index].get("fingerprint")
        if (
            not isinstance(source, str)
            or len(source) != 64
            or any(c not in "0123456789abcdef" for c in source)
        ):
            raise RotationInputError("PIT_UNAVAILABLE")
        result["anchor_evidence"][str(offset)] = dict(
            session=day.isoformat(), close=close, source_fingerprint=source
        )
    result["input_digest"] = fingerprint(
        {
            "ticker": result["ticker"],
            "timeframe": timeframe,
            "price_basis": basis,
            "anchors": result["anchor_evidence"],
        }
    )
    result["quality_status"] = "ELIGIBLE"
