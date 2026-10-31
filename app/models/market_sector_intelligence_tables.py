"""Additive independent market-sector history; no run-sector relationships."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, object_session

from app.db import Base

_ID = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB(), "postgresql")
_METRICS = (
    "rs_5_pct",
    "rs_21_pct",
    "rs_63_pct",
    "rs_prior_21_pct",
    "relative_momentum_pp",
    "etf_return_63_pct",
    "spy_return_63_pct",
)


class MarketSectorRotationSnapshot(Base):
    __tablename__ = "market_sector_rotation_snapshots"

    id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    market_session: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cutoff_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort_status: Mapped[str] = mapped_column(String(16), nullable=False)
    eligible_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_material_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_summary_json: Mapped[dict] = mapped_column(_JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "market_session",
            "algorithm_version",
            "configuration_digest",
            name="uq_msi_rotation_session_version_config",
        ),
        CheckConstraint(
            "cohort_status = 'COMPLETE' AND eligible_count = 11", name="ck_msi_rotation_complete"
        ),
        CheckConstraint(
            "benchmark_symbol = 'SPY' AND timeframe = '1 day'", name="ck_msi_rotation_benchmark"
        ),
        Index("idx_msi_rotation_latest", market_session.desc(), id.desc()),
        Index("idx_msi_rotation_algorithm", "algorithm_version"),
    )


class MarketSectorRotationRow(Base):
    __tablename__ = "market_sector_rotation_rows"

    id: Mapped[int] = mapped_column(_ID, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("market_sector_rotation_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    sector_canonical: Mapped[str] = mapped_column(String(128), nullable=False)
    sector_display: Mapped[str] = mapped_column(String(128), nullable=False)
    etf_symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    price_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    benchmark_price_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_sessions_json: Mapped[dict] = mapped_column(_JSON, nullable=False)
    rs_5_pct: Mapped[float] = mapped_column(Float, nullable=False)
    rs_21_pct: Mapped[float] = mapped_column(Float, nullable=False)
    rs_63_pct: Mapped[float] = mapped_column(Float, nullable=False)
    rs_prior_21_pct: Mapped[float] = mapped_column(Float, nullable=False)
    relative_momentum_pp: Mapped[float] = mapped_column(Float, nullable=False)
    etf_return_63_pct: Mapped[float] = mapped_column(Float, nullable=False)
    spy_return_63_pct: Mapped[float] = mapped_column(Float, nullable=False)
    rotation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "etf_symbol", name="uq_msi_rotation_symbol"),
        UniqueConstraint("snapshot_id", "rank", name="uq_msi_rotation_rank"),
        CheckConstraint("rank BETWEEN 1 AND 11", name="ck_msi_rotation_rank"),
        CheckConstraint("quality_status = 'ELIGIBLE'", name="ck_msi_rotation_eligible"),
        CheckConstraint(
            "price_basis IN ('TRADES', 'ADJUSTED_LAST') AND price_basis = benchmark_price_basis",
            name="ck_msi_rotation_basis",
        ),
        CheckConstraint(
            "rotation_state IN ('Leading', 'Improving', 'Weakening', 'Lagging')",
            name="ck_msi_rotation_state",
        ),
        *(
            CheckConstraint(
                f"{name} BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308",
                name=f"ck_msi_finite_{name}",
            )
            for name in _METRICS
        ),
    )


def _guard_insert(_mapper, _connection, target):
    from app.services.market_sector_intelligence_capture_authority import require_capture_insert

    require_capture_insert(object_session(target), target)


def _reject_mutation(_mapper, _connection, _target):
    raise ValueError("MARKET_SECTOR_ROTATION_IMMUTABLE")


for _model in (MarketSectorRotationSnapshot, MarketSectorRotationRow):
    event.listen(_model, "before_insert", _guard_insert)
    event.listen(_model, "before_update", _reject_mutation)
    event.listen(_model, "before_delete", _reject_mutation)
