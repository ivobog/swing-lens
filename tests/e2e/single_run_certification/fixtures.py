from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCompany, CeriProcessingRun
from app.models.tables import (
    IBContract,
    PriceBar,
    UploadRun,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerTargetStopOutcome,
)
from app.services.ceri.enums import CeriDataset
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
)
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.processing_run_service import CeriProcessingRunService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider
from app.services.winner_probability.config import load_winner_probability_config

FIXTURE_VERSION = "single-run-certification-1.0.0"
CANONICAL_TICKERS = ("ALFA", "BRAV", "CHAR", "DELT", "ECHO", "FOXT", "GOLF", "RISK")
DECOY_TICKER = "DECOY_ONLY_CANARY"
BENCHMARKS = ("SPY", "QQQ")


@dataclass(frozen=True)
class SeedResult:
    decoy_run_id: int
    fixture_hash: str
    ceri_ingestion_run_ids: tuple[int, ...]
    ceri_processing_run_ids: tuple[int, ...]
    pre_run_counts: dict[str, int]


def write_canonical_csv(path: Path) -> str:
    headers = [
        "Symbol",
        "Description",
        "Sector",
        "Price",
        "Market capitalization",
        "Revenue growth %, Quarterly YoY",
        "Revenue growth %, TTM YoY",
        "Revenue growth %, 5 year CAGR",
        "Earnings per share diluted growth %, Quarterly YoY",
        "Earnings per share diluted growth %, TTM YoY",
        "Gross margin %, Trailing 12 months",
        "EBITDA margin %, Trailing 12 months",
        "Operating margin %, Trailing 12 months",
        "Net margin %, Trailing 12 months",
        "Return on equity %, Trailing 12 months",
        "Return on invested capital %, Trailing 12 months",
        "Free cash flow margin %, Trailing 12 months",
        "Free cash flow growth %, TTM YoY",
        "Net debt to EBITDA ratio, Trailing 12 months",
        "Current ratio, Quarterly",
        "Price to earnings ratio",
        "Forward non-GAAP price to earnings, Annual",
        "Price to free cash flow ratio",
        "Price x average volume, 30 days",
        "Upcoming earnings date",
    ]
    rows = [
        _csv_row(
            "ALFA",
            "Alpha Systems",
            "Technology Services",
            145,
            85_000_000_000,
            34,
            31,
            24,
            41,
            38,
            78,
            35,
            31,
            25,
            33,
            29,
            24,
            32,
            0.2,
            3.2,
            24,
            19,
            22,
            190_000_000,
            "2026-09-15",
        ),
        _csv_row(
            "BRAV",
            "Bravo Health",
            "Health Technology",
            92,
            24_000_000_000,
            18,
            17,
            15,
            21,
            19,
            64,
            24,
            20,
            16,
            20,
            18,
            15,
            18,
            0.8,
            2.4,
            31,
            25,
            28,
            80_000_000,
            "2026-08-12",
        ),
        _csv_row(
            "CHAR",
            "Charlie Retail",
            "Retail Trade",
            38,
            6_000_000_000,
            -4,
            -2,
            2,
            -8,
            -6,
            28,
            8,
            4,
            1,
            3,
            2,
            2,
            -10,
            4.5,
            0.9,
            55,
            44,
            49,
            12_000_000,
            "",
        ),
        _csv_row(
            "DELT",
            "Delta Energy",
            "Energy Minerals",
            71,
            14_000_000_000,
            9,
            7,
            4,
            6,
            5,
            49,
            27,
            23,
            18,
            17,
            15,
            16,
            7,
            0.5,
            2.0,
            13,
            11,
            12,
            70_000_000,
            "2026-10-01",
        ),
        _csv_row(
            "ECHO",
            "Echo Unknown",
            "Mystery Quantum",
            22,
            900_000_000,
            12,
            10,
            8,
            15,
            12,
            55,
            18,
            15,
            9,
            12,
            11,
            10,
            8,
            1.1,
            1.8,
            29,
            23,
            25,
            8_000_000,
            "",
        ),
        _csv_row(
            "FOXT",
            "Foxtrot Cloud",
            "Technology Services",
            118,
            42_000_000_000,
            29,
            27,
            22,
            35,
            32,
            74,
            32,
            29,
            22,
            30,
            26,
            21,
            28,
            0.1,
            3.8,
            28,
            21,
            24,
            160_000_000,
            "2026-09-03",
        ),
        _csv_row(
            "GOLF",
            "Golf Incomplete",
            "Producer Manufacturing",
            17,
            750_000_000,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            2_000_000,
            "",
        ),
        _csv_row(
            "RISK",
            "Risk Bio",
            "Health Technology",
            53,
            3_200_000_000,
            44,
            39,
            28,
            55,
            48,
            82,
            12,
            7,
            -3,
            -5,
            -2,
            -4,
            -12,
            7.2,
            0.7,
            88,
            76,
            82,
            20_000_000,
            "2026-08-10",
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csv_row(ticker: str, company: str, sector: str, *values: Any) -> dict[str, Any]:
    keys = [
        "Price",
        "Market capitalization",
        "Revenue growth %, Quarterly YoY",
        "Revenue growth %, TTM YoY",
        "Revenue growth %, 5 year CAGR",
        "Earnings per share diluted growth %, Quarterly YoY",
        "Earnings per share diluted growth %, TTM YoY",
        "Gross margin %, Trailing 12 months",
        "EBITDA margin %, Trailing 12 months",
        "Operating margin %, Trailing 12 months",
        "Net margin %, Trailing 12 months",
        "Return on equity %, Trailing 12 months",
        "Return on invested capital %, Trailing 12 months",
        "Free cash flow margin %, Trailing 12 months",
        "Free cash flow growth %, TTM YoY",
        "Net debt to EBITDA ratio, Trailing 12 months",
        "Current ratio, Quarterly",
        "Price to earnings ratio",
        "Forward non-GAAP price to earnings, Annual",
        "Price to free cash flow ratio",
        "Price x average volume, 30 days",
        "Upcoming earnings date",
    ]
    return {
        "Symbol": ticker,
        "Description": company,
        "Sector": sector,
        **dict(zip(keys, values, strict=True)),
    }


def seed_prerequisites(database_url: str) -> SeedResult:
    engine = create_engine(database_url)
    ceri_ingestion_ids: list[int] = []
    ceri_processing_ids: list[int] = []
    with Session(engine) as db:
        decoy = UploadRun(
            filename="decoy-run-never-mix.csv",
            status="COMPLETED",
            row_count=1,
            processed_at=datetime(2026, 7, 1, 20, 0, tzinfo=UTC),
            notes=f"isolation canary {DECOY_TICKER}",
        )
        db.add(decoy)
        db.flush()
        _seed_market_cache(db)
        _seed_winner_history(db, decoy.id)
        db.commit()
        ceri_ingestion_ids, ceri_processing_ids = _seed_ceri_manual_evidence(db)
        db.commit()
        counts = {
            "upload_runs": db.query(UploadRun).count(),
            "price_bars": db.query(PriceBar).count(),
            "ib_contracts": db.query(IBContract).count(),
            "winner_prediction_snapshots": db.query(WinnerPredictionSnapshot).count(),
            "ceri_companies": db.query(CeriCompany).count(),
        }
        result = SeedResult(
            decoy_run_id=decoy.id,
            fixture_hash=_fixture_hash(counts),
            ceri_ingestion_run_ids=tuple(ceri_ingestion_ids),
            ceri_processing_run_ids=tuple(ceri_processing_ids),
            pre_run_counts=counts,
        )
    engine.dispose()
    return result


def _seed_market_cache(db: Session) -> None:
    for index, ticker in enumerate((*CANONICAL_TICKERS, *BENCHMARKS), start=1):
        db.add(
            IBContract(
                ticker=ticker,
                ib_conid=900_000 + index,
                symbol=ticker,
                exchange="SMART",
                primary_exchange="NASDAQ" if index % 2 else "NYSE",
                currency="USD",
                sec_type="STK",
                local_symbol=ticker,
                trading_class="NMS",
                resolution_status="RESOLVED",
                last_resolved_at=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
            )
        )
        # FOXT deliberately needs deterministic IB completion; GOLF deliberately
        # remains technically insufficient after its small cache.
        count = 180 if ticker == "FOXT" else 90 if ticker == "GOLF" else 320
        for what_to_show in ("ADJUSTED_LAST", "TRADES"):
            for bar in _ohlcv(ticker, count):
                db.add(
                    PriceBar(
                        ticker=ticker,
                        bar_date=bar["date"],
                        timeframe="1 day",
                        open=bar["open"],
                        high=bar["high"],
                        low=bar["low"],
                        close=bar["close"],
                        volume=bar["volume"],
                        source="QA_SEED",
                        what_to_show=what_to_show,
                        adjustment_type="adjusted" if what_to_show == "ADJUSTED_LAST" else None,
                        data_hash=hashlib.sha256(
                            f"{ticker}:{what_to_show}:{bar['date']}".encode()
                        ).hexdigest(),
                    )
                )


def _ohlcv(ticker: str, count: int) -> list[dict[str, Any]]:
    seed = sum(ord(char) for char in ticker)
    end = date.today()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    dates: list[date] = []
    current = end
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    rows: list[dict[str, Any]] = []
    for index, bar_date in enumerate(reversed(dates)):
        slope = -0.04 if ticker in {"CHAR", "RISK"} else 0.06 + (seed % 7) * 0.01
        base = Decimal(str(35 + seed % 90)) + Decimal(str(index)) * Decimal(str(slope))
        rows.append(
            {
                "date": bar_date,
                "open": base - Decimal("0.30"),
                "high": base + Decimal("1.10"),
                "low": base - Decimal("1.00"),
                "close": base,
                "volume": Decimal(800_000 + seed * 100 + index * 500),
            }
        )
    return rows


def _seed_winner_history(db: Session, decoy_run_id: int) -> None:
    config = load_winner_probability_config()
    raw_definition = config.primary_outcome_definition
    definition = WinnerOutcomeDefinition(
        definition_id=raw_definition.id,
        label=raw_definition.label,
        entry_model=raw_definition.entry_model,
        horizon_sessions=raw_definition.horizon_sessions,
        target_pct=raw_definition.target_pct,
        stop_pct=raw_definition.stop_pct,
        same_bar_conflict_policy=raw_definition.same_bar_conflict_policy,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        is_primary=True,
        is_active=True,
        metadata_json={"fixture": FIXTURE_VERSION},
    )
    db.add(definition)
    db.flush()
    for index in range(20):
        ticker = f"HIST{index:02d}"
        captured = datetime(2026, 5, 1, 20, 0, tzinfo=UTC) + timedelta(days=index)
        prediction = WinnerPredictionSnapshot(
            run_id=decoy_run_id,
            ticker=ticker,
            prediction_as_of_date=captured.date(),
            source_data_cutoff_at=captured,
            captured_at=captured,
            planned_entry_session=captured.date() + timedelta(days=1),
            entry_schedule_status="RESOLVED",
            entry_data_status="AVAILABLE",
            eligibility_status="ELIGIBLE",
            setup_family="Breakout",
            setup_classification="Breakout Base",
            ranking_profile="momentum_swing",
            fundamental_score=Decimal("8.2"),
            technical_score=Decimal("8.4"),
            combined_score=Decimal("8.3"),
            market_regime="Confirmed Uptrend",
            market_risk_state="Green",
            sector_state="Leading",
            sector_rank=1,
            technical_data_quality="ok",
            fundamental_coverage=Decimal("0.95"),
            feature_schema_version=config.feature_schema.version,
            feature_vector_hash=hashlib.sha256(ticker.encode()).hexdigest(),
            config_hash=config.config_hash,
            calculation_version=config.engine.calculation_version,
            feature_json={
                "setup_family": "Breakout",
                "dual_score_band": "8_plus",
                "score_band": "8_plus",
                "market_risk_state": "Green",
                "sector_state": "Leading",
                "ranking_profile": "momentum_swing",
                "sector_leadership_bucket": "leader",
                "market_regime_family": "Confirmed Uptrend",
                "global": "global",
            },
            source_ids_json={},
            warning_flags_json=[],
            lineage_json={"fixture": FIXTURE_VERSION},
            retention_class="permanent",
        )
        db.add(prediction)
        db.flush()
        won = index % 3 != 0
        forward = WinnerForwardOutcome(
            prediction_id=prediction.id,
            entry_model="NEXT_OPEN",
            horizon_sessions=5,
            entry_session=prediction.planned_entry_session,
            due_session=prediction.planned_entry_session + timedelta(days=8),
            status="MATURED",
            revision=1,
            is_current_revision=True,
            entry_price=Decimal("100"),
            close_return_pct=Decimal("4.0") if won else Decimal("-2.5"),
            mfe_pct=Decimal("5.0") if won else Decimal("1.0"),
            mae_pct=Decimal("-1.0") if won else Decimal("-3.0"),
            matured_at=captured + timedelta(days=10),
            metadata_json={"fixture": FIXTURE_VERSION},
        )
        db.add(forward)
        db.flush()
        db.add(
            WinnerTargetStopOutcome(
                prediction_id=prediction.id,
                outcome_definition_id=definition.id,
                forward_outcome_id=forward.id,
                entry_model="NEXT_OPEN",
                horizon_sessions=5,
                status="MATURED",
                revision=1,
                is_current_revision=True,
                target_pct=Decimal("2.5"),
                stop_pct=Decimal("2.0"),
                target_hit=won,
                stop_hit=not won,
                first_event="TARGET_FIRST" if won else "STOP_FIRST",
                event_session=prediction.planned_entry_session + timedelta(days=3),
                same_bar_conflict=False,
                primary_winner=won,
                optimistic_winner=won,
                conservative_winner=won,
                evaluated_at=captured + timedelta(days=10),
                metadata_json={"fixture": FIXTURE_VERSION},
            )
        )


def _seed_ceri_manual_evidence(db: Session) -> tuple[list[int], list[int]]:
    for ticker in CANONICAL_TICKERS:
        db.add(
            CeriCompany(
                ticker=ticker,
                exchange="NASDAQ",
                company_name=f"{ticker} Certification Company",
                current_provider_ids_json={"manual": f"manual-{ticker.lower()}"},
            )
        )
    db.flush()

    records: dict[CeriDataset, list[dict[str, Any]]] = {dataset: [] for dataset in CeriDataset}
    for ticker_index, ticker in enumerate(CANONICAL_TICKERS):
        direction = -1 if ticker == "RISK" else 1
        base = Decimal("1.00") + Decimal(ticker_index) / Decimal("10")
        for observation, observed_at, multiplier in (
            ("baseline", "2026-06-15T20:15:00Z", Decimal("1.0")),
            (
                "current",
                "2026-08-07T20:15:00Z",
                Decimal("1.25") if direction > 0 else Decimal("0.70"),
            ),
        ):
            records[CeriDataset.ESTIMATES].append(
                {
                    "provider_record_id": f"{ticker}-eps-{observation}",
                    "provider_company_id": f"manual-{ticker.lower()}",
                    "ticker": ticker,
                    "exchange": "NASDAQ",
                    "metric": "EPS_DILUTED",
                    "period_type": "CURRENT_FISCAL_YEAR",
                    "fiscal_period_end": "2026-12-31",
                    "consensus": str(base * multiplier),
                    "high": str(base * multiplier * Decimal("1.10")),
                    "low": str(base * multiplier * Decimal("0.90")),
                    "analyst_count": 12 + ticker_index,
                    "upward_count": 9 if direction > 0 else 1,
                    "downward_count": 1 if direction > 0 else 8,
                    "currency": "USD",
                    "published_at": observed_at,
                    "observed_at": observed_at,
                    "export_policy": "exportable",
                }
            )
        records[CeriDataset.GUIDANCE].append(
            {
                "provider_record_id": f"{ticker}-guidance-1",
                "ticker": ticker,
                "action": "RAISED" if direction > 0 else "LOWERED",
                "metric": "REVENUE",
                "period_type": "CURRENT_FISCAL_YEAR",
                "point": str(100 + ticker_index * 10),
                "currency": "USD",
                "confidence": "high" if ticker != "GOLF" else "low",
                "announced_at": "2026-08-06T20:15:00Z",
                "published_at": "2026-08-06T20:15:00Z",
            }
        )
        records[CeriDataset.CATALYSTS].append(
            {
                "provider_record_id": f"{ticker}-catalyst-1",
                "ticker": ticker,
                "category": "REGULATORY" if ticker == "RISK" else "PRODUCT",
                "subtype": "binary-review" if ticker == "RISK" else "launch",
                "subject_key": f"{ticker.lower()}-event",
                "canonical_text": "Deterministic risk review"
                if ticker == "RISK"
                else "Deterministic product launch",
                "status": "ANNOUNCED",
                "direction": "STRONG_NEGATIVE" if ticker == "RISK" else "POSITIVE",
                "materiality": 0.95 if ticker == "RISK" else 0.70,
                "announced_at": "2026-08-07T19:00:00Z",
                "expected_date": "2026-09-15",
                "date_confidence": "EXACT_DATE",
                "source_confidence": "HIGH",
                "published_at": "2026-08-07T19:00:00Z",
            }
        )

    provider = ManualCeriProvider(records, provider_terms_version=FIXTURE_VERSION)
    service = CeriIngestionService(registry=CeriProviderRegistry(providers={"manual": provider}))
    ingestion_ids: list[int] = []
    processing_ids: list[int] = []
    for dataset in (CeriDataset.ESTIMATES, CeriDataset.GUIDANCE, CeriDataset.CATALYSTS):
        for ticker in CANONICAL_TICKERS:
            result = service.ingest(
                db,
                CeriIngestionRequest(
                    provider="manual",
                    dataset=dataset,
                    ticker=ticker,
                    request_key=f"certification:{FIXTURE_VERSION}:{dataset.value}:{ticker}",
                ),
            )
            db.flush()
            if result.ingestion_run_id is not None:
                ingestion_ids.append(result.ingestion_run_id)
                processing = CeriProcessingRun(
                    job_type="CERI_NORMALIZE",
                    status="RUNNING",
                    deterministic_request_key=f"certification:normalize:{result.ingestion_run_id}",
                    scope_json={"ticker": ticker, "dataset": dataset.value},
                    started_at=datetime.now(UTC),
                )
                db.add(processing)
                db.flush()
                CeriNormalizationService().normalize(
                    db,
                    processing_run=processing,
                    ingestion_run_id=result.ingestion_run_id,
                )
                processing_ids.append(processing.id)
    db.flush()
    feature_result = CeriFeatureRebuildService().rebuild(
        db,
        CeriFeatureRebuildRequest(
            ticker=None,
            # Capture uses a UTC cutoff. Keep the feature session on that same
            # clock so the fixture stays valid around a local-midnight rollover.
            as_of_session=datetime.now(UTC).date(),
            mode="AS_KNOWN",
        ),
    )
    feature_run, _ = CeriProcessingRunService().create_or_get(
        db,
        job_type="CERI_REBUILD_FEATURES",
        request_key=f"certification:features:{FIXTURE_VERSION}",
        scope={"tickers": list(CANONICAL_TICKERS)},
    )
    CeriProcessingRunService().finish(
        db,
        feature_run,
        status="PARTIAL" if feature_result.failed else "COMPLETED",
        counts=feature_result.as_dict(),
    )
    processing_ids.append(feature_run.id)
    return ingestion_ids, processing_ids


def _fixture_hash(counts: dict[str, int]) -> str:
    payload = {
        "version": FIXTURE_VERSION,
        "tickers": CANONICAL_TICKERS,
        "decoy": DECOY_TICKER,
        "counts": counts,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
