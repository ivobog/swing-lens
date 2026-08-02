from datetime import date
from types import SimpleNamespace

from app.models.tables import SectorRotationRow
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_rotation_dtos import SectorUniverseMetrics
from app.services.sector_rotation_service import (
    CALCULATION_VERSION,
    MODE_UNIVERSE_ONLY,
    SectorRotationService,
)


def test_build_snapshot_ranks_decisions_builds_summary_and_persists() -> None:
    repository = FakeSectorRepository()
    service = _service(
        universe_rows=[
            _metrics("Technology", score=8.4, ticker_count=12, top_25_count=3),
            _metrics(
                "Utilities",
                score=4.2,
                ticker_count=6,
                top_25_count=0,
                danger_share=0.2,
            ),
        ],
        repository=repository,
        market_repository=FakeMarketRepository(run_snapshot=_market_snapshot()),
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=True,
        config=load_sector_rotation_config(),
    )

    assert dto.run_id == 7
    assert dto.as_of_date == "2026-07-28"
    assert dto.mode == MODE_UNIVERSE_ONLY
    assert dto.calculation_version == CALCULATION_VERSION
    assert dto.market_regime_snapshot_id == 3
    assert [row.sector for row in dto.rows] == ["Technology", "Utilities"]
    assert [row.rank for row in dto.rows] == [1, 2]
    assert dto.summary["leading_sector"] == "Technology"
    assert dto.summary["weakest_sector"] == "Utilities"
    assert dto.summary["riskiest_sector"] == "Utilities"
    assert dto.summary["ticker_count"] == 18
    assert len(repository.saved) == 1
    saved = repository.saved[0]
    assert saved.run_id == 7
    assert saved.leading_sector == "Technology"
    assert saved.rows[0].sector == "Technology"
    assert saved.rows[0].current_rank == 1
    assert saved.rows[0].sector_final_score == 8.4


def test_build_snapshot_persist_false_does_not_save() -> None:
    repository = FakeSectorRepository()
    service = _service(
        universe_rows=[_metrics("Technology", score=8.0)],
        repository=repository,
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=False,
        config=load_sector_rotation_config(),
    )

    assert dto.summary["leading_sector"] == "Technology"
    assert repository.saved == []
    assert dto.debug["persist_requested"] is False


def test_build_snapshot_empty_run_returns_warning_and_empty_summary() -> None:
    repository = FakeSectorRepository()
    service = _service(universe_rows=[], repository=repository)

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=True,
        config=load_sector_rotation_config(),
    )

    assert dto.rows == []
    assert dto.summary == {
        "leading_sector": None,
        "weakest_sector": None,
        "riskiest_sector": None,
        "most_represented_top25_sector": None,
        "fastest_improving_sector": None,
        "sector_count": 0,
        "ticker_count": 0,
        "leading_sector_ticker_count": None,
    }
    assert dto.warnings == [
        "missing_asof_market_regime_context",
        "empty_sector_rotation_universe",
    ]
    assert repository.saved[0].rows == []


def test_build_snapshot_uses_previous_rows_for_rank_and_score_changes() -> None:
    previous_snapshot = SimpleNamespace(id=11)
    previous_rows = [
        SectorRotationRow(
            snapshot_id=11,
            sector="Technology",
            sector_slug="technology",
            sector_final_score=7.0,
            rotation_state="Leading",
            sector_permission="full_allowed",
            confidence="high",
            current_rank=2,
        )
    ]
    service = _service(
        universe_rows=[
            _metrics("Healthcare", score=8.8),
            _metrics("Technology", score=8.0),
        ],
        repository=FakeSectorRepository(
            previous_snapshot=previous_snapshot,
            previous_rows=previous_rows,
        ),
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=False,
        config=load_sector_rotation_config(),
    )

    technology = next(row for row in dto.rows if row.sector == "Technology")
    assert technology.rank == 2
    assert technology.previous_rank == 2
    assert technology.rank_change == 0
    assert technology.score_change == 1.0
    assert dto.summary["fastest_improving_sector"] == "Technology"


def test_build_snapshot_uses_asof_global_market_snapshot_when_run_snapshot_missing() -> None:
    service = _service(
        universe_rows=[_metrics("Technology", score=8.0)],
        market_repository=FakeMarketRepository(
            run_snapshot=None,
            global_snapshot=_market_snapshot(
                as_of_date=date(2026, 7, 28),
                risk_state="Red",
                risk_off=True,
            ),
        ),
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=False,
        config=load_sector_rotation_config(),
    )

    assert dto.debug["market_regime"]["risk_state"] == "Red"
    assert dto.rows[0].permission == "watch_only"
    assert "market_risk_off" in dto.rows[0].warnings


def test_build_snapshot_does_not_attach_future_global_market_snapshot() -> None:
    service = _service(
        universe_rows=[_metrics("Technology", score=8.0)],
        market_repository=FakeMarketRepository(
            run_snapshot=None,
            global_snapshot=None,
            latest_snapshot=_market_snapshot(
                as_of_date=date(2026, 7, 29),
                risk_state="Red",
                risk_off=True,
            ),
        ),
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=False,
        config=load_sector_rotation_config(),
    )

    assert dto.market_regime_snapshot_id is None
    assert dto.debug["market_regime"] == {
        "available": False,
        "missing_reason": "missing_asof_market_regime_context",
    }
    assert "missing_asof_market_regime_context" in dto.warnings
    assert dto.rows[0].permission == "reduced_size"
    assert "market_risk_off" not in dto.rows[0].warnings


def test_build_snapshot_uses_etf_metrics_when_enabled() -> None:
    config = load_sector_rotation_config()
    config["etf_score"]["enabled"] = True
    repository = FakeSectorRepository()
    service = _service(
        universe_rows=[_metrics("Technology", score=8.0)],
        etf_rows=[_etf_metrics("Technology", score=6.0)],
        repository=repository,
        market_repository=FakeMarketRepository(run_snapshot=_market_snapshot()),
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=True,
        config=config,
    )

    assert dto.mode == "combined"
    assert dto.rows[0].final_score == 7.1
    assert dto.rows[0].etf_score == 6.0
    assert dto.rows[0].debug["score_source"] == "combined"
    assert dto.etf_rows[0].proxy_ticker == "XLK"
    saved_row = repository.saved[0].rows[0]
    assert saved_row.etf_rotation_score == 6.0
    assert saved_row.etf_metrics["proxy_ticker"] == "XLK"


def test_build_snapshot_falls_back_when_etf_enabled_but_proxy_missing() -> None:
    config = load_sector_rotation_config()
    config["etf_score"]["enabled"] = True
    service = _service(
        universe_rows=[_metrics("Technology", score=8.0)],
        etf_rows=[_etf_metrics("Technology", score=None, warnings=["missing_xlk_etf_data"])],
        market_repository=FakeMarketRepository(run_snapshot=_market_snapshot()),
    )

    dto = service.build_sector_rotation_snapshot(
        object(),
        run_id=7,
        as_of_date=date(2026, 7, 28),
        persist=False,
        config=config,
    )

    assert dto.rows[0].final_score == 8.0
    assert dto.rows[0].etf_score is None
    assert "missing_etf_confirmation" in dto.rows[0].warnings
    assert "missing_xlk_etf_data" in dto.rows[0].warnings


def _service(
    universe_rows: list[SectorUniverseMetrics],
    etf_rows=None,
    repository=None,
    market_repository=None,
) -> SectorRotationService:
    return SectorRotationService(
        universe_service=FakeUniverseService(universe_rows),
        etf_service=FakeEtfService(etf_rows or []),
        repository=repository or FakeSectorRepository(),
        market_repository=market_repository or FakeMarketRepository(),
    )


def _metrics(
    sector: str,
    score: float,
    ticker_count: int = 10,
    top_25_count: int = 1,
    danger_share: float = 0.0,
) -> SectorUniverseMetrics:
    slug = sector.lower().replace(" ", "-")
    return SectorUniverseMetrics(
        sector=sector,
        sector_slug=slug,
        ticker_count=ticker_count,
        universe_share=1.0,
        average_fundamental_score=7.0,
        average_technical_score=score,
        average_final_score=score,
        average_profile_score=score,
        top_counts={
            "top_10": min(top_25_count, 10),
            "top_25": top_25_count,
            "top_50": top_25_count,
        },
        setup_distribution={"Fresh breakout": 1},
        warning_distribution={},
        buyable_count=1,
        watch_count=0,
        danger_count=round(ticker_count * danger_share),
        buyable_share=0.1,
        watch_share=0.0,
        danger_share=danger_share,
        clean_pullback_count=0,
        breakout_count=1,
        vcp_count=0,
        tight_base_breakout_count=0,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        profile_distribution={"momentum_swing": {"top_25_count": top_25_count}},
        component_scores={"average_technical_score": score},
        universe_leadership_score=score,
        confidence="high" if ticker_count >= 10 else "normal",
        reason_codes=["low_danger_density"] if danger_share == 0 else ["high_danger_density"],
        warnings=[],
        debug={},
    )


def _market_snapshot(
    as_of_date: date = date(2026, 7, 28),
    risk_state: str = "Green",
    risk_off: bool = False,
):
    return SimpleNamespace(
        id=3,
        as_of_date=as_of_date,
        regime="Bull trend",
        risk_state=risk_state,
        risk_off=risk_off,
    )


def _etf_metrics(sector: str, score: float | None, warnings: list[str] | None = None):
    return SimpleNamespace(
        sector=sector,
        sector_slug=sector.lower().replace(" ", "-"),
        proxy_ticker="XLK",
        benchmark_ticker="SPY",
        as_of_date="2026-07-28",
        etf_rotation_score=score,
        component_scores={"trend": score or 0.0},
        metrics={"above_sma50": True},
        warnings=warnings or [],
        debug={},
    )


class FakeUniverseService:
    def __init__(self, rows: list[SectorUniverseMetrics]) -> None:
        self.rows = rows
        self.calls = []

    def build(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


class FakeEtfService:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.calls = []

    def build(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


class FakeSectorRepository:
    def __init__(self, previous_snapshot=None, previous_rows=None) -> None:
        self.previous_snapshot = previous_snapshot
        self.previous_rows = previous_rows or []
        self.saved = []

    def get_previous_snapshot(self, *_args, **_kwargs):
        return self.previous_snapshot

    def get_snapshot_rows(self, *_args, **_kwargs):
        return self.previous_rows

    def save_snapshot(self, _db, dto):
        self.saved.append(dto)
        return SimpleNamespace(id=22)


class FakeMarketRepository:
    def __init__(
        self,
        run_snapshot=None,
        global_snapshot=None,
        latest_snapshot=None,
    ) -> None:
        self.run_snapshot = run_snapshot
        self.global_snapshot = global_snapshot
        self.latest_snapshot = latest_snapshot

    def latest_for_run(self, *_args, **_kwargs):
        return self.run_snapshot

    def latest(self, *_args, **_kwargs):
        return self.latest_snapshot

    def latest_for_run_as_of_or_before(self, _db, _run_id, as_of_date):
        if self.run_snapshot is None or self.run_snapshot.as_of_date > as_of_date:
            return None
        return self.run_snapshot

    def latest_global_as_of_or_before(self, _db, as_of_date):
        if self.global_snapshot is None or self.global_snapshot.as_of_date > as_of_date:
            return None
        return self.global_snapshot
