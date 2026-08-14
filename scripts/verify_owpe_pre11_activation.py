"""Verify persisted pre-1.1 activation invariants against PostgreSQL."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal

EXPECTED_REQUEST_KEY = (
    "d555a6b40f63092527b7997932686342e1be32d0582cbfe5d49f71122c6afa8c"
)
EXPECTED_MANIFEST_HASH = (
    "dda5048538702f6eb9ae42f2aebefc86f19b988e3f2aa494e34900f27d462f54"
)


def _mutation_is_rejected(statement: str) -> bool:
    with SessionLocal() as db:
        try:
            db.execute(text(statement))
            db.flush()
        except DBAPIError as exc:
            db.rollback()
            return "append-only" in str(exc.orig)
        db.rollback()
        return False


def main() -> int:
    with SessionLocal() as db:
        result = db.execute(
            text(
                """
                SELECT
                    count(*) AS decisions,
                    count(*) FILTER (WHERE training_allowed) AS approved,
                    count(DISTINCT request_key) AS request_key_count,
                    min(request_key) AS request_key,
                    count(DISTINCT evidence_manifest_hash) AS manifest_hash_count,
                    min(evidence_manifest_hash) AS manifest_hash
                FROM winner_training_eligibility_decisions
                """
            )
        ).mappings().one()
        replays = db.execute(
            text(
                """
                SELECT count(*) AS replay_count,
                       count(*) FILTER (WHERE primary_winner) AS wins,
                       min(id) AS min_id,
                       max(id) AS max_id
                FROM winner_training_outcome_replays
                """
            )
        ).mappings().one()
        db.rollback()

    checks = {
        "decisions": result["decisions"] == 8859,
        "approved": result["approved"] == 390,
        "request_key": (
            result["request_key_count"] == 1
            and result["request_key"] == EXPECTED_REQUEST_KEY
        ),
        "manifest_hash": (
            result["manifest_hash_count"] == 1
            and result["manifest_hash"] == EXPECTED_MANIFEST_HASH
        ),
        "replays": replays["replay_count"] == 390,
        "wins": replays["wins"] == 145,
        "decision_update_rejected": _mutation_is_rejected(
            "UPDATE winner_training_eligibility_decisions "
            "SET classified_by = classified_by WHERE id = 1"
        ),
        "decision_delete_rejected": _mutation_is_rejected(
            "DELETE FROM winner_training_eligibility_decisions WHERE id = 1"
        ),
        "replay_update_rejected": _mutation_is_rejected(
            "UPDATE winner_training_outcome_replays "
            "SET replayed_by = replayed_by WHERE id = 1"
        ),
        "replay_delete_rejected": _mutation_is_rejected(
            "DELETE FROM winner_training_outcome_replays WHERE id = 1"
        ),
    }
    payload = {
        "checks": checks,
        "counts": dict(result) | dict(replays),
        "matches": all(checks.values()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if payload["matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
