from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

RESTRICTED_VALUE_TEMPLATE = "<restricted:{field}>"

_SENSITIVE_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "confirmation_token",
    "credential",
    "execution_token",
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


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if is_sensitive_field(normalize_field(key_text)):
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
        return redact_text(value)
    return value


def token_fingerprint(token: str | None) -> dict[str, str | None]:
    if not token:
        return {"hash": None, "suffix": None}
    return {
        "hash": hashlib.sha256(token.encode("utf-8")).hexdigest()[:16],
        "suffix": token[-6:],
    }


def redacted_token_metadata(
    token: str | None,
    *,
    field_name: str = "execution_token",
) -> dict[str, str | None]:
    fingerprint = token_fingerprint(token)
    return {
        f"{field_name}_hash": fingerprint["hash"],
        f"{field_name}_suffix": fingerprint["suffix"],
    }


def normalize_field(field: str) -> str:
    return field.strip().lower()


def is_sensitive_field(field: str) -> bool:
    return any(fragment in field for fragment in _SENSITIVE_FIELD_FRAGMENTS)


def redact_text(value: str) -> str:
    redacted = _BEARER_PATTERN.sub("Bearer <restricted:token>", value)
    redacted = _LOCAL_PATH_PATTERN.sub("<restricted:path>", redacted)
    if _SQL_DETAIL_PATTERN.search(redacted):
        return "<restricted:sql>"
    return redacted
