from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.external
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("module", "test_name"),
    [
        ("liquidity", "external_ib_bid_ask"),
        ("short-pressure", "external_ib_fee_rate"),
        ("volatility", "external_ib_volatility"),
        ("options", "external_ib_generic_ticks"),
        ("scanner", "external_ib_scanner"),
        ("histogram", "external_ib_histogram"),
        ("flex", "external_ib_flex"),
    ],
)
def test_external_ib_market_intelligence(module: str, test_name: str) -> None:
    if os.environ.get("SWINGLENS_RUN_EXTERNAL_IBMI", "").lower() not in {"1", "true", "yes"}:
        pytest.skip(f"{test_name} requires SWINGLENS_RUN_EXTERNAL_IBMI=true")
    command = [
        sys.executable,
        "scripts/validate_ib_market_intelligence.py",
        "--module",
        module,
        "--tickers",
        os.environ.get("SWINGLENS_EXTERNAL_IBMI_TICKERS", "AAPL"),
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
