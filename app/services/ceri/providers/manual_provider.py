from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

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
from app.services.ceri.enums import (
    CeriDataset,
    CeriProviderCapability,
    ExportPolicy,
)


class ManualProviderError(ValueError):
    pass


class ManualCeriProvider:
    name = "manual"

    def __init__(
        self,
        records: Mapping[CeriDataset | str, Iterable[dict[str, Any]]] | None = None,
        *,
        provider_terms_version: str = "manual-fixture-1.0",
    ) -> None:
        self.provider_terms_version = provider_terms_version
        self._records = _normalize_records(records or {})

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        dataset: CeriDataset | str | None = None,
        provider_terms_version: str = "manual-fixture-1.0",
    ) -> ManualCeriProvider:
        if path.is_dir():
            records: dict[CeriDataset, list[dict[str, Any]]] = {}
            for child in path.iterdir():
                if child.suffix.lower() not in {".json", ".csv"}:
                    continue
                inferred = _infer_dataset(child, dataset=None)
                records.setdefault(inferred, []).extend(_read_records(child))
            return cls(records, provider_terms_version=provider_terms_version)

        inferred = _coerce_dataset(dataset) if dataset is not None else _infer_dataset(path)
        return cls(
            {inferred: _read_records(path)},
            provider_terms_version=provider_terms_version,
        )

    def capabilities(self) -> ProviderCapabilities:
        datasets = frozenset(self._records) or frozenset(CeriDataset)
        dataset_capabilities = {
            CeriDataset.ESTIMATES: CeriProviderCapability.ESTIMATES,
            CeriDataset.EARNINGS: CeriProviderCapability.EARNINGS,
            CeriDataset.GUIDANCE: CeriProviderCapability.GUIDANCE,
            CeriDataset.CATALYSTS: CeriProviderCapability.CATALYSTS,
        }
        capabilities = {
            CeriProviderCapability.HEALTH,
            CeriProviderCapability.IDENTITY,
            *(dataset_capabilities[dataset] for dataset in datasets),
        }
        return ProviderCapabilities(
            provider=self.name,
            capabilities=frozenset(capabilities),
            datasets=datasets,
        )

    def health(self) -> ProviderHealth:
        count = sum(len(rows) for rows in self._records.values())
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            quota_status="manual",
            message=f"{count} manual record(s) loaded.",
        )

    def resolve_company(self, query: CompanyQuery) -> list[ProviderCompany]:
        rows = self._records.get(CeriDataset.ESTIMATES, []) + self._records.get(
            CeriDataset.CATALYSTS,
            [],
        )
        matches: dict[tuple[str, str | None], ProviderCompany] = {}
        query_ticker = query.ticker.upper() if query.ticker else None
        for row in rows:
            ticker = _text(row, "ticker")
            provider_company_id = _optional_text(row, "provider_company_id")
            cik = _optional_text(row, "cik")
            if query_ticker and ticker.upper() != query_ticker:
                continue
            if query.provider_company_id and provider_company_id != query.provider_company_id:
                continue
            if query.cik and cik != query.cik:
                continue
            key = (ticker.upper(), _optional_text(row, "exchange"))
            matches[key] = ProviderCompany(
                provider=self.name,
                provider_company_id=provider_company_id,
                ticker=ticker.upper(),
                exchange=_optional_text(row, "exchange"),
                cik=cik,
                name=_optional_text(row, "company_name"),
            )
        return list(matches.values())

    def fetch_estimate_snapshots(self, request: EstimateRequest) -> Iterable[RawProviderRecord]:
        yield from self._records_for_request(CeriDataset.ESTIMATES, request.ticker)

    def fetch_earnings_actuals(self, request: EarningsRequest) -> Iterable[RawProviderRecord]:
        yield from self._records_for_request(CeriDataset.EARNINGS, request.ticker)

    def fetch_guidance(self, request: GuidanceRequest) -> Iterable[RawProviderRecord]:
        yield from self._records_for_request(CeriDataset.GUIDANCE, request.ticker)

    def fetch_catalysts(self, request: CatalystRequest) -> Iterable[RawProviderRecord]:
        yield from self._records_for_request(CeriDataset.CATALYSTS, request.ticker)

    def _records_for_request(
        self,
        dataset: CeriDataset,
        ticker: str,
    ) -> Iterable[RawProviderRecord]:
        for row in self._records.get(dataset, []):
            row_ticker = _optional_text(row, "ticker")
            if row_ticker and row_ticker.upper() != ticker.upper():
                continue
            yield _raw_provider_record(
                provider=self.name,
                provider_terms_version=self.provider_terms_version,
                dataset=dataset,
                row=row,
            )


def _normalize_records(
    records: Mapping[CeriDataset | str, Iterable[dict[str, Any]]],
) -> dict[CeriDataset, list[dict[str, Any]]]:
    normalized: dict[CeriDataset, list[dict[str, Any]]] = {}
    for dataset, rows in records.items():
        normalized[_coerce_dataset(dataset)] = [dict(row) for row in rows]
    return normalized


def _read_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key in ("records", "rows", "data"):
                value = raw.get(key)
                if isinstance(value, list):
                    return [dict(row) for row in value]
            return [dict(raw)]
        if isinstance(raw, list):
            return [dict(row) for row in raw]
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    raise ManualProviderError(f"Unsupported manual provider file: {path}")


def _infer_dataset(path: Path, dataset: CeriDataset | str | None = None) -> CeriDataset:
    if dataset is not None:
        return _coerce_dataset(dataset)
    lowered = path.stem.lower()
    for candidate in CeriDataset:
        if candidate.value in lowered:
            return candidate
    raise ManualProviderError(f"Cannot infer CERI dataset from file name: {path.name}")


def _raw_provider_record(
    *,
    provider: str,
    provider_terms_version: str,
    dataset: CeriDataset,
    row: dict[str, Any],
) -> RawProviderRecord:
    provider_record_id = _optional_text(row, "provider_record_id") or _optional_text(row, "id")
    payload = dict(row)
    if not provider_record_id:
        provider_record_id = "malformed:" + hashlib.sha256(
            json.dumps(row, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        payload["_ceri_quarantine_reason"] = "missing_provider_record_id"
    return RawProviderRecord(
        provider=provider,
        dataset=dataset,
        provider_record_id=provider_record_id,
        payload={**payload, "provider_terms_version": provider_terms_version},
        published_at=_optional_datetime(row, "published_at"),
        observed_at=_optional_datetime(row, "observed_at"),
        source_url=_optional_text(row, "source_url"),
        export_policy=_optional_text(row, "export_policy") or ExportPolicy.EXPORTABLE.value,
    )


def _coerce_dataset(value: CeriDataset | str) -> CeriDataset:
    try:
        return value if isinstance(value, CeriDataset) else CeriDataset(str(value))
    except ValueError as exc:
        raise ManualProviderError(f"Unsupported manual provider dataset: {value}") from exc


def _text(row: dict[str, Any], key: str) -> str:
    value = _optional_text(row, key)
    if not value:
        raise ManualProviderError(f"manual provider row is missing {key}")
    return value


def _optional_text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return str(value).strip()


def _optional_datetime(row: dict[str, Any], key: str) -> datetime | None:
    value = _optional_text(row, key)
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ManualProviderError(f"{key} must be an ISO datetime") from exc
