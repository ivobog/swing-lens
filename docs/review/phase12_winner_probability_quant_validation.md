# Phase 12 Review - Winner Probability Engine Quant Validation

Date: 2026-08-02
Reviewer: Codex
Scope: OWPE prediction capture, feature schema, evidence construction, cohort probability
estimation, shadow training, calibration, drift, model registry, reproduction, exports, API/UI
presentation, and winner-probability tests.

## Objective

Assess statistical validity, leakage controls, calibration, reproducibility, and safe presentation
of probabilities.

## Executive Summary

Phase 12 is not exit-ready.

The cohort-based decision-time estimator is conservative and has several strong controls. Evidence
must predate the prediction training cutoff, outcomes must be matured before inclusion, reconstructed
history is excluded from production evidence, dependent repeated episodes are collapsed, cold-start
probabilities are withheld, and persisted estimates carry immutable evidence manifests. The shadow
training path uses walk-forward folds, grouped episodes, fold-local preprocessing, approved
training algorithms, log loss/Brier metrics, and baseline comparisons.

The blockers are governance and presentation gaps. The model registry can register and promote an
arbitrary `algorithm` string if artifact and metric fields pass validation, so the allow-list used
by shadow training is not enforced at promotion. Promotion gates check sample count, ECE, coverage,
artifact validity, and critical drift, but do not require Brier/log-loss improvement versus global
and cohort baselines, confidence intervals, cohort stability, persisted calibration bins, drift
freshness, or an explicit human approval record beyond actor/reason text. User-facing probability
output shows sample size, interval, training cutoff, and evidence manifest, but not calibration
status or model status/version as a visible part of the probability itself.

## Evidence Log

| Check | Result | Notes |
| --- | --- | --- |
| Phase 12 checklist from `C:/Users/Ivica/Downloads/software_review_plan.md` | Reviewed | Objective, review activities, outputs, and exit criteria mapped. |
| Focused winner probability suite | Passed | `uv run pytest tests\winner_probability -q` -> `119 passed, 1 warning in 9.41s`. |
| Outcome definitions and target/stop semantics | Mostly satisfied | Config defines primary target-before-stop label, entry model, horizon, target/stop thresholds, and conservative same-bar policy. Pending outcomes are materialized at capture. |
| Decision-time feature capture | Partial | Feature extraction validates source row timestamps against capture time and stores source ids/hashes, but the feature-schema cutoff validator is not wired into extraction as a per-feature audit. |
| Evidence temporal leakage controls | Satisfied for cohort evidence | Evidence excludes current prediction, future source cutoffs, outcomes matured/evaluated at or after cutoff, superseded revisions invisible at cutoff, dependent episodes, and reconstructed history. |
| Walk-forward validation | Mostly satisfied | Shadow training folds keep train cutoffs before test starts and disjoint episode groups; preprocessing is fit on each train fold. No embargo window beyond strict earlier-than-test-start grouping. |
| Calibration and drift metrics | Partially satisfied | Services compute Brier, log loss, ECE, reliability bins, win-rate deltas, and PSI. Promotion does not require persisted calibration/drift freshness. |
| Reproducibility | Mostly satisfied | Estimates reproduce from exact evidence membership and manifest hashes. Target/stop revision is stored in metadata and manifest payload; reproduction loads target/stop id but does not explicitly assert metadata revision equality. |
| Probability presentation | Partial | UI shows probability, interval, grade, sample size, training cutoff, manifest, source/version on the detail manifest block, and reproduction. It does not visibly show calibration status or model lifecycle status/version next to each probability. |

## Quantitative Validation Report

Current decision-time production source:

- Source: cohort baseline (`COHORT`, `cohort_baseline_v1`).
- Label: target-before-stop primary winner for configured outcome definition.
- Cutoff: estimate `training_cutoff_at` equals `prediction.source_data_cutoff_at`
  (`app/services/winner_probability/probability_estimator.py:84`).
- Evidence: historical matured target/stop outcomes before cutoff, excluding dependent episodes and
  reconstructed history (`app/services/winner_probability/evidence_service.py:53-93`).
- Probability: posterior cohort probability with configured prior strength and prior probability
  (`app/services/winner_probability/cohort_statistics.py:36-55`).
- Uncertainty: normal-approximation interval over the posterior denominator, evidence grade by
  effective-n and interval-width thresholds (`app/services/winner_probability/cohort_statistics.py:63-88`).
- Insufficient evidence: probability and interval are withheld when no eligible cohort satisfies
  effective-n/width/grade gates (`app/services/winner_probability/probability_estimator.py:251-356`).

Current shadow validation source:

- Algorithm: `regularized_logistic_regression` only; gradient boosted trees are blocked in training
  (`app/services/winner_probability/model_training.py:19-22`,
  `app/services/winner_probability/model_training.py:120-121`).
- Fold design: chronological walk-forward groups, training examples strictly before each test
  start, disjoint episode groups (`app/services/winner_probability/model_training.py:59-104`).
- Preprocessing: numeric mean/std and categorical vocabulary are fit inside each fold before test
  transform (`app/services/winner_probability/model_training.py:237-276`).
- Metrics: model log loss, model Brier score, global baseline log loss, cohort baseline log loss,
  fold count, sample count, and baseline probabilities
  (`app/services/winner_probability/model_training.py:222-235`,
  `app/services/winner_probability/model_training.py:416-431`).
- Persisted evidence: fold plan, preprocessing, metrics, warnings, training cutoff, and artifact
  hash are stored on `WinnerModelTrainingRun`
  (`app/services/winner_probability/model_training.py:184-218`).

Required additions before promotion-quality validation:

- Require model log loss and Brier to improve against global and cohort baselines by configured
  margins with confidence intervals or bootstrap bands.
- Persist calibration report ids/bins used for promotion and require ECE/Brier/log-loss freshness
  within an as-of window.
- Add cohort stability checks across setup family, ranking profile, market risk, sector state,
  earnings risk, and data-quality cohorts.
- Add explicit class-balance and effective independent episode count reporting.
- Define embargo sessions for same ticker/setup families when outcomes overlap the next fold's test
  window.

## Leakage and Bias Checklist

| Risk | Status | Evidence / Action |
| --- | --- | --- |
| Target leakage through current row outcome | Controlled | Current prediction id is excluded from evidence (`app/services/winner_probability/evidence_service.py:52`). |
| Future evidence leakage | Controlled | Prediction source cutoff, forward maturity, and target/stop evaluation must be before training cutoff (`app/services/winner_probability/evidence_service.py:53-90`). |
| Outcome revision leakage | Mostly controlled | Superseded outcomes are included only if still visible at cutoff (`app/services/winner_probability/evidence_service.py:59-71`). Evidence members store forward revision and target/stop revision metadata (`app/services/winner_probability/evidence_manifest_service.py:82-97`). |
| Reconstructed-history leakage | Controlled | Reconstructed predictions are excluded from evidence (`app/services/winner_probability/evidence_service.py:93`). |
| Repeated observations | Mostly controlled | Dependent episode rows are excluded and evidence is reduced to one row per episode (`app/services/winner_probability/evidence_service.py:92-107`). Shadow folds group by episode id. |
| Feature timestamp leakage | Partial | Extraction validates row timestamps against capture time (`app/services/winner_probability/feature_extractor.py:277-291`), and backfill passes run processed/uploaded time as capture time (`app/services/winner_probability/backfill.py:214-257`). The feature-schema validator exists (`app/services/winner_probability/feature_schema.py:48-60`) but is not called by extraction. |
| Universe leakage / survivorship bias | Open limitation | Evidence is based on captured/uploaded candidate universes. Reconstructed rows are excluded, but there is no fixed constituent universe or delisted-name adjustment. Label output should call this a candidate-universe probability. |
| Selection bias | Open limitation | Cohorts are built from rows that entered the SwingLens pipeline, not from all possible stocks. Require model card disclosure and cohort dashboard. |
| Class imbalance | Partial | Sample/effective-n and raw win counts are stored, but no imbalance threshold or stratified CI is part of promotion. |
| Sample-size sufficiency | Partial | Cohort effective-n and interval width gate estimate serving; promotion only checks `sample_n` from model metrics. |
| Preprocessing leakage | Controlled for shadow training | Fold-local preprocessing is fit on train examples only. Final artifact preprocessing is fit on all eligible pre-cutoff training examples after validation. |

## Model-Card Template

Required fields for every promoted or shadow model:

| Section | Required content |
| --- | --- |
| Identity | Model key, model id, algorithm, artifact hash, artifact format/schema, feature schema version, calculation version, config hash, dependency versions. |
| Intended use | Research-only target-before-stop probability for the configured label; not a generic positive-return forecast or trading recommendation. |
| Training data | Training window start/end, training cutoff, included universes, excluded reconstructed history, independent episode count, raw rows, effective n, class balance. |
| Label | Outcome definition id, entry model, horizon sessions, target pct, stop pct, same-bar policy, pending/excluded handling, revision policy. |
| Features | Immutable feature order, core feature list, missingness policy, per-feature availability rules, timestamp/cutoff audit summary. |
| Validation | Walk-forward fold plan, embargo policy, log loss, Brier, ECE, calibration bins, global baseline, cohort baseline, discrimination metric, confidence intervals. |
| Cohort stability | Metrics by setup family, ranking profile, market risk, sector state, earnings risk, technical data quality, and minimum cohort sample. |
| Drift | Baseline window, recent window, Brier delta, ECE delta, win-rate delta, PSI, minimum sample, breached segments, last calculated date. |
| Limitations | Candidate-universe selection bias, survivorship limitations, cold-start behavior, overlapping outcomes, stale/missing market data handling. |
| Governance | Created by, reviewed by, approval event id, promotion gate result, reason, activated_at, rollback/fallback model. |

## Promotion-Gate Specification

Current gates:

- New models cannot start as `ACTIVE`; initial status must be candidate, shadow, or rejected
  (`app/services/winner_probability/model_registry.py:43-106`).
- Promotion evaluates artifact validity, minimum sample, ECE threshold, coverage threshold, and
  critical drift breach (`app/services/winner_probability/model_registry.py:108-145`).
- Promotion retires replaced active models and records lifecycle events with actor/reason
  (`app/services/winner_probability/model_registry.py:147-180`).

Required gates before exit:

| Gate | Required rule |
| --- | --- |
| Algorithm allow-list | Registry and promotion must reject algorithms outside an approved set such as `cohort` and `regularized_logistic_regression`. |
| Artifact reproducibility | Artifact hash must be recomputed from stored payload; feature order, calibration payload, dependency versions, training cutoff, and config hash must match. |
| Temporal validation | Training examples must have source cutoff and outcome visibility strictly before training cutoff; fold test examples must be later than all fold training examples plus configured embargo. |
| Minimum evidence | Require independent episode count, effective n, class-balance bounds, and minimum cohort coverage. |
| Baseline superiority | Require model Brier/log-loss to beat global and cohort baselines by configured margin or keep model in shadow. |
| Calibration | Require ECE below threshold, calibration bins persisted, enough samples per critical probability band, and no severe cohort calibration failures. |
| Drift | Require recent drift metrics calculated within the configured window and no sufficient-sample breached segment. Missing drift metrics should block promotion. |
| Human approval | Require an explicit approval event with reviewer identity, timestamp, model card hash, gate report hash, and reason. Actor/reason strings alone are audit metadata, not approval. |
| Rollback | Require an active fallback or rollback plan before replacing the current active model. |

## Calibration and Drift Dashboard Requirements

The current model-health page renders registered models, calibration bins, and drift indicators
(`app/templates/winner_probability_models.html:11-126`). It should be extended with:

- Last calibration calculation timestamp and whether it is fresh.
- Current serving model id/version next to each probability and on run/ticker pages.
- Calibration status badge for every probability: calibrated, stale, insufficient, shadow-only, or
  cohort-baseline.
- Reliability chart with bin n/effective n and confidence intervals.
- Brier, log loss, ECE, calibration slope/intercept, and coverage over time.
- Segment tabs for setup family, ranking profile, market risk, sector state, earnings risk, and data
  quality.
- Drift trend charts for Brier delta, ECE delta, win-rate delta, and PSI.
- Promotion gate report with pass/fail reasons and links to model card, training run, artifact
  hash, and approval event.

## Findings Register

### PH12-001 - Registry promotion does not enforce an approved algorithm allow-list

Severity: High

Evidence:

- Shadow training blocks unapproved algorithms
  (`app/services/winner_probability/model_training.py:19-22`,
  `app/services/winner_probability/model_training.py:120-121`; tested at
  `tests/winner_probability/test_quantitative_validation.py:113-129`).
- Model registration accepts `algorithm` as an arbitrary string and stores it without allow-list
  validation (`app/services/winner_probability/model_registry.py:43-106`).
- Promotion validates status, artifact, sample, ECE, coverage, and drift, but never validates
  `model.algorithm` (`app/services/winner_probability/model_registry.py:108-180`).
- Registry tests use `algorithm="cohort"` and do not cover rejection of an unapproved registered
  algorithm (`tests/winner_probability/test_model_registry.py:226-250`).

Impact: A model record with an unapproved algorithm could be registered as shadow/candidate and
promoted if its manually supplied artifact, calibration payload, and metrics pass the existing
checks. This violates the Phase 12 requirement that unapproved algorithms cannot be trained or
promoted.

Recommendation:

- Add a registry-level allow-list that includes only approved serving algorithms.
- Validate algorithm in `register_model`, `evaluate_promotion`, and any import/backfill path.
- Add tests proving an unapproved registered model cannot become active even with passing metrics.

### PH12-002 - Promotion gates are too weak for quantitative approval

Severity: High

Evidence:

- Existing gate logic checks sample count, ECE, coverage, artifact validity, and breached drift
  (`app/services/winner_probability/model_registry.py:108-145`).
- A candidate with `sample_n=80`, `ece=0.02`, and `coverage=0.9` promotes in tests
  (`tests/winner_probability/test_model_registry.py:81-101`,
  `tests/winner_probability/test_model_registry.py:226-250`).
- Shadow reports compute model log loss, Brier, and global/cohort baseline log loss, but registry
  promotion does not require these metrics or compare them to baselines
  (`app/services/winner_probability/model_training.py:222-235`,
  `app/services/winner_probability/model_training.py:416-431`).
- Drift absence is not blocking; only existing sufficient-sample breached rows block promotion
  (`app/services/winner_probability/model_registry.py:135`,
  `app/services/winner_probability/model_registry.py:275-286`).

Impact: A model can become active without documented superiority over simpler baselines, without
fresh calibration/drift evidence, and without cohort stability checks. This does not meet the Phase
12 promotion-gate or minimum-evidence standard.

Recommendation:

- Require a signed/persisted gate report containing Brier, log loss, ECE, coverage, baseline
  comparison, confidence intervals, cohort stability, drift freshness, and sample sufficiency.
- Treat missing required metrics or stale drift/calibration rows as promotion blockers.
- Keep all non-passing candidates in shadow/rejected status.

### PH12-003 - Probability UI does not visibly include calibration status or model status/version

Severity: Medium

Evidence:

- The reusable estimate partial displays probability, interval, grade, raw n, effective n, training
  cutoff, and manifest, but no calibration status, model lifecycle status, or model version label
  (`app/templates/partials/_winner_probability_estimate.html:3-27`).
- The run table displays estimate kind and evidence values, but no model status/version or
  calibration freshness per row (`app/templates/winner_probability_run.html:124-174`).
- The ticker detail page exposes source/source version in the evidence manifest block, but not as
  part of the probability panel and not with calibration state
  (`app/templates/winner_probability_ticker.html:31-43`,
  `app/templates/winner_probability_ticker.html:124-137`).
- The API estimate payload includes `model_version_id`, `source_version`, sample context, and
  manifest hash, but no calibration status/freshness field
  (`app/services/winner_probability/api_service.py:662-693`).

Impact: Users can see the probability and interval but cannot immediately tell whether the number
is calibrated, stale, cohort-baseline-only, shadow-model-generated, or tied to an active model
version. This fails the Phase 12 exit criterion for calibration status, sample context, and model
version visibility.

Recommendation:

- Add `calibration_status`, `calibration_calculated_at`, `model_key`, `model_status`, and
  `model_version_label` to estimate payloads.
- Show these fields next to probability on run and ticker pages and in CSV/JSON exports.
- Use explicit wording for cohort baseline vs active calibrated model.

### PH12-004 - Feature cutoff validation exists but is not integrated as a per-feature audit

Severity: Medium

Evidence:

- Feature schema defines `validate_source_available_at(feature_name, source_available_at,
  prediction_cutoff_at)` and tests it
  (`app/services/winner_probability/feature_schema.py:48-60`,
  `tests/winner_probability/test_feature_schema.py:39-69`).
- Feature extraction validates all source rows against `captured_at`, not through the schema
  registry and not per feature (`app/services/winner_probability/feature_extractor.py:67-91`,
  `app/services/winner_probability/feature_extractor.py:277-291`).
- The stored `source_data_cutoff_at` is the max created/updated/uploaded/processed timestamp of
  source rows (`app/services/winner_probability/feature_extractor.py:262-274`).

Impact: Current capture is mostly protected by timestamp checks and source cutoff storage, especially
for trusted backfills, but there is no auditable per-feature assertion that each feature was
available at or before the decision cutoff. That weakens leakage evidence for model cards and
external review.

Recommendation:

- Invoke the feature-schema validator for every captured core feature.
- Persist per-feature availability metadata or a compact audit hash.
- Fail capture when a required feature source is after the prediction cutoff; warn and null optional
  contextual features when appropriate.

## Exit Criteria Assessment

| Exit criterion | Status | Notes |
| --- | --- | --- |
| No unresolved temporal leakage exists | Partial | Cohort evidence controls are strong. Per-feature cutoff audit and universe/selection bias remain open. |
| Every model artifact is reproducible from stored evidence | Partial | Cohort estimates reproduce from exact evidence manifests. Registry artifacts are hash-validated only when payload is available; promotion does not require recomputing payload hash from stored evidence/training run. |
| Probability output includes calibration status, sample context, and model version | Not met | Sample context is visible; calibration status and model status/version are not visible per probability. |
| Promotion requires documented quantitative gates and human approval | Not met | Gates exist but are incomplete; actor/reason lifecycle metadata is not a formal approval workflow. |

## Verification

- `uv run pytest tests\winner_probability -q` -> `119 passed, 1 warning in 9.41s`.
