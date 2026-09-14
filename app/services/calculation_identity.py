from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app.services.canonical_evidence import CanonicalEvidenceSerializer

CALCULATION_IDENTITY_SCHEMA_VERSION = "calculation-identity-v1"

class IdentityState(StrEnum):
    """Evidence state for one identity dimension.

    UNKNOWN and LEGACY_UNKNOWN are deliberately distinct from NOT_APPLICABLE.
    Neither unknown state is evidence that two values agree.
    """

    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


@dataclass(frozen=True)
class IdentityDimension[T]:
    state: IdentityState
    value: T | None = None

    def __post_init__(self) -> None:
        if self.state is IdentityState.KNOWN and self.value is None:
            raise ValueError("a KNOWN identity dimension requires a value")
        if self.state is not IdentityState.KNOWN and self.value is not None:
            raise ValueError(f"{self.state.value} identity dimensions cannot carry a value")

    @classmethod
    def known(cls, value: T) -> IdentityDimension[T]:
        return cls(IdentityState.KNOWN, value)

    @classmethod
    def unknown(cls) -> IdentityDimension[T]:
        return cls(IdentityState.UNKNOWN)

    @classmethod
    def not_applicable(cls) -> IdentityDimension[T]:
        return cls(IdentityState.NOT_APPLICABLE)

    @classmethod
    def legacy_unknown(cls) -> IdentityDimension[T]:
        return cls(IdentityState.LEGACY_UNKNOWN)


@dataclass(frozen=True)
class DigestIdentity:
    algorithm: str
    digest: str
    proof_boundary: str

    def as_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "digest": self.digest,
            "proof_boundary": self.proof_boundary,
        }


@dataclass(frozen=True)
class VersionIdentity:
    namespace: str
    version: str

    def as_dict(self) -> dict[str, str]:
        return {"namespace": self.namespace, "version": self.version}


class ConfigurationCoverage(StrEnum):
    COMPLETE_EFFECTIVE_CONFIGURATION = "COMPLETE_EFFECTIVE_CONFIGURATION"
    PARTIAL_DEBUG = "PARTIAL_DEBUG"


@dataclass(frozen=True)
class EffectiveConfigurationIdentity:
    """Attestation that a digest covers a fully resolved effective configuration."""

    namespace: str
    fingerprint: DigestIdentity
    resolution_contract: VersionIdentity
    coverage: ConfigurationCoverage

    def as_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "fingerprint": self.fingerprint.as_dict(),
            "resolution_contract": self.resolution_contract.as_dict(),
            "coverage": self.coverage.value,
        }


@dataclass(frozen=True)
class CalendarIdentity:
    calendar_id: str
    calendar_version: str
    exchange_timezone: str
    bar_readiness_version: IdentityDimension[VersionIdentity]

    def as_dict(self) -> dict[str, Any]:
        return {
            "calendar_id": self.calendar_id,
            "calendar_version": self.calendar_version,
            "exchange_timezone": self.exchange_timezone,
            "bar_readiness_version": _dimension_payload(
                self.bar_readiness_version, lambda value: value.as_dict()
            ),
        }


@dataclass(frozen=True)
class SourceArtifactReference:
    artifact_type: str
    artifact_id: str
    revision_id: IdentityDimension[str]
    fingerprint: IdentityDimension[DigestIdentity]

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "artifact_id": self.artifact_id,
            "revision_id": _dimension_payload(self.revision_id),
            "fingerprint": _dimension_payload(
                self.fingerprint, lambda value: value.as_dict()
            ),
        }


@dataclass(frozen=True)
class SourceLineageIdentity:
    references: tuple[SourceArtifactReference, ...]
    aggregate_fingerprint: IdentityDimension[DigestIdentity]
    proof_boundary: str

    def as_dict(self) -> dict[str, Any]:
        references = [reference.as_dict() for reference in self.references]
        references.sort(key=CanonicalEvidenceSerializer.dumps)
        return {
            "references": references,
            "aggregate_fingerprint": _dimension_payload(
                self.aggregate_fingerprint, lambda value: value.as_dict()
            ),
            "proof_boundary": self.proof_boundary,
        }


@dataclass(frozen=True)
class ExecutionIdentity:
    """Operational ownership/attempt identity; never accepted by compatibility APIs."""

    run_id: int | None = None
    pipeline_run_id: int | None = None
    background_job_id: int | None = None
    root_job_id: int | None = None
    execution_token: str | None = None
    worker_attempt: int | None = None


@dataclass(frozen=True)
class CalculationOwnership:
    """Stable ownership references copied into a semantic identity envelope."""

    run_id: IdentityDimension[int]
    pipeline_id: IdentityDimension[int]


@dataclass(frozen=True)
class CalculationSubject:
    ticker: IdentityDimension[str]
    company_id: IdentityDimension[int]


@dataclass(frozen=True)
class CalculationContextIdentity:
    market_calculation_context_id: IdentityDimension[int]
    context_fingerprint: IdentityDimension[DigestIdentity]


@dataclass(frozen=True)
class TemporalIdentity:
    as_of_session: IdentityDimension[date]
    calculation_cutoff: IdentityDimension[datetime]
    calendar: IdentityDimension[CalendarIdentity]


@dataclass(frozen=True)
class ConfigurationIdentity:
    effective_configuration: IdentityDimension[EffectiveConfigurationIdentity]


@dataclass(frozen=True)
class AlgorithmIdentity:
    calculation_version: IdentityDimension[VersionIdentity]
    model_version: IdentityDimension[VersionIdentity]
    schema_version: IdentityDimension[VersionIdentity]
    engine_version: IdentityDimension[VersionIdentity]
    components: IdentityDimension[tuple[VersionIdentity, ...]]


@dataclass(frozen=True)
class GenerationIdentity:
    generation_id: IdentityDimension[str]
    generation_key: IdentityDimension[str]
    generation_watermark: IdentityDimension[DigestIdentity]
    generation_cutoff: IdentityDimension[datetime]


@dataclass(frozen=True)
class IdentityValidationIssue:
    dimension: str
    message: str


@dataclass(frozen=True)
class CalculationIdentityValidation:
    issues: tuple[IdentityValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def require_valid(self) -> None:
        if self.issues:
            rendered = "; ".join(
                f"{issue.dimension}: {issue.message}" for issue in self.issues
            )
            raise CalculationIdentityValidationError(rendered)


class CalculationIdentityValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CalculationIdentityFingerprint:
    value: str
    algorithm: str = "sha256"
    schema_version: str = CALCULATION_IDENTITY_SCHEMA_VERSION

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class CalculationIdentity:
    """Typed semantic identity for one calculation truth.

    Operational attempt fields intentionally cannot be stored here. The envelope
    references stable run/pipeline ownership but never worker tokens or job attempts.
    """

    ownership: CalculationOwnership
    subject: CalculationSubject
    calculation_context: CalculationContextIdentity
    temporal: TemporalIdentity
    configuration: ConfigurationIdentity
    algorithm: AlgorithmIdentity
    source_lineage: IdentityDimension[SourceLineageIdentity]
    generation: GenerationIdentity
    schema_version: str = CALCULATION_IDENTITY_SCHEMA_VERSION

    def validate(self) -> CalculationIdentityValidation:
        return CalculationIdentityValidator.validate(self)

    def canonical_payload(self) -> dict[str, Any]:
        self.validate().require_valid()
        components = self.algorithm.components
        return CanonicalEvidenceSerializer.canonicalize(
            {
                "schema_version": self.schema_version,
                "ownership": {
                    "run_id": _dimension_payload(self.ownership.run_id),
                    "pipeline_id": _dimension_payload(self.ownership.pipeline_id),
                },
                "subject": {
                    "ticker": _dimension_payload(self.subject.ticker),
                    "company_id": _dimension_payload(self.subject.company_id),
                },
                "calculation_context": {
                    "market_calculation_context_id": _dimension_payload(
                        self.calculation_context.market_calculation_context_id
                    ),
                    "context_fingerprint": _dimension_payload(
                        self.calculation_context.context_fingerprint,
                        lambda value: value.as_dict(),
                    ),
                },
                "temporal": {
                    "as_of_session": _dimension_payload(self.temporal.as_of_session),
                    "calculation_cutoff": _dimension_payload(
                        self.temporal.calculation_cutoff
                    ),
                    "calendar": _dimension_payload(
                        self.temporal.calendar, lambda value: value.as_dict()
                    ),
                },
                "configuration": {
                    "effective_configuration": _dimension_payload(
                        self.configuration.effective_configuration,
                        lambda value: value.as_dict(),
                    )
                },
                "algorithm": {
                    "calculation_version": _dimension_payload(
                        self.algorithm.calculation_version,
                        lambda value: value.as_dict(),
                    ),
                    "model_version": _dimension_payload(
                        self.algorithm.model_version, lambda value: value.as_dict()
                    ),
                    "schema_version": _dimension_payload(
                        self.algorithm.schema_version, lambda value: value.as_dict()
                    ),
                    "engine_version": _dimension_payload(
                        self.algorithm.engine_version, lambda value: value.as_dict()
                    ),
                    "components": _dimension_payload(
                        components,
                        lambda values: sorted(
                            (value.as_dict() for value in values),
                            key=CanonicalEvidenceSerializer.dumps,
                        ),
                    ),
                },
                "source_lineage": _dimension_payload(
                    self.source_lineage, lambda value: value.as_dict()
                ),
                "generation": {
                    "generation_id": _dimension_payload(self.generation.generation_id),
                    "generation_key": _dimension_payload(self.generation.generation_key),
                    "generation_watermark": _dimension_payload(
                        self.generation.generation_watermark,
                        lambda value: value.as_dict(),
                    ),
                    "generation_cutoff": _dimension_payload(
                        self.generation.generation_cutoff
                    ),
                },
            }
        )

    def canonical_json(self) -> str:
        return CanonicalEvidenceSerializer.dumps(self.canonical_payload())

    def fingerprint(self) -> CalculationIdentityFingerprint:
        return CalculationIdentityFingerprint(
            CanonicalEvidenceSerializer.fingerprint(self.canonical_payload())
        )

    @classmethod
    def from_canonical_payload(cls, payload: dict[str, Any]) -> CalculationIdentity:
        if payload.get("schema_version") != CALCULATION_IDENTITY_SCHEMA_VERSION:
            raise CalculationIdentityValidationError("unsupported calculation identity schema")
        try:
            ownership = payload["ownership"]
            subject = payload["subject"]
            context = payload["calculation_context"]
            temporal = payload["temporal"]
            configuration = payload["configuration"]
            algorithm = payload["algorithm"]
            generation = payload["generation"]
            identity = cls(
                ownership=CalculationOwnership(
                    run_id=_parse_dimension(ownership["run_id"], _parse_int),
                    pipeline_id=_parse_dimension(ownership["pipeline_id"], _parse_int),
                ),
                subject=CalculationSubject(
                    ticker=_parse_dimension(subject["ticker"], _parse_text),
                    company_id=_parse_dimension(subject["company_id"], _parse_int),
                ),
                calculation_context=CalculationContextIdentity(
                    market_calculation_context_id=_parse_dimension(
                        context["market_calculation_context_id"], _parse_int
                    ),
                    context_fingerprint=_parse_dimension(
                        context["context_fingerprint"], _parse_digest
                    ),
                ),
                temporal=TemporalIdentity(
                    as_of_session=_parse_dimension(temporal["as_of_session"], _parse_date),
                    calculation_cutoff=_parse_dimension(
                        temporal["calculation_cutoff"], _parse_datetime
                    ),
                    calendar=_parse_dimension(temporal["calendar"], _parse_calendar),
                ),
                configuration=ConfigurationIdentity(
                    effective_configuration=_parse_dimension(
                        configuration["effective_configuration"], _parse_effective_config
                    )
                ),
                algorithm=AlgorithmIdentity(
                    calculation_version=_parse_dimension(
                        algorithm["calculation_version"], _parse_version
                    ),
                    model_version=_parse_dimension(algorithm["model_version"], _parse_version),
                    schema_version=_parse_dimension(
                        algorithm["schema_version"], _parse_version
                    ),
                    engine_version=_parse_dimension(
                        algorithm["engine_version"], _parse_version
                    ),
                    components=_parse_dimension(
                        algorithm["components"],
                        lambda values: tuple(_parse_version(value) for value in values),
                    ),
                ),
                source_lineage=_parse_dimension(
                    payload["source_lineage"], _parse_source_lineage
                ),
                generation=GenerationIdentity(
                    generation_id=_parse_dimension(generation["generation_id"], _parse_text),
                    generation_key=_parse_dimension(generation["generation_key"], _parse_text),
                    generation_watermark=_parse_dimension(
                        generation["generation_watermark"], _parse_digest
                    ),
                    generation_cutoff=_parse_dimension(
                        generation["generation_cutoff"], _parse_datetime
                    ),
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CalculationIdentityValidationError(
                f"malformed calculation identity payload: {exc}"
            ) from exc
        identity.validate().require_valid()
        return identity

    @classmethod
    def legacy_unknown(
        cls,
        *,
        run_id: int | None = None,
        ticker: str | None = None,
    ) -> CalculationIdentity:
        legacy: IdentityDimension[Any] = IdentityDimension.legacy_unknown()
        return cls(
            ownership=CalculationOwnership(
                run_id=IdentityDimension.known(run_id) if run_id is not None else legacy,
                pipeline_id=legacy,
            ),
            subject=CalculationSubject(
                ticker=IdentityDimension.known(ticker.upper()) if ticker else legacy,
                company_id=legacy,
            ),
            calculation_context=CalculationContextIdentity(legacy, legacy),
            temporal=TemporalIdentity(legacy, legacy, legacy),
            configuration=ConfigurationIdentity(legacy),
            algorithm=AlgorithmIdentity(legacy, legacy, legacy, legacy, legacy),
            source_lineage=legacy,
            generation=GenerationIdentity(legacy, legacy, legacy, legacy),
        )


class CalculationIdentityValidator:
    @classmethod
    def validate(cls, identity: CalculationIdentity) -> CalculationIdentityValidation:
        issues: list[IdentityValidationIssue] = []
        if identity.schema_version != CALCULATION_IDENTITY_SCHEMA_VERSION:
            issues.append(IdentityValidationIssue("schema_version", "unsupported schema version"))

        cls._positive_int(identity.ownership.run_id, "ownership.run_id", issues)
        cls._positive_int(identity.ownership.pipeline_id, "ownership.pipeline_id", issues)
        cls._positive_int(identity.subject.company_id, "subject.company_id", issues)
        cls._positive_int(
            identity.calculation_context.market_calculation_context_id,
            "calculation_context.market_calculation_context_id",
            issues,
        )
        cls._text(identity.subject.ticker, "subject.ticker", issues)
        if (
            identity.subject.ticker.state is IdentityState.KNOWN
            and identity.subject.ticker.value != identity.subject.ticker.value.strip().upper()
        ):
            issues.append(
                IdentityValidationIssue(
                    "subject.ticker", "ticker must use canonical uppercase representation"
                )
            )
        cls._digest(
            identity.calculation_context.context_fingerprint,
            "calculation_context.context_fingerprint",
            issues,
        )
        cls._datetime(identity.temporal.calculation_cutoff, "temporal.calculation_cutoff", issues)
        if identity.temporal.as_of_session.state is IdentityState.KNOWN and (
            not isinstance(identity.temporal.as_of_session.value, date)
            or isinstance(identity.temporal.as_of_session.value, datetime)
        ):
            issues.append(
                IdentityValidationIssue(
                    "temporal.as_of_session", "known value must be a date"
                )
            )
        cls._calendar(identity.temporal.calendar, "temporal.calendar", issues)
        cls._configuration(identity.configuration.effective_configuration, issues)
        for path, dimension in (
            ("algorithm.calculation_version", identity.algorithm.calculation_version),
            ("algorithm.model_version", identity.algorithm.model_version),
            ("algorithm.schema_version", identity.algorithm.schema_version),
            ("algorithm.engine_version", identity.algorithm.engine_version),
        ):
            cls._version(dimension, path, issues)
        if identity.algorithm.components.state is IdentityState.KNOWN:
            components = identity.algorithm.components.value or ()
            if not components:
                issues.append(
                    IdentityValidationIssue(
                        "algorithm.components", "known components cannot be empty"
                    )
                )
            for index, component in enumerate(components):
                cls._version_value(component, f"algorithm.components[{index}]", issues)

        cls._source_lineage(identity.source_lineage, issues)
        cls._text(identity.generation.generation_id, "generation.generation_id", issues)
        cls._text(identity.generation.generation_key, "generation.generation_key", issues)
        cls._digest(
            identity.generation.generation_watermark,
            "generation.generation_watermark",
            issues,
        )
        cls._datetime(identity.generation.generation_cutoff, "generation.generation_cutoff", issues)

        if (
            identity.ownership.pipeline_id.state is IdentityState.KNOWN
            and identity.ownership.run_id.state is not IdentityState.KNOWN
        ):
            issues.append(
                IdentityValidationIssue(
                    "ownership.pipeline_id", "a known pipeline_id requires a known run_id"
                )
            )
        if identity.temporal.as_of_session.state is IdentityState.KNOWN:
            if identity.temporal.calculation_cutoff.state is not IdentityState.KNOWN:
                issues.append(
                    IdentityValidationIssue(
                        "temporal.calculation_cutoff",
                        "a historical/as-of session requires a known cutoff",
                    )
                )
            if identity.temporal.calendar.state is not IdentityState.KNOWN:
                issues.append(
                    IdentityValidationIssue(
                        "temporal.calendar",
                        "a historical/as-of session requires a known calendar identity",
                    )
                )
        if (
            identity.calculation_context.market_calculation_context_id.state
            is IdentityState.KNOWN
            and any(
                dimension.state is not IdentityState.KNOWN
                for dimension in (
                    identity.temporal.as_of_session,
                    identity.temporal.calculation_cutoff,
                    identity.temporal.calendar,
                )
            )
        ):
            issues.append(
                IdentityValidationIssue(
                    "calculation_context.market_calculation_context_id",
                    "a known market context requires a complete temporal identity",
                )
            )

        generation_dimensions = (
            identity.generation.generation_id,
            identity.generation.generation_key,
            identity.generation.generation_watermark,
            identity.generation.generation_cutoff,
        )
        if any(item.state is IdentityState.KNOWN for item in generation_dimensions) and any(
            item.state is not IdentityState.KNOWN for item in generation_dimensions
        ):
            issues.append(
                IdentityValidationIssue(
                    "generation",
                    "generation identity must provide id, key, watermark, and cutoff together",
                )
            )
        return CalculationIdentityValidation(tuple(issues))

    @staticmethod
    def _positive_int(
        dimension: IdentityDimension[Any], path: str, issues: list[IdentityValidationIssue]
    ) -> None:
        if dimension.state is IdentityState.KNOWN and (
            not isinstance(dimension.value, int)
            or isinstance(dimension.value, bool)
            or dimension.value <= 0
        ):
            issues.append(IdentityValidationIssue(path, "known value must be a positive integer"))

    @staticmethod
    def _text(
        dimension: IdentityDimension[Any], path: str, issues: list[IdentityValidationIssue]
    ) -> None:
        if dimension.state is IdentityState.KNOWN and (
            not isinstance(dimension.value, str) or not dimension.value.strip()
        ):
            issues.append(IdentityValidationIssue(path, "known value must be non-empty text"))

    @staticmethod
    def _datetime(
        dimension: IdentityDimension[Any], path: str, issues: list[IdentityValidationIssue]
    ) -> None:
        if dimension.state is not IdentityState.KNOWN:
            return
        value = dimension.value
        if not isinstance(value, datetime):
            issues.append(IdentityValidationIssue(path, "known value must be a datetime"))
        elif value.tzinfo is None or value.utcoffset() is None:
            issues.append(IdentityValidationIssue(path, "timestamp must be timezone-aware"))

    @classmethod
    def _digest(
        cls,
        dimension: IdentityDimension[Any],
        path: str,
        issues: list[IdentityValidationIssue],
    ) -> None:
        if dimension.state is IdentityState.KNOWN:
            cls._digest_value(dimension.value, path, issues)

    @staticmethod
    def _digest_value(value: Any, path: str, issues: list[IdentityValidationIssue]) -> None:
        if not isinstance(value, DigestIdentity):
            issues.append(IdentityValidationIssue(path, "known value must be a DigestIdentity"))
            return
        if value.algorithm != "sha256":
            issues.append(IdentityValidationIssue(path, "only sha256 is supported"))
        if len(value.digest) != 64 or any(char not in "0123456789abcdef" for char in value.digest):
            issues.append(IdentityValidationIssue(path, "sha256 digest must be 64 lowercase hex"))
        if not value.proof_boundary.strip():
            issues.append(IdentityValidationIssue(path, "proof_boundary must be declared"))

    @classmethod
    def _version(
        cls,
        dimension: IdentityDimension[Any],
        path: str,
        issues: list[IdentityValidationIssue],
    ) -> None:
        if dimension.state is IdentityState.KNOWN:
            cls._version_value(dimension.value, path, issues)

    @staticmethod
    def _version_value(value: Any, path: str, issues: list[IdentityValidationIssue]) -> None:
        if not isinstance(value, VersionIdentity):
            issues.append(IdentityValidationIssue(path, "known value must be a VersionIdentity"))
            return
        for name, text in (("namespace", value.namespace), ("version", value.version)):
            if (
                not isinstance(text, str)
                or not text.strip()
                or any(char.isspace() for char in text)
            ):
                issues.append(
                    IdentityValidationIssue(path, f"{name} must be non-empty without whitespace")
                )

    @classmethod
    def _calendar(
        cls,
        dimension: IdentityDimension[Any],
        path: str,
        issues: list[IdentityValidationIssue],
    ) -> None:
        if dimension.state is not IdentityState.KNOWN:
            return
        value = dimension.value
        if not isinstance(value, CalendarIdentity):
            issues.append(IdentityValidationIssue(path, "known value must be a CalendarIdentity"))
            return
        for name, text in (
            ("calendar_id", value.calendar_id),
            ("calendar_version", value.calendar_version),
            ("exchange_timezone", value.exchange_timezone),
        ):
            if not isinstance(text, str) or not text.strip():
                issues.append(IdentityValidationIssue(path, f"{name} must be non-empty"))
        cls._version(value.bar_readiness_version, f"{path}.bar_readiness_version", issues)

    @classmethod
    def _configuration(
        cls,
        dimension: IdentityDimension[Any],
        issues: list[IdentityValidationIssue],
    ) -> None:
        path = "configuration.effective_configuration"
        if dimension.state is not IdentityState.KNOWN:
            return
        value = dimension.value
        if not isinstance(value, EffectiveConfigurationIdentity):
            issues.append(
                IdentityValidationIssue(path, "known value must attest effective configuration")
            )
            return
        if not value.namespace.strip():
            issues.append(IdentityValidationIssue(path, "namespace must be non-empty"))
        cls._digest_value(value.fingerprint, f"{path}.fingerprint", issues)
        cls._version_value(value.resolution_contract, f"{path}.resolution_contract", issues)
        if value.coverage is not ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION:
            issues.append(
                IdentityValidationIssue(
                    path,
                    "effective configuration requires COMPLETE_EFFECTIVE_CONFIGURATION coverage",
                )
            )

    @classmethod
    def _source_lineage(
        cls,
        dimension: IdentityDimension[Any],
        issues: list[IdentityValidationIssue],
    ) -> None:
        path = "source_lineage"
        if dimension.state is not IdentityState.KNOWN:
            return
        value = dimension.value
        if not isinstance(value, SourceLineageIdentity):
            issues.append(
                IdentityValidationIssue(path, "known value must be a SourceLineageIdentity")
            )
            return
        if not value.proof_boundary.strip():
            issues.append(IdentityValidationIssue(path, "proof_boundary must be declared"))
        if not value.references and value.aggregate_fingerprint.state is not IdentityState.KNOWN:
            issues.append(
                IdentityValidationIssue(path, "known lineage requires references or a fingerprint")
            )
        canonical_refs: set[str] = set()
        for index, reference in enumerate(value.references):
            ref_path = f"{path}.references[{index}]"
            if not reference.artifact_type.strip() or not reference.artifact_id.strip():
                issues.append(
                    IdentityValidationIssue(ref_path, "artifact type and id must be non-empty")
                )
            cls._text(reference.revision_id, f"{ref_path}.revision_id", issues)
            cls._digest(reference.fingerprint, f"{ref_path}.fingerprint", issues)
            rendered = CanonicalEvidenceSerializer.dumps(reference.as_dict())
            if rendered in canonical_refs:
                issues.append(IdentityValidationIssue(ref_path, "duplicate source reference"))
            canonical_refs.add(rendered)
        cls._digest(value.aggregate_fingerprint, f"{path}.aggregate_fingerprint", issues)


class CalculationIdentityCompatibilityStatus(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    INSUFFICIENT_IDENTITY = "INSUFFICIENT_IDENTITY"


@dataclass(frozen=True)
class CompatibilityDimensionRule:
    dimension: str
    allow_not_applicable: bool = False


@dataclass(frozen=True)
class CalculationIdentityCompatibilityProfile:
    name: str
    rules: tuple[CompatibilityDimensionRule, ...]


@dataclass(frozen=True)
class CalculationIdentityMismatch:
    dimension: str
    expected: Any
    actual: Any
    reason: str


@dataclass(frozen=True)
class CalculationIdentityCompatibility:
    status: CalculationIdentityCompatibilityStatus
    policy: str
    mismatches: tuple[CalculationIdentityMismatch, ...]
    expected_fingerprint: str | None
    actual_fingerprint: str | None

    @property
    def accepted(self) -> bool:
        return self.status in {
            CalculationIdentityCompatibilityStatus.EXACT_MATCH,
            CalculationIdentityCompatibilityStatus.COMPATIBLE,
        }

    def diagnostic(self) -> str:
        if not self.mismatches:
            return f"CALCULATION_IDENTITY_{self.status.value}: policy={self.policy}"
        mismatch = self.mismatches[0]
        return (
            "CALCULATION_IDENTITY_MISMATCH: "
            f"dimension={mismatch.dimension} expected={mismatch.expected} "
            f"actual={mismatch.actual} policy={self.policy} reason={mismatch.reason}"
        )


STRICT_DECISION_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "STRICT_DECISION_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path, allow_not_applicable=allow_na)
        for path, allow_na in (
            ("ownership.run_id", False),
            ("ownership.pipeline_id", False),
            ("subject.ticker", False),
            ("subject.company_id", True),
            ("calculation_context.market_calculation_context_id", False),
            ("calculation_context.context_fingerprint", False),
            ("temporal.as_of_session", False),
            ("temporal.calculation_cutoff", False),
            ("temporal.calendar", False),
            ("configuration.effective_configuration", False),
            ("algorithm.calculation_version", False),
            ("algorithm.model_version", True),
            ("algorithm.schema_version", True),
            ("algorithm.engine_version", True),
            ("algorithm.components", True),
            ("source_lineage", False),
            ("generation.generation_id", True),
            ("generation.generation_key", True),
            ("generation.generation_watermark", True),
            ("generation.generation_cutoff", True),
        )
    ),
)

PIPELINE_CONTEXT_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "PIPELINE_CONTEXT_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path)
        for path in (
            "ownership.run_id",
            "ownership.pipeline_id",
            "calculation_context.market_calculation_context_id",
            "temporal.as_of_session",
            "temporal.calculation_cutoff",
            "temporal.calendar",
        )
    ),
)

TEMPORAL_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "TEMPORAL_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path)
        for path in (
            "calculation_context.market_calculation_context_id",
            "calculation_context.context_fingerprint",
            "temporal.as_of_session",
            "temporal.calculation_cutoff",
            "temporal.calendar",
        )
    ),
)

FEATURE_REUSE_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "FEATURE_REUSE_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path, allow_not_applicable=allow_na)
        for path, allow_na in (
            ("subject.ticker", False),
            ("subject.company_id", True),
            ("temporal.as_of_session", False),
            ("temporal.calculation_cutoff", False),
            ("temporal.calendar", False),
            ("configuration.effective_configuration", False),
            ("algorithm.calculation_version", False),
            ("algorithm.model_version", True),
            ("algorithm.schema_version", True),
            ("algorithm.engine_version", True),
            ("algorithm.components", True),
            ("source_lineage", False),
        )
    ),
)

GENERATION_COMPATIBILITY = CalculationIdentityCompatibilityProfile(
    "GENERATION_COMPATIBILITY",
    tuple(
        CompatibilityDimensionRule(path, allow_not_applicable=allow_na)
        for path, allow_na in (
            ("configuration.effective_configuration", False),
            ("algorithm.calculation_version", False),
            ("algorithm.model_version", True),
            ("algorithm.schema_version", True),
            ("algorithm.engine_version", True),
            ("algorithm.components", True),
            ("source_lineage", False),
            ("generation.generation_id", False),
            ("generation.generation_key", False),
            ("generation.generation_watermark", False),
            ("generation.generation_cutoff", False),
        )
    ),
)


class CalculationIdentityCompatibilityValidator:
    @classmethod
    def compare(
        cls,
        expected: CalculationIdentity,
        actual: CalculationIdentity,
        *,
        policy: CalculationIdentityCompatibilityProfile,
    ) -> CalculationIdentityCompatibility:
        if not isinstance(expected, CalculationIdentity) or not isinstance(
            actual, CalculationIdentity
        ):
            raise TypeError("calculation compatibility requires two CalculationIdentity objects")
        expected_validation = expected.validate()
        actual_validation = actual.validate()
        if not expected_validation.valid or not actual_validation.valid:
            issues = tuple(
                CalculationIdentityMismatch(
                    issue.dimension,
                    "structurally valid identity",
                    issue.message,
                    "STRUCTURAL_VALIDATION_FAILED",
                )
                for issue in (*expected_validation.issues, *actual_validation.issues)
            )
            return CalculationIdentityCompatibility(
                CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY,
                policy.name,
                issues,
                None,
                None,
            )

        expected_fingerprint = str(expected.fingerprint())
        actual_fingerprint = str(actual.fingerprint())
        incompatible: list[CalculationIdentityMismatch] = []
        insufficient: list[CalculationIdentityMismatch] = []
        for rule in policy.rules:
            left = _dimension_at(expected, rule.dimension)
            right = _dimension_at(actual, rule.dimension)
            unknown_states = {IdentityState.UNKNOWN, IdentityState.LEGACY_UNKNOWN}
            if left.state in unknown_states or right.state in unknown_states:
                insufficient.append(
                    CalculationIdentityMismatch(
                        rule.dimension,
                        _diagnostic_value(left),
                        _diagnostic_value(right),
                        "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE",
                    )
                )
                continue
            if (
                left.state is IdentityState.NOT_APPLICABLE
                or right.state is IdentityState.NOT_APPLICABLE
            ):
                if (
                    rule.allow_not_applicable
                    and left.state is IdentityState.NOT_APPLICABLE
                    and right.state is IdentityState.NOT_APPLICABLE
                ):
                    continue
                target = insufficient if left.state is right.state else incompatible
                target.append(
                    CalculationIdentityMismatch(
                        rule.dimension,
                        _diagnostic_value(left),
                        _diagnostic_value(right),
                        (
                            "NOT_APPLICABLE_NOT_PERMITTED"
                            if left.state is right.state
                            else "APPLICABILITY_MISMATCH"
                        ),
                    )
                )
                continue
            left_value = _encode_value(left.value)
            right_value = _encode_value(right.value)
            nested_unknowns = (
                *_unknown_dimension_paths(left_value, rule.dimension),
                *_unknown_dimension_paths(right_value, rule.dimension),
            )
            if nested_unknowns:
                insufficient.extend(
                    CalculationIdentityMismatch(
                        path,
                        "KNOWN",
                        state,
                        "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE",
                    )
                    for path, state in nested_unknowns
                )
                continue
            if CanonicalEvidenceSerializer.dumps(left_value) != (
                CanonicalEvidenceSerializer.dumps(right_value)
            ):
                incompatible.append(
                    CalculationIdentityMismatch(
                        rule.dimension,
                        _diagnostic_value(left),
                        _diagnostic_value(right),
                        "KNOWN_VALUES_DIFFER",
                    )
                )

        if incompatible:
            status = CalculationIdentityCompatibilityStatus.INCOMPATIBLE
            mismatches = tuple(incompatible)
        elif insufficient:
            status = CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY
            mismatches = tuple(insufficient)
        elif expected_fingerprint == actual_fingerprint:
            status = CalculationIdentityCompatibilityStatus.EXACT_MATCH
            mismatches = ()
        else:
            status = CalculationIdentityCompatibilityStatus.COMPATIBLE
            mismatches = ()
        return CalculationIdentityCompatibility(
            status,
            policy.name,
            mismatches,
            expected_fingerprint,
            actual_fingerprint,
        )


_DIMENSION_PATHS: dict[str, Any] = {
    "ownership.run_id": lambda value: value.ownership.run_id,
    "ownership.pipeline_id": lambda value: value.ownership.pipeline_id,
    "subject.ticker": lambda value: value.subject.ticker,
    "subject.company_id": lambda value: value.subject.company_id,
    "calculation_context.market_calculation_context_id": (
        lambda value: value.calculation_context.market_calculation_context_id
    ),
    "calculation_context.context_fingerprint": (
        lambda value: value.calculation_context.context_fingerprint
    ),
    "temporal.as_of_session": lambda value: value.temporal.as_of_session,
    "temporal.calculation_cutoff": lambda value: value.temporal.calculation_cutoff,
    "temporal.calendar": lambda value: value.temporal.calendar,
    "configuration.effective_configuration": (
        lambda value: value.configuration.effective_configuration
    ),
    "algorithm.calculation_version": lambda value: value.algorithm.calculation_version,
    "algorithm.model_version": lambda value: value.algorithm.model_version,
    "algorithm.schema_version": lambda value: value.algorithm.schema_version,
    "algorithm.engine_version": lambda value: value.algorithm.engine_version,
    "algorithm.components": lambda value: value.algorithm.components,
    "source_lineage": lambda value: value.source_lineage,
    "generation.generation_id": lambda value: value.generation.generation_id,
    "generation.generation_key": lambda value: value.generation.generation_key,
    "generation.generation_watermark": lambda value: value.generation.generation_watermark,
    "generation.generation_cutoff": lambda value: value.generation.generation_cutoff,
}


def _dimension_at(identity: CalculationIdentity, path: str) -> IdentityDimension[Any]:
    try:
        return _DIMENSION_PATHS[path](identity)
    except KeyError as exc:
        raise ValueError(f"unknown compatibility dimension: {path}") from exc


def _dimension_payload(
    dimension: IdentityDimension[Any], encoder=lambda value: value
) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": dimension.state.value}
    if dimension.state is IdentityState.KNOWN:
        payload["value"] = encoder(dimension.value)
    return payload


def _encode_value(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, tuple) and all(isinstance(item, VersionIdentity) for item in value):
        return sorted((item.as_dict() for item in value), key=CanonicalEvidenceSerializer.dumps)
    return value


def _diagnostic_value(dimension: IdentityDimension[Any]) -> Any:
    if dimension.state is not IdentityState.KNOWN:
        return dimension.state.value
    return CanonicalEvidenceSerializer.canonicalize(_encode_value(dimension.value))


def _unknown_dimension_paths(value: Any, path: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        state = value.get("state")
        if state in {IdentityState.UNKNOWN.value, IdentityState.LEGACY_UNKNOWN.value}:
            found.append((path, str(state)))
            return tuple(found)
        for key, item in value.items():
            if key != "state":
                found.extend(_unknown_dimension_paths(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_unknown_dimension_paths(item, f"{path}[{index}]"))
    return tuple(found)


def _parse_dimension(payload: dict[str, Any], parser) -> IdentityDimension[Any]:
    state = IdentityState(str(payload["state"]))
    if state is IdentityState.KNOWN:
        return IdentityDimension.known(parser(payload["value"]))
    return IdentityDimension(state)


def _parse_digest(payload: dict[str, Any]) -> DigestIdentity:
    return DigestIdentity(
        algorithm=_parse_text(payload["algorithm"]),
        digest=_parse_text(payload["digest"]),
        proof_boundary=_parse_text(payload["proof_boundary"]),
    )


def _parse_version(payload: dict[str, Any]) -> VersionIdentity:
    return VersionIdentity(
        namespace=_parse_text(payload["namespace"]),
        version=_parse_text(payload["version"]),
    )


def _parse_calendar(payload: dict[str, Any]) -> CalendarIdentity:
    return CalendarIdentity(
        calendar_id=_parse_text(payload["calendar_id"]),
        calendar_version=_parse_text(payload["calendar_version"]),
        exchange_timezone=_parse_text(payload["exchange_timezone"]),
        bar_readiness_version=_parse_dimension(
            payload["bar_readiness_version"], _parse_version
        ),
    )


def _parse_effective_config(payload: dict[str, Any]) -> EffectiveConfigurationIdentity:
    return EffectiveConfigurationIdentity(
        namespace=_parse_text(payload["namespace"]),
        fingerprint=_parse_digest(payload["fingerprint"]),
        resolution_contract=_parse_version(payload["resolution_contract"]),
        coverage=ConfigurationCoverage(str(payload["coverage"])),
    )


def _parse_source_reference(payload: dict[str, Any]) -> SourceArtifactReference:
    return SourceArtifactReference(
        artifact_type=_parse_text(payload["artifact_type"]),
        artifact_id=_parse_text(payload["artifact_id"]),
        revision_id=_parse_dimension(payload["revision_id"], _parse_text),
        fingerprint=_parse_dimension(payload["fingerprint"], _parse_digest),
    )


def _parse_source_lineage(payload: dict[str, Any]) -> SourceLineageIdentity:
    return SourceLineageIdentity(
        references=tuple(_parse_source_reference(value) for value in payload["references"]),
        aggregate_fingerprint=_parse_dimension(
            payload["aggregate_fingerprint"], _parse_digest
        ),
        proof_boundary=_parse_text(payload["proof_boundary"]),
    )


def _parse_text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("expected text")
    return value


def _parse_int(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("expected integer")
    return value


def _parse_date(value: Any) -> date:
    return date.fromisoformat(_parse_text(value))


def _parse_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(_parse_text(value))
