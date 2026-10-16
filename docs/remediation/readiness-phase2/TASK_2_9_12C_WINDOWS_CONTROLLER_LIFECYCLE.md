# Task 2.9.12c Windows Controller Lifecycle Certification

## Verdict

**PASS.** The exact controller-exit and generation-reuse case passed.

`TASK 2.9.12C WINDOWS CONTROLLER LIFECYCLE: CLOSED`

## Timing and command

- Unit start: `2026-10-14T16:20:15.1585945+02:00`.
- Unit deadline: `2026-10-14T16:40:15.1585945+02:00` (20 minutes).
- Pytest timestamp: `2026-10-14T16:20:26.617801+02:00`.
- Finish: approximately `2026-10-14T16:22:10.372+02:00`.
- Elapsed: `103.754` seconds; case time `103.544` seconds.

```text
.\.venv\Scripts\python.exe -m pytest \
  'tests/e2e/test_windows_lifecycle_persistence.py::test_windows_runtime_survives_controller_exit_and_reuses_generation' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12C_CERTIFICATION.xml
```

## Root cause and correction

The retained test failed in 1.041 seconds before launch because its state constant
pointed at `data/cache/swinglens-lifecycle.json`. It required that canonical file to
be absent, read it after start, and unlinked it during cleanup. The test therefore
conflicted with an existing user's runtime and could have deleted unrelated state.

The fixture now creates `swinglens-lifecycle.json` beneath its pytest `tmp_path`,
passes the path as `SWINGLENS_LIFECYCLE_STATE_PATH`, and makes the controller
subprocess plus imported production-equivalent probe resolve the same task-owned
file. Every read, reuse check, stop operation, and unlink is scoped to that path. No
global Windows default was changed.

## Lifecycle and generation proof

The passing assertions proved:

- the first controller returned successfully and its web/supervisor/worker children
  remained alive for the bounded 45-second controller-exit window;
- the second controller reused exactly the first `runtimeInstanceId`, supervisor
  identity, web identity, and database supervisor/worker registrations;
- exactly one supervisor, one web process, and one worker matched that runtime;
- no stale-state retirement occurred;
- graceful stop emitted all four required shutdown events;
- all three isolated ports lost their listeners and all three task-owned process roles
  reached count zero.

The retained supervisor-state artifact showed runtime instance `3728…`, supervisor
and worker registration generation 1, zero worker/web restart failures, both child
states `RUNNING`, and `cycle_failed=false`. Generation 1 was reused rather than
spuriously replaced.

## Isolation and cleanup

- The disposable PostgreSQL fixture, unique worker ID, three unique loopback ports,
  temporary lifecycle state, temporary supervisor state, and evidence directory were
  independent of local runtime state.
- The canonical repository lifecycle file remained absent before and after the case.
- Final process inspection found no command line containing the test's
  `windows-persistence-` identity.
- Provider ingest, Winner automatic workflows, and market-data prewarm were disabled;
  no live provider was invoked.
- Cleanup removed only the task-owned state file after the test-owned processes had
  stopped.
