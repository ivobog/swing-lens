"""Targeted supported Run 43 acquisition repair and operator continuation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "output/run43-recovery"
FORENSICS = ROOT / "docs/forensics/run43-market-data-readiness"


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def preserved(db):
    from sqlalchemy import text

    context = db.scalar(text("select to_jsonb(c) from market_calculation_contexts c where id=78"))
    expected_context = json.loads((FORENSICS / "context.json").read_text())[0]
    assert context == expected_context, "Run 43 frozen context changed"
    old_items = list(
        db.scalars(
            text(
                "select to_jsonb(i)-'execution_token' from ib_fetch_items i "
                "where id in (5599,5600) order by id"
            )
        )
    )
    expected = json.loads((FORENSICS / "dds-failed-items.json").read_text())
    assert old_items == expected, "Original DDS failure evidence changed"
    return {
        "original_failed_items_unchanged": True,
        "frozen_context_unchanged": True,
        "failed_items_sha256": fingerprint(old_items),
        "context_sha256": fingerprint(context),
    }


def visible_coverage(db):
    from sqlalchemy import text

    from app.services.price_bar_repository import load_price_bar_rows_for_context

    context = db.scalar(text("select to_jsonb(c) from market_calculation_contexts c where id=78"))
    symbols = list(
        db.scalars(
            text(
                "select ticker from market_data_session_dispositions "
                "where pipeline_run_id=43 and is_current_revision order by ticker"
            )
        )
    )
    from datetime import date

    rows = load_price_bar_rows_for_context(
        db,
        symbols,
        what_to_show=("TRADES", "ADJUSTED_LAST"),
        timeframes=("1 day",),
        max_session=date.fromisoformat(context["latest_completed_session"]),
        as_of=datetime.fromisoformat(context["cutoff_at"]),
        calculation_context_id=78,
        session_count=1,
        one_source_per_session=True,
        source_priority=("TRADES", "ADJUSTED_LAST"),
    )
    latest = {row.ticker: row.bar_date.isoformat() for row in rows}
    return {
        "expected_session": context["latest_completed_session"],
        "requested_tickers": len(symbols),
        "ready_tickers": sum(latest.get(s) == context["latest_completed_session"] for s in symbols),
        "blockers": [s for s in symbols if latest.get(s) != context["latest_completed_session"]],
        "dds": [
            {
                "id": row.id,
                "session": row.bar_date,
                "feed": row.what_to_show,
                "first_fetch_run_id": row.first_fetch_run_id,
                "first_fetch_item_id": row.first_fetch_item_id,
                "first_seen_at": row.first_seen_at,
            }
            for row in rows
            if row.ticker == "DDS"
        ],
    }


def enqueue(db):
    from app.models.tables import IBFetchRun, PipelineRun
    from app.services.ib_fetch_job_service import (
        FetchJobOptions,
        _admit_fetch_authority,
        _subset_fetch_plan,
        create_queued_fetch_run,
        submit_fetch_job,
    )
    from app.services.ib_fetch_plan_service import (
        _decision_counts,
        fetch_plan_from_dict,
        fetch_plan_to_dict,
    )
    from app.services.ib_historical_request_scope import DAILY_BAR_ADMISSION_V2
    from app.services.scope_refresh_adoption import require_semantic_authority
    from app.services.work_scope_identity import AcquisitionPlanRevisionReason

    assert not (OUT / "repair-enqueued.json").exists(), "Repair already enqueued; observe it"
    proof = preserved(db)
    pipeline = db.get(PipelineRun, 43)
    assert pipeline.upload_run_id == 43 and pipeline.status == "BLOCKED"
    assert pipeline.current_step == "FETCHING_MARKET_DATA"
    previous = db.get(IBFetchRun, 23)
    assert previous.status == "PARTIAL" and previous.failure_count == 2
    original_payload = pipeline.result_json["ib_fetch_plan"]
    original = fetch_plan_from_dict(original_payload)
    assert fetch_plan_to_dict(original) == original_payload
    # Prove the supported pipeline resume will recover the same existing fetch
    # authority and preserve successes; serialization must not invent a replan.
    restored_authority = _admit_fetch_authority(
        db, original, FetchJobOptions(True), parent_scope_id=pipeline.scope_id
    )
    assert restored_authority == require_semantic_authority(previous)
    subset = _subset_fetch_plan(original, {("DDS", "ADJUSTED_LAST"), ("DDS", "TRADES")})
    items = [
        replace(item, response_admission_policy=DAILY_BAR_ADMISSION_V2) for item in subset.items
    ]
    repair = replace(subset, items=items, decision_counts=_decision_counts(items))
    assert repair.estimated_request_count == len(items) == 2
    assert {item.request_end_date.isoformat() for item in items} == {"2026-10-16"}
    options = FetchJobOptions(False)
    authority = _admit_fetch_authority(
        db,
        repair,
        options,
        parent_scope_id=pipeline.scope_id,
        previous_plan_id=previous.acquisition_plan_id,
        revision_reason=AcquisitionPlanRevisionReason.POLICY_CHANGE,
    )
    fetch = create_queued_fetch_run(db, repair, options, semantic_authority=authority)
    job = submit_fetch_job(db, fetch.id, repair, options)
    proof.update(
        pipeline_run_id=43,
        context_id=78,
        original_fetch_run_id=23,
        repair_fetch_run_id=fetch.id,
        repair_job_id=job.id,
        repair_authority=authority.as_dict(),
        plan=fetch_plan_to_dict(repair),
        queued_at_utc=datetime.now(UTC).isoformat(),
        frozen_plan_roundtrip_preserved=True,
    )
    db.commit()
    save("repair-enqueued.json", proof)
    print(
        json.dumps(
            {
                key: proof[key]
                for key in (
                    "repair_fetch_run_id",
                    "repair_job_id",
                    "original_failed_items_unchanged",
                    "frozen_context_unchanged",
                    "frozen_plan_roundtrip_preserved",
                )
            }
        )
    )


def observe(db):
    from sqlalchemy import text

    proof = preserved(db)
    enqueued = json.loads((OUT / "repair-enqueued.json").read_text())
    fetch_id = enqueued["repair_fetch_run_id"]
    job_id = enqueued["repair_job_id"]
    queries = {
        "pipeline": "select to_jsonb(p) from pipeline_runs p where id=43",
        "steps": "select to_jsonb(s) from pipeline_steps s where pipeline_run_id=43 order by id",
        "repair_fetch": "select to_jsonb(f)-'execution_token' from ib_fetch_runs f where id=:fetch",
        "repair_items": (
            "select to_jsonb(i)-'execution_token' from ib_fetch_items i "
            "where fetch_run_id=:fetch order by id"
        ),
        "jobs": (
            "select to_jsonb(j)-'execution_token' from background_jobs j "
            "where id=:job or related_run_id=43 order by id"
        ),
        "readiness": (
            "select to_jsonb(d) from market_data_session_dispositions d "
            "where pipeline_run_id=43 order by id"
        ),
        "dds_bars": (
            "select to_jsonb(b) from price_bars b where ticker='DDS' "
            "and bar_date>='2026-09-25' order by bar_date,what_to_show"
        ),
    }
    state = {
        name: list(db.scalars(text(query), {"fetch": fetch_id, "job": job_id}))
        for name, query in queries.items()
    }
    coverage = visible_coverage(db)
    state.update(
        preservation=proof, coverage=coverage, observed_at_utc=datetime.now(UTC).isoformat()
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    save("state-" + stamp + ".json", state)
    pipeline = state["pipeline"][0]
    print(
        json.dumps(
            {
                "pipeline_status": pipeline["status"],
                "stage": pipeline["current_step"],
                "error": pipeline["error_message"],
                "repair_status": state["repair_fetch"][0]["status"],
                "items": [
                    {
                        k: i[k]
                        for k in (
                            "id",
                            "what_to_show",
                            "status",
                            "fetched",
                            "inserted",
                            "error_message",
                        )
                    }
                    for i in state["repair_items"]
                ],
                "coverage": coverage,
                "readiness": dict(
                    Counter(
                        r["disposition"] for r in state["readiness"] if r["is_current_revision"]
                    )
                ),
                "steps": [{"name": s["step_name"], "status": s["status"]} for s in state["steps"]],
            },
            default=str,
        )
    )


def resume(db):
    from app.models.tables import IBFetchRun, PipelineRun
    from app.services.pipeline_service import resume_pipeline

    assert not (OUT / "pipeline-restarted.json").exists(), "Restart already enqueued"
    proof = preserved(db)
    repair = json.loads((OUT / "repair-enqueued.json").read_text())
    fetch = db.get(IBFetchRun, repair["repair_fetch_run_id"])
    assert fetch.status == "COMPLETED" and fetch.success_count == 2 and fetch.failure_count == 0
    coverage = visible_coverage(db)
    assert coverage["requested_tickers"] == coverage["ready_tickers"] == 238
    assert coverage["blockers"] == []
    pipeline = db.get(PipelineRun, 43)
    assert pipeline.status == "BLOCKED" and pipeline.current_step == "FETCHING_MARKET_DATA"
    assert pipeline.execution_authority_state == "ACTIVE"
    # The worker accepts a full restart from VALIDATING_RUN; mid-fetch resume is
    # not a supported execution boundary. Prior stage attempts are archived.
    from app.models.tables import BackgroundJob
    from app.services.background_job_service import request_job_cancel

    wrapper = db.get(BackgroundJob, repair["repair_job_id"])
    if wrapper.status == "QUEUED" and wrapper.error_message:
        # The acquisition committed; its progress-result serialization failed.
        # Cancel pointless queued wrapper retries through the supported API.
        request_job_cancel(db, wrapper.id)
        proof["repair_wrapper_retry_cancelled"] = wrapper.id
    pipeline = resume_pipeline(db, 43, resume_from_step="VALIDATING_RUN")
    proof.update(
        job_id=pipeline.result_json["background_job_id"],
        coverage=coverage,
        queued_at_utc=datetime.now(UTC).isoformat(),
    )
    db.commit()
    save("pipeline-restarted.json", proof)
    print(json.dumps(proof, default=str))


def main():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.settings import get_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("enqueue", "observe", "resume"))
    args = parser.parse_args()
    options = {"application_name": "run43-focused-recovery"}
    if args.action == "observe":
        options["options"] = "-c default_transaction_read_only=on -c statement_timeout=60000"
    engine = create_engine(get_settings().database_url, connect_args=options)
    try:
        with Session(engine) as db:
            assert db.scalar(text("select current_database()")) == "swinglens"
            {"enqueue": enqueue, "observe": observe, "resume": resume}[args.action](db)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
