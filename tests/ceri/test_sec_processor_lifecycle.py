from __future__ import annotations

import pytest

from app.models.ceri_tables import CeriCompany, CeriSecProcessorRelease, CeriSecSyncState
from app.services.ceri.sec.processor_lifecycle import (
    SecProcessorReleaseStatus,
    certify_processor,
    establish_worker_processor_identity,
    fence_worker_against_active_processor,
    lifecycle_state,
    promote_processor,
    register_deployed_processor,
)
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
from app.services.pipeline_prerequisites import WorkerProcessorDriftError
from app.services.runtime_mutation_authority import (
    RuntimeMutationAuthority,
    RuntimeMutationAuthorityError,
)


class Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class LifecycleDb:
    def __init__(self, releases):
        self.releases = list(releases)
        self.added = []
        self.flushes = 0

    def scalars(self, _statement):
        return Rows(self.releases)

    def get(self, _model, processor_signature):
        return next(
            (
                row
                for row in self.releases
                if row.processor_signature == processor_signature
            ),
            None,
        )

    def add(self, row):
        self.added.append(row)
        self.releases.append(row)

    def flush(self):
        self.flushes += 1


def test_deployed_signature_is_not_implicitly_active() -> None:
    db = LifecycleDb(
        [
            CeriSecProcessorRelease(processor_signature="old", status="ACTIVE"),
            CeriSecProcessorRelease(
                processor_signature="sec-guidance:eed017654682a0c9", status="DEPLOYED"
            ),
        ]
    )

    state = lifecycle_state(db)

    assert state.active_signature == "old"
    assert state.deployed_signature == "sec-guidance:eed017654682a0c9"
    assert state.deployed_is_active is False


def test_registering_newly_deployed_code_does_not_activate_it() -> None:
    db = LifecycleDb([])

    deployed = register_deployed_processor(
        db,
        authority=RuntimeMutationAuthority.normal("test.sec_processor.register"),
        git_sha="abc123",
    )

    assert deployed.status == SecProcessorReleaseStatus.DEPLOYED
    assert deployed.deployed_git_sha == "abc123"
    assert lifecycle_state(db).active_signature is None


def test_normal_worker_startup_retains_processor_registration() -> None:
    db = LifecycleDb([])

    deployed = establish_worker_processor_identity(
        db,
        authority=RuntimeMutationAuthority.normal("worker.startup.sec_identity"),
        git_sha="abc123",
    )

    assert deployed in db.added
    assert deployed.status == SecProcessorReleaseStatus.DEPLOYED
    assert deployed.deployed_git_sha == "abc123"
    assert db.flushes == 1


def test_processor_registration_rejects_untyped_authority() -> None:
    with pytest.raises(
        RuntimeMutationAuthorityError, match="RUNTIME_MUTATION_AUTHORITY_REQUIRED"
    ):
        register_deployed_processor(
            LifecycleDb([]), authority=None  # type: ignore[arg-type]
        )


def test_certification_worker_startup_is_repeatable_and_read_only() -> None:
    release = CeriSecProcessorRelease(
        processor_signature=sec_guidance_processor_signature(),
        status=SecProcessorReleaseStatus.DEPLOYED,
        deployed_git_sha="abc123",
    )
    db = LifecycleDb([release])
    authority = RuntimeMutationAuthority.certification(
        "worker.startup.sec_identity", session_id="cert-session"
    )

    first = establish_worker_processor_identity(
        db, authority=authority, git_sha="abc123"
    )
    second = establish_worker_processor_identity(
        db, authority=authority, git_sha="abc123"
    )

    assert first is release and second is release
    assert db.added == []
    assert db.flushes == 0


def test_certification_worker_startup_fails_closed_for_missing_or_mismatched_identity() -> None:
    authority = RuntimeMutationAuthority.certification(
        "worker.startup.sec_identity", session_id="cert-session"
    )

    with pytest.raises(
        RuntimeMutationAuthorityError, match="CERTIFICATION_PROCESSOR_NOT_REGISTERED"
    ):
        establish_worker_processor_identity(LifecycleDb([]), authority=authority)

    mismatch = LifecycleDb(
        [
            CeriSecProcessorRelease(
                processor_signature=sec_guidance_processor_signature(),
                status=SecProcessorReleaseStatus.DEPLOYED,
                deployed_git_sha="different-sha",
            )
        ]
    )
    with pytest.raises(
        RuntimeMutationAuthorityError, match="CERTIFICATION_PROCESSOR_IDENTITY_MISMATCH"
    ):
        establish_worker_processor_identity(
            mismatch, authority=authority, git_sha="current-sha"
        )

    assert mismatch.added == []
    assert mismatch.flushes == 0


def test_promotion_is_explicit_and_retires_previous_active() -> None:
    old = CeriSecProcessorRelease(processor_signature="old", status="ACTIVE")
    target = CeriSecProcessorRelease(processor_signature="new", status="CERTIFIED")
    db = LifecycleDb([old, target])

    promoted = promote_processor(db, processor_signature="new", actor="operator")

    assert promoted.status == SecProcessorReleaseStatus.ACTIVE
    assert promoted.activated_by == "operator"
    assert old.status == SecProcessorReleaseStatus.RETIRED
    assert old.retired_at is not None


def test_repromoting_current_active_processor_is_idempotent() -> None:
    target = CeriSecProcessorRelease(processor_signature="current", status="ACTIVE")
    db = LifecycleDb([target])

    promoted = promote_processor(db, processor_signature="current", actor="operator")

    assert promoted is target
    assert promoted.status == SecProcessorReleaseStatus.ACTIVE


def test_recertifying_current_active_processor_does_not_demote_it() -> None:
    target = CeriSecProcessorRelease(processor_signature="current", status="ACTIVE")
    db = LifecycleDb([target])

    certified = certify_processor(
        db,
        processor_signature="current",
        evidence={"passed": True},
        actor="operator",
    )

    assert certified.status == SecProcessorReleaseStatus.ACTIVE
    assert certified.certification_evidence_json == {"passed": True}


def test_uncertified_processor_cannot_be_promoted() -> None:
    db = LifecycleDb(
        [CeriSecProcessorRelease(processor_signature="new", status="DEPLOYED")]
    )

    with pytest.raises(ValueError, match="CERTIFIED"):
        promote_processor(db, processor_signature="new", actor="operator")


def test_stale_worker_is_fenced_from_claiming_for_a_different_active_signature() -> None:
    db = LifecycleDb(
        [
            CeriSecProcessorRelease(processor_signature="old", status="ACTIVE"),
            CeriSecProcessorRelease(
                processor_signature="sec-guidance:eed017654682a0c9", status="DEPLOYED"
            ),
        ]
    )

    with pytest.raises(WorkerProcessorDriftError, match="incompatible"):
        fence_worker_against_active_processor(db)


class DiagnosticDb:
    def __init__(self, companies, sync_rows):
        self.responses = [Rows(companies), Rows(sync_rows)]

    def scalars(self, _statement):
        return self.responses.pop(0)


def test_readiness_diagnostics_distinguish_blocking_categories() -> None:
    companies = [
        CeriCompany(ticker="READY", cik="1"),
        CeriCompany(ticker="OLD", cik="2"),
        CeriCompany(ticker="COLD", cik="3"),
        CeriCompany(ticker="NOCIK", cik=None),
        CeriCompany(
            ticker="FOREIGN",
            cik=None,
            sec_applicability="NOT_APPLICABLE",
            sec_applicability_reason="operator-reviewed non-SEC instrument",
        ),
    ]
    sync_rows = [
        CeriSecSyncState(cik="1", dataset="guidance", processor_signature="current"),
        CeriSecSyncState(cik="2", dataset="guidance", processor_signature="old"),
    ]

    result = diagnose_sec_readiness(
        DiagnosticDb(companies, sync_rows),
        tickers=[
            "READY",
            "OLD",
            "COLD",
            "NOCIK",
            "FOREIGN",
            "UNKNOWN",
            "BAD TICKER",
        ],
        processor_signature="current",
    )

    assert result.counts() == {
        "READY": 1,
        "CIK_MISSING": 1,
        "SYNC_STATE_MISSING": 1,
        "SIGNATURE_MISMATCH": 1,
        "SEC_NOT_APPLICABLE": 1,
        "UNRESOLVED_MAPPING": 1,
        "INVALID_TICKER": 1,
        "OTHER_BLOCKING_REASON": 0,
    }
    assert result.complete is False


def test_readiness_diagnostics_reject_invalid_ticker_syntax() -> None:
    result = diagnose_sec_readiness(
        DiagnosticDb([], []),
        tickers=["BAD TICKER"],
        processor_signature="current",
    )

    assert result.tickers[0].category.value == "INVALID_TICKER"


def test_readiness_requires_current_signature_for_every_required_cik() -> None:
    result = diagnose_sec_readiness(
        DiagnosticDb(
            [CeriCompany(ticker="DUAL", cik="1"), CeriCompany(ticker="DUAL", cik="2")],
            [CeriSecSyncState(cik="1", dataset="guidance", processor_signature="current")],
        ),
        tickers=["DUAL"],
        processor_signature="current",
    )

    assert result.tickers[0].category.value == "SYNC_STATE_MISSING"
    assert result.complete is False
