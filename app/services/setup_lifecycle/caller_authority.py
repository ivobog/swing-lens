"""Exact pipeline authority delivery for supported Setup continuation callers."""

from app.models.tables import PipelineRun
from app.services.configuration_delivery import pipeline_configuration_delivery
from app.services.entrypoint_authority import EntryPointAuthorityError
from app.services.market_calculation_context_service import (
    market_context_for_pipeline,
    resolve_pipeline_market_context,
)


def setup_run_cutoff(db, *, run_id: int, pipeline_run_id: int | None):
    if pipeline_run_id is None:
        raise EntryPointAuthorityError(
            "SETUP_PIPELINE_AUTHORITY_REQUIRED",
            "Setup continuation requires an explicit pipeline_run_id with retained context. "
            "Start a new Full Pipeline calculation for current work.",
        )
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None or pipeline.upload_run_id != run_id:
        raise EntryPointAuthorityError(
            "SETUP_PIPELINE_SCOPE_MISMATCH", "The explicit pipeline does not own this upload run."
        )
    cutoff = market_context_for_pipeline(db, pipeline)
    return resolve_pipeline_market_context(
        db,
        calculation_context_id=cutoff.context_id,
        upload_run_id=run_id,
        pipeline_run_id=pipeline_run_id,
    )


@pipeline_configuration_delivery
def evaluate_setup_run_with_authority(db, pipeline_run_id, *, run_id, requester):
    from app.services.setup_lifecycle.evaluation_service import SetupLifecycleEvaluationService

    cutoff = setup_run_cutoff(db, run_id=run_id, pipeline_run_id=pipeline_run_id)
    return SetupLifecycleEvaluationService().evaluate_run(
        db,
        run_id,
        requester=requester,
        market_cutoff=cutoff,
        pipeline_run_id=pipeline_run_id,
    )
