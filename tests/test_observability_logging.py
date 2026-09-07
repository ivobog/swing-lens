from __future__ import annotations

import json
import logging

from app.observability.correlation import root_action_scope
from app.observability.logging import JsonLogFormatter


def test_json_logs_include_correlation_and_redact_secrets() -> None:
    formatter = JsonLogFormatter("worker")
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "provider.failed", (), None)
    record.authorization = "Bearer secret-token"
    record.local_path = r"C:\Users\Ivica\Downloads\secret.csv"
    with root_action_scope("SCHEDULER", "winner") as context:
        payload = json.loads(formatter.format(record))

    assert payload["event"] == "provider.failed"
    assert payload["process_role"] == "worker"
    assert payload["root_correlation_id"] == context.root_correlation_id
    assert payload["authorization"] == "<restricted:authorization>"
    assert "secret-token" not in json.dumps(payload)
    assert "Ivica" not in json.dumps(payload)
