"""Contextual source permission, evaluated against exact frozen producer evidence."""

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Numeric, inspect, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import NO_VALUE

from app.models.tables import (
    CoreCalculationEvidence,
    FundamentalScore,
    SectorRotationRow,
    SectorRotationSnapshot,
)
from app.services.contextual_calculation_identity import (
    artifact_identity,
    build_ibmi_feature_identity,
)
from app.services.core_calculation_evidence import EvidenceUnavailableError
from app.services.producer_readiness import (
    ConsumerEligibilityDecision,
    ConsumerEligibilityStatus,
    EligibilityReason,
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessStatus,
    legacy_readiness,
    readiness_from_evidence,
)

CONTEXTUAL_ELIGIBILITY_KEY = "contextual_consumer_eligibility"
FUNDAMENTAL_ADMISSION_EVIDENCE_CONTRACT = "FUNDAMENTAL_ADMISSION_EVIDENCE_CONTRACT"


class FundamentalAdmissionEvidenceContractError(EvidenceUnavailableError):
    """A deterministic mismatch between a score and its persisted admission evidence."""

    code = FUNDAMENTAL_ADMISSION_EVIDENCE_CONTRACT

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        self.diagnostics = diagnostics or {}
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class ContextualConsumerPolicy:
    producer: str
    consumer: str
    policy_version: str
    module: str | None = None
    required_readiness_version: str | None = None

    def evaluate(
        self,
        readiness: ProducerReadinessEnvelope,
        *,
        consumer: str | None = None,
        config: NativeReadinessMetrics | None = None,
    ) -> ConsumerEligibilityDecision:
        from app.services.decision_effective_configuration import (
            validate_delivered_readiness_policy,
        )

        validate_delivered_readiness_policy(self)
        if readiness.producer != self.producer or consumer not in {None, self.consumer}:
            raise ValueError("Contextual eligibility producer/consumer mismatch")
        if config is not None and config.canonical_payload():
            raise ValueError("Contextual eligibility v1 has no configurable overrides")
        status = ConsumerEligibilityStatus.POLICY_UNDECIDED
        if readiness.blocking_reasons or readiness.status in {
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
            ReadinessStatus.ERROR,
            ReadinessStatus.STALE,
        }:
            status = ConsumerEligibilityStatus.INELIGIBLE
            reasons = (EligibilityReason.PRODUCER_READINESS_BLOCKED,)
        elif readiness.status is ReadinessStatus.READY:
            if self.required_readiness_version not in {None, readiness.readiness_policy_version}:
                reasons = (EligibilityReason.PRODUCER_READINESS_VERSION_UNSUPPORTED,)
            else:
                status = ConsumerEligibilityStatus.ELIGIBLE
                reasons = ()
        elif readiness.status is ReadinessStatus.DEGRADED:
            reasons = (EligibilityReason.DEGRADED_POLICY_UNDECIDED,)
        else:
            reasons = (EligibilityReason.PRODUCER_READINESS_UNKNOWN,)
        return ConsumerEligibilityDecision(
            self.consumer,
            status,
            reasons,
            self.policy_version,
            readiness.fingerprint(),
            readiness.evidence_id,
        )


IBMI_LIQUIDITY_TO_RANKING = ContextualConsumerPolicy(
    "IBMI",
    "RANKING",
    "ibmi-liquidity-to-ranking-v1",
    "LIQUIDITY",
)
IBMI_VOLATILITY_TO_CERI = ContextualConsumerPolicy(
    "IBMI",
    "CERI",
    "ibmi-volatility-to-ceri-v1",
    "VOLATILITY",
)
IBMI_SHORT_PRESSURE_TO_CERI = ContextualConsumerPolicy(
    "IBMI",
    "CERI",
    "ibmi-short-pressure-to-ceri-v1",
    "SHORT_PRESSURE",
)
REGIME_TO_SETUP = ContextualConsumerPolicy(
    "REGIME", "SETUP", "regime-to-setup-v2", required_readiness_version="regime-readiness-v2"
)
SECTOR_TO_SETUP = ContextualConsumerPolicy(
    "SECTOR", "SETUP", "sector-to-setup-v2", required_readiness_version="sector-readiness-v2"
)
FUNDAMENTAL_TO_COMBINED = ContextualConsumerPolicy(
    "FUNDAMENTAL", "COMBINED", "fundamental-to-combined-v1"
)
FUNDAMENTAL_TO_RANKING = ContextualConsumerPolicy(
    "FUNDAMENTAL", "RANKING", "fundamental-to-ranking-v1"
)
FUNDAMENTAL_TO_SETUP = ContextualConsumerPolicy("FUNDAMENTAL", "SETUP", "fundamental-to-setup-v1")
COMBINED_TO_SETUP = ContextualConsumerPolicy(
    "COMBINED", "SETUP", "combined-to-setup-v2", required_readiness_version="combined-readiness-v2"
)
COMBINED_TO_SECTOR = ContextualConsumerPolicy(
    "COMBINED",
    "SECTOR",
    "combined-to-sector-v2",
    required_readiness_version="combined-readiness-v2",
)
RANKING_TO_SECTOR = ContextualConsumerPolicy(
    "RANKING", "SECTOR", "ranking-to-sector-v2", required_readiness_version="ranking-readiness-v2"
)
REGIME_TO_SECTOR = ContextualConsumerPolicy(
    "REGIME", "SECTOR", "regime-to-sector-v2", required_readiness_version="regime-readiness-v2"
)
PRIOR_SECTOR_TO_SECTOR = ContextualConsumerPolicy(
    "SECTOR",
    "SECTOR",
    "prior-sector-to-sector-v2",
    required_readiness_version="sector-readiness-v2",
)


def contextual_decision_input(
    row: Any | None,
    policy: ContextualConsumerPolicy,
) -> tuple[Any | None, dict[str, Any]]:
    evidence = _resolve_contextual_evidence(row, policy)
    if evidence is not None:
        readiness = readiness_from_evidence(evidence)
    else:
        readiness = legacy_readiness(policy.producer)
    decision = policy.evaluate(readiness)
    included = decision.status is ConsumerEligibilityStatus.ELIGIBLE
    orthogonal_decision = None
    activation_mode = "LEGACY"
    if evidence is not None and policy in {
        FUNDAMENTAL_TO_COMBINED,
        FUNDAMENTAL_TO_RANKING,
    }:
        from app.services.eligibility_policy import ConsumerEligibilityOutcome
        from app.services.fundamental_admission_policy import (
            evaluate_fundamental_admission,
            fundamental_input_from_frozen_row,
        )
        from app.settings import FundamentalAdmissionMode, get_settings

        mode = get_settings().fundamental_admission_mode
        activation_mode = mode.value
        if mode is not FundamentalAdmissionMode.LEGACY:
            frozen_input = _fundamental_frozen_input(evidence)
            policy_input = fundamental_input_from_frozen_row(
                frozen_input,
                consumer_edge=f"FUNDAMENTAL_TO_{policy.consumer}",
            )
            orthogonal_decision = evaluate_fundamental_admission(
                policy_input,
                authoritative=mode is FundamentalAdmissionMode.ACTIVE,
            )
            legacy_outcome = {
                ConsumerEligibilityStatus.ELIGIBLE: ConsumerEligibilityOutcome.INCLUDED,
                ConsumerEligibilityStatus.INELIGIBLE: ConsumerEligibilityOutcome.REJECTED_REQUIRED,
                ConsumerEligibilityStatus.POLICY_UNDECIDED: ConsumerEligibilityOutcome.UNDECIDED,
            }[decision.status]
            if mode is FundamentalAdmissionMode.ACTIVE:
                included = (
                    orthogonal_decision.eligibility is ConsumerEligibilityOutcome.INCLUDED
                )
    frozen = {
        "decision": decision.to_dto(),
        "producer_readiness": readiness.to_dto(),
        "included": included,
        "source_feature_id": getattr(row, "id", None),
    }
    if activation_mode != "LEGACY":
        frozen["fundamental_admission_mode"] = activation_mode
    if orthogonal_decision is not None:
        frozen["orthogonal_decision"] = orthogonal_decision.canonical_payload()
        frozen["fundamental_admission_input"] = asdict(policy_input)
        frozen["legacy_outcome"] = legacy_outcome.value
    if not included:
        return None, frozen
    payload = evidence.payload_json
    if policy.producer == "IBMI":
        payload = payload["derived_output"]
    return _project_frozen(row, payload, evidence.id, evidence=evidence), frozen


def record_fundamental_admission(
    db: Session,
    *,
    row: Any,
    policy: ContextualConsumerPolicy,
    permission: dict[str, Any],
    pipeline_run_id: int | None,
) -> None:
    """Append the non-legacy decision produced at an actual consumer write boundary."""

    mode = permission.get("fundamental_admission_mode", "LEGACY")
    if mode == "LEGACY":
        return
    if policy not in {FUNDAMENTAL_TO_COMBINED, FUNDAMENTAL_TO_RANKING}:
        raise ValueError("Fundamental admission recording is limited to score-native edges")
    if object_session(row) is not db:
        raise ValueError("Fundamental admission recording requires the attached transaction")
    evidence = _resolve_contextual_evidence(row, policy)
    if evidence is None:
        _raise_evidence_contract(row, policy, "persisted evidence pointer is absent")
    _validate_admission_permission_binding(row, policy, permission, evidence)
    from app.services.consumer_eligibility_ledger import (
        EligibilityShadowEvaluation,
        persist_consumer_decision,
        persist_shadow_evaluation,
    )
    from app.services.eligibility_policy import ConsumerEligibilityOutcome
    from app.services.fundamental_admission_policy import (
        FUNDAMENTAL_ADMISSION_SHADOW_POLICY_VERSION,
        evaluate_fundamental_admission,
        fundamental_input_from_frozen_row,
    )
    from app.settings import FundamentalAdmissionMode

    activation = FundamentalAdmissionMode(mode)
    policy_input = fundamental_input_from_frozen_row(
        _fundamental_frozen_input(evidence),
        consumer_edge=f"FUNDAMENTAL_TO_{policy.consumer}",
    )
    decision = evaluate_fundamental_admission(
        policy_input,
        authoritative=activation is FundamentalAdmissionMode.ACTIVE,
    )
    if decision.canonical_payload() != permission.get("orthogonal_decision"):
        raise ValueError("Fundamental admission permission/evidence binding mismatch")
    if activation is FundamentalAdmissionMode.SHADOW:
        persist_shadow_evaluation(
            db,
            EligibilityShadowEvaluation(
                pipeline_run_id=pipeline_run_id,
                upload_run_id=evidence.run_id,
                ticker=evidence.ticker,
                consumer_edge=decision.consumer_edge,
                policy_version=FUNDAMENTAL_ADMISSION_SHADOW_POLICY_VERSION,
                source_identity_fingerprint=evidence.calculation_identity_fingerprint,
                decision_cutoff=decision.decision_cutoff,
                legacy_outcome=ConsumerEligibilityOutcome(permission["legacy_outcome"]),
                proposed_decision=decision,
                frozen_input=asdict(policy_input),
            ),
        )
        return
    persist_consumer_decision(
        db,
        decision=decision,
        source_artifact_kind="FUNDAMENTAL",
        pipeline_run_id=pipeline_run_id,
        upload_run_id=evidence.run_id,
    )


def certify_fundamental_admission_orm_contract(
    db: Session,
    *,
    expected_mode: str,
) -> dict[str, Any]:
    """Exercise the real admission boundary on an unloaded row without retaining writes."""

    if expected_mode == "LEGACY":
        return {"status": "SKIPPED_LEGACY", "relationship_initially_loaded": None}
    if not isinstance(db, Session):
        raise TypeError("Fundamental admission ORM certification requires a real Session")
    with Session(bind=db.get_bind()) as verification_db:
        row = verification_db.scalar(
            select(FundamentalScore)
            .where(FundamentalScore.evidence_id.is_not(None))
            .order_by(FundamentalScore.id.desc())
            .limit(1)
        )
        if row is None:
            raise FundamentalAdmissionEvidenceContractError(
                "deployment certification requires a persisted representative score",
                diagnostics={"contract": "fundamental-admission-evidence-v1"},
            )
        initially_loaded = (
            inspect(row).attrs.calculation_evidence.loaded_value is not NO_VALUE
        )
        if initially_loaded:
            raise FundamentalAdmissionEvidenceContractError(
                "deployment certification probe did not start with unloaded evidence",
                diagnostics={
                    "contract": "fundamental-admission-evidence-v1",
                    "score_id": row.id,
                    "run_id": row.run_id,
                    "ticker": row.ticker.upper(),
                    "evidence_id": row.evidence_id,
                },
            )
        try:
            _projected, permission = contextual_decision_input(row, FUNDAMENTAL_TO_COMBINED)
            if permission.get("fundamental_admission_mode") != expected_mode:
                raise FundamentalAdmissionEvidenceContractError(
                    "runtime admission mode does not match the certification request",
                    diagnostics={
                        "contract": "fundamental-admission-evidence-v1",
                        "expected_mode": expected_mode,
                        "observed_mode": permission.get("fundamental_admission_mode", "LEGACY"),
                    },
                )
            rollback_only = verification_db.begin_nested()
            try:
                record_fundamental_admission(
                    verification_db,
                    row=row,
                    policy=FUNDAMENTAL_TO_COMBINED,
                    permission=permission,
                    pipeline_run_id=None,
                )
                verification_db.flush()
            finally:
                rollback_only.rollback()
        except InvalidRequestError as exc:
            raise FundamentalAdmissionEvidenceContractError(
                "unloaded relationship reached an ORM access forbidden by lazy='raise'",
                diagnostics={
                    "contract": "fundamental-admission-evidence-v1",
                    "score_id": row.id,
                    "run_id": row.run_id,
                    "ticker": row.ticker.upper(),
                    "evidence_id": row.evidence_id,
                    "orm_error_type": exc.__class__.__name__,
                },
            ) from exc
        finally:
            verification_db.rollback()
        return {
            "status": "PASSED",
            "mode": expected_mode,
            "relationship_initially_loaded": initially_loaded,
            "sample_score_id": row.id,
            "sample_evidence_id": row.evidence_id,
        }


def _resolve_contextual_evidence(
    row: Any | None,
    policy: ContextualConsumerPolicy,
) -> CoreCalculationEvidence | None:
    """Resolve and validate an exact evidence pointer without requiring relationship state."""

    if row is None or getattr(row, "evidence_id", None) is None:
        return None
    loaded = inspect(row).attrs.calculation_evidence.loaded_value
    if loaded is NO_VALUE:
        db = object_session(row)
        if db is None:
            _raise_evidence_contract(row, policy, "detached context not loaded")
        with db.no_autoflush:
            evidence = db.get(CoreCalculationEvidence, row.evidence_id)
    else:
        evidence = loaded
    if evidence is None or evidence.id != row.evidence_id:
        _raise_evidence_contract(row, policy, "contextual pointer unresolved")
    if evidence.artifact_kind != policy.producer:
        _raise_evidence_contract(row, policy, "contextual producer mismatch")
    if policy.producer == "IBMI":
        identity = build_ibmi_feature_identity(row)
        if (
            row.module != policy.module
            or evidence.ranking_profile != policy.module
            or evidence.ticker != row.ticker.upper()
        ):
            _raise_evidence_contract(row, policy, "contextual module/scope mismatch")
    else:
        identity = artifact_identity(row)
        if evidence.run_id != row.run_id:
            _raise_evidence_contract(row, policy, "contextual owner mismatch")
        if (
            policy.producer in {"FUNDAMENTAL", "COMBINED", "TECHNICAL"}
            and evidence.ticker != row.ticker.upper()
        ):
            _raise_evidence_contract(row, policy, "contextual ticker mismatch")
        if policy.producer == "RANKING" and (
            evidence.ticker != row.ticker.upper()
            or evidence.ranking_profile != row.ranking_profile
        ):
            _raise_evidence_contract(row, policy, "Ranking ticker/profile mismatch")
    if str(identity.fingerprint()) != evidence.calculation_identity_fingerprint:
        _raise_evidence_contract(row, policy, "contextual identity mismatch")
    return evidence


def _validate_admission_permission_binding(
    row: Any,
    policy: ContextualConsumerPolicy,
    permission: dict[str, Any],
    evidence: CoreCalculationEvidence,
) -> None:
    if permission.get("source_feature_id") != row.id:
        _raise_evidence_contract(row, policy, "permission source score does not match")
    bound_evidence_ids = (
        (permission.get("producer_readiness") or {}).get("evidence_id"),
        (permission.get("decision") or {}).get("producer_evidence_id"),
        (permission.get("orthogonal_decision") or {}).get("source_evidence_id"),
    )
    if any(evidence_id != evidence.id for evidence_id in bound_evidence_ids):
        _raise_evidence_contract(row, policy, "permission evidence pointer does not match")
    orthogonal = permission.get("orthogonal_decision") or {}
    if orthogonal.get("source_identity_fingerprint") != evidence.calculation_identity_fingerprint:
        _raise_evidence_contract(row, policy, "permission evidence identity does not match")
    if orthogonal.get("consumer_edge") != f"FUNDAMENTAL_TO_{policy.consumer}":
        _raise_evidence_contract(row, policy, "permission consumer edge does not match")


def _raise_evidence_contract(
    row: Any,
    policy: ContextualConsumerPolicy,
    message: str,
) -> None:
    if policy in {FUNDAMENTAL_TO_COMBINED, FUNDAMENTAL_TO_RANKING}:
        raise FundamentalAdmissionEvidenceContractError(
            message,
            diagnostics={
                "contract": "fundamental-admission-evidence-v1",
                "consumer": policy.consumer,
                "score_id": getattr(row, "id", None),
                "run_id": getattr(row, "run_id", None),
                "ticker": str(getattr(row, "ticker", "")).upper() or None,
                "evidence_id": getattr(row, "evidence_id", None),
            },
        )
    raise EvidenceUnavailableError(f"EVIDENCE_UNAVAILABLE: {message}")


def _fundamental_frozen_input(evidence: CoreCalculationEvidence) -> dict[str, Any]:
    payload = evidence.payload_json
    debug = payload.get("debug_json") or {}
    cutoff = evidence.calculated_at
    if cutoff is None or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise EvidenceUnavailableError(
            "EVIDENCE_UNAVAILABLE: Fundamental calculation cutoff is not certified"
        )
    return {
        "calculation_identity_fingerprint": evidence.calculation_identity_fingerprint,
        "evidence_id": evidence.id,
        "decision_cutoff": cutoff,
        "fundamental_score": payload.get("fundamental_score"),
        "fundamental_label": payload.get("fundamental_label"),
        "scoring_model_version": payload.get("scoring_model_version"),
        "coverage": debug.get("coverage") or {},
        "component_coverage": debug.get("component_coverage") or {},
        "parse_diagnostics": debug.get("parse_diagnostics") or {},
        "v2_warning_flags_json": payload.get("v2_warning_flags_json") or {},
    }


def frozen_sector_row(snapshot: SectorRotationSnapshot | None, row: Any | None):
    if snapshot is None or row is None:
        return None
    evidence = snapshot.calculation_evidence
    candidates = [
        item
        for item in evidence.payload_json["rows"]
        if str(item["sector"]).casefold() == str(row.sector).casefold()
    ]
    if row.snapshot_id != snapshot.id or len(candidates) != 1:
        raise EvidenceUnavailableError("EVIDENCE_UNAVAILABLE: Sector row/snapshot mismatch")
    return _project_frozen(row, candidates[0], evidence.id)


def _project_frozen(
    row: Any,
    payload: dict[str, Any],
    evidence_id: int,
    *,
    evidence: Any | None = None,
):
    values = {}
    for column in inspect(type(row)).columns:
        key = column.key
        if key not in payload and key.endswith("_json"):
            key = key.removesuffix("_json")
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(column.type, Numeric) and value is not None:
            value = Decimal(str(value))
            native = getattr(row, column.key)
            if isinstance(native, Decimal) and native == value:
                value = native
        elif isinstance(column.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif isinstance(column.type, Date) and isinstance(value, str):
            value = date.fromisoformat(value)
        values[column.key] = value
    values["id"] = row.id
    if isinstance(row, SectorRotationRow):
        values["snapshot_id"] = row.snapshot_id
    else:
        values["evidence_id"] = evidence_id
    result = type(row)(**values)
    if evidence is not None:
        result.calculation_evidence = evidence
    return result


def setup_with_contextual_permission(snapshot: Any):
    """Apply recorded omission, never reevaluate historical decisions under today's policy.

    Optional missing context does not block the complete consumer. This removes only
    contextual influences, including mutable residual values in a Setup projection.
    """
    frozen = snapshot.source_lineage.get(CONTEXTUAL_ELIGIBILITY_KEY)
    if frozen is None:
        source_ids = getattr(snapshot, "source_ids", {}) or snapshot.source_lineage.get(
            "source_ids", {}
        )
        if not any(
            source_ids.get(key)
            for key in (
                "market_regime_snapshot_id",
                "sector_rotation_snapshot_id",
            )
        ) and not snapshot.source_lineage.get("calculation_identity"):
            return snapshot
        frozen = {}
    omitted_keys = []
    for edge, producer, keys in (
        ("regime", "REGIME", ("market_regime", "market_gate")),
        ("sector", "SECTOR", ("sector_rank", "sector_confidence")),
        ("fundamental", "FUNDAMENTAL", ("liquidity",)),
        ("combined", "COMBINED", ("earnings_risk",)),
    ):
        permission = frozen.get(edge)
        if permission is None:
            omitted_keys.extend(keys)
            continue
        dto = permission["decision"]
        readiness = ProducerReadinessEnvelope.from_payload(permission["producer_readiness"])
        decoded = ConsumerEligibilityDecision(
            dto["consumer"],
            ConsumerEligibilityStatus(dto["status"]),
            tuple(EligibilityReason(reason) for reason in dto["reasons"]),
            dto["policy_version"],
            dto["producer_readiness_fingerprint"],
            dto["producer_evidence_id"],
        )
        if (
            decoded.consumer != "SETUP"
            or readiness.producer != producer
            or decoded.producer_readiness_fingerprint != readiness.fingerprint()
            or decoded.producer_evidence_id != readiness.evidence_id
        ):
            raise ValueError("Setup contextual eligibility/readiness binding mismatch")
        if decoded.status is not ConsumerEligibilityStatus.ELIGIBLE:
            omitted_keys.extend(keys)
    if not omitted_keys:
        return snapshot
    signals = dict(snapshot.signals)
    lineage = dict(snapshot.source_lineage)
    if "market_regime" in omitted_keys:
        lineage["market_regime_as_of"] = None
    if "sector_rank" in omitted_keys:
        lineage["sector_rotation_as_of"] = None
    for key in omitted_keys:
        if key in signals:
            signals[key] = replace(signals[key], raw_value=None, normalized_value=None)
    return replace(snapshot, signals=signals, source_lineage=lineage)
