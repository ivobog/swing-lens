"""Opt-in test lane accounting; does not alter collection, execution or outcomes."""

import json
from pathlib import Path

_outcomes = {}
_deselected = []
_warnings = []
_collection_errors = []


def pytest_addoption(parser):
    parser.addoption("--t12e-lane-report", default=None)


def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome in {"failed", "skipped"}:
        _outcomes[report.nodeid] = report.outcome


def pytest_deselected(items):
    _deselected.extend(item.nodeid for item in items)


def pytest_warning_recorded(warning_message, when, nodeid, location):
    _warnings.append(
        {"category": warning_message.category.__name__, "message": str(warning_message.message)}
    )


def pytest_collectreport(report):
    if report.failed:
        _collection_errors.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    output = session.config.getoption("--t12e-lane-report")
    if output:
        Path(output).write_text(
            json.dumps(
                {
                    "exit_status": int(exitstatus),
                    "arguments": list(session.config.invocation_params.args),
                    "outcomes": _outcomes,
                    "deselected": _deselected,
                    "warnings": _warnings,
                    "collection_errors": _collection_errors,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
