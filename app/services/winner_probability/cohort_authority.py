"""Exact retained populations and configuration at Winner cohort writers."""

from decimal import Decimal

from sqlalchemy import Numeric, select

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_mutation_authority import _validate_configuration, core_writer_member
from app.services.decision_effective_configuration import resolve_winner_configuration
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
from app.services.winner_probability.pre11_compatibility_service import EVIDENCE_ORIGIN_NATIVE


def is_certified_financial_member(member) -> bool:
    """Proof presence is a selector gate; population_bodies verifies the bodies."""

    return bool(
        member.evidence_origin == EVIDENCE_ORIGIN_NATIVE
        and isinstance((member.prediction.lineage_json or {}).get("native_capture_proof"), dict)
        and isinstance(
            (member.forward_outcome.metadata_json or {}).get("native_outcome_proof"), dict
        )
        and isinstance(
            (member.target_stop_outcome.metadata_json or {}).get("native_outcome_proof"), dict
        )
    )


def certified_financial_population(evidence):
    """Keep legacy/replay observations readable, but out of financial authority."""

    return tuple(member for member in evidence if is_certified_financial_member(member))


def retained_row(db, row):
    if row is None or row.id is None:
        raise ValueError("MUTATION_WINNER_RETAINED_SOURCE_REQUIRED")
    with db.no_autoflush:
        stored = (
            db.execute(select(*row.__table__.columns).where(row.__table__.c.id == row.id))
            .mappings()
            .one_or_none()
        )
    if stored is None or any(
        Canonical.dumps(value) != Canonical.dumps(getattr(row, key))
        for key, value in stored.items()
    ):
        raise ValueError("MUTATION_WINNER_RETAINED_SOURCE_BODY_MISMATCH")
    return dict(stored)


def population_bodies(db, evidence, *, financial=False, cutoff=None):
    from dataclasses import fields, is_dataclass

    from app.models.tables import (
        WinnerForwardOutcome,
        WinnerPredictionSnapshot,
        WinnerTargetStopOutcome,
    )
    from app.services.winner_probability.outcome_authority import validate_outcome_body
    from app.services.winner_probability.prediction_authority import validate_capture_body

    bodies = []
    seen = set()
    members = tuple(evidence)
    transaction = db.get_transaction() if hasattr(db, "get_transaction") else None
    authority_cache = None
    if transaction is not None:
        cache_state = db.info.get("winner_population_authority_cache")
        if cache_state is None or cache_state["transaction"] is not transaction:
            cache_state = {"transaction": transaction, "bodies": {}}
            db.info["winner_population_authority_cache"] = cache_state
        authority_cache = cache_state["bodies"]
        diagnostic_cache = db.info.get("winner_diagnostic_population_cache")
        if diagnostic_cache is None or diagnostic_cache["transaction"] is not transaction:
            diagnostic_cache = {"transaction": transaction, "rows": {}}
            db.info["winner_diagnostic_population_cache"] = diagnostic_cache
    else:
        diagnostic_cache = None
    pending = []
    for member in members:
        prediction_id = int(member.prediction.id)
        forward_id = int(member.forward_outcome.id)
        target_id = int(member.target_stop_outcome.id)
        key = (prediction_id, forward_id, target_id)
        if key in seen:
            raise ValueError("MUTATION_WINNER_EXACT_POPULATION_SCOPE_REQUIRED")
        seen.add(key)
        cache_key = (
            *key,
            Decimal(str(member.inclusion_weight)),
        )
        cached = authority_cache.get(cache_key) if authority_cache is not None else None
        cache_valid = cached is not None and (
            not financial or cutoff in cached["financial_cutoffs"]
        )
        if not cache_valid:
            pending.append((member, cache_key))
        else:
            bodies.append(cached["body"])
    if not pending:
        return bodies

    models = (WinnerPredictionSnapshot, WinnerForwardOutcome, WinnerTargetStopOutcome)
    member_fields = ("prediction", "forward_outcome", "target_stop_outcome")
    retained_by_model = {}
    for member_field, model in zip(member_fields, models, strict=True):
        ids = {int(getattr(member, member_field).id) for member, _ in pending}
        if not ids:
            retained_by_model[model] = {}
            continue
        columns = tuple(model.__table__.columns)
        with db.no_autoflush:
            retained = [
                model(**dict(row))
                for row in db.execute(
                    select(*columns).where(model.id.in_(ids)).with_for_update()
                ).mappings()
            ]
        if len(retained) != len(ids):
            raise ValueError("MUTATION_WINNER_RETAINED_SOURCE_REQUIRED")
        retained_by_model[model] = {int(row.id): row for row in retained}
    pending_bodies = []
    for member, cache_key in pending:
        supplied = (
            member.prediction,
            member.forward_outcome,
            member.target_stop_outcome,
        )
        resolved = []
        for value, model in zip(
            supplied,
            (WinnerPredictionSnapshot, WinnerForwardOutcome, WinnerTargetStopOutcome),
            strict=True,
        ):
            actual = retained_by_model[model].get(int(value.id))
            if actual is None:
                raise ValueError("MUTATION_WINNER_RETAINED_SOURCE_REQUIRED")
            if isinstance(value, model):
                names = (column.key for column in model.__table__.columns)
            elif hasattr(value, "column_values"):
                names = value.column_values.keys()
            elif is_dataclass(value):
                names = [field.name for field in fields(value)]
            else:
                raise ValueError("MUTATION_WINNER_EXACT_SOURCE_DTO_REQUIRED")
            if any(
                Canonical.dumps(getattr(value, name)) != Canonical.dumps(getattr(actual, name))
                for name in names
            ):
                raise ValueError("MUTATION_WINNER_FROZEN_SOURCE_DTO_MISMATCH")
            resolved.append(actual)
        prediction, forward, target = resolved
        if diagnostic_cache is not None:
            diagnostic_cache["rows"].update(
                {
                    (WinnerPredictionSnapshot, int(prediction.id)): prediction,
                    (WinnerForwardOutcome, int(forward.id)): forward,
                    (WinnerTargetStopOutcome, int(target.id)): target,
                }
            )
        key = (prediction.id, forward.id, target.id)
        if (
            forward.prediction_id != prediction.id
            or target.prediction_id != prediction.id
            or target.forward_outcome_id != forward.id
        ):
            raise ValueError("MUTATION_WINNER_EXACT_POPULATION_SCOPE_REQUIRED")
        if financial:
            validate_capture_body(prediction)
            validate_outcome_body(forward)
            validate_outcome_body(target)
            if (
                cutoff is None
                or forward.matured_at is None
                or target.evaluated_at is None
                or max(forward.matured_at, target.evaluated_at, prediction.captured_at) >= cutoff
            ):
                raise ValueError("MUTATION_WINNER_POPULATION_KNOWN_CUTOFF_REQUIRED")
        if Decimal(str(member.inclusion_weight)) <= 0:
            raise ValueError("MUTATION_WINNER_POSITIVE_POPULATION_WEIGHT_REQUIRED")
        from app.services.core_calculation_evidence import calculation_evidence_payload
        from app.services.winner_probability.outcome_authority import OPERATIONAL_COLUMNS

        values = [
            calculation_evidence_payload(
                prediction,
                excluded_columns={"entry_data_status", "superseded_at", "combined_result_id"},
            ),
            calculation_evidence_payload(forward, excluded_columns=OPERATIONAL_COLUMNS),
            calculation_evidence_payload(target, excluded_columns=OPERATIONAL_COLUMNS),
        ]
        body = {
            "ids": list(key),
            "body_fingerprints": [Canonical.fingerprint(value) for value in values],
            "authority": "NATIVE_CERTIFIED"
            if (prediction.lineage_json or {}).get("native_capture_proof")
            else "LEGACY_NONCERTIFIED_FROZEN_FACTS",
        }
        pending_bodies.append(body)
        if authority_cache is not None:
            cached = authority_cache.get(cache_key)
            financial_cutoffs = set(cached["financial_cutoffs"]) if cached else set()
            if financial:
                financial_cutoffs.add(cutoff)
            authority_cache[cache_key] = {
                "body": body,
                "financial_cutoffs": financial_cutoffs,
            }
    bodies.extend(pending_bodies)
    body_by_ids = {tuple(body["ids"]): body for body in bodies}
    return [
        body_by_ids[
            (
                int(member.prediction.id),
                int(member.forward_outcome.id),
                int(member.target_stop_outcome.id),
            )
        ]
        for member in members
    ]


def operation_authority(db, *, domain, writer, config, now, manifest, context=None):
    if config is None or now is None or now.tzinfo is None:
        raise ValueError("MUTATION_WINNER_COHORT_CONFIGURATION_AND_OPERATION_TIME_REQUIRED")
    family = "cohort" if domain is MutationDomain.WINNER_COHORT else "generation"
    effective = resolve_winner_configuration(config, family=family)
    if not effective.winner_config().engine.enabled:
        raise ValueError("MUTATION_WINNER_COHORT_OPERATION_DISABLED")
    roles = (
        ("generation", "estimate")
        if domain is MutationDomain.WINNER_PUBLICATION
        else ("evidence_manifest" if family == "cohort" else "generation_manifest",)
    )
    fingerprint = Canonical.fingerprint(manifest)
    ownership = current_domain_write_ownership()
    expected = DomainMutationContext(
        domain=domain,
        semantic_mode=MutationSemanticMode.PUBLICATION
        if domain is MutationDomain.WINNER_PUBLICATION
        else MutationSemanticMode.CANONICAL_CALCULATION,
        entrypoint=MutationEntryPointDescriptor(writer, "NATIVE_SEMANTIC_WRITER"),
        writer=MutationWriterDescriptor(writer, "phase5-winner-cohort-v1", domain),
        configuration=effective.snapshot.identity,
        temporal=MarketClockService().cutoff_for(now, reason="WINNER_COHORT_EXPLICIT_OPERATION"),
        reason="Exact native population, contract and operation configuration",
        durable=ownership is not None,
        execution=ownership,
        evidence=tuple(
            MutationEvidenceReference(role, "native_manifest", fingerprint, fingerprint)
            for role in roles
        ),
    )
    if context is not None and context != expected:
        raise ValueError("MUTATION_WINNER_COHORT_EXACT_CONTEXT_MISMATCH")
    fence_mutation_transaction(db, expected)
    _validate_configuration(db, expected, effective.snapshot)
    lock_decision_scope(
        db,
        {
            "winner_cohort_operation": writer,
            "configuration": effective.snapshot.semantic_hash,
            "outcome_id": manifest.get("outcome_definition_id"),
        },
    )
    return expected, effective


def generation_body(row):
    fields = (
        "generation_key",
        "refresh_state_id",
        "outcome_definition_id",
        "watermark_hash",
        "watermark_json",
        "feature_schema_version",
        "calculation_version",
        "config_hash",
        "eligibility_policy_version",
        "compatibility_policy_version",
        "cohort_algorithm_version",
        "training_cutoff_at",
        "requested_at",
    )
    return {key: getattr(row, key) for key in fields}


def validate_generation(db, row, config):
    from app.services.winner_probability.cohort_generation_service import (
        CohortGenerationService,
        canonical_generation_key,
        canonical_watermark_hash,
    )

    retained_row(db, row)
    proof = (row.metrics_json or {}).get("native_generation_proof")
    if not isinstance(proof, dict) or proof.get("fingerprint") != Canonical.fingerprint(
        generation_body(row)
    ):
        raise ValueError("MUTATION_WINNER_CERTIFIED_GENERATION_REQUIRED")
    effective = resolve_winner_configuration(config, family="generation")
    if proof["configuration"] != effective.snapshot.as_dict():
        raise ValueError("MUTATION_WINNER_GENERATION_BIRTH_CONFIGURATION_MISMATCH")
    watermark = CohortGenerationService._generation_watermark(row)
    contract = CohortGenerationService._contract_from_generation(row)
    if row.watermark_hash != canonical_watermark_hash(
        watermark
    ) or row.generation_key != canonical_generation_key(contract, watermark):
        raise ValueError("MUTATION_WINNER_GENERATION_IDENTITY_MISMATCH")
    return proof


def statistic_body(row):
    body = {}
    for column in row.__table__.columns:
        if column.key in {"id", "created_at", "updated_at"}:
            continue
        value = getattr(row, column.key)
        if column.key == "metadata_json":
            value = {
                key: item for key, item in (value or {}).items() if key != "native_cohort_proof"
            }
        if isinstance(column.type, Numeric) and value is not None and column.type.scale is not None:
            value = Decimal(str(value)).quantize(Decimal(1).scaleb(-column.type.scale))
        body[column.key] = value
    return body


@core_writer_member(
    (
        "app.services.winner_probability.cohort_materialization_service:CohortMaterializationService.materialize_slice",
        "app.services.winner_probability.probability_estimator:ProbabilityEstimator._create_estimate",
        "app.services.winner_probability.probability_estimator:ProbabilityEstimator._materialize_cohort_statistic",
    )
)
def seal_statistic(db, row, *, evidence, config, statistics):
    from app.services.winner_probability.cohort_statistics import CohortStatisticsService

    bodies = population_bodies(db, evidence, financial=True, cutoff=row.training_cutoff_at)
    native = CohortStatisticsService().calculate(evidence, config)
    if native != statistics:
        raise ValueError("MUTATION_WINNER_COHORT_NATIVE_STATISTICS_MISMATCH")
    for field in (
        "sample_n",
        "effective_n",
        "wins",
        "raw_rate",
        "posterior_probability",
        "lower_bound",
        "upper_bound",
        "median_return_pct",
        "median_mfe_pct",
        "median_mae_pct",
        "evidence_grade",
    ):
        if getattr(row, field) != getattr(native, field):
            raise ValueError("MUTATION_WINNER_COHORT_FINANCIAL_RESULT_MISMATCH")
    row.metadata_json = {
        **(row.metadata_json or {}),
        "native_cohort_proof": {
            "contract": "winner-native-cohort-statistic-v1",
            "fingerprint": Canonical.fingerprint(statistic_body(row)),
            "configuration": resolve_winner_configuration(
                config, family="cohort"
            ).snapshot.as_dict(),
            "population": bodies,
            "semantics": "FROZEN_GENERATION" if row.generation_id else "CURRENT_RULES_INLINE",
        },
    }


def validate_statistic(db, row, config):
    retained_row(db, row)
    return validate_statistic_proof(row, config)


def validate_statistic_proof(row, config):
    proof = (row.metadata_json or {}).get("native_cohort_proof")
    if (
        not isinstance(proof, dict)
        or proof.get("fingerprint") != Canonical.fingerprint(statistic_body(row))
        or proof.get("configuration")
        != resolve_winner_configuration(config, family="cohort").snapshot.as_dict()
    ):
        raise ValueError("MUTATION_WINNER_CERTIFIED_COHORT_STATISTIC_REQUIRED")
    return proof


def validate_completion(db, generation, config):
    from app.models.tables import WinnerCohortStatistic, WinnerEvidenceManifest
    from app.services.winner_probability.cohort_statistics import CohortStatisticsService
    from app.services.winner_probability.evidence_manifest_service import EvidenceManifestService
    from app.services.winner_probability.mutation_authority import diagnostic_population

    proof = (generation.metrics_json or {}).get("native_generation_completion")
    rows = list(
        db.scalars(
            select(WinnerCohortStatistic)
            .where(WinnerCohortStatistic.generation_id == generation.id)
            .order_by(WinnerCohortStatistic.id)
        )
    )
    actual = [
        {
            "id": row.id,
            "fingerprint": Canonical.fingerprint(validate_statistic_proof(row, config)),
        }
        for row in rows
    ]
    if (
        not isinstance(proof, dict)
        or proof.get("statistics") != actual
        or proof.get("root_manifest_hash") != generation.root_manifest_hash
        or proof.get("planned_group_count") != generation.planned_group_count
        or len(rows) != generation.planned_group_count
        or generation.completed_group_count != len(rows)
        or generation.failed_group_count
    ):
        raise ValueError("MUTATION_WINNER_EXACT_GENERATION_COMPLETION_REQUIRED")
    root = db.scalar(
        select(WinnerEvidenceManifest).where(
            WinnerEvidenceManifest.manifest_hash == generation.root_manifest_hash
        )
    )
    EvidenceManifestService.validate_manifest_content(root)
    manifest_ids = {int(row.evidence_manifest_id) for row in rows}
    manifests = {
        int(manifest.id): manifest
        for manifest in db.scalars(
            select(WinnerEvidenceManifest).where(WinnerEvidenceManifest.id.in_(manifest_ids))
        )
    }
    if len(manifests) != len(manifest_ids):
        raise ValueError("MUTATION_WINNER_EXACT_MANIFEST_CONTENT_REQUIRED")
    for row in rows:
        manifest = manifests[int(row.evidence_manifest_id)]
        EvidenceManifestService.validate_manifest_content(manifest)
        evidence = diagnostic_population(
            db, manifest, generation.outcome_definition_id, as_of=generation.training_cutoff_at
        )
        population_bodies(db, evidence, financial=True, cutoff=generation.training_cutoff_at)
        native = CohortStatisticsService().calculate(evidence, config)
        for field in (
            "sample_n",
            "effective_n",
            "wins",
            "posterior_probability",
            "lower_bound",
            "upper_bound",
            "evidence_grade",
        ):
            if getattr(row, field) != getattr(native, field):
                raise ValueError("MUTATION_WINNER_GENERATION_STATISTIC_CONTENT_MISMATCH")
    return proof
