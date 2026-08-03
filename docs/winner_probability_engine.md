# Winner Probability Engine Maintainer Notes

The Outcome-Calibrated Winner Probability Engine (OWPE) estimates decision-time probabilities from
historical, point-in-time evidence. It is disabled by default and remains decision support only.

## Inputs

- Feature and model config in `config/winner_probability.yaml`.
- Decision-time feature extraction in `app/services/winner_probability/`.
- Setup lifecycle, ranking, market, sector, technical, and CERI context where configured.
- Model artifacts and hashes persisted in winner-probability tables.

## Versioning

Bump feature schema, calculation version, model artifact schema/hash, or export schema when feature
definitions, outcome definitions, model algorithms, calibration/drift gates, evidence manifests, or
export meanings change.

## Validation

```powershell
uv run pytest tests/winner_probability -q
uv run pytest tests/test_golden_pipeline.py -q
```

## Review Rules

- Preserve point-in-time cutoffs and source hashes.
- Model promotion requires quantitative gate evidence, artifact hash, and rollback/fallback path.
- Do not expose admin/model routes unless feature flags and local-admin protections are correct.
- Export manifests must include enough metadata to reproduce or explain mismatches.
