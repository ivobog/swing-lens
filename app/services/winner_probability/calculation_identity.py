from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from app.services.calculation_identity import (
    AlgorithmIdentity,
    CalculationIdentity,
    CalculationIdentityCompatibilityProfile,
    CalculationIdentityCompatibilityValidator,
    CompatibilityDimensionRule,
    ConfigurationIdentity,
    DigestIdentity,
    GenerationIdentity,
    IdentityDimension,
    SourceArtifactReference,
    SourceLineageIdentity,
    VersionIdentity,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.contextual_calculation_identity import (
    artifact_identity,
    build_contextual_result_identity,
    consumer_context_identity,
    contextual_compatibility,
    expected_regime_identity,
    expected_sector_identity,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)


class WinnerCalculationIdentityError(ValueError):
    pass


WINNER_HANDOFF_CONTRACT = "transition-decision-handoff-v1"


def _profile(
    name: str, *dimensions: tuple[str, bool]
) -> CalculationIdentityCompatibilityProfile:
    return CalculationIdentityCompatibilityProfile(
        name,
        tuple(
            CompatibilityDimensionRule(dimension, allow_not_applicable=allow_na)
            for dimension, allow_na in dimensions
        ),
    )


_WINNER_RUN_CONTEXT_DIMENSIONS = (
    ("ownership.run_id", False),
    ("ownership.pipeline_id", False),
    ("subject.ticker", False),
    ("calculation_context.market_calculation_context_id", False),
    ("calculation_context.context_fingerprint", False),
    ("temporal.as_of_session", False),
    ("temporal.calculation_cutoff", False),
    ("temporal.calendar", False),
)

WINNER_HANDOFF_COMPATIBILITY = _profile(
    "WINNER_HANDOFF_COMPATIBILITY",
    ("ownership.run_id", False),
    ("ownership.pipeline_id", False),
    ("calculation_context.market_calculation_context_id", False),
    ("calculation_context.context_fingerprint", False),
    ("temporal.as_of_session", False),
    ("temporal.calculation_cutoff", False),
    ("temporal.calendar", False),
    ("algorithm.calculation_version", False),
    ("source_lineage", False),
)
WINNER_RAW_COMPATIBILITY = _profile(
    "WINNER_RAW_COMPATIBILITY",
    ("ownership.run_id", False),
    ("subject.ticker", False),
    ("source_lineage", False),
)
WINNER_FUNDAMENTAL_COMPATIBILITY = _profile(
    "WINNER_FUNDAMENTAL_COMPATIBILITY", *_WINNER_RUN_CONTEXT_DIMENSIONS
)
WINNER_TECHNICAL_COMPATIBILITY = _profile(
    "WINNER_TECHNICAL_COMPATIBILITY", *_WINNER_RUN_CONTEXT_DIMENSIONS
)
WINNER_COMBINED_COMPATIBILITY = _profile(
    "WINNER_COMBINED_COMPATIBILITY", *_WINNER_RUN_CONTEXT_DIMENSIONS
)
WINNER_RANKING_COMPATIBILITY = _profile(
    "WINNER_RANKING_COMPATIBILITY", *_WINNER_RUN_CONTEXT_DIMENSIONS
)
WINNER_REGIME_COMPATIBILITY = _profile(
    "WINNER_REGIME_COMPATIBILITY",
    ("temporal.as_of_session", False),
    ("temporal.calculation_cutoff", False),
    ("temporal.calendar", False),
    ("configuration.effective_configuration", False),
    ("algorithm.calculation_version", False),
    ("algorithm.engine_version", False),
)
WINNER_SECTOR_COMPATIBILITY = _profile(
    "WINNER_SECTOR_COMPATIBILITY",
    ("ownership.run_id", False),
    ("ownership.pipeline_id", False),
    ("calculation_context.market_calculation_context_id", False),
    ("calculation_context.context_fingerprint", False),
    ("temporal.as_of_session", False),
    ("temporal.calculation_cutoff", False),
    ("temporal.calendar", False),
    ("configuration.effective_configuration", False),
    ("algorithm.calculation_version", False),
    ("algorithm.engine_version", False),
)


@dataclass(frozen=True)
class WinnerSourceAcquisition:
    run_context: Any
    ticker_context: Any
    decision_identity: CalculationIdentity
    handoff_identity: CalculationIdentity
    source_artifacts: tuple[tuple[str, Any, CalculationIdentity], ...]


def validate_winner_handoff(
    handoff: Any,
    *,
    run_id: int,
    market_cutoff: MarketCalculationCutoff,
) -> tuple[CalculationIdentity, int]:
    if handoff is None:
        raise WinnerCalculationIdentityError(
            "CALCULATION_IDENTITY_REJECTED: consumer=Winner producer=DecisionHandoff "
            "reason=LEGACY_UNKNOWN"
        )
    payload = getattr(handoff, "manifest_json", None)
    if not isinstance(payload, dict):
        raise WinnerCalculationIdentityError("Winner Decision Handoff manifest is missing")
    if payload.get("contract") != WINNER_HANDOFF_CONTRACT:
        raise WinnerCalculationIdentityError("Winner Decision Handoff contract is unknown")
    actual_fingerprint = CanonicalEvidenceSerializer.fingerprint(payload)
    if actual_fingerprint != getattr(handoff, "manifest_fingerprint", None):
        raise WinnerCalculationIdentityError("Winner Decision Handoff fingerprint mismatch")
    binding = payload.get("binding") or {}
    run_payload = payload.get("run") or {}
    context_payload = payload.get("market_context") or {}
    pipeline_id = getattr(handoff, "pipeline_run_id", None)
    if not isinstance(pipeline_id, int):
        raise WinnerCalculationIdentityError("Winner Decision Handoff pipeline is unknown")
    expected_values = {
        "upload_run_id": run_id,
        "pipeline_run_id": pipeline_id,
        "market_context_id": market_cutoff.context_id,
        "run_start_anchor_fingerprint": getattr(
            handoff, "run_start_anchor_fingerprint", None
        ),
    }
    actual_values = {
        "upload_run_id": run_payload.get("upload_run_id"),
        "pipeline_run_id": run_payload.get("pipeline_run_id"),
        "market_context_id": context_payload.get("id"),
        "run_start_anchor_fingerprint": binding.get("run_start_anchor_fingerprint"),
    }
    if (
        getattr(handoff, "upload_run_id", None) != run_id
        or getattr(handoff, "market_calculation_context_id", None)
        != market_cutoff.context_id
        or actual_values != expected_values
    ):
        raise WinnerCalculationIdentityError("Winner Decision Handoff ownership mismatch")
    actual_cutoff = _cutoff_from_handoff(context_payload)
    expected = _handoff_identity(
        market_cutoff,
        run_id=run_id,
        pipeline_id=pipeline_id,
        manifest_fingerprint=actual_fingerprint,
    )
    actual = _handoff_identity(
        actual_cutoff,
        run_id=int(run_payload["upload_run_id"]),
        pipeline_id=int(run_payload["pipeline_run_id"]),
        manifest_fingerprint=actual_fingerprint,
    )
    comparison = CalculationIdentityCompatibilityValidator.compare(
        expected, actual, policy=WINNER_HANDOFF_COMPATIBILITY
    )
    if not comparison.accepted:
        raise WinnerCalculationIdentityError(comparison.diagnostic())
    return actual, pipeline_id


def acquire_winner_sources(
    run_context: Any,
    ticker_context: Any,
    *,
    run_id: int,
    market_cutoff: MarketCalculationCutoff,
    winner_config: Any,
) -> WinnerSourceAcquisition:
    handoff = getattr(run_context, "decision_handoff_manifest", None)
    handoff_identity, pipeline_id = validate_winner_handoff(
        handoff, run_id=run_id, market_cutoff=market_cutoff
    )
    ticker = str(ticker_context.raw_row.ticker).strip().upper()
    decision = consumer_context_identity(
        market_cutoff=market_cutoff,
        run_id=run_id,
        pipeline_id=pipeline_id,
        ticker=ticker,
    )
    lineage = _ticker_lineage(handoff, ticker)

    raw_expected = lineage.get("raw_row")
    raw_identity = _validate_raw(
        ticker_context.raw_row,
        expected_artifact=raw_expected,
        run_id=run_id,
        ticker=ticker,
    )
    sources: list[tuple[str, Any, CalculationIdentity]] = [
        ("DecisionHandoff", handoff, handoff_identity),
        ("RawCompanyRow", ticker_context.raw_row, raw_identity),
    ]

    fundamental, fundamental_identity = _direct_source(
        ticker_context.fundamental_score,
        expected_artifact=lineage.get("fundamental_score"),
        expected_identity=decision,
        policy=WINNER_FUNDAMENTAL_COMPATIBILITY,
        required=False,
        kind="FundamentalScore",
    )
    technical, technical_identity = _direct_source(
        ticker_context.technical_score,
        expected_artifact=lineage.get("technical_score"),
        expected_identity=decision,
        policy=WINNER_TECHNICAL_COMPATIBILITY,
        required=True,
        kind="TechnicalScore",
    )
    combined, combined_identity = _direct_source(
        ticker_context.combined_result,
        expected_artifact=lineage.get("combined_result"),
        expected_identity=decision,
        policy=WINNER_COMBINED_COMPATIBILITY,
        required=True,
        kind="CombinedResult",
    )
    for kind, artifact, identity in (
        ("FundamentalScore", fundamental, fundamental_identity),
        ("TechnicalScore", technical, technical_identity),
        ("CombinedResult", combined, combined_identity),
    ):
        if artifact is not None and identity is not None:
            sources.append((kind, artifact, identity))

    expected_rankings = lineage.get("ranking_results") or []
    rankings: list[tuple[Any, CalculationIdentity]] = []
    for candidate in ticker_context.ranking_results:
        identity = artifact_identity(candidate)
        compatible = contextual_compatibility(
            decision, identity, policy=WINNER_RANKING_COMPATIBILITY
        )
        if compatible.accepted and any(
            _artifact_matches(candidate, expected) for expected in expected_rankings
        ):
            rankings.append((candidate, identity))
    rankings.sort(key=lambda item: (item[0].profile_rank, item[0].ranking_profile, item[0].id))
    selected_rankings = tuple(item[0] for item in rankings)
    for candidate, identity in rankings:
        sources.append(("RankingResult", candidate, identity))

    regime_config = load_market_regime_command_center_config()
    regime_expected = expected_regime_identity(
        market_cutoff=market_cutoff, config=regime_config
    )
    market, market_identity = _context_source(
        _candidates(
            run_context,
            "market_regime_candidates",
            "market_regime_snapshot",
        ),
        expected_artifact=lineage.get("market_regime_snapshot"),
        expected_identity=regime_expected,
        policy=WINNER_REGIME_COMPATIBILITY,
    )
    if market is not None and market_identity is not None:
        sources.append(("MarketRegimeSnapshot", market, market_identity))

    sector_config = load_sector_rotation_config()
    mode = (
        "combined"
        if bool(sector_config.get("etf_score", {}).get("enabled", False))
        else "universe_only"
    )
    sector_expected = expected_sector_identity(
        context=replace(
            decision,
            subject=replace(
                decision.subject,
                ticker=IdentityDimension.not_applicable(),
            ),
        ),
        config_hash=sector_rotation_config_hash(sector_config),
        calculation_version="sector-rotation-1.0.0",
        mode=mode,
    )
    sector, sector_identity = _context_source(
        _candidates(
            run_context,
            "sector_rotation_candidates",
            "sector_rotation_snapshot",
        ),
        expected_artifact=lineage.get("sector_rotation_snapshot"),
        expected_identity=sector_expected,
        policy=WINNER_SECTOR_COMPATIBILITY,
    )
    sector_row = None
    if sector is not None and sector_identity is not None:
        sources.append(("SectorRotationSnapshot", sector, sector_identity))
        sector_rows = getattr(run_context, "sector_rows_by_snapshot", {}) or {}
        rows = sector_rows.get(sector.id, {})
        sector_name = ticker_context.raw_row.sector_canonical or ticker_context.raw_row.sector
        sector_row = rows.get(sector_name)
        if sector_row is None and getattr(run_context, "sector_rotation_snapshot", None) is sector:
            sector_row = ticker_context.sector_row
        if not _artifact_matches(sector_row, lineage.get("sector_rotation_row")):
            sector_row = None

    selected_ticker = replace(
        ticker_context,
        fundamental_score=fundamental,
        technical_score=technical,
        combined_result=combined,
        ranking_results=selected_rankings,
        sector_row=sector_row,
    )
    selected_run = replace(
        run_context,
        market_regime_snapshot=market,
        sector_rotation_snapshot=sector,
    )
    _ = winner_config
    return WinnerSourceAcquisition(
        run_context=selected_run,
        ticker_context=selected_ticker,
        decision_identity=decision,
        handoff_identity=handoff_identity,
        source_artifacts=tuple(sources),
    )


def build_winner_prediction_identity(
    acquisition: WinnerSourceAcquisition,
    *,
    config: Any,
    feature_vector_hash: str,
) -> CalculationIdentity:
    return build_contextual_result_identity(
        base=acquisition.decision_identity,
        namespace="winner-prediction",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        engine_version=config.feature_schema.version,
        source_artifacts=acquisition.source_artifacts,
        source_payload={
            "feature_schema_version": config.feature_schema.version,
            "feature_vector_hash": feature_vector_hash,
        },
    )


def _direct_source(
    artifact: Any,
    *,
    expected_artifact: Any,
    expected_identity: CalculationIdentity,
    policy: CalculationIdentityCompatibilityProfile,
    required: bool,
    kind: str,
) -> tuple[Any | None, CalculationIdentity | None]:
    if artifact is None:
        return None, None
    identity = artifact_identity(artifact)
    result = contextual_compatibility(expected_identity, identity, policy=policy)
    if result.accepted and _artifact_matches(artifact, expected_artifact):
        return artifact, identity
    if required:
        raise WinnerCalculationIdentityError(
            f"CALCULATION_IDENTITY_REJECTED: consumer=Winner producer={kind} "
            f"policy={policy.name} result={result.status.value}"
        )
    return None, None


def _context_source(
    candidates: tuple[Any, ...],
    *,
    expected_artifact: Any,
    expected_identity: CalculationIdentity,
    policy: CalculationIdentityCompatibilityProfile,
) -> tuple[Any | None, CalculationIdentity | None]:
    for artifact in candidates:
        identity = artifact_identity(artifact)
        if not _artifact_matches(artifact, expected_artifact):
            continue
        if contextual_compatibility(
            expected_identity, identity, policy=policy
        ).accepted:
            return artifact, identity
    return None, None


def _validate_raw(
    raw: Any,
    *,
    expected_artifact: Any,
    run_id: int,
    ticker: str,
) -> CalculationIdentity:
    if not isinstance(expected_artifact, dict):
        raise WinnerCalculationIdentityError("Winner raw source is absent from Handoff")
    expected = _raw_identity(
        run_id=run_id,
        ticker=ticker,
        artifact_id=expected_artifact.get("id"),
        semantic_hash=expected_artifact.get("semantic_hash"),
    )
    actual_artifact = semantic_artifact_identity(raw)
    actual = _raw_identity(
        run_id=getattr(raw, "run_id", None),
        ticker=getattr(raw, "ticker", ""),
        artifact_id=actual_artifact["id"],
        semantic_hash=actual_artifact["semantic_hash"],
    )
    comparison = CalculationIdentityCompatibilityValidator.compare(
        expected, actual, policy=WINNER_RAW_COMPATIBILITY
    )
    if not comparison.accepted:
        raise WinnerCalculationIdentityError(comparison.diagnostic())
    return actual


def _raw_identity(
    *, run_id: Any, ticker: str, artifact_id: Any, semantic_hash: Any
) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    base = CalculationIdentity.legacy_unknown(run_id=run_id, ticker=str(ticker).upper())
    if not isinstance(run_id, int) or not isinstance(semantic_hash, str):
        return base
    reference = SourceArtifactReference(
        "RawCompanyRow",
        str(artifact_id),
        na,
        IdentityDimension.known(
            DigestIdentity("sha256", semantic_hash, "immutable Handoff raw-row semantics")
        ),
    )
    lineage = SourceLineageIdentity(
        (reference,),
        IdentityDimension.known(
            DigestIdentity("sha256", semantic_hash, "exact Handoff raw-row identity")
        ),
        "exact RawCompanyRow ID and semantic hash",
    )
    return replace(
        base,
        ownership=replace(base.ownership, run_id=IdentityDimension.known(run_id)),
        subject=replace(base.subject, ticker=IdentityDimension.known(str(ticker).upper())),
        configuration=ConfigurationIdentity(na),
        algorithm=AlgorithmIdentity(na, na, na, na, na),
        source_lineage=IdentityDimension.known(lineage),
        generation=GenerationIdentity(na, na, na, na),
    )


def _handoff_identity(
    cutoff: MarketCalculationCutoff,
    *,
    run_id: int,
    pipeline_id: int,
    manifest_fingerprint: str,
) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=run_id,
        pipeline_id=pipeline_id,
    )
    reference = SourceArtifactReference(
        "TransitionDecisionHandoffManifest",
        f"sha256:{manifest_fingerprint}",
        na,
        IdentityDimension.known(
            DigestIdentity(
                "sha256", manifest_fingerprint, "complete immutable Handoff manifest"
            )
        ),
    )
    return replace(
        base,
        configuration=ConfigurationIdentity(na),
        algorithm=AlgorithmIdentity(
            IdentityDimension.known(
                VersionIdentity("winner-decision-handoff", WINNER_HANDOFF_CONTRACT)
            ),
            na,
            na,
            na,
            na,
        ),
        source_lineage=IdentityDimension.known(
            SourceLineageIdentity(
                (reference,),
                IdentityDimension.known(
                    DigestIdentity(
                        "sha256", manifest_fingerprint, "complete Handoff fingerprint"
                    )
                ),
                "validated immutable Decision Handoff",
            )
        ),
    )


def _cutoff_from_handoff(payload: dict[str, Any]) -> MarketCalculationCutoff:
    try:
        cutoff_at = _datetime(payload["cutoff_at"])
        daily_ready = payload.get("daily_bar_ready_at")
        return MarketCalculationCutoff(
            cutoff_at=cutoff_at,
            exchange_timezone=str(payload["exchange_timezone"]),
            latest_completed_session=_date(payload["latest_completed_session"]),
            daily_bar_ready_at=_datetime(daily_ready) if daily_ready is not None else None,
            calendar_version=str(payload["calendar_version"]),
            bar_readiness_version=str(payload["bar_readiness_version"]),
            cutoff_reason=str(payload["cutoff_reason"]),
            context_id=int(payload["id"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WinnerCalculationIdentityError(
            f"Winner Decision Handoff market context is incomplete: {exc}"
        ) from exc


def semantic_artifact_identity(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    excluded = {"created_at", "updated_at", "last_seen_at", "calculated_at"}
    payload = {
        column.name: getattr(row, column.name, None)
        for column in row.__table__.columns
        if column.name not in excluded
    }
    return {
        "id": getattr(row, "id", None),
        "semantic_hash": CanonicalEvidenceSerializer.fingerprint(payload),
    }


def _artifact_matches(row: Any | None, expected: Any) -> bool:
    return isinstance(expected, dict) and semantic_artifact_identity(row) == {
        "id": expected.get("id"),
        "semantic_hash": expected.get("semantic_hash"),
    }


def _ticker_lineage(handoff: Any, ticker: str) -> dict[str, Any]:
    artifacts = (handoff.manifest_json or {}).get("artifact_lineage") or {}
    lineage = artifacts.get(ticker)
    if not isinstance(lineage, dict):
        raise WinnerCalculationIdentityError(
            f"Winner Decision Handoff has no artifact lineage for {ticker}"
        )
    return lineage


def _candidates(run_context: Any, plural: str, singular: str) -> tuple[Any, ...]:
    values = tuple(getattr(run_context, plural, ()) or ())
    if values:
        return values
    value = getattr(run_context, singular, None)
    return (value,) if value is not None else ()


def _datetime(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
