# Technical Scoring and Pine Parity Maintainer Notes

Technical scoring computes Pine-compatible OHLCV features and decision-time technical labels. It is
advisory research output, not a trading instruction.

## Inputs

- Cached daily OHLCV bars from Interactive Brokers.
- Pine defaults in `config/pine_defaults.yaml`.
- Technical scoring config in `config/technical_scoring_v4.yaml`.
- Indicator code in `app/services/technical_indicators.py`.

## Versioning

Bump technical engine/config versions when indicator formulas, readiness requirements, Pine parity
behavior, weekly aggregation, stop/target logic, confidence labels, or export columns change.

## Validation

```powershell
uv run pytest tests/test_technical_indicators.py tests/test_technical_score_v4.py -q
uv run pytest tests/test_adaptive_technical_features.py tests/test_technical_confidence.py -q
uv run pytest tests/test_golden_pipeline.py -q
```

## Review Rules

- Preserve point-in-time behavior for historical runs.
- Keep missing/insufficient data explicit instead of failing a whole run.
- Update chart payload docs and route/export inventory when output contracts change.
- Verify Pine parity before accepting formula changes.
