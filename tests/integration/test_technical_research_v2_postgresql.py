from copy import deepcopy

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
import test_core_effective_configuration as core_fixtures
from native_mutation_support import seed_native_core
from sqlalchemy.orm import Session
from test_fundamental_ranker_v2 import _quality_values
from test_technical_work import _synthetic_frame

from app.models.tables import CoreCalculationEvidence
from app.services.core_effective_configuration import freeze_core_configuration
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
from app.services.winner_probability.consumer_eligibility import (
    WinnerSourceEligibilityError,
    winner_decision_inputs,
)

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_new_research_configuration_admits_but_never_grants_transitive_trading_authority(
    contextual_engine, monkeypatch
):
    import app.settings as settings_module

    active = settings_module.get_settings().model_copy(
        update={"fundamental_admission_mode": settings_module.FundamentalAdmissionMode.ACTIVE}
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: active)
    original_factory = core_fixtures.core_configurations
    legacy = list(original_factory())
    for index in (2, 3):
        old = legacy[index]
        values = old.values
        values.pop("technical_admission")
        values.pop("technical_research_decision_ceiling", None)
        legacy[index] = freeze_core_configuration(
            old.snapshot.family.namespace,
            values,
            sources={
                entry.key: entry.source
                for entry in old.snapshot.entries
                if not entry.key.startswith("technical_admission.")
                and entry.key != "technical_research_decision_ceiling"
            },
        )
    frame = _synthetic_frame()
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].iloc[::-1].to_numpy()
    # The shared quality fixture intentionally omits three coverage fields.
    # This regression requires a genuinely complete Fundamental producer.
    raw = {
        **_quality_values(),
        "total_assets_estimate_annual": "11000000000",
        "ebit_per_share_ttm": "6",
        "number_of_shareholders_annual": "5000",
    }
    from app.services.column_mapper import load_alias_map

    aliases = load_alias_map()
    raw = {aliases[key][0]: value for key, value in raw.items()}
    with Session(contextual_engine) as db:
        monkeypatch.setattr(core_fixtures, "core_configurations", lambda: tuple(legacy))
        _, _, v1 = seed_native_core(db, run_id=7, price_frame=frame, raw_values=raw)
        assert not v1["combined"].is_complete and not v1["ranking"].is_complete
        assert (
            v1["combined"].debug_json["technical_consumer_eligibility"]["decision"][
                "policy_version"
            ]
            == "technical-to-combined-v1"
        )
        addresses = [row.evidence_id for row in v1.values()]
        original_payloads = {
            address: deepcopy(db.get(CoreCalculationEvidence, address).payload_json)
            for address in addresses
        }
        monkeypatch.setattr(core_fixtures, "core_configurations", original_factory)
        cutoff, _, v2 = seed_native_core(
            db, run_id=8, price_frame=frame, raw_values=raw, contextual_snapshots=True
        )
        source = db.get(CoreCalculationEvidence, v2["technical"].evidence_id)
        assert source.payload_json["producer_readiness"]["status"] == "DEGRADED"
        for kind in ("combined", "ranking"):
            row = v2[kind]
            assert row.is_complete and row.has_fundamental and row.has_technical, {
                "technical_status": row.debug_json["technical_consumer_eligibility"]["decision"][
                    "status"
                ],
                "fundamental": row.debug_json["contextual_consumer_eligibility"]["fundamental"],
            }
            permission = row.debug_json["technical_consumer_eligibility"]["decision"]
            if kind == "combined":
                from app.services.core_effective_configuration import (
                    core_configuration_from_evidence,
                )

                configuration = core_configuration_from_evidence(
                    db.get(CoreCalculationEvidence, row.evidence_id)
                )
                assert (
                    configuration.values["technical_research_decision_ceiling"]
                    == "preserve-stricter-v1"
                )
            assert permission["policy_version"] == f"technical-to-{kind}-v2"
            assert permission["status"] == "ELIGIBLE"
            assert permission["justification"]["research_only"] is True
            assert all(permission["justification"]["checks"].values())
            assert row.position_size_hint == "No new entry"
            label = row.combined_decision if kind == "combined" else row.decision_label
            assert label not in {"Candidate", "Strong candidate"}
        db.commit()
        context = SetupLifecycleSourceLoader().load_run_context(db, 8, market_cutoff=cutoff)
        ticker = context.tickers[0]
        setup = SetupLifecycleSnapshotBuilder().build(ticker)
        assert setup.dto.data_quality_label == "INSUFFICIENT"
        assert setup.dto.promoted_fields["dual_score"] is None
        assert setup.dto.signals["setup_score"]["value"] is None
        assert (
            setup.dto.source_lineage["technical_consumer_eligibility"]["decision"]["policy_version"]
            == "technical-to-setup-v1"
        )
        with pytest.raises(WinnerSourceEligibilityError) as blocked:
            winner_decision_inputs(context, ticker)
        assert "technical" in blocked.value.sources
        assert blocked.value.reason == "winner_readiness_undecided"
        for address, payload in original_payloads.items():
            assert db.get(CoreCalculationEvidence, address).payload_json == payload
    with Session(contextual_engine) as db:
        assert all(
            db.get(CoreCalculationEvidence, address).payload_json == payload
            for address, payload in original_payloads.items()
        )
