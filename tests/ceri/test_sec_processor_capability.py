from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.services.ceri.sec.processor_capability import (
    SEC_CAPABILITY_JOB_TYPES,
    SecProcessorCapabilityState,
    evaluate_sec_processor_capability,
)
from app.services.ceri.sec.processor_signature import (
    SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION,
    sec_guidance_processor_identity_inputs,
    sec_guidance_processor_signature,
)


@dataclass
class Release:
    processor_signature: str
    status: str


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class Db:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self, _query):
        return Rows(self.rows)


def test_signature_v1_is_stable_for_one_hundred_computations() -> None:
    signatures = {sec_guidance_processor_signature() for _ in range(100)}
    assert signatures == {"sec-guidance:eed017654682a0c9"}
    assert SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION == "sec-processor-signature-v1"
    assert sec_guidance_processor_identity_inputs() == (
        "sec-html-text-v1",
        "guidance-regex-visible-text-v3",
        "paragraph-locator-v1",
        "guidance-forms-v1",
    )


def test_signature_is_stable_across_process_role_env_timezone_and_cwd(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    expected = sec_guidance_processor_signature()
    roles = ("WEB", "DURABLE_WORKER", "CLI_OR_MAINTENANCE", "SUPERVISOR")
    timezones = ("UTC", "Europe/Zurich", "America/New_York")
    working_directories = (root, tmp_path)
    observed: list[str] = []
    script = (
        "import json; "
        "from app.services.ceri.sec.processor_signature import "
        "sec_guidance_processor_signature; "
        "print(json.dumps({'signature': sec_guidance_processor_signature()}, sort_keys=True))"
    )

    # Twenty genuinely fresh interpreters.  Insertion order of the unrelated
    # environment entries and the current working directory alternate on every
    # invocation; neither is part of the semantic identity contract.
    for index in range(20):
        env = os.environ.copy()
        variables = [
            ("PROCESS_ROLE", roles[index % len(roles)]),
            ("RUNTIME_MODE", "CERTIFICATION" if index % 2 else "NORMAL"),
            ("TZ", timezones[index % len(timezones)]),
            ("JOB_WORKER_ENABLED", "true" if index % 2 else "false"),
            ("CERI_SCHEDULER_ENABLED", "true" if index % 3 else "false"),
            ("IB_GATEWAY_HOST", f"unrelated-{index}.invalid"),
            ("IB_GATEWAY_PORT", str(4001 + index)),
            ("OBSERVABILITY_METRICS_ENABLED", "true" if index % 2 else "false"),
        ]
        if index % 2:
            variables.reverse()
        for key, value in variables:
            env[key] = value
        env["PYTHONPATH"] = str(root)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=working_directories[index % len(working_directories)],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        observed.append(json.loads(completed.stdout)["signature"])

    assert observed == [expected] * 20


def test_capability_state_matrix_is_deterministic() -> None:
    expected = sec_guidance_processor_signature()
    cases = (
        ([], SecProcessorCapabilityState.NOT_ACTIVE),
        ([Release("bad", "ACTIVE")], SecProcessorCapabilityState.SIGNATURE_MALFORMED),
        (
            [Release("sec-guidance:948beb114caa8da9", "ACTIVE")],
            SecProcessorCapabilityState.NOT_REGISTERED,
        ),
        (
            [
                Release("sec-guidance:948beb114caa8da9", "ACTIVE"),
                Release(expected, "DEPLOYED"),
            ],
            SecProcessorCapabilityState.SIGNATURE_MISMATCH,
        ),
        (
            [
                Release("sec-guidance:948beb114caa8da9", "ACTIVE"),
                Release(expected, "ACTIVE"),
            ],
            SecProcessorCapabilityState.MULTIPLE_ACTIVE,
        ),
        ([Release(expected, "ACTIVE")], SecProcessorCapabilityState.READY),
    )
    for rows, state in cases:
        capability = evaluate_sec_processor_capability(Db(rows))
        assert capability.state is state
        assert capability.ready is (state is SecProcessorCapabilityState.READY)


def test_sec_capability_boundary_is_conservative_but_not_global() -> None:
    assert {"FULL_PIPELINE", "SEC_READINESS_REPAIR"} <= SEC_CAPABILITY_JOB_TYPES
    assert all(
        name.startswith(("CERI_", "SEC_")) for name in SEC_CAPABILITY_JOB_TYPES - {"FULL_PIPELINE"}
    )
    assert "WORKER_RECOVERY_PROBE" not in SEC_CAPABILITY_JOB_TYPES
    assert "IB_FETCH" not in SEC_CAPABILITY_JOB_TYPES
    assert "WINNER_OUTCOME_MATURATION" not in SEC_CAPABILITY_JOB_TYPES
