# Versioning Policy

SwingLens keeps several version streams because historical research outputs must remain
reproducible.

| Artifact | Version Source | Bump Rule |
| --- | --- | --- |
| Application | `pyproject.toml`, `app/main.py` | Bump for releases; keep values aligned |
| Database schema | Alembic revision | New migration for each persisted schema change |
| Fundamental scoring | scoring/model version fields and config | Bump on formula, threshold, label, weight, or field meaning change |
| Technical/Pine scoring | technical engine/config version | Bump on indicator, readiness, parameter, or Pine-parity contract change |
| Combined decisions/ranking | ranking config/export schema | Bump when gates, labels, weights, sorting, or output columns change |
| Market regime | calculation version, config version, config hash | Bump logic version for formulas; config version for policy contract |
| Sector rotation | calculation version, config version, config hash | Same rules as market regime |
| Setup lifecycle | engine version, schema version, config version/hash | Bump schema for payload contract, engine for lifecycle logic |
| Winner probability | feature schema, calculation version, artifact schema/hash | Bump on feature, outcome, estimate, model, or artifact semantic changes |
| CERI | calculation/config/provider terms/export versions | Bump for scoring, provider policy, redaction, purge, or export semantics |
| Exports | export schema version | Bump when columns, meanings, evidence mode, or redaction semantics change |

## Required Review Evidence

- Golden fixtures updated when score/ranking/probability outputs change.
- Route/export inventory updated when API or download paths change.
- Changelog entry added for user-visible, operational, schema, or model-governance changes.
- ADR added or updated for durable architecture, safety, retention, or provider-policy changes.
- Rollback or restore path documented for migrations and destructive lifecycle changes.
