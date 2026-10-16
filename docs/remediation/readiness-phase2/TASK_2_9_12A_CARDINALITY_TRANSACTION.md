# Task 2.9.12a Cardinality and Transaction Certification

## Verdict

**FAIL.** The single authorized final certification failed. This work unit stopped
without a second correction or rerun.

## Timing and command

- Unit start: `2026-10-14T15:55:56.5013810+02:00`.
- Unit deadline: `2026-10-14T16:20:56.5013810+02:00` (25 minutes).
- Pytest timestamp: `2026-10-14T15:56:53.594659+02:00`.
- Pytest elapsed: `1157.047` seconds; case elapsed: `1155.848` seconds.
- Result: `1 failed, 238 warnings in 1157.12s`.

```text
.\.venv\Scripts\python.exe -m pytest \
  'tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-100]' \
  --browser chromium -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12A_CERTIFICATION.xml
```

## Original transaction and reviewed correction

The original retained run failed at `120.404372` seconds with a PostgreSQL backend
`idle in transaction`; its last statement loaded the frozen
`market_calculation_contexts` row. The certification enables the Technical artifact
cache, selecting the pure-sequential scorer. That path loaded a database-backed work
item and then performed CPU calculation while keeping the ORM transaction open.

`app/services/technical_score_service.py` now closes only a pipeline-owned read
transaction after each immutable `TechnicalWorkItem` is materialized and before its
CPU calculation. Independent local artifact-cache writes are committed before the
next calculation. On a failed item the local read/cache transaction is rolled back.
The fenced `finalize_technical_scores` publication transaction remains unchanged and
still publishes the complete Technical score/evidence set atomically.

This correction did remove the retained Technical checkpoint failure: the final run
continued through Technical scoring, the asynchronous provider/normalization/feature
graph, CERI finalization, CERI capture, change detection, alert rebuild, and the
decision-handoff manifest. The unchanged runtime monitor recorded clean observations
through those stages.

## Final failure

At `2026-10-14T14:15:48.099097+00:00`, during the resumed pipeline's
`CAPTURING_SETUP_SIGNALS` stage, the monitor found a different backend at
`120.397988` seconds. Its last statement loaded the exact frozen
`execution_configuration_anchors` row by `anchor_id`; PostgreSQL reported
`idle in transaction`, `ClientRead`.

The preceding `FREEZING_DECISION_HANDOFF_MANIFEST` stage completed in `33224.306` ms,
and `CAPTURING_SETUP_SIGNALS` began at `2026-10-14T14:13:47.658632+00:00`. This proves
the remaining breach is downstream configuration-delivery/setup-capture transaction
scope, not the original Technical read interval.

The exact traceback is retained in `TASK_2_9_12A_CERTIFICATION.xml`; a concise
redacted checkpoint copy is retained in `TASK_2_9_12A_FAILURE_DIAGNOSTIC.log`.

## Contract and safety outcome

- The 120-second threshold and transaction observer were not changed.
- No fake calculation, population reduction, source relaxation, or fencing bypass was
  introduced.
- Because the observer stopped the pipeline before completion, the final 100-member
  financial counts and publication assertions were not reached and are **not
  certified**.
- The generated disposable database was removed by teardown. No matching runtime
  process or test-port listener remained.
- Deterministic/frozen adapters were used; no live provider was called.
- T14 source pins were regenerated with the official current-overlay generator after
  reviewing the new read/cache transaction checkpoints and validated as
  `{"stale": []}`.

## Remaining blocker

The next separately authorized correction must materialize the exact frozen
configuration/setup-capture inputs, close their read transaction before long CPU or
orchestration work, and revalidate the anchor plus execution ownership at the
authoritative write boundary. Only then may this exact browser case receive a new
focused certification.

No Task 2.9.12a closure statement is issued.
