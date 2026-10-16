# Task 2.9.12i — Historical v1/v2 Decision-Anchor Compatibility

## A. Executive verdict

**PARTIAL.** A deterministic, fail-closed v1/v2 verifier is implemented and the focused
PostgreSQL regression is authored, but the only permitted post-fix pytest invocation stopped
during collection before PostgreSQL was reached. The active system Python interpreter did not
contain the declared `prometheus_client` dependency. No replacement pytest process was started.

- Start: `2026-10-14T21:46:58.1870624+02:00`
- Deadline: `2026-10-14T22:21:58.1870624+02:00`
- Evidence finish: `2026-10-14T21:51:34.9189041+02:00`
- Branch: `codex/ceri-authority-remediation`
- Baseline commit: `6656c55dd59c294c2ea471571863046e02bf7c35`
- Chromium/browser/full-suite runs: **not started**

The implementation is not being represented as runtime-certified. The full Phase 2.9 release
remains **UNCERTIFIED**.

## B. Contract analysis

Both contracts use `CanonicalEvidenceSerializer.canonicalize` and its SHA-256 canonical
fingerprint implementation. Their top-level schema is the same:

- `contract`
- `run`
- `ticker_population`
- `market_context`
- `source_cutoffs`
- `immutable_source_lineage`

The `run` object in v1 contains `id`, `filename`, `uploaded_at`, `processed_at`, `row_count`,
`status`, `pine_engine_version`, `python_engine_version`, `raw_row_count`, and
`raw_rows_fingerprint`. V2 contains the same fields except for exactly `processed_at` and
`status`. No other exclusion was added.

Both versions retain the exact ticker population; context identity, cutoff, session, calendar,
bar-readiness and reason; cutoff constraints; and per-ticker selection key, raw-row identity,
expected pointer, eligible bars, and producer signatures. The raw-row fingerprint is rebuilt
from the current run's semantic raw-row payloads.

## C. Historical compatibility policy

- New anchors are written only as `transition-run-start-anchor-v2`.
- Stored v1 and v2 contracts are selected explicitly from the persisted `contract` value.
- The exact persisted JSON must independently reproduce the stored fingerprint before source
  comparison occurs.
- V2 is compared exactly with a newly rebuilt authoritative v2 payload.
- V1 must contain its historical `run.status` and `run.processed_at` keys. The verifier creates
  an in-memory immutable projection by removing only those keys and changing only the projection's
  contract label to v2, then compares it with a newly rebuilt authoritative v2 payload.
- The stored v1 JSON and fingerprint are never updated or replaced.
- Missing, unknown, structurally confused, or fingerprint-invalid contracts fail closed. There is
  no v1-then-v2 fallback.
- No historical migration is needed by the implemented policy.

This policy is designed to let v1 survive legitimate lifecycle bookkeeping changes while still
rejecting any immutable source drift. Its new PostgreSQL proof did not execute in this task, so
historical v1 resume remains **NOT CERTIFIED**.

## D. Authority comparison

| Operand | Source | Verification |
|---|---|---|
| Stored payload | `TransitionPreflightPlan.run_start_anchor_json` | Exact canonical fingerprint must equal the stored fingerprint |
| Upload identity and stable metadata | Current `UploadRun` selected by the caller-bound run id | Rebuilt through the production anchor builder |
| Raw source membership/content | Current ordered `RawCompanyRow` rows for that run | Count plus semantic-payload fingerprint |
| Context and PIT boundary | Caller-bound `MarketCalculationCutoff`, itself reconstructed from the persisted context on production paths | Exact immutable projection comparison |
| Ticker/source lineage | Current transition results or reconstructed decision manifests | Exact population and per-ticker lineage comparison |
| Handoff binding | `TransitionDecisionHandoffManifest` | Existing payload/fingerprint comparison plus equality to the plan anchor fingerprint |
| Execution authority | Existing pipeline/job, token, generation, cancellation, lease, and superseded-root guards | Unchanged; anchor validity grants no independent execution authority |

## E. Changed files

- `app/services/transition_preflight_plan_service.py`
  - Added explicit v1 and v2 constants while retaining v2 as the default.
  - Extended `build_run_start_anchor_manifest` with explicit supported-contract construction.
  - Added `_verify_run_start_anchor_manifest`, including exact stored-hash verification,
    structural version checks, and independent current-source comparison.
  - Routed enqueue verification, handoff freezing/reuse, and Setup pre-mutation verification
    through the single verifier.
- `tests/integration/test_transition_preflight_plan_postgresql.py`
  - Added a real canonical-v1 continuation case and the required fail-closed matrix.
  - Retained the existing v2 continuation/source-drift case as P1.
- `docs/remediation/calculation-lineage/PHASE2_9_semantic_review_source_pins.json`
  - Refreshed the current reviewed overlay with the official generator; this is not a historical
    certification artifact.
- `docs/remediation/readiness-phase2/TASK_2_9_12I_ANCHOR_VERSION_COMPATIBILITY.md`
- `docs/remediation/readiness-phase2/TASK_2_9_12I_CERTIFICATION.xml`
- `docs/remediation/readiness-phase2/TASK_2_9_12I_COMPATIBILITY_MATRIX.json`

No schema, migration, financial formula, CERI normalization, transaction threshold, writer, or
commit boundary changed.

## F. Positive certification

P1 has retained evidence from Task 2.9.12f: the existing v2 test passed there and remains in the
focused node list. Static inspection confirms new writes still default to v2 and the v2 payload
is unchanged.

P2 is implemented in
`test_historical_v1_anchor_is_verified_without_rewrite_and_fails_closed`. It builds v1 through
the production builder, consumes the plan through `start_pipeline`, freezes/reuses the handoff,
advances only upload lifecycle fields, invokes Setup pre-mutation verification, and asserts the
original v1 JSON/fingerprint remain unchanged. **P2 did not execute in this task.**

The one focused invocation was:

```text
pytest -q tests/integration/test_transition_preflight_plan_postgresql.py::test_two_phase_manifest_accepts_committed_run_local_artifacts_and_is_idempotent tests/integration/test_transition_preflight_plan_postgresql.py::test_historical_v1_anchor_is_verified_without_rewrite_and_fails_closed
```

It exited `1` after 7.9405 seconds during `tests/conftest.py` import with
`ModuleNotFoundError: No module named 'prometheus_client'`. The dependency is declared in both
`pyproject.toml` and `uv.lock`; the command resolved to system Python rather than the repository
virtual environment. No test case, migration, fixture, or PostgreSQL transaction began.

## G. Negative certification

The focused test contains production-path cases for:

- raw-source mutation → `DECISION_MANIFEST_MISMATCH`;
- frozen context mutation → fail closed at the anchor boundary;
- persisted JSON tamper without matching fingerprint → `DECISION_MANIFEST_MISMATCH`;
- unknown contract → `MALFORMED_DECISION_MANIFEST`;
- missing anchor → `MALFORMED_DECISION_MANIFEST`;
- wrong upload-run binding → `STALE_PREFLIGHT`;
- a v2-shaped document labelled v1 → `MALFORMED_DECISION_MANIFEST`.

Each case asserts zero `SetupSignalSnapshot` publication, preserved handoff fingerprint, rollback,
and unchanged durable v1 anchor. These cases were statically compiled and linted but did not run.
The existing v2 raw-source negative passed in retained Task 2.9.12f evidence.

## H. Transactions

The implementation performs reads and comparisons inside caller-owned Sessions and introduces no
commit, rollback, writer, or Session-lifetime change. Existing row locks, handoff uniqueness,
publication ordering, and caller rollback behavior are preserved. Because collection failed,
there is no new transaction-duration or disposable-PostgreSQL runtime observation.

## I. T14 source pins

The changed verifier boundaries were reviewed as read-side authority checks. They add no operation
family or mutation ownership. The official current-overlay generator/validator was run with the
repository virtual environment. Final output:

```json
{"stale": []}
```

No immutable historical T14 artifact was modified.

## J. Safety

- No Chromium, browser, full suite, live pipeline, or provider call was started.
- No normal application data or historical Run 22–33 data was read or changed.
- The failed pytest collection created no disposable database, so no database cleanup was needed.
- No v1 row was migrated or rewritten.
- Existing execution fencing and publication authority were not changed.
- Pre-existing working-tree modifications were preserved; no reset, stash, discard, commit, push,
  merge, or deployment occurred.
- Static checks passed: `py_compile`, `ruff check`, `ruff format --check`, and `git diff --check`.

## K. Compatibility matrix

The machine-readable matrix is in `TASK_2_9_12I_COMPATIBILITY_MATRIX.json`. Expected behavior is
fully specified, but all newly added v1 runtime rows remain `NOT_EXECUTED` in this task.

## L. Remaining blockers

1. **Historical v1 compatibility:** implemented but not runtime-certified because the sole focused
   invocation failed at collection.
2. **Chromium-100 terminal certification:** outstanding and not started.
3. **Full Phase 2.9 release certification:** outstanding and explicitly not implied by this work.

## M. Next action

Under a separately authorized task, use the repository virtual environment to execute exactly the
two recorded compatibility nodes once and preserve native JUnit output. If both pass, Task 2.9.12i
can be closed and only then should **Task 2.9.12j: Final Chromium-100 Certification** be considered.
Do not start either action from this report.

`TASK 2.9.12I HISTORICAL ANCHOR COMPATIBILITY: PARTIAL — FOCUSED POSTGRESQL CERTIFICATION NOT EXECUTED`

