"""Deterministic key/location inventory. Never read .env or emit configuration values."""

from __future__ import annotations

import ast
import csv
import re
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs/remediation/calculation-lineage/T13A_configuration_authority_inventory.csv"
CONFIG_WORDS = re.compile(
    r"settings|config|params|profile|threshold|weight|policy|rule|feature_flag|DEFAULT_", re.I
)
SECRET_WORDS = re.compile(r"password|passwd|api_key|credential|private_key|secret|token", re.I)
BEHAVIORAL_SETTINGS = (
    "ib_use_rth",
    "ib_fetch_benchmarks",
    "ib_benchmarks",
    "ib_required_daily_bars",
    "ib_daily_bar_stale_after_days",
    "ib_default_duration",
    "ib_full_backfill_duration",
    "ib_top_up_duration",
    "ib_refresh_duration",
    "ib_default_bar_size",
    "ib_revision_window_sessions",
    "ib_liquidity_lookback_sessions",
    "ib_fee_rate_lookback_sessions",
    "ib_volatility_lookback_sessions",
    "ib_histogram_period",
    "ib_flex_report_timezone",
    "ib_intelligence_shortlist_limit",
    "ib_intelligence_historical_chunk_days",
    "sec_readiness_policy",
    "sec_guidance_lookback_days",
    "sec_guidance_max_documents_per_ticker",
    "eodhd_terms_version",
    "eodhd_daily_call_budget",
    "max_csv_rows",
    "max_csv_columns",
    "winner_probability_capture_in_pipeline",
    "market_data_prewarm_watchlist",
    "market_data_prewarm_config_version",
)


def subsystem(path: str, key: str = "") -> str:
    text = path.lower()
    if "settings.py" in text:
        text += "/" + key.lower()
    for needle, family in (
        ("winner", "Winner"),
        ("setup_lifecycle/alert", "Alerts"),
        ("setup_lifecycle/lifecycle", "Lifecycle"),
        ("setup_lifecycle", "Setup/Lifecycle"),
        ("ceri", "CERI"),
        ("sec_", "CERI"),
        ("eodhd", "CERI"),
        ("ib_market_intelligence", "IBMI"),
        ("ib_intelligence", "IBMI"),
        ("ib_liquidity", "IBMI"),
        ("ib_short", "IBMI"),
        ("ib_volatility", "IBMI"),
        ("ib_histogram", "IBMI"),
        ("ib_scanner", "IBMI"),
        ("ib_flex", "IBMI"),
        ("fundamental", "Fundamental"),
        ("ranking", "Ranking"),
        ("combined", "Combined"),
        ("scoring_weights", "Combined"),
        ("column_aliases", "Fundamental"),
        ("market_regime", "Regime"),
        ("sector", "Sector"),
        ("consumer_eligibility", "Readiness policies"),
        ("producer_readiness", "Readiness policies"),
        ("technical", "Technical"),
        ("pine_defaults", "Technical"),
        ("pipeline", "Pipeline"),
        ("market_calculation_context", "Pipeline"),
        ("market_clock", "Pipeline"),
        ("background_job", "Background jobs"),
        ("observability", "Observability"),
    ):
        if needle in text:
            return family
    return "Runtime/shared"


def classification(path: str, key: str, *, setting: bool = False) -> str:
    if SECRET_WORDS.search(key) or key == "database_url":
        return "SECURITY_SECRET"
    if setting:
        if key.startswith(("observability_", "db_monitor_", "worker_memory_")):
            return "OBSERVABILITY"
        if key in {"ib_host", "ib_port", "ib_client_id", "eodhd_base_url", "ib_flex_base_url"}:
            return "ENVIRONMENTAL_DEPENDENCY"
        if key in BEHAVIORAL_SETTINGS or (
            key.startswith(("technical_", "ceri_", "setup_", "winner_", "ib_", "sec_"))
            and (key.endswith("enabled") or key.endswith("mode") or key.endswith("policy"))
        ):
            return "BEHAVIORAL"
        return "OPERATIONAL"
    family = subsystem(path, key)
    if family == "Observability":
        return "OBSERVABILITY"
    if family in {
        "Fundamental",
        "Technical",
        "Combined",
        "Ranking",
        "Regime",
        "Sector",
        "CERI",
        "IBMI",
        "Setup/Lifecycle",
        "Lifecycle",
        "Alerts",
        "Winner",
        "Readiness policies",
        "Pipeline",
    }:
        return "BEHAVIORAL"
    return "UNKNOWN_CLASSIFICATION"


class Inventory(ast.NodeVisitor):
    def __init__(self, path: str, settings_keys: dict[str, str]):
        self.path = path
        self.settings_keys = settings_keys
        self.function = "module"
        self.rows = set()

    def record(self, node, key: str, kind: str, setting: bool = False):
        # Only key names, never default values, reprs, source excerpts or credentials.
        if not re.fullmatch(r"[\w. /-]{1,240}", key, re.ASCII):
            key = "DYNAMIC_KEY"
        category = self.settings_keys.get(key.lower())
        category = category or classification(self.path, key, setting=setting)
        if category == "SECURITY_SECRET":
            authority = "SECRET_READ"
        elif category == "OBSERVABILITY" or category == "OPERATIONAL":
            authority = "OPERATIONAL_DIRECT_READ"
        elif "/routers/" in self.path or "/export" in self.path or "_dtos" in self.path:
            authority = "DISPLAY_ONLY"
        elif "legacy" in self.path:
            authority = "LEGACY"
        elif self.function.startswith(("load_", "resolve_", "_parse", "_validate")):
            authority = "CANONICAL_RESOLVER"
        elif category == "BEHAVIORAL":
            authority = "BEHAVIORAL_DIRECT_READ"
        else:
            authority = "UNKNOWN_AUTHORITY"
        self.rows.add(
            (
                self.path,
                node.lineno,
                subsystem(self.path, key),
                self.function,
                kind,
                key,
                category,
                authority,
            )
        )

    def visit_FunctionDef(self, node):
        previous = self.function
        self.function = node.name
        self.generic_visit(node)
        self.function = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node):
        names = [child.id for child in ast.walk(node.value) if isinstance(child, ast.Name)]
        attrs = [child.attr for child in ast.walk(node.value) if isinstance(child, ast.Attribute)]
        if any(CONFIG_WORDS.search(name) for name in names + attrs):
            self.record(node, node.attr, "ATTRIBUTE_READ", "settings" in names + attrs)
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value)
            if CONFIG_WORDS.search(receiver) or receiver in {"os", "os.environ"}:
                if node.func.attr in {"get", "getenv", "pop", "setdefault"}:
                    key = (
                        node.args[0].value
                        if node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                        else "DYNAMIC_KEY"
                    )
                    self.record(
                        node,
                        key,
                        "ENVIRONMENT_READ" if receiver.startswith("os") else "MAPPING_READ",
                    )
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if CONFIG_WORDS.search(ast.unparse(node.value)):
            key = (
                node.slice.value
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
                else "DYNAMIC_KEY"
            )
            self.record(node, key, "SUBSCRIPT_READ")
        self.generic_visit(node)

    def visit_Assign(self, node):
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id.isupper()
                and CONFIG_WORDS.search(target.id)
            ):
                self.record(node, target.id, "CODE_DEFAULT_DECLARATION")
        self.generic_visit(node)


def yaml_keys(value, prefix=""):
    if isinstance(value, dict):
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            name = prefix + str(key)
            yield name
            yield from yaml_keys(item, name + ".")


def main():
    settings_path = ROOT / "app/settings.py"
    tree = ast.parse(settings_path.read_text(encoding="utf-8-sig"))
    settings_nodes = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    settings = {
        node.target.id: classification("app/settings.py", node.target.id, setting=True)
        for node in settings_nodes.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    rows = set()
    for node in settings_nodes.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            inventory = Inventory("app/settings.py", settings)
            inventory.record(node, node.target.id, "SETTINGS_DECLARATION", True)
            rows.update(inventory.rows)
    for path in sorted((ROOT / "app").rglob("*.py")):
        inventory = Inventory(path.relative_to(ROOT).as_posix(), settings)
        inventory.visit(ast.parse(path.read_text(encoding="utf-8-sig")))
        rows.update(inventory.rows)
    for path in sorted((ROOT / "config").glob("*.yaml")):
        relative = path.relative_to(ROOT).as_posix()
        # Versioned repository config only; never local .env or runtime settings.
        for key in yaml_keys(yaml.safe_load(path.read_text(encoding="utf-8"))):
            inventory = Inventory(relative, settings)
            inventory.function = "load_repository_yaml"
            inventory.record(type("Location", (), {"lineno": 0})(), key, "YAML_KEY_DECLARATION")
            rows.update(inventory.rows)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "path",
                "line",
                "subsystem",
                "function",
                "read_kind",
                "key",
                "classification",
                "authority",
            )
        )
        writer.writerows(sorted(rows))
    print(f"Inventory rows: {len(rows)}; settings keys: {len(settings)}")
    print(dict(sorted(Counter(row[-1] for row in rows).items())))


if __name__ == "__main__":
    main()
