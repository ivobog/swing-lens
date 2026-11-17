"""Run CERI validation with local credentials confined to disposable databases.

No secret is printed or retained. Production is inspected in read-only
transactions; all DDL and application execution use QA.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs/remediation/ceri-context-116"
SOURCE_CLONE = "swinglens_qa_ceri_context_1794788997961"


def credentials():
    primary = ROOT.parents[1]
    return make_url(dotenv_values(primary / ".env")["DATABASE_URL"])


def prepare():
    url = credentials()
    # Resume the already retained historical QA fixture. Copying its TOAST
    # storage again is unnecessary and caused excessive IO on this workstation.
    name = SOURCE_CLONE
    assert name.startswith("swinglens_qa_ceri_") and name != url.database
    with psycopg.connect(
        url.set(drivername="postgresql", database=name).render_as_string(hide_password=False),
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    ) as conn:
        expected = json.loads((REPORT / "authoritative-input/evidence.json").read_text())[
            "results"
        ]["proofRows"]
        actual = conn.execute("""SELECT s.run_id,s.id,s.company_id,s.ticker,
            s.calculation_context_id,
            s.evidence_hash,s.evidence_id,e.calculation_identity_fingerprint,e.evidence_key
            FROM ceri_score_snapshots s JOIN core_calculation_evidence e ON e.id=s.evidence_id
            WHERE s.run_id IN (61,62) ORDER BY s.id""").fetchall()
        columns = [
            "run_id",
            "snapshot_id",
            "company_id",
            "ticker",
            "calculation_context_id",
            "evidence_hash",
            "evidence_id",
            "calculation_identity_fingerprint",
            "evidence_key",
        ]
        assert [
            {k: row[k] for k in columns}
            for row in sorted(expected, key=lambda row: row["snapshot_id"])
        ] == [dict(zip(columns, row, strict=True)) for row in actual]
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_index WHERE indrelid='ceri_score_snapshots'::regclass"
            ).fetchone()[0]
            == 0
        )
    (REPORT / "disposable-environment.json").write_text(
        json.dumps(
            {
                "database": name,
                "source_clone": SOURCE_CLONE,
                "historical_proof_rows_verified": len(actual),
                "post_data_restore_exit": None,
                "production_access": "read-only schema export",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        PGHOST=url.host or "127.0.0.1",
        PGPORT=str(url.port or 5432),
        PGUSER=url.username or "postgres",
        PGPASSWORD=url.password or "",
    )
    pg = Path(r"C:\Program Files\PostgreSQL\18\bin")
    dump = REPORT / (name + "-post-data.dump")
    subprocess.run(
        [
            str(pg / "pg_dump.exe"),
            "--dbname",
            url.database,
            "--format=custom",
            "--section=post-data",
            "--lock-wait-timeout=10s",
            "--file",
            str(dump),
        ],
        env={**env, "PGOPTIONS": "-c default_transaction_read_only=on"},
        check=True,
    )
    result = subprocess.run(
        [
            str(pg / "pg_restore.exe"),
            "--dbname",
            name,
            "--single-transaction",
            "--exit-on-error",
            str(dump),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    (REPORT / "schema-restore.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    (REPORT / "disposable-environment.json").write_text(
        json.dumps(
            {
                "database": name,
                "source_clone": SOURCE_CLONE,
                "post_data_restore_exit": result.returncode,
                "production_access": "read-only schema export",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("disposable database:", name, "schema restore exit:", result.returncode)
    return result.returncode


def run_tests(args):
    url = credentials()
    metadata_file = REPORT / "disposable-environment.json"
    name = (
        json.loads(metadata_file.read_text())["database"]
        if metadata_file.exists()
        else SOURCE_CLONE
    )
    assert name.startswith("swinglens_qa_ceri_") and name != url.database
    env = dict(os.environ)
    env["SWINGLENS_TEST_POSTGRES_ADMIN_URL"] = url.set(
        drivername="postgresql", database="postgres"
    ).render_as_string(hide_password=False)
    env["DATABASE_URL"] = url.set(database=name).render_as_string(hide_password=False)
    env["SWINGLENS_DATABASE_SAFETY_CONTEXT"] = "DISPOSABLE_TEST"
    env["PYTHONPATH"] = str(ROOT)
    # Let the repository's evidence guard allocate an invocation-owned output.
    launcher = (
        "import sys; sys.path.insert(0,'tests'); "
        "from evidence_output import output_root; import pytest; "
        "raise SystemExit(pytest.main(sys.argv[1:]+"
        "['--junitxml='+str(output_root()/'results.xml')]))"
    )
    return subprocess.run([sys.executable, "-c", launcher, *args], cwd=ROOT, env=env).returncode


def inspect_historical():
    """Read-only fingerprints and missing FK identifiers; export no row bodies."""
    url = credentials()
    observed = {}
    queries = {
        "scores": """SELECT s.id,s.calculation_context_id,s.evidence_hash,
            md5(to_jsonb(s)::text),md5(to_jsonb(e)::text),
            (SELECT md5(coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.id),'[]'::jsonb)::text)
             FROM ceri_evidence_dispositions d WHERE d.ceri_snapshot_id=s.id)
            FROM ceri_score_snapshots s LEFT JOIN core_calculation_evidence e ON e.id=s.evidence_id
            WHERE s.run_id IN (61,62) ORDER BY s.id""",
        "manifest41": """SELECT id,manifest_fingerprint,md5(to_jsonb(m)::text),
            jsonb_array_length(manifest_json->'ceri_score_snapshots')
            FROM transition_decision_handoff_manifests m WHERE id=41""",
        "pipeline66": "SELECT id,status,md5(to_jsonb(p)::text) FROM pipeline_runs p WHERE id=66",
        "job2546": "SELECT id,status,md5(to_jsonb(j)::text) FROM background_jobs j WHERE id=2546",
        "context116": """SELECT id,pipeline_run_id,md5(to_jsonb(c)::text)
            FROM market_calculation_contexts c WHERE id=116""",
    }
    with (
        psycopg.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False),
            options="-c default_transaction_read_only=on -c statement_timeout=45000",
        ) as source,
        psycopg.connect(
            url.set(drivername="postgresql", database=SOURCE_CLONE).render_as_string(
                hide_password=False
            ),
            options="-c default_transaction_read_only=on -c statement_timeout=45000",
        ) as target,
    ):
        for label, conn in [("production_read_only", source), ("disposable", target)]:
            observed[label] = {
                key: conn.execute(query).fetchall() for key, query in queries.items()
            }
        observed["historical_equal"] = observed["production_read_only"] == observed["disposable"]
        fks = source.execute("""SELECT c.conname,c.conrelid::regclass::text,
            c.confrelid::regclass::text,
            ARRAY(SELECT attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,n)
                  JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.n),
            ARRAY(SELECT attname FROM unnest(c.confkey) WITH ORDINALITY k(attnum,n)
                  JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.attnum ORDER BY k.n)
            FROM pg_constraint c
            WHERE c.contype='f' AND c.connamespace='public'::regnamespace""").fetchall()
        missing = []
        for constraint, child, parent, ck, pk in fks:

            def relation(alias, key):
                return sql.Identifier(alias, key)

            join = sql.SQL(" AND ").join(
                sql.SQL("{}={}").format(relation("f", a), relation("p", b))
                for a, b in zip(ck, pk, strict=True)
            )
            required = sql.SQL(" AND ").join(
                sql.SQL("{} IS NOT NULL").format(relation("f", a)) for a in ck
            )
            query = sql.SQL(
                "SELECT DISTINCT {} FROM {} f LEFT JOIN {} p ON {} "
                "WHERE {} AND {} IS NULL LIMIT 101"
            ).format(
                sql.SQL(",").join(relation("f", a) for a in ck),
                sql.Identifier(child),
                sql.Identifier(parent),
                join,
                required,
                relation("p", pk[0]),
            )
            rows = target.execute(query).fetchall()
            if rows:
                missing.append(
                    {
                        "constraint": constraint,
                        "child": child,
                        "parent": parent,
                        "parent_keys": pk,
                        "missing_identifiers_up_to_101": rows,
                    }
                )
        observed["missing_fk_parents"] = missing
    path = REPORT / "historical-state-and-prerequisites.json"
    path.write_text(json.dumps(observed, indent=2, default=str), encoding="utf-8")
    print(
        "historical equal:",
        observed["historical_equal"],
        "missing FK relationships:",
        len(missing),
        flush=True,
    )
    for row in missing:
        print(
            row["child"],
            "requires",
            row["parent"],
            len(row["missing_identifiers_up_to_101"]),
            flush=True,
        )
    return 0


def migration_preflight():
    url = credentials()
    queries = "\n".join(
        line
        for line in (REPORT / "preflight.sql").read_text().splitlines()
        if not line.lstrip().startswith("--")
    )
    outputs = []
    with psycopg.connect(
        url.set(drivername="postgresql", database=SOURCE_CLONE).render_as_string(
            hide_password=False
        ),
        options="-c default_transaction_read_only=on -c statement_timeout=45000",
    ) as conn:
        for query in queries.split(";"):
            if query.strip():
                cursor = conn.execute(query)
                outputs.append(
                    {
                        "sql": query.strip(),
                        "columns": [c.name for c in cursor.description],
                        "rows": cursor.fetchall(),
                    }
                )
    (REPORT / "migration-preflight.json").write_text(
        json.dumps(outputs, indent=2, default=str), encoding="utf-8"
    )
    for result in outputs:
        print(result["sql"].splitlines()[0], "rows:", len(result["rows"]), flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "inspect", "preflight", "test"])
    parsed, remaining = parser.parse_known_args()
    raise SystemExit(
        prepare()
        if parsed.operation == "prepare"
        else inspect_historical()
        if parsed.operation == "inspect"
        else migration_preflight()
        if parsed.operation == "preflight"
        else run_tests(remaining)
    )
