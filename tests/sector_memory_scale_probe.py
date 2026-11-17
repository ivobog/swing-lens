"""Isolated native JSON transport probe; synthetic rows are deliberately unsealed.

This verifies bounded ORM residency and exact serialization at observed production
sizes. It does not certify a 466-company financial calculation or publication.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import threading
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database_safety import assert_disposable_database
from app.models.tables import CoreCalculationEvidence, TechnicalSourceManifest
from app.services import core_mutation_authority
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.core_mutation_authority import (
    SECTOR_EVIDENCE_RESIDENT_BYTES,
    SECTOR_EVIDENCE_RESIDENT_LIMIT,
    sector_evidence_read_scope,
    sector_evidence_rows,
)
from app.services.process_memory import process_memory_snapshot
from app.services.sector_rotation_dtos import (
    SectorRotationDecision,
    SectorRotationSnapshotDto,
    SectorUniverseMetrics,
)
from app.services.sector_serialization import canonical_sector_payload, sector_dataclass_payload

OBSERVED_PAYLOAD_BYTES = 488_347_349
OBSERVED_MANIFEST_BYTES = 68_003_948
MIB = 1024**2


def _producer_payload(target):
    count = 400
    while True:
        value = {
            "fixture_type": "UNSEALED_SYNTHETIC_TRANSPORT_ONLY",
            "features": [
                {"name": f"feature-{index}-@@ROW00000@@", "value": index / 7,
                 "observations": [index, index + 1, index + 2],
                 "stats": {"minimum": -index, "maximum": index + 4,
                           "valid": True, "source": f"source-@@ROW00000@@-{index}"},
                 "flags": ["OBSERVED", "COMPLETE", "SYNTHETIC"]}
                for index in range(count)
            ],
        }
        encoded = json.dumps(value, separators=(",", ":"))
        if len(encoded) >= target:
            return encoded
        count = int(count * target / len(encoded)) + 2


def _seed(engine):
    with engine.begin() as db:
        db.execute(text("INSERT INTO upload_runs(id,filename,row_count,status) "
                        "VALUES(7,'synthetic-scale.csv',466,'COMPLETED')"))
        db.execute(text("INSERT INTO pipeline_runs(id,upload_run_id,status) VALUES(1,7,'RUNNING')"))
        db.execute(text("INSERT INTO market_calculation_contexts(id,pipeline_run_id,upload_run_id,"
                        "cutoff_at,exchange_timezone,latest_completed_session,calendar_version,"
                        "bar_readiness_version,cutoff_reason) "
                        "VALUES(1,1,7,now(),'America/New_York',"
                        "'2026-11-09','SYNTHETIC','SYNTHETIC','SYNTHETIC_TRANSPORT')"))
        for kind, count, size in (
            ("FUNDAMENTAL", 466, 114_600), ("TECHNICAL", 466, 144_700),
            ("COMBINED", 466, 138_650), ("RANKING", 2330, 133_700),
        ):
            db.execute(text("INSERT INTO core_calculation_evidence(artifact_kind,run_id,ticker,"
                "ranking_profile,calculation_identity_fingerprint,calculation_identity_json,"
                "payload_fingerprint,payload_json,source_evidence_ids_json,"
                "evidence_key,calculated_at) "
                "SELECT CAST(:kind AS text),7,'SYN'||lpad(n::text,4,'0'),"
                "CASE WHEN CAST(:kind AS text)='RANKING' "
                "THEN 'profile-'||((n-1)/466)::text ELSE NULL END,repeat(md5(n::text),2),"
                "jsonb_build_object('fixture','UNSEALED_TRANSPORT','row',n),repeat('0',64),"
                "CAST(replace(:payload,'@@ROW00000@@',lpad(n::text,12,'0')) AS jsonb),"
                "'{}'::jsonb,CAST(:kind AS text)||':'||n::text,now() "
                "FROM generate_series(1,:count) AS n"),
                {"kind": kind, "payload": _producer_payload(size), "count": count})
        db.execute(text("INSERT INTO technical_source_manifests(run_id,pipeline_run_id,"
            "calculation_context_id,manifest_digest,manifest_json,source_count,state_count) "
            "SELECT 7,1,1,repeat('1',64),jsonb_build_object('fixture','SYNTHETIC_TRANSPORT',"
            "'sources',jsonb_agg(jsonb_build_object('source',s,'states',states))),903,680736 "
            "FROM generate_series(1,903) AS s CROSS JOIN LATERAL (SELECT jsonb_agg("
            "jsonb_build_object('version_id',(s*1000)+n,'bar_date','2026-11-09','revision',0,"
            "'digest',md5(s::text||':'||n::text),'status','OBSERVED')) AS states "
            "FROM generate_series(1,CASE WHEN s=903 THEN 628 ELSE 754 END) AS n) AS nested"))


def _dto(pins):
    permissions = {
        f"source-{pin['evidence_id']}": {
            "decision": {"producer_evidence_id": pin["evidence_id"], "allowed": True},
            "readiness": {"context": "frozen-synthetic", "warnings": ["SYNTHETIC"],
                          "state": {"native": True, "source_kind": pin["artifact_kind"]}},
        } for pin in pins
    }
    shared = {"financial_inputs": pins, "raw_inputs": [
        {"id": index, "fingerprint": hashlib.sha256(str(index).encode()).hexdigest()}
        for index in range(466)
    ]}
    rows = []
    for index in range(11):
        rows.append(SectorUniverseMetrics(
            sector=f"Sector {index}", sector_slug=f"sector-{index}", ticker_count=42 + (index < 4),
            universe_share=1 / 11, average_fundamental_score=7.1, average_technical_score=6.9,
            average_final_score=7.0, average_profile_score=7.2, top_counts={"top25": 2},
            setup_distribution={"buyable": 12}, warning_distribution={"incomplete": 1},
            buyable_count=12, watch_count=20, danger_count=10, buyable_share=12 / 42,
            watch_share=20 / 42, danger_share=10 / 42, clean_pullback_count=5, breakout_count=4,
            vcp_count=3, tight_base_breakout_count=2, extended_or_overheated_count=1,
            missing_fundamental_count=0, missing_technical_count=1,
            debug={"native_source_manifest": shared,
                   "contextual_consumer_eligibility": permissions},
        ))
    dto = SectorRotationSnapshotDto(
        run_id=7, as_of_date="2026-11-09", mode="universe", calculation_version="SYNTHETIC",
        config_version="synthetic", config_hash="0" * 64, default_ranking_profile="profile-0",
        rows=[SectorRotationDecision(
            sector=row.sector, sector_slug=row.sector_slug, final_score=7.0 + index / 100,
            universe_score=7.0, etf_score=6.9, rotation_state="LEADING", permission="ALLOW",
            position_size_multiplier=1.0, confidence="high", rank=index + 1,
            previous_rank=index + 2, rank_change=1, score_change=0.1,
        ) for index, row in enumerate(rows)], universe_rows=rows,
    )
    return replace(dto, debug={"native_input_envelope": {
        "universe_rows": sector_dataclass_payload(rows), "etf_rows": [],
    }})


def main(output):
    source_root = Path(__file__).resolve().parents[1]
    assert Path(core_mutation_authority.__file__).resolve().is_relative_to(source_root)
    url = os.environ["SECTOR_SCALE_DATABASE_URL"]
    assert_disposable_database(
        url, active_database_url=os.environ["SECTOR_SCALE_ACTIVE_DATABASE_URL"]
    )
    engine = create_engine(url)
    print("Preparing synthetic native transport rows", flush=True)
    _seed(engine)
    report = {"started_at": datetime.now(UTC).isoformat(), "source_root": str(source_root),
        "scope": (
        "Native PostgreSQL ORM transport/residency and serialization only; unsealed synthetic "
        "sources do not authorize financial publication."), "provider_calls": 0}
    with engine.connect() as db:
        report["fixture"] = dict(db.execute(text("SELECT count(*) AS evidence_rows,"
            "sum(octet_length(payload_json::text)) AS payload_json_text_bytes "
            "FROM core_calculation_evidence")).mappings().one())
        report["fixture"]["manifest_json_text_bytes"] = db.scalar(text(
            "SELECT octet_length(manifest_json::text) FROM technical_source_manifests"))
    assert report["fixture"]["evidence_rows"] == 3728
    assert report["fixture"]["payload_json_text_bytes"] >= OBSERVED_PAYLOAD_BYTES
    assert report["fixture"]["manifest_json_text_bytes"] >= OBSERVED_MANIFEST_BYTES
    stop = threading.Event()
    phase = {"name": "start"}
    samples = []

    def sample():
        while not stop.is_set():
            observed = process_memory_snapshot()
            samples.append({"phase": phase["name"], "rss_bytes": observed.rss_bytes,
                            "private_bytes": observed.private_bytes})
            stop.wait(0.05)

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        with Session(engine) as db, sector_evidence_read_scope(db):
            phase["name"] = "native-bounded-evidence-transport"
            addresses = list(db.scalars(select(CoreCalculationEvidence.id)))
            records = sector_evidence_rows(db, addresses)
            pins = []
            for address in records:
                row = records[address]
                assert row.payload_json["fixture_type"] == "UNSEALED_SYNTHETIC_TRANSPORT_ONLY"
                pins.append({"evidence_id": row.id, "artifact_kind": row.artifact_kind,
                             "ticker": row.ticker, "run_id": row.run_id})
            report["transport"] = {"peak_resident_count": records.peak_resident_count,
                "peak_resident_bytes": records.peak_resident_bytes,
                "full_load_batches": records.full_load_batches}
            assert records.peak_resident_count <= SECTOR_EVIDENCE_RESIDENT_LIMIT
            assert records.peak_resident_bytes <= SECTOR_EVIDENCE_RESIDENT_BYTES
            phase["name"] = "shared-technical-manifest-resident"
            manifest = db.scalar(select(TechnicalSourceManifest)).manifest_json
            assert len(manifest["sources"]) == 903
            phase["name"] = "sector-dto-serialization-parity"
            dto = _dto(pins)
            new = sector_dataclass_payload(dto)
            old = asdict(dto)
            assert new == old
            report["financial_rows_equal"] = new["rows"] == old["rows"]
            phase["name"] = "legacy-canonical-fingerprint-reference"
            expected = Canonical.fingerprint(old)
            del old
            gc.collect()
            phase["name"] = "scoped-streaming-canonical-fingerprint"
            actual = Canonical.fingerprint_streaming(canonical_sector_payload(new))
            assert actual == expected
            report["canonical_fingerprint_equal"] = True
            report["canonical_fingerprint"] = actual
            report["shared_manifest_states"] = sum(
                len(row["states"]) for row in manifest["sources"]
            )
            assert report["shared_manifest_states"] == 680736
    finally:
        stop.set()
        thread.join(5)
        engine.dispose()
    def measured(item):
        return item["private_bytes"] or item["rss_bytes"]

    peak = max(samples, key=measured)
    report["peak_sample"] = peak
    report["peak_measured_bytes"] = measured(peak)
    report["measurement_basis"] = (
        "private_bytes" if peak["private_bytes"] is not None else "rss_bytes"
    )
    report["sample_count"] = len(samples)
    report["critical_budget_bytes"] = 6144 * MIB
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["result"] = "PASS" if measured(peak) < 6144 * MIB else "FAIL"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    assert report["result"] == "PASS"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
