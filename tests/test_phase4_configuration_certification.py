"""Phase-4 authority attacks that do not require storage or external services."""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from app.services import contextual_consumer_eligibility, technical_consumer_eligibility
from app.services.column_mapper import map_csv_rows
from app.services.configuration_delivery import (
    ConfigurationDelivery,
    configuration_delivery_scope,
)
from app.services.core_effective_configuration import (
    freeze_core_configuration,
    resolve_fundamental_configuration,
)
from app.services.decision_effective_configuration import (
    configuration_from_payload,
    resolve_readiness_policy_configuration,
)
from app.services.producer_readiness import legacy_readiness
from app.services.winner_probability import consumer_eligibility


def _policies():
    result = {}
    for module in (
        contextual_consumer_eligibility,
        technical_consumer_eligibility,
        consumer_eligibility,
    ):
        for policy in vars(module).values():
            if isinstance(
                policy,
                (
                    contextual_consumer_eligibility.ContextualConsumerPolicy,
                    technical_consumer_eligibility.TechnicalConsumerPolicy,
                ),
            ):
                config = resolve_readiness_policy_configuration(policy)
                result[config.snapshot.family.namespace] = policy
    assert len(result) == 23
    return result


@pytest.mark.parametrize("namespace,policy", list(_policies().items()), ids=list(_policies()))
def test_every_native_readiness_policy_executable_drift_preserves_retained_decision(
    namespace, policy
):
    frozen = resolve_readiness_policy_configuration(policy)
    delivery = ConfigurationDelivery({"test": "C1"}, {namespace: frozen})
    readiness = legacy_readiness(getattr(policy, "producer", "TECHNICAL"))
    with configuration_delivery_scope(delivery):
        original = policy.evaluate(readiness)
        retained = original.to_dto()
        changed = replace(policy, policy_version=policy.policy_version + "-C2")
        with pytest.raises(ValueError, match="READINESS_POLICY_CONFIGURATION_ANCHOR_MISMATCH"):
            changed.evaluate(readiness)
        assert policy.evaluate(readiness).to_dto() == retained
        assert original.policy_version == policy.policy_version
        assert (
            configuration_from_payload(frozen.snapshot.as_dict()).snapshot.as_dict()
            == frozen.snapshot.as_dict()
        )


def test_fundamental_alias_drift_is_material_and_empty_mapping_never_resolves_current(tmp_path):
    alias_path = tmp_path / "aliases.yaml"
    alias_path.write_text("revenue_growth_ttm_yoy: [Revenue C1]\n", encoding="utf-8")
    c1 = resolve_fundamental_configuration(alias_path=alias_path)
    alias_path.write_text("revenue_growth_ttm_yoy: [Revenue C2]\n", encoding="utf-8")
    c2 = resolve_fundamental_configuration(alias_path=alias_path)
    assert c1.snapshot.semantic_hash != c2.snapshot.semantic_hash
    assert c1.snapshot.family.schema_version == "core.fundamental-v2"
    with patch("pathlib.Path.open", side_effect=AssertionError("C1 mapping reread C2")):
        assert map_csv_rows(
            [{"Revenue C1": "18", "Revenue C2": "-90"}],
            c1.values["column_aliases"],
        )[0].canonical == {"revenue_growth_ttm_yoy": "18"}
        assert map_csv_rows([{"Revenue C1": "18"}], {})[0].canonical == {}
        assert "Revenue C1" in json.dumps(configuration_from_payload(c1.snapshot.as_dict()).values)


def test_pre_fix_fundamental_configuration_is_readable_but_cannot_execute_mapping():
    c1 = resolve_fundamental_configuration()
    values = c1.values
    del values["column_aliases"]
    retained = freeze_core_configuration("core.fundamental", values)
    retained = replace(
        retained,
        snapshot=replace(
            retained.snapshot,
            family=replace(retained.snapshot.family, schema_version="core.fundamental-v1"),
        ),
    )
    restored = configuration_from_payload(retained.snapshot.as_dict())
    assert restored.snapshot.semantic_hash == retained.snapshot.semantic_hash
    with pytest.raises(ValueError, match="incomplete core effective configuration"):
        retained.require_family("core.fundamental")


def test_native_ibmi_enum_resolves_the_inherited_module_without_current_reads():
    from app.services.contextual_effective_configuration import resolve_ibmi_configuration
    from app.services.ib_market_intelligence.enums import IntelligenceModule

    c1 = resolve_ibmi_configuration(module="liquidity")
    delivery = ConfigurationDelivery({"test": "C1"}, {"contextual.ibmi.liquidity": c1})
    with (
        configuration_delivery_scope(delivery),
        patch("pathlib.Path.open", side_effect=AssertionError("live config")),
    ):
        assert (
            resolve_ibmi_configuration(module=IntelligenceModule.LIQUIDITY).snapshot == c1.snapshot
        )


@pytest.mark.parametrize("tampered", [False, True])
def test_handoff_validates_immutable_combined_source_pins(tampered):
    from datetime import UTC, date, datetime
    from types import SimpleNamespace as Row

    from app.services.transition_preflight_plan_service import (
        TransitionPreflightError,
        _validate_handoff_temporal_lineage,
    )

    cutoff = Row(
        context_id=11,
        cutoff_at=datetime(2026, 9, 16, 18, tzinfo=UTC),
        latest_completed_session=date(2026, 9, 15),
        calendar_version="fixture-calendar",
    )
    fundamental = Row(id=300, evidence_id=41, run_id=7, ticker="ACME")
    technical = Row(
        id=400,
        evidence_id=42,
        run_id=7,
        ticker="ACME",
        calculation_context_id=11,
        calculation_cutoff_at=cutoff.cutoff_at,
        input_as_of_session=cutoff.latest_completed_session,
        calendar_version=cutoff.calendar_version,
    )
    combined = Row(
        run_id=7,
        ticker="ACME",
        debug_json={
            "source_ids": {
                "raw_row_id": 9,
                "fundamental_evidence_id": 41,
                "technical_evidence_id": 42,
            }
        },
    )
    context = Row(
        ticker="ACME",
        raw_row=Row(id=9, run_id=7),
        fundamental_score=fundamental,
        technical_score=technical,
        combined_result=combined,
        market_regime_snapshot=None,
        sector_rotation_snapshot=None,
        sector_rotation_row=None,
        ranking_results=[],
        price_bars=[],
    )
    evidence = Row(
        source_evidence_ids_json={"fundamental": 999 if tampered else 41, "technical": 42}
    )
    with patch(
        "app.services.core_calculation_evidence.get_certified_evidence_for_row",
        return_value=evidence,
    ):
        if tampered:
            with pytest.raises(TransitionPreflightError, match="combined_evidence_sources"):
                _validate_handoff_temporal_lineage(
                    Row(scalars=lambda _query: []),
                    upload_run_id=7,
                    market_cutoff=cutoff,
                    built_rows=[(context, None)],
                )
        else:
            _validate_handoff_temporal_lineage(
                Row(scalars=lambda _query: []),
                upload_run_id=7,
                market_cutoff=cutoff,
                built_rows=[(context, None)],
            )


@pytest.mark.parametrize(
    "key",
    [
        "database_url",
        "provider_token",
        "ib_credentials",
        "webhook_secret",
        "smtp_password",
        "session_secret",
    ],
)
def test_secret_sentinel_never_enters_semantics_provenance_or_repr(key):
    from app.services.effective_configuration import (
        ConfigurationClassification as Classification,
    )
    from app.services.effective_configuration import (
        ConfigurationEntry,
        EffectiveConfigurationSnapshot,
    )

    original = resolve_fundamental_configuration().snapshot
    entry = ConfigurationEntry(key, "T13E_SECRET_MUST_NOT_APPEAR", Classification.SECURITY_SECRET)
    protected = EffectiveConfigurationSnapshot(
        replace(
            original.family, keys=(*original.family.keys, (key, Classification.SECURITY_SECRET))
        ),
        (*original.entries, entry),
    )
    assert protected.semantic_hash == original.semantic_hash
    assert protected.resolution_hash == original.resolution_hash
    assert "T13E_SECRET_MUST_NOT_APPEAR" not in json.dumps(protected.as_dict()) + repr(protected)


@pytest.mark.parametrize("tampered", [False, True])
def test_projection_normalization_cannot_conceal_changed_combined_source(tampered):
    from types import SimpleNamespace as Row

    from app.services.core_calculation_evidence import CoreEvidenceKind
    from app.services.pipeline_executor import _validate_projection_source_pins

    retained = {"raw_row_id": 9, "fundamental_evidence_id": 41, "technical_evidence_id": 42}
    evidence = Row(
        source_evidence_ids_json={"fundamental": 41, "technical": 42},
        payload_json={"debug_json": {"source_ids": retained}},
    )
    projection = {
        "debug_json": {
            "source_ids": {"raw_row_id": 9, "fundamental_score_id": 100, "technical_score_id": 200},
            "contextual_consumer_eligibility": {"fundamental": {"source_feature_id": 100}},
        }
    }
    from app.models.tables import FundamentalScore, TechnicalScore

    def get_source(model, address):
        assert model is (FundamentalScore if address == 100 else TechnicalScore)
        return Row(evidence_id=(999 if tampered else 41) if address == 100 else 42)

    db = Row(get=get_source)
    if tampered:
        with pytest.raises(ValueError, match="source evidence changed"):
            _validate_projection_source_pins(
                db, kind=CoreEvidenceKind.COMBINED, projection=projection, evidence=evidence
            )
    else:
        _validate_projection_source_pins(
            db, kind=CoreEvidenceKind.COMBINED, projection=projection, evidence=evidence
        )
