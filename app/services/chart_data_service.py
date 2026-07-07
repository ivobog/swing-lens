from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import TechnicalScore
from app.services.price_bar_repository import load_preferred_ohlcv_frames

EMPTY_CHART_MESSAGE = "No cached chart data. Fetch IB bars first."


def build_ticker_chart_payload(db: Session, run_id: int, ticker: str) -> dict[str, Any]:
    normalized_ticker = ticker.upper()
    price_frame, volume_frame = load_preferred_ohlcv_frames(db, normalized_ticker)

    if price_frame.empty:
        return _empty_payload(normalized_ticker)

    bars = _bars_from_frame(price_frame)
    if not bars:
        return _empty_payload(normalized_ticker)

    volume = _volume_from_frame(volume_frame) if volume_frame is not None else []
    latest_close = bars[-1]["close"]
    technical = db.scalar(
        select(TechnicalScore).where(
            TechnicalScore.run_id == run_id,
            TechnicalScore.ticker == normalized_ticker,
        )
    )

    return {
        "ticker": normalized_ticker,
        "timeframe": "1D",
        "bars": bars,
        "volume": volume,
        "overlays": {
            "sma20": calculate_sma_points(bars, 20),
            "sma50": calculate_sma_points(bars, 50),
            "sma200": calculate_sma_points(bars, 200),
        },
        "levels": _levels_from_technical(technical, latest_close),
        "markers": [],
        "message": None,
    }


def calculate_sma_points(bars: Iterable[Any], length: int) -> list[dict[str, Any]]:
    closes: list[float] = []
    points: list[dict[str, Any]] = []

    for bar in bars:
        close = _field_float(bar, "close")
        time = (
            _field_value(bar, "time")
            or _field_value(bar, "date")
            or _field_value(bar, "bar_date")
        )
        if close is None or time is None:
            continue

        closes.append(close)
        if len(closes) >= length:
            points.append(
                {
                    "time": _date_string(time),
                    "value": round(sum(closes[-length:]) / length, 4),
                }
            )

    return points


def _empty_payload(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "timeframe": "1D",
        "bars": [],
        "volume": [],
        "overlays": {"sma20": [], "sma50": [], "sma200": []},
        "levels": {},
        "markers": [],
        "message": EMPTY_CHART_MESSAGE,
    }


def _bars_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for row in frame.sort_values("date").to_dict("records"):
        open_value = _field_float(row, "open")
        high = _field_float(row, "high")
        low = _field_float(row, "low")
        close = _field_float(row, "close")
        time = row.get("date")
        if None in {open_value, high, low, close} or time is None:
            continue
        bars.append(
            {
                "time": _date_string(time),
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
            }
        )
    return bars


def _volume_from_frame(frame: pd.DataFrame) -> list[dict[str, Any]]:
    volume: list[dict[str, Any]] = []
    for row in frame.sort_values("date").to_dict("records"):
        time = row.get("date")
        if time is None:
            continue
        volume.append(
            {
                "time": _date_string(time),
                "value": int(_field_float(row, "volume") or 0),
            }
        )
    return volume


def _levels_from_technical(
    technical: TechnicalScore | None,
    latest_close: float | None,
) -> dict[str, float]:
    levels: dict[str, float] = {}
    if technical is not None:
        stop = _field_float(technical, "suggested_stop")
        target = _field_float(technical, "suggested_target")
        if stop is not None:
            levels["stop"] = stop
        if target is not None:
            levels["target"] = target
    if latest_close is not None:
        levels["entry_reference"] = latest_close
    return levels


def _field_value(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _field_float(item: Any, key: str) -> float | None:
    value = _field_value(item, key)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _date_string(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
