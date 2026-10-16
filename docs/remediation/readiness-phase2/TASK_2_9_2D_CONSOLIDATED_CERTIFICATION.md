# Task 2.9.2d — Consolidated T14D Authority Scaling Certification

## A. Verdict

**PASS**

All six previously remediated authority-scaling cases passed together in one invocation.
This closes only the six-case T14D authority-scaling subgroup, not full T14D or the
Phase 2.9 release.

## B. Execution time

- Started: 2026-10-14 01:43:21 CEST
- Finished: 2026-10-14 01:51:54 CEST
- Elapsed: **8 minutes 33 seconds**
- Test process timeout: 13 minutes
- Task budget: 15 minutes

## C. Results

| Test | Status | Authority SELECTs | Writer invocations |
|---|---|---:|---:|
| capture-1 | PASS | 29 | 1 |
| capture-50 | PASS | 29 | 50 |
| pipeline-1 | PASS | 64 | 1 |
| pipeline-50 | PASS | 64 | 50 |
| material-1 | PASS | 29 | 1 |
| material-50 | PASS | 29 | 50 |

Material changes remained exactly 3 and 150. For every delivery type, the authority
count and complete reviewed function breakdown were identical between populations 1
and 50. The exact per-function assertions passed, so no unclassified or unscoped
authority operation appeared and no parameterization interfered with another.

Population-dependent total SQL, flush, and non-query validation work was observed as
expected, but authority SELECTs remained fixed. No performance remediation was started.

## D. JUnit evidence

One pytest invocation executed exactly the six requested node IDs:

```text
.venv\Scripts\python.exe -m pytest \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[capture-1]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[capture-50]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[pipeline-1]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[pipeline-50]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[material-1]' \
  'tests/integration/test_t14d_ceri_end_to_end_performance_postgresql.py::test_capture_authority_scaling[material-50]' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_2D_CONSOLIDATED.xml
```

Result: **6 passed in 410.79 seconds**.

JUnit artifact:
`docs/remediation/readiness-phase2/TASK_2_9_2D_CONSOLIDATED.xml`.

## E. Source-pin verification

`.venv\Scripts\python.exe -m scripts.qa.t14a_phase2_9_source_pins` returned:

```json
{"stale": []}
```

T14A source pins remain current. No regeneration was performed.

## F. Working-tree safety

- Branch: `codex/ceri-authority-remediation`
- HEAD: `6656c55dd59c294c2ea471571863046e02bf7c35`
- No production code, test logic, expectations, registries, schemas, or historical data
  were modified by this task.
- Existing uncommitted work was preserved.
- The only new artifacts are this report and the consolidated JUnit XML.
- The disposable PostgreSQL fixture completed its forced-drop teardown without error.
  A post-run administrative inspection found no database attributable to this
  invocation; unrelated pre-existing test databases were not modified.

## G. Remaining blockers

The six-case T14D authority-scaling subgroup is **closed**:

- capture: 29 / 29
- pipeline: 64 / 64
- material: 29 / 29

Other T14D and Phase 2.9 defects remain outside this certification.

## H. Next recommended single task

Address the remaining T14D high-cardinality scoped-change query failure as its own
bounded remediation task. Do not combine it with Winner, worker/restart, browser, or
full-suite certification work.
