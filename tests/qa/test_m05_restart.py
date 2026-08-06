from argparse import Namespace

import pytest

from scripts.qa.run_m05_restart import (
    CONTAINER_PREFIX,
    _free_local_port,
    _validate_args,
)


def test_m05_restart_requires_release_scale_and_scoped_container_name() -> None:
    assert CONTAINER_PREFIX == "swinglens-qa-m05-restart-"
    _validate_args(Namespace(tickers=250, bars=756, postgres_image="postgres:16"))

    with pytest.raises(ValueError, match="exactly 250"):
        _validate_args(Namespace(tickers=50, bars=756, postgres_image="postgres:16"))
    with pytest.raises(ValueError, match="official postgres"):
        _validate_args(Namespace(tickers=250, bars=756, postgres_image="vendor/postgres:16"))


def test_m05_restart_allocates_only_a_local_ephemeral_port() -> None:
    port = _free_local_port()

    assert 1 <= port <= 65535
