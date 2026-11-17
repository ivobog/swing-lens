"""Complete approved genuine technical prerequisites referenced by frozen Combined.

The earlier authorization covers technical_scores and their FK ancestors.
This follows exact retained run-62 references, within that existing allowlist;
production is read-only and existing target rows are never changed.
"""

from __future__ import annotations

import hashlib
import json
import shutil

import psycopg
from psycopg import sql

from scripts.qa.ceri_validation_environment import REPORT, SOURCE_CLONE, credentials


def main():
    url = credentials()
    assert url.database != SOURCE_CLONE and SOURCE_CLONE.startswith("swinglens_qa_ceri_")
    allowed = set(json.loads((REPORT / "authorized-closure-tables.json").read_text()))
    assert "technical_scores" in allowed
    archive = REPORT / "attempts/attempt-3-handoff-prerequisite"
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("context-116-certification.json", "native-certification-resumed.log"):
        if not (archive / name).exists():
            shutil.copy2(REPORT / name, archive / name)
    audit_path = REPORT / "authorized-handoff-prerequisites-copy.json"
    audit = {"database": SOURCE_CLONE, "production_access": "READ ONLY", "batches": []}
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
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
        assert not target.execute(
            "SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
            "AND pid<>pg_backend_pid()"
        ).fetchall()
        assert not target.execute(
            "SELECT 1 FROM background_jobs WHERE id>2546 AND status='RUNNING'"
        ).fetchall()
        refs = target.execute(
            "SELECT c.ticker,(c.debug_json->'source_ids'->>'technical_score_id')::bigint,"
            "(e.source_evidence_ids_json->>'technical')::bigint "
            "FROM combined_results c JOIN core_calculation_evidence e ON e.id=c.evidence_id "
            "WHERE c.run_id=62 ORDER BY c.ticker"
        ).fetchall()
        pins = {row[2] for row in refs}
        assert len(refs) == len(pins) == 447 and None not in pins
        genuine = source.execute(
            "SELECT id,ticker,calculation_context_id,evidence_id FROM technical_scores "
            "WHERE evidence_id=ANY(%s) AND run_id=62",
            (list(pins),),
        ).fetchall()
        assert len(genuine) == 447 and all(row[2] == 115 for row in genuine)
        by_ticker = {row[1]: row for row in genuine}
        assert all(
            ticker in by_ticker
            and by_ticker[ticker][3] == pin
            and (compatibility_id is None or by_ticker[ticker][0] == compatibility_id)
            for ticker, compatibility_id, pin in refs
        )
        ids = {row[0] for row in genuine}
        audit["technical_score_ids"] = sorted(ids)
        audit["immutable_combined_source_pins"] = refs
        specs = source.execute("""SELECT c.conrelid::regclass::text,c.confrelid::regclass::text,
            ARRAY(SELECT attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,n)
                  JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.n),
            ARRAY(SELECT attname FROM unnest(c.confkey) WITH ORDINALITY k(attnum,n)
                  JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.attnum ORDER BY k.n)
            FROM pg_constraint c WHERE c.contype='f' AND c.connamespace='public'::regnamespace
            ORDER BY c.conname""").fetchall()

        def predicate(keys, values):
            return sql.SQL("({}) IN ({})").format(
                sql.SQL(",").join(map(sql.Identifier, keys)),
                sql.SQL(",").join(
                    sql.SQL("({})").format(sql.SQL(",").join(map(sql.Literal, row)))
                    for row in values
                ),
            )

        def ensure(table, keys, required):
            assert table in allowed, "UNAUTHORIZED_PARENT_TABLE:" + table
            required = sorted(set(required))
            if not required:
                return
            found = target.execute(
                sql.SQL("SELECT {} FROM {} WHERE {}").format(
                    sql.SQL(",").join(map(sql.Identifier, keys)),
                    sql.Identifier(table),
                    predicate(keys, required),
                )
            ).fetchall()
            missing = sorted(set(required) - set(found))
            if not missing:
                return
            where = predicate(keys, missing)
            assert source.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE {}").format(sql.Identifier(table), where)
            ).fetchone()[0] == len(missing)
            for child, parent, child_keys, parent_keys in specs:
                if child != table:
                    continue
                parents = source.execute(
                    sql.SQL("SELECT DISTINCT {} FROM {} WHERE {}").format(
                        sql.SQL(",").join(map(sql.Identifier, child_keys)),
                        sql.Identifier(table),
                        where,
                    )
                ).fetchall()
                ensure(parent, parent_keys, [row for row in parents if None not in row])
            size = 5 if table == "technical_source_manifests" else 100
            for start in range(0, len(missing), size):
                batch = missing[start : start + size]
                query = sql.SQL("SELECT * FROM {} WHERE {} ORDER BY {}").format(
                    sql.Identifier(table),
                    predicate(keys, batch),
                    sql.SQL(",").join(map(sql.Identifier, keys)),
                )
                digest = hashlib.sha256()
                with source.cursor().copy(sql.SQL("COPY ({}) TO STDOUT").format(query)) as reader:
                    with target.cursor().copy(
                        sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(table))
                    ) as writer:
                        for block in reader:
                            digest.update(block)
                            writer.write(block)
                target.commit()
                audit["batches"].append(
                    {"table": table, "rows": len(batch), "sha256": digest.hexdigest()}
                )
                audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
                print("retained genuine prerequisites", table, len(batch), flush=True)

        ensure("technical_scores", ["id"], [(value,) for value in ids])
        hashes = (
            "SELECT id,md5(to_jsonb(t)::text) FROM technical_scores t WHERE id=ANY(%s) ORDER BY id"
        )
        assert (
            source.execute(hashes, (list(ids),)).fetchall()
            == target.execute(hashes, (list(ids),)).fetchall()
        )
        audit["all_447_technical_rows_match"] = True
        # All current QA writes are stopped; only advance counters for copied tables.
        for table in {row["table"] for row in audit["batches"]}:
            sequence = target.execute(
                "SELECT pg_get_serial_sequence(%s,'id')", (table,)
            ).fetchone()[0]
            if sequence:
                maximum = target.execute(
                    sql.SQL("SELECT coalesce(max(id),1) FROM {}").format(sql.Identifier(table))
                ).fetchone()[0]
                previous = target.execute(
                    sql.SQL("SELECT last_value FROM {}").format(
                        sql.Identifier(*sequence.split("."))
                    )
                ).fetchone()[0]
                target.execute(
                    "SELECT setval(%s::regclass,%s,true)", (sequence, max(maximum, previous))
                )
        target.commit()
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print("All 447 referenced technical scores match production; QA fixture complete", flush=True)


if __name__ == "__main__":
    main()
