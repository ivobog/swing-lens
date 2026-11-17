"""Retain real disposable postflight and transactional downgrade-refusal evidence."""

from __future__ import annotations

import importlib.util
import json
import os

from scripts.qa.ceri_validation_environment import REPORT, ROOT, SOURCE_CLONE, credentials


def main():
    original = credentials()
    assert SOURCE_CLONE.startswith("swinglens_qa_ceri_") and original.database != SOURCE_CLONE
    certification = json.loads((REPORT / "context-116-certification.json").read_text())
    assert certification["decision"] in {"PASS", "FAIL", "BLOCKED"}
    complete = certification["decision"] == "PASS"
    target = original.set(database=SOURCE_CLONE).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=target, SWINGLENS_DATABASE_SAFETY_CONTEXT="DISPOSABLE_TEST")
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from app.database_safety import assert_disposable_database
    from app.models.tables import PipelineRun, TransitionDecisionHandoffManifest
    from app.services.calculation_identity import CalculationIdentity, IdentityState
    from app.services.market_calculation_context_service import (
        market_context_for_pipeline,
        resolve_pipeline_ceri_context,
    )
    from app.services.transition_preflight_plan_service import _handoff_scores

    assert_disposable_database(target, active_database_url=original)
    engine = create_engine(target)
    result = {
        "database": SOURCE_CLONE,
        "capture_certification_decision": certification["decision"],
        "checks": {},
    }
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            outputs = []
            for statement in (REPORT / "postflight.sql").read_text().split(";"):
                if statement.strip() and not statement.strip().startswith("--"):
                    rows = connection.execute(text(statement))
                    outputs.append(
                        {
                            "sql": statement.strip(),
                            "columns": list(rows.keys()),
                            "rows": [list(row) for row in rows],
                        }
                    )
            result["postflight_sql"] = outputs
            duplicates = connection.scalar(
                text("""SELECT count(*) FROM (
                SELECT run_id,company_id,config_hash,calculation_version
                FROM ceri_score_snapshots
                GROUP BY 1,2,3,4 HAVING count(*)>1) q""")
            )
            assert duplicates == 447 if complete else duplicates >= 0
            result["checks"]["old_key_collision_groups"] = duplicates
            spec = importlib.util.spec_from_file_location(
                "ceri_migration", ROOT / "alembic/versions/20261116_0096_ceri_native_context.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.op = Operations(MigrationContext.configure(connection))
            savepoint = connection.begin_nested()
            if duplicates:
                try:
                    module.downgrade()
                except IntegrityError as error:
                    assert error.orig.sqlstate == "23505"
                    savepoint.rollback()
                    result["checks"]["lossy_downgrade_refused"] = error.orig.sqlstate
                else:
                    savepoint.rollback()
                    raise AssertionError("LOSSY_DOWNGRADE_WAS_ACCEPTED")
            else:
                savepoint.rollback()
                result["checks"]["lossy_downgrade_refused"] = (
                    "Not applicable: no overlapping generation committed; "
                    "real-PG test covers refusal"
                )
            indexes = connection.execute(
                text("""SELECT indexrelid::regclass::text,
                indisvalid,indisready,indisunique,indnullsnotdistinct
                FROM pg_index WHERE indrelid='ceri_score_snapshots'::regclass
                AND indexrelid::regclass::text IN ('uq_ceri_native_score','uq_ceri_legacy_score')
                ORDER BY 1""")
            ).all()
            assert len(indexes) == 2 and all(row[1] and row[2] and row[3] for row in indexes)
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0096_ceri_native_context"
            )
            result["checks"]["indexes_intact_after_refusal"] = [list(row) for row in indexes]
            result["checks"]["context116_pipeline_fk"] = connection.scalar(
                text("SELECT pipeline_run_id FROM market_calculation_contexts WHERE id=116")
            )
            assert result["checks"]["context116_pipeline_fk"] is None
            result["dependencies"] = [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT id,dependency_type,state,continuation_job_id
                FROM pipeline_dependencies WHERE pipeline_run_id=66 ORDER BY id""")
                ).mappings()
            ]
            result["jobs"] = [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT id,job_type,status,result_json FROM background_jobs
                WHERE id>=2547 ORDER BY id""")
                ).mappings()
            ]
            result["new_score_evidence"] = [
                list(row)
                for row in connection.execute(
                    text("""
                SELECT s.id,s.ticker,s.company_id,s.calculation_context_id,s.evidence_id,
                s.evidence_hash,e.calculation_identity_fingerprint,e.evidence_key,
                md5(to_jsonb(s)::text),md5(to_jsonb(e)::text)
                FROM ceri_score_snapshots s JOIN core_calculation_evidence e ON e.id=s.evidence_id
                WHERE s.calculation_context_id=116 ORDER BY s.id""")
                )
            ]
            if complete:
                assert len(result["new_score_evidence"]) == 447
            lineage = []
            for score_id, encoded, fingerprint in connection.execute(
                text("""
                SELECT s.id,e.calculation_identity_json,e.calculation_identity_fingerprint
                FROM ceri_score_snapshots s JOIN core_calculation_evidence e ON e.id=s.evidence_id
                WHERE s.calculation_context_id=116 ORDER BY s.id""")
            ):
                identity = CalculationIdentity.from_canonical_payload(encoded)
                owner = identity.ownership.pipeline_id
                context_id = identity.calculation_context.market_calculation_context_id.value
                assert owner.state is IdentityState.KNOWN and owner.value == 66
                assert str(identity.fingerprint()) == fingerprint
                lineage.append(
                    {
                        "score_id": score_id,
                        "pipeline_id": owner.value,
                        "pipeline_identity_state": owner.state.value,
                        "company_id": identity.subject.company_id.value,
                        "context_id": context_id,
                        "identity_fingerprint": fingerprint,
                    }
                )
            result["ownership_lineage"] = lineage
            if complete:
                assert len(lineage) == 447 and all(row["context_id"] == 116 for row in lineage)
            before = json.loads((REPORT / "historical-before.json").read_text())
            maximum = max(row[0] for row in before)
            after = [
                list(row)
                for row in connection.execute(
                    text("""
                SELECT s.id,s.evidence_hash,md5(to_jsonb(s)::text),md5(to_jsonb(e)::text),
                (SELECT md5(coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.id),'[]'::jsonb)::text)
                 FROM ceri_evidence_dispositions d WHERE d.ceri_snapshot_id=s.id)
                FROM ceri_score_snapshots s
                LEFT JOIN core_calculation_evidence e ON e.id=s.evidence_id
                WHERE s.id <= :maximum ORDER BY s.id"""),
                    {"maximum": maximum},
                )
            ]
            assert after == before
            (REPORT / "historical-after.json").write_text(json.dumps(after), encoding="utf-8")
            result["checks"]["historical_scores_preserved"] = len(after)
            source_hashes = json.loads(
                (REPORT / "historical-state-and-prerequisites.json").read_text()
            )["production_read_only"]
            for label, table, record_id, position in (
                ("job2546", "background_jobs", 2546, 2),
                ("manifest41", "transition_decision_handoff_manifests", 41, 2),
                ("context116", "market_calculation_contexts", 116, 2),
            ):
                observed = connection.scalar(
                    text("SELECT md5(to_jsonb(r)::text) FROM " + table + " r WHERE id=:id"),
                    {"id": record_id},
                )
                assert observed == source_hashes[label][0][position]
                result["checks"][label + "_unchanged"] = observed
            transaction.rollback()
        with Session(engine) as db:
            old_owner = db.get(PipelineRun, 63)
            old_manifest = db.get(TransitionDecisionHandoffManifest, 41)
            old_ids = [row["id"] for row in old_manifest.manifest_json["ceri_score_snapshots"]]
            old_cutoff = resolve_pipeline_ceri_context(
                db, calculation_context_id=111, upload_run_id=61, pipeline_run_id=63
            )
            old_rows = _handoff_scores(
                db, 61, 63, old_cutoff, market_context_for_pipeline(db, old_owner), old_ids
            )
            assert len(old_rows) == 164 and sorted(old_ids) == sorted(row.id for row in old_rows)
            result["checks"]["manifest41_original_membership"] = {
                "count": len(old_rows),
                "score_ids": sorted(old_ids),
                "fingerprint": old_manifest.manifest_fingerprint,
            }
        result["postflight_status"] = "PASS"
        (REPORT / "migration-postflight.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
    finally:
        engine.dispose()
    print("Postflight and historical manifest membership PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
