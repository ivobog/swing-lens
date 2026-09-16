"""Capture representative core business values in a checkout (baseline or current).

Run with the repository as cwd and --output pointing to an ignored QA artifact.
Only configuration lineage metadata is excluded; scores, decisions, explanations,
warnings and readiness-related fields remain in the byte-for-byte comparison.
"""

import argparse
import importlib.util
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / "tests")]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from test_combined_ranking_identity_adoption import FakeDb  # noqa: E402
from test_fundamental_ranker_v2 import _quality_values  # noqa: E402
from test_fundamental_ranker_v2 import _row as fundamental_row  # noqa: E402
from test_ranking_profile_engine import _fundamental, _row, _technical  # noqa: E402

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical  # noqa: E402
from app.services.combined_decision import _load_scoring_config, combine_row_decision  # noqa: E402
from app.services.core_calculation_evidence import calculation_evidence_payload  # noqa: E402
from app.services.fundamental_ranker_v2 import (  # noqa: E402
    load_fundamentals_v2_config,
    score_rows_v2,
)
from app.services.pine_replica_engine import score_from_feature_result  # noqa: E402
from app.services.ranking_profile_config import get_ranking_profile  # noqa: E402
from app.services.ranking_profile_engine import rank_single_row  # noqa: E402
from app.services.technical_indicators import (  # noqa: E402
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
    load_pine_defaults,
)
from app.services.technical_score_service import finalize_technical_scores  # noqa: E402
from app.services.technical_scoring_config import load_technical_scoring_v4_config  # noqa: E402
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config  # noqa: E402
from app.settings import Settings  # noqa: E402


def capture():
    adopted = importlib.util.find_spec("app.services.core_effective_configuration") is not None
    if adopted:
        from app.services.core_effective_configuration import (
            resolve_combined_configuration,
            resolve_fundamental_configuration,
            resolve_ranking_configuration,
            resolve_technical_configuration,
        )
    result = {}
    rows = [fundamental_row("QUALITY", _quality_values()), fundamental_row("SPARSE", {})]
    if adopted:
        fundamentals = score_rows_v2(rows, config=resolve_fundamental_configuration().values)
    else:
        fundamentals = score_rows_v2(rows)
    result["fundamental"] = [asdict(item) for item in fundamentals]
    assert load_fundamentals_v2_config().data  # native authority exists in both checkouts
    pine, v4, v5 = (
        load_pine_defaults(),
        load_technical_scoring_v4_config(),
        load_technical_scoring_v5_config(),
    )
    settings = Settings(
        _env_file=None, technical_v5_enabled=False, technical_v5_shadow_compare_enabled=False
    )
    frozen = (
        resolve_technical_configuration(pine=pine, v4=v4, v5=v5, settings=settings)
        if adopted
        else None
    )
    if frozen:
        values = frozen.values
        pine, v4, v5 = values["pine"], values["v4"], values["v5"]
    technicals = {}
    for name, count in (("supported", 1600), ("insufficient", 35)):
        close = np.linspace(100, 180, count)
        frame = pd.DataFrame(
            dict(
                date=pd.bdate_range(end="2026-09-15", periods=count),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=np.full(count, 1_000_000),
            )
        )
        feature = calculate_technical_features(frame, ticker="ACME", params=pine, v4_params=v4)
        base = score_from_feature_result(
            feature,
            params=pine,
            v4_params=v4,
            htf_features=calculate_htf_trend_features(
                frame, params=pine, latest_completed_session=date(2026, 9, 15)
            ),
            relative_strength_features=calculate_relative_strength_features(
                frame, frame, frame, params=pine
            ),
            market_features=feature.latest,
            qqq_market_features=feature.latest,
        )
        kwargs = {"effective_configuration": frozen} if adopted else {}
        row = finalize_technical_scores(
            FakeDb(),
            7,
            [base],
            v4_params=v4,
            v5_params=v5,
            settings=settings,
            persist=False,
            **kwargs,
        )[0]
        technicals[name] = calculation_evidence_payload(row)
    result["technical"] = technicals
    config = _load_scoring_config()
    combined_config = resolve_combined_configuration(config).values if adopted else config
    combined, rankings = {}, {}
    for name, fundamental, technical in (
        (
            "ready",
            _fundamental("ACME", 7),
            _technical("ACME", trend=7, momentum=7, setup=7, risk=2, rs=7),
        ),
        ("missing", None, None),
    ):
        decision = asdict(
            combine_row_decision(
                _row("ACME"),
                fundamental,
                technical,
                config=combined_config,
                today=date(2026, 9, 16),
            )
        )
        decision["debug_evidence"].pop("config_snapshot", None)
        decision["debug_evidence"].pop("config_hash", None)
        combined[name] = decision
        for profile_name in (
            "momentum_swing",
            "quality_momentum",
            "early_rocket",
            "clean_compounder_pullback",
            "defensive_quality",
        ):
            profile = get_ranking_profile(profile_name)
            ranking_config = config
            if adopted:
                snapshot = resolve_ranking_configuration(profile, config)
                profile, ranking_config = snapshot.ranking_profile(), snapshot.values
            rankings[f"{name}:{profile_name}"] = asdict(
                rank_single_row(
                    profile=profile,
                    row=_row("ACME"),
                    fundamental=fundamental,
                    technical=technical,
                    config=ranking_config,
                    today=date(2026, 9, 16),
                )
            )
    result.update(combined=combined, ranking=rankings)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = capture()
    args.output.write_bytes(Canonical.bytes(payload))
    print("Core business capture SHA-256:", Canonical.fingerprint(payload))
