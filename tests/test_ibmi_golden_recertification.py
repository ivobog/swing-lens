"""Versioned runtime configuration and non-promoting candidate validation."""

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.settings import Settings

sys.path.insert(0, str(Path(__file__).parent / "e2e"))
from single_run_certification import candidate_validation as validation  # noqa: E402
from single_run_certification import golden_oracle as retained  # noqa: E402

from scripts.qa.golden_provenance import (  # noqa: E402
    ProvenanceError,
    inspect_proposal,
    verify_baseline,
)


def test_versioned_runtime_config_preserves_every_historical_seal(monkeypatch):
    monkeypatch.delenv("IB_INTELLIGENCE_CONFIG_PATH", raising=False)
    settings = Settings(_env_file=None)
    current = load_ib_market_intelligence_config(settings=settings)
    historical = load_ib_market_intelligence_config(
        path=Path("config/ib_market_intelligence.yaml"),
        settings=settings,
    )
    assert settings.ib_intelligence_config_path.name == "ib_market_intelligence_v1_0_2.yaml"
    assert current.calculation_version == "ibmi-1.0.2"
    assert historical.calculation_version == "ibmi-1.0.0"
    before, after = copy.deepcopy(historical.raw), copy.deepcopy(current.raw)
    for value in (before, after):
        value["engine"].pop("calculation_version")
        value["engine"].pop("config_version")
    assert before == after  # Includes all budgets, thresholds and lookbacks.
    assert len(verify_baseline()["artifacts"]) == 45


def test_proposal_does_not_select_or_authorize_a_golden(monkeypatch):
    monkeypatch.delenv(validation.CANDIDATE_ENV, raising=False)
    # This historical IBMI-only contract keeps its original v5 financial literals.
    # Current v7 financial propagation is certified by the native A04 lane.
    monkeypatch.setattr(
        validation, "PROPOSAL", Path(
            "docs/verification/forward-quality-v22/release/golden-v6/"
            "previous-pending-v5-proposal.json"
        ),
    )
    proposal = validation.candidate_proposal()
    inspected = inspect_proposal(proposal)
    assert inspected["financial_equivalence"] == "NOT_EVALUATED"
    assert inspected["authenticated_human_approval"] == "NOT_VERIFIED"
    assert inspected["activation_authorized"] is False
    assert validation.expected_manifest(False)["version"] == "a05-v3"
    for mode in (False, True):
        candidate = validation.expected_manifest(mode, candidate=True)
        baseline = retained.expected_manifest(mode)
        validation.require_complete(candidate)
        assert candidate["version"] == "a05-v5"
        # The new capture contract carries the verified parent owner. No
        # financial value or other semantic field may drift from the baseline.
        expected_domains = copy.deepcopy(baseline["completion"]["domains"])
        assert len(expected_domains["captures"]) == 10
        for row in expected_domains["captures"]:
            assert row["pipeline_ownership_state"] == "UNKNOWN"
            row["pipeline_ownership_state"] = "KNOWN"
        assert candidate["completion"]["domains"] == expected_domains
        assert candidate["completion"]["variants"] == baseline["completion"]["variants"]
        assert sum(len(v) for v in candidate["completion"]["domains"].values()) == 402
        assert candidate["candidate_validation"]["activation_authorized"] is False
        assert candidate["input_manifest_sha256"] != baseline["input_manifest_sha256"]


def _clone_declared_data(tmp_path):
    proposal = validation.candidate_proposal()
    paths = set(proposal["historical_artifacts"])
    for declaration in (
        proposal["candidate"]["artifacts"],
        proposal["independent_derivation"]["artifacts"],
        proposal["contract_change"]["configuration_sha256"],
    ):
        paths.update(declaration)
    paths.update({str(validation.PROPOSAL), "config/certification_registry.json"})
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(validation.ROOT / relative, target)
    return proposal


def test_changed_candidate_literals_fail_even_after_prior_success(tmp_path, monkeypatch):
    proposal = _clone_declared_data(tmp_path)
    monkeypatch.setattr(validation, "ROOT", tmp_path)
    assert validation.expected_manifest(True, candidate=True)["version"] == "a05-v7"
    old = "tests/e2e/single_run_certification/golden/v3/oracle-completion/core-expected.json"
    path = tmp_path / proposal["candidate"]["expectation_artifacts"][old]
    changed = json.loads(path.read_text())
    changed["combined"][0]["final_score"] = "99.9999"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ProvenanceError, match="PROVENANCE_ARTIFACT_HASH_MISMATCH"):
        validation.expected_manifest(True, candidate=True)


def test_candidate_cannot_claim_promotion_or_reuse_another_binding():
    manifest = validation.expected_manifest(False, candidate=True)
    for key, value in (("activation_authorized", True), ("proposal_digest", "0" * 64)):
        forged = copy.deepcopy(manifest)
        forged["candidate_validation"][key] = value
        with pytest.raises(retained.OracleMismatch, match="CANDIDATE_BINDING_MISMATCH"):
            validation.require_complete(forged)
    assert (
        hashlib.sha256(Path("config/ib_market_intelligence.yaml").read_bytes()).hexdigest()
        == "9d9dba1499294d15fbb8a0a3af6a29af58e7d96e93df7ce85b8797c608ea5430"
    )


@pytest.mark.parametrize("historical_adapter_imported", (False, True))
def test_native_browser_candidate_selection_refuses_a_stale_adapter(
    historical_adapter_imported,
):
    program = """
import os, sys
from pathlib import Path
os.environ.pop('SWINGLENS_CERTIFICATION_CANDIDATE_VALIDATION', None)
sys.path.insert(0, str(Path.cwd() / 'tests/e2e'))
if sys.argv[1] == 'True':
    from single_run_certification import candidate_validation
    assert candidate_validation.VERSION == 'a05-v3'
from scripts.qa.release_evidence import _enable_native_browser_candidate, EvidenceError
if sys.argv[1] == 'True':
    try:
        _enable_native_browser_candidate()
    except EvidenceError as error:
        assert 'initialized before selection' in str(error)
    else:
        raise AssertionError('historical adapter silently reused for candidate')
else:
    _enable_native_browser_candidate()
    from single_run_certification import candidate_validation
    assert candidate_validation.VERSION == 'a05-v7'
    candidate_validation.require_complete(candidate_validation.expected_manifest(True))
    assert candidate_validation.candidate_binding()['activation_authorized'] is False
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(historical_adapter_imported)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
