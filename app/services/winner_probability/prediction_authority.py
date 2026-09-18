"""Retained capture proof for downstream Winner writers.

The capture owner validates and recomputes its exact upstream inputs. This proof
binds its resulting financial snapshot and creation-time lineage, while allowing
explicitly separate entry-data and supersession projections.
"""

from decimal import Decimal

from sqlalchemy import Numeric, select

from app.models.tables import WinnerPredictionSnapshot
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.combined_ranking_identity import calculation_identity_from_debug

PROOF_KEY = "native_capture_proof"
PROJECTION_COLUMNS = {
    "id",
    "captured_at",
    "entry_data_status",
    "superseded_at",
    "lineage_json",
    "combined_result_id",
}


def prediction_body(prediction, lineage_keys):
    body = {}
    for column in WinnerPredictionSnapshot.__table__.columns:
        if column.key in PROJECTION_COLUMNS:
            continue
        value = getattr(prediction, column.key)
        if value is not None and isinstance(column.type, Numeric):
            value = Decimal(str(value)).quantize(Decimal(1).scaleb(-column.type.scale))
        body[column.key] = value
    body["lineage"] = {
        key: (prediction.lineage_json or {}).get(key) for key in sorted(lineage_keys)
    }
    body["combined_result_id"] = (prediction.source_ids_json or {}).get("combined_result_id")
    return body


def seal_capture(prediction):
    if prediction.id is not None:
        raise ValueError("MUTATION_WINNER_CAPTURE_CREATION_ONLY")
    identity = calculation_identity_from_debug(prediction.lineage_json)
    if identity is None:
        raise ValueError("MUTATION_WINNER_CAPTURE_IDENTITY_REQUIRED")
    keys = sorted(set(prediction.lineage_json) - {PROOF_KEY})
    prediction.lineage_json = {
        **prediction.lineage_json,
        PROOF_KEY: {
            "contract": "winner-native-capture-v1",
            "identity_fingerprint": str(identity.fingerprint()),
            "lineage_keys": keys,
            "body_fingerprint": Canonical.fingerprint(prediction_body(prediction, keys)),
        },
    }


def validate_capture_body(prediction):
    proof = (prediction.lineage_json or {}).get(PROOF_KEY)
    identity = calculation_identity_from_debug(prediction.lineage_json)
    if prediction.combined_result_id not in {
        None,
        (prediction.source_ids_json or {}).get("combined_result_id"),
    }:
        raise ValueError("MUTATION_WINNER_COMBINED_COMPATIBILITY_ADDRESS_MISMATCH")
    if not isinstance(proof, dict) or proof.get("contract") != "winner-native-capture-v1":
        raise ValueError("MUTATION_WINNER_CERTIFIED_CAPTURE_REQUIRED")
    if (
        identity is None
        or proof.get("identity_fingerprint") != str(identity.fingerprint())
        or proof.get("body_fingerprint")
        != Canonical.fingerprint(prediction_body(prediction, proof["lineage_keys"]))
    ):
        raise ValueError("MUTATION_WINNER_CAPTURE_BODY_MISMATCH")
    return identity


def validate_prediction_source(db, prediction):
    if prediction is None or prediction.id is None:
        raise ValueError("MUTATION_WINNER_RETAINED_PREDICTION_REQUIRED")
    columns = tuple(WinnerPredictionSnapshot.__table__.columns)
    with db.no_autoflush:
        retained = (
            db.execute(
                select(*columns)
                .where(WinnerPredictionSnapshot.id == prediction.id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
    if retained is None or any(
        Canonical.dumps(retained[column.key]) != Canonical.dumps(getattr(prediction, column.key))
        for column in columns
        if column.key not in {"entry_data_status", "superseded_at"}
    ):
        raise ValueError("MUTATION_WINNER_PREDICTION_SOURCE_MISMATCH")
    return validate_capture_body(prediction)
