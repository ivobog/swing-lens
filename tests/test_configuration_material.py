"""Warm-cache attacks on pure configuration calculations, never authority reuse."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from types import SimpleNamespace

import pytest
from test_core_effective_configuration import core_configurations

from app.services import configuration_delivery
from app.services.contextual_effective_configuration import _restore
from app.services.core_effective_configuration import CoreEffectiveConfiguration
from app.services.core_mutation_authority import _configuration_material, _validate_configuration
from app.services.effective_configuration import EffectiveConfigurationSnapshot


@pytest.mark.parametrize(
    "attack",
    ["semantic", "resolution", "identity", "value", "provenance", "boolean-type"],
)
def test_warm_configuration_material_rechecks_declared_and_typed_content(attack):
    configuration = deepcopy(core_configurations()[2].snapshot)
    entry = configuration.entries[0]
    if attack == "boolean-type":
        object.__setattr__(entry, "defaulted", True)
        for name in ("semantic_hash", "resolution_hash", "identity"):
            configuration.__dict__.pop(name, None)
    context = SimpleNamespace(pipeline_run_id=None, execution=None)
    _validate_configuration(object(), context, configuration)
    if attack == "semantic":
        object.__setattr__(configuration, "semantic_hash", "0" * 64)
    elif attack == "resolution":
        object.__setattr__(configuration, "resolution_hash", "0" * 64)
    elif attack == "identity":
        object.__setattr__(configuration, "identity", core_configurations()[0].snapshot.identity)
    elif attack == "value":
        object.__setattr__(entry, "canonical_value_json", '{"type":"string","value":"changed"}')
    elif attack == "provenance":
        object.__setattr__(entry, "source", replace(entry.source, identifier="changed-source"))
    else:
        # Python considers True == 1; canonical configuration evidence does not.
        object.__setattr__(entry, "defaulted", 1)
    with pytest.raises(ValueError, match="MUTATION_CONFIGURATION_FINGERPRINT_MISMATCH"):
        _validate_configuration(object(), context, configuration)


@pytest.mark.parametrize("field", ["producer_version", "evaluated_at", "compatibility_policy"])
def test_warm_configuration_material_keeps_exact_retained_metadata(monkeypatch, field):
    retained = core_configurations()[2].snapshot
    lookups = []
    monkeypatch.setattr(configuration_delivery, "binding_reference", lambda *a, **k: {})

    def delivery(*args, **kwargs):
        lookups.append(kwargs)
        return SimpleNamespace(
            configurations={"core.combined": CoreEffectiveConfiguration(retained)}
        )

    monkeypatch.setattr(configuration_delivery, "load_configuration_delivery", delivery)
    context = SimpleNamespace(pipeline_run_id=17, execution=None)
    _validate_configuration(object(), context, retained)
    _validate_configuration(object(), context, retained)
    assert len(lookups) == 2
    changed = (
        replace(retained, producer_version="different-producer")
        if field == "producer_version"
        else replace(retained, evaluated_at=datetime(2026, 11, 17, tzinfo=UTC))
        if field == "evaluated_at"
        else replace(
            retained, family=replace(retained.family, compatibility_policy="different-policy")
        )
    )
    assert changed.semantic_hash == retained.semantic_hash
    assert changed.resolution_hash == retained.resolution_hash
    with pytest.raises(ValueError, match="MUTATION_RETAINED_CONFIGURATION_MISMATCH"):
        _validate_configuration(object(), context, changed)
    assert len(lookups) == 3


def test_nonstandard_snapshot_extension_keeps_uncached_serialization():
    class ExtendedSnapshot(EffectiveConfigurationSnapshot):
        extra = "first"

        def as_dict(self):
            return {**super().as_dict(), "extension": self.extra}

    original = core_configurations()[2].snapshot
    configuration = ExtendedSnapshot(original.family, original.entries)
    before = _configuration_material(configuration)
    ExtendedSnapshot.extra = "second"
    after = _configuration_material(configuration)
    assert before[:2] == after[:2]
    assert before[3] != after[3]


def test_restore_shares_only_type_metadata_within_each_fresh_call():
    class First(Enum):
        VALUE = "one"

    class Second(Enum):
        VALUE = "one"

    @dataclass
    class Row:
        value: First

    @dataclass
    class Bundle:
        rows: list[Row]

    payload = {"rows": [{"value": "one"}, {"value": "one"}]}
    first = _restore(Bundle, payload)
    Row.__annotations__["value"] = Second
    second = _restore(Bundle, payload)
    assert [row.value for row in first.rows] == [First.VALUE, First.VALUE]
    assert [row.value for row in second.rows] == [Second.VALUE, Second.VALUE]
    assert first.rows[0] is not first.rows[1]
    first.rows.clear()
    assert len(second.rows) == len(payload["rows"]) == 2
