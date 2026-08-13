from __future__ import annotations

from app.services.ceri.sec.readiness_service import (
    SecReadinessFacts,
    SecReadinessPolicy,
    SecReadinessService,
    SecReadinessState,
)


def test_active_without_bootstrap_is_explicitly_bootstrap_required() -> None:
    result = SecReadinessService().assess(
        SecReadinessFacts(cik="0000123456", active=True, bootstrap_certified=False),
        policy=SecReadinessPolicy.REQUIRE_READY,
    )

    assert result.state is SecReadinessState.BOOTSTRAP_REQUIRED
    assert result.run_evidence_status == "REJECTED"
    assert not result.may_ingest


def test_allow_degraded_preserves_bootstrap_gap_as_run_status() -> None:
    result = SecReadinessService().assess(
        SecReadinessFacts(cik="0000123456", active=True, bootstrap_certified=False),
        policy=SecReadinessPolicy.ALLOW_DEGRADED,
    )

    assert result.state is SecReadinessState.BOOTSTRAP_REQUIRED
    assert result.run_evidence_status == "DEGRADED"
    assert result.may_ingest


def test_readiness_states_cover_no_cik_bootstrapping_failure_and_ready() -> None:
    service = SecReadinessService()

    assert service.assess(SecReadinessFacts(cik=None)).state is SecReadinessState.NO_CIK
    assert service.assess(
        SecReadinessFacts(cik="1", bootstrap_in_progress=True)
    ).state is SecReadinessState.BOOTSTRAPPING
    assert service.assess(
        SecReadinessFacts(cik="1", failed=True)
    ).state is SecReadinessState.FAILED
    assert service.assess(
        SecReadinessFacts(cik="1", bootstrap_certified=True)
    ).state is SecReadinessState.READY


def test_terminal_reuse_is_ready_without_network_or_extraction() -> None:
    result = SecReadinessService().assess(
        SecReadinessFacts(
            cik="1",
            bootstrap_certified=True,
            terminal_documents_reused=3,
            documents_downloaded=0,
            documents_extracted=0,
        )
    )

    assert result.state is SecReadinessState.READY_TERMINAL_REUSE
    assert result.run_evidence_status == "READY"


def test_processor_signature_change_requires_fresh_bootstrap() -> None:
    result = SecReadinessService().assess(
        SecReadinessFacts(
            cik="1",
            bootstrap_certified=False,
            prior_signature_certified=True,
        ),
        policy=SecReadinessPolicy.REQUIRE_READY,
    )

    assert result.state is SecReadinessState.BOOTSTRAP_REQUIRED
    assert result.reason == "PROCESSOR_SIGNATURE_CHANGED"


def test_dominant_provider_gap_forces_run_level_degraded_or_rejected() -> None:
    service = SecReadinessService()

    degraded = service.aggregate_run_status(
        ["READY", "DEGRADED"], dominant_provider_status="DEGRADED", strict=False
    )
    rejected = service.aggregate_run_status(
        ["READY", "DEGRADED"], dominant_provider_status="DEGRADED", strict=True
    )

    assert degraded == "DEGRADED"
    assert rejected == "REJECTED"
