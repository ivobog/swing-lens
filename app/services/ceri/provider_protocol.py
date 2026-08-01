from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

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


class CeriProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities:
        ...

    def health(self) -> ProviderHealth:
        ...

    def resolve_company(self, query: CompanyQuery) -> list[ProviderCompany]:
        ...

    def fetch_estimate_snapshots(self, request: EstimateRequest) -> Iterable[RawProviderRecord]:
        ...

    def fetch_earnings_actuals(self, request: EarningsRequest) -> Iterable[RawProviderRecord]:
        ...

    def fetch_guidance(self, request: GuidanceRequest) -> Iterable[RawProviderRecord]:
        ...

    def fetch_catalysts(self, request: CatalystRequest) -> Iterable[RawProviderRecord]:
        ...
