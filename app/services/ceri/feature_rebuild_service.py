from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriRevisionFeature,
)
from app.models.tables import RawCompanyRow
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import HistoricalViewMode
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService


@dataclass(frozen=True)
class CeriFeatureRebuildRequest:
    company_ids: tuple[int, ...] | None = None
    ticker: str | None = None
    as_of_session: date | None = None
    from_session: date | None = None
    to_session: date | None = None
    run_id: int | None = None
    mode: str = "AS_KNOWN"


@dataclass(frozen=True)
class CeriFeatureRebuildResult:
    features: int = 0
    features_deduplicated: int = 0
    earnings_updated: int = 0
    processed_companies: int = 0
    warnings: int = 0
    failed: int = 0
    errors: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["feature_count"] = self.features
        result["warnings"] = self.warnings
        result["errors"] = list(self.errors)
        return result


class CeriFeatureRebuildService:
    """Rebuilds durable normalized CERI feature rows from canonical evidence."""

    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        revisions: CeriRevisionFeatureService | None = None,
        surprise: CeriSurpriseFeatureService | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.revisions = revisions or CeriRevisionFeatureService(config=self.config)
        self.surprise = surprise or CeriSurpriseFeatureService(config=self.config)

    def rebuild(
        self, db: Session, request: CeriFeatureRebuildRequest, *, processing_run: Any | None = None
    ) -> CeriFeatureRebuildResult:
        companies = self._companies(db, request)
        cutoff = request.as_of_session or request.to_session or date.today()
        cutoff_at = datetime.combine(cutoff, time(23, 59, 59), tzinfo=UTC)
        try:
            mode = HistoricalViewMode(request.mode)
        except ValueError:
            mode = HistoricalViewMode.AS_KNOWN
        features = deduped = earnings_updated = warnings = failed = 0
        errors: list[dict[str, Any]] = []
        for company in companies:
            try:
                company_features: list[CeriRevisionFeature] = []
                for metric in (metric.value for metric in self.config.metrics.required):
                    calculated = self.revisions.calculate_windows(
                        db, company_id=company.id, metric=metric, cutoff_at=cutoff_at, mode=mode
                    )
                    for feature in calculated:
                        existing = self._existing_feature(db, feature)
                        if existing is not None:
                            company_features.append(existing)
                            deduped += 1
                            continue
                        db.add(feature)
                        db.flush()
                        company_features.append(feature)
                        features += 1
                        warnings += len(feature.warnings_json or [])
                    self._add_acceleration(calculated, company_features)
                estimates = _load(db, CeriEstimateSnapshot)
                earnings = [
                    row for row in _load(db, CeriEarningsActual) if row.company_id == company.id
                ]
                if earnings:
                    self.surprise.summarize(earnings, estimates)
                    for row in earnings:
                        if row.consensus_snapshot_id is not None:
                            earnings_updated += 1
                if processing_run is not None:
                    processing_run.checkpoint_json = {
                        "company_id": company.id,
                        "as_of_session": cutoff.isoformat(),
                    }
            except Exception as exc:
                failed += 1
                errors.append(
                    {"company_id": company.id, "error": str(exc).replace("\n", " ")[:500]}
                )
        return CeriFeatureRebuildResult(
            features, deduped, earnings_updated, len(companies), warnings, failed, tuple(errors)
        )

    def _companies(self, db: Session, request: CeriFeatureRebuildRequest) -> list[CeriCompany]:
        requested_ids = set(request.company_ids or ())
        companies = _load(db, CeriCompany)
        if request.run_id is not None:
            tickers = {
                row.ticker.upper()
                for row in _load(db, RawCompanyRow)
                if row.run_id == request.run_id
            }
            companies = [company for company in companies if company.ticker.upper() in tickers]
        if request.ticker:
            companies = [
                company for company in companies if company.ticker.upper() == request.ticker.upper()
            ]
        if requested_ids:
            companies = [company for company in companies if company.id in requested_ids]
        return sorted(companies, key=lambda company: (company.ticker.upper(), company.id or 0))

    def _existing_feature(
        self, db: Session, feature: CeriRevisionFeature
    ) -> CeriRevisionFeature | None:
        for existing in _load(db, CeriRevisionFeature):
            if (
                existing.company_id == feature.company_id
                and existing.metric == feature.metric
                and existing.period_key == feature.period_key
                and existing.as_of_session == feature.as_of_session
                and existing.window_days == feature.window_days
                and existing.config_hash == feature.config_hash
                and existing.calculation_version == feature.calculation_version
            ):
                return existing
        return None

    def _add_acceleration(
        self, calculated: list[CeriRevisionFeature], all_features: list[CeriRevisionFeature]
    ) -> None:
        if len(calculated) < 2:
            return
        recent = min(calculated, key=lambda feature: feature.window_days)
        longer = max(calculated, key=lambda feature: feature.window_days)
        if recent is not longer:
            # The feature may already be persisted; mutating it only changes a
            # derived value and its evidence hash, never the source evidence.
            self.revisions.with_acceleration(recent, longer)


def _load(db: Session, model: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)
