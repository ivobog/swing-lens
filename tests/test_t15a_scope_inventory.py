from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "docs" / "build_t15a_artifacts.py"
OUT = ROOT / "docs" / "remediation" / "calculation-lineage"


def _module():
    spec = importlib.util.spec_from_file_location("build_t15a_artifacts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_t15a_machine_artifacts_are_deterministic_and_complete() -> None:
    generated = _module().build_artifacts()

    assert set(generated) == {
        "T15A_scope_operation_inventory.json",
        "T15A_scope_refresh_authority_matrix.csv",
        "T15A_phase6_handoff.json",
        "T15A_validation_summary.json",
    }
    for name, content in generated.items():
        assert (OUT / name).read_text(encoding="utf-8") == content

    inventory = json.loads(generated["T15A_scope_operation_inventory.json"])
    assert inventory["source"]["t14d_certified_family_count"] == 220
    assert inventory["counts"]["total"] == len(inventory["records"])
    assert inventory["counts"]["unknown"] == 0
    assert all(
        record["phase6_target_task"] in {"T15B", "T15C", "T15D"} for record in inventory["records"]
    )
    assert all(record["status"] for record in inventory["records"])


def test_handoff_keeps_phase7_original_context_separate() -> None:
    handoff = json.loads((OUT / "T15A_phase6_handoff.json").read_text(encoding="utf-8"))

    assert "Phase 7" in handoff["phase7_exclusion"]
    assert "fabricate" in handoff["phase7_exclusion"]
    assert {"T15B", "T15C", "T15D"} == set(handoff["decomposition"])
