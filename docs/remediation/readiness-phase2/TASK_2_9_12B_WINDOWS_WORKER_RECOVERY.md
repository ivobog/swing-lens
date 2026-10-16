# Task 2.9.12b Windows Worker Recovery Certification

## Verdict

**PASS.** Both exact parameterized cases passed in one focused invocation.

`TASK 2.9.12B WINDOWS WORKER RECOVERY: CLOSED`

## Timing and command

- Unit start: `2026-10-14T16:17:05.1625320+02:00`.
- Unit deadline: `2026-10-14T16:42:05.1625320+02:00` (25 minutes).
- Pytest timestamp: `2026-10-14T16:17:17.955860+02:00`.
- Finish: approximately `2026-10-14T16:19:34.543+02:00`.
- Elapsed: `136.587` seconds.

```text
.\.venv\Scripts\python.exe -m pytest \
  'tests/e2e/test_windows_durable_worker_recovery.py::test_windows_worker_kill_self_heals_repeatedly[0]' \
  'tests/e2e/test_windows_durable_worker_recovery.py::test_windows_worker_kill_self_heals_repeatedly[1]' \
  -q --tb=short \
  --junitxml=docs/remediation/readiness-phase2/TASK_2_9_12B_CERTIFICATION.xml
```

| Case | JUnit time | Verdict |
|---|---:|---|
| `[0]` | 69.639 s | PASS |
| `[1]` | 66.712 s | PASS |

## Root cause and correction

The retained runs had already replaced the killed worker, resumed and completed the
50-checkpoint recovery probe, completed a new 3-checkpoint job, and proved a live
worker registration. They then timed out because readiness and web replacement were
hard-coded to port 8000.

The fixture now selects a free loopback port for each parameter, verifies it has no
listener, passes it through both the child environment and `Settings`, and uses that
same port for readiness, listener ownership, controlled web termination, replacement,
and post-restart readiness. A web PID is terminated only after proving it descends
from the test-owned supervisor. No process-name or global kill was introduced.

## Recovery evidence

Both state files reported:

- supervisor registration generation 1;
- replacement worker registration generation 2;
- `DURABLE_WORKER_PROCESS_LOST` as the controlled worker failure reason;
- worker restart count 1 and state `RUNNING`;
- `WEB_CHILD_EXITED` as the controlled web failure reason;
- web restart count 1 and state `RUNNING`;
- `cycle_failed=false` and no crash loop.

Case `[0]` changed from worker instance `47c…` to `d53…`; case `[1]` changed from
its independent generation-1 instance to `7c2…`. Full ephemeral identifiers remain
in the pytest temporary supervisor-state artifacts. The readiness assertions proved
`supervisor`, `web`, `worker`, and `topology` check states were all `ok` before the
web fault, and readiness returned `ok` again after replacement.

## Isolation and cleanup

- Each parameter used a separate disposable database, temporary supervisor-state
  file, loopback port, lifecycle operation ID, configuration fingerprint, and runtime
  instance ID.
- Provider ingest and automatic Winner work were disabled; no live IB, SEC, EODHD,
  or CERI provider path ran.
- Final process inspection found no command line containing the test runtime prefix.
- Cleanup targeted only each test supervisor process group. No existing local runtime
  was stopped or queried through port 8000.
- The second case did not reuse the first case's state or generation.
