"""Observed-size transport proof, paired with separate native authority tests."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config

from app.database_safety import run_guarded_alembic_upgrade

pytestmark = pytest.mark.integration


def _isolated_probe_environment(database_url, source_root):
    # Preserve the genuine active identity only for the read-only safety check.
    # Import-time app engines and any spawned compute worker target disposable.
    active_reference = os.environ["DATABASE_URL"]
    run_guarded_alembic_upgrade(Config("alembic.ini"), database_url)
    return {
        **os.environ, "DATABASE_URL": database_url, "SECTOR_SCALE_DATABASE_URL": database_url,
        "SECTOR_SCALE_ACTIVE_DATABASE_URL": active_reference,
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "JOB_WORKER_ENABLED": "false", "MARKET_DATA_PREWARM_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED": "false",
        "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED": "false",
        "PYTHONPATH": os.pathsep.join((str(source_root), str(source_root / "tests"))),
    }


@pytest.mark.slow
@pytest.mark.destructive
def test_observed_466_company_source_shape_stays_below_memory_budget(
    disposable_postgres_database, tmp_path,
):
    output = Path(os.environ.get("SECTOR_MEMORY_SCALE_OUTPUT", tmp_path / "memory-scale.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).parents[2]
    environment = _isolated_probe_environment(disposable_postgres_database, source_root)
    process = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "sector_memory_scale_probe.py"),
         "--output", str(output)], env=environment, cwd=source_root,
        text=True, capture_output=True, timeout=900,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(output.read_text())
    assert report["result"] == "PASS"
    assert report["provider_calls"] == 0
    assert report["financial_rows_equal"] and report["canonical_fingerprint_equal"]
    assert report["fixture"]["evidence_rows"] == 3728
    assert report["peak_measured_bytes"] < report["critical_budget_bytes"]


@pytest.mark.slow
@pytest.mark.destructive
def test_full_native_466_company_five_profile_sector_publication_memory(
    disposable_postgres_database, tmp_path,
):
    output = Path(os.environ.get(
        "SECTOR_FULL_NATIVE_SCALE_OUTPUT", tmp_path / "full-native-scale.json"
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).parents[2]
    environment = _isolated_probe_environment(disposable_postgres_database, source_root)
    process = subprocess.run(
        [sys.executable, str(source_root / "tests/sector_full_native_scale_probe.py"),
         "--output", str(output)], env=environment, cwd=source_root,
        text=True, capture_output=True, timeout=7200,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(output.read_text())
    assert report["status"] == "PASS"
    assert report["provider_calls"] == 0
    assert report["fixture"]["companies"] == 466
    assert len(report["fixture"]["profiles"]) == 5
    assert report["published_sector_rows"] == 11
    assert report["canonical_and_financial_parity"]
    assert report["peak_sample"]["measured_bytes"] < report["critical_budget_bytes"]
    counts = {row["artifact_kind"]: row["rows"] for row in report["evidence"]}
    assert counts == {"FUNDAMENTAL": 466, "TECHNICAL": 466, "COMBINED": 466,
                      "RANKING": 2330, "REGIME": 1, "SECTOR": 1}
