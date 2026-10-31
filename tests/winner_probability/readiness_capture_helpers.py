"""Separate native READY acquisitions; original legacy identity fixtures are retained."""

from itertools import count

from contextual_readiness_helpers import seal_contextual
from core_readiness_helpers import populate_fundamental_source, seal_core

_ids = count(300000)


def seal_winner_source(row, kind, *, evidence_id=None):
    if kind in {"REGIME", "SECTOR"}:
        raise ValueError("Use contextual sealing for snapshots")
    return seal_core(row, kind, evidence_id=evidence_id or next(_ids))


def ready_identity_context(**kwargs):
    from test_winner_calculation_identity_adoption import _identity_context, _refresh_handoff

    context, cutoff = _identity_context(**kwargs)
    ticker = context.tickers[0]
    populate_fundamental_source(ticker.fundamental_score, raw_row=ticker.raw_row)
    seal_winner_source(
        ticker.fundamental_score, "FUNDAMENTAL", evidence_id=ticker.fundamental_score.evidence_id
    )
    seal_winner_source(
        ticker.combined_result, "COMBINED", evidence_id=ticker.combined_result.evidence_id
    )
    ticker.technical_score.technical_confidence = "normal"
    seal_winner_source(
        ticker.technical_score,
        "TECHNICAL",
        evidence_id=ticker.technical_score.evidence_id,
    )
    for row in ticker.ranking_results:
        seal_winner_source(row, "RANKING", evidence_id=row.evidence_id)
    for market in context.market_regime_candidates:
        if market.debug_json.get("calculation_identity"):
            market.debug_json = {
                **market.debug_json,
                "input_symbols": {"primary_market": "SPY"},
                "market_inputs": {"SPY": {"insufficient_data": False}},
            }
            seal_contextual(market, evidence_id=market.evidence_id or next(_ids))
    seal_contextual(
        context.sector_rotation_snapshot,
        rows=(ticker.sector_row,),
        evidence_id=context.sector_rotation_snapshot.evidence_id,
    )
    _refresh_handoff(context, cutoff)
    return context, cutoff


def reseal_context(context, cutoff, source):
    from test_winner_calculation_identity_adoption import _refresh_handoff

    ticker = context.tickers[0]
    if source == "technical":
        seal_winner_source(ticker.technical_score, "TECHNICAL")
    elif source == "ranking":
        seal_winner_source(ticker.ranking_results[0], "RANKING")
    elif source in {"fundamental", "combined"}:
        seal_winner_source(
            getattr(ticker, "fundamental_score" if source == "fundamental" else "combined_result"),
            source.upper(),
        )
    elif source == "regime":
        seal_contextual(context.market_regime_snapshot, evidence_id=next(_ids))
    else:
        seal_contextual(
            context.sector_rotation_snapshot,
            rows=(ticker.sector_row,),
            evidence_id=next(_ids),
        )
    _refresh_handoff(context, cutoff)
