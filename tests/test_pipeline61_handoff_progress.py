"""Control-flow and serialization regressions; native financial proof is separate."""
from collections import Counter
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

import app.services.setup_lifecycle.snapshot_builder as snapshots
import app.services.setup_lifecycle.source_loader as sources
import app.services.transition_preflight_plan_service as handoff
from app.models.tables import MarketRegimeSnapshot, TransitionDecisionHandoffManifest
from app.services.decision_handoff_contract import handoff_artifact_identity


class MemoryDb:
    def __init__(self):
        self.transaction = object()
        self.dirty = set()
        self.added = []

    def scalar(self, _statement):
        return None

    def scalars(self, _statement):
        return []

    def get(self, _model, _address):
        return SimpleNamespace(upload_run_id=7, pipeline_run_id=61, result_json={})

    def get_transaction(self):
        return self.transaction

    def add(self, row):
        self.added.append(row)
        row.id = len(self.added)

    def flush(self):
        pass


def cutoff():
    return SimpleNamespace(
        context_id=106, cutoff_at=datetime(2026, 11, 11, 23, tzinfo=UTC),
        latest_completed_session=date(2026, 11, 11), calendar_version="calendar-v1",
        exchange_timezone="America/New_York", daily_bar_ready_at=None,
        bar_readiness_version="ready-v1", cutoff_reason="TEST",
    )


def contexts(count=159):
    sector = SimpleNamespace(id=53, market_regime_snapshot_id=None, seal="valid")
    market = SimpleNamespace(id=53)
    return [
        (SimpleNamespace(
            ticker=f"T{index:03}", raw_row=SimpleNamespace(id=index, run_id=7),
            fundamental_score=None, technical_score=SimpleNamespace(id=index),
            combined_result=None, ranking_results=(), market_regime_snapshot=market,
            sector_rotation_snapshot=sector, sector_rotation_row=None, price_bars=(),
        ), SimpleNamespace(source_data_hash=f"input-{index}"))
        for index in range(count)
    ]


def isolate_lineage(monkeypatch):
    monkeypatch.setattr(handoff, "_temporal_artifact_matches", lambda *_: True)
    monkeypatch.setattr(handoff, "validate_required_handoff_context_artifact", lambda *a, **k: None)
    monkeypatch.setattr(handoff, "invalid_price_bar_rows_for_context", lambda *a, **k: ())


def test_shared_pair_is_validated_once_per_operation_and_never_across_calls(monkeypatch):
    isolate_lineage(monkeypatch)
    rows = contexts()
    calls = []

    def validate(_db, sector, market, **_kwargs):
        calls.append((sector.seal, sector.market_regime_snapshot_id, id(market)))
        if sector.seal != "valid":
            raise ValueError("tampered seal")
        return sector.market_regime_snapshot_id in (None, market.id)

    monkeypatch.setattr(handoff, "_sector_market_parent_matches", validate)
    db = MemoryDb()
    kwargs = dict(upload_run_id=7, market_cutoff=cutoff(), built_rows=rows)
    handoff._validate_handoff_temporal_lineage(db, **kwargs)
    assert len(calls) == 1
    handoff._validate_handoff_temporal_lineage(db, **kwargs)
    assert len(calls) == 2
    rows[0][0].sector_rotation_snapshot.seal = "tampered"
    with pytest.raises(ValueError, match="tampered seal"):
        handoff._validate_handoff_temporal_lineage(db, **kwargs)
    assert len(calls) == 3


@pytest.mark.parametrize("mutation", ["seal", "pointer"])
def test_shared_pair_validation_occurs_after_ticker_callbacks(monkeypatch, mutation):
    isolate_lineage(monkeypatch)
    rows = contexts()
    sector = rows[0][0].sector_rotation_snapshot

    def checkpoint(**progress):
        if progress["processed"] == 10:
            if mutation == "seal":
                sector.seal = "tampered"
            else:
                sector.market_regime_snapshot_id = 999

    def validate(_db, candidate, market, **_kwargs):
        if candidate.seal != "valid":
            raise ValueError("tampered seal")
        return candidate.market_regime_snapshot_id in (None, market.id)

    monkeypatch.setattr(handoff, "_sector_market_parent_matches", validate)
    error = ValueError if mutation == "seal" else handoff.TransitionPreflightError
    with pytest.raises(error):
        handoff._validate_handoff_temporal_lineage(
            MemoryDb(), upload_run_id=7, market_cutoff=cutoff(), built_rows=rows,
            checkpoint_callback=checkpoint,
        )


def test_payload_hashes_each_shared_context_once_after_ticker_callbacks(monkeypatch):
    rows = contexts()
    calls = Counter()
    monkeypatch.setattr(handoff, "validate_required_handoff_context_artifact", lambda *a, **k: None)

    def identity(row, **_kwargs):
        if row is None:
            return None
        calls[id(row)] += 1
        return {"id": row.id, "semantic_hash": getattr(row, "seal", "native")}

    monkeypatch.setattr(handoff, "_artifact_identity", identity)
    seen = []

    def checkpoint(**progress):
        if progress["phase"] != "ARTIFACT_COMPANY_INPUTS_SERIALIZED":
            assert calls[id(rows[0][0].sector_rotation_snapshot)] == 1
            return
        seen.append(progress["processed"])
        # No shared identity has been cached yet, so callbacks cannot make a
        # later ticker silently reuse a successful hash from before mutation.
        assert calls[id(rows[0][0].sector_rotation_snapshot)] == 0

    result = handoff._build_decision_handoff_payload(
        MemoryDb(), plan=SimpleNamespace(id=1, upload_run_id=7, pipeline_run_id=61,
                                        run_start_anchor_fingerprint="anchor"),
        market_cutoff=cutoff(), ceri_market_cutoff=cutoff(), built_rows=rows,
        decision_manifests={}, checkpoint_callback=checkpoint,
    )
    assert seen == list(range(1, 160))
    assert calls[id(rows[0][0].market_regime_snapshot)] == 1
    assert calls[id(rows[0][0].sector_rotation_snapshot)] == 1
    assert len(result["artifact_lineage"]) == 159


def test_actual_streaming_artifact_hash_preserves_bytes_and_cancellation():
    row = MarketRegimeSnapshot(id=53, run_id=7, debug_json={"large": "x" * 1_100_000})
    expected = handoff_artifact_identity(row)
    progress = []
    observed = handoff._artifact_identity(
        row, checkpoint_callback=lambda **values: progress.append(values)
    )
    assert observed == expected
    assert progress and all(item["phase"] == "HANDOFF_EVIDENCE_HASHED" for item in progress)
    assert "canonical-bytes:" in progress[-1]["current_item"]

    def cancel(**_values):
        raise RuntimeError("cancelled during real hash")

    with pytest.raises(RuntimeError, match="cancelled during real hash"):
        handoff._artifact_identity(row, checkpoint_callback=cancel)


@pytest.mark.parametrize("changed", [None, "transaction", "dirty_source", "setup_pointer"])
def test_fresh_plan_preparation_forwards_progress_and_reuses_sources(monkeypatch, changed):
    calls = Counter()
    events = []
    rows = contexts(1)
    rows[0][0].ticker = "AAPL"
    db = MemoryDb()
    clock = cutoff()

    def loaded(_self, _db, _run, **kwargs):
        calls["load"] += 1
        kwargs["checkpoint_callback"](phase="RUN_CONTEXT_SOURCE_LOADED", processed=1,
                                      total=1, current_item="AAPL", last_completed_item="AAPL")
        return SimpleNamespace(tickers=[rows[0][0]])

    def built(_db, _context, **kwargs):
        calls["build"] += 1
        kwargs["checkpoint_callback"](phase="SETUP_INPUTS_BUILT", processed=1,
                                      total=1, current_item="AAPL", last_completed_item="AAPL")
        return rows

    def reconstructed(_db, **kwargs):
        calls["reconstruct"] += 1
        assert kwargs["checkpoint_callback"] is not None
        confidence = "LOW" if changed == "setup_pointer" and calls["reconstruct"] > 1 else "HIGH"
        return {"AAPL": {"decision_manifest": {"candidate": {"confidence": confidence}}}}

    def validate(_db, **kwargs):
        calls["validate"] += 1
        assert kwargs["checkpoint_callback"] is not None

    monkeypatch.setattr(sources.SetupLifecycleSourceLoader, "load_run_context", loaded)
    monkeypatch.setattr(snapshots, "build_run_context_snapshots", built)
    monkeypatch.setattr(snapshots, "SetupLifecycleSnapshotBuilder", lambda: object())
    monkeypatch.setattr(handoff, "_pipeline_downstream_tickers", lambda *a, **k: {"AAPL"})
    monkeypatch.setattr(handoff, "reconstruct_transition_decision_manifests", reconstructed)
    monkeypatch.setattr(handoff, "_resolve_decision_ceri_context", lambda *a, **k: clock)
    monkeypatch.setattr(handoff, "_validate_handoff_temporal_lineage", validate)
    monkeypatch.setattr(
        handoff, "build_run_start_anchor_manifest",
        lambda *a, **kwargs: {"anchor": kwargs["decision_manifests"]},
    )
    monkeypatch.setattr(
        handoff, "_build_decision_handoff_payload", lambda *a, **k: {"proof": "native"}
    )
    def checkpoint(**values):
        events.append(values["phase"])
        if values["phase"] == "PLAN_LOADED":
            if changed == "transaction":
                db.transaction = object()
            elif changed == "dirty_source":
                db.dirty.add(object())

    if changed == "setup_pointer":
        with pytest.raises(handoff.TransitionPreflightError) as error:
            handoff.freeze_transition_decision_handoff_manifest(
                db, upload_run_id=7, market_cutoff=clock, checkpoint_callback=checkpoint,
            )
        assert error.value.code == "DECISION_MANIFEST_MISMATCH"
        assert calls["reconstruct"] == 2
        return
    result = handoff.freeze_transition_decision_handoff_manifest(
        db, upload_run_id=7, market_cutoff=clock, checkpoint_callback=checkpoint,
    )
    assert isinstance(result, TransitionDecisionHandoffManifest)
    preparations = 2 if changed else 1
    assert calls == {"load": preparations, "build": 2, "reconstruct": 2, "validate": 1}
    assert events.index("SETUP_INPUTS_BUILT") < events.index("PLAN_LOADED")
