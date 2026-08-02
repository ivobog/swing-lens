from __future__ import annotations

from dataclasses import dataclass

from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.dtos import ProviderCapabilities, ProviderHealth
from app.services.ceri.enums import CeriDataset
from app.services.ceri.enums import CeriProvider as CeriProviderName
from app.services.ceri.provider_protocol import CeriProvider
from app.services.ceri.providers.manual_provider import ManualCeriProvider
from app.services.ceri.providers.primary_provider import PrimaryCeriProvider


class CeriProviderRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderDatasetPolicy:
    max_stale_days: int
    export_policy: str
    raw_payload_storage_allowed: bool


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
        try:
            dataset_key = CeriDataset(dataset)
        except ValueError as exc:
            raise CeriProviderRegistryError(f"CERI dataset is not configured: {dataset}") from exc
        policy = self.config.datasets.get(dataset_key)
        if policy is None:
            raise CeriProviderRegistryError(f"CERI dataset is not configured: {dataset}")
        return ProviderDatasetPolicy(
            max_stale_days=policy.max_stale_days,
            export_policy=policy.export_policy.value,
            raw_payload_storage_allowed=policy.export_policy.value == "exportable",
        )


def _default_providers() -> dict[str, CeriProvider]:
    return {
        CeriProviderName.MANUAL.value: ManualCeriProvider(),
        CeriProviderName.PRIMARY.value: PrimaryCeriProvider(),
    }
