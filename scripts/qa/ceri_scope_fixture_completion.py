"""Copy the 447 genuine frozen scope members explicitly authorized by the user.

Production is read-only. Only missing children of this one retained scope are
copied; existing rows and financial evidence are never changed. Repair QA
sequence counters and defer only the abandoned continuation claim through the
normal control API, retaining the committed 447 snapshots.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta

import psutil
import psycopg
from psycopg import sql

from scripts.qa.ceri_validation_environment import REPORT, SOURCE_CLONE, credentials

SCOPE = "64e85af7ad2fd70281a845671b1b3de85f5121242a7c35f9aa1c7794465eab29"
MEMBERS_MD5 = "4a7be91ac8c27e6c5a12e1ed9b57c9c1"
PARENT_MD5 = "c3961f00219df7867f441f211e123624"


def main():
    source = credentials()
    assert source.database != SOURCE_CLONE and SOURCE_CLONE.startswith("swinglens_qa_ceri_")
    target_url = source.set(database=SOURCE_CLONE).render_as_string(hide_password=False)
    os.environ.update(DATABASE_URL=target_url, SWINGLENS_DATABASE_SAFETY_CONTEXT="DISPOSABLE_TEST")
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.orm import Session

    from app.database_safety import assert_disposable_database
    from app.models.tables import BackgroundJob, PipelineRun
    from app.services.background_job_service import mark_job_deferred

    assert_disposable_database(target_url, active_database_url=source)
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        args = process.info["cmdline"] or []
        if process.info["name"] == "python.exe" and any(
            item
            in {
                "scripts.qa.ceri_context_116_certification",
                "scripts.qa.ceri_context_116_continuation",
            }
            for item in args
        ):
            raise RuntimeError("QA_CERTIFICATION_WRITER_STILL_ALIVE")
    archive = REPORT / "attempts/attempt-3"
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("context-116-certification.json", "native-certification.log"):
        retained = archive / name
        if not retained.exists():
            shutil.copy2(REPORT / name, retained)
    audit = {"database": SOURCE_CLONE, "scope_id": SCOPE, "production_access": "READ ONLY"}
    fingerprint = """SELECT count(*),md5(coalesce(
        jsonb_agg(to_jsonb(m) ORDER BY ordinal),'[]'::jsonb)::text)
        FROM work_scope_members m WHERE scope_id=%s"""
    with (
        psycopg.connect(
            source.set(drivername="postgresql").render_as_string(hide_password=False),
            options="-c default_transaction_read_only=on -c statement_timeout=30000",
        ) as production,
        psycopg.connect(
            source.set(drivername="postgresql", database=SOURCE_CLONE).render_as_string(
                hide_password=False
            ),
            options="-c statement_timeout=30000",
        ) as target,
    ):
        production.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        assert not target.execute(
            "SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid<>pg_backend_pid()"
        ).fetchall()
        for connection in (production, target):
            assert (
                connection.execute(
                    "SELECT md5(to_jsonb(s)::text) FROM work_scope_records s WHERE scope_id=%s",
                    (SCOPE,),
                ).fetchone()[0]
                == PARENT_MD5
            )
        assert production.execute(fingerprint, (SCOPE,)).fetchone() == (447, MEMBERS_MD5)
        before = target.execute(fingerprint, (SCOPE,)).fetchone()
        assert before[0] in (0, 447)
        audit["before"] = before
        if before[0] == 0:
            query = sql.SQL(
                "COPY (SELECT * FROM work_scope_members WHERE scope_id={} "
                "ORDER BY ordinal) TO STDOUT"
            ).format(sql.Literal(SCOPE))
            digest = hashlib.sha256()
            with production.cursor().copy(query) as reader:
                with target.cursor().copy("COPY work_scope_members FROM STDIN") as writer:
                    for block in reader:
                        digest.update(block)
                        writer.write(block)
            audit["copied"] = 447
            audit["copy_sha256"] = digest.hexdigest()
        else:
            audit["copied"] = 0
        after = target.execute(fingerprint, (SCOPE,)).fetchone()
        assert after == (447, MEMBERS_MD5)
        assert (
            target.execute(
                "SELECT count(*) FROM work_scope_members "
                "WHERE scope_id=%s AND subject_type<>'TICKER'",
                (SCOPE,),
            ).fetchone()[0]
            == 0
        )
        assert (
            target.execute(
                "SELECT count(*) FROM ceri_evidence_dispositions d JOIN "
                "ceri_score_snapshots s ON s.id=d.ceri_snapshot_id "
                "WHERE s.calculation_context_id=116"
            ).fetchone()[0]
            == 0
        )
        audit["after"] = after
        target.commit()
        # Retain a successful data-copy audit even if a later control/counter
        # prerequisite is incomplete. Retrying the copy then remains explicit.
        (REPORT / "authorized-scope-members-copy.json").write_text(
            json.dumps(audit, indent=2), encoding="utf-8"
        )
    engine = create_engine(target_url, hide_parameters=True)
    with Session(engine) as db:
        running = list(db.scalars(select(BackgroundJob).where(BackgroundJob.status == "RUNNING")))
        assert all(
            job.id == 2550
            or (job.id <= 2546 and job.lease_expires_at < datetime.now(UTC))
            for job in running
        )
        audit["retained_expired_historical_running_jobs"] = [
            job.id for job in running if job.id != 2550
        ]
        owner = db.get(PipelineRun, 66)
        assert owner.status == "WAITING_FOR_CERI_COMPLETION"
        child = db.get(BackgroundJob, 2550)
        assert child.scope_id == SCOPE and child.payload_json["pipeline_run_id"] == 66
        if child.status == "RUNNING":
            mark_job_deferred(
                db,
                child,
                delay=timedelta(0),
                reason="DISPOSABLE_QA_GENUINE_FROZEN_SCOPE_MEMBERS_RESTORED",
                execution_token=child.execution_token,
            )
        assert child.status == "QUEUED"
        audit["job_2550"] = "DEFERRED_FOR_FRESH_CLAIM"
        columns = db.execute(
            text("""SELECT table_name,column_name FROM information_schema.columns
            WHERE table_schema='public' AND
            (column_default LIKE 'nextval(%' OR is_identity='YES')""")
        ).all()
        quote = engine.dialect.identifier_preparer.quote
        counters = []
        for table, column in columns:
            sequence = db.scalar(
                text("SELECT pg_get_serial_sequence(:table,:column)"),
                {"table": table, "column": column},
            )
            if sequence:
                maximum = db.scalar(
                    text("SELECT coalesce(max(" + quote(column) + "),1) FROM " + quote(table))
                )
                sequence_sql = ".".join(quote(part) for part in sequence.split("."))
                previous = db.scalar(text("SELECT last_value FROM " + sequence_sql))
                value = max(maximum, previous)
                db.execute(
                    text("SELECT setval(CAST(:sequence AS regclass),:maximum,true)"),
                    {"sequence": sequence, "maximum": value},
                )
                counters.append(
                    {"table": table, "column": column, "before": previous, "after": value}
                )
        audit["qa_sequence_counters"] = counters
        db.commit()
    engine.dispose()
    (REPORT / "authorized-scope-members-copy.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "copied": audit["copied"],
                "fingerprint": MEMBERS_MD5,
                "job_2550": audit["job_2550"],
                "sequence_counters": len(counters),
            }
        )
    )


if __name__ == "__main__":
    main()
