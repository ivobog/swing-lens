"""Hash complete immutable SETUP evidence rows without exporting financial bodies."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "evidence"
sys.path.insert(0, str(ROOT))


def read_pins(url):
    from sqlalchemy import create_engine, text

    engine = create_engine(
        url,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=60000"},
        isolation_level="REPEATABLE READ",
    )
    try:
        with engine.connect() as db:
            assert db.scalar(text("show transaction_read_only")) == "on"
            rows = list(
                db.scalars(
                    text(
                        "select to_jsonb(e) from core_calculation_evidence e "
                        "join setup_signal_snapshots s on s.evidence_id=e.id "
                        "where s.run_id=41 and e.artifact_kind='SETUP' order by e.id"
                    )
                )
            )
            assert len(rows) == 88
            return {
                str(row["id"]): hashlib.sha256(
                    json.dumps(row, sort_keys=True, default=str).encode()
                ).hexdigest()
                for row in rows
            }
    finally:
        engine.dispose()


def main(action):
    from sqlalchemy.engine import make_url

    from app.database_safety import assert_disposable_database
    from app.settings import get_settings

    source = make_url(get_settings().database_url)
    assert source.database == "swinglens"
    baseline = OUT / "original-setup-evidence-full-row-pins.json"
    if action == "baseline-and-clone":
        assert not baseline.exists(), "Never overwrite the original evidence baseline"
        original = read_pins(source)
        baseline.write_text(json.dumps(original, indent=2), encoding="utf-8")
    else:
        original = json.loads(baseline.read_text())
    if action in {"baseline-and-clone", "clone-after"}:
        meta = json.loads((OUT / "production-clone.json").read_text())
        target = source.set(database=meta["database"])
        assert_disposable_database(target, active_database_url=source)
    else:
        target = source
    actual = read_pins(target)
    result = {
        "scope": "clone" if action in {"baseline-and-clone", "clone-after"} else "live",
        "complete_evidence_rows": len(actual),
        "complete_evidence_row_hashes_match": original == actual,
        "source_bodies_exported": False,
        "read_only_enforced": True,
        "actual_row_sha256": actual,
    }
    name = (
        "clone-setup-evidence-preservation.json"
        if action in {"baseline-and-clone", "clone-after"}
        else "production-setup-evidence-preservation.json"
    )
    (OUT / name).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "actual_row_sha256"}))
    assert original == actual


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("baseline-and-clone", "clone-after", "live-after"))
    main(parser.parse_args().action)
