from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.redaction import (
    RESTRICTED_VALUE_TEMPLATE,
    is_sensitive_field,
    normalize_field,
    redact_sensitive,
)


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
        normalized = normalize_field(field)
        configured = self._configured.get(normalized)
        if configured == "exportable":
            return CeriFieldPolicy(field=field, exportable=True, reason="configured_exportable")
        if configured == "restricted":
            return CeriFieldPolicy(field=field, exportable=False, reason="configured_restricted")
        if is_sensitive_field(normalized):
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

