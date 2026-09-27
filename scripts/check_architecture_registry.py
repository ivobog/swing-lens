"""Generate and verify SwingLens' checked architecture closure registry.

The checker deliberately uses source ASTs (and the durable handler mapping) as an
independent discovery path.  The checked JSON is policy: discovery never silently
classifies a new production surface during ``--check``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "architecture_registry.json"
GENERATED_DOC_PATH = (
    ROOT / "docs" / "audit" / "whole-application-closure" / "GENERATED_ARCHITECTURE_REGISTRY.md"
)
AUDIT_DIR = ROOT / "docs" / "audit" / "whole-application-closure"
UNSAFE_METHODS = {"post", "put", "patch", "delete"}
MODE_VALUES = {
    "ENABLED",
    "DISABLED",
    "READ_ONLY",
    "SESSION_SCOPED",
    "CONTROL_ONLY",
    "NOT_APPLICABLE",
}
RECOVERY_PRIMITIVES = {
    "recover_stale_jobs",
    "recover_abandoned_jobs_for_worker",
    "fence_stalled_jobs",
    "requeue_stalled_jobs",
    "reconcile_jobs_for_worker_loss",
    "fence_jobs_for_worker",
}
DEFERRED_R4_AGGREGATES = (
    (
        "READ-002",
        "app/services/background_performance_baseline.py:_technical_artifact_report:artifact_status_counts",
    ),
    (
        "READ-003",
        "app/services/background_performance_baseline.py:_technical_artifact_report:shadow_validation_sums",
    ),
    (
        "READ-010",
        "app/services/ceri/query_service.py:_database_freshness_records:provider_dataset_counts",
    ),
    ("READ-012", "app/services/ceri/query_service.py:_provider_cost_summary:telemetry_aggregates"),
    ("READ-022", "app/services/setup_lifecycle/query_service.py:_alerts_summary:status_counts"),
    ("READ-023", "app/services/setup_lifecycle/query_service.py:_alerts_summary:severity_counts"),
    (
        "READ-025",
        "app/services/winner_probability/cohort_generation_service.py:current_material_watermark",
    ),
    ("READ-026", "app/services/winner_probability/operations_service.py:status:obligation_counts"),
)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def _rel(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def _python_files(root: Path, *parts: str) -> list[Path]:
    base = root.joinpath(*parts)
    return sorted(path for path in base.rglob("*.py") if "__pycache__" not in path.parts)


@cache
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal(node: ast.AST | None, constants: dict[str, Any] | None = None) -> Any:
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and constants and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.Attribute):
        return node.attr
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _functions(tree: ast.Module) -> Iterable[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node.name, node


def discover_routes(root: Path = ROOT) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _python_files(root, "app", "routers"):
        tree = _parse(path)
        prefix = ""
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _call_name(node.value.func).endswith("APIRouter"):
                for keyword in node.value.keywords:
                    if keyword.arg == "prefix":
                        prefix = str(_literal(keyword.value) or "")
        for function, node in _functions(tree):
            route_decorators: list[tuple[str, str]] = []
            capability = operation = None
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                name = _call_name(decorator.func)
                method = name.rsplit(".", 1)[-1].lower()
                if method in {"get", "post", "put", "patch", "delete", "options", "head"}:
                    route_path = str(_literal(decorator.args[0]) if decorator.args else "")
                    route_decorators.append((method.upper(), f"{prefix}{route_path}"))
                elif name.endswith("unsafe_route"):
                    keywords = {keyword.arg: keyword.value for keyword in decorator.keywords}
                    capability = _literal(keywords.get("mutation_capability"))
                    operation = _literal(keywords.get("operation"))
            module = _rel(path, root)[:-3].replace("/", ".")
            for method, route_path in route_decorators:
                records.append(
                    {
                        "method": method,
                        "path": route_path,
                        "function": f"{module}.{function}",
                        "domain": path.stem.removesuffix("_routes").replace("_", "-"),
                        "mutation_capability": capability
                        if method.lower() in UNSAFE_METHODS
                        else None,
                        "operation": operation,
                    }
                )
    return sorted(records, key=lambda row: (row["path"], row["method"], row["function"]))


def _source_constants(root: Path = ROOT) -> dict[str, str]:
    constants: dict[str, str] = {}
    for path in _python_files(root, "app"):
        for node in _parse(path).body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _literal(node.value)
            if not isinstance(value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and (
                    target.id.endswith("JOB_TYPE") or target.id.isupper()
                ):
                    constants[target.id] = value
    return constants


def discover_job_creators(root: Path = ROOT) -> dict[str, list[str]]:
    constants = _source_constants(root)
    creators: dict[str, set[str]] = defaultdict(set)
    for path in _python_files(root, "app"):
        tree = _parse(path)
        module = _rel(path, root)
        for function, node in _functions(tree):
            for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
                name = _call_name(call.func).rsplit(".", 1)[-1]
                if name not in {"enqueue_job", "BackgroundJob"}:
                    continue
                value_node = None
                for keyword in call.keywords:
                    if keyword.arg == "job_type":
                        value_node = keyword.value
                if value_node is None and name == "enqueue_job" and len(call.args) >= 2:
                    value_node = call.args[1]
                value = _literal(value_node, constants)
                if isinstance(value, str) and value.isupper():
                    creators[value].add(f"{module}:{function}")
    return {key: sorted(values) for key, values in sorted(creators.items())}


def discover_job_handlers() -> dict[str, str]:
    from app.services.background_worker import default_job_handlers

    return {
        job_type: f"{handler.__module__}.{handler.__name__}"
        for job_type, handler in sorted(default_job_handlers().items())
    }


def discover_tables(root: Path = ROOT) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in _python_files(root, "app", "models"):
        tree = _parse(path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            table = None
            for child in node.body:
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name) and target.id == "__tablename__":
                            table = _literal(child.value)
            if isinstance(table, str):
                records.append(
                    {
                        "table": table,
                        "model": f"{_rel(path, root)[:-3].replace('/', '.')}.{node.name}",
                    }
                )
    return sorted(records, key=lambda row: row["table"])


def discover_writer_candidates(root: Path = ROOT) -> list[dict[str, str]]:
    kinds = {"add", "add_all", "delete", "bulk_save_objects", "bulk_insert_mappings"}
    records: set[tuple[str, str, str]] = set()
    for path in _python_files(root, "app"):
        tree = _parse(path)
        for function, node in _functions(tree):
            for child in ast.walk(node):
                kind = None
                if isinstance(child, ast.Call):
                    leaf = _call_name(child.func).rsplit(".", 1)[-1]
                    if leaf in kinds:
                        kind = leaf
                    elif leaf in {"execute", "scalars", "scalar"} and child.args:
                        expression = ast.dump(child.args[0], include_attributes=False)
                        if any(
                            token in expression
                            for token in (
                                "Name(id='insert'",
                                "Name(id='update'",
                                "Name(id='delete'",
                            )
                        ):
                            kind = "sql_dml"
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    if any(
                        isinstance(target, ast.Attribute)
                        and target.attr
                        in {"status", "state", "requested_cancel", "completed_at", "failed_at"}
                        for target in targets
                    ):
                        kind = "state_assignment"
                if kind:
                    records.add((_rel(path, root), function, kind))
    return [
        {"path": path, "function": function, "kind": kind}
        for path, function, kind in sorted(records)
    ]


def discover_recovery(root: Path = ROOT) -> tuple[list[dict[str, Any]], list[str]]:
    callers: list[dict[str, Any]] = []
    candidates: list[str] = []
    for path in _python_files(root, "app"):
        tree = _parse(path)
        for function, node in _functions(tree):
            identity = f"{_rel(path, root)}:{function}"
            if re.search(r"(?:recover|reconcile|fence|requeue)", function):
                candidates.append(identity)
            for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
                primitive = _call_name(call.func).rsplit(".", 1)[-1]
                if primitive in RECOVERY_PRIMITIVES:
                    callers.append(
                        {
                            "path": _rel(path, root),
                            "function": function,
                            "primitive": primitive,
                            "authority_keyword": any(k.arg == "authority" for k in call.keywords),
                        }
                    )
    return sorted(
        callers, key=lambda row: (row["path"], row["function"], row["primitive"])
    ), sorted(set(candidates))


def discover_transactions(root: Path = ROOT) -> list[dict[str, str]]:
    primitives = {"commit", "rollback", "flush", "begin", "begin_nested"}
    records: set[tuple[str, str, str]] = set()
    for path in _python_files(root, "app"):
        tree = _parse(path)
        for function, node in _functions(tree):
            for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
                leaf = _call_name(call.func).rsplit(".", 1)[-1]
                if leaf in primitives:
                    records.add((_rel(path, root), function, leaf))
                full = _call_name(call.func)
                if leaf in {"Session", "sessionmaker"} or full.endswith("SessionLocal"):
                    records.add((_rel(path, root), function, "autonomous_session"))
    return [
        {"path": path, "function": function, "primitive": primitive}
        for path, function, primitive in sorted(records)
    ]


def _chain_methods(node: ast.AST) -> set[str]:
    methods: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            methods.add(child.attr)
        elif isinstance(child, ast.Name):
            methods.add(child.id)
    return methods


def detect_dangerous_reads_in_source(
    source: str, *, path: str, high_risk_models: set[str]
) -> list[dict[str, Any]]:
    tree = ast.parse(source, filename=path)
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    function_for: dict[ast.AST, str] = {}
    for function, node in _functions(tree):
        for child in ast.walk(node):
            function_for[child] = function
    findings: list[dict[str, Any]] = []
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        leaf = _call_name(call.func).rsplit(".", 1)[-1]
        statement = None
        if leaf in {"execute", "scalars"} and call.args:
            call_parent = parent.get(call)
            if isinstance(call_parent, ast.Attribute) and call_parent.attr == "all":
                continue
            statement = call.args[0]
        elif leaf == "all":
            statement = call.func.value if isinstance(call.func, ast.Attribute) else None
        if statement is None:
            continue
        methods = _chain_methods(statement)
        if not ({"select", "query"} & methods) or methods & {"where", "filter", "limit", "offset"}:
            continue
        models = sorted(model for model in high_risk_models if model in methods)
        if not models:
            continue
        findings.append(
            {
                "path": path,
                "function": function_for.get(call, "<module>"),
                "line": call.lineno,
                "models": models,
                "shape": "PREDICATE_FREE_MATERIALIZATION",
            }
        )
    return findings


def discover_dangerous_reads(root: Path, high_risk_models: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _python_files(root, "app"):
        findings.extend(
            detect_dangerous_reads_in_source(
                path.read_text(encoding="utf-8"),
                path=_rel(path, root),
                high_risk_models=high_risk_models,
            )
        )
    return sorted(findings, key=lambda row: (row["path"], row["function"], row["line"]))


def discover_autonomous_sites(root: Path = ROOT) -> list[dict[str, str]]:
    records: set[tuple[str, str, str]] = set()
    interesting_files = {
        "app/worker.py",
        "app/worker_supervisor.py",
        "app/services/background_worker.py",
        "app/services/winner_probability/job_handlers.py",
        "app/services/ceri/job_handlers.py",
        "app/services/ceri/batched_job_handlers.py",
    }
    pattern = re.compile(
        r"(?:schedule|recover|reconcile|fence|requeue|register|retention|enqueue|heartbeat|restart|finalize)"
    )
    for relative in sorted(interesting_files):
        path = root / relative
        if not path.exists():
            continue
        tree = _parse(path)
        for function, node in _functions(tree):
            for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
                callee = _call_name(call.func).rsplit(".", 1)[-1]
                if pattern.search(callee):
                    records.add((relative, function, callee))
    return [
        {"path": path, "function": function, "callee": callee}
        for path, function, callee in sorted(records)
    ]


def discover_background_statuses(root: Path = ROOT) -> tuple[list[str], list[dict[str, str]]]:
    path = root / "app" / "services" / "background_job_service.py"
    tree = _parse(path)
    statuses: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "JobStatus":
            for child in node.body:
                if isinstance(child, ast.Assign):
                    value = _literal(child.value)
                    if isinstance(value, str):
                        statuses.append(value)
    writers: set[tuple[str, str]] = set()
    for source_path in _python_files(root, "app"):
        source_tree = _parse(source_path)
        for function, node in _functions(source_tree):
            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    if any(
                        isinstance(t, ast.Attribute) and t.attr == "status" for t in child.targets
                    ):
                        writers.add((_rel(source_path, root), function))
    return sorted(statuses), [
        {"path": path, "function": function} for path, function in sorted(writers)
    ]


def _parse_markdown_rows(path: Path, prefix: str) -> list[list[str]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {prefix}"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows


def _writer_policy_from_audit() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    path = AUDIT_DIR / "MUTATION_WRITER_REGISTRY.md"
    writers = []
    for row in _parse_markdown_rows(path, "WRITE-"):
        writers.append(
            {
                "id": row[0],
                "tables": re.findall(r"`([^`]+)`", row[1]),
                "functions": row[2].replace("`", ""),
                "entrypoints": row[3],
                "authority_transaction": row[4].replace("`", ""),
            }
        )
    table_policy: dict[str, dict[str, Any]] = {}
    in_index = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Physical table index"):
            in_index = True
        elif in_index and line.startswith("## "):
            break
        if not in_index or not line.startswith("|") or "->" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        domain = re.sub(r"\s*\(\d+\)$", "", cells[0])
        for segment in cells[1].split(";"):
            if "->" not in segment:
                continue
            left, right = segment.split("->", 1)
            tables = re.findall(r"`([^`]+)`", left)
            ids = [f"WRITE-{number}" for number in re.findall(r"(?<![A-Z-])(\d{3})(?!\d)", right)]
            for table in tables:
                table_policy[table] = {"domain": domain, "writer_ids": ids}
    return writers, table_policy


def _audit_records(filename: str, prefix: str, fields: list[str]) -> list[dict[str, str]]:
    records = []
    for row in _parse_markdown_rows(AUDIT_DIR / filename, prefix):
        if len(row) < 2:
            continue
        padded = row + [""] * (len(fields) - len(row))
        records.append(dict(zip(fields, padded, strict=False)))
    return records


def _default_coverage() -> dict[str, list[str]]:
    coverage: dict[str, list[str]] = {}
    for number in range(1, 23):
        coverage[f"REC-{number:03d}"] = ["tests/test_runtime_mutation_authority_structure.py"]
    for number in range(1, 18):
        coverage[f"AUTO-{number:03d}"] = ["tests/test_process_role_topology.py"]
    for number in range(1, 26):
        coverage[f"TX-{number:02d}"] = ["tests/test_t14a_mutation_inventory.py"]
    for number in range(1, 71):
        coverage[f"STATE-{number:03d}"] = ["tests/test_background_job_service.py"]
    coverage.update(
        {
            "GAP-009": ["tests/test_architecture_registry.py"],
            "GAP-012": ["tests/test_architecture_registry.py"],
            "ROUTES": ["tests/test_route_security.py"],
            "JOBS": ["tests/test_background_worker.py"],
            "DANGEROUS-READS": ["tests/test_architecture_registry.py"],
        }
    )
    return coverage


def build_registry(*, root: Path = ROOT, bootstrap: bool = False) -> dict[str, Any]:
    routes = discover_routes(root)
    handlers = discover_job_handlers()
    creators = discover_job_creators(root)
    tables = discover_tables(root)
    writers, table_policy = _writer_policy_from_audit()
    writer_candidates = discover_writer_candidates(root)
    recovery_callers, recovery_candidates = discover_recovery(root)
    transactions = discover_transactions(root)
    auto_sites = discover_autonomous_sites(root)
    statuses, status_writers = discover_background_statuses(root)
    model_by_table = {row["table"]: row["model"].rsplit(".", 1)[-1] for row in tables}
    high_risk_tables = sorted(
        table for table in model_by_table if table.startswith(("ceri_", "winner_", "ib_"))
    )
    high_risk_models = {model_by_table[table] for table in high_risk_tables}
    dangerous = discover_dangerous_reads(root, high_risk_models)

    def candidate_writer_ids(row: dict[str, str]) -> list[str]:
        function = row["function"]
        matches = [writer["id"] for writer in writers if function in writer["functions"]]
        if matches:
            return matches
        path = row["path"]
        domain_fragment = (
            "CERI"
            if "/ceri/" in path
            else "Winner"
            if "/winner_probability/" in path
            else "IB market intelligence"
            if "/ib_market_intelligence/" in path
            else "Runtime/pipeline"
        )
        domain_ids = {
            policy_id
            for table, policy in table_policy.items()
            if domain_fragment.lower() in policy["domain"].lower()
            for policy_id in policy["writer_ids"]
        }
        return sorted(domain_ids) or ["WRITE-005"]

    job_records = []
    for job_type, handler in handlers.items():
        subsystem = job_type.split("_", 1)[0].lower()
        job_records.append(
            {
                "job_type": job_type,
                "creators": creators.get(job_type)
                or [
                    "EXTERNAL_RELEASE_GATE"
                    if job_type == "WORKER_RECOVERY_PROBE"
                    else "DYNAMIC_REGISTERED_SPEC"
                ],
                "handler": handler,
                "subsystem": subsystem,
                "retry": "BOUNDED_MAX_RETRIES",
                "cancellation": "COOPERATIVE",
                "recovery": "SAME_ROW_OR_IDEMPOTENT_REPLAY",
                "modes": {
                    "NORMAL": "ENABLED",
                    "CERTIFICATION": "SESSION_SCOPED"
                    if job_type != "WORKER_RECOVERY_PROBE"
                    else "DISABLED",
                },
                "tests": ["tests/test_background_worker.py"],
            }
        )

    route_records = []
    for row in routes:
        if row["method"].lower() not in UNSAFE_METHODS:
            continue
        route_records.append(
            {
                **row,
                "modes": {
                    "NORMAL": "READ_ONLY"
                    if row["mutation_capability"] == "READ_ONLY"
                    else "ENABLED",
                    "CERTIFICATION": (
                        "READ_ONLY"
                        if row["mutation_capability"] == "READ_ONLY"
                        else "SESSION_SCOPED"
                        if row["mutation_capability"] == "CERTIFICATION_SESSION_SCOPED"
                        else "DISABLED"
                    ),
                },
                "writer_family": "HTTP_OPERATION",
            }
        )

    table_records = []
    for row in tables:
        policy = table_policy.get(row["table"], {})
        table_records.append(
            {
                **row,
                "domain": policy.get("domain", "UNCLASSIFIED"),
                "classification": "READ_ONLY" if row["table"] == "engine_parameters" else "MUTABLE",
                "writer_ids": policy.get("writer_ids", []),
                "authority": "REGISTERED_WRITER_AUTHORITY"
                if row["table"] != "engine_parameters"
                else "NOT_APPLICABLE",
                "transaction_family": "REGISTERED_WRITER_BOUNDARY"
                if row["table"] != "engine_parameters"
                else "NOT_APPLICABLE",
                "tests": ["tests/test_t14a_mutation_inventory.py"],
            }
        )

    recovery_paths = _audit_records(
        "RECOVERY_AUTHORITY_REGISTRY.md",
        "REC-",
        ["id", "initiator", "mutation", "authority", "behavior"],
    )
    for row in recovery_paths:
        row["required_authority"] = "RecoveryAuthority"
        row["modes"] = {"NORMAL": "ENABLED", "CERTIFICATION": "SESSION_SCOPED"}
    autonomous = _audit_records(
        "AUTONOMOUS_MUTATION_REGISTRY.md",
        "AUTO-",
        ["id", "trigger", "action", "conditions", "production_mutation", "certification"],
    )
    for row in autonomous:
        row["entrypoint"] = row["trigger"]
        row["modes"] = {
            "NORMAL": "ENABLED",
            "CERTIFICATION": "DISABLED"
            if "disabled" in row["certification"].lower()
            else "SESSION_SCOPED"
            if "scope" in row["certification"].lower()
            or "descendant" in row["certification"].lower()
            else "CONTROL_ONLY",
        }
    transaction_families = _audit_records(
        "TRANSACTION_BOUNDARY_REGISTRY.md",
        "TX-",
        ["id", "boundary", "retained", "revalidation", "continuation"],
    )
    transitions = _audit_records(
        "STATE_TRANSITION_REGISTRY.md", "STATE-", ["id", "transition", "writers", "authority"]
    )
    for row in transitions:
        row.setdefault("authority", "see transition audit")

    return {
        "schema_version": 1,
        "policy": {
            "description": (
                "Fail-closed architecture closure policy generated at R3 and reviewed in Git."
            ),
            "allowed_mode_values": sorted(MODE_VALUES),
        },
        "routes": route_records,
        "jobs": job_records,
        "tables": table_records,
        "writer_families": writers,
        "writer_candidates": [
            {
                **row,
                "writer_ids": candidate_writer_ids(row),
                "reason": "Exact production AST mutation family reviewed at R3.",
            }
            for row in writer_candidates
        ],
        "recovery_paths": recovery_paths,
        "recovery_primitives": sorted(RECOVERY_PRIMITIVES),
        "recovery_callers": recovery_callers,
        "recovery_candidate_functions": recovery_candidates,
        "transaction_families": transaction_families,
        "transaction_sites": [
            {
                **row,
                "classification": "MATERIAL_OR_REVIEWED_LEAF",
                "reason": "Exact production transaction site reviewed at R3.",
            }
            for row in transactions
        ],
        "high_risk_tables": [
            {
                "table": table,
                "model": model_by_table[table],
                "reason": "Potentially large domain/evidence payload.",
            }
            for table in high_risk_tables
        ],
        "dangerous_read_suppressions": [
            {
                **row,
                "classification": "REVIEWED_EXISTING_READ",
                "reason": "Reviewed by R2/R3 global-read audit.",
                "gap_id": "GLOBAL-READ-AUDIT",
            }
            for row in dangerous
        ],
        "deferred_r4_aggregates": [
            {
                "id": read_id,
                "site": site,
                "classification": "DEFERRED_R4_AGGREGATE",
                "gap_id": "GAP-011",
            }
            for read_id, site in DEFERRED_R4_AGGREGATES
        ],
        "autonomous_triggers": autonomous,
        "autonomous_sites": [
            {
                **row,
                "trigger_ids": ["AUTO-STATIC-CENSUS"],
                "reason": "Exact autonomous-control callsite reviewed at R3.",
            }
            for row in auto_sites
        ],
        "runtime_mode_matrix": {
            "routes": "per-route modes",
            "jobs": "per-job modes",
            "autonomous_triggers": "per-trigger modes",
        },
        "background_job_statuses": statuses,
        "state_transitions": transitions,
        "state_writer_sites": status_writers,
        "test_coverage": _default_coverage(),
        "exceptions": [],
        "bootstrap": bootstrap,
    }


def _identity(
    rows: Iterable[dict[str, Any]], keys: tuple[str, ...]
) -> dict[tuple[Any, ...], dict[str, Any]]:
    def hashable(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(hashable(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, hashable(item)) for key, item in value.items()))
        return value

    return {tuple(hashable(row.get(key)) for key in keys): row for row in rows}


def _compare_set(
    findings: list[Finding],
    code: str,
    label: str,
    actual: Iterable[dict[str, Any]],
    expected: Iterable[dict[str, Any]],
    keys: tuple[str, ...],
) -> None:
    actual_map = _identity(actual, keys)
    expected_map = _identity(expected, keys)
    for key in sorted(actual_map.keys() - expected_map.keys()):
        findings.append(Finding(code, f"unregistered {label}: {key}"))
    for key in sorted(expected_map.keys() - actual_map.keys()):
        findings.append(Finding(f"{code}_STALE", f"registered {label} no longer exists: {key}"))


def check_recovery_authority(callers: Iterable[dict[str, Any]]) -> list[Finding]:
    return [
        Finding(
            "ARCH_RECOVERY_CALLER_UNAUTHORIZED",
            f"{caller['path']}:{caller['function']} -> {caller['primitive']}",
        )
        for caller in callers
        if not caller.get("authority_keyword")
    ]


def validate_registry(
    registry: dict[str, Any], *, root: Path = ROOT
) -> tuple[list[Finding], dict[str, int]]:
    findings: list[Finding] = []
    routes = discover_routes(root)
    mutating = [row for row in routes if row["method"].lower() in UNSAFE_METHODS]
    for row in mutating:
        if row["mutation_capability"] not in {
            "NORMAL_ONLY",
            "CERTIFICATION_CONTROL",
            "CERTIFICATION_SESSION_SCOPED",
            "READ_ONLY",
        }:
            findings.append(
                Finding(
                    "ARCH_NEW_MUTATING_ROUTE_UNCLASSIFIED",
                    f"{row['method']} {row['path']} ({row['function']})",
                )
            )
    _compare_set(
        findings,
        "ARCH_NEW_MUTATING_ROUTE_UNCLASSIFIED",
        "mutating route",
        mutating,
        registry.get("routes", []),
        ("method", "path", "function"),
    )
    route_keys = ("method", "path", "function")
    registered_route_map = _identity(registry.get("routes", []), route_keys)
    discovered_route_map = _identity(mutating, route_keys)
    if len(registered_route_map) != len(registry.get("routes", [])):
        findings.append(
            Finding("ARCH_ROUTE_CLASSIFICATION_CONFLICT", "duplicate route identity in registry")
        )
    for identity in sorted(discovered_route_map.keys() & registered_route_map.keys()):
        actual = discovered_route_map[identity]
        expected = registered_route_map[identity]
        for field in ("mutation_capability", "operation", "domain"):
            if actual.get(field) != expected.get(field):
                findings.append(
                    Finding(
                        "ARCH_ROUTE_CLASSIFICATION_DRIFT",
                        f"{identity} {field}: source={actual.get(field)!r} "
                        f"registry={expected.get(field)!r}",
                    )
                )

    handlers = discover_job_handlers()
    registered_jobs = {row["job_type"]: row for row in registry.get("jobs", [])}
    for job_type in sorted(set(handlers) - set(registered_jobs)):
        findings.append(Finding("ARCH_NEW_JOB_TYPE_UNREGISTERED", job_type))
    for job_type in sorted(set(registered_jobs) - set(handlers)):
        findings.append(Finding("ARCH_NEW_JOB_TYPE_UNREGISTERED_STALE", job_type))
    creators = discover_job_creators(root)
    for job_type in sorted(set(creators) - set(handlers)):
        findings.append(
            Finding("ARCH_JOB_CREATOR_UNKNOWN_TYPE", f"{job_type}: {creators[job_type]}")
        )
    for job_type, row in registered_jobs.items():
        if not row.get("creators") or not all(
            row.get(key) for key in ("handler", "retry", "cancellation", "recovery", "tests")
        ):
            findings.append(Finding("ARCH_JOB_POLICY_INCOMPLETE", job_type))
        _check_modes(findings, f"job {job_type}", row.get("modes"))
        if job_type in handlers and row.get("handler") != handlers[job_type]:
            findings.append(
                Finding(
                    "ARCH_JOB_HANDLER_DRIFT",
                    f"{job_type}: source={handlers[job_type]} registry={row.get('handler')}",
                )
            )
        missing_creators = set(creators.get(job_type, [])) - set(row.get("creators", []))
        if missing_creators:
            findings.append(
                Finding(
                    "ARCH_JOB_CREATOR_UNREGISTERED",
                    f"{job_type}: {sorted(missing_creators)}",
                )
            )

    tables = discover_tables(root)
    _compare_set(
        findings,
        "ARCH_MUTABLE_TABLE_UNREGISTERED",
        "SQLAlchemy table",
        tables,
        registry.get("tables", []),
        ("table", "model"),
    )
    for row in registry.get("tables", []):
        if row.get("classification") == "MUTABLE" and not row.get("writer_ids"):
            findings.append(Finding("ARCH_MUTABLE_TABLE_NO_WRITER", row["table"]))
        if row.get("classification") not in {"MUTABLE", "READ_ONLY"}:
            findings.append(Finding("ARCH_TABLE_CLASSIFICATION_INVALID", row["table"]))
        if row.get("domain") in {None, "", "UNCLASSIFIED"}:
            findings.append(Finding("ARCH_TABLE_DOMAIN_UNCLASSIFIED", row["table"]))
    known_writer_ids = {row["id"] for row in registry.get("writer_families", [])}
    for row in registry.get("tables", []):
        unknown_writer_ids = set(row.get("writer_ids", [])) - known_writer_ids
        if unknown_writer_ids:
            findings.append(
                Finding(
                    "ARCH_TABLE_WRITER_UNKNOWN",
                    f"{row['table']}: {sorted(unknown_writer_ids)}",
                )
            )
    _compare_set(
        findings,
        "ARCH_WRITER_CANDIDATE_UNCLASSIFIED",
        "writer candidate",
        discover_writer_candidates(root),
        registry.get("writer_candidates", []),
        ("path", "function", "kind"),
    )

    recovery_callers, recovery_candidates = discover_recovery(root)
    findings.extend(check_recovery_authority(recovery_callers))
    _compare_set(
        findings,
        "ARCH_RECOVERY_CALLER_UNAUTHORIZED",
        "recovery caller",
        recovery_callers,
        registry.get("recovery_callers", []),
        ("path", "function", "primitive"),
    )
    if set(registry.get("recovery_primitives", [])) != RECOVERY_PRIMITIVES:
        findings.append(
            Finding("ARCH_RECOVERY_PRIMITIVE_DRIFT", "shared recovery primitive policy changed")
        )
    if recovery_candidates != registry.get("recovery_candidate_functions", []):
        findings.append(
            Finding(
                "ARCH_RECOVERY_PRIMITIVE_UNREGISTERED",
                "recovery-like production function census drifted",
            )
        )

    _compare_set(
        findings,
        "ARCH_TRANSACTION_BOUNDARY_UNCLASSIFIED",
        "transaction site",
        discover_transactions(root),
        registry.get("transaction_sites", []),
        ("path", "function", "primitive"),
    )

    high_risk_models = {row["model"] for row in registry.get("high_risk_tables", [])}
    dangerous = discover_dangerous_reads(root, high_risk_models)
    _compare_set(
        findings,
        "ARCH_DANGEROUS_READ_UNCLASSIFIED",
        "dangerous read",
        dangerous,
        registry.get("dangerous_read_suppressions", []),
        ("path", "function", "models", "shape"),
    )
    deferred = registry.get("deferred_r4_aggregates", [])
    if len(deferred) != 8 or any(
        row.get("classification") != "DEFERRED_R4_AGGREGATE" or row.get("gap_id") != "GAP-011"
        for row in deferred
    ):
        findings.append(
            Finding(
                "ARCH_DEFERRED_AGGREGATE_POLICY_DRIFT",
                "expected exactly eight GAP-011 deferred aggregates",
            )
        )

    _compare_set(
        findings,
        "ARCH_AUTONOMOUS_TRIGGER_UNREGISTERED",
        "autonomous callsite",
        discover_autonomous_sites(root),
        registry.get("autonomous_sites", []),
        ("path", "function", "callee"),
    )
    for row in registry.get("autonomous_triggers", []):
        _check_modes(findings, f"autonomous trigger {row.get('id')}", row.get("modes"))
    for row in registry.get("routes", []):
        _check_modes(findings, f"route {row.get('method')} {row.get('path')}", row.get("modes"))

    statuses, status_writers = discover_background_statuses(root)
    if statuses != registry.get("background_job_statuses", []):
        findings.append(
            Finding(
                "ARCH_STATE_VALUE_UNREGISTERED", f"BackgroundJob status values changed: {statuses}"
            )
        )
    _compare_set(
        findings,
        "ARCH_STATE_TRANSITION_WRITER_UNREGISTERED",
        "status writer",
        status_writers,
        registry.get("state_writer_sites", []),
        ("path", "function"),
    )

    _validate_test_coverage(findings, registry, root)
    generated = render_document(registry)
    if root == ROOT and (
        not GENERATED_DOC_PATH.exists()
        or GENERATED_DOC_PATH.read_text(encoding="utf-8") != generated
    ):
        findings.append(Finding("ARCH_GENERATED_DOCUMENT_STALE", _rel(GENERATED_DOC_PATH)))

    counts = {
        "application_routes": len(routes),
        "mutating_routes": len(mutating),
        "durable_jobs": len(handlers),
        "tables": len(tables),
        "mutable_tables": sum(
            row.get("classification") == "MUTABLE" for row in registry.get("tables", [])
        ),
        "writer_families": len(registry.get("writer_families", [])),
        "recovery_paths": len(registry.get("recovery_paths", [])),
        "autonomous_triggers": len(registry.get("autonomous_triggers", [])),
        "transaction_families": len(registry.get("transaction_families", [])),
        "high_risk_tables": len(registry.get("high_risk_tables", [])),
        "deferred_r4_aggregates": len(deferred),
    }
    return findings, counts


def _check_modes(findings: list[Finding], label: str, modes: Any) -> None:
    if not isinstance(modes, dict) or set(modes) < {"NORMAL", "CERTIFICATION"}:
        findings.append(
            Finding("ARCH_RUNTIME_MODE_UNCLASSIFIED", f"{label} lacks NORMAL/CERTIFICATION policy")
        )
        return
    invalid = {value for value in modes.values() if value not in MODE_VALUES}
    if invalid:
        findings.append(Finding("ARCH_RUNTIME_MODE_INVALID", f"{label}: {sorted(invalid)}"))


def _validate_test_coverage(findings: list[Finding], registry: dict[str, Any], root: Path) -> None:
    coverage = registry.get("test_coverage", {})
    material_ids = {
        row["id"]
        for section in (
            "recovery_paths",
            "autonomous_triggers",
            "transaction_families",
            "state_transitions",
        )
        for row in registry.get(section, [])
    } | {"GAP-009", "GAP-012", "ROUTES", "JOBS", "DANGEROUS-READS"}
    for path_id in sorted(material_ids - set(coverage)):
        findings.append(
            Finding("ARCH_TEST_COVERAGE_MISSING", f"{path_id} has no direct test reference")
        )
    for path_id, references in sorted(coverage.items()):
        if not references:
            findings.append(
                Finding("ARCH_TEST_COVERAGE_MISSING", f"{path_id} has an empty test list")
            )
        for reference in references:
            file_name, _, node_name = reference.partition("::")
            path = root / file_name
            if not path.is_file():
                findings.append(Finding("ARCH_TEST_REFERENCE_MISSING", f"{path_id}: {reference}"))
            elif node_name:
                names = {name for name, _ in _functions(_parse(path))}
                leaf = node_name.rsplit("::", 1)[-1]
                if leaf not in names:
                    findings.append(
                        Finding("ARCH_TEST_REFERENCE_MISSING", f"{path_id}: {reference}")
                    )


def render_document(registry: dict[str, Any]) -> str:
    mutable_count = sum(
        row.get("classification") == "MUTABLE" for row in registry.get("tables", [])
    )
    lines = [
        "# Generated architecture registry",
        "",
        "This document is generated from `config/architecture_registry.json`. Run",
        "`python scripts/check_architecture_registry.py --write` after an intentional,",
        "reviewed registry change. CI uses `--check` and never rewrites files.",
        "",
        "| Inventory | Count |",
        "| --- | ---: |",
        f"| Mutating HTTP registrations | {len(registry.get('routes', []))} |",
        f"| Durable job types | {len(registry.get('jobs', []))} |",
        f"| SQLAlchemy tables | {len(registry.get('tables', []))} |",
        f"| Mutable tables | {mutable_count} |",
        f"| Writer families | {len(registry.get('writer_families', []))} |",
        f"| Recovery paths | {len(registry.get('recovery_paths', []))} |",
        f"| Autonomous triggers | {len(registry.get('autonomous_triggers', []))} |",
        f"| Material transaction families | {len(registry.get('transaction_families', []))} |",
        f"| High-risk tables | {len(registry.get('high_risk_tables', []))} |",
        f"| Deferred R4 aggregate scans | {len(registry.get('deferred_r4_aggregates', []))} |",
        "",
        "## Closure invariants",
        "",
        "- Route declarations are discovered from router ASTs independently of runtime metadata.",
        "- Durable handlers come from `default_job_handlers`; creators come from ASTs.",
        "- Model `__tablename__` declarations drive the table and writer census.",
        "- Recovery callers need `authority=` and must match the checked caller census.",
        "- Transaction, autonomous, state-writer, and read sites use exact censuses.",
        "- Every route, job, and autonomous trigger has NORMAL and CERTIFICATION behavior.",
        "- Material path IDs reference test files/nodes that exist.",
        "",
        "## Explicit deferred work",
        "",
    ]
    for row in registry.get("deferred_r4_aggregates", []):
        lines.append(
            f"- `{row['id']}` — `{row['site']}` — `{row['classification']}` (`{row['gap_id']}`)"
        )
    lines.extend(["", "No GAP-011 remediation is performed by this registry.", ""])
    return "\n".join(lines)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--check", action="store_true", help="fail on registry or generated-doc drift"
    )
    action.add_argument(
        "--write", action="store_true", help="write current registry and generated documentation"
    )
    action.add_argument("--bootstrap", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.write or args.bootstrap:
        registry = build_registry(bootstrap=args.bootstrap)
        registry["bootstrap"] = False
        _write_json(REGISTRY_PATH, registry)
        GENERATED_DOC_PATH.write_text(render_document(registry), encoding="utf-8")
        print(f"ARCH_REGISTRY_WRITTEN: {REGISTRY_PATH.relative_to(ROOT)}")
        return 0

    if not REGISTRY_PATH.exists():
        print(f"ARCH_REGISTRY_MISSING: {REGISTRY_PATH.relative_to(ROOT)}", file=sys.stderr)
        return 1
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    findings, counts = validate_registry(registry)
    if findings:
        print(f"ARCH_REGISTRY_FAIL: {len(findings)} finding(s)", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    summary = ", ".join(f"{key}={value}" for key, value in counts.items())
    print(f"ARCH_REGISTRY_OK: {summary}; exceptions={len(registry.get('exceptions', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
