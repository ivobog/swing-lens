from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriFeatureBuildState,
    CeriGuidanceEvent,
    CeriRevisionFeature,
)
from app.models.tables import PriceBar
from app.services.ceri.config import load_ceri_config
from app.services.ceri.feature_rebuild_service import (
    CeriFeatureRebuildRequest,
    CeriFeatureRebuildService,
    _copy_revision_derived,
    _latest_price_event,
)


def test_feature_rebuild_persists_windows_and_acceleration() -> None:
    company = CeriCompany(id=7, ticker="MSFT", exchange="US")
    revisions = StubRevisionService()
    estimates = [
        CeriEstimateSnapshot(
            source_record_id=index,
            company_id=7,
            metric=metric,
            fiscal_period_end=date(2027, 12, 31),
            period_type=slot,
            canonical_period_slot=slot,
            canonical_observation_key=f"{metric}:{slot}",
        )
        for index, (metric, slot) in enumerate(
            (
                (metric, slot)
                for metric in ("EPS_DILUTED", "REVENUE")
                for slot in (
                    "CURRENT_QUARTER",
                    "NEXT_QUARTER",
                    "CURRENT_FISCAL_YEAR",
                    "NEXT_FISCAL_YEAR",
                )
            ),
            start=1,
        )
    ]
    db = FakeDb(
        {
            CeriCompany: [company],
            CeriEstimateSnapshot: estimates,
            CeriRevisionFeature: [],
        }
    )

    result = CeriFeatureRebuildService(revisions=revisions).rebuild(
        db,
        CeriFeatureRebuildRequest(ticker="MSFT", as_of_session=date(2026, 8, 7)),
    )

    rows = db.rows[CeriRevisionFeature]
    assert result.features == 16
    assert result.processed_companies == 1
    assert len(rows) == 16
    assert {row.window_days for row in rows} == {7, 90}
    assert rows[0].acceleration == Decimal("0.10") or rows[1].acceleration == Decimal("0.10")
    assert any(row.feature_family == "confidence" for row in db.rows[CeriDerivedFeature])


def test_price_response_parent_selection_rejects_unusable_events() -> None:
    reported = CeriEarningsActual(
        id=10,
        source_record_id=10,
        company_id=7,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        report_session=date(2026, 8, 12),
        actual_value=Decimal("1"),
        event_kind="REPORTED",
    )
    upcoming = CeriEarningsActual(
        id=11,
        source_record_id=11,
        company_id=7,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 9, 30),
        report_session=date(2026, 8, 13),
        actual_value=None,
        event_kind="UPCOMING",
    )
    rejected_guidance = CeriGuidanceEvent(
        id=20,
        source_record_id=20,
        company_id=7,
        effective_session=date(2026, 8, 14),
        accepted_for_scoring=False,
    )
    event = CeriCatalystEvent(
        id=30,
        company_id=7,
        category="REGULATORY",
        subject_key="other-issuer",
    )
    rejected_catalyst = CeriCatalystEventRevision(
        id=31,
        catalyst_event_id=30,
        source_record_id=31,
        revision_number=1,
        is_current=True,
        effective_session=date(2026, 8, 14),
        status="SCHEDULED",
        direction="UNKNOWN",
        issuer_relevance=False,
        relevance_reason="ISSUER_RELEVANCE_MISMATCH",
    )
    db = FakeDb(
        {
            CeriEarningsActual: [reported, upcoming],
            CeriGuidanceEvent: [rejected_guidance],
            CeriCatalystEvent: [event],
            CeriCatalystEventRevision: [rejected_catalyst],
        }
    )

    selected = _latest_price_event(db, 7, date(2026, 8, 14))

    assert selected is not None
    assert selected[0:2] == ("EARNINGS", 10)


def test_price_response_parent_is_absent_when_all_events_are_rejected() -> None:
    upcoming = CeriEarningsActual(
        id=11,
        source_record_id=11,
        company_id=7,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 9, 30),
        report_session=date(2026, 8, 13),
        actual_value=None,
        event_kind="UPCOMING",
    )
    rejected_guidance = CeriGuidanceEvent(
        id=20,
        source_record_id=20,
        company_id=7,
        effective_session=date(2026, 8, 12),
        accepted_for_scoring=None,
    )
    db = FakeDb(
        {
            CeriEarningsActual: [upcoming],
            CeriGuidanceEvent: [rejected_guidance],
            CeriCatalystEvent: [],
            CeriCatalystEventRevision: [],
        }
    )

    assert _latest_price_event(db, 7, date(2026, 8, 14)) is None


class StubRevisionService:
    def calculate_windows(self, _db, *, company_id, metric, period_slot, cutoff_at, mode):
        return [
            CeriRevisionFeature(
                company_id=company_id,
                metric=metric,
                period_key=f"{metric}:{period_slot}:current",
                period_slot=period_slot,
                as_of_session=cutoff_at.date(),
                window_days=window,
                actual_elapsed_days=window,
                absolute_change=Decimal("1.0" if window == 7 else "0.3"),
                pct_change=Decimal("0.1"),
                config_hash="hash",
                calculation_version="test",
            )
            for window in (7, 90)
        ]

    def with_acceleration(self, recent, _longer):
        recent.acceleration = Decimal("0.10")


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.next_id = 1
        self.scalar_calls = 0

    def scalars(self, statement):
        self.scalar_calls += 1
        model = statement.column_descriptions[0]["entity"]
        return FakeResult(self.rows.get(model, []))

    def add(self, row):
        self.rows.setdefault(type(row), []).append(row)

    def flush(self):
        for rows in self.rows.values():
            for row in rows:
                if getattr(row, "id", None) is None:
                    row.id = self.next_id
                    self.next_id += 1
def test_reused_revision_feature_copies_values_and_full_comparison_lineage() -> None:
    target = CeriRevisionFeature(
        company_id=1,
        metric="EPS_DILUTED",
        period_key="same-key",
        as_of_session=date(2026, 8, 14),
        window_days=30,
        current_snapshot_id=10,
        baseline_snapshot_id=11,
        current_source_record_id=100,
        baseline_source_record_id=101,
        comparison_mode="STALE_MODE",
        unavailable_reason="stale_reason",
        source_observation_ids_json=[100, 101],
        config_version="test",
        config_hash="hash",
        calculation_version="ceri-1.2.0",
        evidence_hash="old",
    )
    source = CeriRevisionFeature(
        company_id=1,
        metric="EPS_DILUTED",
        period_key="same-key",
        as_of_session=date(2026, 8, 14),
        window_days=30,
        current_snapshot_id=20,
        baseline_snapshot_id=21,
        current_source_record_id=200,
        baseline_source_record_id=201,
        provider_retrospective_source_record_id=201,
        comparison_mode="SAME_PROVIDER_RELATIVE",
        baseline_origin="PROVIDER_RETROSPECTIVE_WINDOW",
        known_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
        reference_at=datetime(2026, 7, 15, 12, tzinfo=UTC),
        pct_change=Decimal("5.0"),
        unavailable_reason=None,
        source_observation_ids_json=[200, 201],
        provider_selection_reason="same_provider_relative",
        config_version="test",
        config_hash="hash",
        calculation_version="ceri-1.2.0",
        evidence_hash="new",
    )

    _copy_revision_derived(target, source)

    assert target.pct_change == Decimal("5.0")
    assert target.current_snapshot_id == 20
    assert target.baseline_snapshot_id == 21
    assert target.current_source_record_id == 200
    assert target.baseline_source_record_id == 201
    assert target.provider_retrospective_source_record_id == 201
    assert target.comparison_mode == "SAME_PROVIDER_RELATIVE"
    assert target.known_at == source.known_at
    assert target.reference_at == source.reference_at
    assert target.unavailable_reason is None
    assert target.source_observation_ids_json == [200, 201]
    assert target.provider_selection_reason == "same_provider_relative"


def test_optimized_batch_second_run_skips_unchanged_companies() -> None:
    companies = [CeriCompany(id=index, ticker=f"T{index}") for index in (1, 2, 3)]
    estimates = [_estimate(company.id) for company in companies]
    db = FakeDb({
        CeriCompany: companies,
        CeriEstimateSnapshot: estimates,
        CeriRevisionFeature: [],
        CeriFeatureBuildState: [],
    })
    service = CeriFeatureRebuildService(revisions=StubRevisionService())
    request = CeriFeatureRebuildRequest(
        tickers=("T1", "T2", "T3"), as_of_session=date(2026, 8, 7)
    )

    first = service.rebuild(db, request)
    second = service.rebuild(db, request)

    assert first.companies_rebuilt == 3
    assert second.companies_rebuilt == 0
    assert second.companies_skipped_unchanged == 3
    assert len(db.rows[CeriFeatureBuildState]) == 3
    identities = {
        (row.company_id, row.metric, row.period_key, row.window_days)
        for row in db.rows[CeriRevisionFeature]
    }
    assert len(identities) == len(db.rows[CeriRevisionFeature])


def test_changed_company_evidence_rebuilds_only_that_company() -> None:
    companies = [CeriCompany(id=index, ticker=f"T{index}") for index in (1, 2)]
    estimates = [_estimate(company.id) for company in companies]
    db = FakeDb({
        CeriCompany: companies,
        CeriEstimateSnapshot: estimates,
        CeriRevisionFeature: [],
        CeriFeatureBuildState: [],
    })
    service = CeriFeatureRebuildService(revisions=StubRevisionService())
    request = CeriFeatureRebuildRequest(
        tickers=("T1", "T2"), as_of_session=date(2026, 8, 7)
    )
    service.rebuild(db, request)

    estimates[0].consensus = Decimal("2.5")
    result = service.rebuild(db, request)

    assert result.companies_rebuilt == 1
    assert result.companies_skipped_unchanged == 1


def test_relevant_price_bar_change_forces_rebuild() -> None:
    company = CeriCompany(id=1, ticker="T1")
    db = FakeDb({
        CeriCompany: [company],
        CeriEstimateSnapshot: [_estimate(1)],
        CeriRevisionFeature: [],
        CeriFeatureBuildState: [],
        PriceBar: [],
    })
    service = CeriFeatureRebuildService(revisions=StubRevisionService())
    request = CeriFeatureRebuildRequest(ticker="T1", as_of_session=date(2026, 8, 7))
    service.rebuild(db, request)
    db.rows[PriceBar].append(PriceBar(
        id=900,
        ticker="T1",
        bar_date=date(2026, 8, 6),
        timeframe="1d",
        close=Decimal("10"),
        source="ibkr",
        what_to_show="ADJUSTED_LAST",
    ))

    result = service.rebuild(db, request)

    assert result.companies_rebuilt == 1
    assert result.companies_skipped_unchanged == 0


def test_config_hash_change_forces_rebuild() -> None:
    company = CeriCompany(id=1, ticker="T1")
    db = FakeDb({
        CeriCompany: [company],
        CeriEstimateSnapshot: [_estimate(1)],
        CeriRevisionFeature: [],
        CeriFeatureBuildState: [],
    })
    request = CeriFeatureRebuildRequest(ticker="T1", as_of_session=date(2026, 8, 7))
    CeriFeatureRebuildService(revisions=StubRevisionService()).rebuild(db, request)
    changed_config = replace(load_ceri_config(), config_hash="changed-config-hash")

    result = CeriFeatureRebuildService(
        config=changed_config, revisions=StubRevisionService()
    ).rebuild(db, request)

    assert result.companies_rebuilt == 1
    assert result.companies_skipped_unchanged == 0


def test_batch_query_families_are_constant_and_company_failures_are_isolated() -> None:
    companies = [CeriCompany(id=index, ticker=f"T{index}") for index in (1, 2, 3)]
    db = FakeDb({
        CeriCompany: companies,
        CeriEstimateSnapshot: [_estimate(company.id) for company in companies],
        CeriRevisionFeature: [],
        CeriFeatureBuildState: [],
    })

    class FailingRevisionService(StubRevisionService):
        def calculate_windows(self, db, *, company_id, **kwargs):
            if company_id == 2:
                raise ValueError("fixture failure")
            return super().calculate_windows(db, company_id=company_id, **kwargs)

    result = CeriFeatureRebuildService(revisions=FailingRevisionService()).rebuild(
        db,
        CeriFeatureRebuildRequest(
            tickers=("T1", "T2", "T3"), as_of_session=date(2026, 8, 7)
        ),
    )

    assert result.companies_rebuilt == 2
    assert result.failed == 1
    assert result.errors[0]["company_id"] == 2
    assert db.scalar_calls <= 12


def _estimate(company_id: int) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=company_id,
        source_record_id=company_id,
        company_id=company_id,
        metric="EPS_DILUTED",
        fiscal_period_end=date(2027, 12, 31),
        period_type="CURRENT_QUARTER",
        canonical_period_slot="CURRENT_QUARTER",
        canonical_observation_key=f"{company_id}:EPS:CQ",
        consensus=Decimal("2.0"),
    )
