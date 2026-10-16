# Task 2.9.2c — CERI Material Authority Scaling

## A. Verdict

**PASS**

Both requested material cases pass. The production path was correct and bounded; only
the material-specific T14D expectation was stale. This verdict does not certify the
broader T14D suite or Phase 2.9 release.

## B. Duration

- Started: 2026-10-14 01:17:26 CEST
- Final verification completed: 2026-10-14 01:25:28 CEST
- Actual elapsed time: **8 minutes 2 seconds**
- Budget: 20 minutes

## C. Root cause

Both tests reached and completed material capture. Material-1 produced three material
changes and material-50 produced 150, with writer invocations of 1 and 50. Each then
failed only at the aggregate authority assertion: 29 observed versus 27 permitted.

The complete authority SELECT composition was identical at both populations:

```text
PrefetchedSourceBodies.load        23
_source_value                       2
_eligible_source_backed_rows        1
_capture_earnings_rows              1
_capture_source_bundle              1
effective_disposition_by_snapshot   1
```

The two-count delta is the same required source-integrity mechanism previously proven
for direct capture, but it was independently established here from both material
traces. `_source_value` re-reads ordinary ORM writer inputs by exact primary-key address
under a shared lock and compares their durable bodies. The two reads remain fixed while
population grows from 1 to 50. The other 27 reads are likewise batch-wide source,
eligibility, earnings, bundle, and disposition checks. No authority SELECT is
population-dependent, unscoped, or unclassified.

Material delivery is standalone in this test and does not require pipeline semantic
authority. Source/calculation identity is enforced through the admitted run, frozen
market cutoff, point-in-time source eligibility, locked source bundle, and mutation
authority. There was no stale fixture, missing semantic mapping, N+1 authority query,
or production integrity defect.

The smallest correction changes only the material budget from 27 to 29 and adds an
exact per-function `Counter` assertion. Capture remains 29 and pipeline remains 64.

## D. Authority scaling

| Metric | Before | After |
|---|---|---|
| Material-1 observed / permitted | 29 / 27 | 29 / 29 |
| Material-50 observed / permitted | 29 / 27 | 29 / 29 |
| Fixed authority SELECTs | 29 | 29, exact composition asserted |
| Population-dependent authority SELECTs | 0 | 0 |
| Writer invocations | 1 / 50 | 1 / 50 |
| Unscoped authority reads | 0 observed | 0 observed |
| Unclassified operations | 0 observed | 0 observed |

Non-query source validations grow with the material artifacts (14 to 700), while their
authority SELECT footprint remains fixed. This is bounded validation work, not an
authority-read N+1.

## E. Files changed

- `tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py`
  - Corrected only the material expected count from 27 to 29.
  - Added an exact material per-function authority assertion.
- `docs/remediation/readiness-phase2/TASK_2_9_2C_BASELINE.xml`
  - Machine-readable failing baseline.
- `docs/remediation/readiness-phase2/TASK_2_9_2C_CERTIFICATION.xml`
  - Machine-readable passing certification.
- `docs/remediation/readiness-phase2/TASK_2_9_2C_CERI_MATERIAL_AUTHORITY.md`
  - This report.

No production source, migration, semantic registry, source-review metadata, provider
configuration, or historical run data was changed.

## F. Tests and evidence

Baseline command:

```text
.venv\Scripts\python.exe -m pytest \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[material-1]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[material-50]' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_2C_BASELINE.xml
```

Result: **2 failed in 217.92 seconds**. Both completed material capture and failed only
with `29 == 27`. The breakdown was identical; writer invocations were 1 and 50.

Certification used the same two node IDs and options, writing
`TASK_2_9_2C_CERTIFICATION.xml`.

Result: **2 passed in 220.12 seconds**.

Post-fix evidence:

- material-1: 29 authority SELECTs, 1 writer invocation, 3 material changes.
- material-50: 29 authority SELECTs, 50 writer invocations, 150 material changes.
- Both: 23 locked bundle loads, 2 exact-identity `_source_value` reads, and one each of
  eligibility, earnings, bundle, and effective-disposition reads.
- Targeted source-pin validation:
  `.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins` returned
  `{"stale": []}`.
- Targeted `git diff --check` completed without whitespace errors; its only output was
  the existing line-ending conversion warning.

Capture and pipeline cases were not rerun. Diff inspection confirmed that their fixture,
branch-specific assertions, and expected values remain unchanged. No broad, browser,
E2E, provider, normal-database, or full-suite test was run.

## G. Safety verification

- Source selection remains restricted to the run's company IDs and `RawCompanyRow`
  upload membership.
- The source bundle remains run/cutoff-bound and locks the exact durable source bodies.
- `_source_value` exact-primary-key revalidation remains active; no integrity read was
  removed, cached across an unsafe boundary, or reclassified.
- Point-in-time eligibility and the frozen market cutoff are unchanged.
- Material changes retain their snapshot evidence IDs and comparison state checks.
- Writer invocations and material outputs match population; authority SELECTs do not
  grow with it.
- No cross-run substitution, current/latest fallback, unknown authority, or unscoped
  source read was introduced.
- T14A source pins remain current: `{"stale": []}`.
- Existing unrelated working-tree changes were preserved.

## H. Remaining blockers

Current-task blockers: **none**.

Other Phase 2.9 blockers, if any, remain out of scope. This report does not claim full
T14D or release certification.

## I. Recommended next single task

Run one short consolidated verification of the six already-remediated T14D scaling
cases: capture-1/50, pipeline-1/50, and material-1/50. Do not expand that verification
into the full T14D suite.
