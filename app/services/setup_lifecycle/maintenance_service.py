from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select

from app.models.tables import SetupLifecycleEpisode, SetupLifecycleEvent, SetupSignalSnapshot
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.change_detector import SetupLifecycleChangeDetector
from app.services.setup_lifecycle.enums import SetupFamily
from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
from app.services.setup_lifecycle.repository import SetupLifecycleRepository


@dataclass(frozen=True)
class SetupLifecycleMaintenanceResult:
    status: str = "COMPLETED"
    aged: int = 0
    expired: int = 0
    repaired: int = 0
    alerts_created: int = 0
    alerts_suppressed: int = 0
    skipped: int = 0
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "aged": self.aged,
            "expired": self.expired,
            "repaired": self.repaired,
            "alerts_created": self.alerts_created,
            "alerts_suppressed": self.alerts_suppressed,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
        }


class SetupLifecycleMaintenanceService:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        episode_service: SetupLifecycleEpisodeService | None = None,
        alert_service: SetupLifecycleAlertService | None = None,
        change_detector: SetupLifecycleChangeDetector | None = None,
    ) -> None:
        self.repository = repository or SetupLifecycleRepository()
        self.episode_service = episode_service or SetupLifecycleEpisodeService(
            repository=self.repository
        )
        self.alert_service = alert_service or SetupLifecycleAlertService(
            repository=self.repository
        )
        self.change_detector = change_detector or SetupLifecycleChangeDetector(
            repository=self.repository
        )

    def daily_maintenance(
        self,
        db,
        *,
        as_of_date: date,
        market_session_completed: bool = True,
        evaluation_run_id: int | None = None,
    ) -> SetupLifecycleMaintenanceResult:
        if not market_session_completed:
            return SetupLifecycleMaintenanceResult(
                status="SKIPPED",
                skipped=1,
                warnings=("MARKET_SESSION_NOT_COMPLETED",),
            )
        aged = 0
        expired = 0
        warnings: list[str] = []
        for episode in self._active_episodes(db):
            try:
                family = SetupFamily(episode.setup_family)
            except ValueError:
                warnings.append(f"UNKNOWN_SETUP_FAMILY:{episode.setup_family}")
                continue
            result = self.episode_service.apply_observation_gap(
                db,
                ticker=episode.ticker,
                timeframe=episode.timeframe,
                setup_family=family,
                observed_on=as_of_date,
                evaluation_run_id=evaluation_run_id,
            )
            if result.updated:
                aged += 1
            if result.closed:
                expired += 1
        return SetupLifecycleMaintenanceResult(
            aged=aged,
            expired=expired,
            warnings=tuple(warnings),
        )

    def repair_ticker(
        self,
        db,
        *,
        ticker: str,
        as_of_date: date | None = None,
        setup_family: str | None = None,
        evaluation_run_id: int | None = None,
    ) -> SetupLifecycleMaintenanceResult:
        repaired = 0
        alerts_created = 0
        alerts_suppressed = 0
        for snapshot in self._repair_snapshots(db, ticker=ticker, as_of_date=as_of_date):
            result = self.episode_service.apply_snapshot(
                db,
                snapshot,
                evaluation_run_id=evaluation_run_id,
            )
            if setup_family is not None and result.decision.setup_family.value != setup_family:
                continue
            repaired += int(result.updated or result.opened or result.closed)
            alert_result = self.alert_service.evaluate_episode_result(
                db,
                result,
                evaluation_run_id=evaluation_run_id,
            )
            alerts_created += alert_result.created
            alerts_suppressed += alert_result.suppressed
        return SetupLifecycleMaintenanceResult(
            repaired=repaired,
            alerts_created=alerts_created,
            alerts_suppressed=alerts_suppressed,
        )

    def rebuild_alerts(
        self,
        db,
        *,
        ticker: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> SetupLifecycleMaintenanceResult:
        self.alert_service.seed_builtin_rules(db)
        lifecycle = self._lifecycle_events(
            db,
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
        )
        changes = self._signal_change_events(
            db,
            ticker=ticker,
            date_from=date_from,
            date_to=date_to,
        )
        created = 0
        suppressed = 0
        for event in lifecycle:
            result = self.alert_service.evaluate_lifecycle_event(db, event)
            created += result.created
            suppressed += result.suppressed
        result = self.alert_service.evaluate_signal_change_events(db, changes)
        created += result.created
        suppressed += result.suppressed
        return SetupLifecycleMaintenanceResult(
            alerts_created=created,
            alerts_suppressed=suppressed,
        )

    def _active_episodes(self, db) -> list[SetupLifecycleEpisode]:
        return list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(SetupLifecycleEpisode.status == "ACTIVE")
                .order_by(SetupLifecycleEpisode.ticker, SetupLifecycleEpisode.id)
            )
        )

    def _repair_snapshots(
        self,
        db,
        *,
        ticker: str,
        as_of_date: date | None,
    ) -> list[SetupSignalSnapshot]:
        statement = (
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == self.repository.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.is_canonical.is_(True))
        )
        if as_of_date is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date == as_of_date)
        return list(
            db.scalars(
                statement.order_by(
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )

    def _lifecycle_events(
        self,
        db,
        *,
        ticker: str | None,
        date_from: date | None,
        date_to: date | None,
    ) -> list[SetupLifecycleEvent]:
        statement = select(SetupLifecycleEvent).where(
            SetupLifecycleEvent.is_current_version.is_(True)
        )
        if ticker:
            statement = statement.where(
                SetupLifecycleEvent.ticker == self.repository.normalize_ticker(ticker)
            )
        if date_from is not None:
            statement = statement.where(SetupLifecycleEvent.effective_date >= date_from)
        if date_to is not None:
            statement = statement.where(SetupLifecycleEvent.effective_date <= date_to)
        return list(db.scalars(statement.order_by(SetupLifecycleEvent.id)))

    def _signal_change_events(
        self,
        db,
        *,
        ticker: str | None,
        date_from: date | None,
        date_to: date | None,
    ):
        from app.models.tables import SignalChangeEvent

        statement = select(SignalChangeEvent)
        if ticker:
            statement = statement.where(
                SignalChangeEvent.ticker == self.repository.normalize_ticker(ticker)
            )
        if date_from is not None:
            statement = statement.where(SignalChangeEvent.effective_date >= date_from)
        if date_to is not None:
            statement = statement.where(SignalChangeEvent.effective_date <= date_to)
        return list(db.scalars(statement.order_by(SignalChangeEvent.id)))
