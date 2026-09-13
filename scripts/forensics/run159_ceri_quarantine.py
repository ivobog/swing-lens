from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.services.ceri.evidence_eligibility import (
    EXCLUDED,
    EvidenceDispositionRequest,
    append_evidence_dispositions,
)
from app.settings import Settings

FIRST_SNAPSHOT_ID = 11226
LAST_SNAPSHOT_ID = 11407
EXPECTED_COUNT = 182
EXPECTED_RUN_ID = 159
EXPECTED_LINEAGE_SHA256 = "58c7605806cf171d532480d945a822a922781d92abc4053777f27e55cc4581b6"
REASON_CODE = "UNAUTHORIZED_CERTIFICATION_EXECUTION"
INCIDENT_REFERENCE = "RUN159_CERTIFICATION_CLAIM_LEAK"
ACTOR_SOURCE = "swinglens-run159-quarantine-certification"

CONSUMER_MATRIX = [
    {
        "consumer": "CERI dashboard/latest API/screener/ranking",
        "selector": "CeriQueryService._filtered_snapshots/latest",
        "latest": True,
        "prior": False,
        "scope": "global or run",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI ticker detail/history API",
        "selector": "CeriQueryService.ticker/ticker_history",
        "latest": True,
        "prior": False,
        "scope": "global",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI change currentness",
        "selector": "_latest_referenced_snapshot_subquery",
        "latest": True,
        "prior": True,
        "scope": "global",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI alert currentness",
        "selector": "_latest_snapshot_subquery",
        "latest": True,
        "prior": False,
        "scope": "global",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI capture lifecycle comparison",
        "selector": "capture_service._prior_snapshot",
        "latest": False,
        "prior": True,
        "scope": "company history",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI change rebuild",
        "selector": "CeriChangeRebuildService._snapshots/comparison_history",
        "latest": False,
        "prior": True,
        "scope": "global or scoped",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI alert rebuild",
        "selector": "CeriAlertService.rebuild_alerts",
        "latest": False,
        "prior": True,
        "scope": "change batch",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI current export",
        "selector": "CeriExportService.current_view",
        "latest": True,
        "prior": False,
        "scope": "global or run",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "outcome feature export",
        "selector": "CeriOutcomeFeatureExportService.export_snapshots",
        "latest": False,
        "prior": False,
        "scope": "provided PIT set",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "controlled replay",
        "selector": "CeriControlledReplayService.replay",
        "latest": False,
        "prior": False,
        "scope": "source run",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "transition decision handoff manifest",
        "selector": "_build_decision_handoff_payload/_validate_handoff_temporal_lineage",
        "latest": False,
        "prior": False,
        "scope": "run",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "IB trade research journal",
        "selector": "journal CERI cutoff query",
        "latest": True,
        "prior": False,
        "scope": "run+ticker",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "run detail CERI summary",
        "selector": "run_routes._ceri_context",
        "latest": False,
        "prior": False,
        "scope": "run",
        "quarantine_aware": True,
        "modified": True,
    },
    {
        "consumer": "CERI operations/reproduction",
        "selector": "operations_status",
        "latest": False,
        "prior": False,
        "scope": "forensic/operational",
        "quarantine_aware": False,
        "modified": False,
        "reason": "must preserve raw evidence counts",
    },
    {
        "consumer": "pipeline/performance effect counts",
        "selector": "pipeline_executor/background_performance_baseline",
        "latest": False,
        "prior": False,
        "scope": "operational run",
        "quarantine_aware": False,
        "modified": False,
        "reason": "counts persisted effects, not decision semantics",
    },
    {
        "consumer": "provider purge/reproduction",
        "selector": "purge_service/snapshot_service",
        "latest": False,
        "prior": False,
        "scope": "explicit forensic operation",
        "quarantine_aware": False,
        "modified": False,
        "reason": "explicit source preservation/diagnostic path",
    },
    {
        "consumer": "Setup inputs",
        "selector": "Setup source loader/builders",
        "latest": False,
        "prior": False,
        "scope": "run",
        "quarantine_aware": None,
        "modified": False,
        "reason": "no CERI snapshot dependency",
    },
    {
        "consumer": "Winner features",
        "selector": "Winner feature/evidence services",
        "latest": False,
        "prior": False,
        "scope": "run",
        "quarantine_aware": None,
        "modified": False,
        "reason": "no CERI snapshot dependency",
    },
]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _canonical_hash(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, default=_json_value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source_ids(rows: list[dict[str, Any]]) -> list[int]:
    values: set[int] = set()
    for row in rows:
        lineage = row.get("evidence_lineage_json") or {}
        for state in lineage.get("evidence_states", []):
            if (
                state.get("evidence_type") == "SOURCE_RECORD"
                and state.get("evidence_id") is not None
            ):
                values.add(int(state["evidence_id"]))
    return sorted(values)


def _high_opportunity(row: dict[str, Any] | None) -> bool | None:
    if row is None:
        return None
    ledger = row.get("event_risk_ledger_json") or {}
    return bool(
        row.get("opportunity_score") is not None
        and float(row["opportunity_score"]) >= 7.0
        and row.get("posture") == "Positive"
        and row.get("event_risk_score") is not None
        and float(row["event_risk_score"]) <= 3.0
        and ledger.get("accepted_evidence") is True
    )


def analyze(connection) -> dict[str, Any]:
    schema_head = str(connection.scalar(text("select version_num from alembic_version")))
    snapshot_rows = [
        {key: _json_value(value) for key, value in row._mapping.items()}
        for row in connection.execute(
            text(
                "select * from ceri_score_snapshots where id between :first and :last order by id"
            ),
            {"first": FIRST_SNAPSHOT_ID, "last": LAST_SNAPSHOT_ID},
        )
    ]
    by_id = {int(row["id"]): row for row in snapshot_rows}
    expected_ids = list(range(FIRST_SNAPSHOT_ID, LAST_SNAPSHOT_ID + 1))
    latest_rows = [
        dict(row._mapping)
        for row in connection.execute(
            text(
                "with before_ranked as ("
                " select s.*,row_number() over(partition by company_id "
                "order by cutoff_at desc nulls last,as_of_session desc nulls last,"
                "id desc) rn"
                " from ceri_score_snapshots s),after_ranked as ("
                " select s.*,row_number() over(partition by company_id "
                "order by cutoff_at desc nulls last,as_of_session desc nulls last,"
                "id desc) rn"
                " from ceri_score_snapshots s where id not between :first and :last)"
                " select c.id contaminated_id,c.ticker,c.company_id,b.id latest_before_id,"
                " a.id latest_after_id,a.comparison_snapshot_id prior_after_id,"
                " c.comparison_snapshot_id prior_before_id,b.posture posture_before,"
                " a.posture posture_after,b.opportunity_score opportunity_before,"
                " a.opportunity_score opportunity_after,b.event_risk_score risk_before,"
                " a.event_risk_score risk_after,b.event_risk_ledger_json risk_ledger_before,"
                " a.event_risk_ledger_json risk_ledger_after "
                "from ceri_score_snapshots c join before_ranked b "
                "on b.company_id=c.company_id and b.rn=1 "
                "left join after_ranked a on a.company_id=c.company_id and a.rn=1 "
                "where c.id between :first and :last order by c.ticker"
            ),
            {"first": FIRST_SNAPSHOT_ID, "last": LAST_SNAPSHOT_ID},
        )
    ]
    comparison = []
    for row in latest_rows:
        before = {
            "opportunity_score": row["opportunity_before"],
            "event_risk_score": row["risk_before"],
            "posture": row["posture_before"],
            "event_risk_ledger_json": row["risk_ledger_before"],
        }
        after = (
            {
                "opportunity_score": row["opportunity_after"],
                "event_risk_score": row["risk_after"],
                "posture": row["posture_after"],
                "event_risk_ledger_json": row["risk_ledger_after"],
            }
            if row["latest_after_id"] is not None
            else None
        )
        comparison.append(
            {
                "ticker": row["ticker"],
                "latest_before": row["latest_before_id"],
                "latest_after": row["latest_after_id"],
                "prior_before": row["prior_before_id"],
                "prior_after": row["prior_after_id"],
                "classification_before": row["posture_before"],
                "classification_after": row["posture_after"],
                "opportunity_classification_before": _high_opportunity(before),
                "opportunity_classification_after": _high_opportunity(after),
                "reason": REASON_CODE,
            }
        )
    source_ids = _source_ids(snapshot_rows)
    has_dispositions = inspect(connection).has_table("ceri_evidence_dispositions")
    disposition_summary = None
    if has_dispositions:
        disposition_summary = dict(
            connection.execute(
                text(
                    "with effective as (select d.*,row_number() over("
                    "partition by ceri_snapshot_id order by created_at desc,id desc) rn "
                    "from ceri_evidence_dispositions d) "
                    "select count(*) filter(where rn=1 and disposition='EXCLUDED' "
                    "and ceri_snapshot_id between :first and :last) "
                    "intended_effective_excluded,"
                    "count(*) filter(where incident_reference=:incident "
                    "and ceri_snapshot_id not between :first and :last) "
                    "unrelated_incident_rows "
                    "from effective"
                ),
                {
                    "first": FIRST_SNAPSHOT_ID,
                    "last": LAST_SNAPSHOT_ID,
                    "incident": INCIDENT_REFERENCE,
                },
            )
            .one()
            ._mapping
        )
    downstream = {
        "ceri_change_events": {
            "count": int(
                connection.scalar(
                    text(
                        "select count(*) from ceri_change_events "
                        "where from_snapshot_id between 11226 and 11407 "
                        "or to_snapshot_id between 11226 and 11407"
                    )
                )
                or 0
            ),
            "classification": "A: purely historical artifacts, preserve",
        },
        "ceri_alert_events": {
            "count": int(
                connection.scalar(
                    text(
                        "select count(*) from ceri_alert_events a "
                        "join ceri_change_events c on c.id=a.source_change_event_id "
                        "where c.from_snapshot_id between 11226 and 11407 "
                        "or c.to_snapshot_id between 11226 and 11407"
                    )
                )
                or 0
            ),
            "classification": (
                "A: historical alert artifacts; currentness now resolves against eligible latest"
            ),
        },
        "transition_preflight_plans": {
            "count": int(
                connection.scalar(
                    text("select count(*) from transition_preflight_plans where upload_run_id=159")
                )
                or 0
            ),
            "classification": "C: immutable historical plan; preserve",
        },
        "transition_decision_handoff_manifests": {
            "count": int(
                connection.scalar(
                    text(
                        "select count(*) from transition_decision_handoff_manifests "
                        "where upload_run_id=159"
                    )
                )
                or 0
            ),
            "classification": "C: immutable decision artifact; none observed",
        },
        "setup_signal_snapshots": {
            "count": int(
                connection.scalar(
                    text("select count(*) from setup_signal_snapshots where run_id=159")
                )
                or 0
            ),
            "classification": "not derived",
        },
        "winner_prediction_snapshots": {
            "count": int(
                connection.scalar(
                    text("select count(*) from winner_prediction_snapshots where run_id=159")
                )
                or 0
            ),
            "classification": "not derived",
        },
        "controlled_replays": {
            "count": int(
                connection.scalar(
                    text("select count(*) from ceri_controlled_replays where source_run_id=159")
                )
                or 0
            ),
            "classification": "not derived",
        },
        "materialized_views": list(
            connection.execute(
                text(
                    "select matviewname from pg_matviews where schemaname='public' "
                    "order by matviewname"
                )
            ).scalars()
        ),
    }
    aggregates = {
        "contaminated_snapshots": len(snapshot_rows),
        "tickers_affected": len({row["ticker"] for row in snapshot_rows}),
        "selected_as_latest": sum(
            FIRST_SNAPSHOT_ID <= int(row["latest_before"]) <= LAST_SNAPSHOT_ID for row in comparison
        ),
        "selectable_as_prior": len(snapshot_rows),
        "already_selected_as_prior_by_newer_snapshot": int(
            connection.scalar(
                text(
                    "select count(*) from ceri_score_snapshots "
                    "where comparison_snapshot_id between 11226 and 11407"
                )
            )
            or 0
        ),
        "tickers_with_alternate_latest": sum(row["latest_after"] is not None for row in comparison),
        "tickers_without_alternate_latest": sum(row["latest_after"] is None for row in comparison),
        "latest_selections_changed": sum(
            row["latest_before"] != row["latest_after"] for row in comparison
        ),
        "prior_selections_changed": sum(
            row["prior_before"] != row["prior_after"] for row in comparison
        ),
        "lifecycle_classifications_changed": sum(
            row["classification_before"] != row["classification_after"] for row in comparison
        ),
        "opportunity_classifications_changed": sum(
            row["opportunity_classification_before"] != row["opportunity_classification_after"]
            for row in comparison
        ),
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "transaction": "REPEATABLE READ, READ ONLY",
        "schema_head": schema_head,
        "incident": {
            "run_id": EXPECTED_RUN_ID,
            "snapshot_range": [FIRST_SNAPSHOT_ID, LAST_SNAPSHOT_ID],
            "expected_count": EXPECTED_COUNT,
            "ids_exact": sorted(by_id) == expected_ids,
            "all_expected_run": all(row["run_id"] == EXPECTED_RUN_ID for row in snapshot_rows),
            "source_lineage_ids": source_ids,
            "source_lineage_sha256": hashlib.sha256(
                json.dumps(source_ids, separators=(",", ":")).encode()
            ).hexdigest(),
            "source_snapshot_rows_sha256": _canonical_hash(snapshot_rows),
            "tickers": sorted(row["ticker"] for row in snapshot_rows),
        },
        "aggregates": aggregates,
        "before_after": comparison,
        "consumer_matrix": CONSUMER_MATRIX,
        "downstream": downstream,
        "dispositions": disposition_summary,
    }


def _validate_preconditions(inventory: dict[str, Any]) -> None:
    incident = inventory["incident"]
    failures = []
    if incident["expected_count"] != inventory["aggregates"]["contaminated_snapshots"]:
        failures.append("snapshot count")
    if not incident["ids_exact"]:
        failures.append("contiguous IDs")
    if not incident["all_expected_run"]:
        failures.append("Run 159 ownership")
    if incident["source_lineage_sha256"] != EXPECTED_LINEAGE_SHA256:
        failures.append("incident lineage SHA-256")
    if failures:
        raise RuntimeError("quarantine precondition mismatch: " + ", ".join(failures))


def apply_quarantine(engine) -> dict[str, Any]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.execute(text("set transaction read only"))
        before = analyze(connection)
        connection.rollback()
    _validate_preconditions(before)
    if before["schema_head"] != "0074_ceri_evidence_quarantine":
        raise RuntimeError("schema must be 0074_ceri_evidence_quarantine before quarantine")
    before_hash = before["incident"]["source_snapshot_rows_sha256"]
    requests = tuple(
        EvidenceDispositionRequest(
            ceri_snapshot_id=snapshot_id,
            disposition=EXCLUDED,
            reason_code=REASON_CODE,
            incident_reference=INCIDENT_REFERENCE,
            actor_source=ACTOR_SOURCE,
            notes="Excluded from future decision semantics; original forensic evidence preserved.",
            metadata_json={
                "affected_run_id": EXPECTED_RUN_ID,
                "incident_lineage_sha256": EXPECTED_LINEAGE_SHA256,
            },
        )
        for snapshot_id in range(FIRST_SNAPSHOT_ID, LAST_SNAPSHOT_ID + 1)
    )
    with Session(engine) as db:
        active = int(
            db.scalar(
                text(
                    "select count(*) from background_jobs where status in "
                    "('QUEUED','RUNNING','RETRYING','RECOVERING')"
                )
            )
            or 0
        )
        if active:
            raise RuntimeError(f"runtime is not quiescent: {active} active durable jobs")
        inserted = append_evidence_dispositions(db, requests)
        db.commit()
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        connection.execute(text("set transaction read only"))
        after = analyze(connection)
        connection.rollback()
    if after["incident"]["source_snapshot_rows_sha256"] != before_hash:
        raise RuntimeError("source CERI snapshot rows changed during quarantine")
    dispositions = after["dispositions"] or {}
    if dispositions.get("intended_effective_excluded") != EXPECTED_COUNT:
        raise RuntimeError("not all intended snapshots are effectively excluded")
    if dispositions.get("unrelated_incident_rows") != 0:
        raise RuntimeError("incident quarantine included unrelated snapshots")
    after["application"] = {
        "inserted_rows_this_attempt": inserted,
        "source_rows_mutated": False,
        "source_snapshot_rows_sha256_before": before_hash,
        "source_snapshot_rows_sha256_after": after["incident"]["source_snapshot_rows_sha256"],
    }
    return after


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze or apply the Run 159 CERI quarantine")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    url = make_url(settings.database_url)
    if url.database != "swinglens":
        raise RuntimeError("this incident certification is restricted to the swinglens database")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        if args.apply:
            result = apply_quarantine(engine)
        else:
            with engine.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                connection.execute(text("set transaction read only"))
                result = analyze(connection)
                connection.rollback()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, default=_json_value), encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "schema_head": result["schema_head"],
                    "aggregates": result["aggregates"],
                    "dispositions": result["dispositions"],
                    "application": result.get("application"),
                },
                default=_json_value,
                sort_keys=True,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
