from __future__ import annotations

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.services.redaction import redact_sensitive, redact_text

_TEXT_ERROR_FIELDS = frozenset(
    {
        "error",
        "error_message",
        "last_error_message",
        "last_error_detail",
        "failure_reason",
        "unavailable_reason",
        "quarantine_reason",
        "rejection_reason",
        "invalidated_reason",
    }
)
_STRUCTURED_ERROR_MARKERS = (
    "error",
    "result_json",
    "evidence_json",
    "diagnostic",
    "metadata_json",
    "reasons_json",
    "warning_flags_json",
    "ambiguity_json",
    "shadow",
)


def redact_persistence_value(field_name: str, value: Any) -> Any:
    """Redact at the final ORM persistence boundary, regardless of caller hygiene."""
    normalized = field_name.lower()
    if isinstance(value, str) and (
        normalized in _TEXT_ERROR_FIELDS or normalized.endswith(("_error", "_error_message"))
    ):
        return redact_text(value)
    if isinstance(value, (dict, list, tuple)) and any(
        marker in normalized for marker in _STRUCTURED_ERROR_MARKERS
    ):
        return redact_sensitive(value)
    return value


def redact_session_persistence_boundaries(session: Session) -> None:
    for instance in session.new.union(session.dirty):
        state = inspect(instance)
        for attribute in state.mapper.column_attrs:
            name = attribute.key
            current = getattr(instance, name, None)
            redacted = redact_persistence_value(name, current)
            if redacted != current:
                setattr(instance, name, redacted)


def install_persistence_redaction() -> None:
    if event.contains(Session, "before_flush", _before_flush):
        return
    event.listen(Session, "before_flush", _before_flush)


def _before_flush(session: Session, _flush_context: Any, _instances: Any) -> None:
    redact_session_persistence_boundaries(session)
