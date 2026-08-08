from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.services.ceri.config import CurrencyConversionConfig, load_ceri_config


@dataclass(frozen=True)
class CurrencyConversionResult:
    canonical_value: Decimal | None
    canonical_currency: str | None
    canonical_scale: Decimal | None
    conversion_rate: Decimal | None
    conversion_source_record_id: int | None
    conversion_effective_at: datetime | None
    comparable: bool
    warnings: tuple[str, ...] = ()


class CeriCurrencyConversionService:
    def __init__(self, config: CurrencyConversionConfig | None = None) -> None:
        self.config = config or load_ceri_config().currency_conversion

    def convert(
        self,
        value: Decimal | None,
        *,
        source_currency: str | None,
        source_scale: Decimal | None,
        canonical_currency: str | None,
        canonical_scale: Decimal | None,
        conversion_rate: Decimal | None = None,
        conversion_source: str | None = None,
        conversion_source_record_id: int | None = None,
        conversion_effective_at: datetime | None = None,
    ) -> CurrencyConversionResult:
        source_scale = source_scale or Decimal("1")
        canonical_scale = canonical_scale or source_scale
        if value is None:
            return CurrencyConversionResult(
                canonical_value=None,
                canonical_currency=canonical_currency or source_currency,
                canonical_scale=canonical_scale,
                conversion_rate=None,
                conversion_source_record_id=None,
                conversion_effective_at=None,
                comparable=True,
            )

        source_currency = _currency(source_currency)
        canonical_currency = _currency(canonical_currency) or source_currency
        if source_currency == canonical_currency:
            return CurrencyConversionResult(
                canonical_value=(value * source_scale / canonical_scale),
                canonical_currency=canonical_currency,
                canonical_scale=canonical_scale,
                conversion_rate=Decimal("1"),
                conversion_source_record_id=conversion_source_record_id,
                conversion_effective_at=conversion_effective_at,
                comparable=True,
            )

        if not self._verified(conversion_rate, conversion_source, conversion_effective_at):
            return CurrencyConversionResult(
                canonical_value=None,
                canonical_currency=None,
                canonical_scale=None,
                conversion_rate=None,
                conversion_source_record_id=None,
                conversion_effective_at=None,
                comparable=False,
                warnings=("currency_conversion_unavailable",),
            )

        return CurrencyConversionResult(
            canonical_value=(value * source_scale * conversion_rate / canonical_scale),
            canonical_currency=canonical_currency,
            canonical_scale=canonical_scale,
            conversion_rate=conversion_rate,
            conversion_source_record_id=conversion_source_record_id,
            conversion_effective_at=conversion_effective_at,
            comparable=True,
        )

    def _verified(
        self,
        conversion_rate: Decimal | None,
        conversion_source: str | None,
        conversion_effective_at: datetime | None,
    ) -> bool:
        if conversion_rate is None or conversion_effective_at is None:
            return False
        if (
            self.config.require_verified_basis
            and conversion_source not in self.config.allowed_sources
        ):
            return False
        return True


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    parsed = Decimal(str(value))
    if parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _currency(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return value.upper()
