"""Reviewed issuer reporting authorities, independent of SEC ticker coverage.

An absent SEC ticker is never evidence of an exemption. Only an exact, reviewed
registry entry or an existing explicit applicability decision can exclude SEC.
The registry is version controlled; its evidence is retained in the company's
SEC applicability reason when the normal source writer creates the mapping.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

FILING_AUTHORITY_REGISTRY_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "ceri_filing_authorities.yaml"
)


class FilingAuthority(StrEnum):
    SEC = "SEC"
    FDIC = "FDIC"
    OCC = "OCC"
    FED = "FED"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ReviewedFilingAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticker: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.-]{0,15}$")
    company_name: str = Field(min_length=1)
    authority: FilingAuthority
    authority_id: str = Field(min_length=1)
    reviewed_on: date
    reason: str = Field(min_length=1)
    sources: tuple[HttpUrl, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_reviewed_authority(self):
        if self.authority is FilingAuthority.UNKNOWN:
            raise ValueError("An unknown filing authority cannot grant a reviewed exemption.")
        return self

    @property
    def sec_required(self) -> bool:
        return self.authority is FilingAuthority.SEC

    @property
    def applicability_reason(self) -> str:
        sources = ", ".join(str(url) for url in self.sources)
        return (
            f"{self.authority.value} reporting issuer; authority ID {self.authority_id}. "
            f"{self.reason} Reviewed {self.reviewed_on.isoformat()}. Sources: {sources}"
        )


class FilingAuthorityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1, le=1)
    companies: tuple[ReviewedFilingAuthority, ...]

    @model_validator(mode="after")
    def require_unique_tickers(self):
        tickers = [entry.ticker for entry in self.companies]
        if len(set(tickers)) != len(tickers):
            raise ValueError("Filing authority registry contains duplicate tickers.")
        return self


def reviewed_filing_authority(
    ticker: str, *, path: Path | None = None
) -> ReviewedFilingAuthority | None:
    registry = FilingAuthorityRegistry.model_validate(
        yaml.safe_load((path or FILING_AUTHORITY_REGISTRY_PATH).read_text(encoding="utf-8"))
    )
    symbol = ticker.strip().upper()
    return next((entry for entry in registry.companies if entry.ticker == symbol), None)
