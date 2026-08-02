from __future__ import annotations

from app.services.ceri.dtos import CompanyQuery, EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType, CeriProviderCapability
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider
from app.services.ceri.providers.primary_provider import (
    PrimaryCeriProvider,
    PrimaryProviderUnavailable,
)


def test_manual_provider_contract_passes_before_primary_provider_is_enabled() -> None:
    provider = ManualCeriProvider(
        {
            "estimates": [
                {
                    "provider_record_id": "manual-est-1",
                    "ticker": "MSFT",
                    "provider_company_id": "manual-msft",
                    "published_at": "2026-08-01T20:00:00+00:00",
                    "observed_at": "2026-08-01T20:01:00+00:00",
                    "metric": "EPS_DILUTED",
                    "period_type": "CURRENT_QUARTER",
                }
            ]
        }
    )

    capabilities = provider.capabilities()
    records = list(
        provider.fetch_estimate_snapshots(
            EstimateRequest(
                company_id=1,
                ticker="MSFT",
                metrics=(CeriMetric.EPS_DILUTED,),
                period_types=(CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )
    companies = provider.resolve_company(CompanyQuery(ticker="MSFT"))

    assert capabilities.supports(CeriProviderCapability.ESTIMATES)
    assert provider.health().healthy is True
    assert records[0].payload["provider_terms_version"] == "manual-fixture-1.0"
    assert records[0].export_policy == "exportable"
    assert companies[0].provider_company_id == "manual-msft"


def test_primary_provider_is_credentials_gated_and_does_not_expose_secret() -> None:
    provider = PrimaryCeriProvider(api_key="sk-test-secret")

    metadata = provider.safe_metadata()
    health = provider.health()

    assert provider.configured is True
    assert health.healthy is True
    assert metadata["credential_env_var"] == "CERI_PRIMARY_PROVIDER_API_KEY"
    assert "sk-test-secret" not in str(metadata)
    assert metadata["licensing_policy"]["export_policy"] == "restricted"


def test_primary_provider_without_credentials_degrades_health() -> None:
    provider = PrimaryCeriProvider(api_key="")

    health = provider.health()

    assert health.healthy is False
    assert health.quota_status == "credentials_missing"
    try:
        provider.resolve_company(CompanyQuery(ticker="MSFT"))
    except PrimaryProviderUnavailable as exc:
        assert "CERI_PRIMARY_PROVIDER_API_KEY" in str(exc)
    else:
        raise AssertionError("primary provider should require credentials")


def test_registry_reports_provider_priority_and_primary_capabilities() -> None:
    registry = CeriProviderRegistry()

    assert registry.priority_order()[:2] == ("manual", "primary")
    assert registry.capabilities("primary").supports(CeriProviderCapability.HEALTH)
    assert registry.health("primary").provider == "primary"
