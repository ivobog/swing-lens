"""Native Phase-5 test authority. No production writer bypasses."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.tables import ExecutionConfigurationBinding, PipelineRun, PriceBar
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.configuration_delivery import persist_configuration_anchor
from app.services.market_calculation_context_service import create_pipeline_market_context


def bound_pipeline(db, run_id, configurations, *, pipeline_id=None, cutoff_at=None):
    pipeline = PipelineRun(upload_run_id=run_id, status="RUNNING")
    if pipeline_id is not None:
        pipeline.id = pipeline_id
    db.add(pipeline)
    db.flush()
    cutoff = create_pipeline_market_context(
        db, pipeline, cutoff_at=cutoff_at or datetime(2026, 9, 16, 21, tzinfo=UTC)
    )
    configs = {}
    for config in configurations:
        namespace = config.snapshot.family.namespace
        key = (
            namespace + ":" + config.values["profile"]["name"]
            if namespace == "core.ranking"
            else namespace
        )
        configs[key] = config
    anchor = persist_configuration_anchor(db, configs)
    db.add(
        ExecutionConfigurationBinding(
            binding_key=f"pipeline:{pipeline.id}",
            pipeline_run_id=pipeline.id,
            anchor_id=anchor["anchor_id"],
        )
    )
    db.flush()
    return cutoff, pipeline.id


def seed_price_frame(db, ticker, frame, cutoff_at):
    """Already observed source facts, bounded before the fixed test cutoff."""
    observed = cutoff_at - timedelta(minutes=1)
    rows = []
    from sqlalchemy import select

    existing = set(
        db.execute(
            select(PriceBar.bar_date, PriceBar.what_to_show).where(PriceBar.ticker == ticker)
        ).all()
    )
    for what in ("ADJUSTED_LAST", "TRADES"):
        for item in frame.to_dict(orient="records"):
            if (item["date"].date(), what) in existing:
                continue
            values = {
                key: Decimal(str(item[key])) for key in ("open", "high", "low", "close", "volume")
            }
            rows.append(
                PriceBar(
                    ticker=ticker,
                    bar_date=item["date"].date(),
                    timeframe="1 day",
                    what_to_show=what,
                    source="IBKR",
                    created_at=observed,
                    first_seen_at=observed,
                    last_seen_at=observed,
                    revision_count=0,
                    data_hash=Canonical.fingerprint(values),
                    **values,
                )
            )
    db.add_all(rows)
    db.flush()


def seed_native_core(db, *, run_id=7, extra_configurations=(), cutoff_at=None, raw_values=None):
    import pandas as pd
    from test_core_effective_configuration import core_configurations
    from test_fundamental_ranker_v2 import _quality_values
    from test_technical_work import _synthetic_frame

    from app.models.tables import RawCompanyRow, UploadRun
    from app.services.combined_decision import refresh_combined_results
    from app.services.core_effective_configuration import resolve_technical_configuration
    from app.services.earnings_date_parser import parse_earnings_date
    from app.services.fundamental_score_service import recalculate_run_fundamentals
    from app.services.ranking_profile_service import refresh_ranking_profile
    from app.services.technical_indicators import load_pine_defaults
    from app.services.technical_score_service import score_run_technicals
    from app.services.technical_scoring_config import load_technical_scoring_v4_config
    from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
    from app.settings import get_settings

    db.add(UploadRun(id=run_id, filename="native-mutation.csv", status="COMPLETED"))
    db.flush()
    db.add(
        RawCompanyRow(
            run_id=run_id,
            row_number=1,
            ticker="ACME",
            sector="Technology",
            sector_canonical="Information Technology",
            upcoming_earnings_date=parse_earnings_date(
                (raw_values or {}).get("upcoming_earnings_date")
            ),
            raw_json={
                "Symbol": "ACME",
                **(_quality_values() if raw_values is None else raw_values),
            },
        )
    )
    db.flush()
    configs = list(core_configurations())
    configs[1] = resolve_technical_configuration(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
        settings=get_settings(),
    )
    cutoff, pipeline_id = bound_pipeline(
        db, run_id, [*configs, *extra_configurations], cutoff_at=cutoff_at
    )
    frame = _synthetic_frame()
    frame["date"] = pd.bdate_range(end=cutoff.latest_completed_session, periods=len(frame))
    for ticker in ("ACME", "SPY", "QQQ", "XLK"):
        seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
    args = dict(market_cutoff=cutoff, pipeline_run_id=pipeline_id)
    f = recalculate_run_fundamentals(db, run_id, **args, effective_configuration=configs[0])[0]
    t = score_run_technicals(
        db, run_id, tickers=["ACME"], **args, effective_configuration=configs[1]
    )[0]
    c = refresh_combined_results(
        db,
        run_id,
        **args,
        effective_configuration=configs[2],
        source_evidence={"ACME": {"fundamental": f.evidence_id, "technical": t.evidence_id}},
    )[0]
    r = refresh_ranking_profile(
        db, run_id, "momentum_swing", **args, effective_configuration=configs[3]
    )[0]
    db.commit()
    return cutoff, configs, {"fundamental": f, "technical": t, "combined": c, "ranking": r}
