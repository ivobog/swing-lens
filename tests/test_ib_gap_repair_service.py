from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services import ib_gap_repair_service as repair


def _receipt(*, feed="TRADES", accepted=(3, 5, 6, 9), missing=(4,), conid=1, suffix="a"):
    return SimpleNamespace(
        ticker="HLT",
        what_to_show=feed,
        timeframe="1 day",
        receipt_hash=suffix * 64,
        acquisition_plan_id="parent-plan",
        accepted_at=datetime(2026, 11, 10, tzinfo=UTC),
        contract_identity={
            "symbol": "HLT",
            "conId": conid,
            "secType": "STK",
            "currency": "USD",
            "exchange": "SMART",
        },
        missing_sessions=tuple(date(2026, 11, day) for day in missing),
        payload_json={"accepted_bars": [{"bar_date": f"2026-11-{day:02d}"} for day in accepted]},
    )


def _preview(receipts):
    return repair.build_gap_repair_preview(
        pipeline=SimpleNamespace(id=58, upload_run_id=57, scope_id="retained-scope"),
        context=SimpleNamespace(
            id=103,
            cutoff_at=datetime(2026, 11, 10, tzinfo=UTC),
            latest_completed_session=date(2026, 11, 9),
        ),
        receipts=receipts,
        configuration_reference={"anchor_id": "retained-anchor"},
    )


def test_trade_gap_plan_uses_manifest_day_even_when_latest_bar_is_current():
    receipt = _receipt()
    preview = _preview([receipt])
    item = preview.plan.items[0]
    assert item.duration == "1 D"
    assert item.request_start_date == item.request_end_date == date(2026, 11, 4)
    assert item.request_end_datetime == "20261104-23:59:59"
    assert item.latest_bar_date == item.freshness_threshold_date == date(2026, 11, 9)
    assert preview.manifest["obligations"][0]["missing_sessions"] == ["2026-11-04"]
    assert preview.manifest["configuration_reference"] == {"anchor_id": "retained-anchor"}
    assert receipt.missing_sessions == (date(2026, 11, 4),)
    assert preview.manifest_fingerprint == _preview([receipt]).manifest_fingerprint


def test_adjusted_gap_plan_keeps_frozen_current_endpoint_and_expiry():
    item = _preview([_receipt(feed="ADJUSTED_LAST")]).plan.items[0]
    assert item.duration == "4 D"
    assert item.request_start_date == date(2026, 11, 4)
    assert item.request_end_date == item.reviewed_session_expiry == date(2026, 11, 9)
    assert item.missing_start_date == item.missing_end_date == date(2026, 11, 4)
    assert item.request_end_datetime == ""


def test_later_accepted_receipt_closes_gap_without_modifying_original():
    original = _receipt()
    repaired = _receipt(accepted=(4,), missing=(), suffix="b")
    with pytest.raises(ValueError, match="NO_OUTSTANDING_HISTORY_GAPS"):
        _preview([original, repaired])
    assert original.missing_sessions == (date(2026, 11, 4),)


def test_other_contract_cannot_close_frozen_contract_gap():
    preview = _preview([_receipt(), _receipt(accepted=(4,), missing=(), conid=2, suffix="b")])
    assert preview.plan.items[0].contract_identity["conId"] == 1


def test_legacy_rejected_response_requires_new_admission_not_a_fabricated_receipt():
    with pytest.raises(ValueError, match="GAP_REPAIR_RECEIPT_REQUIRED"):
        _preview([])


def test_changed_manifest_fails_before_enqueuing_or_admitting(monkeypatch):
    preview = _preview([_receipt()])
    monkeypatch.setattr(repair, "prepare_gap_repair", lambda *_a, **_kw: preview)
    db = SimpleNamespace(scalar=lambda *_a: SimpleNamespace(id=58))
    with pytest.raises(ValueError, match="GAP_REPAIR_MANIFEST_CHANGED"):
        repair.submit_gap_repair(db, pipeline_run_id=58, expected_manifest_fingerprint="stale")


def test_submit_creates_one_frozen_child_with_original_configuration(monkeypatch):
    preview = _preview([_receipt()])
    pipeline = SimpleNamespace(id=58, upload_run_id=57, scope_id="retained-scope")
    monkeypatch.setattr(repair, "prepare_gap_repair", lambda *_a, **_kw: preview)
    monkeypatch.setattr(repair, "active_job_for_request_key", lambda *_a: None)
    calls = {}

    def admit(_db, plan, options, **kwargs):
        calls["authority"] = kwargs
        return "child-authority"

    def enqueue(_db, kind, payload, **kwargs):
        calls["payload"], calls["enqueue"] = payload, kwargs
        return SimpleNamespace(id=99)

    monkeypatch.setattr(repair, "_admit_fetch_authority", admit)
    monkeypatch.setattr(
        repair, "create_queued_fetch_run", lambda *_a, **_kw: SimpleNamespace(id=42)
    )
    monkeypatch.setattr(repair, "enqueue_job", enqueue)
    monkeypatch.setattr(repair, "bind_semantic_authority", lambda *_a: None)
    result = repair.submit_gap_repair(
        SimpleNamespace(scalar=lambda *_a: pipeline),
        pipeline_run_id=58,
        expected_manifest_fingerprint=preview.manifest_fingerprint,
    )
    assert (result.fetch_run_id, result.job_id) == (42, 99)
    assert calls["authority"]["parent_scope_id"] == "retained-scope"
    assert calls["authority"]["previous_plan_id"] == "parent-plan"
    assert calls["payload"][repair.ANCHOR_KEY] == {"anchor_id": "retained-anchor"}
    assert calls["payload"]["pipeline_run_id"] == 58
    assert calls["enqueue"]["max_retries"] == 1
    assert preview.plan.requested_tickers == ["HLT"]


def test_worker_rechecks_manifest_before_provider_execution(monkeypatch):
    preview = _preview([_receipt()])
    monkeypatch.setattr(
        repair,
        "current_delivery",
        lambda: SimpleNamespace(
            anchor=preview.manifest["configuration_reference"],
        ),
    )
    monkeypatch.setattr(repair, "prepare_gap_repair", lambda *_a, **_kw: preview)
    scope = SimpleNamespace(
        scope_kind="ib-price-acquisition",
        parent_scope_id="retained-scope",
        acquisition_plan_id="child-plan",
        payload_json={
            "scope_definition": {
                "exact_fetch_plan_hash": repair.Canonical.fingerprint(
                    repair.fetch_plan_to_dict(preview.plan)
                ),
            }
        },
    )
    payload = {
        "gap_repair_manifest": preview.manifest,
        "gap_repair_manifest_fingerprint": preview.manifest_fingerprint,
        "pipeline_run_id": 58,
        "market_calculation_context_id": preview.manifest["market_calculation_context_id"],
        "market_cutoff_at": preview.manifest["market_cutoff_at"],
        "input_as_of_session": preview.manifest["expected_session"],
    }
    kwargs = dict(
        job=SimpleNamespace(payload_json=payload),
        fetch_run=SimpleNamespace(
            run_id=57, scope_id="child-scope", acquisition_plan_id="child-plan"
        ),
        plan=preview.plan,
    )
    repair.validate_gap_repair_delivery(SimpleNamespace(get=lambda *_a: scope), **kwargs)
    scope.parent_scope_id = "unrelated-parent"
    with pytest.raises(ValueError, match="GAP_REPAIR_DELIVERY_CHANGED"):
        repair.validate_gap_repair_delivery(SimpleNamespace(get=lambda *_a: scope), **kwargs)
    payload["gap_repair_manifest_fingerprint"] = "forged"
    with pytest.raises(ValueError, match="GAP_REPAIR_MANIFEST_INVALID"):
        repair.validate_gap_repair_delivery(SimpleNamespace(), **kwargs)


def test_configuration_enrollment_is_limited_to_exact_gap_repair_transport():
    from app.services.configuration_delivery import is_configuration_bound_job

    assert is_configuration_bound_job("FULL_PIPELINE", {})
    assert not is_configuration_bound_job("IB_FETCH", {})
    assert not is_configuration_bound_job("IB_FETCH", {"gap_repair_manifest": {}})
    assert is_configuration_bound_job(
        "IB_FETCH",
        {
            "gap_repair_manifest": {"version": "ib-history-gap-repair-v1"},
            "gap_repair_manifest_fingerprint": "a" * 64,
        },
    )


@pytest.mark.parametrize(
    "field",
    [
        "market_calculation_context_id",
        "market_cutoff_at",
        "input_as_of_session",
    ],
)
def test_repair_rejects_changed_context_witness_before_query_or_provider(monkeypatch, field):
    preview = _preview([_receipt()])
    monkeypatch.setattr(
        repair,
        "current_delivery",
        lambda: SimpleNamespace(
            anchor=preview.manifest["configuration_reference"],
        ),
    )
    payload = {
        "gap_repair_manifest": preview.manifest,
        "gap_repair_manifest_fingerprint": preview.manifest_fingerprint,
        "market_calculation_context_id": preview.manifest["market_calculation_context_id"],
        "market_cutoff_at": preview.manifest["market_cutoff_at"],
        "input_as_of_session": preview.manifest["expected_session"],
    }
    payload[field] = "tampered"
    with pytest.raises(ValueError, match="GAP_REPAIR_CONTEXT_PAYLOAD_CHANGED"):
        repair.validate_gap_repair_delivery(
            SimpleNamespace(),
            job=SimpleNamespace(payload_json=payload),
            fetch_run=None,
            plan=None,
        )


@pytest.mark.parametrize("delivered", [None, SimpleNamespace(anchor={"anchor_id": "wrong"})])
def test_gap_repair_requires_exact_active_delivery_before_query_or_provider(monkeypatch, delivered):
    preview = _preview([_receipt()])
    monkeypatch.setattr(repair, "current_delivery", lambda: delivered)
    payload = {
        "gap_repair_manifest": preview.manifest,
        "gap_repair_manifest_fingerprint": preview.manifest_fingerprint,
    }
    with pytest.raises(ValueError, match="GAP_REPAIR_CONFIGURATION_DELIVERY_"):
        repair.validate_gap_repair_delivery(
            SimpleNamespace(),
            job=SimpleNamespace(payload_json=payload),
            fetch_run=None,
            plan=None,
        )
