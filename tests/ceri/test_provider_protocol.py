from __future__ import annotations

from app.services.ceri.dtos import CompanyQuery
from app.services.ceri.enums import CeriProviderCapability
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider


def test_manual_provider_exposes_required_protocol_methods() -> None:
    provider = ManualCeriProvider()

    for method_name in [
        "capabilities",
        "health",
        "resolve_company",
        "fetch_estimate_snapshots",
        "fetch_earnings_actuals",
        "fetch_guidance",
        "fetch_catalysts",
    ]:
        assert callable(getattr(provider, method_name))


def test_provider_registry_reports_capabilities_and_health() -> None:
    provider = ManualCeriProvider(
        {
            "estimates": [
                {
                    "provider_record_id": "est-1",
                    "ticker": "MSFT",
                    "published_at": "2026-08-01T20:15:00Z",
                }
            ]
        }
    )
    registry = CeriProviderRegistry(providers={"manual": provider})

    capabilities = registry.capabilities("manual")
    health = registry.health("manual")

    assert CeriProviderCapability.ESTIMATES in capabilities.capabilities
    assert health.healthy is True
    assert "1 manual record" in health.message


def test_manual_provider_resolves_company_from_loaded_records() -> None:
    provider = ManualCeriProvider(
        {
            "estimates": [
                {
                    "provider_record_id": "est-1",
                    "ticker": "MSFT",
                    "exchange": "NASDAQ",
                    "provider_company_id": "manual-msft",
                    "cik": "789019",
                    "company_name": "Microsoft",
                }
            ]
        }
    )

    matches = provider.resolve_company(CompanyQuery(ticker="MSFT"))

    assert len(matches) == 1
    assert matches[0].provider_company_id == "manual-msft"
    assert matches[0].ticker == "MSFT"
