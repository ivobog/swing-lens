# Fundamental Scoring Maintainer Notes

Fundamental scoring converts mapped CSV fundamentals into durable `fundamental_scores` rows. Raw
upload values remain preserved on `raw_company_rows.raw_json`.

## Inputs

- Uploaded CSV rows mapped through `config/column_aliases.yaml`.
- Numeric parsing from `app/services/numeric_parser.py`.
- Fundamental score logic in `app/services/fundamental_ranker_v2.py`.

## Versioning

Bump the fundamental scoring version when formulas, thresholds, labels, component meanings, warning
flags, or parsed field semantics change. Update golden fixtures and release notes in the same PR.

## Validation

```powershell
uv run pytest tests/test_fundamental_ranker.py tests/test_fundamental_ranker_v2.py -q
uv run pytest tests/test_fundamental_components_v2.py tests/test_fundamentals_v2_acceptance.py -q
uv run pytest tests/test_golden_pipeline.py -q
```

## Review Rules

- Keep raw uploaded evidence unchanged.
- Document formula changes in this file and `docs/versioning.md` if policy changes.
- Treat changes that alter score order or labels as model/scoring changes.
- Do not backfill historical scores without an explicit, auditable rebuild path.
