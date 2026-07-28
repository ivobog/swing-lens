import csv
import json
from datetime import UTC, date, datetime
from io import StringIO

from app.models.tables import SectorRotationRow, SectorRotationSnapshot
from app.services.sector_rotation_dtos import (
    SectorRotationDecision,
    SectorRotationSnapshotDto,
    SectorUniverseMetrics,
)
from app.services.sector_rotation_export_service import (
    SECTOR_ROTATION_CSV_HEADERS,
    export_sector_rotation_csv,
    export_sector_rotation_json,
    export_sector_rotation_markdown,
    snapshot_to_payload,
)


def test_snapshot_to_payload_includes_snapshot_rows_components_and_debug() -> None:
    payload = snapshot_to_payload(
        _snapshot(),
        [_row("Utilities", rank=2), _row("Technology", rank=1)],
    )

    assert payload["snapshot"]["as_of_date"] == "2026-07-28"
    assert payload["snapshot"]["mode"] == "universe_only"
    assert payload["snapshot"]["summary"] == {"leading_sector": "Technology"}
    assert [row["sector"] for row in payload["rows"]] == ["Technology", "Utilities"]
    assert payload["rows"][0]["component_scores"] == {"risk_control": 9.0}
    assert payload["rows"][0]["reasons"] == ["top_candidate_overrepresentation"]
    assert payload["rows"][0]["debug"] == {"source": "unit"}


def test_export_sector_rotation_csv_has_stable_headers_and_rank_order() -> None:
    csv_text = export_sector_rotation_csv(
        _snapshot(),
        [_row("Utilities", rank=2), _row("Technology", rank=1)],
    )
    rows = list(csv.DictReader(StringIO(csv_text)))

    assert csv_text.startswith(",".join(SECTOR_ROTATION_CSV_HEADERS))
    assert [row["sector"] for row in rows] == ["Technology", "Utilities"]
    assert rows[0]["rank"] == "1"
    assert rows[0]["state"] == "Leading"
    assert rows[0]["permission"] == "full_allowed"
    assert rows[0]["final_score"] == "8.1"
    assert rows[0]["top_25_share"] == "0.4286"
    assert rows[0]["warnings"] == "missing_etf_confirmation"
    assert rows[0]["reasons"] == "top_candidate_overrepresentation"


def test_export_sector_rotation_json_returns_stable_json() -> None:
    payload = json.loads(export_sector_rotation_json(_snapshot(), [_row("Technology", rank=1)]))

    assert payload["snapshot"]["run_id"] == 7
    assert payload["rows"][0]["sector_slug"] == "technology"
    assert payload["rows"][0]["warning_distribution"] == {"liquidity_warning": 1}


def test_export_sector_rotation_markdown_omits_fake_etf_details_for_universe_only() -> None:
    markdown = export_sector_rotation_markdown(_snapshot(), [_row("Technology", rank=1)])

    assert "# Sector Rotation Brief - 2026-07-28" in markdown
    assert "| 1 | Technology | Leading | full_allowed | 8.10 | high |" in markdown
    assert "ETF confirmation was not used" in markdown
    assert "XLK confirmed" not in markdown


def test_export_functions_accept_in_memory_snapshot_dto() -> None:
    dto = _snapshot_dto()

    payload = snapshot_to_payload(dto)
    csv_text = export_sector_rotation_csv(dto)
    markdown = export_sector_rotation_markdown(dto)

    assert payload["snapshot"]["id"] is None
    assert payload["rows"][0]["sector"] == "Technology"
    assert "Technology" in csv_text
    assert "Sector Rotation Brief" in markdown


def _snapshot() -> SectorRotationSnapshot:
    return SectorRotationSnapshot(
        id=5,
        run_id=7,
        market_regime_snapshot_id=3,
        as_of_date=date(2026, 7, 28),
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="hash-a",
        mode="universe_only",
        default_ranking_profile="momentum_swing",
        benchmark_ticker="SPY",
        sector_count=2,
        ticker_count=20,
        leading_sector="Technology",
        weakest_sector="Utilities",
        riskiest_sector="Utilities",
        summary_json={"leading_sector": "Technology"},
        warning_flags_json=["missing_etf_confirmation"],
        debug_json={"source": "snapshot"},
        created_at=datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
    )


def _row(sector: str, rank: int) -> SectorRotationRow:
    slug = sector.lower()
    return SectorRotationRow(
        id=rank,
        snapshot_id=5,
        sector=sector,
        sector_slug=slug,
        ticker_count=14,
        universe_share=0.7,
        average_fundamental_score=7.1,
        average_technical_score=8.2,
        average_final_score=7.8,
        average_profile_score=7.5,
        top_10_count=3,
        top_25_count=6,
        top_50_count=10,
        top_25_share=0.4286,
        buyable_count=4,
        watch_count=2,
        danger_count=1,
        buyable_share=0.2857,
        watch_share=0.1429,
        danger_share=0.0714,
        universe_leadership_score=8.1,
        sector_final_score=8.1,
        rotation_state="Leading" if sector == "Technology" else "Lagging",
        sector_permission="full_allowed" if sector == "Technology" else "avoid_new_longs",
        position_size_multiplier=1.0 if sector == "Technology" else 0.0,
        confidence="high",
        current_rank=rank,
        profile_distribution_json={"momentum_swing": {"top_25_count": 6}},
        setup_distribution_json={"Fresh breakout": 2},
        warning_distribution_json={"liquidity_warning": 1},
        component_scores_json={"risk_control": 9.0},
        reason_codes_json=["top_candidate_overrepresentation"],
        warning_flags_json=["missing_etf_confirmation"],
        debug_json={"source": "unit"},
    )


def _snapshot_dto() -> SectorRotationSnapshotDto:
    universe = _universe_metrics()
    decision = SectorRotationDecision(
        sector="Technology",
        sector_slug="technology",
        final_score=8.1,
        universe_score=8.1,
        etf_score=None,
        rotation_state="Leading",
        permission="full_allowed",
        position_size_multiplier=1.0,
        confidence="high",
        rank=1,
        previous_rank=None,
        rank_change=None,
        score_change=None,
        reasons=["top_candidate_overrepresentation"],
        warnings=[],
        debug={"score_source": "universe_only"},
    )
    return SectorRotationSnapshotDto(
        run_id=7,
        as_of_date="2026-07-28",
        mode="universe_only",
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="hash-a",
        default_ranking_profile="momentum_swing",
        rows=[decision],
        benchmark_ticker="SPY",
        universe_rows=[universe],
        summary={"leading_sector": "Technology"},
        warnings=[],
        debug={},
    )


def _universe_metrics() -> SectorUniverseMetrics:
    return SectorUniverseMetrics(
        sector="Technology",
        sector_slug="technology",
        ticker_count=14,
        universe_share=0.7,
        average_fundamental_score=7.1,
        average_technical_score=8.2,
        average_final_score=7.8,
        average_profile_score=7.5,
        top_counts={"top_10": 3, "top_25": 6, "top_50": 10},
        setup_distribution={"Fresh breakout": 2},
        warning_distribution={"liquidity_warning": 1},
        buyable_count=4,
        watch_count=2,
        danger_count=1,
        buyable_share=0.2857,
        watch_share=0.1429,
        danger_share=0.0714,
        clean_pullback_count=1,
        breakout_count=2,
        vcp_count=1,
        tight_base_breakout_count=1,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        profile_distribution={"momentum_swing": {"top_25_count": 6}},
        component_scores={"risk_control": 9.0},
        universe_leadership_score=8.1,
        confidence="high",
        reason_codes=["top_candidate_overrepresentation"],
        warnings=[],
        debug={"source": "unit"},
    )
