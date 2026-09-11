# SwingLens Basic Operator Path Recovery Findings — 2026-09-11

## A. Executive summary

The three requested root causes were reproduced and the narrow fixes were implemented:

1. Windows lifecycle state now records the `CREATE_NEW_PROCESS_GROUP` leader separately from the inner Python supervisor and sends `CTRL_BREAK_EVENT` only to that strongly verified group identity.
2. The IB preflight UI recognizes authoritative `IB_API_READY`, presents distinct failure states, requires explicit operator confirmation, and uses a revised static-asset URL so stale JavaScript cannot preserve the old mapping.
3. Lifecycle status separates process/socket, application, worker, database/schema, IB, disk, and observability states. The disk threshold warning remains explicitly non-blocking.

The bounded final-readiness retry was also narrowed to transport/database-probe sampling failures. It has three attempts, 300 ms between attempts, emits failure/recovery or exhaustion messages, returns semantic schema/worker/topology/runtime/SEC failures immediately, and leaves an exhausted database failure as `failed`.

Certification is **not complete**. Three clean start/status/stop repetition cycles passed. On clean cycle 4, start and status passed, but canonical stop terminated the detached verification process before returning an exit/result. It stopped the supervisor, web, and worker and released port 8000, but left the version-4 lifecycle state file. Per the task stop condition, no further patch or restart-cycle work was performed. A subsequent fresh canonical stop removed the stale state and exited 0.

## B. PowerShell debugger root cause

Classification: `DEBUGGER_FROM_SCRIPT`.

Fresh `pwsh -NoProfile` processes had zero breakpoints. All PowerShell profile paths were absent, and repository searches found no `Set-PSBreakpoint`, `Wait-Debugger`, `Set-PSDebug`, runspace debugger, or equivalent instrumentation in the lifecycle path. The apparent pause at `SwingLensLifecycle.psm1` line 29 (`$exitCode = $LASTEXITCODE`) was where PowerShell observed the native helper result, not the initiating defect.

The initiating action was `lifecycle_probe.py` calling `os.kill(..., CTRL_BREAK_EVENT)`. On Windows, that event is addressed by process-group ID. The existing runtime state recorded the inner real-interpreter supervisor PID, while `CREATE_NEW_PROCESS_GROUP` had been applied to the outer venv launcher PID. Interactive console routing could therefore surface as debugger entry at the next PowerShell statement.

## C. Stop/restart root cause

The stop path confused a verified application process identity with the Windows process-group leader. Runtime-state version 4 now records both. Stop revalidates the web listener, supervisor, ancestry, creation times, repository/runtime token, and process group immediately before signaling. Version-3 state is handled by deriving the outermost matching runtime launcher.

The narrow unit/integration safety tests pass, and three clean repeated stops exited 0 with no debugger, no processes, port 8000 free, and state removed. However, cycle 4 reproduced an incomplete return/cleanup: all processes exited and port 8000 was free, but the caller terminated before recording an exit and the state file remained. Because restart composes stop followed by start, restart cannot be certified while that stop behavior remains.

## D. IB readiness root cause

Classification: `BACKEND_SUCCESS_UI_MISMAP`.

The authoritative route returned `IB_API_READY`; the browser recognized only legacy `READY`. The resulting modal combined a success detail (`IB Gateway API connection successful.`) with the false title `Interactive Brokers is not connected`.

The UI now accepts `IB_API_READY` (and one-release legacy `READY` compatibility), requires `api_connected=true`, distinguishes process-not-running, process-running/API-not-ready, session-lost, and probe-error states, and shows a positive confirmation modal before any form submission.

IB health uses deterministic, read-only, process-role-specific client IDs 22–25, separate from operational client ID 21. Probes serialize within a process and always disconnect in `finally`.

## E. IB Gateway log correlation

With Gateway open on `127.0.0.1:4002`, the final direct HTTP proof returned in 447 ms:

- HTTP 200
- state `IB_API_READY`
- health client ID 22 (web role)
- connected and API-ready true
- server version 176
- current-time smoke response true
- no error/failure category

SwingLens logs at the corresponding request show connect, connected/logged/API synchronization, current-time synchronization, clean disconnect, and HTTP 200. A post-probe `API Client: disconnected` Gateway display is therefore the expected idle state after a successful short-lived probe, not a readiness failure.

## F. READY/DEGRADED root cause

The live `/ready` payload listed every core component as `ok`; `ib` was informational `optional_unavailable`; only `disk` was `degraded` with `disk_free_warning:13.6%`. Configuration thresholds were warning 15% and critical 5%.

The status output after the fix was:

```text
Database           READY
Schema             HEAD
Web/API            READY
Application        READY
Worker             READY
Disk               WARNING - 13.6% free
IB API             ON DEMAND - verified before IB-required work
Prometheus         READY
Grafana            READY
OVERALL            DEGRADED (non-blocking warnings: Disk)
```

Disk is `NON_BLOCKING_WARNING`; on-demand IB is `INFORMATIONAL`; database/schema/runtime/worker failures remain `BLOCKING`.

## G. Fixes

- Persist and validate the Windows process-group identity independently of inner supervisor/web identities.
- Signal only the revalidated group leader and fail closed on identity change.
- Add IB health client-ID range 22–25, process-role allocation, serialized same-process probes, and client ID in the diagnostic payload.
- Map authoritative IB states to distinct UI semantics and require confirmation before enqueue.
- Rev the shared JavaScript asset URL to defeat stale browser caching.
- Treat optional unavailable capabilities as informational in aggregate readiness.
- Present process, application, worker, database/schema, disk, IB, and observability separately.
- Retry only transient transport/database readiness samples, at most three times with two 300 ms waits; log retry, recovery, and exhaustion.

## H. Manual operator certification

- Canonical start: passed, exit 0, strongly verified web PID, all mandatory components ready.
- Canonical status: passed, exit 0, disk warning isolated from Web/API and worker.
- Run 155 UI: passed. It displayed `IB Connected` and `IB Gateway connected — API ready`; Cancel was clicked. No pipeline was submitted and active business-job count remained zero.
- Canonical restart: not certified in this final run because the required three restart repetitions were not reached after the stop failure.
- Canonical stop: standalone cleanup passed, and cycles 1–3 passed; cycle 4 failed to return and left stale state despite releasing all processes and the port.

Repeated result:

- Required start/status/stop cycles: 5
- Fully passed: 3
- Cycle 4: start/status passed; stop incomplete as described above
- Restart cycles completed after repetition gate: 0 of 3
- Duplicate logical runtimes/workers observed in completed cycles: 0
- Orphans after completed stops: 0
- Active unrelated business jobs: 0
- Final cleanup: port 8000 free, zero SwingLens runtime processes, state absent

## I. Regression tests

Focused lifecycle, process-identity, readiness, IB, UI mapping, pre-enqueue gate, and worker-supervisor tests: **121 passed** with 22 dependency/deprecation warnings. Ruff over the relevant application/scripts/tests scope passed.

The readiness retry tests specifically prove recovery logging, immediate return for migration/worker/topology/runtime/SEC semantic failures, and persistent database failure remaining failed after the strict three-attempt budget.

No SEC clone matrix, temporal/PIT certification, production pipeline, canary, or historical repair was run.

## J. Residual risks

The cycle-4 stop non-return/stale-state result is a certification blocker. Although process and port cleanup completed and a subsequent standalone stop cleaned the state, a lifecycle command must also return deterministically and finish state cleanup. Until that exact behavior is diagnosed in a separate authorized task and the full 5/3 repetition gate passes, final canary work is not safe to resume.

