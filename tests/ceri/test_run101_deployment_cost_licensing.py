from __future__ import annotations

from app.models.ceri_tables import CeriProviderRequestTelemetry
from app.services.ceri.deployment_identity import build_deployment_identity
from app.services.ceri.provider_cost_ledger import ProviderCostLedger


def test_deployment_identity_contains_reproducibility_boundary() -> None:
    identity = build_deployment_identity(
        git_sha="abc123",
        dirty=True,
        image_digest="sha256:image",
        schema_revision="0042_ceri_run101_fail_closed",
        config_hash="config",
        calculation_version="ceri-1.2.0",
        provider_signatures={"eodhd": "adapter-v2", "sec": "extractor-v2"},
    )

    assert identity == {
        "git_sha": "abc123",
        "git_dirty": True,
        "image_digest": "sha256:image",
        "schema_revision": "0042_ceri_run101_fail_closed",
        "config_hash": "config",
        "calculation_version": "ceri-1.2.0",
        "provider_signatures": {"eodhd": "adapter-v2", "sec": "extractor-v2"},
    }


def test_provider_ledger_aggregates_requests_runtime_storage_and_cost_units() -> None:
    rows = [
        CeriProviderRequestTelemetry(
            provider="eodhd",
            endpoint="dataset:estimates",
            call_cost=2,
            latency_ms=120,
            response_bytes=1000,
            stored_bytes=400,
        ),
        CeriProviderRequestTelemetry(
            provider="eodhd",
            endpoint="dataset:earnings",
            call_cost=1,
            latency_ms=80,
            response_bytes=500,
            stored_bytes=200,
        ),
    ]

    ledger = ProviderCostLedger().summarize(rows)

    assert ledger["eodhd"] == {
        "request_rows": 2,
        "call_cost_units": 3,
        "runtime_ms": 200,
        "response_bytes": 1500,
        "stored_bytes": 600,
    }
