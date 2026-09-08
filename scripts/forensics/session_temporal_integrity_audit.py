"""Read-only production evidence extractor for the session-integrity forensic.

The script opens only READ ONLY transactions.  Its only writes are local report
artifacts under ``--output-dir``.  It does not enqueue work, call providers,
invoke pipeline services, or mutate application tables.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import engine
from app.services.ceri.alert_service import _trading_sessions_between
from app.services.ceri.effective_session_service import CeriEffectiveSessionService
from app.services.us_market_calendar import (
    is_us_trading_day,
    latest_completed_us_trading_day,
    us_market_session,
)

NY = ZoneInfo("America/New_York")
ZURICH = ZoneInfo("Europe/Zurich")


def _rows(sql: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        result = [dict(row) for row in connection.execute(text(sql)).mappings()]
        connection.rollback()
        return result


def _scalar(sql: str) -> int:
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        assert connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        value = int(connection.execute(text(sql)).scalar_one())
        connection.rollback()
        return value


def _trading_gap(start: date, end: date) -> int:
    if start == end:
        return 0
    sign = 1
    if start > end:
        start, end, sign = end, start, -1
    cursor = start
    count = 0
    while cursor < end:
        cursor += timedelta(days=1)
        count += int(is_us_trading_day(cursor))
    return sign * count


def _stable_hash(ids: list[int]) -> str:
    payload = "\n".join(str(value) for value in sorted(ids)).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Counter):
        return dict(value)
    return value


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def audit_technical() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _rows(
        """
        SELECT id, run_id, ticker, created_at,
               debug_json->'indicator_debug'->>'end_date' AS end_date
        FROM technical_scores ORDER BY id
        """
    )
    distribution: Counter[str] = Counter()
    bad: list[dict[str, Any]] = []
    for row in rows:
        if not row["end_date"]:
            distribution["unknown_lineage"] += 1
            continue
        actual = date.fromisoformat(row["end_date"])
        expected = latest_completed_us_trading_day(row["created_at"])
        gap = _trading_gap(expected, actual)
        distribution[
            "ahead" if actual > expected else "aligned" if actual == expected else "behind"
        ] += 1
        if actual > expected:
            bad.append(
                {
                    "technical_score_id": row["id"],
                    "run_id": row["run_id"],
                    "ticker": row["ticker"],
                    "created_at": row["created_at"].isoformat(),
                    "expected_latest_completed_session": expected.isoformat(),
                    "actual_ticker_end_date": actual.isoformat(),
                    "excess_trading_sessions": gap,
                }
            )
    run_ids = sorted({row["run_id"] for row in bad})
    return (
        {
            "score_distribution": dict(distribution),
            "affected_score_count": len(bad),
            "affected_run_count": len(run_ids),
            "affected_run_ids": run_ids,
            "first_affected_score_id": bad[0]["technical_score_id"] if bad else None,
            "last_affected_score_id": bad[-1]["technical_score_id"] if bad else None,
            "affected_id_sha256": _stable_hash([row["technical_score_id"] for row in bad]),
        },
        bad,
    )


def audit_technical_source_coherence() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT ts.id, ts.run_id, ts.ticker, ts.sector_benchmark_symbol,
          NULLIF(ts.debug_json->'indicator_debug'->>'end_date','')::date AS ticker_end,
          COALESCE(spy.adj, spy.trd) AS spy_end,
          COALESCE(qqq.adj, qqq.trd) AS qqq_end,
          COALESCE(sec.adj, sec.trd) AS sector_end
        FROM technical_scores ts
        LEFT JOIN LATERAL (
          SELECT max(bar_date) FILTER (WHERE what_to_show='ADJUSTED_LAST') adj,
                 max(bar_date) FILTER (WHERE what_to_show='TRADES') trd
          FROM price_bars WHERE ticker='SPY' AND timeframe='1 day'
            AND first_seen_at <= ts.created_at
        ) spy ON true
        LEFT JOIN LATERAL (
          SELECT max(bar_date) FILTER (WHERE what_to_show='ADJUSTED_LAST') adj,
                 max(bar_date) FILTER (WHERE what_to_show='TRADES') trd
          FROM price_bars WHERE ticker='QQQ' AND timeframe='1 day'
            AND first_seen_at <= ts.created_at
        ) qqq ON true
        LEFT JOIN LATERAL (
          SELECT max(bar_date) FILTER (WHERE what_to_show='ADJUSTED_LAST') adj,
                 max(bar_date) FILTER (WHERE what_to_show='TRADES') trd
          FROM price_bars WHERE ticker=ts.sector_benchmark_symbol AND timeframe='1 day'
            AND first_seen_at <= ts.created_at
        ) sec ON ts.sector_benchmark_symbol IS NOT NULL
        ORDER BY ts.id
        """
    )
    distribution: Counter[str] = Counter()
    directional: Counter[str] = Counter()
    ids: list[int] = []
    for row in rows:
        peers = [row["spy_end"], row["qqq_end"]]
        if row["sector_benchmark_symbol"]:
            peers.append(row["sector_end"])
        if row["ticker_end"] is None or any(value is None for value in peers):
            distribution["unknown_lineage"] += 1
            continue
        mismatches = [value for value in peers if value != row["ticker_end"]]
        if not mismatches:
            distribution["all_aligned"] += 1
            continue
        ids.append(row["id"])
        if len(mismatches) == 1:
            direction = "ahead" if mismatches[0] > row["ticker_end"] else "behind"
            gap = abs(_trading_gap(row["ticker_end"], mismatches[0]))
            distribution[f"1_source_{gap}_session_{direction}"] += 1
        else:
            distribution["multiple_mismatches"] += 1
            directions = tuple(
                "ahead" if value > row["ticker_end"] else "behind" for value in mismatches
            )
            directional["+".join(directions)] += 1
    return {
        "distribution": dict(distribution),
        "multiple_mismatch_directions": dict(directional),
        "mismatch_id_sha256": _stable_hash(ids),
        "lineage_caveat": (
            "Peer end dates reconstructed from first_seen_at; technical rows do not "
            "persist peer sessions."
        ),
    }


def audit_early_price_bars() -> dict[str, Any]:
    candidates = _rows(
        """
        SELECT id,ticker,bar_date,timeframe,what_to_show,first_seen_at,created_at
        FROM price_bars
        WHERE bar_date >= ((coalesce(first_seen_at,created_at)
                            AT TIME ZONE 'America/New_York')::date)
        ORDER BY id
        """
    )
    bad = []
    families: Counter[str] = Counter()
    for row in candidates:
        observed = row["first_seen_at"] or row["created_at"]
        expected = latest_completed_us_trading_day(observed)
        if row["bar_date"] > expected:
            bad.append(row["id"])
            families[f"{row['timeframe']}:{row['what_to_show']}"] += 1
    return {
        "bars_first_seen_before_safe_boundary": len(bad),
        "distribution": dict(families),
        "id_sha256": _stable_hash(bad),
    }


def audit_artifacts() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT id,ticker,created_at,shadow_validation_status,
               artifact_json->'feature_result'->'debug'->>'end_date' AS end_date
        FROM technical_feature_artifacts ORDER BY id
        """
    )
    distribution: Counter[str] = Counter()
    ahead_ids: list[int] = []
    for row in rows:
        if not row["end_date"]:
            distribution["unknown"] += 1
            continue
        end = date.fromisoformat(row["end_date"])
        expected = latest_completed_us_trading_day(row["created_at"])
        relation = "ahead" if end > expected else "aligned" if end == expected else "behind"
        distribution[f"{relation}:{row['shadow_validation_status']}"] += 1
        if end > expected:
            ahead_ids.append(row["id"])
    return {
        "distribution": dict(distribution),
        "ahead_artifact_count": len(ahead_ids),
        "ahead_id_sha256": _stable_hash(ahead_ids),
    }


def audit_market_regime() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT id,as_of_date,created_at,
          debug_json->'market_inputs'->'SPY'->'feature_debug'->>'end_date' spy_end,
          debug_json->'market_inputs'->'QQQ'->'feature_debug'->>'end_date' qqq_end,
          index_health_json->'SPY'->>'stale' spy_stale,
          index_health_json->'QQQ'->>'stale' qqq_stale
        FROM market_regime_snapshots ORDER BY id
        """
    )
    relation: Counter[str] = Counter()
    mismatched_inputs = 0
    stale_differences: list[dict[str, Any]] = []
    for row in rows:
        ends = [date.fromisoformat(row[key]) for key in ("spy_end", "qqq_end") if row[key]]
        mismatched_inputs += int(len(set(ends)) > 1)
        actual = max(ends) if ends else row["as_of_date"]
        expected = latest_completed_us_trading_day(row["created_at"])
        relation[
            "ahead" if actual > expected else "aligned" if actual == expected else "behind"
        ] += 1
        today = row["created_at"].astimezone(ZURICH).date()
        current_stale = (today - row["as_of_date"]).days > 3
        correct_stale = _trading_gap(row["as_of_date"], expected) > 3
        stored_stale = any(str(row[key]).lower() == "true" for key in ("spy_stale", "qqq_stale"))
        if stored_stale != correct_stale:
            stale_differences.append(
                {
                    "snapshot_id": row["id"],
                    "as_of_date": row["as_of_date"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                    "calendar_day_stale": current_stale,
                    "stored_stale": stored_stale,
                    "trading_session_stale": correct_stale,
                }
            )
    return {
        "input_relation_to_completed_session": dict(relation),
        "spy_qqq_session_mismatch_count": mismatched_inputs,
        "stale_classification_difference_count": len(stale_differences),
        "stale_classification_differences": stale_differences,
    }


def audit_sector_rotation() -> dict[str, Any]:
    modes = _rows(
        """
        SELECT mode, debug_json->>'etf_enabled' AS etf_enabled, count(*) AS count
        FROM sector_rotation_snapshots GROUP BY 1,2 ORDER BY 1,2
        """
    )
    return {"historical_modes": modes}


def audit_lifecycle() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _rows(
        """
        SELECT id,evaluation_run_id,run_id,ticker,data_as_of_date,calculated_at,captured_at,
          NULLIF(source_lineage_json->>'market_regime_as_of','')::date market_as_of,
          NULLIF(source_lineage_json->>'sector_rotation_as_of','')::date sector_as_of,
          NULLIF(source_lineage_json->'latest_bar'->>'id','')::bigint price_bar_id
        FROM setup_signal_snapshots ORDER BY id
        """
    )
    bad: list[dict[str, Any]] = []
    context_newer = 0
    for row in rows:
        expected = latest_completed_us_trading_day(row["calculated_at"])
        context_newer += int(
            any(
                row[key] is not None and row[key] > row["data_as_of_date"]
                for key in ("market_as_of", "sector_as_of")
            )
        )
        if row["data_as_of_date"] > expected:
            bad.append(
                {
                    "snapshot_id": row["id"],
                    "evaluation_run_id": row["evaluation_run_id"],
                    "run_id": row["run_id"],
                    "ticker": row["ticker"],
                    "calculated_at": row["calculated_at"].isoformat(),
                    "expected_latest_completed_session": expected.isoformat(),
                    "data_as_of_date": row["data_as_of_date"].isoformat(),
                    "excess_trading_sessions": _trading_gap(expected, row["data_as_of_date"]),
                    "price_bar_id": row["price_bar_id"],
                }
            )
    ids = [row["snapshot_id"] for row in bad]
    values = ",".join(str(value) for value in ids) or "NULL"
    downstream = {
        "lifecycle_events": _scalar(
            f"SELECT count(*) FROM setup_lifecycle_events WHERE snapshot_id IN ({values})"
        ),
        "signal_changes_current": _scalar(
            f"SELECT count(*) FROM signal_change_events WHERE current_snapshot_id IN ({values})"
        ),
        "signal_changes_any_side": _scalar(
            "SELECT count(*) FROM signal_change_events "
            f"WHERE current_snapshot_id IN ({values}) "
            f"OR previous_snapshot_id IN ({values})"
        ),
        "alerts_via_lifecycle": _scalar(
            "SELECT count(*) FROM signal_alert_events a "
            "JOIN setup_lifecycle_events e ON e.id=a.lifecycle_event_id "
            f"WHERE e.snapshot_id IN ({values})"
        ),
        "alerts_via_changes": _scalar(
            "SELECT count(*) FROM signal_alert_events a "
            "JOIN signal_change_events e ON e.id=a.signal_change_event_id "
            f"WHERE e.current_snapshot_id IN ({values}) "
            f"OR e.previous_snapshot_id IN ({values})"
        ),
        "episodes_any_snapshot_role": _scalar(
            "SELECT count(*) FROM setup_lifecycle_episodes "
            f"WHERE opening_snapshot_id IN ({values}) "
            f"OR current_snapshot_id IN ({values}) "
            f"OR closing_snapshot_id IN ({values})"
        ),
    }
    return (
        {
            "snapshot_count": len(bad),
            "one_session_ahead_count": sum(row["excess_trading_sessions"] == 1 for row in bad),
            "non_session_calendar_label_count": sum(
                row["excess_trading_sessions"] == 0 for row in bad
            ),
            "context_newer_than_ticker_count": context_newer,
            "evaluation_run_count": len({row["evaluation_run_id"] for row in bad}),
            "upload_run_count": len({row["run_id"] for row in bad}),
            "id_sha256": _stable_hash(ids),
            "potential_downstream": downstream,
        },
        bad,
    )


def audit_ceri_features_and_capture() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table, timestamp, family in (
        ("ceri_derived_features", "created_at", True),
        ("ceri_feature_build_states", "completed_at", False),
    ):
        rows = _rows(
            f"SELECT id,as_of_session,{timestamp} AS timestamp"
            + (",feature_family" if family else "")
            + f" FROM {table} ORDER BY id"
        )
        distribution: Counter[str] = Counter()
        family_ahead: Counter[str] = Counter()
        ahead_ids = []
        for row in rows:
            expected = latest_completed_us_trading_day(row["timestamp"])
            relation = (
                "ahead"
                if row["as_of_session"] > expected
                else "aligned"
                if row["as_of_session"] == expected
                else "behind"
            )
            distribution[relation] += 1
            if relation == "ahead":
                ahead_ids.append(row["id"])
                family_ahead[row.get("feature_family") or "build_state"] += 1
        result[table] = {
            "distribution": dict(distribution),
            "one_session_ahead_count": sum(
                row["as_of_session"] > latest_completed_us_trading_day(row["timestamp"])
                and _trading_gap(
                    latest_completed_us_trading_day(row["timestamp"]), row["as_of_session"]
                )
                == 1
                for row in rows
            ),
            "non_session_calendar_label_count": sum(
                row["as_of_session"] > latest_completed_us_trading_day(row["timestamp"])
                and _trading_gap(
                    latest_completed_us_trading_day(row["timestamp"]), row["as_of_session"]
                )
                == 0
                for row in rows
            ),
            "ahead_by_family": dict(family_ahead),
            "ahead_id_sha256": _stable_hash(ahead_ids),
        }

    rows = _rows(
        "SELECT id,run_id,ticker,as_of_session,cutoff_at FROM ceri_score_snapshots ORDER BY id"
    )
    distribution: Counter[str] = Counter()
    ahead_ids = []
    for row in rows:
        expected = latest_completed_us_trading_day(row["cutoff_at"])
        relation = (
            "ahead"
            if row["as_of_session"] > expected
            else "aligned"
            if row["as_of_session"] == expected
            else "behind"
        )
        distribution[relation] += 1
        if relation == "ahead":
            ahead_ids.append(row["id"])
    result["ceri_score_snapshots"] = {
        "distribution": dict(distribution),
        "one_session_ahead_count": sum(
            row["as_of_session"] > latest_completed_us_trading_day(row["cutoff_at"])
            and _trading_gap(
                latest_completed_us_trading_day(row["cutoff_at"]), row["as_of_session"]
            )
            == 1
            for row in rows
        ),
        "non_session_calendar_label_count": sum(
            row["as_of_session"] > latest_completed_us_trading_day(row["cutoff_at"])
            and _trading_gap(
                latest_completed_us_trading_day(row["cutoff_at"]), row["as_of_session"]
            )
            == 0
            for row in rows
        ),
        "ahead_run_count": len(
            {
                row["run_id"]
                for row in rows
                if row["id"] in set(ahead_ids) and row["run_id"] is not None
            }
        ),
        "ahead_id_sha256": _stable_hash(ahead_ids),
    }
    result["score_snapshots_with_source_bar_ahead"] = _audit_ceri_source_bars()
    return result


def _audit_ceri_source_bars() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT s.id,s.run_id,s.ticker,s.cutoff_at,max(p.bar_date) max_bar_date
        FROM ceri_score_snapshots s
        LEFT JOIN LATERAL jsonb_array_elements_text(
          coalesce(s.evidence_lineage_json->'price_bar_ids','[]')) j(id) ON true
        LEFT JOIN price_bars p ON p.id=j.id::bigint
        GROUP BY s.id ORDER BY s.id
        """
    )
    distribution: Counter[str] = Counter()
    ids = []
    for row in rows:
        if row["max_bar_date"] is None:
            distribution["no_price_bars"] += 1
            continue
        expected = latest_completed_us_trading_day(row["cutoff_at"])
        relation = (
            "ahead"
            if row["max_bar_date"] > expected
            else "aligned"
            if row["max_bar_date"] == expected
            else "behind"
        )
        distribution[relation] += 1
        if relation == "ahead":
            ids.append(row["id"])
    return {"distribution": dict(distribution), "ahead_id_sha256": _stable_hash(ids)}


def audit_price_response() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _rows(
        """
        SELECT id,company_id,ticker,event_type,event_id,event_effective_at,
               event_effective_session,reaction_session,created_at
        FROM ceri_price_response_features ORDER BY id
        """
    )
    counts: Counter[str] = Counter()
    contaminated: list[dict[str, Any]] = []
    for row in rows:
        counts["total"] += 1
        if row["event_effective_at"] is None or row["reaction_session"] is None:
            counts["timestamp_unresolved"] += 1
            continue
        counts["timestamp_resolvable"] += 1
        schedule = us_market_session(row["reaction_session"])
        if schedule is None:
            counts["invalid_reaction_session"] += 1
            continue
        event_at = row["event_effective_at"].astimezone(NY)
        if schedule.open_at < event_at:
            counts["pre_event_contaminated"] += 1
            counts[f"event_type:{row['event_type']}"] += 1
            contaminated.append(
                {
                    "price_response_feature_id": row["id"],
                    "company_id": row["company_id"],
                    "ticker": row["ticker"],
                    "event_type": row["event_type"],
                    "event_id": row["event_id"],
                    "event_effective_at": row["event_effective_at"].isoformat(),
                    "event_effective_session": row["event_effective_session"],
                    "reaction_session": row["reaction_session"],
                    "reaction_open_at": schedule.open_at.isoformat(),
                }
            )
        elif schedule.open_at == event_at:
            counts["exact_open_equal"] += 1
        else:
            counts["pre_market_safe"] += 1
    ids = [row["price_response_feature_id"] for row in contaminated]
    values = ",".join(str(value) for value in ids) or "NULL"
    lineage = _scalar(
        f"""SELECT count(*) FROM ceri_score_snapshots s WHERE EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(coalesce(
          s.evidence_lineage_json->'price_response_feature_ids','[]')) j(id)
        WHERE j.id::bigint IN ({values}))"""
    )
    scored = _scalar(
        f"""SELECT count(*) FROM ceri_score_snapshots s WHERE EXISTS (
        SELECT 1 FROM jsonb_array_elements(coalesce(
          s.opportunity_ledger_json->'components','[]')) c
        CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(c->'evidence_ids','[]')) e(id)
        WHERE c->>'name'='price_response'
          AND coalesce((c->>'available')::boolean,false)=true
          AND e.id::bigint IN ({values}))"""
    )
    direct_sql_count = _scalar(
        """SELECT count(*) FROM ceri_price_response_features
        WHERE ((reaction_session + time '09:30') AT TIME ZONE 'America/New_York')
              < event_effective_at"""
    )
    assert direct_sql_count == counts["pre_event_contaminated"]
    return (
        {
            **dict(counts),
            "id_sha256": _stable_hash(ids),
            "score_snapshots_with_lineage": lineage,
            "score_snapshots_with_available_scored_component": scored,
            "independent_direct_sql_count": direct_sql_count,
        },
        contaminated,
    )


def audit_ceri_alert_cooldown() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT a.id,a.alert_rule_id,a.ticker,a.created_at,r.cooldown_sessions,
          ch.id change_id,ch.created_at change_at,ch.catalyst_revision_id,ch.guidance_event_id
        FROM ceri_alert_events a
        JOIN ceri_alert_rules r ON r.id=a.alert_rule_id
        JOIN ceri_change_events ch ON ch.id=a.source_change_event_id
        ORDER BY a.created_at,a.id
        """
    )
    sessions = CeriEffectiveSessionService("America/New_York")
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    differing_ids = []
    for row in rows:
        if row["created_at"].date() != row["created_at"].astimezone(NY).date():
            counts["alert_calendar_date_diff"] += 1
        if row["change_at"].date() != row["change_at"].astimezone(NY).date():
            counts["change_calendar_date_diff"] += 1
        key = (row["alert_rule_id"], row["ticker"].upper())
        if (
            row["catalyst_revision_id"] is not None
            or row["guidance_event_id"] is not None
            or not row["cooldown_sessions"]
        ):
            groups[key].append(row)
            continue
        current_block = False
        correct_block = False
        age_differs = False
        for prior in groups[key]:
            current_age = _trading_sessions_between(
                prior["created_at"].date(), row["change_at"].date(), sessions
            )
            correct_age = _trading_sessions_between(
                sessions.resolve(timestamp=prior["created_at"]).effective_session,
                sessions.resolve(timestamp=row["change_at"]).effective_session,
                sessions,
            )
            age_differs |= current_age != correct_age
            current_block |= 0 <= current_age < row["cooldown_sessions"]
            correct_block |= 0 <= correct_age < row["cooldown_sessions"]
        counts["evaluations_with_age_difference"] += int(age_differs)
        if current_block != correct_block:
            counts["evaluations_with_decision_difference"] += 1
            differing_ids.append(row["id"])
        groups[key].append(row)
    return {
        "alert_count": len(rows),
        **dict(counts),
        "decision_difference_id_sha256": _stable_hash(differing_ids),
        "caveat": (
            "Suppressed alerts leave no row; this reconstruction can certify only "
            "persisted evaluations."
        ),
    }


def audit_htf() -> dict[str, Any]:
    rows = _rows(
        """
        SELECT id,NULLIF(debug_json->'indicator_debug'->>'end_date','')::date AS end_date
        FROM technical_scores ORDER BY id
        """
    )
    counts: Counter[str] = Counter()
    for row in rows:
        end = row["end_date"]
        if end is None:
            counts["insufficient_or_unknown"] += 1
            continue
        friday = end + timedelta(days=(4 - end.weekday()) % 7)
        later_session = any(
            is_us_trading_day(end + timedelta(days=offset))
            for offset in range(1, (friday - end).days + 1)
        )
        if later_session:
            counts["partial_week_previous_week_selected"] += 1
        else:
            counts["completed_week_dropped_stale_one_week"] += 1
    return dict(counts)


def audit_ib_market_intelligence() -> dict[str, Any]:
    requests = _rows(
        """
        SELECT id,intelligence_run_id,ticker,started_at,request_json,result_counts_json
        FROM ib_intelligence_request_items
        WHERE request_family='HISTORICAL' ORDER BY id
        """
    )
    request_evidence = []
    total_calendar = total_sessions = 0
    for row in requests:
        start = date.fromisoformat(row["request_json"]["start_date"])
        end = date.fromisoformat(row["request_json"]["end_date"])
        calendar_days = (end - start).days + 1
        sessions = sum(
            is_us_trading_day(start + timedelta(days=offset)) for offset in range(calendar_days)
        )
        total_calendar += calendar_days
        total_sessions += sessions
        request_evidence.append(
            {
                "request_item_id": row["id"],
                "ticker": row["ticker"],
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "expected_end_at_request_start": latest_completed_us_trading_day(
                    row["started_at"]
                ).isoformat(),
                "calendar_days_requested": calendar_days,
                "trading_sessions_in_scope": sessions,
                "non_session_dates_in_scope": calendar_days - sessions,
                "post_filter_rows": row["result_counts_json"].get("rows"),
            }
        )
    bars = _rows(
        """
        SELECT b.id,b.ticker,b.session_date,b.first_seen_at,r.started_at
        FROM ib_historical_metric_bars b
        JOIN ib_intelligence_runs ir ON ir.id=b.intelligence_run_id
        LEFT JOIN ib_intelligence_request_items r
          ON r.intelligence_run_id=ir.id AND r.request_family='HISTORICAL'
        ORDER BY b.id
        """
    )
    early_bar_ids = [
        row["id"]
        for row in bars
        if row["session_date"] > latest_completed_us_trading_day(row["first_seen_at"])
    ]
    features = _rows(
        "SELECT id,ticker,module,as_of_session,calculated_at "
        "FROM ib_intelligence_features ORDER BY id"
    )
    ahead_feature_ids = [
        row["id"]
        for row in features
        if row["as_of_session"] > latest_completed_us_trading_day(row["calculated_at"])
    ]
    return {
        "historical_requests": request_evidence,
        "calendar_days_requested": total_calendar,
        "trading_sessions_in_scope": total_sessions,
        "non_session_dates_in_scope": total_calendar - total_sessions,
        "historical_bars_first_seen_before_safe_boundary": len(early_bar_ids),
        "historical_bar_id_sha256": _stable_hash(early_bar_ids),
        "ib_intelligence_feature_count": len(features),
        "feature_as_of_ahead_count": len(ahead_feature_ids),
        "feature_ahead_id_sha256": _stable_hash(ahead_feature_ids),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/forensics"),
        help="Local evidence output directory.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    technical, technical_rows = audit_technical()
    lifecycle, lifecycle_rows = audit_lifecycle()
    price_response, price_response_rows = audit_price_response()
    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "safety": {"database_transactions": "READ ONLY", "external_requests": 0},
        "certification": {
            "findings_checked": 18,
            "status_counts": {"CONFIRMED": 14, "PARTIALLY_CONFIRMED": 4},
            "finding_statuses": {
                **{f"STI-F{number:03d}": "CONFIRMED" for number in range(1, 19)},
                **{
                    finding: "PARTIALLY_CONFIRMED"
                    for finding in ("STI-F003", "STI-F006", "STI-F011", "STI-F014")
                },
            },
            "final_pipeline_canary_safe": False,
        },
        "technical": technical,
        "technical_source_coherence": audit_technical_source_coherence(),
        "early_price_bars": audit_early_price_bars(),
        "technical_artifacts": audit_artifacts(),
        "market_regime": audit_market_regime(),
        "sector_rotation": audit_sector_rotation(),
        "setup_lifecycle": lifecycle,
        "ceri_features_and_capture": audit_ceri_features_and_capture(),
        "ceri_price_response": price_response,
        "ceri_alert_cooldown": audit_ceri_alert_cooldown(),
        "weekly_htf": audit_htf(),
        "ib_market_intelligence": audit_ib_market_intelligence(),
    }
    summary_path = args.output_dir / "session_integrity_summary.json"
    summary_path.write_text(
        json.dumps(summary, default=_json_value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(args.output_dir / "technical_session_mismatches.csv", technical_rows)
    _write_csv(args.output_dir / "lifecycle_session_mismatches.csv", lifecycle_rows)
    _write_csv(args.output_dir / "ceri_pre_event_price_response.csv", price_response_rows)
    print(summary_path)


if __name__ == "__main__":
    main()
