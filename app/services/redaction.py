from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

RESTRICTED_VALUE_TEMPLATE = "<restricted:{field}>"

_SENSITIVE_FIELD_FRAGMENTS = (
    "access_key",
    "access_token",
    "api_key",
    "apikey",
    "api-key",
    "auth",
    "authorization",
    "bearer",
    "confirmation_token",
    "credential",
    "execution_token",
    "password",
    "passwd",
    "pwd",
    "provider_secret",
    "refresh_token",
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
_AUTH_SCHEME_PATTERN = re.compile(
    r"(?i)\b(?P<scheme>bearer|basic)\s+"
    r"(?P<credential>(?:\"(?:\\.|[^\"\\])*\")|(?:'(?:\\.|[^'\\])*')|[^\s,;]+)"
)
_URI_USERINFO_PATTERN = re.compile(r"(?i)(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?<![a-z0-9_-])(?:[\"']?)"
    r"(?:password|passwd|pwd|api[_-]?key|apikey|x-api-key|token|auth[_-]?token|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret|provider[_-]?secret|"
    r"[a-z0-9.-]+[_-](?:api[_-]?key|token|secret|credential|password)|"
    r"access[_-]?key|secret[_-]?key|authorization|signature)"
    r"(?:[\"']?)\s*(?:=|:)\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s&,;}\]]+)"
)


def _assignment_replacement(match: re.Match[str]) -> str:
    raw = match.group("value")
    quote = raw[0] if raw[:1] in {'"', "'"} and raw[-1:] == raw[:1] else ""
    return f"{match.group('prefix')}{quote}<restricted:secret>{quote}"


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
        # A token suffix is reversible evidence and must never enter telemetry.
        "suffix": None,
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
    redacted = _AUTH_SCHEME_PATTERN.sub(
        lambda match: f"{match.group('scheme')} <restricted:credentials>", value
    )
    redacted = _URI_USERINFO_PATTERN.sub(
        lambda match: f"{match.group('scheme')}<restricted:userinfo>@", redacted
    )
    redacted = _ASSIGNMENT_PATTERN.sub(_assignment_replacement, redacted)
    # Kept for compatibility with older, narrower bearer matching behavior.
    redacted = _BEARER_PATTERN.sub("Bearer <restricted:token>", redacted)
    redacted = _LOCAL_PATH_PATTERN.sub("<restricted:path>", redacted)
    if _SQL_DETAIL_PATTERN.search(redacted):
        return "<restricted:sql>"
    return redacted
