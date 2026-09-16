"""Reproducible repository-wide source census with explicit review boundaries.

This is an inventory, not a proof of arbitrary Python data flow. Native runtime
attacks and manual call-graph review supply the certification evidence. Keyword
hits are discovery evidence; only actual configuration source reads are classified.
No source values or environment contents are copied into the report.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.services.configuration_delivery import EXECUTION_SETTINGS
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = re.compile(
    r"settings\.|os\.getenv|os\.environ|threshold|weight|profile|rule|policy|cooldown|"
    r"dedup|provider|priority|fallback|model_version|enabled|feature_flag",
    re.I,
)
ACQUISITION = set(
    """
ib_backoff_seconds ib_daily_bar_stale_after_days ib_default_bar_size ib_default_duration
ib_fee_rate_lookback_sessions ib_fetch_benchmarks ib_flex_activity_query_id ib_flex_base_url
ib_flex_journal_enabled ib_flex_query_id ib_flex_trades_query_id ib_intelligence_config_path
ib_liquidity_enabled ib_liquidity_lookback_sessions ib_options_activity_enabled
ib_refresh_duration ib_required_daily_bars ib_revision_audit_enabled ib_revision_window_sessions
ib_scanner_enabled ib_top_up_duration ib_use_rth ib_volatility_lookback_sessions
ib_histogram_enabled ib_histogram_period ib_flex_report_timezone ib_flex_trade_query_id
ib_full_backfill_duration ib_intelligence_shortlist_limit
market_data_prewarm_config_version market_data_prewarm_max_tickers market_data_prewarm_watchlist
sec_guidance_lookback_days sec_guidance_max_documents_per_ticker sec_document_incremental_mode
sec_readiness_policy eodhd_terms_version ib_benchmarks
""".split()
)
DISPLAY = set(
    """
app_name application_version chart_max_bars history_default_page_size history_max_page_size
max_export_rows max_export_size_mb runs_default_page_size ceri_ui_enabled ceri_admin_enabled
winner_probability_admin_enabled
""".split()
)
SCHEDULING = set(
    """
job_worker_enabled embedded_job_worker_enabled durable_worker_process_enabled
ceri_batched_workflow_enabled ceri_legacy_pipeline_scheduling_enabled
winner_probability_auto_maturation_enabled winner_probability_auto_cohort_refresh_enabled
winner_cohort_refresh_max_groups_per_slice winner_cohort_refresh_max_wall_seconds
winner_latest_rescore_max_predictions_per_slice market_data_prewarm_enabled
""".split()
)
SECRET = {"database_url", "eodhd_api_key", "ib_flex_token", "sec_user_agent"}
OPERATIONAL = set(
    """
allow_public_bind app_host app_port cache_dir ceri_barrier_retry_seconds
ceri_batch_checkpoint_interval ceri_feature_batch_size ceri_normalization_batch_size
ceri_provider_batch_size cleanup_cache_retention_days cleanup_export_retention_days
cleanup_job_retention_days cleanup_orphan_upload_grace_days database_connect_timeout_seconds
database_pool_max_overflow database_pool_size database_pool_timeout_seconds debug deployment_id
eodhd_base_url eodhd_daily_call_budget eodhd_http_timeout_seconds eodhd_max_attempts
eodhd_requests_per_minute export_dir fetch_technical_overlap_enabled ib_client_id
ib_flex_http_timeout_seconds ib_flex_poll_attempts ib_flex_poll_seconds ib_force_conservative_mode
ib_gateway_auto_launch_enabled ib_gateway_executable_path ib_health_client_id
ib_health_timeout_seconds ib_host ib_intelligence_historical_chunk_days
ib_intelligence_historical_min_spacing_seconds ib_intelligence_historical_requests_per_minute
ib_intelligence_live_concurrency ib_intelligence_market_data_line_cap
ib_intelligence_request_max_attempts ib_intelligence_retry_initial_seconds
ib_intelligence_retry_max_seconds ib_intelligence_tws_min_spacing_seconds ib_max_retries
ib_min_seconds_between_requests ib_port ib_readiness_max_age_seconds ib_request_delay_seconds
ib_requests_per_minute ib_timeout_seconds job_age_promotion_seconds
job_long_stage_progress_timeout_seconds job_market_data_progress_timeout_seconds
job_max_consecutive_interactive_claims job_poll_interval_seconds job_progress_timeout_seconds
job_stale_after_seconds job_watchdog_interval_seconds job_worker_heartbeat_interval_seconds
job_worker_heartbeat_timeout_seconds job_worker_id market_data_prewarm_cancel_bound_seconds
market_data_prewarm_resume_delay_seconds max_csv_columns max_csv_rows max_upload_size_mb
process_role queue_fairness_enabled runtime_instance_id runtime_mode sec_document_lease_seconds
sec_document_retry_base_seconds sec_http_timeout_seconds sec_requests_per_second
setup_latest_bar_projection_enabled setup_latest_bar_projection_shadow_compare_enabled
supervisor_restart_backoff_initial_seconds supervisor_restart_backoff_max_seconds
supervisor_restart_budget supervisor_restart_window_seconds swinglens_postgres_data_dir
swinglens_postgres_executable swinglens_postgres_expected_major swinglens_postgres_service
technical_artifact_cache_enabled technical_artifact_cache_mode
technical_artifact_cache_shadow_read_enabled
technical_artifact_cache_write_enabled technical_max_in_flight technical_process_pool_enabled
technical_pure_boundary_enabled technical_pure_boundary_shadow_compare_enabled
technical_series_version_maintenance_enabled technical_worker_processes upload_dir
worker_shutdown_grace_seconds
""".split()
)
CONFIG_PATHS = {"ceri_config_path", "ceri_taxonomy_path", "ib_intelligence_config_path"}
ROOT_LOADERS = {
    "load_alias_map",
    "load_fundamental_v2_config",
    "load_pine_defaults",
    "load_technical_scoring_v4_config",
    "load_technical_scoring_v5_config",
    "load_ranking_profiles",
    "load_market_regime_config",
    "load_sector_rotation_config",
    "load_ceri_config",
    "load_setup_lifecycle_config",
    "load_winner_probability_config",
    "load_ib_market_intelligence_config",
    "_load_scoring_config",
}
CURRENT_RULES_FILES = {
    "app/services/ceri/config.py",
    "app/services/setup_lifecycle/config.py",
    "app/services/winner_probability/config.py",
}


def setting_class(key):
    if key in SECRET:
        return "SECRET", "Credential excluded before configuration canonicalization."
    if key in EXECUTION_SETTINGS or key in CONFIG_PATHS:
        return (
            "PHASE4_FROZEN",
            "Root resolution or delivered execution switch; scoped native loaders.",
        )
    if key.startswith(("db_monitor_", "observability_", "worker_memory_")):
        return (
            "OBSERVABILITY_ONLY",
            "Monitoring/diagnostics; execution pressure may stop work, not change its meaning.",
        )
    if key in DISPLAY:
        return "DISPLAY_ONLY", "Presentation/admin visibility and paging/export limits."
    if key in SCHEDULING:
        return "SCHEDULING_ONLY", "Scheduling, implementation dispatch, or bounded work slicing."
    if key in ACQUISITION:
        return (
            "OUT_OF_SCOPE",
            (
                "Acquisition/source-normalization planning; financial IBMI "
                "windows come from frozen typed config."
            ),
        )
    if key == "use_durable_pipeline":
        return (
            "LEGACY",
            "Explicit legacy entry-point selector; certified durable path is the default.",
        )
    if key == "ceri_feature_rebuild_impl_version":
        return (
            "OBSERVABILITY_ONLY",
            "Displayed implementation version; algorithm identity is recorded separately.",
        )
    if key in OPERATIONAL:
        return (
            "OPERATIONAL_ONLY",
            (
                "Reviewed transport/runtime/cache/concurrency/resource control; "
                "native parity is a separate check."
            ),
        )
    return "UNKNOWN_AUTHORITY", "New source requires manual classification before certification."


def inventory():
    paths = []
    for directory in ("app", "scripts", "alembic", "tools"):
        if (ROOT / directory).exists():
            paths.extend((ROOT / directory).rglob("*.py"))
    paths.extend(ROOT.glob("*.py"))
    rows, index, files = [], [], []
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_text(encoding="utf-8-sig")
        files.append({"path": relative, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        for number, line in enumerate(content.splitlines(), 1):
            terms = sorted(set(match.group(0).lower() for match in KEYWORDS.finditer(line)))
            if terms:
                index.append({"path": relative, "line": number, "terms": terms})
        tree = ast.parse(content)
        for node in ast.walk(tree):
            key = None
            receiver = ""
            if isinstance(node, ast.Attribute) and node.attr in Settings.model_fields:
                receiver = ast.unparse(node.value)
                if "settings" in receiver.lower() or relative == "app/settings.py":
                    key = node.attr
            if isinstance(node, ast.Call):
                call = ast.unparse(node.func)
                if (
                    call == "getattr"
                    and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                ):
                    receiver = ast.unparse(node.args[0])
                    if (
                        "settings" in receiver.lower()
                        and node.args[1].value in Settings.model_fields
                    ):
                        key = node.args[1].value
                elif (
                    call == "getattr"
                    and len(node.args) > 1
                    and "settings" in ast.unparse(node.args[0]).lower()
                ):
                    category = (
                        "PHASE4_FROZEN"
                        if relative.endswith("configuration_delivery.py")
                        else "OPERATIONAL_ONLY"
                    )
                    rows.append(
                        {
                            "path": relative,
                            "line": node.lineno,
                            "kind": "DYNAMIC_SETTINGS_READ",
                            "symbol": ast.unparse(node),
                            "classification": category,
                            ("reason"): (
                                "Reviewed explicit EXECUTION_SETTINGS whitelist / baseline "
                                "recording / lifecycle runtime fence."
                            ),
                        }
                    )
                leaf = call.rsplit(".", 1)[-1]
                models = {
                    "SignalAlertRule",
                    "CeriAlertRule",
                    "WinnerModelVersion",
                    "WinnerOutcomeDefinition",
                    "WinnerCohortGeneration",
                    "EffectiveConfigurationRecord",
                    "ExecutionConfigurationAnchor",
                    "ExecutionConfigurationBinding",
                }
                selected = {
                    part.id
                    for arg in node.args
                    for part in ast.walk(arg)
                    if isinstance(part, ast.Name) and part.id in models
                }
                if selected and leaf in {"select", "get", "query"}:
                    category = "PHASE4_FROZEN"
                    reason = "Root selection, immutable history, or delivered rule IDs."
                    if "api_service" in relative:
                        category = "DISPLAY_ONLY"
                        reason = "API addressed display; rescore creates new lineage."
                    elif "model_registry" in relative:
                        category = "CURRENT_RULES_EXPLICIT"
                        reason = "Explicit model promotion; predictions remain immutable."
                    if not relative.startswith("app/"):
                        category = "OUT_OF_SCOPE"
                        reason = "Migration/QA/tool source inventory."
                    rows.append(
                        {
                            "path": relative,
                            "line": node.lineno,
                            "kind": "DATABASE_AUTHORITY_READ",
                            "symbol": call,
                            "models": sorted(selected),
                            "classification": category,
                            "reason": reason,
                        }
                    )
                raw = call in {"os.getenv", "os.environ.get", "yaml.safe_load", "json.load"}
                raw = raw or leaf in {"read_text", "read_bytes", "open"}
                if leaf in ROOT_LOADERS or raw:
                    if not relative.startswith("app/"):
                        category, reason = (
                            "OUT_OF_SCOPE",
                            "QA/migration/command inventory; inspect command root separately.",
                        )
                    elif relative in CURRENT_RULES_FILES and leaf in {"open", "safe_load"}:
                        category, reason = (
                            "CURRENT_RULES_EXPLICIT",
                            (
                                "New-root native parser branch; delivered scope returns before "
                                "this read."
                            ),
                        )
                    elif (
                        leaf in ROOT_LOADERS
                        or (
                            leaf.startswith("load_")
                            and any(word in leaf for word in ("config", "profile", "default"))
                        )
                        or relative.endswith("configuration_source_values.py")
                    ):
                        category, reason = (
                            "PHASE4_FROZEN",
                            "Authoritative root loader or delivered typed configuration adapter.",
                        )
                    elif call.startswith(("os.getenv", "os.environ")):
                        env_key = (
                            str(node.args[0].value).lower()
                            if node.args and isinstance(node.args[0], ast.Constant)
                            else None
                        )
                        if env_key in Settings.model_fields:
                            category, reason = setting_class(env_key)
                        elif (
                            "primary_provider" in relative
                            and node.args
                            and isinstance(node.args[0], ast.Attribute)
                            and node.args[0].attr == "credential_env_var"
                        ):
                            category, reason = (
                                "SECRET",
                                "Dynamic provider credential environment key.",
                            )
                        elif "/observability/" in relative:
                            category, reason = (
                                "OBSERVABILITY_ONLY",
                                "Test/deployment monitor attribution.",
                            )
                        elif "primary_provider" in relative and any(
                            isinstance(arg, ast.Constant)
                            and any(
                                word in str(arg.value).lower()
                                for word in ("key", "token", "secret")
                            )
                            for arg in node.args[:1]
                        ):
                            category, reason = (
                                "SECRET",
                                "Provider credential helper; values never retained.",
                            )
                        elif "sec/provider" in relative:
                            category, reason = (
                                "OUT_OF_SCOPE",
                                (
                                    "SEC acquisition budget/lookback; user-agent redacted "
                                    "separately."
                                ),
                            )
                        else:
                            category, reason = (
                                "OPERATIONAL_ONLY",
                                "Process/deployment identity and lifecycle controls.",
                            )
                    elif "research" in relative or relative.endswith("fundamental_ranker.py"):
                        category, reason = (
                            "LEGACY",
                            "Legacy/research boundary; no historical configuration promotion.",
                        )
                    elif any(
                        part in relative
                        for part in (
                            "config",
                            "configuration",
                            "column_mapper",
                            "technical_indicators",
                        )
                    ):
                        category, reason = (
                            "PHASE4_FROZEN",
                            (
                                "Declared source parser/resolver; runtime guard verifies "
                                "anchored paths do not reopen it."
                            ),
                        )
                    else:
                        category, reason = (
                            "OUT_OF_SCOPE",
                            (
                                "Data/document/cache/export/process files; not an effective "
                                "financial configuration source."
                            ),
                        )
                    names = [
                        arg.value
                        for arg in node.args[:1]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    ]
                    rows.append(
                        {
                            "path": relative,
                            "line": node.lineno,
                            "kind": "SOURCE_CALL",
                            "symbol": call,
                            "names": names,
                            "classification": category,
                            "reason": reason,
                        }
                    )
            if key:
                category, reason = setting_class(key)
                if not relative.startswith("app/"):
                    category, reason = (
                        "OUT_OF_SCOPE",
                        "Command/QA/tool runtime; source census includes these paths.",
                    )
                rows.append(
                    {
                        "path": relative,
                        "line": node.lineno,
                        "kind": "SETTINGS_READ",
                        "symbol": key,
                        "classification": category,
                        "reason": reason,
                    }
                )
            if (
                isinstance(node, ast.Subscript)
                and ast.unparse(node.value) == "os.environ"
                and isinstance(node.ctx, ast.Load)
            ):
                rows.append(
                    {
                        "path": relative,
                        "line": node.lineno,
                        "kind": "ENVIRONMENT_READ",
                        "symbol": ast.unparse(node),
                        "classification": "OUT_OF_SCOPE",
                        ("reason"): (
                            "Runtime/acquisition/QA environment; no environment contents "
                            "read by this census."
                        ),
                    }
                )
    rows.sort(key=lambda row: (row["path"], row["line"], row["kind"], row["symbol"]))
    unresolved = Counter(row["classification"] for row in rows if row["path"].startswith("app/"))
    return {
        "schema": "t13e-repository-authority-review-v1",
        "files": files,
        ("scope"): (
            "All Python source under app/scripts/alembic/tools and repository root; tests excluded."
        ),
        ("proof_boundary"): (
            "AST/keyword discovery plus reviewed call graph and native "
            "runtime attacks; not arbitrary data-flow verification."
        ),
        "source_reads": rows,
        "keyword_index": index,
        "counts": dict(sorted(Counter(row["classification"] for row in rows).items())),
        "certified_material_unresolved": {
            key: unresolved[key]
            for key in ("POTENTIAL_CONFIG_BYPASS", "CONFIRMED_CONFIG_BYPASS", "UNKNOWN_AUTHORITY")
        },
        "excluded_semantics": [
            "Acquisition-plan/source-normalization freezing",
            "Algorithm correctness",
            "Legacy unanchored upload/fallback projections",
            "Privileged SQL",
        ],
    }


if __name__ == "__main__":
    target = ROOT / "docs/remediation/calculation-lineage/T13E_configuration_authority_audit.json"
    result = inventory()
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "files": len(result["files"]),
                "reads": len(result["source_reads"]),
                "keyword_hits": len(result["keyword_index"]),
                "counts": result["counts"],
            }
        )
    )
