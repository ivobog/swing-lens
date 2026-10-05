from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.ceri.constants import (
    CERI_PIPELINE_CAPTURE_STEP,
    CERI_PIPELINE_PROVIDER_INGEST_STEP,
    CERI_PIPELINE_STEPS,
)
from app.services.setup_lifecycle.constants import (
    SLSE_PIPELINE_CAPTURE_STEP,
    SLSE_PIPELINE_EVALUATION_STEP,
    SLSE_PIPELINE_STEPS,
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
DECISION_HANDOFF_PIPELINE_STEP = "FREEZING_DECISION_HANDOFF_MANIFEST"
SECTOR_ROTATION_PIPELINE_STEP = "SECTOR_ROTATION_SNAPSHOT"
WINNER_CAPTURE_PIPELINE_STEP = "CAPTURING_WINNER_PREDICTIONS"

CANONICAL_BASE_PIPELINE_STAGES = (
    "VALIDATING_RUN",
    "SCORING_FUNDAMENTALS",
    "FETCHING_MARKET_DATA",
    "SCORING_TECHNICALS",
    "MARKET_REGIME_SNAPSHOT",
    "COMBINING_RESULTS",
    "RANKING_PROFILES",
    SECTOR_ROTATION_PIPELINE_STEP,
)
CANONICAL_PIPELINE_STAGES = frozenset(
    (
        *CANONICAL_BASE_PIPELINE_STAGES,
        CERI_PIPELINE_PROVIDER_INGEST_STEP,
        *CERI_PIPELINE_STEPS,
        DECISION_HANDOFF_PIPELINE_STEP,
        *SLSE_PIPELINE_STEPS,
        WINNER_CAPTURE_PIPELINE_STEP,
    )
)


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
_PIPELINE_STAGE_METADATA_ENTRIES = (
    _stage("VALIDATING_RUN", StageExecutionClass.BOUNDED),
    _stage("SCORING_FUNDAMENTALS", StageExecutionClass.BOUNDED),
    _stage("FETCHING_MARKET_DATA", StageExecutionClass.MARKET_DATA),
    _stage("SCORING_TECHNICALS", StageExecutionClass.LONG_RUNNING),
    _stage("MARKET_REGIME_SNAPSHOT", StageExecutionClass.BOUNDED),
    _stage("COMBINING_RESULTS", StageExecutionClass.BOUNDED),
    _stage("RANKING_PROFILES", StageExecutionClass.BOUNDED),
    _stage(
        SECTOR_ROTATION_PIPELINE_STEP,
        StageExecutionClass.LONG_RUNNING,
        checkpoint_semantics=StageCheckpointSemantics.DURABLE_BATCH,
    ),
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
        DECISION_HANDOFF_PIPELINE_STEP,
        StageExecutionClass.LONG_RUNNING,
        checkpoint_semantics=StageCheckpointSemantics.ITEM_PROGRESS,
    ),
    _stage(
        WINNER_CAPTURE_PIPELINE_STEP,
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

PIPELINE_STAGE_METADATA = {
    metadata.identity: metadata for metadata in _PIPELINE_STAGE_METADATA_ENTRIES
}


def validate_canonical_stage_registry() -> None:
    identities = tuple(metadata.identity for metadata in _PIPELINE_STAGE_METADATA_ENTRIES)
    duplicates = sorted({identity for identity in identities if identities.count(identity) > 1})
    missing = sorted(CANONICAL_PIPELINE_STAGES.difference(PIPELINE_STAGE_METADATA))
    if duplicates or missing:
        raise RuntimeError(
            "PIPELINE_STAGE_REGISTRY_INCOMPLETE: "
            f"missing={missing or []} duplicates={duplicates or []}"
        )


validate_canonical_stage_registry()


def stage_metadata(stage: str | None) -> PipelineStageMetadata:
    if stage is None:
        return _stage("UNSPECIFIED", StageExecutionClass.BOUNDED)
    if stage in CANONICAL_PIPELINE_STAGES and stage not in PIPELINE_STAGE_METADATA:
        raise RuntimeError(f"PIPELINE_STAGE_REGISTRY_MISSING:{stage}")
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
