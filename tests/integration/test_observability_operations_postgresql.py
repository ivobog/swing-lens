from __future__ import annotations

from time import perf_counter

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy.orm import Session

from app.services.operations_service import OperationsService
from app.settings import get_settings

pytestmark = pytest.mark.integration
contextual_engine = contextual.contextual_engine


def test_operations_snapshot_is_bounded_and_completes_within_budget(contextual_engine) -> None:
    service = OperationsService(engine=contextual_engine, settings=get_settings())
    started = perf_counter()
    with Session(contextual_engine) as db:
        snapshot = service.snapshot(db)
    elapsed = perf_counter() - started

    assert elapsed < 3.0
    assert len(snapshot["workers"]) <= 20
    assert len(snapshot["providers"]) <= 50
    assert len(snapshot["recent_roots"]) <= 20
    assert len(snapshot["anomalies"]) <= service.limit
    assert {"health", "supervisor", "queue", "pipelines", "database", "providers"} <= set(snapshot)
