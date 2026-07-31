from __future__ import annotations

from datetime import UTC, date, datetime

from _phase3_helpers import FakeWinnerRepository, build_run_context

from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.episode_service import WinnerEpisodeService
from app.services.winner_probability.feature_extractor import WinnerFeatureExtractor


def test_consecutive_same_ticker_setup_signals_share_episode() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository()
    service = WinnerEpisodeService(repository)
    extractor = WinnerFeatureExtractor()
    first_context = build_run_context(as_of_date=date(2026, 7, 31))
    second_context = build_run_context(as_of_date=date(2026, 8, 3))

    first = extractor.extract(
        first_context,
        first_context.tickers[0],
        config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )
    second = extractor.extract(
        second_context,
        second_context.tickers[0],
        config,
        captured_at=datetime(2026, 8, 3, 21, 30, tzinfo=UTC),
    )

    first_assignment = service.assign_episode(object(), first, config)
    second_assignment = service.assign_episode(object(), second, config)

    assert first_assignment.is_dependent is False
    assert second_assignment.is_dependent is True
    assert second_assignment.episode.id == first_assignment.episode.id


def test_signal_after_cooldown_creates_new_episode() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository()
    service = WinnerEpisodeService(repository)
    extractor = WinnerFeatureExtractor()
    first_context = build_run_context(as_of_date=date(2026, 7, 31))
    later_context = build_run_context(as_of_date=date(2026, 8, 14))

    first = extractor.extract(
        first_context,
        first_context.tickers[0],
        config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )
    later = extractor.extract(
        later_context,
        later_context.tickers[0],
        config,
        captured_at=datetime(2026, 8, 14, 21, 30, tzinfo=UTC),
    )

    first_assignment = service.assign_episode(object(), first, config)
    later_assignment = service.assign_episode(object(), later, config)

    assert later_assignment.is_dependent is False
    assert later_assignment.episode.id != first_assignment.episode.id
