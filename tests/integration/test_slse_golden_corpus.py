from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    MarketRegimeSnapshot,
    PriceBar,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
    TechnicalScore,
    UploadRun,
)
from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService
from app.services.setup_lifecycle.export_service import export_alerts_csv, export_changes_csv
from app.services.setup_lifecycle.maintenance_service import SetupLifecycleMaintenanceService
from app.services.setup_lifecycle.query_service import (
    SetupLifecycleFilters,
    SetupLifecycleListQuery,
    SetupLifecycleQueryService,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_FIXTURE_VERSION = "slse-golden-1.0.0"


@dataclass(frozen=True)
class SourceDay:
    as_of: date
    setup_score: float | None = 6.0
    technical_score: float | None = 7.5
    classification: str | None = "Breakout Base"
    close: float | None = 95.0
    pivot: float = 100.0
    trend_score: float = 7.5
    relative_strength: float = 8.0
    leadership_score: float = 8.0
    range_percentile: float = 55.0
    volume_percentile: float = 55.0
    sector_rank: int = 5
    market_regime: str = "RISK_ON"
    market_gate: bool = True
    earnings_risk: str = "LOW"
    liquidity_score: float = 8.0
    processed_as_of: date | None = None
    add_bar: bool = True
    include_optional_context: bool = True
    derived: dict[str, object] = field(default_factory=dict)
    contraction: bool = False
    box_failure: bool = False


@dataclass(frozen=True)
class ProductionScenario:
    name: str
    ticker: str
    days: tuple[SourceDay, ...]
    expected_family: str
    expected_state: str
    expected_actionability: str
    expected_alert_types: tuple[str, ...] = ()


PRODUCTION_SCENARIOS = (
    ProductionScenario(
        "clean breakout",
        "GBRK",
        (
            SourceDay(date(2026, 7, 27), derived={"volume_dry_up": True}),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                close=99,
                range_percentile=35,
                volume_percentile=30,
                contraction=True,
            ),
            SourceDay(
                date(2026, 7, 29),
                setup_score=7.8,
                close=101,
                range_percentile=30,
                volume_percentile=25,
                contraction=True,
                derived={"fresh_breakout": True},
            ),
            SourceDay(
                date(2026, 7, 30),
                setup_score=8.0,
                close=102,
                range_percentile=28,
                volume_percentile=24,
                contraction=True,
                derived={"fresh_breakout": True},
            ),
            SourceDay(
                date(2026, 7, 31),
                setup_score=8.1,
                close=102,
                range_percentile=27,
                volume_percentile=23,
                contraction=True,
                derived={"fresh_breakout": True},
            ),
            SourceDay(
                date(2026, 8, 3),
                setup_score=8.1,
                close=102,
                range_percentile=27,
                volume_percentile=23,
                contraction=True,
                derived={"fresh_breakout": True},
            ),
        ),
        "BREAKOUT",
        "CONFIRMED",
        "ACTIONABLE",
        ("NEW_READY", "NEW_TRIGGER", "NEW_CONFIRMATION"),
    ),
    ProductionScenario(
        "failed breakout",
        "GFBR",
        (
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=101),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                close=96,
                derived={"failed_breakout": True},
            ),
        ),
        "BREAKOUT",
        "FAILED",
        "BLOCKED",
        ("NEW_FAILURE",),
    ),
    ProductionScenario(
        "clean bull pullback",
        "GPBK",
        (
            SourceDay(
                date(2026, 7, 27),
                setup_score=7.8,
                classification="Pullback Uptrend",
                close=99,
                range_percentile=38,
                volume_percentile=35,
                derived={
                    "held_near_support": True,
                    "red_vol_declining": True,
                },
            ),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                classification="Pullback Uptrend",
                close=101,
                range_percentile=32,
                volume_percentile=30,
                derived={
                    "held_near_support": True,
                    "red_vol_declining": True,
                    "volume_ratio": 1.2,
                },
            ),
            SourceDay(
                date(2026, 7, 29),
                setup_score=8.0,
                classification="Pullback Uptrend",
                close=102,
                range_percentile=28,
                volume_percentile=25,
                derived={
                    "held_near_support": True,
                    "red_vol_declining": True,
                    "volume_ratio": 1.2,
                },
            ),
            SourceDay(
                date(2026, 7, 30),
                setup_score=8.1,
                classification="Pullback Uptrend",
                close=103,
                range_percentile=27,
                volume_percentile=24,
                derived={
                    "held_near_support": True,
                    "red_vol_declining": True,
                    "volume_ratio": 1.2,
                },
            ),
        ),
        "PULLBACK",
        "CONFIRMED",
        "ACTIONABLE",
        ("NEW_TRIGGER", "NEW_CONFIRMATION"),
    ),
    ProductionScenario(
        "deteriorating pullback",
        "GDPB",
        (
            SourceDay(
                date(2026, 7, 27),
                setup_score=7.8,
                classification="Pullback Uptrend",
                close=99,
                derived={"held_near_support": True},
            ),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                classification="Pullback Uptrend",
                close=95,
                derived={"heavy_mid_ma_break": True},
            ),
        ),
        "PULLBACK",
        "FAILED",
        "BLOCKED",
        ("NEW_FAILURE",),
    ),
    ProductionScenario(
        "VCP",
        "GVCP",
        (
            SourceDay(
                date(2026, 7, 27),
                classification="VCP",
                setup_score=6.5,
                volume_percentile=30,
                contraction=True,
            ),
            SourceDay(
                date(2026, 7, 28),
                classification="VCP",
                setup_score=7.8,
                close=99,
                volume_percentile=28,
                contraction=True,
            ),
            SourceDay(
                date(2026, 7, 29),
                classification="VCP",
                setup_score=7.8,
                close=101,
                volume_percentile=25,
                contraction=True,
            ),
        ),
        "VCP",
        "TRIGGERED",
        "ACTIONABLE",
        ("NEW_READY", "NEW_TRIGGER"),
    ),
    ProductionScenario(
        "continuation",
        "GCON",
        (
            SourceDay(
                date(2026, 7, 27),
                classification="Continuation Pause",
                setup_score=6.5,
                range_percentile=35,
            ),
            SourceDay(
                date(2026, 7, 28),
                classification="Continuation Pause",
                setup_score=7.8,
                close=99,
                range_percentile=30,
            ),
            SourceDay(
                date(2026, 7, 29),
                classification="Continuation Pause",
                setup_score=7.8,
                close=101,
                range_percentile=28,
            ),
        ),
        "CONTINUATION",
        "TRIGGERED",
        "ACTIONABLE",
        ("NEW_READY", "NEW_TRIGGER"),
    ),
    ProductionScenario(
        "extended momentum",
        "GEXT",
        (
            SourceDay(
                date(2026, 7, 27),
                classification="Continuation Pause",
                setup_score=7.8,
                close=99,
                range_percentile=35,
            ),
            SourceDay(
                date(2026, 7, 28),
                classification="Continuation Pause",
                setup_score=7.8,
                close=99,
                range_percentile=30,
            ),
            SourceDay(
                date(2026, 7, 29),
                classification="Continuation Pause",
                setup_score=8.0,
                close=110,
                range_percentile=25,
                derived={"atr": 3.0},
            ),
        ),
        "CONTINUATION",
        "EXTENDED",
        "WATCH_ONLY",
        ("NEW_EXTENSION",),
    ),
    ProductionScenario(
        "market gate block",
        "GMKT",
        (
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=99),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                close=99,
                market_regime="RISK_OFF",
                market_gate=False,
            ),
        ),
        "BREAKOUT",
        "READY",
        "BLOCKED",
        ("GATE_BLOCKED",),
    ),
    ProductionScenario(
        "choppy score oscillation",
        "GCHP",
        (
            SourceDay(date(2026, 7, 27), setup_score=7.4, close=99),
            SourceDay(date(2026, 7, 28), setup_score=7.6, close=99),
            SourceDay(date(2026, 7, 29), setup_score=7.4, close=99),
            SourceDay(date(2026, 7, 30), setup_score=7.6, close=99),
        ),
        "BREAKOUT",
        "READY",
        "ACTIONABLE",
        ("NEW_READY",),
    ),
    ProductionScenario(
        "missing optional context",
        "GMOC",
        (
            SourceDay(
                date(2026, 7, 27),
                setup_score=7.8,
                close=99,
                include_optional_context=False,
            ),
        ),
        "BREAKOUT",
        "READY",
        "LOW_CONFIDENCE",
    ),
    ProductionScenario(
        "stale data",
        "GSTL",
        (
            SourceDay(
                date(2026, 7, 20),
                setup_score=7.8,
                close=99,
                processed_as_of=date(2026, 8, 5),
            ),
        ),
        "BREAKOUT",
        "READY",
        "BLOCKED",
    ),
    ProductionScenario(
        "stale-to-fresh",
        "GSTF",
        (
            SourceDay(
                date(2026, 7, 20),
                setup_score=7.8,
                close=99,
                processed_as_of=date(2026, 8, 5),
            ),
            SourceDay(date(2026, 8, 6), setup_score=7.8, close=99),
        ),
        "BREAKOUT",
        "READY",
        "ACTIONABLE",
    ),
    ProductionScenario(
        "fresh-to-stale",
        "GFTS",
        (
            SourceDay(date(2026, 7, 20), setup_score=7.8, close=99),
            SourceDay(
                date(2026, 7, 21),
                setup_score=7.8,
                close=99,
                processed_as_of=date(2026, 8, 5),
            ),
            SourceDay(
                date(2026, 7, 22),
                setup_score=7.8,
                close=99,
                processed_as_of=date(2026, 8, 6),
            ),
        ),
        "BREAKOUT",
        "READY",
        "BLOCKED",
        ("DATA_DEGRADED",),
    ),
    ProductionScenario(
        "earnings block",
        "GERN",
        (
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=99),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                close=99,
                earnings_risk="IMMINENT",
            ),
        ),
        "BREAKOUT",
        "READY",
        "BLOCKED",
        ("GATE_BLOCKED",),
    ),
    ProductionScenario(
        "liquidity block",
        "GLIQ",
        (
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=99),
            SourceDay(
                date(2026, 7, 28),
                setup_score=7.8,
                close=99,
                liquidity_score=4.0,
            ),
        ),
        "BREAKOUT",
        "READY",
        "BLOCKED",
        ("GATE_BLOCKED",),
    ),
    ProductionScenario(
        "sector acceleration",
        "GSEC",
        (
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=99, sector_rank=9),
            SourceDay(date(2026, 7, 28), setup_score=7.8, close=99, sector_rank=5),
        ),
        "BREAKOUT",
        "READY",
        "ACTIONABLE",
        ("SECTOR_ACCELERATION",),
    ),
    ProductionScenario(
        "score acceleration",
        "GSCR",
        (
            SourceDay(date(2026, 7, 27), setup_score=6.0, technical_score=6.5),
            SourceDay(date(2026, 7, 28), setup_score=6.0, technical_score=6.6),
            SourceDay(date(2026, 7, 29), setup_score=6.0, technical_score=6.8),
            SourceDay(date(2026, 7, 30), setup_score=6.0, technical_score=7.2),
        ),
        "BREAKOUT",
        "DEVELOPING",
        "WATCH_ONLY",
        ("SCORE_ACCELERATION",),
    ),
    ProductionScenario(
        "direct initial READY",
        "GDIR",
        (SourceDay(date(2026, 7, 27), setup_score=7.8, close=99),),
        "BREAKOUT",
        "READY",
        "ACTIONABLE",
    ),
    ProductionScenario(
        "direct initial TRIGGERED",
        "GDIT",
        (SourceDay(date(2026, 7, 27), setup_score=7.8, close=101),),
        "BREAKOUT",
        "TRIGGERED",
        "ACTIONABLE",
    ),
)

SPECIAL_GOLDEN_SCENARIOS = (
    "missing required data",
    "one filtered absence",
    "prolonged absence/expiry",
    "same-day revision",
    "retry/idempotency",
    "canonical revision",
)


def test_versioned_golden_source_sequences_run_through_the_production_stack(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        for scenario in PRODUCTION_SCENARIOS:
            for day in scenario.days:
                source_run_id = _seed_source_day(db, scenario.ticker, day)
                result = SetupLifecycleEvaluationService().evaluate_run(
                    db,
                    source_run_id,
                    requester=GOLDEN_FIXTURE_VERSION,
                )
                db.commit()
                assert result.failed == 0, (scenario.name, day.as_of, result.errors_by_ticker)
                _assert_every_layer(db, scenario, day, source_run_id)

            episode = db.scalar(
                select(SetupLifecycleEpisode)
                .where(SetupLifecycleEpisode.ticker == scenario.ticker)
                .order_by(SetupLifecycleEpisode.id.desc())
                .limit(1)
            )
            assert episode is not None
            assert episode.setup_family == scenario.expected_family
            assert episode.current_state == scenario.expected_state, (
                scenario.name,
                episode.state_age_sessions,
                [
                    (
                        item.data_as_of_date,
                        item.lifecycle_state_candidate,
                        item.primary_phase,
                        ((item.signals_json or {}).get("close_trigger_cross") or {}).get("value"),
                    )
                    for item in db.scalars(
                        select(SetupSignalSnapshot)
                        .where(SetupSignalSnapshot.ticker == scenario.ticker)
                        .order_by(SetupSignalSnapshot.data_as_of_date)
                    )
                ],
                [
                    (
                        event.effective_date,
                        event.from_state,
                        event.to_state,
                        (event.evidence_json or {}).get("follow_through_sessions"),
                        (event.evidence_json or {}).get("close_trigger_cross"),
                        event.reason_codes_json,
                    )
                    for event in db.scalars(
                        select(SetupLifecycleEvent)
                        .where(SetupLifecycleEvent.ticker == scenario.ticker)
                        .where(SetupLifecycleEvent.event_type != "CANONICAL_SELECTION")
                        .order_by(SetupLifecycleEvent.effective_date, SetupLifecycleEvent.id)
                    )
                ],
            )
            assert episode.current_actionability == scenario.expected_actionability, (
                scenario.name,
                episode.confidence_score,
                episode.metadata_json,
            )
            alert_types = set(
                db.scalars(
                    select(SignalAlertRule.rule_id)
                    .join(
                        SignalAlertEvent,
                        SignalAlertEvent.alert_rule_id == SignalAlertRule.id,
                    )
                    .where(SignalAlertEvent.ticker == scenario.ticker)
                )
            )
            assert set(scenario.expected_alert_types) <= alert_types
            _assert_scenario_specific(db, scenario)
    engine.dispose()


def test_production_golden_catalog_has_unique_versioned_scenarios() -> None:
    names = [scenario.name for scenario in PRODUCTION_SCENARIOS]
    names.extend(SPECIAL_GOLDEN_SCENARIOS)
    tickers = [scenario.ticker for scenario in PRODUCTION_SCENARIOS]

    assert GOLDEN_FIXTURE_VERSION == "slse-golden-1.0.0"
    assert len(names) == 25
    assert len(names) == len(set(names))
    assert len(tickers) == len(set(tickers))


def test_special_golden_sequences_cover_absence_revisions_and_retry(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    evaluator = SetupLifecycleEvaluationService()
    with Session(engine) as db:
        missing_cases = (
            (
                "GMRT",
                SourceDay(
                    date(2026, 7, 27),
                    technical_score=None,
                    setup_score=6.0,
                    classification="Breakout Base",
                ),
                "MISSING_REQUIRED_TECHNICAL_SCORE",
            ),
            (
                "GMRS",
                SourceDay(date(2026, 7, 27), setup_score=None),
                "MISSING_REQUIRED_SETUP_SCORE",
            ),
            (
                "GMRC",
                SourceDay(date(2026, 7, 27), classification=None),
                "MISSING_REQUIRED_CLASSIFICATION",
            ),
            (
                "GMRP",
                SourceDay(date(2026, 7, 27), close=None, add_bar=False),
                "MISSING_REQUIRED_CLOSE_PRICE",
            ),
        )
        for ticker, day, warning in missing_cases:
            run_id = _seed_source_day(db, ticker, day)
            result = evaluator.evaluate_run(db, run_id, requester=GOLDEN_FIXTURE_VERSION)
            db.commit()
            assert result.failed == 0
            snapshot = db.scalar(
                select(SetupSignalSnapshot).where(SetupSignalSnapshot.run_id == run_id)
            )
            assert snapshot is not None
            assert float(snapshot.required_feature_coverage) == 0.75
            assert warning in snapshot.warning_flags_json
            assert snapshot.actionability_candidate != "ACTIONABLE"
            assert snapshot.confidence_score < 70

        absence_run = _seed_source_day(
            db,
            "GABS",
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=99),
        )
        evaluator.evaluate_run(db, absence_run, requester=GOLDEN_FIXTURE_VERSION)
        db.commit()
        one_gap = SetupLifecycleMaintenanceService().daily_maintenance(
            db,
            as_of_date=date(2026, 7, 28),
        )
        db.commit()
        absence_episode = db.scalar(
            select(SetupLifecycleEpisode).where(SetupLifecycleEpisode.ticker == "GABS")
        )
        assert one_gap.aged >= 1
        assert one_gap.expired == 0
        assert absence_episode is not None
        assert absence_episode.status == "ACTIVE"
        assert absence_episode.missing_observation_sessions == 1

        expiry_run = _seed_source_day(
            db,
            "GEXP",
            SourceDay(date(2026, 7, 27), setup_score=7.8, close=99),
        )
        evaluator.evaluate_run(db, expiry_run, requester=GOLDEN_FIXTURE_VERSION)
        db.commit()
        maintenance = SetupLifecycleMaintenanceService()
        for gap_date in (
            date(2026, 7, 28),
            date(2026, 7, 29),
            date(2026, 7, 30),
            date(2026, 7, 31),
        ):
            expiry_result = maintenance.daily_maintenance(db, as_of_date=gap_date)
            db.commit()
        repeated = maintenance.daily_maintenance(db, as_of_date=date(2026, 8, 3))
        db.commit()
        expiry_episode = db.scalar(
            select(SetupLifecycleEpisode).where(SetupLifecycleEpisode.ticker == "GEXP")
        )
        expiry_events = list(
            db.scalars(
                select(SetupLifecycleEvent).where(
                    SetupLifecycleEvent.ticker == "GEXP",
                    SetupLifecycleEvent.to_state == "EXPIRED",
                )
            )
        )
        assert expiry_result.expired >= 1
        assert repeated.expired == 0
        assert expiry_episode is not None
        assert expiry_episode.status == "CLOSED"
        assert expiry_episode.current_state == "EXPIRED"
        assert len(expiry_events) == 1

        first_revision_run = _seed_source_day(
            db,
            "GREV",
            SourceDay(date(2026, 8, 3), setup_score=6.0, technical_score=6.5),
        )
        evaluator.evaluate_run(db, first_revision_run, requester=GOLDEN_FIXTURE_VERSION)
        second_revision_run = _seed_source_day(
            db,
            "GREV",
            SourceDay(
                date(2026, 8, 3),
                setup_score=7.8,
                technical_score=8.0,
                close=99,
                add_bar=False,
            ),
        )
        evaluator.evaluate_run(db, second_revision_run, requester=GOLDEN_FIXTURE_VERSION)
        db.commit()
        revisions = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.ticker == "GREV")
                .order_by(SetupSignalSnapshot.id)
            )
        )
        assert len(revisions) == 2
        assert len({row.source_data_hash for row in revisions}) == 2
        assert sum(row.is_canonical for row in revisions) == 1
        assert revisions[0].superseded_by_snapshot_id == revisions[1].id
        canonical_audits = list(
            db.scalars(
                select(SetupLifecycleEvent).where(
                    SetupLifecycleEvent.ticker == "GREV",
                    SetupLifecycleEvent.event_type == "CANONICAL_REVISION",
                )
            )
        )
        assert len(canonical_audits) == 2
        assert (
            sum(
                (row.evidence_json or {}).get("previous_snapshot_id") is not None
                for row in canonical_audits
            )
            == 1
        )

        retry_run = _seed_source_day(
            db,
            "GRTY",
            SourceDay(date(2026, 8, 3), setup_score=7.8, close=99),
        )
        evaluator.evaluate_run(db, retry_run, requester=GOLDEN_FIXTURE_VERSION)
        db.commit()
        before_retry = _domain_counts(db, "GRTY")
        evaluator.evaluate_run(db, retry_run, requester=GOLDEN_FIXTURE_VERSION)
        db.commit()
        assert _domain_counts(db, "GRTY") == before_retry
        assert (
            db.scalar(
                select(func.count())
                .select_from(SetupLifecycleEvaluationRun)
                .where(SetupLifecycleEvaluationRun.source_run_id == retry_run)
            )
            == 2
        )

        canonical_first = _seed_source_day(
            db,
            "GCAN",
            SourceDay(date(2026, 8, 3), setup_score=6.0, technical_score=6.5),
        )
        evaluator.evaluate_run(db, canonical_first, requester=GOLDEN_FIXTURE_VERSION)
        canonical_revision = _seed_source_day(
            db,
            "GCAN",
            SourceDay(
                date(2026, 8, 3),
                setup_score=6.8,
                technical_score=7.0,
                add_bar=False,
            ),
        )
        evaluator.evaluate_run(db, canonical_revision, requester=GOLDEN_FIXTURE_VERSION)
        selected_revision = db.scalar(
            select(SetupSignalSnapshot).where(
                SetupSignalSnapshot.run_id == canonical_revision,
                SetupSignalSnapshot.is_canonical.is_(True),
            )
        )
        revised_episode = db.scalar(
            select(SetupLifecycleEpisode).where(SetupLifecycleEpisode.ticker == "GCAN")
        )
        assert revised_episode is not None
        assert revised_episode.state_age_sessions == 0
        next_run = _seed_source_day(
            db,
            "GCAN",
            SourceDay(date(2026, 8, 4), setup_score=7.8, technical_score=7.8, close=99),
        )
        evaluator.evaluate_run(db, next_run, requester=GOLDEN_FIXTURE_VERSION)
        db.commit()
        next_snapshot = db.scalar(
            select(SetupSignalSnapshot).where(SetupSignalSnapshot.run_id == next_run)
        )
        next_changes = list(
            db.scalars(
                select(SignalChangeEvent).where(
                    SignalChangeEvent.current_snapshot_id == next_snapshot.id
                )
            )
        )
        assert selected_revision is not None
        assert next_snapshot is not None
        assert next_changes
        assert {row.previous_snapshot_id for row in next_changes} == {selected_revision.id}
    engine.dispose()


def _upgrade(database_url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _domain_counts(db: Session, ticker: str) -> tuple[int, int, int, int]:
    return (
        db.scalar(
            select(func.count())
            .select_from(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == ticker)
        ),
        db.scalar(
            select(func.count())
            .select_from(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.ticker == ticker)
        ),
        db.scalar(
            select(func.count())
            .select_from(SignalChangeEvent)
            .where(SignalChangeEvent.ticker == ticker)
        ),
        db.scalar(
            select(func.count())
            .select_from(SignalAlertEvent)
            .where(SignalAlertEvent.ticker == ticker)
        ),
    )


def _assert_scenario_specific(db: Session, scenario: ProductionScenario) -> None:
    snapshots = list(
        db.scalars(
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == scenario.ticker)
            .order_by(SetupSignalSnapshot.data_as_of_date, SetupSignalSnapshot.id)
        )
    )
    lifecycle_events = list(
        db.scalars(
            select(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.ticker == scenario.ticker)
            .where(
                SetupLifecycleEvent.event_type.in_(
                    (
                        "EPISODE_OPENED",
                        "STATE_TRANSITION",
                        "PHASE_TRANSITION",
                        "ACTIONABILITY_CHANGE",
                    )
                )
            )
            .order_by(SetupLifecycleEvent.effective_date, SetupLifecycleEvent.id)
        )
    )
    alert_rule_ids = list(
        db.scalars(
            select(SignalAlertRule.rule_id)
            .join(SignalAlertEvent, SignalAlertEvent.alert_rule_id == SignalAlertRule.id)
            .where(SignalAlertEvent.ticker == scenario.ticker)
        )
    )
    for rule_id in scenario.expected_alert_types:
        assert alert_rule_ids.count(rule_id) == 1, (scenario.name, alert_rule_ids)

    if scenario.name in {"failed breakout", "deteriorating pullback"}:
        assert lifecycle_events[-1].to_state == "FAILED"
        assert not ({"NEW_READY", "NEW_TRIGGER", "NEW_CONFIRMATION"} & set(alert_rule_ids))
    elif scenario.name == "choppy score oscillation":
        state_transitions = [
            event for event in lifecycle_events if event.from_state != event.to_state
        ]
        assert [event.to_state for event in state_transitions].count("READY") == 1
        assert "TIGHTENING" not in [
            event.to_state for event in state_transitions if event.from_state == "READY"
        ]
    elif scenario.name == "missing optional context":
        assert float(snapshots[-1].required_feature_coverage) == 1.0
        assert "MISSING_OPTIONAL_CONTEXT" in snapshots[-1].warning_flags_json
        assert snapshots[-1].data_quality_label == "NORMAL"
    elif scenario.name == "stale data":
        assert snapshots[-1].freshness_status == "STALE"
        assert "STALE_PRICE_BAR" in snapshots[-1].warning_flags_json
        assert snapshots[-1].data_quality_label == "INSUFFICIENT"
    elif scenario.name == "stale-to-fresh":
        assert [row.freshness_status for row in snapshots] == ["STALE", "FRESH"]
        assert "DATA_DEGRADED" not in alert_rule_ids
    elif scenario.name == "fresh-to-stale":
        assert [row.freshness_status for row in snapshots] == ["FRESH", "STALE", "STALE"]
    elif scenario.name in {"market gate block", "earnings block", "liquidity block"}:
        assert {row.lifecycle_state_candidate for row in snapshots} == {"READY"}
        gate_alert = db.scalar(
            select(SignalAlertEvent)
            .join(SignalAlertRule, SignalAlertEvent.alert_rule_id == SignalAlertRule.id)
            .where(
                SignalAlertEvent.ticker == scenario.ticker,
                SignalAlertRule.rule_id == "GATE_BLOCKED",
            )
        )
        assert gate_alert is not None
        blockers = set((gate_alert.evidence_json or {}).get("blockers") or ())
        expected_blocker = {
            "market gate block": "MARKET_POLICY_BLOCK",
            "earnings block": "IMMINENT_EARNINGS",
            "liquidity block": "LIQUIDITY_RISK",
        }[scenario.name]
        assert expected_blocker in blockers
    elif scenario.name == "sector acceleration":
        event = db.scalar(
            select(SignalChangeEvent).where(
                SignalChangeEvent.ticker == scenario.ticker,
                SignalChangeEvent.signal_key == "sector_rank",
            )
        )
        assert event is not None
        assert float(event.normalized_delta) == 4.0
        assert (event.evidence_json or {}).get("sector_confidence") == "HIGH"
    elif scenario.name == "score acceleration":
        event = db.scalar(
            select(SignalChangeEvent)
            .where(
                SignalChangeEvent.ticker == scenario.ticker,
                SignalChangeEvent.signal_key == "technical_score",
            )
            .order_by(SignalChangeEvent.effective_date.desc())
        )
        assert event is not None
        velocity = ((event.evidence_json or {}).get("velocity") or {}).get("3") or {}
        assert float(velocity["old_value"]) == 6.5
        assert float(velocity["new_value"]) == 7.2
        assert float(velocity["normalized_delta"]) == 0.7
    elif scenario.name == "direct initial READY":
        assert lifecycle_events[0].event_type == "EPISODE_OPENED"
        assert lifecycle_events[0].to_state == "READY"
        assert "SKIPPED_PRIOR_PROGRESSION" in lifecycle_events[0].reason_codes_json
        assert "NEW_READY" not in alert_rule_ids
    elif scenario.name == "direct initial TRIGGERED":
        assert lifecycle_events[0].event_type == "EPISODE_OPENED"
        assert lifecycle_events[0].to_state == "TRIGGERED"
        assert "SKIPPED_PRIOR_PROGRESSION" in lifecycle_events[0].reason_codes_json
        assert "NEW_TRIGGER" not in alert_rule_ids


def _seed_source_day(db: Session, ticker: str, spec: SourceDay) -> int:
    processed_date = spec.processed_as_of or spec.as_of
    processed_at = datetime.combine(processed_date, datetime.min.time(), tzinfo=UTC).replace(
        hour=21
    )
    run = UploadRun(
        filename=f"{GOLDEN_FIXTURE_VERSION}-{ticker}-{spec.as_of}.csv",
        status="COMPLETED",
        row_count=1,
        uploaded_at=processed_at,
        processed_at=processed_at,
        notes=GOLDEN_FIXTURE_VERSION,
    )
    db.add(run)
    db.flush()
    raw = RawCompanyRow(
        run_id=run.id,
        row_number=1,
        ticker=ticker,
        company_name=f"Golden {ticker}",
        sector="Technology",
        sector_canonical="Technology",
        raw_json={
            "pivot_price": spec.pivot,
            "trigger_price": spec.pivot,
            "earnings_risk_level": spec.earnings_risk,
            "fixture_version": GOLDEN_FIXTURE_VERSION,
        },
    )
    db.add(raw)
    db.add(
        FundamentalScore(
            run_id=run.id,
            ticker=ticker,
            fundamental_score=Decimal("8.5"),
            liquidity_risk_score=Decimal(str(spec.liquidity_score)),
        )
    )
    if spec.technical_score is not None or spec.classification is not None:
        derived = {
            "atr": 2.0,
            "atr_pct": 2.0,
            "volume_ratio": 1.1,
            "volume_dry_up": spec.volume_percentile <= 35,
            "red_vol_declining": False,
            "held_near_support": False,
            "pullback_depth_pct": 8.0,
            "failed_breakout": False,
            "heavy_mid_ma_break": False,
            "fresh_breakout": False,
            **spec.derived,
        }
        db.add(
            TechnicalScore(
                run_id=run.id,
                ticker=ticker,
                dual_score=(
                    Decimal(str(spec.technical_score)) if spec.technical_score is not None else None
                ),
                trend_score=Decimal(str(spec.trend_score)),
                momentum_score=Decimal("7.0"),
                setup_score=(
                    Decimal(str(spec.setup_score)) if spec.setup_score is not None else None
                ),
                risk_score=Decimal("2.0"),
                relative_strength_score=Decimal(str(spec.relative_strength)),
                leadership_score=Decimal(str(spec.leadership_score)),
                classification=spec.classification,
                stage="GOLDEN",
                technical_confidence="HIGH",
                data_quality_score=Decimal("9.0"),
                vcp_score=Decimal("7.8") if spec.classification == "VCP" else None,
                box_tightness_score=Decimal("7.5") if spec.contraction else None,
                atr_percentile_252=Decimal("25"),
                volume_percentile_252=Decimal(str(spec.volume_percentile)),
                range_percentile_252=Decimal(str(spec.range_percentile)),
                extension_percentile_252=Decimal("30"),
                feature_flags_json=[],
                warning_flags_json=[],
                v4_debug_json={
                    "contraction": {"range_contraction": spec.contraction},
                    "box": {"box_failure": spec.box_failure},
                },
                debug_json={"derived": derived},
                created_at=processed_at,
            )
        )
    db.add(
        CombinedResult(
            run_id=run.id,
            ticker=ticker,
            company_name=f"Golden {ticker}",
            sector="Technology",
            final_score=Decimal("90"),
            fundamental_score=Decimal("8.5"),
            technical_classification=spec.classification,
            dual_score=(
                Decimal(str(spec.technical_score)) if spec.technical_score is not None else None
            ),
            combined_decision="WATCH",
            earnings_risk_level=spec.earnings_risk,
            is_complete=True,
            has_fundamental=True,
            has_technical=spec.technical_score is not None,
        )
    )
    if spec.include_optional_context:
        market = MarketRegimeSnapshot(
            run_id=run.id,
            as_of_date=spec.as_of,
            calculation_version=GOLDEN_FIXTURE_VERSION,
            config_version=GOLDEN_FIXTURE_VERSION,
            regime=spec.market_regime,
            risk_state="NORMAL" if spec.market_gate else "RISK_OFF",
            score=80.0 if spec.market_gate else 20.0,
            risk_off=not spec.market_gate,
            gate_ok=spec.market_gate,
            confidence="HIGH",
            action_summary="Golden fixture",
            evidence_hash=f"market-{run.id}",
        )
        db.add(market)
        db.flush()
        sector = SectorRotationSnapshot(
            run_id=run.id,
            market_regime_snapshot_id=market.id,
            as_of_date=spec.as_of,
            calculation_version=GOLDEN_FIXTURE_VERSION,
            config_version=GOLDEN_FIXTURE_VERSION,
            config_hash=f"sector-{run.id}",
            mode="LIVE",
            sector_count=1,
            ticker_count=1,
            evidence_hash=f"sector-evidence-{run.id}",
        )
        db.add(sector)
        db.flush()
        db.add(
            SectorRotationRow(
                snapshot_id=sector.id,
                sector="Technology",
                sector_slug="technology",
                ticker_count=1,
                rotation_state="LEADING",
                sector_permission="ALLOW",
                confidence="HIGH",
                current_rank=spec.sector_rank,
            )
        )
    if spec.add_bar and spec.close is not None:
        db.add(
            PriceBar(
                ticker=ticker,
                bar_date=spec.as_of,
                timeframe="1 day",
                open=Decimal(str(spec.close - 1)),
                high=Decimal(str(spec.close + 1)),
                low=Decimal(str(spec.close - 2)),
                close=Decimal(str(spec.close)),
                volume=Decimal("1000000"),
                source="GOLDEN",
                what_to_show="TRADES",
                data_hash=f"{GOLDEN_FIXTURE_VERSION}:{ticker}:{spec.as_of}:{spec.close}",
            )
        )
    db.commit()
    return run.id


def _assert_every_layer(
    db: Session,
    scenario: ProductionScenario,
    day: SourceDay,
    source_run_id: int,
) -> None:
    source_count = db.scalar(
        select(func.count()).select_from(RawCompanyRow).where(RawCompanyRow.run_id == source_run_id)
    )
    assert source_count == 1
    snapshot = db.scalar(
        select(SetupSignalSnapshot)
        .where(SetupSignalSnapshot.run_id == source_run_id)
        .where(SetupSignalSnapshot.ticker == scenario.ticker)
        .where(SetupSignalSnapshot.is_canonical.is_(True))
    )
    assert snapshot is not None
    assert snapshot.engine_version == "slse-1.3.0"
    assert snapshot.config_version == "2026-08-14-velocity-trigger-distance"
    assert snapshot.config_hash
    assert snapshot.source_data_hash
    assert snapshot.confidence_score is not None
    assert snapshot.actionability_candidate is not None
    trigger_reference = (snapshot.debug_json or {}).get("trigger_reference") or {}
    if day.add_bar and day.close is not None:
        expected_distance = (
            (Decimal(str(day.pivot)) - Decimal(str(day.close)))
            / Decimal(str(day.pivot))
            * Decimal("100")
        ).quantize(Decimal("0.000001"))
        assert snapshot.trigger_price == Decimal(str(day.pivot))
        assert snapshot.distance_to_pivot_pct == expected_distance
        assert trigger_reference["reference_price"] == str(day.pivot)
        expected_source_path = (
            "raw_company_rows.raw_json.pivot_price"
            if scenario.expected_family in {"BREAKOUT", "VCP"}
            else "raw_company_rows.raw_json.trigger_price"
        )
        assert trigger_reference["source_path"] == expected_source_path
        assert trigger_reference["missing_reason"] is None
    else:
        assert snapshot.distance_to_pivot_pct is None
    assert snapshot.distance_to_pivot_pct not in {Decimal("999"), Decimal("-999")}
    lifecycle_events = list(
        db.scalars(
            select(SetupLifecycleEvent).where(SetupLifecycleEvent.snapshot_id == snapshot.id)
        )
    )
    signal_changes = list(
        db.scalars(
            select(SignalChangeEvent).where(SignalChangeEvent.current_snapshot_id == snapshot.id)
        )
    )
    assert lifecycle_events or signal_changes
    query = SetupLifecycleQueryService()
    market = query.changes(
        db,
        SetupLifecycleListQuery(
            filters=SetupLifecycleFilters(
                ticker=scenario.ticker,
                as_of_date=day.as_of,
            ),
            limit=500,
        ),
    )
    if market["total"] == 0:
        market = query.changes(
            db,
            SetupLifecycleListQuery(
                filters=SetupLifecycleFilters(
                    ticker=scenario.ticker,
                    as_of_date=day.as_of,
                    transition="NO_MATERIAL_CHANGE",
                ),
                limit=500,
            ),
        )
    assert market["total"] > 0
    if day.add_bar and day.close is not None:
        assert {
            item["trigger_distance_pct"]
            for item in market["items"]
            if item.get("snapshot_id") == snapshot.id
        } == {float(expected_distance)}
    assert scenario.ticker in export_changes_csv(market)
    alerts = query.alerts(
        db,
        SetupLifecycleListQuery(
            filters=SetupLifecycleFilters(
                ticker=scenario.ticker,
                as_of_date=day.as_of,
            ),
            limit=500,
        ),
    )
    if alerts["total"]:
        assert scenario.ticker in export_alerts_csv(alerts)
