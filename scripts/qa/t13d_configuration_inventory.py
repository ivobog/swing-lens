"""Bounded decision authority census; units and reviewed exclusions stay explicit."""

import ast
import json
from collections import Counter
from pathlib import Path

from app.services.configuration_delivery import EXECUTION_SETTINGS, resolve_readiness_configurations
from app.services.decision_effective_configuration import (
    resolve_alert_configuration,
    resolve_ceri_decision_configuration,
    resolve_lifecycle_configuration,
    resolve_setup_configuration,
    resolve_winner_configuration,
)
from app.services.effective_configuration import ConfigurationSourceKind

SETTING_GROUPS = {
    "T13D_FROZEN": set(EXECUTION_SETTINGS),
    "DISPLAY_ONLY": {"ceri_ui_enabled"},
    "OBSERVABILITY_ONLY": {
        "setup_latest_bar_projection_shadow_compare_enabled",
        "worker_memory_tracemalloc_enabled",
        "worker_memory_profile_interval_items",
        "worker_memory_warning_mb",
        "worker_memory_critical_mb",
        "worker_memory_top_allocations",
        "observability_evidence_cleanup_interval_seconds",
        "observability_fanout_thresholds",
        "observability_fanout_critical",
        "observability_fanout_warning",
    },
    "OPERATIONAL_ONLY": {
        "setup_latest_bar_projection_enabled",
        "ceri_config_path",
        "ceri_taxonomy_path",
        "winner_probability_admin_enabled",
        "ceri_admin_enabled",
        "sec_requests_per_second",
        "ib_host",
        "ib_port",
        "ib_client_id",
        "ib_timeout_seconds",
        "ib_flex_trade_query_id",
        "ib_flex_activity_query_id",
        "ib_intelligence_request_max_attempts",
        "ib_intelligence_retry_initial_seconds",
        "ib_intelligence_retry_max_seconds",
        "ib_intelligence_historical_requests_per_minute",
        "ib_intelligence_historical_min_spacing_seconds",
        "ib_intelligence_tws_min_spacing_seconds",
        "ib_intelligence_live_concurrency",
        "ib_intelligence_market_data_line_cap",
        "ib_flex_poll_attempts",
        "ib_flex_poll_seconds",
    },
    "SCHEDULING_ONLY": {
        "winner_latest_rescore_max_predictions_per_slice",
        "winner_cohort_refresh_max_groups_per_slice",
        "winner_cohort_refresh_max_wall_seconds",
        "ceri_legacy_pipeline_scheduling_enabled",
        "ceri_batched_workflow_enabled",
        "runtime_mode",
        "winner_probability_auto_maturation_enabled",
        "winner_probability_auto_cohort_refresh_enabled",
        "sec_document_incremental_mode",
        "sec_readiness_policy",
        "job_worker_id",
        "job_worker_heartbeat_timeout_seconds",
        "job_worker_heartbeat_interval_seconds",
        "job_stale_after_seconds",
        "queue_fairness_enabled",
        "job_max_consecutive_interactive_claims",
        "job_age_promotion_seconds",
        "job_poll_interval_seconds",
        "fetch_technical_overlap_enabled",
    },
    "OUT_OF_SCOPE": {
        "ib_intelligence_historical_chunk_days",
        "ib_intelligence_shortlist_limit",
        "ib_flex_report_timezone",
        "ib_volatility_lookback_sessions",
        "ib_histogram_period",
        "ib_fee_rate_lookback_sessions",
        "ib_liquidity_lookback_sessions",
    },
    "SECRET": {"ib_flex_token", "database_url", "fmp_api_key", "sec_user_agent"},
}


def source_files():
    files = set(Path("app/services/setup_lifecycle").glob("*.py"))
    files.update(Path("app/services/winner_probability").glob("*.py"))
    files.update(
        Path("app/services") / name
        for name in (
            "ceri/config.py",
            "ceri/change_detection_service.py",
            "ceri/alert_service.py",
            "ceri/job_handlers.py",
            "ceri/feature_flags.py",
            "pipeline_executor.py",
            "pipeline_service.py",
            "background_worker.py",
            "background_job_service.py",
            "configuration_delivery.py",
            "decision_effective_configuration.py",
            "technical_feature_flags.py",
            "ib_market_intelligence/orchestration.py",
            "technical_consumer_eligibility.py",
            "contextual_consumer_eligibility.py",
            "producer_readiness.py",
        )
    )
    return sorted(files)


def setting_key(node):
    if not isinstance(node, ast.Attribute):
        return None
    value = node.value
    if isinstance(value, ast.Name) and value.id == "settings":
        return node.attr
    if isinstance(value, ast.Attribute) and value.attr == "settings":
        return node.attr
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "get_settings"
    ):
        return node.attr
    return None


def census():
    reads = []
    for path in source_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            key = setting_key(node)
            classification = None
            reason = None
            if key:
                classification = next(
                    (label for label, keys in SETTING_GROUPS.items() if key in keys),
                    "UNKNOWN_REMAINING",
                )
                reason = "Reviewed Settings key; acquisition remains a separate source."
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else ""
                if name == "get_settings":
                    classification = "OPERATIONAL_ONLY"
                    reason = "Getter delivers retained switches; keys are counted separately."
                elif name in {"ceri_flags", "load_pine_defaults"} or (
                    name.startswith("load_") and "config" in name
                ):
                    classification = "T13D_FROZEN"
                    reason = "Native loader intercepts delivery before current resolution."
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"open", "safe_load"}
                    and path.name == "config.py"
                ):
                    classification = "CURRENT_RULES_EXPLICIT"
                    reason = "New-root profile parse; delivery returns before this branch."
                elif isinstance(node.func, ast.Attribute) and node.func.attr == "getenv":
                    classification = "UNKNOWN_REMAINING"
                    reason = "Environment source requires explicit review."
            if classification is None and isinstance(node, ast.Attribute):
                parent = parents.get(node)
                expression = ast.get_source_segment(source, node) or ""
                if not isinstance(parent, ast.Attribute) and expression.startswith(
                    ("config.", "self.config.", "cfg.", "rule.")
                ):
                    classification = "T13D_FROZEN"
                    reason = "Native policy/rule after own resolver; standalone uses current rules."
                    if expression == "rule.id":
                        classification = "OPERATIONAL_ONLY"
                        reason = "Physical rule FK address; selected decision values remain frozen."
                    if any(
                        part in expression.split(".")
                        for part in ("api", "retention", "replay", "config_hash")
                    ):
                        classification = "OPERATIONAL_ONLY"
                        reason = "Orchestration/compatibility metadata, outside own semantic hash."
            if classification:
                reads.append(
                    {
                        "path": path.as_posix(),
                        "line": node.lineno,
                        "expression": ast.get_source_segment(source, node),
                        "key": key,
                        "classification": classification,
                        "reason": reason,
                    }
                )
    return sorted(reads, key=lambda row: (row["path"], row["line"], row["expression"]))


def inventory():
    families = [
        resolve_setup_configuration(),
        resolve_lifecycle_configuration(),
        resolve_alert_configuration(),
        resolve_ceri_decision_configuration(family="alerts"),
        resolve_ceri_decision_configuration(family="changes"),
    ]
    families.extend(
        resolve_winner_configuration(family=name)
        for name in ("prediction", "outcome", "cohort", "generation")
    )
    families.extend(resolve_readiness_configurations())
    entries = []
    for config in families:
        for entry in config.snapshot.entries:
            if entry.source.kind is ConfigurationSourceKind.UNKNOWN:
                raise ValueError("UNKNOWN_MATERIAL_CONFIGURATION_SOURCE")
            entries.append(
                {
                    "family": config.snapshot.family.namespace,
                    "key": entry.key,
                    "classification": entry.classification.value,
                    "value_type": entry.value_type.value,
                    "source": entry.source.as_dict(),
                    "defaulted": entry.defaulted,
                    "identity": config.snapshot.identity.as_dict(),
                }
            )
    reads = census()
    counts = dict(Counter(row["classification"] for row in reads))
    result = {
        "schema": "t13d-decision-authority-inventory-v1",
        "entries": entries,
        "entry_counts": dict(Counter(row["classification"] for row in entries)),
        "source_boundaries": reads,
        "source_boundary_counts": counts,
        "reviewed_settings": {key: sorted(value) for key, value in SETTING_GROUPS.items()},
        "unknown_material_remaining": counts.get("UNKNOWN_REMAINING", 0),
        "scope": "Decision and delivery scope; bounded AST census.",
        "units": "Typed STRUCTURE units and source sites are separate counts.",
        "exclusions": "Acquisition/privileged SQL deferred; transport may delay work.",
    }
    if result["unknown_material_remaining"]:
        raise ValueError(
            json.dumps([row for row in reads if row["classification"] == "UNKNOWN_REMAINING"])
        )
    return result


if __name__ == "__main__":
    result = inventory()
    Path(
        "docs/remediation/calculation-lineage/T13D_decision_configuration_inventory.json"
    ).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"entries", "source_boundaries", "reviewed_settings"}
            },
            sort_keys=True,
        )
    )
