# SwingLens Phase 3 Configuration, Secrets, and Feature Flags

Review date: 2026-08-02
Phase 0 baseline: `docs/review/phase0_baseline.md`
Phase 1 traceability: `docs/review/phase1_requirements_traceability.md`
Review target commit: `0a53f5761c4356fbf32f448eeeb0a2d4bd4bd685`

## Objective

Phase 3 verifies that runtime settings, YAML configuration, secrets, feature flags, and provider
controls are validated, deterministic, secure, and observable. Special attention is paid to
score-producing configuration and flags that can silently change output.

## Evidence Log

Inspected configuration surfaces:

- `app/settings.py`
- `.env.example`
- `.gitignore`
- all `config/*.yaml`
- config loaders under `app/services`
- provider and export policy code under `app/services/ceri`
- feature-flag usage in `app/services/pipeline_service.py`, `app/services/pipeline_executor.py`,
  routers, templates, and tests
- SQLAlchemy model fields for `config_hash`, `config_version`, `calculation_version`,
  `parameters_json`, and model versions

Targeted command evidence:

| Command | Result | Notes |
|---|---:|---|
| Settings invalid-value construction experiment | Accepted invalid values | Negative ports/delays/page sizes and contradictory flags were accepted |
| `rg "EngineParameters|parameters_json|engine_parameters" app tests` | Model only | Table exists, no service writes found |
| `pytest tests/test_settings.py tests/test_config_files.py tests/test_technical_scoring_config.py tests/test_ranking_profile_config.py tests/test_sector_rotation_config.py tests/test_market_regime_policy.py tests/setup_lifecycle/test_setup_lifecycle_config.py tests/winner_probability/test_config.py tests/ceri/test_ceri_config.py tests/ceri/test_ceri_export_service.py tests/ceri/test_provider_protocol.py tests/ceri/test_ceri_provider_contracts_phase10.py -q` | Passed | `86 passed in 5.44s` |

## Configuration Inventory

| File | Loader/validation maturity | Hash/version lineage |
|---|---|---|
| `.env.example` / `app/settings.py` | Pydantic types only; no range or cross-field validation | No runtime settings hash |
| `config/column_aliases.yaml` | Shape/coverage tests; no dedicated schema loader | Raw rows preserve source fields; no config hash |
| `config/pine_defaults.yaml` | Required-section tests; loaded into technical flow indirectly | Pine/Python versions exist, but no active `EngineParameters` writes found |
| `config/scoring_weights.yaml` | Shape/sum tests only; `_load_scoring_config` returns raw dict | No config hash/model version persisted for combined decisions |
| `config/fundamentals_v2.yaml` | Shape tests; loader returns raw dict | `model_version` stored in fundamental debug/model fields; no full config hash |
| `config/technical_scoring_v4.yaml` | Defaults merge plus regime-weight validation | Engine version in debug; no full config hash seen for technical rows |
| `config/ranking_profiles.yaml` | Dedicated parser with weights, thresholds, unknown component checks | Ranking debug includes engine version; no per-profile config hash |
| `config/market_regime_command_center.yaml` | Dedicated parser validates policy matrix and risk states | `calculation_version` and `config_version` persisted |
| `config/sector_rotation.yaml` | Dedicated parser validates weights, taxonomy, permissions, thresholds | `config_hash` persisted on snapshots |
| `config/setup_lifecycle.yaml` | Strong typed parser, cross-section guard rails, hash stability tests | `config_hash` persisted throughout SLSE artifacts |
| `config/winner_probability.yaml` | Strong typed parser, cross-section guard rails, hash stability tests | `config_hash` persisted on OWPE artifacts |
| `config/ceri.yaml` | Strong typed parser, provider/export/purge/retention guard rails, hash stability tests | `config_hash` and `config_version` persisted on CERI artifacts |
| `config/ceri_catalyst_taxonomy.yaml` | Dedicated taxonomy parser with transition/category checks | Taxonomy hash via CERI config hash helper |

## Feature Flag Matrix

Runtime flags from `Settings`:

| Flag family | Parent flag | Child/action flags | Current behavior |
|---|---|---|---|
| Durable pipeline | `use_durable_pipeline` | n/a | Controls route behavior for full pipeline start |
| Worker | `job_worker_enabled` | n/a | Starts embedded worker in FastAPI lifespan |
| Winner probability | `winner_probability_enabled` | `winner_probability_capture_in_pipeline`, `winner_probability_admin_enabled` | Admin/capture can be enabled while parent is false |
| Setup lifecycle | `setup_lifecycle_enabled` | pipeline, alerts, replay, reconstruction, purge flags | Child flags can be enabled while parent is false |
| CERI | `ceri_enabled` | provider ingest, run capture, UI, alerts, admin, backfill | Child flags can be enabled while parent is false |

Pipeline-specific evidence:

- `pipeline_step_names` uses `ceri_run_capture_enabled` directly, not `ceri_enabled`.
- `pipeline_step_names` uses `setup_lifecycle_pipeline_step_enabled` directly, not
  `setup_lifecycle_enabled`.
- Winner prediction capture is always in the base pipeline step list, and executor skips capture
  when `_winner_probability_capture_enabled` is false.

This staged rollout may be intentional, but it is not encoded as a validated compatibility contract.

## Secret and Provider Controls

Positive evidence:

- `.env` is ignored by `.gitignore`.
- CERI primary provider reads `CERI_PRIMARY_PROVIDER_API_KEY` from the environment and does not
  store the key in safe metadata.
- CERI primary provider reports `credentials_missing` when no key is configured.
- Live primary-provider fetches raise until a licensed adapter implementation exists.
- CERI export policy masks configured restricted fields and recursively redacts sensitive field
  names, bearer tokens, local filesystem paths, SQL details, raw payloads, source URLs, and provider
  secrets.
- CERI config rejects provider-license purge enabled by default and validates preview,
  confirmation, and audit requirements.

Residual gaps:

- Database URL in `.env.example` contains a default password. It is local-only and conventional for
  dev, but still a credential-shaped value displayed in docs/settings contexts.
- General non-CERI logs/error paths were not fully audited in Phase 3.
- Runtime settings are not redacted or hashed as a whole.

## Findings Register

ID: PH3-001
Title: Runtime settings accept invalid and unsafe values
Severity: S1 High
Confidence: Confirmed
Affected components: `app/settings.py`, app startup, IB fetch pacing, uploads, pagination, worker
Evidence: A construction experiment accepted `app_port=-1`, `max_upload_size_mb=-5`,
`ib_port=-4002`, negative IB timeout/delay/backoff/retry values, negative worker stale time, zero or
negative page sizes, and `app_host='0.0.0.0'` with `debug=True`.
Reproduction steps: Instantiate `Settings(_env_file=None, ...)` with the invalid values listed in
the evidence log.
Expected behavior: Invalid high-risk runtime settings fail fast with actionable errors.
Observed behavior: Pydantic type coercion accepts values that are nonsensical or unsafe.
Impact: Misconfiguration can break startup/runtime behavior, disable pacing safeguards, produce bad
pagination, bypass upload-size intent, or expose debug behavior beyond localhost.
Root cause or likely cause: `Settings` uses plain field types without `Field` bounds or Pydantic
model validators.
Recommended remediation: Add explicit bounds and cross-field validators for ports, sizes, delays,
rate limits, retries, page sizes, worker timings, debug/public bind, and feature-flag dependencies.
Acceptance criteria: Invalid examples above raise `ValidationError`; valid `.env.example` still
loads; unsafe public binding with debug enabled fails or requires an explicit override.
Regression tests required: Negative and boundary tests in `tests/test_settings.py`.
Owner profile: Backend engineer
Dependencies: Decide whether public binding is ever supported.

ID: PH3-002
Title: Core score-producing YAML configs lack schema validation and full config-hash lineage
Severity: S1 High
Confidence: Strong
Affected components: `config/scoring_weights.yaml`, `config/fundamentals_v2.yaml`,
`config/pine_defaults.yaml`, `config/technical_scoring_v4.yaml`, combined/fundamental/technical
scoring services
Evidence: `_load_scoring_config` returns `yaml.safe_load` as a raw dict; fundamentals v2 loader
also returns a raw dict; tests mostly check required sections and sums. Later subsystems persist
`config_hash`, but core combined decisions and technical/fundamental scoring do not consistently
persist a full effective config hash. `EngineParameters` is modeled but no service writes were found.
Reproduction steps: Inspect `app/services/combined_decision.py:341-343`,
`app/services/fundamental_ranker_v2.py:105-110`, `app/services/technical_scoring_config.py`, and
`rg "EngineParameters|parameters_json|engine_parameters" app tests`.
Expected behavior: Every score-producing run records effective config/model versions and hashes; bad
weights, missing sections, unknown keys, contradictory gates, and malformed thresholds fail fast.
Observed behavior: Some old/core config paths rely on raw dict access and partial tests rather than a
schema object with hash lineage.
Impact: A configuration change can silently alter final scores, labels, position-size hints, or
technical behavior without durable reproduction evidence.
Root cause or likely cause: Newer advanced engines implemented dedicated config schemas; older core
engines predate that pattern.
Recommended remediation: Add typed config loaders and `*_config_hash` helpers for core scoring,
persist effective hashes/versions in `EngineParameters` or score/debug fields, and validate unknown
keys plus high-risk thresholds.
Acceptance criteria: Each core scoring artifact can identify the exact effective configuration and
model version used to produce it.
Regression tests required: Invalid config tests, hash stability tests, and lineage persistence tests.
Owner profile: Backend/quant engineer
Dependencies: Decide canonical run-level config snapshot storage.

ID: PH3-003
Title: Unknown environment variables and typos are silently ignored
Severity: S2 Medium
Confidence: Confirmed
Affected components: `app/settings.py`, local setup, operations
Evidence: `SettingsConfigDict(extra="ignore")` causes unknown keys in `.env` or runtime construction
to be ignored.
Reproduction steps: Inspect `app/settings.py` model config and instantiate `Settings` with a
misspelled field or place one in `.env`.
Expected behavior: Unknown high-risk settings fail fast or are reported at startup.
Observed behavior: Typos are accepted silently.
Impact: A user may believe a safety, DB, IB, or feature flag override is active when it is not.
Root cause or likely cause: Pydantic settings default chosen for compatibility with evolving `.env`.
Recommended remediation: Use `extra="forbid"` for runtime construction or add startup warnings for
unknown `.env` keys. If compatibility requires ignore, provide a settings self-check endpoint/log.
Acceptance criteria: Misspelled settings are visible in tests/startup diagnostics.
Regression tests required: Unknown env key test.
Owner profile: Backend engineer
Dependencies: Decide compatibility policy for legacy `.env` files.

ID: PH3-004
Title: Parent/child feature-flag compatibility is not validated
Severity: S2 Medium
Confidence: Confirmed
Affected components: `app/settings.py`, `pipeline_service`, `pipeline_executor`, CERI, SLSE, OWPE
Evidence: `Settings` accepted `ceri_admin_enabled=True` while `ceri_enabled=False`,
`winner_probability_admin_enabled=True` while `winner_probability_enabled=False`, and
`setup_lifecycle_pipeline_step_enabled=True` while `setup_lifecycle_enabled=False`. Pipeline step
construction reads child pipeline flags directly.
Reproduction steps: Instantiate `Settings(_env_file=None, ...)` with the contradictory combinations
above; inspect `pipeline_step_names`.
Expected behavior: Valid staged combinations are documented and enforced; invalid combinations fail
fast with clear messages.
Observed behavior: Any combination is accepted by settings, and some child flags activate routes or
pipeline steps independently of parent flags.
Impact: Operators can unintentionally expose advanced UI/admin routes or run optional pipeline work
while believing the parent engine is disabled.
Root cause or likely cause: Staged rollout flags were added incrementally without a central
compatibility matrix.
Recommended remediation: Define a feature-flag compatibility matrix and enforce it in `Settings`
validators or startup checks.
Acceptance criteria: Each parent/child combination is classified as valid, invalid, or intentionally
shadow-mode; tests cover all invalid/high-risk combinations.
Regression tests required: Pairwise feature-flag tests in `tests/test_settings.py` and pipeline step
tests.
Owner profile: Backend/product owner
Dependencies: Product decision on shadow mode semantics.

ID: PH3-005
Title: Technical-scoring config merge allows unknown override keys without warning
Severity: S2 Medium
Confidence: Strong
Affected components: `app/services/technical_scoring_config.py`, `config/technical_scoring_v4.yaml`
Evidence: `_deep_merge` copies unknown override keys into the effective config; validation only
checks regime weights. Other numeric ranges and unknown keys are not validated.
Reproduction steps: Inspect `_deep_merge` and `_validate_regime_weights` in
`app/services/technical_scoring_config.py`.
Expected behavior: Unknown keys, invalid enum-like values, negative lengths/percentiles, and
contradictory thresholds fail fast.
Observed behavior: Unknown keys are retained and most fields are not range-validated.
Impact: Misspelled technical knobs may be ignored by consuming code while still appearing in the
effective config, or invalid values may reach indicator/scoring code.
Root cause or likely cause: Flexible default merge without a schema.
Recommended remediation: Replace dict merge with a typed schema or recursive allowed-key validator.
Acceptance criteria: Unknown keys and invalid numeric ranges raise actionable config errors.
Regression tests required: Add malformed override tests.
Owner profile: Backend/quant engineer
Dependencies: Enumerate allowed technical v4 schema.

## Action Backlog

Immediate:

- Add `Settings` bounds and cross-field validators for unsafe values.
- Decide and document valid parent/child feature-flag combinations.
- Add config hash lineage for core score-producing configs.

Near term:

- Convert `scoring_weights.yaml`, `fundamentals_v2.yaml`, `pine_defaults.yaml`, and
  `technical_scoring_v4.yaml` to typed config loaders with stable hash helpers.
- Start writing `engine_parameters` or an equivalent run-level config snapshot during pipeline runs.
- Add startup diagnostics for unknown `.env` keys and unsafe public bind/debug combinations.

Structural:

- Create a configuration schema catalogue covering every YAML file and environment variable.
- Add pairwise feature-flag tests for advanced engines.
- Build a redaction checklist for non-CERI error/log/export paths.

## Test Additions Proposal

- `tests/test_settings.py`: invalid ports, sizes, delays, pagination, worker timing, public bind +
  debug, unknown settings, and incompatible feature flags.
- Core config tests: unknown keys, duplicate keys where detectable, malformed YAML, invalid enums,
  negative thresholds, weight sums, contradictory gates.
- Lineage tests: score-producing runs persist config hashes/versions and can reproduce effective
  config identity.
- Technical v4 tests: reject unknown override keys and invalid percentile/length thresholds.
- Secret tests: non-CERI exports/log payload helpers redact database URLs, local paths, bearer
  tokens, and SQL details where applicable.

## Decision Records Needed

- DR-PH3-001: Runtime public binding policy and debug safety rules.
- DR-PH3-002: Canonical mechanism for run-level effective configuration snapshots.
- DR-PH3-003: Feature-flag compatibility matrix and shadow-mode semantics.
- DR-PH3-004: Whether `.env` unknown keys should fail startup or warn.

## Phase Scorecard

| Dimension | Rating | Rationale |
|---|---|---|
| Runtime settings validation | Red | Invalid and unsafe values are accepted |
| YAML schema validation | Amber | Advanced engines are strong; older/core scoring configs are weaker |
| Config lineage | Amber | Advanced artifacts carry hashes; core scoring lineage is incomplete |
| Feature-flag correctness | Amber | Flags are inventoried but compatibility is not centrally enforced |
| Secret handling | Green/Amber | CERI is strong; non-CERI paths need later audit |
| Provider/license controls | Green | CERI credentials, export policy, purge defaults, and provider gating are tested |
| Test coverage | Amber | Focused config tests pass, but runtime invalid-setting tests are missing |

## Exit Report

Passed checks:

- All YAML files were inventoried.
- Strong config loaders and hash stability were confirmed for SLSE, OWPE, CERI, sector rotation, and
  ranking profiles.
- Focused configuration/security tests passed: `86 passed`.
- CERI secret/provider/export/license controls are represented in code and tests.

Failed checks:

- Runtime `Settings` does not fail fast on invalid numeric values, unsafe debug/public bind, or
  contradictory feature flags.
- Core scoring configs do not yet have consistent typed schemas and full config-hash lineage.
- Unknown environment keys are silently ignored.

Deferred items:

- Full non-CERI log/error redaction audit.
- Duplicate-key YAML detection strategy.
- Full pairwise feature-flag matrix.
- Runtime startup validation implementation.

Phase 3 status: advanced-engine config governance is comparatively mature, but base runtime settings
and core score-producing config lineage need hardening before configuration can be treated as
production code across the whole app.
