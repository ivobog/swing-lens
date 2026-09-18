"""Authority for the supporting, alert-driving CERI change ledger."""

from sqlalchemy import select

from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import CoreCalculationEvidence
from app.services.calculation_identity import CalculationIdentity
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.decision_mutation_authority import decision_authority, validate_retained_decision


def validate_score_source(db, snapshot):
    if snapshot is None or snapshot.id is None or snapshot.evidence_id is None:
        raise ValueError("MUTATION_CERI_CHANGE_CERTIFIED_SCORE_SOURCE_REQUIRED")
    persisted = db.scalar(
        select(CeriScoreSnapshot).where(CeriScoreSnapshot.id == snapshot.id).with_for_update()
    )
    evidence = db.get(CoreCalculationEvidence, snapshot.evidence_id)
    validate_retained_decision(db, evidence, contract="unused")
    if persisted is None or evidence.artifact_kind != "CERI":
        raise ValueError("MUTATION_CERI_CHANGE_SCORE_SOURCE_KIND_MISMATCH")
    # Comparison pointers are supporting state advanced by this owner; they
    # do not redefine the financial decision sealed before change detection.
    supporting = {"comparison_state", "comparison_snapshot_id"}
    for field, expected in evidence.payload_json["decision_output"].items():
        if field not in supporting and Canonical.dumps(
            {field: getattr(snapshot, field)}
        ) != Canonical.dumps({field: expected}):
            raise ValueError("MUTATION_CERI_CHANGE_SCORE_SOURCE_BODY_MISMATCH: " + field)
    identity = CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json)
    if (
        evidence.run_id != snapshot.run_id
        or evidence.ticker != snapshot.ticker
        or identity.temporal.as_of_session.value != snapshot.as_of_session
        or identity.temporal.calculation_cutoff.value != snapshot.cutoff_at
        or identity.subject.company_id.value != snapshot.company_id
    ):
        raise ValueError("MUTATION_CERI_CHANGE_SCORE_SOURCE_SCOPE_MISMATCH")
    return evidence, identity


def validate_score_comparison(db, *, current, prior, comparison_state):
    from app.services.ceri.change_semantics import classify_snapshot_comparison

    current_evidence, identity = validate_score_source(db, current)
    prior_evidence = None
    if prior is not None:
        prior_evidence, _ = validate_score_source(db, prior)
        if (
            prior.company_id != current.company_id
            or prior.ticker != current.ticker
            or (prior.as_of_session, prior.cutoff_at, prior.id)
            >= (current.as_of_session, current.cutoff_at, current.id)
        ):
            raise ValueError("MUTATION_CERI_CHANGE_SCORE_PREDECESSOR_MISMATCH")
        if (
            comparison_state.value == "COMPARABLE"
            and current_evidence.payload_json["effective_configuration_at_creation"][
                "semantic_hash"
            ]
            != prior_evidence.payload_json["effective_configuration_at_creation"]["semantic_hash"]
        ):
            raise ValueError("MUTATION_CERI_CHANGE_CORE_CONFIGURATION_TRANSITION")
    if comparison_state != classify_snapshot_comparison(prior, current):
        raise ValueError("MUTATION_CERI_CHANGE_NATIVE_COMPARISON_MISMATCH")
    return current_evidence, prior_evidence, identity


def change_body(event):
    return Canonical.canonicalize(
        {
            column.name: getattr(event, column.name)
            for column in event.__table__.columns
            if column.name not in {"id", "created_at"}
        }
    )


def _normalized_source(db, model, source_id):
    if source_id is None:
        raise ValueError("MUTATION_CERI_CHANGE_EXACT_NORMALIZED_SOURCE_REQUIRED")
    source = db.get(model, source_id)
    columns = tuple(model.__table__.columns)
    with db.no_autoflush:
        retained = (
            db.execute(select(*columns).where(model.id == source_id).with_for_update())
            .mappings()
            .one_or_none()
        )
    if source is None or retained is None:
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_MISSING")
    actual = {column.name: getattr(source, column.name) for column in columns}
    if Canonical.dumps(actual) != Canonical.dumps(dict(retained)):
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_BODY_MISMATCH")
    return source, {"id": source_id, "body_fingerprint": Canonical.fingerprint(actual)}


def validate_normalized_change(
    db,
    service,
    *,
    company_id,
    change_type,
    effective_session,
    delta,
    config_hash,
    calculation_version,
    catalyst_revision_id,
    guidance_event_id,
    prior_catalyst_revision_id,
    prior_guidance_event_id,
    market_cutoff,
    comparison_state,
    source_validation=False,
):
    from app.models.ceri_tables import (
        CeriCatalystEvent,
        CeriCatalystEventRevision,
        CeriGuidanceEvent,
        CeriSourceRecord,
    )
    from app.services.ceri.change_detection_service import (
        _catalyst_change_eligible,
        _catalyst_change_type,
        _catalyst_delta,
        _guidance_delta,
    )
    from app.services.ceri.change_semantics import ComparisonState
    from app.services.ceri.enums import CeriChangeType
    from app.services.decision_mutation_authority import operational_decision_authority
    from app.services.market_clock_service import MarketCalculationCutoff, MarketClockService

    configuration = service.effective_configuration
    configuration.require_family("decision.ceri.changes")
    if not configuration.values["enabled"]:
        raise ValueError("MUTATION_CERI_CHANGE_DISABLED")
    if not isinstance(market_cutoff, MarketCalculationCutoff):
        raise ValueError("MUTATION_CERI_CHANGE_EXPLICIT_OPERATION_TIME_REQUIRED")
    native_time = MarketClockService().cutoff_for(
        market_cutoff.cutoff_at, reason=market_cutoff.cutoff_reason
    )
    time_fields = (
        "cutoff_at",
        "latest_completed_session",
        "calendar_version",
        "exchange_timezone",
        "bar_readiness_version",
        "daily_bar_ready_at",
    )
    if any(getattr(native_time, key) != getattr(market_cutoff, key) for key in time_fields):
        raise ValueError("MUTATION_CERI_CHANGE_OPERATION_TIME_MISMATCH")
    if market_cutoff.context_id is not None:
        from app.models.tables import MarketCalculationContext
        from app.services.market_calculation_context_service import cutoff_from_row

        record = db.get(MarketCalculationContext, market_cutoff.context_id)
        if record is None or any(
            getattr(cutoff_from_row(record), key) != getattr(market_cutoff, key)
            for key in time_fields
        ):
            raise ValueError("MUTATION_CERI_CHANGE_OPERATION_CONTEXT_MISMATCH")
    if (catalyst_revision_id is None) == (guidance_event_id is None):
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_KIND_MISMATCH")
    sources = {}
    if catalyst_revision_id is not None:
        current, sources["current"] = _normalized_source(
            db, CeriCatalystEventRevision, catalyst_revision_id
        )
        parent, sources["canonical_event"] = _normalized_source(
            db, CeriCatalystEvent, current.catalyst_event_id
        )
        if parent.company_id != company_id or prior_guidance_event_id is not None:
            raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SCOPE_MISMATCH")
        prior = None
        if current.prior_revision_id != prior_catalyst_revision_id:
            raise ValueError("MUTATION_CERI_CHANGE_EXACT_PREDECESSOR_REQUIRED")
        if prior_catalyst_revision_id is not None:
            prior, sources["prior"] = _normalized_source(
                db, CeriCatalystEventRevision, prior_catalyst_revision_id
            )
            if (
                prior.catalyst_event_id != current.catalyst_event_id
                or prior.revision_number >= current.revision_number
            ):
                raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_PREDECESSOR_MISMATCH")
        if not _catalyst_change_eligible(current, prior):
            raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_INELIGIBLE")
        native_type, native_delta = (
            _catalyst_change_type(current, prior),
            _catalyst_delta(current, prior),
        )
        native_config = "event_revision"
        timestamp = current.announced_at
    else:
        current, sources["current"] = _normalized_source(db, CeriGuidanceEvent, guidance_event_id)
        if current.company_id != company_id or prior_catalyst_revision_id is not None:
            raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SCOPE_MISMATCH")
        if current.supersedes_id != prior_guidance_event_id:
            raise ValueError("MUTATION_CERI_CHANGE_EXACT_PREDECESSOR_REQUIRED")
        prior = None
        if prior_guidance_event_id is not None:
            prior, sources["prior"] = _normalized_source(
                db, CeriGuidanceEvent, prior_guidance_event_id
            )
            if (
                prior.company_id != company_id
                or prior.metric != current.metric
                or prior.period_type != current.period_type
                or prior.effective_at is None
                or current.effective_at is None
                or (prior.effective_at, prior.id) >= (current.effective_at, current.id)
            ):
                raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_PREDECESSOR_MISMATCH")
        native_type = {
            "RAISED": CeriChangeType.GUIDANCE_RAISED,
            "LOWERED": CeriChangeType.GUIDANCE_LOWERED,
            "WITHDRAWN": CeriChangeType.GUIDANCE_WITHDRAWN,
        }.get(current.action)
        if (
            current.accepted_for_scoring is not True
            or native_type is None
            or (prior is not None and prior.action == current.action)
        ):
            raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_INELIGIBLE")
        native_delta = _guidance_delta(current, prior.action if prior is not None else None)
        native_config = "guidance_event"
        timestamp = current.effective_at
    record, sources["source_record"] = _normalized_source(
        db, CeriSourceRecord, current.source_record_id
    )
    from app.models.ceri_tables import CeriCompany

    company = db.get(CeriCompany, company_id)
    hints = record.company_hint_json or {}
    ticker_hint, cik_hint = hints.get("ticker"), hints.get("cik")
    ticker_matches = (
        company is not None
        and ticker_hint is not None
        and str(ticker_hint).strip().upper() == company.ticker.strip().upper()
    )
    cik_matches = (
        company is not None
        and company.cik is not None
        and cik_hint is not None
        and str(cik_hint).lstrip("0") == str(company.cik).lstrip("0")
    )
    if not (ticker_matches or cik_matches):
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_COMPANY_SOURCE_REQUIRED")
    sources["company_scope"] = {
        "company_id": company.id,
        "ticker_hint": ticker_hint,
        "cik_hint": cik_hint,
        "matched_ticker": ticker_matches,
        "matched_cik": cik_matches,
    }
    from app.services.ceri.source_record_service import source_record_content_hash

    if record.raw_json is not None:
        source_hash, retained_hash = (
            source_record_content_hash(record.raw_json),
            record.content_hash,
        )
    elif record.restricted_normalized_json is not None:
        source_hash = source_record_content_hash(record.restricted_normalized_json)
        retained_hash = record.normalized_hash
    else:
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_PAYLOAD_REQUIRED")
    if source_hash != retained_hash:
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_HASH_MISMATCH")
    if prior is not None:
        predecessor_record, sources["prior_source_record"] = _normalized_source(
            db, CeriSourceRecord, prior.source_record_id
        )
        prior_timestamp = prior.announced_at if catalyst_revision_id else prior.effective_at
        prior_known = (
            predecessor_record.observed_at
            or predecessor_record.published_at
            or predecessor_record.ingested_at
        )
        if (
            prior_timestamp is None
            or prior_timestamp > timestamp
            or prior.effective_session is None
            or prior.effective_session > current.effective_session
            or prior_known is None
            or prior_known > market_cutoff.cutoff_at
            or predecessor_record.ingested_at > market_cutoff.cutoff_at
            or predecessor_record.quarantine_reason is not None
        ):
            raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_PREDECESSOR_NOT_KNOWN")
    if catalyst_revision_id is not None:
        from app.services.ceri.catalyst_taxonomy import CeriCatalystTaxonomy

        normalized = CeriCatalystTaxonomy().normalize(record, company_id=company_id)
        expected_source = {
            "announced_at": normalized.announced_at,
            "expected_date": normalized.expected_date,
            "effective_session": normalized.effective_session,
            "status": normalized.status.value,
            "direction": normalized.direction.value,
            "materiality": normalized.materiality,
            "date_confidence": normalized.date_confidence.value,
            "source_confidence": normalized.confidence.value,
            "issuer_relevance": normalized.issuer_relevance,
            "relevance_reason": normalized.relevance_reason,
        }
    else:
        from app.services.ceri.guidance_normalizer import CeriGuidanceNormalizer

        normalized = CeriGuidanceNormalizer().normalize(record, company_id=company_id)
        if record.provider == "sec":
            from app.services.ceri.guidance_comparison_service import compare_guidance

            comparison = compare_guidance(normalized, prior)
            normalized.action, normalized.confidence = comparison.action, comparison.confidence
        expected_source = {
            key: getattr(normalized, key)
            for key in (
                "action",
                "metric",
                "period_type",
                "period_label",
                "low_value",
                "high_value",
                "point_value",
                "unit",
                "currency",
                "comparison_basis",
                "confidence",
                "effective_at",
                "effective_session",
            )
        }
    if any(getattr(current, key) != value for key, value in expected_source.items()):
        raise ValueError("MUTATION_CERI_CHANGE_NATIVE_NORMALIZED_SOURCE_MISMATCH")
    known_times = [
        record.observed_at or record.published_at or record.ingested_at,
        record.ingested_at,
        timestamp,
    ]
    if (
        record.quarantine_reason is not None
        or timestamp is None
        or any(value is None or value > market_cutoff.cutoff_at for value in known_times)
        or current.effective_session is None
        or current.effective_session > market_cutoff.latest_completed_session
    ):
        raise ValueError("MUTATION_CERI_CHANGE_NORMALIZED_SOURCE_NOT_KNOWN")
    if (
        change_type != native_type
        or effective_session != current.effective_session
        or config_hash != native_config
        or calculation_version != "ceri-1.0.0"
        or comparison_state != ComparisonState.COMPARABLE
        or Canonical.dumps(delta) != Canonical.dumps(native_delta)
    ):
        raise ValueError("MUTATION_CERI_CHANGE_NATIVE_NORMALIZED_DELTA_MISMATCH")
    manifest = {
        "company_id": company_id,
        "sources": sources,
        "operation_time": {key: getattr(market_cutoff, key) for key in time_fields},
        "effective_session": effective_session,
        "change_type": change_type.value,
        "delta": native_delta,
        "configuration": configuration.snapshot.as_dict(),
    }
    if not source_validation:
        operational_decision_authority(
            db,
            writer="persist_ceri_normalized_change",
            manifest=manifest,
            job_types=("CERI_CHANGE_DETECTION",),
        )
    return {
        "artifact_role": "SUPPORTING_CHANGE_LEDGER",
        "classification": "SUPPORTED_DISTINCT_SAFE",
        "source_kind": "NORMALIZED_EVENT_DERIVATION",
        **manifest,
    }


def validate_score_change(
    db,
    service,
    *,
    company_id,
    change_type,
    effective_session,
    delta,
    config_hash,
    calculation_version,
    from_snapshot_id,
    to_snapshot_id,
    comparison_state,
    source_validation=False,
):
    from app.services.contextual_calculation_identity import build_contextual_result_identity
    from app.services.domain_mutation import MutationDomain, MutationSemanticMode

    configuration = service.effective_configuration
    configuration.require_family("decision.ceri.changes")
    if not configuration.values["enabled"]:
        raise ValueError("MUTATION_CERI_CHANGE_DISABLED")
    current = db.get(CeriScoreSnapshot, to_snapshot_id) if to_snapshot_id else None
    prior = db.get(CeriScoreSnapshot, from_snapshot_id) if from_snapshot_id else None
    current_evidence, prior_evidence, base = validate_score_comparison(
        db,
        current=current,
        prior=prior,
        comparison_state=comparison_state,
    )
    from app.services.ceri.change_detection_service import CeriChangeDetectionService

    calculator = CeriChangeDetectionService.__new__(CeriChangeDetectionService)
    calculator.effective_configuration = configuration
    calculator.config = configuration.ceri_decision_config()
    expected = calculator._score_changes(current, prior).get(change_type)
    if (
        expected is None
        or current.company_id != company_id
        or current.as_of_session != effective_session
        or current.config_hash != config_hash
        or current.calculation_version != calculation_version
        or Canonical.dumps(expected) != Canonical.dumps(delta)
    ):
        raise ValueError("MUTATION_CERI_CHANGE_NATIVE_SCORE_DELTA_MISMATCH")
    sources = {
        "current": {
            "snapshot_id": current.id,
            "evidence_id": current_evidence.id,
            "key": current_evidence.evidence_key,
        },
        "prior": {
            "snapshot_id": prior.id,
            "evidence_id": prior_evidence.id,
            "key": prior_evidence.evidence_key,
        },
    }
    identity = configuration.bind(
        build_contextual_result_identity(
            base=base,
            namespace="ceri-change",
            config_hash=configuration.snapshot.semantic_hash,
            calculation_version="ceri-change-v1",
            engine_version="ceri-change-v1",
            source_artifacts=(),
            source_payload={"sources": sources, "delta": delta, "change_type": change_type.value},
        )
    )
    if not source_validation:
        decision_authority(
            db,
            domain=MutationDomain.CURRENT_PROJECTION,
            writer="persist_ceri_change",
            identity=identity,
            configuration=configuration,
            records={"target_evidence": current_evidence},
            manifests={
                "projection_scope": {
                    "sources": sources,
                    "delta": delta,
                    "change_type": change_type.value,
                }
            },
            semantic_mode=MutationSemanticMode.CURRENT_PROJECTION_ADVANCE,
        )
    from app.services.core_mutation_authority import identity_cutoff

    cutoff = identity_cutoff(db, identity)
    return {
        "artifact_role": "SUPPORTING_CHANGE_LEDGER",
        "classification": "SUPPORTED_DISTINCT_SAFE",
        "source_kind": "CERTIFIED_SCORE_COMPARISON",
        "sources": sources,
        "calculation_identity": identity.canonical_payload(),
        "operation_time": {
            "cutoff_at": cutoff.cutoff_at,
            "session": cutoff.latest_completed_session,
            "calendar_version": cutoff.calendar_version,
            "exchange_timezone": cutoff.exchange_timezone,
            "bar_readiness_version": cutoff.bar_readiness_version,
        },
        "config_hash": config_hash,
        "calculation_version": calculation_version,
    }
