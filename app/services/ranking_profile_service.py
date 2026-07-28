from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import (
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services.combined_decision import _load_scoring_config
from app.services.ranking_profile_config import (
    RankingProfileConfig,
    get_ranking_profile,
    load_ranking_profiles,
)
from app.services.ranking_profile_engine import RankingProfileDecision, rank_profile


def get_ranking_profiles() -> list[RankingProfileConfig]:
    return load_ranking_profiles()


def refresh_all_ranking_profiles(
    db: Session,
    run_id: int,
    today: date | None = None,
) -> list[RankingResult]:
    _require_run(db, run_id)
    profiles = load_ranking_profiles()
    rows, fundamentals, technicals = _load_run_inputs(db, run_id)
    config = _load_scoring_config()

    db.execute(delete(RankingResult).where(RankingResult.run_id == run_id))

    all_results: list[RankingResult] = []
    for profile in profiles:
        decisions = rank_profile(
            profile=profile,
            rows=rows,
            fundamentals=fundamentals,
            technicals=technicals,
            config=config,
            today=today,
        )
        models = [_to_ranking_model(run_id, decision) for decision in decisions]
        db.add_all(models)
        all_results.extend(models)

    db.flush()
    return all_results


def refresh_ranking_profile(
    db: Session,
    run_id: int,
    profile_name: str,
    today: date | None = None,
) -> list[RankingResult]:
    _require_run(db, run_id)
    profile = get_ranking_profile(profile_name)
    rows, fundamentals, technicals = _load_run_inputs(db, run_id)
    config = _load_scoring_config()

    db.execute(
        delete(RankingResult).where(
            RankingResult.run_id == run_id,
            RankingResult.ranking_profile == profile.name,
        )
    )

    decisions = rank_profile(
        profile=profile,
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        config=config,
        today=today,
    )
    models = [_to_ranking_model(run_id, decision) for decision in decisions]
    db.add_all(models)
    db.flush()
    return models


def get_ranking_results(
    db: Session,
    run_id: int,
    profile_name: str,
) -> list[RankingResult]:
    return list(
        db.scalars(
            select(RankingResult)
            .where(
                RankingResult.run_id == run_id,
                RankingResult.ranking_profile == profile_name,
            )
            .order_by(RankingResult.profile_rank)
        )
    )


def get_all_ranking_results(db: Session, run_id: int) -> list[RankingResult]:
    return list(
        db.scalars(
            select(RankingResult)
            .where(RankingResult.run_id == run_id)
            .order_by(RankingResult.ranking_profile, RankingResult.profile_rank)
        )
    )


def _require_run(db: Session, run_id: int) -> None:
    if db.get(UploadRun, run_id) is None:
        raise ValueError(f"Upload run {run_id} was not found")


def _load_run_inputs(
    db: Session,
    run_id: int,
) -> tuple[
    list[RawCompanyRow],
    dict[str, FundamentalScore],
    dict[str, TechnicalScore],
]:
    rows = _raw_rows_for_run(db, run_id)
    fundamentals = {
        score.ticker.upper(): score
        for score in _fundamentals_for_run(db, run_id)
    }
    technicals = {
        score.ticker.upper(): score
        for score in _technicals_for_run(db, run_id)
    }
    return rows, fundamentals, technicals


def _raw_rows_for_run(db: Session, run_id: int) -> list[RawCompanyRow]:
    return list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )


def _fundamentals_for_run(db: Session, run_id: int) -> list[FundamentalScore]:
    return list(
        db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id))
    )


def _technicals_for_run(db: Session, run_id: int) -> list[TechnicalScore]:
    return list(db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id)))


def _to_ranking_model(
    run_id: int,
    decision: RankingProfileDecision,
) -> RankingResult:
    return RankingResult(
        run_id=run_id,
        raw_row_id=decision.raw_row_id,
        ticker=decision.ticker,
        company_name=decision.company_name,
        sector=decision.sector,
        ranking_profile=decision.ranking_profile,
        ranking_label=decision.ranking_label,
        profile_rank=decision.profile_rank,
        profile_score=_to_decimal(decision.profile_score) or Decimal("0"),
        technical_profile_score=_to_decimal(decision.technical_profile_score),
        fundamental_score=_to_decimal(decision.fundamental_score),
        base_technical_score=_to_decimal(decision.base_technical_score),
        technical_classification=decision.technical_classification,
        fundamental_label=decision.fundamental_label,
        decision_label=decision.decision_label,
        position_size_hint=decision.position_size_hint,
        notes=", ".join(decision.notes),
        warning_flags_json=decision.warning_flags,
        penalties_json=decision.penalties,
        gates_json=decision.gates,
        component_scores_json=decision.component_scores,
        debug_json=decision.debug,
        upcoming_earnings_date=decision.upcoming_earnings_date,
        days_until_earnings=decision.days_until_earnings,
        earnings_risk_level=decision.earnings_risk_level,
        is_complete=decision.is_complete,
        has_warning=decision.has_warning,
        has_fundamental=decision.has_fundamental,
        has_technical=decision.has_technical,
        sort_bucket=decision.sort_bucket,
    )


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))
