"""Read-only historical integrity snapshot; repeat with the same ID ceilings."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402

directory = Path(__file__).resolve().parent
before_path = directory / "integrity-before.json"
previous = json.loads(before_path.read_text()) if before_path.exists() else None
tables = [
    "core_calculation_evidence",
    "ceri_sec_filing_documents",
    "ceri_sec_document_extractions",
    "ceri_sec_sync_states",
    "transition_decision_handoff_manifests",
    "winner_evidence_manifests",
]
with engine.connect() as connection:
    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(text("SET LOCAL statement_timeout = '60s'"))
    data = {"ceilings": {}, "historical": {}}
    for table in tables:
        ceiling = (
            previous["ceilings"][table]
            if previous
            else connection.execute(text(f"select coalesce(max(id),0) from {table}")).scalar_one()
        )
        data["ceilings"][table] = ceiling
        data["historical"][table] = dict(
            connection.execute(
                text(
                    f"select count(*) as count, md5(string_agg(id::text || md5(to_jsonb(t)::text),"
                    f" '' order by id)) as hash from {table} t where id <= :ceiling"
                ),
                {"ceiling": ceiling},
            )
            .mappings()
            .one()
        )
    for name, query in {
        "frozen_records": "select count(*) as count,md5(string_agg(resolution_hash || md5(payload_json::text),'' order by resolution_hash)) as hash from effective_configuration_records",
        "incident_anchor": "select anchor_id,fingerprint,md5(payload_json::text) as hash from execution_configuration_anchors where anchor_id='c3a654bf956deacdda507957a963743c15162eb6b6955004a377ba78c393e1f2'",
        "incident_bindings": "select md5(string_agg(binding_key || anchor_id,'' order by binding_key)) as hash from execution_configuration_bindings where binding_key in ('job:43268','pipeline:151')",
        "repaired_ingestions": "select md5(string_agg(id::text || md5(to_jsonb(t)::text),'' order by id)) as hash from ceri_ingestion_runs t where id in (53354,53355,53356,53357)",
        "repaired_source_records": "select count(*) as count,md5(string_agg(id::text || md5(to_jsonb(t)::text),'' order by id)) as hash from ceri_source_records t where ingestion_run_id in (53354,53355,53356,53357)",
    }.items():
        data["historical"][name] = dict(connection.execute(text(query)).mappings().one())
    job_ceiling = (
        previous["ceilings"]["jobs"]
        if previous
        else connection.execute(text("select max(id) from background_jobs")).scalar_one()
    )
    pipeline_ceiling = (
        previous["ceilings"]["pipelines"]
        if previous
        else connection.execute(text("select max(id) from pipeline_runs")).scalar_one()
    )
    data["ceilings"].update(jobs=job_ceiling, pipelines=pipeline_ceiling)
    data["historical"]["original_bindings"] = dict(
        connection.execute(
            text(
                "select count(*) as count,md5(string_agg(to_jsonb(t)::text,'' order by binding_key)) as hash "
                "from execution_configuration_bindings t where job_id <= :job or pipeline_run_id <= :pipeline "
                "or winner_cohort_generation_id is not null"
            ),
            {"job": job_ceiling, "pipeline": pipeline_ceiling},
        )
        .mappings()
        .one()
    )
    data["captured_at"] = str(connection.execute(text("select now()")).scalar_one())
output = directory / (
    sys.argv[1]
    if len(sys.argv) > 1
    else "integrity-after.json"
    if previous
    else "integrity-before.json"
)
output.write_text(json.dumps(data, indent=2), encoding="utf-8")
if previous:
    print({key: value == previous["historical"][key] for key, value in data["historical"].items()})
else:
    print({key: value.get("count", "fingerprinted") for key, value in data["historical"].items()})
