"""Readiness-at-creation, independent of consumer actionability policies."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from types import SimpleNamespace
from typing import Any, Protocol

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

READINESS_POLICY_VERSION = "producer-readiness-v1"
READINESS_PAYLOAD_KEY = "producer_readiness"


class ReadinessStatus(Enum):
    # Deliberately not StrEnum: LifecycleState.READY must not compare equal.
    READY = "READY"
    DEGRADED = "DEGRADED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STALE = "STALE"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


class ReadinessReason(Enum):
    SIGNALS_UNAVAILABLE = "SIGNALS_UNAVAILABLE"
    LEGACY_READINESS_UNKNOWN = "LEGACY_READINESS_UNKNOWN"
    PRODUCER_WARNING = "PRODUCER_WARNING"
    LOW_NATIVE_CONFIDENCE = "LOW_NATIVE_CONFIDENCE"
    INSUFFICIENT_NATIVE_CONFIDENCE = "INSUFFICIENT_NATIVE_CONFIDENCE"
    TECH_INSUFFICIENT_HISTORY = "TECH_INSUFFICIENT_HISTORY"
    TECH_ERROR = "TECH_ERROR"
    FUNDAMENTAL_MISSING_DATA = "FUNDAMENTAL_MISSING_DATA"
    COMBINED_INCOMPLETE = "COMBINED_INCOMPLETE"
    RANKING_INCOMPLETE = "RANKING_INCOMPLETE"
    REGIME_STALE_INPUT = "REGIME_STALE_INPUT"
    SECTOR_INCOMPLETE_CONTEXT = "SECTOR_INCOMPLETE_CONTEXT"
    CERI_INSUFFICIENT_AVAILABLE_WEIGHT = "CERI_INSUFFICIENT_AVAILABLE_WEIGHT"
    CERI_STALE_SOURCE = "CERI_STALE_SOURCE"
    IBMI_UNAVAILABLE = "IBMI_UNAVAILABLE"
    IBMI_ERROR = "IBMI_ERROR"
    IBMI_STALE_SOURCE = "IBMI_STALE_SOURCE"
    SETUP_STALE_SOURCE = "SETUP_STALE_SOURCE"
    SETUP_NEAR_STALE_SOURCE = "SETUP_NEAR_STALE_SOURCE"
    INSUFFICIENT_NATIVE_DATA_QUALITY = "INSUFFICIENT_NATIVE_DATA_QUALITY"
    LOW_NATIVE_DATA_QUALITY = "LOW_NATIVE_DATA_QUALITY"
    LIFECYCLE_OBSERVATION_GAP = "LIFECYCLE_OBSERVATION_GAP"


@dataclass(frozen=True)
class NativeReadinessMetrics:
    """Immutable canonical storage; every decoded view is a defensive copy.

    Field names and units remain producer-native, including nested ledgers.
    """

    canonical_json: str = "{}"

    def __post_init__(self) -> None:
        values = json.loads(self.canonical_json)
        if not isinstance(values, dict):
            raise TypeError("native metrics require a named metric object")
        object.__setattr__(self, "canonical_json", Canonical.dumps(values))

    @classmethod
    def freeze(cls, values: dict[str, Any]) -> NativeReadinessMetrics:
        return cls(Canonical.dumps(values))

    def canonical_payload(self) -> dict[str, Any]:
        return json.loads(self.canonical_json)


@dataclass(frozen=True)
class ProducerReadinessEnvelope:
    producer: str
    status: ReadinessStatus
    calculation_identity_fingerprint: str | None
    blocking_reasons: tuple[ReadinessReason, ...] = ()
    warning_reasons: tuple[ReadinessReason, ...] = ()
    native_confidence: NativeReadinessMetrics = NativeReadinessMetrics()
    native_coverage: NativeReadinessMetrics = NativeReadinessMetrics()
    native_freshness: NativeReadinessMetrics = NativeReadinessMetrics()
    native_signals: NativeReadinessMetrics = NativeReadinessMetrics()
    calculation_versions: NativeReadinessMetrics = NativeReadinessMetrics()
    evaluated_at: str | None = None
    business_anchor: str | None = None
    readiness_policy_version: str = READINESS_POLICY_VERSION
    evidence_id: int | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not ReadinessStatus:
            raise TypeError("status must be ReadinessStatus, never LifecycleState")
        for name in (
            "native_confidence",
            "native_coverage",
            "native_freshness",
            "native_signals",
            "calculation_versions",
        ):
            if type(getattr(self, name)) is not NativeReadinessMetrics:
                raise TypeError("native metrics must be immutable NativeReadinessMetrics")
        if self.status not in {ReadinessStatus.UNKNOWN, ReadinessStatus.LEGACY_UNKNOWN} and (
            not self.calculation_identity_fingerprint
        ):
            raise ValueError("supported readiness must bind to Calculation Identity")
        for name in ("blocking_reasons", "warning_reasons"):
            reasons = getattr(self, name)
            if any(type(reason) is not ReadinessReason for reason in reasons):
                raise TypeError("readiness reasons must be typed")
            object.__setattr__(self, name, tuple(sorted(set(reasons), key=lambda r: r.value)))
        if self.blocking_reasons and self.status in {
            ReadinessStatus.READY,
            ReadinessStatus.DEGRADED,
        }:
            raise ValueError("blocking readiness reasons cannot certify ready/degraded output")

    def canonical_payload(self) -> dict[str, Any]:
        # The database address is assigned after hashing, and is not semantic input.
        return {
            "producer": self.producer,
            "status": self.status.value,
            "calculation_identity_fingerprint": self.calculation_identity_fingerprint,
            "blocking_reasons": [r.value for r in self.blocking_reasons],
            "warning_reasons": [r.value for r in self.warning_reasons],
            **{
                name: getattr(self, name).canonical_payload()
                for name in (
                    "native_confidence",
                    "native_coverage",
                    "native_freshness",
                    "native_signals",
                    "calculation_versions",
                )
            },
            "evaluated_at": self.evaluated_at,
            "business_anchor": self.business_anchor,
            "readiness_policy_version": self.readiness_policy_version,
        }

    def to_dto(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "evidence_id": self.evidence_id}

    def fingerprint(self) -> str:
        return Canonical.fingerprint(self.canonical_payload())

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ProducerReadinessEnvelope:
        values = dict(payload)
        values["status"] = ReadinessStatus(values["status"])
        for name in ("blocking_reasons", "warning_reasons"):
            values[name] = tuple(ReadinessReason(r) for r in values[name])
        for name in (
            "native_confidence",
            "native_coverage",
            "native_freshness",
            "native_signals",
            "calculation_versions",
        ):
            values[name] = NativeReadinessMetrics.freeze(values[name])
        return cls(**values)


class ConsumerEligibilityStatus(Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    POLICY_UNDECIDED = "POLICY_UNDECIDED"


class EligibilityReason(Enum):
    POLICY_NOT_IMPLEMENTED = "POLICY_NOT_IMPLEMENTED"
    PRODUCER_READINESS_UNKNOWN = "PRODUCER_READINESS_UNKNOWN"


@dataclass(frozen=True)
class ConsumerEligibilityDecision:
    consumer: str
    status: ConsumerEligibilityStatus
    reasons: tuple[EligibilityReason, ...]
    policy_version: str
    producer_readiness_fingerprint: str
    producer_evidence_id: int | None

    def __post_init__(self) -> None:
        if type(self.status) is not ConsumerEligibilityStatus:
            raise TypeError("eligibility requires ConsumerEligibilityStatus")
        if any(type(reason) is not EligibilityReason for reason in self.reasons):
            raise TypeError("eligibility reasons must be typed")
        object.__setattr__(self, "reasons", tuple(sorted(set(self.reasons), key=lambda r: r.value)))

    def to_dto(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer,
            "status": self.status.value,
            "reasons": [r.value for r in self.reasons],
            "policy_version": self.policy_version,
            "producer_readiness_fingerprint": self.producer_readiness_fingerprint,
            "producer_evidence_id": self.producer_evidence_id,
        }


class ConsumerEligibilityPolicy(Protocol):
    def evaluate(
        self,
        readiness: ProducerReadinessEnvelope,
        *,
        consumer: str,
        config: NativeReadinessMetrics,
    ) -> ConsumerEligibilityDecision: ...


class UndecidedConsumerEligibilityPolicy:
    """Placeholder only. No business consumer is migrated by T12A."""

    def evaluate(
        self,
        readiness: ProducerReadinessEnvelope,
        *,
        consumer: str,
        config: NativeReadinessMetrics | None = None,
    ) -> ConsumerEligibilityDecision:
        reasons = [EligibilityReason.POLICY_NOT_IMPLEMENTED]
        if readiness.status in {ReadinessStatus.UNKNOWN, ReadinessStatus.LEGACY_UNKNOWN}:
            reasons.append(EligibilityReason.PRODUCER_READINESS_UNKNOWN)
        return ConsumerEligibilityDecision(
            consumer,
            ConsumerEligibilityStatus.POLICY_UNDECIDED,
            tuple(reasons),
            "undecided-v1",
            readiness.fingerprint(),
            readiness.evidence_id,
        )


def normalize_producer_readiness(
    producer: str,
    payload: dict[str, Any],
    *,
    identity_fingerprint: str,
    calculation_versions: dict[str, Any] | None = None,
    evaluated_at: Any = None,
    business_anchor: Any = None,
) -> ProducerReadinessEnvelope:
    """Interpret only already-produced signals, without new numerical thresholds."""
    output = payload.get("decision_output", payload.get("derived_output", payload))
    output = Canonical.canonicalize(output)
    confidence_names = (
        "technical_confidence",
        "confidence",
        "data_confidence",
        "confidence_score",
        "confidence_label",
        "data_quality_score",
        "data_quality_label",
        "technical_data_quality",
        "confidence_ledger_json",
        "confidence_components",
    )
    coverage_names = (
        "data_coverage_score",
        "coverage_pct",
        "opportunity_coverage_pct",
        "coverage_status",
        "required_feature_coverage",
        "fundamental_coverage",
        "sector_count",
        "ticker_count",
    )
    freshness_names = (
        "freshness_status",
        "input_as_of_session",
        "input_symbols",
        "index_health",
    )
    signal_names = (
        "insufficient_data",
        "missing_data_json",
        "warning_flags_json",
        "warnings_json",
        "v2_warning_flags_json",
        "warning_flags",
        "warnings",
        "is_complete",
        "missing_data_penalty",
        "opportunity_ledger_json",
        "data_quality_label",
        "eligibility_status",
        "exclusion_reason",
        "reasons_json",
        "components_json",
        "missing_observation_sessions",
        "observation_gap_threshold",
    )
    confidence = _select(output, confidence_names)
    coverage = _select(output, coverage_names)
    freshness = _select(output, freshness_names)
    signals = _select(output, signal_names)
    warnings: list[ReadinessReason] = []
    blocks: list[ReadinessReason] = []
    status = ReadinessStatus.UNKNOWN
    label_keys = (
        ("confidence_label", "technical_confidence")
        if producer in {"SETUP", "LIFECYCLE"}
        else (
            "technical_confidence",
            "confidence",
            "data_confidence",
            "confidence_label",
            "technical_data_quality",
        )
    )
    label = next(
        (str(output[k]).upper() for k in label_keys if output.get(k) is not None),
        "",
    )
    if label in {"HIGH", "NORMAL"}:
        status = ReadinessStatus.READY
    elif label == "LOW":
        status = ReadinessStatus.DEGRADED
        warnings.append(ReadinessReason.LOW_NATIVE_CONFIDENCE)
    elif label == "INSUFFICIENT":
        status = ReadinessStatus.INSUFFICIENT_EVIDENCE
        blocks.append(ReadinessReason.INSUFFICIENT_NATIVE_CONFIDENCE)
    native_warnings = any(
        output.get(k)
        for k in (
            "warning_flags_json",
            "warnings_json",
            "warning_flags",
            "warnings",
        )
    )
    if native_warnings:
        warnings.append(ReadinessReason.PRODUCER_WARNING)
    if producer == "TECHNICAL":
        missing = output.get("missing_data_json") or {}
        history = (output.get("v4_debug_json") or {}).get("data_readiness") or {}
        if history:
            signals["technical_data_readiness"] = history
        error_details = (output.get("debug_json") or {}).get("error")
        if error_details is not None:
            signals["error"] = error_details
        if (
            output.get("insufficient_data") is True
            or missing.get("insufficient_history")
            or history.get("has_sufficient_history") is False
        ):
            status = ReadinessStatus.INSUFFICIENT_EVIDENCE
            blocks.append(ReadinessReason.TECH_INSUFFICIENT_HISTORY)
        if label == "ERROR":
            status = ReadinessStatus.ERROR
            blocks.append(ReadinessReason.TECH_ERROR)
        if output.get("insufficient_data") is not False and not blocks:
            status = ReadinessStatus.UNKNOWN
    elif producer == "FUNDAMENTAL":
        # Coverage is preserved in its native units; sparse-data flag is existing policy.
        if output.get("data_coverage_score") is not None:
            status = ReadinessStatus.READY
        debug_coverage = (output.get("debug_json") or {}).get("coverage")
        if debug_coverage:
            coverage["coverage"] = debug_coverage
        diagnostics = (output.get("debug_json") or {}).get("parse_diagnostics")
        if diagnostics is not None:
            signals["parse_diagnostics"] = diagnostics
        if (output.get("v2_warning_flags_json") or {}).get("flags"):
            warnings.append(ReadinessReason.PRODUCER_WARNING)
        if "sparse_fundamental_data" in (
            (output.get("v2_warning_flags_json") or {}).get("flags") or []
        ) or output.get("missing_data_penalty") not in (
            None,
            0,
            "0",
        ):
            status = ReadinessStatus.DEGRADED
            warnings.append(ReadinessReason.FUNDAMENTAL_MISSING_DATA)
    elif producer in {"COMBINED", "RANKING"}:
        complete = output.get("is_complete")
        if complete is True:
            status = ReadinessStatus.READY
        elif complete is False:
            status = ReadinessStatus.INSUFFICIENT_EVIDENCE
            blocks.append(ReadinessReason[f"{producer}_INCOMPLETE"])
    elif producer == "REGIME":
        all_warnings = output.get("warnings", output.get("warnings_json", [])) or []
        if "stale_market_data" in all_warnings or "severely_stale_market_data" in all_warnings:
            status = ReadinessStatus.STALE
            blocks.append(ReadinessReason.REGIME_STALE_INPUT)
    elif producer == "SECTOR":
        rows = output.get("rows")
        if isinstance(rows, list):
            rows = sorted(rows, key=Canonical.dumps)
            confidence["sector_confidences"] = [
                {"sector": row.get("sector"), "confidence": row.get("confidence")} for row in rows
            ]
            signals["sector_warnings"] = [
                {"sector": row.get("sector"), "warnings": row.get("warnings", [])} for row in rows
            ]
            if any(row.get("warnings") for row in rows):
                warnings.append(ReadinessReason.PRODUCER_WARNING)
            labels = {str(row.get("confidence", "")).upper() for row in rows}
            if rows and labels <= {"HIGH", "NORMAL"}:
                status = ReadinessStatus.READY
            elif labels & {"LOW", "INSUFFICIENT"}:
                status = ReadinessStatus.DEGRADED
                warnings.append(ReadinessReason.SECTOR_INCOMPLETE_CONTEXT)
    elif producer == "CERI":
        opportunity = output.get("opportunity_ledger_json") or {}
        if opportunity.get("rated") is False or "opportunity_component_coverage_insufficient" in (
            output.get("warnings_json") or []
        ):
            status = ReadinessStatus.INSUFFICIENT_EVIDENCE
            blocks.append(ReadinessReason.CERI_INSUFFICIENT_AVAILABLE_WEIGHT)
        coverage["opportunity_ledger"] = opportunity
        details = (output.get("confidence_ledger_json") or {}).get("freshness")
        if details:
            freshness["provider_feed_freshness"] = details
            if details.get("status") == "STALE":
                blocks.append(ReadinessReason.CERI_STALE_SOURCE)
                if status not in {ReadinessStatus.ERROR, ReadinessStatus.INSUFFICIENT_EVIDENCE}:
                    status = ReadinessStatus.STALE
    elif producer == "IBMI":
        availability = output.get("coverage_status")
        if availability == "FAILED" or output.get("freshness_status") == "FAILED":
            status = ReadinessStatus.ERROR
            blocks.append(ReadinessReason.IBMI_ERROR)
        elif availability in {"UNAVAILABLE", "NOT_SUPPORTED", "SUBSCRIPTION_REQUIRED"}:
            status = ReadinessStatus.INSUFFICIENT_EVIDENCE
            blocks.append(ReadinessReason.IBMI_UNAVAILABLE)
        elif availability != "AVAILABLE" and not blocks:
            status = ReadinessStatus.UNKNOWN
        if output.get("freshness_status") == "STALE":
            blocks.append(ReadinessReason.IBMI_STALE_SOURCE)
            if status not in {ReadinessStatus.ERROR, ReadinessStatus.INSUFFICIENT_EVIDENCE}:
                status = ReadinessStatus.STALE
        elif output.get("freshness_status") != "AVAILABLE" and not blocks:
            status = ReadinessStatus.UNKNOWN
    elif producer == "WINNER":
        # Upstream quality labels and Winner exclusion status are not a producer
        # certification of probability evidence. That contract is deferred to T12D.
        status = ReadinessStatus.UNKNOWN
    elif producer == "SETUP":
        quality = str(output.get("data_quality_label") or "").upper()
        if quality == "INSUFFICIENT":
            blocks.append(ReadinessReason.INSUFFICIENT_NATIVE_DATA_QUALITY)
            status = ReadinessStatus.INSUFFICIENT_EVIDENCE
        elif quality == "LOW":
            warnings.append(ReadinessReason.LOW_NATIVE_DATA_QUALITY)
        if output.get("freshness_status") == "STALE":
            blocks.append(ReadinessReason.SETUP_STALE_SOURCE)
            if status is not ReadinessStatus.INSUFFICIENT_EVIDENCE:
                status = ReadinessStatus.STALE
        elif output.get("freshness_status") == "NEAR_STALE":
            warnings.append(ReadinessReason.SETUP_NEAR_STALE_SOURCE)
    elif producer == "LIFECYCLE" and output.get("missing_observation_sessions", 0) > 0:
        warnings.append(ReadinessReason.LIFECYCLE_OBSERVATION_GAP)
    # Lifecycle trading state and Winner consumer exclusions are never readiness inputs.
    if warnings and status is ReadinessStatus.READY:
        status = ReadinessStatus.DEGRADED
    if status is ReadinessStatus.UNKNOWN:
        warnings.append(ReadinessReason.SIGNALS_UNAVAILABLE)
    return ProducerReadinessEnvelope(
        producer=producer,
        status=status,
        calculation_identity_fingerprint=identity_fingerprint,
        blocking_reasons=tuple(blocks),
        warning_reasons=tuple(warnings),
        native_confidence=NativeReadinessMetrics.freeze(confidence),
        native_coverage=NativeReadinessMetrics.freeze(coverage),
        native_freshness=NativeReadinessMetrics.freeze(freshness),
        native_signals=NativeReadinessMetrics.freeze(signals),
        calculation_versions=NativeReadinessMetrics.freeze(calculation_versions or {}),
        evaluated_at=Canonical.canonicalize(evaluated_at),
        business_anchor=Canonical.canonicalize(business_anchor),
    )


def readiness_from_evidence(evidence: Any) -> ProducerReadinessEnvelope:
    """Read frozen metadata only; never re-normalize old evidence under today's policy."""
    payload = evidence.payload_json or {}
    stored = payload.get(READINESS_PAYLOAD_KEY)
    if stored is None:
        return legacy_readiness(
            evidence.artifact_kind,
            evidence_id=evidence.id,
            identity_fingerprint=evidence.calculation_identity_fingerprint,
        )
    envelope = ProducerReadinessEnvelope.from_payload(stored)
    if (
        envelope.producer != evidence.artifact_kind
        or envelope.calculation_identity_fingerprint != evidence.calculation_identity_fingerprint
    ):
        raise ValueError("readiness/evidence identity mismatch")
    return replace(envelope, evidence_id=evidence.id)


def readiness_from_lifecycle_evaluation(evaluation: Any) -> ProducerReadinessEnvelope:
    return readiness_from_evidence(
        SimpleNamespace(
            id=evaluation.id,
            artifact_kind="LIFECYCLE",
            payload_json=evaluation.payload_json,
            calculation_identity_fingerprint=evaluation.calculation_identity_fingerprint,
        )
    )


def readiness_from_winner_prediction(prediction: Any) -> ProducerReadinessEnvelope:
    from app.services.combined_ranking_identity import calculation_identity_from_debug

    identity = calculation_identity_from_debug(prediction.lineage_json)
    return readiness_from_evidence(
        SimpleNamespace(
            id=prediction.id,
            artifact_kind="WINNER",
            payload_json=prediction.lineage_json,
            calculation_identity_fingerprint=str(identity.fingerprint()) if identity else None,
        )
    )


def legacy_readiness(
    producer: str,
    *,
    evidence_id: int | None = None,
    identity_fingerprint: str | None = None,
) -> ProducerReadinessEnvelope:
    return ProducerReadinessEnvelope(
        producer,
        ReadinessStatus.LEGACY_UNKNOWN,
        identity_fingerprint,
        warning_reasons=(ReadinessReason.LEGACY_READINESS_UNKNOWN,),
        evidence_id=evidence_id,
    )


def _select(output: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: output[name] for name in names if output.get(name) is not None}
