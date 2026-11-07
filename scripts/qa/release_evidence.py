"""Evidence adapter for the existing certification CLI and native test runners.

GitHub Actions schedules jobs; pytest, Node and promtool execute tests. This
module records and validates their evidence. A04 owns every pipeline decision.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE_LANES = {
    "migration-harness",
    "populated-restore",
    "python-regressions",
    "golden-scoring",
    "browser-smoke",
    "windows-recovery",
    "node",
    "promql",
    "a04",
}
CATEGORIES = ("passed", "failed", "errored", "skipped", "blocked", "incomplete")


class EvidenceError(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        raise EvidenceError("missing or linked evidence: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceError("evidence must be an object: " + str(path))
    return value


def identity() -> dict:
    from scripts.production_runtime_certification import _git, _working_tree_identity

    fingerprint, dirty = _working_tree_identity()
    return {"revision": _git("rev-parse", "HEAD"), "fingerprint": fingerprint, "dirty": dirty}


def policy() -> dict:
    value = read_json(ROOT / "config/certification_registry.json")["full_release"]
    validate_policy(value)
    return value


def core_policy(impact: dict, *, event: str | None = None) -> dict:
    """Apply reviewed selection to the original lanes, never to their pass rules."""
    from scripts.qa.check_certification_registry import ci_comparison_baseline, source_impact_plan

    manifest = read_json(ROOT / "config/certification_registry.json")
    selection = manifest["test_selection"]
    if set(selection["profiles"]) != {
        "core",
        "affected",
        "extended",
        "diagnostic",
        "live-authorized",
    }:
        raise EvidenceError("reviewed selection profiles missing")
    expected = source_impact_plan(
        manifest, impact["changed_paths"], baseline_known=impact["baseline_known"]
    )
    if "comparison" in impact:
        comparison = ci_comparison_baseline(ROOT)
        expected["comparison"] = comparison
        if impact["baseline_known"] != (comparison["baseline"] is not None):
            raise EvidenceError("source comparison baseline is not verified")
    if expected != impact or impact["review_required"]:
        raise EvidenceError("source impact is stale or requires review")
    rows = selection["entries"]
    core_contracts = {
        r["contract"] for r in rows if r["tier"] in {"RELEASE_CORE", "NATIVE_A04_PRODUCER"}
    }
    if (
        not {c["id"] for c in manifest["source_contracts"]["contracts"] if c["risk"] == "P0"}
        <= core_contracts
    ):
        raise EvidenceError("required P0 core contract omitted")
    mapped = {t["id"]: t for c in manifest["source_contracts"]["contracts"] for t in c["tests"]}
    separate = {"MIGRATION", "DB_RESTORE", "NUMERIC_GOLDENS", "WINDOWS_RECOVERY", "NODE", "PROMQL"}
    python = {
        r["exact_test_id"]
        for r in rows
        if r["tier"] == "RELEASE_CORE" and r["contract"] not in separate
    }
    python.update(t for t in impact["direct_fast_tests"] if mapped[t]["tier"] == "FAST")
    python.update(
        t for t in impact["required_native_browser_tests"] if mapped[t]["tier"] == "NATIVE"
    )
    python.update(selection["policy_controls"])
    browser = {r["exact_test_id"] for r in rows if r["tier"] == "RELEASE_CORE_BROWSER"}
    dispositions = []
    specialized = []
    for row in rows:
        tier, node = row["tier"], row["exact_test_id"]
        triggered = any(
            fnmatchcase(p, pattern)
            for p in impact["changed_paths"]
            for pattern in row["trigger_paths"]
        )
        if (event in {"push", "schedule", "workflow_dispatch"}
            and row["contract"] == "SCALE_88_100" and tier == "EXTENDED_TRIGGERED"):
            triggered = True
        if triggered and tier == "EXTENDED_TRIGGERED":
            specialized.append(node)
        if triggered and tier in {
            "CHANGE_AFFECTED_NATIVE",
            "CHANGE_AFFECTED_RECOVERY",
            "CHANGE_AFFECTED_BROWSER",
        }:
            if (
                "BROWSER" in tier
                or "RECOVERY" in tier
                or mapped.get(node, {}).get("tier") == "BROWSER"
            ):
                browser.add(node)
            else:
                python.add(node)
        selected = (
            tier in {"RELEASE_CORE", "RELEASE_CORE_BROWSER", "NATIVE_A04_PRODUCER"}
            or node in python
            or node in browser
            or node in specialized
        )
        dispositions.append(
            {
                **row,
                "selected": selected,
                "triggered": triggered,
                "outcome": "NOT_RUN" if selected else row["deferred_outcome"],
            }
        )
    value = copy.deepcopy(policy())
    value["profile"] = "core"
    value["scope"] = (
        "Curated core and reviewed source-impact contracts; not full-release or live promotion"
    )
    value["base_lanes"]["python-regressions"]["selectors"] = sorted(python)
    value["base_lanes"]["browser-smoke"]["selectors"] = sorted(browser)
    value["a04_prerequisites"] = {
        "kind": "pytest", "required": True,
        "selectors": sorted(specialized), "markers": "", "ignores": [],
        "browsers": sorted({
            browser for browser in ("chromium", "firefox")
            if any("[" + browser + "-" in node for node in specialized)
        }),
        "postgres_major": 18,
        "conditional_pr_cardinality": "approved-pr-native-100-88-handoff-v1",
    }
    value["selection_manifest"] = {
        "profiles": selection["profiles"],
        "entries": dispositions,
        "affected_T0_T1": sorted(python),
        "source_impact": impact,
        "deferred_is_pass": False,
    }
    validate_policy(value)
    return value


def validation_selection(impact: dict, level: str) -> dict[str, list[str]]:
    """Use affected contracts before certification; these groups issue no certificate."""
    if level not in {"developer", "integration"}:
        raise EvidenceError("unknown validation level")
    core_policy(impact)  # Recheck reviewed ownership, comparison and unknown impact.
    manifest = read_json(ROOT / "config/certification_registry.json")
    mapped = {
        test["id"]: test
        for contract in manifest["source_contracts"]["contracts"]
        for test in contract["tests"]
    }
    groups = {"affected-fast": sorted(
        set(impact["direct_fast_tests"]) | set(manifest["test_selection"]["policy_controls"])
    )}
    if level == "integration":
        native = sorted({
            node for node in impact["required_native_browser_tests"]
            if mapped[node]["tier"] == "NATIVE"
        })
        if native:
            groups["affected-postgresql"] = native
        if any(p.startswith("app/") for p in impact["changed_paths"]):
            # Exercise cardinality, the non-round tail and alternate handoff once
            # before the release matrix. Nothing here defers a required A04 case.
            pipeline = sorted({
                row["exact_test_id"] for row in manifest["test_selection"]["entries"]
                if row["contract"] == "SCALE_88_100" and row["exact_test_id"].endswith(
                    ("[chromium-False-100]", "[chromium-True-88]")
                )
            })
            if len(pipeline) != 2:
                raise EvidenceError("reviewed native population/handoff pair missing")
            groups["native-pipeline-100-and-88-handoff"] = pipeline
    return {name: selectors for name, selectors in groups.items() if selectors}


def prepare_core_plan(run_id: str, attempt: str, event: str, extended: bool, impact: dict) -> dict:
    contract = core_policy(impact, event=event)
    plan = prepare_plan(run_id, attempt, event, extended)
    plan.pop("plan_sha256")
    plan.update(
        profile="core",
        source_impact=impact,
        policy_sha256=digest(contract),
        selection_manifest=contract["selection_manifest"],
        a04_prerequisites=contract["a04_prerequisites"],
    )
    plan["required_lanes"].update(copy.deepcopy(contract["base_lanes"]))
    plan["plan_sha256"] = digest(plan)
    return plan


def validate_policy(value: dict) -> None:
    if set(value["base_lanes"]) != BASE_LANES:
        raise EvidenceError("required release lane omitted or downgraded")
    for name, lane in value["base_lanes"].items():
        if lane.get("required") is not True or not lane.get("job") or not lane.get("kind"):
            raise EvidenceError("required lane applicability missing: " + name)
    if value["base_lanes"]["browser-smoke"].get("browsers") != ["chromium", "firefox"]:
        raise EvidenceError("both required browser engines must survive")
    for name in ("node", "promql"):
        cases = value["base_lanes"][name].get("case_ids", [])
        if not cases or len(cases) != len(set(cases)):
            raise EvidenceError("native cross-language identities missing: " + name)
    if set(value["conditional_lanes"]) != {"ceri-extended", "population-extended"}:
        raise EvidenceError("extended tier registration changed")


def prepare_plan(run_id: str, attempt: str, event: str, extended: bool) -> dict:
    if (
        not run_id
        or not attempt
        or event not in {"local", "push", "pull_request", "schedule", "workflow_dispatch"}
    ):
        raise EvidenceError("explicit execution identity/event required")
    contract = policy()
    lanes = copy.deepcopy(contract["base_lanes"])
    if event == "schedule" or (event == "workflow_dispatch" and extended):
        lanes["ceri-extended"] = copy.deepcopy(contract["conditional_lanes"]["ceri-extended"])
    if event == "workflow_dispatch" and extended:
        lanes["population-extended"] = copy.deepcopy(
            contract["conditional_lanes"]["population-extended"]
        )
    plan = {
        "schema": "swinglens.full-release-plan.v1",
        "execution_id": run_id,
        "attempt": attempt,
        "event": event,
        "extended_scale": extended,
        "created_at": datetime.now(UTC).isoformat(),
        "source": identity(),
        "policy_sha256": digest(contract),
        "required_lanes": lanes,
        "not_applicable": {
            name: {"status": "NOT_RUN", "reason": "existing trigger does not apply", **spec}
            for name, spec in contract["conditional_lanes"].items()
            if name not in lanes
        },
        "advisory_manual": contract["advisory_manual"],
        "verdict": "NOT_RUN",
    }
    plan["plan_sha256"] = digest(plan)
    return plan


def validate_plan(plan: dict, *, current: bool = False) -> None:
    payload = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if plan.get("schema") != "swinglens.full-release-plan.v1" or digest(payload) != plan.get(
        "plan_sha256"
    ):
        raise EvidenceError("invalid release plan seal")
    contract = (core_policy(plan["source_impact"], event=plan["event"])
                if plan.get("profile") == "core" else policy())
    if digest(contract) != plan["policy_sha256"]:
        raise EvidenceError("release policy changed")
    expected = copy.deepcopy(contract["base_lanes"])
    if plan["event"] == "schedule" or (
        plan["event"] == "workflow_dispatch" and plan["extended_scale"]
    ):
        expected["ceri-extended"] = contract["conditional_lanes"]["ceri-extended"]
    if plan["event"] == "workflow_dispatch" and plan["extended_scale"]:
        expected["population-extended"] = contract["conditional_lanes"]["population-extended"]
    if plan["required_lanes"] != expected:
        raise EvidenceError("release selection differs from reviewed policy")
    if plan.get("a04_prerequisites") != contract.get("a04_prerequisites"):
        raise EvidenceError("specialized prerequisite selection differs from reviewed policy")
    if (
        plan.get("profile") == "core"
        and plan.get("selection_manifest") != contract["selection_manifest"]
    ):
        raise EvidenceError("selected/deferred scope differs from reviewed policy")
    if current and identity() != plan["source"]:
        raise EvidenceError("source changed since release plan")


def context(plan: dict, lane: str) -> dict:
    return {
        "execution_id": plan["execution_id"],
        "attempt": plan["attempt"],
        "plan_sha256": plan["plan_sha256"],
        "source": plan["source"],
        "lane": lane,
    }


def lane_spec(plan: dict, lane: str) -> dict:
    if lane == "a04-prerequisites-pr":
        from scripts.qa.evidence_reuse import reduced_spec

        return reduced_spec(plan)
    if lane == "a04-prerequisites":
        return plan["a04_prerequisites"]
    return plan["required_lanes"][lane]


def safe_file(root: Path, relative: str) -> Path:
    path = root / relative
    if (
        Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not path.is_file()
        or path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise EvidenceError("missing or unsafe artifact: " + relative)
    return path


def junit_cases(path: Path, kind: str) -> dict[str, str]:
    tree = ET.parse(path)
    cases = list(tree.getroot().iter("testcase"))
    if not cases:
        raise EvidenceError("zero-test report cannot certify")
    result = {}
    for case in cases:
        name = case.attrib["name"]
        key = (
            case.attrib.get("classname", "") + "::" + name
            if kind == "pytest"
            else kind + "::" + name
        )
        if key in result:
            raise EvidenceError("duplicate test identity")
        status = (
            "errored"
            if case.find("error") is not None
            else "failed"
            if case.find("failure") is not None
            else "skipped"
            if case.find("skipped") is not None
            else "passed"
        )
        result[key] = status
    for suite in tree.getroot().iter("testsuite"):
        descendants = list(suite.iter("testcase"))
        if "tests" in suite.attrib and int(suite.attrib["tests"]) != len(descendants):
            raise EvidenceError("inconsistent report test count")
        for attribute, tag in (
            ("failures", "failure"),
            ("errors", "error"),
            ("skipped", "skipped"),
        ):
            if attribute in suite.attrib and int(suite.attrib[attribute]) != sum(
                c.find(tag) is not None for c in descendants
            ):
                raise EvidenceError("inconsistent report outcome count")
    # Node's native reporter emits totals as comments rather than suite attributes.
    for field, expected in (
        ("tests", len(cases)),
        ("fail", sum(v == "failed" for v in result.values())),
        ("skipped", sum(v == "skipped" for v in result.values())),
    ):
        match = re.search(r"<!-- " + field + r" (\d+) -->", path.read_text(encoding="utf-8"))
        if match and int(match[1]) != expected:
            raise EvidenceError("inconsistent native report total")
    return result


def controlled_environment(variables=None) -> str:
    """Optional reuse seal; standalone native observer copies need no app imports."""
    settings_path = ROOT / "app/settings.py"
    if not settings_path.is_file() or (ROOT / ".env").exists():
        return ""
    from app import settings as settings_module

    if Path(settings_module.__file__).resolve() != settings_path.resolve():
        return ""
    names = {name.upper() for name in settings_module.Settings.model_fields}
    for field in settings_module.Settings.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, str):
            names.add(alias.upper())
        elif hasattr(alias, "choices"):
            names.update(a.upper() for a in alias.choices if isinstance(a, str))
    names.discard("DATABASE_URL")  # Owned host/safety/server version checked separately.
    names.update({"NODE_OPTIONS", "NODE_PATH", "TZ", "LANG", "LC_ALL", "PYTHONOPTIMIZE",
                  "PYTHONHASHSEED", "PYTHONWARNINGS"})
    values = os.environ if variables is None else variables
    return digest({key.upper(): value for key, value in values.items() if key.upper() in names})


def environment_profile(variables=None) -> dict:
    import importlib.metadata
    from urllib.parse import urlsplit

    import psutil

    values = os.environ if variables is None else variables
    database = urlsplit(values.get("DATABASE_URL", ""))

    cpu_model = platform.processor()
    if Path("/proc/cpuinfo").is_file():
        cpu_model = next((line.partition(":")[2].strip()
                          for line in Path("/proc/cpuinfo").read_text().splitlines()
                          if line.startswith("model name")), cpu_model)
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pytest": importlib.metadata.version("pytest"),
        "database": {
            "host": database.hostname,
            "port": database.port,
            "name": database.path.lstrip("/"),
        },
        "database_safety_context": values.get("SWINGLENS_DATABASE_SAFETY_CONTEXT"),
        "provider_mode": values.get("SWINGLENS_CERTIFICATION_PROVIDER_MODE", "deterministic"),
        "controlled_environment_sha256": controlled_environment(values),
        "input_date": datetime.now(UTC).date().isoformat(),
        "hardware": {"machine": platform.machine(), "processor": cpu_model,
                     "cpu_count": os.cpu_count(), "memory_bytes": psutil.virtual_memory().total},
    }


def junit_key(nodeid: str) -> str:
    base, bracket, parameters = nodeid.partition("[")
    parts = base.split("::")
    return (
        ".".join([parts[0].removesuffix(".py").replace("/", "."), *parts[1:-1]])
        + "::"
        + parts[-1]
        + bracket
        + parameters
    )


def _selection(config) -> dict:
    def relative(value):
        path, *suffix = str(value).split("::")
        p = Path(path)
        if p.is_absolute():
            path = p.relative_to(ROOT).as_posix()
        return "::".join([path.replace("\\", "/"), *suffix])

    if (
        config.option.keyword
        or config.option.deselect
        or config.option.ignore_glob
        or getattr(config.option, "lf", False)
    ):
        raise EvidenceError("undeclared partial pytest selection")
    return {
        "selectors": [relative(p) for p in config.args],
        "markers": config.option.markexpr,
        "ignores": sorted(relative(p) for p in (config.option.ignore or [])),
        "browsers": config.getoption("browser", default=[]) or [],
    }


_capture: dict = {}


def pytest_sessionstart(session):
    global _capture
    path = os.environ.get("SWINGLENS_RELEASE_PLAN")
    if not path:
        _capture = {}
        return
    plan = read_json(Path(path))
    validate_plan(plan, current=True)
    lane = os.environ["SWINGLENS_RELEASE_LANE"]
    spec = lane_spec(plan, lane)
    if spec["kind"] != "pytest" or _selection(session.config) != {
        key: spec[key] for key in ("selectors", "markers", "ignores", "browsers")
    }:
        raise EvidenceError("pytest command differs from declared lane")
    mode = os.environ["SWINGLENS_RELEASE_CAPTURE"]
    if mode not in {"collection", "execution"} or session.config.option.collectonly != (
        mode == "collection"
    ):
        raise EvidenceError("independent collection/execution phase required")
    directory = Path(os.environ["SWINGLENS_RELEASE_RECORD_DIR"])
    profile = environment_profile()
    if spec.get("postgres_major"):
        if (
            profile["database_safety_context"] != "DISPOSABLE_TEST"
            or profile["database"]["host"] not in {"127.0.0.1", "localhost", "::1"}
            or not profile["database"]["name"].startswith(("swinglens_ci_", "swinglens_pytest_"))
        ):
            raise EvidenceError("owned disposable native database required")
        import psycopg

        with psycopg.connect(
            os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"),
            connect_timeout=5,
        ) as db:
            if db.info.server_version // 10000 != spec["postgres_major"]:
                raise EvidenceError("PostgreSQL tool version mismatch")
            profile["postgresql"] = {
                "version": db.execute("select version()").fetchone()[0],
                "database": db.execute("select current_database()").fetchone()[0],
                "postmaster_started_at": str(
                    db.execute("select pg_postmaster_start_time()").fetchone()[0]
                ),
            }
    _capture = {
        "context": context(plan, lane),
        "mode": mode,
        "directory": directory,
        "selected": [],
        "started": [],
        "phases": {},
        "environment": profile,
        "browsers_observed": {},
        "plan": plan,
    }


def pytest_collection_finish(session):
    if not _capture:
        return
    _capture["selected"] = [item.nodeid for item in session.items]
    if _capture["mode"] == "collection":
        write_json(
            _capture["directory"] / "collection.json",
            {
                "context": _capture["context"],
                "selected": _capture["selected"],
                "selection": _selection(session.config),
                "collect_only": True,
                "environment": _capture["environment"],
            },
        )


def pytest_runtest_logstart(nodeid, location):
    if _capture:
        _capture["started"].append(nodeid)


def pytest_runtest_call(item):
    if _capture and "browser" in item.funcargs:
        browser = item.funcargs["browser"]
        name = item.funcargs.get("browser_name")
        if name:
            _capture["browsers_observed"][name] = browser.version


def pytest_runtest_logreport(report):
    if _capture:
        _capture["phases"].setdefault(report.nodeid, {})[report.when] = {
            "outcome": report.outcome,
            "seconds": report.duration,
            "wasxfail": bool(getattr(report, "wasxfail", False)),
        }
        if report.failed:
            _record_failure_diagnostic(report)


def integration_performance(snapshot: dict) -> dict:
    """Integration tripwires; the original runtime observers remain authoritative."""
    small = snapshot["ticker_count"] <= 10
    stage_budget = 300 if small else 600
    total_budget = 900 if small else 3600  # Existing native-case absolute deadlines.
    rows = []
    for step in snapshot["pipeline_steps"]:
        start, end = step.get("started_at"), step.get("completed_at")
        duration = ((datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start)))
                    .total_seconds() if start and end else None)
        rows.append({"stage": step["step_name"], "seconds": duration,
                     "budget_seconds": stage_budget,
                     "passed": step["status"] == "COMPLETED" and duration is not None
                     and 0 <= duration <= stage_budget})
    duration = snapshot.get("pipeline_seconds")
    passed = (snapshot.get("pipeline_status") == "COMPLETED"
              and bool(rows) and all(row["passed"] for row in rows))
    passed = passed and duration is not None and 0 <= duration <= total_budget
    return {"status": "PASS" if passed else "FAIL", "stages": rows,
            "pipeline_seconds": duration, "pipeline_budget_seconds": total_budget,
            "scope": "integration feedback only; no release or production authority"}


def _retain_integration_runtime(item, report) -> None:
    from sqlalchemy.engine import make_url

    from app.services.redaction import redact_text

    fixture = item.funcargs.get("certification_environment")
    if fixture is None:
        return
    artifact = Path(fixture.artifact_dir)
    owner = read_json(artifact / ".test-output-owner.json")
    metadata = read_json(artifact / "environment.json")
    target = make_url(fixture.database_url)
    if (
        target.host not in {"127.0.0.1", "localhost", "::1"}
        or not re.fullmatch(r"swinglens_pytest_cert_[0-9a-f]{12}", target.database or "")
        or metadata.get("database_name") != target.database
        or metadata.get("execution_id") != fixture.execution_id
        or metadata.get("database_safety_context") != "DISPOSABLE_TEST"
        or owner != {"owner": "SwingLens pytest", "execution_id": fixture.execution_id}
    ):
        raise EvidenceError("native retention requires the exact owned disposable database")
    from evidence_output import new_owned_directory

    # Native certificate files may already be sealed at call completion. Keep
    # feedback and failed inputs in a separately owned sibling, never in that seal.
    directory = new_owned_directory(artifact.parent / "validation-feedback", fixture.execution_id)
    import psycopg
    from psycopg.rows import dict_row

    record = {"schema": "swinglens.integration-runtime-diagnostic.v1",
              "execution_id": fixture.execution_id, "nodeid": item.nodeid,
              "original_outcome": report.outcome, "certification": "NOT_EVALUATED",
              "source_fingerprint": os.environ.get("SWINGLENS_CERTIFICATION_CODE_FINGERPRINT")}
    url = target.set(drivername="postgresql")
    with psycopg.connect(url.render_as_string(hide_password=False), connect_timeout=5,
                         row_factory=dict_row) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        db.execute("SET LOCAL statement_timeout='5s'")
        if not db.execute("select 1 from background_workers where worker_id=%s",
                          ("certification-" + fixture.execution_id,)).fetchone():
            raise EvidenceError("native retention worker ownership does not match")
        pipeline = db.execute(
            "select id, upload_run_id, status, extract(epoch from (completed_at-started_at)) "
            "as seconds from pipeline_runs order by id desc limit 1"
        ).fetchone()
        record["pipeline_steps"] = db.execute(
            "select step_name,status,started_at,completed_at,retry_count "
            "from pipeline_steps where pipeline_run_id=%s order by step_order",
            (pipeline["id"],),
        ).fetchall() if pipeline else []
        record["ticker_count"] = db.execute(
            "select count(*) as total from raw_company_rows where run_id=%s",
            (pipeline["upload_run_id"],),
        ).fetchone()["total"] if pipeline else 0
        record["pipeline_seconds"] = float(pipeline["seconds"]) if (
            pipeline and pipeline["seconds"] is not None
        ) else None
        record["pipeline_status"] = pipeline["status"] if pipeline else "NOT_RUN"
    record["pipeline_steps"] = json.loads(json.dumps(record["pipeline_steps"], default=str))
    record["performance"] = integration_performance(record)
    if report.failed:
        record["diagnostic"] = "\n".join(
            redact_text(line) for line in str(report.longrepr).splitlines()
        )
        binary = shutil.which("pg_dump")
        if not binary:
            record["retention_error"] = "pg_dump unavailable; original failure preserved"
        else:
            pending = directory / "failed-input.dump.pending"
            dumped = subprocess.run(
                [binary, "-Fc", "--no-owner", "--no-acl", "-h", target.host,
                 "-p", str(target.port or 5432), "-U", target.username,
                 "-d", target.database, "-f", str(pending)],
                env={**os.environ, "PGPASSWORD": target.password or ""},
                capture_output=True, timeout=60, check=False,
            )
            if dumped.returncode == 0:
                final = directory / "failed-input.dump"
                pending.rename(final)
                record["retained_input"] = {
                    "path": final.name, "sha256": file_digest(final),
                    "snapshot": "pg_dump snapshot after original failure, before teardown",
                }
            else:
                record["retention_error"] = "pg_dump failed; original failure preserved"
    write_json(directory / "validation-runtime.json", record)


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    retain = (os.environ.get("SWINGLENS_IMPACT_VALIDATION") == "integration"
              or os.environ.get("SWINGLENS_RETAIN_NATIVE_RUNTIME") == "true")
    if report.when == "call" and retain:
        try:
            _retain_integration_runtime(item, report)
        except Exception as exc:
            # Diagnostics cannot replace the original native report. Missing
            # runtime proof keeps the integration group non-passing in the parent.
            import warnings

            warnings.warn("Integration retention unavailable: " + type(exc).__name__,
                          pytest.PytestWarning, stacklevel=1)


def _record_failure_diagnostic(report) -> None:
    """Retain the first actionable failure before a long session can be interrupted.

    These records are diagnostics only. Original native phases and complete
    collection/execution/JUnit agreement remain the sole lane pass evidence.
    """
    try:
        from app.services.redaction import redact_text

        directory = _capture["directory"] / "failures"
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(suffix=".pending", dir=directory)
        path = Path(temporary)
        value = {
            "schema": "swinglens.release-failure-diagnostic.v1",
            "context": _capture["context"],
            "observed_at": datetime.now(UTC).isoformat(),
            "nodeid": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "diagnostic": "\n".join(
                redact_text(line) for line in str(report.longrepr).splitlines()
            ),
            "certification": "NOT_EVALUATED_DIAGNOSTIC_ONLY",
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        path.replace(path.with_suffix(".json"))
    except Exception as exc:
        # A diagnostic I/O problem must never replace the original test failure.
        _capture.setdefault("diagnostic_errors", []).append(type(exc).__name__)


def pytest_sessionfinish(session, exitstatus):
    if not _capture:
        return
    errors = []
    try:
        validate_plan(_capture["plan"], current=True)
    except (EvidenceError, OSError, ValueError) as exc:
        errors.append(str(exc))
        session.exitstatus = 1
    if _capture["mode"] == "collection":
        path = _capture["directory"] / "collection.json"
        if path.is_file():
            value = read_json(path)
            value.update({"exit_code": int(exitstatus), "errors": errors})
            write_json(path, value)
    else:
        write_json(
            _capture["directory"] / "execution.json",
            {key: value for key, value in _capture.items() if key not in {"directory", "plan"}}
            | {"exit_code": int(exitstatus), "errors": errors, "session_finished": True,
               "finished_at": datetime.now(UTC).isoformat()},
        )


def _pytest_results(root: Path, plan: dict, lane: str, observed: dict) -> dict:
    collection = read_json(root / "collection.json")
    execution = read_json(root / "execution.json")
    expected = context(plan, lane)
    if collection.get("context") != expected or execution.get("context") != expected:
        raise EvidenceError("stale/foreign collection or execution")
    selected = collection["selected"]
    if collection.get("selection") != {
        key: lane_spec(plan, lane)[key]
        for key in ("selectors", "markers", "ignores", "browsers")
    }:
        raise EvidenceError("intended collection used a different selection rule")
    if (
        not selected
        or len(selected) != len(set(selected))
        or collection.get("exit_code") != 0
        or collection.get("errors")
    ):
        raise EvidenceError("invalid independent intended collection")
    if execution["selected"] != selected or len(execution["started"]) != len(
        set(execution["started"])
    ):
        raise EvidenceError("intended test never collected or duplicate start")
    if execution.get("errors") or not execution.get("session_finished"):
        raise EvidenceError("pytest process/session did not finish")
    if set(observed) != {junit_key(node) for node in selected}:
        raise EvidenceError("JUnit omitted or substituted an intended test")
    categories = {key: [] for key in CATEGORIES}
    for node in selected:
        phases = execution["phases"].get(node, {})
        if any(p.get("wasxfail") for p in phases.values()):
            status = "skipped"
        elif any(phases.get(p, {}).get("outcome") == "failed" for p in ("setup", "teardown")):
            status = "errored"
        elif any(p.get("outcome") == "skipped" for p in phases.values()):
            status = "skipped"
        elif set(phases) != {"setup", "call", "teardown"}:
            status = "incomplete"
        else:
            status = "passed" if phases["call"]["outcome"] == "passed" else "failed"
        if observed[junit_key(node)] != status:
            raise EvidenceError("JUnit and native phase outcomes disagree")
        categories[status].append(node)
    spec = lane_spec(plan, lane)
    profile = execution["environment"]
    if (
        not profile.get("python", "").startswith("3.12.")
        or not profile.get("pytest")
        or profile.get("provider_mode") != "deterministic"
    ):
        raise EvidenceError("required Python/pytest/provider environment identity missing")
    if spec.get("postgres_major") and (
        profile.get("database_safety_context") != "DISPOSABLE_TEST"
        or profile.get("database", {}).get("host") not in {"127.0.0.1", "localhost", "::1"}
        or not profile.get("database", {})
        .get("name", "")
        .startswith(("swinglens_ci_", "swinglens_pytest_"))
        or not profile.get("postgresql", {})
        .get("version", "")
        .startswith("PostgreSQL " + str(spec["postgres_major"]) + ".")
        or not profile.get("postgresql", {}).get("postmaster_started_at")
    ):
        raise EvidenceError("required native PostgreSQL environment identity missing")
    if spec["browsers"] and (
        set(execution["browsers_observed"]) != set(spec["browsers"])
        or not all(execution["browsers_observed"].values())
    ):
        raise EvidenceError("required actual browser engine omitted")
    return {
        "selected": selected,
        "collected": execution["selected"],
        "executed": execution["started"],
        "body_executed": [node for node in selected if "call" in execution["phases"].get(node, {})],
        **categories,
        "process_exit_code": execution["exit_code"],
        "environment": execution["environment"],
        "browsers_observed": execution["browsers_observed"],
        "phases": execution["phases"],
        **({"finished_at": execution["finished_at"]} if execution.get("finished_at") else {}),
    }


def a04_results(root: Path, plan: dict, lane: str) -> dict:
    """Consume A04 through its original finalizer; never rewrite sealed inputs."""
    from single_run_certification.reporting import EXACT_TEST_IDS, finalize_pipeline_certificate

    from scripts.qa.golden_provenance import verify_baseline

    verify_baseline(ROOT)

    from single_run_certification import candidate_validation

    required_candidate = (
        candidate_validation.candidate_binding()
        if candidate_validation.candidate_available()
        else None
    )

    report = read_json(root / "a04" / "report.json")
    if report.get("candidate_validation") != required_candidate:
        raise EvidenceError("A04 candidate proposal/input/oracle binding missing or stale")
    if report.get("release_context") != context(plan, lane):
        raise EvidenceError("stale or foreign A04 release context")
    if (
        report.get("working_tree_fingerprint") != plan["source"]["fingerprint"]
        or report.get("git_sha") != plan["source"]["revision"]
    ):
        raise EvidenceError("A04 source mismatch")
    prerequisites = plan.get("a04_prerequisites", {}).get("selectors", [])
    directory = root / "a04" / "source-impact-prerequisites"
    conditional = None
    if (directory / "pr-disposition.json").is_file():
        from scripts.qa.evidence_reuse import validate_pr_selection

        conditional = validate_pr_selection(
            directory, plan, feedback_root=root / "a04/runtime-evidence/validation-feedback"
        )
        if read_json(directory / "pr-disposition.json") != conditional:
            raise EvidenceError("conditional PR prerequisite disposition differs from native proof")
        prerequisites = conditional["selected"]
    if report.get("specialized_prerequisites", []) != prerequisites:
        raise EvidenceError("required specialized prerequisite omitted from A04")
    prerequisite_proof = None
    if prerequisites:
        if conditional:
            prerequisite_proof = conditional["current_native"]
        elif (directory / "carry-forward.json").is_file():
            from scripts.qa.evidence_reuse import check_reuse

            probe = read_json(directory / "current-environment.json")
            prerequisite_proof = check_reuse(
                directory, plan, "a04-prerequisites", probe["environment"]
            )
        else:
            observed = junit_cases(directory / "results.xml", "pytest")
            prerequisite_proof = _pytest_results(directory, plan, "a04-prerequisites", observed)
        if (
            prerequisite_proof["process_exit_code"] != 0
            or set(prerequisite_proof["passed"]) != set(prerequisites)
            or set(prerequisite_proof["body_executed"]) != set(prerequisites)
            or any(prerequisite_proof[key] for key in CATEGORIES if key != "passed")
        ):
            raise EvidenceError("specialized prerequisite failed, skipped or never executed")
    invocation = report["invocation"]
    if (
        report.get("verdict") != "CERTIFIED"
        or invocation.get("exit_code") != 0
        or invocation.get("failed")
    ):
        raise EvidenceError("A04 child is noncertified")
    if any(
        invocation.get(key) != list(EXACT_TEST_IDS) for key in ("selected", "collected", "executed")
    ):
        raise EvidenceError("incomplete A04 invocation")
    certificates = report["certificates"]
    if (
        report.get("provider_mode") != "deterministic"
        or len(certificates) != 2
        or {c["test_id"] for c in certificates} != set(EXACT_TEST_IDS)
        or len({c["execution_id"] for c in certificates}) != 2
        or any(c.get("verdict") != "CERTIFIED" for c in certificates)
    ):
        raise EvidenceError("A04 protected variant omitted")
    proofs = []
    for item in certificates:
        original = safe_file(root / "a04", item["path"])
        certificate = read_json(original)
        if certificate.get("candidate_validation") != required_candidate:
            raise EvidenceError("A04 candidate certificate binding missing or stale")
        expected = {
            **certificate["identity"],
            "source_fingerprint": plan["source"]["fingerprint"],
            "source_revision": plan["source"]["revision"],
            "invocation_id": report["execution_id"],
            "test_id": item["test_id"],
            "handoff": item["test_id"] == EXACT_TEST_IDS[1],
            "execution_id": item["execution_id"],
            "runtime_instance_id": item["execution_id"],
        }
        with tempfile.TemporaryDirectory(prefix="swinglens-a16-recheck-") as temporary:
            copied = Path(temporary) / "evidence"
            if any(
                p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                for p in original.parent.rglob("*")
            ):
                raise EvidenceError("linked A04 evidence prohibited")
            shutil.copytree(original.parent, copied, symlinks=False)
            checked = finalize_pipeline_certificate(
                copied, invocation=invocation, expected_identity=expected
            )
        if checked != certificate or checked["verdict"] != "CERTIFIED":
            raise EvidenceError("A04 finalizer rejects child/gates/artifact seal")
        proofs.append(
            {
                "path": "a04/" + item["path"].replace("\\", "/"),
                "execution_id": item["execution_id"],
                "sha256": file_digest(original),
                "identity": certificate["identity"],
            }
        )
    return {
        "selected": list(EXACT_TEST_IDS),
        "collected": list(EXACT_TEST_IDS),
        "executed": list(EXACT_TEST_IDS),
        "body_executed": list(EXACT_TEST_IDS),
        **{key: list(EXACT_TEST_IDS) if key == "passed" else [] for key in CATEGORIES},
        "process_exit_code": 0,
        "a04_certificates": proofs,
        **({"specialized_prerequisites": prerequisite_proof} if prerequisites else {}),
        **({"conditional_pr_disposition": conditional} if conditional else {}),
    }


def begin_native(root: Path, plan: dict, lane: str) -> None:
    validate_plan(plan, current=True)
    if (
        plan["required_lanes"][lane]["kind"] not in {"node", "promql"}
        or (root / "native-start.json").exists()
    ):
        raise EvidenceError("native evidence start must be unique")
    write_json(
        root / "native-start.json",
        {
            "context": context(plan, lane),
            "started_at": datetime.now(UTC).isoformat(),
            "platform": platform.platform(),
            "controlled_environment_sha256": controlled_environment(),
        },
    )


def _native_results(root: Path, plan: dict, lane: str, observed: dict) -> dict:
    spec = plan["required_lanes"][lane]
    start = read_json(root / "native-start.json")
    if start["context"] != context(plan, lane):
        raise EvidenceError("stale native invocation")
    modified = (root / "results.xml").stat().st_mtime
    if (root / "native-file-time.json").is_file():
        original_time = read_json(root / "native-file-time.json")
        if (original_time.get("context") != context(plan, lane)
            or original_time.get("results_sha256") != file_digest(root / "results.xml")):
            raise EvidenceError("native original report time/hash/context differs")
        modified = original_time["mtime_ns"] / 1_000_000_000
    if datetime.fromtimestamp(modified, UTC) < datetime.fromisoformat(
        start["started_at"]
    ):
        raise EvidenceError("native result predates invocation")
    version = safe_file(root, "version.txt").read_text(encoding="utf-8").strip()
    if not re.search(spec["tool_version_pattern"], version):
        raise EvidenceError("required native tool version missing/mismatched")
    if set(observed) != set(spec["case_ids"]):
        raise EvidenceError("native test missing, uncollected or unexpected")
    return {
        "selected": spec["case_ids"],
        "collected": list(observed),
        "executed": list(observed),
        "body_executed": [
            node for node, status in observed.items() if status in {"passed", "failed"}
        ],
        "process_exit_code": 0,
        **{key: [node for node, status in observed.items() if status == key] for key in CATEGORIES},
        "environment": {
            "tool_version": version,
            "platform": start["platform"],
            "declared_configuration": spec.get("configuration", {}),
            **({"controlled_environment_sha256": start["controlled_environment_sha256"]}
               if start.get("controlled_environment_sha256") else {}),
        },
    }


def evaluate_lane(root: Path, plan: dict, lane: str, job_result: str) -> dict:
    spec = plan["required_lanes"][lane]
    reasons = []
    empty = {key: [] for key in ("selected", "collected", "executed", "body_executed", *CATEGORIES)}
    result = empty
    if job_result != "success":
        reasons.append("required CI test step did not succeed: " + job_result)
    try:
        if (root / "carry-forward.json").is_file():
            from scripts.qa.evidence_reuse import check_reuse

            probe = read_json(root / "current-environment.json")
            result = check_reuse(root, plan, lane, probe["environment"])
        elif spec["kind"] == "a04":
            result = a04_results(root, plan, lane)
        else:
            observed = junit_cases(safe_file(root, "results.xml"), spec["kind"])
            result = (
                _pytest_results(root, plan, lane, observed)
                if spec["kind"] == "pytest"
                else _native_results(root, plan, lane, observed)
            )
        if (
            result.get("process_exit_code") != 0
            or not result["selected"]
            or set(result["selected"]) != set(result["passed"])
            or any(result[key] for key in CATEGORIES if key != "passed")
        ):
            reasons.append("required selected cases failed/errored/skipped/blocked/incomplete")
        if set(result["executed"]) != set(result["selected"]):
            reasons.append("selected execution incomplete")
    except (EvidenceError, KeyError, TypeError, ValueError, OSError, ET.ParseError,
            subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        reasons.append(str(exc))
        # Preserve observations even when a consistency check fails. Unavailable
        # evidence is explicitly unknown, never evidence of zero successful tests.
        result = _partial_results(root, plan, lane, spec, empty)
    if spec["kind"] in {"node", "promql"}:
        result["process_exit_code"] = 0 if job_result == "success" else None
    if job_result in {"skipped", "cancelled"}:
        result["blocked"] = [n for n in result["selected"] if n not in result["executed"]]
        result["incomplete"] = [n for n in result["incomplete"] if n not in result["blocked"]]
    files = {}
    for path in root.rglob("*"):
        if path.is_file() and path.name != "lane.json":
            if path.is_symlink():
                reasons.append("linked evidence prohibited")
            else:
                files[path.relative_to(root).as_posix()] = file_digest(path)
    return {
        "schema": "swinglens.release-lane.v1",
        "context": context(plan, lane),
        "job_result": job_result,
        "kind": spec["kind"],
        "results": result,
        "counts": {
            key: len(result[key])
            for key in ("selected", "collected", "executed", "body_executed", *CATEGORIES)
        },
        "artifacts": files,
        "reasons": reasons,
        "verdict": "NONCERTIFIED" if reasons else "CERTIFIED",
    }


def _partial_results(root: Path, plan: dict, lane: str, spec: dict, empty: dict) -> dict:
    result = {**empty, "evidence_complete": False}
    if spec["kind"] in {"node", "promql"}:
        result["selected"] = spec["case_ids"]
        result["incomplete"] = spec["case_ids"]
    elif spec["kind"] == "a04":
        try:
            report = read_json(root / "a04/report.json")
            result.update(
                {
                    key: report.get("invocation", {}).get(key, [])
                    for key in ("selected", "collected", "executed")
                }
            )
            result["incomplete"] = result["selected"]
            result["unverified_a04_references"] = report.get("certificates", [])
        except (EvidenceError, KeyError, TypeError, OSError):
            pass
    else:
        try:
            intended = read_json(root / "collection.json")
            if intended.get("context") == context(plan, lane):
                result["selected"] = intended["selected"]
                result["incomplete"] = intended["selected"]
            execution = read_json(root / "execution.json")
            if execution.get("context") == context(plan, lane):
                result["collected"] = execution["selected"]
                result["executed"] = execution["started"]
                result["body_executed"] = [
                    node for node, phases in execution["phases"].items() if "call" in phases
                ]
                result["process_exit_code"] = execution["exit_code"]
                result["environment"] = execution["environment"]
        except (EvidenceError, KeyError, TypeError, OSError):
            pass
    try:
        if spec["kind"] != "a04" and (root / "results.xml").is_file():
            try:
                raw = junit_cases(root / "results.xml", spec["kind"])
                result.update(
                    {
                        **{
                            key: [node for node, status in raw.items() if status == key]
                            for key in CATEGORIES
                        },
                    }
                )
                if spec["kind"] in {"node", "promql"}:
                    result["collected"] = result["executed"] = list(raw)
                    result["incomplete"] = [n for n in result["selected"] if n not in raw]
                if spec["kind"] == "pytest" and (root / "collection.json").is_file():
                    intended = read_json(root / "collection.json")
                    if intended.get("context") == context(plan, lane):
                        names = {junit_key(node): node for node in intended["selected"]}
                        raw = {
                            names.get(node, "UNDECLARED_JUNIT::" + node): status
                            for node, status in raw.items()
                        }
                        result.update(
                            {
                                **{
                                    key: [node for node, status in raw.items() if status == key]
                                    for key in CATEGORIES
                                },
                            }
                        )
                        result["selected"] = intended["selected"]
                        result["incomplete"] = [
                            node for node in intended["selected"] if node not in raw
                        ]
            except (EvidenceError, KeyError, TypeError, ValueError, OSError, ET.ParseError):
                pass
    except (EvidenceError, KeyError, TypeError, ValueError, OSError, ET.ParseError):
        pass
    if not result["selected"]:
        result["intended_selection_unknown"] = True
    return result


def record_lane(root: Path, plan: dict, lane: str, job_result: str) -> dict:
    validate_plan(plan, current=True)
    if (root / "lane.json").exists():
        raise EvidenceError("refuse to replace a terminal lane receipt")
    if (plan["required_lanes"][lane]["kind"] in {"node", "promql"}
        and not (root / "carry-forward.json").exists() and (root / "results.xml").is_file()):
        if (root / "native-file-time.json").exists():
            raise EvidenceError("refuse to replace original native report time")
        write_json(root / "native-file-time.json", {
            "context": context(plan, lane), "results_sha256": file_digest(root / "results.xml"),
            "mtime_ns": (root / "results.xml").stat().st_mtime_ns,
        })
    value = evaluate_lane(root, plan, lane, job_result)
    write_json(root / "lane.json", value)
    return value


def aggregate(plan: dict, root: Path, jobs: dict) -> dict:
    validate_plan(plan, current=True)
    reasons = []
    lanes = {}
    required_jobs = {"release-plan", *(row["job"] for row in plan["required_lanes"].values())}
    for name in sorted(required_jobs):
        if jobs.get(name, {}).get("result") != "success":
            reasons.append("required job missing/failed/skipped/cancelled: " + name)
    for name in plan["required_lanes"]:
        directory = root / name
        checked = None
        try:
            # Keep actual observed cases in a noncertified manifest too.
            checked = evaluate_lane(directory, plan, name, "unknown")
            receipt = read_json(directory / "lane.json")
            if receipt.get("context") != context(plan, name):
                raise EvidenceError("stale/substituted lane receipt")
            for relative, expected in receipt["artifacts"].items():
                if file_digest(safe_file(directory, relative)) != expected:
                    raise EvidenceError("artifact seal invalid: " + relative)
            checked = evaluate_lane(directory, plan, name, receipt["job_result"])
            if checked != receipt or checked["verdict"] != "CERTIFIED":
                raise EvidenceError("lane results are inconsistent or noncertified")
            lanes[name] = receipt
        except (EvidenceError, KeyError, TypeError, ValueError, OSError, ET.ParseError,
                subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            reasons.append(name + ": " + str(exc))
            lanes[name] = {
                **(checked or {}),
                "verdict": "NONCERTIFIED",
                "validation_error": str(exc),
            }
    verdict = "NONCERTIFIED" if reasons else "CERTIFIED"
    result = {
        "schema": "swinglens.full-release-certification.v1",
        "execution_id": plan["execution_id"],
        "attempt": plan["attempt"],
        "scope": "main CI full-release regressions; separate live promotion",
        "provider_mode": "deterministic",
        "git_sha": plan["source"]["revision"],
        "working_tree_fingerprint": plan["source"]["fingerprint"],
        "plan": plan,
        "plan_sha256": plan["plan_sha256"],
        "jobs": jobs,
        "lanes": lanes,
        "reasons": reasons,
        "verdict": verdict,
        "gates": {
            "FULL_RELEASE_REGRESSIONS": "PASS" if verdict == "CERTIFIED" else "FAIL",
            "EXACT_PIPELINE_CERTIFICATION": "PASS"
            if lanes.get("a04", {}).get("verdict") == "CERTIFIED"
            else "FAIL",
            "SMALL_LIVE_PROVIDER_CANARY": "NOT_RUN",
        },
        "safe_for_normal_run": False,
        "advisory_manual": {
            name: {"status": "NOT_RUN", **value} for name, value in plan["advisory_manual"].items()
        },
        "not_applicable": plan["not_applicable"],
    }
    if plan.get("profile") == "core":
        result.update(
            schema="swinglens.core-release-certification.v1",
            scope=(
                "Curated core and declared affected contracts; not full-release or live promotion"
            ),
            core_verdict="CORE_RELEASE_FAILED" if reasons else "CORE_RELEASE_CERTIFIED",
            selection_manifest=plan["selection_manifest"],
        )
        result["gates"]["CORE_RELEASE_REGRESSIONS"] = result["gates"]["FULL_RELEASE_REGRESSIONS"]
        result["gates"]["FULL_RELEASE_REGRESSIONS"] = "NOT_RUN"
        result["selection_manifest"] = {
            **plan["selection_manifest"],
            "source": plan["source"],
            "actual_lane_cases": {
                name: {
                    "process_exit_code": receipt.get("results", {}).get("process_exit_code"),
                    **{key: receipt.get("results", {}).get(key, []) for key in CATEGORIES},
                }
                for name, receipt in lanes.items()
            },
        }
        disposition = lanes.get("a04", {}).get("results", {}).get("conditional_pr_disposition")
        if disposition:
            deferred = {row["test_id"] for row in disposition["deferred"]}
            result["selection_manifest"]["conditional_pr_disposition"] = disposition
            result["selection_manifest"]["entries"] = [
                {**row, "selected": False, "outcome": "NOT_RUN",
                 "deferral_basis": disposition["rule"]}
                if row["exact_test_id"] in deferred else row
                for row in result["selection_manifest"]["entries"]
            ]
    return result
