"""Adversarial tests of the trusted review and disposable worker boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from single_run_certification import candidate_validation as validation
from single_run_certification import golden_oracle as retained
from single_run_certification.frozen_ceri import require_c06_offline_runtime
from test_golden_update_governance import isolated as fixture_isolated
from test_golden_update_governance import save

from scripts.qa import golden_provenance as review

isolated = fixture_isolated


@pytest.fixture
def reviewed_contract(isolated, monkeypatch):
    """Exercise trusted sealing with small independent test pins."""
    root, baseline = isolated
    inputs = review._read(root / review.GOLDEN / "manifest.json")
    supplement = review._read(root / review.GOLDEN / "oracle-completion/manifest.json")
    inputs["version"], supplement["expectation_revision"] = review.C06_VERSIONS
    mappings = {
        old: (review.C06_GOLDEN / Path(old).relative_to(review.GOLDEN)).as_posix()
        for old in baseline["artifacts"]
        if old.endswith(("/expected-results.json", "-expected.json"))
    }
    artifacts = {}
    for old, new in mappings.items():
        artifacts[new] = save(root / new, review._read(root / old))

    guidance_path = (
        review.C06_GOLDEN / "oracle-completion/ceri-expected.json"
    ).as_posix()
    replay_path = (review.C06_GOLDEN / "provider-replay.json").as_posix()
    runtime_path = (review.C06_GOLDEN / "runtime-settings.json").as_posix()
    ceri = review._read(root / guidance_path)
    filings = []
    for index, row in enumerate(ceri["guidance"], start=1):
        ticker = row["ticker"]
        accession = f"0000000000-26-{index:06d}"
        locator = f"{accession}/{ticker.lower()}-synthetic-8k.htm#paragraph-2"
        key = f"{accession}:{locator}"
        body = f"synthetic test {ticker}"
        filings.append({
            "ticker": ticker, "cik": f"09000000{index:02d}", "accession": accession,
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "issuer_proved": True, "legacy_record_key": row["record_key"],
            "independent_expected": {"record_key": key, "locator": locator},
        })
        row["record_key"] = key
    artifacts[guidance_path] = save(root / guidance_path, ceri)
    replay = {"sec": {"offline_filings": {
        "schema": "swinglens.a04-offline-sec-filings.v1",
        "source_class": "SYNTHETIC_TEST_ONLY_NOT_EDGAR",
        "filings": filings,
    }}}
    artifacts[replay_path] = save(root / replay_path, replay)
    runtime = {
        **retained.artifact("runtime-settings.json"),
        "sec_document_incremental_mode": "SHADOW",
    }
    artifacts[runtime_path] = save(root / runtime_path, runtime)
    worksheet = save(root / review.C06_WORKSHEET, {"synthetic": True})
    monkeypatch.setattr(review, "C06_OFFLINE_SHA256", artifacts[replay_path])
    monkeypatch.setattr(review, "C06_WORKSHEET_SHA256", worksheet)
    monkeypatch.setattr(validation, "ROOT", root)
    for manifest, folder in (
        (inputs, review.C06_GOLDEN),
        (supplement, review.C06_GOLDEN / "oracle-completion"),
    ):
        for name in manifest["artifacts"]:
            path = (folder / name).as_posix()
            if path not in artifacts:
                baseline_path = review.GOLDEN / Path(path).relative_to(review.C06_GOLDEN)
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                data = (root / baseline_path).read_bytes()
                target.write_bytes(data)
                artifacts[path] = hashlib.sha256(data).hexdigest()
            manifest["artifacts"][name] = artifacts[path]

    input_name = (review.C06_GOLDEN / "manifest.json").as_posix()
    completion = (review.C06_GOLDEN / "oracle-completion/manifest.json").as_posix()
    artifacts[input_name] = save(root / input_name, inputs)
    artifacts[completion] = save(root / completion, supplement)
    pins = {
        "input_manifest_sha256": artifacts[input_name],
        "completion_manifest_sha256": artifacts[completion],
        "input_identity": retained.canonical_hash(inputs),
    }
    monkeypatch.setattr(review, "C06_INPUTS", pins)
    proposal = {
        "candidate": {
            "input_version": review.C06_VERSIONS[0],
            "expectation_revision": review.C06_VERSIONS[1],
            "input_manifest": input_name,
            "completion_manifest": completion,
            "expectation_artifacts": mappings,
            "artifacts": artifacts,
            **pins,
        },
        "contract_change": {"source_revision": review.C06_SOURCE},
        "independent_derivation": {"artifacts": {review.C06_WORKSHEET: worksheet}},
    }
    return root, proposal, replay, guidance_path, runtime


def test_reviewed_c06_rekeys_and_runtime_flags(reviewed_contract):
    root, proposal, _, _, runtime = reviewed_contract
    review._require_candidate_complete(proposal, root)
    for handoff in (False, True):
        flags = retained.expected_manifest(handoff)["flags"]
        assert flags["sec_document_incremental_mode"] == "OFF"
        assert validation._candidate_runtime_flags(proposal, runtime, handoff) == {
            **flags, "sec_document_incremental_mode": "SHADOW",
        }
        changed = {**runtime, "ceri_enabled": False}
        with pytest.raises(retained.OracleMismatch, match="SPECIMEN_CHANGE_REQUIRES_REVIEW"):
            validation._candidate_runtime_flags(proposal, changed, handoff)
        historical = {"candidate": {"input_version": "a05-v7", "expectation_revision": "a05b-r7"}}
        with pytest.raises(retained.OracleMismatch, match="SPECIMEN_CHANGE_REQUIRES_REVIEW"):
            validation._candidate_runtime_flags(historical, runtime, handoff)
        assert validation._candidate_runtime_flags(
            historical, retained.artifact("runtime-settings.json"), handoff,
        ) == flags


@pytest.mark.parametrize(
    "fault",
    ("extra", "missing", "duplicate", "unrelated", "math", "ticker", "cik",
     "accession", "paragraph", "hash", "unattributed", "self_resealed"),
)
def test_c06_rejects_unreviewed_candidate_payload(reviewed_contract, fault):
    root, proposal, replay, guidance_path, _ = reviewed_contract
    if fault in {"ticker", "cik", "accession", "paragraph", "hash", "unattributed"}:
        rejection = "PROVENANCE_ARTIFACT_HASH_MISMATCH"
        row = replay["sec"]["offline_filings"]["filings"][0]
        field = {"hash": "body_sha256", "unattributed": "issuer_proved"}.get(fault, fault)
        if fault == "paragraph":
            row["independent_expected"]["locator"] += "-wrong"
        else:
            row[field] = False if fault == "unattributed" else "wrong"
        filename = (review.C06_GOLDEN / "provider-replay.json").as_posix()
        proposal["candidate"]["artifacts"][filename] = save(root / filename, replay)
    else:
        rejection = "GITHUB_INDEPENDENT_ORACLE_INCOMPLETE"
        values = review._read(root / guidance_path)
        rows = values["guidance"]
        if fault == "extra":
            rows.append({**rows[-1], "record_key": "extra"})
        elif fault == "missing":
            rows.pop()
        elif fault == "duplicate":
            rows[-1] = rows[0]
        elif fault == "unrelated":
            values["estimates"][0]["record_key"] += "-unauthorized"
        else:
            rows[0]["point_value"] = "999.000000"
        digest = save(root / guidance_path, values)
        proposal["candidate"]["artifacts"][guidance_path] = digest
        if fault == "self_resealed":
            rejection = "C06_GOLDEN_TRUSTED_FIXTURE_MISMATCH"
            completion_path = proposal["candidate"]["completion_manifest"]
            manifest = review._read(root / completion_path)
            manifest["artifacts"]["ceri-expected.json"] = digest
            proposal["candidate"]["completion_manifest_sha256"] = save(
                root / completion_path, manifest,
            )
    with pytest.raises(review.ProvenanceError, match=rejection):
        review._require_candidate_complete(proposal, root)


def test_reviewed_c06_environment_is_offline_disposable():
    database_url = "postgresql+psycopg://worker@127.0.0.1:55490/swinglens_pytest_cert_c06"
    env = {
        "DATABASE_URL": database_url,
        "RUNTIME_MODE": "CERTIFICATION",
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "DISPOSABLE_TEST",
        "SWINGLENS_CERTIFICATION_CANDIDATE_VALIDATION": "1",
        "SWINGLENS_CERTIFICATION_PROVIDER_MODE": "deterministic",
        "CERTIFICATION_FROZEN_CERI_PROVIDERS": "true",
        "CERTIFICATION_GOLDEN_VERSION": "a05-v8",
        "SEC_DOCUMENT_INCREMENTAL_MODE": "SHADOW",
    }
    require_c06_offline_runtime(env, database_url)
    for key, value in {
        "RUNTIME_MODE": "NORMAL",
        "SWINGLENS_DATABASE_SAFETY_CONTEXT": "PRODUCTION",
        "SWINGLENS_CERTIFICATION_PROVIDER_MODE": "live",
        "SWINGLENS_CERTIFICATION_CANDIDATE_VALIDATION": "0",
        "CERTIFICATION_FROZEN_CERI_PROVIDERS": "false",
        "CERTIFICATION_GOLDEN_VERSION": "a05-v7",
        "SEC_DOCUMENT_INCREMENTAL_MODE": "OFF",
    }.items():
        with pytest.raises(RuntimeError, match="REQUIRES_OFFLINE_DISPOSABLE_SHADOW"):
            require_c06_offline_runtime({**env, key: value}, database_url)
    for url in (
        database_url.replace("127.0.0.1", "production.example"),
        database_url.replace("swinglens_pytest_cert_c06", "swinglens"),
        database_url + "?host=production.example",
    ):
        with pytest.raises(RuntimeError, match="REQUIRES_OFFLINE_DISPOSABLE_SHADOW"):
            require_c06_offline_runtime({**env, "DATABASE_URL": url}, url)
