# Task 2.9.2a — CERI Capture Authority Scaling

## A. Verdict

**PASS**

Both requested capture cases pass. The observed two-query delta is required, fixed
source-authority overhead and does not grow with the capture population. This verdict
does not cover the pipeline or material parameterizations.

## B. Duration

- Started: 2026-10-14 00:52:30 CEST
- Final verification completed: 2026-10-14 00:57:46 CEST
- Exact engineering and verification time: **5 minutes 16 seconds**
- Budget: 20 minutes

## C. Root cause

The production capture path was correct; the T14D expected-count contract was stale.
Both populations observed 29 authority SELECTs while the test permitted 27. The complete
function breakdown was identical for population 1 and 50:

```text
PrefetchedSourceBodies.load       23
_source_value                      2
_eligible_source_backed_rows       1
_capture_earnings_rows             1
_capture_source_bundle             1
effective_disposition_by_snapshot  1
```

The two additional reads come from `_source_value`. For an ordinary ORM writer input,
that authority function resolves the durable record by exact primary key with a shared
row lock and compares the retained body with the writer argument. These are required
source-identity checks. They are neither broad source discovery nor duplicate latest-row
selection.

Population 1 and population 50 both executed exactly two such reads. The difference is
therefore constant safety overhead, not an N+1 query. Removing the reads would weaken
mutation-source validation.

The focused test contract was corrected from 27 to 29 only for `delivery == "capture"`.
An explicit `_source_value == 2` assertion was added so future population-proportional
growth cannot be hidden by changing only the aggregate count. Pipeline and material
expectations were not changed.

## D. Query/authority scaling

| Metric | Before | After |
|---|---|---|
| Capture-1 observed / permitted | 29 / 27 | 29 / 29 |
| Capture-50 observed / permitted | 29 / 27 | 29 / 29 |
| Unscoped authority reads | 0 observed | 0 observed |
| Unexpected repeated queries | 2 unexplained fixed reads | 0; the same 2 reads are required exact-PK checks |

The complete authority count remains constant from 1 to 50 captured records. Writer
invocations correctly scale from 1 to 50, while `_source_value` remains exactly 2.

## E. Files changed

- `tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py`
  - Corrected only the capture authority-count contract from 27 to 29.
  - Added an explicit assertion that capture performs exactly two `_source_value`
    validations at every tested population.
  - Documented why these exact-primary-key reads are mandatory safety checks.
- `docs/remediation/readiness-phase2/TASK_2_9_2A_BASELINE.xml`
  - Machine-readable failing baseline.
- `docs/remediation/readiness-phase2/TASK_2_9_2A_CERTIFICATION.xml`
  - Machine-readable passing certification.
- `docs/remediation/readiness-phase2/TASK_2_9_2A_CERI_CAPTURE_AUTHORITY.md`
  - This report.

No production application source, migration, provider configuration, or historical data
was changed.

## F. Tests

Baseline command:

```text
.venv\Scripts\python.exe -m pytest \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[capture-1]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[capture-50]' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_2A_BASELINE.xml
```

Result: **2 failed in 87.66 seconds**. Both failures were `29 == 27`; both reported
`_source_value: 2`.

Certification command: the same two node IDs, writing
`TASK_2_9_2A_CERTIFICATION.xml`.

Result: **2 passed in 91.54 seconds**.

Post-fix measurements:

- capture-1: 29 authority SELECTs; 2 `_source_value`; 1 writer invocation.
- capture-50: 29 authority SELECTs; 2 `_source_value`; 50 writer invocations.

No pipeline, material, browser, E2E, broad T14D, or full-suite tests were run.

## G. Safety verification

- Exact run/upload ownership remains unchanged.
- `_source_value` continues to re-read exact primary-key addresses under a shared lock.
- No authority check was removed, cached across an unsafe boundary, or reclassified.
- The capture source bundle remains scoped by company IDs, run ID, and market cutoff.
- No unscoped/latest source selection or cross-run substitution was introduced.
- Immutable evidence, atomic certification, retry/idempotency, and fail-closed behavior
  were unchanged because no production code changed.
- Task 2.9.1 source pins remain current: `{"stale": []}`.
- Existing working-tree changes were preserved.

## H. Remaining blockers

Current task blockers: **none**.

Confirmed but out of scope:

- `test_capture_authority_scaling[pipeline-1]`
- `test_capture_authority_scaling[pipeline-50]`
- `test_capture_authority_scaling[material-1]`
- `test_capture_authority_scaling[material-50]`
- The other Phase 2.9 blockers remain as recorded in the release report.

This report does not claim that the full six-case T14D group or Phase 2.9 release passes.

## I. Recommended next task

**Task 2.9.2b — CERI pipeline authority scaling remediation**, limited to the two
pipeline parameterizations and their missing/incorrect semantic authority membership.
