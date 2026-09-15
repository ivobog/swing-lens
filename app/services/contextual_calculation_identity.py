from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MarketCalculationContext
from app.services.calculation_identity import (
    AlgorithmIdentity,
    CalculationContextIdentity,
    CalculationIdentity,
    CalculationIdentityCompatibilityProfile,
    CalculationIdentityCompatibilityStatus,
    CalculationIdentityCompatibilityValidator,
    CalculationOwnership,
    CalculationSubject,
    CalendarIdentity,
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
    TemporalIdentity,
    VersionIdentity,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.combined_ranking_identity import (
    CALCULATION_IDENTITY_FINGERPRINT_KEY,
    CALCULATION_IDENTITY_KEY,
    CALCULATION_IDENTITY_POLICY_KEY,
    CalculationIdentityAdoptionResult,
    CalculationIdentityAdoptionStatus,
    calculation_identity_from_debug,
    embed_calculation_identity,
)
from app.services.market_calculation_context_service import (
    market_calculation_context_fingerprint,
)
from app.services.market_clock_service import MarketCalculationCutoff


def _profile(name: str, *dimensions: tuple[str, bool]) -> CalculationIdentityCompatibilityProfile:
    return CalculationIdentityCompatibilityProfile(
        name,
        tuple(
            CompatibilityDimensionRule(dimension, allow_not_applicable=allow_na)
            for dimension, allow_na in dimensions
        ),
    )


_RUN_CONTEXT_DIMENSIONS = (
    ("ownership.run_id", False),
    ("ownership.pipeline_id", False),
    ("subject.ticker", False),
    ("calculation_context.market_calculation_context_id", False),
    ("calculation_context.context_fingerprint", False),
    ("temporal.as_of_session", False),
    ("temporal.calculation_cutoff", False),
    ("temporal.calendar", False),
)

REGIME_CONTEXT_COMPATIBILITY = _profile(
    "REGIME_CONTEXT_COMPATIBILITY",
    ("temporal.as_of_session", False),
    ("temporal.calculation_cutoff", False),
    ("temporal.calendar", False),
    ("configuration.effective_configuration", False),
    ("algorithm.calculation_version", False),
    ("algorithm.engine_version", False),
)
SECTOR_RANKING_COMPATIBILITY = _profile("SECTOR_RANKING_COMPATIBILITY", *_RUN_CONTEXT_DIMENSIONS)
SECTOR_REGIME_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "SECTOR_REGIME_COMPATIBILITY", REGIME_CONTEXT_COMPATIBILITY.rules
)
SECTOR_PRIOR_COMPATIBILITY = _profile(
    "SECTOR_PRIOR_COMPATIBILITY",
    ("temporal.calendar", False),
    ("configuration.effective_configuration", False),
    ("algorithm.calculation_version", False),
    ("algorithm.engine_version", False),
    ("algorithm.components", False),
)
SETUP_TECHNICAL_COMPATIBILITY = _profile("SETUP_TECHNICAL_COMPATIBILITY", *_RUN_CONTEXT_DIMENSIONS)
SETUP_COMBINED_COMPATIBILITY = _profile("SETUP_COMBINED_COMPATIBILITY", *_RUN_CONTEXT_DIMENSIONS)
SETUP_RANKING_METADATA_COMPATIBILITY = _profile(
    "SETUP_RANKING_METADATA_COMPATIBILITY", *_RUN_CONTEXT_DIMENSIONS
)
SETUP_REGIME_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "SETUP_REGIME_COMPATIBILITY", REGIME_CONTEXT_COMPATIBILITY.rules
)
SETUP_SECTOR_COMPATIBILITY = _profile(
    "SETUP_SECTOR_COMPATIBILITY",
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
CERI_IBMI_COMPATIBILITY = _profile(
    "CERI_IBMI_COMPATIBILITY",
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
IBMI_CONTEXT_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "IBMI_CONTEXT_COMPATIBILITY", CERI_IBMI_COMPATIBILITY.rules
)
CERI_CONTEXT_COMPATIBILITY = _profile("CERI_CONTEXT_COMPATIBILITY", *_RUN_CONTEXT_DIMENSIONS)


def pipeline_id_for_cutoff(
    db: Any,
    *,
    run_id: int,
    market_cutoff: MarketCalculationCutoff,
) -> int | None:
    if market_cutoff.context_id is None or not isinstance(db, Session):
        return None
    row = db.scalar(
        select(MarketCalculationContext).where(
            MarketCalculationContext.id == market_cutoff.context_id,
            MarketCalculationContext.upload_run_id == run_id,
        )
    )
    pipeline_id = getattr(row, "pipeline_run_id", None)
    return int(pipeline_id) if pipeline_id is not None else None


def consumer_context_identity(
    *,
    market_cutoff: MarketCalculationCutoff,
    run_id: int | None,
    pipeline_id: int | None,
    ticker: str | None = None,
    company_id: int | None = None,
    globally_reusable: bool = False,
) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    unknown = IdentityDimension.unknown()
    global_or_none = globally_reusable or run_id is None
    context_available = market_cutoff.context_id is not None and not global_or_none
    return CalculationIdentity(
        ownership=CalculationOwnership(
            na if global_or_none else IdentityDimension.known(run_id),
            (
                na
                if global_or_none
                else IdentityDimension.known(pipeline_id)
                if pipeline_id is not None
                else unknown
            ),
        ),
        subject=CalculationSubject(
            IdentityDimension.known(ticker.upper()) if ticker else na,
            IdentityDimension.known(company_id) if company_id is not None else na,
        ),
        calculation_context=CalculationContextIdentity(
            IdentityDimension.known(market_cutoff.context_id) if context_available else na,
            (
                IdentityDimension.known(
                    DigestIdentity(
                        "sha256",
                        market_calculation_context_fingerprint(market_cutoff),
                        "complete persisted MarketCalculationContext semantics",
                    )
                )
                if context_available
                else na
            ),
        ),
        temporal=TemporalIdentity(
            IdentityDimension.known(market_cutoff.latest_completed_session),
            IdentityDimension.known(market_cutoff.cutoff_at),
            IdentityDimension.known(
                CalendarIdentity(
                    "SWINGLENS_US_EQUITIES",
                    market_cutoff.calendar_version,
                    market_cutoff.exchange_timezone,
                    IdentityDimension.known(
                        VersionIdentity("market-bar-readiness", market_cutoff.bar_readiness_version)
                    ),
                )
            ),
        ),
        configuration=ConfigurationIdentity(na),
        algorithm=AlgorithmIdentity(na, na, na, na, na),
        source_lineage=na,
        generation=GenerationIdentity(na, na, na, na),
    )


def artifact_identity(artifact: Any) -> CalculationIdentity:
    debug_json = getattr(artifact, "debug_json", None)
    persisted_fingerprint = (debug_json or {}).get(CALCULATION_IDENTITY_FINGERPRINT_KEY)
    cached = getattr(artifact, "_calculation_identity_cache", None)
    if cached is not None and cached[0] == persisted_fingerprint:
        return cached[1]
    identity = calculation_identity_from_debug(debug_json)
    if identity is not None:
        try:
            artifact._calculation_identity_cache = (persisted_fingerprint, identity)
        except (AttributeError, TypeError):
            pass
        return identity
    run_id = getattr(artifact, "run_id", None)
    ticker = getattr(artifact, "ticker", None)
    return CalculationIdentity.legacy_unknown(run_id=run_id, ticker=ticker)


def contextual_compatibility(
    expected: CalculationIdentity,
    actual: CalculationIdentity,
    *,
    policy: CalculationIdentityCompatibilityProfile,
    require_source_lineage: bool = True,
    include_fingerprints: bool = True,
) -> CalculationIdentityAdoptionResult:
    comparison = CalculationIdentityCompatibilityValidator.compare(
        expected,
        actual,
        policy=policy,
        include_fingerprints=include_fingerprints,
    )
    diagnostics = [
        (
            f"dimension={item.dimension} expected={item.expected} actual={item.actual} "
            f"reason={item.reason}"
        )
        for item in comparison.mismatches
    ]
    required = {
        "configuration.effective_configuration": actual.configuration.effective_configuration,
        "algorithm.calculation_version": actual.algorithm.calculation_version,
    }
    if require_source_lineage:
        required["source_lineage"] = actual.source_lineage
    for dimension, value in required.items():
        if value.state is IdentityState.KNOWN:
            continue
        reason = (
            "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE"
            if value.state in {IdentityState.UNKNOWN, IdentityState.LEGACY_UNKNOWN}
            else "NOT_APPLICABLE_NOT_PERMITTED"
        )
        diagnostics.append(f"dimension={dimension} actual={value.state.value} reason={reason}")
    insufficient = comparison.status is CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY
    insufficient = insufficient or any(
        "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE" in item for item in diagnostics
    )
    if insufficient:
        status = CalculationIdentityAdoptionStatus.INSUFFICIENT_IDENTITY
    elif diagnostics or not comparison.accepted:
        status = CalculationIdentityAdoptionStatus.INCOMPATIBLE
    else:
        status = CalculationIdentityAdoptionStatus.COMPATIBLE
    return CalculationIdentityAdoptionResult(
        status,
        policy.name,
        tuple(diagnostics),
        comparison.expected_fingerprint,
        comparison.actual_fingerprint,
    )


def ibmi_contextual_compatibility(
    *,
    expected: CalculationIdentity,
    actual: CalculationIdentity,
    policy: CalculationIdentityCompatibilityProfile = CERI_IBMI_COMPATIBILITY,
) -> CalculationIdentityAdoptionResult:
    result = contextual_compatibility(expected, actual, policy=policy)
    diagnostics = list(result.diagnostics)
    expected_cutoff = expected.temporal.calculation_cutoff
    actual_cutoff = actual.temporal.calculation_cutoff
    if (
        expected_cutoff.state is not IdentityState.KNOWN
        or actual_cutoff.state is not IdentityState.KNOWN
    ):
        diagnostics.append(
            "dimension=temporal.calculation_cutoff reason=UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE"
        )
    elif _aware(actual_cutoff.value) > _aware(expected_cutoff.value):
        diagnostics.append(
            "dimension=temporal.calculation_cutoff "
            f"expected=at-or-before:{_aware(expected_cutoff.value).isoformat()} "
            f"actual={_aware(actual_cutoff.value).isoformat()} reason=FUTURE_CUTOFF"
        )
    if any("UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE" in item for item in diagnostics):
        status = CalculationIdentityAdoptionStatus.INSUFFICIENT_IDENTITY
    elif diagnostics:
        status = CalculationIdentityAdoptionStatus.INCOMPATIBLE
    else:
        status = CalculationIdentityAdoptionStatus.COMPATIBLE
    return replace(result, status=status, diagnostics=tuple(diagnostics))


def expected_ibmi_identity(
    *,
    context: CalculationIdentity,
    ticker: str,
    config_hash: str,
    calculation_version: str,
    source_version: str,
    module: str,
) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    calendar = context.temporal.calendar
    if calendar.state is IdentityState.KNOWN:
        calendar = IdentityDimension.known(replace(calendar.value, bar_readiness_version=na))
    return replace(
        context,
        ownership=CalculationOwnership(na, na),
        subject=CalculationSubject(IdentityDimension.known(ticker.upper()), na),
        calculation_context=CalculationContextIdentity(na, na),
        temporal=replace(context.temporal, calendar=calendar),
        configuration=ConfigurationIdentity(
            _effective_config(
                f"ibmi-{module.lower()}",
                config_hash,
                "complete persisted IBMI effective configuration",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version("ibmi-calculation", calculation_version),
            na,
            na,
            _version("ibmi-source", source_version),
            na,
        ),
        source_lineage=na,
    )


def build_ibmi_feature_identity(feature: Any) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    cutoff = getattr(feature, "calculation_cutoff_at", None)
    calendar_version = getattr(feature, "calendar_version", None)
    hashes = sorted(set(getattr(feature, "source_evidence_hashes_json", None) or ()))
    references = tuple(
        SourceArtifactReference(
            "IBMI-source-evidence",
            digest,
            na,
            IdentityDimension.known(
                DigestIdentity("sha256", digest, "IBMI persisted source evidence hash")
            ),
        )
        for digest in hashes
    )
    lineage = (
        IdentityDimension.known(_lineage(references, "IBMI persisted source evidence"))
        if references
        else IdentityDimension.unknown()
    )
    module = str(getattr(feature, "module", "feature")).lower()
    return CalculationIdentity(
        ownership=CalculationOwnership(na, na),
        subject=CalculationSubject(IdentityDimension.known(str(feature.ticker).upper()), na),
        calculation_context=CalculationContextIdentity(na, na),
        temporal=TemporalIdentity(
            IdentityDimension.known(feature.as_of_session),
            (
                IdentityDimension.known(_aware(cutoff))
                if cutoff is not None
                else IdentityDimension.unknown()
            ),
            (
                IdentityDimension.known(
                    CalendarIdentity(
                        "SWINGLENS_US_EQUITIES",
                        calendar_version,
                        "America/New_York",
                        na,
                    )
                )
                if calendar_version
                else IdentityDimension.unknown()
            ),
        ),
        configuration=ConfigurationIdentity(
            _effective_config(
                f"ibmi-{module}",
                getattr(feature, "config_hash", None),
                "complete persisted IBMI effective configuration",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version("ibmi-calculation", getattr(feature, "calculation_version", None)),
            na,
            na,
            _version("ibmi-source", getattr(feature, "source_version", None)),
            na,
        ),
        source_lineage=lineage,
        generation=GenerationIdentity(na, na, na, na),
    )


def expected_regime_identity(
    *, market_cutoff: MarketCalculationCutoff, config: Any
) -> CalculationIdentity:
    base = consumer_context_identity(
        market_cutoff=market_cutoff,
        run_id=None,
        pipeline_id=None,
        globally_reusable=True,
    )
    payload = asdict(config)
    version = str(config.calculation_version)
    return replace(
        base,
        configuration=ConfigurationIdentity(
            _effective_config(
                "market-regime",
                CanonicalEvidenceSerializer.fingerprint(payload),
                "complete effective Market Regime configuration",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version("market-regime-calculation", version),
            IdentityDimension.not_applicable(),
            IdentityDimension.not_applicable(),
            _version("market-regime-engine", version),
            IdentityDimension.not_applicable(),
        ),
    )


def build_regime_identity(
    *,
    market_cutoff: MarketCalculationCutoff,
    config: Any,
    run_id: int | None,
    pipeline_id: int | None,
    source_payload: Any,
) -> CalculationIdentity:
    expected = expected_regime_identity(market_cutoff=market_cutoff, config=config)
    base = consumer_context_identity(
        market_cutoff=market_cutoff,
        run_id=run_id,
        pipeline_id=pipeline_id,
        globally_reusable=run_id is None,
    )
    return replace(
        expected,
        ownership=base.ownership,
        calculation_context=base.calculation_context,
        source_lineage=IdentityDimension.known(
            _payload_lineage(source_payload, "bounded benchmark inputs consumed by Market Regime")
        ),
    )


def expected_sector_identity(
    *,
    context: CalculationIdentity,
    config_hash: str,
    calculation_version: str,
    mode: str,
) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    return replace(
        context,
        subject=CalculationSubject(na, na),
        configuration=ConfigurationIdentity(
            _effective_config(
                "sector-rotation", config_hash, "complete effective Sector Rotation configuration"
            )
        ),
        algorithm=AlgorithmIdentity(
            _version("sector-rotation-calculation", calculation_version),
            na,
            na,
            _version("sector-rotation-engine", calculation_version),
            IdentityDimension.known((VersionIdentity("sector-rotation-mode", mode),)),
        ),
        source_lineage=na,
    )


def build_contextual_result_identity(
    *,
    base: CalculationIdentity,
    namespace: str,
    config_hash: str,
    calculation_version: str,
    engine_version: str,
    source_artifacts: Iterable[tuple[str, Any, CalculationIdentity]],
    source_payload: Any,
    company_id: int | None = None,
) -> CalculationIdentity:
    na = IdentityDimension.not_applicable()
    references = tuple(
        _identity_reference(kind, artifact, identity)
        for kind, artifact, identity in source_artifacts
    )
    payload_reference = _payload_reference(
        f"{namespace}-input-envelope", source_payload, f"{namespace} contextual input envelope"
    )
    lineage = _lineage(
        (*references, payload_reference), f"validated contextual inputs consumed by {namespace}"
    )
    subject = base.subject
    if company_id is not None:
        subject = replace(subject, company_id=IdentityDimension.known(company_id))
    return replace(
        base,
        subject=subject,
        configuration=ConfigurationIdentity(
            _effective_config(
                namespace,
                config_hash,
                f"complete effective {namespace} configuration",
            )
        ),
        algorithm=AlgorithmIdentity(
            _version(f"{namespace}-calculation", calculation_version),
            na,
            na,
            _version(f"{namespace}-engine", engine_version),
            na,
        ),
        source_lineage=IdentityDimension.known(lineage),
        generation=GenerationIdentity(na, na, na, na),
    )


def embed_identity(payload: dict[str, Any] | None, identity: CalculationIdentity, *, policy: str):
    return embed_calculation_identity(payload, identity, policy=policy)


def identity_metadata(identity: CalculationIdentity, *, policy: str) -> dict[str, Any]:
    return {
        CALCULATION_IDENTITY_KEY: identity.canonical_payload(),
        CALCULATION_IDENTITY_FINGERPRINT_KEY: str(identity.fingerprint()),
        CALCULATION_IDENTITY_POLICY_KEY: policy,
    }


def _effective_config(namespace: str, digest: Any, proof: str):
    if not isinstance(digest, str) or len(digest) != 64:
        return IdentityDimension.unknown()
    return IdentityDimension.known(
        EffectiveConfigurationIdentity(
            namespace,
            DigestIdentity("sha256", digest, proof),
            VersionIdentity(f"{namespace}-config-resolution", "1"),
            ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION,
        )
    )


def _version(namespace: str, value: Any):
    if not isinstance(value, str) or not value.strip():
        return IdentityDimension.unknown()
    return IdentityDimension.known(VersionIdentity(namespace, value.strip()))


def _identity_reference(kind: str, artifact: Any, identity: CalculationIdentity):
    fingerprint = str(identity.fingerprint())
    artifact_id = getattr(artifact, "id", None)
    return SourceArtifactReference(
        kind,
        str(artifact_id) if artifact_id is not None else f"identity:{fingerprint}",
        IdentityDimension.known(fingerprint),
        IdentityDimension.known(
            DigestIdentity("sha256", fingerprint, f"complete {kind} CalculationIdentity")
        ),
    )


def _payload_reference(kind: str, payload: Any, proof: str):
    digest = CanonicalEvidenceSerializer.fingerprint(payload)
    return SourceArtifactReference(
        kind,
        f"sha256:{digest}",
        IdentityDimension.not_applicable(),
        IdentityDimension.known(DigestIdentity("sha256", digest, proof)),
    )


def _payload_lineage(payload: Any, proof: str):
    return _lineage((_payload_reference("contextual-source-envelope", payload, proof),), proof)


def _lineage(references: tuple[SourceArtifactReference, ...], proof: str):
    aggregate = CanonicalEvidenceSerializer.fingerprint(
        [reference.as_dict() for reference in references]
    )
    return SourceLineageIdentity(
        references,
        IdentityDimension.known(DigestIdentity("sha256", aggregate, proof)),
        proof,
    )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
