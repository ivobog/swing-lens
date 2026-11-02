from datetime import UTC, datetime

import pandas as pd

from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_rotation_math import required_sessions
from app.services.market_sector_intelligence_rotation_service import fingerprint
from app.services.us_market_calendar import subtract_us_trading_sessions

NOW = datetime(2026, 10, 21, 20, 30, tzinfo=UTC)
CUTOFF = MarketClockService().cutoff_for(NOW, reason="SYNTHETIC_TEST")
ANCHORS = required_sessions(CUTOFF.latest_completed_session)


def synthetic_frame(ticker, *, basis="TRADES", end=None):
    end = end or CUTOFF.latest_completed_session
    sessions = [subtract_us_trading_sessions(end, offset) for offset in reversed(range(64))]
    frame = pd.DataFrame({"date": sessions, "close": [100.0] * 64})
    if ticker != "SPY":
        for offset, close in {0: 120.0, 5: 115.0, 21: 110.0, 42: 105.0, 63: 100.0}.items():
            frame.loc[frame["date"] == subtract_us_trading_sessions(end, offset), "close"] = close
    else:
        for offset, close in {0: 110.0, 5: 108.0, 21: 105.0, 42: 102.0, 63: 100.0}.items():
            frame.loc[frame["date"] == subtract_us_trading_sessions(end, offset), "close"] = close
    frame.attrs["price_basis"] = basis
    return frame


def loader_with(frames=None, *, calls=None):
    def load(_db, ticker, **kwargs):
        if calls is not None:
            calls.append((ticker, kwargs))
        frame = (
            frames[ticker].copy()
            if frames is not None and ticker in frames
            else synthetic_frame(ticker)
        )
        frame.attrs["pit_source_manifest"] = {
            "ticker": ticker,
            "what_to_show": frame.attrs.get("price_basis"),
            "timeframe": "1 day",
            "as_of": kwargs["as_of"].isoformat(),
            "max_session": kwargs["max_session"].isoformat(),
            "acquisition_authority": None,
            "states": [
                {
                    "id": index + 1,
                    "fingerprint": fingerprint(
                        {
                            "ticker": ticker,
                            "date": str(row["date"]),
                            "close": str(row["close"]),
                            "basis": frame.attrs.get("price_basis"),
                            "source": "SYNTHETIC",
                        }
                    ),
                }
                for index, (_, row) in enumerate(frame.iterrows())
            ],
        }
        return frame, None

    return load


def complete_preview():
    from app.services.market_sector_intelligence_rotation_service import MarketSectorRotationService

    return MarketSectorRotationService(frame_loader=loader_with()).preview(object(), cutoff=CUTOFF)


def symbols():
    return [sector.etf_symbol for sector in load_market_sector_intelligence_config().sectors] + [
        "SPY"
    ]
