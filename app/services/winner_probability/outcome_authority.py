"""Outcome authority retains forecast rules and exact operation price contents."""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Numeric, select

from app.models.tables import PriceBar, PriceBarRevision
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_mutation_authority import _validate_configuration, core_writer_member
from app.services.decision_effective_configuration import configuration_from_payload
from app.services.decision_mutation_authority import lock_decision_scope
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
from app.services.market_clock_service import MarketClockService
from app.services.winner_probability.prediction_authority import validate_prediction_source
from app.services.winner_probability.trading_session_service import horizon_due_session

PROOF_KEY = "native_outcome_proof"
OBLIGATION_SCOPE_COLUMNS = (
    "prediction_id",
    "forward_outcome_id",
    "ib_contract_id",
    "ticker_snapshot",
    "ib_conid_snapshot",
    "symbol_snapshot",
    "local_symbol_snapshot",
    "exchange_snapshot",
    "primary_exchange_snapshot",
    "currency_snapshot",
    "sec_type_snapshot",
    "trading_class_snapshot",
    "entry_session",
    "required_through_session",
    "required_sessions_json",
    "timeframe",
    "what_to_show",
)


def obligation_scope_body(row):
    return {key: getattr(row, key) for key in OBLIGATION_SCOPE_COLUMNS}


def validate_obligation_scope(row):
    proof = (row.metadata_json or {}).get("native_obligation_scope")
    if not isinstance(proof, dict) or proof.get("fingerprint") != Canonical.fingerprint(
        obligation_scope_body(row)
    ):
        raise ValueError("MUTATION_WINNER_CERTIFIED_OBLIGATION_SCOPE_REQUIRED")
    return proof


OPERATIONAL_COLUMNS = {
    "id",
    "created_at",
    "updated_at",
    "is_current_revision",
    "superseded_at",
    "last_attempted_at",
    "retry_not_before_at",
    "first_pending_at",
    "pending_reason_code",
    "last_attempted_bar_watermark",
}


def outcome_body(row):
    body = {}
    for column in row.__table__.columns:
        if column.key in OPERATIONAL_COLUMNS:
            continue
        value = getattr(row, column.key)
        if column.key == "metadata_json":
            value = {
                key: item
                for key, item in (value or {}).items()
                if key
                not in {PROOF_KEY, "native_pending_proof", "pending_reason", "last_attempted_at"}
            }
        if value is not None and isinstance(column.type, Numeric):
            # PostgreSQL NUMERIC rounds ties away from zero when retaining its
            # declared scale. Seal that retained value before the SQL reload.
            value = Decimal(str(value)).quantize(
                Decimal(1).scaleb(-column.type.scale), rounding=ROUND_HALF_UP
            )
        body[column.key] = value
    return body


def validate_outcome_body(row):
    proof = (row.metadata_json or {}).get(PROOF_KEY)
    if not isinstance(proof, dict) or proof.get("contract") != "winner-native-outcome-v1":
        raise ValueError("MUTATION_WINNER_CERTIFIED_OUTCOME_REQUIRED")
    if proof.get("body_fingerprint") != Canonical.fingerprint(outcome_body(row)):
        raise ValueError("MUTATION_WINNER_OUTCOME_FINANCIAL_BODY_MISMATCH")
    return proof


def validate_retained_outcome(db, row):
    with db.no_autoflush:
        stored = (
            db.execute(
                select(*row.__table__.columns).where(row.__table__.c.id == row.id).with_for_update()
            )
            .mappings()
            .one_or_none()
        )
    if stored is None or any(
        Canonical.dumps(value) != Canonical.dumps(getattr(row, key))
        for key, value in stored.items()
    ):
        raise ValueError("MUTATION_WINNER_OUTCOME_RETAINED_PREDECESSOR_MISMATCH")
    if (
        getattr(row, "matured_at", None) is not None
        or getattr(row, "evaluated_at", None) is not None
    ):
        validate_outcome_body(row)
    elif row.source_bar_lineage_hash is not None:
        raise ValueError("MUTATION_WINNER_OUTCOME_UNCERTIFIED_FINANCIAL_VALUES")
    else:
        proof = (row.metadata_json or {}).get("native_pending_proof")
        if not isinstance(proof, dict) or proof.get("body_fingerprint") != Canonical.fingerprint(
            outcome_body(row)
        ):
            raise ValueError("MUTATION_WINNER_CERTIFIED_PENDING_OUTCOME_REQUIRED")


def refresh_exact_predecessor(db, row):
    """Reload a clean, sealed row by PK after locking; never select another revision."""
    with db.no_autoflush:
        stored = db.execute(
            select(row.__table__.c.metadata_json).where(row.__table__.c.id == row.id)
        ).scalar_one()
    metadata = row.metadata_json or {}
    pending = metadata.get("native_pending_proof")
    if pending != (stored or {}).get("native_pending_proof"):
        raise ValueError("MUTATION_WINNER_OUTCOME_RETAINED_PREDECESSOR_MISMATCH")
    proof = metadata.get(PROOF_KEY)
    if proof is not None:
        if proof != (stored or {}).get(PROOF_KEY):
            raise ValueError("MUTATION_WINNER_OUTCOME_RETAINED_PREDECESSOR_MISMATCH")
        validate_outcome_body(row)
    elif not pending or pending["body_fingerprint"] != Canonical.fingerprint(outcome_body(row)):
        raise ValueError("MUTATION_WINNER_CERTIFIED_PENDING_OUTCOME_REQUIRED")
    db.refresh(row)


def operation_configuration(db, retained):
    """Execution permission is delivered; financial rules remain the birth contract."""
    ownership = current_domain_write_ownership()
    if ownership is None:
        return retained
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery

    delivery = load_configuration_delivery(db, binding_reference(db, job_id=ownership.job_id))
    candidates = [
        config
        for config in delivery.configurations.values()
        if config.snapshot.family.namespace == "decision.winner.outcome"
    ]
    if len(candidates) != 1:
        raise ValueError("MUTATION_WINNER_OUTCOME_DELIVERED_OPERATION_CONFIGURATION_REQUIRED")
    operation = candidates[0]
    operation.require_family("decision.winner.outcome")
    if not operation.winner_config().engine.enabled:
        raise ValueError("MUTATION_WINNER_OUTCOME_OPERATION_DISABLED")
    return operation


@core_writer_member(
    "app.services.winner_probability.pending_outcome_service:PendingOutcomeService.materialize_pending_outcomes"
)
def seal_pending(db, row, prediction):
    row.metadata_json = {
        **(row.metadata_json or {}),
        "native_pending_proof": {
            "contract": "winner-native-pending-v1",
            "prediction_id": prediction.id,
            "capture_identity": prediction.lineage_json["native_capture_proof"],
            "body_fingerprint": Canonical.fingerprint(outcome_body(row)),
        },
    }


def pending_authority(db, prediction, config, mutation_context=None):
    from app.services.decision_effective_configuration import resolve_winner_configuration

    identity = validate_prediction_source(db, prediction)
    effective = configuration_from_payload(
        prediction.lineage_json["outcome_effective_configuration"]
    )
    effective.require_family("decision.winner.outcome")
    requested = resolve_winner_configuration(config, family="outcome")
    if "reference_policy" not in requested.values and "reference_policy" in effective.values:
        from app.services.decision_effective_configuration import (
            winner_outcome_reference_configuration,
        )

        requested = winner_outcome_reference_configuration(
            requested, effective.values["reference_policy"]
        )
    if requested.snapshot.semantic_hash != effective.snapshot.semantic_hash:
        raise ValueError("MUTATION_WINNER_PENDING_CAPTURE_CONFIGURATION_MISMATCH")
    if (
        not effective.winner_config().engine.enabled
        or not effective.winner_config().pending_outcomes["materialize_at_capture"]
        or prediction.eligibility_status != "ELIGIBLE"
    ):
        raise ValueError("MUTATION_WINNER_PENDING_CAPTURE_PERMISSION_REQUIRED")
    clock = MarketClockService().cutoff_for(
        prediction.captured_at, reason="WINNER_PENDING_CAPTURE_OPERATION"
    )
    body = {
        "prediction_id": prediction.id,
        "capture_identity": identity.canonical_payload(),
        "capture_proof": prediction.lineage_json["native_capture_proof"],
    }
    ownership = current_domain_write_ownership()
    operation = operation_configuration(db, effective)
    if ownership is not None:
        from app.services.decision_effective_configuration import (
            winner_outcome_reference_configuration,
        )

        birth = winner_outcome_reference_configuration(
            operation, effective.values["reference_policy"]
        )
        if birth.snapshot.semantic_hash != effective.snapshot.semantic_hash:
            raise ValueError("MUTATION_WINNER_PENDING_DELIVERED_CAPTURE_CONFIGURATION_MISMATCH")
    expected = DomainMutationContext(
        domain=MutationDomain.WINNER_OUTCOME,
        semantic_mode=MutationSemanticMode.OUTCOME_MATURATION,
        entrypoint=MutationEntryPointDescriptor(
            "PendingOutcomeService.materialize_pending_outcomes", "SUPPORTING_CAPTURE_LEDGER"
        ),
        writer=MutationWriterDescriptor(
            "PendingOutcomeService.materialize_pending_outcomes",
            "phase5-winner-outcome-v1",
            MutationDomain.WINNER_OUTCOME,
        ),
        reason="Supporting pending obligations from exact certified capture",
        configuration=operation.snapshot.identity,
        temporal=clock,
        run_id=prediction.run_id,
        durable=ownership is not None,
        execution=ownership,
        evidence=tuple(
            MutationEvidenceReference(
                role, "native_manifest", Canonical.fingerprint(value), Canonical.fingerprint(value)
            )
            for role, value in (("prediction_contract", body), ("outcome_price_manifest", []))
        ),
    )
    if mutation_context is not None and mutation_context != expected:
        raise ValueError("MUTATION_WINNER_PENDING_EXACT_CONTEXT_MISMATCH")
    fence_mutation_transaction(db, expected)
    _validate_configuration(db, expected, operation.snapshot)
    lock_decision_scope(db, {"winner_pending": prediction.id})
    return effective


def obligation_authority(db, outcomes, *, now, writer):
    from app.models.tables import BackgroundJob
    from app.services.core_mutation_authority import _active_writers
    from app.services.decision_mutation_authority import operational_decision_authority

    if now is None or now.tzinfo is None:
        raise ValueError("MUTATION_WINNER_OBLIGATION_EXPLICIT_OPERATION_TIME_REQUIRED")
    scope = []
    for outcome in outcomes:
        from app.models.tables import WinnerPredictionSnapshot

        prediction = db.get(WinnerPredictionSnapshot, outcome.prediction_id)
        validate_prediction_source(db, prediction)
        validate_retained_outcome(db, outcome)
        if (
            outcome.entry_model != "NEXT_OPEN"
            or outcome.horizon_sessions != 5
            or outcome.entry_session != prediction.planned_entry_session
            or outcome.due_session != horizon_due_session(outcome.entry_session, 5)
        ):
            raise ValueError("MUTATION_WINNER_OBLIGATION_CAPTURE_SCOPE_MISMATCH")
        scope.append(
            {
                "prediction_id": prediction.id,
                "run_id": prediction.run_id,
                "outcome_id": outcome.id,
                "entry": outcome.entry_session,
                "due": outcome.due_session,
                "capture_proof": prediction.lineage_json["native_capture_proof"],
            }
        )
        lock_decision_scope(db, {"winner_obligation": outcome.id})
    ownership = current_domain_write_ownership()
    run_id = None
    job_types = (
        "WINNER_OUTCOME_MATURATION",
        "WINNER_OUTCOME_REVISION_CHECK",
        "IB_FETCH",
        "WINNER_PREDICTION_CAPTURE",
        "WINNER_HISTORICAL_BACKFILL",
    )
    if ownership is not None:
        job = db.get(BackgroundJob, ownership.job_id)
        run_id = job.related_run_id
        if run_id is not None and any(row["run_id"] != run_id for row in scope):
            raise ValueError("MUTATION_WINNER_OBLIGATION_EXECUTION_RUN_MISMATCH")
        parent = (
            "app.services.winner_probability.capture_service:"
            "WinnerPredictionCaptureService._capture_ticker"
        )
        if any(active_db is db and owner == parent for active_db, owner in _active_writers.get()):
            job_types = job_types + (job.job_type,)
    operational_decision_authority(
        db,
        writer=writer,
        manifest={"outcomes": scope, "now": now},
        run_id=run_id,
        job_types=job_types,
    )
    return scope


def price_manifest(db, rows, *, clock):
    """Current rows are explicit contents, with a revision ID when it exists.

    Birth rows can lack a PriceBarRevision ID. Their entire retained contents are
    pinned; that does not close WIN-006's historical revision-identity gap.
    """
    result = []
    from app.services.bar_cache_service import _price_bar_values, price_bar_data_hash

    for row in sorted(rows, key=lambda item: item.id):
        table = PriceBar.__table__
        with db.no_autoflush:
            stored = (
                db.execute(select(*table.columns).where(table.c.id == row.id).with_for_update())
                .mappings()
                .one_or_none()
            )
        if stored is None or any(
            Canonical.dumps(value) != Canonical.dumps(getattr(row, key))
            for key, value in stored.items()
        ):
            raise ValueError("MUTATION_WINNER_OUTCOME_PRICE_SOURCE_MISMATCH")
        if row.bar_date > clock.latest_completed_session or any(
            value is not None and value > clock.cutoff_at
            for value in (row.created_at, row.first_seen_at, row.revised_at)
        ):
            raise ValueError("MUTATION_WINNER_OUTCOME_PRICE_NOT_KNOWN_AT_OPERATION")
        if row.data_hash != price_bar_data_hash(row):
            raise ValueError("MUTATION_WINNER_OUTCOME_PRICE_CONTENT_HASH_MISMATCH")
        revision = db.scalar(
            select(PriceBarRevision).where(
                PriceBarRevision.price_bar_id == row.id,
                PriceBarRevision.revision_number == row.revision_count,
                PriceBarRevision.new_data_hash == row.data_hash,
                PriceBarRevision.observed_at <= clock.cutoff_at,
            )
        )
        revision_values = None
        if revision is not None:
            with db.no_autoflush:
                revision_values = (
                    db.execute(
                        select(*PriceBarRevision.__table__.columns).where(
                            PriceBarRevision.id == revision.id
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if (
                revision_values is None
                or any(
                    Canonical.dumps(value) != Canonical.dumps(getattr(revision, key))
                    for key, value in revision_values.items()
                )
                or revision.created_at > clock.cutoff_at
            ):
                raise ValueError("MUTATION_WINNER_OUTCOME_RETAINED_PRICE_REVISION_MISMATCH")
        if row.revision_count and (
            revision is None
            or revision.new_values_json != _price_bar_values(row)
            or (revision.ticker, revision.bar_date, revision.timeframe, revision.what_to_show)
            != (row.ticker, row.bar_date, row.timeframe, row.what_to_show)
        ):
            raise ValueError("MUTATION_WINNER_OUTCOME_EXACT_PRICE_REVISION_REQUIRED")
        result.append(
            {
                "id": row.id,
                "values": dict(stored),
                "price_bar_revision_id": revision.id if revision is not None else None,
                "price_bar_revision": dict(revision_values)
                if revision_values is not None
                else None,
            }
        )
    return Canonical.canonicalize(result)


def forward_authority(db, *, prediction, outcome, now, batch, mutation_context=None):
    identity = validate_prediction_source(db, prediction)
    if (
        now is None
        or now.tzinfo is None
        or now < max(prediction.source_data_cutoff_at, prediction.captured_at)
    ):
        raise ValueError("MUTATION_WINNER_OUTCOME_EXPLICIT_OPERATION_TIME_REQUIRED")
    if prediction.eligibility_status != "ELIGIBLE":
        raise ValueError("MUTATION_WINNER_OUTCOME_CAPTURE_PERMISSION_REQUIRED")
    effective = configuration_from_payload(
        prediction.lineage_json["outcome_effective_configuration"]
    )
    effective.require_family("decision.winner.outcome")
    config = effective.winner_config()
    if not config.engine.enabled:
        raise ValueError("MUTATION_WINNER_OUTCOME_DISABLED")
    from app.services.winner_probability.config import ENTRY_MODEL_SIGNAL_CLOSE_DIAGNOSTIC
    from app.services.winner_probability.trading_session_service import latest_completed_session

    entry = (
        latest_completed_session(prediction.source_data_cutoff_at)
        if outcome.entry_model == ENTRY_MODEL_SIGNAL_CLOSE_DIAGNOSTIC
        else prediction.planned_entry_session
    )
    if (
        outcome.prediction_id != prediction.id
        or outcome.entry_model
        not in (config.entry_models.production, *config.entry_models.diagnostics)
        or outcome.horizon_sessions not in config.horizon.sessions
        or outcome.entry_session != entry
        or outcome.due_session
        != (horizon_due_session(entry, outcome.horizon_sessions) if entry else None)
        or not outcome.is_current_revision
    ):
        raise ValueError("MUTATION_WINNER_OUTCOME_CAPTURE_CONTRACT_MISMATCH")
    validate_retained_outcome(db, outcome)
    clock = MarketClockService().cutoff_for(now, reason="WINNER_OUTCOME_EXPLICIT_OPERATION")
    bars = {
        row.id: row
        for group in batch.bars_by_ticker.values()
        for row in group
        if outcome.entry_session is not None
        and outcome.due_session is not None
        and outcome.entry_session <= row.bar_date <= outcome.due_session
    }
    prices = price_manifest(db, tuple(bars.values()), clock=clock)
    contract = {
        "prediction_id": prediction.id,
        "capture_identity": identity.canonical_payload(),
        "capture_proof": prediction.lineage_json["native_capture_proof"],
        "predecessor_id": outcome.id,
        "predecessor_revision": outcome.revision,
        "predecessor_body": outcome_body(outcome),
    }
    ownership = current_domain_write_ownership()
    operation = operation_configuration(db, effective)
    expected = DomainMutationContext(
        domain=MutationDomain.WINNER_OUTCOME,
        semantic_mode=MutationSemanticMode.OUTCOME_MATURATION,
        entrypoint=MutationEntryPointDescriptor(
            "OutcomeMaturationService.process_forward_outcome", "NATIVE_SEMANTIC_WRITER"
        ),
        writer=MutationWriterDescriptor(
            "OutcomeMaturationService.process_forward_outcome",
            "phase5-winner-outcome-v1",
            MutationDomain.WINNER_OUTCOME,
        ),
        reason="Exact forecast contract and explicit operation price contents",
        configuration=operation.snapshot.identity,
        temporal=clock,
        run_id=prediction.run_id,
        durable=ownership is not None,
        execution=ownership,
        evidence=tuple(
            MutationEvidenceReference(
                role, "native_manifest", Canonical.fingerprint(body), Canonical.fingerprint(body)
            )
            for role, body in (
                ("prediction_contract", contract),
                ("outcome_price_manifest", prices),
            )
        ),
    )
    if mutation_context is not None and mutation_context != expected:
        raise ValueError("MUTATION_WINNER_OUTCOME_EXACT_CONTEXT_MISMATCH")
    fence_mutation_transaction(db, expected)
    _validate_configuration(db, expected, operation.snapshot)
    lock_decision_scope(
        db,
        {
            "winner_outcome": prediction.id,
            "entry_model": outcome.entry_model,
            "horizon": outcome.horizon_sessions,
        },
    )
    return expected, effective, prices


@core_writer_member(
    "app.services.winner_probability.outcome_service:OutcomeMaturationService.process_forward_outcome"
)
def seal_outcome(db, row, *, context, configuration, prices):
    row.metadata_json = {
        **(row.metadata_json or {}),
        PROOF_KEY: {
            "contract": "winner-native-outcome-v1",
            "body_fingerprint": Canonical.fingerprint(outcome_body(row)),
            "mutation_authority": context.canonical_payload(),
            "configuration": configuration.snapshot.as_dict(),
            "prices": prices,
        },
    }
