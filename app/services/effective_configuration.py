"""Phase-4 frozen effective values; resolution remains owned by each family."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timezone
from decimal import Decimal
from enum import Enum, StrEnum
from functools import cached_property
from typing import Any
from zoneinfo import ZoneInfo

from app.services.calculation_identity import (
    CalculationIdentity,
    ConfigurationCoverage,
    ConfigurationIdentity,
    DigestIdentity,
    EffectiveConfigurationIdentity,
    IdentityDimension,
    IdentityState,
    VersionIdentity,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical

CONFIGURATION_PAYLOAD_KEY = "effective_configuration_at_creation"
CONFIGURATION_SCHEMA_VERSION = "effective-configuration-v1"
PROOF_BOUNDARY = "canonical resolved behavioral configuration payload equality only"


class ConfigurationClassification(StrEnum):
    BEHAVIORAL = "BEHAVIORAL"
    OPERATIONAL = "OPERATIONAL"
    SECURITY_SECRET = "SECURITY_SECRET"
    OBSERVABILITY = "OBSERVABILITY"
    ENVIRONMENTAL_DEPENDENCY = "ENVIRONMENTAL_DEPENDENCY"
    UNKNOWN_CLASSIFICATION = "UNKNOWN_CLASSIFICATION"


class ConfigurationSourceKind(StrEnum):
    CODE_DEFAULT = "CODE_DEFAULT"
    SETTINGS_MODEL = "SETTINGS_MODEL"
    ENVIRONMENT = "ENVIRONMENT"
    DOTENV = "DOTENV"
    DATABASE = "DATABASE"
    PROFILE = "PROFILE"
    REQUEST = "REQUEST"
    PIPELINE_CONTEXT = "PIPELINE_CONTEXT"
    POLICY_VERSION = "POLICY_VERSION"
    UNKNOWN = "UNKNOWN"


class ConfigurationValueType(StrEnum):
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    REAL = "REAL"
    STRING = "STRING"
    STRUCTURE = "STRUCTURE"


def _identifier(value: str) -> str:
    # Metadata is a declared, non-secret identifier, never a URL, DSN or arbitrary repr.
    if (
        not isinstance(value, str)
        or not value.strip()
        or not re.fullmatch(r"[\w . /-]{1,240}", value, re.ASCII)
    ):
        raise ValueError("configuration metadata requires a safe non-secret identifier")
    return value


@dataclass(frozen=True)
class ConfigurationSource:
    kind: ConfigurationSourceKind = ConfigurationSourceKind.UNKNOWN
    identifier: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConfigurationSourceKind):
            raise TypeError("configuration source kind must be typed")
        if self.kind is ConfigurationSourceKind.UNKNOWN and (
            self.identifier is not None or self.version is not None
        ):
            raise ValueError("unknown source cannot invent provenance")
        for value in (self.identifier, self.version):
            if value is not None:
                _identifier(value)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "identifier": self.identifier, "version": self.version}


UNKNOWN_SOURCE = ConfigurationSource()


@dataclass(frozen=True)
class ConfigurationResolution:
    resolver: str
    contract_version: str
    precedence: tuple[ConfigurationSourceKind, ...] = ()  # highest priority first

    def __post_init__(self) -> None:
        _identifier(self.resolver)
        _identifier(self.contract_version)
        if not isinstance(self.precedence, tuple) or any(
            not isinstance(item, ConfigurationSourceKind) for item in self.precedence
        ):
            raise TypeError("precedence must be an immutable tuple of source kinds")
        if len(set(self.precedence)) != len(self.precedence):
            raise ValueError("precedence cannot repeat source kinds")

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolver": self.resolver,
            "contract_version": self.contract_version,
            "precedence": [item.value for item in self.precedence],
        }


def _real(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError("REAL configuration requires a finite number")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("configuration numbers must be finite")
    if number == 0:
        return "0"
    # No Decimal.normalize(): ambient decimal precision must not round configuration.
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _value(value: Any) -> Any:
    """Tagged values prevent dates, strings, numbers and collection shapes colliding."""
    if isinstance(value, Enum):
        return _value(value.value)
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, (float, Decimal)):
        return {"type": "real", "value": _real(value)}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("configuration mappings require string keys")
        if any(_secret_key(key) for key in value):
            raise ValueError("secret-bearing keys require separate SECURITY_SECRET entries")
        return {"type": "mapping", "value": {key: _value(item) for key, item in value.items()}}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_value(item) for item in value]
        unordered = isinstance(value, (set, frozenset))
        if unordered:
            items.sort(key=Canonical.dumps)
        return {"type": "set" if unordered else "list", "value": items}
    # Explicit temporal types supported by the Phase-2 serializer; no object repr fallback.
    if isinstance(value, timezone) and value.utcoffset(None).microseconds:
        raise ValueError("standalone configuration timezone offsets require whole seconds")
    for temporal_type, tag in (
        (datetime, "datetime"),
        (date, "date"),
        (time, "time"),
        (timezone, "timezone"),
        (ZoneInfo, "zoneinfo"),
    ):
        if isinstance(value, temporal_type):
            return {"type": tag, "value": Canonical.canonicalize(value)}
    raise TypeError("unsupported configuration value type")


def _secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    # DSN is an identifier component/prefix/suffix, not an arbitrary substring:
    # "thresholds.net_debt..." otherwise falsely matches across the separator.
    return (
        normalized.startswith("dsn")
        or normalized.endswith("dsn")
        or any("dsn" in part for part in re.findall(r"[a-z0-9]+", key.lower()))
    ) or any(
        word in normalized
        for word in (
            "password",
            "passwd",
            "apikey",
            "accesskey",
            "credential",
            "privatekey",
            "secret",
            "token",
            "databaseurl",
            "connectionstring",
        )
    )


@dataclass(frozen=True, init=False)
class ConfigurationEntry:
    key: str
    classification: ConfigurationClassification
    value_type: ConfigurationValueType
    canonical_value_json: str
    source: ConfigurationSource
    defaulted: bool
    overridden: bool

    def __init__(
        self,
        key: str,
        value: Any,
        classification: ConfigurationClassification,
        value_type: ConfigurationValueType = ConfigurationValueType.STRUCTURE,
        source: ConfigurationSource = UNKNOWN_SOURCE,
        *,
        defaulted: bool = False,
        overridden: bool = False,
    ) -> None:
        _identifier(key)
        if not isinstance(classification, ConfigurationClassification) or not isinstance(
            value_type, ConfigurationValueType
        ):
            raise TypeError("configuration classification and value type must be typed")
        if not isinstance(source, ConfigurationSource):
            raise TypeError("configuration source must be typed")
        secret = classification is ConfigurationClassification.SECURITY_SECRET
        if _secret_key(key) and not secret:
            raise ValueError("secret-bearing key must be classified SECURITY_SECRET")
        if not secret:
            if isinstance(value, Enum):
                value = value.value
            expected = {
                ConfigurationValueType.BOOLEAN: bool,
                ConfigurationValueType.INTEGER: int,
                ConfigurationValueType.STRING: str,
            }.get(value_type)
            if expected is not None and type(value) is not expected and value is not None:
                raise TypeError("configuration value does not match declared type")
        normalized = (
            None
            if secret
            else {"type": "real", "value": _real(value)}
            if value_type is ConfigurationValueType.REAL and value is not None
            else _value(value)
        )
        for name, item in {
            "key": key,
            "classification": classification,
            "value_type": value_type,
            "canonical_value_json": Canonical.dumps(normalized),
            "source": ConfigurationSource() if secret else source,
            "defaulted": False if secret else bool(defaulted),
            "overridden": False if secret else bool(overridden),
        }.items():
            object.__setattr__(self, name, item)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "classification": self.classification.value,
            "value_type": self.value_type.value,
            "value": json.loads(self.canonical_value_json),
            "source": self.source.as_dict(),
            "defaulted": self.defaulted,
            "overridden": self.overridden,
        }


@dataclass(frozen=True)
class ConfigurationFamily:
    namespace: str
    schema_version: str
    resolution: ConfigurationResolution
    keys: tuple[tuple[str, ConfigurationClassification], ...]
    compatibility_policy: str = "exact-semantic-v1"

    def __post_init__(self) -> None:
        for value in (self.namespace, self.schema_version, self.compatibility_policy):
            _identifier(value)
        if not isinstance(self.resolution, ConfigurationResolution):
            raise TypeError("family resolution must be typed")
        if not isinstance(self.keys, tuple) or any(
            not isinstance(item, tuple) for item in self.keys
        ):
            raise TypeError("family keys must be immutable tuples")
        if len(dict(self.keys)) != len(self.keys):
            raise ValueError("duplicate configuration family keys")
        for key, classification in self.keys:
            _identifier(key)
            if not isinstance(classification, ConfigurationClassification):
                raise TypeError("family classifications must be typed")


@dataclass(frozen=True)
class EffectiveConfigurationSnapshot:
    family: ConfigurationFamily
    entries: tuple[ConfigurationEntry, ...]
    producer_version: str | None = None
    evaluated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.family, ConfigurationFamily) or not isinstance(self.entries, tuple):
            raise TypeError("snapshot requires a typed family and immutable entries")
        if any(not isinstance(entry, ConfigurationEntry) for entry in self.entries):
            raise TypeError("snapshot entries must be typed")
        actual = {entry.key: entry.classification for entry in self.entries}
        if len(actual) != len(self.entries) or actual != dict(self.family.keys):
            raise ValueError("snapshot must cover exactly the declared classified family keys")
        if self.producer_version is not None:
            _identifier(self.producer_version)
        if self.evaluated_at is not None:
            if not isinstance(self.evaluated_at, datetime):
                raise TypeError("configuration evaluation context must be an aware datetime")
            Canonical.canonicalize(self.evaluated_at)
            object.__setattr__(self, "evaluated_at", self.evaluated_at.astimezone(UTC))

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "contract_schema": CONFIGURATION_SCHEMA_VERSION,
            "namespace": self.family.namespace,
            "schema_version": self.family.schema_version,
            "values": {
                entry.key: {
                    "value_type": entry.value_type.value,
                    "value": json.loads(entry.canonical_value_json),
                }
                for entry in sorted(self.entries, key=lambda entry: entry.key)
                if entry.classification is ConfigurationClassification.BEHAVIORAL
            },
        }

    @cached_property
    def semantic_hash(self) -> str:
        return Canonical.fingerprint(self.semantic_payload())

    def resolution_payload(self) -> dict[str, Any]:
        return {
            "semantic": self.semantic_payload(),
            "resolution": self.family.resolution.as_dict(),
            "entries": [
                entry.as_dict()
                for entry in sorted(self.entries, key=lambda e: e.key)
                if entry.classification is not ConfigurationClassification.SECURITY_SECRET
            ],
        }

    @cached_property
    def resolution_hash(self) -> str:
        return Canonical.fingerprint(self.resolution_payload())

    @cached_property
    def identity(self) -> EffectiveConfigurationIdentity:
        coverage = (
            ConfigurationCoverage.PARTIAL_DEBUG
            if any(
                e.classification is ConfigurationClassification.UNKNOWN_CLASSIFICATION
                for e in self.entries
            )
            else ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION
        )
        return EffectiveConfigurationIdentity(
            self.family.namespace,
            DigestIdentity("sha256", self.semantic_hash, PROOF_BOUNDARY),
            # This is the shared effective-value schema, not a source-specific resolver ID.
            VersionIdentity(self.family.namespace, self.family.schema_version),
            coverage,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.resolution_payload(),
            "identity": self.identity.as_dict(),
            "semantic_hash": self.semantic_hash,
            "resolution_hash": self.resolution_hash,
            "producer_version": self.producer_version,
            "evaluated_at": Canonical.canonicalize(self.evaluated_at),
            "compatibility_policy": self.family.compatibility_policy,
        }


class ConfigurationCompatibilityStatus(StrEnum):
    EXACT = "EXACT"
    COMPATIBLE = "COMPATIBLE"  # reserved for a future explicitly reviewed consumer policy
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


@dataclass(frozen=True)
class ConfigurationCompatibilityResult:
    status: ConfigurationCompatibilityStatus
    reason: str


def compare_configuration(
    left: IdentityDimension[EffectiveConfigurationIdentity],
    right: IdentityDimension[EffectiveConfigurationIdentity],
) -> ConfigurationCompatibilityResult:
    if IdentityState.LEGACY_UNKNOWN in {left.state, right.state}:
        return ConfigurationCompatibilityResult(
            ConfigurationCompatibilityStatus.LEGACY_UNKNOWN, "historical configuration unavailable"
        )
    if left.state is not IdentityState.KNOWN or right.state is not IdentityState.KNOWN:
        return ConfigurationCompatibilityResult(
            ConfigurationCompatibilityStatus.UNKNOWN, "configuration unavailable"
        )
    for identity in (left.value, right.value):
        if not isinstance(identity, EffectiveConfigurationIdentity) or (
            identity.coverage is not ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION
            or identity.fingerprint.algorithm != "sha256"
            or not re.fullmatch(r"[0-9a-f]{64}", identity.fingerprint.digest)
        ):
            return ConfigurationCompatibilityResult(
                ConfigurationCompatibilityStatus.UNKNOWN, "complete configuration proof unavailable"
            )
    exact = (
        left.value.namespace == right.value.namespace
        and left.value.resolution_contract == right.value.resolution_contract
        and left.value.fingerprint.digest == right.value.fingerprint.digest
    )
    return ConfigurationCompatibilityResult(
        ConfigurationCompatibilityStatus.EXACT
        if exact
        else ConfigurationCompatibilityStatus.INCOMPATIBLE,
        "equal canonical behavioral payload"
        if exact
        else "different behavioral identity or schema",
    )


@dataclass(frozen=True)
class ConfigurationDrift:
    historical: IdentityDimension[EffectiveConfigurationIdentity]
    current: IdentityDimension[EffectiveConfigurationIdentity]

    @property
    def compatibility(self) -> ConfigurationCompatibilityResult:
        return compare_configuration(self.historical, self.current)

    @property
    def detected(self) -> bool | None:
        status = self.compatibility.status
        if status is ConfigurationCompatibilityStatus.EXACT:
            return False
        if status is ConfigurationCompatibilityStatus.INCOMPATIBLE:
            return True
        return None


def bind_configuration(
    identity: CalculationIdentity,
    snapshot: EffectiveConfigurationSnapshot,
) -> CalculationIdentity:
    if not isinstance(identity, CalculationIdentity) or not isinstance(
        snapshot, EffectiveConfigurationSnapshot
    ):
        raise TypeError("configuration binding requires typed identity and snapshot")
    if snapshot.identity.coverage is not ConfigurationCoverage.COMPLETE_EFFECTIVE_CONFIGURATION:
        raise ValueError("unknown classifications cannot bind complete calculation configuration")
    return replace(
        identity, configuration=ConfigurationIdentity(IdentityDimension.known(snapshot.identity))
    )


def configuration_from_evidence(evidence: Any) -> IdentityDimension[EffectiveConfigurationIdentity]:
    """Verify the frozen payload and binding; never read today's resolver or environment."""
    payload = evidence.payload_json.get(CONFIGURATION_PAYLOAD_KEY)
    if payload is None:
        return IdentityDimension.legacy_unknown()
    semantic_hash = Canonical.fingerprint(payload["semantic"])
    resolution_hash = Canonical.fingerprint(
        {key: payload[key] for key in ("semantic", "resolution", "entries")}
    )
    identity = CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json)
    dimension = identity.configuration.effective_configuration
    if (
        semantic_hash != payload["semantic_hash"]
        or resolution_hash != payload["resolution_hash"]
        or dimension.state is not IdentityState.KNOWN
        or dimension.value.as_dict() != payload["identity"]
        or dimension.value.fingerprint.digest != semantic_hash
        or Canonical.fingerprint(evidence.payload_json) != evidence.payload_fingerprint
        or str(identity.fingerprint()) != evidence.calculation_identity_fingerprint
    ):
        raise ValueError("frozen effective configuration integrity or identity binding mismatch")
    return dimension
