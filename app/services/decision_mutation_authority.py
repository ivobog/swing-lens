"""Persisted decision authority, resolved within the native writer transaction."""

from datetime import date

from sqlalchemy import select, tuple_

from app.models.tables import PriceBar
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_mutation_authority import require_payload_reference
from app.services.price_bar_evidence import (
    price_bar_immutable_evidence_manifest,
    price_bar_immutable_evidence_set_hash,
)
from app.services.price_bar_repository import project_price_bar_rows_as_of


def lock_decision_scope(db, scope):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    if isinstance(db, Session) and db.get_bind().dialect.name == "postgresql":
        with db.no_autoflush:
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
                {"scope": Canonical.dumps(scope)},
            )


def operational_decision_authority(db, *, writer, manifest, run_id=None, job_types=()):
    """Fence bookkeeping explicitly; this confers no financial evidence permission."""
    from sqlalchemy.orm import Session

    from app.models.tables import BackgroundJob, UploadRun
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationDomain,
        MutationEntryPointDescriptor,
        MutationEvidenceReference,
        MutationSemanticMode,
        MutationWriterDescriptor,
        fence_mutation_transaction,
    )
    from app.services.domain_write_fence import current_domain_write_ownership

    if not isinstance(db, Session):
        return
    if run_id is not None and db.get(UploadRun, run_id) is None:
        raise ValueError("MUTATION_OPERATIONAL_SOURCE_RUN_MISSING")
    ownership = current_domain_write_ownership()
    fingerprint = Canonical.fingerprint(manifest)
    context = DomainMutationContext(
        domain=MutationDomain.OPERATIONAL,
        semantic_mode=MutationSemanticMode.OPERATIONAL,
        entrypoint=MutationEntryPointDescriptor(writer, "SUPPORTED_DISTINCT_SAFE"),
        writer=MutationWriterDescriptor(
            writer, "phase5-operational-decision-v1", MutationDomain.OPERATIONAL
        ),
        reason="Mutate explicitly scoped bookkeeping without financial evidence authority",
        evidence=(
            MutationEvidenceReference(
                "native_operation_scope", "native_manifest", fingerprint, fingerprint
            ),
        ),
        run_id=run_id,
        durable=ownership is not None,
        execution=ownership,
    )
    fence_mutation_transaction(db, context)
    if ownership is not None:
        job = db.get(BackgroundJob, ownership.job_id)
        if job.job_type not in job_types or (
            run_id is not None and job.related_run_id != run_id
        ):
            raise ValueError("MUTATION_OPERATIONAL_EXECUTION_SCOPE_MISMATCH")


def validate_episode_projection(db, episode):
    from app.models.tables import SetupLifecycleEvaluationEvidence, SetupLifecycleTransitionEvidence

    prior = db.get(SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id)
    validate_retained_decision(
        db,
        prior,
        contract="setup-lifecycle-evaluation-evidence-v1",
        payload_key="payload_fingerprint",
    )
    if (
        prior.ticker != episode.ticker
        or prior.timeframe != episode.timeframe
        or prior.setup_family != episode.setup_family
        or prior.output_state != episode.current_state
        or prior.output_phase != episode.current_phase
        or prior.decision_session > episode.current_as_of_date
        or prior.payload_json.get("execution_semantics") == "CURRENT_RULES_RETROSPECTIVE"
    ):
        raise ValueError("MUTATION_LIFECYCLE_EPISODE_EVIDENCE_MISMATCH")
    expected = prior.payload_json.get("projection_after")
    if not isinstance(expected, dict):
        raise ValueError("MUTATION_LIFECYCLE_CERTIFIED_PROJECTION_REQUIRED")
    actual = {key: getattr(episode, key) for key in expected}
    if Canonical.dumps(actual) != Canonical.dumps(expected):
        differences = sorted(
            key
            for key in expected
            if Canonical.dumps(actual[key]) != Canonical.dumps(expected[key])
        )
        raise ValueError("MUTATION_LIFECYCLE_EPISODE_PROJECTION_MISMATCH: " + ",".join(differences))
    bound_id = prior.payload_json.get("episode_id")
    if bound_id is not None and bound_id != episode.id:
        raise ValueError("MUTATION_LIFECYCLE_EPISODE_TARGET_MISMATCH")
    transition = db.get(SetupLifecycleTransitionEvidence, episode.latest_transition_evidence_id)
    validate_retained_decision(db, transition, contract="setup-lifecycle-transition-evidence-v1")
    if (
        transition.payload_json.get("episode_id") != episode.id
        or transition.ticker != episode.ticker
        or transition.timeframe != episode.timeframe
        or transition.setup_family != episode.setup_family
    ):
        raise ValueError("MUTATION_LIFECYCLE_EPISODE_TARGET_MISMATCH")


def lifecycle_projection_after(snapshot, episode, decision, actionability, completed_sessions):
    from app.services.setup_lifecycle.episode_service import _episode_metadata

    changed_state = episode is None or episode.current_state != decision.proposed_state.value
    return {
        "ticker": snapshot.ticker,
        "timeframe": snapshot.timeframe,
        "setup_family": decision.setup_family.value,
        "status": "CLOSED" if decision.proposed_state.value in {"FAILED", "EXPIRED"} else "ACTIVE",
        "current_as_of_date": snapshot.data_as_of_date,
        "last_observed_on": snapshot.data_as_of_date,
        "missing_observation_sessions": 0,
        "current_state": decision.proposed_state.value,
        "current_phase": decision.phase_code,
        "state_entered_on": snapshot.data_as_of_date if changed_state else episode.state_entered_on,
        "state_age_sessions": 0
        if changed_state
        else episode.state_age_sessions + completed_sessions,
        "current_actionability": actionability.actionability.value,
        "confidence_score": decision.confidence_score,
        "confidence_label": decision.confidence_label.value,
        "opening_snapshot_id": snapshot.id if episode is None else episode.opening_snapshot_id,
        "current_snapshot_id": snapshot.id,
        "engine_version": snapshot.engine_version if episode is None else episode.engine_version,
        "config_version": snapshot.config_version if episode is None else episode.config_version,
        "config_hash": snapshot.config_hash if episode is None else episode.config_hash,
        "metadata_json": _episode_metadata(snapshot, decision, actionability),
    }


def validate_setup_inputs(
    db, snapshot, identity, sources, *, configuration, declaration_only=False
):
    lineage = snapshot.source_lineage_json or {}
    pins = lineage.get("source_ids") or {}
    if "technical" not in sources:
        raise ValueError("MUTATION_SETUP_TECHNICAL_EVIDENCE_REQUIRED")
    for role, source in sources.items():
        key = "ranking" if role == "ranking_metadata" else role
        if pins.get(key + "_evidence_id") != source.evidence_id:
            raise ValueError("MUTATION_SETUP_SOURCE_ADDRESS_MISMATCH: " + role)
    require_payload_reference(
        identity,
        "setup-lifecycle-input-envelope",
        {
            "source_ids": pins,
            "data_as_of_date": snapshot.data_as_of_date,
            "latest_bar": lineage.get("latest_bar"),
            "trigger_reference": lineage.get("trigger_reference"),
            "native_calculation_inputs": lineage.get("native_calculation_inputs"),
        },
    )
    price_manifest = lineage.get("pit_price_evidence")
    if not isinstance(price_manifest, dict) or not isinstance(price_manifest.get("bars"), list):
        raise ValueError("MUTATION_SETUP_PIT_PRICE_AUTHORITY_REQUIRED")
    rows = []
    seen = set()
    addresses = []
    for pin in price_manifest["bars"]:
        fields = pin["fields"]
        scope = (fields["ticker"], fields["bar_date"], fields["timeframe"], fields["what_to_show"])
        if scope in seen or fields["ticker"] != snapshot.ticker:
            raise ValueError("MUTATION_SETUP_PRICE_SCOPE_MISMATCH")
        seen.add(scope)
        session = date.fromisoformat(fields["bar_date"])
        if session > identity.temporal.as_of_session.value:
            raise ValueError("MUTATION_SETUP_PRICE_ADDRESS_MISSING")
        addresses.append((fields["ticker"], session, fields["timeframe"], fields["what_to_show"]))
    # Retain the same exact addresses and row locks, then project all of their
    # immutable observations at the declared cutoff in one native batch.
    retained = (
        list(
            db.scalars(
                select(PriceBar)
                .where(
                    tuple_(
                        PriceBar.ticker,
                        PriceBar.bar_date,
                        PriceBar.timeframe,
                        PriceBar.what_to_show,
                    ).in_(addresses)
                )
                .with_for_update()
            )
        )
        if addresses
        else []
    )
    projected = project_price_bar_rows_as_of(
        db, retained, as_of=identity.temporal.calculation_cutoff.value
    )
    by_address = {}
    for row in projected:
        address = (row.ticker, row.bar_date, row.timeframe, row.what_to_show)
        if address in by_address:
            raise ValueError("MUTATION_SETUP_PRICE_SCOPE_MISMATCH")
        by_address[address] = row
    for pin, address in zip(price_manifest["bars"], addresses, strict=True):
        row = by_address.get(address)
        if row is None:
            raise ValueError("MUTATION_SETUP_PRICE_ADDRESS_MISSING")
        if Canonical.fingerprint(
            price_bar_immutable_evidence_manifest(row)
        ) != Canonical.fingerprint(pin):
            raise ValueError("MUTATION_SETUP_PIT_PRICE_FINGERPRINT_MISMATCH")
        rows.append(row)
    if price_bar_immutable_evidence_set_hash(rows) != price_manifest.get("series_fingerprint"):
        raise ValueError("MUTATION_SETUP_PRICE_SET_MISMATCH")
    if not declaration_only:
        validate_native_setup_output(db, snapshot, identity, rows, configuration)
    return price_manifest


def validate_native_setup_output(db, snapshot, identity, price_rows, configuration):
    """Recompute Setup from exact retained inputs, never caller-promoted scores."""
    from app.models.tables import (
        CombinedResult,
        CoreCalculationEvidence,
        FundamentalScore,
        MarketRegimeSnapshot,
        RankingResult,
        RawCompanyRow,
        SectorRotationRow,
        SectorRotationSnapshot,
        SetupSignalSnapshot,
        SetupSignalSnapshotCurrentSelection,
        TechnicalScore,
    )
    from app.services.core_calculation_evidence import calculation_evidence_payload
    from app.services.core_mutation_authority import (
        _validate_evidence,
        identity_cutoff,
        validate_financial_source_context,
        validate_source_address,
        validate_source_configuration,
        validate_source_values,
    )
    from app.services.decision_effective_configuration import DecisionEffectiveConfiguration
    from app.services.domain_write_fence import current_domain_write_ownership
    from app.services.setup_lifecycle.decision_evidence import _SETUP_PROJECTION_FIELDS
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository
    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
    from app.services.setup_lifecycle.source_loader import TickerSourceContext

    lineage = snapshot.source_lineage_json or {}
    inputs = lineage.get("native_calculation_inputs")
    if not isinstance(inputs, dict):
        raise ValueError("MUTATION_SETUP_NATIVE_INPUTS_REQUIRED")
    pins = lineage.get("source_ids") or {}
    raw = db.scalar(
        select(RawCompanyRow).where(RawCompanyRow.id == inputs.get("raw_row_id")).with_for_update()
    )
    if (
        raw is None
        or raw.id != pins.get("raw_row_id")
        or raw.run_id != snapshot.run_id
        or raw.ticker != snapshot.ticker
        or Canonical.fingerprint(raw.raw_json) != inputs.get("raw_fingerprint")
    ):
        raise ValueError("MUTATION_SETUP_RAW_INPUT_MISMATCH")
    selected = {}
    for field, model, pin in (
        ("fundamental_score", FundamentalScore, "fundamental_score_id"),
        ("technical_score", TechnicalScore, "technical_score_id"),
        ("combined_result", CombinedResult, "combined_result_id"),
        ("market_regime_snapshot", MarketRegimeSnapshot, "market_regime_snapshot_id"),
        ("sector_rotation_snapshot", SectorRotationSnapshot, "sector_rotation_snapshot_id"),
    ):
        row_id = pins.get(pin)
        selected[field] = db.get(model, row_id) if row_id is not None else None
        if row_id is not None and selected[field] is None:
            raise ValueError("MUTATION_SETUP_NATIVE_SOURCE_MISSING: " + field)
    if selected["fundamental_score"] is not None:
        from app.services.combined_ranking_identity import (
            calculation_identity_from_debug,
            require_fundamental_raw_source,
        )

        require_fundamental_raw_source(
            calculation_identity_from_debug(selected["fundamental_score"].debug_json),
            raw,
            consumer="setup",
        )
    rankings = []
    for pin in inputs.get("ranking_results", ()):
        ranking = db.scalar(
            select(RankingResult).where(RankingResult.id == pin["id"]).with_for_update()
        )
        evidence = db.get(CoreCalculationEvidence, pin.get("evidence_id"))
        if ranking is None or ranking.evidence_id != pin.get("evidence_id"):
            raise ValueError("MUTATION_SETUP_RANKING_INPUT_MISMATCH")
        _validate_evidence(evidence)
        validate_source_address(identity, "ranking_metadata", ranking, evidence)
        validate_source_values(ranking, evidence, db=db)
        validate_financial_source_context(identity, evidence)
        validate_source_configuration(db, identity, evidence, current_domain_write_ownership())
        rankings.append(ranking)
    sector_id = inputs.get("sector_row_id")
    sector_row = db.get(SectorRotationRow, sector_id) if sector_id is not None else None
    sector_snapshot = selected["sector_rotation_snapshot"]
    if sector_id is not None and (
        sector_row is None
        or sector_snapshot is None
        or sector_row.snapshot_id != sector_snapshot.id
    ):
        raise ValueError("MUTATION_SETUP_SECTOR_ROW_INPUT_MISMATCH")
    if sector_row is not None:
        from app.services.setup_lifecycle.source_loader import _sector_key

        if _sector_key(sector_row.sector) != _sector_key(raw.sector_canonical or raw.sector):
            raise ValueError("MUTATION_SETUP_SECTOR_ROW_TARGET_MISMATCH")
    history = []
    for pin in inputs.get("history", ()):
        prior = db.scalar(
            select(SetupSignalSnapshot).where(SetupSignalSnapshot.id == pin["id"]).with_for_update()
        )
        if (
            prior is None
            or prior.evidence_id != pin.get("evidence_id")
            or prior.ticker != snapshot.ticker
            or prior.timeframe != snapshot.timeframe
            or prior.data_as_of_date >= snapshot.data_as_of_date
            or prior.is_canonical != pin.get("is_canonical")
        ):
            raise ValueError("MUTATION_SETUP_HISTORY_INPUT_MISMATCH")
        validate_setup_projection(db, prior)
        if pin.get("is_canonical") and not db.scalar(
            select(SetupSignalSnapshotCurrentSelection.id).where(
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == prior.id
            )
        ):
            raise ValueError("MUTATION_SETUP_HISTORY_SELECTION_AUTHORITY_REQUIRED")
        history.append(prior)
    config = DecisionEffectiveConfiguration(configuration)
    context = TickerSourceContext(
        raw_row=raw,
        **selected,
        ranking_results=tuple(rankings),
        ranking_results_by_profile={row.ranking_profile: row for row in rankings},
        sector_rotation_row=sector_row,
        price_bars=tuple(price_rows),
        market_cutoff=identity_cutoff(db, identity),
    )
    dto = (
        SetupLifecycleSnapshotBuilder(config.setup_config())
        .build(context, history=tuple(history))
        .dto
    )
    expected = SetupLifecycleRepository(config.setup_config())._snapshot_from_write(dto)
    excluded = _SETUP_PROJECTION_FIELDS | {"calculated_at", "origin_type"}
    expected_payload = calculation_evidence_payload(expected, excluded_columns=excluded)
    actual_payload = calculation_evidence_payload(snapshot, excluded_columns=excluded)
    changed = [key for key, value in expected_payload.items() if actual_payload.get(key) != value]
    if changed:
        raise ValueError("MUTATION_SETUP_NATIVE_OUTPUT_MISMATCH: " + ",".join(sorted(changed)))


def validate_setup_projection(db, snapshot):
    from app.models.tables import CoreCalculationEvidence
    from app.services.calculation_identity import CalculationIdentity
    from app.services.core_calculation_evidence import calculation_evidence_payload
    from app.services.core_mutation_authority import (
        projection_mutation_context,
        validate_projection_mutation,
    )
    from app.services.setup_lifecycle.decision_evidence import _SETUP_PROJECTION_FIELDS

    with db.no_autoflush:
        evidence = db.get(CoreCalculationEvidence, snapshot.evidence_id)
        if evidence is None or evidence.artifact_kind != "SETUP":
            raise ValueError("MUTATION_SETUP_CERTIFIED_PROJECTION_TARGET_REQUIRED")
        validate_projection_mutation(db, evidence, projection_mutation_context(evidence))
        identity = CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json)
        if (
            evidence.ticker != snapshot.ticker
            or evidence.run_id != snapshot.run_id
            or evidence.payload_json.get("timeframe") != snapshot.timeframe
            or identity.temporal.as_of_session.value
            != (snapshot.input_as_of_session or snapshot.data_as_of_date)
            or identity.temporal.calculation_cutoff.value != snapshot.calculation_cutoff_at
        ):
            raise ValueError("MUTATION_SETUP_PROJECTION_SCOPE_MISMATCH")
        current = calculation_evidence_payload(snapshot, excluded_columns=_SETUP_PROJECTION_FIELDS)
        changed = sorted(
            key
            for key, value in evidence.payload_json.items()
            if key in current and current.get(key) != value
        )
        if changed:
            raise ValueError(
                "MUTATION_SETUP_PROJECTION_PAYLOAD_MISMATCH: " + ",".join(changed)
            )


def validate_retained_decision(db, row, *, contract, payload_key="payload"):
    """A prior decision keeps its own identity/configuration; never rebind it."""
    from app.models.tables import CoreCalculationEvidence
    from app.services.core_mutation_authority import _validate_evidence

    if isinstance(row, CoreCalculationEvidence):
        _validate_evidence(row)
        return
    if row is None or Canonical.fingerprint(row.payload_json) != row.payload_fingerprint:
        raise ValueError("MUTATION_DECISION_EVIDENCE_FINGERPRINT_MISMATCH")
    if row.evidence_key != Canonical.fingerprint(
        {"contract": contract, payload_key: row.payload_fingerprint}
    ):
        raise ValueError("MUTATION_DECISION_EVIDENCE_KEY_MISMATCH")
    aliases = {"counters_json": "counters", "reasons_json": "reasons", "warnings_json": "warnings"}
    for column in row.__table__.columns:
        key = aliases.get(column.key, column.key)
        if key not in row.payload_json:
            continue
        actual, expected = getattr(row, column.key), row.payload_json[key]
        if column.key in {"reasons_json", "warnings_json"}:
            actual, expected = sorted(set(actual or [])), sorted(set(expected or []))
        if Canonical.dumps(actual) != Canonical.dumps(expected):
            raise ValueError("MUTATION_DECISION_EVIDENCE_COLUMNS_MISMATCH: " + column.key)


def decision_authority(
    db,
    *,
    domain,
    writer,
    identity,
    configuration,
    records,
    manifests=None,
    mutation_context=None,
    semantic_mode=None,
):
    """Compose native exact references with Phase 0/1/4 persisted authorities.

    The native writer supplies exact artifacts and output identity. Declaration
    construction is followed by independent retained-storage validation here.
    """
    from app.models.tables import CoreCalculationEvidence, UploadRun
    from app.services.calculation_identity import IdentityState
    from app.services.core_mutation_authority import (
        _validate_configuration,
        _validate_evidence,
        identity_cutoff,
    )
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationDomain,
        MutationEntryPointDescriptor,
        MutationEvidenceReference,
        MutationSemanticMode,
        MutationWriterDescriptor,
        fence_mutation_transaction,
    )
    from app.services.domain_write_fence import current_domain_write_ownership
    from app.services.market_calculation_context_service import (
        assert_pipeline_calculation_context,
        market_calculation_context_fingerprint,
    )

    if identity is None or configuration is None:
        raise ValueError("MUTATION_DECISION_IDENTITY_AND_CONFIGURATION_REQUIRED")
    ownership = current_domain_write_ownership()
    pins = []
    for role, record in sorted(records.items()):
        if record is None:
            raise ValueError("MUTATION_DECISION_SOURCE_MISSING: " + role)
        persisted = db.get(type(record), record.id)
        if persisted is None:
            raise ValueError("MUTATION_DECISION_SOURCE_MISSING: " + role)
        if isinstance(persisted, CoreCalculationEvidence):
            _validate_evidence(persisted)
        elif Canonical.fingerprint(persisted.payload_json) != persisted.payload_fingerprint:
            raise ValueError("MUTATION_DECISION_SOURCE_FINGERPRINT_MISMATCH: " + role)
        pins.append(
            MutationEvidenceReference(
                role,
                persisted.__tablename__,
                persisted.id,
                persisted.evidence_key,
            )
        )
    for role, manifest in sorted((manifests or {}).items()):
        digest = Canonical.fingerprint(manifest)
        pins.append(MutationEvidenceReference(role, "native_manifest", digest, digest))
    run_id = (
        identity.ownership.run_id.value
        if (identity.ownership.run_id.state is IdentityState.KNOWN)
        else None
    )
    pipeline_id = (
        identity.ownership.pipeline_id.value
        if (identity.ownership.pipeline_id.state is IdentityState.KNOWN)
        else None
    )
    expected = DomainMutationContext(
        domain=domain,
        semantic_mode=semantic_mode or MutationSemanticMode.CANONICAL_CALCULATION,
        entrypoint=MutationEntryPointDescriptor(writer, "NATIVE_SEMANTIC_WRITER"),
        writer=MutationWriterDescriptor(writer, "phase5-decision-writer-v1", domain),
        reason="Persist explicitly bound native decision",
        calculation_identity=identity,
        temporal=identity_cutoff(db, identity),
        configuration=configuration.snapshot.identity,
        evidence=tuple(pins),
        run_id=run_id,
        pipeline_run_id=pipeline_id,
        durable=ownership is not None,
        execution=ownership,
    )
    context = mutation_context if mutation_context is not None else expected
    if not isinstance(context, DomainMutationContext) or (
        context.domain != expected.domain
        or context.semantic_mode != expected.semantic_mode
        or context.writer != expected.writer
        or context.calculation_identity != identity
        or context.configuration != expected.configuration
        or context.temporal != expected.temporal
        or context.evidence != expected.evidence
        or context.run_id != run_id
        or context.pipeline_run_id != pipeline_id
        or context.execution != expected.execution
        or context.durable != expected.durable
    ):
        raise ValueError("MUTATION_DECISION_NATIVE_AUTHORITY_MISMATCH")
    fence_mutation_transaction(db, context)
    if context.execution is not None:
        from types import SimpleNamespace

        from app.services.domain_write_fence import assert_current_execution_ownership

        # Use the shared execution fence rather than taking an unconditional
        # job-row lock here. During long durable work this validates the token
        # without retaining a lock; the outer domain commit takes the mandatory
        # FOR UPDATE fence immediately before publication.
        job = SimpleNamespace(
            **assert_current_execution_ownership(
                db,
                job_id=context.execution.job_id,
                execution_token=context.execution.execution_token,
            )
        )
        # Daily maintenance explicitly targets all active episodes, across
        # their source upload runs. Its own job has no related upload. Prove
        # this native scope and exact retained Setup origin rather than copying
        # the original pipeline or dropping financial source-run traceability.
        from datetime import date, datetime, time
        from zoneinfo import ZoneInfo

        from app.services.market_clock_service import EXCHANGE_TIMEZONE, MarketClockService

        maintenance_cutoff = None
        maintenance_setup = records.get("setup")
        if (
            job.job_type == "SETUP_LIFECYCLE_DAILY_MAINTENANCE"
            and maintenance_setup is None
            and records.get("evaluation") is not None
        ):
            maintenance_setup = db.get(
                CoreCalculationEvidence, records["evaluation"].setup_evidence_id
            )
            _validate_evidence(maintenance_setup)
        if job.job_type == "SETUP_LIFECYCLE_DAILY_MAINTENANCE":
            maintenance_day = date.fromisoformat(str(job.payload_json["as_of_date"]))
            maintenance_cutoff = MarketClockService().cutoff_for(
                datetime.combine(maintenance_day, time.max, tzinfo=ZoneInfo(EXCHANGE_TIMEZONE)),
                reason="LIFECYCLE_CURRENT_STATE_MAINTENANCE_AS_OF_DAY",
            )
        maintenance_scope = (
            job.job_type == "SETUP_LIFECYCLE_DAILY_MAINTENANCE"
            and job.related_run_id is None
            and pipeline_id is None
            and domain
            in {
                MutationDomain.LIFECYCLE_EVALUATION,
                MutationDomain.LIFECYCLE_TRANSITION,
                MutationDomain.ALERT_DECISION,
            }
            and maintenance_setup is not None
            and maintenance_setup.run_id == run_id
            and job.payload_json.get("market_session_completed", True) is True
            and job.payload_json.get("as_of_date") is not None
            and maintenance_cutoff is not None
            and context.temporal.context_id is None
            and all(
                getattr(context.temporal, field) == getattr(maintenance_cutoff, field)
                for field in (
                    "cutoff_at",
                    "latest_completed_session",
                    "exchange_timezone",
                    "calendar_version",
                    "bar_readiness_version",
                )
            )
        )
        if run_id is not None and job.related_run_id != run_id and not maintenance_scope:
            raise ValueError("MUTATION_DECISION_EXECUTION_RUN_SCOPE_MISMATCH")
        if pipeline_id is not None and job.payload_json.get("pipeline_run_id") != pipeline_id:
            raise ValueError("MUTATION_DECISION_EXECUTION_PIPELINE_SCOPE_MISMATCH")
    if run_id is not None and db.get(UploadRun, run_id) is None:
        raise ValueError("MUTATION_DECISION_RUN_MISSING")
    if pipeline_id is not None:
        retained = assert_pipeline_calculation_context(
            db,
            pipeline_run_id=pipeline_id,
            upload_run_id=run_id,
            supplied_context=context.temporal,
        )
        fingerprint = identity.calculation_context.context_fingerprint.value
        if fingerprint is None or fingerprint.digest != market_calculation_context_fingerprint(
            retained
        ):
            raise ValueError("MUTATION_DECISION_CONTEXT_FINGERPRINT_MISMATCH")
    _validate_configuration(db, context, configuration.snapshot)
    return context


def lifecycle_evaluation_authority(
    db,
    *,
    snapshot,
    setup,
    episode,
    configuration,
    mutation_context=None,
    prior_snapshots=(),
    evaluation_run_id=None,
):
    from app.services.calculation_identity import CalculationIdentity
    from app.services.contextual_calculation_identity import build_contextual_result_identity
    from app.services.domain_mutation import MutationDomain, MutationSemanticMode

    validate_setup_projection(db, snapshot)
    identity = configuration.bind(
        build_contextual_result_identity(
            base=CalculationIdentity.from_canonical_payload(setup.calculation_identity_json),
            namespace="lifecycle-evaluation",
            config_hash=configuration.snapshot.semantic_hash,
            calculation_version=snapshot.engine_version,
            engine_version=snapshot.engine_version,
            source_artifacts=(),
            source_payload={
                "setup_evidence_id": setup.id,
                "episode_id": getattr(episode, "id", None),
                "prior_snapshot_ids": [
                    item.source_ids.get("snapshot_id") for item in prior_snapshots
                ],
                "prior_evaluation_evidence_id": getattr(
                    episode, "latest_evaluation_evidence_id", None
                ),
                "prior_transition_evidence_id": getattr(
                    episode, "latest_transition_evidence_id", None
                ),
            },
        )
    )
    manifests = {}
    from app.models.tables import SetupLifecycleEvaluationRun

    run = (
        db.get(SetupLifecycleEvaluationRun, evaluation_run_id)
        if evaluation_run_id is not None
        else None
    )
    if evaluation_run_id is not None and (
        run is None or run.source_run_id not in {None, snapshot.run_id}
    ):
        raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_SCOPE_MISMATCH")
    if run is not None:
        native_config = configuration.setup_config()
        if (
            run.config_hash != native_config.config_hash
            or run.config_version != native_config.engine.config_version
            or run.engine_version != native_config.engine.version
        ):
            raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_CONFIGURATION_MISMATCH")
        if run.mode == "REPLAY":
            from dataclasses import replace

            from app.services.calculation_identity import IdentityDimension

            # Current-rules replay is a new operation. Its retained Setup input
            # keeps the original pipeline proof; that pipeline does not own the
            # replay's configuration or authorize advancing current episodes.
            identity = replace(
                identity,
                ownership=replace(
                    identity.ownership, pipeline_id=IdentityDimension.not_applicable()
                ),
            )
    if episode is not None:
        validate_episode_projection(db, episode)
        from app.models.tables import SetupLifecycleEvaluationEvidence

        prior = db.get(SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id)
        if (prior.payload_json.get("effective_configuration_at_creation") or {}).get(
            "semantic_hash"
        ) != configuration.snapshot.semantic_hash:
            raise ValueError("MUTATION_LIFECYCLE_EPISODE_CONFIGURATION_MISMATCH")
        if episode.current_as_of_date > snapshot.data_as_of_date:
            raise ValueError("MUTATION_LIFECYCLE_EPISODE_REGRESSION")
        if episode.ticker != snapshot.ticker or episode.timeframe != snapshot.timeframe:
            raise ValueError("MUTATION_LIFECYCLE_EPISODE_SCOPE_MISMATCH")
        manifests["previous_episode"] = {
            "id": episode.id,
            "evaluation": episode.latest_evaluation_evidence_id,
            "transition": episode.latest_transition_evidence_id,
        }
    if run is None or run.mode != "REPLAY":
        from app.models.tables import SetupLifecycleEpisode

        future = db.scalar(
            select(SetupLifecycleEpisode.id)
            .where(
                SetupLifecycleEpisode.ticker == snapshot.ticker,
                SetupLifecycleEpisode.timeframe == snapshot.timeframe,
                SetupLifecycleEpisode.current_as_of_date > snapshot.data_as_of_date,
            )
            .limit(1)
        )
        if future is not None:
            raise ValueError("MUTATION_LIFECYCLE_EPISODE_REGRESSION")
    return decision_authority(
        db,
        domain=MutationDomain.LIFECYCLE_EVALUATION,
        writer="persist_lifecycle_evaluation_evidence",
        identity=identity,
        configuration=configuration,
        records={"setup": setup},
        manifests=manifests,
        mutation_context=mutation_context,
        semantic_mode=(
            MutationSemanticMode.CURRENT_RULES_RETROSPECTIVE
            if run is not None and run.mode == "REPLAY"
            else MutationSemanticMode.CANONICAL_CALCULATION
        ),
    )


def validate_native_lifecycle_output(
    db,
    *,
    snapshot,
    episode,
    decision,
    actionability,
    configuration,
    prior_snapshots,
    evaluation_run_id,
    transition_eligible,
):
    from dataclasses import asdict, replace

    from app.models.tables import SetupLifecycleEvaluationRun, SetupSignalSnapshot
    from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
    from app.services.setup_lifecycle.enums import LifecycleState, SetupFamily
    from app.services.setup_lifecycle.episode_service import (
        _opens_episode,
        _persistence_sessions,
        _request,
        normalized_snapshot_from_row,
    )
    from app.services.setup_lifecycle.lifecycle_engine import SetupLifecycleEngine
    from app.services.technical_consumer_eligibility import setup_technical_blocked

    history = []
    seen = set()
    for supplied in prior_snapshots:
        prior_id = supplied.source_ids.get("snapshot_id")
        prior = db.get(SetupSignalSnapshot, prior_id) if prior_id is not None else None
        if (
            prior is None
            or prior_id in seen
            or prior.ticker != snapshot.ticker
            or prior.timeframe != snapshot.timeframe
            or prior.data_as_of_date >= snapshot.data_as_of_date
        ):
            raise ValueError("MUTATION_LIFECYCLE_HISTORY_SCOPE_MISMATCH")
        validate_setup_projection(db, prior)
        normalized = normalized_snapshot_from_row(prior)
        if Canonical.dumps(asdict(normalized)) != Canonical.dumps(asdict(supplied)):
            raise ValueError("MUTATION_LIFECYCLE_HISTORY_PAYLOAD_MISMATCH")
        seen.add(prior_id)
        history.append(normalized)
    native_config = configuration.setup_config()
    normalized = normalized_snapshot_from_row(snapshot)
    request_args = {"previous_snapshots": tuple(history)}
    if episode is not None:
        request_args.update(
            previous_state=LifecycleState(episode.current_state),
            previous_phase=episode.current_phase,
            previous_confidence_score=episode.confidence_score,
            state_age_sessions=episode.state_age_sessions,
            persistence_sessions=_persistence_sessions(episode),
            missing_observation_sessions=episode.missing_observation_sessions,
        )
    expected = SetupLifecycleEngine(config=native_config).evaluate(
        _request(normalized, **request_args)
    )
    if episode is not None and setup_technical_blocked(normalized):
        expected = replace(expected, setup_family=SetupFamily(episode.setup_family))
    expected_actionability = SetupLifecycleActionabilityPolicy(native_config).evaluate(
        expected, normalized
    )
    if Canonical.dumps(asdict(expected)) != Canonical.dumps(asdict(decision)):
        raise ValueError("MUTATION_LIFECYCLE_NATIVE_DECISION_MISMATCH")
    if (
        actionability.actionability != expected_actionability.actionability
        or tuple(actionability.blockers) != expected_actionability.blockers
    ):
        raise ValueError("MUTATION_LIFECYCLE_NATIVE_ACTIONABILITY_MISMATCH")
    run = db.get(SetupLifecycleEvaluationRun, evaluation_run_id) if evaluation_run_id else None
    expected_transition = (
        False
        if run is not None and run.mode == "REPLAY"
        else (
            _opens_episode(expected)
            if episode is None
            else episode.current_state != expected.proposed_state.value
            or episode.current_phase != expected.phase_code
        )
    )
    if transition_eligible != expected_transition:
        raise ValueError("MUTATION_LIFECYCLE_NATIVE_TRANSITION_ELIGIBILITY_MISMATCH")


def lifecycle_transition_authority(db, *, event, evaluation, prior_id, mutation_context=None):
    from app.models.tables import (
        SetupLifecycleEpisode,
        SetupLifecycleTransitionEvidence,
        SetupSignalSnapshot,
    )
    from app.services.calculation_identity import CalculationIdentity
    from app.services.decision_effective_configuration import configuration_from_payload
    from app.services.domain_mutation import MutationDomain
    from app.services.effective_configuration import CONFIGURATION_PAYLOAD_KEY

    validate_retained_decision(
        db,
        evaluation,
        contract="setup-lifecycle-evaluation-evidence-v1",
        payload_key="payload_fingerprint",
    )
    if (
        evaluation.ticker != event.ticker
        or evaluation.timeframe != event.timeframe
        or evaluation.setup_family != event.setup_family
        or evaluation.decision_session != event.effective_date
        or evaluation.output_state != event.to_state
        or evaluation.output_phase != event.to_phase
        or evaluation.previous_state != event.from_state
        or not evaluation.transition_eligible
        or evaluation.prior_transition_evidence_id != prior_id
    ):
        raise ValueError("MUTATION_LIFECYCLE_TRANSITION_EVALUATION_MISMATCH")
    episode = db.get(SetupLifecycleEpisode, event.episode_id)
    if (
        episode is None
        or episode.ticker != event.ticker
        or episode.timeframe != event.timeframe
        or episode.setup_family != event.setup_family
        or episode.latest_evaluation_evidence_id != evaluation.id
        or (
            evaluation.payload_json.get("episode_id") is not None
            and evaluation.payload_json["episode_id"] != episode.id
        )
    ):
        raise ValueError("MUTATION_LIFECYCLE_TRANSITION_EPISODE_MISMATCH")
    frozen = evaluation.payload_json
    gap = frozen.get("execution_semantics") == "CURRENT_STATE_REPAIR"
    if gap:
        confidence = frozen["confidence_score"]
        label = frozen["confidence_label"]
        actionability = "WATCH_ONLY"
        immediate = False
        reasons = ["OBSERVATION_GAP_EXPIRED"]
        native_evidence = {
            "missing_observation_sessions": frozen["counters"]["missing_observation_sessions"],
            "observation_gap_threshold": frozen["counters"]["observation_gap_threshold"],
        }
        if event.snapshot_id is not None:
            raise ValueError("MUTATION_LIFECYCLE_TRANSITION_SOURCE_MISMATCH")
    else:
        source = db.get(SetupSignalSnapshot, event.snapshot_id) if event.snapshot_id else None
        if source is None or source.evidence_id != evaluation.setup_evidence_id:
            raise ValueError("MUTATION_LIFECYCLE_TRANSITION_SOURCE_MISMATCH")
        if frozen.get("episode_id") is None and episode.opening_snapshot_id != source.id:
            raise ValueError("MUTATION_LIFECYCLE_TRANSITION_EPISODE_MISMATCH")
        confidence = frozen["decision"]["confidence_score"]
        label = frozen["decision"]["confidence_label"]
        actionability = frozen["decision"]["actionability"]
        immediate = frozen["decision"]["immediate_transition"]
        reasons = list(frozen["reasons"])
        if frozen["previous_state"] is None and frozen["output_state"] in {
            "READY",
            "TRIGGERED",
            "CONFIRMED",
            "EXTENDED",
        }:
            reasons.append("SKIPPED_PRIOR_PROGRESSION")
        metadata = frozen["projection_after"]["metadata_json"]
        native_evidence = {
            **frozen["decision"]["evidence"],
            "actionability": {
                "reason_codes": metadata["actionability_reason_codes"],
                "blockers": metadata["blockers"],
                "metadata": metadata["actionability_metadata"],
            },
        }
    native_type = (
        "EPISODE_OPENED"
        if event.from_state is None
        else ("STATE_TRANSITION" if event.from_state != event.to_state else "PHASE_TRANSITION")
    )
    from app.services.setup_lifecycle.enums import LifecycleState
    from app.services.setup_lifecycle.episode_service import _severity
    from app.services.setup_lifecycle.repository import SetupLifecycleRepository

    native_key = SetupLifecycleRepository.stable_key(
        "episode_event",
        str(event.evaluation_run_id or ""),
        str(episode.id),
        native_type,
        event.ticker,
        event.timeframe,
        event.setup_family,
        event.effective_date.isoformat(),
        event.from_state or "",
        event.to_state,
        event.from_phase or "",
        event.to_phase,
        episode.config_hash,
    )
    if (
        event.confidence_score != confidence
        or event.confidence_label != label
        or event.actionability_after != actionability
        or event.immediate_transition != immediate
        or event.event_type != native_type
        or sorted(set(event.reason_codes_json or [])) != sorted(set(reasons))
        or Canonical.dumps(event.evidence_json) != Canonical.dumps(native_evidence)
        or event.severity != _severity(LifecycleState(event.to_state)).value
        or event.source_event_key != native_key
        or event.evaluation_run_id != evaluation.evaluation_run_id
        or event.config_hash != episode.config_hash
    ):
        raise ValueError("MUTATION_LIFECYCLE_TRANSITION_NATIVE_OUTPUT_MISMATCH")
    manifests = {}
    if prior_id is not None:
        prior = db.get(SetupLifecycleTransitionEvidence, prior_id)
        validate_retained_decision(db, prior, contract="setup-lifecycle-transition-evidence-v1")
        if (
            prior.ticker != event.ticker
            or prior.timeframe != event.timeframe
            or (
                prior.setup_family != event.setup_family
                or prior.effective_session > event.effective_date
            )
        ):
            raise ValueError("MUTATION_LIFECYCLE_TRANSITION_PRIOR_MISMATCH")
        manifests["previous_episode"] = {"transition": prior.id, "key": prior.evidence_key}
    identity = CalculationIdentity.from_canonical_payload(
        evaluation.payload_json["calculation_identity"]
    )
    configuration = configuration_from_payload(evaluation.payload_json[CONFIGURATION_PAYLOAD_KEY])
    return decision_authority(
        db,
        domain=MutationDomain.LIFECYCLE_TRANSITION,
        writer="persist_lifecycle_transition_evidence",
        identity=identity,
        configuration=configuration,
        records={"evaluation": evaluation},
        manifests=manifests,
        mutation_context=mutation_context,
    )


def _alert_decision_authority(
    db,
    *,
    rule,
    ticker,
    timeframe,
    session,
    semantic_key,
    source_event_key,
    decision,
    setup_id,
    evaluation_id,
    transition_id,
    cooldown_id,
    dedup_id,
    configuration,
    mutation_context,
    payload,
    reasons,
):
    from app.models.tables import (
        CoreCalculationEvidence,
        SetupLifecycleEvaluationEvidence,
        SetupLifecycleTransitionEvidence,
        SignalAlertDecisionEvidence,
        SignalAlertRuleEvidence,
    )
    from app.services.calculation_identity import CalculationIdentity
    from app.services.contextual_calculation_identity import build_contextual_result_identity
    from app.services.domain_mutation import MutationDomain
    from app.services.setup_lifecycle.alert_service import _trading_sessions_between
    from app.services.setup_lifecycle.decision_evidence import (
        persist_alert_rule_evidence,
        prior_generated_alert_decision,
    )

    if configuration is None:
        raise ValueError("MUTATION_ALERT_CONFIGURATION_REQUIRED")
    if decision not in {"GENERATED", "SUPPRESSED_DEDUP", "SUPPRESSED_COOLDOWN", "INELIGIBLE"}:
        raise ValueError("MUTATION_ALERT_DECISION_MODE_REJECTED")
    configuration.require_family("decision.alerts.setup")
    selected = next(
        (r for r in configuration.values["rules"] if r["rule_id"] == rule.rule_id), None
    )
    if selected is None or any(
        Canonical.canonicalize(getattr(rule, k)) != v for k, v in selected.items()
    ):
        raise ValueError("MUTATION_ALERT_FROZEN_RULE_MISMATCH")
    lock_decision_scope(db, ("setup-alert", rule.rule_id, ticker.upper(), timeframe))
    from app.services.setup_lifecycle.alert_authority import validate_alert_condition

    native_condition = validate_alert_condition(
        db,
        rule=rule,
        payload=payload,
        ticker=ticker,
        timeframe=timeframe,
        session=session,
        source_event_key=source_event_key,
        semantic_key=semantic_key,
        setup_id=setup_id,
        evaluation_id=evaluation_id,
        transition_id=transition_id,
    )
    from app.services.setup_lifecycle.alert_service import (
        _market_restrictions_match,
        _reconstructed_source,
    )

    eligible = (
        rule.enabled
        and payload.get("source_confidence", 0) >= rule.minimum_confidence
        and _market_restrictions_match(rule, native_condition)
        and not _reconstructed_source(native_condition)
    )
    if decision in {"GENERATED", "SUPPRESSED_DEDUP", "SUPPRESSED_COOLDOWN"} and not eligible:
        raise ValueError("MUTATION_ALERT_NATIVE_PERMISSION_REJECTED")
    expected_reasons = {
        "GENERATED": (f"{rule.rule_id}_ALERT",),
        "SUPPRESSED_DEDUP": ("DUPLICATE_EVENT_KEY",),
        "SUPPRESSED_COOLDOWN": ("COOLDOWN_ACTIVE",),
    }
    if decision in expected_reasons and sorted(set(reasons)) != sorted(expected_reasons[decision]):
        raise ValueError("MUTATION_ALERT_NATIVE_REASONS_MISMATCH")
    if decision == "INELIGIBLE":
        if _reconstructed_source(native_condition):
            expected_ineligible = "RECONSTRUCTED_SUPPRESSED"
        elif not _market_restrictions_match(rule, native_condition):
            expected_ineligible = "MARKET_RESTRICTION"
        elif not rule.enabled:
            expected_ineligible = "RULE_DISABLED"
        elif payload.get("source_confidence", 0) < rule.minimum_confidence:
            expected_ineligible = "MINIMUM_CONFIDENCE"
        else:
            from app.models.tables import SignalAlertEvent
            from app.services.setup_lifecycle.repository import SetupLifecycleRepository

            legacy_key = SetupLifecycleRepository.alert_event_key(
                rule_id=rule.rule_id,
                source_event_key=source_event_key,
                ticker=ticker,
                episode_id=payload["episode_id"],
                effective_date=session,
                evaluation_run_id=payload["evaluation_run_id"],
            )
            legacy = db.scalar(
                select(SignalAlertEvent).where(
                    SignalAlertEvent.event_key == legacy_key,
                    SignalAlertEvent.decision_evidence_id.is_(None),
                )
            )
            if legacy is None:
                raise ValueError("MUTATION_ALERT_NATIVE_INELIGIBILITY_REQUIRED")
            expected_ineligible = "LEGACY_DEDUP_PREDECESSOR_UNKNOWN"
        if sorted(set(reasons)) != [expected_ineligible]:
            raise ValueError("MUTATION_ALERT_NATIVE_REASONS_MISMATCH")
    evaluation = db.get(SetupLifecycleEvaluationEvidence, evaluation_id) if evaluation_id else None
    if evaluation_id is not None:
        validate_retained_decision(
            db,
            evaluation,
            contract="setup-lifecycle-evaluation-evidence-v1",
            payload_key="payload_fingerprint",
        )
        if setup_id is not None and evaluation.setup_evidence_id != setup_id:
            raise ValueError("MUTATION_ALERT_EVALUATION_SETUP_MISMATCH")
        setup_id = evaluation.setup_evidence_id
        base = CalculationIdentity.from_canonical_payload(
            evaluation.payload_json["calculation_identity"]
        )
        if evaluation.payload_json.get("execution_semantics") == "CURRENT_STATE_REPAIR":
            source_confidence = evaluation.payload_json["confidence_score"]
        else:
            source_confidence = evaluation.payload_json["decision"]["confidence_score"]
        if payload.get("source_confidence") != source_confidence:
            raise ValueError("MUTATION_ALERT_SOURCE_CONFIDENCE_MISMATCH")
    else:
        setup = db.get(CoreCalculationEvidence, setup_id) if setup_id else None
        validate_retained_decision(db, setup, contract="unused")
        if setup.artifact_kind != "SETUP":
            raise ValueError("MUTATION_ALERT_SETUP_KIND_MISMATCH")
        base = CalculationIdentity.from_canonical_payload(setup.calculation_identity_json)
        evaluation = setup
    if isinstance(evaluation, CoreCalculationEvidence):
        native_session = evaluation.payload_json.get("data_as_of_date")
        if native_session is not None and not isinstance(native_session, date):
            native_session = date.fromisoformat(str(native_session))
    else:
        native_session = evaluation.decision_session
    if evaluation.ticker != ticker.upper() or native_session != session:
        raise ValueError("MUTATION_ALERT_SOURCE_SCOPE_OR_TIME_MISMATCH")
    if (
        isinstance(evaluation, SetupLifecycleEvaluationEvidence)
        and evaluation.timeframe != timeframe
    ):
        raise ValueError("MUTATION_ALERT_SOURCE_SCOPE_OR_TIME_MISMATCH")
    if (
        isinstance(evaluation, CoreCalculationEvidence)
        and evaluation.payload_json.get("timeframe") != timeframe
    ):
        raise ValueError("MUTATION_ALERT_SOURCE_SCOPE_OR_TIME_MISMATCH")
    records = {"evaluation": evaluation, "rule": persist_alert_rule_evidence(db, rule)}
    if transition_id is not None:
        transition = db.get(SetupLifecycleTransitionEvidence, transition_id)
        validate_retained_decision(
            db, transition, contract="setup-lifecycle-transition-evidence-v1"
        )
        if transition.evaluation_evidence_id != evaluation_id:
            raise ValueError("MUTATION_ALERT_TRANSITION_EVALUATION_MISMATCH")
        records["transition"] = transition
    prior = prior_generated_alert_decision(
        db,
        rule_id=rule.rule_id,
        ticker=ticker,
        timeframe=timeframe,
        semantic_key=semantic_key,
        effective_session=session,
    )
    active = (
        prior is not None
        and rule.cooldown_sessions > 0
        and (_trading_sessions_between(prior.effective_session, session) <= rule.cooldown_sessions)
    )
    if cooldown_id is not None and (not active or prior.id != cooldown_id):
        raise ValueError("MUTATION_ALERT_COOLDOWN_PREDECESSOR_MISMATCH")
    if cooldown_id is not None:
        validate_retained_decision(db, prior, contract="signal-alert-decision-evidence-v1")
        records["cooldown_predecessor"] = prior
    if decision == "SUPPRESSED_COOLDOWN" and cooldown_id is None:
        raise ValueError("MUTATION_ALERT_COOLDOWN_PREDECESSOR_REQUIRED")
    if decision == "GENERATED" and active:
        # A native deterministic retry may reuse its exact original decision.
        if prior.source_event_key != source_event_key:
            raise ValueError("MUTATION_ALERT_COOLDOWN_ACTIVE")
    if dedup_id is not None:
        duplicate = db.get(SignalAlertDecisionEvidence, dedup_id)
        validate_retained_decision(db, duplicate, contract="signal-alert-decision-evidence-v1")
        duplicate_rule = db.get(SignalAlertRuleEvidence, duplicate.rule_evidence_id)
        validate_retained_decision(db, duplicate_rule, contract="signal-alert-rule-evidence-v1")
        if (
            duplicate_rule.rule_id != rule.rule_id
            or duplicate.decision != "GENERATED"
            or duplicate.ticker != ticker.upper()
            or duplicate.timeframe != timeframe
            or (
                duplicate.source_event_key != source_event_key
                or duplicate.effective_session > session
            )
        ):
            raise ValueError("MUTATION_ALERT_DEDUP_PREDECESSOR_MISMATCH")
        records["dedup_predecessor"] = duplicate
    if decision == "SUPPRESSED_DEDUP" and dedup_id is None:
        raise ValueError("MUTATION_ALERT_DEDUP_PREDECESSOR_REQUIRED")
    if decision == "GENERATED" and (
        not rule.enabled or payload.get("source_confidence", 0) < rule.minimum_confidence
    ):
        raise ValueError("MUTATION_ALERT_DECISION_PERMISSION_REJECTED")
    identity = configuration.bind(
        build_contextual_result_identity(
            base=base,
            namespace="alert-decision",
            config_hash=configuration.snapshot.semantic_hash,
            calculation_version="alert-decision-v1",
            engine_version="alert-decision-v1",
            source_artifacts=(),
            source_payload={
                "rule": records["rule"].evidence_key,
                "evaluation": evaluation.evidence_key,
                "source_event_key": source_event_key,
                "semantic_key": semantic_key,
                "cooldown_predecessor": cooldown_id,
                "dedup_predecessor": dedup_id,
            },
        )
    )
    return decision_authority(
        db,
        domain=MutationDomain.ALERT_DECISION,
        writer="persist_alert_decision_evidence",
        identity=identity,
        configuration=configuration,
        records=records,
        mutation_context=mutation_context,
    )
