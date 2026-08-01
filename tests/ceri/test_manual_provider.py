from __future__ import annotations

from pathlib import Path

from app.services.ceri.dtos import EstimateRequest
from app.services.ceri.enums import CeriDataset, CeriMetric, CeriPeriodType
from app.services.ceri.providers.manual_provider import ManualCeriProvider


def test_manual_provider_reads_json_records(tmp_path: Path) -> None:
    path = tmp_path / "manual_estimates.json"
    path.write_text(
        """
        [
          {
            "provider_record_id": "est-1",
            "ticker": "MSFT",
            "metric": "EPS_DILUTED",
            "published_at": "2026-08-01T20:15:00Z"
          }
        ]
        """,
        encoding="utf-8",
    )

    provider = ManualCeriProvider.from_path(path)
    records = list(
        provider.fetch_estimate_snapshots(
            EstimateRequest(
                company_id=None,
                ticker="MSFT",
                metrics=(CeriMetric.EPS_DILUTED,),
                period_types=(CeriPeriodType.CURRENT_FISCAL_YEAR,),
            )
        )
    )

    assert len(records) == 1
    assert records[0].dataset is CeriDataset.ESTIMATES
    assert records[0].provider_record_id == "est-1"
    assert records[0].published_at is not None


def test_manual_provider_reads_csv_records_with_explicit_dataset(tmp_path: Path) -> None:
    path = tmp_path / "fixture.csv"
    path.write_text(
        "provider_record_id,ticker,published_at\ncat-1,FIX,2026-08-01T20:15:00Z\n",
        encoding="utf-8",
    )

    provider = ManualCeriProvider.from_path(path, dataset=CeriDataset.CATALYSTS)
    records = list(provider.fetch_catalysts(_catalyst_request("FIX")))

    assert len(records) == 1
    assert records[0].dataset is CeriDataset.CATALYSTS
    assert records[0].provider_record_id == "cat-1"


def test_manual_provider_marks_missing_record_id_as_quarantined() -> None:
    provider = ManualCeriProvider(
        {
            CeriDataset.ESTIMATES: [
                {"ticker": "MSFT", "published_at": "2026-08-01T20:15:00Z"}
            ]
        }
    )

    records = list(
        provider.fetch_estimate_snapshots(
            EstimateRequest(
                company_id=None,
                ticker="MSFT",
                metrics=(CeriMetric.EPS_DILUTED,),
                period_types=(CeriPeriodType.CURRENT_FISCAL_YEAR,),
            )
        )
    )

    assert records[0].provider_record_id.startswith("malformed:")
    assert records[0].payload["_ceri_quarantine_reason"] == "missing_provider_record_id"


def _catalyst_request(ticker: str):
    from app.services.ceri.dtos import CatalystRequest

    return CatalystRequest(company_id=None, ticker=ticker)
