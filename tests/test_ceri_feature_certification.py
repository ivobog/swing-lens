from __future__ import annotations

import pytest

from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CERI_RUN_FINALIZE,
    build_ceri_batched_workflow_plan,
)
from app.services.ceri.feature_certification_workflow import (
    CeriFeatureCertificationRequest,
    admit_ceri_feature_certification,
    build_ceri_feature_certification_plan,
)
from app.services.certification_runtime import (
    CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
    CertificationRuntimeViolation,
    _is_authorized_root,
    ceri_feature_certification_root_payload,
)
from app.settings import RuntimeMode, Settings

WORKFLOW_KEY = "ceri:feature-certification:test:config"
PROVIDER_SCOPE = {"eodhd": ("earnings", "estimates")}


def _plan(tickers: tuple[str, ...] = ("SYN1", "SYN2")):
    return build_ceri_feature_certification_plan(
        workflow_key=WORKFLOW_KEY,
        tickers=tickers,
        provider_datasets=PROVIDER_SCOPE,
        checkpoint_interval=1,
    )


def test_feature_certification_graph_is_bounded_and_has_no_finalizer() -> None:
    plan = _plan()
    assert plan.provider_batches == 2
    assert plan.normalization_batches == 2
    assert plan.feature_batches == 1
    assert [job.job_type for job in plan.jobs] == [
        CERI_PROVIDER_INGEST_BATCH,
        CERI_NORMALIZE_BATCH,
        CERI_PROVIDER_INGEST_BATCH,
        CERI_NORMALIZE_BATCH,
        CERI_FEATURE_BATCH,
    ]
    assert CERI_RUN_FINALIZE not in {job.job_type for job in plan.jobs}
    assert all(job.payload["tickers"] == ["SYN1", "SYN2"] for job in plan.jobs)
    assert all(job.payload["checkpoint_interval"] == 1 for job in plan.jobs)


@pytest.mark.parametrize("tickers", [(), ("ONLY",), ("A", "B", "C")])
def test_feature_certification_requires_exactly_two_tickers(tickers: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="EXACTLY_TWO"):
        _plan(tickers)


def test_feature_certification_rejects_duplicate_wildcard_and_provider_expansion() -> None:
    with pytest.raises(ValueError, match="DUPLICATE"):
        _plan(("SYN", "syn"))
    with pytest.raises(ValueError, match="INVALID_TICKER"):
        _plan(("*", "SYN"))
    with pytest.raises(ValueError, match="PROVIDER_SCOPE_INVALID"):
        build_ceri_feature_certification_plan(
            workflow_key=WORKFLOW_KEY,
            tickers=("SYN1", "SYN2"),
            provider_datasets={"eodhd": ("earnings", "estimates", "catalysts")},
            checkpoint_interval=1,
        )


def test_certification_root_authorization_fails_closed_on_permission_or_scope_change() -> None:
    payload = {
        **ceri_feature_certification_root_payload(session_id="cert-session"),
        "tickers": ["SYN1", "SYN2"],
        "provider_datasets": {"eodhd": ["earnings", "estimates"]},
        "checkpoint_interval": 1,
        "feature_batch_count": 1,
        "run_id": 1,
        "pipeline_run_id": 2,
        "calculation_context_id": 3,
        "workflow_key": WORKFLOW_KEY,
        "request_key": "test",
        "configuration_identity": "configuration",
        "cutoff_at": "2026-09-25T22:00:00+00:00",
        "as_of_session": "2026-09-25",
        "calendar_version": "calendar",
        "scope_id": "scope",
        "refresh_cycle_id": "refresh",
        "acquisition_plan_id": "plan",
        "allow_score_capture": False,
        "allow_change_detection": False,
        "allow_alerts": False,
        "allow_setup_publication": False,
        "allow_winner_publication": False,
        "allow_lifecycle_publication": False,
        "allow_downstream_continuation": False,
    }
    assert _is_authorized_root(
        CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        payload,
        session_id="cert-session",
    )
    assert not _is_authorized_root(
        CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        {**payload, "allow_score_capture": True},
        session_id="cert-session",
    )
    assert not _is_authorized_root(
        CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        {**payload, "tickers": ["SYN1", "SYN2", "SYN3"]},
        session_id="cert-session",
    )


def test_normal_pipeline_plan_still_has_its_finalizer() -> None:
    plan = build_ceri_batched_workflow_plan(
        run_id=123,
        tickers=("SYN1", "SYN2"),
        config_hash="config",
        provider_datasets={"eodhd": ()},
    )
    assert plan.jobs[-1].job_type == CERI_RUN_FINALIZE


def test_admission_rejects_normal_runtime_before_any_database_access() -> None:
    with pytest.raises(CertificationRuntimeViolation, match="MODE_REQUIRED"):
        admit_ceri_feature_certification(
            object(),  # type: ignore[arg-type]
            CeriFeatureCertificationRequest(
                tickers=("SYN1", "SYN2"),
                request_key="normal-mode-must-fail",
            ),
            settings=Settings(_env_file=None, runtime_mode=RuntimeMode.NORMAL),
        )
