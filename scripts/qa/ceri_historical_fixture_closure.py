"""Complete only the historical QA data copy explicitly authorized by the user.

Production is read-only. Existing QA rows are never updated or deleted.
The allowlist is deliberately closed; a newly discovered table fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

from scripts.qa.ceri_validation_environment import REPORT, SOURCE_CLONE, credentials

ALLOWED = {"upload_runs", "setup_signal_snapshots", "work_scope_records", "technical_scores"}


def main():
    url = credentials()
    assert SOURCE_CLONE.startswith("swinglens_qa_ceri_") and SOURCE_CLONE != url.database
    env = {
        **os.environ,
        "PGHOST": url.host or "127.0.0.1",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "postgres",
        "PGPASSWORD": url.password or "",
    }
    pg = Path(r"C:\Program Files\PostgreSQL\18\bin")
    dump = REPORT / (SOURCE_CLONE + "-post-data.dump")
    listing = subprocess.check_output([str(pg / "pg_restore.exe"), "--list", str(dump)], text=True)
    selected = REPORT / "keys-restore.list"
    selected.write_text(
        "\n".join(line for line in listing.splitlines() if " FK CONSTRAINT " not in line) + "\n",
        encoding="utf-8",
    )
    with psycopg.connect(
        url.set(drivername="postgresql", database=SOURCE_CLONE).render_as_string(
            hide_password=False
        ),
        options="-c default_transaction_read_only=on",
    ) as check:
        keys_present = check.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_constraint "
            "WHERE conrelid='ceri_score_snapshots'::regclass AND contype='p')"
        ).fetchone()[0]
    restore = (
        subprocess.run(
            [
                str(pg / "pg_restore.exe"),
                "--dbname",
                SOURCE_CLONE,
                "--single-transaction",
                "--exit-on-error",
                "--use-list",
                str(selected),
                str(dump),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        if not keys_present
        else None
    )
    if restore is not None:
        (REPORT / "keys-restore.log").write_text(restore.stdout + restore.stderr, encoding="utf-8")
    if restore is not None and restore.returncode:
        raise RuntimeError("QA_KEY_RESTORE_FAILED")
    audit_path = REPORT / "authorized-fixture-copy.json"
    retained = json.loads(audit_path.read_text()) if audit_path.exists() else []
    with (
        psycopg.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False),
            options="-c default_transaction_read_only=on -c statement_timeout=300000",
        ) as source,
        psycopg.connect(
            url.set(drivername="postgresql", database=SOURCE_CLONE).render_as_string(
                hide_password=False
            ),
            options="-c statement_timeout=300000",
        ) as target,
    ):
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        specs = source.execute("""SELECT c.conrelid::regclass::text,c.confrelid::regclass::text,
            ARRAY(SELECT attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,n)
                  JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.n),
            ARRAY(SELECT attname FROM unnest(c.confkey) WITH ORDINALITY k(attnum,n)
                  JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.attnum ORDER BY k.n)
            FROM pg_constraint c WHERE c.contype='f' AND c.connamespace='public'::regnamespace
            ORDER BY c.conname""").fetchall()
        # The user explicitly authorized referenced native prerequisites after
        # technical_scores was discovered. Follow only the FK ancestor graph
        # of the named tables and manifest 41's preflight representation.
        allowed = ALLOWED | {"transition_preflight_plans", "transition_decision_handoff_manifests"}
        while True:
            expanded = allowed | {parent for child, parent, _, _ in specs if child in allowed}
            if expanded == allowed:
                break
            allowed = expanded
        (REPORT / "authorized-closure-tables.json").write_text(
            json.dumps(sorted(allowed), indent=2), encoding="utf-8"
        )

        def copy_exact(table, keys, values):
            # Technical manifests retain large complete source envelopes.
            # Bound each COPY statement rather than weakening any contents.
            batch_size = 5 if table == "technical_source_manifests" else 100
            for offset in range(0, len(values), batch_size):
                batch = values[offset : offset + batch_size]
                query = sql.SQL("SELECT * FROM {} WHERE ({}) IN ({}) ORDER BY {}").format(
                    sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, keys)),
                    sql.SQL(",").join(
                        sql.SQL("({})").format(sql.SQL(",").join(map(sql.Literal, row)))
                        for row in batch
                    ),
                    sql.SQL(",").join(map(sql.Identifier, keys)),
                )
                found = source.execute(
                    sql.SQL("SELECT count(*) FROM ({}) q").format(query)
                ).fetchone()[0]
                if found != len(batch):
                    raise RuntimeError("GENUINE_PARENT_ROWS_UNAVAILABLE: " + table)
                digest = hashlib.sha256()
                with (
                    source.cursor().copy(sql.SQL("COPY ({}) TO STDOUT").format(query)) as reader,
                    target.cursor().copy(
                        sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(table))
                    ) as writer,
                ):
                    for block in reader:
                        digest.update(block)
                        writer.write(block)
                retained.append({"table": table, "rows": found, "sha256": digest.hexdigest()})
                target.commit()
                audit_path.write_text(json.dumps(retained, indent=2), encoding="utf-8")
                print("retained genuine rows", table, found, flush=True)

        # Manifest 41 and its exact preflight parent are separately authorized.
        plan_id = source.execute(
            "SELECT preflight_plan_id FROM transition_decision_handoff_manifests WHERE id=41"
        ).fetchone()[0]
        if not target.execute(
            "SELECT 1 FROM transition_preflight_plans WHERE id=%s", (plan_id,)
        ).fetchone():
            copy_exact("transition_preflight_plans", ["id"], [(plan_id,)])
        if not target.execute(
            "SELECT 1 FROM transition_decision_handoff_manifests WHERE id=41"
        ).fetchone():
            copy_exact("transition_decision_handoff_manifests", ["id"], [(41,)])
        for _iteration in range(20):
            added = 0
            disallowed = set()
            for child, parent, ck, pk in specs:
                join = sql.SQL(" AND ").join(
                    sql.SQL("{}={}").format(sql.Identifier("f", a), sql.Identifier("p", b))
                    for a, b in zip(ck, pk, strict=True)
                )
                required = sql.SQL(" AND ").join(
                    sql.SQL("{} IS NOT NULL").format(sql.Identifier("f", a)) for a in ck
                )
                query = sql.SQL(
                    "SELECT DISTINCT {} FROM {} f LEFT JOIN {} p ON {} "
                    "WHERE {} AND {} IS NULL LIMIT 50001"
                ).format(
                    sql.SQL(",").join(sql.Identifier("f", a) for a in ck),
                    sql.Identifier(child),
                    sql.Identifier(parent),
                    join,
                    required,
                    sql.Identifier("p", pk[0]),
                )
                missing = target.execute(query).fetchall()
                if not missing:
                    continue
                if parent not in allowed:
                    disallowed.add(parent)
                    continue
                if len(missing) > 50000:
                    raise RuntimeError("QA_PARENT_COPY_BOUND_EXCEEDED: " + parent)
                copy_exact(parent, pk, missing)
                added += len(missing)
            if not added:
                if disallowed:
                    raise RuntimeError(
                        "UNAUTHORIZED_ADDITIONAL_PARENT_TABLES: " + ",".join(sorted(disallowed))
                    )
                target.commit()
                break
        else:
            raise RuntimeError("QA_PARENT_CLOSURE_DID_NOT_CONVERGE")
    (REPORT / "authorized-fixture-copy.json").write_text(
        json.dumps(retained, indent=2), encoding="utf-8"
    )
    selected = REPORT / "foreign-keys-restore.list"
    selected.write_text(
        "\n".join(line for line in listing.splitlines() if " FK CONSTRAINT " in line) + "\n",
        encoding="utf-8",
    )
    restore = subprocess.run(
        [
            str(pg / "pg_restore.exe"),
            "--dbname",
            SOURCE_CLONE,
            "--single-transaction",
            "--exit-on-error",
            "--use-list",
            str(selected),
            str(dump),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    (REPORT / "foreign-keys-restore.log").write_text(
        restore.stdout + restore.stderr, encoding="utf-8"
    )
    if restore.returncode:
        raise RuntimeError("QA_FOREIGN_KEY_VALIDATION_FAILED")
    metadata = json.loads((REPORT / "disposable-environment.json").read_text())
    metadata["post_data_restore_exit"] = 0
    metadata["authorized_fixture_copy"] = "authorized-fixture-copy.json"
    (REPORT / "disposable-environment.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print("all QA constraints restored and validated", flush=True)


if __name__ == "__main__":
    main()
