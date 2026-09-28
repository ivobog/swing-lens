from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from app.models.ceri_tables import CeriEarningsActual, CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import load_ceri_config
from app.services.ceri.deployment_identity import (
    CERI_EVIDENCE_SCHEMA_REVISION,
    DeploymentSchemaMismatch,
    build_deployment_identity,
    current_deployment_identity,
)
from app.services.ceri.pit_eligibility import estimate_snapshot_historical_eligibility_at
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService
from app.services.ceri.upcoming_earnings_authority import select_upcoming_earnings


def test_critical_provenance_cap_executes_without_double_numeric_penalty() -> None:
    clean = _confidence().calculate(
        as_of_session=date(2026, 9, 21),
        revision_features=_confidence_features(),
        dataset_freshness_days={"estimates": 0},
    )
    weak = _confidence().calculate(
        as_of_session=date(2026, 9, 21),
        revision_features=_confidence_features(warning="missing_observation_timestamp"),
        dataset_freshness_days={"estimates": 0},
    )

    assert clean.label.value == "High"
    assert weak.label.value == "Low"
    assert weak.score == clean.score
    assert "critical_provenance_cap_applied" in weak.reasons
    assert weak.caps[-1].startswith("CRITICAL_PROVENANCE_CAP_LOW:")


def test_critical_provenance_cap_boundary_disabled_and_reconfigured(tmp_path: Path) -> None:
    low_service = _confidence()
    features = _confidence_features(warning="current_snapshot_unavailable")
    low = low_service.calculate(
        as_of_session=date(2026, 9, 21),
        revision_features=features,
        dataset_freshness_days={"estimates": 0},
    )
    disabled_service = _confidence_from_yaml(tmp_path, None, "disabled")
    disabled = disabled_service.calculate(
        as_of_session=date(2026, 9, 21),
        revision_features=features,
        dataset_freshness_days={"estimates": 0},
    )
    normal_service = _confidence_from_yaml(tmp_path, "Normal", "normal")
    normal = normal_service.calculate(
        as_of_session=date(2026, 9, 21),
        revision_features=features,
        dataset_freshness_days={"estimates": 0},
    )

    assert low.label.value == "Low"
    assert disabled.label.value == "High"
    assert normal.label.value == "Normal"
    assert disabled_service.config.config_hash != low_service.config.config_hash
    assert normal_service.config.config_hash != low_service.config.config_hash
    assert low.label.value == "Low"  # prior result is immutable under later config loads


def test_surprise_knowledge_time_requires_possession_and_external_authority() -> None:
    report_10 = _earnings(report_at=datetime(2026, 9, 21, 10, tzinfo=UTC))
    report_13 = _earnings(report_at=datetime(2026, 9, 21, 13, tzinfo=UTC))
    received_late = _estimate(
        known_at=None,
        retrieved_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
        provider_observed_at=datetime(2026, 9, 21, 9, tzinfo=UTC),
    )

    assert estimate_snapshot_historical_eligibility_at(received_late) == datetime(
        2026, 9, 21, 12, tzinfo=UTC
    )
    report_10_result = CeriSurpriseFeatureService().attach_consensus_snapshot(
        report_10, [received_late]
    )
    report_13_result = CeriSurpriseFeatureService().attach_consensus_snapshot(
        report_13, [received_late]
    )
    assert report_10_result.consensus_snapshot_id is None
    assert report_13_result.consensus_snapshot_id == 7

    published_after_receipt = _estimate(
        known_at=None,
        retrieved_at=datetime(2026, 9, 21, 9, tzinfo=UTC),
        provider_observed_at=datetime(2026, 9, 21, 12, tzinfo=UTC),
    )
    assert estimate_snapshot_historical_eligibility_at(published_after_receipt) == datetime(
        2026, 9, 21, 12, tzinfo=UTC
    )


def test_legacy_estimate_without_possession_is_explicitly_ineligible() -> None:
    legacy = _estimate(
        known_at=None,
        retrieved_at=None,
        provider_observed_at=datetime(2026, 9, 21, 9, tzinfo=UTC),
    )
    assert estimate_snapshot_historical_eligibility_at(legacy) is None
    feature = CeriSurpriseFeatureService().attach_consensus_snapshot(
        _earnings(report_at=datetime(2026, 9, 21, 13, tzinfo=UTC)), [legacy]
    )
    assert feature.consensus_snapshot_id is None
    assert feature.warnings == ("pre_report_consensus_unavailable",)


def test_deployment_identity_reports_current_database_head_and_separate_evidence_schema() -> None:
    identity = current_deployment_identity(config_hash="cfg", calculation_version="calc")

    assert identity["schema_revision"] == "0086_ceri_feature_source_manifest"
    assert identity["database_schema_revision"] == "0086_ceri_feature_source_manifest"
    assert identity["repository_schema_revision"] == "0086_ceri_feature_source_manifest"
    assert identity["schema_revision_match"] is True
    assert identity["ceri_evidence_schema_revision"] == CERI_EVIDENCE_SCHEMA_REVISION


def test_deployment_identity_rejects_stale_database_head_and_preserves_historical_identity() -> (
    None
):
    historical = build_deployment_identity(
        git_sha="old",
        dirty=False,
        image_digest=None,
        schema_revision="0050_sec_processor_promotion",
        config_hash="old-config",
        calculation_version="old-calc",
        provider_signatures={},
    )
    frozen = deepcopy(historical)

    try:
        current_deployment_identity(
            config_hash="cfg",
            calculation_version="calc",
            database_schema_revision="0050_sec_processor_promotion",
        )
    except DeploymentSchemaMismatch as exc:
        assert "database=0050_sec_processor_promotion" in str(exc)
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("stale database revision passed deployment certification")
    assert historical == frozen
    assert historical["schema_revision"] == "0050_sec_processor_promotion"
    assert "ceri_evidence_schema_revision" not in historical


def test_upcoming_earnings_conflict_retains_candidates_and_frozen_selection() -> None:
    cutoff = datetime(2026, 9, 21, 13, tzinfo=UTC)
    sources = {
        1: _source(1, provider="eodhd", retrieved_at=cutoff - timedelta(hours=2)),
        2: _source(2, provider="manual", retrieved_at=cutoff - timedelta(hours=1)),
    }
    rows = [
        _upcoming(1, source_id=1, session=date(2026, 10, 10)),
        _upcoming(2, source_id=2, session=date(2026, 10, 12)),
    ]

    result = select_upcoming_earnings(
        company_id=42,
        as_of_session=date(2026, 9, 21),
        cutoff_at=cutoff,
        earnings=rows,
        source_records=sources,
        config=load_ceri_config(),
    )
    evidence = result.evidence()

    assert result.selected is not None
    assert result.selected.source_record_id == 2
    assert evidence["selected_provider"] == "manual"
    assert evidence["selected_exact_value"] == "2026-10-12"
    assert {item["source_record_id"] for item in evidence["candidates"]} == {1, 2}
    assert all(item["temporally_eligible"] for item in evidence["candidates"])


def test_upcoming_earnings_temporal_attack_and_revision_pin() -> None:
    published = datetime(2026, 9, 21, 9, tzinfo=UTC)
    retrieved = datetime(2026, 9, 21, 12, tzinfo=UTC)
    source_v1 = _source(10, provider="manual", retrieved_at=retrieved)
    source_v1.published_at = published
    row_v1 = _upcoming(10, source_id=10, session=date(2026, 10, 10))
    config = load_ceri_config()

    before_possession = select_upcoming_earnings(
        company_id=42,
        as_of_session=date(2026, 9, 21),
        cutoff_at=datetime(2026, 9, 21, 10, tzinfo=UTC),
        earnings=[row_v1],
        source_records={10: source_v1},
        config=config,
    )
    after_possession = select_upcoming_earnings(
        company_id=42,
        as_of_session=date(2026, 9, 21),
        cutoff_at=datetime(2026, 9, 21, 13, tzinfo=UTC),
        earnings=[row_v1],
        source_records={10: source_v1},
        config=config,
    )
    pinned_v1 = deepcopy(after_possession.evidence())
    source_v2 = _source(
        11,
        provider="manual",
        retrieved_at=datetime(2026, 9, 22, 12, tzinfo=UTC),
    )
    source_v2.provider_record_id = source_v1.provider_record_id
    source_v2.supersedes_id = 10
    row_v2 = _upcoming(11, source_id=11, session=date(2026, 10, 14))
    revised = select_upcoming_earnings(
        company_id=42,
        as_of_session=date(2026, 9, 22),
        cutoff_at=datetime(2026, 9, 22, 13, tzinfo=UTC),
        earnings=[row_v1, row_v2],
        source_records={10: source_v1, 11: source_v2},
        config=config,
    )

    assert before_possession.selected is None
    assert before_possession.reason == "NO_ELIGIBLE_SOURCE_BACKED_UPCOMING_EARNINGS"
    assert after_possession.selected is not None
    assert after_possession.selected.earnings_session == date(2026, 10, 10)
    assert revised.selected is not None
    assert revised.selected.source_record_id == 11
    assert revised.selected.earnings_session == date(2026, 10, 14)
    assert pinned_v1["selected_source_record_id"] == 10
    assert pinned_v1["selected_exact_value"] == "2026-10-10"


def _confidence() -> CeriConfidenceService:
    return CeriConfidenceService(config=load_ceri_config())


def _confidence_from_yaml(tmp_path: Path, cap: str | None, suffix: str) -> CeriConfidenceService:
    payload = yaml.safe_load(Path("config/ceri.yaml").read_text(encoding="utf-8"))
    payload["confidence"]["critical_provenance_cap"] = cap
    path = tmp_path / f"ceri-{suffix}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return CeriConfidenceService(
        config=load_ceri_config(path, Path("config/ceri_catalyst_taxonomy.yaml"))
    )


def _confidence_features(warning: str | None = None) -> list[object]:
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            pct_change=1,
            revision_confidence_score=10,
            upward_count=6,
            downward_count=0,
            warnings_json=([warning] if warning and index == 0 else []),
        )
        for index in range(24)
    ]


def _earnings(*, report_at: datetime) -> CeriEarningsActual:
    return CeriEarningsActual(
        id=5,
        source_record_id=5,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        report_at=report_at,
        report_session=report_at.date(),
        actual_value=Decimal("2"),
        event_kind="REPORTED",
    )


def _estimate(
    *,
    known_at: datetime | None,
    retrieved_at: datetime | None,
    provider_observed_at: datetime | None,
) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        id=7,
        source_record_id=7,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 6, 30),
        consensus=Decimal("1"),
        effective_at=datetime(2026, 9, 20, 18, tzinfo=UTC),
        known_at=known_at,
        retrieved_at=retrieved_at,
        provider_observed_at=provider_observed_at,
        canonical_observation_key="estimate-7",
    )


def _source(
    identifier: int,
    *,
    provider: str,
    retrieved_at: datetime,
) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=identifier,
        provider=provider,
        dataset="earnings",
        provider_record_id=f"event-{identifier}",
        retrieved_at=retrieved_at,
        ingested_at=retrieved_at,
        content_hash=f"hash-{identifier}",
        idempotency_key=f"key-{identifier}",
    )


def _upcoming(
    identifier: int,
    *,
    source_id: int,
    session: date,
) -> CeriEarningsActual:
    return CeriEarningsActual(
        id=identifier,
        source_record_id=source_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="CURRENT_QUARTER",
        fiscal_period_end=date(2026, 9, 30),
        report_at=datetime(session.year, session.month, session.day, 21, tzinfo=UTC),
        report_session=session,
        actual_value=None,
        event_kind="UPCOMING",
    )
