"""Read-only forensic artifact generator for CERI upload run 101.

This script never invokes ingestion, normalization, feature rebuild, capture, or
change generation.  PostgreSQL is explicitly placed in a read-only transaction
before evidence is queried.  The only writes are CSV/JSON files under docs/qa.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriScoreSnapshot
from app.services.ceri.config import load_ceri_config
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.settings import get_settings

RUN_ID = 101
OUT_DIR = Path("docs/qa")
CAPTURE_CUTOFF = datetime.fromisoformat("2026-08-13T19:12:51.330758+02:00")
SEC_SIGNATURE = "sec-guidance:910cfd73179f55a7"
EODHD_COMPONENTS = {
    "revision_magnitude",
    "revision_breadth",
    "revision_acceleration",
    "surprise_trend",
    "catalysts",
}
SEC_COMPONENTS = {"guidance"}


def _rows(connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True, default=str)
                    if isinstance(value, (dict, list, tuple))
                    else value
                    for key, value in row.items()
                }
            )


def _equal(left: Any, right: Any, tolerance: float = 1e-9) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), abs_tol=tolerance)
    return left == right


def _components(snapshot: CeriScoreSnapshot) -> list[dict[str, Any]]:
    return list((snapshot.opportunity_ledger_json or {}).get("components") or [])


def _independent_opportunity(
    components: list[dict[str, Any]],
    *,
    allowed: set[str] | None = None,
    threshold: float,
    conflict_penalty: float = 0.0,
) -> tuple[float, float | None, str]:
    selected = [
        component
        for component in components
        if allowed is None or str(component.get("name")) in allowed
    ]
    available = [
        component
        for component in selected
        if component.get("available") is True and component.get("value") is not None
    ]
    coverage = 100.0 * sum(float(component.get("weight") or 0.0) for component in available)
    if coverage + 1e-9 < threshold:
        return coverage, None, "Unrated"
    weighted = sum(
        max(0.0, min(10.0, float(component["value"]))) * float(component.get("weight") or 0.0)
        for component in available
    )
    available_weight = sum(float(component.get("weight") or 0.0) for component in available)
    score = max(0.0, min(10.0, weighted / available_weight - conflict_penalty))
    return coverage, score, "Rated"


def _independent_risk(snapshot: CeriScoreSnapshot, penalty_cap: float) -> float:
    ledger = snapshot.event_risk_ledger_json or {}
    components = ledger.get("components") or []
    dominant = max((float(row.get("score") or 0.0) for row in components), default=0.0)
    penalties = sum(float(row.get("value") or 0.0) for row in ledger.get("penalties") or [])
    return min(10.0, dominant + min(penalty_cap, penalties))


def _selected_guidance_ids(snapshot: CeriScoreSnapshot) -> list[int]:
    for component in _components(snapshot):
        if component.get("name") == "guidance" and component.get("available") is True:
            return [int(value) for value in component.get("evidence_ids") or []]
    return []


def _source_counts(
    snapshot: CeriScoreSnapshot, sources: dict[int, dict[str, Any]]
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    source_ids = (snapshot.component_json or {}).get("source_ids") or []
    for source_id in source_ids:
        source = sources.get(int(source_id))
        if source:
            counts[str(source["provider"])] += 1
    return counts


def main() -> None:
    settings = get_settings()
    config = load_ceri_config(settings.ceri_config_path, settings.ceri_taxonomy_path)
    engine = create_engine(
        settings.database_url,
        connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        snapshots = list(
            session.scalars(
                select(CeriScoreSnapshot)
                .where(CeriScoreSnapshot.run_id == RUN_ID)
                .order_by(CeriScoreSnapshot.ticker)
            )
        )

        source_rows = _rows(
            connection,
            """
            WITH ids AS (
              SELECT DISTINCT
                (jsonb_array_elements_text(COALESCE(component_json->'source_ids','[]')))::bigint id
              FROM ceri_score_snapshots WHERE run_id=:run_id
            )
            SELECT sr.id, sr.provider, sr.dataset, sr.published_at, sr.observed_at,
                   sr.ingested_at, sr.retrieved_at, sr.source_timestamp
            FROM ceri_source_records sr JOIN ids ON ids.id=sr.id
            """,
            run_id=RUN_ID,
        )
        sources = {int(row["id"]): row for row in source_rows}
        change_counts = {
            str(row["ticker"]): int(row["n"])
            for row in _rows(
                connection,
                """
                SELECT s.ticker,count(*) n FROM ceri_score_snapshots s
                JOIN ceri_change_events ce ON ce.to_snapshot_id=s.id
                WHERE s.run_id=:run_id AND ce.change_type='OPPORTUNITY_UPGRADED'
                GROUP BY s.ticker
                """,
                run_id=RUN_ID,
            )
        }
        alert_counts = {
            str(row["ticker"]): int(row["n"])
            for row in _rows(
                connection,
                """
                SELECT s.ticker,count(*) n FROM ceri_score_snapshots s
                JOIN ceri_change_events ce ON ce.to_snapshot_id=s.id
                JOIN ceri_alert_events ae ON ae.source_change_event_id=ce.id
                WHERE s.run_id=:run_id GROUP BY s.ticker
                """,
                run_id=RUN_ID,
            )
        }
        stale_guidance = {
            int(row["guidance_id"]): row
            for row in _rows(
                connection,
                """
                SELECT g.id guidance_id,g.effective_at,g.low_value,g.high_value,g.point_value,
                       g.unit,g.currency,g.action,g.metric,g.period_type,g.filing_accession,
                       g.evidence_locator
                FROM ceri_guidance_events g
                WHERE g.id = ANY(:ids)
                """,
                ids=[
                    identifier
                    for snapshot in snapshots
                    for identifier in _selected_guidance_ids(snapshot)
                ]
                or [-1],
            )
        }

        reproducer = CeriSnapshotService(config=config)
        audit_rows: list[dict[str, Any]] = []
        ablation_rows: list[dict[str, Any]] = []
        threshold = float(config.revision.minimum_component_coverage_pct)
        for snapshot in snapshots:
            components = _components(snapshot)
            penalties = snapshot.opportunity_ledger_json.get("penalties") or []
            conflict_penalty = sum(
                float(row.get("value") or 0.0)
                for row in penalties
                if row.get("name") == "conflict_penalty"
            )
            independent_coverage, independent_score, independent_state = _independent_opportunity(
                components,
                threshold=threshold,
                conflict_penalty=conflict_penalty,
            )
            independent_risk = _independent_risk(
                snapshot,
                float(config.event_risk.get("secondary_penalty_cap", 2.0)),
            )
            reproduction = reproducer.reproduce_snapshot(snapshot)
            available_components = [
                str(component.get("name"))
                for component in components
                if component.get("available") is True
            ]
            missing_components = [
                str(component.get("name"))
                for component in components
                if component.get("available") is not True
            ]
            selected_ids = _selected_guidance_ids(snapshot)
            flags: list[str] = []
            if snapshot.opportunity_coverage_pct < threshold and snapshot.posture != "Unrated":
                flags.append("BELOW_THRESHOLD_RATED")
            if snapshot.opportunity_score is None and change_counts.get(snapshot.ticker, 0):
                flags.append("NULL_SCORE_OPPORTUNITY_UPGRADE")
            if alert_counts.get(snapshot.ticker, 0):
                flags.append("UNRATED_ACTIONABLE_ALERT")
            if selected_ids:
                flags.append("STALE_SEC_GUIDANCE_SELECTED")
                values = [stale_guidance.get(identifier, {}) for identifier in selected_ids]
                if any(
                    abs(float(value.get(field))) > 1_000
                    for value in values
                    for field in ("low_value", "high_value", "point_value")
                    if value.get(field) is not None
                ):
                    flags.append("IMPLAUSIBLE_SEC_GUIDANCE_VALUE")
            if not available_components:
                flags.append("NO_AVAILABLE_OPPORTUNITY_COMPONENTS")
            source_count = _source_counts(snapshot, sources)
            reconstruction_match = all(
                (
                    _equal(snapshot.opportunity_coverage_pct, independent_coverage),
                    _equal(snapshot.opportunity_score, independent_score),
                    _equal(snapshot.event_risk_score, independent_risk),
                    (snapshot.posture == "Unrated") == (independent_state == "Unrated"),
                    reproduction.matches,
                )
            )
            audit_rows.append(
                {
                    "ticker": snapshot.ticker,
                    "snapshot_id": snapshot.id,
                    "company_id": snapshot.company_id,
                    "as_of_session": snapshot.as_of_session,
                    "cutoff_at": snapshot.cutoff_at,
                    "eodhd_evidence_count": source_count.get("eodhd", 0),
                    "sec_evidence_count": source_count.get("sec", 0),
                    "available_components": available_components,
                    "missing_components": missing_components,
                    "component_coverage": snapshot.opportunity_coverage_pct,
                    "independently_calculated_coverage": independent_coverage,
                    "stored_ceri_score": snapshot.opportunity_score,
                    "recalculated_ceri_score": independent_score,
                    "stored_event_risk": snapshot.event_risk_score,
                    "recalculated_event_risk": independent_risk,
                    "confidence": snapshot.data_confidence,
                    "confidence_coverage_pct": snapshot.coverage_pct,
                    "rating": snapshot.posture,
                    "stored_evidence_hash": snapshot.evidence_hash,
                    "reproduced_evidence_hash": reproduction.reproduced_hash,
                    "reconstruction_match": reconstruction_match,
                    "selected_guidance_ids": selected_ids,
                    "change_events": change_counts.get(snapshot.ticker, 0),
                    "alerts": alert_counts.get(snapshot.ticker, 0),
                    "anomaly_flags": flags,
                }
            )

            scenarios = {
                "FULL": None,
                "WITHOUT_EODHD": {
                    name
                    for name in {c.get("name") for c in components}
                    if name not in EODHD_COMPONENTS
                },
                "WITHOUT_SEC": {
                    name
                    for name in {c.get("name") for c in components}
                    if name not in SEC_COMPONENTS
                },
                "EODHD_ONLY": EODHD_COMPONENTS,
                "SEC_ONLY": SEC_COMPONENTS,
            }
            full_coverage = independent_coverage
            full_score = independent_score
            for scenario, allowed in scenarios.items():
                coverage, score, state = _independent_opportunity(
                    components,
                    allowed=allowed,
                    threshold=threshold,
                    conflict_penalty=conflict_penalty,
                )
                ablation_rows.append(
                    {
                        "ticker": snapshot.ticker,
                        "snapshot_id": snapshot.id,
                        "scenario": scenario,
                        "coverage_pct": coverage,
                        "confidence": "Insufficient",
                        "ceri_score": score,
                        "rating_state": state,
                        "event_risk_score": independent_risk,
                        "component_changed_vs_full": not _equal(coverage, full_coverage),
                        "score_changed_vs_full": not _equal(score, full_score),
                        "rating_changed_vs_full": state != independent_state,
                        "material_decision_changed": state != independent_state,
                        "lifecycle_or_alert_eligibility_changed": state != independent_state,
                    }
                )

        eodhd_rows = _eodhd_reconciliation(connection)
        sec_rows = _sec_reconciliation(connection, snapshots)
        summary = _summary(
            connection,
            config=config,
            audit_rows=audit_rows,
            eodhd_rows=eodhd_rows,
            sec_rows=sec_rows,
            ablation_rows=ablation_rows,
        )
        session.close()

    _csv(OUT_DIR / "run101_ceri_snapshot_audit.csv", audit_rows)
    _csv(OUT_DIR / "run101_eodhd_reconciliation.csv", eodhd_rows)
    _csv(OUT_DIR / "run101_sec_reconciliation.csv", sec_rows)
    _csv(OUT_DIR / "run101_source_ablation.csv", ablation_rows)
    (OUT_DIR / "run101_forensic_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


def _eodhd_reconciliation(connection) -> list[dict[str, Any]]:
    rows = _rows(
        connection,
        """
        WITH universe AS (
          SELECT DISTINCT upper(ticker) ticker FROM raw_company_rows WHERE run_id=:run_id
        ), ingestion AS (
          SELECT upper(scope_json->>'ticker') ticker,dataset,status,requested_count,fetched_count,
                 inserted_count,deduplicated_count,corrected_count,quarantined_count,
                 failed_count,retry_count,errors_json,id ingestion_run_id
          FROM ceri_ingestion_runs
          WHERE (scope_json->>'run_id')::int=:run_id AND provider='eodhd'
        ), normalized AS (
          SELECT upper(ir.scope_json->>'ticker') ticker,ir.dataset,
                 count(DISTINCT es.id) estimates,count(DISTINCT ea.id) actuals,
                 count(DISTINCT cr.id) catalyst_revisions,
                 count(DISTINCT es.id) FILTER
                   (WHERE es.canonical_currency IS NULL) missing_currency,
                 count(DISTINCT es.id) FILTER
                   (WHERE es.canonical_currency IS NOT NULL) usable_currency
          FROM ceri_ingestion_runs ir
          LEFT JOIN ceri_source_records sr ON sr.ingestion_run_id=ir.id
          LEFT JOIN ceri_estimate_snapshots es ON es.source_record_id=sr.id
          LEFT JOIN ceri_earnings_actuals ea ON ea.source_record_id=sr.id
          LEFT JOIN ceri_catalyst_event_revisions cr ON cr.source_record_id=sr.id
          WHERE (ir.scope_json->>'run_id')::int=:run_id AND ir.provider='eodhd'
          GROUP BY 1,2
        ), features AS (
          SELECT c.ticker,count(*) features,count(*) FILTER(WHERE rf.pct_change IS NOT NULL) usable
          FROM ceri_companies c JOIN ceri_revision_features rf ON rf.company_id=c.id
          WHERE rf.as_of_session='2026-08-13' AND rf.config_hash=:config_hash GROUP BY c.ticker
        )
        SELECT u.ticker,u.ticker||'.US' provider_symbol,'US' exchange,
          i.dataset,i.status,i.ingestion_run_id,i.requested_count provider_observations,
          i.fetched_count,i.inserted_count,i.deduplicated_count,i.corrected_count,
          i.quarantined_count,i.failed_count,i.retry_count,i.errors_json,
          COALESCE(n.estimates,0) normalized_estimates,COALESCE(n.actuals,0) normalized_actuals,
          COALESCE(n.catalyst_revisions,0) normalized_catalyst_revisions,
          COALESCE(n.missing_currency,0) missing_currency_rows,
          COALESCE(n.usable_currency,0) currency_usable_rows,
          COALESCE(f.features,0) revision_features,COALESCE(f.usable,0) usable_revision_features
        FROM universe u LEFT JOIN ingestion i ON i.ticker=u.ticker
        LEFT JOIN normalized n ON n.ticker=u.ticker AND n.dataset=i.dataset
        LEFT JOIN features f ON f.ticker=u.ticker
        ORDER BY u.ticker,i.dataset
        """,
        run_id=RUN_ID,
        config_hash=load_ceri_config().config_hash,
    )
    return rows


def _sec_reconciliation(connection, snapshots: list[CeriScoreSnapshot]) -> list[dict[str, Any]]:
    selected_by_ticker = {
        snapshot.ticker: _selected_guidance_ids(snapshot) for snapshot in snapshots
    }
    rows = _rows(
        connection,
        """
        WITH universe AS (
          SELECT DISTINCT upper(r.ticker) ticker,c.id company_id,c.cik
          FROM raw_company_rows r LEFT JOIN ceri_companies c ON c.ticker=upper(r.ticker)
          WHERE r.run_id=:run_id
        ), ingest AS (
          SELECT upper(scope_json->>'ticker') ticker,id ingestion_run_id,status,failed_count,
                 requested_count,fetched_count,inserted_count,deduplicated_count,retry_count,
                 errors_json,started_at,completed_at
          FROM ceri_ingestion_runs
          WHERE (scope_json->>'run_id')::int=:run_id AND provider='sec'
        ), registry AS (
          SELECT u.ticker,count(DISTINCT d.id) documents,
            count(DISTINCT x.id) FILTER(WHERE x.status='COMPLETED_WITH_RECORDS')
              completed_with_records,
            count(DISTINCT x.id) FILTER(WHERE x.status='COMPLETED_NO_RECORDS') completed_no_records,
            count(DISTINCT x.id) FILTER(
              WHERE x.status NOT IN('COMPLETED_WITH_RECORDS','COMPLETED_NO_RECORDS')
            ) nonterminal,
            count(DISTINCT d.id) FILTER(
              WHERE d.last_downloaded_at BETWEEN :start_at AND :end_at
            ) downloaded_during_run,
            count(DISTINCT x.id) FILTER(
              WHERE x.completed_at BETWEEN :start_at AND :end_at
            ) extracted_during_run,
            COALESCE(sum(DISTINCT d.last_content_bytes),0) cached_bytes
          FROM universe u LEFT JOIN ceri_sec_filing_documents d
            ON ltrim(d.cik,'0')=ltrim(u.cik,'0')
          LEFT JOIN ceri_sec_document_extractions x
            ON x.document_id=d.id AND x.processor_signature=:signature
          GROUP BY u.ticker
        ), guidance AS (
          SELECT u.ticker,count(g.id) historical_guidance_rows,
                 count(g.id) FILTER(WHERE g.accepted_for_scoring IS TRUE) explicitly_accepted,
                 count(DISTINCT g.filing_accession) historical_accessions
          FROM universe u LEFT JOIN ceri_guidance_events g ON g.company_id=u.company_id
          GROUP BY u.ticker
        )
        SELECT u.ticker,u.company_id,u.cik,i.ingestion_run_id,i.status,i.failed_count,
          i.requested_count,i.fetched_count,i.inserted_count,i.deduplicated_count,i.retry_count,
          i.errors_json,i.started_at,i.completed_at,r.documents,r.completed_with_records,
          r.completed_no_records,r.nonterminal,r.downloaded_during_run,r.extracted_during_run,
          r.cached_bytes,g.historical_guidance_rows,g.explicitly_accepted,g.historical_accessions
        FROM universe u LEFT JOIN ingest i ON i.ticker=u.ticker
        LEFT JOIN registry r ON r.ticker=u.ticker LEFT JOIN guidance g ON g.ticker=u.ticker
        ORDER BY u.ticker
        """,
        run_id=RUN_ID,
        start_at=datetime.fromisoformat("2026-08-13T17:28:00+02:00"),
        end_at=datetime.fromisoformat("2026-08-13T17:29:00+02:00"),
        signature=SEC_SIGNATURE,
    )
    for row in rows:
        selected = selected_by_ticker.get(str(row["ticker"]), [])
        row["selected_guidance_ids"] = selected
        row["selected_guidance_count"] = len(selected)
    return rows


def _summary(
    connection,
    *,
    config,
    audit_rows: list[dict[str, Any]],
    eodhd_rows: list[dict[str, Any]],
    sec_rows: list[dict[str, Any]],
    ablation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    run = _rows(connection, "SELECT * FROM upload_runs WHERE id=:run_id", run_id=RUN_ID)[0]
    jobs = _rows(
        connection,
        """SELECT job_type,status,count(*) n,sum(retry_count) retries,min(started_at) first_started,
                  max(completed_at) last_completed
           FROM background_jobs WHERE related_run_id=:run_id GROUP BY 1,2 ORDER BY 1,2""",
        run_id=RUN_ID,
    )
    historical = _rows(
        connection,
        """SELECT count(*) snapshots,count(DISTINCT run_id) runs,
                  count(DISTINCT as_of_session) sessions,
                  min(as_of_session) first_session,max(as_of_session) last_session
           FROM ceri_score_snapshots""",
    )[0]
    changes = _rows(
        connection,
        """
        WITH s AS (SELECT id FROM ceri_score_snapshots WHERE run_id=:run_id)
        SELECT ce.change_type,count(*) n FROM ceri_change_events ce JOIN s ON s.id=ce.to_snapshot_id
        GROUP BY 1 ORDER BY n DESC
        """,
        run_id=RUN_ID,
    )
    alerts = _rows(
        connection,
        """
        WITH s AS (SELECT id FROM ceri_score_snapshots WHERE run_id=:run_id),
        ch AS (SELECT ce.id FROM ceri_change_events ce JOIN s ON s.id=ce.to_snapshot_id)
        SELECT count(*) n,count(DISTINCT ticker) tickers FROM ceri_alert_events a
        JOIN ch ON ch.id=a.source_change_event_id
        """,
        run_id=RUN_ID,
    )[0]
    eodhd_dataset = defaultdict(lambda: defaultdict(int))
    for row in eodhd_rows:
        dataset = str(row.get("dataset"))
        for key in (
            "provider_observations",
            "fetched_count",
            "inserted_count",
            "deduplicated_count",
            "corrected_count",
            "failed_count",
            "normalized_estimates",
            "normalized_actuals",
            "normalized_catalyst_revisions",
            "missing_currency_rows",
            "usable_revision_features",
        ):
            eodhd_dataset[dataset][key] += int(row.get(key) or 0)
    sec_status = defaultdict(int)
    for row in sec_rows:
        sec_status[str(row.get("status"))] += 1
    without_sec = [row for row in ablation_rows if row["scenario"] == "WITHOUT_SEC"]
    without_eodhd = [row for row in ablation_rows if row["scenario"] == "WITHOUT_EODHD"]
    return {
        "generated_at": datetime.now(UTC),
        "read_only": True,
        "run": run,
        "capture_cutoff": CAPTURE_CUTOFF,
        "config_version": config.engine.config_version,
        "config_hash": config.config_hash,
        "calculation_version": config.engine.calculation_version,
        "minimum_component_coverage_pct": config.revision.minimum_component_coverage_pct,
        "snapshot_count": len(audit_rows),
        "ticker_count": len({row["ticker"] for row in audit_rows}),
        "unrated_count": sum(row["rating"] == "Unrated" for row in audit_rows),
        "zero_opportunity_coverage_count": sum(
            float(row["component_coverage"] or 0.0) == 0.0 for row in audit_rows
        ),
        "below_threshold_count": sum(
            float(row["component_coverage"] or 0.0) < config.revision.minimum_component_coverage_pct
            for row in audit_rows
        ),
        "reconstruction_matches": sum(bool(row["reconstruction_match"]) for row in audit_rows),
        "hash_matches": sum(
            row["stored_evidence_hash"] == row["reproduced_evidence_hash"] for row in audit_rows
        ),
        "selected_guidance_tickers": sum(bool(row["selected_guidance_ids"]) for row in audit_rows),
        "null_score_upgrade_tickers": sum(
            "NULL_SCORE_OPPORTUNITY_UPGRADE" in row["anomaly_flags"] for row in audit_rows
        ),
        "unrated_alert_tickers": sum(
            "UNRATED_ACTIONABLE_ALERT" in row["anomaly_flags"] for row in audit_rows
        ),
        "jobs": jobs,
        "changes": changes,
        "alerts": alerts,
        "eodhd": {key: dict(value) for key, value in eodhd_dataset.items()},
        "sec_status": dict(sec_status),
        "sec_downloads_during_run": sum(
            int(row.get("downloaded_during_run") or 0) for row in sec_rows
        ),
        "sec_extractions_during_run": sum(
            int(row.get("extracted_during_run") or 0) for row in sec_rows
        ),
        "without_eodhd_component_changes": sum(
            row["component_changed_vs_full"] for row in without_eodhd
        ),
        "without_eodhd_material_decision_changes": sum(
            row["material_decision_changed"] for row in without_eodhd
        ),
        "without_sec_component_changes": sum(
            row["component_changed_vs_full"] for row in without_sec
        ),
        "without_sec_material_decision_changes": sum(
            row["material_decision_changed"] for row in without_sec
        ),
        "historical_ceri": historical,
    }


if __name__ == "__main__":
    main()
