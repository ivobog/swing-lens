"""Deterministic discovery census. Reviewed authority is separate from syntax hits.

No application startup, database connection, environment reads, or SQL execution.
Static call edges are candidates, not proof of runtime reachability.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MECHANISMS = {
    "add",
    "add_all",
    "merge",
    "delete",
    "commit",
    "flush",
    "execute",
    "scalar",
    "scalars",
    "executemany",
    "bulk_insert_mappings",
    "bulk_update_mappings",
    "bulk_save_objects",
    "update",
    "insert",
    "upsert",
    "save",
}
CAMPAIGN = re.compile(
    r"repair|replay|rebuild|refresh|recalculat|recomput|backfill|bootstrap|recover|resume|"
    r"restore|resync|mature|maturation|rescore|capture|finalize|persist|publish|advance|replace",
    re.I,
)
SQL_MUTATION = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE|COPY)\b", re.I)


def scoped_nodes(node):
    """Descend this scope, excluding nested functions/classes (counted separately)."""
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from scoped_nodes(child)


def discover(root=ROOT):
    paths = sorted(
        path
        for directory in ("app", "scripts", "alembic")
        for path in (root / directory).rglob("*.py")
    )
    models, functions, files, module_sites = {}, [], [], []
    parsed = []
    for path in paths:
        source = path.read_text(encoding="utf-8-sig")
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(source)
        for node in scoped_nodes(tree):
            if isinstance(node, ast.Call):
                expression = ast.unparse(node.func)
                if expression.rsplit(".", 1)[-1] in MECHANISMS or expression.startswith("event."):
                    module_sites.append(
                        {
                            "path": relative,
                            "line": node.lineno,
                            "expression": ast.unparse(node)[:300],
                        }
                    )
        parsed.append((relative, source, tree))
        files.append({"path": relative, "sha256": hashlib.sha256(source.encode()).hexdigest()})
        if relative.startswith("app/models/"):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "__tablename__"
                            for t in item.targets
                        ):
                            models[node.name] = item.value.value

    returns, return_candidates = {}, {}
    for relative, _, tree in parsed:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
                refs = {
                    n.id
                    for n in ast.walk(node.returns)
                    if isinstance(n, ast.Name) and n.id in models
                }
                if refs:
                    returns.setdefault((relative, node.name), set()).update(refs)
                    return_candidates.setdefault(node.name, []).append(refs)

    def collect(relative, source, tree, prefix=""):
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                collect(relative, source, node, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nodes = list(scoped_nodes(node))
                calls = sorted({ast.unparse(n.func) for n in nodes if isinstance(n, ast.Call)})
                model_names = sorted(
                    {n.id for n in nodes if isinstance(n, ast.Name) and n.id in models}
                )
                sites = []
                for n in nodes:
                    if isinstance(n, ast.Call):
                        name = ast.unparse(n.func)
                        method = name.rsplit(".", 1)[-1]
                        if method in MECHANISMS:
                            sites.append(
                                {
                                    "line": n.lineno,
                                    "mechanism": method,
                                    "expression": ast.unparse(n)[:300],
                                }
                            )
                sql = sorted(
                    {
                        n.value
                        for n in nodes
                        if isinstance(n, ast.Constant)
                        and isinstance(n.value, str)
                        and SQL_MUTATION.search(n.value)
                    }
                )

                def named_models(value):
                    refs = {
                        n.id for n in ast.walk(value) if isinstance(n, ast.Name) and n.id in models
                    }
                    for n in ast.walk(value):
                        # A Session.execute method is not a same-named domain
                        # method returning a model. Never infer receiver types by
                        # suffix alone (notably CeriPurgeService.execute).
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                            name = n.func.id
                            local = returns.get((relative, name))
                            candidates = return_candidates.get(name, [])
                            refs.update(local or (candidates[0] if len(candidates) == 1 else set()))
                    return refs

                variables, mutating_variables = {}, set()
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation:
                        variables[arg.arg] = named_models(arg.annotation)
                for n in nodes:
                    if isinstance(n, (ast.Assign, ast.AnnAssign)):
                        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                        refs = named_models(n.value) if n.value else set()
                        is_mutating = n.value is not None and any(
                            isinstance(x, ast.Call)
                            and ast.unparse(x.func).rsplit(".", 1)[-1]
                            in {"insert", "update", "delete", "pg_insert", "pg_update", "pg_delete"}
                            for x in ast.walk(n.value)
                        )
                        for target in targets:
                            if isinstance(target, ast.Name) and refs:
                                variables.setdefault(target.id, set()).update(refs)
                            if isinstance(target, ast.Name) and is_mutating:
                                mutating_variables.add(target.id)
                    if isinstance(n, (ast.For, ast.comprehension)) and isinstance(
                        n.target, ast.Name
                    ):
                        refs = named_models(n.iter)
                        for v in ast.walk(n.iter):
                            if isinstance(v, ast.Name):
                                refs.update(variables.get(v.id, set()))
                        variables.setdefault(n.target.id, set()).update(refs)

                def inferred(value, variables=variables):
                    refs = named_models(value)
                    for v in ast.walk(value):
                        if isinstance(v, ast.Name):
                            refs.update(variables.get(v.id, set()))
                    return refs

                write_models, orm_sites, field_sites, attribute_sites = set(), [], [], []
                for n in nodes:
                    if isinstance(n, ast.Call):
                        call = ast.unparse(n.func)
                        method = call.rsplit(".", 1)[-1]
                        receiver = call.rsplit(".", 1)[0]
                        if method in {
                            "add",
                            "add_all",
                            "merge",
                            "delete",
                            "execute",
                            "scalar",
                            "scalars",
                            "executemany",
                            "bulk_insert_mappings",
                            "bulk_update_mappings",
                            "bulk_save_objects",
                        }:
                            database = bool(
                                re.search(
                                    r"^(?:self\.)?(?:db|session|conn|connection|cursor|startup_db|cleanup_db)\b",
                                    receiver,
                                )
                            )
                            mutating_sql = method in {
                                "execute",
                                "executemany",
                                "scalar",
                                "scalars",
                            } and (
                                any(
                                    isinstance(x, ast.Call)
                                    and ast.unparse(x.func).rsplit(".", 1)[-1]
                                    in {
                                        "update",
                                        "delete",
                                        "insert",
                                        "pg_insert",
                                        "pg_update",
                                        "pg_delete",
                                    }
                                    for x in ast.walk(n)
                                )
                                or any(
                                    isinstance(x, ast.Name) and x.id in mutating_variables
                                    for x in ast.walk(n)
                                )
                            )
                            repository = "repository" in receiver or receiver in {
                                "self",
                                "repository",
                            }
                            if (database or repository) and (
                                method not in {"execute", "executemany", "scalar", "scalars"}
                                or mutating_sql
                            ):
                                refs = inferred(n)
                                write_models.update(refs)
                                orm_sites.append(
                                    {
                                        "line": n.lineno,
                                        "mechanism": method,
                                        "models": sorted(refs),
                                        "expression": ast.unparse(n)[:300],
                                    }
                                )
                        if method == "setattr" and n.args:
                            refs = inferred(n.args[0])
                            if refs:
                                write_models.update(refs)
                                field_sites.append(n.lineno)
                    if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                        for target in targets:
                            if isinstance(target, ast.Attribute):
                                attribute_sites.append(
                                    {"line": n.lineno, "target": ast.unparse(target)}
                                )
                                refs = inferred(target.value)
                                if refs:
                                    write_models.update(refs)
                                    field_sites.append(n.lineno)
                routes = [
                    ast.unparse(d)
                    for d in node.decorator_list
                    if isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in {"get", "post", "put", "patch", "delete", "api_route"}
                ]
                functions.append(
                    {
                        "id": relative + ":" + prefix + node.name,
                        "path": relative,
                        "symbol": prefix + node.name,
                        "public_addressable": isinstance(tree, (ast.Module, ast.ClassDef)),
                        "line": node.lineno,
                        "end_line": node.end_lineno,
                        "calls": calls,
                        "models": model_names,
                        "tables": [models[m] for m in model_names],
                        "sites": sorted(sites, key=lambda s: (s["line"], s["mechanism"])),
                        "sql_mutation_strings": sql,
                        "routes": routes,
                        "orm_sites": orm_sites,
                        "write_models": sorted(write_models),
                        "created_models": sorted(
                            {
                                n.func.id
                                for n in nodes
                                if isinstance(n, ast.Call)
                                and isinstance(n.func, ast.Name)
                                and n.func.id in models
                            }
                        ),
                        "field_sites": sorted(set(field_sites)),
                        "attribute_sites": attribute_sites,
                        "campaign": bool(CAMPAIGN.search(prefix + node.name)),
                        "scope": (
                            "MIGRATION_ONLY" if relative.startswith("alembic/") else "PRODUCTION"
                        ),
                    }
                )
                collect(relative, source, node, prefix + node.name + ".")

    for relative, source, tree in parsed:
        collect(relative, source, tree)
    return {
        "models": dict(sorted(models.items())),
        "files": files,
        "functions": sorted(functions, key=lambda f: f["id"]),
        "module_sites": module_sites,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(discover(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
