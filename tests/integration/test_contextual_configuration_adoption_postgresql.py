from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from alembic.config import Config
from native_mutation_support import seed_native_core, seed_price_frame
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_ibmi_immutable_constituent_evidence import _metric

from alembic import command
from app.models.ceri_tables import CeriCompany
from app.models.tables import CoreCalculationEvidence, UploadRun
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import ceri_config_hash
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.contextual_calculation_identity import (
    REGIME_CONTEXT_COMPATIBILITY,
    build_contextual_result_identity,
    consumer_context_identity,
    contextual_compatibility,
    expected_regime_identity,
    identity_metadata,
)
from app.services.contextual_effective_configuration import (
    contextual_configuration_from_evidence,
    resolve_ceri_configuration,
    resolve_ibmi_configuration,
    resolve_regime_configuration,
    resolve_sector_configuration,
)
from app.services.ib_market_intelligence.calculations import calculate_liquidity
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.decision_evidence import IbmiFeatureConstituents
from app.services.ib_market_intelligence.evidence_hash import evidence_hash
from app.services.ib_market_intelligence.repository import (
    persist_feature,
    persist_historical_metric_bar,
)
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.sector_rotation_service import SectorRotationService

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


@pytest.fixture
def contextual_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def forbidden(*_args, **_kwargs):
    raise AssertionError("provided/historical contextual authority read a live source")


def test_native_regime_full_policy_equal_output_drift_retry_and_cross_run(
    contextual_engine, monkeypatch
):
    cutoff = MarketClockService().cutoff_for(
        datetime(2026, 9, 14, 21, tzinfo=UTC), reason="T13C_PG"
    )
    dates = pd.bdate_range(end=cutoff.latest_completed_session, periods=400)
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": range(100, 500),
            "high": range(102, 502),
            "low": range(99, 499),
            "close": range(101, 501),
            "volume": 1000000,
        }
    )
    c1 = resolve_regime_configuration()
    native = c1.regime_config()
    c2 = resolve_regime_configuration(
        replace(
            native,
            freshness={
                **native.freshness,
                "max_stale_trading_days": native.freshness["max_stale_trading_days"] + 1,
            },
        ),
        pine=c1.values["feature"]["pine"],
        v4=c1.values["feature"]["v4"],
    )
    monkeypatch.setattr(
        "app.services.market_regime_command_center.load_market_regime_command_center_config",
        forbidden,
    )
    monkeypatch.setattr("app.services.technical_indicators.load_pine_defaults", forbidden)
    monkeypatch.setattr(
        "app.services.technical_indicators.load_technical_scoring_v4_config", forbidden
    )
    service = MarketRegimeCommandCenterService()
    with Session(contextual_engine) as db:
        for ticker in ("SPY", "QQQ"):
            seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
        first = service.build_snapshot(db, market_cutoff=cutoff, effective_configuration=c1)
        # DTO debug is named debug rather than the ORM's debug_json.
        from app.services.combined_ranking_identity import calculation_identity_from_debug

        i1 = calculation_identity_from_debug(first.debug)
        e1 = (
            db.query(CoreCalculationEvidence)
            .filter_by(calculation_identity_fingerprint=str(i1.fingerprint()))
            .one()
        )
        original, address = deepcopy(e1.payload_json), e1.id
        second = service.build_snapshot(db, market_cutoff=cutoff, effective_configuration=c2)
        i2 = calculation_identity_from_debug(second.debug)
        assert (first.regime, first.score, first.policy) == (
            second.regime,
            second.score,
            second.policy,
        )
        assert i1.configuration != i2.configuration
        expected = expected_regime_identity(market_cutoff=cutoff, config=c1.regime_config())
        assert contextual_compatibility(expected, i1, policy=REGIME_CONTEXT_COMPATIBILITY).accepted
        assert not contextual_compatibility(
            expected, i2, policy=REGIME_CONTEXT_COMPATIBILITY
        ).accepted
        retry = service.build_snapshot(
            db, market_cutoff=cutoff, effective_configuration=c1, expected_calculation_identity=i1
        )
        assert calculation_identity_from_debug(retry.debug).fingerprint() == i1.fingerprint()
        with pytest.raises(ValueError, match="RETRY_MISMATCH"):
            service.build_snapshot(
                db,
                market_cutoff=cutoff,
                effective_configuration=c2,
                expected_calculation_identity=i1,
            )
        db.commit()
    with Session(contextual_engine) as db:
        historical = db.get(CoreCalculationEvidence, address)
        assert historical.payload_json == original
        monkeypatch.setattr("pathlib.Path.open", forbidden)
        assert contextual_configuration_from_evidence(historical).values == c1.values


def test_native_sector_same_parents_config_drift_preserves_prior_authority(contextual_engine):
    c1 = resolve_sector_configuration()
    with Session(contextual_engine) as seed_db:
        cutoff, _, _ = seed_native_core(
            seed_db, extra_configurations=(c1, resolve_regime_configuration())
        )
    values = c1.values["config"]
    values["defaults"]["min_tickers_for_normal_confidence"] += 1
    c2 = resolve_sector_configuration(values)
    service = SectorRotationService()
    with Session(contextual_engine) as db:
        MarketRegimeCommandCenterService().build_snapshot(
            db, market_cutoff=cutoff, effective_configuration=resolve_regime_configuration()
        )
        first = service.build_sector_rotation_snapshot(
            db, 7, market_cutoff=cutoff, effective_configuration=c1
        )
        native = (
            db.query(CoreCalculationEvidence)
            .filter_by(
                calculation_identity_fingerprint=first.debug["calculation_identity_fingerprint"]
            )
            .one()
        )
        original, address = deepcopy(native.payload_json), native.id
        db.commit()
        # A pipeline retains C1. C2 cannot reuse its ownership while changing
        # the producer's frozen configuration authority.
        with pytest.raises(ValueError, match="MUTATION_RETAINED_CONFIGURATION_MISMATCH"):
            service.build_sector_rotation_snapshot(
                db, 7, market_cutoff=cutoff, effective_configuration=c2
            )
        assert native.payload_json == original
        db.commit()
    with Session(contextual_engine) as db:
        prior = db.get(CoreCalculationEvidence, address)
        assert prior.payload_json == original
        assert (
            contextual_configuration_from_evidence(prior).snapshot.semantic_hash
            == c1.snapshot.semantic_hash
        )


def test_native_ceri_four_outputs_config_drift_and_history(contextual_engine):
    c1 = resolve_ceri_configuration()
    native = c1.ceri_config()
    changed = replace(native, event_risk={**native.event_risk, "secondary_penalty_cap": 2.25})
    changed = replace(changed, config_hash=ceri_config_hash(changed))
    c2 = resolve_ceri_configuration(changed)
    cutoff = MarketClockService().cutoff_for(
        datetime(2026, 9, 14, 21, tzinfo=UTC), reason="T13C_CERI"
    )
    with Session(contextual_engine) as db:
        db.add_all(
            [
                UploadRun(id=7, filename="t13c.csv", status="COMPLETED"),
                CeriCompany(id=1, ticker="ACME"),
            ]
        )
        db.flush()
        evidence = []
        for frozen in (c1, c2):
            config = frozen.ceri_config()
            base = consumer_context_identity(
                market_cutoff=cutoff, run_id=7, pipeline_id=None, ticker="ACME", company_id=1
            )
            identity = frozen.bind(
                build_contextual_result_identity(
                    base=base,
                    namespace="ceri-context",
                    config_hash=config.config_hash,
                    calculation_version=config.engine.calculation_version,
                    engine_version=config.engine.calculation_version,
                    source_artifacts=(),
                    source_payload={"explicit_unavailable_inputs": True},
                    company_id=1,
                )
            )
            service = CeriSnapshotService(effective_configuration=frozen)
            row = service.build_snapshot(
                run_id=7,
                company_id=1,
                ticker="ACME",
                as_of_session=cutoff.latest_completed_session,
                cutoff_at=cutoff.cutoff_at,
                opportunity=CeriOpportunityScoreService(config).calculate(revision_features=[]),
                event_risk=CeriEventRiskService(config).calculate(
                    as_of_session=cutoff.latest_completed_session
                ),
                confidence=CeriConfidenceService(config).calculate(
                    as_of_session=cutoff.latest_completed_session, revision_features=[]
                ),
                source_ids=[],
                evidence_lineage={
                    "historical_view_mode": "AS_KNOWN",
                    **identity_metadata(identity, policy="T13C_NATIVE_PG"),
                },
            )
            service.persist_snapshot(db, row)
            evidence.append(db.get(CoreCalculationEvidence, row.evidence_id))
        assert evidence[0].id != evidence[1].id
        assert (
            contextual_configuration_from_evidence(evidence[0]).snapshot.semantic_hash
            == c1.snapshot.semantic_hash
        )
        assert (
            contextual_configuration_from_evidence(evidence[1]).snapshot.semantic_hash
            == c2.snapshot.semantic_hash
        )
        original, address = deepcopy(evidence[0].payload_json), evidence[0].id
        db.commit()
    with Session(contextual_engine) as db:
        assert db.get(CoreCalculationEvidence, address).payload_json == original


def test_ceri_comparison_uses_sealed_consumer_configuration_with_equal_yaml_hash(contextual_engine):
    from datetime import timedelta

    from app.models.ceri_tables import CeriScoreSnapshot
    from app.services.ceri.change_authority import validate_score_comparison
    from app.services.ceri.change_semantics import ComparisonState, select_prior_comparison

    c1 = resolve_ceri_configuration()
    consumer = {**c1.values["consumer"], "volatility_enabled": True, "ibmi_enabled": True}
    c2 = resolve_ceri_configuration(c1.ceri_config(), consumer=consumer)
    assert c1.ceri_config().config_hash == c2.ceri_config().config_hash
    assert c1.snapshot.semantic_hash != c2.snapshot.semantic_hash
    with Session(contextual_engine) as db:
        db.add_all(
            [
                UploadRun(id=7, filename="prior.csv", status="COMPLETED"),
                UploadRun(id=8, filename="current.csv", status="COMPLETED"),
                UploadRun(id=9, filename="same-policy.csv", status="COMPLETED"),
                CeriCompany(id=1, ticker="ACME"),
            ]
        )
        db.flush()
        rows = []
        for index, frozen in enumerate((c1, c2, c2)):
            run_id = 7 + index
            cutoff = MarketClockService().cutoff_for(
                datetime(2026, 9, 14, 21, tzinfo=UTC) + timedelta(minutes=index),
                reason="CERI_TRANSITION",
            )
            config = frozen.ceri_config()
            base = consumer_context_identity(
                market_cutoff=cutoff, run_id=run_id, pipeline_id=None, ticker="ACME", company_id=1
            )
            identity = frozen.bind(
                build_contextual_result_identity(
                    base=base,
                    namespace="ceri-context",
                    config_hash=config.config_hash,
                    calculation_version=config.engine.calculation_version,
                    engine_version=config.engine.calculation_version,
                    source_artifacts=(),
                    source_payload={"explicit_unavailable_inputs": True},
                    company_id=1,
                )
            )
            service = CeriSnapshotService(effective_configuration=frozen)
            row = service.build_snapshot(
                run_id=run_id,
                company_id=1,
                ticker="ACME",
                as_of_session=cutoff.latest_completed_session,
                cutoff_at=cutoff.cutoff_at,
                opportunity=CeriOpportunityScoreService(config).calculate(revision_features=[]),
                event_risk=CeriEventRiskService(config).calculate(
                    as_of_session=cutoff.latest_completed_session
                ),
                confidence=CeriConfidenceService(config).calculate(
                    as_of_session=cutoff.latest_completed_session, revision_features=[]
                ),
                source_ids=[],
                evidence_lineage={
                    "historical_view_mode": "AS_KNOWN",
                    **identity_metadata(identity, policy="CERI_TRANSITION_PG"),
                },
            )
            service.persist_snapshot(db, row)
            rows.append(row)
        db.commit()
        addresses = [row.id for row in rows]
    # Reload from PostgreSQL; no test-supplied relationship or live config hash.
    with Session(contextual_engine) as db:
        prior, current, same = [db.get(CeriScoreSnapshot, address) for address in addresses]
        selected, state, excluded = select_prior_comparison(current, [prior])
        assert selected is prior and state is ComparisonState.CONFIG_TRANSITION and excluded == 1
        validate_score_comparison(db, current=current, prior=prior, comparison_state=state)
        with pytest.raises(ValueError, match="MUTATION_CERI_CHANGE_CORE_CONFIGURATION_TRANSITION"):
            validate_score_comparison(
                db, current=current, prior=prior, comparison_state=ComparisonState.COMPARABLE
            )
        selected, state, excluded = select_prior_comparison(same, [prior, current])
        assert selected is current and state is ComparisonState.COMPARABLE and excluded == 0
        validate_score_comparison(db, current=same, prior=current, comparison_state=state)


def test_handoff_accepts_only_certified_policy_omitted_sector_parent(contextual_engine):
    from test_technical_work import _synthetic_frame

    from app.models.tables import MarketRegimeSnapshot, SectorRotationSnapshot
    from app.services.transition_preflight_plan_service import _sector_market_parent_matches

    c1 = resolve_sector_configuration()
    regime = resolve_regime_configuration()
    frame = _synthetic_frame()
    for column in ("open", "high", "low", "close"):
        frame[column] = frame[column].iloc[::-1].to_numpy()
    with Session(contextual_engine) as seed_db:
        cutoff, _, _ = seed_native_core(
            seed_db, extra_configurations=(c1, regime), price_frame=frame
        )
    with Session(contextual_engine) as db:
        MarketRegimeCommandCenterService().build_snapshot(
            db, run_id=7, market_cutoff=cutoff, effective_configuration=regime
        )
        SectorRotationService().build_sector_rotation_snapshot(
            db, 7, market_cutoff=cutoff, effective_configuration=c1
        )
        db.commit()
        market = db.query(MarketRegimeSnapshot).filter_by(run_id=7).one()
        sector = db.query(SectorRotationSnapshot).filter_by(run_id=7).one()
        assert sector.market_regime_snapshot_id is None
        assert _sector_market_parent_matches(db, sector, market)
        sector.market_regime_snapshot_id = market.id + 100
        assert not _sector_market_parent_matches(db, sector, market)
        sector.market_regime_snapshot_id = None
        evidence = db.get(CoreCalculationEvidence, sector.evidence_id)
        original = deepcopy(evidence.payload_json)
        changed = deepcopy(original)
        changed["debug"]["contextual_consumer_eligibility"]["regime"]["included"] = True
        evidence.payload_json = changed
        with db.no_autoflush, pytest.raises(
            ValueError, match="MUTATION_SOURCE_PAYLOAD_FINGERPRINT_MISMATCH"
        ):
            _sector_market_parent_matches(db, sector, market)
        db.rollback()
        assert _sector_market_parent_matches(db, sector, market)
        db.rollback()


def test_native_ibmi_liquidity_metric_drift_exact_constituents_history(contextual_engine):
    original = load_ib_market_intelligence_config()
    changed_raw = deepcopy(original.raw)
    changed_raw["liquidity"]["minimum_dollar_volume"] += 1
    changed = replace(original, raw=changed_raw, config_hash=evidence_hash(changed_raw))
    cutoff = datetime(2026, 9, 14, 21, tzinfo=UTC)
    with Session(contextual_engine) as db:
        metric, _ = persist_historical_metric_bar(db, _metric(close=100), observed_at=cutoff)
        evidence = []
        for source in (original, changed):
            frozen = resolve_ibmi_configuration(source, "liquidity")
            config = frozen.ibmi_config(source)
            result = calculate_liquidity(
                [metric],
                as_of=date(2026, 9, 14),
                config={**config.section("liquidity"), **config.section("freshness")},
            )
            row, _ = persist_feature(
                db,
                ticker="ACME",
                ib_conid=1,
                as_of_session=date(2026, 9, 14),
                feature=result,
                config=config,
                calculated_at=cutoff,
                calculation_cutoff_at=cutoff,
                constituents=IbmiFeatureConstituents(metric_bars=(metric,)),
            )
            evidence.append(db.get(CoreCalculationEvidence, row.evidence_id))
        assert evidence[0].id != evidence[1].id
        assert (
            evidence[0].payload_json["derived_output"]["score"]
            == evidence[1].payload_json["derived_output"]["score"]
        )
        assert (
            evidence[0].payload_json["constituent_manifest"]
            == evidence[1].payload_json["constituent_manifest"]
        )
        historical_config = contextual_configuration_from_evidence(evidence[0]).ibmi_config()
        assert historical_config.config_hash == original.config_hash
        result = calculate_liquidity(
            [metric],
            as_of=date(2026, 9, 14),
            config={
                **historical_config.section("liquidity"),
                **historical_config.section("freshness"),
            },
        )
        retry, inserted = persist_feature(
            db,
            ticker="ACME",
            ib_conid=1,
            as_of_session=date(2026, 9, 14),
            feature=result,
            config=historical_config,
            calculated_at=cutoff,
            calculation_cutoff_at=cutoff,
            constituents=IbmiFeatureConstituents(metric_bars=(metric,)),
        )
        assert not inserted and retry.evidence_id == evidence[0].id
        original_payload, address = deepcopy(evidence[0].payload_json), evidence[0].id
        db.commit()
    with Session(contextual_engine) as db:
        assert db.get(CoreCalculationEvidence, address).payload_json == original_payload
