"""Core selection and claim boundaries; no application certification is inferred."""

import copy

import pytest

from scripts.qa import check_certification_registry as registry
from scripts.qa import release_evidence as release


def impact(*paths):
    return registry.source_impact_plan(registry.load_manifest(), list(paths))


def test_core_keeps_all_required_lane_kinds_and_P0_contracts_without_duplicate_A04():
    contract = release.core_policy(impact("config/certification_registry.json"))
    assert set(contract["base_lanes"]) == release.BASE_LANES
    assert contract["base_lanes"]["browser-smoke"]["browsers"] == ["chromium", "firefox"]
    assert len(contract["base_lanes"]["browser-smoke"]["selectors"]) == 5
    selected = {
        r["exact_test_id"] for r in contract["selection_manifest"]["entries"] if r["selected"]
    }
    a04 = {
        r["exact_test_id"]
        for r in contract["selection_manifest"]["entries"]
        if r["tier"] == "NATIVE_A04_PRODUCER"
    }
    assert len(a04) == 2 and a04 <= selected
    assert a04.isdisjoint(contract["base_lanes"]["python-regressions"]["selectors"])
    assert a04.isdisjoint(contract["base_lanes"]["browser-smoke"]["selectors"])
    deferred = [r for r in contract["selection_manifest"]["entries"] if not r["selected"]]
    assert all(r["outcome"] in {"NOT_RUN", "ARCHIVAL_BLOCKED"} for r in deferred)
    assert any(r["outcome"] == "ARCHIVAL_BLOCKED" for r in deferred)


def test_unknown_high_risk_impact_cannot_certify_core():
    with pytest.raises(release.EvidenceError, match="requires review"):
        release.core_policy(impact("app/services/new_unreviewed_writer.py"))


def test_changed_cohort_cannot_silently_defer_its_specialized_failure():
    contract = release.core_policy(
        impact("app/services/winner_probability/cohort_authority.py")
    )
    required = contract["a04_prerequisites"]["selectors"]
    assert required and any("88" in node for node in required)
    entries = contract["selection_manifest"]["entries"]
    assert all(row["selected"] for row in entries if row["exact_test_id"] in required)
    plan = release.prepare_core_plan(
        "synthetic-prerequisite-only", "1", "local", False,
        impact("app/services/winner_probability/cohort_authority.py"),
    )
    plan["a04_prerequisites"]["selectors"].pop()
    plan.pop("plan_sha256")
    plan["plan_sha256"] = release.digest(plan)
    with pytest.raises(release.EvidenceError, match="specialized prerequisite"):
        release.validate_plan(plan)


def test_core_plan_cannot_accept_changed_selection_or_scope():
    plan = release.prepare_core_plan(
        "synthetic-selection-only",
        "1",
        "local",
        False,
        impact("config/certification_registry.json"),
    )
    for fault in ("selection", "scope"):
        changed = copy.deepcopy(plan)
        if fault == "selection":
            changed["required_lanes"]["python-regressions"]["selectors"].pop()
        else:
            changed["selection_manifest"]["entries"][0]["selected"] = False
        changed.pop("plan_sha256")
        changed["plan_sha256"] = release.digest(changed)
        with pytest.raises(release.EvidenceError, match="selection|scope"):
            release.validate_plan(changed)


def test_missing_core_evidence_never_becomes_full_release_or_live_approval(tmp_path):
    plan = release.prepare_core_plan(
        "synthetic-selection-only",
        "1",
        "local",
        False,
        impact("config/certification_registry.json"),
    )
    result = release.aggregate(plan, tmp_path, {})
    assert result["verdict"] == "NONCERTIFIED"
    assert result["core_verdict"] == "CORE_RELEASE_FAILED"
    assert result["gates"]["FULL_RELEASE_REGRESSIONS"] == "NOT_RUN"
    assert result["gates"]["SMALL_LIVE_PROVIDER_CANARY"] == "NOT_RUN"
    assert not result["safe_for_normal_run"]
    assert len(result["lanes"]) == 9 and result["reasons"]
