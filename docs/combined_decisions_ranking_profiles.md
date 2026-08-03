# Combined Decisions and Ranking Profiles Maintainer Notes

Combined decisions synthesize fundamental and technical evidence into run-level research labels.
Ranking profiles provide named views over the same evidence without placing broker orders.

## Inputs

- Fundamental scores from upload processing.
- Technical scores from cached bar analysis.
- Ranking profile config in `config/ranking_profiles.yaml`.
- Combined-decision logic in `app/services/combined_decision.py`.
- Ranking logic in `app/services/ranking_profile_service.py`.

## Versioning

Bump config/export schema versions when labels, gates, warning flags, profile weights, sort order,
position-size hints, or export meanings change.

## Validation

```powershell
uv run pytest tests/test_combined_decision.py tests/test_ranking_profile_config.py -q
uv run pytest tests/test_ranking_profile_service.py tests/test_ranking_profile_routes.py -q
uv run pytest tests/test_ranking_profiles_golden.py tests/test_golden_pipeline.py -q
```

## Review Rules

- Market regime and sector rotation are advisory overlays; do not silently mutate persisted ranking
  evidence.
- Preserve warning flags and evidence fields in exports.
- Treat any label/gate/order change as high-risk scoring behavior.
