"""Persistence and receipt validation for a pre-active SEC extraction."""

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import insert, select

from app.models.ceri_tables import CeriIngestionRunSourceRecord, CeriSourceRecord
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.enums import CeriDataset
from app.services.ceri.preparation_visibility import PREPARATION_SOURCE
from app.services.ceri.source_record_service import source_record_content_hash


@dataclass(frozen=True)
class PreparationSourceOwner:
    authorization_sha256: str
    processor_signature: str
    run_id: int
    scope_id: str
    generation: int

    def source_identity(self, filing, body, provider_record_id):
        fingerprint = Canonical.fingerprint({
            "owner": asdict(self), "filing": filing, "body": body,
            "canonical_provider_record_id": provider_record_id,
        })
        return f"sec-cache-preparation:{fingerprint}"


def retain_prepared_sources(db, *, owner, filing, body, records):
    """Called under the ingestion generation lock after the native guard passes."""
    table = CeriSourceRecord.__table__
    members = CeriIngestionRunSourceRecord.__table__
    result = []
    locator = f'{filing["accession"]}/{filing["filename"]}#paragraph-'
    for record in records:
        canonical_id = record.provider_record_id
        source_reference = record.payload.get("source_reference")
        if (
            record.provider != "sec"
            or record.dataset != CeriDataset.GUIDANCE
            or record.payload.get("cik") != filing["cik"]
            or record.payload.get("filing_accession") != filing["accession"]
            or record.payload.get("_ceri_quarantine_reason")
            or not isinstance(source_reference, str)
            or not source_reference.startswith(locator)
            or canonical_id != f'{filing["accession"]}:{source_reference}'
        ):
            raise ValueError("SEC_PREPARATION_SOURCE_LINEAGE_INVALID")
        storage_identity = owner.source_identity(filing, body, canonical_id)
        content_hash = source_record_content_hash(record.payload)
        source_id = db.execute(insert(table).values(
            ingestion_run_id=owner.run_id,
            provider="sec",
            dataset=PREPARATION_SOURCE,
            provider_record_id=storage_identity,
            idempotency_key=f"{storage_identity}:{content_hash}",
            company_hint_json={"cik": filing["cik"], "ticker": record.payload["ticker"]},
            source_reference=source_reference,
            retrieved_at=datetime.fromisoformat(body["possessed_at"]),
            restricted_normalized_json=Canonical.canonicalize(record.payload),
            content_hash=content_hash,
            export_policy="restricted",
            redistribution_allowed=False,
            purge_eligible=False,
        ).returning(table.c.id)).scalar_one()
        db.execute(insert(members).values(
            ingestion_run_id=owner.run_id, source_record_id=source_id,
            ticker=record.payload["ticker"], provider="sec", dataset=PREPARATION_SOURCE,
            ingestion_outcome="INSERTED",
        ))
        result.append({"id": source_id, "hash": content_hash, "provider_record_id": canonical_id})
    return sorted(result, key=lambda source: source["id"])


def matches_prepared_sources(db, *, owner, filing, body, sources):
    table = CeriSourceRecord.__table__
    members = CeriIngestionRunSourceRecord.__table__
    indexed = {item["id"]: item for item in sources}
    if not indexed or len(indexed) != len(sources):
        return False
    owned = (
        table.c.source_reference.startswith(
            f'{filing["accession"]}/{filing["filename"]}#paragraph-', autoescape=True,
        ),
        table.c.ingestion_run_id == owner.run_id,
        table.c.dataset == PREPARATION_SOURCE,
        table.c.provider == "sec",
    )
    if set(db.scalars(select(table.c.id).where(*owned))) != set(indexed):
        return False
    rows = db.execute(select(table).join(members, table.c.id == members.c.source_record_id).where(
        *owned,
        members.c.ingestion_run_id == owner.run_id,
        members.c.dataset == PREPARATION_SOURCE,
        members.c.provider == "sec",
    )).mappings().all()
    if {row["id"] for row in rows} != set(indexed) or len(rows) != len(indexed):
        return False
    for row in rows:
        evidence = indexed[row["id"]]
        canonical_id = evidence["provider_record_id"]
        storage_identity = owner.source_identity(filing, body, canonical_id)
        payload = row["restricted_normalized_json"]
        if (
            not isinstance(payload, dict)
            or payload.get("cik") != filing["cik"]
            or payload.get("filing_accession") != filing["accession"]
            or payload.get("source_reference") != row["source_reference"]
            or not (row["source_reference"] or "").startswith(
                f'{filing["accession"]}/{filing["filename"]}#paragraph-'
            )
            or canonical_id != f'{filing["accession"]}:{row["source_reference"]}'
            or row["provider_record_id"] != storage_identity
            or row["idempotency_key"] != f'{storage_identity}:{evidence["hash"]}'
            or row["supersedes_id"] is not None
            or row["correction_type"] is not None
            or row["quarantine_reason"] is not None
            or row["retrieved_at"] != datetime.fromisoformat(body["possessed_at"])
            or row["content_hash"] != evidence["hash"]
            or source_record_content_hash(payload) != evidence["hash"]
        ):
            return False
    return True
