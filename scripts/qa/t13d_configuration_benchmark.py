"""Local computational benchmark, not an end-to-end production latency claim."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from app.services.configuration_delivery import (
    ConfigurationDelivery,
    configuration_delivery_scope,
    resolve_pipeline_configurations,
)
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.winner_probability.feature_extractor import WinnerFeatureExtractor


class EmptyRuleRows:
    def __iter__(self):
        return iter(())

    def all(self):
        return []


class RulePreload:
    reads = 0

    def scalars(self, _statement):
        self.reads += 1
        return EmptyRuleRows()


def benchmark(size=100):
    # Reuse native financial test inputs. Only database/acquisition is absent;
    # the Setup builder and Winner feature extractor are the actual calculators.
    sys.path[:0] = [str(Path("tests").resolve()), str(Path("tests/winner_probability").resolve())]
    from _phase3_helpers import build_run_context
    from setup_lifecycle.test_snapshot_builder import _ticker_context

    setup_input = _ticker_context()
    winner_input = build_run_context()
    db = RulePreload()
    started = perf_counter()
    configurations = resolve_pipeline_configurations(db)
    for configuration in configurations.values():
        configuration.snapshot.as_dict()
    root_ms = (perf_counter() - started) * 1000
    reference = {"anchor_id": "benchmark-only", "fingerprint": "benchmark-only"}
    delivery = ConfigurationDelivery(reference, configurations)
    with (
        configuration_delivery_scope(delivery),
        patch("pathlib.Path.open", side_effect=AssertionError("batch reparsed current file")),
    ):
        started = perf_counter()
        builder = SetupLifecycleSnapshotBuilder()
        for _ in range(size):
            builder.build(setup_input)
        setup_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        native = configurations["decision.winner.prediction"].winner_config()
        extractor = WinnerFeatureExtractor()
        for _ in range(size):
            extractor.extract(
                winner_input,
                winner_input.tickers[0],
                native,
                decision_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
            )
        winner_ms = (perf_counter() - started) * 1000
    return {
        "schema": "t13d-computational-benchmark-v1",
        "items": size,
        "pipeline_bundle_resolution_and_serialization_ms": round(root_ms, 3),
        "configuration_families": len(configurations),
        "rule_preload_queries": db.reads,
        "setup_native_batch_ms": round(setup_ms, 3),
        "winner_native_feature_batch_ms": round(winner_ms, 3),
        "batch_current_file_reads": 0,
        "batch_configuration_db_reads": 0,
        "snapshot_bytes": {
            key: len(json.dumps(value.snapshot.as_dict(), separators=(",", ":")).encode())
            for key, value in configurations.items()
        },
        "limit": "Native test inputs; excludes acquisition/storage/cohort and SLA.",
    }


if __name__ == "__main__":
    result = benchmark()
    target = Path(".qa_work/t13d-benchmark.json")
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "snapshot_bytes"}, sort_keys=True
        )
    )
