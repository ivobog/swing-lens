from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.original_context_reconstruction import (
    AuthorityAvailability,
    AuthorityDimension,
    AuthorityReference,
    AuthorityReferenceKind,
    AuthorityResolution,
    OriginalContextReconstructionManifest,
    ReconstructionComparison,
    ReconstructionComparisonStatus,
    ReconstructionMode,
    ReconstructionOutcome,
    ReconstructionResult,
    ReconstructionStatus,
    authorize_reconstruction,
    require_batch_resolution,
)


def _reference(
    identity: str,
    *,
    kind: AuthorityReferenceKind = AuthorityReferenceKind.RETAINED_HISTORICAL,
    proven: bool = False,
) -> AuthorityReference:
    return AuthorityReference(
        "fixture",
        identity,
        kind,
        proven,
        f"historical-proof:{identity}" if proven else None,
    )


def _exact(dimension: AuthorityDimension, identity: str | None = None) -> AuthorityResolution:
    return AuthorityResolution(
        dimension,
        AuthorityAvailability.EXACT,
        True,
        (_reference(identity or dimension.value.lower()),),
    )


def _manifest(
    *,
    mode: ReconstructionMode = ReconstructionMode.ORIGINAL_CONTEXT,
    authority: tuple[AuthorityResolution, ...] | None = None,
) -> OriginalContextReconstructionManifest:
    return OriginalContextReconstructionManifest(
        target_artifact_type="SetupLifecycleDecision",
        target_artifact_id="setup-42",
        target_historical_decision_id="decision-17",
        requested_mode=mode,
        authority=authority
        or (
            _exact(AuthorityDimension.CALCULATION_IDENTITY),
            _exact(AuthorityDimension.EFFECTIVE_CONFIGURATION),
            _exact(AuthorityDimension.SOURCE_REVISION),
            _exact(AuthorityDimension.PREDECESSOR_STATE),
            _exact(AuthorityDimension.RULE_POLICY_VERSION),
            _exact(AuthorityDimension.BUSINESS_CUTOFF),
        ),
        calculation_identity="calc-1",
        scope_identity="scope-1",
        refresh_identity="refresh-1",
        acquisition_plan_identity="plan-1",
    )


def _missing(dimension: AuthorityDimension) -> AuthorityResolution:
    return AuthorityResolution(
        dimension,
        AuthorityAvailability.LEGACY_UNKNOWN,
        True,
        reason="not retained on legacy artifact",
        permanently_unavailable=True,
    )


def test_exact_original_context_requires_every_material_dimension() -> None:
    manifest = _manifest()

    assert manifest.status is ReconstructionStatus.EXACT
    assert manifest.completeness.required_dimensions == 6
    assert manifest.completeness.exact == 6
    assert manifest.completeness.exact_for_original_context is True
    authorization = authorize_reconstruction(manifest)
    assert authorization.authorized is True
    assert authorization.outcome is ReconstructionOutcome.READY


@pytest.mark.parametrize(
    "dimension",
    [
        AuthorityDimension.EFFECTIVE_CONFIGURATION,
        AuthorityDimension.SOURCE_REVISION,
        AuthorityDimension.PREDECESSOR_STATE,
        AuthorityDimension.RULE_POLICY_VERSION,
    ],
)
def test_missing_material_authority_fails_original_context(dimension: AuthorityDimension) -> None:
    authority = tuple(
        _missing(item.dimension) if item.dimension is dimension else item
        for item in _manifest().authority
    )
    manifest = _manifest(authority=authority)

    assert manifest.status is ReconstructionStatus.PERMANENTLY_UNAVAILABLE
    authorization = authorize_reconstruction(manifest)
    assert authorization.authorized is False
    assert authorization.outcome is ReconstructionOutcome.ORIGINAL_CONTEXT_UNAVAILABLE
    assert authorization.requested_mode is ReconstructionMode.ORIGINAL_CONTEXT


@pytest.mark.parametrize(
    "dimension,kind",
    [
        (AuthorityDimension.EFFECTIVE_CONFIGURATION, AuthorityReferenceKind.CURRENT_STATE),
        (AuthorityDimension.SOURCE_REVISION, AuthorityReferenceKind.CURRENT_REPLACEMENT),
        (AuthorityDimension.PREDECESSOR_STATE, AuthorityReferenceKind.CURRENT_STATE),
        (AuthorityDimension.RULE_POLICY_VERSION, AuthorityReferenceKind.CURRENT_STATE),
    ],
)
def test_current_authority_cannot_substitute_for_historical_authority(
    dimension: AuthorityDimension, kind: AuthorityReferenceKind
) -> None:
    current = AuthorityResolution(
        dimension,
        AuthorityAvailability.EXACT,
        True,
        (_reference(f"current-{dimension.value}", kind=kind),),
    )
    authority = tuple(
        current if item.dimension is dimension else item for item in _manifest().authority
    )
    manifest = _manifest(authority=authority)

    assert manifest.status is ReconstructionStatus.INSUFFICIENT_EVIDENCE
    assert authorize_reconstruction(manifest).authorized is False


def test_current_state_is_usable_only_with_independent_historical_identity_proof() -> None:
    config = AuthorityResolution(
        AuthorityDimension.EFFECTIVE_CONFIGURATION,
        AuthorityAvailability.EXACT,
        True,
        (
            _reference(
                "config-content-hash-1",
                kind=AuthorityReferenceKind.CURRENT_STATE,
                proven=True,
            ),
        ),
    )
    authority = tuple(
        config if item.dimension is AuthorityDimension.EFFECTIVE_CONFIGURATION else item
        for item in _manifest().authority
    )

    assert _manifest(authority=authority).status is ReconstructionStatus.EXACT


def test_current_state_boolean_without_proof_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="independent historical proof identity"):
        AuthorityReference(
            "fixture",
            "current-config",
            AuthorityReferenceKind.CURRENT_STATE,
            True,
        )


def test_bounded_authority_is_not_exact_and_cannot_claim_original_context() -> None:
    source = AuthorityResolution(
        AuthorityDimension.SOURCE_REVISION,
        AuthorityAvailability.BOUNDED,
        True,
        (_reference("source-1"), _reference("source-2")),
        reason="either retained revision could have been selected",
    )
    authority = tuple(
        source if item.dimension is AuthorityDimension.SOURCE_REVISION else item
        for item in _manifest().authority
    )
    manifest = _manifest(authority=authority)

    assert manifest.status is ReconstructionStatus.BOUNDED
    assert authorize_reconstruction(manifest).authorized is False


def test_operator_must_explicitly_request_current_rules_retrospective() -> None:
    original = _manifest(
        authority=(
            _exact(AuthorityDimension.CALCULATION_IDENTITY),
            _missing(AuthorityDimension.RULE_POLICY_VERSION),
        )
    )
    refused = authorize_reconstruction(original)
    retrospective = replace(original, requested_mode=ReconstructionMode.CURRENT_RULES_RETROSPECTIVE)
    accepted = authorize_reconstruction(retrospective)

    assert refused.authorized is False
    assert refused.requested_mode is ReconstructionMode.ORIGINAL_CONTEXT
    assert accepted.authorized is True
    assert accepted.reconstruction_status is ReconstructionStatus.CURRENT_RULES_ONLY
    assert accepted.outcome is ReconstructionOutcome.CURRENT_RULES_RETROSPECTIVE_READY


def test_future_knowledge_reference_is_rejected_as_current_replacement() -> None:
    later_fact = AuthorityResolution(
        AuthorityDimension.TEMPORAL_POSSESSION_AUTHORITY,
        AuthorityAvailability.EXACT,
        True,
        (
            _reference(
                "fact-observed-after-cutoff", kind=AuthorityReferenceKind.CURRENT_REPLACEMENT
            ),
        ),
    )
    manifest = _manifest(authority=(_exact(AuthorityDimension.BUSINESS_CUTOFF), later_fact))

    assert (
        authorize_reconstruction(manifest).outcome
        is ReconstructionOutcome.ORIGINAL_CONTEXT_UNAVAILABLE
    )


def test_manifest_fingerprint_is_order_independent_and_semantic() -> None:
    manifest = _manifest()
    reordered = replace(manifest, authority=tuple(reversed(manifest.authority)))
    changed = replace(
        manifest,
        authority=tuple(
            _exact(item.dimension, "different-source")
            if item.dimension is AuthorityDimension.SOURCE_REVISION
            else item
            for item in manifest.authority
        ),
    )

    assert manifest.canonical_json() == reordered.canonical_json()
    assert manifest.fingerprint() == reordered.fingerprint()
    assert manifest.fingerprint() != changed.fingerprint()


def test_operational_attempt_metadata_has_no_place_in_semantic_manifest() -> None:
    fields = OriginalContextReconstructionManifest.__dataclass_fields__

    assert "background_job_id" not in fields
    assert "worker_attempt" not in fields
    assert "execution_token" not in fields


def test_retrospective_comparison_cannot_claim_original_match() -> None:
    with pytest.raises(ValueError, match="cannot claim"):
        ReconstructionComparison(
            manifest_fingerprint=_manifest().fingerprint(),
            mode=ReconstructionMode.CURRENT_RULES_RETROSPECTIVE,
            comparison_target_id="original-1",
            status=ReconstructionComparisonStatus.MATCHED_ORIGINAL,
        )


def test_immutable_result_cannot_claim_original_output_with_incomplete_authority() -> None:
    manifest = _manifest(
        authority=(
            _exact(AuthorityDimension.CALCULATION_IDENTITY),
            _missing(AuthorityDimension.SOURCE_REVISION),
        )
    )
    comparison = ReconstructionComparison(
        manifest_fingerprint=manifest.fingerprint(),
        mode=ReconstructionMode.ORIGINAL_CONTEXT,
        comparison_target_id="original-1",
        status=ReconstructionComparisonStatus.INSUFFICIENT_AUTHORITY,
    )

    with pytest.raises(ValueError, match="cannot produce"):
        ReconstructionResult(
            manifest_fingerprint=manifest.fingerprint(),
            mode=ReconstructionMode.ORIGINAL_CONTEXT,
            reconstruction_status=manifest.status,
            authority_completeness=manifest.completeness,
            result_fingerprint="fabricated-result",
            comparison=comparison,
        )


def test_exact_result_is_bound_to_manifest_completeness_and_comparison() -> None:
    manifest = _manifest()
    comparison = ReconstructionComparison(
        manifest_fingerprint=manifest.fingerprint(),
        mode=ReconstructionMode.ORIGINAL_CONTEXT,
        comparison_target_id="original-1",
        status=ReconstructionComparisonStatus.MATCHED_ORIGINAL,
    )

    result = ReconstructionResult(
        manifest_fingerprint=manifest.fingerprint(),
        mode=ReconstructionMode.ORIGINAL_CONTEXT,
        reconstruction_status=manifest.status,
        authority_completeness=manifest.completeness,
        result_fingerprint="result-sha256",
        comparison=comparison,
    )

    assert result.result_fingerprint == "result-sha256"


@pytest.mark.parametrize("size", [1, 50, 200])
def test_authority_reference_batch_is_bounded_and_order_preserving(size: int) -> None:
    requested = tuple(f"source-{index}" for index in range(size))
    loaded = tuple(_reference(identity) for identity in reversed(requested))

    resolved = require_batch_resolution(requested, loaded)

    assert tuple(reference.reference_id for reference in resolved) == requested


def test_batch_resolution_never_fills_missing_history_from_current_state() -> None:
    with pytest.raises(ValueError, match=r"missing=\['source-1'\]"):
        require_batch_resolution(
            ("source-1",),
            (_reference("source-2", kind=AuthorityReferenceKind.CURRENT_STATE),),
        )


def test_authority_contract_rejects_ambiguous_or_invalid_manifests() -> None:
    with pytest.raises(ValueError, match="requires a reference"):
        AuthorityResolution(
            AuthorityDimension.SOURCE_EVIDENCE,
            AuthorityAvailability.EXACT,
            True,
        )
    with pytest.raises(ValueError, match="must be unique"):
        _manifest(
            authority=(
                _exact(AuthorityDimension.SOURCE_EVIDENCE, "a"),
                _exact(AuthorityDimension.SOURCE_EVIDENCE, "b"),
            )
        )
