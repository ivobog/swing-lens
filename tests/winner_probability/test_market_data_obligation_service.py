from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.services.winner_probability.market_data_obligation_service import (
    RecoveryNeed,
    build_recovery_request_plan,
    complete_basis_for_rows,
    price_series_watermark,
    required_outcome_sessions,
)


def _bar(session: date, basis: str, *, data_hash: str | None = None):
    return SimpleNamespace(
        id=int(session.strftime("%Y%m%d")),
        bar_date=session,
        what_to_show=basis,
        data_hash=data_hash or f"{basis}-{session.isoformat()}",
        revision_count=0,
    )


def test_required_outcome_sessions_are_entry_through_h5() -> None:
    assert required_outcome_sessions(date(2026, 8, 3), 5) == (
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
    )


def test_complete_basis_never_mixes_partial_adjusted_and_trades() -> None:
    sessions = required_outcome_sessions(date(2026, 8, 3), 5)
    adjusted = [_bar(session, "ADJUSTED_LAST") for session in sessions[:-1]]
    trades = [_bar(session, "TRADES") for session in sessions]

    selected, rows = complete_basis_for_rows([*adjusted, *trades], sessions)

    assert selected == "TRADES"
    assert tuple(row.bar_date for row in rows) == sessions


def test_incomplete_bases_do_not_form_a_mixed_sequence() -> None:
    sessions = required_outcome_sessions(date(2026, 8, 3), 5)
    adjusted = [_bar(session, "ADJUSTED_LAST") for session in sessions[:3]]
    trades = [_bar(session, "TRADES") for session in sessions[3:]]

    assert complete_basis_for_rows([*adjusted, *trades], sessions) == (None, ())


def test_recovery_requests_merge_overlapping_outcomes_by_contract_and_basis() -> None:
    needs = (
        RecoveryNeed(
            outcome_id=11,
            prediction_id=101,
            contract_id=7,
            ib_conid=12345,
            ticker="MSFT",
            what_to_show="ADJUSTED_LAST",
            missing_sessions=(date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)),
        ),
        RecoveryNeed(
            outcome_id=12,
            prediction_id=102,
            contract_id=7,
            ib_conid=12345,
            ticker="MSFT",
            what_to_show="ADJUSTED_LAST",
            missing_sessions=(date(2026, 8, 6), date(2026, 8, 7)),
        ),
        RecoveryNeed(
            outcome_id=11,
            prediction_id=101,
            contract_id=7,
            ib_conid=12345,
            ticker="MSFT",
            what_to_show="TRADES",
            missing_sessions=(date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)),
        ),
    )

    requests = build_recovery_request_plan(needs)

    assert len(requests) == 2
    adjusted = next(row for row in requests if row.what_to_show == "ADJUSTED_LAST")
    assert adjusted.outcome_ids == (11, 12)
    assert adjusted.first_missing_session == date(2026, 8, 5)
    assert adjusted.last_missing_session == date(2026, 8, 7)


def test_missing_entry_requests_entry_through_h5_while_suffix_stays_bounded() -> None:
    sessions = required_outcome_sessions(date(2026, 8, 3), 5)
    requests = build_recovery_request_plan(
        (
            RecoveryNeed(21, 201, 8, 54321, "AMD", "TRADES", sessions),
            RecoveryNeed(22, 202, 9, 65432, "NVDA", "TRADES", sessions[-1:]),
        )
    )

    amd = next(row for row in requests if row.ticker == "AMD")
    nvda = next(row for row in requests if row.ticker == "NVDA")
    assert (amd.first_missing_session, amd.last_missing_session) == (sessions[0], sessions[-1])
    assert (nvda.first_missing_session, nvda.last_missing_session) == (
        sessions[-1],
        sessions[-1],
    )


def test_price_watermark_is_order_independent_and_changes_with_bar_content() -> None:
    sessions = required_outcome_sessions(date(2026, 8, 3), 5)
    rows = [_bar(session, "ADJUSTED_LAST") for session in sessions]

    first = price_series_watermark(rows)
    second = price_series_watermark(list(reversed(rows)))
    changed = price_series_watermark(
        [*rows[:-1], _bar(sessions[-1], "ADJUSTED_LAST", data_hash="revised")]
    )

    assert first == second
    assert changed != first
