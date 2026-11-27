from datetime import UTC, datetime

import pytest

from app.services.ceri.dtos import EstimateRequest
from app.services.ceri.enums import CeriMetric, CeriPeriodType
from app.services.ceri.providers.eodhd_client import EodhdClientConfig, EodhdHttpClient
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider


@pytest.mark.parametrize(
    ("count_fields", "expected"),
    [
        pytest.param(
            {
                "epsRevisionsUpLast30days": 4,
                "epsRevisionsDownLast30days": 1,
                "epsRevisionsUpLast7days": 2,
                "epsRevisionsDownLast7days": 3,
            },
            (4, 1),
            id="prefer-complete-30-day-pair",
        ),
        pytest.param(
            {"epsRevisionsUpLast7days": 2, "epsRevisionsDownLast7days": 3},
            (2, 3),
            id="complete-7-day-fallback",
        ),
        pytest.param(
            {
                "epsRevisionsUpLast30days": None,
                "epsRevisionsDownLast30days": 8,
                "epsRevisionsUpLast7days": 2,
                "epsRevisionsDownLast7days": 0,
            },
            (2, 0),
            id="7-day-fallback-after-null-with-zero",
        ),
        pytest.param(
            {
                "epsRevisionsUpLast30days": 3,
                "epsRevisionsDownLast30days": "",
                "epsRevisionsUpLast7days": 0,
                "epsRevisionsDownLast7days": 1,
            },
            (0, 1),
            id="7-day-fallback-after-empty-field",
        ),
        pytest.param(
            {
                "epsRevisionsUpLast30days": 0,
                "epsRevisionsDownLast30days": "0",
                "epsRevisionsUpLast7days": 2,
                "epsRevisionsDownLast7days": 3,
            },
            (0, "0"),
            id="explicit-zero-pair-still-preferred",
        ),
        pytest.param(
            {"epsRevisionsUpLast7days": 2, "epsRevisionsDownLast30days": 8},
            (None, None),
            id="7-day-up-and-30-day-down",
        ),
        pytest.param(
            {"epsRevisionsUpLast30days": 2, "epsRevisionsDownLast7days": 8},
            (None, None),
            id="30-day-up-and-7-day-down",
        ),
        pytest.param(
            {"epsRevisionsUpLast30days": 0},
            (None, None),
            id="zero-up-count-with-missing-down-count",
        ),
        pytest.param(
            {"epsRevisionsDownLast7days": 1},
            (None, None),
            id="incomplete-fallback",
        ),
        pytest.param({}, (None, None), id="no-count-evidence"),
    ],
)
def test_eodhd_estimates_map_revision_counts_from_one_window(count_fields, expected):
    response = [
        {
            "code": "AAPL.US",
            "period": "0q",
            "date": "2026-09-30",
            "earningsEstimateAvg": 2,
            "epsTrend7daysAgo": 1.9,
            "revenueEstimateAvg": 100,
            **count_fields,
        }
    ]
    client = EodhdHttpClient(
        EodhdClientConfig(api_key="fixture"), transport=lambda *_args: response
    )
    provider = EodhdCeriProvider(client=client, clock=lambda: datetime(2026, 8, 8, tzinfo=UTC))
    records = list(
        provider.fetch_estimate_snapshots(
            EstimateRequest(
                None,
                "AAPL",
                (CeriMetric.EPS_DILUTED, CeriMetric.REVENUE),
                (CeriPeriodType.CURRENT_QUARTER,),
            )
        )
    )

    eps = [record.payload for record in records if record.payload["metric"] == "EPS_DILUTED"]
    assert len(eps) == 2
    assert {(row["upward_count"], row["downward_count"]) for row in eps} == {expected}
    revenue = [record.payload for record in records if record.payload["metric"] == "REVENUE"]
    assert len(revenue) == 1
    assert "upward_count" not in revenue[0] and "downward_count" not in revenue[0]
