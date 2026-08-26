# CERI Freshness Configuration Audit

## Baseline resolution

The effective baseline loaded `config/ceri.yaml` with hash `d7686bfd...d5c2b`, version `2026-08-14-changes-alerts-remediation-r1`, calculation `ceri-1.2.0`.

| Dataset | Effective max stale days | Baseline consumers |
|---|---:|---|
| estimates | 7 | Ops; ticker evidence API; confidence (but incorrectly applied to worst dataset) |
| catalysts | 2 | Ops; ticker evidence API |
| earnings | 30 | Ops; ticker evidence API |
| guidance | 14 | Ops; ticker evidence API |

No database config table or feature flag overrides dataset thresholds. `.env` controls the runtime CERI feature flags and declares `CERI_CONFIG_PATH`; however, pre-fix `load_ceri_config()` used a default Python constant and ignored the settings path when called without arguments. The current path happened to match, so baseline numeric values were not altered, but the resolution contract was defective.

## Remediated resolution

- `load_ceri_config()` lazily resolves `Settings.ceri_config_path` and `Settings.ceri_taxonomy_path` unless explicit paths are supplied.
- Semantic version: `ceri-1.3.0`.
- Config version: `2026-08-26-freshness-semantics-r1`.
- Config hash: `c9f0043f2ea5519a923faa01b901071d6817290fa5fdf4532e9cc1f2b88dfa29`.
- Dataset thresholds remain 7/2/30/14; no threshold was increased or suppressed.
- Provider feed age for both Ops and scoring uses the same `freshness_age_days` function and `America/New_York` date boundary.
- Confidence uses only estimates feed age against the estimates threshold.
- Evidence observation/retrieval ages are reported separately and are not silently substituted into feed health.

Classification: `CONFIG_RESOLUTION_BUG`, Medium severity, no baseline production-value impact, fixed without migration.
