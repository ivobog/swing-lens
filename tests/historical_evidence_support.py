"""Pre-Phase-5 ledger fixtures for retained history/readiness/schema regressions.

This fixture creates historical data in disposable tests. It is not a producer,
mutation permit, or substitute for tests of live canonical writer authority.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
)
from app.services.calculation_identity import IdentityDimension, IdentityState
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.core_calculation_evidence import (
    calculation_evidence_payload,
    normalize_configuration_business_payload,
)
from app.services.effective_configuration import (
    CONFIGURATION_PAYLOAD_KEY,
    ConfigurationCompatibilityStatus,
    EffectiveConfigurationSnapshot,
    compare_configuration,
)
from app.services.producer_readiness import READINESS_PAYLOAD_KEY, normalize_producer_readiness

_UNSET = object()


def seed_pre_phase5_evidence(
    db,
    *,
    kind,
    current_row,
    sources=None,
    payload=None,
    scope_ticker=_UNSET,
    scope_profile=_UNSET,
    calculation_identity=None,
    effective_configuration=None,
    **kwargs,
):
    identity = calculation_identity or calculation_identity_from_debug(
        getattr(current_row, "debug_json", None)
        or getattr(current_row, "evidence_lineage_json", None)
    )
    if identity is None:
        if effective_configuration is not None:
            raise ValueError("configuration evidence requires an explicit Calculation Identity")
        return None
    if effective_configuration is not None and not isinstance(
        effective_configuration, EffectiveConfigurationSnapshot
    ):
        raise TypeError("effective configuration evidence requires a typed snapshot")
    config = identity.configuration.effective_configuration
    if (
        config.state is IdentityState.KNOWN
        and config.value.namespace.startswith(("core.", "contextual.", "decision."))
        and effective_configuration is None
    ):
        raise ValueError("CORE_EFFECTIVE_CONFIGURATION_REQUIRED")
    if (
        effective_configuration is not None
        and compare_configuration(
            config, IdentityDimension.known(effective_configuration.identity)
        ).status
        is not ConfigurationCompatibilityStatus.EXACT
    ):
        raise ValueError("effective configuration must match the bound Calculation Identity")
    source_ids = {}
    for role, source in sorted((sources or {}).items()):
        if source.evidence_id is None:
            from app.services.core_calculation_evidence import EvidenceUnavailableError

            raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: historical source is unsealed")
        source_ids[role] = int(source.evidence_id)
    payload = Canonical.canonicalize(
        payload if payload is not None else calculation_evidence_payload(current_row)
    )
    if CONFIGURATION_PAYLOAD_KEY in payload:
        raise ValueError("effective configuration is owned by the evidence writer")
    if effective_configuration is not None:
        normalize_configuration_business_payload(
            payload, namespace=effective_configuration.family.namespace, source_ids=source_ids
        )
        payload[CONFIGURATION_PAYLOAD_KEY] = effective_configuration.as_dict()
    if READINESS_PAYLOAD_KEY in payload:
        raise ValueError("readiness-at-creation is owned by the evidence writer")
    encoded = identity.canonical_payload()
    payload[READINESS_PAYLOAD_KEY] = normalize_producer_readiness(
        kind.value,
        payload,
        identity_fingerprint=str(identity.fingerprint()),
        calculation_versions=encoded.get("algorithm", {}),
        evaluated_at=encoded["temporal"]["calculation_cutoff"].get("value"),
        business_anchor=encoded["temporal"]["as_of_session"].get("value"),
    ).canonical_payload()
    key = Canonical.fingerprint(
        {
            "artifact_kind": kind.value,
            "calculation_identity_fingerprint": str(identity.fingerprint()),
            "payload_fingerprint": Canonical.fingerprint(payload),
            "source_evidence_ids": source_ids,
        }
    )
    evidence = db.scalar(
        select(CoreCalculationEvidence).where(CoreCalculationEvidence.evidence_key == key)
    )
    if evidence is None:
        ticker = getattr(current_row, "ticker", None) if scope_ticker is _UNSET else scope_ticker
        profile = (
            getattr(current_row, "ranking_profile", None)
            if scope_profile is _UNSET
            else scope_profile
        )
        evidence = CoreCalculationEvidence(
            artifact_kind=kind.value,
            run_id=getattr(current_row, "run_id", None),
            ticker=ticker.upper() if ticker else None,
            ranking_profile=profile,
            calculation_identity_fingerprint=str(identity.fingerprint()),
            calculation_identity_json=encoded,
            payload_fingerprint=Canonical.fingerprint(payload),
            payload_json=payload,
            source_evidence_ids_json=source_ids,
            evidence_key=key,
            calculated_at=identity.temporal.calculation_cutoff.value
            or datetime(2026, 9, 1, tzinfo=UTC),
        )
        db.add(evidence)
        db.flush()
        for role, address in source_ids.items():
            db.add(
                CoreCalculationEvidenceSource(
                    evidence_id=evidence.id, source_role=role, source_evidence_id=address
                )
            )
        db.flush()
    current_row.evidence_id = evidence.id
    if hasattr(type(current_row), "calculation_evidence"):
        set_committed_value(current_row, "calculation_evidence", evidence)
    projection = db.scalar(
        select(CoreCalculationCurrentProjection).where(
            CoreCalculationCurrentProjection.artifact_kind == kind.value,
            CoreCalculationCurrentProjection.run_id == evidence.run_id,
            CoreCalculationCurrentProjection.ticker == evidence.ticker,
            CoreCalculationCurrentProjection.ranking_profile_key
            == (evidence.ranking_profile or ""),
        )
    )
    if projection is None:
        db.add(
            CoreCalculationCurrentProjection(
                artifact_kind=kind.value,
                run_id=evidence.run_id,
                ticker=evidence.ticker,
                ranking_profile_key=evidence.ranking_profile or "",
                evidence_id=evidence.id,
            )
        )
    else:
        projection.evidence_id = evidence.id
    db.flush()
    return evidence
