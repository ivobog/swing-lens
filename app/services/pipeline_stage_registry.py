from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.ceri.constants import (
    CERI_PIPELINE_CAPTURE_STEP,
    CERI_PIPELINE_PROVIDER_INGEST_STEP,
)
from app.services.setup_lifecycle.constants import (
    SLSE_PIPELINE_CAPTURE_STEP,
    SLSE_PIPELINE_EVALUATION_STEP,
)


class StageExecutionClass(StrEnum):
    BOUNDED = "BOUNDED"
    MARKET_DATA = "MARKET_DATA"
    LONG_RUNNING = "LONG_RUNNING"


class StageCheckpointSemantics(StrEnum):
    STAGE_BOUNDARY = "STAGE_BOUNDARY"
    ITEM_PROGRESS = "ITEM_PROGRESS"
    DURABLE_BATCH = "DURABLE_BATCH"


CERI_PROVIDER_PROGRESS_STAGE = CERI_PIPELINE_PROVIDER_INGEST_STEP
CERI_NORMALIZE_PROGRESS_STAGE = "CERI_NORMALIZE"
CERI_FEATURE_PROGRESS_STAGE = "CERI_FEATURE_REBUILD"
CERI_FEATURE_PREPARE_PROGRESS_STAGE = "CERI_FEATURE_PREPARE"
CERI_CAPTURE_PROGRESS_STAGE = "CERI_CAPTURE_RUN"
CERI_CHANGE_DETECTION_PROGRESS_STAGE = "CERI_CHANGE_DETECTION"


@dataclass(frozen=True)
class PipelineStageMetadata:
    identity: str
    execution_class: StageExecutionClass
    heartbeat_expected: bool = True
    recoverable: bool = True
    checkpoint_semantics: StageCheckpointSemantics = StageCheckpointSemantics.STAGE_BOUNDARY


def _stage(
    identity: str,
    execution_class: StageExecutionClass,
    *,
    checkpoint_semantics: StageCheckpointSemantics = StageCheckpointSemantics.STAGE_BOUNDARY,
) -> PipelineStageMetadata:
    return PipelineStageMetadata(
        identity=identity,
        execution_class=execution_class,
        checkpoint_semantics=checkpoint_semantics,
    )


# This is the single operational classification registry. Pipeline builders own
# ordering; the registry owns watchdog/recovery behavior. Canonical constants
# are imported wherever a stage already has a domain owner so spelling cannot
# diverge from execution code.
PIPELINE_STAGE_METADATA = {
    metadata.identity: metadata
    for metadata in (
        _stage("FETCHING_MARKET_DATA", StageExecutionClass.MARKET_DATA),
        _stage("SCORING_TECHNICALS", StageExecutionClass.LONG_RUNNING),
        _stage(
            CERI_PROVIDER_PROGRESS_STAGE,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.DURABLE_BATCH,
        ),
        _stage(
            CERI_NORMALIZE_PROGRESS_STAGE,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.DURABLE_BATCH,
        ),
        _stage(
            CERI_FEATURE_PROGRESS_STAGE,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.DURABLE_BATCH,
        ),
        _stage(
            CERI_FEATURE_PREPARE_PROGRESS_STAGE,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.DURABLE_BATCH,
        ),
        _stage(
            CERI_CAPTURE_PROGRESS_STAGE,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
        ),
        _stage(
            CERI_CHANGE_DETECTION_PROGRESS_STAGE,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
        ),
        _stage(
            CERI_PIPELINE_CAPTURE_STEP,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
        ),
        _stage(
            SLSE_PIPELINE_CAPTURE_STEP,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
        ),
        _stage(
            SLSE_PIPELINE_EVALUATION_STEP,
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
        ),
        _stage(
            "CAPTURING_WINNER_PREDICTIONS",
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
        ),
        _stage("RESOLVING_IDENTIFIERS", StageExecutionClass.LONG_RUNNING),
        _stage(
            "PREPARING_SEC_EVIDENCE",
            StageExecutionClass.LONG_RUNNING,
            checkpoint_semantics=StageCheckpointSemantics.DURABLE_BATCH,
        ),
        _stage("RECHECKING_READINESS", StageExecutionClass.LONG_RUNNING),
        _stage("RETRYING_TRANSIENT_FAILURES", StageExecutionClass.LONG_RUNNING),
    )
}


def stage_metadata(stage: str | None) -> PipelineStageMetadata:
    if stage is None:
        return _stage("UNSPECIFIED", StageExecutionClass.BOUNDED)
    return PIPELINE_STAGE_METADATA.get(stage, _stage(stage, StageExecutionClass.BOUNDED))


def progress_timeout_seconds(
    stage: str | None,
    *,
    default_timeout_seconds: int,
    market_data_timeout_seconds: int,
    long_stage_timeout_seconds: int,
) -> int:
    execution_class = stage_metadata(stage).execution_class
    if execution_class is StageExecutionClass.MARKET_DATA:
        return market_data_timeout_seconds
    if execution_class is StageExecutionClass.LONG_RUNNING:
        return long_stage_timeout_seconds
    return default_timeout_seconds
