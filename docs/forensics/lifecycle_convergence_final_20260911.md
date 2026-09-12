# SwingLens lifecycle convergence final forensic report — 2026-09-11

## 1. Executive verdict

**NOT CERTIFIED.** Stopped-generation convergence was remediated in `7d3a950d...`, the
calendar-sensitive fixture in `65d0343...`, and the Windows command-boundary disappearance and
graceful-stop ordering race were remediated by the final executable tree at `0267ebd...`. Exact-SHA
CI run `34674107533` is green. The authorized final-SHA machine probe proved cross-command survival
after the controller boundary and exact idempotent reuse of the same supervisor, WEB, worker, and
runtime instance. Its required first `stop`, however, failed before issuing the shutdown request
because the durable worker did not acknowledge quiescence within the controller timeout. Per the
explicit no-retry rule, the bounded restart certification was not run. Required diagnose evidence
was captured, and the single permitted safe-containment stop then exercised the remediated orderly
shutdown path successfully and returned the machine to canonical `STOPPED`, with zero active jobs,
all lifecycle ports free, and PostgreSQL still running. Every prior failed attempt below remains
intact and traceable.

## 2. Original baseline

The preserved baseline is
[`lifecycle_convergence_baseline_20260911.md`](lifecycle_convergence_baseline_20260911.md). Its
starting point was branch `codex/final-certification-runtime-topology-remediation`, SHA
`e61bdbd76551a78d5c83cf077f7093f94c66e86b`, with no live SwingLens runtime, authoritative local
PostgreSQL 18.3 at schema head `0072_ceri_artifact_context_lineage`, no active business jobs, and no
running observability stack. Baseline CI run `34601813963` was red because disposable CI databases
were being subjected to authoritative-machine provenance rules.

Before the interrupted final certification at `095a552`, remediation had already established
authoritative PostgreSQL provenance, a clean preflight, first and idempotent starts, healthy
supervisor/web/worker topology, and available Prometheus/Grafana with only a low-disk warning.

## 3. Root-cause tree

```text
Lifecycle non-convergence
├── Ambiguous runtime ownership (remediated)
│   ├── web-owned supervisor path
│   ├── SupervisorProcessManager ownership path
│   └── process-group leader confused with inner Python supervisor
├── Unsafe environment/database inference (remediated)
│   ├── CI/pytest presence implicitly selected trust semantics
│   └── disposable databases were checked as authoritative local databases
├── Over-broad startup readiness gate (remediated)
│   └── full application /ready made optional integrations block core startup
├── Incomplete operation correlation/diagnostics (remediated)
│   └── restart phases and children lacked one durable operation/generation identity
└── Stopped-generation convergence defect (REMEDIATED in 7d3a950)
    ├── interrupted restart stopped the old tree but left its version-5 state record
    ├── checkout advanced from 095a552 to tree-identical merge SHA 0fb435e
    ├── desired fingerprint changed solely because the Git SHA is fingerprinted
    ├── no recorded process is alive and lifecycle ports are free
    └── start, stop, and status reject/classify the stale record before retiring it
```

## 4. Final canonical supervisor-root topology

The accepted NORMAL topology is `supervisor-root-v1`:

```text
swinglens.ps1 / lifecycle controller
  -> app.worker_supervisor (process-group and restart owner)
       -> app.serve (WEB)
       -> app.worker (DURABLE_WORKER)
```

The FastAPI lifespan does not launch a supervisor, `SupervisorProcessManager` is removed, and the
legacy worker flag is not an ownership switch. The final failure occurred before this tree launched;
it did not resurrect any forbidden ownership path.

## 5. Database safety-context result

The runtime uses explicit `AUTHORITATIVE_LOCAL` safety semantics. Final probes verified:

- endpoint `postgresql+psycopg://127.0.0.1:5432/swinglens` (credentials omitted);
- PostgreSQL `18.3`;
- Windows service `postgresql-x64-18`;
- executable `C:\Program Files\PostgreSQL\18\bin\postgres.exe` for listener PID `6976`;
- data directory `C:/Program Files/PostgreSQL/18/data`;
- current and expected Alembic head `0072_ceri_artifact_context_lineage`;
- `USE_DURABLE_PIPELINE=true`;
- provenance verified and schema at head.

Disposable CI uses explicit `DISPOSABLE_TEST` with an allowed disposable database prefix and rejects
the active SwingLens database. No CI/pytest ambient-variable inference remains.

## 6. Readiness split result

The implemented contract is:

- `/health`: web-process liveness;
- `/ready/core`: authoritative database/schema/storage plus canonical supervisor/web/worker identity,
  registration freshness, ancestry, and mandatory listeners;
- `/ready`: comprehensive application-operational health, including optional providers and queues.

At the recovered stopped checkpoint and at final physical shutdown, all three HTTP endpoints were
unreachable as expected because port 8000 had no listener. Prior to interruption, the running
`095a552` runtime had passed `/health` and `/ready/core`; `/ready` was degraded only by the existing
low-disk warning. The reconciled runtime never launched, so no post-reconciliation HTTP readiness
claim is made.

## 7. Runtime-generation result

The generation covers Git SHA, checkout, Python executable, runtime mode, topology version,
authoritative database identity, Alembic heads, critical configuration, ports, and worker modes.

- stale recorded SHA: `095a552e21f92523a7d8e67df2109742a0cad0a9`;
- stale fingerprint: `e54e16bd93327d546a0584d6600711bef6bc397b8ce400ac0be66e1fb4795ffa`;
- desired reconciled SHA: `0fb435ee6eb76049ddf6d76d859775fec35768f5`;
- desired fingerprint: `156a71dbbf347e9b8725282a589c380c81757f3f73e55b680b9f3a1afa5fafe3`;
- stale runtime instance: `44718fd5e4414bf4b3e9488a4b9e7c9b`;
- topology version in both records: `supervisor-root-v1`.

Active-generation mismatch is correctly fail-closed. The unresolved problem is that the same guard
also blocks convergence when every recorded process is dead and every lifecycle port is released.

## 8. Crash-loop behavior

The supervisor implements bounded, per-child restart windows and reason/stage reporting. Windows
durable worker-kill recovery passed in exact-SHA CI run `34634676999`; the preceding exact-SHA main
run `34633478836` also passed it. The last real runtime's supervisor state showed zero web/worker
restart failures and `cycle_failed=false` before shutdown. No real-machine crash-loop exercise was
performed after reconciliation because the first canonical start failed before launch.

## 9. Lifecycle operation correlation / flight recorder

The lifecycle journal is `logs/lifecycle/lifecycle.jsonl`. The interrupted and final operations were:

| Operation ID | Action | Result | Evidence |
|---|---|---|---|
| `02550f9b-a6ba-4ee9-8d77-026ebfad3e86` | interrupted `restart` | no terminal event | begin at 17:34:49Z; old web/worker shutdown events followed; no replacement boot |
| `69a9bad8-b70e-4af7-9df9-903c27830157` | final `start` | failure | `RESTART_REQUIRED`, generation fingerprint differs |
| `74e0a150-a8e5-4679-b28c-c28bd5df985b` | post-failure `diagnose` | success | sanitized bundle captured |
| `7d98c1dc-0328-4b79-896f-8337e11e75e1` | safety `stop` | failure | same `RESTART_REQUIRED` conflict |
| `73aea43a-8611-4094-a90d-aee70d2c21ee` | final `status` | failure classification | `CONFLICT`, runtime null, no active jobs |

The original successful start operation `0167a896-2072-4c53-a470-55e4e8011da1` propagated one
operation ID, runtime instance ID, Git SHA, and fingerprint through supervisor, web, and worker logs.

## 10. Prometheus/Grafana lifecycle observability

Prometheus (`127.0.0.1:9090`) and Grafana (`127.0.0.1:3000`) remained reachable. Grafana health was
HTTP 200 with database `ok`; Prometheus `/-/ready` returned ready. At physical runtime shutdown the
three targets `swinglens-web`, `swinglens-worker`, and `swinglens-supervisor` were all `down` with
connection-refused scrape errors, which matches the stopped-runtime observability contract. No
claim of 3/3 targets up is made for the reconciled SHA because the runtime did not start.

## 11. Diagnose command result

Recovery diagnose succeeded at bundle
`artifacts/diagnostics/lifecycle-20260911T183547Z-1320cc31-4e7b-4046-b249-aa4de978d55f`.
Post-failure diagnose succeeded at bundle
`artifacts/diagnostics/lifecycle-20260911T184458Z-74e0a150-a8e5-4679-b28c-c28bd5df985b`.
The latter classified the most likely boundary as `CORE_RUNTIME_STOPPED_OR_UNREACHABLE`, captured
zero active jobs, verified PostgreSQL/schema state, and preserved desired/stale generation evidence.
Both bundles were read-only with respect to business state.

## 12. CI failures encountered during remediation and fixes

- Run `34601813963` at `e61bdbd`: PostgreSQL 16 Linux and temporary PostgreSQL 18 Windows databases
  were incorrectly treated as authoritative local. Fixed by explicit safety contexts and disposable
  database naming/rejection rules.
- Run `34621645170` at `93ef856`: Chromium/Firefox smoke and populated restore failed; the restore
  used an invalid `pg_dump` URL form. Fixed by the PostgreSQL 18/browser certification harness changes
  in `329f3d7`.
- Run `34624686236` at `329f3d7`: 13 full-suite failures exposed POSIX listener evidence assumptions,
  dotenv provenance fixture assumptions, and one winner reliability fixture. Fixed narrowly in
  `095a552`.
- Run `34626305077` at `095a552`: all canonical CI jobs green.
- Reconciled exact-SHA runs `34633478836` (main) and `34634676999` (remediation branch) at `0fb435e`:
  all required non-nightly jobs green.

## 13. Previous interrupted restart classification

**`RESTART_PARTIALLY_COMPLETED`.** Operation `02550f9b-a6ba-4ee9-8d77-026ebfad3e86` has an
`operation_begin` but no `operation_complete`. The old runtime's worker and web emitted shutdown
events, registrations have stopping timestamps, all recorded PIDs are absent, and ports
8000/9101/9102 are released. There is no replacement supervisor/web/worker boot after the restart
begin. Thus the stop phase completed physically, the start phase did not occur, and the operation
did not close its journal/state transaction.

## 14. Branch/main reconciliation details

After `git fetch origin`:

- `git rev-list --left-right --count origin/main...HEAD` returned `4 0`;
- merge base was `095a552e21f92523a7d8e67df2109742a0cad0a9`;
- main-only commits were merge commits `91806dc`, `48503ad`, `0ed4664`, and `0fb435e`;
- `git diff HEAD origin/main` was empty;
- both trees were `72685182542448b53b660f979cc195f3ec36c93f`.

There was therefore no semantic overlap to resolve and no risk of resurrecting web-owned supervisor,
`SupervisorProcessManager`, implicit disposable-database authority, or `/ready` as the startup gate.
With the runtime physically stopped, `git merge --ff-only origin/main` safely advanced the branch to
`0fb435e`, and that fast-forward was pushed.

## 15. Exact final branch and SHA

- Branch: `codex/final-certification-runtime-topology-remediation`
- Reconciled/runtime-tested SHA: `0fb435ee6eb76049ddf6d76d859775fec35768f5`
- Reconciled tree: `72685182542448b53b660f979cc195f3ec36c93f`

The eventual documentation-only closure commit containing this report does not alter the tested
runtime tree; the SHA above is the exact executable tree used for the final attempt.

## 16. Exact GitHub CI run and result

- Required branch push run: `34634676999`
- SHA: `0fb435ee6eb76049ddf6d76d859775fec35768f5`
- Result: **SUCCESS**
- Green jobs: `Lint, Test, and Migration Gates`, `Chromium and Firefox Smoke`, and
  `Windows Durable Worker Recovery Gate`.
- Green required steps include lint, tracked-secret scan, route inventory, Alembic head graph,
  upgrade/current/metadata drift, clean migration harness, populated backup/restore, full unit and
  service suite, golden scoring, Chromium+Firefox smoke, and repeated Windows worker-kill recovery.
- `Nightly Performance Baseline` was event-conditionally skipped and is not a canonical push gate.

The same SHA also completed green on main in run `34633478836`.

## 17. Final real-machine sequence

Preconditions passed: exact reconciled SHA, clean tracked/untracked status after locally excluding
preserved diagnostic bundles, zero active jobs, verified authoritative PostgreSQL, schema at head,
and no live runtime.

| Command | Result |
|---|---|
| `swinglens.ps1 start` | **FAIL**, exit 1, `RESTART_REQUIRED`, operation `69a9bad8-b70e-4af7-9df9-903c27830157` |
| second `start` | not run; bounded sequence stopped on first failure |
| running `status` | not run; runtime never launched |
| `restart` | not run; no live retry loop permitted |
| post-restart `status` | not run |
| required failure `diagnose` | **PASS**, operation `74e0a150-a8e5-4679-b28c-c28bd5df985b` |
| required safety `stop` | **FAIL**, exit 1, same `RESTART_REQUIRED`, operation `7d98c1dc-0328-4b79-896f-8337e11e75e1` |
| final read-only `status -Json` | `CONFLICT`, runtime null, zero active jobs |

No second live certification sequence was attempted and no live patch/retry loop was entered.

## 18. Final process/listener state

- WEB: absent;
- SUPERVISOR: absent;
- DURABLE_WORKER: absent;
- port 8000: released;
- port 9101: released;
- port 9102: released;
- PostgreSQL: still running, listener PID `6976`, port 5432;
- Prometheus/Grafana: still running through Docker Desktop, ports 9090/3000;
- runtime state: stale version-5 record from `095a552` remains;
- controller state: `CONFLICT`, not canonically stopped.

## 19. Remaining warnings

- Local disk free space is 13.8%, below the configured 15% warning threshold but above the 5%
  critical threshold. This is the pre-existing non-blocking low-disk degradation.
- Prometheus's three SwingLens targets are down because the runtime is stopped; Prometheus and Grafana
  themselves are healthy.
- The stale runtime state makes `status`, `start`, and `stop` conflict after the checkout-generation
  change.

## 20. Remaining risks

Stopped-generation convergence is remediated and covered by deterministic Python and PowerShell
regressions. The remaining certification gap is procedural evidence: the second bounded machine
sequence was interrupted during its required restart and, under the explicit no-retry rule, could
not be resumed. The branch therefore remains not lifecycle-certified even though the interrupted
generation subsequently converged safely through `stop` and the machine reached canonical
`STOPPED`. A future explicitly authorized bounded certification would be required to establish a
terminal successful restart and post-restart readiness sequence.

## 21. Exact tests executed

Local focused command:

```text
.venv\Scripts\python.exe -m pytest -q
  tests/ops/test_lifecycle_config.py
  tests/ops/test_lifecycle_contracts.py
  tests/ops/test_lifecycle_convergence.py
  tests/ops/test_lifecycle_observability.py
  tests/ops/test_lifecycle_powershell.py
  tests/ops/test_lifecycle_safety.py
```

Result: **95 passed, 1 dependency deprecation warning, 104.36 seconds**.

Canonical CI run `34634676999` executed the Linux full suite and golden gate, migrations and drift
checks, populated restore, browser certification in Chromium and Firefox, Windows durable worker
recovery, lint, route inventory, and tracked-secret scan. No already-green full suite was repeated
locally.

## 22. Final `git status --short`

Clean after committing this report. Recovery/failure diagnostic bundles remain preserved locally and
are excluded only through `.git/info/exclude`; they were not deleted or added to production evidence.

## 23. Original attempt business-data mutation confirmation

No business pipeline, provider processing, Winner/CERI operation, broker work, enqueue, historical
repair, canary, or evidence mutation was performed during recovery, reconciliation, CI observation,
or final certification. The reconciled runtime never started. Read-only status/diagnose probes and
lifecycle journal/metrics writes were the only operational observations; PostgreSQL business data
remained untouched.

## 24. Final stopped-generation convergence remediation — 2026-09-12

### Defect and exact code cause

The original failure was reproduced as a persisted version-5 runtime state from Git SHA `095a552`
whose WEB, SUPERVISOR, process-group, and worker processes were dead while ports 8000, 9101, and
9102 were free. `scripts/ops/lifecycle_probe.py::_runtime_state_report()` compared the persisted
runtime fingerprint with the desired fingerprint before inspecting the recorded WEB PID. Because
Git SHA remains intentionally included in the fingerprint, the tree-identical merge SHA change made
the dead record return `RESTART_REQUIRED` before it could reach the existing stale-state path.

Remediation commit `7d3a950d449581fea73fe1486449a0a52742ab24` implements the explicit state
machine `ACTIVE_VALID`, `ACTIVE_GENERATION_MISMATCH`, `DEAD_STALE`, `AMBIGUOUS_CONFLICT`, and
`MISSING`. Process identity, PID creation time, repository, runtime instance, ancestry/topology, and
listener evidence are evaluated before generation mismatch semantics. A generation mismatch still
returns `RESTART_REQUIRED` when the runtime is strongly verified alive. A record becomes
`DEAD_STALE` only after every recorded identity available from the canonical and supervisor state
is absent, no canonical WEB/SUPERVISOR/DURABLE_WORKER process exists under this checkout, and ports
8000/9101/9102 are unoccupied. PID reuse, partial survival, unknown role processes, listener
occupancy, corrupt state, and incomplete evidence remain fail-closed as `AMBIGUOUS_CONFLICT`.

### Status and controlled retirement policy

`status` is read-only with respect to runtime state. It now reports a safely dead record as overall
`STOPPED`, `runtimeActive=false`, `staleStatePresent=true`, and `conflict=false`, together with safe
recorded/desired Git SHA and fingerprint values, stale runtime instance ID, and stale reason.

Only `start`, `stop`, and `restart`, while already inside the lifecycle repository mutex/file lock,
invoke stale-state retirement. The record is revalidated against processes and listeners, moved to
`data/cache/lifecycle-archive`, and journaled as `stale_runtime_state_retired` with reason
`DEAD_GENERATION_RETIRED`. The event contains only the old runtime instance, old/desired Git SHA and
fingerprint, topology version, and retirement operation ID. Active and ambiguous states are never
retired by this path.

### Regression evidence

The focused local gate passed **115 tests** in 116.97 seconds, covering lifecycle config, contracts,
convergence, observability, PowerShell controller behavior, process safety, and the new
stopped-generation fixture. The permanent regressions include:

- dead recorded WEB with same or different fingerprint -> `DEAD_STALE`;
- strongly verified active old generation -> `RESTART_REQUIRED`;
- reused recorded PID, surviving supervisor/worker, unknown canonical role, and occupancy of each
  of 8000/9101/9102 -> `AMBIGUOUS_CONFLICT`;
- dead stale `status` -> `STOPPED` without state mutation;
- dead stale `start`/`stop`/`restart` -> controlled retirement and clean/idempotent behavior;
- Git-SHA-only generation change -> different fingerprint, with dead convergence and active
  protection both preserved;
- interrupted-restart persisted-state fixture -> next start retires the record and launches cleanly.

Repository-wide Ruff and `git diff --check` also passed before the remediation commit.

### Exact-SHA canonical CI

- Runtime remediation SHA: `7d3a950d449581fea73fe1486449a0a52742ab24`.
- GitHub Actions run: `34639244871`.
- Result: **SUCCESS**.
- `Lint, Test, and Migration Gates`: passed in 14m52s, including secret scan, route inventory,
  Alembic graph/current/drift, clean migration, populated restore, full unit/service coverage, and
  golden scoring.
- `Chromium and Firefox Smoke`: passed in 3m12s.
- `Windows Durable Worker Recovery Gate`: passed in 4m48s.
- The scheduled-only nightly performance job was correctly skipped for the push event.

### Second bounded real-machine attempt

The original failed attempt in sections 9, 17, and 18 is preserved. After exact-SHA CI became green,
one new bounded attempt began at `7d3a950`:

| Command / operation | Result |
|---|---|
| pre-retirement `status`, operation `3cc0b5d5-c50b-4425-baf7-2f48638ece03` | **PASS** — `OVERALL STOPPED`; stale state explicitly reported; no conflict or mutation |
| first `start`, operation `2945a2a4-5637-4608-acb8-386185dd7b5a` | **PASS** — archived old instance `44718fd5...`; `DEAD_GENERATION_RETIRED`; launched new instance `0836bba8...` |
| second `start`, operation `b04094eb-1746-479f-b360-f40bc96e718e` | **PASS** — reused strongly verified WEB PID `13880` |
| running `status`, operation `fd51e5eb-4cb2-4c1d-8334-47b851cf5377` | **PASS** — core OK, WEB/worker ready, Prometheus/Grafana ready, only the pre-existing 13.8% disk warning |
| `restart`, operation `3076297e-6a12-4756-8a1e-dc40c04f9a25` | **INTERRUPTED** — operation begin exists; supervisor and worker shutdown followed; no operation-complete event and no replacement start |
| failure `diagnose`, operation `6e315e19-109a-4bc3-a95b-8b1cf1f5ab2e` | **PASS** — sanitized bundle `artifacts/diagnostics/lifecycle-20260912T002853Z-6e315e19-109a-4bc3-a95b-8b1cf1f5ab2e` |
| required safe `stop`, operation `0a14ad4e-a8dc-414a-87f5-7cd234d867ba` | **PASS** — recognized interrupted instance `0836bba8...` as dead stale, archived/journaled it, and returned `OVERALL STOPPED` |
| final JSON `status`, operation `3fcd02ff-0f35-427b-a11f-247f55f8066d` | **PASS** — `STOPPED`, `MISSING`, runtime inactive, no stale canonical state, zero active jobs |

The successful running checkpoints proved canonical supervisor-root readiness and all three
Prometheus SwingLens targets up through the controller's core/target gates. No direct final claim is
made for the unexecuted post-restart status or `/health` check because the restart did not complete.
No retry or live patch loop was performed.

### Final machine state and verdict

- WEB, SUPERVISOR, and DURABLE_WORKER processes: absent;
- ports 8000, 9101, and 9102: free;
- canonical runtime-state file: absent (`MISSING`);
- archived stale evidence: preserved for both the original old generation and interrupted
  remediation generation;
- active `RUNNING`/`RECOVERING` jobs: zero;
- PostgreSQL 18.3: running on PID `6976`, schema head `0072_ceri_artifact_context_lineage`;
- Prometheus/Grafana: stopped by the safe-stop path;
- business pipelines/evidence: untouched.

Final verdict remains **NOT CERTIFIED** because the required restart and subsequent checks did not
complete. The stopped-generation convergence defect itself is remediated, exact-SHA CI is green,
and failure containment converged to canonical stopped state without manual runtime-state deletion.

## 25. Final certification closure attempt — 2026-09-12

### Calendar-sensitive E2E fixture root cause and remediation

CI run `34662075615` executed on Saturday 2026-09-12. The single-run fixture passed
`datetime.now(UTC).date()` directly as `CeriFeatureRebuildRequest.as_of_session`; production
correctly rejected that Saturday with `ValueError: as_of_session must be a valid exchange session`.
The defect was confined to the test fixture. Production exchange-session validation was not changed.

Commit `65d034311fe1658bc3ce8021c521839d88e0cc4e` adds
`certification_as_of_session(reference_timestamp)`, which derives the latest completed valid
session through `MarketClockService`, SwingLens's canonical exchange-calendar policy. The same
session now anchors seeded CERI features and OHLCV rows. The deterministic IB adapter also uses
`latest_completed_us_trading_day` and `previous_us_trading_day`; the certification path no longer
uses `date.today()` plus weekend-only arithmetic for exchange sessions.

The permanent fixture regression covers a weekday after daily-bar readiness, Saturday, Sunday, and
the 2026 Labor Day exchange holiday. Each case asserts both the exact derived session and
`is_us_trading_day(session)`. Focused fixture tests passed 4/4; the fixture plus the existing temporal
integrity regressions passed 42/42. The complete `tests/e2e/single_run_certification` suite passed
5/5 in 180.47 seconds against a guarded disposable PostgreSQL database. CI-scope Ruff
(`app tests scripts`) and `git diff --check` passed. A full local Windows browser command completed
45 tests successfully and had two non-calendar local failures: a 30-second database-screenshot
timeout after the certification pipeline completed and a port-8000 readiness timeout in the Windows
worker-recovery test. Canonical CI independently passed both affected jobs.

### Exact-SHA canonical CI

- Final code SHA: `65d034311fe1658bc3ce8021c521839d88e0cc4e`.
- GitHub Actions run: `34667043222`.
- Result: **SUCCESS**.
- `Lint, Test, and Migration Gates`: passed in 12m19s.
- `Chromium and Firefox Smoke`: passed in 2m50s.
- `Windows Durable Worker Recovery Gate`: passed in 5m02s.
- `Nightly Performance Baseline`: correctly skipped for the push event.

The browser gate passed on Saturday 2026-09-12, directly proving that the original calendar failure
does not recur on the exact repaired SHA.

### Real-machine preconditions

The final closure attempt began at `2026-09-12T02:30:56Z`. Structured preflight status proved:

- branch `codex/final-certification-runtime-topology-remediation` at exact code SHA `65d0343`;
- clean worktree;
- `OVERALL STOPPED`, runtime classification `MISSING`, runtime inactive, no conflict;
- WEB, SUPERVISOR, and DURABLE_WORKER absent;
- ports 8000, 9101, and 9102 free;
- zero active `RUNNING` or `RECOVERING` jobs;
- authoritative PostgreSQL 18.3 on PID `6976`, database `swinglens`, provenance from `.env`;
- current and expected Alembic head `0072_ceri_artifact_context_lineage`.

Precondition diagnose bundle:
`artifacts/diagnostics/lifecycle-20260912T022704Z-d25f5a8c-bf75-49bd-836a-1a00cbb6b9b4`.

### Bounded sequence and precise failure boundary

| Command / operation | Result |
|---|---|
| pre-start `status`, `08d43771-f879-453d-a8a2-c3476d6319ca` | **PASS** — canonical `STOPPED` |
| first `start`, `8b9fdf3c-8f4c-43eb-b144-55059050ac5a` | **PASS** — operation completed; instance `e7df018a...`; core, WEB, worker, Prometheus, and Grafana ready; only 14.1% free-disk warning |
| second `start`, `3dbc51c5-3ecd-4ee3-8875-442e1f6552a7` | **CERTIFICATION FAIL** — found instance `e7df018a...` dead, retired it as `DEAD_GENERATION_RETIRED`, and launched different instance `bd9f6b5d...`; idempotence was not proved |
| running `status` | not run after the failure boundary |
| `restart` | not run; no restart operation ID exists |
| post-restart `status` / readiness | not run |
| required failure `diagnose`, `fed26961-9d83-4d73-ad0e-2b35e962a5d3` | **PASS** — bundle `artifacts/diagnostics/lifecycle-20260912T023725Z-fed26961-9d83-4d73-ad0e-2b35e962a5d3` |
| recovery `stop`, `980212d1-2264-4ebb-9899-7884be0ca004` | **FAIL** — `LIFECYCLE_OPERATION_FAILED`; verified runtime could not be signaled because the platform kill call returned an exception |
| post-stop-failure `diagnose`, `b8d50614-f197-434a-b88e-5b7b6f2d1b52` | **PASS** — bundle `artifacts/diagnostics/lifecycle-20260912T024003Z-b8d50614-f197-434a-b88e-5b7b6f2d1b52` |
| read-only `status`, `7f7078bf-7ab2-4340-8663-90469eb3372e` | **PASS** — physical `STOPPED`, zero jobs, state safely classified `DEAD_STALE` |
| convergence `stop`, `a3441820-0cc6-4f07-84a5-aa63c9d567df` | **PASS** — retired dead instance `bd9f6b5d...` under the lifecycle lock |
| final `status`, `c241b389-f272-4f89-8760-c6e7fbd35938` | **PASS** — `STOPPED`, `MISSING`, runtime inactive, zero active jobs |

The first start has a successful terminal controller journal event at `2026-09-12T02:34:21Z`.
The second start began ten seconds later and classified every recorded process from that runtime as
absent by `2026-09-12T02:34:45Z`. No orderly shutdown journal event exists for instance
`e7df018a...`; therefore the evidence establishes external or abrupt process-tree disappearance but
does not identify the terminating actor. Per the no-retry rule, no new certification loop or live
patch was attempted.

### Final machine state, safety, and verdict

- WEB, SUPERVISOR, and DURABLE_WORKER: absent;
- ports 8000, 9101, and 9102: free;
- runtime state: `MISSING`, `runtimeActive=false`, `staleStatePresent=false`, no conflict;
- active `RUNNING`/`RECOVERING` jobs: zero;
- PostgreSQL 18.3: still listening on PID `6976`, schema at head;
- Prometheus/Grafana: stopped by recovery convergence;
- business data mutated: **NO**.

No authoritative business pipeline, CERI ingestion/rebuild, Winner maturation/cohort refresh,
market-data fetch, IB Gateway processing, SEC processing, broker action, historical repair, canary,
or business-evidence mutation was performed. Only disposable E2E databases and normal lifecycle
registration, heartbeat, journal, diagnostic, and observability state were written.

Final verdict is **NOT CERTIFIED**. Exact-SHA canonical CI and the calendar repair are green, but the
complete required real restart sequence lacks successful idempotent-start, restart, post-restart
status/readiness, and running diagnose evidence from one uninterrupted bounded attempt.

## 26. Windows runtime-persistence forensic remediation — 2026-09-12

### Preserved evidence and process timeline

The failed `e7df018a8f9847d19705bd6758daf78b` generation, operation IDs
`8b9fdf3c-8f4c-43eb-b144-55059050ac5a` and
`3dbc51c5-3ecd-4ee3-8875-442e1f6552a7`, and both requested diagnose bundles were inspected before
production changes. The bundles remain at
`artifacts/diagnostics/lifecycle-20260912T023725Z-fed26961-9d83-4d73-ad0e-2b35e962a5d3` and
`artifacts/diagnostics/lifecycle-20260912T024003Z-b8d50614-f197-434a-b88e-5b7b6f2d1b52`.
There is no `logs/lifecycle-supervisor.log`; the configured supervisor sink is
`logs/lifecycle-supervisor.err.log`.

All timestamps below are UTC. An unavailable value is recorded as unavailable rather than inferred.

| PID | Role | Created | Parent / group | First heartbeat | Last heartbeat | Last role log / state / listener | Disappearance / exit |
|---:|---|---|---|---|---|---|---|
| `19928` | supervisor venv launcher / process-group root | `02:32:43.981` | parent unavailable; group `19928` | n/a | n/a | launcher-specific terminal record unavailable | absent by `02:34:45.909`; exit code unavailable |
| `16424` | supervisor interpreter | `02:32:44.009816` | parent/group root `19928` | acquisition write `02:32:47.710` | overwritten registration makes the final heartbeat unrecoverable | last supervisor log `02:32:47.751`; the per-instance final supervisor-state write was not archived | absent by `02:34:45.909`; exit code unavailable |
| `13568` | WEB venv launcher | creation timestamp unavailable | child of supervisor; child group root `13568` | n/a | n/a | launcher-specific terminal record unavailable | absent by `02:34:45.909`; exit code unavailable |
| `13788` | WEB interpreter / listener | `02:32:47.728924` | parent/group launcher `13568` | n/a | n/a | final WEB log and final listener-backed scrape `02:34:22.009747`; no shutdown record | absent by `02:34:45.909`; exit code unavailable |
| `10620` | worker venv launcher | creation timestamp unavailable | child of supervisor; child group root `10620` | n/a | n/a | launcher-specific terminal record unavailable | absent by `02:34:45.909`; exit code unavailable |
| `25484` | durable worker interpreter | `02:32:47.763036` | parent/group launcher `10620` | registration/startup `02:32:56.598` | `02:34:21.045316` recovered from the next supervisor's worker-loss evidence | final worker role log `02:32:58.217516`; final per-instance supervisor-state write unavailable | absent before the next supervisor checked at `02:35:41.706`; exit code unavailable |

The first controller operation completed successfully at `02:34:21.449062`. The independent second
start began at `02:34:31.113357`, and its locked revalidation proved the entire old topology absent
before retiring the state at `02:34:45.909236`. The old generation has no signal, shutdown-begin,
process-shutdown, fatal-exception, ownership-timeout, Windows Error Reporting, Application, System,
Defender, or Code Integrity record. Security process-termination auditing was not readable without
elevation, so no claim is made from that unavailable source.

### Minimal lifetime and Windows containment evidence

`scripts/qa/windows_process_lifetime_harness.py` reproduces the actual boundary without using the
business database. It records PID, parent PID, UTC creation time, heartbeats, console membership,
`IsProcessInJob`, and `QueryInformationJobObject` limits. Command A ran through the same Codex PTY ->
PowerShell -> Python chain and terminated before each independent Command B inspection.

| Windows creation flags | Codex PTY result |
|---|---|
| `CREATE_NEW_PROCESS_GROUP` (old production behavior) | child died as Command A ended; only heartbeat sequence 1 was durable |
| `CREATE_BREAKAWAY_FROM_JOB` | child died as Command A ended |
| `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` | survived 15 seconds, then disappeared at approximately 37 seconds |
| `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB` | survived beyond 85 seconds and stopped only after an independent stop-file request |

The launcher and child both reported job membership. The inspectable job limits were `0x3000`:
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE=true`, `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK=true`, and
`JOB_OBJECT_LIMIT_BREAKAWAY_OK=false`. The successful combined child still reported membership in a
job, consistent with a nested or persistent outer containment boundary; no job identity was
available, so none is invented. The same old flags survived from a non-PTY shell, explaining why
ordinary local tests did not reproduce the failure.

This experiment identifies H1 as the cause: the inherited Codex PTY execution containment tears
down descendants when its owning command boundary closes. In this environment, that cleanup was the
terminating actor for `e7df018a...`. H2 is eliminated for that generation by the absence of a normal
signal and orderly shutdown event. H3 was not evidenced; cycle failures were caught, and the new
top-level wrapper now persists any future uncaught process failure. H4 is eliminated by the absence
of `runtime.supervisor.ownership_timeout`, the old successful acquisition, and replacement
acquisition only after the old topology was absent. H5 has no supporting OS event and remains
unprovable from process-termination auditing that was not enabled/readable.

### Remediation and permanent regression

The root runtime now launches on Windows with the experimentally proven three-flag combination. It
remains supervisor-rooted and retains strong runtime, repository, PID-creation-time, ancestry,
listener, Git-SHA, and fingerprint validation.

Because a detached runtime cannot safely accept `CTRL_BREAK_EVENT` from an unrelated later console,
Windows `stop` now atomically writes a runtime-instance-scoped shutdown request containing the
strongly revalidated supervisor PID and creation time plus the requesting operation ID. The
supervisor rejects mismatched identity, performs its existing orderly WEB/worker shutdown, removes
the request, and records shutdown-request/begin/complete/process-shutdown events. POSIX retains
SIGTERM. Supervisor logging now also records parent PID, signal name/number, fatal top-level
exceptions, and ownership timeout in the durable lifecycle journal without secrets.

The Windows CI lane now includes
`tests/e2e/test_windows_lifecycle_persistence.py`. Independent controller A launches the exact
production root, reaches READY, and exits; controller B waits 45 seconds, verifies the same runtime,
supervisor, WEB generation, worker/supervisor registration generations, no retirement and one leaf
per role, then reuses it; controller C requests stop and proves all three ports and roles disappear.
The PowerShell stop-order regression additionally requires the controller to wait for the strongly
verified supervisor PID to exit before attempting legacy registered-remainder cleanup.

Local gates after the main remediation passed 146 lifecycle-focused tests and 22 warnings; later
focused sets passed 98, 56, and, after the real-machine stop-order fix, 102 tests with one dependency
warning. Repository-wide Ruff and `git diff --check` passed. Four earlier CI iterations remain part
of the evidence: Windows service-fixture assumption (`34671287025`), controller `PYTHONPATH`
(`34671643050`), shutdown-evidence sink assertion (`34671952565`), and reserved logging field
(`34672287388`). These were test/observability defects found while the core survival/reuse/port-stop
behavior was already passing; each was corrected rather than hidden.

The main runtime remediation SHA `d41f41ce88967333f3cb073fe92c750e5e68bce6` passed exact-SHA CI
run `34672682068`: `Lint, Test, and Migration Gates`, `Chromium and Firefox Smoke`, and `Windows
Durable Worker Recovery Gate` all succeeded. The final stop-order remediation SHA is
`0267ebd554fefe804938fb96678238024c6a4560`; exact-SHA CI run `34674107533` is **SUCCESS**. `Lint,
Test, and Migration Gates` passed in 14m59s, `Chromium and Firefox Smoke` in 2m52s, and the extended
`Windows Durable Worker Recovery Gate` in 4m59s. The scheduled nightly job was correctly skipped.

### Independent-command persistence probe and stop boundary

After run `34672682068` was fully green, the small probe ran in `CERTIFICATION` mode, which disabled
automatic Winner maturation/cohort refresh and market-data prewarm. It did not enqueue or execute a
business job.

| Command / operation | Result |
|---|---|
| preflight `status`, `3b50235a-bf1a-40a7-98e7-f0bd52886d61` | **PASS** — `STOPPED`, runtime state `MISSING`, schema at head, PostgreSQL 18.3 reachable, zero active jobs |
| first `start`, `fdd6df2b-e2d1-4146-8a6a-da7aeeeafabc` | **PASS** — launcher exited normally; instance `647b5b407b2742f8a05d497c01c60f8f`, supervisor PID `23672`, WEB PID `26376`, worker PID `21668`, core/worker/observability ready; only the pre-existing 14.1% disk warning |
| independent status after more than 45 seconds, `d18ba456-8eef-4401-ac39-d8e086dedebf` | **PASS** — original runtime remained healthy |
| independent second `start`, `09a7ada0-a617-45eb-b155-465b17463e7b` | **PASS** — explicitly reused strongly verified WEB PID `26376`; same runtime/supervisor; restart counters remained zero; no stale retirement |
| independent `stop`, `eefccdfe-40a1-4747-af11-2f6678dce3dc` | **FAIL** — the instance-scoped request was accepted and WEB/worker shut down, but the controller raced the still-exiting supervisor through legacy `signal-registered` and received WinError 87 |
| required failure `diagnose`, `90bf47c3-321e-419d-9f6e-7e1734678644` | **PASS** — bundle `artifacts/diagnostics/lifecycle-20260912T044600Z-90bf47c3-321e-419d-9f6e-7e1734678644` |
| read-only failure status, `e92ea660-65de-4ff4-9ac3-85db8acce687` | **PASS** — physical topology stopped, ports free, state safely `DEAD_STALE`, zero active jobs |
| containment convergence stop, `95192646-f0ca-413a-9dde-6d7ad17ce95d` | **PASS** — retired the safely dead state and returned canonical `STOPPED`; PostgreSQL remained running |

The stop failure is causally precise. At `04:45:03.349` the supervisor durably recorded the valid
instance-scoped request and shutdown begin. WEB port 8000 released, worker PID `21668` recorded
process shutdown at `04:45:12.600`, and then the old remainder path called cross-console
`os.kill(..., CTRL_BREAK_EVENT)` before supervisor PID `23672` finished. That call failed at
`04:45:19.177`. Commit `0267ebd...` now waits for the verified supervisor to finish before checking
remainders, eliminating the race without forced PID-only termination.

Per the explicit probe-failure rule, no restart or full bounded certification sequence was run and
the persistence probe was not retried. Therefore the result remains **NOT CERTIFIED**, even though
cross-command survival and exact-generation idempotent reuse passed and the newly discovered stop
ordering defect is locally remediated. Certification requires a future explicitly authorized,
single fresh persistence probe on the final SHA followed, only if it passes, by the one-shot bounded
restart sequence.

Final containment left WEB, SUPERVISOR, and DURABLE_WORKER absent; ports 8000/9101/9102 free;
runtime state `MISSING`; zero active jobs; PostgreSQL 18.3 running on PID `6976`; and business data
untouched. Only lifecycle registration, heartbeat, journal, log, diagnostic, and observability state
was written during this task.

## 27. Final-SHA certification closure attempt — 2026-09-12

### Tested revision and clean baseline

The executable/runtime tree tested was exactly
`0267ebd554fefe804938fb96678238024c6a4560` (`Wait for supervisor during graceful stop`). The
checked-out SHA was the later documentation-only commit
`f652a8957fcb23ca8b8f159b3046836c3974c711`; its only diff from `0267ebd...` was this forensic
report. Exact-code-SHA GitHub Actions run `34674107533` was **SUCCESS**.

The closure began with a clean worktree. Structured status operation
`7739c15b-ec01-4b80-bcc0-97e0ed5e3c53` reported `STOPPED`, runtime classification `MISSING`,
`runtimeActive=false`, no stale state, no conflict, zero active jobs, PostgreSQL 18.3 reachable on
PID `6976`, and current/expected Alembic head `0072_ceri_artifact_context_lineage`. Independent
physical checks found no WEB, SUPERVISOR, or DURABLE_WORKER process and no listener on ports 8000,
9101, or 9102. The PostgreSQL Windows service `postgresql-x64-18` was running from the configured
PostgreSQL 18 executable and data directory.

Every runtime-affecting invocation used `RUNTIME_MODE=CERTIFICATION`. The worker's recorded startup
configuration confirmed certification isolation, including disabled automatic Winner maturation,
Winner cohort refresh, unrelated CERI/background work, market-data prewarm/provider prefetch, cache
refresh, stale-job recovery, and unrelated retry/continuation claims.

### Fresh persistence probe

| Command / operation | Result |
|---|---|
| first `start`, `8c54bb7f-8f67-4b7c-a888-0466e0f1fd69` | **PASS** — controller exited successfully; instance `4b789077343e492b83e0eedbdf42aa19`, supervisor PID `14904`, supervisor process-group PID `17016`, WEB PID `25508`, worker PID `16476`, fingerprint `a8f8fb32d8a0132d33498c599d63041ad7a1ddd9807332f56d99b1437807486f`; core, worker, Prometheus, and Grafana ready; only the pre-existing disk-space warning |
| independent status after controller exit, `49040358-cc41-4b60-aad4-c3347e8b7e50` | **PASS** — invoked about 79 seconds after the first launcher returned; the same runtime instance, supervisor, WEB generation, and fingerprint remained `ACTIVE_VALID`; worker healthy; no stale state or conflict; actual Prometheus targets `swinglens-web`, `swinglens-worker`, and `swinglens-supervisor` all `up` |
| independent second `start`, `876e993c-2603-4dfd-9054-7fac3d323d03` | **PASS** — explicitly reused strongly verified WEB PID `25508`; runtime instance `4b789077...`, supervisor PID `14904`, and WEB generation were unchanged; WEB and worker restart counters remained zero; no retirement, replacement generation, or `DEAD_STALE` transition occurred |
| required first `stop`, `9d529c04-5f33-4c1f-ab05-98fa63a077ce` | **FAIL** — exit code 1 after 66.3 seconds: `Worker did not acknowledge durable quiesce before timeout; stop aborted and claims resumed.` No instance-scoped shutdown request or supervisor shutdown-begin event was emitted by this failed operation |

The failure occurred before the newly remediated supervisor-exit ordering boundary. The immediately
captured state still had the original canonical topology and zero active jobs. This attempt did not
modify production code, did not retry the certification probe, and did not proceed to the bounded
restart certification.

### Required failure evidence and safe containment

Required diagnose operation `729a79f3-e5cf-46fe-8e71-c694bde2aa77` succeeded and wrote
`artifacts/diagnostics/lifecycle-20260912T105158Z-729a79f3-e5cf-46fe-8e71-c694bde2aa77`. It captured
the original runtime instance and PIDs, all three lifecycle listeners, zero active jobs, live
supervisor/worker registrations, restart counters of zero, PostgreSQL/schema provenance, and the
full process tree. During diagnostic collection, direct core/application HTTP probes timed out even
though the registered runtime remained present and Prometheus still reported all three targets
`up`; this is preserved as observed evidence rather than reclassified.

Because the failed stop had aborted before signaling shutdown and the diagnostic proved zero active
jobs, the single permitted safe-containment `stop` was appropriate. Operation
`14e15d49-8d8c-405f-b2a2-6623e2b0cd32` succeeded. The original supervisor accepted the
instance-scoped request at `2026-09-12T10:54:23.736605Z`, recorded shutdown begin, stopped the WEB
and worker, recorded `runtime.shutdown_complete` and supervisor `runtime.process_shutdown` at
`10:54:39Z`, and the controller completed successfully only afterward. This containment operation
therefore exercised the `0267ebd...` graceful supervisor-exit ordering successfully, but it cannot
convert the failed required probe stop into a certification pass.

Final read-only status operation `1b3310e6-025f-4ec2-856f-404384e4f738` reported `STOPPED`, runtime
classification `MISSING`, `runtimeActive=false`, no stale state, no conflict, zero active jobs,
PostgreSQL 18.3 reachable, and schema at head. Physical verification found WEB, SUPERVISOR, and
DURABLE_WORKER absent and ports 8000/9101/9102 free. PostgreSQL remained running on listener PID
`6976`.

### Closure verdict

The complete one-shot lifecycle certification, restart, replacement-runtime checks, post-restart
status, and running diagnose were **not run** because the fresh probe's required first graceful stop
failed. There are therefore no full-certification operation IDs and no old/new restart runtime
instances to report.

No business pipeline, CERI provider ingest/rebuild, Winner maturation/cohort refresh, market-data
refresh, IB or SEC processing, broker action, historical repair, or canary was invoked. The worker
log records certification isolation and no claimed/executed business job; structured checks before,
during, and after the attempt reported zero active jobs. Business data was not mutated. Only normal
lifecycle registration, heartbeat, quiesce/resume control, journal, diagnostic, and observability
state changed.

Final verdict remains **NOT CERTIFIED**. Cross-command survival and independent idempotent start
passed, and the later safe-containment stop proved the final supervisor-exit ordering can complete;
however, the required first probe stop itself failed at durable-worker quiesce, so the gated restart
certification has no valid evidence.
