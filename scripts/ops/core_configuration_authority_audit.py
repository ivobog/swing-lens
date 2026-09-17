"""Reproducible T13B authority inventory, using repository defaults only.

This is an executable key inventory plus a bounded AST candidate census. The
call-graph rationale in the T13B report is required to interpret the census;
ordinary input data reads are not declared to be configuration automatically.
No dotenv, process environment values, credential values or source excerpts
are emitted. Historical evidence never calls this development audit.
"""

import ast
import csv
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from app.services.combined_decision import _load_scoring_config
from app.services.core_effective_configuration import (
    resolve_combined_configuration,
    resolve_fundamental_configuration,
    resolve_ranking_configuration,
    resolve_technical_configuration,
)
from app.services.ranking_profile_config import load_ranking_profiles
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "docs/remediation/calculation-lineage"
CORE_PATHS = (
    "fundamental_score_service",
    "fundamental_ranker",
    "fundamental_ranker_v2",
    "fundamental_components_v2",
    "fundamental_coverage_service",
    "fundamental_warning_service",
    "technical_score_service",
    "technical_work",
    "technical_indicators",
    "pine_replica_engine",
    "technical_scoring_config",
    "technical_scoring_v5_config",
    "technical_score_v4",
    "technical_score_v5",
    "technical_strength_v5",
    "technical_confidence",
    "technical_explainability",
    "technical_feature_flags",
    "adaptive_technical_features",
    "box_breakout",
    "climax_risk",
    "stage_analysis",
    "volatility_contraction",
    "relative_leadership",
    "leadership_v5",
    "sector_benchmark_service",
    "combined_decision",
    "cockpit_sorting",
    "confidence_service",
    "earnings_risk_service",
    "ranking_profile_service",
    "ranking_profile_config",
    "ranking_profile_engine",
    "ranking_profile_components",
    "ranking_profile_gates",
    "ranking_profile_penalties",
    "combined_ranking_identity",
    "core_calculation_evidence",
    "core_effective_configuration",
    "configuration_source_values",
    "core_settings_provenance",
)


def inventory():
    config = _load_scoring_config()
    resolved = [resolve_fundamental_configuration(), resolve_combined_configuration(config)]
    # Read code defaults directly: even Settings(_env_file=None) parses process
    # environment, which must not influence this repository-default inventory.
    flag_defaults = {
        key: Settings.model_fields[key].default
        for key in (
            "technical_v5_enabled",
            "technical_v5_shadow_compare_enabled",
            "technical_v5_persist_shadow_results",
        )
    }
    default_settings = SimpleNamespace(
        **flag_defaults,
        _core_configuration_sources=tuple(
            (key, "CODE_DEFAULT", value) for key, value in flag_defaults.items()
        ),
    )
    resolved.append(
        resolve_technical_configuration(
            pine=load_pine_defaults(),
            v4=load_technical_scoring_v4_config(),
            v5=load_technical_scoring_v5_config(),
            settings=default_settings,
        )
    )
    resolved.extend(
        resolve_ranking_configuration(profile, config) for profile in load_ranking_profiles()
    )
    rows = []
    for item in resolved:
        snapshot = item.snapshot
        profile = item.values.get("profile", {}).get("name", "")
        for entry in sorted(snapshot.entries, key=lambda entry: entry.key):
            rows.append(
                dict(
                    family=snapshot.family.namespace,
                    profile=profile,
                    canonical_key=entry.key,
                    type=json.loads(entry.canonical_value_json)["type"],
                    canonical_value_type=entry.value_type.value,
                    repository_effective_default=entry.canonical_value_json,
                    source_kind=entry.source.kind.value,
                    source_identifier=entry.source.identifier or "",
                    resolver=snapshot.family.resolution.resolver,
                    precedence=" > ".join(
                        kind.value for kind in snapshot.family.resolution.precedence
                    ),
                    classification=entry.classification.value,
                    defaulted=entry.defaulted,
                    consumer={
                        "core.fundamental": (
                            "score_rows_v2;score_components_v2;calculate_coverage_v2;"
                            "build_warning_flags_v2"
                        ),
                        "core.technical": (
                            "calculate_technical_features;calculate_htf_trend_features;"
                            "calculate_relative_strength_features;score_from_feature_result;"
                            "finalize_technical_scores"
                        ),
                        "core.combined": (
                            "combine_row_decision;_weighted_available_score;_decision_label;"
                            "calculate_earnings_risk"
                        ),
                        "core.ranking": (
                            "rank_profile;rank_single_row;apply_profile_gates;"
                            "calculate_profile_penalties;calculate_earnings_risk;_tradeability_penalty"
                        ),
                    }[snapshot.family.namespace],
                    dynamic_read="no during certified scoring",
                    snapshot_ready=True,
                    represented_in_calculation_identity=entry.classification.value == "BEHAVIORAL",
                    schema=snapshot.family.schema_version,
                )
            )
    return rows


def census():
    rows = []
    for module in CORE_PATHS:
        path = ROOT / f"app/services/{module}.py"
        if not path.exists():
            raise ValueError(f"audited core path missing: {module}")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = {
            node: node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        owners = {child: name for node, name in functions.items() for child in ast.walk(node)}
        for node in ast.walk(tree):
            key, read_kind, authority = "", "", ""
            if isinstance(node, ast.Attribute):
                expression = ast.unparse(node)
                if ".settings." in expression or expression.startswith("settings."):
                    key, read_kind = node.attr, "settings_attribute"
                    authority = (
                        "T13B_FROZEN" if key.startswith("technical_v5_") else "OPERATIONAL_ONLY"
                    )
                elif expression.startswith("profile."):
                    key, read_kind = expression, "resolved_profile_attribute"
                    authority = (
                        "DISPLAY_ONLY"
                        if node.attr in {"name", "label", "description"}
                        else "T13B_FROZEN"
                    )
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if (
                    name == "getattr"
                    and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                ):
                    source = ast.unparse(node.args[0])
                    if source in {"settings", "self.settings"}:
                        key, read_kind = str(node.args[1].value), "settings_getattr"
                        authority = (
                            "T13B_FROZEN" if key.startswith("technical_v5_") else "OPERATIONAL_ONLY"
                        )
                elif name in {
                    "load_fundamentals_v2_config",
                    "load_pine_defaults",
                    "load_technical_scoring_v4_config",
                    "load_technical_scoring_v5_config",
                    "load_ranking_profiles",
                    "load_ib_market_intelligence_config",
                    "_load_scoring_config",
                    "get_ranking_profile",
                    "get_settings",
                }:
                    key, read_kind = name, "source_resolution_boundary"
                    authority = "T13C" if "ib_market_intelligence" in name else "T13B_FROZEN"
                    if name == "get_settings":
                        # Semantic subset is explicitly frozen from one copied object.
                        authority = "OPERATIONAL_ONLY"
                elif name.endswith(".get") and any(
                    word in name.lower()
                    for word in ("config", "param", "weight", "threshold", "gate")
                ):
                    key = (
                        str(node.args[0].value)
                        if node.args and isinstance(node.args[0], ast.Constant)
                        else "dynamic-key"
                    )
                    read_kind, authority = "resolved_mapping_read", "T13B_FROZEN"
            if (
                isinstance(node, ast.Name)
                and node.id.startswith("DEFAULT_")
                and isinstance(node.ctx, ast.Load)
            ):
                key, read_kind, authority = (
                    node.id,
                    "native_default_or_algorithm_constant",
                    "T13B_FROZEN",
                )
            if read_kind:
                rows.append(
                    dict(
                        path=path.relative_to(ROOT).as_posix(),
                        line=node.lineno,
                        function=owners.get(node, "module"),
                        read_kind=read_kind,
                        key=key,
                        authority=authority,
                    )
                )
    # Explicit policy/deferred boundaries, already frozen by Phase 3 where applicable.
    for key, authority in (
        ("consumer_readiness_policy_delivery", "T13D"),
        ("pipeline_durable_configuration_anchor", "T13D"),
        ("IBMI_liquidity_producer_configuration", "T13C"),
    ):
        rows.append(
            dict(
                path="T13B-call-graph",
                line=0,
                function="boundary",
                read_kind="deferred_authority",
                key=key,
                authority=authority,
            )
        )
    return sorted(rows, key=lambda row: (row["path"], row["line"], row["key"]))


def write(name, rows):
    with (DESTINATION / name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    keys, reads = inventory(), census()
    write("T13B_core_configuration_inventory.csv", keys)
    write("T13B_core_configuration_reads.csv", reads)
    print("Resolved entries:", len(keys))
    print("Candidate census:", dict(sorted(Counter(row["authority"] for row in reads).items())))
