from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models.tables import WinnerPredictionEpisode
from app.services.us_market_calendar import nth_us_trading_day_from_entry
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.services.winner_probability.feature_extractor import ExtractedPredictionFeatures
from app.services.winner_probability.repository import WinnerProbabilityRepository


@dataclass(frozen=True)
class EpisodeAssignment:
    episode: WinnerPredictionEpisode
    is_dependent: bool


class WinnerEpisodeService:
    def __init__(self, repository: WinnerProbabilityRepository | None = None) -> None:
        self.repository = repository or WinnerProbabilityRepository()

    def assign_episode(
        self,
        db: Session,
        features: ExtractedPredictionFeatures,
        config: WinnerProbabilityConfig,
    ) -> EpisodeAssignment:
        setup_family = str(features.feature_json.get("setup_family") or "unknown")
        trigger_state = str(features.feature_json.get("trigger_state") or "unknown")
        dependency_hash = _dependency_group_hash(
            features.ticker,
            setup_family,
            trigger_state,
        )
        active_episode = self.repository.get_active_episode(
            db,
            dependency_group_hash=dependency_hash,
            signal_date=features.prediction_as_of_date,
        )
        if active_episode is not None:
            return EpisodeAssignment(episode=active_episode, is_dependent=True)

        episode_key = _episode_key(
            ticker=features.ticker,
            setup_family=setup_family,
            trigger_state=trigger_state,
            start_date=features.prediction_as_of_date,
        )
        existing = self.repository.get_episode_by_key(db, episode_key)
        if existing is not None:
            return EpisodeAssignment(episode=existing, is_dependent=True)

        ends_on = _episode_end(features.prediction_as_of_date, config.episode.cooldown_sessions)
        episode = WinnerPredictionEpisode(
            ticker=features.ticker,
            setup_family=setup_family,
            trigger_state=trigger_state,
            episode_key=episode_key,
            starts_on=features.prediction_as_of_date,
            ends_on=ends_on,
            cooldown_sessions=config.episode.cooldown_sessions,
            dependency_group_hash=dependency_hash,
        )
        self.repository.add(db, episode)
        return EpisodeAssignment(episode=episode, is_dependent=False)


def _episode_key(
    *,
    ticker: str,
    setup_family: str,
    trigger_state: str,
    start_date: date,
) -> str:
    return "|".join(
        [
            ticker.upper(),
            setup_family.casefold(),
            trigger_state.casefold(),
            start_date.isoformat(),
        ]
    )


def _episode_end(start_date: date, cooldown_sessions: int) -> date:
    try:
        return nth_us_trading_day_from_entry(start_date, cooldown_sessions)
    except ValueError:
        return start_date


def _dependency_group_hash(ticker: str, setup_family: str, trigger_state: str) -> str:
    payload = f"{ticker.upper()}|{setup_family.casefold()}|{trigger_state.casefold()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
