from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any

from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.calculation_identity import (
    AlgorithmIdentity,
    CalculationIdentity,
    CalculationIdentityCompatibilityProfile,
    CalculationIdentityCompatibilityStatus,
    CalculationIdentityCompatibilityValidator,
    CompatibilityDimensionRule,
    ConfigurationCoverage,
    ConfigurationIdentity,
    DigestIdentity,
    EffectiveConfigurationIdentity,
    GenerationIdentity,
    IdentityDimension,
    IdentityState,
    SourceArtifactReference,
    SourceLineageIdentity,
    VersionIdentity,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ib_market_intelligence.config import IBMarketIntelligenceConfig
from app.services.market_calculation_context_service import (
    calculation_identity_from_market_context,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.ranking_profile_config import RankingProfileConfig

CALCULATION_IDENTITY_KEY = "calculation_identity"
CALCULATION_IDENTITY_FINGERPRINT_KEY = "calculation_identity_fingerprint"
CALCULATION_IDENTITY_POLICY_KEY = "calculation_identity_policy"


COMBINED_INPUT_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "COMBINED_INPUT_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path)
        for path in (
            "ownership.run_id",
            "ownership.pipeline_id",
            "subject.ticker",
            "calculation_context.market_calculation_context_id",
            "calculation_context.context_fingerprint",
            "temporal.as_of_session",
            "temporal.calculation_cutoff",
            "temporal.calendar",
        )
    ),
)

RANKING_INPUT_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "RANKING_INPUT_COMPATIBILITY",
    COMBINED_INPUT_COMPATIBILITY.rules,
)

RANKING_IBMI_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "RANKING_IBMI_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path, allow_not_applicable=allow_na)
        for path, allow_na in (
            ("ownership.run_id", True),
            ("ownership.pipeline_id", True),
            ("subject.ticker", False),
            ("calculation_context.market_calculation_context_id", True),
            ("calculation_context.context_fingerprint", True),
            ("temporal.as_of_session", False),
            ("temporal.calendar", False),
            ("configuration.effective_configuration", False),
            ("algorithm.calculation_version", False),
            ("algorithm.engine_version", False),
        )
    ),
)


class CalculationIdentityAdoptionStatus(StrEnum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    INSUFFICIENT_IDENTITY = "INSUFFICIENT_IDENTITY"


@dataclass(frozen=True)
class CalculationIdentityAdoptionResult:
    status: CalculationIdentityAdoptionStatus
    policy: str
    diagnostics: tuple[str, ...]
    left_fingerprint: str | None
    right_fingerprint: str | None

    @property
    def accepted(self) -> bool:
        return self.status is CalculationIdentityAdoptionStatus.COMPATIBLE


class CalculationIdentityAdoptionError(ValueError):
    def __init__(
        self,
        *,
        consumer: str,
        producer: str,
        result: CalculationIdentityAdoptionResult,
    ) -> None:
        self.consumer = consumer
        self.producer = producer
        self.result = result
        detail = "; ".join(result.diagnostics) or "identity compatibility was not proven"
        super().__init__(
            "CALCULATION_IDENTITY_REJECTED: "
            f"consumer={consumer} producer={producer} policy={result.policy} "
            f"result={result.status.value} details={detail}"
        )


def embed_calculation_identity(
    debug_json: dict[str, Any] | None,
    identity: CalculationIdentity,
    *,
    policy: str,
) -> dict[str, Any]:
    validation = identity.validate()
    if not validation.valid:
        issues = "; ".join(f"{issue.dimension}: {issue.message}" for issue in validation.issues)
        raise ValueError(f"Cannot persist invalid CalculationIdentity: {issues}")
    return {
        **(debug_json or {}),
        CALCULATION_IDENTITY_KEY: identity.canonical_payload(),
        CALCULATION_IDENTITY_FINGERPRINT_KEY: str(identity.fingerprint()),
        CALCULATION_IDENTITY_POLICY_KEY: policy,
    }


def calculation_identity_from_debug(
    debug_json: dict[str, Any] | None,
) -> CalculationIdentity | None:
    payload = (debug_json or {}).get(CALCULATION_IDENTITY_KEY)
    if not isinstance(payload, dict):
        return None
    identity = CalculationIdentity.from_canonical_payload(payload)
    persisted = (debug_json or {}).get(CALCULATION_IDENTITY_FINGERPRINT_KEY)
    if persisted != str(identity.fingerprint()):
        raise ValueError("Persisted CalculationIdentity fingerprint does not match its payload")
    return identity


def legacy_artifact_identity(*, run_id: int, ticker: str) -> CalculationIdentity:
    return CalculationIdentity.legacy_unknown(run_id=run_id, ticker=ticker)


def fundamental_score_identity(score: FundamentalScore) -> CalculationIdentity:
    return calculation_identity_from_debug(score.debug_json) or legacy_artifact_identity(
        run_id=score.run_id,
        ticker=score.ticker,
    )


def technical_score_identity(score: TechnicalScore) -> CalculationIdentity:
    return calculation_identity_from_debug(score.debug_json) or legacy_artifact_identity(
        run_id=score.run_id,
        ticker=score.ticker,
    )


def build_fundamental_score_identity(
    score: FundamentalScore,
    *,
    raw_row: RawCompanyRow,
    market_cutoff: MarketCalculationCutoff,
    pipeline_run_id: int,
) -> CalculationIdentity:
    base = calculation_identity_from_market_context(
        market_cutoff,
        run_id=score.run_id,
        pipeline_id=pipeline_run_id,
        ticker=score.ticker,
    )
    debug = score.debug_json or {}
    config_hash = debug.get("config_hash")
    model_version = score.scoring_model_version or debug.get("model_version")
    return replace(
        base,
        configuration=ConfigurationIdentity(
            _effective_configuration_dimension(
                namespace="fundamental-score",
                digest=config_hash,
                proof_boundary="complete fundamentals_v2.yaml effective scoring configuration",
                resolution_contract="fundamental-config-resolution-v1",
            )
        ),
        algorithm=AlgorithmIdentity(
            calculation_version=_version_dimension("fundamental-calculation", model_version),
            model_version=_version_dimension("fundamental-model", model_version),
            schema_version=IdentityDimension.not_applicable(),
            engine_version=IdentityDimension.not_applicable(),
            components=IdentityDimension.not_applicable(),
        ),
        source_lineage=IdentityDimension.known(
            _source_lineage(
                references=(
                    _source_reference(
                        "RawCompanyRow",
                        getattr(raw_row, "id", None),
                        payload={
                            "run_id": raw_row.run_id,
                            "row_number": raw_row.row_number,
                            "ticker": raw_row.ticker.upper(),
                            "raw_json": raw_row.raw_json,
                        },
                        proof_boundary=(
                            "persisted uploaded raw company row consumed by fundamentals"
                        ),
                    ),
                ),
                proof_boundary="exact raw upload row consumed by FundamentalScore",
            )
        ),
    )


def build_technical_score_identity(
    score: TechnicalScore,
    *,
    market_cutoff: MarketCalculationCutoff,
    pipeline_run_id: int,
    effective_config: dict[str, Any],
) -> CalculationIdentity:
    base = calculation_identity_from_market_context(
        market_cutoff,
        run_id=score.run_id,
        pipeline_id=pipeline_run_id,
        ticker=score.ticker,
    )
    engine_version = score.technical_engine_version
    input_payload = {
        "temporal_lineage": (score.debug_json or {}).get("temporal_lineage"),
        "calculation_context_id": score.calculation_context_id,
        "calculation_cutoff_at": score.calculation_cutoff_at,
        "input_as_of_session": score.input_as_of_session,
        "calendar_version": score.calendar_version,
    }
    return replace(
        base,
        configuration=ConfigurationIdentity(
            _effective_configuration_dimension(
                namespace="technical-score",
                digest=CanonicalEvidenceSerializer.fingerprint(effective_config),
                proof_boundary=(
                    "complete effective technical scoring configuration and semantic flags"
                ),
                resolution_contract="technical-config-resolution-v1",
            )
        ),
        algorithm=AlgorithmIdentity(
            calculation_version=_version_dimension("technical-calculation", engine_version),
            model_version=IdentityDimension.not_applicable(),
            schema_version=IdentityDimension.not_applicable(),
            engine_version=_version_dimension("technical-engine", engine_version),
            components=IdentityDimension.not_applicable(),
        ),
        source_lineage=IdentityDimension.known(
            _source_lineage(
                references=(
                    _source_reference(
                        "TechnicalInputEnvelope",
                        f"{score.run_id}:{score.ticker.upper()}:{market_cutoff.context_id}",
                        payload=input_payload,
                        proof_boundary=(
                            "persisted technical temporal/source-session envelope; does not prove "
                            "underlying price-row immutability"
                        ),
                    ),
                ),
                proof_boundary=(
                    "technical input temporal envelope available on TechnicalScore; detailed "
                    "price revisions remain outside T10B"
                ),
            )
        ),
    )


def validate_source_inputs(
    left: CalculationIdentity,
    right: CalculationIdentity,
    *,
    policy: CalculationIdentityCompatibilityProfile,
) -> CalculationIdentityAdoptionResult:
    comparison = CalculationIdentityCompatibilityValidator.compare(
        left,
        right,
        policy=policy,
    )
    diagnostics = [_compatibility_diagnostic(item) for item in comparison.mismatches]
    for label, identity in (("left", left), ("right", right)):
        diagnostics.extend(_required_source_diagnostics(label, identity))
    if any("UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE" in item for item in diagnostics):
        status = CalculationIdentityAdoptionStatus.INSUFFICIENT_IDENTITY
    elif comparison.status is CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY:
        status = CalculationIdentityAdoptionStatus.INSUFFICIENT_IDENTITY
    elif diagnostics or not comparison.accepted:
        status = CalculationIdentityAdoptionStatus.INCOMPATIBLE
    else:
        status = CalculationIdentityAdoptionStatus.COMPATIBLE
    return CalculationIdentityAdoptionResult(
        status=status,
        policy=policy.name,
        diagnostics=tuple(diagnostics),
        left_fingerprint=comparison.expected_fingerprint,
        right_fingerprint=comparison.actual_fingerprint,
    )


def require_source_inputs(
    left: CalculationIdentity,
    right: CalculationIdentity,
    *,
    policy: CalculationIdentityCompatibilityProfile,
    consumer: str,
    left_producer: str,
    right_producer: str,
) -> CalculationIdentityAdoptionResult:
    result = validate_source_inputs(left, right, policy=policy)
    if not result.accepted:
        raise CalculationIdentityAdoptionError(
            consumer=consumer,
            producer=f"{left_producer}+{right_producer}",
            result=result,
        )
    return result


def require_fundamental_raw_source(
    identity: CalculationIdentity,
    raw_row: RawCompanyRow,
    *,
    consumer: str,
) -> None:
    lineage = identity.source_lineage
    if lineage.state is not IdentityState.KNOWN:
        result = CalculationIdentityAdoptionResult(
            CalculationIdentityAdoptionStatus.INSUFFICIENT_IDENTITY,
            f"{consumer.upper()}_RAW_SOURCE_COMPATIBILITY",
            (
                "producer=FundamentalScore dimension=source_lineage "
                f"actual={lineage.state.value} reason=UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE",
            ),
            str(identity.fingerprint()),
            None,
        )
        raise CalculationIdentityAdoptionError(
            consumer=consumer,
            producer="RawCompanyRow",
            result=result,
        )
    expected = _source_reference(
        "RawCompanyRow",
        getattr(raw_row, "id", None),
        payload={
            "run_id": raw_row.run_id,
            "row_number": raw_row.row_number,
            "ticker": raw_row.ticker.upper(),
            "raw_json": raw_row.raw_json,
        },
        proof_boundary="persisted uploaded raw company row consumed by fundamentals",
    )
    matches = any(
        CanonicalEvidenceSerializer.dumps(reference.as_dict())
        == CanonicalEvidenceSerializer.dumps(expected.as_dict())
        for reference in lineage.value.references
        if reference.artifact_type == "RawCompanyRow"
    )
    if matches:
        return
    result = CalculationIdentityAdoptionResult(
        CalculationIdentityAdoptionStatus.INCOMPATIBLE,
        f"{consumer.upper()}_RAW_SOURCE_COMPATIBILITY",
        (
            "dimension=source_lineage.RawCompanyRow "
            f"expected={expected.artifact_id} actual=not-referenced "
            "reason=KNOWN_VALUES_DIFFER",
        ),
        str(identity.fingerprint()),
        None,
    )
    raise CalculationIdentityAdoptionError(
        consumer=consumer,
        producer="RawCompanyRow",
        result=result,
    )


def build_combined_result_identity(
    *,
    fundamental_identity: CalculationIdentity,
    technical_identity: CalculationIdentity,
    fundamental_score: FundamentalScore,
    technical_score: TechnicalScore,
    config: dict[str, Any],
    calculation_version: str,
    cohort_fingerprint: str,
) -> CalculationIdentity:
    return _build_result_identity(
        spine=technical_identity,
        namespace="combined-decision",
        config_payload=config,
        config_proof="complete scoring_weights.yaml effective Combined configuration",
        calculation_version=calculation_version,
        source_pairs=(
            ("FundamentalScore", fundamental_score, fundamental_identity),
            ("TechnicalScore", technical_score, technical_identity),
        ),
        cohort_fingerprint=cohort_fingerprint,
        source_boundary="validated FundamentalScore and TechnicalScore input identities",
    )


def build_ranking_result_identity(
    *,
    fundamental_identity: CalculationIdentity,
    technical_identity: CalculationIdentity,
    fundamental_score: FundamentalScore,
    technical_score: TechnicalScore,
    profile: RankingProfileConfig,
    global_config: dict[str, Any],
    calculation_version: str,
    cohort_fingerprint: str,
    liquidity_feature: IBIntelligenceFeature | None,
    liquidity_identity: CalculationIdentity | None,
) -> CalculationIdentity:
    sources: list[tuple[str, Any, CalculationIdentity]] = [
        ("FundamentalScore", fundamental_score, fundamental_identity),
        ("TechnicalScore", technical_score, technical_identity),
    ]
    if liquidity_feature is not None and liquidity_identity is not None:
        sources.append(("IBIntelligenceFeature", liquidity_feature, liquidity_identity))
    config_payload = {
        "profile": asdict(profile),
        "earnings_risk_gate": global_config.get("earnings_risk_gate"),
    }
    return _build_result_identity(
        spine=technical_identity,
        namespace=f"ranking-profile:{profile.name}",
        config_payload=config_payload,
        config_proof="complete effective Ranking profile and shared earnings-risk configuration",
        calculation_version=calculation_version,
        source_pairs=tuple(sources),
        cohort_fingerprint=cohort_fingerprint,
        source_boundary="validated Ranking source identities and profile cohort",
    )


def cohort_identity_fingerprint(identities: Iterable[CalculationIdentity]) -> str:
    return CanonicalEvidenceSerializer.fingerprint(
        sorted(str(identity.fingerprint()) for identity in identities)
    )


def build_ibmi_liquidity_identity(feature: IBIntelligenceFeature) -> CalculationIdentity:
    if feature.evidence_id is not None:
        from app.services.contextual_calculation_identity import build_ibmi_feature_identity

        return build_ibmi_feature_identity(feature)
    not_applicable = IdentityDimension.not_applicable()
    cutoff = (
        IdentityDimension.known(feature.calculation_cutoff_at)
        if feature.calculation_cutoff_at is not None
        else IdentityDimension.unknown()
    )
    from app.services.calculation_identity import (
        CalculationContextIdentity,
        CalculationOwnership,
        CalculationSubject,
        CalendarIdentity,
        TemporalIdentity,
    )

    calendar = (
        IdentityDimension.known(
            CalendarIdentity(
                calendar_id="SWINGLENS_US_EQUITIES",
                calendar_version=feature.calendar_version,
                exchange_timezone="America/New_York",
                bar_readiness_version=not_applicable,
            )
        )
        if feature.calendar_version
        else IdentityDimension.unknown()
    )
    source_hashes = sorted(set(feature.source_evidence_hashes_json or []))
    references = tuple(
        SourceArtifactReference(
            artifact_type="IBMI-source-evidence",
            artifact_id=value,
            revision_id=not_applicable,
            fingerprint=IdentityDimension.known(
                DigestIdentity("sha256", value, "IBMI persisted source evidence hash")
            ),
        )
        for value in source_hashes
    )
    source_lineage = (
        IdentityDimension.known(
            _source_lineage(
                references=references,
                proof_boundary="IBMI liquidity feature persisted source evidence hashes",
            )
        )
        if references
        else IdentityDimension.unknown()
    )
    return CalculationIdentity(
        ownership=CalculationOwnership(not_applicable, not_applicable),
        subject=CalculationSubject(IdentityDimension.known(feature.ticker.upper()), not_applicable),
        calculation_context=CalculationContextIdentity(not_applicable, not_applicable),
        temporal=TemporalIdentity(IdentityDimension.known(feature.as_of_session), cutoff, calendar),
        configuration=ConfigurationIdentity(
            _effective_configuration_dimension(
                namespace="ibmi-liquidity",
                digest=feature.config_hash,
                proof_boundary="complete persisted IBMI effective configuration",
                resolution_contract="ibmi-config-resolution-v1",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version_dimension("ibmi-calculation", feature.calculation_version),
            not_applicable,
            not_applicable,
            _version_dimension("ibmi-source", feature.source_version),
            not_applicable,
        ),
        source_lineage=source_lineage,
        generation=GenerationIdentity(
            not_applicable, not_applicable, not_applicable, not_applicable
        ),
    )


def validate_ibmi_liquidity_for_ranking(
    *,
    feature_identity: CalculationIdentity,
    ranking_spine: CalculationIdentity,
    expected_config: IBMarketIntelligenceConfig,
) -> CalculationIdentityAdoptionResult:
    effective = feature_identity.configuration.effective_configuration
    expected = _expected_ibmi_identity(
        ranking_spine,
        expected_config,
        certified=effective.state is IdentityState.KNOWN
        and effective.value.namespace.startswith("contextual.ibmi."),
    )
    comparison = CalculationIdentityCompatibilityValidator.compare(
        expected,
        feature_identity,
        policy=RANKING_IBMI_COMPATIBILITY,
    )
    diagnostics = [_compatibility_diagnostic(item) for item in comparison.mismatches]
    diagnostics.extend(_required_source_diagnostics("ibmi", feature_identity))
    expected_cutoff = ranking_spine.temporal.calculation_cutoff
    actual_cutoff = feature_identity.temporal.calculation_cutoff
    if (
        expected_cutoff.state is not IdentityState.KNOWN
        or actual_cutoff.state is not IdentityState.KNOWN
    ):
        diagnostics.append(
            "dimension=temporal.calculation_cutoff reason=UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE"
        )
    elif actual_cutoff.value > expected_cutoff.value:
        diagnostics.append(
            "dimension=temporal.calculation_cutoff "
            f"expected=at-or-before:{expected_cutoff.value.isoformat()} "
            f"actual={actual_cutoff.value.isoformat()} reason=FUTURE_CUTOFF"
        )
    if comparison.status is CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY or any(
        "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE" in item for item in diagnostics
    ):
        status = CalculationIdentityAdoptionStatus.INSUFFICIENT_IDENTITY
    elif diagnostics or not comparison.accepted:
        status = CalculationIdentityAdoptionStatus.INCOMPATIBLE
    else:
        status = CalculationIdentityAdoptionStatus.COMPATIBLE
    return CalculationIdentityAdoptionResult(
        status,
        RANKING_IBMI_COMPATIBILITY.name,
        tuple(diagnostics),
        comparison.expected_fingerprint,
        comparison.actual_fingerprint,
    )


def identity_fingerprint_from_debug(debug_json: dict[str, Any] | None) -> str | None:
    identity = calculation_identity_from_debug(debug_json)
    return str(identity.fingerprint()) if identity is not None else None


def _build_result_identity(
    *,
    spine: CalculationIdentity,
    namespace: str,
    config_payload: dict[str, Any],
    config_proof: str,
    calculation_version: str,
    source_pairs: tuple[tuple[str, Any, CalculationIdentity], ...],
    cohort_fingerprint: str,
    source_boundary: str,
) -> CalculationIdentity:
    references = tuple(
        _identity_source_reference(artifact_type, artifact, identity)
        for artifact_type, artifact, identity in source_pairs
    )
    lineage = SourceLineageIdentity(
        references=references,
        aggregate_fingerprint=IdentityDimension.known(
            DigestIdentity(
                "sha256",
                cohort_fingerprint,
                "complete ordered set of source CalculationIdentity fingerprints in cohort",
            )
        ),
        proof_boundary=source_boundary,
    )
    not_applicable = IdentityDimension.not_applicable()
    return replace(
        spine,
        configuration=ConfigurationIdentity(
            _effective_configuration_dimension(
                namespace=namespace,
                digest=CanonicalEvidenceSerializer.fingerprint(config_payload),
                proof_boundary=config_proof,
                resolution_contract=f"{namespace}-config-resolution-v1",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version_dimension(namespace, calculation_version),
            not_applicable,
            not_applicable,
            _version_dimension(f"{namespace}-engine", calculation_version),
            not_applicable,
        ),
        source_lineage=IdentityDimension.known(lineage),
        generation=GenerationIdentity(
            not_applicable, not_applicable, not_applicable, not_applicable
        ),
    )


def _expected_ibmi_identity(
    ranking_spine: CalculationIdentity,
    config: IBMarketIntelligenceConfig,
    *,
    certified: bool = True,
) -> CalculationIdentity:
    not_applicable = IdentityDimension.not_applicable()
    from app.services.calculation_identity import CalendarIdentity

    ranking_calendar = ranking_spine.temporal.calendar
    calendar = IdentityDimension.unknown()
    if ranking_calendar.state is IdentityState.KNOWN:
        calendar = IdentityDimension.known(
            CalendarIdentity(
                calendar_id=ranking_calendar.value.calendar_id,
                calendar_version=ranking_calendar.value.calendar_version,
                exchange_timezone=ranking_calendar.value.exchange_timezone,
                bar_readiness_version=not_applicable,
            )
        )
    result = replace(
        ranking_spine,
        ownership=replace(
            ranking_spine.ownership,
            run_id=not_applicable,
            pipeline_id=not_applicable,
        ),
        calculation_context=replace(
            ranking_spine.calculation_context,
            market_calculation_context_id=not_applicable,
            context_fingerprint=not_applicable,
        ),
        temporal=replace(ranking_spine.temporal, calendar=calendar),
        configuration=ConfigurationIdentity(
            _effective_configuration_dimension(
                namespace="ibmi-liquidity",
                digest=config.config_hash,
                proof_boundary="complete persisted IBMI effective configuration",
                resolution_contract="ibmi-config-resolution-v1",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version_dimension("ibmi-calculation", config.calculation_version),
            not_applicable,
            not_applicable,
            _version_dimension("ibmi-source", config.source_version),
            not_applicable,
        ),
        source_lineage=not_applicable,
        generation=GenerationIdentity(
            not_applicable, not_applicable, not_applicable, not_applicable
        ),
    )
    from app.services.contextual_effective_configuration import resolve_ibmi_configuration

    return resolve_ibmi_configuration(config, "liquidity").bind(result) if certified else result


def _effective_configuration_dimension(
    *,
    namespace: str,
    digest: Any,
    proof_boundary: str,
    resolution_contract: str,
) -> IdentityDimension[EffectiveConfigurationIdentity]:
    if not isinstance(digest, str) or len(digest) != 64:
        return IdentityDimension.unknown()
    return IdentityDimension.known(
        EffectiveConfigurationIdentity(
            namespace=namespace,
            fingerprint=DigestIdentity("sha256", digest, proof_boundary),
            resolution_contract=VersionIdentity(resolution_contract, "1"),
            coverage=ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION,
        )
    )


def _version_dimension(namespace: str, value: Any) -> IdentityDimension[VersionIdentity]:
    if not isinstance(value, str) or not value.strip():
        return IdentityDimension.unknown()
    return IdentityDimension.known(VersionIdentity(namespace, value.strip()))


def _source_reference(
    artifact_type: str,
    artifact_id: Any,
    *,
    payload: Any,
    proof_boundary: str,
) -> SourceArtifactReference:
    digest = CanonicalEvidenceSerializer.fingerprint(payload)
    return SourceArtifactReference(
        artifact_type=artifact_type,
        artifact_id=str(artifact_id) if artifact_id is not None else f"sha256:{digest}",
        revision_id=IdentityDimension.not_applicable(),
        fingerprint=IdentityDimension.known(DigestIdentity("sha256", digest, proof_boundary)),
    )


def _identity_source_reference(
    artifact_type: str,
    artifact: Any,
    identity: CalculationIdentity,
) -> SourceArtifactReference:
    fingerprint = str(identity.fingerprint())
    artifact_id = getattr(artifact, "id", None)
    config = identity.configuration.effective_configuration
    if (
        config.state is IdentityState.KNOWN
        and config.value.namespace.startswith("core.")
        and getattr(artifact, "evidence_id", None) is not None
    ):
        artifact_id = f"evidence:{artifact.evidence_id}"
    return SourceArtifactReference(
        artifact_type=artifact_type,
        artifact_id=str(artifact_id) if artifact_id is not None else f"identity:{fingerprint}",
        revision_id=IdentityDimension.known(fingerprint),
        fingerprint=IdentityDimension.known(
            DigestIdentity(
                "sha256",
                fingerprint,
                f"complete supplied CalculationIdentity envelope for {artifact_type}",
            )
        ),
    )


def _source_lineage(
    *,
    references: tuple[SourceArtifactReference, ...],
    proof_boundary: str,
) -> SourceLineageIdentity:
    aggregate = CanonicalEvidenceSerializer.fingerprint(
        [reference.as_dict() for reference in references]
    )
    return SourceLineageIdentity(
        references=references,
        aggregate_fingerprint=IdentityDimension.known(
            DigestIdentity("sha256", aggregate, proof_boundary)
        ),
        proof_boundary=proof_boundary,
    )


def _required_source_diagnostics(
    label: str,
    identity: CalculationIdentity,
) -> list[str]:
    required = {
        "configuration.effective_configuration": identity.configuration.effective_configuration,
        "algorithm.calculation_version": identity.algorithm.calculation_version,
        "source_lineage": identity.source_lineage,
    }
    diagnostics: list[str] = []
    for dimension, value in required.items():
        if value.state is IdentityState.KNOWN:
            continue
        reason = (
            "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE"
            if value.state in {IdentityState.UNKNOWN, IdentityState.LEGACY_UNKNOWN}
            else "NOT_APPLICABLE_NOT_PERMITTED"
        )
        diagnostics.append(
            f"producer={label} dimension={dimension} actual={value.state.value} reason={reason}"
        )
    return diagnostics


def _compatibility_diagnostic(mismatch: Any) -> str:
    return (
        f"dimension={mismatch.dimension} expected={mismatch.expected} "
        f"actual={mismatch.actual} reason={mismatch.reason}"
    )
