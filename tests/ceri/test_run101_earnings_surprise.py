from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.ceri_tables import CeriEarningsActual, CeriEstimateSnapshot
from app.services.ceri.dtos import EarningsRequest
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService


def test_earnings_acquisition_separates_reported_history_from_upcoming_calendar() -> None:
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    client = EarningsClient(now.date())

    records = list(
        EodhdCeriProvider(client=client, clock=lambda: now).fetch_earnings_actuals(
            EarningsRequest(None, "TEST")
        )
    )

    assert len(client.calls) == 2
    assert client.calls[0]["to"] <= now.date().isoformat()
    assert client.calls[1]["from"] >= now.date().isoformat()
    assert {record.payload["event_kind"] for record in records} == {"REPORTED", "UPCOMING"}
    assert next(r for r in records if r.payload["event_kind"] == "REPORTED").payload[
        "actual_value"
    ] == 0
    assert next(r for r in records if r.payload["event_kind"] == "REPORTED").payload[
        "estimate"
    ] == 0


def test_reported_provider_surprise_is_retained_with_semantic_lineage() -> None:
    earnings = _earnings(1, report_days_ago=10, actual=Decimal("1.2"))
    earnings.provider_consensus_value = Decimal("1.0")
    earnings.provider_surprise_pct = Decimal("20")
    earnings.provider_consensus_semantics = "REPORT_TIME_CONSENSUS"

    feature = CeriSurpriseFeatureService().attach_consensus_snapshot(earnings, [])

    assert feature.surprise_absolute == Decimal("0.2")
    assert feature.surprise_pct == Decimal("20")
    assert earnings.consensus_selection_reason == "provider_report_time_consensus_and_surprise"


def test_upcoming_event_is_excluded_from_surprise_trend() -> None:
    reported = _earnings(1, report_days_ago=10, actual=Decimal("1.2"))
    reported.provider_consensus_value = Decimal("1.0")
    reported.provider_consensus_semantics = "REPORT_TIME_CONSENSUS"
    upcoming = _earnings(2, report_days_ago=-10, actual=None)
    upcoming.event_kind = "UPCOMING"

    summary = CeriSurpriseFeatureService().summarize([upcoming, reported], [])

    assert [feature.earnings_actual_id for feature in summary.features] == [1]


def test_last_four_selects_only_reported_events() -> None:
    rows = []
    for index in range(6):
        row = _earnings(index + 1, report_days_ago=10 + index, actual=Decimal("1"))
        row.provider_consensus_value = Decimal("1")
        row.provider_consensus_semantics = "REPORT_TIME_CONSENSUS"
        rows.append(row)
    upcoming = _earnings(99, report_days_ago=-1, actual=None)
    upcoming.event_kind = "UPCOMING"

    summary = CeriSurpriseFeatureService().summarize([upcoming, *rows], [])

    assert len(summary.features) == 4
    assert 99 not in {feature.earnings_actual_id for feature in summary.features}


def test_post_report_estimate_is_not_selected_as_pre_report_consensus() -> None:
    earnings = _earnings(1, report_days_ago=10, actual=Decimal("1.2"))
    before = _estimate(1, earnings.report_at - timedelta(hours=1), Decimal("1.0"))
    after = _estimate(2, earnings.report_at + timedelta(hours=1), Decimal("1.1"))

    feature = CeriSurpriseFeatureService().attach_consensus_snapshot(
        earnings, [before, after]
    )

    assert feature.consensus_snapshot_id == before.id


class EarningsClient:
    def __init__(self, today: date) -> None:
        self.today = today
        self.calls: list[dict[str, str]] = []

    def get_json(self, _path, params):
        self.calls.append(dict(params))
        if params["to"] <= self.today.isoformat():
            return [
                {
                    "id": "reported",
                    "reportDate": "2026-08-01",
                    "date": "2026-06-30",
                    "period": "0q",
                    "epsActual": 0,
                    "epsEstimate": 0,
                    "surprisePercent": 0,
                }
            ]
        return [
            {
                "id": "upcoming",
                "reportDate": "2026-09-01",
                "date": "2026-09-30",
                "period": "0q",
                "epsActual": None,
                "epsEstimate": 1,
            }
        ]


def _earnings(
    identifier: int, *, report_days_ago: int, actual: Decimal | None
) -> CeriEarningsActual:
    report_at = datetime(2026, 8, 13, 20, tzinfo=UTC) - timedelta(days=report_days_ago)
    return CeriEarningsActual(
        id=identifier,
        source_record_id=identifier,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        report_at=report_at,
        report_session=report_at.date(),
        actual_value=actual,
        event_kind="REPORTED",
    )


def _estimate(identifier: int, effective_at: datetime, consensus: Decimal):
    return CeriEstimateSnapshot(
        id=identifier,
        source_record_id=identifier,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        consensus=consensus,
        canonical_currency="USD",
        canonical_scale=Decimal("1"),
        effective_at=effective_at,
        effective_session=effective_at.date(),
        known_at=effective_at,
        canonical_observation_key=f"estimate-{identifier}",
    )
