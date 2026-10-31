"""Financial selector SQL over committed historical fixtures, not writer certification."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from historical_evidence_support import seed_pre_phase5_evidence
from sqlalchemy.orm import Session
from test_contextual_calculation_identity_adoption import _sector_identity

from app.models.tables import MarketRegimeSnapshot, SectorRotationSnapshot, UploadRun
from app.services.combined_ranking_identity import embed_calculation_identity
from app.services.contextual_calculation_identity import (
    build_regime_identity,
    consumer_context_identity,
    expected_sector_identity,
)
from app.services.contextual_effective_configuration import resolve_regime_configuration
from app.services.core_calculation_evidence import CoreEvidenceKind
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.market_regime_repository import MarketRegimeRepository
from app.services.sector_rotation_repository import SectorRotationRepository
from app.services.sector_rotation_service import (
    _latest_market_snapshot,
    _select_compatible_previous_snapshot,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine
NOW = datetime(2026, 9, 14, 21, tzinfo=UTC)


def test_regime_sql_latest_current_and_asof_isolation(contextual_engine, record_property):
    cutoff = MarketClockService().cutoff_for(NOW, reason="NATIVE_SELECTOR")
    config = load_market_regime_command_center_config()
    repository = MarketRegimeRepository()
    with Session(contextual_engine) as db:
        db.add_all(
            [UploadRun(id=i, filename=f"selector-{i}.csv", status="COMPLETED") for i in (7, 8)]
        )
        db.flush()
        rows = {}
        specs = (
            ("global", None, cutoff, config, True, 0),
            ("tie", None, cutoff, config, True, 0),
            ("owned", 7, cutoff, config, True, 1),
            ("cross_run_compatible", 8, cutoff, config, True, 2),
            (
                "wrong_config",
                None,
                cutoff,
                replace(config, freshness={**config.freshness, "max_stale_trading_days": 999}),
                True,
                3,
            ),
            (
                "wrong_cutoff",
                None,
                replace(cutoff, cutoff_at=NOW + timedelta(seconds=1)),
                config,
                True,
                4,
            ),
            ("noncurrent", 7, cutoff, config, False, 5),
            (
                "future",
                7,
                MarketClockService().cutoff_for(NOW + timedelta(days=1), reason="FUTURE"),
                config,
                True,
                6,
            ),
        )
        for revision, (name, owner, clock, policy, current, seconds) in enumerate(specs, 1):
            retained = resolve_regime_configuration(policy)
            policy = retained.regime_config()
            identity = build_regime_identity(
                market_cutoff=clock,
                config=policy,
                run_id=owner,
                pipeline_id=None,
                source_payload={"retained_query_fixture": name},
            )
            row = MarketRegimeSnapshot(
                run_id=owner,
                as_of_date=clock.latest_completed_session,
                calculation_version=config.calculation_version,
                config_version="selector",
                regime="RISK_ON",
                risk_state="NORMAL",
                score=8,
                action_summary="query fixture",
                revision=revision,
                is_current_revision=current,
                created_at=NOW + timedelta(seconds=seconds),
                debug_json=embed_calculation_identity(
                    {}, identity, policy="HISTORICAL_QUERY_FIXTURE"
                ),
            )
            db.add(row)
            db.flush()
            seed_pre_phase5_evidence(
                db,
                kind=CoreEvidenceKind.REGIME,
                current_row=row,
                calculation_identity=identity,
                effective_configuration=retained.snapshot,
            )
            rows[name] = (row.id, row.evidence_id)
        db.commit()
    # Independent Sessions avoid identity-map answers and exercise all native predicates.
    for _ in range(2):
        with Session(contextual_engine) as db:
            assert (
                repository.latest_for_run_as_of_or_before(db, 7, date(2026, 9, 14)).id,
                repository.latest_for_run_as_of_or_before(db, 7, date(2026, 9, 14)).evidence_id,
            ) == rows["owned"]
            assert repository.latest_for_run(db, 7).id == rows["future"][0]
            assert (
                repository.latest_global_as_of_or_before(db, date(2026, 9, 14)).id
                == rows["wrong_cutoff"][0]
            )
            assert repository.latest_for_run_as_of_or_before(db, 999, date(2026, 9, 14)) is None
            selected = _latest_market_snapshot(repository, db, 7, date(2026, 9, 14), cutoff)
            # Regime compatibility intentionally permits an exact compatible
            # calculation from another run; ownership is not its financial policy.
            assert (selected.id, selected.evidence_id) == rows["cross_run_compatible"]
            assert (
                _latest_market_snapshot(
                    repository,
                    db,
                    7,
                    date(2026, 9, 14),
                    replace(cutoff, cutoff_at=NOW - timedelta(seconds=1)),
                )
                is None
            )
            tied = repository.contextual_candidates_as_of_or_before(db, date(2026, 9, 14))
            tied_ids = [row.id for row in tied]
            assert tied_ids.index(rows["tie"][0]) < tied_ids.index(rows["global"][0])
            assert rows["future"][0] not in tied_ids
            assert rows["noncurrent"][0] not in tied_ids
    record_property("regime_exact_row_and_evidence_ids", rows)


def test_sector_sql_prior_scope_mode_config_and_order(contextual_engine, record_property):
    cutoff = MarketClockService().cutoff_for(NOW, reason="NATIVE_SELECTOR")
    base = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=None)
    expected = expected_sector_identity(
        context=base,
        config_hash="a" * 64,
        calculation_version="sector-rotation-1.0.0",
        mode="universe_only",
    )
    repository = SectorRotationRepository()
    with Session(contextual_engine) as db:
        db.add_all(
            [UploadRun(id=i, filename=f"selector-{i}.csv", status="COMPLETED") for i in (7, 8)]
        )
        db.flush()
        rows = {}
        specs = (
            ("valid", 7, date(2026, 9, 10), "universe_only", "a" * 64, True, 0),
            ("tie", 7, date(2026, 9, 10), "universe_only", "a" * 64, True, 0),
            ("foreign", 8, date(2026, 9, 11), "universe_only", "a" * 64, True, 1),
            ("global", None, date(2026, 9, 10), "universe_only", "a" * 64, True, 2),
            ("wrong_mode", 7, date(2026, 9, 11), "benchmark_relative", "a" * 64, True, 3),
            ("wrong_config", 7, date(2026, 9, 11), "universe_only", "b" * 64, True, 4),
            ("noncurrent", 7, date(2026, 9, 11), "universe_only", "a" * 64, False, 5),
            ("current", 7, date(2026, 9, 14), "universe_only", "a" * 64, True, 6),
            ("future", 7, date(2026, 9, 15), "universe_only", "a" * 64, True, 7),
        )
        for revision, (name, owner, session, mode, digest, current, seconds) in enumerate(specs, 1):
            clock = MarketClockService().cutoff_for(
                datetime.combine(session, datetime.min.time(), tzinfo=UTC).replace(hour=21),
                reason="HISTORICAL_SELECTOR",
            )
            context = consumer_context_identity(market_cutoff=clock, run_id=owner, pipeline_id=None)
            target = expected_sector_identity(
                context=context,
                config_hash=digest,
                calculation_version="sector-rotation-1.0.0",
                mode=mode,
            )
            identity = _sector_identity(target, context)
            row = SectorRotationSnapshot(
                run_id=owner,
                as_of_date=session,
                mode=mode,
                config_hash=digest,
                calculation_version="sector-rotation-1.0.0",
                revision=revision,
                is_current_revision=current,
                created_at=NOW + timedelta(seconds=seconds),
                debug_json=embed_calculation_identity(
                    {}, identity, policy="HISTORICAL_QUERY_FIXTURE"
                ),
            )
            db.add(row)
            db.flush()
            seed_pre_phase5_evidence(
                db, kind=CoreEvidenceKind.SECTOR, current_row=row, calculation_identity=identity
            )
            rows[name] = (row.id, row.evidence_id)
        db.commit()
    for _ in range(2):
        with Session(contextual_engine) as db:
            assert repository.latest_for_run(db, 7).id == rows["future"][0]
            assert (
                repository.latest_for_run_as_of_or_before(db, 7, date(2026, 9, 14)).id
                == rows["current"][0]
            )
            assert (
                repository.latest_global_as_of_or_before(db, date(2026, 9, 14)).id
                == rows["global"][0]
            )
            assert (
                repository.get_previous_snapshot(
                    db, date(2026, 9, 11), "universe_only", "a" * 64, 7
                ).id
                == rows["tie"][0]
            )
            assert (
                repository.get_previous_snapshot(
                    db, date(2026, 9, 11), "benchmark_relative", "a" * 64, 7
                )
                is None
            )
            # Hold date/mode/owner fixed and change only the requested config.
            # This raw repository method includes historical noncurrent rows;
            # the financial candidate method below enforces current revision.
            for digest, name in (("a" * 64, "noncurrent"), ("b" * 64, "wrong_config")):
                prior = repository.get_previous_snapshot(
                    db, date(2026, 9, 14), "universe_only", digest, 7
                )
                assert (prior.id, prior.evidence_id) == rows[name]
            candidates = repository.previous_snapshot_candidates(
                db, as_of_date=date(2026, 9, 14), mode="universe_only"
            )
            ids = [row.id for row in candidates]
            assert ids.index(rows["tie"][0]) < ids.index(rows["valid"][0])
            assert set(ids) == {
                rows[key][0] for key in ("valid", "tie", "foreign", "global", "wrong_config")
            }
            selected = _select_compatible_previous_snapshot(
                candidates, expected=expected, current_session=date(2026, 9, 14)
            )
            assert (selected.id, selected.evidence_id) == rows["foreign"]
            incompatible = replace(
                expected,
                configuration=replace(
                    expected.configuration,
                    effective_configuration=expected_sector_identity(
                        context=base,
                        config_hash="f" * 64,
                        calculation_version="sector-rotation-1.0.0",
                        mode="universe_only",
                    ).configuration.effective_configuration,
                ),
            )
            assert (
                _select_compatible_previous_snapshot(
                    candidates, expected=incompatible, current_session=date(2026, 9, 14)
                )
                is None
            )
    record_property("sector_exact_row_and_evidence_ids", rows)
