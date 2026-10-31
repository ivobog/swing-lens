"""Focused independent-oracle checks and controlled negative specimens."""
import copy
import csv
import os
import shutil
import subprocess
import sys
from decimal import Decimal

import pytest

from single_run_certification.golden_oracle import (
    DATA,
    ROOT,
    OracleIncomplete,
    OracleMismatch,
    artifact,
    audit_fundamental_worksheet,
    check_ownership,
    check_receipt,
    compare_records,
    decimal_equal,
    eligible_winner_children,
    expected_manifest,
    load_golden,
    require_complete,
)


def test_frozen_inputs_and_decimal_worksheet_are_deterministic():
    values = audit_fundamental_worksheet()
    assert values["fundamental_score"] == "9.1909"
    assert len(values) == 11
    for handoff in (False, True):
        expected = expected_manifest(handoff)
        assert expected == expected_manifest(handoff)
        assert expected["flags"]["setup_capture_handoff_enabled"] is handoff
        assert len(expected["domains"]["stages"]) == 13
        assert len(expected["domains"]["rank_members"]) == 50
        for row in expected["domains"]["fundamental"]:
            for key, value in values.items():
                decimal_equal(value, row[key])
    assert expected_manifest(False)["configuration_envelope_sha256"] != (
        expected_manifest(True)["configuration_envelope_sha256"]
    )


def test_expected_manifest_evaluates_with_application_imports_forbidden():
    code = """
import importlib.abc, sys
class RefuseApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app' or fullname.startswith('app.'):
            raise AssertionError('Oracle imported production: ' + fullname)
sys.meta_path.insert(0, RefuseApplication())
from single_run_certification.golden_oracle import expected_manifest, audit_fundamental_worksheet
assert audit_fundamental_worksheet()['fundamental_score'] == '9.1909'
assert expected_manifest(True)['version'] == 'a05-v3'
assert not any(n == 'app' or n.startswith('app.') for n in sys.modules)
"""
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(ROOT / "tests/e2e")}, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("wrong", ("10.1909", "9.19", "9.191", None, "NaN", True))
def test_meaningful_score_rounding_null_and_nonfinite_mutations_are_rejected(wrong):
    rows = copy.deepcopy(artifact("expected-results.json")["domains"]["fundamental"])
    changed = copy.deepcopy(rows)
    changed[0]["fundamental_score"] = wrong
    with pytest.raises(OracleMismatch):
        compare_records(rows, changed, keys=("ticker",))


def test_legitimate_null_is_preserved_and_zero_is_rejected():
    rows = artifact("negative-control-specimens.json")["null_field"]
    compare_records(rows, copy.deepcopy(rows), keys=("ticker",))
    with pytest.raises(OracleMismatch):
        compare_records(rows, [{"ticker": "NULL_CONTROL", "optional_source": 0}], keys=("ticker",))


@pytest.mark.parametrize("mutation", ("missing", "foreign-same-count", "duplicate-same-count"))
def test_exact_membership_rejects_missing_foreign_and_duplicate_members(mutation):
    rows = artifact("expected-results.json")["domains"]["raw"]
    changed = copy.deepcopy(rows)
    if mutation == "missing":
        changed.pop()
    elif mutation == "foreign-same-count":
        changed[-1]["ticker"] = "FOREIGN_ONLY"
    else:
        changed[-1] = copy.deepcopy(changed[0])
    with pytest.raises(OracleMismatch, match="IDENTITY_MISMATCH"):
        compare_records(rows, changed, keys=("ticker",))


@pytest.mark.parametrize("mutation", ("profile", "rank", "score"))
def test_independently_tied_profile_control_rejects_wrong_assignment(mutation):
    rows = artifact("negative-control-specimens.json")["tied_profile_ranks"]
    changed = copy.deepcopy(rows)
    changed[0][mutation] = {"profile": "defensive_quality", "rank": 2, "score": "7.9"}[mutation]
    with pytest.raises(OracleMismatch):
        compare_records(rows, changed, keys=("profile", "ticker"))


def test_wrong_exchange_session_is_rejected():
    rows = artifact("expected-results.json")["domains"]["technical_indicators"]
    changed = copy.deepcopy(rows)
    changed[0]["session"] = "2026-10-24"
    with pytest.raises(OracleMismatch):
        compare_records(rows, changed, keys=("ticker",), tolerance="0.00000001")


def test_changed_financial_contents_with_identical_ticker_count_break_hash(tmp_path):
    clone = tmp_path / "v1"
    shutil.copytree(DATA, clone)
    with (clone / "universe.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns, rows = reader.fieldnames, list(reader)
    assert len(rows) == 10
    rows[0]["Revenue growth %, TTM YoY"] = "99"
    with (clone / "universe.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(OracleMismatch, match="GOLDEN_INPUT_HASH_MISMATCH: universe.csv"):
        load_golden(clone)


def test_all_frozen_receipts_are_valid_and_future_receipt_is_rejected():
    expected = expected_manifest(False)
    for record in expected["domains"]["ceri_sources"]:
        check_receipt(record, cutoff=expected["business_time"]["ceri_cutoff"])
    with pytest.raises(OracleMismatch, match="FUTURE_OR_UNKNOWN_RECEIPT"):
        check_receipt({"known_at": "2026-10-26T20:32:01Z"},
                      cutoff=expected["business_time"]["ceri_cutoff"])


@pytest.mark.parametrize("field", ("input_manifest_sha256", "configuration_envelope_sha256",
                                   "source", "session", "market_cutoff", "ceri_cutoff"))
def test_wrong_source_context_or_configuration_ownership_is_rejected(field):
    manifest = expected_manifest(False)
    expected = {key: manifest[key] for key in (
        "input_manifest_sha256", "configuration_envelope_sha256",
    )}
    expected.update(source="universe.csv:row:1:BHE", session="2026-10-26",
                    **{k: manifest["business_time"][k] for k in ("market_cutoff", "ceri_cutoff")})
    actual = {**expected, field: "FOREIGN"}
    with pytest.raises(OracleMismatch, match="SOURCE_CONTEXT_CONFIGURATION_MISMATCH"):
        check_ownership(actual, expected)


@pytest.mark.parametrize("domain,keys", (("forward", ("ticker", "entry_model", "horizon")),
                                        ("target_stop", ("ticker", "definition")),
                                        ("estimate", ("ticker", "definition", "kind"))))
def test_missing_downstream_identity_obligation_is_detected(domain, keys):
    expected = eligible_winner_children("CONTROL_ONLY", eligible=True)
    changed = copy.deepcopy(expected[domain])
    changed.pop()
    with pytest.raises(OracleMismatch):
        compare_records(expected[domain], changed, keys=keys)


def test_incorrect_forward_horizon_is_detected():
    expected = eligible_winner_children("CONTROL_ONLY", eligible=True)["forward"]
    changed = copy.deepcopy(expected)
    changed[-1]["horizon"] = 21
    with pytest.raises(OracleMismatch):
        compare_records(expected, changed, keys=("ticker", "entry_model", "horizon"))


def test_legitimate_exclusion_and_empty_provider_earnings_are_not_failures():
    result = eligible_winner_children("CONTROL_ONLY", eligible=False,
                                     exclusion_reason="MISSING_TECHNICAL")
    assert result["acquisition"]["status"] == "EXCLUDED"
    assert result["forward"] == result["target_stop"] == result["estimate"] == []
    policy = artifact("expected-results.json")["conditional_obligations"]["ceri_earnings"]
    assert policy == {"records": [], "reason": "FROZEN_PROVIDER_EXPLICIT_NO_DATA", "failure": False}
    with pytest.raises(OracleMismatch):
        eligible_winner_children("CONTROL_ONLY", eligible=False)


def test_partial_oracle_cannot_claim_complete_expected_truth():
    with pytest.raises(OracleIncomplete, match="INDEPENDENT_ORACLE_INCOMPLETE"):
        require_complete(artifact("expected-results.json"))


def test_indicator_literals_follow_frozen_arithmetic_inputs():
    bars = artifact("market-data.json")["cache"]
    expected = artifact("expected-results.json")["domains"]["technical_indicators"]
    for row in expected:
        close = [Decimal(r["close"]) for r in bars[row["ticker"]]]
        decimal_equal(row["sma50"], sum(close[-50:])/50)
        decimal_equal(row["stock_roc_medium"], (close[-1]/close[-64]-1)*100)
        decimal_equal(row["atr"], "2.1")


def test_business_clock_seam_leaves_native_worker_and_operational_time_live():
    code = """
import time
from datetime import UTC, datetime
from app.services import background_worker, market_calculation_context_service as contexts
from app.services.ceri import source_record_service, orchestration
from app.services.ceri.providers import manual_provider
from app.services.ceri.enums import CeriDataset
from single_run_certification.golden_runtime import business_inputs
handler = background_worker._execute_full_pipeline_job
native_datetime = background_worker.datetime
source_datetime = source_record_service.datetime
before = datetime.now(UTC)
with business_inputs('postgresql://unused@127.0.0.1/swinglens_pytest_cert_clock_only'):
    clock = contexts.MarketClockService()
    cutoff = clock.cutoff_for(datetime.now(UTC), reason='TRANSITION_PREFLIGHT_RESERVED')
    assert cutoff.cutoff_at.isoformat() == '2026-10-26T20:30:00+00:00'
    assert source_record_service.datetime is source_datetime
    assert source_record_service._utcnow() > before
    assert orchestration.datetime is source_datetime
    record = manual_provider._raw_provider_record(
        provider='manual', provider_terms_version='manual-fixture-1.0',
        dataset=CeriDataset.ESTIMATES, row={'provider_record_id':'clock-only','ticker':'BHE'})
    assert record.retrieved_at.isoformat() == '2026-10-26T20:29:00+00:00'
    start = time.monotonic()
    time.sleep(.02)
    assert time.monotonic() > start
    assert background_worker.datetime.now(UTC) > before
    assert background_worker.datetime is native_datetime
    assert background_worker.default_job_handlers()['FULL_PIPELINE'] is handler
assert source_record_service.datetime is source_datetime
"""
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(
                             (str(ROOT), str(ROOT / "tests/e2e")))}, timeout=45)
    assert run.returncode == 0, run.stdout + run.stderr


def test_declared_availability_clocks_preserve_native_pit_validation_and_job_time():
    from datetime import UTC, datetime

    from app.models.tables import (
        BackgroundJob,
        CombinedResult,
        FundamentalScore,
        MarketRegimeSnapshot,
        RankingResult,
        RawCompanyRow,
        SectorRotationSnapshot,
        TechnicalScore,
        UploadRun,
    )
    from app.services.winner_probability.feature_extractor import (
        WinnerFeatureExtractionError,
        _validate_point_in_time_sources,
    )
    from single_run_certification.golden_runtime import supply_availability_defaults

    inputs = load_golden()
    decision_at = datetime.fromisoformat(inputs["business_time"]["decision_at"])
    future = datetime(2030, 1, 1, tzinfo=UTC)
    upload = UploadRun(uploaded_at=future, processed_at=future)
    rows = [upload, *[model(created_at=future) for model in (
        RawCompanyRow, FundamentalScore, TechnicalScore, CombinedResult,
        RankingResult, MarketRegimeSnapshot, SectorRotationSnapshot,
    )]]
    with pytest.raises(WinnerFeatureExtractionError, match="after capture time"):
        _validate_point_in_time_sources(decision_at, *rows)
    job = BackgroundJob(created_at=future)
    for row in [*rows, job]:
        supply_availability_defaults(row, inputs["source_availability"], decision_at)
    _validate_point_in_time_sources(decision_at, *rows)
    assert upload.uploaded_at.isoformat() == inputs["source_availability"]["upload_at"]
    assert job.created_at == future


def test_utf8_source_headers_preserve_both_declared_dollar_volume_values():
    aliases = artifact("configuration.json")["column_aliases.yaml"]
    with (DATA / "universe.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        assert len(reader.fieldnames) == len(set(reader.fieldnames))
        rows = list(reader)
    for field, days, value in (("dollar_volume_10d", 10, "40000000"),
                               ("dollar_volume_60d", 60, "50000000")):
        header = f"Price {chr(0xD7)} average volume, {days} days"
        assert aliases[field][0] == header
        assert all(row[header] == value for row in rows)


def test_rehashing_mutated_inputs_cannot_redefine_an_existing_version(tmp_path):
    import hashlib
    import json

    clone = tmp_path / "v3"
    shutil.copytree(DATA, clone)
    changed = (clone / "universe.csv").read_bytes().replace(b"40000000", b"41000000")
    (clone / "universe.csv").write_bytes(changed)
    manifest = json.loads((clone / "manifest.json").read_text())
    manifest["artifacts"]["universe.csv"] = hashlib.sha256(changed).hexdigest()
    (clone / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    with pytest.raises(OracleMismatch, match="GOLDEN_MANIFEST_HASH_MISMATCH"):
        load_golden(clone)


def test_frozen_csv_preserves_existing_financial_exemplar_and_exact_population(tmp_path):
    from single_run_certification.fixtures import write_canonical_csv

    path = tmp_path / "existing-input.csv"
    write_canonical_csv(path)  # Input construction only; no scoring or output rows.
    with path.open(encoding="utf-8", newline="") as stream:
        exemplar = next(csv.DictReader(stream))
    expected_inputs = [
        {**exemplar, "Symbol": ticker, "Description": f"{ticker} frozen fixture"}
        for ticker in load_golden()["tickers"]
    ]
    with (DATA / "universe.csv").open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == expected_inputs


def test_fixture_identity_uses_frozen_content_manifest():
    from single_run_certification.fixtures import _fixture_hash
    from single_run_certification.golden_oracle import canonical_hash
    from single_run_certification.golden_runtime import business_inputs

    with business_inputs("postgresql://unused@127.0.0.1/swinglens_pytest_cert_identity_only"):
        assert _fixture_hash({"count": 10}) == canonical_hash(load_golden())
        assert _fixture_hash({"count": 10}) == _fixture_hash({"count": 999})



def test_allowlisted_child_installs_frozen_business_scope_without_freezing_liveness(tmp_path):
    code = """
import time
from datetime import UTC, datetime
before = datetime.now(UTC)
import certification_server
from app.services import background_worker, market_calculation_context_service as contexts
from single_run_certification.golden_runtime import current_manifest
assert current_manifest()["version"] == "a05-v3"
cutoff = contexts.MarketClockService().cutoff_for(
    datetime.now(UTC), reason="TRANSITION_PREFLIGHT_RESERVED")
assert cutoff.cutoff_at.isoformat() == "2026-10-26T20:30:00+00:00"
assert cutoff.latest_completed_session.isoformat() == "2026-10-26"
assert background_worker.datetime is datetime
assert background_worker.datetime.now(UTC) >= before
start = time.monotonic()
time.sleep(max(.05, 2 * time.get_clock_info("monotonic").resolution))
assert time.monotonic() > start
"""
    env = {**os.environ,
           "DATABASE_URL": "postgresql+psycopg://unused@127.0.0.1/swinglens_pytest_cert_child_clock",
           "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
           "RUNTIME_MODE": "CERTIFICATION", "CERTIFICATION_GOLDEN_VERSION": "a05-v3",
           "CERTIFICATION_WINNER_CONFIGURATION": str(ROOT / "config/winner_probability.yaml"),
           "CERTIFICATION_FROZEN_CERI_PROVIDERS": "true",
           "CERTIFICATION_OUTCOME_NOW": "2027-01-15T22:00:00+00:00",
           "CERTIFICATION_IB_LOG": str(tmp_path / "unused-provider-log.jsonl"),
           "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "tests/e2e"),
                                         str(ROOT / "tests/e2e/single_run_certification")))}
    child = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                           capture_output=True, text=True, timeout=45)
    assert child.returncode == 0, child.stdout + child.stderr
