"""Completed Setup source graphs must not overlap publication loads."""

import weakref
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.services.setup_lifecycle import snapshot_builder
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService


@dataclass
class Dto:
    ticker: str
    evaluation_run_id: int | None = None
    data_quality_label: str = "HIGH"


class Source:
    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.decoded_payload = bytearray(256 * 1024)


@pytest.mark.parametrize("population", [25, 238])
def test_completed_source_graph_is_released_before_publication(monkeypatch, population):
    references = []

    class Loader:
        def load_run_context(self, *_args, **_kwargs):
            members = tuple(Source(f"S{index}") for index in range(population))
            references.extend(weakref.ref(member) for member in members)
            return SimpleNamespace(tickers=members)

    class Repository:
        next_id = 1
        batches = 0

        def create_evaluation_run(self, *_args, **_kwargs):
            assert all(reference() is None for reference in references)
            return SimpleNamespace(id=7)

        def upsert_snapshots(self, _db, dtos, *, progress_callback=None, should_cancel=None):
            assert all(reference() is None for reference in references)
            assert 0 < len(dtos) <= 10
            self.batches += 1
            result = [SimpleNamespace(id=self.next_id + index) for index in range(len(dtos))]
            self.next_id += len(dtos)
            return result

    def build(_db, context, **_kwargs):
        return tuple(
            (member, SimpleNamespace(dto=Dto(member.ticker), warnings=()))
            for member in context.tickers
        )

    monkeypatch.setattr(snapshot_builder, "build_run_context_snapshots", build)
    repository = Repository()
    service = SetupLifecycleSnapshotCaptureService(loader=Loader(), repository=repository)

    result = service.capture_snapshots_for_run(
        object(), 43, finalize_evaluation_run=False
    )

    assert result.read == result.captured == population
    assert result.failed == 0
    assert repository.batches == (population + 9) // 10
