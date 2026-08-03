from __future__ import annotations

import argparse
from pathlib import Path

from app.main import app

ROUTES_START = "<!-- ROUTE_INVENTORY_START -->"
ROUTES_END = "<!-- ROUTE_INVENTORY_END -->"
EXPORTS_START = "<!-- EXPORT_INVENTORY_START -->"
EXPORTS_END = "<!-- EXPORT_INVENTORY_END -->"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or update SwingLens route inventory docs.")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("docs/routes_exports.md"),
        help="Markdown file containing route inventory markers.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite inventory blocks instead of checking them.",
    )
    args = parser.parse_args()

    current = args.path.read_text(encoding="utf-8")
    updated = replace_inventory_blocks(current)
    if args.write:
        args.path.write_text(updated, encoding="utf-8")
        return 0
    if current != updated:
        print(f"{args.path} is out of date. Run:")
        print(f"python scripts/docs/check_route_inventory.py --write --path {args.path}")
        return 1
    return 0


def replace_inventory_blocks(content: str) -> str:
    content = _replace_block(
        content,
        ROUTES_START,
        ROUTES_END,
        _table_for_routes(_runtime_routes()),
    )
    return _replace_block(
        content,
        EXPORTS_START,
        EXPORTS_END,
        _table_for_routes(_export_routes()),
    )


def _runtime_routes() -> list[dict[str, str]]:
    rows = []
    for route in app.routes:
        path = str(getattr(route, "path", "") or "")
        if not path or path.startswith("/static"):
            continue
        methods = sorted(set(getattr(route, "methods", []) or []) - {"HEAD", "OPTIONS"})
        rows.append(
            {
                "methods": ", ".join(methods),
                "path": path,
                "endpoint": str(getattr(route, "name", "") or ""),
            }
        )
    return sorted(rows, key=lambda item: (item["path"], item["methods"], item["endpoint"]))


def _export_routes() -> list[dict[str, str]]:
    return [
        route
        for route in _runtime_routes()
        if _is_export_route(route["path"], route["endpoint"])
    ]


def _is_export_route(path: str, endpoint: str) -> bool:
    lowered = f"{path} {endpoint}".lower()
    return (
        "export" in lowered
        or path.endswith(".csv")
        or path.endswith(".json")
        or path.endswith(".md")
    )


def _table_for_routes(routes: list[dict[str, str]]) -> str:
    lines = ["| Methods | Path | Endpoint |", "| --- | --- | --- |"]
    for route in routes:
        lines.append(
            "| {methods} | `{path}` | `{endpoint}` |".format(
                methods=route["methods"],
                path=route["path"],
                endpoint=route["endpoint"],
            )
        )
    return "\n".join(lines)


def _replace_block(content: str, start: str, end: str, replacement: str) -> str:
    start_index = content.index(start) + len(start)
    end_index = content.index(end)
    return f"{content[:start_index]}\n{replacement}\n{content[end_index:]}"


if __name__ == "__main__":
    raise SystemExit(main())
