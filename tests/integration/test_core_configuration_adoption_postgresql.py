from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_core_effective_configuration import changed_configuration, core_configurations
from test_fundamental_ranker_v2 import _quality_values
from test_technical_work import _synthetic_frame

from alembic import command
from app.models.tables import CoreCalculationEvidence, RawCompanyRow, UploadRun
from app.services import (
    combined_decision,
    fundamental_score_service,
    ranking_profile_service,
    technical_score_service,
)
from app.services.calculation_identity import CalculationIdentity
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    get_current_evidence,
    get_evidence_by_id,
    get_evidence_for_identity,
    persist_core_evidence,
)
from app.services.core_effective_configuration import (
    core_configuration_from_evidence,
    resolve_technical_configuration,
)
from app.services.effective_configuration import ConfigurationDrift, configuration_from_evidence
from app.services.market_calculation_context_service import reserve_preflight_market_context
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def _changed_paths(left, right, prefix=""):
    if isinstance(left, dict) and isinstance(right, dict):
        return [
            path
            for key in left.keys() | right.keys()
            for path in _changed_paths(left.get(key), right.get(key), f"{prefix}.{key}")
        ]
    return [prefix] if left != right else []


def test_native_core_producers_frozen_config_drift_retry_history_and_composition(
    disposable_postgres_database, monkeypatch
):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    settings = Settings(
        _env_file=None,
        technical_v5_enabled=False,
        technical_v5_shadow_compare_enabled=False,
        eodhd_api_key="synthetic-core-credential-not-evidence",
    )
    monkeypatch.setattr(technical_score_service, "get_settings", lambda: settings)
    frame = _synthetic_frame()
    # Stable in-memory PIT input adapter; persistence/configuration run natively.
    monkeypatch.setattr(
        technical_score_service, "_load_preferred_bounded", lambda *_args: (frame, frame)
    )
    monkeypatch.setattr(technical_score_service, "_call_price_frame", lambda *_args: frame)
    configurations = list(core_configurations())
    configurations[1] = resolve_technical_configuration(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
        settings=settings,
    )
    try:
        with Session(engine) as db:
            db.add(UploadRun(id=7, filename="core-configuration.csv", status="COMPLETED"))
            db.flush()
            db.add(
                RawCompanyRow(
                    run_id=7,
                    row_number=1,
                    ticker="ACME",
                    raw_json={"Symbol": "ACME", **_quality_values()},
                )
            )
            db.flush()
            cutoff = reserve_preflight_market_context(
                db, upload_run_id=7, cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC)
            )
            args = dict(market_cutoff=cutoff, pipeline_run_id=11)

            def calculate(configs):
                f = fundamental_score_service.recalculate_run_fundamentals(
                    db, 7, **args, effective_configuration=configs[0]
                )[0]
                t = technical_score_service.score_run_technicals(
                    db, 7, tickers=["ACME"], **args, effective_configuration=configs[1]
                )[0]
                c = combined_decision.refresh_combined_results(
                    db, 7, **args, effective_configuration=configs[2]
                )[0]
                r = ranking_profile_service.refresh_ranking_profile(
                    db, 7, "momentum_swing", **args, effective_configuration=configs[3]
                )[0]
                return [db.get(CoreCalculationEvidence, row.evidence_id) for row in (f, t, c, r)]

            first = calculate(configurations)
            ids = [e.id for e in first]
            payloads = [deepcopy(e.payload_json) for e in first]
            assert all(
                "synthetic-core-credential-not-evidence" not in Canonical.dumps(payload)
                for payload in payloads
            )

            # Drift current authoritative entry resolvers: anchored retry must not call them.
            def current_source_attack(*_args, **_kwargs):
                raise AssertionError("anchored retry reread live configuration")

            monkeypatch.setattr(
                fundamental_score_service,
                "resolve_fundamental_configuration",
                current_source_attack,
            )
            monkeypatch.setattr(
                technical_score_service, "resolve_technical_configuration", current_source_attack
            )
            monkeypatch.setattr(
                combined_decision, "resolve_combined_configuration", current_source_attack
            )
            monkeypatch.setattr(
                ranking_profile_service, "get_ranking_profile", current_source_attack
            )
            retry = calculate([core_configuration_from_evidence(e) for e in first])
            for old, repeated in zip(first, retry, strict=True):
                assert old.calculation_identity_json == repeated.calculation_identity_json
                assert old.payload_json == repeated.payload_json, _changed_paths(
                    old.payload_json, repeated.payload_json
                )
            assert [e.id for e in retry] == ids
            second_configs = [changed_configuration(c) for c in configurations]
            second = calculate(second_configs)
            for kind, c1, c2, old, new, original in zip(
                (
                    CoreEvidenceKind.FUNDAMENTAL,
                    CoreEvidenceKind.TECHNICAL,
                    CoreEvidenceKind.COMBINED,
                    CoreEvidenceKind.RANKING,
                ),
                configurations,
                second_configs,
                first,
                second,
                payloads,
                strict=True,
            ):
                assert old.id != new.id
                assert old.calculation_identity_fingerprint != new.calculation_identity_fingerprint
                assert old.payload_json == original
                assert core_configuration_from_evidence(old).values == c1.values
                assert core_configuration_from_evidence(new).values == c2.values
                assert (
                    ConfigurationDrift(
                        configuration_from_evidence(old), configuration_from_evidence(new)
                    ).detected
                    is True
                )
                assert get_evidence_by_id(db, evidence_id=old.id, kind=kind).id == old.id
                assert (
                    get_evidence_for_identity(
                        db,
                        kind=kind,
                        calculation_identity=old.calculation_identity_fingerprint,
                        ticker="ACME",
                    ).id
                    == old.id
                )
                current = get_current_evidence(
                    db,
                    kind=kind,
                    run_id=7,
                    ticker="ACME",
                    ranking_profile="momentum_swing" if kind is CoreEvidenceKind.RANKING else None,
                )
                assert current.id == new.id
                identity = CalculationIdentity.from_canonical_payload(old.calculation_identity_json)
                with pytest.raises(ValueError, match="must match"):
                    persist_core_evidence(
                        db,
                        kind=kind,
                        current_row=SimpleNamespace(run_id=7, ticker="ACME"),
                        payload={},
                        calculation_identity=identity,
                        effective_configuration=c2.snapshot,
                    )
                with pytest.raises(ValueError, match="CORE_EFFECTIVE_CONFIGURATION_REQUIRED"):
                    persist_core_evidence(
                        db, kind=kind, current_row=SimpleNamespace(), calculation_identity=identity
                    )
            assert (
                core_configuration_from_evidence(first[3]).values["profile"]["technical_weight"]
                == 0.55
            )
            assert (
                core_configuration_from_evidence(second[3]).values["profile"]["technical_weight"]
                == 0.6
            )
            for chain in (first, second):
                for downstream in chain[2:]:
                    assert downstream.source_evidence_ids_json["fundamental"] == chain[0].id
                    assert downstream.source_evidence_ids_json["technical"] == chain[1].id
                    assert (
                        "fundamental_components"
                        not in core_configuration_from_evidence(downstream).values
                    )
            # F2 changes downstream identity through upstream evidence;
            # C/R own rules remain unchanged.
            composed = calculate(
                [second_configs[0], configurations[1], configurations[2], configurations[3]]
            )
            assert (
                core_configuration_from_evidence(composed[2]).snapshot.semantic_hash
                == configurations[2].snapshot.semantic_hash
            )
            assert (
                composed[2].calculation_identity_fingerprint
                != first[2].calculation_identity_fingerprint
            )
            assert (
                composed[3].calculation_identity_fingerprint
                != first[3].calculation_identity_fingerprint
            )
            db.commit()
        with Session(engine) as db:
            for evidence_id, original in zip(ids, payloads, strict=True):
                historical = db.get(CoreCalculationEvidence, evidence_id)
                assert historical.payload_json == original
                assert core_configuration_from_evidence(historical) is not None
                historical.payload_json = {"changed": True}
                with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
                    db.flush()
                db.rollback()
                historical = db.get(CoreCalculationEvidence, evidence_id)
                db.delete(historical)
                with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
                    db.flush()
                db.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "kind",
    [
        CoreEvidenceKind.FUNDAMENTAL,
        CoreEvidenceKind.TECHNICAL,
        CoreEvidenceKind.COMBINED,
        CoreEvidenceKind.RANKING,
    ],
)
def test_each_core_legacy_never_inherits_current_configuration(disposable_postgres_database, kind):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    try:
        with Session(engine) as db:
            db.add(UploadRun(id=1, filename="legacy.csv", status="COMPLETED"))
            db.flush()
            evidence = persist_core_evidence(
                db,
                kind=kind,
                current_row=SimpleNamespace(run_id=1, ticker="ACME", evidence_id=None),
                payload={"score": 7},
                calculation_identity=CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME"),
            )
            assert configuration_from_evidence(evidence).state.value == "LEGACY_UNKNOWN"
            assert core_configuration_from_evidence(evidence) is None
            assert "synthetic-sensitive-value" not in Canonical.dumps(evidence.payload_json)
    finally:
        engine.dispose()
