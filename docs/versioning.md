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

## Fundamental model v2.2 compatibility registration

`fundamentals_v2.2` changes only the forward-quality formula (8% outer weight).
The repository profile now selects v2.2; retained v2.1 configurations continue to
select the original formula and reproduce their original configuration hash.
Unsupported model versions fail closed.

The certified distinct-field counts are v2.1=79 and v2.2=76. v2.1 retains admission
policy v3 and its original frozen readiness configuration. v2.2 registers admission
policy v4 with the same 65%/50% thresholds and all existing investment, integrity,
and consumer requirements. Retained Core configuration determines the matching
registration, and shadow ledger metadata uses the evaluated policy version.

No historical score/evidence is migrated, invalidated, or backfilled. Returning new
calculations to v2.1 requires the retained v2.1 profile and matching frozen authority;
relabeling a v2.2 configuration as v2.1 is rejected. This is separate from release
authorization: protected golden source pins require the existing authenticated
update/independent derivation workflow before certification or promotion.
