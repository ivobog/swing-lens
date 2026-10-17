# Run 40 remediation on GitHub main

The Run 40 correction was integrated onto GitHub main as it stood at
`d3b66c32767257d1187719206fa948656ffe3045`. The original forensic evidence
and the earlier [isolated certification report](REPORT.md) remain available.
The isolated report describes the candidate before this merge; the results below
apply to the combined main code.

The combined code removes healthy-job watchdog row locking, keeps transient
control-plane retries claimable with matching parent authority, publishes Setup
lifecycle output atomically, validates replay against retained evidence, and
reports useful progress during the 88-symbol workload. Main's newer technical
and ranking evidence batching was retained. Setup's nested semantic writers now
share the containing publication savepoint, while every final domain commit
keeps its ownership fence. The lifecycle progress adapter records control-plane
progress without asking main's checkpoint helper to commit unfinished lifecycle
work. The runtime observer now treats a long worker transaction as healthy only
while this pipeline has a current lease, worker and control-loop heartbeats,
recent durable progress, and no cancellation request. It still reports stale
transactions and unrelated sessions. Certification's worker child inherited
`PYTEST_CURRENT_TEST`; explicit worker, supervisor and web entry points now take
precedence when assigning a database connection's process role.

The [forensic investigation](../../forensics/run40-lifecycle-failure/REPORT.md)
proves the original 16.916-second supervisor lock hold and the two heartbeats
that exceeded the unchanged 3-second lock timeout. It also documents the 937
events left by the failed evaluation and the unclaimable queued retry.

## Integrated verification

The combined source passed 517 affected unit tests, 34 database-monitor unit
tests, 14 distinct disposable PostgreSQL Run 40 cases, and the architecture
registry check with zero
exceptions. PostgreSQL coverage includes a delayed healthy watchdog, true
fencing, actual worker retry and continuation claims, exhaustion, cancellation,
failed-SQL rollback, native two-symbol publication/replay, retained original
proofs and alerts, Winner continuation, technical/ranking interruption, and
active-versus-stalled transaction observation.

The clean 10-symbol browser pipeline passed on the combined code: 10 each of
fundamental, technical and combined results, published Setup and Winner outputs,
60 provider source records, and no CERI stall or recovery. Total pipeline time was 166.899 seconds;
the longest CERI checkpoint gap was 2.069 seconds. The first integration attempt
failed because main's lifecycle progress adapter requested a mutation commit
inside the new atomic writer savepoint. The adapter was corrected; the passing
browser rerun and the updated unit contract verify the correction.
[Passing 10-symbol JUnit](evidence/main-browser-10.xml) and
[provider metrics](evidence/main-provider-10-metrics.json) are retained.

The first 88-symbol browser attempt reached lifecycle evaluation, where main's
old blanket 120-second PostgreSQL transaction-age rule rejected an atomic
writer transaction without checking whether worker progress remained healthy.
The observer correction has a real PostgreSQL regression covering
healthy progress, stale progress, cancellation, and an unrelated connection.
The next strict run exposed inherited pytest context labeling the worker's
connection `swinglens-test`; it was rejected by the narrower observer role
check. The process-role order was corrected and 34 monitor tests passed,
including explicit worker/supervisor/web entry points under inherited pytest
context. Both failed strict attempts and their passing comprehensive companion
results are retained:
[blanket observer failure](evidence/main-browser-88-observer-failure.xml),
[role labeling failure](evidence/main-browser-88-role-failure.xml),
[observer policy regression](evidence/main-observer-policy.xml),
[role-order tests](evidence/main-role-order.xml), and
[companion comprehensive report](evidence/main-comprehensive-role-failure.json).
The final strict 88-symbol browser test passed on clean source `17b5f4a`:
terminal COMPLETED, 88 each of the required native outputs, 528 provider
source records, exactly two FULL_PIPELINE jobs, no stalls or recoveries, and
2,316 runtime observations with zero invariant failures. Pipeline time was
1,294.627 seconds; CERI workflow time was 407.286 seconds with a 14.345-second
maximum checkpoint gap. The observer correctly allowed the healthy atomic
lifecycle writer transaction after it exceeded the prior 120-second limit;
[live transaction evidence](evidence/main-active-atomic-transaction.txt) and
[88-symbol metrics](evidence/main-provider-88-metrics.json) are retained.
The comprehensive browser regression also passed on that source: all 13 stages
completed, 623/623 comparisons passed, 19 GUI surfaces and 49 database
relationships checked, zero failures. The combined batch was 2 passed in
1,957.68 seconds. Its [JUnit](evidence/main-browser-88-comprehensive-final.xml)
and [report](evidence/main-comprehensive-final.json) are retained.

The final browser command, run from the clean integration worktree using the
shared virtual environment and the repository's disposable PostgreSQL fixture,
was:

```text
python -m pytest
  tests/e2e/single_run_certification/test_real_writer_cardinality.py::test_frozen_real_writer_cardinality[chromium-88]
  tests/e2e/single_run_certification/test_single_run_certification.py::test_single_run_comprehensive_e2e_certification[chromium]
  -q --tb=short
```

`ruff check` passed for all changed Python files; `git diff --check` passed.
The architecture check reported zero exceptions. `ruff format --check` flags
two formatting differences in `background_job_service.py` and its unit test;
both are present on the pre-remediation GitHub main and outside the edited
lines. The certified source was left unchanged.

| Final verification | Result |
|---|---|
| Watchdog lock contention and genuine stall fencing | PASS |
| Infrastructure retry, claimability, exhaustion and cancellation | PASS |
| Lifecycle publication and event replay idempotency | PASS |
| Downstream alerts and Winner compatibility | PASS |
| 88-symbol evaluation and complete pipeline | PASS |
| Comprehensive browser regression | PASS |
| Safe to start a new production run now | NO: deploy this source and complete release checks first |

## Release boundary

No production process was restarted and Run 40's production records were not
retried, repaired or republished. Committing and pushing source to GitHub does
not activate that source in the running production processes. The original
failed pipeline, evaluation, queued job and retained events require a separate
audited disposition. Keep the failed history and prefer a fresh run after an
authorized clean deployment and release checks.

The earlier native 88-symbol case with 321 historical snapshots passed in an
isolated cold process. Its [JUnit](evidence/native-88-final-runtime.xml) and
[measurement](evidence/final-runtime-88.json) belong to that earlier source
version. The integrated main version has direct PostgreSQL replay tests and its
own passing 88-symbol browser pipeline. The earlier native history measurements
and the integrated browser measurements cover different datasets and must not
be used to infer a speedup ratio.
