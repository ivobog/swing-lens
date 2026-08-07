from __future__ import annotations

from app.services.ceri.dtos import (
    CompanyQuery,
    ProviderCapabilities,
    ProviderCompany,
    ProviderHealth,
    RawProviderRecord,
)
from app.services.ceri.enums import CeriDataset, CeriProviderCapability
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.validation_service import CeriProviderValidationService


def test_eodhd_license_policy_restricts_raw_and_requires_purge() -> None:
    policy = CeriProviderRegistry().provider_license_policy("eodhd", "estimates")

    assert policy.raw_payload_storage_allowed is False
    assert policy.export_policy == "restricted"
    assert policy.purge_required_on_termination is True
    assert policy.deletion_deadline_days == 30


class FixtureProvider:
    name = "fixture"

    def capabilities(self):
        return ProviderCapabilities("fixture", frozenset(), frozenset())

    def health(self):
        return ProviderHealth("fixture", True)

    def resolve_company(self, query: CompanyQuery):
        return [ProviderCompany("fixture", f"{query.ticker}.X", query.ticker or "")]

    def fetch_estimate_snapshots(self, request):
        yield RawProviderRecord(
            "fixture",
            CeriDataset.ESTIMATES,
            "estimate-1",
            {"ticker": request.ticker, "consensus": 1},
            None,
            None,
        )

    def fetch_earnings_actuals(self, request):
        return iter(())

    def fetch_guidance(self, request):
        return iter(())

    def fetch_catalysts(self, request):
        return iter(())


def test_validation_service_produces_offline_summary() -> None:
    summary = CeriProviderValidationService().validate(FixtureProvider(), ("MSFT", "AAPL"))

    assert summary.sample_size == 2
    assert summary.identity_successes == 2
    assert summary.estimate_coverage == 2
    assert summary.errors == ()
    assert summary.ready is True
    assert summary.unique_sample_size == 2


class InvalidFixtureProvider(FixtureProvider):
    def capabilities(self):
        return ProviderCapabilities(
            "fixture",
            frozenset(
                {
                    CeriProviderCapability.IDENTITY,
                    CeriProviderCapability.ESTIMATES,
                    CeriProviderCapability.EARNINGS,
                }
            ),
            frozenset({CeriDataset.ESTIMATES, CeriDataset.EARNINGS}),
        )

    def fetch_estimate_snapshots(self, request):
        for consensus in (None, 2):
            yield RawProviderRecord(
                "fixture",
                CeriDataset.ESTIMATES,
                "duplicate-estimate",
                {
                    "ticker": request.ticker,
                    "metric": "EPS_DILUTED",
                    "consensus": consensus,
                    "high": 1,
                    "low": 2,
                    "analyst_count": -1,
                },
                None,
                None,
            )

    def fetch_earnings_actuals(self, request):
        yield RawProviderRecord(
            "fixture",
            CeriDataset.EARNINGS,
            "earning-1",
            {"actual_value": None},
            None,
            None,
        )


def test_validation_gate_blocks_duplicates_ranges_and_missing_consensus() -> None:
    summary = CeriProviderValidationService().validate(
        InvalidFixtureProvider(), ("MSFT", "MSFT")
    )

    assert summary.sample_size == 1
    assert summary.duplicate_provider_records == 1
    assert summary.invalid_estimate_ranges == 2
    assert summary.missing_consensus == 1
    assert summary.missing_earnings_dates == 1
    assert summary.missing_earnings_actuals == 1
    assert summary.ready is False
    assert {
        "duplicate_provider_records",
        "estimate_ranges_invalid",
        "consensus_missing",
    } <= set(summary.blocking_reasons)
