from __future__ import annotations

from app.services.setup_lifecycle.adapter_input_audit import (
    adapter_input_specs,
    audit_adapter_input_coverage,
)


def test_every_adapter_input_is_documented_and_registered_for_production() -> None:
    assert audit_adapter_input_coverage() == ()


def test_adapter_input_catalog_contains_complete_lineage_contract() -> None:
    specs = adapter_input_specs()

    assert {item.adapter for item in specs} == {
        "breakout",
        "pullback",
        "vcp",
        "continuation",
        "generic",
    }
    assert all(item.business_meaning for item in specs)
    assert all(item.srs_sdd_rule for item in specs)
    assert all(item.source_entity for item in specs)
    assert all(item.source_path for item in specs)
    assert all(item.source_effective_date for item in specs)
    assert all(item.snapshot_builder_mapping for item in specs)
    assert all(item.signals_json_key == item.signal_key for item in specs)
    assert all(item.null_behavior for item in specs)


def test_obsolete_unsourced_magic_inputs_are_not_in_the_catalog() -> None:
    keys = {item.signal_key for item in adapter_input_specs()}

    assert keys.isdisjoint(
        {
            "follow_through_sessions",
            "support_distance_atr",
            "declining_volume",
            "reversal_ready",
            "support_break",
            "failed_pullback",
            "contraction_count",
            "failed_vcp",
            "failed_continuation",
            "extended_atr_from_trigger",
        }
    )
