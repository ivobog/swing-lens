from datetime import date
from decimal import Decimal

import pytest

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services import ranking_profile_service
from app.services.ranking_profile_service import (
    execute_ranking_pipeline_step,
    refresh_all_ranking_profiles,
    refresh_ranking_profile,
)

TODAY = date(2026, 7, 7)


def test_refresh_all_ranking_profiles_persists_enabled_profiles(monkeypatch) -> None:
    db = FakeDb(upload_runs={7: UploadRun(id=7, filename="sample.csv", status="COMPLETED")})
    _patch_run_inputs(monkeypatch)

    results = refresh_all_ranking_profiles(db, run_id=7, today=TODAY)

    assert len(results) == 10
    assert len(db.added) == 10
    assert db.flushes == 1
    assert db.executed == []
    assert {result.ranking_profile for result in results} == {
        "momentum_swing",
        "quality_momentum",
        "early_rocket",
        "clean_compounder_pullback",
        "defensive_quality",
    }
    for profile_name in {result.ranking_profile for result in results}:
        profile_results = [
            result for result in results if result.ranking_profile == profile_name
        ]
        assert [result.profile_rank for result in profile_results] == [1, 2]
        assert {result.ticker for result in profile_results} == {"MOMO", "QUAL"}


def test_refresh_all_ranking_profiles_is_idempotent_at_service_boundary(monkeypatch) -> None:
    db = FakeDb(upload_runs={7: UploadRun(id=7, filename="sample.csv", status="COMPLETED")})
    _patch_run_inputs(monkeypatch)

    first = refresh_all_ranking_profiles(db, run_id=7, today=TODAY)
    second = refresh_all_ranking_profiles(db, run_id=7, today=TODAY)

    assert len(first) == len(second) == 10
    assert len(db.added) == 10
    assert db.executed == []
    assert db.flushes == 2


def test_refresh_one_profile_persists_only_named_profile(monkeypatch) -> None:
    db = FakeDb(upload_runs={7: UploadRun(id=7, filename="sample.csv", status="COMPLETED")})
    _patch_run_inputs(monkeypatch)

    results = refresh_ranking_profile(
        db,
        run_id=7,
        profile_name="early_rocket",
        today=TODAY,
    )

    assert len(results) == 2
    assert {result.ranking_profile for result in results} == {"early_rocket"}
    assert db.executed == []
    assert db.flushes == 1


def test_refresh_ranking_profiles_preserves_existing_combined_results(monkeypatch) -> None:
    combined = CombinedResult(run_id=7, ticker="MOMO", final_rank=1)
    db = FakeDb(
        upload_runs={7: UploadRun(id=7, filename="sample.csv", status="COMPLETED")},
        combined_results=[combined],
    )
    _patch_run_inputs(monkeypatch)

    refresh_all_ranking_profiles(db, run_id=7, today=TODAY)

    assert db.combined_results == [combined]


def test_refresh_all_ranking_profiles_raises_for_missing_run(monkeypatch) -> None:
    db = FakeDb()
    _patch_run_inputs(monkeypatch)

    with pytest.raises(ValueError, match="Upload run 404 was not found"):
        refresh_all_ranking_profiles(db, run_id=404, today=TODAY)


def test_ranking_pipeline_step_is_explicitly_skipped_without_profiles(monkeypatch) -> None:
    monkeypatch.setattr(ranking_profile_service, "load_ranking_profiles", lambda: [])

    result = execute_ranking_pipeline_step(FakeDb(), run_id=7)

    assert result.status == "SKIPPED"
    assert result.reason == "no_configured_ranking_profiles"
    assert result.profile_count == result.result_count == 0


def test_ranking_pipeline_step_fails_when_profiles_produce_zero_results(monkeypatch) -> None:
    monkeypatch.setattr(ranking_profile_service, "load_ranking_profiles", lambda: [object()])
    monkeypatch.setattr(ranking_profile_service, "_raw_rows_for_run", lambda *_: _rows())
    monkeypatch.setattr(ranking_profile_service, "refresh_all_ranking_profiles", lambda *_: [])

    with pytest.raises(RuntimeError, match="produced zero results"):
        execute_ranking_pipeline_step(FakeDb(), run_id=7)


def test_run105_shaped_ranking_step_reports_186_by_configured_profiles(monkeypatch) -> None:
    profiles = [object() for _ in range(5)]
    rows = [_row(1000 + index, f"T{index:03}", index + 1) for index in range(186)]
    persisted = [
        object()
        for _ in range(len(rows) * len(profiles))
    ]
    monkeypatch.setattr(ranking_profile_service, "load_ranking_profiles", lambda: profiles)
    monkeypatch.setattr(ranking_profile_service, "_raw_rows_for_run", lambda *_: rows)
    monkeypatch.setattr(
        ranking_profile_service,
        "refresh_all_ranking_profiles",
        lambda *_: persisted,
    )

    result = execute_ranking_pipeline_step(FakeDb(), run_id=105)

    assert result.status == "COMPLETED"
    assert result.profile_count == 5
    assert result.result_count == 930


def test_refresh_one_profile_raises_for_unknown_profile(monkeypatch) -> None:
    db = FakeDb(upload_runs={7: UploadRun(id=7, filename="sample.csv", status="COMPLETED")})
    _patch_run_inputs(monkeypatch)

    with pytest.raises(ValueError, match="unknown ranking profile"):
        refresh_ranking_profile(db, run_id=7, profile_name="unknown", today=TODAY)


def test_to_ranking_model_persists_decision_fields(monkeypatch) -> None:
    _patch_run_inputs(monkeypatch)
    db = FakeDb(upload_runs={7: UploadRun(id=7, filename="sample.csv", status="COMPLETED")})

    result = refresh_ranking_profile(
        db,
        run_id=7,
        profile_name="momentum_swing",
        today=TODAY,
    )[0]

    assert result.run_id == 7
    assert result.raw_row_id == 101
    assert result.ranking_label == "Momentum Swing"
    assert result.profile_score is not None
    assert result.technical_profile_score is not None
    assert result.fundamental_score is not None
    assert result.base_technical_score is not None
    assert result.decision_label in {"Strong candidate", "Candidate"}
    assert result.notes
    assert isinstance(result.warning_flags_json, list)
    assert result.component_scores_json["momentum_strength"] > 0
    assert result.debug_json["ranking_engine_version"] == "1.1.0"


class FakeDb:
    def __init__(
        self,
        upload_runs: dict[int, UploadRun] | None = None,
        combined_results: list[CombinedResult] | None = None,
    ) -> None:
        self.upload_runs = upload_runs or {}
        self.combined_results = combined_results or []
        self.executed = []
        self.added = []
        self.flushes = 0

    def get(self, model, row_id):
        if model is UploadRun:
            return self.upload_runs.get(row_id)
        return None

    def execute(self, statement) -> None:
        self.executed.append(statement)

    def add_all(self, rows) -> None:
        self.added.extend(rows)

    def flush(self) -> None:
        self.flushes += 1


def _patch_run_inputs(monkeypatch) -> None:
    monkeypatch.setattr(ranking_profile_service, "_raw_rows_for_run", lambda _db, _run_id: _rows())
    monkeypatch.setattr(
        ranking_profile_service,
        "_fundamentals_for_run",
        lambda _db, _run_id: _fundamentals(),
    )
    monkeypatch.setattr(
        ranking_profile_service,
        "_technicals_for_run",
        lambda _db, _run_id: _technicals(),
    )
    monkeypatch.setattr(ranking_profile_service, "_load_scoring_config", lambda: _config())
    monkeypatch.setattr(
        ranking_profile_service,
        "_load_liquidity_features",
        lambda _db, _cutoff: {},
    )


def _rows() -> list[RawCompanyRow]:
    return [
        _row(101, "MOMO", row_number=1),
        _row(102, "QUAL", row_number=2),
        _row(103, "MOMO", row_number=3),
    ]


def _fundamentals() -> list[FundamentalScore]:
    return [
        _fundamental("MOMO", 7.8),
        _fundamental("QUAL", 8.9),
    ]


def _technicals() -> list[TechnicalScore]:
    return [
        _technical(
            "MOMO",
            trend=8.4,
            momentum=9.2,
            setup=8.6,
            risk=1.6,
            rs=9.4,
            breakout=8.8,
            vcp=8.2,
        ),
        _technical(
            "QUAL",
            trend=7.4,
            momentum=7.2,
            setup=7.0,
            risk=2.2,
            rs=7.5,
            breakout=7.0,
            vcp=7.2,
        ),
    ]


def _row(row_id: int, ticker: str, row_number: int) -> RawCompanyRow:
    return RawCompanyRow(
        id=row_id,
        run_id=7,
        row_number=row_number,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        raw_json={"Symbol": ticker},
    )


def _fundamental(ticker: str, score: float) -> FundamentalScore:
    return FundamentalScore(
        run_id=7,
        ticker=ticker,
        fundamental_score=Decimal(str(score)),
        fundamental_label="High-quality quant",
    )


def _technical(
    ticker: str,
    *,
    trend: float,
    momentum: float,
    setup: float,
    risk: float,
    rs: float,
    breakout: float,
    vcp: float,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=7,
        ticker=ticker,
        trend_score=Decimal(str(trend)),
        momentum_score=Decimal(str(momentum)),
        setup_score=Decimal(str(setup)),
        risk_score=Decimal(str(risk)),
        market_score=Decimal("8.0"),
        combined_relative_strength_score=Decimal(str(rs)),
        dual_score=Decimal(str((trend + momentum + setup + rs) / 4.0)),
        classification="Prime clean pullback",
        pullback_health="Healthy",
        technical_confidence="normal",
        vcp_score=Decimal(str(vcp)),
        box_tightness_score=Decimal("8.0"),
        breakout_quality_score=Decimal(str(breakout)),
        climax_risk_score=Decimal("1.4"),
        debug_json={"derived": {"rs_new_high": ticker == "MOMO"}},
    )


def _config() -> dict:
    return {
        "earnings_risk_gate": {
            "enabled": True,
            "block_if_within_days": 2,
            "high_risk_if_within_days": 5,
            "medium_risk_if_within_days": 10,
            "missing_date_policy": "ignore",
            "apply_to_combined_score": True,
            "block_new_entries": True,
            "penalties": {
                "blocked": 3.0,
                "high": 2.0,
                "medium": 1.0,
                "unknown": 0.0,
                "clear": 0.0,
            },
        }
    }
