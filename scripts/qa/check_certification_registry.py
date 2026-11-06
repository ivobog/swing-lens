"""Check declarative certification membership; never execute a registered tool.

A04 remains the only authority for its pipeline verdict. This checker verifies
registration, lifecycle conservation and source/CI bindings, not pipeline results.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config/certification_registry.json"
LIFECYCLES = {
    "ACTIVE_RELEASE_CERTIFICATION",
    "ORDINARY_AUTOMATED_REGRESSION",
    "EXTENDED_INTEGRATION_PERFORMANCE",
    "MANUAL_DIAGNOSTIC",
    "MANUAL_OPERATIONAL_RECOVERY",
    "ARCHIVED_HISTORICAL_FORENSIC",
    "RETIRED_EXECUTABLE_CANDIDATE",
}
GATE_FIELDS = {
    "id",
    "entry_point",
    "command",
    "scope",
    "required",
    "lane",
    "evidence_format",
    "verdict_semantics",
    "exit_semantics",
    "dependencies",
    "prerequisites",
    "ownership",
    "safety",
    "automatic",
    "certificate_authority",
    "source_sha256",
    "ci_command",
}
SOURCE_DIGEST_POLICY = "sha256-crlf-to-lf-v1"


class RegistryError(ValueError):
    pass


def load_manifest(path: Path = MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def registered_gate(manifest: dict, gate_id: str) -> dict:
    found = [gate for gate in manifest["gates"] if gate["id"] == gate_id]
    if len(found) != 1:
        raise RegistryError("unknown or ambiguous gate: " + gate_id)
    return found[0]


def automatic_commands(manifest: dict) -> list[list[str]]:
    """Exact membership only. No script-name, directory or PASS discovery."""
    return [list(gate["command"]) for gate in manifest["gates"] if gate["automatic"]]


def source_sha256(path: Path) -> str:
    """Bind text source across Git's platform line endings; preserve byte seals separately."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _file(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.is_file():
        raise RegistryError("missing/unsafe registered path: " + relative)
    return path


def _check_a04(manifest: dict, root: Path) -> dict:
    # These modules are stdlib-only at import. No app, DB, provider, worker or
    # historical utility is imported; financial expectations stay in A05B files.
    sys.path.insert(0, str(root / "tests/e2e"))
    from single_run_certification.golden_oracle import expected_manifest, require_complete
    from single_run_certification.reporting import (
        EXACT_TEST_IDS,
        PIPELINE_SCOPE,
        pipeline_gate_inventory,
    )

    from scripts.qa.golden_provenance import ProvenanceError, verify_baseline

    try:
        provenance = verify_baseline(root, manifest.get("golden_update_policy", {}))
    except (ProvenanceError, OSError, ValueError, KeyError) as exc:
        raise RegistryError("golden provenance prerequisite: " + str(exc)) from exc

    gate = registered_gate(manifest, "EXACT_PIPELINE_CERTIFICATION")
    if gate["selectors"] != list(EXACT_TEST_IDS) or gate["scope"] != PIPELINE_SCOPE:
        raise RegistryError("A04 selector/scope drift")
    if gate["entry_point"] != "scripts/production_runtime_certification.py":
        raise RegistryError("A04 producer drift")
    if gate["command"] != [
        "python",
        "-m",
        "scripts.production_runtime_certification",
        "--provider-mode",
        "deterministic",
    ]:
        raise RegistryError("A04 invocation drift")
    if gate["certificate_authority"] != "A04_FINALIZER":
        raise RegistryError("A04 finalizer authority missing")
    reference = manifest["a04_authority"]
    inventories = []
    for mode in (False, True):
        expected = expected_manifest(mode)
        require_complete(expected)
        if (
            reference["input_sha256"] != expected["input_manifest_sha256"]
            or reference["oracle_sha256"] != expected["completion"]["manifest_sha256"]
        ):
            raise RegistryError("A04 input/oracle seal drift")
        inventory = pipeline_gate_inventory(expected)
        if len(inventory) != reference["required_gate_count"]:
            raise RegistryError("A04 required obligation inventory drift")
        inventories.append(inventory)
    if inventories[0] != inventories[1]:
        raise RegistryError("ambiguous A04 handoff obligation registration")
    source = ast.parse(_file(root, gate["entry_point"]).read_text(encoding="utf-8"))
    native = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_exact_deterministic"
    )
    selected = [
        node
        for node in ast.walk(native)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "selected" for target in node.targets)
    ]
    if len(selected) != 1 or ast.dump(selected[0].value) != ast.dump(
        ast.parse("list(EXACT_TEST_IDS)", mode="eval").body
    ):
        raise RegistryError("A04 runner no longer uses its exact authority selector")
    focused = [
        node
        for node in ast.walk(native)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_test_group"
        and len(node.args) == 4
        and isinstance(node.args[2], ast.Constant)
        and node.args[2].value == "exact-gate-regressions"
    ]
    if len(focused) != 1 or ast.literal_eval(focused[0].args[3]) != tuple(
        reference["regression_selectors"]
    ):
        raise RegistryError("A04 required regression selection drift")
    required = manifest["promotion_requirements"]
    if (
        len(required) != 3
        or {row["id"] for row in required}
        != {
            "EXACT_PIPELINE_CERTIFICATION",
            "FULL_RELEASE_REGRESSIONS",
            "SMALL_LIVE_PROVIDER_CANARY",
        }
        or not all(row["required"] for row in required)
    ):
        raise RegistryError("required promotion gate omitted")
    for requirement in required:
        if requirement["id"] == "FULL_RELEASE_REGRESSIONS":
            if (
                requirement.get("producer") != "FULL_RELEASE_REGRESSIONS"
                or requirement.get("state") != "SEPARATE_SOURCE_BOUND_AGGREGATE"
            ):
                raise RegistryError("full-release producer cannot be substituted or inferred")
            aggregate = registered_gate(manifest, "FULL_RELEASE_REGRESSIONS")
            if (
                aggregate["entry_point"] != gate["entry_point"]
                or aggregate["certificate_authority"] != "NONE"
            ):
                raise RegistryError("full release must reuse the runner and original A04 authority")
        elif requirement.get("producer") != requirement["id"]:
            raise RegistryError("missing required active producer registration")
        else:
            registered_gate(manifest, requirement["producer"])
    return {
        "scope": PIPELINE_SCOPE,
        "selectors": list(EXACT_TEST_IDS),
        "declared_obligations": len(inventories[0]),
        "verdict_authority": "A04_UNCHANGED",
        "golden_provenance": provenance,
    }


def _check_historical_tools(manifest: dict, rows: list[dict], root: Path) -> dict:
    policy = manifest.get("historical_tool_policy", {})
    if (
        policy.get("schema") != "swinglens.historical-tool-policy.v1"
        or not {"entry_points_sha256", "category_counts", "manual_guard", "manual_guard_sha256"}
        <= policy.keys()
    ):
        raise RegistryError("final historical tool policy missing")
    historical = [row for row in rows if row["historical_decision"]]
    paths = {row["entry_point"] for row in historical}
    digest = hashlib.sha256("\n".join(sorted(paths)).encode()).hexdigest()
    if len(paths) != 29 or digest != policy["entry_points_sha256"]:
        raise RegistryError("final historical 29-tool population changed")
    if (
        dict(Counter(row.get("historical_final_category") for row in historical))
        != policy["category_counts"]
    ):
        raise RegistryError("final historical category conservation mismatch")
    if source_sha256(_file(root, policy["manual_guard"])) != policy["manual_guard_sha256"]:
        raise RegistryError("historical manual authorization guard changed")
    for row in historical:
        path = row["entry_point"]
        category = row.get("historical_final_category")
        if category not in {"A", "B", "C", "D"} or row["automatic_certification"] != "DENIED":
            raise RegistryError(
                "historical entry lacks a verified noncertifying lifecycle: " + path
            )
        if (
            not row.get("historical_original_proposal")
            or len(row.get("historical_source_archive_sha256", "")) != 64
        ):
            raise RegistryError("historical original evidence provenance missing: " + path)
        if category == "B" and not row.get("historical_replacement_tests"):
            raise RegistryError("unique historical regression protection missing: " + path)
        if not path.endswith(".py"):
            if row.get("historical_entry_policy") != "READ_ONLY_SQL_ARCHIVE":
                raise RegistryError("historical SQL scope missing")
            continue
        tree = ast.parse(_file(root, path).read_text(encoding="utf-8-sig"))
        body = list(tree.body)
        while body and (
            (
                isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            )
            or (isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__")
        ):
            body.pop(0)

        def refusal(node, prefix):
            return (
                isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name)
                and node.exc.func.id == "SystemExit"
                and len(node.exc.args) == 1
                and isinstance(node.exc.args[0], ast.Constant)
                and isinstance(node.exc.args[0].value, str)
                and node.exc.args[0].value.startswith(prefix)
            )

        mode = row.get("historical_entry_policy")
        if mode in {"ARCHIVED_IMPORT_BLOCKED", "RETIRED_IMPORT_BLOCKED"}:
            prefix = (
                "HISTORICAL_EXECUTION_RETIRED"
                if category == "D"
                else "HISTORICAL_EXECUTION_ARCHIVED"
            )
            valid = bool(body) and refusal(body[0], prefix)
        elif mode == "ARCHIVED_CLI_BOUNDED_HELPERS":
            valid = (
                bool(body)
                and isinstance(body[0], ast.If)
                and ast.dump(body[0].test)
                == ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body)
                and bool(body[0].body)
                and refusal(body[0].body[0], "HISTORICAL_EXECUTION_ARCHIVED")
            )
        elif mode == "EXPLICIT_MANUAL_TARGET_AND_HUMAN_WRITE_CONFIRMATION":
            calls = [
                node
                for node in body
                if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "_a10_authorize"
            ]
            early = body[: body.index(calls[0])] if calls else []
            expected_path = (
                "_a10_sys.path.insert(0, str(_a10_Path(__file__).resolve().parents["
                + str(len(Path(path).parts) - 1)
                + "]))"
            )
            valid = (
                category == "C"
                and len(early) == 5
                and isinstance(early[2], ast.If)
                and ast.dump(early[2].test)
                == ast.dump(ast.parse('__name__ != "__main__"', mode="eval").body)
                and isinstance(early[3], ast.Expr)
                and ast.dump(early[3]) == ast.dump(ast.parse(expected_path).body[0])
                and len(calls) == 1
                and len(calls[0].value.args) == 1
                and isinstance(calls[0].value.args[0], ast.Constant)
                and calls[0].value.args[0].value == path
                and next(
                    (
                        ast.literal_eval(k.value)
                        for k in calls[0].value.keywords
                        if k.arg == "operations"
                    ),
                    None,
                )
                == json.loads(row["historical_manual_operations"])
                and any(
                    isinstance(n, ast.If)
                    and any(refusal(x, "HISTORICAL_MANUAL_IMPORT_REFUSED") for x in n.body)
                    for n in early
                )
                and all(
                    n.module in {"pathlib", "scripts.forensics.historical_tool_safety"}
                    for n in early
                    if isinstance(n, ast.ImportFrom)
                )
                and all(
                    all(a.name == "sys" for a in n.names)
                    for n in early
                    if isinstance(n, ast.Import)
                )
            )
        else:
            valid = False
        if not valid:
            raise RegistryError("historical early execution restriction missing: " + path)
    # Inspect indirect command text as well as the existing direct-invocation parser.
    command_text = json.dumps(manifest["full_release"])
    for workflow in (
        *list((root / ".github/workflows").glob("*.yml")),
        *list((root / ".github/workflows").glob("*.yaml")),
    ):
        command_text += "\n" + "\n".join(
            line for line in workflow.read_text().splitlines() if not line.lstrip().startswith("#")
        )
    command_text = command_text.replace("\\\\", "/").replace("\\", "/")
    for path in paths:
        module = path[:-3].replace("/", ".") if path.endswith(".py") else path
        if path in command_text or module in command_text:
            raise RegistryError(
                "historical tool referenced by automatic release invocation: " + path
            )
    return {
        "verified_entries": 29,
        "categories": policy["category_counts"],
        "current_release_authority": "NONE",
        "historical_operations_executed": 0,
    }


def _check_full_release(manifest: dict, root: Path) -> None:
    import yaml

    from scripts.qa.release_evidence import validate_policy

    contract = manifest["full_release"]
    try:
        validate_policy(contract)
    except ValueError as exc:
        raise RegistryError(str(exc)) from exc
    workflow = yaml.load(
        _file(root, ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    jobs = workflow["jobs"]
    aggregate = jobs["full-release"]
    required_jobs = {"release-plan", *(r["job"] for r in contract["base_lanes"].values())}
    if aggregate.get("if") != "always()" or set(aggregate["needs"]) != required_jobs:
        raise RegistryError("required terminal aggregate dependencies missing")
    terminal = next(
        (s for s in aggregate["steps"] if s.get("name") == "Aggregate full-release certification"),
        {},
    )
    if terminal.get("if") != "always()" or terminal.get("continue-on-error"):
        raise RegistryError("terminal full-release verdict must always enforce failure")
    for job in required_jobs:
        if jobs[job].get("continue-on-error") or jobs[job].get("if"):
            raise RegistryError("required CI job condition/downgrade prohibited: " + job)
    steps = {s.get("name"): s for s in jobs["lint-test-migrations"]["steps"]}
    for name in contract["preflight_steps"]:
        if name not in steps or steps[name].get("continue-on-error") or steps[name].get("if"):
            raise RegistryError("required original preflight omitted/downgraded: " + name)
    for lane, spec in {**contract["base_lanes"], **contract["conditional_lanes"]}.items():
        job_steps = {s.get("name"): s for s in jobs[spec["job"]]["steps"]}
        if spec["step"] not in job_steps or job_steps[spec["step"]].get("continue-on-error"):
            raise RegistryError("required test step omitted/downgraded: " + lane)
        record = job_steps.get("Record release " + lane, {})
        terminal_command = "--record-release-lane " + lane
        if lane == "a04":
            terminal_command = (
                "-m scripts.qa.owned_certification record --release-plan "
                "test-results/release-plan/plan.json --live-root test-results/release-live/a04 "
                "--snapshot-root test-results/release/a04 --job-result "
                '"${{ steps.release_a04.outcome }}"'
            )
            invoked = job_steps[spec["step"]].get("run", "")
            if (
                "-m scripts.qa.owned_certification run " not in invoked
                or "--live-root test-results/release-live/a04" not in invoked
                or "--report-dir test-results/release-live/a04/a04" not in invoked
            ):
                raise RegistryError("A04 process ownership/live evidence boundary missing")
        if not record.get("if", "").startswith(
            "always()"
        ) or terminal_command not in record.get("run", ""):
            raise RegistryError("terminal evidence capture missing: " + lane)
        if spec["kind"] == "pytest":
            if "--collect-only" not in job_steps.get("Collect release " + lane, {}).get("run", ""):
                raise RegistryError("independent intended collection missing: " + lane)
            env = job_steps[spec["step"]].get("env", {})
            if (
                env.get("PYTEST_PLUGINS") != "scripts.qa.release_evidence"
                or env.get("SWINGLENS_RELEASE_CAPTURE") != "execution"
            ):
                raise RegistryError("native phase observation missing: " + lane)
    selection = manifest.get("test_selection")
    if selection:
        if selection["default_profile"] != "core" or set(selection["profiles"]) != {
            "core",
            "affected",
            "extended",
            "diagnostic",
            "live-authorized",
        }:
            raise RegistryError("reviewed core selection profiles missing")
        steps = {s.get("name"): s for s in jobs["release-plan"]["steps"]}
        prepare = steps["Prepare release plan without executing tests"]["run"]
        if "--release-profile core" not in prepare or "--impact-plan" not in prepare:
            raise RegistryError("default core release source impact is missing")
        for lane in ("python-regressions", "browser-smoke"):
            spec = contract["base_lanes"][lane]
            job_steps = {s.get("name"): s for s in jobs[spec["job"]]["steps"]}
            for name in ("Collect release " + lane, spec["step"]):
                command = job_steps[name]["run"]
                if (
                    "--release-selectors " + lane not in command
                    or '"${selected[@]}"' not in command
                ):
                    raise RegistryError(
                        "core selection is not consumed by collection/execution: " + lane
                    )


def validate_registry(manifest: dict, root: Path = ROOT) -> dict:
    if manifest.get("schema") != "swinglens.certification-registry.v1":
        raise RegistryError("unsupported registry schema")
    if manifest.get("source_digest_policy") != SOURCE_DIGEST_POLICY:
        raise RegistryError("unsupported source digest policy")
    lifecycle_path = _file(root, manifest["standalone_registry"])
    with lifecycle_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    paths = [row["entry_point"] for row in rows]
    if len(rows) != manifest["baseline_entry_count"] or len(set(paths)) != len(paths):
        raise RegistryError("standalone lifecycle inventory incomplete/duplicate")
    membership = hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
    if membership != manifest["baseline_entry_points_sha256"]:
        raise RegistryError("standalone lifecycle inventory differs from frozen baseline")
    for row in rows:
        if (
            row["target_lifecycle"] not in LIFECYCLES
            or not row["rationale"]
            or not row["audit_evidence"]
        ):
            raise RegistryError("missing lifecycle disposition/provenance: " + row["entry_point"])
        current = source_sha256(_file(root, row["entry_point"]))
        if current != row["current_source_sha256"]:
            raise RegistryError(
                "standalone source changed; review registration: " + row["entry_point"]
            )
        if (
            row["target_lifecycle"] != "ACTIVE_RELEASE_CERTIFICATION"
            and row["automatic_certification"] != "DENIED"
        ):
            raise RegistryError("noncertifying tool given automatic certification permission")
    by_path = {row["entry_point"]: row for row in rows}
    additions = manifest["additional_entries"]
    for row in additions:
        if row["entry_point"] in by_path or row["target_lifecycle"] not in LIFECYCLES:
            raise RegistryError("duplicate/ambiguous additional entry")
        by_path[row["entry_point"]] = row
    ids = []
    commands = []
    producers = set()
    for gate in manifest["gates"]:
        if GATE_FIELDS - gate.keys() or any(
            gate[key] in (None, "", [])
            for key in GATE_FIELDS - {"automatic", "required", "ci_command"}
        ):
            raise RegistryError("required active gate information missing")
        if type(gate["required"]) is not bool or type(gate["automatic"]) is not bool:
            raise RegistryError("gate applicability must be explicit boolean")
        command = gate["command"]
        if not isinstance(command, list) or not all(isinstance(token, str) for token in command):
            raise RegistryError("command must be an exact argument vector")
        producer = (
            command[2].replace(".", "/") + ".py"
            if len(command) > 2 and command[1] == "-m"
            else command[1]
            if len(command) > 1
            else None
        )
        if not command or command[0] != "python" or producer != gate["entry_point"]:
            raise RegistryError("command does not invoke its approved producer")
        entry = by_path.get(gate["entry_point"])
        if entry is None or entry["target_lifecycle"] != "ACTIVE_RELEASE_CERTIFICATION":
            raise RegistryError(
                "unapproved/historical/operational gate producer: " + gate["entry_point"]
            )
        if gate["certificate_authority"] not in {"NONE", "A04_FINALIZER"}:
            raise RegistryError("unregistered certificate authority")
        if source_sha256(_file(root, gate["entry_point"])) != gate["source_sha256"]:
            raise RegistryError("active gate source fingerprint changed")
        if (
            gate["certificate_authority"] == "A04_FINALIZER"
            and gate["id"] != "EXACT_PIPELINE_CERTIFICATION"
        ):
            raise RegistryError("ordinary gate cannot claim independent pipeline authority")
        if "EXTERNAL_PROVIDER" in gate["safety"] and gate["automatic"]:
            raise RegistryError("manual external-provider tool cannot execute automatically")
        if gate["ci_command"]:
            if gate["ci_command"] != "uv run " + " ".join(command):
                raise RegistryError("CI command differs from approved invocation")
            workflow = _file(root, ".github/workflows/ci.yml").read_text()
            if gate["ci_command"] not in workflow:
                raise RegistryError("active CI registration missing: " + gate["id"])
        elif gate["id"] == "SMALL_LIVE_PROVIDER_CANARY":
            if (
                command
                != [
                    "python",
                    "-m",
                    "scripts.production_runtime_certification",
                    "--provider-mode",
                    "live",
                ]
                or gate["automatic"]
            ):
                raise RegistryError("live provider boundary requires separate manual invocation")
        elif gate["id"] != "EXACT_PIPELINE_CERTIFICATION":
            raise RegistryError("active invocation lacks an approved runner/CI binding")
        ids.append(gate["id"])
        commands.append(tuple(gate["command"]))
        producers.add(gate["entry_point"])
    if len(ids) != len(set(ids)) or len(commands) != len(set(commands)):
        raise RegistryError("duplicate/ambiguous gate registration")
    if producers != {
        path
        for path, row in by_path.items()
        if row["target_lifecycle"] == "ACTIVE_RELEASE_CERTIFICATION"
    }:
        raise RegistryError("active tool lacks explicit gate registration")
    # A manual tool's exact path cannot be added to CI as a direct invocation.
    # Canonical pytest tests importing a bounded helper remain ordinary tests.
    for workflow in (root / ".github/workflows").glob("*.yml"):
        for line in workflow.read_text().splitlines():
            for match in re.finditer(
                r"(?:python3?|pwsh|powershell)\s+"
                r"(?:-m\s+([\w.]+)|([\w./-]+\.(?:py|ps1)))",
                line,
            ):
                command_path = (
                    match.group(1).replace(".", "/") + ".py" if match.group(1) else match.group(2)
                )
                if command_path in by_path and command_path not in producers:
                    raise RegistryError("unapproved direct CI tool invocation: " + command_path)
    historical = [row for row in rows if row["historical_decision"]]
    if len(historical) != manifest["historical_entry_count"]:
        raise RegistryError("historical lifecycle inventory incomplete")
    expected = manifest["historical_disposition_counts"]
    if dict(Counter(row["historical_decision"] for row in historical)) != expected:
        raise RegistryError("historical disposition conservation mismatch")
    _check_full_release(manifest, root)
    historical_policy = _check_historical_tools(manifest, rows, root)
    return {
        "schema": "swinglens.certification-registry-check.v1",
        "registry_valid": True,
        "baseline_entries": len(rows),
        "historical_entries": len(historical),
        "historical_policy": historical_policy,
        "active_gates": ids,
        "lifecycle_counts": dict(Counter(row["target_lifecycle"] for row in rows)),
        "a04": _check_a04(manifest, root),
        "pipeline_result": "NOT_EVALUATED",
        "live_promotion": "CLOSED_PENDING_SEPARATE_REQUIRED_EVIDENCE",
    }


def _contract_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    result = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
        if isinstance(node, ast.ClassDef):
            result.update(
                node.name + "." + child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
    return result


def validate_source_contracts(manifest: dict, root: Path = ROOT) -> dict:
    """Check reviewed intent, never infer execution from AST or assertion presence."""
    policy = manifest["source_contracts"]
    contracts = policy["contracts"]
    if policy["schema"] != "swinglens.source-contracts.v1" or len(contracts) != 10:
        raise RegistryError("bounded source-contract policy missing")
    _file(root, policy["review_checklist"])
    ids = [c["id"] for c in contracts]
    if len(set(ids)) != len(ids):
        raise RegistryError("duplicate source contract")
    with _file(root, manifest["standalone_registry"]).open(encoding="utf-8", newline="") as f:
        forbidden = {r["entry_point"] for r in csv.DictReader(f) if r["historical_decision"]}
    lanes = {
        **manifest["full_release"]["base_lanes"],
        **manifest["full_release"]["conditional_lanes"],
    }
    for contract in contracts:
        if not all(
            contract.get(k)
            for k in ("owner", "risk", "behavior", "sources", "tests", "limitations", "oracle")
        ):
            raise RegistryError("contract lacks owner, impact decision or scope: " + contract["id"])
        binding = {k: v for k, v in contract.items() if k != "review_sha256"}
        if contract.get("review_sha256") != _contract_digest(binding):
            raise RegistryError("reviewed source/contract association changed: " + contract["id"])
        if contract["coverage_claim"] != "STATIC_INTENT":
            raise RegistryError("static metadata cannot establish measured execution")
        for source in contract["sources"]:
            if source["symbol"] not in _symbols(_file(root, source["path"])):
                raise RegistryError(
                    "source reference points to wrong contract/symbol: " + source["path"]
                )
        for test in contract["tests"]:
            path, sep, symbol = test["id"].partition("::")
            if path in forbidden or not path.startswith("tests/") or not sep:
                raise RegistryError("historical/operational entry cannot be an active test")
            file = _file(root, path)
            if symbol.split("[")[0] not in _symbols(file):
                raise RegistryError("claimed test ID no longer exists: " + test["id"])
            if source_sha256(file) != test["reviewed_test_sha256"]:
                raise RegistryError(
                    "test/assertion changed; re-review effectiveness: " + test["id"]
                )
            if test["lane"] not in lanes or not test["positive"] or not test["negative"]:
                raise RegistryError("test tier/assertion scope missing")
            if (test["tier"], test["fidelity"]) not in {
                ("FAST", "UNIT_DOUBLES"),
                ("NATIVE", "POSTGRESQL_SERVICE"),
                ("BROWSER", "DETERMINISTIC_NATIVE_WORKER_BROWSER"),
                ("EXTENDED", "DETERMINISTIC_NATIVE_WORKER_BROWSER"),
            }:
                raise RegistryError("unsupported test tier/fidelity promotion")
            function = next(
                n
                for n in ast.parse(file.read_text(encoding="utf-8-sig")).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == symbol.split("[")[0]
            )
            fixtures = {a.arg for a in function.args.args}
            if test["tier"] == "NATIVE" and not any(
                a.endswith("_engine") or a == "disposable_postgres_database" for a in fixtures
            ):
                raise RegistryError("unit/replay source lacks reviewed native fixture boundary")
            if (
                test["tier"] == "BROWSER"
                and not {"certification_environment", "certification_page"} <= fixtures
            ):
                raise RegistryError("unit/replay source lacks reviewed worker/browser boundary")
    return {"contracts": len(contracts), "claim": "STATIC_INTENT", "execution": "NOT_VERIFIED"}


def assess_contract_observation(
    contract: dict, observation: dict, current: dict, root: Path = ROOT
) -> dict:
    """Review an explicit execution observation; this is not a release verdict.

    Evidence producers remain A04/A16 and retained native/fault probes. Presence
    of source references or asserts alone is deliberately insufficient.
    """
    level = observation["level"]
    levels = ["STATIC_INTENT", "MEASURED_EXECUTION", "ASSERTED_CONTRACT", "FAULT_SENSITIVE"]
    if level not in levels:
        raise RegistryError("unknown coverage claim")
    if level == "STATIC_INTENT":
        return {"level": level, "freshness": "NOT_EXECUTION_EVIDENCE"}
    required = {
        "test_id",
        "source_fingerprint",
        "source_revision",
        "started_at",
        "test_sha256",
        "environment_sha256",
        "selection_sha256",
        "fidelity",
        "artifacts",
        "reached_sources",
        "execution_status",
    }
    if not required <= observation.keys() or observation.get("kind") != "EXECUTION_OBSERVATION":
        raise RegistryError("measured claim supported only by static metadata")
    tests = [t for t in contract["tests"] if t["id"] == observation["test_id"]]
    if len(tests) != 1 or observation["fidelity"] != tests[0]["fidelity"]:
        raise RegistryError("test promoted beyond reviewed native/provider fidelity")
    for key in ("source_fingerprint", "test_sha256", "environment_sha256", "selection_sha256"):
        if not isinstance(observation[key], str) or not re.fullmatch(
            r"[0-9a-f]{64}", observation[key]
        ):
            raise RegistryError("missing execution identity: " + key)
    if observation["execution_status"] not in {"PASS", "FAIL", "EXPECTED_REJECTION", "INCOMPLETE"}:
        raise RegistryError("execution status is not verified")
    try:
        timestamp = datetime.fromisoformat(observation["started_at"])
    except (TypeError, ValueError) as exc:
        raise RegistryError("execution timestamp missing or invalid") from exc
    if timestamp.tzinfo is None or timestamp > datetime.now(UTC):
        raise RegistryError("execution timestamp missing or future")
    if not observation["source_revision"] or not observation["artifacts"]:
        raise RegistryError("execution provenance missing")
    sources = {(s["path"], s["symbol"]) for s in contract["sources"]}
    reached = {(s["path"], s["symbol"]) for s in observation["reached_sources"]}
    if not reached or not reached <= sources:
        raise RegistryError("execution did not reach a reviewed source contract")
    artifacts = {}
    for artifact in observation["artifacts"]:
        if (
            hashlib.sha256(_file(root, artifact["path"]).read_bytes()).hexdigest()
            != artifact["sha256"]
        ):
            raise RegistryError("execution artifact changed")
        artifacts[artifact["path"]] = _file(root, artifact["path"])

    def witness(name):
        reference = observation.get(name, {})
        if reference.get("artifact") not in artifacts or "expected" not in reference:
            raise RegistryError("missing execution witness: " + name)
        content = artifacts[reference["artifact"]].read_text(encoding="utf-8-sig")
        if "line" in reference:
            content = content.splitlines()[reference["line"] - 1]
        value = json.loads(content)
        for key in reference.get("pointer", "").strip("/").split("/"):
            if key:
                value = value[int(key)] if isinstance(value, list) else value[key]
        if value != reference["expected"]:
            raise RegistryError("execution witness does not match: " + name)
        return value

    qualified = {p.removesuffix(".py").replace("/", ".") + "." + s for p, s in reached}
    if witness("source_witness") not in qualified:
        raise RegistryError("source witness is only static or belongs to another contract")
    if levels.index(level) >= 2 and not observation.get("reached_assertions"):
        raise RegistryError("claimed contract has no reached assertion")
    if level == "FAULT_SENSITIVE" and not (
        observation.get("fault", {}).get("outcome") == "EXPECTED_REJECTION"
        and observation["fault"].get("reached_assertion") in observation["reached_assertions"]
    ):
        raise RegistryError("negative control has no reached rejecting assertion")
    if levels.index(level) >= 2 and witness("assertion_witness") not in (True, "PASS", "FAILED"):
        raise RegistryError("meaningful assertion was not reached")
    if level == "FAULT_SENSITIVE" and witness("fault_witness") not in (
        "FAILED",
        "EXPECTED_REJECTION",
    ):
        raise RegistryError("fault did not fail at its intended boundary")
    fresh = (
        observation["source_fingerprint"] == current["fingerprint"]
        and observation["source_revision"] == current["revision"]
        and observation["test_sha256"] == tests[0]["reviewed_test_sha256"]
        and observation["environment_sha256"] == current["environment_sha256"]
        and observation["selection_sha256"] == current["selection_sha256"]
    )
    if observation.get("claim_current") and not fresh:
        raise RegistryError("old PASS cannot establish current-source coverage")
    return {
        "level": level,
        "freshness": "CURRENT_IDENTITIES_MATCH" if fresh else "HISTORICAL_ONLY",
        "execution_status": observation["execution_status"],
        "release_verdict": "NOT_EVALUATED",
    }


def source_impact_plan(manifest: dict, changed: list[str], *, baseline_known: bool = True) -> dict:
    """Suggest reviewed selectors; never execute or subtract mandatory CI lanes."""
    policy = manifest["source_contracts"]
    affected = []
    unknown = []
    historical = []
    with _file(ROOT, manifest["standalone_registry"]).open(encoding="utf-8", newline="") as f:
        archived = {r["entry_point"] for r in csv.DictReader(f) if r["historical_decision"]}
    paths = sorted({p.replace("\\", "/").removeprefix("./") for p in changed})
    for path in paths:
        if path.startswith("/") or ".." in Path(path).parts or ":" in path:
            raise RegistryError("unsafe changed source path")
        if path in archived:
            historical.append(path)
            continue
        found = []
        for c in policy["contracts"]:
            exact = [s["path"] for s in c["sources"]] + [t["id"].split("::")[0] for t in c["tests"]]
            if (
                path in exact
                or path in policy["selection_policy_paths"]
                or any(fnmatchcase(path, p) for p in c["impact_patterns"])
            ):
                found.append(c["id"])
        if not found:
            unknown.append(
                {
                    "path": path,
                    "owner": "UNKNOWN",
                    "impact": "NOT_VERIFIED",
                    "risk": "HIGH"
                    if path.startswith(tuple(policy["high_risk_roots"]))
                    else "UNREVIEWED",
                }
            )
        affected.extend(found)
    selected = [c for c in policy["contracts"] if c["id"] in set(affected)]
    tests = [t for c in selected for t in c["tests"]]
    return {
        "schema": "swinglens.source-impact-plan.v1",
        "changed_paths": paths,
        "plan_only": True,
        "affected_contracts": [
            {
                "id": c["id"],
                "owner": c["owner"],
                "risk": c["risk"],
                "reason": "explicit source/test association or reviewed impact pattern",
            }
            for c in selected
        ],
        "direct_fast_tests": sorted({t["id"] for t in tests if t["tier"] == "FAST"}),
        "required_native_browser_tests": sorted(
            {t["id"] for t in tests if t["tier"] in {"NATIVE", "BROWSER"}}
        ),
        "a04_refresh_required": bool(paths),
        "a04_refresh_rule": (
            "Compare the original A04 whole-source fingerprint. Changed covered "
            "source/test/oracle/policy invalidates older current-source claims. "
            "Documentation/untracked source can also change that identity."
        ),
        "required_release_lanes": manifest["full_release"]["base_lanes"],
        "conditional_extended_lanes": manifest["full_release"]["conditional_lanes"],
        "advisory_manual": manifest["full_release"]["advisory_manual"],
        "unknown_impact": unknown,
        "historical_manual_review_only": historical,
        "baseline_known": baseline_known,
        "review_required": not baseline_known or any(r["risk"] == "HIGH" for r in unknown),
        "status": "REVIEW_REQUIRED"
        if not baseline_known or any(r["risk"] == "HIGH" for r in unknown)
        else "PLAN_READY",
        "current_coverage": "NOT_VERIFIED",
        "release_verdict": "NOT_EVALUATED",
    }


def ci_comparison_baseline(root: Path = ROOT) -> dict:
    """Resolve CI's comparison without treating an unknown history as an empty diff.

    Default-branch snapshots certify the complete core on every invocation. Their
    impact comparison is the last first-parent change, not a claim about changes
    since a previous certificate. Unknown paths still require owner review.
    """
    import os
    import subprocess

    def git(*arguments):
        return subprocess.check_output(
            ["git", *arguments], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text()) if event_path else {}
    result = {
        "event": event_name,
        "tested_source": git("rev-parse", "HEAD"),
        "baseline": None,
        "policy": "event-base-v1",
        "reason": None,
    }
    base = event.get("pull_request", {}).get("base", {}).get("sha") or event.get("before")
    try:
        if event_name in {"schedule", "workflow_dispatch"}:
            result["policy"] = "trusted-default-branch-first-parent-snapshot-v1"
            repository = event.get("repository", {})
            default = repository.get("default_branch")
            if (
                repository.get("full_name") != "ivobog/swing-lens"
                or os.environ.get("GITHUB_REPOSITORY") != "ivobog/swing-lens"
                or default != "main"
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or os.environ.get("GITHUB_SHA") != result["tested_source"]
            ):
                raise ValueError("snapshot must test the trusted default branch source")
            if git("rev-parse", "--is-shallow-repository") != "false":
                raise ValueError("snapshot requires complete first-parent history")
            base = git("rev-parse", "--verify", "HEAD^1")
            if base == result["tested_source"]:
                raise ValueError("self comparison is not a snapshot baseline")
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", base, result["tested_source"]],
                cwd=root, check=True, capture_output=True,
            )
        if not base or set(base) == {"0"}:
            raise ValueError("event supplies no comparison commit")
        result["baseline"] = git(
            "rev-parse", "--verify", "--end-of-options", base + "^{commit}"
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        result["reason"] = str(exc)
        result["baseline"] = None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate only; never execute tools")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--diff-file", type=Path, help="UTF-8 newline-separated repository paths")
    parser.add_argument(
        "--diff-base", help="Git commit/ref; include working changes and untracked files"
    )
    parser.add_argument(
        "--ci-impact", action="store_true", help="read GitHub event base; plan only"
    )
    parser.add_argument("--impact-output", type=Path)
    args = parser.parse_args()
    try:
        manifest = load_manifest()
        result = validate_registry(manifest)
        result["source_contracts"] = validate_source_contracts(manifest)
        changed = list(args.changed_file)
        known = True
        if args.diff_file:
            lines = args.diff_file.read_text(encoding="utf-8").splitlines()
            if any(line.startswith("diff --git ") for line in lines):
                changed.extend(line[6:] for line in lines if line.startswith(("--- a/", "+++ b/")))
            else:
                changed.extend(line for line in lines if line)
        base = args.diff_base
        comparison = None
        if args.ci_impact:
            comparison = ci_comparison_baseline()
            base = comparison["baseline"]
            known = base is not None
        if base:
            import subprocess

            commit = subprocess.check_output(
                ["git", "rev-parse", "--verify", "--end-of-options", base + "^{commit}"],
                cwd=ROOT,
                text=True,
            ).strip()
            changed.extend(
                subprocess.check_output(
                    ["git", "diff", "--no-renames", "--name-only", commit, "--"],
                    cwd=ROOT,
                    text=True,
                ).splitlines()
            )
            changed.extend(
                subprocess.check_output(
                    ["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, text=True
                ).splitlines()
            )
        if changed or args.ci_impact or args.diff_file or args.diff_base or args.impact_output:
            result["impact"] = source_impact_plan(manifest, changed, baseline_known=known)
            if comparison is not None:
                result["impact"]["comparison"] = comparison
            if args.impact_output:
                args.impact_output.parent.mkdir(parents=True, exist_ok=True)
                args.impact_output.write_text(json.dumps(result["impact"], indent=2) + "\n")
    except (RegistryError, KeyError, OSError, ValueError, StopIteration) as exc:
        print(
            json.dumps(
                {"registry_valid": False, "error": str(exc), "pipeline_result": "NOT_EVALUATED"}
            )
        )
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
