from __future__ import annotations

import json
from typing import Any


class SetupLifecycleReconciliationError(RuntimeError):
    """Bounded, structured lifecycle authority/preflight failure."""

    def __init__(self, code: str, **details: Any) -> None:
        self.code = str(code)
        self.details = _bounded(details)
        self.diagnostics = self.details
        super().__init__(
            f"{self.code}:"
            + json.dumps(
                self.details,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, **self.details}


def _bounded(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "<bounded>"
    if isinstance(value, dict):
        return {
            str(key)[:96]: _bounded(item, depth=depth + 1) for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_bounded(item, depth=depth + 1) for item in list(value)[:32]]
    if isinstance(value, str):
        return value[:512]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:512]
