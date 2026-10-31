"""Sealed A05B literal expectations for the unchanged v3 business inputs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

COMPLETION = Path(__file__).with_name("golden") / "v3" / "oracle-completion"
COMPLETION_SHA256 = "bebee63caf04d69a27c3a92dba67b0620c96ac272759a846785ea5b31122689a"
CONTRACTS = {"technical", "combined", "ranking", "market_regime_sector", "ceri", "setup", "winner"}
KEYS = {
    "technical": ("ticker",),
    "technical_contributions": ("ticker",),
    "combined": ("ticker",),
    "ranking": ("profile", "ticker"),
    "market_regime": ("as_of_date",),
    "sector": ("sector",),
    "estimates": ("ticker", "record_key"),
    "guidance": ("ticker", "record_key"),
    "catalysts": ("ticker", "record_key"),
    "revisions": ("ticker", "metric", "period_slot", "window_days"),
    "derived": ("ticker", "feature_family", "feature_key"),
    "price_response": ("ticker",),
    "captures": ("ticker",),
    "earnings": ("ticker",),
    "changes": ("ticker",),
    "alerts": ("ticker",),
    "setup": ("ticker", "timeframe", "data_as_of_date"),
    "setup_episodes": ("ticker", "setup_family"),
    "setup_events": ("ticker", "event_type"),
    "setup_changes": ("ticker",),
    "setup_alerts": ("ticker",),
    "winner_acquisition": ("ticker",),
    "winner": ("ticker", "prediction_as_of_date"),
    "winner_forwards": ("ticker", "entry_model", "horizon_sessions"),
    "winner_targets": ("ticker", "definition"),
    "winner_estimates": ("ticker", "definition", "estimate_kind"),
}


def relationship_key(section: str, row: dict) -> str:
    return json.dumps(
        [row[key] for key in KEYS[section]],
        separators=(",", ":"),
        default=lambda value: value.isoformat(),
    )


def relationship_inventory(manifest: dict) -> list[str]:
    """Expand reviewed semantic members before observing any database rows."""
    domains = manifest["completion"]["domains"]
    names = [section + ":sealed_evidence" for section in ("combined", "ranking", "fundamental")]
    names += [
        "sector_context",
        "regime_context",
        "regime_bounded_inputs",
        "ceri_capture_context",
        "ceri_native_workflow_owner",
        "setup_canonical_selected",
        "setup_evaluation_roles",
        "setup_live_publication",
        "setup_episode_references",
        "setup_handoff_path",
        "winner_acquisition_references",
        "winner_target_references",
        "winner_estimate_configuration",
    ]
    for section, kind, sources in (
        ("technical", "TECHNICAL", False),
        ("combined", "COMBINED", True),
        ("ranking", "RANKING", True),
        ("market_regime", "REGIME", False),
        ("sector", "SECTOR", True),
        ("captures", "CERI", False),
        ("setup", "SETUP", True),
    ):
        for row in domains[section]:
            key = relationship_key(section, row)
            checks = ["configuration", "scope"]
            if "ticker" in row:
                checks.append("subject")
            if sources:
                checks.append("sources")
            names += [f"immutable_{check}:{kind}:{key}" for check in checks]
    for section, prefixes in (
        ("combined", ("combined_sources",)),
        ("technical", ("technical_context",)),
        ("derived", ("ceri_pipeline_owned",)),
        ("revisions", ("ceri_pipeline_owned",)),
        ("price_response", ("ceri_pipeline_owned",)),
        ("captures", ("ceri_capture_issuer",)),
        ("estimates", ("ceri_source_issuer",)),
        ("setup", ("setup_source",)),
        (
            "winner",
            (
                "winner_sources",
                "winner_native_foreign_keys",
                "winner_handoff",
                "winner_feature_scope",
            ),
        ),
    ):
        for row in domains[section]:
            names += [f"{prefix}:{section}:{relationship_key(section, row)}" for prefix in prefixes]
    assert len(names) == len(set(names)), "duplicate declared semantic relationship"
    return sorted(names)


def load_completion(input_identity: str, directory: Path = COMPLETION) -> dict:
    from single_run_certification.golden_oracle import OracleMismatch

    encoded = (directory / "manifest.json").read_bytes()
    if hashlib.sha256(encoded).hexdigest() != COMPLETION_SHA256:
        raise OracleMismatch("COMPLETION_MANIFEST_HASH_MISMATCH")
    seal = json.loads(encoded)
    if seal["base_input_identity"] != input_identity:
        raise OracleMismatch("COMPLETION_INPUT_IDENTITY_MISMATCH")
    for name, expected in seal["artifacts"].items():
        if (
            Path(name).name != name
            or hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected
        ):
            raise OracleMismatch("COMPLETION_ARTIFACT_HASH_MISMATCH: " + name)
    domains = {}
    variants = {}
    for name in (
        "core-expected.json",
        "context-expected.json",
        "ceri-expected.json",
        "downstream-expected.json",
    ):
        values = json.loads((directory / name).read_text(encoding="utf-8"))
        domains.update({key: value for key, value in values.items() if key in KEYS})
        variants.update(values.get("handoff_variants", {}))
    return {**seal, "manifest_sha256": COMPLETION_SHA256, "domains": domains, "variants": variants}


def validate_completion(manifest: dict) -> None:
    from single_run_certification.golden_oracle import OracleIncomplete

    completion = manifest.get("completion") or {}
    contracts = completion.get("contracts") or {}
    missing = set(CONTRACTS) - set(contracts)
    reviewed = load_completion(manifest["input_manifest_sha256"])
    if completion.get("domains") != reviewed["domains"]:
        missing.add("reviewed_literals")
    if completion.get("unresolved") or completion.get("variants") != reviewed["variants"]:
        missing.add("unresolved_or_variants")
    for name, contract in contracts.items():
        if contract.get("sections") != reviewed["contracts"].get(name, {}).get("sections"):
            missing.add(name + ":sections")
        if contract.get("status") != "INDEPENDENTLY_DERIVED" or not contract.get("sections"):
            missing.add(name)
        for section in contract.get("sections", ()):
            rows = completion.get("domains", {}).get(section)
            if not isinstance(rows, list):
                missing.add(section)
                continue
            fields = reviewed["required_fields"].get(section)
            if len(rows) != len(reviewed["domains"][section]):
                missing.add(section + ":population")
            if fields and (not rows or any(not set(fields) <= row.keys() for row in rows)):
                missing.add(section + ":required_fields")
    if completion.get("base_input_identity") != manifest.get("input_manifest_sha256"):
        missing.add("input_identity")
    if missing:
        raise OracleIncomplete("INDEPENDENT_ORACLE_INCOMPLETE: " + ", ".join(sorted(missing)))


def compare_vector(
    expected: list[dict], actual: list[dict], *, keys: tuple[str, ...], tolerance="0.00000001"
) -> None:
    from single_run_certification.golden_oracle import OracleMismatch, decimal_equal

    def normalized(value):
        if isinstance(value, str) and len(value) > 10 and value[10] in {"T", " "}:
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                if timestamp.tzinfo is not None:
                    return timestamp.astimezone(UTC).isoformat()
        if isinstance(value, datetime):
            return value.astimezone(UTC).isoformat()
        return value.isoformat() if isinstance(value, date) else value

    def identity(row):
        return tuple(normalized(row[key]) for key in keys)

    wanted = Counter(identity(row) for row in expected)
    found = Counter(identity(row) for row in actual)
    if wanted != found or any(n != 1 for n in found.values()):
        raise OracleMismatch(f"IDENTITY_MISMATCH missing={wanted - found} extra={found - wanted}")
    by_key = {identity(row): row for row in actual}

    def compare(value, observed, location):
        if isinstance(value, dict):
            if (
                not isinstance(observed, dict)
                or not value.keys() <= observed.keys()
                or (not value and observed)
            ):
                raise OracleMismatch("MISSING_FIELDS: " + location)
            for name in value:
                compare(value[name], observed[name], location + "." + name)
        elif isinstance(value, list):
            if not isinstance(observed, (list, tuple)) or len(value) != len(observed):
                raise OracleMismatch("LIST_MISMATCH: " + location)
            for index, item in enumerate(value):
                compare(item, observed[index], location + f"[{index}]")
        elif value is None or isinstance(value, bool):
            if value is not observed:
                raise OracleMismatch("NULL_OR_STATE_MISMATCH: " + location)
        else:
            value = normalized(value)
            observed = normalized(observed)
            try:
                number = Decimal(str(value))
            except InvalidOperation:
                if value != observed:
                    raise OracleMismatch(
                        f"FIELD_MISMATCH: {location} expected={value} actual={observed}"
                    ) from None
            else:
                try:
                    decimal_equal(number, observed, tolerance=tolerance)
                except OracleMismatch as exc:
                    raise OracleMismatch(f"{location}: {exc}") from exc

    for row in expected:
        key = identity(row)
        compare(row, by_key[key], str(key))


def references_match(expected: dict, actual: dict) -> bool:
    """Resolve opaque IDs by their business roles, preserving issuer associations."""
    return all(actual.get(role) == reference for role, reference in expected.items())
