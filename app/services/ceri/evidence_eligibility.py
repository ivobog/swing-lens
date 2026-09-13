from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriEvidenceDisposition, CeriScoreSnapshot

ELIGIBLE = "ELIGIBLE"
EXCLUDED = "EXCLUDED"


@dataclass(frozen=True)
class EvidenceDispositionRequest:
    ceri_snapshot_id: int
    disposition: str
    reason_code: str
    incident_reference: str
    actor_source: str
    notes: str | None = None
    metadata_json: dict[str, Any] | None = None

    def values(self) -> dict[str, Any]:
        metadata = self.metadata_json or {}
        semantic = {
            "ceri_snapshot_id": self.ceri_snapshot_id,
            "disposition": self.disposition,
            "reason_code": self.reason_code,
            "incident_reference": self.incident_reference,
            "actor_source": self.actor_source,
            "notes": self.notes,
            "metadata_json": metadata,
        }
        return {
            **semantic,
            "event_fingerprint": hashlib.sha256(
                json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }


def eligible_snapshot_predicate(snapshot: Any = CeriScoreSnapshot) -> Any:
    """Return the canonical SQL predicate for decision-use eligibility.

    The most recent disposition is ordered deterministically by (created_at, id).
    Absence of any disposition preserves legacy eligibility.
    """

    effective = (
        select(CeriEvidenceDisposition.disposition)
        .where(CeriEvidenceDisposition.ceri_snapshot_id == snapshot.id)
        .order_by(
            CeriEvidenceDisposition.created_at.desc(),
            CeriEvidenceDisposition.id.desc(),
        )
        .limit(1)
        .correlate(snapshot)
        .scalar_subquery()
    )
    return func.coalesce(effective, ELIGIBLE) == ELIGIBLE


def effective_disposition_subquery(name: str | None = None) -> Any:
    return (
        select(
            CeriEvidenceDisposition.ceri_snapshot_id,
            CeriEvidenceDisposition.disposition,
        )
        .distinct(CeriEvidenceDisposition.ceri_snapshot_id)
        .order_by(
            CeriEvidenceDisposition.ceri_snapshot_id,
            CeriEvidenceDisposition.created_at.desc(),
            CeriEvidenceDisposition.id.desc(),
        )
        .subquery(name)
    )


def apply_snapshot_eligibility(
    statement: Any,
    *,
    snapshot: Any = CeriScoreSnapshot,
    name: str | None = None,
) -> Any:
    """Apply the canonical set-based eligibility join to a snapshot statement."""

    effective = effective_disposition_subquery(name)
    return statement.outerjoin(
        effective,
        effective.c.ceri_snapshot_id == snapshot.id,
    ).where(func.coalesce(effective.c.disposition, ELIGIBLE) == ELIGIBLE)


def eligible_snapshot_select(
    *entities: Any,
    snapshot: Any = CeriScoreSnapshot,
    name: str | None = None,
) -> Any:
    return apply_snapshot_eligibility(
        select(*(entities or (snapshot,))),
        snapshot=snapshot,
        name=name,
    )


def effective_disposition_by_snapshot(
    db: Session,
    snapshot_ids: Iterable[int] | None = None,
) -> dict[int, str]:
    requested = None if snapshot_ids is None else {int(value) for value in snapshot_ids}
    collections = getattr(db, "collections", None)
    if not isinstance(collections, dict):
        collections = getattr(db, "rows_by_model", None)
    if isinstance(collections, dict):
        rows = list(collections.get(CeriEvidenceDisposition, ()))
        if requested is not None:
            rows = [row for row in rows if row.ceri_snapshot_id in requested]
        latest: dict[int, CeriEvidenceDisposition] = {}
        for row in rows:
            current = latest.get(row.ceri_snapshot_id)
            key = (row.created_at, row.id or 0)
            if current is None or key > (current.created_at, current.id or 0):
                latest[row.ceri_snapshot_id] = row
        return {snapshot_id: row.disposition for snapshot_id, row in latest.items()}

    if not callable(getattr(db, "execute", None)):
        return {}
    ranked = select(
        CeriEvidenceDisposition.ceri_snapshot_id,
        CeriEvidenceDisposition.disposition,
        func.row_number()
        .over(
            partition_by=CeriEvidenceDisposition.ceri_snapshot_id,
            order_by=(
                CeriEvidenceDisposition.created_at.desc(),
                CeriEvidenceDisposition.id.desc(),
            ),
        )
        .label("disposition_rank"),
    )
    if requested is not None:
        if not requested:
            return {}
        ranked = ranked.where(CeriEvidenceDisposition.ceri_snapshot_id.in_(requested))
    ranked = ranked.subquery("ranked_ceri_evidence_dispositions")
    rows = db.execute(
        select(ranked.c.ceri_snapshot_id, ranked.c.disposition).where(
            ranked.c.disposition_rank == 1
        )
    )
    return {int(row[0]): str(row[1]) for row in rows}


def filter_eligible_snapshots(
    db: Session,
    snapshots: Iterable[CeriScoreSnapshot],
) -> list[CeriScoreSnapshot]:
    rows = list(snapshots)
    dispositions = effective_disposition_by_snapshot(
        db, (row.id for row in rows if row.id is not None)
    )
    return [row for row in rows if dispositions.get(row.id, ELIGIBLE) != EXCLUDED]


def append_evidence_dispositions(
    db: Session,
    requests: Sequence[EvidenceDispositionRequest],
) -> int:
    """Append disposition events atomically; exact duplicate requests are no-ops."""

    if not requests:
        return 0
    values = [request.values() for request in requests]
    if any(value["disposition"] not in {ELIGIBLE, EXCLUDED} for value in values):
        raise ValueError("unsupported CERI evidence disposition")
    statement = (
        pg_insert(CeriEvidenceDisposition)
        .values(values)
        .on_conflict_do_nothing(
            constraint="uq_ceri_evidence_dispositions_event_fingerprint"
        )
        .returning(CeriEvidenceDisposition.id)
    )
    return len(list(db.scalars(statement)))
