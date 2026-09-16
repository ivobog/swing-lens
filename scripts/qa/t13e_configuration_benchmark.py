"""Native computational batches inside a persisted, cold-loaded delivery."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from sqlalchemy import event

from app.services.configuration_delivery import (
    configuration_delivery_scope,
    load_configuration_delivery,
)


def benchmark(session_factory, reference, size=100):
    repo = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo / "tests"), str(repo / "tests/winner_probability")]
    from _phase3_helpers import build_run_context
    from setup_lifecycle.test_snapshot_builder import _ticker_context

    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
    from app.services.winner_probability.feature_extractor import WinnerFeatureExtractor

    selects = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    with session_factory() as db:
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", observe)
        started = perf_counter()
        delivery = load_configuration_delivery(db, reference)
        cold_ms = (perf_counter() - started) * 1000
        cold_selects = len(selects)
        setup_input, winner_input = _ticker_context(), build_run_context()
        with (
            configuration_delivery_scope(delivery),
            patch("pathlib.Path.open", side_effect=AssertionError("batch reread current config")),
        ):
            started = perf_counter()
            builder = SetupLifecycleSnapshotBuilder()
            for _ in range(size):
                builder.build(setup_input)
            setup_ms = (perf_counter() - started) * 1000
            started = perf_counter()
            native = delivery.configurations["decision.winner.prediction"].winner_config()
            extractor = WinnerFeatureExtractor()
            for _ in range(size):
                extractor.extract(
                    winner_input,
                    winner_input.tickers[0],
                    native,
                    decision_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
                )
            winner_ms = (perf_counter() - started) * 1000
        event.remove(engine, "before_cursor_execute", observe)
        return {
            "schema": "t13e-cold-persisted-delivery-native-benchmark-v1",
            "items": size,
            "families": len(delivery.configurations),
            "cold_bundle_selects": cold_selects,
            "cold_bundle_ms": round(cold_ms, 3),
            "setup_100_ms": round(setup_ms, 3),
            "winner_features_100_ms": round(winner_ms, 3),
            "batch_db_selects": len(selects) - cold_selects,
            "batch_current_file_reads": 0,
            "snapshot_bytes": sum(
                len(json.dumps(cfg.snapshot.as_dict()).encode())
                for cfg in delivery.configurations.values()
            ),
            ("limit"): (
                "Native synthetic computational inputs; public Ranking/Regime "
                "stage timings measured separately; no production SLA."
            ),
        }
