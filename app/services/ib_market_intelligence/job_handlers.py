from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.ib_market_intelligence.orchestration import (
    execute_feature_rebuild,
    execute_flex_import,
    execute_histogram_fetch,
    execute_historical_refresh,
    execute_live_snapshot,
    execute_scanner_run,
)

IB_INTELLIGENCE_HISTORICAL_REFRESH = "IB_INTELLIGENCE_HISTORICAL_REFRESH"
IB_INTELLIGENCE_LIVE_SNAPSHOT = "IB_INTELLIGENCE_LIVE_SNAPSHOT"
IB_SCANNER_RUN = "IB_SCANNER_RUN"
IB_HISTOGRAM_FETCH = "IB_HISTOGRAM_FETCH"
IB_FLEX_IMPORT = "IB_FLEX_IMPORT"
IB_INTELLIGENCE_REBUILD_FEATURES = "IB_INTELLIGENCE_REBUILD_FEATURES"


def implemented_ib_intelligence_job_handlers() -> dict[
    str, Callable[[Session, BackgroundJob], dict[str, Any]]
]:
    return {
        IB_INTELLIGENCE_HISTORICAL_REFRESH: execute_historical_refresh,
        IB_INTELLIGENCE_LIVE_SNAPSHOT: execute_live_snapshot,
        IB_SCANNER_RUN: execute_scanner_run,
        IB_HISTOGRAM_FETCH: execute_histogram_fetch,
        IB_FLEX_IMPORT: execute_flex_import,
        IB_INTELLIGENCE_REBUILD_FEATURES: execute_feature_rebuild,
    }
