"""R2 production-shape proofs for bounded reads and control-plane commits."""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriAlertRule,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriCompany,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.ib_market_intelligence_tables import (
    IBScannerCandidate,
    IBScannerRun,
    IBTradeEpisode,
    IBTradeResearchLink,
)
from app.models.tables import BackgroundJob, UploadRun
from app.services.background_job_service import (
    JobStatus,
    heartbeat_job,
    is_cancel_requested,
    record_job_progress,
    request_job_cancel,
)
from app.services.ceri.alert_service import _cooldown_alerts
from app.services.ceri.job_handlers import _eligible_changes
from app.services.ceri.normalization_service import _next_catalyst_revision_number
from app.services.ceri.purge_service import (
    CeriPurgeService,
    _apply_purge_lifecycle,
    _manifest_hash,
    _manifest_hash_input,
)
from app.services.domain_write_fence import (
    assert_current_execution_ownership,
    deferred_execution_ownership_lock,
)
from app.services.ib_market_intelligence.journal import _episodes_for_fill_statement
from app.services.ib_market_intelligence.query_service import scanner_runs, trade_journal

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_r2_bounded_query_and_transaction_shapes(contextual_engine, record_property):
    now = datetime(2026, 9, 26, 18, tzinfo=UTC)
    seeded = _seed_r2_population(contextual_engine, now=now)

    alert_metrics = _prove_alert_scope(contextual_engine, seeded["alert_change_ids"])
    purge_metrics = _prove_purge_scope(contextual_engine, seeded)
    catalyst_metrics = _prove_catalyst_scope(contextual_engine, seeded["catalyst_event_id"])
    ib_metrics = _prove_ib_scope(contextual_engine)
    progress_metrics = _prove_progress_separation(contextual_engine, now=now)

    record_property("alert_query_shape", alert_metrics)
    record_property("purge_query_shape", purge_metrics)
    record_property("catalyst_query_shape", catalyst_metrics)
    record_property("ib_query_shape", ib_metrics)
    record_property("progress_separation", progress_metrics)


def _seed_r2_population(engine, *, now: datetime) -> dict[str, object]:
    as_of = now.date()
    with Session(engine) as db:
        run = UploadRun(filename="r2-bounded.csv", status="COMPLETED", row_count=0)
        db.add(run)
        db.flush()
        companies = [CeriCompany(ticker=f"R2C{index:04d}", exchange="US") for index in range(255)]
        db.add_all(companies)
        db.flush()
        snapshots: list[CeriScoreSnapshot] = []
        changes: list[CeriChangeEvent] = []
        for index, company in enumerate(companies):
            snapshot = _snapshot(
                company,
                ticker=company.ticker,
                as_of=as_of,
                cutoff=now,
                run_id=run.id,
                evidence_hash=f"alert-snapshot-{index}",
            )
            snapshots.append(snapshot)
            db.add(snapshot)
        db.flush()
        for index in range(1020):
            company = companies[index % len(companies)]
            snapshot = snapshots[index % len(snapshots)]
            changes.append(
                CeriChangeEvent(
                    company_id=company.id,
                    to_snapshot_id=snapshot.id,
                    change_type="SCORE_CHANGED",
                    severity="INFO",
                    importance="MATERIAL",
                    signal_class="INFORMATIONAL",
                    comparison_state="COMPARABLE",
                    delta_json={"index": index},
                    dedup_key=f"r2-alert-change-{index}",
                    created_at=now + timedelta(microseconds=index),
                )
            )
        db.add_all(changes)
        db.flush()
        alert_change_ids = [int(row.id) for row in changes[:5]]

        large_payload = "x" * 16_384
        target_source = _source(
            "purge-target", now, provider="eodhd", license_scope="personal", purge_eligible=True,
            payload=large_payload,
        )
        unrelated_sources = [
            _source(
                f"purge-unrelated-{index}",
                now,
                provider="other",
                license_scope="commercial",
                purge_eligible=False,
                payload=large_payload,
            )
            for index in range(250)
        ]
        db.add_all([target_source, *unrelated_sources])
        db.flush()
        purge_target_snapshot = _snapshot(
            companies[0],
            ticker=companies[0].ticker,
            as_of=as_of,
            cutoff=now,
            run_id=None,
            evidence_hash="purge-target-snapshot",
            source_id=target_source.id,
        )
        purge_unrelated_snapshot = _snapshot(
            companies[1],
            ticker=companies[1].ticker,
            as_of=as_of,
            cutoff=now,
            run_id=None,
            evidence_hash="purge-unrelated-snapshot",
            source_id=unrelated_sources[0].id,
        )
        db.add_all((purge_target_snapshot, purge_unrelated_snapshot))
        db.flush()
        purge_changes = [
            CeriChangeEvent(
                company_id=companies[index].id,
                to_snapshot_id=snapshot.id,
                change_type="SCORE_CHANGED",
                severity="WARNING",
                importance="MATERIAL",
                signal_class="ACTIONABLE",
                comparison_state="COMPARABLE",
                delta_json={},
                dedup_key=f"r2-purge-change-{index}",
                created_at=now,
            )
            for index, snapshot in enumerate((purge_target_snapshot, purge_unrelated_snapshot))
        ]
        db.add_all(purge_changes)
        rule = CeriAlertRule(
            rule_id="r2-purge-rule",
            enabled=True,
            severity="WARNING",
            thresholds_json={},
            scope_json={},
            cooldown_sessions=5,
            config_version="r2",
            source_event_types_json=["SCORE_CHANGED"],
        )
        db.add(rule)
        db.flush()
        alerts = [
            CeriAlertEvent(
                alert_rule_id=rule.id,
                source_change_event_id=change.id,
                event_key=f"r2-purge-alert-{index}",
                ticker=companies[index].ticker,
                severity="WARNING",
                status="UNREAD",
                evidence_json={
                    "alert_rule": "r2-purge-rule",
                    "native_alert_proof": {"operation_time": {"session": as_of.isoformat()}},
                },
                importance="MATERIAL",
                signal_class="ACTIONABLE",
                validity_classification="VALID",
                created_at=now,
            )
            for index, change in enumerate(purge_changes)
        ]
        db.add_all(alerts)

        catalyst_source = _source("catalyst-target", now)
        unrelated_catalyst_sources = [
            _source(f"catalyst-unrelated-{index}", now) for index in range(250)
        ]
        db.add_all([catalyst_source, *unrelated_catalyst_sources])
        db.flush()
        target_event = CeriCatalystEvent(
            company_id=companies[0].id,
            category="PRODUCT",
            subject_key="r2-target",
        )
        unrelated_events = [
            CeriCatalystEvent(
                company_id=companies[(index % 254) + 1].id,
                category="PRODUCT",
                subject_key=f"r2-unrelated-{index}",
            )
            for index in range(250)
        ]
        db.add_all([target_event, *unrelated_events])
        db.flush()
        db.add_all(
            [
                _revision(target_event.id, catalyst_source.id, number)
                for number in range(1, 4)
            ]
            + [
                _revision(event.id, source.id, 1)
                for event, source in zip(unrelated_events, unrelated_catalyst_sources, strict=True)
            ]
        )

        scanner = IBScannerRun(
            scanner_name="r2",
            scanner_version="1",
            instrument="STK",
            location="STK.US.MAJOR",
            scan_code="TOP_PERC_GAIN",
            max_results=600,
            filters_json=[],
            config_hash="r2",
            status="COMPLETED",
            started_at=now,
            completed_at=now,
        )
        db.add(scanner)
        db.flush()
        db.add_all(
            [
                IBScannerCandidate(
                    scanner_run_id=scanner.id,
                    rank=index + 1,
                    ticker=f"IB{index:04d}",
                    ib_conid=index + 1,
                    contract_metadata_json={},
                    scanner_metadata_json={},
                    universe_source="IBKR_SCANNER",
                    enrichment_status="PENDING",
                )
                for index in range(600)
            ]
        )
        episodes = [
            IBTradeEpisode(
                episode_key=f"r2-episode-{index}",
                ticker=f"IB{index:04d}",
                direction="LONG",
                opened_at=now - timedelta(minutes=index),
                entry_quantity=Decimal("1"),
                exit_quantity=Decimal("0"),
                average_entry_price=Decimal("10"),
                average_exit_price=None,
                deployed_entry_capital=Decimal("10"),
                gross_pnl=None,
                broker_realized_pnl=None,
                commissions=Decimal("0"),
                fees=Decimal("0"),
                net_pnl=None,
                return_pct=None,
                holding_seconds=None,
                status="OPEN",
                matching_policy="FIFO_POSITION_V1",
                fill_ids_json=[index + 1],
                is_excluded=False,
            )
            for index in range(600)
        ]
        db.add_all(episodes)
        db.flush()
        db.add_all(
            [
                IBTradeResearchLink(
                    trade_episode_id=episode.id,
                    matching_status="MATCHED",
                    matching_policy="latest-completed-before-entry-v1",
                    decision_timestamp=now - timedelta(days=1),
                    context_json={"setup_family": "MOMENTUM"},
                    leakage_check="PASS",
                    ambiguity_json=[],
                )
                for episode in episodes
            ]
        )
        db.commit()
        return {
            "alert_change_ids": alert_change_ids,
            "catalyst_event_id": int(target_event.id),
            "purge_source_id": int(target_source.id),
            "unrelated_source_id": int(unrelated_sources[0].id),
            "purge_snapshot_id": int(purge_target_snapshot.id),
            "unrelated_snapshot_id": int(purge_unrelated_snapshot.id),
            "purge_change_id": int(purge_changes[0].id),
            "unrelated_change_id": int(purge_changes[1].id),
            "purge_alert_id": int(alerts[0].id),
            "unrelated_alert_id": int(alerts[1].id),
        }


def _prove_alert_scope(engine, change_ids: list[int]) -> dict[str, int]:
    statements: list[tuple[str, object]] = []
    loaded: Counter[str] = Counter()
    with Session(engine) as db:
        event.listen(
            db,
            "loaded_as_persistent",
            lambda _db, row: loaded.update([row.__tablename__]),
        )
        listener = _capture_selects(
            statements,
            ("ceri_change_events", "ceri_companies", "ceri_alert_events"),
        )
        event.listen(engine, "before_cursor_execute", listener)
        try:
            rows = _eligible_changes(db, {"change_ids": change_ids}, limit=250)
            company_ids = sorted({row.company_id for row in rows})
            companies = list(
                db.scalars(select(CeriCompany).where(CeriCompany.id.in_(company_ids)))
            )
            rule = db.scalar(select(CeriAlertRule).where(CeriAlertRule.rule_id == "r2-purge-rule"))
            cooldown_rows = _cooldown_alerts(db, rule=rule, ticker="R2C0000")
        finally:
            event.remove(engine, "before_cursor_execute", listener)
    assert [row.id for row in rows] == change_ids
    assert {row.id for row in companies} == set(company_ids)
    assert len(cooldown_rows) == 1
    assert len(statements) == 3
    assert all(re.search(r"\bWHERE\b", sql, re.I) for sql, _params in statements)
    assert loaded["ceri_change_events"] == 5
    assert loaded["ceri_companies"] == 5
    assert loaded["ceri_alert_events"] == 1
    return _metrics(statements, seeded=1279, scoped=11, materialized=11)


def _prove_purge_scope(engine, seeded: dict[str, object]) -> dict[str, int]:
    statements: list[tuple[str, object]] = []
    loaded: Counter[str] = Counter()
    with Session(engine) as db:
        event.listen(
            db,
            "loaded_as_persistent",
            lambda _db, row: loaded.update([row.__tablename__]),
        )
        listener = _capture_selects(statements, ("ceri_", "core_calculation_evidence"))
        event.listen(engine, "before_cursor_execute", listener)
        try:
            manifest = CeriPurgeService()._lifecycle_manifest(db, "eodhd", "personal")
            preview_hash = _manifest_hash(_manifest_hash_input(manifest, "eodhd", "personal"))
            assert manifest["affected_counts"]["source_records"] == 1
            assert manifest["invalidated_derivatives"]["score_snapshots"] == 1
            assert manifest["invalidated_derivatives"]["change_events"] == 1
            assert manifest["invalidated_derivatives"]["alert_events"] == 1
            _apply_purge_lifecycle.__wrapped__(
                db,
                manifest,
                preview_manifest_hash=preview_hash,
                audit_id=None,
            )
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", listener)
    assert not [sql for sql, _params in statements if not re.search(r"\bWHERE\b", sql, re.I)]
    assert loaded["ceri_source_records"] == 1
    with Session(engine) as verify:
        target_source = verify.get(CeriSourceRecord, seeded["purge_source_id"])
        other_source = verify.get(CeriSourceRecord, seeded["unrelated_source_id"])
        target_snapshot = verify.get(CeriScoreSnapshot, seeded["purge_snapshot_id"])
        other_snapshot = verify.get(CeriScoreSnapshot, seeded["unrelated_snapshot_id"])
        target_change = verify.get(CeriChangeEvent, seeded["purge_change_id"])
        other_change = verify.get(CeriChangeEvent, seeded["unrelated_change_id"])
        target_alert = verify.get(CeriAlertEvent, seeded["purge_alert_id"])
        other_alert = verify.get(CeriAlertEvent, seeded["unrelated_alert_id"])
        assert target_source.raw_json is None and target_source.export_policy == "purged"
        assert other_source.raw_json is not None and other_source.export_policy != "purged"
        assert target_snapshot.data_confidence == "Invalidated"
        assert other_snapshot.data_confidence == "Normal"
        assert target_change.severity == "INVALIDATED"
        assert other_change.severity == "WARNING"
        assert target_alert.status == "INVALIDATED"
        assert other_alert.status == "UNREAD"
    result = _metrics(statements, seeded=1532, scoped=4, materialized=sum(loaded.values()))
    result["large_payload_rows_materialized"] = loaded["ceri_source_records"]
    return result


def _prove_catalyst_scope(engine, event_id: int) -> dict[str, int]:
    statements: list[tuple[str, object]] = []
    with Session(engine) as db:
        listener = _capture_selects(statements, ("ceri_catalyst_event_revisions",))
        event.listen(engine, "before_cursor_execute", listener)
        try:
            next_number = _next_catalyst_revision_number(db, event_id)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
    assert next_number == 4
    assert len(statements) == 1
    assert re.search(r"\bWHERE\b", statements[0][0], re.I)
    return _metrics(statements, seeded=253, scoped=3, materialized=1)


def _prove_ib_scope(engine) -> dict[str, int]:
    statements: list[tuple[str, object]] = []
    loaded: Counter[str] = Counter()
    with Session(engine) as db:
        event.listen(
            db,
            "loaded_as_persistent",
            lambda _db, row: loaded.update([row.__tablename__]),
        )
        listener = _capture_selects(
            statements,
            ("ib_trade_episodes", "ib_trade_research_links", "ib_scanner_candidates"),
        )
        event.listen(engine, "before_cursor_execute", listener)
        try:
            journal = trade_journal(db, page_size=100)
            scanner = scanner_runs(db, limit=1, candidate_limit=500)
            locked = list(db.scalars(_episodes_for_fill_statement(1)))
            locked_fill_ids = [row.fill_ids_json for row in locked]
            db.rollback()
        finally:
            event.remove(engine, "before_cursor_execute", listener)
    assert len(journal["episodes"]) == 100
    assert journal["pagination"] == {"page_size": 100, "has_more": True, "next_before_id": 100}
    assert len(scanner["candidate_pool"]) == 500
    assert locked_fill_ids == [[1]]
    assert loaded["ib_trade_episodes"] <= 102
    assert loaded["ib_trade_research_links"] == 100
    assert loaded["ib_scanner_candidates"] == 500
    lock_sql = [sql for sql, _params in statements if "FOR UPDATE" in sql.upper()]
    assert len(lock_sql) == 1 and re.search(r"\bWHERE\b", lock_sql[0], re.I)
    with Session(engine) as page_db:
        second_page = trade_journal(
            page_db,
            page_size=100,
            before_id=journal["pagination"]["next_before_id"],
        )
    first_ids = {row["id"] for row in journal["episodes"]}
    second_ids = {row["id"] for row in second_page["episodes"]}
    assert len(second_ids) == 100 and first_ids.isdisjoint(second_ids)
    assert second_page["episodes"][0]["id"] == 101
    return _metrics(
        statements,
        seeded=1801,
        scoped=703,
        materialized=sum(loaded.values()),
    )


def _prove_progress_separation(engine, *, now: datetime) -> dict[str, object]:
    token = "r2-control-token"
    with Session(engine) as seed:
        job = BackgroundJob(
            job_type="CERI_CHANGE_DETECTION",
            status=JobStatus.RUNNING,
            payload_json={},
            execution_token=token,
            worker_id="r2-worker",
            worker_instance_id="r2-worker-instance",
            lease_owner="r2-worker",
            heartbeat_at=now,
            lease_expires_at=now + timedelta(minutes=5),
            started_at=now,
            progress_sequence=0,
            progress_processed=0,
            requested_cancel=False,
        )
        seed.add(job)
        seed.commit()
        job_id = int(job.id)

    domain = Session(engine)
    try:
        with deferred_execution_ownership_lock():
            assert_current_execution_ownership(
                domain,
                job_id=job_id,
                execution_token=token,
            )
            domain_row = CeriCompany(ticker="R2ROLLBACK", exchange="US")
            domain.add(domain_row)
            domain.flush()
            with Session(engine) as control:
                detached = BackgroundJob(
                    id=job_id,
                    job_type="CERI_CHANGE_DETECTION",
                    status=JobStatus.RUNNING,
                    execution_token=token,
                )
                heartbeat_job(control, detached, execution_token=token)
                record_job_progress(
                    control,
                    job_id=job_id,
                    execution_token=token,
                    stage="change_detection",
                    processed=25,
                    total=100,
                )
                control.commit()
            with Session(engine) as observer:
                visible = observer.get(BackgroundJob, job_id)
                assert visible.progress_processed == 25
                assert visible.heartbeat_at > now
                assert observer.scalar(
                    select(CeriCompany.id).where(CeriCompany.ticker == "R2ROLLBACK")
                ) is None
            with Session(engine) as canceller:
                request_job_cancel(canceller, job_id)
                canceller.commit()
            with Session(engine) as control:
                assert is_cancel_requested(control, job_id) is True
            domain.rollback()
    finally:
        domain.close()
    with Session(engine) as observer:
        visible = observer.get(BackgroundJob, job_id)
        assert visible.progress_processed == 25
        assert visible.requested_cancel is True
        assert observer.scalar(
            select(CeriCompany.id).where(CeriCompany.ticker == "R2ROLLBACK")
        ) is None
    return {
        "domain_visible_before_rollback": False,
        "progress_visible_before_rollback": True,
        "progress_survived_domain_rollback": True,
        "cancellation_visible_between_chunks": True,
    }


def _snapshot(
    company: CeriCompany,
    *,
    ticker: str,
    as_of: date,
    cutoff: datetime,
    run_id: int | None,
    evidence_hash: str,
    source_id: int | None = None,
) -> CeriScoreSnapshot:
    return CeriScoreSnapshot(
        run_id=run_id,
        company_id=company.id,
        ticker=ticker,
        as_of_session=as_of,
        cutoff_at=cutoff,
        opportunity_score=5,
        data_confidence="Normal",
        coverage_pct=100,
        posture="Mixed",
        component_json={"source_ids": [source_id]} if source_id else {"source_ids": []},
        evidence_lineage_json={},
        config_version="r2",
        config_hash="r2",
        calculation_version="r2",
        evidence_contract_version="r2",
        comparison_state="COMPARABLE",
        evidence_hash=evidence_hash,
    )


def _source(
    key: str,
    known_at: datetime,
    *,
    provider: str = "test",
    license_scope: str | None = None,
    purge_eligible: bool = False,
    payload: str = "small",
) -> CeriSourceRecord:
    return CeriSourceRecord(
        provider=provider,
        dataset="r2",
        provider_record_id=key,
        retrieved_at=known_at,
        ingested_at=known_at,
        raw_json={"payload": payload},
        content_hash=f"hash-{key}",
        idempotency_key=f"r2-{key}",
        license_scope=license_scope,
        purge_eligible=purge_eligible,
    )


def _revision(event_id: int, source_id: int, number: int) -> CeriCatalystEventRevision:
    return CeriCatalystEventRevision(
        catalyst_event_id=event_id,
        source_record_id=source_id,
        revision_number=number,
        is_current=number == 3,
        status="ANNOUNCED",
        direction="POSITIVE",
    )


def _capture_selects(target: list[tuple[str, object]], tables: tuple[str, ...]):
    def capture(_connection, _cursor, sql, parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT") and any(table in sql for table in tables):
            target.append((sql, parameters))

    return capture


def _metrics(
    statements: list[tuple[str, object]],
    *,
    seeded: int,
    scoped: int,
    materialized: int,
) -> dict[str, int]:
    return {
        "seeded_rows": seeded,
        "scoped_rows": scoped,
        "materialized_rows": materialized,
        "query_count": len(statements),
        "max_parameters": max((_parameter_count(params) for _sql, params in statements), default=0),
        "predicate_free_reads": sum(
            not re.search(r"\bWHERE\b", sql, re.I) for sql, _params in statements
        ),
    }


def _parameter_count(parameters: object) -> int:
    if isinstance(parameters, dict):
        return len(parameters)
    if isinstance(parameters, (tuple, list)):
        return len(parameters)
    return 0
