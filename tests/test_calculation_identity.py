from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.calculation_identity import (
    GENERATION_COMPATIBILITY,
    STRICT_DECISION_COMPATIBILITY,
    AlgorithmIdentity,
    CalculationContextIdentity,
    CalculationIdentity,
    CalculationIdentityCompatibilityStatus,
    CalculationIdentityCompatibilityValidator,
    CalculationIdentityValidationError,
    CalculationOwnership,
    CalculationSubject,
    CalendarIdentity,
    ConfigurationCoverage,
    ConfigurationIdentity,
    DigestIdentity,
    EffectiveConfigurationIdentity,
    ExecutionIdentity,
    GenerationIdentity,
    IdentityDimension,
    IdentityState,
    SourceArtifactReference,
    SourceLineageIdentity,
    TemporalIdentity,
    VersionIdentity,
)


def _digest(label: str, boundary: str) -> DigestIdentity:
    return DigestIdentity("sha256", hashlib.sha256(label.encode()).hexdigest(), boundary)


def _version(namespace: str, value: str) -> VersionIdentity:
    return VersionIdentity(namespace, value)


def _source_lineage(*, revision: str = "17", reverse: bool = False) -> SourceLineageIdentity:
    references = (
        SourceArtifactReference(
            "price-series",
            "AAPL:1d:adjusted",
            IdentityDimension.known(revision),
            IdentityDimension.known(_digest("prices", "eligible price-series rows")),
        ),
        SourceArtifactReference(
            "fundamental-score",
            "991",
            IdentityDimension.not_applicable(),
            IdentityDimension.known(_digest("fundamentals", "fundamental score payload")),
        ),
    )
    return SourceLineageIdentity(
        references=tuple(reversed(references)) if reverse else references,
        aggregate_fingerprint=IdentityDimension.known(
            _digest("aggregate", "complete listed source references")
        ),
        proof_boundary="the listed source artifacts, revisions, and fingerprints",
    )


def _identity(
    *,
    cutoff: datetime | None = None,
    source_reverse: bool = False,
    with_generation: bool = False,
) -> CalculationIdentity:
    cutoff = cutoff or datetime(2026, 9, 10, 20, tzinfo=UTC)
    not_applicable = IdentityDimension.not_applicable()
    generation = (
        GenerationIdentity(
            generation_id=IdentityDimension.known("41"),
            generation_key=IdentityDimension.known("winner-generation-41"),
            generation_watermark=IdentityDimension.known(
                _digest("watermark-41", "Winner material evidence watermark")
            ),
            generation_cutoff=IdentityDimension.known(cutoff),
        )
        if with_generation
        else GenerationIdentity(
            not_applicable,
            not_applicable,
            not_applicable,
            not_applicable,
        )
    )
    return CalculationIdentity(
        ownership=CalculationOwnership(
            run_id=IdentityDimension.known(101),
            pipeline_id=IdentityDimension.known(202),
        ),
        subject=CalculationSubject(
            ticker=IdentityDimension.known("AAPL"),
            company_id=IdentityDimension.known(303),
        ),
        calculation_context=CalculationContextIdentity(
            market_calculation_context_id=IdentityDimension.known(404),
            context_fingerprint=IdentityDimension.known(
                _digest("market-context", "complete MarketCalculationContext")
            ),
        ),
        temporal=TemporalIdentity(
            as_of_session=IdentityDimension.known(date(2026, 9, 10)),
            calculation_cutoff=IdentityDimension.known(cutoff),
            calendar=IdentityDimension.known(
                CalendarIdentity(
                    calendar_id="SWINGLENS_US_EQUITIES",
                    calendar_version="swinglens-us-equities-v1",
                    exchange_timezone="America/New_York",
                    bar_readiness_version=IdentityDimension.known(
                        _version("market-bar-readiness", "daily-close-plus-15m-v1")
                    ),
                )
            ),
        ),
        configuration=ConfigurationIdentity(
            IdentityDimension.known(
                EffectiveConfigurationIdentity(
                    namespace="test-calculator",
                    fingerprint=_digest("effective-config", "complete resolved config"),
                    resolution_contract=_version("config-resolution", "v1"),
                    coverage=ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION,
                )
            )
        ),
        algorithm=AlgorithmIdentity(
            calculation_version=IdentityDimension.known(
                _version("calculation", "calc-v2")
            ),
            model_version=IdentityDimension.known(_version("model", "model-v3")),
            schema_version=IdentityDimension.known(_version("schema", "schema-v4")),
            engine_version=IdentityDimension.known(_version("engine", "engine-v5")),
            components=IdentityDimension.known(
                (
                    _version("eligibility-policy", "eligibility-v2"),
                    _version("normalizer", "normalizer-v1"),
                )
            ),
        ),
        source_lineage=IdentityDimension.known(
            _source_lineage(reverse=source_reverse)
        ),
        generation=generation,
    )


def _compare(left: CalculationIdentity, right: CalculationIdentity):
    return CalculationIdentityCompatibilityValidator.compare(
        left,
        right,
        policy=STRICT_DECISION_COMPATIBILITY,
    )


def test_identical_identity_validates_fingerprints_equal_and_is_exact() -> None:
    left = _identity()
    right = _identity()

    assert left.validate().valid
    assert right.validate().valid
    assert left.fingerprint() == right.fingerprint()
    assert _compare(left, right).status is CalculationIdentityCompatibilityStatus.EXACT_MATCH


@pytest.mark.parametrize(
    ("dimension", "mutate"),
    [
        (
            "ownership.run_id",
            lambda value: replace(
                value,
                ownership=replace(value.ownership, run_id=IdentityDimension.known(102)),
            ),
        ),
        (
            "ownership.pipeline_id",
            lambda value: replace(
                value,
                ownership=replace(value.ownership, pipeline_id=IdentityDimension.known(203)),
            ),
        ),
        (
            "subject.ticker",
            lambda value: replace(
                value,
                subject=replace(value.subject, ticker=IdentityDimension.known("MSFT")),
            ),
        ),
        (
            "subject.company_id",
            lambda value: replace(
                value,
                subject=replace(value.subject, company_id=IdentityDimension.known(304)),
            ),
        ),
        (
            "calculation_context.market_calculation_context_id",
            lambda value: replace(
                value,
                calculation_context=replace(
                    value.calculation_context,
                    market_calculation_context_id=IdentityDimension.known(405),
                ),
            ),
        ),
        (
            "calculation_context.context_fingerprint",
            lambda value: replace(
                value,
                calculation_context=replace(
                    value.calculation_context,
                    context_fingerprint=IdentityDimension.known(
                        _digest("different-context", "complete MarketCalculationContext")
                    ),
                ),
            ),
        ),
        (
            "temporal.as_of_session",
            lambda value: replace(
                value,
                temporal=replace(
                    value.temporal,
                    as_of_session=IdentityDimension.known(date(2026, 9, 11)),
                ),
            ),
        ),
        (
            "temporal.calculation_cutoff",
            lambda value: replace(
                value,
                temporal=replace(
                    value.temporal,
                    calculation_cutoff=IdentityDimension.known(
                        datetime(2026, 9, 11, 20, tzinfo=UTC)
                    ),
                ),
            ),
        ),
        (
            "temporal.calendar",
            lambda value: replace(
                value,
                temporal=replace(
                    value.temporal,
                    calendar=IdentityDimension.known(
                        replace(
                            value.temporal.calendar.value,
                            calendar_version="swinglens-us-equities-v2",
                        )
                    ),
                ),
            ),
        ),
        (
            "configuration.effective_configuration",
            lambda value: replace(
                value,
                configuration=ConfigurationIdentity(
                    IdentityDimension.known(
                        replace(
                            value.configuration.effective_configuration.value,
                            fingerprint=_digest(
                                "changed-config", "complete resolved config"
                            ),
                        )
                    )
                ),
            ),
        ),
        (
            "algorithm.calculation_version",
            lambda value: replace(
                value,
                algorithm=replace(
                    value.algorithm,
                    calculation_version=IdentityDimension.known(
                        _version("calculation", "calc-v3")
                    ),
                ),
            ),
        ),
        (
            "algorithm.model_version",
            lambda value: replace(
                value,
                algorithm=replace(
                    value.algorithm,
                    model_version=IdentityDimension.known(_version("model", "model-v4")),
                ),
            ),
        ),
        (
            "algorithm.schema_version",
            lambda value: replace(
                value,
                algorithm=replace(
                    value.algorithm,
                    schema_version=IdentityDimension.known(
                        _version("schema", "schema-v5")
                    ),
                ),
            ),
        ),
        (
            "algorithm.engine_version",
            lambda value: replace(
                value,
                algorithm=replace(
                    value.algorithm,
                    engine_version=IdentityDimension.known(
                        _version("engine", "engine-v6")
                    ),
                ),
            ),
        ),
        (
            "algorithm.components",
            lambda value: replace(
                value,
                algorithm=replace(
                    value.algorithm,
                    components=IdentityDimension.known(
                        (_version("eligibility-policy", "eligibility-v3"),)
                    ),
                ),
            ),
        ),
        (
            "source_lineage",
            lambda value: replace(
                value,
                source_lineage=IdentityDimension.known(_source_lineage(revision="18")),
            ),
        ),
    ],
)
def test_each_material_dimension_mismatch_is_rejected_and_changes_fingerprint(
    dimension: str,
    mutate,
) -> None:
    expected = _identity()
    actual = mutate(expected)

    result = _compare(expected, actual)

    assert result.status is CalculationIdentityCompatibilityStatus.INCOMPATIBLE
    assert result.mismatches[0].dimension == dimension
    assert expected.fingerprint() != actual.fingerprint()


def test_same_run_and_ticker_do_not_hide_pipeline_mismatch() -> None:
    expected = _identity()
    actual = replace(
        expected,
        ownership=replace(expected.ownership, pipeline_id=IdentityDimension.known(999)),
    )

    assert expected.ownership.run_id == actual.ownership.run_id
    assert expected.subject.ticker == actual.subject.ticker
    assert _compare(expected, actual).status is CalculationIdentityCompatibilityStatus.INCOMPATIBLE


def test_unknown_is_not_equality_under_strict_policy() -> None:
    unknown = ConfigurationIdentity(IdentityDimension.unknown())
    left = replace(_identity(), configuration=unknown)
    right = replace(_identity(), configuration=unknown)

    result = _compare(left, right)

    assert result.status is CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY
    assert result.mismatches[0].dimension == "configuration.effective_configuration"
    assert result.mismatches[0].reason == "UNKNOWN_IS_NOT_COMPATIBILITY_EVIDENCE"


def test_nested_unknown_source_revision_is_not_hidden_by_known_lineage() -> None:
    base = _identity()
    lineage = base.source_lineage.value
    first = replace(lineage.references[0], revision_id=IdentityDimension.unknown())
    unknown_lineage = replace(lineage, references=(first, *lineage.references[1:]))
    left = replace(base, source_lineage=IdentityDimension.known(unknown_lineage))
    right = replace(base, source_lineage=IdentityDimension.known(unknown_lineage))

    result = _compare(left, right)

    assert result.status is CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY
    assert "source_lineage" in result.mismatches[0].dimension


def test_not_applicable_on_both_sides_is_allowed_by_named_policy() -> None:
    left = _identity()
    right = _identity()
    left = replace(
        left,
        algorithm=replace(left.algorithm, model_version=IdentityDimension.not_applicable()),
    )
    right = replace(
        right,
        algorithm=replace(right.algorithm, model_version=IdentityDimension.not_applicable()),
    )

    assert _compare(left, right).status is CalculationIdentityCompatibilityStatus.EXACT_MATCH


def test_legacy_identity_cannot_pass_strict_compatibility() -> None:
    left = CalculationIdentity.legacy_unknown(run_id=101, ticker="AAPL")
    right = CalculationIdentity.legacy_unknown(run_id=101, ticker="AAPL")

    result = _compare(left, right)

    assert left.validate().valid
    assert result.status is CalculationIdentityCompatibilityStatus.INSUFFICIENT_IDENTITY


def test_timezone_equivalence_source_order_and_round_trip_are_canonical() -> None:
    utc = _identity(cutoff=datetime(2026, 9, 10, 20, tzinfo=UTC))
    zurich = _identity(
        cutoff=datetime(2026, 9, 10, 22, tzinfo=ZoneInfo("Europe/Zurich")),
        source_reverse=True,
    )

    assert utc.fingerprint() == zurich.fingerprint()
    restored = CalculationIdentity.from_canonical_payload(utc.canonical_payload())
    assert restored.canonical_json() == utc.canonical_json()
    assert restored.fingerprint() == utc.fingerprint()


def test_one_generation_dimension_change_is_rejected_by_generation_policy() -> None:
    expected = _identity(with_generation=True)
    actual = replace(
        expected,
        generation=replace(
            expected.generation,
            generation_id=IdentityDimension.known("42"),
        ),
    )

    result = CalculationIdentityCompatibilityValidator.compare(
        expected,
        actual,
        policy=GENERATION_COMPATIBILITY,
    )

    assert result.status is CalculationIdentityCompatibilityStatus.INCOMPATIBLE
    assert result.mismatches[0].dimension == "generation.generation_id"
    assert expected.fingerprint() != actual.fingerprint()


@pytest.mark.parametrize(
    ("dimension", "mutate"),
    [
        (
            "generation.generation_id",
            lambda value: replace(
                value.generation, generation_id=IdentityDimension.known("42")
            ),
        ),
        (
            "generation.generation_key",
            lambda value: replace(
                value.generation,
                generation_key=IdentityDimension.known("winner-generation-42"),
            ),
        ),
        (
            "generation.generation_watermark",
            lambda value: replace(
                value.generation,
                generation_watermark=IdentityDimension.known(
                    _digest("watermark-42", "Winner material evidence watermark")
                ),
            ),
        ),
        (
            "generation.generation_cutoff",
            lambda value: replace(
                value.generation,
                generation_cutoff=IdentityDimension.known(
                    datetime(2026, 9, 11, 20, tzinfo=UTC)
                ),
            ),
        ),
    ],
)
def test_each_generation_dimension_is_material_under_generation_policy(
    dimension: str,
    mutate,
) -> None:
    expected = _identity(with_generation=True)
    actual = replace(expected, generation=mutate(expected))

    result = CalculationIdentityCompatibilityValidator.compare(
        expected,
        actual,
        policy=GENERATION_COMPATIBILITY,
    )

    assert result.status is CalculationIdentityCompatibilityStatus.INCOMPATIBLE
    assert result.mismatches[0].dimension == dimension
    assert expected.fingerprint() != actual.fingerprint()


def test_invalid_historical_identity_with_missing_cutoff_fails_validation() -> None:
    valid = _identity()
    invalid = replace(
        valid,
        temporal=replace(valid.temporal, calculation_cutoff=IdentityDimension.unknown()),
    )

    validation = invalid.validate()

    assert not validation.valid
    assert any(
        issue.dimension == "temporal.calculation_cutoff" for issue in validation.issues
    )


def test_timezone_naive_cutoff_and_partial_config_hash_fail_validation() -> None:
    valid = _identity()
    naive = replace(
        valid,
        temporal=replace(
            valid.temporal,
            calculation_cutoff=IdentityDimension.known(datetime(2026, 9, 10, 20)),
        ),
    )
    partial_config = replace(
        valid,
        configuration=ConfigurationIdentity(
            IdentityDimension.known(
                EffectiveConfigurationIdentity(
                    namespace="test-calculator",
                    fingerprint=DigestIdentity("sha256", "deadbeef", "partial debug fields"),
                    resolution_contract=_version("config-resolution", "v1"),
                    coverage=ConfigurationCoverage.PARTIAL_DEBUG,
                )
            )
        ),
    )

    assert any("timezone-aware" in issue.message for issue in naive.validate().issues)
    assert any("64 lowercase hex" in issue.message for issue in partial_config.validate().issues)
    assert any(
        "COMPLETE_EFFECTIVE_CONFIGURATION" in issue.message
        for issue in partial_config.validate().issues
    )


def test_generation_fields_cannot_be_used_in_impossible_partial_combination() -> None:
    valid = _identity()
    invalid = replace(
        valid,
        generation=replace(
            valid.generation,
            generation_id=IdentityDimension.known("42"),
        ),
    )

    assert any(issue.dimension == "generation" for issue in invalid.validate().issues)


def test_mismatch_diagnostic_names_dimension_values_and_policy() -> None:
    expected = _identity()
    actual = replace(
        expected,
        temporal=replace(
            expected.temporal,
            calculation_cutoff=IdentityDimension.known(
                expected.temporal.calculation_cutoff.value + timedelta(days=1)
            ),
        ),
    )

    result = _compare(expected, actual)
    diagnostic = result.diagnostic()

    assert "dimension=temporal.calculation_cutoff" in diagnostic
    assert "expected=2026-09-10T20:00:00.000000Z" in diagnostic
    assert "actual=2026-09-11T20:00:00.000000Z" in diagnostic
    assert "policy=STRICT_DECISION_COMPATIBILITY" in diagnostic


def test_execution_identity_cannot_satisfy_calculation_compatibility() -> None:
    execution = ExecutionIdentity(run_id=101, pipeline_run_id=202, execution_token="secret")

    with pytest.raises(TypeError, match="CalculationIdentity"):
        CalculationIdentityCompatibilityValidator.compare(
            execution,  # type: ignore[arg-type]
            execution,  # type: ignore[arg-type]
            policy=STRICT_DECISION_COMPATIBILITY,
        )


def test_unknown_dimension_payload_is_explicit_and_has_no_null_value() -> None:
    identity = replace(
        _identity(),
        configuration=ConfigurationIdentity(IdentityDimension.unknown()),
    )

    payload = identity.canonical_payload()["configuration"]["effective_configuration"]

    assert payload == {"state": IdentityState.UNKNOWN.value}


def test_deserialization_rejects_wrong_scalar_type_in_typed_field() -> None:
    payload = _identity().canonical_payload()
    payload["ownership"]["run_id"]["value"] = "101"

    with pytest.raises(CalculationIdentityValidationError, match="expected integer"):
        CalculationIdentity.from_canonical_payload(payload)
