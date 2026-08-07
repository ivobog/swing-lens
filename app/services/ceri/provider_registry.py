from __future__ import annotations

from dataclasses import dataclass

from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import ProviderCapabilities, ProviderHealth
from app.services.ceri.enums import CeriDataset, ExportPolicy
from app.services.ceri.enums import CeriProvider as CeriProviderName
from app.services.ceri.provider_protocol import CeriProvider
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.providers.manual_provider import ManualCeriProvider
from app.services.ceri.providers.primary_provider import PrimaryCeriProvider
from app.services.ceri.sec.provider import SecCeriProvider


class CeriProviderRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderDatasetPolicy:
    max_stale_days: int
    export_policy: str
    raw_payload_storage_allowed: bool
    normalized_value_storage_allowed: bool = True
    derived_value_storage_allowed: bool = True
    redistribution_allowed: bool = False
    purge_required_on_termination: bool = False
    deletion_deadline_days: int | None = None
    terms_version: str = "unknown"


@dataclass(frozen=True)
class ProviderLicensePolicy:
    provider: str
    dataset: str
    usage_scope: str
    raw_payload_storage_allowed: bool
    raw_payload_retention_days: int | None
    normalized_value_storage_allowed: bool
    derived_value_storage_allowed: bool
    export_policy: str
    redistribution_allowed: bool
    purge_required_on_termination: bool
    deletion_deadline_days: int | None
    terms_version: str


class CeriProviderRegistry:
    def __init__(
        self,
        providers: dict[str, CeriProvider] | None = None,
        config: CeriConfig | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self._providers = providers or _default_providers()

    def get(self, name: str) -> CeriProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise CeriProviderRegistryError(f"CERI provider is not registered: {name}")
        return provider

    def priority_order(self) -> tuple[str, ...]:
        return tuple(provider.value for provider in self.config.providers.priority)

    def capabilities(self, name: str) -> ProviderCapabilities:
        return self.get(name).capabilities()

    def health(self, name: str) -> ProviderHealth:
        return self.get(name).health()

    def dataset_policy(self, dataset: str) -> ProviderDatasetPolicy:
        return self.license_policy(CeriProviderName.MANUAL.value, dataset)

    def license_policy(self, provider: str, dataset: str) -> ProviderDatasetPolicy:
        try:
            dataset_key = CeriDataset(dataset)
        except ValueError as exc:
            raise CeriProviderRegistryError(f"CERI dataset is not configured: {dataset}") from exc
        policy = self.config.datasets.get(dataset_key)
        if policy is None:
            raise CeriProviderRegistryError(f"CERI dataset is not configured: {dataset}")
        if provider == CeriProviderName.EODHD.value:
            return ProviderDatasetPolicy(
                max_stale_days=policy.max_stale_days,
                export_policy=ExportPolicy.RESTRICTED.value,
                raw_payload_storage_allowed=False,
                normalized_value_storage_allowed=True,
                derived_value_storage_allowed=True,
                redistribution_allowed=False,
                purge_required_on_termination=True,
                deletion_deadline_days=30,
                terms_version="2026-08-personal",
            )
        if provider == CeriProviderName.SEC.value:
            return ProviderDatasetPolicy(
                max_stale_days=policy.max_stale_days,
                export_policy=ExportPolicy.EXPORTABLE.value,
                raw_payload_storage_allowed=False,
                terms_version="sec-public-fair-access",
            )
        return ProviderDatasetPolicy(
            max_stale_days=policy.max_stale_days,
            export_policy=policy.export_policy.value,
            raw_payload_storage_allowed=policy.export_policy.value == "exportable",
            terms_version=self.config.retention.provider_terms_version,
        )

    def provider_license_policy(self, provider: str, dataset: str) -> ProviderLicensePolicy:
        policy = self.license_policy(provider, dataset)
        return ProviderLicensePolicy(
            provider=provider,
            dataset=dataset,
            usage_scope="personal" if provider == "eodhd" else "public_first_party",
            raw_payload_storage_allowed=policy.raw_payload_storage_allowed,
            raw_payload_retention_days=policy.deletion_deadline_days,
            normalized_value_storage_allowed=policy.normalized_value_storage_allowed,
            derived_value_storage_allowed=policy.derived_value_storage_allowed,
            export_policy=policy.export_policy,
            redistribution_allowed=policy.redistribution_allowed,
            purge_required_on_termination=policy.purge_required_on_termination,
            deletion_deadline_days=policy.deletion_deadline_days,
            terms_version=policy.terms_version,
        )


def _default_providers() -> dict[str, CeriProvider]:
    return {
        CeriProviderName.MANUAL.value: ManualCeriProvider(),
        CeriProviderName.PRIMARY.value: PrimaryCeriProvider(),
        CeriProviderName.EODHD.value: EodhdCeriProvider(),
        CeriProviderName.SEC.value: SecCeriProvider(),
    }
