from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.services.ceri.dtos import (
    CatalystRequest,
    CompanyQuery,
    EarningsRequest,
    EstimateRequest,
    GuidanceRequest,
    ProviderCapabilities,
    ProviderCompany,
    ProviderHealth,
    RawProviderRecord,
)
from app.services.ceri.enums import CeriDataset, CeriProviderCapability, ExportPolicy


class PrimaryProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderRateLimitPolicy:
    requests_per_minute: int = 60
    burst: int = 10
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ProviderRetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0


@dataclass(frozen=True)
class ProviderLicensingPolicy:
    export_policy: str = ExportPolicy.RESTRICTED.value
    retention_days: int = 365
    raw_payload_storage_allowed: bool = False
    redistribution_allowed: bool = False


class PrimaryCeriProvider:
    name = "primary"
    credential_env_var = "CERI_PRIMARY_PROVIDER_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider_terms_version: str = "primary-commercial-unconfigured",
        rate_limit: ProviderRateLimitPolicy | None = None,
        retry_policy: ProviderRetryPolicy | None = None,
        licensing_policy: ProviderLicensingPolicy | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.getenv(self.credential_env_var)
        self.provider_terms_version = provider_terms_version
        self.rate_limit = rate_limit or ProviderRateLimitPolicy()
        self.retry_policy = retry_policy or ProviderRetryPolicy()
        self.licensing_policy = licensing_policy or ProviderLicensingPolicy()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider=self.name,
            capabilities=frozenset(
                {
                    CeriProviderCapability.HEALTH,
                    CeriProviderCapability.IDENTITY,
                    CeriProviderCapability.ESTIMATES,
                    CeriProviderCapability.EARNINGS,
                    CeriProviderCapability.GUIDANCE,
                    CeriProviderCapability.CATALYSTS,
                }
            ),
            datasets=frozenset(CeriDataset),
        )

    def health(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                checked_at=datetime.now(UTC),
                quota_status="credentials_missing",
                message=f"Set {self.credential_env_var} to enable the primary provider.",
            )
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=datetime.now(UTC),
            quota_status=f"{self.rate_limit.requests_per_minute}/minute",
            message="Primary provider configured; live fetch implementation is gated.",
        )

    def resolve_company(self, query: CompanyQuery) -> list[ProviderCompany]:
        self._require_configured()
        return []

    def fetch_estimate_snapshots(self, request: EstimateRequest) -> Iterable[RawProviderRecord]:
        self._raise_live_fetch_disabled()

    def fetch_earnings_actuals(self, request: EarningsRequest) -> Iterable[RawProviderRecord]:
        self._raise_live_fetch_disabled()

    def fetch_guidance(self, request: GuidanceRequest) -> Iterable[RawProviderRecord]:
        self._raise_live_fetch_disabled()

    def fetch_catalysts(self, request: CatalystRequest) -> Iterable[RawProviderRecord]:
        self._raise_live_fetch_disabled()

    def safe_metadata(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "configured": self.configured,
            "credential_env_var": self.credential_env_var,
            "provider_terms_version": self.provider_terms_version,
            "rate_limit": self.rate_limit.__dict__,
            "retry_policy": self.retry_policy.__dict__,
            "licensing_policy": self.licensing_policy.__dict__,
        }

    def _require_configured(self) -> None:
        if not self.configured:
            raise PrimaryProviderUnavailable(
                f"{self.name} provider requires {self.credential_env_var}."
            )

    def _raise_live_fetch_disabled(self) -> None:
        self._require_configured()
        raise PrimaryProviderUnavailable(
            "Primary provider live fetches require a licensed adapter implementation."
        )
