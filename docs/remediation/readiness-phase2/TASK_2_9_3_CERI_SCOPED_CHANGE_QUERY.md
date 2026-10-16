# Task 2.9.3 — CERI High-Cardinality Scoped-Change Query

## A. Verdict

**PASS**

The target regression passes with the original membership-parameter budget. The defect
was in test instrumentation, not production SQL: the prior metric counted fixed
fail-closed eligibility constants as source-membership expansion.

## B. Duration

- Started: 2026-10-14 02:06:37 CEST
- Finished: 2026-10-14 02:13:00 CEST
- Elapsed: **6 minutes 23 seconds**
- Budget: 20 minutes

## C. Root cause

The failing assertion took the maximum count of every bind in every captured CERI
SELECT. The 33-bind statement was an eligible-score-snapshot query, not an expanded
source-body lookup. Its parameters were:

- 2 integer company-membership IDs;
- 1 integer run ID;
- 1 session date and 1 cutoff timestamp;
- 28 fixed string/boolean binds implementing disposition eligibility, terminal pipeline
  states, safe-partial outcome identity, integrity status, JSON paths, and fail-closed
  defaults.

Those 28 safety binds are constant and unrelated to source population. Removing or
literalizing them would weaken or obscure eligibility enforcement without reducing
membership cardinality.

The existing production path was already bounded. It chunks five target companies in
groups of two, scopes snapshots by exact run/company/session/cutoff, follows relational
company→event→revision and company→guidance ownership, joins source records for temporal
eligibility, and locks exact selected identities. Across 250 unrelated companies and
500 unrelated source records, the largest actual membership list contained four source
IDs. All ten intended source bodies—and no unrelated IDs—were loaded.

The test now separately records total binds and membership `IN` binds for the reviewed
snapshot, event, revision, guidance, and source identity columns. The unchanged `<= 10`
contract applies to the latter. A negative fixture now adds an unrelated-run snapshot
with the same ticker as a target; exact run/company ownership still excludes it.

## D. Query comparison

| Metric | Before | After |
|---|---|---|
| SQL parameter count | 33 total binds, incorrectly treated as membership | 4 maximum membership binds; 33 total safety/boundary binds retained as diagnostics |
| Permitted maximum | 10 | 10 |
| Membership scoping mechanism | Chunked relational IDs, but instrumentation counted all binds | Same production scoping; instrumented only reviewed membership `IN` columns |
| Unrelated rows returned | 0 | 0, including one same-ticker unrelated-run snapshot |
| Unscoped source reads | 0 | 0 |
| Duplicate results | 0 observed | 0 observed |

The remediation does not raise the budget or alter production selection.

## E. Files changed

- `tests/integration/test_ceri_change_rebuild_scoping_postgresql.py`
  - Separates total SQL binds from scoped-membership binds.
  - Retains exact SQL, parameter-name, and parameter-type diagnostics.
  - Applies the existing limit of 10 to actual membership expansion.
  - Adds an unrelated-run, same-ticker negative snapshot.
- `docs/remediation/readiness-phase2/TASK_2_9_3_BASELINE.xml`
  - Original failing `33 <= 10` baseline.
- `docs/remediation/readiness-phase2/TASK_2_9_3_DIAGNOSTIC.xml`
  - Exact 33-bind SQL and parameter classification.
- `docs/remediation/readiness-phase2/TASK_2_9_3_CERTIFICATION.xml`
  - Passing focused certification with four membership binds.
- `docs/remediation/readiness-phase2/TASK_2_9_3_CERI_SCOPED_CHANGE_QUERY.md`
  - This report.

No production source, schema, migration, query builder, authority mapping, or query
budget was changed.

## F. Tests and evidence

Exact command:

```text
.venv\Scripts\python.exe -m pytest \
  'tests/integration/test_ceri_change_rebuild_scoping_postgresql.py::test_high_cardinality_unrelated_rows_never_enter_scoped_change_reads' \
  -q --tb=short \
  --junitxml=<artifact path>
```

- Baseline: **1 failed in 9.07 seconds**, `33 <= 10`; artifact
  `TASK_2_9_3_BASELINE.xml`.
- Diagnostic: **1 failed in 8.93 seconds**, preserving the full 33-bind statement,
  names, and types; artifact `TASK_2_9_3_DIAGNOSTIC.xml`.
- Final certification: **1 passed in 8.96 seconds**; artifact
  `TASK_2_9_3_CERTIFICATION.xml`.

Final evidence:

- maximum scoped-membership parameters: 4 (`id_1_1` through `id_1_4`);
- maximum total parameters: 33, retained and explained;
- target companies: 5;
- selected source bodies: 10;
- unrelated companies/source records: 250/500;
- unrelated same-ticker snapshots selected: 0;
- predicate-free large CERI reads: 0;
- detector outputs: exactly 5 score, 5 revision, and 5 guidance identities, without
  duplicates.

No broad, six-case T14D, browser/E2E, live-provider, or full-suite test was run.

## G. Safety verification

- Snapshot selection remains anchored to exact `run_id`, chunked `company_id`, session,
  cutoff, effective disposition, and owner eligibility.
- Catalyst revisions remain anchored through selected event IDs and joined source IDs.
- Guidance remains anchored to company IDs and joined source IDs.
- Source-body locks use only exact IDs derived from the selected revisions/guidance.
- The same-ticker negative row proves ticker equality cannot bypass run/company scope.
- The effective-disposition exclusion and terminal-owner fail-closed predicates remain
  in the measured 33-bind SQL; no quarantine/exclusion guard was removed.
- Source possession/publication/observation timestamps remain bounded by the requested
  cutoff; no current/latest or cross-run fallback was introduced.
- T14A source-pin validation returned `{"stale": []}`.
- Existing unrelated working-tree changes were preserved.

## H. Remaining blockers

Current-task blockers: **none**. The high-cardinality scoped-change regression is
closed. Other Phase 2.9 failures remain out of scope.

## I. Recommended next single task

Remediate
`test_public_upload_enqueue_process_restart_native_worker_and_frozen_history[decision-graph]`
as the next bounded Phase 2.9 task. Do not combine it with the remaining resume, retry,
heartbeat, Winner, browser, or full-suite failures.
