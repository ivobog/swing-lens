from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.ceri.config import CeriConfig, load_ceri_config

RESTRICTED_VALUE_TEMPLATE = "<restricted:{field}>"

_SENSITIVE_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "credential",
    "password",
    "provider_secret",
    "raw_payload",
    "secret",
    "source_url",
    "token",
)
_LOCAL_PATH_PATTERN = re.compile(
    r"(?i)([a-z]:\\(?:users|documents|downloads|appdata|temp|tmp)\\[^\s,;]+|"
    r"/(?:users|home|tmp|var/folders)/[^\s,;]+)"
)
_SQL_DETAIL_PATTERN = re.compile(
    r"(?is)\b(select|insert|update|delete|merge|with|alter|drop|create)\b.+\b(from|into|table|where|values)\b"
)
_BEARER_PATTERN = re.compile(r"(?i)bearer\s+[a-z0-9._\-]+")


@dataclass(frozen=True)
class CeriFieldPolicy:
    field: str
    exportable: bool
    reason: str


class CeriExportPolicyRegistry:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()
        self._configured = {
            field: policy.lower()
            for field, policy in self.config.exports.default_view_fields.items()
        }

    def policy_for(self, field: str) -> CeriFieldPolicy:
        normalized = _normalize_field(field)
        configured = self._configured.get(normalized)
        if configured == "exportable":
            return CeriFieldPolicy(field=field, exportable=True, reason="configured_exportable")
        if configured == "restricted":
            return CeriFieldPolicy(field=field, exportable=False, reason="configured_restricted")
        if _is_sensitive_field(normalized):
            return CeriFieldPolicy(field=field, exportable=False, reason="sensitive_field")
        return CeriFieldPolicy(field=field, exportable=True, reason="default_exportable")

    def is_exportable(self, field: str) -> bool:
        return self.policy_for(field).exportable

    def mask(self, field: str) -> str:
        return RESTRICTED_VALUE_TEMPLATE.format(field=field)

    def export_value(self, field: str, value: Any) -> Any:
        if not self.is_exportable(field):
            return self.mask(field)
        return redact_sensitive(value)

    def export_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {field: self.export_value(field, value) for field, value in row.items()}

    def permitted_payload(self, payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if payload is None:
            return None
        permitted: dict[str, Any] = {}
        for field, value in payload.items():
            if self.is_exportable(str(field)):
                permitted[str(field)] = redact_sensitive(value)
        return permitted


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _is_sensitive_field(_normalize_field(key_text)):
                redacted[key_text] = (
                    None
                    if nested in (None, "")
                    else RESTRICTED_VALUE_TEMPLATE.format(field=key_text)
                )
            else:
                redacted[key_text] = redact_sensitive(nested)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _normalize_field(field: str) -> str:
    return field.strip().lower()


def _is_sensitive_field(field: str) -> bool:
    return any(fragment in field for fragment in _SENSITIVE_FIELD_FRAGMENTS)


def _redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub("Bearer <restricted:token>", value)
    redacted = _LOCAL_PATH_PATTERN.sub("<restricted:path>", redacted)
    if _SQL_DETAIL_PATTERN.search(redacted):
        return "<restricted:sql>"
    return redacted
