"""Bounded native SQL selection with exact authorization/revision fallbacks."""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session

from app.models.tables import PriceBar, PriceBarRevision
from app.services import price_bar_repository as prices
from app.services.ib_bar_admission_service import immutable_bar_fingerprint

CUTOFF = datetime(2026, 11, 12, 8, tzinfo=UTC)
DAY = date(2026, 11, 11)
KEY = ("AAPL", "TRADES", "1 day")


class PriceSession(Session):
    """Real SQLite price queries; retained revisions supplied as typed ORM rows.

    Native PostgreSQL receipt suites independently verify SQL revision persistence.
    This adapter exercises the unchanged production reconstruction algorithm.
    """
    revisions = ()

    def scalars(self, statement, *args, **kwargs):
        if any(getattr(table, "name", None) == "price_bar_revisions"
               for table in statement.get_final_froms()):
            return iter(self.revisions)
        return super().scalars(statement, *args, **kwargs)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    PriceBar.__table__.create(engine)
    monkeypatch.setattr(prices, "_pipeline_acquisition_visibilities", lambda *_a, **_k: {})
    with PriceSession(engine, autoflush=False) as session:
        yield session
    engine.dispose()


def values(identifier, *, day=DAY, source="TRADES", after=False, close=100, **extra):
    seen = CUTOFF + timedelta(hours=1) if after else CUTOFF - timedelta(days=1)
    return {
        "id": identifier, "ticker": "AAPL", "bar_date": day, "timeframe": "1 day",
        "what_to_show": source, "source": "IB", "open": Decimal(close),
        "high": Decimal(close + 1), "low": Decimal(close - 1), "close": Decimal(close),
        "volume": Decimal(1000), "created_at": seen, "first_seen_at": seen,
        "last_seen_at": seen, "revised_at": None, "revision_count": 0,
        "data_hash": str(close), **extra,
    }


def seed(db, *rows):
    db.execute(insert(PriceBar), rows)
    db.commit()


def load(db, *, limited=True, callback=None, sources=("TRADES",), priority=("TRADES",),
         tickers=("AAPL",)):
    return prices.load_price_bar_rows_for_context(
        db, tickers, what_to_show=sources, timeframes=("1 day",),
        max_session=DAY, as_of=CUTOFF, calculation_context_id=106,
        session_count=2 if limited else None, one_source_per_session=limited,
        source_priority=priority, checkpoint_callback=callback,
    )


def visibility(members, **extra):
    return prices.PipelineAcquisitionVisibility(
        pipeline_run_id=61, upload_run_id=59, calculation_context_id=106,
        acquisition_plan_id="plan", scope_id="scope", refresh_cycle_id="cycle",
        fetch_run_id=2, fetch_item_id=3, completed_at=CUTOFF + timedelta(hours=3),
        bar_admissions=tuple(members), legacy_success=False, **extra,
    )


def member(row, *, accepted_at, receipt_hash):
    return {
        "price_bar_id": row.id, "immutable_fingerprint": immutable_bar_fingerprint(row),
        "data_hash": row.data_hash, "revision_count": row.revision_count,
        "accepted_at": accepted_at.isoformat(), "receipt_hash": receipt_hash,
    }


def test_two_sessions_materialize_only_needed_rows_and_match_full_history(db):
    seed(db, *(values(index + 1, day=DAY - timedelta(days=index)) for index in range(500)))
    loaded = []
    def observe(row, _context):
        loaded.append(row.id)
    event.listen(PriceBar, "load", observe)
    try:
        limited = load(db)
    finally:
        event.remove(PriceBar, "load", observe)
    assert [row.bar_date for row in limited] == [DAY - timedelta(days=1), DAY]
    assert len(loaded) == 2
    complete = load(db, limited=False)
    assert [row.id for row in limited] == [row.id for row in complete[-2:]]


def test_invalid_latest_revision_pages_back_to_two_provable_dates(db):
    seed(db,
         values(1, revised_at=CUTOFF + timedelta(hours=1), revision_count=1),
         values(2, day=DAY - timedelta(days=2)),
         values(3, day=DAY - timedelta(days=7)),
         values(4, day=DAY - timedelta(days=9)))
    progress = []
    selected = load(db, callback=lambda **item: progress.append(item))
    assert [row.id for row in selected] == [3, 2]
    assert [row.id for row in selected] == [row.id for row in load(db, limited=False)[-2:]]
    pages = [item for item in progress if item["phase"] == "PRICE_SESSION_PAGE_VERIFIED"]
    assert len(pages) == 2
    assert pages[0]["processed"] == 0 and pages[1]["processed"] == 1


def test_unowned_post_cutoff_bar_never_displaces_valid_history(db):
    seed(db, values(1, after=True), values(2, day=DAY - timedelta(days=1)),
         values(3, day=DAY - timedelta(days=2)))
    assert [row.id for row in load(db)] == [3, 2]


def test_tickers_finish_independently_when_one_needs_older_candidate_pages(db):
    seed(db, values(1), values(2, day=DAY - timedelta(days=1)),
         values(10, ticker="MSFT", revised_at=CUTOFF + timedelta(hours=1), revision_count=1),
         values(11, ticker="MSFT", day=DAY - timedelta(days=1),
                revised_at=CUTOFF + timedelta(hours=1), revision_count=1),
         values(12, ticker="MSFT", day=DAY - timedelta(days=2)),
         values(13, ticker="MSFT", day=DAY - timedelta(days=3)))
    rows = load(db, tickers=("AAPL", "MSFT", "EMPTY"))
    assert [row.id for row in rows] == [2, 1, 13, 12]


def test_all_sources_on_selected_dates_preserve_priority_and_session_gaps(db):
    seed(db, values(1), values(2, source="ADJUSTED_LAST", close=80),
         values(3, day=DAY - timedelta(days=6), source="ADJUSTED_LAST", close=70),
         values(4, day=DAY - timedelta(days=9)))
    rows = load(db, sources=("TRADES", "ADJUSTED_LAST"))
    assert [(row.id, row.what_to_show) for row in rows] == [(3, "ADJUSTED_LAST"), (1, "TRADES")]
    reverse = load(db, sources=("TRADES", "ADJUSTED_LAST"), priority=("ADJUSTED_LAST", "TRADES"))
    assert [row.id for row in reverse] == [3, 2]


def test_index_preserves_exact_receipt_and_timestamp_hash_tie_breaking(db, monkeypatch):
    seed(db, values(1, after=True))
    row = db.get(PriceBar, 1)
    matching = member(row, accepted_at=CUTOFF + timedelta(hours=2), receipt_hash="a")
    tie = dict(matching, receipt_hash="z")
    unrelated = dict(matching, price_bar_id=99, receipt_hash="other")
    different_version = dict(
        matching, immutable_fingerprint="f" * 64, receipt_hash="later-version",
        accepted_at=(CUTOFF + timedelta(hours=3)).isoformat(),
    )
    vis = {KEY: visibility([matching, tie, unrelated, different_version])}
    monkeypatch.setattr(prices, "_pipeline_acquisition_visibilities", lambda *_a, **_k: vis)
    index = prices._index_visibility_admissions(vis)
    legacy = prices._price_bar_visibility_boundary(row, as_of=CUTOFF, visibilities=vis)
    expected = row._ib_bar_admission
    row._ib_bar_admission = None
    indexed = prices._price_bar_visibility_boundary(
        row, as_of=CUTOFF, visibilities=vis, admission_index=index)
    assert indexed == legacy
    assert row._ib_bar_admission == expected == tie
    assert load(db)[0]._ib_bar_admission == tie
    # A new read indexes the changed immutable receipt set rather than reusing a
    # global successful-validation or row-ID cache.
    newer = dict(
        matching, accepted_at=(CUTOFF + timedelta(hours=3)).isoformat(), receipt_hash="new",
    )
    vis[KEY] = visibility([newer])
    assert load(db)[0]._ib_bar_admission == newer


def test_receipted_late_bar_reconstructs_exact_version_before_selection(db, monkeypatch):
    prior = CUTOFF + timedelta(hours=2)
    later = CUTOFF + timedelta(hours=4)
    seed(db, values(1, after=True, close=140, revised_at=later, revision_count=3),
         values(2, day=DAY - timedelta(days=1)))
    current = db.get(PriceBar, 1)
    accepted = PriceBar(**values(1, after=True, close=120, revised_at=prior, revision_count=2))
    # Match database timestamp/numeric representation, which is also what a
    # native receipt is sealed against.
    accepted.created_at = current.created_at
    accepted.first_seen_at = current.first_seen_at
    accepted.last_seen_at = current.last_seen_at
    receipt = member(accepted, accepted_at=CUTOFF + timedelta(hours=3), receipt_hash="accepted")
    monkeypatch.setattr(prices, "_pipeline_acquisition_visibilities",
                        lambda *_a, **_k: {KEY: visibility([receipt])})
    db.revisions = (
        PriceBarRevision(id=10, price_bar_id=1, revision_number=2, observed_at=prior,
                         previous_data_hash="100", previous_values_json={}),
        PriceBarRevision(id=11, price_bar_id=1, revision_number=3, observed_at=later,
                         previous_data_hash="120", previous_values_json={
                             "open": "120", "high": "121", "low": "119", "close": "120",
                             "volume": "1000", "source": "IB", "adjustment_type": None,
                         }),
    )
    rows = load(db)
    assert rows[-1].id == 1 and rows[-1].close == 120 and rows[-1].revision_count == 2
    assert rows[-1]._ib_bar_admission == receipt
    assert rows[-1]._pit_projection_revision_id == 11


def test_empty_history_and_cancel_during_verified_page(db):
    assert load(db) == []
    seed(db, values(1, revised_at=CUTOFF + timedelta(hours=1), revision_count=1),
         values(2, day=DAY - timedelta(days=2)), values(3, day=DAY - timedelta(days=3)))
    class Cancelled(RuntimeError):
        pass
    def cancel(**progress):
        if progress["phase"] == "PRICE_SESSION_PAGE_VERIFIED":
            raise Cancelled
    with pytest.raises(Cancelled):
        load(db, callback=cancel)
    assert not db.dirty
