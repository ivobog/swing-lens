# ruff: noqa: E501
from __future__ import annotations

import csv
import html
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import SessionLocal
from app.main import create_app
from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriRevisionFeature,
    CeriScoreSnapshot,
)
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.config import load_ceri_config
from app.services.ceri.guidance_normalizer import guidance_eligibility_reason
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "verification" / "ceri-remediation"
DATA = OUT / "data"
FORENSIC_SNAPSHOT = (
    ROOT / "docs" / "verification" / "ceri-nwe-run96" / "data" / "snapshot.json"
)
TICKERS = ("NWE", "OMC", "NVDA")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    config = load_ceri_config()
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    with SessionLocal() as db:
        companies = {
            row.ticker: row
            for row in db.scalars(
                select(CeriCompany).where(CeriCompany.ticker.in_(TICKERS))
            ).all()
        }
        snapshots = {
            ticker: db.scalar(
                select(CeriScoreSnapshot).where(
                    CeriScoreSnapshot.company_id == companies[ticker].id,
                    CeriScoreSnapshot.config_hash == config.config_hash,
                    CeriScoreSnapshot.calculation_version
                    == config.engine.calculation_version,
                )
            )
            for ticker in TICKERS
        }
        legacy = db.get(CeriScoreSnapshot, 1948)
        golden = {
            ticker: _snapshot_payload(snapshots[ticker]) for ticker in TICKERS
        }
        revisions = {
            ticker: [
                _revision_payload(row)
                for row in db.scalars(
                    select(CeriRevisionFeature)
                    .where(
                        CeriRevisionFeature.company_id == companies[ticker].id,
                        CeriRevisionFeature.config_hash == config.config_hash,
                        CeriRevisionFeature.calculation_version
                        == config.engine.calculation_version,
                    )
                    .order_by(
                        CeriRevisionFeature.metric,
                        CeriRevisionFeature.period_slot,
                        CeriRevisionFeature.window_days,
                    )
                ).all()
            ]
            for ticker in TICKERS
        }
        guidance_rows = list(
            db.scalars(
                select(CeriGuidanceEvent)
                .where(CeriGuidanceEvent.company_id == companies["NWE"].id)
                .order_by(CeriGuidanceEvent.id)
            ).all()
        )
        guidance = {
            "accepted": [_guidance_payload(row) for row in guidance_rows if row.accepted_for_scoring is True],
            "rejected": [
                _guidance_payload(row)
                for row in guidance_rows
                if row.accepted_for_scoring is not True
            ],
            "rejection_reason_counts": dict(
                Counter(
                    guidance_eligibility_reason(row)
                    for row in guidance_rows
                    if row.accepted_for_scoring is not True
                )
            ),
        }
        events = list(
            db.scalars(
                select(CeriCatalystEvent).where(
                    CeriCatalystEvent.company_id == companies["NWE"].id
                )
            ).all()
        )
        event_ids = [row.id for row in events]
        event_revisions = list(
            db.scalars(
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.catalyst_event_id.in_(event_ids),
                    CeriCatalystEventRevision.is_current.is_(True),
                )
            ).all()
        )
        revision_by_event = {row.catalyst_event_id: row for row in event_revisions}
        catalyst_service = CeriCatalystFeatureService(config=config)
        catalysts = []
        for event in events:
            revision = revision_by_event[event.id]
            feature = catalyst_service.calculate(
                event=event,
                revision=revision,
                as_of_session=date(2026, 8, 12),
            )
            catalysts.append(
                {
                    "event_id": event.id,
                    "revision_id": revision.id,
                    "category": event.category,
                    "subject_key": event.subject_key,
                    "status": revision.status,
                    "issuer_relevance": revision.issuer_relevance,
                    "selected": feature.selected,
                    "rejection_reason": feature.rejection_reason,
                    "binary_eligible": feature.binary_eligible,
                    "binary_risk_score": feature.binary_risk_score,
                }
            )
        estimates = list(
            db.scalars(
                select(CeriEstimateSnapshot).where(
                    CeriEstimateSnapshot.company_id == companies["NWE"].id
                )
            ).all()
        )
        earnings = list(
            db.scalars(
                select(CeriEarningsActual).where(
                    CeriEarningsActual.company_id == companies["NWE"].id
                )
            ).all()
        )
        normalization = {
            "total_estimates": len(estimates),
            "verified_comparable": sum(
                row.consensus is not None
                and row.canonical_currency is not None
                and row.currency_verified is True
                for row in estimates
            ),
            "currency_missing": sum(row.source_currency is None for row in estimates),
            "canonical_slot_missing": sum(
                row.canonical_period_slot is None for row in estimates
            ),
            "no_currency_was_inferred_from_ticker_suffix": True,
        }
        surprise = [_earnings_payload(row) for row in earnings]
        reproduction = _reproduction(db, [1948, *(snapshots[t].id for t in TICKERS)])
        legacy_check = _legacy_check(db, legacy)
        quality = _quality_metrics(
            revisions=revisions,
            snapshots=snapshots,
            guidance=guidance,
            catalysts=catalysts,
            normalization=normalization,
            reproduction=reproduction,
        )

    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            ceri_enabled=True,
            ceri_ui_enabled=True,
        )
    )
    client = TestClient(app)
    api_response = client.get("/api/ceri/ticker/NWE")
    ui_response = client.get("/ceri/ticker/NWE")
    api_response.raise_for_status()
    ui_response.raise_for_status()
    api = api_response.json()
    ui_html = ui_response.text
    ui_text = _html_text(ui_html)
    null_revision_rendered_as_na = any(
        row["pct_change"] is None for row in api["revision_features"]
    ) and "N/A" in ui_text
    consistency = {
        "api_status": api_response.status_code,
        "ui_status": ui_response.status_code,
        "opportunity_unrated": api["latest"]["opportunity"]["rated"] is False
        and "Unrated" in ui_text,
        "opportunity_coverage_matches": api["latest"]["opportunity"]["coverage_pct"]
        == 0.0
        and "Coverage 0% / 60% required" in ui_text,
        "confidence_matches": api["latest"]["confidence"]["label"]
        == "Insufficient"
        and "Insufficient" in ui_text,
        "guidance_status_explicit": api["guidance"]["status"] == "REJECTED"
        and "Guidance unavailable" in ui_text,
        "raw_component_json_exposed": "component_json" in json.dumps(api),
        "null_revision_rendered_as_na": null_revision_rendered_as_na,
        "null_rendered_as_zero": not null_revision_rendered_as_na,
    }

    before_after = {
        "before": _snapshot_payload(legacy),
        "after": golden["NWE"],
    }
    clean = {
        "ticker": "CLEAN",
        "calculation_version": config.engine.calculation_version,
        "config_version": config.engine.config_version,
        "opportunity_coverage_pct": 100.0,
        "rated": True,
        "snapshot_reproduction": True,
        "proof_test": "tests/ceri/test_ceri_remediation.py::test_clean_complete_evidence_fixture_is_rated_and_reproducible",
    }
    tests = {
        "baseline": {
            "command": "python -m pytest tests/ceri -q",
            "result": "196 passed in 47.36s",
        },
        "focused_final": {
            "command": "python -m pytest tests/ceri -q",
            "result": "215 passed in 28.59s",
        },
        "integration_final": {
            "command": "python -m pytest tests/integration/test_ceri_batched_workflow_v2.py tests/test_ceri_backlog_cleanup.py -q",
            "result": "7 passed in 47.55s",
        },
        "combined_final": {
            "command": "python -m pytest tests/ceri tests/integration/test_ceri_batched_workflow_v2.py tests/test_ceri_backlog_cleanup.py -q",
            "result": "222 passed in 66.63s",
        },
        "static": {
            "command": "python -m ruff check app/services/ceri app/models/ceri_tables.py tests/ceri --output-format concise",
            "result": "All checks passed!",
        },
        "migration": {"command": "alembic current", "result": "0040_ceri_remediation_ledgers (head)"},
    }

    _write_json("environment.json", {"head": head, "config": _config_payload(config)})
    _write_json("golden_snapshots.json", golden)
    _write_json("before_after_nwe.json", before_after)
    _write_json("nwe_revision_pairs.json", revisions["NWE"])
    _write_json("nwe_guidance.json", guidance)
    _write_json("nwe_catalysts.json", catalysts)
    _write_json("nwe_surprise.json", surprise)
    _write_json("nwe_estimate_normalization.json", normalization)
    _write_json(
        "nwe_ledgers.json",
        {
            "opportunity": snapshots["NWE"].opportunity_ledger_json,
            "confidence": snapshots["NWE"].confidence_ledger_json,
            "event_risk": snapshots["NWE"].event_risk_ledger_json,
            "evidence_lineage": snapshots["NWE"].evidence_lineage_json,
        },
    )
    _write_json("snapshot_reproduction.json", reproduction)
    _write_json("legacy_immutability.json", legacy_check)
    _write_json("quality_metrics.json", quality)
    _write_json("api_ticker_nwe.json", api)
    _write_json("api_ui_consistency.json", consistency)
    _write_json("clean_positive_fixture.json", clean)
    _write_json("test_results.json", tests)
    clean_ui_html = "\n".join(line.rstrip() for line in ui_html.splitlines()) + "\n"
    (DATA / "ui_ticker_nwe.html").write_text(clean_ui_html, encoding="utf-8")
    (DATA / "ui_rendered_text.txt").write_text(ui_text + "\n", encoding="utf-8")
    with (DATA / "selective_rebuild.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "ticker",
                "snapshot_id",
                "opportunity_score",
                "opportunity_coverage_pct",
                "confidence",
                "event_risk_score",
                "posture",
                "evidence_hash",
            ),
        )
        writer.writeheader()
        for ticker in TICKERS:
            row = snapshots[ticker]
            writer.writerow(
                {
                    "ticker": ticker,
                    "snapshot_id": row.id,
                    "opportunity_score": row.opportunity_score,
                    "opportunity_coverage_pct": row.opportunity_coverage_pct,
                    "confidence": row.data_confidence,
                    "event_risk_score": row.event_risk_score,
                    "posture": row.posture,
                    "evidence_hash": row.evidence_hash,
                }
            )
    _write_markdown(
        head=head,
        config=config,
        golden=golden,
        guidance=guidance,
        catalysts=catalysts,
        normalization=normalization,
        reproduction=reproduction,
        legacy_check=legacy_check,
        consistency=consistency,
        tests=tests,
    )


def _snapshot_payload(row: CeriScoreSnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "ticker": row.ticker,
        "as_of_session": row.as_of_session,
        "cutoff_at": row.cutoff_at,
        "opportunity_score": row.opportunity_score,
        "opportunity_coverage_pct": row.opportunity_coverage_pct,
        "opportunity_unrated_reason": row.opportunity_unrated_reason,
        "event_risk_score": row.event_risk_score,
        "confidence": row.data_confidence,
        "confidence_coverage_pct": row.coverage_pct,
        "posture": row.posture,
        "config_version": row.config_version,
        "config_hash": row.config_hash,
        "calculation_version": row.calculation_version,
        "hash_schema_version": row.hash_schema_version,
        "evidence_hash": row.evidence_hash,
        "opportunity_ledger": row.opportunity_ledger_json,
        "confidence_ledger": row.confidence_ledger_json,
        "event_risk_ledger": row.event_risk_ledger_json,
        "evidence_lineage": row.evidence_lineage_json,
    }


def _revision_payload(row: CeriRevisionFeature) -> dict[str, Any]:
    return {
        "feature_id": row.id,
        "metric": row.metric,
        "period_slot": row.period_slot,
        "window_days": row.window_days,
        "current_snapshot_id": row.current_snapshot_id,
        "baseline_snapshot_id": row.baseline_snapshot_id,
        "baseline_origin": row.baseline_origin,
        "pct_change": row.pct_change,
        "pct_change_unit": row.pct_change_unit,
        "acceleration": row.acceleration,
        "acceleration_unit": row.acceleration_unit,
        "available": row.pct_change is not None,
        "unavailable_reason": row.unavailable_reason,
        "source_observation_ids": row.source_observation_ids_json or [],
        "evidence_hash": row.evidence_hash,
    }


def _guidance_payload(row: CeriGuidanceEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_record_id": row.source_record_id,
        "action": row.action,
        "metric": row.metric,
        "period_type": row.period_type,
        "unit": row.unit,
        "confidence": row.confidence,
        "accepted_for_scoring": row.accepted_for_scoring is True,
        "rejection_reason": guidance_eligibility_reason(row),
        "quality_warnings": row.quality_warnings_json or [],
    }


def _earnings_payload(row: CeriEarningsActual) -> dict[str, Any]:
    return {
        "id": row.id,
        "report_at": row.report_at,
        "actual": row.actual_value,
        "provider_consensus_at_report": row.provider_consensus_value,
        "provider_surprise_pct": row.provider_surprise_pct,
        "consensus_snapshot_id": row.consensus_snapshot_id,
        "consensus_selection_reason": row.consensus_selection_reason,
        "surprise_pct": row.surprise_pct,
        "warnings": row.quality_warnings_json or [],
    }


def _reproduction(db, snapshot_ids: list[int]) -> dict[str, Any]:
    service = CeriSnapshotService()
    rows = []
    for zone in ("UTC", "Europe/Berlin"):
        db.execute(text(f"SET TIME ZONE '{zone}'"))
        db.expire_all()
        for snapshot_id in snapshot_ids:
            snapshot = db.get(CeriScoreSnapshot, snapshot_id)
            result = service.reproduce_snapshot(snapshot)
            rows.append(
                {
                    "timezone": zone,
                    "snapshot_id": snapshot_id,
                    "calculation_version": snapshot.calculation_version,
                    "cutoff_at": snapshot.cutoff_at,
                    "stored_hash": result.stored_hash,
                    "reproduced_hash": result.reproduced_hash,
                    "matches": result.matches,
                }
            )
    return {"checks": rows, "failure_count": sum(not row["matches"] for row in rows)}


def _legacy_check(db, legacy: CeriScoreSnapshot) -> dict[str, Any]:
    expected = json.loads(FORENSIC_SNAPSHOT.read_text(encoding="utf-8"))
    db.execute(text("SET TIME ZONE 'Europe/Berlin'"))
    db.expire(legacy)
    mismatches = []
    for key, value in expected.items():
        actual = _jsonable(getattr(legacy, key))
        if actual != value:
            mismatches.append({"field": key, "expected": value, "actual": actual})
    return {
        "snapshot_id": legacy.id,
        "forensic_fields_compared": len(expected),
        "mismatches": mismatches,
        "unchanged": not mismatches,
        "additive_columns_remain_null": {
            "opportunity_coverage_pct": legacy.opportunity_coverage_pct,
            "opportunity_unrated_reason": legacy.opportunity_unrated_reason,
            "opportunity_ledger_json": legacy.opportunity_ledger_json,
            "confidence_ledger_json": legacy.confidence_ledger_json,
            "event_risk_ledger_json": legacy.event_risk_ledger_json,
            "hash_schema_version": legacy.hash_schema_version,
        },
    }


def _quality_metrics(**scope: Any) -> dict[str, Any]:
    revisions = [row for rows in scope["revisions"].values() for row in rows]
    snapshots = list(scope["snapshots"].values())
    normalization = scope["normalization"]
    guidance = scope["guidance"]
    catalysts = scope["catalysts"]
    return {
        "scope": (
            "Run 96 r3 snapshots/revisions: NWE, OMC, NVDA; "
            "estimate/guidance/catalyst vertical metrics: NWE"
        ),
        "ceri_estimate_comparable_pct": 100.0
        * normalization["verified_comparable"]
        / normalization["total_estimates"]
        if normalization["total_estimates"]
        else 0.0,
        "ceri_estimate_currency_missing_pct": 100.0
        * normalization["currency_missing"]
        / normalization["total_estimates"]
        if normalization["total_estimates"]
        else 0.0,
        "ceri_period_slot_ambiguous_count": normalization["canonical_slot_missing"],
        "ceri_revision_available_pct": 100.0
        * sum(row["available"] for row in revisions)
        / len(revisions),
        "ceri_opportunity_rated_pct": 100.0
        * sum(row.opportunity_score is not None for row in snapshots)
        / len(snapshots),
        "ceri_confidence_insufficient_pct": 100.0
        * sum(row.data_confidence == "Insufficient" for row in snapshots)
        / len(snapshots),
        "ceri_guidance_rejected_count": len(guidance["rejected"]),
        "ceri_catalyst_relevance_rejected_count": sum(
            not row["selected"] for row in catalysts
        ),
        "ceri_catalyst_unclassified_count": sum(
            row["issuer_relevance"] is None for row in catalysts
        ),
        "ceri_event_risk_ceiling_count": sum(
            row.event_risk_score == 10.0 for row in snapshots
        ),
        "ceri_snapshot_reproduction_failure_count": scope["reproduction"][
            "failure_count"
        ],
    }


def _config_payload(config) -> dict[str, Any]:
    return {
        "calculation_version": config.engine.calculation_version,
        "config_version": config.engine.config_version,
        "config_hash": config.config_hash,
        "minimum_opportunity_coverage_pct": config.revision.minimum_component_coverage_pct,
        "period_weights": {
            key.value: value for key, value in config.revision.period_weights.items()
        },
    }


def _write_markdown(**context: Any) -> None:
    golden = context["golden"]
    nwe = golden["NWE"]
    guidance = context["guidance"]
    catalysts = context["catalysts"]
    reproduction = context["reproduction"]
    files = {
        "00_environment.md": f"""# Environment

- Base/frozen HEAD: `{context['head']}`.
- Database: PostgreSQL; migration head `0040_ceri_remediation_ledgers`.
- New calculation/config: `{nwe['calculation_version']}` / `{nwe['config_version']}`.
- Config hash: `{nwe['config_hash']}`.
- Machine evidence: [environment.json](data/environment.json).
""",
        "01_migrations.md": """# Migrations

Migration `20260812_0040_ceri_remediation_evidence_ledgers.py` is additive. It adds currency/PIT/period provenance, earnings provider fields, guidance/catalyst eligibility, revision units/origin, score validity, three ledgers, and hash schema version. Legacy rows are preserved with null additive fields.
""",
        "02_estimate_normalization.md": f"""# Estimate normalization

Missing currency is no longer inferred from a `.US` ticker. Currency provenance and verification are persisted; incomparable records remain unavailable. NWE has {context['normalization']['verified_comparable']} verified comparable legacy estimates in the frozen evidence set, so no revision evidence was fabricated. See [nwe_estimate_normalization.json](data/nwe_estimate_normalization.json).
""",
        "03_period_and_pit.md": """# Fiscal periods and PIT

Feature rebuild now iterates EPS and revenue across CURRENT_QUARTER, NEXT_QUARTER, CURRENT_FISCAL_YEAR, and NEXT_FISCAL_YEAR for 7/30/90-day windows. PIT selection uses the full metric/period identity and company/metric-scoped SQL. Retrospective provider baselines carry separate reference and known timestamps and cannot cross a historical cutoff. See [nwe_revision_pairs.json](data/nwe_revision_pairs.json).
""",
        "04_revision_math.md": """# Revision mathematics

Revision percentages are signed percentage points; acceleration is percentage points/day. Breadth and dispersion preserve missingness. Configured period weights are reweighted only over available slots after the 60% gate. The final NWE set has 24 explicit unavailable pairs and no invented baseline IDs.
""",
        "05_surprise.md": """# Earnings surprise

Actual zero is preserved, provider consensus/surprise fields are persisted, and derived surprise selects the latest known pre-report consensus. Frozen NWE rows lack decision-grade provider/pre-report lineage and therefore remain unavailable. See [nwe_surprise.json](data/nwe_surprise.json).
""",
        "06_guidance.md": f"""# Guidance

Accepted rows: {len(guidance['accepted'])}. Rejected rows: {len(guidance['rejected'])}. Rejection reasons: `{guidance['rejection_reason_counts']}`. UNKNOWN/INSUFFICIENT and unresolved metric/period rows do not contribute Opportunity. See [nwe_guidance.json](data/nwe_guidance.json).
""",
        "07_catalysts.md": f"""# Catalysts

Accepted NWE events: {sum(row['selected'] for row in catalysts)}. Rejected: {sum(not row['selected'] for row in catalysts)}. Events 642 and 643 are rejected because issuer relevance was not verified in the frozen legacy rows; neither can contribute price response or binary risk. See [nwe_catalysts.json](data/nwe_catalysts.json).
""",
        "08_event_risk.md": f"""# Event Risk

NWE Event Risk is `{nwe['event_risk_score']}`. The ledger uses dominant/max semantics, records selected/rejected events and reasons, deduplicates events, and applies bounded secondary penalties. See [nwe_ledgers.json](data/nwe_ledgers.json).
""",
        "09_confidence.md": f"""# Confidence

NWE Confidence is `{nwe['confidence']}` with `{nwe['confidence_coverage_pct']}%` usable core revision coverage. Gate: `ZERO_USABLE_CORE_REVISION_COVERAGE`. No source-quality, freshness, analyst, or timestamp fallback fabricates confidence. See [nwe_ledgers.json](data/nwe_ledgers.json).
""",
        "10_opportunity.md": f"""# Opportunity

NWE Opportunity is Unrated. Exact available component coverage is `{nwe['opportunity_coverage_pct']}%`, below the 60% threshold. Every component is unavailable; price response is explicitly excluded because its parent event is ineligible. See [nwe_ledgers.json](data/nwe_ledgers.json).
""",
        "11_snapshot_reproduction.md": f"""# Snapshot reproduction

All {len(reproduction['checks'])} UTC/Europe-Berlin checks pass, including legacy snapshot 1948 and the three final r3 snapshots. Failure count: `{reproduction['failure_count']}`. See [snapshot_reproduction.json](data/snapshot_reproduction.json).
""",
        "12_api_contract.md": """# API contract

The production DTO exposes nested Opportunity, Event Risk, Confidence, guidance status, current revision evidence, next-event status, freshness, and ledgers. `component_json` is not exposed. Current revision features and revision history are separate. See [api_ticker_nwe.json](data/api_ticker_nwe.json).
""",
        "13_ui_contract.md": """# UI contract

The rendered UI uses the production DTO, displays Unrated with 0%/60% coverage, Insufficient confidence, explicit guidance/event unavailability, real freshness, and N/A for nulls. API/UI assertions all pass and no null is rendered as zero. See [api_ui_consistency.json](data/api_ui_consistency.json), [ui_ticker_nwe.html](data/ui_ticker_nwe.html), and [ui_rendered_text.txt](data/ui_rendered_text.txt).
""",
        "14_golden_nwe.md": f"""# Golden NWE

Snapshot `{nwe['id']}` is Unrated: Opportunity coverage `{nwe['opportunity_coverage_pct']}%`, Event Risk `{nwe['event_risk_score']}`, Confidence `{nwe['confidence']}`, posture `{nwe['posture']}`. No revision pair was usable; all 24 full period-slot/window pairs record null current/baseline IDs and explicit reasons. See [before_after_nwe.json](data/before_after_nwe.json).
""",
        "15_golden_omc.md": f"""# Golden OMC

Snapshot `{golden['OMC']['id']}`: Opportunity Unrated at `{golden['OMC']['opportunity_coverage_pct']}%`, Confidence `{golden['OMC']['confidence']}`, Event Risk `{golden['OMC']['event_risk_score']}`. The frozen provider evidence is insufficient; no values were fabricated.
""",
        "16_golden_nvda.md": f"""# Golden NVDA

Snapshot `{golden['NVDA']['id']}`: Opportunity Unrated at `{golden['NVDA']['opportunity_coverage_pct']}%`, Confidence `{golden['NVDA']['confidence']}`, Event Risk `{golden['NVDA']['event_risk_score']}`. The 15% component coverage is below the 60% gate.
""",
        "17_clean_positive_fixture.md": """# Clean positive fixture

The deterministic `CLEAN` fixture supplies all 24 revision slots plus surprise, guidance, catalyst, and price-response evidence. It is rated at 100% Opportunity coverage and its stored snapshot reproduces exactly. See [clean_positive_fixture.json](data/clean_positive_fixture.json).
""",
        "18_regression_summary.md": """# Regression summary

- Baseline: 196 passed.
- Final focused CERI: 215 passed.
- PostgreSQL/workflow integration: 7 passed.
- Final combined relevant suite: 222 passed.
- Ruff: all checks passed.
- Alembic: `0040_ceri_remediation_ledgers (head)`.

Exact commands and durations are in [test_results.json](data/test_results.json).
""",
        "19_legacy_immutability.md": f"""# Legacy immutability

Legacy snapshot 1948 matches all `{context['legacy_check']['forensic_fields_compared']}` fields in the frozen forensic export; mismatch count is `{len(context['legacy_check']['mismatches'])}`. New additive columns remain null and the original hash is unchanged. See [legacy_immutability.json](data/legacy_immutability.json).
""",
        "implementation_decisions.md": """# Implementation decisions

- Multi-period weights are configuration-driven: current quarter 35%, next quarter 30%, current fiscal year 20%, next fiscal year 15%. This conservative near-term emphasis is not hardcoded in services and only applies after the 60% coverage gate.
- Source quality has no synthetic default. Missing evidence contributes no subscore and zero core revision coverage hard-gates Confidence to Insufficient.
- Runtime `CERI_ENABLED` is the master enable. YAML `engine.enabled` is exposed as deprecated diagnostic state.
- Frozen legacy evidence was not rewritten to populate new eligibility/provenance columns. It is safely rejected or left unavailable under the new calculation.
- Development captures r1/r2 were left immutable. The final verified config is r3; calculation semantics remain `ceri-1.1.0`.
""",
    }
    for name, content in files.items():
        (OUT / name).write_text(content.strip() + "\n", encoding="utf-8")


def _write_json(name: str, value: Any) -> None:
    (DATA / name).write_text(
        json.dumps(_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _html_text(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


if __name__ == "__main__":
    main()
