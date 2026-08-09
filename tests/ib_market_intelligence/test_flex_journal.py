from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.ib_market_intelligence_tables import IBExecutionFill
from app.services.ib_market_intelligence.flex import (
    IBFlexClient,
    IBFlexError,
    parse_flex_report,
)
from app.services.ib_market_intelligence.journal import construct_trade_episodes

CSV_REPORT = (
    """AccountId,TradeID,TradeDate,TradeTime,Symbol,Buy/Sell,Quantity,TradePrice,"""
    """IBCommission,Fees,Currency,Exchange,OrderReference,FifoPnlRealized
U1234567,E1,20260807,093000,AAPL,BUY,60,100,1.00,0.10,USD,NASDAQ,SL-1,
U1234567,E2,20260807,093001,AAPL,BUY,40,101,0.80,0.10,USD,NASDAQ,SL-1,
U1234567,E3,20260808,100000,AAPL,SELL,50,105,1.00,0.20,USD,NASDAQ,SL-2,225
U1234567,E4,20260809,100000,AAPL,SELL,50,106,1.00,0.20,USD,NASDAQ,SL-3,275
"""
)


def test_flex_csv_parser_normalizes_and_masks_account():
    rows = parse_flex_report(CSV_REPORT)
    assert len(rows) == 4
    assert rows[0].symbol == "AAPL"
    assert rows[0].account_masked_label == "***4567"
    assert rows[0].account_hash and "U1234567" not in rows[0].account_hash
    assert rows[2].broker_realized_pnl == Decimal("225")


def test_flex_xml_parser_supports_trade_rows():
    payload = """<FlexQueryResponse><FlexStatements><FlexStatement><Trades>
    <Trade accountId="U1" tradeID="X1" tradeDate="20260807" tradeTime="093000"
      symbol="MSFT" buySell="BUY" quantity="1" tradePrice="420" currency="USD" />
    </Trades></FlexStatement></FlexStatements></FlexQueryResponse>"""
    rows = parse_flex_report(payload)
    assert rows[0].symbol == "MSFT"
    assert rows[0].price == Decimal("420")


def test_flex_timezone_less_timestamp_is_normalized_from_configured_zone():
    rows = parse_flex_report(CSV_REPORT, report_timezone="America/New_York")
    assert rows[0].trade_time == datetime(2026, 8, 7, 13, 30, tzinfo=UTC)


def test_flex_explicit_offset_is_normalized_to_utc():
    payload = (
        "AccountId,TradeID,DateTime,Symbol,Buy/Sell,Quantity,TradePrice\n"
        "U1,X1,2026-08-07T09:30:00-04:00,MSFT,BUY,1,420\n"
    )
    rows = parse_flex_report(payload, report_timezone="Europe/Zurich")
    assert rows[0].trade_time == datetime(2026, 8, 7, 13, 30, tzinfo=UTC)


def test_flex_client_two_step_flow_and_token_redaction():
    calls = []

    def transport(url, _timeout):
        calls.append(url)
        if "SendRequest" in url:
            return (
                "<FlexStatementResponse><Status>Success</Status>"
                "<ReferenceCode>ABC</ReferenceCode></FlexStatementResponse>"
            )
        return CSV_REPORT

    client = IBFlexClient(
        token="SECRET-TOKEN", base_url="https://example.test", transport=transport
    )
    reference, report = client.download("QUERY", attempts=1, poll_seconds=1)
    assert reference == "ABC" and report == CSV_REPORT
    assert all("SECRET-TOKEN" in call for call in calls)
    failing = IBFlexClient(
        token="SECRET-TOKEN",
        base_url="https://example.test",
        transport=lambda *_args: (_ for _ in ()).throw(RuntimeError("SECRET-TOKEN failed")),
    )
    with pytest.raises(IBFlexError) as exc:
        failing.send_request("QUERY")
    assert "SECRET-TOKEN" not in str(exc.value)


def _fill(fill_id, when, side, qty, price, commission="0", fees="0", pnl=None):
    return IBExecutionFill(
        id=fill_id,
        flex_import_run_id=1,
        external_execution_id=f"E{fill_id}",
        symbol="XYZ",
        side=side,
        execution_time=when,
        quantity=Decimal(str(qty)),
        price=Decimal(str(price)),
        commission=Decimal(commission),
        fees=Decimal(fees),
        broker_realized_pnl=Decimal(str(pnl)) if pnl is not None else None,
        raw_record_hash=f"h{fill_id}",
        is_superseded=False,
        is_excluded=False,
    )


def test_trade_episode_partial_fills_partial_exits_and_costs():
    start = datetime(2026, 8, 7, 13, 30, tzinfo=UTC)
    fills = [
        _fill(1, start, "BUY", 60, 100, "1", "0.1"),
        _fill(2, start + timedelta(seconds=1), "BUY", 40, 101, "1", "0.1"),
        _fill(3, start + timedelta(days=1), "SELL", 50, 105, "1", "0.2", 225),
        _fill(4, start + timedelta(days=2), "SELL", 50, 106, "1", "0.2", 275),
    ]
    episodes = construct_trade_episodes(fills)
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode.position == 0
    assert episode.entry_quantity == 100
    assert episode.exit_quantity == 100
    assert episode.gross_pnl == Decimal("510.0")
    assert episode.commissions == Decimal("4")
    assert episode.fees == Decimal("0.6")
    assert episode.broker_realized_pnl == Decimal("500")


def test_trade_episode_crossing_zero_opens_reversed_episode():
    start = datetime(2026, 8, 7, 13, 30, tzinfo=UTC)
    fills = [
        _fill(1, start, "BUY", 100, 10, "1"),
        _fill(2, start + timedelta(days=1), "SELL", 150, 12, "1.5"),
        _fill(3, start + timedelta(days=2), "BUY", 50, 11, "1"),
    ]
    episodes = construct_trade_episodes(fills)
    assert [episode.direction for episode in episodes] == ["LONG", "SHORT"]
    assert all(episode.position == 0 for episode in episodes)
    assert episodes[0].gross_pnl == Decimal("200")
    assert episodes[1].gross_pnl == Decimal("50")
