import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import (
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services.calculation_identity import (
    PIPELINE_CONTEXT_COMPATIBILITY,
    CalculationIdentity,
    CalculationIdentityCompatibilityValidator,
    IdentityState,
)
from app.services.combined_decision import _load_scoring_config
from app.services.combined_ranking_identity import (
    RANKING_IBMI_COMPATIBILITY,
    RANKING_INPUT_COMPATIBILITY,
    build_ibmi_liquidity_identity,
    build_ranking_result_identity,
    calculation_identity_from_debug,
    cohort_identity_fingerprint,
    embed_calculation_identity,
    fundamental_score_identity,
    require_fundamental_raw_source,
    require_source_inputs,
    technical_score_identity,
    validate_ibmi_liquidity_for_ranking,
)
from app.services.core_calculation_evidence import CoreEvidenceKind, persist_core_evidence
from app.services.ib_market_intelligence.config import (
    load_ib_market_intelligence_config,
)
from app.services.market_calculation_context_service import (
    calculation_identity_from_market_context,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.ranking_profile_config import (
    RankingProfileConfig,
    get_ranking_profile,
    load_ranking_profiles,
)
from app.services.ranking_profile_engine import (
    RANKING_ENGINE_VERSION,
    RankingProfileDecision,
    rank_profile,
)

logger = logging.getLogger(__name__)


def get_ranking_profiles() -> list[RankingProfileConfig]:
    return load_ranking_profiles()


@dataclass(frozen=True)
class RankingPipelineResult:
    status: str
    profile_count: int
    result_count: int
    reason: str | None
    results: tuple[RankingResult, ...]


@dataclass(frozen=True)
class _ValidatedRankingSources:
    row: RawCompanyRow
    fundamental: FundamentalScore
    technical: TechnicalScore
    fundamental_identity: CalculationIdentity
    technical_identity: CalculationIdentity


def execute_ranking_pipeline_step(
    db: Session,
    run_id: int,
    *,
    market_cutoff: MarketCalculationCutoff | None = None,
    pipeline_run_id: int | None = None,
) -> RankingPipelineResult:
    profiles = load_ranking_profiles()
    if not profiles:
        return RankingPipelineResult(
            status="SKIPPED",
            profile_count=0,
            result_count=0,
            reason="no_configured_ranking_profiles",
            results=(),
        )
    rows = _raw_rows_for_run(db, run_id)
    if market_cutoff is None and pipeline_run_id is None:
        results = refresh_all_ranking_profiles(db, run_id)
    else:
        results = refresh_all_ranking_profiles(
            db,
            run_id,
            market_cutoff=market_cutoff,
            pipeline_run_id=pipeline_run_id,
        )
    if rows and not results:
        raise RuntimeError(
            "configured ranking profiles produced zero results for non-empty run inputs"
        )
    return RankingPipelineResult(
        status="COMPLETED",
        profile_count=len(profiles),
        result_count=len(results),
        reason=None,
        results=tuple(results),
    )


def refresh_all_ranking_profiles(
    db: Session,
    run_id: int,
    today: date | None = None,
    *,
    market_cutoff: MarketCalculationCutoff | None = None,
    pipeline_run_id: int | None = None,
) -> list[RankingResult]:
    _require_run(db, run_id)
    profiles = load_ranking_profiles()
    rows, fundamentals, technicals = _load_run_inputs(db, run_id)
    config = _load_scoring_config()
    validated = _validated_ranking_sources(
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        run_id=run_id,
        market_cutoff=market_cutoff,
        pipeline_run_id=pipeline_run_id,
    )
    calculation_cutoff = _validated_cutoff(validated)
    evaluation_date = _validated_evaluation_date(validated, today=today)
    liquidity_features = _load_liquidity_features(db, calculation_cutoff)
    safe_liquidity, liquidity_identities = _identity_safe_liquidity(
        liquidity_features,
        validated=validated,
    )

    desired: list[RankingResult] = []
    for profile in profiles:
        profile_liquidity = safe_liquidity if profile.tradeability_overlay.enabled else {}
        decisions = rank_profile(
            profile=profile,
            rows=rows,
            fundamentals=fundamentals,
            technicals=technicals,
            config=config,
            today=evaluation_date,
            liquidity_features=profile_liquidity,
        )
        cohort_identities = [
            identity
            for item in validated.values()
            for identity in (item.fundamental_identity, item.technical_identity)
        ]
        if profile.tradeability_overlay.enabled:
            cohort_identities.extend(liquidity_identities.values())
        cohort_fingerprint = cohort_identity_fingerprint(cohort_identities)
        models = []
        for decision in decisions:
            item = validated[decision.ticker]
            liquidity = profile_liquidity.get(decision.ticker)
            liquidity_identity = liquidity_identities.get(decision.ticker)
            identity = build_ranking_result_identity(
                fundamental_identity=item.fundamental_identity,
                technical_identity=item.technical_identity,
                fundamental_score=item.fundamental,
                technical_score=item.technical,
                profile=profile,
                global_config=config,
                calculation_version=RANKING_ENGINE_VERSION,
                cohort_fingerprint=cohort_fingerprint,
                liquidity_feature=liquidity,
                liquidity_identity=liquidity_identity if liquidity is not None else None,
            )
            models.append(_to_ranking_model(run_id, decision, identity))
        desired.extend(models)

    return _persist_rankings(db, run_id=run_id, desired=desired, source_rows=validated)


def refresh_ranking_profile(
    db: Session,
    run_id: int,
    profile_name: str,
    today: date | None = None,
    *,
    market_cutoff: MarketCalculationCutoff | None = None,
    pipeline_run_id: int | None = None,
) -> list[RankingResult]:
    _require_run(db, run_id)
    profile = get_ranking_profile(profile_name)
    rows, fundamentals, technicals = _load_run_inputs(db, run_id)
    config = _load_scoring_config()
    validated = _validated_ranking_sources(
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        run_id=run_id,
        market_cutoff=market_cutoff,
        pipeline_run_id=pipeline_run_id,
    )
    calculation_cutoff = _validated_cutoff(validated)
    evaluation_date = _validated_evaluation_date(validated, today=today)
    liquidity_features = _load_liquidity_features(db, calculation_cutoff)
    safe_liquidity, liquidity_identities = _identity_safe_liquidity(
        liquidity_features,
        validated=validated,
    )
    profile_liquidity = safe_liquidity if profile.tradeability_overlay.enabled else {}

    decisions = rank_profile(
        profile=profile,
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        config=config,
        today=evaluation_date,
        liquidity_features=profile_liquidity,
    )
    cohort_identities = [
        identity
        for item in validated.values()
        for identity in (item.fundamental_identity, item.technical_identity)
    ]
    if profile.tradeability_overlay.enabled:
        cohort_identities.extend(liquidity_identities.values())
    cohort_fingerprint = cohort_identity_fingerprint(cohort_identities)
    models = []
    for decision in decisions:
        item = validated[decision.ticker]
        liquidity = profile_liquidity.get(decision.ticker)
        identity = build_ranking_result_identity(
            fundamental_identity=item.fundamental_identity,
            technical_identity=item.technical_identity,
            fundamental_score=item.fundamental,
            technical_score=item.technical,
            profile=profile,
            global_config=config,
            calculation_version=RANKING_ENGINE_VERSION,
            cohort_fingerprint=cohort_fingerprint,
            liquidity_feature=liquidity,
            liquidity_identity=(
                liquidity_identities.get(decision.ticker) if liquidity is not None else None
            ),
        )
        models.append(_to_ranking_model(run_id, decision, identity))
    return _persist_rankings(db, run_id=run_id, desired=models, source_rows=validated)


def _persist_rankings(
    db: Session,
    *,
    run_id: int,
    desired: list[RankingResult],
    source_rows: dict[str, _ValidatedRankingSources] | None = None,
) -> list[RankingResult]:
    existing = {
        (row.ranking_profile, row.ticker.upper()): row for row in _existing_rankings(db, run_id)
    }
    _preflight_ranking_persistence(existing=existing, desired=desired)
    persisted: list[RankingResult] = []
    added: list[RankingResult] = []
    for candidate in desired:
        key = (candidate.ranking_profile, candidate.ticker.upper())
        current = existing.get(key)
        if current is None:
            added.append(candidate)
            persisted.append(candidate)
            existing[key] = candidate
            continue
        current_identity = calculation_identity_from_debug(current.debug_json)
        if current_identity is None:
            _replace_legacy_ranking(db, current=current, candidate=candidate)
            persisted.append(candidate)
            existing[key] = candidate
            continue
        _copy_ranking_values(current, candidate)
        persisted.append(current)
    if added:
        db.add_all(added)
    db.flush()
    if isinstance(db, Session):
        for result in persisted:
            item = (source_rows or {}).get(result.ticker.upper())
            if item is None:
                raise ValueError(
                    f"EVIDENCE_UNAVAILABLE: Ranking source rows missing for {result.ticker}"
                )
            persist_core_evidence(
                db,
                kind=CoreEvidenceKind.RANKING,
                current_row=result,
                sources={
                    "fundamental": item.fundamental,
                    "technical": item.technical,
                },
            )
    return persisted


def _preflight_ranking_persistence(
    *,
    existing: dict[tuple[str, str], RankingResult],
    desired: list[RankingResult],
) -> None:
    for candidate in desired:
        candidate_identity = calculation_identity_from_debug(candidate.debug_json)
        if candidate_identity is None:
            raise ValueError(
                "CALCULATION_IDENTITY_PERSISTENCE_CONFLICT: desired RankingResult "
                "has no calculation identity"
            )
        current = existing.get((candidate.ranking_profile, candidate.ticker.upper()))
        if current is None:
            continue
        if calculation_identity_from_debug(current.debug_json) is None:
            continue


def _replace_legacy_ranking(
    db: Session,
    *,
    current: RankingResult,
    candidate: RankingResult,
) -> None:
    deleter = getattr(db, "delete", None)
    if callable(deleter):
        deleter(current)
        db.flush()
    else:
        added = getattr(db, "added", None)
        if isinstance(added, list) and current in added:
            added.remove(current)
    db.add_all([candidate])


def _existing_rankings(db: Session, run_id: int) -> list[RankingResult]:
    scalars = getattr(db, "scalars", None)
    if callable(scalars):
        return list(scalars(select(RankingResult).where(RankingResult.run_id == run_id)))
    return [
        row
        for row in getattr(db, "added", [])
        if isinstance(row, RankingResult) and row.run_id == run_id
    ]


def _copy_ranking_values(target: RankingResult, source: RankingResult) -> None:
    immutable = {
        "id",
        "run_id",
        "ranking_profile",
        "ticker",
        "evidence_id",
        "created_at",
        "updated_at",
    }
    for column in RankingResult.__table__.columns:
        if column.name not in immutable:
            setattr(target, column.name, getattr(source, column.name))


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


def _require_run(db: Session, run_id: int) -> UploadRun:
    run = db.get(UploadRun, run_id)
    if run is None:
        raise ValueError(f"Upload run {run_id} was not found")
    return run


def _load_liquidity_features(
    db: Session,
    cutoff: datetime,
) -> dict[str, IBIntelligenceFeature]:
    features = db.scalars(
        select(IBIntelligenceFeature)
        .where(
            IBIntelligenceFeature.module == "LIQUIDITY",
            IBIntelligenceFeature.calculated_at <= cutoff,
            IBIntelligenceFeature.as_of_session <= cutoff.date(),
        )
        .order_by(
            IBIntelligenceFeature.ticker,
            IBIntelligenceFeature.as_of_session.desc(),
            IBIntelligenceFeature.calculated_at.desc(),
            IBIntelligenceFeature.id.desc(),
        )
    ).all()
    latest: dict[str, IBIntelligenceFeature] = {}
    for feature in features:
        latest.setdefault(feature.ticker.upper(), feature)
    return latest


def _load_run_inputs(
    db: Session,
    run_id: int,
) -> tuple[
    list[RawCompanyRow],
    dict[str, FundamentalScore],
    dict[str, TechnicalScore],
]:
    rows = _raw_rows_for_run(db, run_id)
    fundamentals = {score.ticker.upper(): score for score in _fundamentals_for_run(db, run_id)}
    technicals = {score.ticker.upper(): score for score in _technicals_for_run(db, run_id)}
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
    return list(db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id)))


def _technicals_for_run(db: Session, run_id: int) -> list[TechnicalScore]:
    return list(db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id)))


def _to_ranking_model(
    run_id: int,
    decision: RankingProfileDecision,
    calculation_identity: CalculationIdentity | None = None,
) -> RankingResult:
    debug_json = decision.debug
    if calculation_identity is not None:
        debug_json = embed_calculation_identity(
            debug_json,
            calculation_identity,
            policy=RANKING_INPUT_COMPATIBILITY.name,
        )
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
        debug_json=debug_json,
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


def _validated_ranking_sources(
    *,
    rows: list[RawCompanyRow],
    fundamentals: dict[str, FundamentalScore],
    technicals: dict[str, TechnicalScore],
    run_id: int,
    market_cutoff: MarketCalculationCutoff | None,
    pipeline_run_id: int | None,
) -> dict[str, _ValidatedRankingSources]:
    validated: dict[str, _ValidatedRankingSources] = {}
    for row in _unique_rows(rows):
        ticker = row.ticker.upper()
        fundamental = fundamentals.get(ticker)
        technical = technicals.get(ticker)
        if fundamental is None or technical is None:
            raise ValueError(
                "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
                f"producer=FundamentalScore+TechnicalScore ticker={ticker} "
                "result=INSUFFICIENT_IDENTITY details=required source artifact is absent"
            )
        fundamental_identity = fundamental_score_identity(fundamental)
        technical_identity = technical_score_identity(technical)
        require_fundamental_raw_source(
            fundamental_identity,
            row,
            consumer="RankingResult",
        )
        compatibility = require_source_inputs(
            fundamental_identity,
            technical_identity,
            policy=RANKING_INPUT_COMPATIBILITY,
            consumer="RankingResult",
            left_producer="FundamentalScore",
            right_producer="TechnicalScore",
        )
        _validate_explicit_pipeline_context(
            source_identity=technical_identity,
            run_id=run_id,
            ticker=ticker,
            market_cutoff=market_cutoff,
            pipeline_run_id=pipeline_run_id,
        )
        logger.debug(
            "ranking input calculation identity compatible",
            extra={
                "calculation_identity_policy": compatibility.policy,
                "calculation_identity_result": compatibility.status.value,
                "calculation_identity_left_fingerprint": compatibility.left_fingerprint,
                "calculation_identity_right_fingerprint": compatibility.right_fingerprint,
            },
        )
        validated[ticker] = _ValidatedRankingSources(
            row,
            fundamental,
            technical,
            fundamental_identity,
            technical_identity,
        )
    _require_single_ranking_context(validated)
    return validated


def _validated_cutoff(validated: dict[str, _ValidatedRankingSources]) -> datetime:
    if not validated:
        raise ValueError(
            "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
            "result=INSUFFICIENT_IDENTITY details=no validated source identity"
        )
    cutoffs = {
        item.technical_identity.temporal.calculation_cutoff.value
        for item in validated.values()
        if item.technical_identity.temporal.calculation_cutoff.state is IdentityState.KNOWN
    }
    if len(cutoffs) != 1:
        raise ValueError(
            "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
            "dimension=temporal.calculation_cutoff details=cohort cutoff is not unique and known"
        )
    return next(iter(cutoffs))


def _validated_session(validated: dict[str, _ValidatedRankingSources]) -> date:
    sessions = {
        item.technical_identity.temporal.as_of_session.value
        for item in validated.values()
        if item.technical_identity.temporal.as_of_session.state is IdentityState.KNOWN
    }
    if len(sessions) != 1:
        raise ValueError(
            "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
            "dimension=temporal.as_of_session details=cohort session is not unique and known"
        )
    return next(iter(sessions))


def _validated_evaluation_date(
    validated: dict[str, _ValidatedRankingSources],
    *,
    today: date | None,
) -> date:
    session = _validated_session(validated)
    if today is not None and today != session:
        raise ValueError(
            "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
            f"dimension=temporal.as_of_session expected={session.isoformat()} "
            f"actual={today.isoformat()} reason=EVALUATION_DATE_MISMATCH"
        )
    return session


def _identity_safe_liquidity(
    features: dict[str, IBIntelligenceFeature],
    *,
    validated: dict[str, _ValidatedRankingSources],
) -> tuple[dict[str, IBIntelligenceFeature], dict[str, CalculationIdentity]]:
    config = load_ib_market_intelligence_config()
    safe: dict[str, IBIntelligenceFeature] = {}
    identities: dict[str, CalculationIdentity] = {}
    for ticker, feature in features.items():
        source = validated.get(ticker)
        if source is None:
            continue
        identity = build_ibmi_liquidity_identity(feature)
        result = validate_ibmi_liquidity_for_ranking(
            feature_identity=identity,
            ranking_spine=source.technical_identity,
            expected_config=config,
        )
        if result.accepted:
            safe[ticker] = feature
            identities[ticker] = identity
            continue
        logger.debug(
            "ranking optional IBMI liquidity omitted due calculation identity",
            extra={
                "calculation_identity_policy": RANKING_IBMI_COMPATIBILITY.name,
                "calculation_identity_result": result.status.value,
                "calculation_identity_fingerprint": result.right_fingerprint,
                "calculation_identity_failed_dimensions": list(result.diagnostics),
                "ticker": ticker,
            },
        )
    return safe, identities


def _validate_explicit_pipeline_context(
    *,
    source_identity: CalculationIdentity,
    run_id: int,
    ticker: str,
    market_cutoff: MarketCalculationCutoff | None,
    pipeline_run_id: int | None,
) -> None:
    if market_cutoff is None and pipeline_run_id is None:
        return
    if market_cutoff is None or pipeline_run_id is None:
        raise ValueError(
            "Ranking identity validation requires market_cutoff and pipeline_run_id together"
        )
    expected = calculation_identity_from_market_context(
        market_cutoff,
        run_id=run_id,
        pipeline_id=pipeline_run_id,
        ticker=ticker,
    )
    compatibility = CalculationIdentityCompatibilityValidator.compare(
        expected,
        source_identity,
        policy=PIPELINE_CONTEXT_COMPATIBILITY,
    )
    if not compatibility.accepted:
        raise ValueError(
            "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
            f"producer=PipelineContext {compatibility.diagnostic()}"
        )


def _require_single_ranking_context(
    validated: dict[str, _ValidatedRankingSources],
) -> None:
    values = list(validated.values())
    if not values:
        return
    expected = values[0].technical_identity
    for item in values[1:]:
        compatibility = CalculationIdentityCompatibilityValidator.compare(
            expected,
            item.technical_identity,
            policy=PIPELINE_CONTEXT_COMPATIBILITY,
        )
        if not compatibility.accepted:
            raise ValueError(
                "CALCULATION_IDENTITY_REJECTED: consumer=RankingResult "
                f"producer=TechnicalScore ticker={item.row.ticker.upper()} "
                f"{compatibility.diagnostic()}"
            )


def _unique_rows(rows: list[RawCompanyRow]) -> list[RawCompanyRow]:
    seen: set[str] = set()
    unique: list[RawCompanyRow] = []
    for row in rows:
        ticker = row.ticker.upper()
        if ticker not in seen:
            seen.add(ticker)
            unique.append(row)
    return unique
