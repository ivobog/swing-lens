"""Exact owner of a market or post-acquisition CERI calculation."""

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.orm import aliased

from app.models.tables import MarketCalculationContext, PipelineRun


def bound_pipeline_id(upload_id, context_id):
    """Return NULL if the persisted context binding is missing or ambiguous."""
    context = aliased(MarketCalculationContext)
    owner = aliased(PipelineRun)
    return (
        select(func.min(owner.id))
        .select_from(context)
        .join(
            owner,
            and_(
                owner.upload_run_id == context.upload_run_id,
                (owner.id == context.pipeline_run_id)
                | and_(
                    context.pipeline_run_id.is_(None),
                    context.cutoff_reason == "CERI_POST_ACQUISITION_FROZEN",
                    owner.result_json["ceri_calculation_context_id"].astext
                    == cast(context.id, String),
                ),
            ),
        )
        .where(context.id == context_id, context.upload_run_id == upload_id)
        .having(func.count(owner.id) == 1)
        .correlate_except(context, owner)
        .scalar_subquery()
    )
