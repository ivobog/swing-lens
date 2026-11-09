import json
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models.tables import IBContract
from app.services import instrument_symbol_changes as changes
from app.services.ib_api import Contract
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    fetch_plan_from_dict,
    fetch_plan_to_dict,
)

SESSION = date(2026, 11, 6)


@pytest.fixture
def rename_day(monkeypatch):
    monkeypatch.setattr(
        changes, "datetime",
        SimpleNamespace(now=lambda _tz: datetime(2026, 11, 9, 10, tzinfo=UTC)),
    )
    return changes.active_symbol_change("TCBI")


def _contract(**overrides):
    return Contract(**{
        "conId": 21521400, "symbol": "TXCP", "secType": "STK", "exchange": "SMART",
        "primaryExchange": "TXSE", "currency": "USD", "localSymbol": "TXCP",
        **overrides,
    })


class ContractDb:
    def __init__(self, row=None):
        self.row = row

    def scalar(self, _query):
        return self.row

    def add(self, row):
        self.row = row

    def flush(self):
        pass


def test_symbol_change_activates_on_effective_date_only():
    assert changes.active_symbol_change("TCBI", as_of=date(2026, 11, 8)) is None
    change = changes.active_symbol_change(" tcbi ", as_of=date(2026, 11, 9))
    assert change.current_symbol == "TXCP"
    assert change.ib_conid == 21521400
    assert changes.active_symbol_change("TCBIO", as_of=date(2026, 11, 9)) is None


def test_resolver_qualifies_verified_conid_and_retains_source_ticker(rename_day):
    requests = []

    def qualify(request):
        requests.append(request)
        return [_contract()]

    db = ContractDb(IBContract(ticker="TCBI", resolution_status="FAILED"))
    result = resolve_us_stock_contract(db, "TCBI", SimpleNamespace(qualifyContracts=qualify))
    assert requests[0].conId == 21521400
    assert requests[0].symbol == ""
    assert result.ticker == result.cache_row.ticker == "TCBI"
    assert result.contract.symbol == result.cache_row.symbol == "TXCP"
    assert result.symbol_change_evidence == rename_day.evidence()
    assert result.cache_row.resolution_status == "RESOLVED"


@pytest.mark.parametrize("override", [
    {"conId": 474517460}, {"symbol": "TXCP PRB"}, {"currency": "CAD"},
    {"secType": "OPT"}, {"primaryExchange": "NASDAQ"},
])
def test_resolver_rejects_wrong_security_or_listing(rename_day, override):
    result = resolve_us_stock_contract(
        ContractDb(), "TCBI", SimpleNamespace(qualifyContracts=lambda _r: [_contract(**override)])
    )
    assert result.contract is None
    assert result.cache_row.ib_conid is None
    assert result.status == "FAILED"
    assert "SYMBOL_CHANGE_IDENTITY_MISMATCH" in result.error_message


def test_stale_cached_symbol_requires_requalification(rename_day):
    calls = []
    db = ContractDb(IBContract(
        ticker="TCBI", symbol="TCBI", ib_conid=21521400, sec_type="STK", currency="USD",
        primary_exchange="NASDAQ", resolution_status="RESOLVED",
    ))
    result = resolve_us_stock_contract(
        db, "TCBI", SimpleNamespace(qualifyContracts=lambda r: calls.append(r) or [_contract()])
    )
    assert len(calls) == 1
    assert result.contract.symbol == "TXCP"


def test_verified_cached_alias_retains_mapping_evidence(rename_day):
    db = ContractDb(IBContract(
        ticker="TCBI", symbol="TXCP", ib_conid=21521400, sec_type="STK", currency="USD",
        primary_exchange="TXSE", exchange="SMART", resolution_status="RESOLVED",
    ))
    result = resolve_us_stock_contract(db, "TCBI", object())
    assert result.contract.conId == 21521400
    assert result.symbol_change_evidence == rename_day.evidence()


def test_alias_requires_exact_evidence_and_frozen_source_session(rename_day):
    identity = _contract().dict()
    evidence = rename_day.evidence()
    accepts = changes.contract_identity_matches_ticker
    assert accepts("TCBI", identity, evidence, reviewed_session=SESSION)
    assert not accepts("TCBI", identity, evidence)
    assert not accepts("TCBI", identity, reviewed_session=SESSION)
    assert not accepts("TCBI", identity, {**evidence, "ib_conid": 474517460})
    assert not accepts("TCBI", identity, evidence, reviewed_session=date(2026, 11, 9))
    assert not accepts("OTHER", identity, evidence, reviewed_session=SESSION)


def test_registry_rejects_ambiguous_symbol_mapping(tmp_path):
    payload = json.loads(changes.REGISTRY_PATH.read_text())
    payload["changes"].append({**payload["changes"][0], "ib_conid": 474517460})
    path = tmp_path / "changes.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="REGISTRY_INVALID"):
        changes.load_symbol_changes(path)


def test_stale_cached_identity_is_not_frozen_into_a_new_plan(rename_day):
    from app.services.ib_fetch_plan_service import _contract_identities

    row = IBContract(
        ticker="TCBI", symbol="TCBI", ib_conid=21521400, resolution_status="RESOLVED",
        primary_exchange="NASDAQ", sec_type="STK", currency="USD",
    )
    db = SimpleNamespace(scalars=lambda _query: [row])
    assert _contract_identities(db, ["TCBI"]) == {}
    row.symbol = "TXCP"
    row.primary_exchange = "TXSE"
    assert _contract_identities(db, ["TCBI"])["TCBI"][0]["symbol"] == "TXCP"


def test_legacy_plan_roundtrip_does_not_add_alias_fields(rename_day):
    item = FetchPlanItem(
        ticker="TCBI", contract_status="MISSING", what_to_show="TRADES",
        action=FetchAction.CONTRACT_RESOLUTION_REQUIRED, duration=None, bar_size="1 day",
        current_bar_count=0, first_bar_date=None, latest_bar_date=None, required_bars=252,
        reason="Frozen deferred qualification", estimated_request_count=1,
        request_end_date=SESSION,
    )
    plan = FetchPlan(
        run_id=54, requested_tickers=["TCBI"], symbols_including_benchmarks=["TCBI"],
        items=[item], estimated_request_count=1, estimated_full_backfills=1,
        estimated_top_ups=0, estimated_refreshes=0, estimated_skips=0, warnings=[],
    )
    legacy = fetch_plan_to_dict(plan)
    assert "contract_symbol_change" not in legacy["items"][0]
    assert "runtime_symbol_change" not in legacy["items"][0]
    assert fetch_plan_to_dict(fetch_plan_from_dict(legacy)) == legacy
    mapped = replace(item, contract_identity=_contract().dict(),
                     contract_symbol_change=rename_day.evidence())
    payload = fetch_plan_to_dict(replace(plan, items=[mapped]))
    assert fetch_plan_to_dict(fetch_plan_from_dict(payload)) == payload
    assert payload["requested_tickers"] == ["TCBI"]
