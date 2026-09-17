"""Fixed native calculator inputs for eight-domain before/after parity.

Run this script with either certified baseline or adoption checkout as cwd.
Native PostgreSQL writer tests separately certify persistence authority.
"""

import argparse
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / "tests"), str(Path.cwd() / "scripts/qa")]

from core_configuration_behavior_capture import capture  # noqa: E402
from test_market_regime_policy import _policy_for, _result  # noqa: E402
from test_sector_rotation_policy import _decide, _metrics  # noqa: E402

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical  # noqa: E402
from app.services.ceri.confidence_service import CeriConfidenceService  # noqa: E402
from app.services.ceri.event_risk_service import CeriEventRiskService  # noqa: E402
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService  # noqa: E402
from app.services.contextual_effective_configuration import (  # noqa: E402
    resolve_ceri_configuration,
    resolve_ibmi_configuration,
)
from app.services.ib_market_intelligence.calculations import calculate_liquidity  # noqa: E402
from app.services.ib_market_intelligence.config import (  # noqa: E402
    load_ib_market_intelligence_config,
)
from app.services.market_regime import (  # noqa: E402
    REGIME_BULL_TREND,
    REGIME_CHOPPY,
    REGIME_UNKNOWN,
)


def capture_all():
    result = capture()
    result["regime"] = [
        asdict(_policy_for(_result(name, score=score)))
        for name, score in ((REGIME_BULL_TREND, 9), (REGIME_CHOPPY, 5), (REGIME_UNKNOWN, 0))
    ]
    result["sector"] = [
        asdict(_decide(_metrics(score), market={"risk_state": state}))
        for score, state in ((8.2, "Green"), (5.6, "Yellow"), (None, "Gray"))
    ]
    config = resolve_ceri_configuration().ceri_config()
    as_of = date(2026, 9, 14)
    result["ceri"] = {
        "opportunity": asdict(CeriOpportunityScoreService(config).calculate(revision_features=[])),
        "event_risk": asdict(CeriEventRiskService(config).calculate(as_of_session=as_of)),
        "confidence": asdict(
            CeriConfidenceService(config).calculate(as_of_session=as_of, revision_features=[])
        ),
    }
    original = load_ib_market_intelligence_config()
    config = resolve_ibmi_configuration(original, "liquidity").ibmi_config(original)
    bars = [
        SimpleNamespace(
            session_date=as_of,
            open_value=100,
            close_value=100.05,
            data_hash="fixed-source-observation",
        )
    ]
    result["ibmi"] = [
        asdict(
            calculate_liquidity(
                inputs,
                as_of=as_of,
                config={**config.section("liquidity"), **config.section("freshness")},
            )
        )
        for inputs in (bars, [])
    ]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = capture_all()
    args.output.write_bytes(Canonical.bytes(payload))
    print(Canonical.fingerprint(payload))
