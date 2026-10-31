"""Narrow explicit-CLI authority for the two independent D2 history tables.

Reuses NORMAL_ONLY runtime capability and the configured CLI process role. It
does not mint a pipeline context, worker lease or authority for another domain.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from app.services.runtime_mutation_authority import (
    MutationCapability,
    RuntimeMutationAuthority,
    RuntimeMutationAuthorityError,
    require_runtime_mutation_authority,
)
from app.settings import ProcessRole

OPERATION = "market_sector_intelligence.capture"
_capture = ContextVar("market_sector_rotation_capture", default=None)


@dataclass(frozen=True)
class MarketSectorCaptureAuthority:
    runtime: RuntimeMutationAuthority
    process_role: ProcessRole

    def __post_init__(self):
        runtime = require_runtime_mutation_authority(self.runtime)
        runtime.require_capability(MutationCapability.NORMAL_ONLY)
        if (
            self.process_role is not ProcessRole.CLI_OR_MAINTENANCE
            or runtime.operation != OPERATION
        ):
            raise RuntimeMutationAuthorityError(
                "MARKET_SECTOR_CLI_AUTHORITY_REQUIRED",
                "Only the explicit Market Sector Intelligence CLI may capture independent history.",
            )

    @classmethod
    def from_settings(cls, settings):
        return cls(
            RuntimeMutationAuthority.from_settings(settings, operation=OPERATION),
            settings.process_role,
        )


def require_capture_authority(authority):
    if type(authority) is not MarketSectorCaptureAuthority:
        raise RuntimeMutationAuthorityError(
            "MARKET_SECTOR_CLI_AUTHORITY_REQUIRED",
            "Independent history requires typed CLI capture authority.",
        )
    authority.__post_init__()
    return authority


@contextmanager
def capture_transaction(db, authority, snapshot_digest, row_digests):
    require_capture_authority(authority)
    if not db.in_transaction() or _capture.get() is not None:
        raise ValueError("MARKET_SECTOR_CAPTURE_TRANSACTION_REQUIRED")
    seal = dict(
        db=db,
        transaction=db.get_transaction(),
        snapshot_digest=snapshot_digest,
        row_digests=row_digests,
        snapshot_id=None,
    )
    token = _capture.set(seal)
    try:
        yield seal
    finally:
        _capture.reset(token)


def require_capture_insert(db, row):
    seal = _capture.get()
    if seal is None or db is not seal["db"] or db.get_transaction() is not seal["transaction"]:
        raise ValueError("MARKET_SECTOR_CAPTURE_TRANSACTION_REQUIRED")
    if hasattr(row, "snapshot_digest"):
        if row.snapshot_digest != seal["snapshot_digest"]:
            raise ValueError("MARKET_SECTOR_CAPTURE_EVIDENCE_MISMATCH")
    elif (
        row.snapshot_id != seal["snapshot_id"]
        or seal["row_digests"].get(row.etf_symbol) != row.source_digest
    ):
        raise ValueError("MARKET_SECTOR_CAPTURE_EVIDENCE_MISMATCH")
