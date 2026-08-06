from argparse import Namespace

import pytest

from scripts.qa.run_m05_soak import _soak_mode, _validate_args


def _args(**overrides):
    values = {
        "duration_hours": 8.0,
        "interval_seconds": 900.0,
        "max_cycles": None,
        "tickers": 50,
        "bars": 756,
        "http_iterations": 1,
        "admin_url": "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
    }
    values.update(overrides)
    return Namespace(**values)


def test_m05_soak_distinguishes_release_duration_from_shakedown() -> None:
    assert _soak_mode(_args()) == "RELEASE_SOAK"
    assert _soak_mode(_args(duration_hours=0.02, max_cycles=2)) == "SHAKEDOWN"
    _validate_args(_args())


def test_m05_soak_rejects_remote_or_unsafe_bounds() -> None:
    with pytest.raises(ValueError, match="localhost"):
        _validate_args(
            _args(admin_url="postgresql://postgres:postgres@db.example.com:5432/postgres")
        )
    with pytest.raises(ValueError, match="between 1 and 250"):
        _validate_args(_args(tickers=1000))
    with pytest.raises(ValueError, match="between 0.01 and 24"):
        _validate_args(_args(duration_hours=0.001))
