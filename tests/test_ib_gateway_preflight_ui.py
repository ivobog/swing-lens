from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _preflight_view(payload: dict) -> dict:
    script = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("app/static/app.js", "utf8");
const context = { document: { addEventListener() {} } };
vm.runInNewContext(source, context);
process.stdout.write(JSON.stringify(context.ibGatewayPreflightView(JSON.parse(process.argv[1]))));
"""
    completed = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_authoritative_backend_success_maps_to_connected_ui() -> None:
    assert _preflight_view(
        {"status": "IB_API_READY", "api_connected": True, "api_ready": True}
    ) == {
        "ready": True,
        "indicator": "IB Connected",
        "title": "IB Gateway connected — API ready",
    }


def test_distinct_backend_failures_do_not_collapse_to_offline() -> None:
    assert _preflight_view({"status": "IB_PROCESS_RUNNING_API_NOT_READY"})["title"] == (
        "IB Gateway is open — API not ready"
    )
    assert _preflight_view({"status": "IB_PROCESS_NOT_RUNNING"})["title"] == (
        "IB Gateway is not running"
    )
    assert _preflight_view({"status": "IB_SESSION_LOST"})["title"] == (
        "IB Gateway API session was lost"
    )
    assert _preflight_view({"status": "CONFIG_ERROR"})["title"] == (
        "SwingLens could not verify IB API status"
    )


def test_successful_preflight_requires_operator_confirmation_before_submit() -> None:
    source = Path("app/static/app.js").read_text(encoding="utf-8")
    submit_handler = source.split('form.addEventListener("submit"', 1)[1].split(
        'retryButton?.addEventListener', 1
    )[0]

    assert 'showReady(status);' in submit_handler
    assert 'submitWithPolicy("REQUIRE_IB");' not in submit_handler
