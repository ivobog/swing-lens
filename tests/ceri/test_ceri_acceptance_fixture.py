from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.ceri_tables import (
    CeriEstimateSnapshot,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.routers import ceri_routes
from app.services.ceri.export_policy import CeriExportPolicyRegistry, redact_sensitive
from app.services.ceri.purge_service import (
    CeriPurgeError,
    CeriPurgeExecuteRequest,
    CeriPurgePreviewRequest,
    CeriPurgeService,
    confirmation_token_for_preview,
)
from app.settings import Settings


def test_field_export_policy_masks_restricted_fields_and_local_paths() -> None:
    registry = CeriExportPolicyRegistry()

    row = registry.export_row(
        {
            "ticker": "MSFT",
            "source_url": "https://vendor.example/record",
            "raw_payload": {"provider_secret": "secret"},
            "notes": r"loaded from C:\Users\Ivica\Downloads\vendor.csv",
        }
    )

    assert row["ticker"] == "MSFT"
    assert row["source_url"] == "<restricted:source_url>"
    assert row["raw_payload"] == "<restricted:raw_payload>"
    assert row["notes"] == "loaded from <restricted:path>"
    assert "secret" not in str(row)


def test_redaction_blocks_auth_tokens_sql_details_and_nested_secrets() -> None:
    redacted = redact_sensitive(
        {
            "authorization": "Bearer abc123",
            "message": "select * from ceri_source_records where provider_secret = 'x'",
            "nested": {"api_key": "secret"},
        }
    )

    assert redacted["authorization"] == "<restricted:authorization>"
    assert redacted["message"] == "<restricted:sql>"
    assert redacted["nested"]["api_key"] == "<restricted:api_key>"
    assert "abc123" not in str(redacted)
    assert "secret" not in str(redacted)


def test_purge_preview_and_execute_are_audited_without_deleting_sources() -> None:
    source = CeriSourceRecord(
        id=10,
        provider="primary",
        provider_terms_version="primary-terms-2026",
        dataset="estimates",
        provider_record_id="est-1",
        content_hash="hash",
        idempotency_key="idem",
        export_policy="restricted",
    )
    estimate = CeriEstimateSnapshot(id=20, source_record_id=10, company_id=42)
    revision = CeriRevisionFeature(id=30, company_id=42, source_observation_ids_json=[10])
    score = CeriScoreSnapshot(id=40, company_id=42, ticker="MSFT")
    db = FakeDb(
        {
            CeriSourceRecord: [source],
            CeriEstimateSnapshot: [estimate],
            CeriRevisionFeature: [revision],
            CeriScoreSnapshot: [score],
        }
    )
    service = CeriPurgeService()

    preview = service.preview(
        db,
        CeriPurgePreviewRequest(
            provider="primary",
            license_scope="estimates",
            actor="local-admin",
            reason="license test",
        ),
    )
    token = confirmation_token_for_preview(preview.preview_manifest_hash)
    executed = service.execute(
        db,
        CeriPurgeExecuteRequest(
            provider="primary",
            license_scope="estimates",
            actor="local-admin",
            reason="confirmed license test",
            confirmation_token=token,
            preview_manifest_hash=preview.preview_manifest_hash,
        ),
    )

    assert preview.affected_counts_json["source_records"] == 1
    assert preview.affected_counts_json["estimate_snapshots"] == 1
    assert preview.invalidated_derivatives_json["revision_features"] == 1
    assert preview.invalidated_derivatives_json["score_snapshots"] == 1
    assert executed.status == "EXECUTED"
    assert source in db.collections[CeriSourceRecord]
    assert db.deleted == []


def test_purge_execute_requires_matching_confirmation_token() -> None:
    db = FakeDb({CeriPurgeAudit: [_audit()]})

    with pytest.raises(CeriPurgeError):
        CeriPurgeService().execute(
            db,
            CeriPurgeExecuteRequest(
                provider="primary",
                license_scope="estimates",
                actor="local-admin",
                reason="bad token",
                confirmation_token="wrong",
                preview_manifest_hash="preview-hash",
            ),
        )


def test_localhost_binding_and_admin_csrf_boundary_are_preserved() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_host == "127.0.0.1"
    with pytest.raises(HTTPException):
        ceri_routes.create_ceri_ingestion_run(
            request=_admin_request(csrf=False),
            db=FakeDb({}),  # type: ignore[arg-type]
            payload={"ticker": "MSFT", "dataset": "estimates"},
        )


def _audit() -> CeriPurgeAudit:
    return CeriPurgeAudit(
        id=1,
        provider="primary",
        license_scope="estimates",
        preview_manifest_hash="preview-hash",
        actor="local-admin",
        reason="preview",
        confirmation_token_hash="not-the-token",
        affected_counts_json={},
        invalidated_derivatives_json={},
        status="PREVIEWED",
    )


def _admin_request(*, csrf: bool):
    csrf_token = "secure-test-token"
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                local_admin_csrf_token=csrf_token,
                settings=Settings(
                    _env_file=None,
                    job_worker_enabled=False,
                    ceri_admin_enabled=True,
                )
            )
        ),
        client=SimpleNamespace(host="testclient"),
        headers={"x-csrf-token": csrf_token} if csrf else {},
        query_params={},
    )


class FakeDb:
    def __init__(self, collections) -> None:
        self.collections = {model: list(rows) for model, rows in collections.items()}
        self.added = []
        self.deleted = []
        self.next_id = 100

    def add(self, row) -> None:
        self.added.append(row)
        self.collections.setdefault(type(row), []).append(row)

    def flush(self) -> None:
        for rows in self.collections.values():
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = self.next_id
                    self.next_id += 1

    def delete(self, row) -> None:
        self.deleted.append(row)

    def scalar(self, statement):
        model = statement.column_descriptions[0]["entity"]
        rows = self.collections.get(model, [])
        if model is CeriPurgeAudit:
            return rows[0] if rows else None
        return rows[0] if rows else None

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        return FakeScalarResult(self.collections.get(model, []))


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows
