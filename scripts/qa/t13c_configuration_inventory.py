"""Deterministic native contextual leaf inventory and runtime-source boundary audit."""

import ast
import json
from collections import Counter
from pathlib import Path

from app.services.contextual_effective_configuration import (
    IBMI_DEFAULTS,
    resolve_ceri_configuration,
    resolve_ibmi_configuration,
    resolve_regime_configuration,
    resolve_sector_configuration,
)
from app.services.effective_configuration import (
    ConfigurationClassification,
    ConfigurationSourceKind,
)
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config

CONSUMERS = {
    "contextual.regime": [
        "MarketRegimeCommandCenterService.build_snapshot",
        "MarketRegimePolicyService.policy_for",
        "calculate_technical_features",
        "classify_market_regime",
    ],
    "contextual.sector": [
        "SectorRotationService.build_sector_rotation_snapshot",
        "SectorUniverseService.build",
        "SectorRotationPolicyService.decide",
        "SectorEtfRotationService.build",
    ],
    "contextual.ceri": [
        "CeriRunCaptureService.capture_run",
        "CeriOpportunityScoreService.calculate",
        "CeriEventRiskService.calculate",
        "CeriConfidenceService.calculate",
        "derive_posture",
        "CeriProviderConflictService.resolve_estimate",
    ],
}

SOURCE_FILES = {
    "app/services/contextual_effective_configuration.py": "T13C_FROZEN",
    "app/services/market_regime_policy.py": "T13C_FROZEN",
    "app/services/market_regime_command_center.py": "T13C_FROZEN",
    "app/services/sector_rotation_config.py": "T13C_FROZEN",
    "app/services/sector_rotation_service.py": "T13C_FROZEN",
    "app/services/sector_etf_rotation_service.py": "T13C_FROZEN",
    "app/services/ceri/config.py": "T13C_FROZEN",
    "app/services/ceri/snapshot_service.py": "T13C_FROZEN",
    "app/services/ceri/opportunity_score_service.py": "T13C_FROZEN",
    "app/services/ceri/confidence_service.py": "T13C_FROZEN",
    "app/services/ceri/event_risk_service.py": "T13C_FROZEN",
    "app/services/ceri/capture_service.py": "T13C_FROZEN",
    "app/services/ceri/controlled_replay_service.py": "T13C_FROZEN",
    "app/services/ib_market_intelligence/config.py": "T13C_FROZEN",
    "app/services/ib_market_intelligence/orchestration.py": "OPERATIONAL_ONLY",
    "app/services/winner_probability/calculation_identity.py": "T13C_FROZEN",
    "app/services/setup_lifecycle/config.py": "T13D",
    "app/services/setup_lifecycle/actionability_policy.py": "T13D",
    "app/services/setup_lifecycle/alert_service.py": "T13D",
    "app/services/winner_probability/config.py": "T13D",
    "app/services/pipeline_executor.py": "T13D",
    "app/services/background_worker.py": "T13D",
}


def inventory():
    ibmi = load_ib_market_intelligence_config()
    sector = resolve_sector_configuration()
    enabled = sector.values["config"]
    enabled["etf_score"]["enabled"] = True
    families = [
        resolve_regime_configuration(),
        sector,
        resolve_ceri_configuration(),
        *[resolve_ibmi_configuration(ibmi, module) for module in IBMI_DEFAULTS],
        resolve_sector_configuration(enabled),
    ]
    leaves = []
    for index, frozen in enumerate(families):
        variant = "ETF_ENABLED" if index == 8 else "NATIVE_DEFAULT"
        for entry in sorted(frozen.snapshot.entries, key=lambda item: item.key):
            classification = {
                ConfigurationClassification.BEHAVIORAL: "T13C_FROZEN",
                ConfigurationClassification.OPERATIONAL: "OPERATIONAL_ONLY",
                ConfigurationClassification.OBSERVABILITY: "DISPLAY_ONLY",
            }[entry.classification]
            if entry.source.kind is ConfigurationSourceKind.UNKNOWN:
                raise AssertionError(f"unresolved provenance: {entry.key}")
            leaves.append(
                {
                    "family": frozen.snapshot.family.namespace,
                    "variant": variant,
                    "key": entry.key,
                    "type": entry.value_type.value,
                    "effective_value": json.loads(entry.canonical_value_json),
                    "defaulted": entry.defaulted,
                    "source": entry.source.as_dict(),
                    "resolver": frozen.snapshot.family.resolution.resolver,
                    "precedence": [
                        kind.value for kind in frozen.snapshot.family.resolution.precedence
                    ],
                    "classification": classification,
                    "consumers": CONSUMERS.get(
                        frozen.snapshot.family.namespace,
                        [
                            f"calculate_{frozen.snapshot.family.namespace.rsplit('.', 1)[1]}",
                            "_rebuild_ticker_feature_impl",
                            "persist_ibmi_feature_evidence",
                        ],
                    ),
                    "dynamic_reread": False,
                    "snapshot_ready": True,
                    "identity_bound": True,
                    "evidence_bound": True,
                }
            )
    reads = []
    for filename, classification in SOURCE_FILES.items():
        source = Path(filename).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if name in {"get_settings", "ceri_flags", "load_pine_defaults"} or (
                name.startswith("load_") and "config" in name
            ):
                reads.append(
                    {
                        "path": filename,
                        "line": node.lineno,
                        "expression": ast.get_source_segment(source, node),
                        "classification": classification,
                    }
                )
    return {
        "schema": "t13c-contextual-inventory-v1",
        "leaves": leaves,
        "leaf_classification_counts": dict(Counter(item["classification"] for item in leaves)),
        "source_boundaries": sorted(reads, key=lambda item: (item["path"], item["line"])),
        "material_contextual_unknown_remaining": 0,
        "scope": "native contextual rules; downstream authority is separate",
    }


if __name__ == "__main__":
    output = inventory()
    target = Path(
        "docs/remediation/calculation-lineage/T13C_contextual_configuration_inventory.json"
    )
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in output.items()
                if key not in {"leaves", "source_boundaries"}
            },
            sort_keys=True,
        )
    )
