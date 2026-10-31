"""Independent, stdlib-only expectations; no application imports or selectors.

An incomplete domain is an explicit obligation, never an inferred pass.
Actual projections/reconciliation and certificate aggregation belong to A04.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION = "a05-v3"
DATA = Path(__file__).with_name("golden") / "v3"
MANIFEST_SHA256 = "ffe8d308d0146fa6b6728b497554a907e5ac7cee3d0c15bc5d831efb726dcabe"


class OracleMismatch(AssertionError):
    pass


class OracleIncomplete(RuntimeError):
    pass


def canonical_hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_golden(directory: Path = DATA, *, verify_sources: bool = True) -> dict:
    encoded = (directory / "manifest.json").read_bytes()
    if hashlib.sha256(encoded).hexdigest() != MANIFEST_SHA256:
        raise OracleMismatch("GOLDEN_MANIFEST_HASH_MISMATCH")
    manifest = json.loads(encoded)
    if manifest["version"] != VERSION:
        raise OracleMismatch("GOLDEN_VERSION_MISMATCH")
    for relative, expected in manifest["artifacts"].items():
        path = directory / relative
        if path.parent != directory or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise OracleMismatch(f"GOLDEN_INPUT_HASH_MISMATCH: {relative}")
    if verify_sources:
        for relative, expected in manifest["config_sources"].items():
            if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
                raise OracleMismatch(f"GOLDEN_CONFIGURATION_DRIFT: {relative}")
    return manifest


def artifact(name: str):
    # No cached verification: modified files must fail even within one process.
    load_golden()
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def expected_manifest(handoff: bool) -> dict:
    if type(handoff) is not bool:
        raise OracleMismatch("HANDOFF_MUST_BE_BOOLEAN")
    inputs = load_golden()
    expected = artifact("expected-results.json")
    config = artifact("configuration.json")
    flags = {**artifact("runtime-settings.json"), **{
        "ceri_enabled": True, "ceri_provider_ingest_enabled": True,
        "ceri_batched_workflow_enabled": True, "ceri_run_capture_enabled": True,
        "setup_lifecycle_enabled": True, "setup_lifecycle_pipeline_step_enabled": True,
        "setup_capture_handoff_enabled": handoff, "winner_probability_enabled": True,
        "winner_probability_capture_in_pipeline": True, "sec_document_incremental_mode": "OFF",
        "ceri_provider_batch_size": 25, "ceri_normalization_batch_size": 50,
        "ceri_feature_batch_size": 50, "ceri_batch_checkpoint_interval": 5,
    }}
    result = {
        **copy.deepcopy(expected), "input_manifest_sha256": canonical_hash(inputs),
        "business_time": inputs["business_time"], "flags": flags,
        "source_availability": inputs.get("source_availability"),
        "configuration_envelope_sha256": canonical_hash({"configuration": config, "flags": flags}),
    }
    from single_run_certification.golden_completion import load_completion

    result["completion"] = load_completion(result["input_manifest_sha256"])
    result["unresolved"] = result["completion"]["unresolved"]
    return result


def require_complete(manifest: dict) -> None:
    if manifest["unresolved"]:
        raise OracleIncomplete("INDEPENDENT_ORACLE_INCOMPLETE: " + ", ".join(
            obligation["domain"] for obligation in manifest["unresolved"]
        ))
    from single_run_certification.golden_completion import validate_completion

    validate_completion(manifest)


def decimal_equal(expected, actual, *, tolerance="0") -> None:
    if expected is None or actual is None:
        if expected is not actual:
            raise OracleMismatch("NULL_MISMATCH")
        return
    if isinstance(actual, bool):
        raise OracleMismatch("BOOLEAN_IS_NOT_A_FINANCIAL_NUMBER")
    try:
        left, right = Decimal(str(expected)), Decimal(str(actual))
        if not left.is_finite() or not right.is_finite() or abs(left-right) > Decimal(tolerance):
            raise OracleMismatch(f"NUMERIC_MISMATCH: expected={expected} actual={actual}")
    except InvalidOperation as exc:
        raise OracleMismatch("INVALID_FINANCIAL_NUMBER") from exc


def compare_records(expected: list[dict], actual: list[dict], *, keys: tuple[str, ...],
                    tolerance="0") -> None:
    def identity(row):
        return tuple(row[key] for key in keys)

    wanted, found = Counter(map(identity, expected)), Counter(map(identity, actual))
    if wanted != found or any(count != 1 for count in found.values()):
        raise OracleMismatch(f"IDENTITY_MISMATCH missing={wanted-found} extra={found-wanted}")
    actual_by_key = {identity(row): row for row in actual}
    for expected_row in expected:
        actual_row = actual_by_key[identity(expected_row)]
        for field, value in expected_row.items():
            if field not in actual_row:
                raise OracleMismatch(f"MISSING_FIELD: {field}")
            observed = actual_row[field]
            if isinstance(value, str) and value.replace(".", "", 1).replace("-", "", 1).isdigit():
                decimal_equal(value, observed, tolerance=tolerance)
            elif value != observed:
                raise OracleMismatch(f"FIELD_MISMATCH: {identity(expected_row)} {field}")


def check_receipt(record: dict, *, cutoff: str) -> None:
    known = record.get("known_at")
    if known is None or datetime.fromisoformat(known.replace("Z", "+00:00")) > (
        datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    ):
        raise OracleMismatch("FUTURE_OR_UNKNOWN_RECEIPT")


def check_ownership(actual: dict, expected: dict) -> None:
    for name in ("input_manifest_sha256", "configuration_envelope_sha256", "source",
                 "session", "market_cutoff", "ceri_cutoff"):
        if actual.get(name) != expected.get(name):
            raise OracleMismatch(f"SOURCE_CONTEXT_CONFIGURATION_MISMATCH: {name}")


def eligible_winner_children(ticker: str, *, eligible: bool, exclusion_reason=None) -> dict:
    """Expand only a separately established eligibility decision; never select it."""
    if not eligible:
        if not exclusion_reason:
            raise OracleMismatch("EXCLUSION_REQUIRES_REASON")
        return {"acquisition": {"ticker": ticker, "status": "EXCLUDED",
                                "reason": exclusion_reason}, "forward": [],
                "target_stop": [], "estimate": []}
    obligations = artifact("expected-results.json")["conditional_obligations"]
    policy = obligations["winner_eligible_children"]
    return {
        "acquisition": {"ticker": ticker, "status": "ELIGIBLE"},
        "forward": [{"ticker": ticker, "entry_model": model, "horizon": n}
                    for model in policy["entry_models"] for n in policy["horizons"]],
        "target_stop": [{"ticker": ticker, "definition": name}
                        for name in policy["target_stop_definitions"]],
        "estimate": [{"ticker": ticker, "kind": policy["estimate_kind"],
                      "definition": policy["target_stop_definitions"][0]}],
    }


def audit_fundamental_worksheet() -> dict[str, str]:
    worksheet = artifact("fundamental-derivation.json")
    quantum = Decimal(".0001")

    def rounded(value):
        return max(Decimal(0), min(Decimal(10), value)).quantize(
            quantum, rounding=ROUND_HALF_EVEN,
        )

    results = {}
    for name, component in worksheet["components"].items():
        numerator = sum(Decimal(t["normalized"])*Decimal(t["weight"])
                        for t in component["terms"])
        denominator = sum(Decimal(t["weight"]) for t in component["terms"])
        score = rounded(rounded(numerator/denominator)+Decimal(component["bonus"]))
        decimal_equal(component["expected"], score)
        results[name] = str(score)
    aggregate = rounded(sum(Decimal(results[k])*Decimal(str(w))
                            for k, w in worksheet["weights"].items()))
    decimal_equal(worksheet["expected"], aggregate)
    results["fundamental_score"] = str(aggregate)
    return results
