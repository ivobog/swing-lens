# Task 2.9.2b — CERI Pipeline Authority Scaling

## A. Verdict

**PASS**

Both requested pipeline cases pass. The correction is test-only: it supplies the
production-required frozen semantic authority to the native pipeline fixture and pins
the current, fixed 64-SELECT authority footprint. This verdict does not cover capture,
material, broader T14D, or Phase 2.9 certification.

## B. Duration

- Started: 2026-10-14 01:01:14 CEST
- Finished: 2026-10-14 01:10:29 CEST
- Actual elapsed time: **9 minutes 14 seconds**
- Budget: 20 minutes

## C. Root cause

The production path was fail-closed and correct; the pipeline test fixture was stale.
It created a `PipelineRun`, a pipeline-owned feature request, and a capture job without
the frozen semantic authority now required by pipeline membership admission. Both
baseline cases therefore stopped before capture instrumentation with
`CERI_FEATURE_MEMBERSHIP_AUTHORITY_INVALID:MISSING`.

The fixture now admits one `full-pipeline-run` authority over the exact run ticker set
and cutoff, binds it to the parent `PipelineRun`, passes it to
`CeriFeatureRebuildRequest`, and binds the same authority to the durable capture job.
The job binding is flushed before direct native execution so the retained database
scope and in-memory delivery scope are identical.

Once the real path executed, both populations reported the same 64 authority SELECTs,
not the stale permitted value of 68. Their complete function breakdown was identical:

```text
PrefetchedSourceBodies.load        47
assert_current_execution_ownership  7
load_configuration_delivery         2
resolve_pipeline_market_context     2
_source_value                       2
_eligible_source_backed_rows        1
_capture_earnings_rows              1
_capture_source_bundle              1
effective_disposition_by_snapshot   1
```

These are fixed ownership, configuration, frozen-context, exact-source, eligibility,
and disposition checks. `PrefetchedSourceBodies.load` uses scoped statements and
shared row locks; the source bundle is built from exact company IDs, run ID, and market
cutoff. No authority SELECT grows with population. Writer invocations correctly grow
from 1 to 50, and non-query score-source validations grow from 14 to 63, without
creating an authority-read N+1. The pipeline-only aggregate budget was corrected from
68 to 64 and an exact per-function `Counter` assertion now protects the fixed-cost
contract. Capture and material expectations are unchanged.

## D. Authority scaling

| Metric | Before | After |
|---|---|---|
| Pipeline-1 observed / permitted | N/A / 68; failed before measurement | 64 / 64 |
| Pipeline-50 observed / permitted | N/A / 68; failed before measurement | 64 / 64 |
| Fixed source-authority reads | N/A | 64, identical function composition |
| Population-dependent reads | N/A | 0 authority SELECTs |
| Unscoped authority reads | N/A | 0 observed |
| Unclassified operations | N/A | 0 observed |

The diagnostic run after authority delivery, before updating the stale budget, observed
64 / 68 for both populations and preserved the function-level evidence in
`TASK_2_9_2B_AFTER.xml`.

## E. Files changed

- `tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py`
  - Added established full-pipeline semantic-authority setup and propagation.
  - Corrected only the pipeline expected count from 68 to 64.
  - Added an exact pipeline per-function authority assertion.
- `docs/remediation/readiness-phase2/TASK_2_9_2B_BASELINE.xml`
  - Failing, pre-capture semantic-authority baseline.
- `docs/remediation/readiness-phase2/TASK_2_9_2B_AFTER.xml`
  - Post-fixture diagnostic evidence showing 64 / 68 at both populations.
- `docs/remediation/readiness-phase2/TASK_2_9_2B_CERTIFICATION.xml`
  - Passing focused certification.
- `docs/remediation/readiness-phase2/TASK_2_9_2B_CERI_PIPELINE_AUTHORITY.md`
  - This report.

No production source, migration, source-review metadata, provider configuration, or
historical run data was changed.

## F. Tests and evidence

Baseline and certification used exactly these node IDs:

```text
.venv\Scripts\python.exe -m pytest \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[pipeline-1]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[pipeline-50]' \
  -q --tb=short \
  --junitxml=<artifact path>
```

- Baseline: **2 failed in 74.49 seconds**; both failed with
  `CERI_FEATURE_MEMBERSHIP_AUTHORITY_INVALID:MISSING` before measurement. Artifact:
  `TASK_2_9_2B_BASELINE.xml`.
- Post-fixture diagnostic: **2 failed in 98.10 seconds** only because both observed
  64 against the stale 68 budget. Pipeline-1 had 1 writer invocation; pipeline-50 had
  50. Artifact: `TASK_2_9_2B_AFTER.xml`.
- Certification: **2 passed in 96.25 seconds**. Pipeline-1 and pipeline-50 each had
  exactly 64 authority SELECTs with identical breakdowns; writer invocations were 1
  and 50. Artifact: `TASK_2_9_2B_CERTIFICATION.xml`.
- Targeted source-pin validation:
  `.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins` returned
  `{"stale": []}`.
- `git diff --check` completed without whitespace errors; its only output was existing
  line-ending conversion warnings.

No capture, material, browser/E2E, broad T14D, live-provider, normal-database, or
full-suite tests were run.

## G. Safety verification

- Frozen membership contains only the exact uppercase tickers for the upload run.
- Pipeline, feature request, and durable capture job share the same scope, refresh
  cycle, and acquisition-plan IDs.
- Pipeline feature membership still resolves through `RawCompanyRow.run_id`; no
  cross-run or current/latest fallback was introduced.
- Capture resolves the immutable calculation context against the expected upload run,
  cutoff, session, and calendar version.
- Source bundles remain company-scoped and run/cutoff-bound, use exact source IDs, and
  retain mutation-authority shared locks and body comparison.
- No integrity check, fail-closed behavior, mutation wrapper, retry rule, or evidence
  immutability rule was weakened.
- Every measured authority operation is classified; no unscoped source query was
  observed.
- T14A source pins remain current: `{"stale": []}`.
- Existing unrelated working-tree changes were preserved.

## H. Remaining blockers

Current-task blockers: **none**.

The two material-authority cases and all other Phase 2.9 failures remain out of scope.
This report does not claim that all six scaling cases or the release pass.

## I. Recommended next single task

**Task 2.9.2c — CERI material-authority scaling remediation**, limited to
`material-1` and `material-50`.
