"""Run-free, SELECT-only ETF technical preview on one frozen PIT context."""

import logging
import math
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pandas as pd
import yaml
from sqlalchemy.orm import Session

from app.services import market_sector_intelligence_technical_adapter as adapter
from app.services.market_clock_service import MarketClockService, clock_for_cutoff
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_coverage import _session
from app.services.market_sector_intelligence_read_window import bounded_cached_read
from app.services.market_sector_intelligence_rotation_service import fingerprint
from app.services.price_bar_repository import load_preferred_ohlcv_frames
from app.services.us_market_calendar import is_us_trading_day, subtract_us_trading_sessions

logger = logging.getLogger(__name__)
OHLCV = ("open", "high", "low", "close", "volume")


class TechnicalConfigurationError(ValueError):
    pass


class MarketSectorTechnicalService:
    def __init__(self, *, clock=None, frame_loader=None):
        self.clock = clock or MarketClockService()
        self.frame_loader = frame_loader or load_preferred_ohlcv_frames
        self.native_loader = frame_loader is None

    def preview(self, db, *, config=None, cutoff=None):
        config = config or load_market_sector_intelligence_config()
        symbols = [sector.etf_symbol for sector in config.sectors]
        try:
            policy = adapter.scoped_policy(symbols)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
            raise TechnicalConfigurationError("TECHNICAL_CONFIGURATION_INVALID") from exc
        cutoff = cutoff or self.clock.cutoff_for(datetime.now(UTC), reason="ETF_TECHNICAL_D3")
        clock_for_cutoff(cutoff)
        first_session = subtract_us_trading_sessions(
            cutoff.latest_completed_session,
            policy.identity["history_window_sessions"] - 1,
            calendar_version=cutoff.calendar_version,
        )
        required_symbols = (
            symbols + [config.benchmark_symbol] + (["QQQ"] if policy.requires_qqq else [])
        )
        sources = {}
        from app.services.market_sector_intelligence_read_window import batch_cached_read

        with (
            db.no_autoflush if isinstance(db, Session) else nullcontext(),
            batch_cached_read(
                db,
                required_symbols,
                timeframe=config.timeframe,
                cutoff=cutoff,
                first=first_session,
                enabled=self.native_loader and config.enabled,
                reuse_fingerprints=False,
            ),
        ):
            for symbol in required_symbols:
                sources[symbol] = self._load(
                    db, symbol, config, cutoff, first_session=first_session
                )
        market, context_reasons = {}, []
        for symbol in [config.benchmark_symbol] + (["QQQ"] if policy.requires_qqq else []):
            if not config.enabled:
                continue
            data = sources[symbol]
            code = (
                "BENCHMARK_UNAVAILABLE"
                if symbol == config.benchmark_symbol
                else "QQQ_CONTEXT_UNAVAILABLE"
            )
            if data["reasons"]:
                context_reasons.append(code)
            elif config.enabled:
                try:
                    market[symbol] = adapter.local_features(
                        data["price"],
                        data["volume"],
                        symbol,
                        cutoff.latest_completed_session,
                        policy,
                        calendar_version=cutoff.calendar_version,
                    ).latest
                except Exception as exc:
                    data["reasons"].append(_error_code(exc))
                    context_reasons.append(code)
        rows, bases, leadership_rows, missing_members = [], {}, [], {}
        for sector in config.sectors:
            from app.services.sector_read_budget import checkpoint

            checkpoint()
            data = sources[sector.etf_symbol]
            reasons = list(dict.fromkeys(data["reasons"] + context_reasons))
            if (
                not reasons
                and data["price_basis"] != sources[config.benchmark_symbol]["price_basis"]
            ):
                reasons.append("INCOMPATIBLE_PRICE_BASIS")
            row = {
                **asdict(sector),
                "selected_price_basis": data["price_basis"],
                "selected_volume_basis": data["volume_basis"],
                "last_session": data["last_session"],
                "source_evidence": _summary(data),
                "input_evidence_digest": fingerprint(
                    {
                        "adapter": policy.identity["adapter_policy_digest"],
                        "calendar_version": cutoff.calendar_version,
                        "sources": {
                            symbol: sources[symbol]["input_digest"]
                            for symbol in (
                                [sector.etf_symbol, config.benchmark_symbol]
                                + (["QQQ"] if policy.requires_qqq else [])
                            )
                        },
                    }
                ),
                "data_reasons": reasons,
                "v4": _unavailable_v4(
                    "DISABLED" if not config.enabled else "INSUFFICIENT_DATA", reasons
                ),
                "v5": _unavailable_v5("DISABLED" if not config.enabled else "NOT_COMPARABLE"),
            }
            if not reasons and config.enabled:
                try:
                    base = adapter.build_base(
                        sector.etf_symbol,
                        data["price"],
                        data["volume"],
                        sources[config.benchmark_symbol]["price"],
                        market[config.benchmark_symbol],
                        market.get("QQQ", {}),
                        cutoff.latest_completed_session,
                        policy,
                        calendar_version=cutoff.calendar_version,
                    )
                    row["v4"] = adapter.v4_payload(base, policy)
                    bases[sector.etf_symbol] = base
                    leadership_row, missing = adapter.leadership_input(base, policy)
                    if missing:
                        missing_members[sector.etf_symbol] = missing
                    else:
                        leadership_rows.append(leadership_row)
                except Exception as exc:
                    row["data_reasons"].append(_error_code(exc))
                    row["v4"] = _unavailable_v4("INSUFFICIENT_DATA", row["data_reasons"])
            if row["v4"]["status"] != "READY":
                missing_members[sector.etf_symbol] = list(row["data_reasons"])
            rows.append(row)
        cohort_complete = config.enabled and len(leadership_rows) == 11 and not missing_members
        if cohort_complete:
            leadership = adapter.fixed_leadership(leadership_rows, symbols, policy)
            for row in rows:
                symbol = row["etf_symbol"]
                try:
                    row["v5"] = adapter.v5_payload(bases[symbol], leadership[symbol], policy)
                except Exception as exc:
                    row["v5"] = _unavailable_v5("ERROR", [_error_code(exc)])
        for row in rows:
            row["comparison"] = _comparison(row)
        v4_count = sum(row["v4"]["status"] == "READY" for row in rows)
        v5_count = sum(row["v5"]["status"] == "READY" for row in rows)
        cohort_status = (
            "DISABLED"
            if not config.enabled
            else "V5_COHORT_NOT_COMPARABLE"
            if not cohort_complete
            else "COMPLETE"
            if v5_count == 11
            else "PARTIAL_ENGINE_FAILURE"
        )
        result = {
            "source_type": "TECHNICAL_PREVIEW",
            "application_scope": "ETF_RESEARCH_DIAGNOSTIC",
            "model_scope": "stock_engine_applied_to_etf",
            "usage": "research_only",
            "feature_enabled": config.enabled,
            "fixed_universe_count": 11,
            "benchmark": "SPY",
            "as_of_utc": cutoff.cutoff_at.isoformat().replace("+00:00", "Z"),
            "market_session": cutoff.latest_completed_session.isoformat(),
            "calendar_version": cutoff.calendar_version,
            "bar_readiness_version": cutoff.bar_readiness_version,
            "read_window_start_session": first_session.isoformat(),
            "cohort_status": "DISABLED"
            if not config.enabled
            else "COMPLETE"
            if v4_count == v5_count == 11
            else "PARTIAL"
            if v4_count or v5_count
            else "UNAVAILABLE",
            "v4_ready_count": v4_count,
            "v5_ready_count": v5_count,
            "v5_cohort_status": cohort_status,
            "v5_missing_members": missing_members,
            "effective_configuration": policy.identity,
            "d2_configuration_digest": config.configuration_digest,
            "market_inputs": {
                symbol: {
                    **_summary(sources[symbol]),
                    "context_role": "market_benchmark"
                    if symbol == "SPY"
                    else "market_regime_context_only",
                }
                for symbol in ["SPY"] + (["QQQ"] if policy.requires_qqq else [])
            },
            "optional_market_context": "QQQ" if policy.requires_qqq else None,
            "rows": rows,
            "evidence_overlap": [
                "Models share OHLCV, trend and SPY-relative evidence.",
                "V5 leadership uses the fixed eleven-ETF cohort.",
                "D2 rotation also uses SPY-relative prices; no D2 measure enters these scores.",
            ],
            "price_semantics": "Selected cached price basis; not certified total return.",
        }
        result["evaluation_digest"] = fingerprint(
            {
                "market_session": result["market_session"],
                "adapter_policy_digest": policy.identity["adapter_policy_digest"],
                "calendar_version": cutoff.calendar_version,
                "bar_readiness_version": cutoff.bar_readiness_version,
                "sources": {
                    symbol: {"digest": data["input_digest"], "reasons": data["reasons"]}
                    for symbol, data in sorted(sources.items())
                },
            }
        )
        return adapter.checked_payload(result)

    def _load(self, db, symbol, config, cutoff, *, first_session=None):
        clock_for_cutoff(cutoff)
        data = dict(
            ticker=symbol,
            price_basis=None,
            volume_basis=None,
            first_session=None,
            last_session=None,
            bar_count=0,
            input_digest=None,
            reasons=[],
            price=None,
            volume=None,
        )
        if not config.enabled:
            data["reasons"] = ["FEATURE_DISABLED"]
            return data
        try:
            if first_session is None:
                policy = adapter.scoped_policy([sector.etf_symbol for sector in config.sectors])
                first_session = subtract_us_trading_sessions(
                    cutoff.latest_completed_session,
                    policy.identity["history_window_sessions"] - 1,
                    calendar_version=cutoff.calendar_version,
                )
            with bounded_cached_read(db, first_session):
                price, volume = self.frame_loader(
                    db,
                    symbol,
                    timeframe=config.timeframe,
                    max_session=cutoff.latest_completed_session,
                    as_of=cutoff.cutoff_at,
                )
            price = _window_frame(price, first_session)
            volume = _window_frame(volume, first_session) if volume is not None else None
            basis = price.attrs.get("price_basis")
            price, price_material = _validate_frame(
                price, symbol, basis, config.timeframe, cutoff, require_volume=volume is None
            )
            volume_basis = basis
            volume_material = None
            if volume is not None:
                volume_basis = volume.attrs.get("volume_basis")
                volume, volume_material = _validate_frame(
                    volume,
                    symbol,
                    volume_basis,
                    config.timeframe,
                    cutoff,
                )
                if list(price["date"]) != list(volume["date"]):
                    raise adapter.TechnicalEvidenceError("MISSING_VOLUME_SESSION")
            working_price = _indicator_frame(price, price_basis=basis)
            if volume is not None:
                # Native normalization checks price volume before its merge.
                # Supply the actual validated effective volume, never a default.
                working_price["volume"] = volume["volume"].to_numpy(copy=True)
            data.update(
                # Preserve the validated manifests outside pandas' indicator
                # working frames: pandas deep-copies attrs on Series operations.
                # OHLCV, dates, source identity and both model inputs stay exact.
                source_manifests={
                    "price": price.attrs["pit_source_manifest"],
                    "volume": volume.attrs["pit_source_manifest"] if volume is not None else None,
                },
                price=working_price,
                volume=_indicator_frame(volume, volume_basis=volume_basis)
                if volume is not None
                else None,
                price_basis=basis,
                volume_basis=volume_basis,
                first_session=price["date"].iloc[0].isoformat(),
                last_session=price["date"].iloc[-1].isoformat(),
                bar_count=len(price),
                input_digest=fingerprint({"price": price_material, "volume": volume_material}),
            )
        except Exception as exc:
            data["reasons"] = [_error_code(exc)]
        return data


def _indicator_frame(frame, **basis):
    working = frame.copy(deep=True)
    working.attrs = basis
    return working


def _window_frame(frame, first_session):
    """Apply the same lower bound to injected frames, preserving state alignment."""
    if frame.empty:
        return frame
    try:
        selected = [i for i, value in enumerate(frame["date"]) if _session(value) >= first_session]
    except (KeyError, ValueError) as exc:
        raise adapter.TechnicalEvidenceError("INVALID_SESSION") from exc
    if len(selected) == len(frame):
        return frame
    manifest = frame.attrs.get("pit_source_manifest")
    if not isinstance(manifest, dict) or len(manifest.get("states", [])) != len(frame):
        raise adapter.TechnicalEvidenceError("PIT_UNAVAILABLE")
    bounded = frame.iloc[selected].copy(deep=True)
    bounded.attrs = deepcopy(frame.attrs)
    bounded.attrs["pit_source_manifest"]["states"] = [manifest["states"][i] for i in selected]
    return bounded


def _validate_frame(frame, symbol, basis, timeframe, cutoff, *, require_volume=True):
    if frame.empty:
        raise adapter.TechnicalEvidenceError("MISSING_DATA")
    if basis not in {"TRADES", "ADJUSTED_LAST"}:
        raise adapter.TechnicalEvidenceError("UNKNOWN_PRICE_BASIS")
    if not set(("date", *OHLCV)).issubset(frame.columns):
        raise adapter.TechnicalEvidenceError("INVALID_COLUMNS")
    # pandas propagates attrs by deep-copying them for column selections and
    # sorting. Keep the full receipt manifest once, outside numerical validation;
    # neither the caller's frame nor any evidence identity is changed.
    frame = frame.copy(deep=False)
    metadata = frame.attrs
    frame.attrs = {key: value for key, value in metadata.items() if key != "pit_source_manifest"}
    try:
        sessions = [_session(value) for value in frame["date"]]
    except ValueError as exc:
        raise adapter.TechnicalEvidenceError("INVALID_SESSION") from exc
    if len(set(sessions)) != len(sessions):
        raise adapter.TechnicalEvidenceError("DUPLICATE_SESSION")
    if any(day > cutoff.latest_completed_session for day in sessions):
        raise adapter.TechnicalEvidenceError("FUTURE_SESSION")
    if any(
        not is_us_trading_day(day, calendar_version=cutoff.calendar_version) for day in sessions
    ):
        raise adapter.TechnicalEvidenceError("INVALID_SESSION")
    if max(sessions) != cutoff.latest_completed_session:
        raise adapter.TechnicalEvidenceError("STALE")
    present = set(sessions)
    day = min(present)
    while day <= cutoff.latest_completed_session:
        if is_us_trading_day(day, calendar_version=cutoff.calendar_version) and day not in present:
            raise adapter.TechnicalEvidenceError("MISSING_TRADING_SESSION")
        day += timedelta(days=1)
    manifest = metadata.get("pit_source_manifest")
    if not isinstance(manifest, dict) or len(manifest.get("states", [])) != len(frame):
        raise adapter.TechnicalEvidenceError("PIT_UNAVAILABLE")
    if (
        manifest.get("ticker") != symbol
        or manifest.get("what_to_show") != basis
        or manifest.get("timeframe") != timeframe
        or pd.Timestamp(manifest.get("as_of")) != cutoff.cutoff_at
        or manifest.get("max_session") != cutoff.latest_completed_session.isoformat()
        or manifest.get("acquisition_authority") is not None
    ):
        raise adapter.TechnicalEvidenceError("PIT_BOUNDARY_MISMATCH")
    for column in ("created_at", "first_seen_at"):
        if column in frame:
            for value in frame[column]:
                stamp = pd.Timestamp(value)
                if pd.isna(stamp) or stamp.tzinfo is None or stamp > cutoff.cutoff_at:
                    raise adapter.TechnicalEvidenceError("POST_CUTOFF_OBSERVATION")
    records = []
    fields = OHLCV if require_volume else OHLCV[:-1]
    values = frame.loc[:, fields].itertuples(index=False, name=None)
    for index, (session, cells) in enumerate(zip(sessions, values, strict=True)):
        record = {"session": session.isoformat()}
        for column, value in zip(fields, cells, strict=True):
            if pd.api.types.is_bool(value):
                raise adapter.TechnicalEvidenceError("INVALID_OHLCV")
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise adapter.TechnicalEvidenceError("INVALID_OHLCV") from exc
            if not math.isfinite(value) or value <= 0:
                raise adapter.TechnicalEvidenceError(
                    "MISSING_VOLUME" if column == "volume" else "INVALID_OHLCV"
                )
            record[column] = value
        if record["high"] < max(record["open"], record["close"], record["low"]) or record[
            "low"
        ] > min(record["open"], record["close"], record["high"]):
            raise adapter.TechnicalEvidenceError("INVALID_OHLC_RANGE")
        digest = manifest["states"][index].get("fingerprint")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise adapter.TechnicalEvidenceError("PIT_UNAVAILABLE")
        record["source_fingerprint"] = digest
        records.append(record)
    material = {
        "symbol": symbol,
        "basis": basis,
        "timeframe": timeframe,
        "bars": sorted(records, key=lambda row: row["session"]),
    }
    normalized = frame.copy(deep=True)
    normalized["date"] = sessions
    normalized = normalized.sort_values("date").reset_index(drop=True)
    normalized.attrs = metadata
    return normalized, material


def _summary(data):
    return {
        key: data[key]
        for key in (
            "ticker",
            "price_basis",
            "volume_basis",
            "first_session",
            "last_session",
            "bar_count",
            "input_digest",
            "reasons",
        )
    }


def _error_code(exc):
    if isinstance(exc, adapter.TechnicalEvidenceError):
        return str(exc)
    logger.warning("ETF technical input/evaluation unavailable (%s)", type(exc).__name__)
    return "TECHNICAL_EVIDENCE_UNAVAILABLE"


def _unavailable_v4(status, reasons):
    return dict(
        status=status,
        final_v4_score=None,
        classification=None,
        action_bias=None,
        confidence=None,
        components=None,
        warnings=list(reasons),
        engine_version=None,
    )


def _unavailable_v5(status, reasons=None):
    return dict(
        status=status,
        technical_composite_score=None,
        confidence_adjusted_score=None,
        classification=None,
        action_bias=None,
        confidence=None,
        components=None,
        leadership_scope=adapter.LEADERSHIP_SCOPE,
        cohort_size=11,
        missing_evidence=reasons or ["V5_COHORT_NOT_COMPARABLE"],
        engine_version=None,
    )


def _comparison(row):
    both = row["v4"]["status"] == row["v5"]["status"] == "READY"
    return {
        "both_available": both,
        "raw_score_delta": row["v5"]["technical_composite_score"] - row["v4"]["final_v4_score"]
        if both
        else None,
        "classification_agreement": row["v4"]["classification"] == row["v5"]["classification"]
        if both
        else None,
        "explanatory_note": (
            "Descriptive arithmetic and exact label equality only. Models are not calibrated "
            "interchangeable predictions; no blended score or trading authorization."
        ),
    }
